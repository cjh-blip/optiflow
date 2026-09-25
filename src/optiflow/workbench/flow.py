"""通用自动化工作台：一条龙流程（Flow）。

全抄 BetterGI 的 OneDragonFlowConfig（抄架构不抄代码）：
- Flow = 一组按顺序执行的任务，带开关 + 断点续跑
- 支持任务重复添加（同任务不同参数 = 两个实例）
- next_task_id 记录下次从哪开始（断点续跑）

示例::

    flow = Flow("一键建案")
    flow.add(room_task, params={"dwg": "布局图.dwg"})
    flow.add(luminaires_task, params={"ies": "auto"})
    flow.add(report_task)
    result = flow.run(ctx)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .task import Task, TaskContext, TaskResult

logger = logging.getLogger(__name__)


@dataclass
class FlowStep:
    """流程中的一步：任务 + 该步参数 + 开关。"""

    task: Task
    step_id: str
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    title: str = ""          # 悬浮窗显示名（默认取 task.name）
    weight: float = 1.0      # 进度权重


class Flow:
    """一条龙：按顺序执行多个任务，带开关/断点/进度。"""

    def __init__(self, name: str = "未命名流程", resume_from: Optional[str] = None):
        self.name = name
        self.steps: List[FlowStep] = []
        self._next_id = 0
        self._resume_from = resume_from      # 断点：从某 step_id 开始

    # ------------------------------------------------------------ 构建
    def add(
        self,
        task: Task,
        *,
        params: Optional[Dict[str, Any]] = None,
        title: str = "",
        weight: float = 1.0,
        enabled: bool = True,
    ) -> "Flow":
        """加一步。同任务可重复添加（每次一个实例）。"""
        self._next_id += 1
        sid = f"{type(task).__name__}#{self._next_id}"
        self.steps.append(FlowStep(
            task=task, step_id=sid, params=params or {},
            title=title or task.name, weight=weight, enabled=enabled,
        ))
        return self

    def disable(self, step_id: str) -> None:
        for s in self.steps:
            if s.step_id == step_id:
                s.enabled = False

    # ------------------------------------------------------------ 执行
    def run(
        self,
        ctx: TaskContext,
        *,
        on_step: Optional[callable] = None,
    ) -> List[TaskResult]:
        """顺序执行启用的步骤。``on_step(step_index, step, result)`` 回调进度。

        返回每步结果（失败即停：返回已执行部分，后续不跑）。
        """
        results: List[TaskResult] = []
        resumed = self._resume_from is None
        for idx, step in enumerate(self.steps):
            if not step.enabled:
                continue
            if not resumed:
                if step.step_id == self._resume_from:
                    resumed = True
                else:
                    continue
            ctx.log(f"▶ {step.title}")
            try:
                result = step.task.run(ctx)
            except Exception as exc:  # noqa: BLE001 - 任务异常视为失败
                logger.exception("流程步骤 %s 异常", step.title)
                result = TaskResult(False, f"异常：{exc}")
            results.append(result)
            if on_step is not None:
                on_step(idx, step, result)
            if not result.ok:
                ctx.log(f"✗ {step.title}：{result.detail}")
                break
            ctx.log(f"✓ {step.title}")
            if ctx.cancelled():
                results.append(TaskResult(False, "已停止"))
                break
        return results

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Flow {self.name!r} steps={len(self.steps)}>"
