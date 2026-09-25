"""自动保存应答触发器（实时 Trigger，只在 dialog 状态激活）。

对应 BetterGI 的「实时触发器 + SupportedGameUiCategory」：
- 调度器每轮先感知 DIALux 状态（recognition.recognize_state）
- 本触发器 supported_states={UiState.DIALOG}：只在「有模态弹窗」状态被派发
- 命中 → 调 driver.auto_answer_save_prompt 自动点「是」（保存确认）
- 其它状态（建模中/计算中）完全不空跑——BetterGI 式状态路由
"""
from __future__ import annotations

import logging

from optiflow.workbench.trigger import Trigger
from optiflow.workbench.recognition import UiState

logger = logging.getLogger(__name__)


class AutoSaveTrigger(Trigger):
    """DIALux 保存确认弹窗自动应答（实时）。"""

    name = "自动保存应答"
    priority = 10        # 低优先级：不抢其它触发
    exclusive = False    # 可后台
    supported_states = {UiState.DIALOG}

    def __init__(self, process_name: str = "DIALux_x64"):
        self.process_name = process_name
        self._answered = 0

    def on_tick(self, ctx, obs=None) -> bool:
        # 命中条件：有保存确认框需要应答
        try:
            from src.executor.uia.driver import auto_answer_save_prompt
            n = auto_answer_save_prompt(self.process_name)
        except Exception as exc:  # noqa: BLE001 - 探测失败不致命
            logger.debug("自动保存探测异常：%s", exc)
            return False
        if n > 0:
            self._answered += n
            ctx.log(f"自动保存应答：点了 {n} 个「是」（累计 {self._answered}）")
            return True
        return False

    def on_trigger(self, ctx):
        return None
