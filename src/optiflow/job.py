"""异步任务模型：submit → job_id → status → result。

三个软件的单个任务动辄几分钟，所以契约从一开始就是异步的。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .ir import Metric


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Progress(BaseModel):
    job_id: str
    status: JobStatus
    percent: float = 0.0
    message: str = ""
    updated_at: datetime = Field(default_factory=_now)


class ResultSet(BaseModel):
    job_id: str
    metrics: list[Metric] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)  # 名称 → 本机路径
    raw: dict[str, Any] = Field(default_factory=dict)

    def metric(self, name: str) -> float | None:
        for m in self.metrics:
            if m.name == name:
                return m.value
        return None
