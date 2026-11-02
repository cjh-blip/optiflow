"""任务句柄落盘：进程死了 job 不能跟着消失。

核心主张只有一条：**上次没跑完的任务，重启后要查得到，且标成 interrupted。**
让它消失才是撒谎。其余都是这条的配套（存什么形状、谁的 pid 算自己人）。
"""
from __future__ import annotations

import os
import time

from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job_store import INTERRUPTED, JobStore
from optiflow.registry import AdapterRegistry
from optiflow.service import PlatformService


def _record(job_id: str = "job-x", *, status: str = "running",
            pid: int = 999_999, **overrides):
    record = {
        "job_id": job_id,
        "pipeline": "lighting_plan",
        "status": status,
        "stage": "alpha",
        "percent": 5.3,
        "message": "正在跑步骤 alpha（1/2）",
        "steps": [],
        "pid": pid,
        "started_wall": 1_700_000_000.0,
        "finished_wall": None,
        "error": None,
    }
    record.update(overrides)
    return record


def _task() -> TaskSpec:
    outline = [Point(x=0, y=0), Point(x=10, y=0), Point(x=10, y=8), Point(x=0, y=8)]
    return TaskSpec(
        kind="layout",
        spaces=[Space(id="r1", name="房间",
                      geometry=Geometry(kind="room", outline=outline, height=3.0))],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"flux": 3000.0},
    )


# ---------------------------------------------------------------- 落盘形状

def test_snapshot_is_status_shaped(tmp_path) -> None:
    store = JobStore(tmp_path)
    store.save_record(_record())
    snap = store.snapshot("job-x")

    assert set(snap) >= {"job_id", "pipeline", "status", "stage",
                         "percent", "message", "steps"}
    assert snap["status"] == "running"
    assert snap["stage"] == "alpha"
    assert snap["percent"] == 5.3
    assert snap["steps"] == []
    # 没有 finished_wall 时 elapsed 用当前时间推，不抛
    assert isinstance(snap["elapsed"], (int, float))


def test_snapshot_unknown_job_is_none(tmp_path) -> None:
    assert JobStore(tmp_path).snapshot("nope") is None


def test_job_id_path_traversal_rejected(tmp_path) -> None:
    store = JobStore(tmp_path)
    for bad in ("../evil", "a/b", "a\\b", "c:", ""):
        try:
            store.path_of(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 没被拒绝")


# ---------------------------------------------------------------- 重启认账

def test_reap_marks_other_pid_interrupted(tmp_path) -> None:
    store = JobStore(tmp_path)
    store.save_record(_record(pid=999_999))

    touched = store.reap_interrupted(pid=os.getpid())
    assert touched == ["job-x"]
    snap = store.snapshot("job-x")
    assert snap["status"] == INTERRUPTED
    assert snap["stage"] == INTERRUPTED
    assert "中断" in snap["message"]


def test_reap_skips_own_pid(tmp_path) -> None:
    """本进程正在跑的任务不能被自己认成中断。"""
    store = JobStore(tmp_path)
    store.save_record(_record(pid=os.getpid()))
    assert store.reap_interrupted() == []
    assert store.snapshot("job-x")["status"] == "running"


def test_reap_skips_terminal_records(tmp_path) -> None:
    store = JobStore(tmp_path)
    store.save_record(_record(job_id="job-done", status="done", pid=999_999))
    store.save_record(_record(job_id="job-fail", status="failed", pid=999_999))

    assert store.reap_interrupted() == []
    assert store.snapshot("job-done")["status"] == "done"
    assert store.snapshot("job-fail")["status"] == "failed"


# ---------------------------------------------------------------- 重启后查得到

def test_status_reads_disk_after_restart(tmp_path) -> None:
    """上一个进程留下的 running 记录：新实例起来要查得到，不是「未知任务」。"""
    JobStore(tmp_path).save_record(_record(pid=999_999))

    service = PlatformService(AdapterRegistry(), project=None, state_root=tmp_path)
    assert "job-x" in service.interrupted_jobs

    snap = service.status("job-x")
    assert snap["job_id"] == "job-x"
    assert snap["status"] == INTERRUPTED
    assert snap["stage"] == INTERRUPTED


def test_no_state_root_means_pure_memory(tmp_path) -> None:
    """没给 state_root 就不落盘——测试与临时调用不吃这个副作用。"""
    service = PlatformService(AdapterRegistry(), project=None)
    assert service.job_store is None
    assert service.interrupted_jobs == []
    assert not (tmp_path / "jobs").exists()


def test_running_job_gets_written_during_run(tmp_path, monkeypatch) -> None:
    """真跑一条流水线，盘上要留下记录，且收尾是 done。"""
    import optiflow.service as svc
    from optiflow.adapters.fake import FakeAdapter
    from optiflow.orchestrator import Pipeline, Step

    monkeypatch.setattr(svc, "get_pipeline", lambda name, **kw: Pipeline(
        goal="单步", steps=[Step(name="alpha", kind="layout",
                                 requires=("simulation",),
                                 build=lambda ctx: ctx.task)]))
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=0.2))
    service = PlatformService(registry, project=None, timeout=10.0,
                              state_root=tmp_path)

    job_id = service.submit("lighting_plan", _task())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if service.status(job_id)["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.01)

    record = JobStore(tmp_path).load_record(job_id)
    assert record is not None, "跑完的任务在盘上没留下记录"
    assert record["status"] == "done"
    assert record["stage"] == "done"
    assert record["percent"] == 100.0
    assert record["pipeline"] == "lighting_plan"
    assert record["steps"], "记录里没有步骤"
