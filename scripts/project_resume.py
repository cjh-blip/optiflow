"""P0-G 验收：项目文件能恢复，并且能接着跑。

必须跨进程——同一进程里内存对象还在，读回等于自己跟自己握手。所以编排两个
子进程：A 跑一趟并存盘，B 另起一个进程读回来，C 接着跑再存。

跑法（仓库根目录）：python scripts/project_resume.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec  # noqa: E402
from optiflow.project_store import project_path  # noqa: E402
from optiflow.service import build_default_service  # noqa: E402


def _task(target_lux: float = 400.0) -> TaskSpec:
    # 目标 400：208 那间屋（97.66 m²）flux 3000 实测算出 406.7lx，能过校验；
    # 用 500 会在 plan 步就不达标，版本数钉不住。
    return TaskSpec(
        kind="layout",
        spaces=[Space(
            id="r1", name="208 会议室",
            geometry=Geometry(kind="room", height=4.0, outline=[
                Point(x=0, y=0), Point(x=11.9, y=0),
                Point(x=11.9, y=8.78), Point(x=0, y=8.78)]),
            work_plane=0.75,
        )],
        constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
        extra={"flux": 3000.0, "fixture_name": "验收灯"},
    )


def _wait(service, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = service.status(job_id)
        if snap["status"] in ("done", "failed", "cancelled"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没结束")


def _summarize(project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "inputs": len(project.inputs),
        "results": len(project.results),
        "versions": len(project.versions),
        "artifacts": sorted(project.artifacts),
        "version_notes": [v.note for v in project.versions],
        "version_metrics": [sorted(v.metrics_summary) for v in project.versions],
    }


def role_run(home: Path) -> None:
    """A：跑一趟流水线，存成项目。"""
    service = build_default_service(output_root=home)
    job_id = service.submit("lighting_plan", _task())
    snap = _wait(service, job_id)
    path = project_path(home, service.project.id)
    print(f"  跑完 job={job_id} status={snap['status']} stage={snap.get('stage')}")
    print(f"  项目文件    : {path.name}（{path.stat().st_size} 字节）")
    print(f"  内存里      : {json.dumps(_summarize(service.project), ensure_ascii=False)}")


def role_read(home: Path) -> None:
    """B：另起进程读回，逐项打印。"""
    service = build_default_service(output_root=home)   # 构造时就会从磁盘读回
    project = service.load_project("default")
    print(f"  读回类型    : {type(project).__name__}")
    print(f"  id / name   : {project.id} / {project.name}")
    print(f"  created_at  : {project.created_at.isoformat()}（{type(project.created_at).__name__}）")
    print(f"  inputs      : {len(project.inputs)} 条，目标 "
          f"{project.inputs[0].constraints[0].target} lx，房间 {len(project.inputs[0].spaces)}")
    print(f"  results     : {len(project.results)} 条")
    print(f"  versions    : {len(project.versions)} 条")
    for v in project.versions:
        print(f"    v{v.version} note={v.note!r} artifacts={sorted(v.artifacts)} "
              f"metrics={len(v.metrics_summary)} 项")
    print(f"  往返摘要    : {json.dumps(_summarize(project), ensure_ascii=False)}")


def role_resume(home: Path) -> None:
    """C：接着跑，版本应该是追加。"""
    service = build_default_service(output_root=home)
    before = len(service.project.versions)
    job_id = service.resume_project("lighting_plan")
    snap = _wait(service, job_id)
    after = len(service.project.versions)
    print(f"  resume 前版本: {before}")
    print(f"  跑完 job={job_id} status={snap['status']}")
    print(f"  resume 后版本: {after}（新增 {after - before}）")
    print(f"  inputs 数量  : {len(service.project.inputs)}（应仍为 1：原始输入只进不回）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P0-G 项目文件恢复验收")
    ap.add_argument("--role", choices=["a", "b", "c"], help="内部用：子进程角色")
    ap.add_argument("--home", help="业务根（内部用）")
    args = ap.parse_args(argv)

    if args.role:
        home = Path(args.home)
        {"a": role_run, "b": role_read, "c": role_resume}[args.role](home)
        return 0

    home = Path(tempfile.mkdtemp(prefix="gs_proj_resume_"))
    print(f"业务根: {home}\n")

    def child(role: str) -> None:
        cp = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--role", role,
             "--home", str(home)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(cp.stdout.rstrip())
        if cp.returncode != 0:
            print(cp.stderr.rstrip()[-1500:])
            raise SystemExit(f"子进程 {role} 失败，退出码 {cp.returncode}")

    print("① 子进程 A：跑一趟流水线并存项目")
    child("a")
    print("\n② 子进程 B：另起进程读回（内存里没有它）")
    child("b")
    print("\n③ 子进程 C：另起进程接着跑")
    child("c")

    path = project_path(home, "default")
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n④ 最终项目文件: {path.name}（{path.stat().st_size} 字节）")
    print(f"   schema_version={doc['schema_version']} "
          f"inputs={len(doc['inputs'])} results={len(doc['derived']['results'])} "
          f"versions={len(doc['derived']['versions'])}")
    print("\n验收结论：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
