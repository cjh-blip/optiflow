"""P0-G：项目文件能恢复，并且能接着跑。

三条要钉住的：
1. 重启后的服务要读回已有项目，不能无脑新建——否则第一次运行就把版本历史覆盖了；
2. 读回来的 Project 带完整 inputs / results / versions；
3. resume 是**追加**版本，不是新建项目。
"""

from __future__ import annotations

import json
import time

import pytest

from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.project import Project
from optiflow.project_store import ProjectFileError, project_path
from optiflow.service import build_default_service


def _task(target_lux: float = 400.0) -> TaskSpec:
    """目标 400：208 那间屋实测算出 406.7lx，能过校验，两步流水线都会归档。

    用 500 的话 plan 步就不达标，只归档一步，版本数没法钉。
    """
    return TaskSpec(
        kind="layout",
        spaces=[Space(
            id="r1", name="会议室",
            geometry=Geometry(kind="room", height=4.0, outline=[
                Point(x=0, y=0), Point(x=11.9, y=0),
                Point(x=11.9, y=8.78), Point(x=0, y=8.78)]),
            work_plane=0.75,
        )],
        constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
        extra={"flux": 3000.0, "fixture_name": "测试灯"},
    )


def _wait(service, job_id: str, timeout: float = 30.0) -> dict:
    """等任务结束。ServiceJob 没有 wait()，调用方自己轮询。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = service.status(job_id)
        if snap["status"] in ("done", "failed", "cancelled"):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没结束")


def _run(service, pipeline: str = "lighting_plan", task: TaskSpec | None = None) -> str:
    job_id = service.submit(pipeline, task or _task())
    snap = _wait(service, job_id)
    assert snap["status"] == "done", (
        f"任务没跑完：status={snap['status']} stage={snap.get('stage')} "
        f"message={snap.get('message')} error={snap.get('error')}")
    return job_id


def test_service_reloads_existing_project_on_restart(tmp_path):
    """重启后的服务必须读回已有项目，否则下一次运行会盖掉版本历史。"""
    first = build_default_service(output_root=tmp_path)
    _run(first)
    versions_before = len(first.project.versions)
    assert versions_before == 2, "两步流水线应该归档两个版本"

    # 模拟重启：同一业务根，全新实例
    second = build_default_service(output_root=tmp_path)
    assert second.project is not None
    assert len(second.project.versions) == versions_before, "重启后版本历史丢了"
    assert len(second.project.inputs) == 1, "重启后原始输入丢了"
    assert len(second.project.results) >= 1


def test_second_run_appends_instead_of_overwriting(tmp_path):
    """重启后再跑一次，是 v2 而不是把 v1 顶掉。"""
    first = build_default_service(output_root=tmp_path)
    _run(first)
    # 模拟重启：同一业务根，全新实例再跑一次
    second = build_default_service(output_root=tmp_path)
    _run(second)

    reloaded = build_default_service(output_root=tmp_path)
    assert len(reloaded.project.versions) == 4, "两次运行应该留下四个版本（每次两步）"


def test_load_project_returns_full_results_and_versions(tmp_path):
    service = build_default_service(output_root=tmp_path)
    _run(service)

    fresh = build_default_service(output_root=tmp_path)
    project = fresh.load_project("default")
    assert project.id == "default"
    assert len(project.inputs) == 1
    assert len(project.results) >= 2
    assert len(project.versions) == 2
    version = project.versions[0]
    assert version.artifacts, "版本里应该有产物路径"
    assert version.metrics_summary.get("illuminance_avg", 0) > 0, "版本里应该有摘要指标"


def test_resume_project_uses_last_input_and_appends_version(tmp_path):
    service = build_default_service(output_root=tmp_path)
    _run(service)
    assert len(service.project.versions) == 2

    job_id = service.resume_project("lighting_plan")
    _wait(service, job_id)
    assert len(service.project.versions) == 4, "resume 应该是追加版本"
    assert service.project.inputs[-1].constraints[0].target == pytest.approx(400.0)


def test_resume_project_without_inputs_raises(tmp_path):
    service = build_default_service(output_root=tmp_path)
    with pytest.raises(ValueError, match="没有原始输入"):
        service.resume_project("lighting_plan")


def test_load_project_without_state_root_raises():
    service = build_default_service()          # 不给业务根
    with pytest.raises(RuntimeError, match="没有业务根"):
        service.load_project("default")


def test_corrupt_project_file_is_not_silently_replaced(tmp_path):
    """文件坏了要炸，不能默默新建一个空项目把它盖了。"""
    path = project_path(tmp_path, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 0, "project": {"id": "default"},
                                "inputs": [], "derived": {}}), encoding="utf-8")
    with pytest.raises(ProjectFileError) as err:
        build_default_service(output_root=tmp_path)
    assert err.value.code == "too_old"


def test_project_id_is_honoured(tmp_path):
    service = build_default_service(output_root=tmp_path, project_id="208")
    _run(service)
    assert project_path(tmp_path, "208").exists()
    fresh = build_default_service(output_root=tmp_path, project_id="208")
    assert isinstance(fresh.project, Project)
    assert len(fresh.project.versions) == 2
