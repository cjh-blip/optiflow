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
from typing import Any, Dict, List, Optional, Sequence

from .adapter import CapabilityDecl
from .adapters.dialux import DialuxAdapter
from .adapters.lumen import LumenPlannerAdapter
from .ir import TaskSpec
from .job import JobStatus
from .job_store import JobStore
from . import project_store
from .orchestrator import Orchestrator, PipelineRun
from .pipelines import DEFAULT_PIPELINES, get_pipeline
from .plan_summary import units_check
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


def step_brief(step_run) -> Dict[str, Any]:
    """步骤摘要：字段名与 step_to_dict 一致，只去掉体积不可控的 raw。

    为什么复用 step_to_dict 再删一个键、而不另写一份字段：两边字段名一旦漂开，
    前端就得同时兼容两种 steps 形状（status 一种、result 一种），
    那比多背一个 pop 贵得多。raw 是适配器原始返回，可能几百 KB，
    塞进每秒好几次的 status() 响应里没有意义。
    """
    brief = step_to_dict(step_run)
    brief.pop("raw", None)
    return brief


#: 进度条的取值策略。事件源是光枢自己的「步」事件，不是时间心跳——
#: 卡住时不涨，才不会骗人。
PROGRESS_START = 5.0        # 一开跑就离开 0：动了比停在原点诚实
PROGRESS_CEILING = 95.0     # 运行中的天花板：算不准就不装准，走完才跳 100
PROGRESS_SIP = 0.3          # 每次「步开始」的微增（档位抄自 Mrite 的 0.3）

#: 任务所处的位置。运行中是「步骤名」，收尾是四个终态之一。
#: 与 percent 正交：stage 回答「在哪」，percent 只回答「大概剩多少」。
STAGE_QUEUED = "queued"
STAGE_RUNNING = "running"
STAGE_DONE = "done"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"
STAGE_ERROR = "error"


def _nudge_percent(percent: float) -> float:
    """一次微增：让「动起来了」可见，但永不越过天花板，也永不回退。"""
    base = max(float(percent), PROGRESS_START)
    return round(min(PROGRESS_CEILING, base + PROGRESS_SIP), 1)


def _advance_percent(percent: float, done: int, total: int) -> float:
    """按已完成步数推进到对应的地板值；只升不降，且不越天花板。"""
    if total <= 0:
        return round(min(PROGRESS_CEILING, float(percent)), 1)
    floor = PROGRESS_START + (PROGRESS_CEILING - PROGRESS_START) * done / total
    return round(min(PROGRESS_CEILING, max(float(percent), floor)), 1)


@dataclass
class ServiceJob:
    job_id: str
    pipeline: str
    status: JobStatus = JobStatus.QUEUED
    stage: str = STAGE_QUEUED
    percent: float = 0.0
    message: str = "已排队"
    run: Optional[PipelineRun] = None
    #: 运行中的同一份 run（与 run 指向同一个对象）。单开一个字段是为了
    #: 不改变 result() 的语义：没跑完不给结果，但跑的过程中要能看见步骤。
    live_run: Optional[PipelineRun] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.monotonic)
    #: 落盘用的挂钟时间。started_at 是 monotonic，只在本进程内有意义，
    #: 两个混用会让“跑了 1200 秒”这种数字在重启后变得荒谬。
    started_wall: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    finished_wall: Optional[float] = None

    def steps_view(self) -> List[Dict[str, Any]]:
        """已落地的步骤（运行中就是“到目前为止跑完的”）。"""
        run = self.live_run if self.live_run is not None else self.run
        if run is None:
            return []
        return [step_brief(s) for s in run.steps]

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "pipeline": self.pipeline,
            "status": self.status.value,
            "stage": self.stage,
            "percent": round(self.percent, 1),
            "message": self.message,
            "steps": self.steps_view(),
        }
        if self.error is not None:
            payload["error"] = self.error
        if self.finished_at is not None:
            payload["elapsed"] = round(self.finished_at - self.started_at, 3)
        return payload


