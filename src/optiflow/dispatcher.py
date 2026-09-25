"""最小编排：TaskSpec → 选适配器 → 提交 → 等待 → 归档到项目。

P0 只要这条链路通，不做任务分解、不做多步流水线（那是编排层往后的事）。
"""

from __future__ import annotations

import time

from .adapter import Adapter
from .ir import TaskSpec
from .job import JobStatus, Progress, ResultSet
from .project import Project, ProjectVersion
from .registry import AdapterRegistry


class AdapterNotFound(Exception):
    """没有任何适配器声明支持该任务类型。"""


class Dispatcher:
    def __init__(self, registry: AdapterRegistry, project: Project | None = None) -> None:
        self.registry = registry
        self.project = project
        self._jobs: dict[str, Adapter] = {}

    def dispatch(self, task: TaskSpec) -> str:
        adapter = self.registry.find(task.kind)
        if adapter is None:
            known = sorted({k for a in self.registry.all() for k in a.capabilities().kinds})
            raise AdapterNotFound(f"没有适配器支持 kind={task.kind!r}；当前能力: {known}")
        if self.project is not None:
            self.project.inputs.append(task)
        job_id = adapter.submit(task)
        self._jobs[job_id] = adapter
        return job_id

    def progress(self, job_id: str) -> Progress:
        return self._adapter_of(job_id).status(job_id)

    def wait(self, job_id: str, timeout: float = 30.0, interval: float = 0.05) -> ResultSet:
        adapter = self._adapter_of(job_id)
        deadline = time.monotonic() + timeout
        while True:
            progress = adapter.status(job_id)
            if progress.status is JobStatus.DONE:
                return adapter.result(job_id)
            if progress.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                raise RuntimeError(f"任务 {job_id} 结束于 {progress.status.value}: {progress.message}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"任务 {job_id} 超时（{timeout}s），最后状态 {progress.status.value}")
            time.sleep(interval)

    def archive(self, job_id: str, note: str = "") -> ProjectVersion:
        """把任务结果写进项目，形成一个版本。"""
        if self.project is None:
            raise RuntimeError("没有绑定项目，无法归档")
        result = self._adapter_of(job_id).result(job_id)
        self.project.results.append(result)
        metrics = {m.name: m.value for m in result.metrics if m.value is not None}
        return self.project.add_version(note=note, artifacts=result.artifacts, metrics_summary=metrics)

    def _adapter_of(self, job_id: str) -> Adapter:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"未知任务 {job_id}（不由本 Dispatcher 提交）") from None
