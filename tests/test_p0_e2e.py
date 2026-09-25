"""P0 验收：假适配器端到端 + 按能力选适配器 + 能力声明诚实。"""

from __future__ import annotations

import pytest

from optiflow.adapters.fake import FakeAdapter
from optiflow.dispatcher import AdapterNotFound, Dispatcher
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import JobStatus
from optiflow.project import Project
from optiflow.registry import AdapterRegistry


@pytest.fixture()
def dispatcher() -> Dispatcher:
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=0.15))
    return Dispatcher(registry, project=Project(id="t", name="测试项目"))


def _task(kind: str = "layout") -> TaskSpec:
    outline = [Point(x=0, y=0), Point(x=10, y=0), Point(x=10, y=8), Point(x=0, y=8)]
    room = Space(id="r1", name="测试房间", geometry=Geometry(kind="room", outline=outline, height=3.0))
    return TaskSpec(
        kind=kind,
        spaces=[room],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"utilization_factor": 0.7, "maintenance_factor": 0.8},
    )


def test_dispatch_picks_adapter_by_capability(dispatcher: Dispatcher) -> None:
    """dispatcher 靠 capabilities() 选适配器，不是硬编码。"""
    assert dispatcher.registry.find("layout") is not None
    assert dispatcher.registry.find("unsupported-kind") is None


def test_unknown_kind_raises(dispatcher: Dispatcher) -> None:
    with pytest.raises(AdapterNotFound) as excinfo:
        dispatcher.dispatch(_task("unsupported-kind"))
    assert "unsupported-kind" in str(excinfo.value)


def test_submit_status_result_end_to_end(dispatcher: Dispatcher) -> None:
    job_id = dispatcher.dispatch(_task())

    first = dispatcher.progress(job_id)
    assert first.status in (JobStatus.RUNNING, JobStatus.DONE)

    result = dispatcher.wait(job_id, timeout=5.0)
    assert result.job_id == job_id
    assert result.metric("fixture_count") is not None
    assert result.metric("fixture_count") > 0
    assert result.metric("area") == pytest.approx(80.0)
    # 平均照度应当落在目标附近（光通量法取整布灯的常态）
    achieved = result.metric("illuminance_avg")
    assert achieved is not None and achieved >= 500.0
    assert result.artifacts


def test_result_can_be_archived_as_project_version(dispatcher: Dispatcher) -> None:
    job_id = dispatcher.dispatch(_task())
    dispatcher.wait(job_id, timeout=5.0)
    version = dispatcher.archive(job_id, note="首版")
    assert version.version == 1
    assert version.metrics_summary["illuminance_avg"] >= 500.0
    assert dispatcher.project is not None
    assert len(dispatcher.project.versions) == 1


def test_capabilities_declare_limits() -> None:
    """能力声明必须诚实：做不了的事写进 limits。"""
    decl = FakeAdapter().capabilities()
    assert "layout" in decl.kinds
    assert decl.limits, "limits 不能为空——不声明限制的能力声明是虚假承诺"


def test_cancel_marks_job_cancelled() -> None:
    adapter = FakeAdapter(duration=10.0)
    job_id = adapter.submit(_task())
    adapter.cancel(job_id)
    assert adapter.status(job_id).status is JobStatus.CANCELLED
