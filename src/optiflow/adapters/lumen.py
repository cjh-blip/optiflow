"""LumenPlannerAdapter —— 算法层接进平台契约（先算后验的「算」那一半）。

它和 DialuxAdapter 都声明 kind="layout"，但角色不同：

- LumenPlannerAdapter：毫秒级出【布灯方案】（灯在哪、预测照度、均匀度）
- DialuxAdapter     ：把方案落成【STF 文件】，再由真机 UI 通道做校核

编排层按「先算后验」把它们串起来（见 optiflow.demo 与 pipeline 一节）。
两者都声明 kind="layout" 是有意的——它们处理的是同一类任务，只是深度不同；
选谁由编排层决定，不是靠把 kind 取个不同的名字来绕开。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..adapter import CapabilityDecl
from ..algo.engine import ALGO_LIMITS, LayoutPlan, plan_layout
from ..ir import Fixture, Metric, Point, TaskSpec
from ..job import JobStatus, Progress, ResultSet

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("build") / "plans"


def apply_plan(task: TaskSpec, plan: LayoutPlan) -> TaskSpec:
    """把算法层算出的方案套回 TaskSpec，供下游（DIALux 文件层）继续用。

    保留原任务的 spaces / constraints，只把 fixtures 换成规划出来的那批——
    这样「先算后验」是一条真的流水线，而不是两份各说各话的数据。
    """
    return TaskSpec(
        kind=task.kind,
        spaces=list(task.spaces),
        fixtures=list(plan.fixtures),
        materials=list(task.materials),
        constraints=list(task.constraints),
        extra=dict(task.extra),
    )


class LumenPlannerAdapter:
    """利用系数法布灯规划器（纯计算，不接触任何外部软件）。"""

    name = "lumen"

    def __init__(self, output_dir: Optional[Path | str] = None, duration: float = 0.0) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        self.duration = duration
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> CapabilityDecl:
        return CapabilityDecl(
            name=self.name,
            kinds=["layout"],
            tags=["algorithm"],
            limits=list(ALGO_LIMITS),
            fragile=False,
        )

    def submit(self, task: TaskSpec) -> str:
        job_id = f"lumen-{uuid.uuid4().hex[:8]}"
        state: Dict[str, Any] = {
            "task": task, "status": JobStatus.QUEUED, "percent": 0.0,
            "message": "已排队", "cancelled": False, "result": None, "error": None,
            "started": time.monotonic(),
        }
        with self._lock:
            self._jobs[job_id] = state
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return job_id

    def _run(self, job_id: str) -> None:
        state = self._jobs[job_id]
        try:
            if state["cancelled"]:
                return
            state["status"] = JobStatus.RUNNING
            state["percent"] = 20.0
            state["message"] = "布灯规划"
            task: TaskSpec = state["task"]
            plan = plan_layout(task, **{
                k: v for k, v in task.extra.get("algo_options", {}).items()
            })
            if state["cancelled"]:
                return
            state["percent"] = 80.0
            state["message"] = "写方案"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            out = self.output_dir / f"{job_id}.json"
            out.write_text(json.dumps({
                "job_id": job_id,
                "metrics": [m.model_dump() for m in plan.metrics],
                "assumptions": plan.assumptions,
                "warnings": plan.warnings,
                "compliant": plan.compliant,
                "fixtures": [f.model_dump() for f in plan.fixtures],
                "rooms": [{
                    "space_id": r.space_id,
                    "count": r.layout.count,
                    "edge_factor": r.edge_factor,
                    "room_index": r.room_index,
                    "utilization_factor": r.utilization_factor,
                    "u0": r.uniformity.u0,
                    "u0_required": r.u0_required,
                    "illuminance_avg": r.predicted_avg_lumen,
                } for r in plan.rooms],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.duration:
                time.sleep(self.duration)
            state["result"] = ResultSet(
                job_id=job_id,
                metrics=list(plan.metrics),
                artifacts={"plan": str(out)},
                raw={
                    "channel": "algorithm",
                    "note": "利用系数法预测方案，非仿真结论；校核请走 DIALux",
                    "assumptions": plan.assumptions,
                    "warnings": plan.warnings,
                    "compliant": plan.compliant,
                },
            )
            state["percent"] = 100.0
            state["message"] = f"方案完成：{plan.metric('fixture_count')} 盏"
            state["status"] = JobStatus.DONE
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常吞进黑洞
            logger.exception("布灯规划失败（%s）", job_id)
            state["error"] = f"{type(exc).__name__}: {exc}"
            state["message"] = state["error"]
            state["status"] = JobStatus.FAILED

    def _state(self, job_id: str) -> Dict[str, Any]:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"未知任务 {job_id}（不由本适配器提交）") from None

    def status(self, job_id: str) -> Progress:
        state = self._state(job_id)
        if state["cancelled"] and state["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
            return Progress(job_id=job_id, status=JobStatus.CANCELLED,
                            percent=state["percent"], message="已取消")
        return Progress(job_id=job_id, status=state["status"],
                        percent=state["percent"], message=state["message"])

    def result(self, job_id: str) -> ResultSet:
        state = self._state(job_id)
        if state["status"] is JobStatus.FAILED:
            raise RuntimeError(f"任务 {job_id} 失败：{state['error']}")
        if state["result"] is None:
            raise RuntimeError(f"任务 {job_id} 尚未完成（{state['status'].value}）")
        return state["result"]

    def cancel(self, job_id: str) -> None:
        state = self._state(job_id)
        state["cancelled"] = True
        if state["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
            state["status"] = JobStatus.CANCELLED
            state["message"] = "已取消"


__all__ = ["LumenPlannerAdapter", "apply_plan"]
