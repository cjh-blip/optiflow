"""编排层 —— 架构图里那层「任务分解 → 选能力 → 拼流水线 → 校验结果」。

为什么需要它（不是把 Dispatcher 重写一遍）：

Dispatcher 只回答「谁支持这个 kind」，然后按**注册顺序**取第一个。
而平台里 LumenPlannerAdapter（算法通道）和 DialuxAdapter（文件通道）都声明
kind="layout" —— 它们处理同一类任务、只是深度不同。注册顺序一变，跑出来的东西就变。
这不是假设：demo 里第二步就被路由回了算法层，ResultSet 里没有 stf 产物，
直接 KeyError: 'stf'。

所以编排层做四件 Dispatcher 不做的事：

1. **任务分解**：把「说目标」拆成有序步骤，每步从上下文构造自己的 TaskSpec；
2. **选能力**：按 kind + tags 选中**唯一**的适配器。**有歧义就报错，不猜**——
   猜错的代价是一条跑到一半才发现走错通道的流水线；
3. **拼流水线**：上一步的产物进入下一步的输入（接缝见 pipelines.apply_plan）；
4. **校验结果**：把带 target 的 Metric 逐条对账，跨步骤的产物一致性也对账。

产出 PipelineRun：每步用了哪个适配器、job_id、结果、校验结论，以及整体成败。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .adapter import Adapter, CapabilityDecl
from .dispatcher import AdapterNotFound, Dispatcher
from .ir import TaskSpec
from .job import ResultSet
from .registry import AdapterRegistry


class AmbiguousAdapter(Exception):
    """有多个适配器都满足条件，且调用方没有给出消歧依据。

    编排层宁可在这里报错，也不按注册顺序随便挑一个——
    悄悄挑错通道的代价，远大于让人早一步写清楚 tags。
    """


@dataclass(frozen=True)
class Check:
    """一条校验结论。passed=False 时 detail 必须说清差在哪。"""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class StepRun:
    name: str
    adapter: str
    job_id: str
    result: ResultSet
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)


@dataclass
class PipelineContext:
    """流水线上下文：原始任务（约束的权威来源） + 各步结果。"""

    task: TaskSpec
    runs: Dict[str, StepRun] = field(default_factory=dict)

    def result_of(self, step_name: str) -> ResultSet:
        try:
            return self.runs[step_name].result
        except KeyError:
            raise KeyError(
                f"步骤 {step_name!r} 还没有结果（已完成: {sorted(self.runs)}）"
            ) from None

    def metric(self, step_name: str, metric_name: str) -> Optional[float]:
        return self.result_of(step_name).metric(metric_name)


CheckFn = Callable[[PipelineContext, StepRun], List[Check]]


@dataclass(frozen=True)
class Step:
    """流水线的一步。

    - build：从上下文构造本步的 TaskSpec（这就是「任务分解」的落点）
    - requires：要求的 tags；配合 kind 一起消歧
    - adapter_name：更直接的点名（调试/兜底用）
    - optional：允许失败而不拖垮整条流水线
    """

    name: str
    kind: str
    build: Callable[[PipelineContext], TaskSpec]
    requires: Tuple[str, ...] = ()
    adapter_name: Optional[str] = None
    optional: bool = False
    checks: Tuple[CheckFn, ...] = ()


@dataclass(frozen=True)
class Pipeline:
    """一条流水线：有序步骤 + 目标描述。"""

    goal: str
    steps: List[Step]


@dataclass
class PipelineRun:
    goal: str
    steps: List[StepRun]
    checks: List[Check] = field(default_factory=list)
    failed_at: Optional[str] = None
    #: 步骤失败的具体原因。只记「停在 plan」而不记为什么，等于让人重跑一遍去猜。
    failure_detail: str = ""
    elapsed: float = 0.0
    cancelled: bool = False

    @property
    def completed(self) -> bool:
        return self.failed_at is None and not self.cancelled and len(self.steps) > 0

    @property
    def ok(self) -> bool:
        return self.completed and all(c.passed for c in self.checks) and all(
            s.ok for s in self.steps)

    @property
    def failures(self) -> List[Check]:
        out = [c for c in self.checks if not c.passed]
        for s in self.steps:
            out.extend(c for c in s.checks if not c.passed)
        return out

    def result_of(self, step_name: str) -> ResultSet:
        for s in self.steps:
            if s.name == step_name:
                return s.result
        raise KeyError(f"没有名为 {step_name!r} 的步骤（已完成: {[s.name for s in self.steps]}）")


# ---------------------------------------------------------------- 常用校验


def metric_targets_met() -> CheckFn:
    """把结果里带 target 的 Metric 逐条对账（target 视为【下限】）。

    本平台的 target 语义就是设计下限：illuminance_avg >= 500、uniformity_u0 >= 0.60。
    将来若有上限类指标（如眩光 UGR <= 19），要单独写一条校验，不要塞进这里——
    把方向含糊掉比多写两行代码危险得多。
    """

    def check(ctx: PipelineContext, run: StepRun) -> List[Check]:
        out: List[Check] = []
        targets = [m for m in run.result.metrics if m.target is not None]
        if not targets:
            out.append(Check(
                name=f"{run.name}:目标对账",
                passed=True,
                detail="本步结果没有带 target 的指标，无需对账",
            ))
            return out
        for m in targets:
            value = m.value if m.value is not None else float("-inf")
            passed = value >= m.target
            out.append(Check(
                name=f"{run.name}:{m.name}>=目标",
                passed=passed,
                detail=(f"{m.name}={value}{m.unit} 目标 {m.target}{m.unit}"
                        if passed else
                        f"{m.name}={value}{m.unit} 低于目标 {m.target}{m.unit}"),
            ))
        return out

    return check


def artifact_exists(key: str, min_bytes: int = 1) -> CheckFn:

    def check(ctx: PipelineContext, run: StepRun) -> List[Check]:
        raw = run.result.artifacts.get(key)
        if raw is None:
            return [Check(
                name=f"{run.name}:产物 {key} 存在",
                passed=False,
                detail=f"结果里没有 {key!r} 产物（实际有: {sorted(run.result.artifacts)}）",
            )]
        path = Path(raw)
        if not path.exists():
            return [Check(name=f"{run.name}:产物 {key} 存在", passed=False,
                          detail=f"路径不存在: {path}")]
        size = path.stat().st_size
        return [Check(
            name=f"{run.name}:产物 {key} 存在",
            passed=size >= min_bytes,
            detail=f"{path}（{size} 字节）" if size >= min_bytes
                   else f"{path} 只有 {size} 字节，小于下限 {min_bytes}",
        )]

    return check


def cross_step_metric_matches(step_a: str, metric_a: str, step_b: str,
                             metric_b: str) -> CheckFn:
    """跨步骤一致性：A 步的某个指标必须等于 B 步的对应指标。

    典型用法：算法层规划的灯具数，必须等于文件层真正写进 STF 的灯具数——
    「算出来 31 盏、导出去 28 盏」这种掉队，只有对账才发现得了。
    """

    def check(ctx: PipelineContext, run: StepRun) -> List[Check]:
        name = f"跨步骤:{step_a}.{metric_a}=={step_b}.{metric_b}"
        try:
            va = ctx.metric(step_a, metric_a)
            vb = ctx.metric(step_b, metric_b)
        except KeyError as exc:
            return [Check(name=name, passed=False, detail=str(exc))]
        if va is None or vb is None:
            return [Check(name=name, passed=False,
                          detail=f"指标缺失：{step_a}.{metric_a}={va}, {step_b}.{metric_b}={vb}")]
        ok = abs(va - vb) < 1e-9
        return [Check(name=name, passed=ok,
                      detail=f"{va} vs {vb}" if ok else f"不一致：{va} != {vb}")]

    return check


# ---------------------------------------------------------------- 编排器


class Orchestrator:
    """按流水线把任务交给对应的适配器，并逐步校验。"""

    def __init__(self, registry: AdapterRegistry, project=None,
                 timeout: float = 120.0, stop_on_failure: bool = True,
                 archive_steps: bool = True) -> None:
        self.registry = registry
        self.project = project
        self.timeout = timeout
        self.stop_on_failure = stop_on_failure
        # 每一步都归档成一个项目版本：项目模型要能回答「这份产物是怎么来的」，
        # 只记最后一步的话，中间过程（谁算的方案、参数是什么）就丢了。
        self.archive_steps = archive_steps

    # -------------------------------------------------- 选能力
    def resolve(self, step: Step) -> Adapter:
        """按 kind + tags（或点名）选出**唯一**适配器；有歧义就报错。"""
        candidates = [a for a in self.registry.all()
                      if step.kind in a.capabilities().kinds]
        if step.adapter_name is not None:
            candidates = [a for a in candidates
                          if a.capabilities().name == step.adapter_name]
        for tag in step.requires:
            candidates = [a for a in candidates if tag in a.capabilities().tags]

        if not candidates:
            known = [(a.capabilities().name, sorted(a.capabilities().tags))
                     for a in self.registry.all() if step.kind in a.capabilities().kinds]
            raise AdapterNotFound(
                f"步骤 {step.name!r} 找不到适配器：kind={step.kind!r} "
                f"requires={list(step.requires)} adapter_name={step.adapter_name!r}；"
                f"候选（同 kind）: {known}"
            )
        if len(candidates) > 1:
            names = sorted(a.capabilities().name for a in candidates)
            raise AmbiguousAdapter(
                f"步骤 {step.name!r} 有 {len(candidates)} 个适配器都满足 "
                f"kind={step.kind!r} requires={list(step.requires)}：{names}。"
                f"请在本步写明 requires=(tag,) 或 adapter_name="
            )
        return candidates[0]

    # -------------------------------------------------- 跑流水线
    def run(self, pipeline: Pipeline, task: TaskSpec,
            on_step: Optional[Callable[[str, int, int, str], None]] = None,
            should_cancel: Optional[Callable[[], bool]] = None) -> PipelineRun:
        """以 task 为原始任务跑完整条流水线。

        on_step(state, done, total, step_name)：进度回调，state 为 start/done；
        should_cancel()：在**每一步开始前**询问是否已取消。

        取消的粒度就是「步」——编排层能在步与步之间停下，但停不下正在跑的那一步
        （那要各适配器自己实现 cancel）。这一点写在 service 的 cancel 文档里，
        不假装能瞬间中断。
        """
        started = time.monotonic()
        ctx = PipelineContext(task=task)
        run = PipelineRun(goal=pipeline.goal, steps=[])
        total = len(pipeline.steps)

        for index, step in enumerate(pipeline.steps):
            if should_cancel is not None and should_cancel():
                run.cancelled = True
                run.failed_at = step.name
                break
            if on_step is not None:
                on_step("start", index, total, step.name)
            adapter = self.resolve(step)
            dispatcher = Dispatcher(_single(adapter), project=self.project)
            step_task = step.build(ctx)
            job_id = dispatcher.dispatch(step_task)
            try:
                result = dispatcher.wait(job_id, timeout=self.timeout)
            except (RuntimeError, TimeoutError) as exc:
                if not step.optional:
                    run.failed_at = step.name
                    run.failure_detail = f"{type(exc).__name__}: {exc}"
                    if self.stop_on_failure:
                        run.elapsed = time.monotonic() - started
                        return run
                    continue
                raise RuntimeError(
                    f"可选步骤 {step.name!r} 不应失败到无法产出结果：{exc}"
                ) from exc

            step_run = StepRun(name=step.name, adapter=adapter.capabilities().name,
                               job_id=job_id, result=result)
            ctx.runs[step.name] = step_run
            for factory in step.checks:
                step_run.checks.extend(factory(ctx, step_run))
            if step.checks:
                run.checks.extend(step_run.checks)
            run.steps.append(step_run)

            if self.project is not None and self.archive_steps and step_run.ok:
                dispatcher.archive(job_id, note=f"{pipeline.goal} / {step.name}")
            if on_step is not None:
                on_step("done", index + 1, total, step.name)

            if not step_run.ok and self.stop_on_failure and not step.optional:
                run.failed_at = step.name
                run.failure_detail = "；".join(
                    f"{c.name}: {c.detail}" for c in step_run.checks if not c.passed)
                run.elapsed = time.monotonic() - started
                return run

        run.elapsed = time.monotonic() - started
        return run


def _single(adapter: Adapter) -> AdapterRegistry:
    """只装一个适配器的注册表：让每一步的 Dispatcher 不可能选错通道。"""
    registry = AdapterRegistry()
    registry.register(adapter)
    return registry


__all__ = [
    "AmbiguousAdapter",
    "Check",
    "CheckFn",
    "Orchestrator",
    "Pipeline",
    "PipelineContext",
    "PipelineRun",
    "Step",
    "StepRun",
    "artifact_exists",
    "cross_step_metric_matches",
    "metric_targets_met",
]
