"""P1 验收：DIALux 文件层适配器（DialuxAdapter）接进平台契约。

钉死四条：
1. dispatcher 靠 capabilities() 选中它（kind="layout"），不是硬编码；
2. submit 是异步的——立刻返回 job_id，不阻塞调用方；
3. result 给 Metric 列表 + artifacts（真实存在的 .stf 路径），且 STF 内容可校验；
4. 能力声明诚实：limits 写明「灯具段被忽略、落灯要 UI 通道」与「本步不驱动真机」。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from optiflow.adapters.dialux import DialuxAdapter
from optiflow.dispatcher import Dispatcher
from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import JobStatus
from optiflow.project import Project
from optiflow.registry import AdapterRegistry


def _task() -> TaskSpec:
    outline = [Point(x=0.0, y=0.0), Point(x=9.57, y=0.0), Point(x=9.57, y=12.97), Point(x=0.0, y=12.97)]
    room = Space(
        id="room-1",
        name="会议室",
        geometry=Geometry(kind="room", outline=outline, height=3.0),
        work_plane=0.75,
    )
    return TaskSpec(
        kind="layout",
        spaces=[room],
        fixtures=[Fixture(id="L1", name="筒灯", position=Point(x=1.0, y=1.0, z=3.0))],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
    )


@pytest.fixture()
def adapter(tmp_path: Path) -> DialuxAdapter:
    return DialuxAdapter(output_dir=tmp_path)


# ---------------------------------------------------------------- 能力路由

def test_dispatcher_picks_dialux_adapter_by_capability(adapter: DialuxAdapter) -> None:
    """注册表里只放 DialuxAdapter，kind="layout" 必须命中它。"""
    registry = AdapterRegistry()
    registry.register(adapter)
    selected = registry.find("layout")
    assert selected is adapter
    assert selected.capabilities().name == "dialux"


def test_dispatcher_routes_through_registry_not_hardcoded(adapter: DialuxAdapter) -> None:
    """真的经过 registry.find：端到端能提交到 DialuxAdapter 并拿到它的产物。"""
    registry = AdapterRegistry()
    registry.register(adapter)
    dispatcher = Dispatcher(registry, project=Project(id="p1", name="P1 验收"))
    job_id = dispatcher.dispatch(_task())
    assert job_id.startswith("dialux-")
    result = dispatcher.wait(job_id, timeout=10.0)
    assert Path(result.artifacts["stf"]).exists()


def test_dialux_adapter_declares_only_layout_kind(adapter: DialuxAdapter) -> None:
    assert adapter.capabilities().kinds == ["layout"]


def test_capabilities_limits_name_the_ui_gap_and_the_no_real_machine_scope(
        adapter: DialuxAdapter) -> None:
    """limits 必须至少两条，且点名「灯具段被忽略/落灯要 UI 通道」与「本步不驱动真机」。"""
    limits = adapter.capabilities().limits
    assert len(limits) >= 2, limits
    joined = " ".join(limits)
    assert "灯具段" in joined and "UI 通道" in joined, limits
    assert "不驱动真机" in joined, limits


# ---------------------------------------------------------------- 异步契约

def test_submit_returns_immediately(adapter: DialuxAdapter) -> None:
    """submit 不许阻塞：即便导出很慢，也必须立刻拿到 job_id。"""
    started = time.monotonic()
    job_id = adapter.submit(_task())
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"submit 阻塞了 {elapsed:.3f}s，异步契约被破坏"
    assert job_id


def test_polling_progress_reaches_done(adapter: DialuxAdapter) -> None:
    job_id = adapter.submit(_task())
    deadline = time.monotonic() + 10.0
    while adapter.status(job_id).status is not JobStatus.DONE:
        assert time.monotonic() < deadline, "导出超时"
        time.sleep(0.02)
    progress = adapter.status(job_id)
    assert progress.percent == 100.0


def test_result_before_done_raises(adapter: DialuxAdapter) -> None:
    """没跑完就取结果必须报错，不能返回半成品。"""
    job_id = adapter.submit(_task())
    adapter.cancel(job_id)
    with pytest.raises(RuntimeError):
        adapter.result(job_id)


def test_cancel_marks_job_cancelled(adapter: DialuxAdapter) -> None:
    job_id = adapter.submit(_task())
    adapter.cancel(job_id)
    assert adapter.status(job_id).status is JobStatus.CANCELLED


# ---------------------------------------------------------------- 结果与产物

def test_result_carries_metrics_and_stf_artifact(adapter: DialuxAdapter) -> None:
    job_id = adapter.submit(_task())
    deadline = time.monotonic() + 10.0
    while adapter.status(job_id).status is not JobStatus.DONE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    result = adapter.result(job_id)
    names = {m.name for m in result.metrics}
    assert {"rooms", "fixtures", "area", "stf_bytes"} <= names
    assert result.metric("rooms") == 1.0
    assert result.metric("fixtures") == 1.0
    assert result.metric("area") == pytest.approx(round(9.57 * 12.97, 2))
    stf = Path(result.artifacts["stf"])
    assert stf.exists() and stf.suffix == ".stf"
    assert result.metric("stf_bytes") == float(stf.stat().st_size)


def test_stf_artifact_is_importable_dialux_text(adapter: DialuxAdapter) -> None:
    """产物是真的 STF：段头齐全、房间闭合、灯具段写出。"""
    job_id = adapter.submit(_task())
    deadline = time.monotonic() + 10.0
    while adapter.status(job_id).status is not JobStatus.DONE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    text = Path(adapter.result(job_id).artifacts["stf"]).read_text(encoding="utf-8")
    assert text.startswith("[VERSION]\nSTFF=1.0\n")
    assert "[ROOM.R1]" in text and "[ROOM.R2]" not in text
    assert "Height=3" in text
    assert "Lum1.Pos=1 1 3" in text, text
    assert b"\r\n" not in Path(adapter.result(job_id).artifacts["stf"]).read_bytes()


def test_no_illuminance_metric_is_faked(adapter: DialuxAdapter) -> None:
    """本步不驱动真机、不做照度计算：结果里不许出现照度指标（假承诺）。"""
    job_id = adapter.submit(_task())
    deadline = time.monotonic() + 10.0
    while adapter.status(job_id).status is not JobStatus.DONE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    result = adapter.result(job_id)
    assert all("illuminance" not in m.name for m in result.metrics)
    assert result.raw["channel"] == "file"


def test_invalid_task_fails_job_instead_of_raising(adapter: DialuxAdapter) -> None:
    """缺净高的房间：任务落到 FAILED 并带原因，而不是把异常抛回调用方。"""
    bad = TaskSpec(kind="layout", spaces=[Space(
        id="r1", name="没净高",
        geometry=Geometry(kind="room", outline=[Point(x=0, y=0), Point(x=4, y=0), Point(x=4, y=3)]),
    )])
    job_id = adapter.submit(bad)
    deadline = time.monotonic() + 10.0
    while adapter.status(job_id).status not in (JobStatus.DONE, JobStatus.FAILED):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    progress = adapter.status(job_id)
    assert progress.status is JobStatus.FAILED
    assert "height" in progress.message

