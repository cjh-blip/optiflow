"""计算监控触发器（实时 Trigger，只在 calculating 状态激活）。

移植 BetterGI「SupportedGameUiCategory 路由」：调度器每轮先感知状态，
本触发器只在 UiState.CALCULATING 被派发——轮询计算完成信号（标题回退/
进度消失），完成后回调 on_finished（供 Flow/悬浮窗接续「读结果」步骤）。

这是「一条龙」从建案走向「算出照度」的感知前置：没有它，计算触发后
只能靠 sleep 猜时长；有了它，算完即刻感知。
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from optiflow.workbench.trigger import Trigger
from optiflow.workbench.recognition import UiState

logger = logging.getLogger(__name__)


class CalcWatchTrigger(Trigger):
    """DIALux 计算监控（实时，仅 calculating 状态）。

    命中语义：检测到「计算已结束」（状态从 CALCULATING 离开时由
    on_state_change 触发一次，之后 on_tick 不再重复报）。
    """

    name = "计算监控"
    priority = 20
    exclusive = False
    background_ok = True          # 计算挂着时窗口可能不在前台
    supported_states = {UiState.CALCULATING, UiState.IDLE}

    def __init__(self, on_finished: Optional[Callable] = None):
        self.on_finished = on_finished   # callback(ctx) 计算完成时调用
        self._was_calculating = False
        self.finished_count = 0

    def on_state_change(self, ctx, old, new) -> None:
        # 计算结束边沿：CALCULATING → 其它（非 UNKNOWN 噪声）
        if old is UiState.CALCULATING and new is not UiState.CALCULATING:
            self._was_calculating = False
            self.finished_count += 1
            ctx.log(f"计算完成（第 {self.finished_count} 次）")
            if self.on_finished is not None:
                try:
                    self.on_finished(ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("计算完成回调异常：%s", exc)

    def on_tick(self, ctx, obs=None) -> bool:
        # 处于 CALCULATING 状态时标记（供边沿检测）
        if obs is not None and obs.state is UiState.CALCULATING:
            self._was_calculating = True
            return False   # 计算中不算「命中」，完成才算
        return False
