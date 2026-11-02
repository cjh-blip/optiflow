"""进度可见性验收：把「运行中的任务到底能拿到什么」打出来。

用法（仓库根目录）：

    python scripts/show_progress.py

两段输出：

① 真流水线 lighting_plan（算法层出方案 → 文件层落 STF）
   证明 status() 里的 steps 是真数据：有适配器名、有指标、有产物、有校验。

② 慢速两步流水线（FakeAdapter 每步 0.4s）
   证明 percent 序列与 stage 变化点：起点 5、微增、封顶 95、完成 100。

为什么分两段：真流水线两步都是毫秒级，中间态窗口比轮询间隔还短，
用它证明「能采到中间态」是自欺。中间态用慢适配器证明，真数据用真流水线证明。
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import optiflow.service as svc  # noqa: E402
from optiflow.adapters.fake import FakeAdapter  # noqa: E402
from optiflow.demo import build_meeting_room_task  # noqa: E402
from optiflow.orchestrator import Pipeline, Step  # noqa: E402
from optiflow.registry import AdapterRegistry  # noqa: E402
from optiflow.service import PlatformService, build_default_service  # noqa: E402

POLL_SECONDS = 0.02


@dataclass
class Sample:
    status: str
    stage: str
    percent: float
    steps: int


def _poll(service: PlatformService, job_id: str, limit: float = 60.0) -> list[Sample]:
    out: list[Sample] = []
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        snap = service.status(job_id)
        out.append(Sample(snap["status"], snap["stage"], snap["percent"],
                          len(snap["steps"])))
        if snap["status"] in ("done", "failed", "cancelled"):
            return out
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"任务 {job_id} 没在 {limit}s 内收尾")


def _print_samples(samples: list[Sample]) -> None:
    seq = [s.percent for s in samples]
    monotonic = all(b >= a for a, b in zip(seq, seq[1:]))
    changes: list[str] = []
    for prev, cur in zip(samples, samples[1:]):
        if cur.stage != prev.stage:
            changes.append(f"{prev.stage} -> {cur.stage} @ {cur.percent}%")

    print(f"  采样点      : {len(samples)}")
    print(f"  percent 序列: {_compress(seq)}")
    print(f"  单调不减    : {monotonic}")
    print(f"  stage 变化点: {'; '.join(changes) if changes else '（无变化）'}")
    print(f"  终态        : {samples[-1].status} / stage={samples[-1].stage} / "
          f"{samples[-1].percent}%")


def _compress(seq: list[float]) -> str:
    """连续相同的只留一个，避免打出几十个重复值。"""
    out: list[float] = []
    for v in seq:
        if not out or out[-1] != v:
            out.append(v)
    return " -> ".join(f"{v:g}" for v in out)


def section_real_pipeline() -> int:
    print("① 真流水线 lighting_plan（算法层 -> 文件层，真适配器）")
    task = build_meeting_room_task()
    # 光通量是硬信息：算法层拒绝猜（algo/engine.py 那条 raise）。
    # 给一个平板灯的典型值，真流水线才跑得完两步。
    task.extra["flux"] = 3000.0
    task.extra["fixture_name"] = "LED 平板灯 3000lm"
    service = build_default_service(output_root=ROOT / "build" / "progress_demo")
    job_id = service.submit("lighting_plan", task)
    samples = _poll(service, job_id)
    _print_samples(samples)

    final = service.status(job_id)
    print("  steps（status() 原样，raw 已剔除）:")
    print(_indent(json.dumps(final["steps"], ensure_ascii=False, indent=2)))
    if final.get("error"):
        print(f"  error: {final['error']}")
    return 0 if final["status"] == "done" else 1


def section_slow_pipeline() -> int:
    print("\n② 慢速两步流水线（FakeAdapter 每步 0.4s，钉中间态）")

    def two_step(goal: str = "慢速两步") -> Pipeline:
        return Pipeline(goal=goal, steps=[
            Step(name="alpha", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task),
            Step(name="beta", kind="layout", requires=("simulation",),
                 build=lambda ctx: ctx.task),
        ])

    original = svc.get_pipeline
    orig_nudge, orig_advance = svc._nudge_percent, svc._advance_percent
    events: list[tuple[str, float]] = []

    def rec_nudge(p: float) -> float:
        value = orig_nudge(p)
        events.append(("步开始微增", value))
        return value

    def rec_advance(p: float, done: int, total: int) -> float:
        value = orig_advance(p, done, total)
        events.append((f"步完成 {done}/{total}", value))
        return value

    svc.get_pipeline = lambda name, **kw: two_step()
    svc._nudge_percent, svc._advance_percent = rec_nudge, rec_advance
    try:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(duration=0.4))
        service = PlatformService(registry, project=None, timeout=10.0)
        job_id = service.submit("lighting_plan", build_meeting_room_task())
        samples = _poll(service, job_id)
        _print_samples(samples)

        running = [s for s in samples if s.status == "running"]
        ceiling = max((s.percent for s in running), default=0.0)
        print(f"  运行中峰值  : {ceiling:g}%（天花板 95，不许越）")
        print(f"  回调级序列  : {' -> '.join(f'{v:g}（{k}）' for k, v in events)}")
        print("                回调级是逻辑真相；采样漏掉的那些值，存活时间短于轮询间隔")

        final = service.status(job_id)
        print(f"  steps 步数  : {len(final['steps'])} -> "
              f"{[s['name'] for s in final['steps']]}")
        return 0 if final["status"] == "done" and final["percent"] == 100.0 else 1
    finally:
        svc.get_pipeline = original
        svc._nudge_percent, svc._advance_percent = orig_nudge, orig_advance


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def main() -> int:
    rc = section_real_pipeline()
    rc |= section_slow_pipeline()
    print(f"\n验收结论：{'通过' if rc == 0 else '不通过'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
