"""平台服务门面 —— HTTP 与 MCP 共用的同一套入口。

为什么要有这一层：

「对外暴露」有两件不同的事：**暴露什么**（契约）和**怎么暴露**（传输）。
如果把业务逻辑写进 HTTP handler，再写一个 MCP server 时就得抄一遍，
两边的行为会慢慢漂开——同一个目标用 HTTP 问和用 MCP 问，答案不一样，
而 AI agent 恰恰是两条路都会走的。

所以：传输层（api.py / mcp.py）只做协议翻译，所有语义都在这一个类里。
将来换 FastAPI 或加 WebSocket，改的是传输层，不是这里。

关于取消的诚实说明：编排层的取消粒度是【步】——能在步与步之间停下，
停不下正在跑的那一步（那要各适配器自己实现 cancel）。
API 文档里写的就是这个粒度，不假装能瞬间中断。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .adapter import Adapter, CapabilityDecl
from .adapters.dialux import DialuxAdapter
from .adapters.lumen import LumenPlannerAdapter
from .ir import TaskSpec
from .job import JobStatus
from .orchestrator import Orchestrator, PipelineRun
from .pipelines import DEFAULT_PIPELINES, get_pipeline
from .project import Project
from .registry import AdapterRegistry


def capability_to_dict(decl: CapabilityDecl) -> Dict[str, Any]:
    """能力声明的对外形状。limits 一定要带出去——
    不把「做不到什么」给到调用方，对方就会拿它当承诺用。"""
    return {
        "name": decl.name,
        "kinds": list(decl.kinds),
        "tags": list(decl.tags),
        "limits": list(decl.limits),
        "fragile": decl.fragile,
    }


def check_to_dict(check) -> Dict[str, Any]:
    return {"name": check.name, "passed": check.passed, "detail": check.detail}


def step_to_dict(step_run) -> Dict[str, Any]:
    return {
        "name": step_run.name,
        "adapter": step_run.adapter,
        "job_id": step_run.job_id,
        "metrics": [m.model_dump() for m in step_run.result.metrics],
        "artifacts": dict(step_run.result.artifacts),
        "raw": dict(step_run.result.raw),
        "checks": [check_to_dict(c) for c in step_run.checks],
    }


def run_to_dict(run: PipelineRun) -> Dict[str, Any]:
    return {
        "goal": run.goal,
        "completed": run.completed,
        "cancelled": run.cancelled,
        "ok": run.ok,
        "failed_at": run.failed_at,
        "failure_detail": run.failure_detail,
        "elapsed": round(run.elapsed, 3),
        "steps": [step_to_dict(s) for s in run.steps],
        "checks": [check_to_dict(c) for c in run.checks],
        "failures": [check_to_dict(c) for c in run.failures],
    }


@dataclass
class ServiceJob:
    job_id: str
    pipeline: str
    status: JobStatus = JobStatus.QUEUED
    percent: float = 0.0
    message: str = "已排队"
    run: Optional[PipelineRun] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "pipeline": self.pipeline,
            "status": self.status.value,
            "percent": round(self.percent, 1),
            "message": self.message,
        }
        if self.error is not None:
            payload["error"] = self.error
        if self.finished_at is not None:
            payload["elapsed"] = round(self.finished_at - self.started_at, 3)
        return payload


class PlatformService:
    """把「目标 → 流水线 → 适配器」这套东西收进一个可被传输层调用的门面。"""

    def __init__(self, registry: AdapterRegistry, project: Optional[Project] = None,
                 timeout: float = 120.0) -> None:
        self.registry = registry
        self.project = project
        self.timeout = timeout
        self._jobs: Dict[str, ServiceJob] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------- 静态能力
    def capabilities(self) -> List[Dict[str, Any]]:
        return [capability_to_dict(a.capabilities()) for a in self.registry.all()]

    def pipelines(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for name, factory in sorted(DEFAULT_PIPELINES.items()):
            pipeline = factory()
            out.append({
                "name": name,
                "goal": pipeline.goal,
                "steps": [{"name": s.name, "kind": s.kind,
                           "requires": list(s.requires),
                           "optional": s.optional} for s in pipeline.steps],
            })
        return out

    # -------------------------------------------------- 提交与查询
    def submit(self, pipeline_name: str, task: TaskSpec | Dict[str, Any]) -> str:
        """立刻返回 job_id，流水线在后台线程里跑。"""
        spec = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
        get_pipeline(pipeline_name)  # 名字不对就当场报错，别等线程里才炸
        job = ServiceJob(job_id=f"job-{uuid.uuid4().hex[:10]}", pipeline=pipeline_name)
        with self._lock:
            self._jobs[job.job_id] = job
        threading.Thread(target=self._run_job, args=(job, spec), daemon=True).start()
        return job.job_id

    def _run_job(self, job: ServiceJob, task: TaskSpec) -> None:
        try:
            if job.cancel_requested:
                job.status = JobStatus.CANCELLED
                job.message = "已取消"
                job.finished_at = time.monotonic()
                return
            job.status = JobStatus.RUNNING
            run = self._execute(job, task)
            job.run = run
            if run.cancelled:
                job.status = JobStatus.CANCELLED
                job.message = f"已取消（停在步骤 {run.failed_at}）"
            elif run.ok:
                job.status = JobStatus.DONE
                job.percent = 100.0
                job.message = "流水线完成，校验全部通过"
            elif run.completed:
                job.status = JobStatus.DONE
                job.percent = 100.0
                job.message = f"流水线跑完但校验未全过（{len(run.failures)} 条不通过）"
            else:
                job.status = JobStatus.FAILED
                reason = f"：{run.failure_detail}" if run.failure_detail else ""
                job.message = f"在步骤 {run.failed_at} 停下{reason}"
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常吞进黑洞
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = job.error
        finally:
            job.finished_at = time.monotonic()

    def _execute(self, job: ServiceJob, task: TaskSpec) -> PipelineRun:
        orchestrator = Orchestrator(self.registry, project=self.project,
                                    timeout=self.timeout)

        def on_step(state: str, done: int, total: int, name: str) -> None:
            if state == "start":
                job.percent = (done / total) * 100.0 if total else 0.0
                job.message = f"正在跑步骤 {name}（{done + 1}/{total}）"
            else:
                job.percent = (done / total) * 100.0 if total else 100.0

        return orchestrator.run(
            get_pipeline(job.pipeline), task,
            on_step=on_step,
            should_cancel=lambda: job.cancel_requested,
        )

    def status(self, job_id: str) -> Dict[str, Any]:
        return self._job(job_id).to_dict()

    def result(self, job_id: str) -> Dict[str, Any]:
        job = self._job(job_id)
        if job.status is JobStatus.FAILED:
            raise RuntimeError(f"任务 {job_id} 失败：{job.error or job.message}")
        if job.run is None:
            raise RuntimeError(
                f"任务 {job_id} 还没产出结果（当前 {job.status.value}）"
            )
        return {**job.to_dict(), **run_to_dict(job.run)}

    def cancel(self, job_id: str) -> Dict[str, Any]:
        """请求取消。**粒度是步**：能在步与步之间停下，停不下正在跑的那一步。"""
        job = self._job(job_id)
        job.cancel_requested = True
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            job.status = JobStatus.CANCELLED
            job.message = "已请求取消（当前步骤结束后生效）"
        return job.to_dict()

    def artifact_path(self, job_id: str, key: str) -> Path:
        """取某个任务产物的真实路径。

        只认【平台自己记录在该任务上的产物】——调用方给不了任意路径，
        所以路径穿越在结构上就不可能发生，不需要再写一层路径校验。
        """
        job = self._job(job_id)
        if job.run is None:
            raise RuntimeError(f"任务 {job_id} 还没有产物（当前 {job.status.value}）")
        available: List[str] = []
        for step in job.run.steps:
            for artifact_key, raw in step.result.artifacts.items():
                available.append(artifact_key)
                if artifact_key != key:
                    continue
                path = Path(raw)
                if not path.exists():
                    raise KeyError(f"产物 {key!r} 已登记但文件不在：{path}")
                return path
        raise KeyError(f"任务 {job_id} 没有名为 {key!r} 的产物；实际有：{sorted(set(available))}")

    def run_sync(self, pipeline_name: str, task: TaskSpec | Dict[str, Any]) -> Dict[str, Any]:
        """同步跑完（CLI、测试、以及 MCP 的同步工具调用用）。"""
        spec = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
        job = ServiceJob(job_id=f"sync-{uuid.uuid4().hex[:8]}", pipeline=pipeline_name)
        with self._lock:
            self._jobs[job.job_id] = job
        self._run_job(job, spec)
        return self.result(job.job_id)

    def _job(self, job_id: str) -> ServiceJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"未知任务 {job_id}") from None


def build_default_registry(output_root: Optional[Path | str] = None) -> AdapterRegistry:
    """平台默认装哪些适配器。HTTP / MCP / CLI 都从这里起，保证行为一致。"""
    root = Path(output_root) if output_root is not None else None
    registry = AdapterRegistry()
    registry.register(LumenPlannerAdapter(
        output_dir=(root / "plans") if root else None))
    registry.register(DialuxAdapter(
        output_dir=(root / "stf") if root else None))
    return registry


def build_default_service(output_root: Optional[Path | str] = None,
                          project_id: str = "default") -> PlatformService:
    return PlatformService(
        build_default_registry(output_root),
        project=Project(id=project_id, name="光枢默认项目"),
    )


__all__ = [
    "PlatformService",
    "ServiceJob",
    "build_default_registry",
    "build_default_service",
    "capability_to_dict",
    "run_to_dict",
]
