"""通用自动化工作台：任务（Task）抽象。

全抄 BetterGI 的 GameTask 模式（抄架构不抄代码）：
- 每个自动化能力 = 一个 Task（执行单元），自带 Name/Enabled/参数
- 实时响应用 Trigger（见 trigger.py），长流程用 Task.run()
- 一条龙 = Flow（见 flow.py），把多个 Task 按顺序串起来

**不绑定任何具体软件**：Task 只约定输入（ctx 上下文）与输出（ok/detail），
DIALux / Zemax / Transport 各自实现 tasks/<app>/ 下的具体任务。

设计原则（用户定）：先堆出足够多的功能城堡，用户再删减——本文件只放
通用骨架，具体能力在 tasks/ 下堆。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class TaskResult:
    """任务执行结果。ok=False 时 detail 是给人看的失败原因。"""

    ok: bool
    detail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


class TaskContext:
    """任务执行上下文：进程句柄、配置、日志回调、取消信号。

    各任务通过它访问目标程序（DIALux/Zemax…）与共享配置，避免任务之间
    直接耦合。未来换软件只需提供一个新的 Context 实现。
    """

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        log: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.config = config or {}
        self._log = log or (lambda _m: None)
        self._cancel = cancel_check or (lambda: False)

    def log(self, msg: str) -> None:
        self._log(msg)

    def cancelled(self) -> bool:
        return self._cancel()


class Task(abc.ABC):
    """任务基类。子类实现 ``run(ctx)`` 做一件事。

    属性约定（同 BetterGI）：
    - ``name``：中文名，UI 悬浮窗显示用（如「自动布灯」）
    - ``enabled``：开关
    - ``priority``：并行触发时的优先级（越大越先）
    - ``exclusive``：是否独占（执行时其它触发暂停）
    """

    name: str = "未命名任务"
    enabled: bool = True
    priority: int = 50
    exclusive: bool = False

    @abc.abstractmethod
    def run(self, ctx: TaskContext) -> TaskResult:
        """执行任务主体。返回 TaskResult。"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} name={self.name!r} enabled={self.enabled}>"
