"""项目模型：输入 + 产物 + 结果 + 版本。

对应照明项目里已有的「方案 A/B/C/D」版本链经验：一个项目是多版本的，
不是一堆散文件。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .ir import TaskSpec
from .job import ResultSet


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectVersion(BaseModel):
    version: int
    created_at: datetime = Field(default_factory=_now)
    note: str = ""
    artifacts: dict[str, str] = Field(default_factory=dict)
    metrics_summary: dict[str, float] = Field(default_factory=dict)


class Project(BaseModel):
    id: str
    name: str = ""
    created_at: datetime = Field(default_factory=_now)
    inputs: list[TaskSpec] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    results: list[ResultSet] = Field(default_factory=list)
    versions: list[ProjectVersion] = Field(default_factory=list)

    def add_version(
        self,
        note: str = "",
        artifacts: dict[str, str] | None = None,
        metrics_summary: dict[str, float] | None = None,
    ) -> ProjectVersion:
        version = ProjectVersion(
            version=len(self.versions) + 1,
            note=note,
            artifacts=dict(artifacts or {}),
            metrics_summary=dict(metrics_summary or {}),
        )
        self.versions.append(version)
        self.artifacts.update(version.artifacts)
        return version
