"""FakeAdapter —— 验证契约用的假适配器，不接任何真软件。

它存在的意义是让 P0 的端到端链路（提交 → 进度 → 结果）能独立于真机跑通。
内部用光通量法算一个模拟布灯数，只是让结果不是空壳，不代表任何真软件行为。
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from ..adapter import CapabilityDecl
from ..ir import Metric, TaskSpec
from ..job import JobStatus, Progress, ResultSet


class FakeAdapter:
    def __init__(self, duration: float = 0.25) -> None:
        self.duration = duration
        self._jobs: dict[str, dict[str, Any]] = {}

    def capabilities(self) -> CapabilityDecl:
        return CapabilityDecl(
            name="fake",
            kinds=["layout", "calculate"],
            tags=["simulation"],
            limits=[
                "结果为光通量法模拟值，未接任何真软件",
                "不做 UI 执行、不做文件导入导出",
            ],
        )

    def submit(self, task: TaskSpec) -> str:
        job_id = f"fake-{uuid.uuid4().hex[:8]}"
        self._jobs[job_id] = {"task": task, "started": time.monotonic(), "cancelled": False}
        return job_id

    def status(self, job_id: str) -> Progress:
        job = self._jobs[job_id]
        if job["cancelled"]:
            return Progress(job_id=job_id, status=JobStatus.CANCELLED, message="已取消")
        elapsed = time.monotonic() - job["started"]
        percent = min(100.0, elapsed / self.duration * 100.0)
        if percent >= 100.0:
            return Progress(job_id=job_id, status=JobStatus.DONE, percent=100.0, message="模拟完成")
        return Progress(
            job_id=job_id,
            status=JobStatus.RUNNING,
            percent=round(percent, 1),
            message="模拟计算中",
        )

    def result(self, job_id: str) -> ResultSet:
        task: TaskSpec = self._jobs[job_id]["task"]
        area = sum(space.area for space in task.spaces)
        target = task.target_of("illuminance_avg") or 500.0

        flux = 3000.0
        for fixture in task.fixtures:
            if "flux" in fixture.properties:
                flux = fixture.properties["flux"]
                break

        uf = float(task.extra.get("utilization_factor", 0.70))
        mf = float(task.extra.get("maintenance_factor", 0.80))

        count = math.ceil(target * area / (flux * uf * mf)) if area > 0 else 0
        achieved = count * flux * uf * mf / area if area > 0 else 0.0

        metrics = [
            Metric(name="fixture_count", value=float(count), unit="盏"),
            Metric(name="illuminance_avg", value=round(achieved, 1), unit="lx", target=target),
            Metric(name="area", value=round(area, 2), unit="m2"),
        ]
        return ResultSet(
            job_id=job_id,
            metrics=metrics,
            artifacts={"report": f"(fake) {job_id}_report.json"},
            raw={"method": "lumen", "note": "模拟值，非真软件结果"},
        )

    def cancel(self, job_id: str) -> None:
        self._jobs[job_id]["cancelled"] = True
