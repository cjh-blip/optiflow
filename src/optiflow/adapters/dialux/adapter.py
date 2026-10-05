"""DialuxAdapter —— DIALux 的**文件层**适配器（本步只做 STF，不驱动真机 UI）。

职责边界（对应 capabilities().limits）：
- 做：TaskSpec → DIALux IR → .stf 文件（DIALux evo 可导入的房间几何 + 灯具段）
- 不做：驱动 DIALux 真机（导入 .stf、排布灯具、出报告）——那是 UI 通道，下一步单独做
- 不做：照度计算。平台这一步不产出 .evo，也不假装算过（result.metrics 里没有照度）

异步契约：submit 立刻返回 job_id，导出在后台线程里跑；status 查进度，result 取产物，
cancel 置取消位。与 FakeAdapter 形状一致，dispatcher 可互换。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...adapter import CapabilityDecl
from ...ir import Metric, TaskSpec
from ...job import JobStatus, Progress, ResultSet
from .stf import write_stf

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("build") / "dialux"


def task_to_dialux_ir(task: TaskSpec, *, project_name: str) -> Dict[str, Any]:
    """平台的 TaskSpec → DIALux 侧 IR dict（stf.py 的输入契约）。

    只搬共享语义：房间环（XY）、净高、灯具位置。平台 IR 的 Point.z 对房间地板无意义
    （DIALux 地板固定 Z=0），灯具 Z 取显式值，缺省落到净高。
    """
    from ...geometry import point_in_polygon

    spaces: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for space in task.spaces:
        if space.geometry.kind != "room":
            skipped.append(f"{space.id}: geometry.kind={space.geometry.kind}，非房间，不导出 STF 房间")
            continue
        polygon = [[p.x, p.y] for p in space.geometry.outline]
        if len(polygon) < 3:
            raise ValueError(f"空间 {space.id!r} 的轮廓不足 3 点（{len(polygon)}），无法生成房间")
        if space.geometry.height is None:
            raise ValueError(f"空间 {space.id!r} 缺 geometry.height，STF 需要净高（ceil_h）")
        spaces.append({
            "id": space.id,
            "name": space.name or space.id,
            "polygon": polygon,
            "ceil_h": float(space.geometry.height),
            "luminaires": [],
        })
    for msg in skipped:
        logger.warning("跳过非房间空间：%s", msg)

    if not spaces:
        raise ValueError("TaskSpec 里没有可导出的房间（spaces 为空或全部非 room）")

    unplaced: List[str] = []
    for fixture in task.fixtures:
        target = None
        for space_ir in spaces:
            if point_in_polygon(fixture.position.x, fixture.position.y,
                                space_ir["polygon"]):
                target = space_ir
                break
        if target is None:
            unplaced.append(fixture.id)
            continue
        z = fixture.position.z or target["ceil_h"]
        target["luminaires"].append({
            "symbol": fixture.name or fixture.id,
            "x": fixture.position.x,
            "y": fixture.position.y,
            "z": z,
            "catalog_match": True,
            "kind": "point",
        })
    if unplaced:
        logger.warning("灯具不在任何房间轮廓内，未写入 STF：%s", unplaced)

    return {
        "schema_version": "0.1",
        "project": {"name": project_name, "source": {"dwg": ""}, "units": "m"},
        "storeys": [{"level": 1, "elevation": 0.0, "spaces": spaces}],
    }


class DialuxAdapter:
    """DIALux 文件层适配器：TaskSpec → .stf（异步）。"""

    name = "dialux"

    def __init__(self, output_dir: Optional[Path | str] = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 能力声明
    def capabilities(self) -> CapabilityDecl:
        return CapabilityDecl(
            name=self.name,
            kinds=["layout"],
            tags=["file"],
            limits=[
                "只产出 STF 文件：灯具以占位符形式保留（真机实测 36/36），可在 DIALux UI 中批量替换为真灯并参与照度计算",
                "本步不驱动真机：不启动 DIALux、不导入 STF、不触发出报告，导入效果未经真机验证",
                "不产出 .evo，也不做照度计算：结果里没有照度类指标，别当作仿真结论",
            ],
            fragile=False,
        )

    # ------------------------------------------------------------ 提交
    def submit(self, task: TaskSpec) -> str:
        """立刻返回 job_id，导出在后台线程里跑。"""
        job_id = f"dialux-{uuid.uuid4().hex[:8]}"
        state: Dict[str, Any] = {
            "task": task,
            "status": JobStatus.QUEUED,
            "percent": 0.0,
            "message": "已排队",
            "cancelled": False,
            "result": None,
            "error": None,
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
            state["percent"] = 10.0
            state["message"] = "IR → DIALux IR"

            task: TaskSpec = state["task"]
            self.output_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.output_dir / f"{job_id}.stf"
            dialux_ir = task_to_dialux_ir(task, project_name=f"optiflow_{job_id}")

            if state["cancelled"]:
                return
            state["percent"] = 50.0
            state["message"] = "写 STF"
            write_stf(dialux_ir, out_path)

            if state["cancelled"]:
                return
            state["percent"] = 100.0
            state["result"] = self._build_result(job_id, task, dialux_ir, out_path)
            state["message"] = f"STF 已生成：{out_path.name}"
            state["status"] = JobStatus.DONE
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常吞进黑洞
            logger.exception("DIALux 导出失败（%s）", job_id)
            state["error"] = f"{type(exc).__name__}: {exc}"
            state["message"] = state["error"]
            state["status"] = JobStatus.FAILED

    def _build_result(self, job_id: str, task: TaskSpec, dialux_ir: Dict[str, Any],
                      out_path: Path) -> ResultSet:
        spaces = dialux_ir["storeys"][0]["spaces"]
        lum_count = sum(len(s["luminaires"]) for s in spaces)
        metrics = [
            Metric(name="rooms", value=float(len(spaces)), unit="间"),
            Metric(name="fixtures", value=float(lum_count), unit="盏"),
            Metric(name="area", value=round(sum(s.area for s in task.spaces), 2), unit="m2"),
            Metric(name="stf_bytes", value=float(out_path.stat().st_size), unit="B"),
        ]
        return ResultSet(
            job_id=job_id,
            metrics=metrics,
            artifacts={"stf": str(out_path)},
            raw={
                "channel": "file",
                "note": "只产出 STF 文件，未驱动 DIALux，未计算照度",
                "luminaires_in_stf": lum_count,
            },
        )

    # ------------------------------------------------------------ 查询 / 取消
    def status(self, job_id: str) -> Progress:
        state = self._jobs[job_id]
        if state["cancelled"] and state["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
            return Progress(job_id=job_id, status=JobStatus.CANCELLED, percent=state["percent"],
                            message="已取消")
        return Progress(job_id=job_id, status=state["status"], percent=state["percent"],
                        message=state["message"])

    def result(self, job_id: str) -> ResultSet:
        state = self._jobs[job_id]
        if state["status"] is JobStatus.FAILED:
            raise RuntimeError(f"任务 {job_id} 失败：{state['error']}")
        if state["result"] is None:
            raise RuntimeError(f"任务 {job_id} 尚未完成（{state['status'].value}）")  # noqa: Q000
        return state["result"]

    def cancel(self, job_id: str) -> None:
        state = self._jobs[job_id]
        state["cancelled"] = True
        if state["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
            state["status"] = JobStatus.CANCELLED
            state["message"] = "已取消"


__all__ = ["DialuxAdapter", "task_to_dialux_ir"]
