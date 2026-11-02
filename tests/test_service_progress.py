"""平台层进度契约：steps 必须说出去，stage 与 percent 互不干扰。

背景（P0-A / P0-B）：

`PipelineRun` 里每一步的 name / adapter / job_id / metrics / artifacts / checks
一直都在，但 `ServiceJob.to_dict()` 只吐 status / percent / message —— 界面拿不到
步骤。**不是没采集，是没外传。**

第二条：stage 回答「在哪一步」，percent 只回答「大概剩多少」。把两者婚成一条
公式，界面就只能说「37%」，说不出「正在布灯」。

这两条用 FakeAdapter 跑真流水线来钉，不接任何真软件。
"""
from __future__ import annotations

import time

import pytest

import optiflow.service as svc
from optiflow.adapters.fake import FakeAdapter
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import ResultSet
from optiflow.orchestrator import Check, Pipeline, Step, StepRun
from optiflow.registry import AdapterRegistry
from optiflow.service import (
    PROGRESS_CEILING,
    PROGRESS_START,
    STAGE_DONE,
    STAGE_FAILED,
    PlatformService,
    step_brief,
    step_to_dict,
)

#: FakeAdapter 每一步耗时。够长，让轮询能采到中间态；够短，测试不拖。
STEP_SECONDS = 0.3
#: 轮询间隔。0.3s 一步 → 每步约 60 个采样点。
POLL_GAP = 0.005


def _task() -> TaskSpec:
    outline = [Point(x=0, y=0), Point(x=10, y=0), Point(x=10, y=8), Point(x=0, y=8)]
    room = Space(id="r1", name="测试房间",
                 geometry=Geometry(kind="room", outline=outline, height=3.0))
    return TaskSpec(
        kind="layout",
        spaces=[room],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"utilization_factor": 0.7, "maintenance_factor": 0.8},
    )


def _two_step_pipeline(goal: str = "测试目标") -> Pipeline:
    return Pipeline(goal=goal, steps=[
        Step(name="alpha", kind="layout", requires=("simulation",),
             build=lambda ctx: ctx.task),
        Step(name="beta", kind="layout", requires=("simulation",),
             build=lambda ctx: ctx.task),
    ])


def _failing_check(ctx, run_step):
    return [Check(name="故意不通过", passed=False, detail="这条是测试构造的失败")]


@pytest.fixture()
def service(monkeypatch) -> PlatformService:
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=STEP_SECONDS))
    monkeypatch.setattr(svc, "get_pipeline", lambda name, **kw: _two_step_pipeline())
    return PlatformService(registry, project=None, timeout=10.0)


def _drain(service: PlatformService, job_id: str, limit: float = 20.0):
    """轮询到终态，返回每次 status() 的原样快照。"""
    samples = []
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        snap = service.status(job_id)
        samples.append(snap)
        if snap["status"] in ("done", "failed", "cancelled"):
            return samples
        time.sleep(POLL_GAP)
    raise AssertionError(f"任务没在 {limit}s 内收尾，最后一份快照：{samples[-1]}")


# ---------------------------------------------------------------- P0-A steps

def test_status_carries_real_steps(service: PlatformService) -> None:
    """跑完以后 status() 里有真实 steps 数组，字段名与 step_to_dict 一致。"""
    job_id = service.submit("lighting_plan", _task())
    final = _drain(service, job_id)[-1]

    steps = final["steps"]
    assert [s["name"] for s in steps] == ["alpha", "beta"]
    for s in steps:
        assert set(s) == {
            "name", "adapter", "job_id", "metrics", "artifacts", "checks",
        }, f"step 字段形状变了：{sorted(s)}"
        assert s["adapter"] == "fake"
        assert s["job_id"], "步骤没有带上它自己的 job_id"
        assert [m["name"] for m in s["metrics"]], "步骤没有带上指标"
        assert s["artifacts"], "步骤没有带上产物键"
        assert isinstance(s["checks"], list)


def test_steps_visible_while_still_running(service: PlatformService) -> None:
    """运行中就能看到已完成的步骤，不是等整条流水线返回才有。"""
    job_id = service.submit("lighting_plan", _task())
    running_with_steps = [
        snap for snap in _drain(service, job_id)
        if snap["status"] == "running" and snap["steps"]
    ]
    assert running_with_steps, "整条流水线里没有一次在 running 状态下看到步骤"
    # 第一次看到步骤时，只可能跑完了第一步（alpha）
    first = running_with_steps[0]
    assert [s["name"] for s in first["steps"]] == ["alpha"]


def test_step_brief_drops_raw_and_keeps_the_rest() -> None:
    """step_brief 只去掉 raw，其余键一个不少——两种 steps 形状必须一致。"""
    step_run = StepRun(name="x", adapter="fake", job_id="j1",
                       result=ResultSet(job_id="j1", raw={"big": "x" * 100}))
    full = step_to_dict(step_run)
    brief = step_brief(step_run)
    assert "raw" not in brief
    assert set(full) - {"raw"} == set(brief)


# ---------------------------------------------------------------- P0-B 进度

def test_percent_monotonic_capped_then_exactly_100(service: PlatformService) -> None:
    """percent 单调不减、运行中不越天花板、完成正好 100。"""
    job_id = service.submit("lighting_plan", _task())
    samples = _drain(service, job_id)

    seq = [s["percent"] for s in samples]
    assert all(b >= a for a, b in zip(seq, seq[1:])), f"percent 回退过：{seq}"

    running = [s["percent"] for s in samples if s["status"] == "running"]
    assert running, "一次 running 快照都没采到"
    assert max(running) <= PROGRESS_CEILING, f"运行中越过了天花板：{max(running)}"
    assert min(running) >= PROGRESS_START, f"running 起点低于 {PROGRESS_START}：{min(running)}"

    assert samples[-1]["percent"] == 100.0


def test_stage_follows_step_names_then_lands_on_done(service: PlatformService) -> None:
    """stage 在运行中是步骤名，收尾是终态。与 percent 各走各的。"""
    job_id = service.submit("lighting_plan", _task())
    samples = _drain(service, job_id)
    stages = [s["stage"] for s in samples]

    assert "alpha" in stages, f"没见过 alpha 阶段：{sorted(set(stages))}"
    assert "beta" in stages, f"没见过 beta 阶段：{sorted(set(stages))}"
    assert stages[-1] == STAGE_DONE


def test_failure_freezes_percent_instead_of_resetting(monkeypatch) -> None:
    """失败时 percent 停在当前位置：不归零（像重跑了），也不跳 100。"""
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=0.15))
    monkeypatch.setattr(svc, "get_pipeline", lambda name, **kw: Pipeline(
        goal="会失败的流水线",
        steps=[
            Step(name="ok_step", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task),
            Step(name="bad_step", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task, checks=(_failing_check,)),
        ],
    ))
    service = PlatformService(registry, project=None, timeout=10.0)

    job_id = service.submit("lighting_plan", _task())
    samples = _drain(service, job_id)
    final = samples[-1]

    assert final["status"] == "failed"
    assert final["stage"] == STAGE_FAILED
    assert 0.0 < final["percent"] < 100.0, f"失败时 percent 不该是 {final['percent']}"
    # 失败前做完的那一步仍然在 steps 里，不能因为整体失败就把已完成的抹掉
    assert final["steps"] and final["steps"][0]["name"] == "ok_step"
    # 中途也没有回退过
    seq = [s["percent"] for s in samples]
    assert all(b >= a for a, b in zip(seq, seq[1:])), f"percent 回退过：{seq}"
