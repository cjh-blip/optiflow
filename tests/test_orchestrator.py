"""编排层测试：任务分解 / 选能力 / 拼流水线 / 校验结果。

最重要的一条在最前面：**有歧义必须报错，不许猜**。
这正是编排层存在的理由——Dispatcher 会按注册顺序悄悄挑一个，
而实测踩过：第二步被路由回算法层，产物里没有 stf，KeyError。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from optiflow.adapters.dialux import DialuxAdapter
from optiflow.adapters.fake import FakeAdapter
from optiflow.adapters.lumen import LumenPlannerAdapter
from optiflow.dispatcher import AdapterNotFound
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import ResultSet
from optiflow.orchestrator import (
    AmbiguousAdapter,
    Check,
    Orchestrator,
    Pipeline,
    PipelineContext,
    Step,
    StepRun,
    artifact_exists,
    cross_step_metric_matches,
    metric_targets_met,
)
from optiflow.pipelines import (
    get_pipeline,
    lighting_plan_pipeline,
    task_with_planned_fixtures,
)
from optiflow.project import Project
from optiflow.registry import AdapterRegistry

OUTLINE = [Point(x=0, y=0), Point(x=11.9, y=0), Point(x=11.9, y=8.78), Point(x=0, y=8.78)]


def _task(u0_target=None, lux_target=500.0) -> TaskSpec:
    room = Space(
        id="room-208", name="208 会议室",
        geometry=Geometry(kind="room", outline=OUTLINE, height=3.0),
        work_plane=0.75,
        reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
    )
    constraints = [Metric(name="illuminance_avg", target=lux_target, unit="lx")]
    if u0_target is not None:
        constraints.append(Metric(name="uniformity_u0", target=u0_target, unit=""))
    return TaskSpec(kind="layout", spaces=[room], constraints=constraints,
                    extra={"flux": 3000.0})


def _full_registry(tmp_path: Path) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(LumenPlannerAdapter(output_dir=tmp_path / "plans"))
    registry.register(DialuxAdapter(output_dir=tmp_path / "stf"))
    return registry


# ============================================================ 选能力


def test_ambiguous_routing_is_refused_not_guessed(tmp_path: Path) -> None:
    """都是 kind=layout 时，不写 tags 就必须报错——不许按注册顺序挑。"""
    registry = _full_registry(tmp_path)
    orchestrator = Orchestrator(registry)
    step = Step(name="x", kind="layout", build=lambda ctx: ctx.task)
    with pytest.raises(AmbiguousAdapter) as excinfo:
        orchestrator.resolve(step)
    message = str(excinfo.value)
    assert "lumen" in message and "dialux" in message
    assert "requires" in message


def test_tags_disambiguate_the_channel(tmp_path: Path) -> None:
    orchestrator = Orchestrator(_full_registry(tmp_path))
    algorithm = orchestrator.resolve(
        Step(name="a", kind="layout", requires=("algorithm",), build=lambda c: c.task))
    file_channel = orchestrator.resolve(
        Step(name="b", kind="layout", requires=("file",), build=lambda c: c.task))
    assert algorithm.capabilities().name == "lumen"
    assert file_channel.capabilities().name == "dialux"


def test_adapter_name_pins_a_specific_adapter(tmp_path: Path) -> None:
    orchestrator = Orchestrator(_full_registry(tmp_path))
    step = Step(name="x", kind="layout", adapter_name="dialux", build=lambda c: c.task)
    assert orchestrator.resolve(step).capabilities().name == "dialux"


def test_unknown_channel_raises_with_candidates_listed(tmp_path: Path) -> None:
    orchestrator = Orchestrator(_full_registry(tmp_path))
    step = Step(name="x", kind="layout", requires=("real-machine",), build=lambda c: c.task)
    with pytest.raises(AdapterNotFound) as excinfo:
        orchestrator.resolve(step)
    assert "real-machine" in str(excinfo.value)


def test_dispatcher_alone_would_have_picked_wrong(tmp_path: Path) -> None:
    """反证：同一个注册表，Dispatcher 会悄悄挑第一个——所以编排层必须存在。"""
    registry = _full_registry(tmp_path)
    assert registry.find("layout").capabilities().name == "lumen"
    registry2 = AdapterRegistry()
    registry2.register(DialuxAdapter(output_dir=tmp_path))
    registry2.register(LumenPlannerAdapter(output_dir=tmp_path))
    assert registry2.find("layout").capabilities().name == "dialux"


# ============================================================ 流水线


def test_lighting_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    orchestrator = Orchestrator(_full_registry(tmp_path),
                                project=Project(id="p", name="流水线"))
    run = orchestrator.run(lighting_plan_pipeline(), _task())
    assert run.completed and run.ok, [c.detail for c in run.failures]
    assert [s.name for s in run.steps] == ["plan", "export"]
    assert [s.adapter for s in run.steps] == ["lumen", "dialux"]
    assert Path(run.result_of("plan").artifacts["plan"]).exists()
    assert Path(run.result_of("export").artifacts["stf"]).exists()
    assert run.elapsed > 0


def test_export_step_reads_the_plan_artifact_not_memory(tmp_path: Path) -> None:
    """接缝是【产物文件】：把 plan.json 改掉，下游就该跟着变。"""
    orchestrator = Orchestrator(_full_registry(tmp_path))
    run = orchestrator.run(lighting_plan_pipeline(), _task())
    plan_path = Path(run.result_of("plan").artifacts["plan"])
    fed = task_with_planned_fixtures(_task(), plan_path)
    assert len(fed.fixtures) == int(run.result_of("plan").metric("fixture_count"))
    assert fed.spaces == _task().spaces


def test_pipeline_stops_at_the_first_failing_step(tmp_path: Path) -> None:
    """U0 目标定到做不到的 0.95 → plan 步校验失败 → export 不该再跑。"""
    orchestrator = Orchestrator(_full_registry(tmp_path))
    run = orchestrator.run(lighting_plan_pipeline(), _task(u0_target=0.95))
    assert run.completed is False
    assert run.ok is False
    assert run.failed_at == "plan"
    assert [s.name for s in run.steps] == ["plan"]
    failed = [c for c in run.failures if "uniformity_u0" in c.name]
    assert failed and "低于目标" in failed[0].detail


def test_get_pipeline_by_name(tmp_path: Path) -> None:
    assert get_pipeline("lighting_plan").steps[0].name == "plan"
    with pytest.raises(KeyError, match="没有名为"):
        get_pipeline("nope")


# ============================================================ 校验器


def test_metric_targets_met_flags_a_missed_target() -> None:
    ctx = PipelineContext(task=_task())
    run = StepRun(name="s", adapter="a", job_id="j", result=ResultSet(
        job_id="j",
        metrics=[Metric(name="illuminance_avg", value=420.0, unit="lx", target=500.0)],
    ))
    ctx.runs["s"] = run
    checks = metric_targets_met()(ctx, run)
    assert len(checks) == 1 and checks[0].passed is False
    assert "420" in checks[0].detail and "500" in checks[0].detail


def test_metric_targets_met_ignores_untargeted_metrics() -> None:
    ctx = PipelineContext(task=_task())
    run = StepRun(name="s", adapter="a", job_id="j", result=ResultSet(
        job_id="j", metrics=[Metric(name="area", value=104.48, unit="m2")]))
    ctx.runs["s"] = run
    checks = metric_targets_met()(ctx, run)
    assert checks[0].passed is True


def test_artifact_exists_detects_missing_key_and_missing_file(tmp_path: Path) -> None:
    ctx = PipelineContext(task=_task())
    run = StepRun(name="s", adapter="a", job_id="j",
                  result=ResultSet(job_id="j", artifacts={}))
    ctx.runs["s"] = run
    assert artifact_exists("stf")(ctx, run)[0].passed is False

    ghost = StepRun(name="s2", adapter="a", job_id="j",
                    result=ResultSet(job_id="j", artifacts={"stf": str(tmp_path / "no.stf")}))
    assert artifact_exists("stf")(ctx, ghost)[0].passed is False

    real = tmp_path / "real.stf"
    real.write_text("[VERSION]", encoding="utf-8")
    ok = StepRun(name="s3", adapter="a", job_id="j",
                 result=ResultSet(job_id="j", artifacts={"stf": str(real)}))
    assert artifact_exists("stf", min_bytes=1)(ctx, ok)[0].passed is True
    assert artifact_exists("stf", min_bytes=10_000)(ctx, ok)[0].passed is False


def test_cross_step_check_detects_a_dropped_fixture() -> None:
    """「算出来 31 盏、导出去 28 盏」这种掉队，只有对账才发现得了。"""
    ctx = PipelineContext(task=_task())
    plan = StepRun(name="plan", adapter="lumen", job_id="j1",
                   result=ResultSet(job_id="j1",
                                    metrics=[Metric(name="fixture_count", value=31.0)]))
    export = StepRun(name="export", adapter="dialux", job_id="j2",
                     result=ResultSet(job_id="j2",
                                      metrics=[Metric(name="fixtures", value=28.0)]))
    ctx.runs["plan"], ctx.runs["export"] = plan, export
    check = cross_step_metric_matches("plan", "fixture_count", "export", "fixtures")
    result = check(ctx, export)
    assert result[0].passed is False
    assert "31" in result[0].detail and "28" in result[0].detail


def test_cross_step_check_passes_when_equal() -> None:
    ctx = PipelineContext(task=_task())
    plan = StepRun(name="plan", adapter="lumen", job_id="j1",
                   result=ResultSet(job_id="j1",
                                    metrics=[Metric(name="fixture_count", value=31.0)]))
    export = StepRun(name="export", adapter="dialux", job_id="j2",
                     result=ResultSet(job_id="j2",
                                      metrics=[Metric(name="fixtures", value=31.0)]))
    ctx.runs["plan"], ctx.runs["export"] = plan, export
    check = cross_step_metric_matches("plan", "fixture_count", "export", "fixtures")
    assert check(ctx, export)[0].passed is True


def test_context_refuses_unknown_step() -> None:
    ctx = PipelineContext(task=_task())
    with pytest.raises(KeyError, match="还没有结果"):
        ctx.result_of("nope")


# ============================================================ 结构


def test_optional_step_failure_does_not_stop_the_pipeline(tmp_path: Path) -> None:
    """可选步骤失败只记校验失败，不拖垮整条线。"""
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=0.01))
    orchestrator = Orchestrator(registry)
    pipeline = Pipeline(goal="可选步骤", steps=[
        Step(name="opt", kind="calculate", requires=("simulation",), optional=True,
             build=lambda ctx: ctx.task,
             checks=(lambda ctx, run: [Check(name="故意失败", passed=False,
                                            detail="构造的失败")],)),
        Step(name="after", kind="calculate", requires=("simulation",),
             build=lambda ctx: ctx.task),
    ])
    run = orchestrator.run(pipeline, _task())
    assert [s.name for s in run.steps] == ["opt", "after"]
    assert run.completed is True
    assert run.ok is False
    assert any(c.name == "故意失败" for c in run.failures)


def test_empty_pipeline_is_not_ok(tmp_path: Path) -> None:
    orchestrator = Orchestrator(_full_registry(tmp_path))
    run = orchestrator.run(Pipeline(goal="空", steps=[]), _task())
    assert run.completed is False and run.ok is False