class PlatformService:
    """把「目标 → 流水线 → 适配器」这套东西收进一个可被传输层调用的门面。"""

    def __init__(self, registry: AdapterRegistry, project: Optional[Project] = None,
                 timeout: float = 120.0,
                 state_root: Optional[Path | str] = None) -> None:
        self.registry = registry
        self.project = project
        self.timeout = timeout
        self._jobs: Dict[str, ServiceJob] = {}
        self._lock = threading.Lock()
        #: 落盘根。None ＝ 纯内存（测试与临时调用走这条），给了才落盘。
        self.state_root: Optional[Path] = (
            Path(state_root) if state_root is not None else None)
        self.job_store: Optional[JobStore] = (
            JobStore(self.state_root) if self.state_root is not None else None)
        #: 启动时被认成中断的 job_id。不是清理，是认账。
        self.interrupted_jobs: List[str] = (
            self.job_store.reap_interrupted() if self.job_store is not None else [])

    # -------------------------------------------------- 静态能力
    def capabilities(self) -> List[Dict[str, Any]]:
        return [capability_to_dict(a.capabilities()) for a in self.registry.all()]

    # -------------------------------------------------- DXF 导入（评价点①）
    def import_dxf(self, filename: str, content_base64: str,
                   config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """DXF → IR ＋ 完整性检查。给「导入自动化 + 允许确认 + 完整性检查」用。

        返回完整 IR（确认后可直接喂下游），附检查清单与房间概要。
        解析失败或内容为空一律 ValueError（传输层翻 400），绝不返回半个结果。
        """
        import base64
        import tempfile

        from .adapters.dialux._chain import polygon_area
        from .adapters.dialux.dxf import ParseConfig, parse_dxf

        if not content_base64:
            raise ValueError("请求里没有 DXF 内容（content_base64）")
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception as exc:  # noqa: BLE001 - 交给传输层做 400
            raise ValueError(f"content_base64 不是合法 base64：{exc}") from None
        if not raw.strip():
            raise ValueError("DXF 内容为空")

        cfg = ParseConfig.from_dict(config) if config else ParseConfig()
        name = Path(filename or "drawing.dxf").name
        with tempfile.TemporaryDirectory(prefix="optiflow_dxf_") as tmp:
            path = Path(tmp) / name
            path.write_bytes(raw)
            try:
                ir = parse_dxf(str(path), cfg)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"DXF 解析失败：{type(exc).__name__}: {exc}") from None

        meta = ir.get("_meta", {})
        rooms_total = int(meta.get("rooms", 0))
        closed = int(meta.get("rooms_closed", 0))
        saw = int(meta.get("rooms_with_sawtooth", 0))
        # 单位出处（解析器给的）：config / header / config_fallback / geometry_fallback
        units_used = meta.get("dxf_units")
        units_source = meta.get("units_source") or "unknown"
        config_source = "caller" if config else "default"
        cfg_note = "" if config_source == "caller" else "（调用方未给解析配置，用的是 ParseConfig 默认）"

        rooms_out: List[Dict[str, Any]] = []
        storeys = ir.get("storeys") or [{}]
        for space in storeys[0].get("spaces", []):
            if str(space.get("name", "")).startswith("家具_"):
                continue
            poly = space.get("polygon") or []
            xs = [float(p[0]) for p in poly] or [0.0]
            ys = [float(p[1]) for p in poly] or [0.0]
            rooms_out.append({
                "id": space.get("id"),
                "name": space.get("name"),
                "vertices": len(poly),
                "area_m2": round(polygon_area(poly), 2) if len(poly) >= 3 else 0.0,
                "width_m": round(max(xs) - min(xs), 2),
                "depth_m": round(max(ys) - min(ys), 2),
                "furniture": len(space.get("furniture") or []),
            })

        checks = [
            {"name": "读到房间", "passed": rooms_total > 0,
             "detail": (f"{rooms_total} 个闭合房间" if rooms_total else
                        f"没读到房间（按 {units_used} 解析）；检查图层与单位设置，"
                        f"或用 config 显式指定 units{cfg_note}")},
            {"name": "房间环闭合", "passed": rooms_total > 0 and closed == rooms_total,
             "detail": f"{closed}/{rooms_total} 个环闭合"},
            {"name": "环上短边", "passed": saw == 0,
             "detail": "无异常短边" if saw == 0 else f"{saw} 个房间环上有 <0.2m 短边，可能混入家具轮廓"},
            units_check(meta, cfg_note),   # 判据口径在 plan_summary.units_check，不在这里另写一份
        ]

        return {
            "ok": all(c["passed"] for c in checks),
            "filename": name,
            # 解析配置出处：caller（调用方显式给）/ default（用 ParseConfig 默认）
            "config_source": config_source,
            "summary": {
                "rooms": rooms_total,
                "rooms_closed": closed,
                "furniture": int(meta.get("furniture", 0)),
                "luminaires": int(meta.get("luminaires", 0)),
                "units": units_used,
                "units_source": units_source,
                "config_source": config_source,
                "notches_removed": int(meta.get("room_notches_removed", 0)),
            },
            "checks": checks,
            "rooms": rooms_out,
            "ir": ir,
        }

    # -------------------------------------------------- 报告组装（评价点④）
    def assemble_report_pdf(self, report_pdf: str, *,
                            criteria: Optional[Dict[str, Any]] = None,
                            criteria_path: Optional[str] = None,
                            price: Optional[Dict[str, Any]] = None,
                            render_images: Optional[Sequence[str]] = None,
                            project_name: str = "会议室照明设计") -> Dict[str, Any]:
        """DIALux 报表 PDF（本地路径）→ 结果包 → 报告 Markdown。

        走路径而不是 base64：报表动辄几十 MB，塞进 HTTP 请求体不划算；
        本接口本来就只绑回环、给本机工具用。criteria_path 同理：判据表 JSON 由服务端读，
        浏览器无法直接读本地文件。
        """
        import json

        from .adapters.dialux.report import extract
        from .report_assembler import assemble_report

        path = Path(report_pdf)
        if not path.exists():
            raise ValueError(f"报表 PDF 不存在：{path}")
        if criteria is None and criteria_path:
            cap = Path(criteria_path)
            if not cap.exists():
                raise ValueError(f"判据表 JSON 不存在：{cap}")
            criteria = json.loads(cap.read_text(encoding="utf-8"))
        package = extract(path, project=project_name)
        # 图件缺失要当场报出是哪一张（对齐 A5-05 的失败诊断口径），不让 pandoc 在后面崩
        images: Optional[List[str]] = None
        if render_images:
            images = [str(p) for p in render_images]
            for img in images:
                if not Path(img).exists():
                    raise ValueError(f"渲染图不存在：{img}")
        markdown = assemble_report(
            package, criteria=criteria, price=price, render_images=images,
            project_name=project_name)
        return {
            "ok": True,
            "source_pdf": str(path),
            "render_images": images or [],
            "package_missing": package.get("_missing") or [],
            # 结果包本体也带出去：抽数动作是「PDF → 结果包 json」目前唯一的入口，
            # 调用方（如壳）要落盘 package.json 供 report.metrics / report.assemble 复用
            "package": package,
            "markdown": markdown,
        }

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
            job.stage = STAGE_RUNNING
            job.percent = PROGRESS_START
            job.started_wall = job.started_wall or time.time()
            self._persist(job)
            run = self._execute(job, task)
            job.run = run
            if run.cancelled:
                job.status = JobStatus.CANCELLED
                job.stage = STAGE_CANCELLED
                job.message = f"已取消（停在步骤 {run.failed_at}）"
            elif run.ok:
                job.status = JobStatus.DONE
                job.stage = STAGE_DONE
                job.percent = 100.0
                job.message = "流水线完成，校验全部通过"
            elif run.completed:
                job.status = JobStatus.DONE
                job.stage = STAGE_DONE
                job.percent = 100.0
                job.message = f"流水线跑完但校验未全过（{len(run.failures)} 条不通过）"
            else:
                job.status = JobStatus.FAILED
                job.stage = STAGE_FAILED
                # percent 故意不动：失败时停在当前位置，不归零也不跳 100。
                # 回退到 0 会让人以为重跑了一次。
                reason = f"：{run.failure_detail}" if run.failure_detail else ""
                job.message = f"在步骤 {run.failed_at} 停下{reason}"
            # 项目持久化：把这次运行并进项目文件（失败也不影响任务状态）。
            self._absorb_run(task, run)
            # 状态一定就立刻落盘一次，不要只靠 finally：status() 读的是内存，
            # 调用方看到 done 的那一刻盘上可能还是 running。
            self._persist(job)
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常吞进黑洞
            job.status = JobStatus.FAILED
            job.stage = STAGE_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = job.error
        finally:
            job.finished_at = time.monotonic()
            job.finished_wall = time.time()
            self._persist(job)

    def _execute(self, job: ServiceJob, task: TaskSpec) -> PipelineRun:
        orchestrator = Orchestrator(self.registry, project=self.project,
                                    timeout=self.timeout)

        def on_step(state: str, done: int, total: int, name: str) -> None:
            # stage 与 percent 各走各的：stage 说「在哪一步」，percent 只做装饰。
            # 把两者婚成一条公式的结果是：界面只能说“37%”，说不出“在布灯”。
            job.stage = name
            if state == "start":
                job.percent = _nudge_percent(job.percent)
                job.message = f"正在跑步骤 {name}（{done + 1}/{total}）"
            else:
                job.percent = _advance_percent(job.percent, done, total)
                job.message = f"步骤 {name} 完成（{done}/{total}）"
            self._persist(job)

        def on_run_update(run: PipelineRun) -> None:
            # 同一个对象引用：后续步骤追加后，job 里看到的就是最新的一份。
            job.live_run = run
            self._persist(job)

        return orchestrator.run(
            get_pipeline(job.pipeline), task,
            on_step=on_step,
            should_cancel=lambda: job.cancel_requested,
            on_run_update=on_run_update,
        )

    def _persist(self, job: ServiceJob) -> None:
        """旁路落盘。没开 state_root 时是空操作，写失败也不会带崩任务。"""
        if self.job_store is not None:
            self.job_store.save_job(job)

    def load_project(self, project_id: str) -> Project:
        """从磁盘读回项目。内存里就是它的话直接给同一个对象。

        读回来的 Project 带完整 inputs / artifacts / results / versions，
        可以继续拿来跑，也可以只用来查看。
        """
        if self.state_root is None:
            raise RuntimeError("没有业务根：项目文件无处可读（需要 state_root）")
        if self.project is not None and self.project.id == project_id:
            return self.project
        return project_store.load(
            project_store.project_path(self.state_root, project_id))

    def resume_project(self, pipeline_name: str = "lighting_plan", *,
                       project_id: Optional[str] = None) -> str:
        """拿项目里最后一条原始输入再跑一遍，跑完是**追加**一个版本。

        只认 ``inputs``：那是原始 TaskSpec，本来就该从头重算。
        results / versions 是派生，不能拿来当输入，否则重算的结果会被旧结果污染。
        """
        project = self.load_project(project_id or (self.project.id if self.project else ""))
        if not project.inputs:
            raise ValueError(
                f"项目 {project.id} 里没有原始输入，无法重算：先跑一次把 inputs 存下来")
        self.project = project          # 后续 _absorb_run 追加到这个项目上
        return self.submit(pipeline_name, project.inputs[-1])

    def _absorb_run(self, task: TaskSpec, run: PipelineRun) -> Optional[Path]:
        """把一次运行落进项目文件。

        分工：**版本与 results 由 Orchestrator 归档**（它每成功一步就 archive 一次，
        受 ``archive_steps`` 控制）；这里只补原始输入并写盘。
        早先这里又 add_version 一遍，结果一次两步流水线凭空多出一个版本。

        失败不抛：项目落盘是旁路，它坏了不该把已经跑完的任务改写成失败。
        """
        if self.state_root is None or self.project is None:
            return None
        try:
            project = self.project
            # inputs 是原始输入，只进不回：重算靠它，results/versions 不能当输入。
            if all(t.model_dump() != task.model_dump() for t in project.inputs):
                project.inputs.append(task)
            return project_store.save(
                project, project_store.project_path(self.state_root, project.id))
        except Exception:  # noqa: BLE001 - 旁路失败不改写任务状态
            return None

    def status(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is not None:
            return job.to_dict()
        # 重启后内存里没有，但盘上可能有上次运行的记录（status 会读到 interrupted）
        if self.job_store is not None:
            snap = self.job_store.snapshot(job_id)
            if snap is not None:
                return snap
        raise KeyError(f"未知任务 {job_id}（内存与落盘记录里都没有）")

    def result(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            # 内存里没有：可能是重启前留下的任务。不要让它听起来像「任务不存在」，
            # 那会把人往错方向查（以为是 job_id 打错或根本没提交过）。
            snap = self.job_store.snapshot(job_id) if self.job_store is not None else None
            if snap is not None:
                raise RuntimeError(
                    f"任务 {job_id} 是上一次运行的记录（状态 {snap['status']}）："
                    f"结果对象没有落盘，重启后取不回来。重新提交一次才能拿到新结果。")
            raise KeyError(f"未知任务 {job_id}（内存与落盘记录里都没有）")
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
        self._persist(job)
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


def _open_or_create(state_root: Optional[Path | str], project_id: str,
                    name: str = "光枢默认项目") -> Project:
    """有项目文件就读回来，没有才新建一个空项目。

    不能无脑 ``Project(...)``：那会让每次重启后的第一次运行把磁盘上的
    版本历史整个覆盖掉——文件还是那个文件，旧版本却没声了，这比报错难查。
    文件坏了（schema_version 不符 / JSON 不合法）就让它抛：报错总比默默覆盖好。
    """
    if state_root is None:
        return Project(id=project_id, name=name)
    path = project_store.project_path(state_root, project_id)
    if path.exists():
        return project_store.load(path)
    return Project(id=project_id, name=name)


def build_default_service(output_root: Optional[Path | str] = None,
                          project_id: str = "default") -> PlatformService:
    return PlatformService(
        build_default_registry(output_root),
        project=_open_or_create(output_root, project_id),
        state_root=output_root,
    )


__all__ = [
    "PlatformService",
    "ServiceJob",
    "build_default_registry",
    "build_default_service",
    "capability_to_dict",
    "run_to_dict",
]
