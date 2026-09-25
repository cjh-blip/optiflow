"""通用自动化工作台：实时触发器（Trigger）抽象。

移植 BetterGI 的 ITaskTrigger（抄架构不抄代码）：
- 需要「短周期持续轮询目标状态、命中即响应」的能力用 Trigger（如：识别到
  保存弹窗→自动应答、计算完成→收结果）
- 需要「休眠等待且有一定流程」的长任务用 Task（见 task.py）

Trigger 由调度器（dispatcher.py）周期调用：先感知状态（recognition.py），
再只把匹配状态的触发器唤醒——同 BetterGI 的 SupportedGameUiCategory 路由。
"""
from __future__ import annotations

import abc
from typing import Any, Optional, Set


class Trigger(abc.ABC):
    """触发器基类。

    属性（对应 BetterGI ITaskTrigger）：
    - ``name``：中文名（悬浮窗/日志显示）
    - ``enabled``：开关
    - ``priority``：越大越先执行
    - ``exclusive``：独占（执行时其它触发器暂停）
    - ``background_ok``：目标程序未激活也能跑（对应 IsBackgroundRunning）
    - ``supported_states``：只在哪些 UiState 激活（None=任意状态；对应
      SupportedGameUiCategory——防止触发器在不相关状态空跑）

    方法：
    - ``init(ctx)``：调度开始时初始化一次
    - ``on_tick(ctx, obs)``：每个轮询周期调用（obs=状态观察）；返回 True 表示命中
    - ``on_trigger(ctx)``：命中后执行的动作
    - ``on_state_change(ctx, old, new)``：状态变化时广播（可选实现）
    """

    name: str = "未命名触发器"
    enabled: bool = True
    priority: int = 50
    exclusive: bool = False
    background_ok: bool = False
    supported_states: Optional[Set] = None   # Set[UiState]；None=任意

    def init(self, ctx) -> None:  # pragma: no cover - 子类可选实现
        """调度开始时调用一次（加载资源/初始化状态）。"""

    @abc.abstractmethod
    def on_tick(self, ctx, obs=None) -> bool:
        """每个轮询周期调用（obs=StateObservation）；返回 True 表示命中。"""
        raise NotImplementedError

    def on_trigger(self, ctx) -> Any:  # pragma: no cover - 子类可选实现
        """命中后的动作。"""
        return None

    def on_state_change(self, ctx, old, new) -> None:  # pragma: no cover
        """状态变化广播（可选实现）。"""

    def accepts(self, state) -> bool:
        """状态路由：本触发器是否在该状态下激活。"""
        return self.supported_states is None or state in self.supported_states

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} name={self.name!r} enabled={self.enabled}>"
