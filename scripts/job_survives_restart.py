"""P0-D 验收：慢 job → 真杀进程 → 重启 → 查得到且 stage=interrupted。

用法（仓库根目录）：

    python scripts/job_survives_restart.py

三个进程，真杀真重启：

  ① 子进程 A 起一个慢任务（每步 30 秒），把 job_id 写到磁盘后挂着不动
  ② 主进程等盘上出现任务记录，然后 ``Popen.kill()`` 把 A 直接掐死
  ③ 子进程 B 用同一个业务根重新起 PlatformService，查那个 job

期望：B 查得到它，status=interrupted、stage=interrupted，不是「未知任务」。

为什么必须真杀进程：在同一进程里把 job 从内存删掉再查，证明不了什么。
这个特性要防的正是「进程整个消失」这件事。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_ROOT = ROOT / "build" / "job_restart"
JOB_MARK = STATE_ROOT / "last_job.txt"


def _task():
    from optiflow.demo import build_meeting_room_task
    task = build_meeting_room_task()
    task.extra["flux"] = 3000.0
    return task


def _slow_service(state_root: Path):
    """每步 30 秒的两步流水线：够长，够被掐。"""
    import optiflow.service as svc
    from optiflow.adapters.fake import FakeAdapter
    from optiflow.orchestrator import Pipeline, Step
    from optiflow.registry import AdapterRegistry
    from optiflow.service import PlatformService

    def two_step(goal: str = "慢速两步") -> Pipeline:
        return Pipeline(goal=goal, steps=[
            Step(name="alpha", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task),
            Step(name="beta", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task),
        ])

    svc.get_pipeline = lambda name, **kw: two_step()
    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=30.0))
    return PlatformService(registry, project=None, timeout=120.0, state_root=state_root)


def do_run(state_root: Path) -> int:
    service = _slow_service(state_root)
    job_id = service.submit("lighting_plan", _task())
    JOB_MARK.parent.mkdir(parents=True, exist_ok=True)
    JOB_MARK.write_text(job_id, encoding="utf-8")
    # 保持存活，等主进程来掐。被 kill 后进程直接消失，不执行任何收尾。
    time.sleep(180)
    return 0


def do_query(state_root: Path, job_id: str) -> int:
    from optiflow.registry import AdapterRegistry
    from optiflow.service import PlatformService

    service = PlatformService(AdapterRegistry(), project=None, timeout=10.0,
                              state_root=state_root)
    print(f"  启动时认成中断的 job : {service.interrupted_jobs}")
    snap = service.status(job_id)
    print(f"  查到的 job_id        : {snap['job_id']}")
    print(f"  status               : {snap['status']}")
    print(f"  stage                : {snap['stage']}")
    print(f"  percent              : {snap['percent']}")
    print(f"  message              : {snap['message']}")
    ok = snap["status"] == "interrupted" and snap["stage"] == "interrupted"
    return 0 if ok else 1


def _spawn(args: list[str]) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.Popen([sys.executable, str(Path(__file__)), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", env=env, cwd=str(ROOT))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["--run"]:
        return do_run(Path(argv[1]))
    if argv[:1] == ["--query"]:
        return do_query(Path(argv[1]), argv[2])

    if STATE_ROOT.exists():
        shutil.rmtree(STATE_ROOT, ignore_errors=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)

    print("① 起慢任务子进程（每步 30s），等它把记录写盘")
    child = _spawn(["--run", str(STATE_ROOT)])
    job_id = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job_files = sorted((STATE_ROOT / "jobs").glob("*.json")) \
            if (STATE_ROOT / "jobs").is_dir() else []
        if job_files:
            job_id = job_files[0].stem
            break
        if child.poll() is not None:
            out, err = child.communicate()
            print(f"  子进程提前退出 rc={child.returncode}\n{out}\n{err[-500:]}")
            return 1
        time.sleep(0.05)
    if job_id is None:
        print("  等了 60s 也没等到任务记录落盘")
        child.kill()
        return 1

    time.sleep(0.8)  # 让它跑进第一步
    record_path = STATE_ROOT / "jobs" / f"{job_id}.json"
    before = json.loads(record_path.read_text(encoding="utf-8"))
    print(f"  掐死前磁盘记录       : status={before['status']} "
          f"stage={before['stage']} percent={before['percent']} pid={before.get('pid')}")

    child.kill()
    child.wait(timeout=30)
    print(f"  已 kill 子进程       : pid={child.pid} rc={child.returncode}")
    after = json.loads(record_path.read_text(encoding="utf-8"))
    print(f"  掐死后磁盘记录       : status={after['status']} stage={after['stage']}"
          f"（进程已死，没人来得及改它）")

    print("\n② 另起进程查这个 job")
    queried = _spawn(["--query", str(STATE_ROOT), job_id])
    out, err = queried.communicate(timeout=120)
    print(out.rstrip() or "(无输出)")
    if err.strip():
        print(f"  stderr: {err[-400:]}")

    print(f"\n验收结论：{'通过' if queried.returncode == 0 else '不通过'}")
    return 0 if queried.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
