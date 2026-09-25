"""P2 接入平台：LumenPlannerAdapter 与「先算后验」流水线。

钉住三件事：

1. 算法层是通过 capabilities() 被 dispatcher 选中的，不是硬编码；
2. 方案是真产物（可读的 JSON + 可复用的 Fixture 列表），不是一份日志；
3. 算法层的输出能真的喂给 DIALux 文件层——两段拼起来是一条流水线。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from optiflow.adapters.dialux import DialuxAdapter
from optiflow.adapters.lumen import LumenPlannerAdapter, apply_plan
from optiflow.algo.engine import ALGO_LIMITS, plan_layout
from optiflow.dispatcher import Dispatcher
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import JobStatus
from optiflow.project import Project
from optiflow.registry import AdapterRegistry

FLUX = 3000.0
OUTLINE = [Point(x=0, y=0), Point(x=9.57, y=0), Point(x=9.57, y=12.97), Point(x=0, y=12.97)]


def _room(space_id: str = "room-1", outline=None, height: float = 3.0) -> Space:
    return Space(
        id=space_id, name="会议室",
        geometry=Geometry(kind="room",
                          outline=outline if outline is not None else OUTLINE,
                          height=height),
        work_plane=0.75,
        reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
    )


def _task() -> TaskSpec:
    return TaskSpec(
        kind="layout", spaces=[_room()],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"flux": FLUX},
    )


def _wait(adapter, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while adapter.status(job_id).status not in (
            JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        assert time.monotonic() < deadline, "超时"
        time.sleep(0.02)
    return adapter.status(job_id)


@pytest.fixture()
def adapter(tmp_path: Path) -> LumenPlannerAdapter:
    return LumenPlannerAdapter(output_dir=tmp_path)


def test_optiflow_is_self_contained_when_only_src_is_on_the_path(tmp_path: Path) -> None:
    """optiflow 必须自包含：只把 src/ 放进 PYTHONPATH 也要能跑通主链路。

    这条是踩出来的（BLOCKED.md B-2）：适配器原先 import 了搬运件 src.planner.core，
    只在「仓库根恰好在 sys.path 上」时成立（测试里靠 conftest 兜住），
    从别处启动就 ModuleNotFoundError: No module named 'src' —— 实测在
    scripts/shell_journey.py 上炸过。所以射线法提成了 optiflow/geometry.py。

    这个测试故意用子进程 + cwd=临时目录：让仓库根【不可能】被隐式加进 sys.path。
    """
    script = tmp_path / "run.py"
    script.write_text(
        "from optiflow.adapters.dialux import DialuxAdapter\n"
        "from optiflow.adapters.lumen import LumenPlannerAdapter\n"
        "from optiflow.orchestrator import Orchestrator\n"
        "from optiflow.pipelines import lighting_plan_pipeline\n"
        "from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec\n"
        "from optiflow.registry import AdapterRegistry\n"
        "outline = [Point(x=0, y=0), Point(x=6, y=0), Point(x=6, y=4), Point(x=0, y=4)]\n"
        "room = Space(id='r', name='r',\n"
        "             geometry=Geometry(kind='room', outline=outline, height=3.0),\n"
        "             reflectance={'ceiling': 0.7, 'wall': 0.5})\n"
        "task = TaskSpec(kind='layout', spaces=[room],\n"
        "                constraints=[Metric(name='illuminance_avg', target=300.0)],\n"
        "                extra={'flux': 3000.0})\n"
        "registry = AdapterRegistry()\n"
        "registry.register(LumenPlannerAdapter())\n"
        "registry.register(DialuxAdapter())\n"
        "run = Orchestrator(registry, archive_steps=False).run(\n"
        "    lighting_plan_pipeline(), task)\n"
        "assert run.ok, [c.detail for c in run.failures]\n"
        "assert run.result_of('export').artifacts['stf']\n"
        "print('SELF-CONTAINED OK')\n",
        encoding="utf-8")

    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run([sys.executable, str(script)], cwd=str(tmp_path),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF-CONTAINED OK" in result.stdout


def test_dispatcher_routes_layout_to_the_planner(adapter: LumenPlannerAdapter) -> None:
    registry = AdapterRegistry()
    registry.register(adapter)
    assert registry.find("layout") is adapter
    assert adapter.capabilities().name == "lumen"


def test_submit_is_async(adapter: LumenPlannerAdapter) -> None:
    started = time.monotonic()
    job_id = adapter.submit(_task())
    assert time.monotonic() - started < 0.5
    assert job_id.startswith("lumen-")


def test_result_carries_metrics_plan_artifact_and_honest_raw(adapter) -> None:
    job_id = adapter.submit(_task())
    assert _wait(adapter, job_id).status is JobStatus.DONE
    result = adapter.result(job_id)
    names = {m.name for m in result.metrics}
    assert {"fixture_count", "illuminance_avg", "uniformity_u0", "compliant"} <= names
    assert result.metric("uniformity_u0") >= 0.6
    assert result.raw["channel"] == "algorithm"
    assert "非仿真结论" in result.raw["note"]
    assert result.raw["assumptions"]
    plan = json.loads(Path(result.artifacts["plan"]).read_text(encoding="utf-8"))
    assert plan["compliant"] is True
    assert len(plan["fixtures"]) == int(result.metric("fixture_count"))
    assert plan["rooms"][0]["count"] == len(plan["fixtures"])


def test_capabilities_limits_come_from_the_algorithm_layer(adapter) -> None:
    limits = adapter.capabilities().limits
    assert limits == ALGO_LIMITS
    joined = " ".join(limits)
    assert "预测方案" in joined and "不是仿真结论" in joined


def test_cancel_marks_cancelled(adapter: LumenPlannerAdapter) -> None:
    job_id = adapter.submit(_task())
    adapter.cancel(job_id)
    assert adapter.status(job_id).status is JobStatus.CANCELLED


def test_failure_is_reported_not_raised(adapter: LumenPlannerAdapter) -> None:
    flat = [Point(x=0, y=0), Point(x=4, y=0), Point(x=4, y=3)]
    bad = TaskSpec(kind="layout", spaces=[_room(height=None)], extra={"flux": FLUX})
    assert flat  # 构造用，确保上面的轮廓写了
    job_id = adapter.submit(bad)
    progress = _wait(adapter, job_id)
    assert progress.status is JobStatus.FAILED
    assert "height" in progress.message


def test_apply_plan_carries_fixtures_downstream() -> None:
    """apply_plan 是流水线的接缝：算法层的方案要能原样变成下游的输入。"""
    task = _task()
    plan = plan_layout(task)
    fed = apply_plan(task, plan)
    assert len(fed.fixtures) == len(plan.fixtures)
    assert fed.spaces == task.spaces
    assert fed.constraints == task.constraints
    for f in fed.fixtures:
        assert f.properties["flux"] == pytest.approx(FLUX)


def test_algorithm_plan_feeds_the_dialux_file_layer(tmp_path: Path) -> None:
    """端到端「先算后验」：算法层出方案 → DIALux 文件层出可导入的 STF。

    这条是 P2 与 P1 的接缝，也是整个平台目前能跑通的最长链路。
    """
    planner = LumenPlannerAdapter(output_dir=tmp_path / "plans")
    exporter = DialuxAdapter(output_dir=tmp_path / "stf")
    registry = AdapterRegistry()
    registry.register(planner)
    registry.register(exporter)
    dispatcher = Dispatcher(registry, project=Project(id="p2", name="先算后验"))

    task = _task()
    plan_job = dispatcher.dispatch(task)
    plan_result = dispatcher.wait(plan_job, timeout=30.0)
    assert plan_result.metric("uniformity_u0") >= 0.6

    # 把方案套回 TaskSpec 再交给文件层 —— 用的是同一份空间与约束
    planned_task = apply_plan(task, plan_layout(task))
    stf_job = exporter.submit(planned_task)
    stf_progress = _wait(exporter, stf_job)
    assert stf_progress.status is JobStatus.DONE, stf_progress.message
    stf_result = exporter.result(stf_job)

    text = Path(stf_result.artifacts["stf"]).read_text(encoding="utf-8")
    assert text.startswith("[VERSION]")
    assert "[ROOM.R1]" in text
    # 算法层规划的每一盏灯都要真的写进 STF，不能中途掉队
    n_lums = sum(1 for line in text.splitlines() if line.startswith("Lum") and ".Pos=" in line)
    assert n_lums == int(plan_result.metric("fixture_count"))
    assert stf_result.metric("fixtures") == float(n_lums)
