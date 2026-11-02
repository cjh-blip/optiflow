"""P0-C 验收：跑流水线 → 保存项目 → **另起进程**读回 → 字段逐项比对。

用法（仓库根目录）：

    python scripts/project_roundtrip.py

为什么要另起进程：同一个解释器里 save 完再 load，只能证明函数闭环，证明不了
「文件在磁盘上真的能再读回来」。真正的风险从来在跨进程那一侧——字段序列化
丢掉、datetime 变成字符串再变不回来、分节结构没还原。

两件事都验：
1. **逐项比对**：新进程把每个字段打出来，与人眼能核的形式。
2. **往返一致**：新进程把读到的 Project 再存一次，与第一次的文件逐字节比对
   （只放过 saved_at 这一行）。两次写出的内容一致，才说明「读」是无损的。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from optiflow import project_store  # noqa: E402
from optiflow.demo import build_meeting_room_task  # noqa: E402
from optiflow.ir import Metric  # noqa: E402
from optiflow.job import ResultSet  # noqa: E402
from optiflow.project import Project  # noqa: E402
from optiflow.service import build_default_service  # noqa: E402

PROJECT_NAME = "demo-208"


def _task():
    task = build_meeting_room_task()
    # demo 自带任务没有 flux，算法层会拒绝（光通量是硬信息）。补一个平板灯典型值。
    task.extra["flux"] = 3000.0
    task.extra["fixture_name"] = "LED 平板灯 3000lm"
    return task


def _wait(service, job_id, limit: float = 60.0) -> dict:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        snap = service.status(job_id)
        if snap["status"] in ("done", "failed", "cancelled"):
            return snap
        time.sleep(0.02)
    raise SystemExit(f"任务 {job_id} 没在 {limit}s 内收尾")


def do_save(root: Path) -> Path:
    service = build_default_service(output_root=root)
    task = _task()
    project = Project(id=PROJECT_NAME, name="208 会议室照明方案", inputs=[task])

    job_id = service.submit("lighting_plan", task)
    final = _wait(service, job_id)
    if final["status"] != "done":
        raise SystemExit(f"流水线没跑完：{final['status']} / {final.get('message')}")

    payload = service.result(job_id)
    metrics_summary: dict[str, float] = {}
    artifacts: dict[str, str] = {}
    for step in payload["steps"]:
        project.results.append(ResultSet(
            job_id=step["job_id"],
            metrics=[Metric(**m) for m in step["metrics"]],
            artifacts=dict(step["artifacts"]),
        ))
        for m in step["metrics"]:
            if m["value"] is not None:
                metrics_summary[m["name"]] = float(m["value"])
        artifacts.update(step["artifacts"])

    project.add_version(note="首版：算法层布灯 + STF 导出",
                        artifacts=artifacts, metrics_summary=metrics_summary)

    path = project_store.project_path(root, PROJECT_NAME)
    project_store.save(project, path)
    tmp = path.with_name(path.name + ".tmp")
    print(f"  保存路径    : {path}")
    print(f"  文件字节数  : {path.stat().st_size}")
    print(f"  .tmp 是否残留: {tmp.exists()}")
    return path


def do_verify(path: Path) -> int:
    project = project_store.load(path)
    print("  读回（新进程）逐项：")
    print(f"    id            : {project.id}")
    print(f"    name          : {project.name}")
    print(f"    created_at    : {project.created_at.isoformat()}  "
          f"（类型 {type(project.created_at).__name__}）")
    print(f"    inputs        : {len(project.inputs)} 条")
    for i in project.inputs:
        print(f"      - kind={i.kind} spaces={len(i.spaces)} "
              f"fixtures={len(i.fixtures)} extra_keys={sorted(i.extra)}")
    print(f"    artifacts     : {len(project.artifacts)} 项 -> {sorted(project.artifacts)}")
    print(f"    results       : {len(project.results)} 条")
    for r in project.results:
        names = [m.name for m in r.metrics]
        print(f"      - job={r.job_id} metrics={len(r.metrics)} {names[:4]}")
    print(f"    versions      : {len(project.versions)} 条")
    for v in project.versions:
        keys = sorted(v.metrics_summary)[:5]
        print(f"      - v{v.version} note={v.note!r} "
              f"artifacts={len(v.artifacts)} summary_keys={keys}")

    # 往返：把读到的对象再存一次，与原件比对（只放过 saved_at）
    with tempfile.TemporaryDirectory(prefix="gsproj_verify_") as tmpdir:
        again = Path(tmpdir) / path.name
        project_store.save(project, again)
        original_doc = json.loads(path.read_text(encoding="utf-8"))
        again_doc = json.loads(again.read_text(encoding="utf-8"))
        original_doc.pop("saved_at", None)
        again_doc.pop("saved_at", None)
        same = json.dumps(original_doc, sort_keys=True, ensure_ascii=False) == \
            json.dumps(again_doc, sort_keys=True, ensure_ascii=False)
    print(f"  往返一致    : {same}")

    ok = (bool(project.id) and project.inputs and project.results
          and project.versions and same)
    return 0 if ok else 1


def _spawn(args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run([sys.executable, str(Path(__file__)), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, cwd=str(ROOT))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["--save"]:
        do_save(Path(argv[1]))
        return 0
    if argv[:1] == ["--verify"]:
        return do_verify(Path(argv[1]))

    root = ROOT / "build" / "project_roundtrip"
    path = project_store.project_path(root, PROJECT_NAME)
    path.unlink(missing_ok=True)

    print("① 子进程 A：跑流水线并保存项目")
    saved = _spawn(["--save", str(root)])
    print(saved.stdout.rstrip() or f"(无输出) stderr={saved.stderr[-400:]}")
    if saved.returncode != 0:
        print(f"  保存失败，退出码 {saved.returncode}")
        return 1
    if not path.is_file():
        print(f"  期望的项目文件不存在：{path}")
        return 1

    print("\n② 子进程 B：另起进程读回并逐项比对")
    verified = _spawn(["--verify", str(path)])
    print(verified.stdout.rstrip() or f"(无输出) stderr={verified.stderr[-400:]}")

    print(f"\n验收结论：{'通过' if verified.returncode == 0 else '不通过'}")
    return 0 if verified.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
