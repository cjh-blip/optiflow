"""通用自动化工作台：触发器调度器（Dispatcher）。

移植 BetterGI 的 TaskTriggerDispatcher（抄架构不抄代码）：
- 后台线程按 interval 周期 Tick
- **每轮先感知状态**（recognition.recognize_state，对应 BetterGI 的
  `Bv.WhichGameUiForTriggers`），再只派发「supported_states 匹配」的触发器
- 状态变化广播 on_state_change（对应 BetterGI 的 UI 变更跟踪）
- 命中（on_tick=True）→ 执行 on_trigger；exclusive 独占；支持 start/stop

这是「实时感知→自动响应」的引擎：工作台启动后持续监控目标程序状态，
命中即处理——不用用户手动点。
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from .recognition import StateObservation, UiState, recognize_state
from .trigger import Trigger

logger = logging.getLogger(__name__)


class Dispatcher:
    """触发器调度器：感知状态 → 路由触发器 → 命中即响应。"""

    def __init__(
        self,
        *,
        interval: float = 0.5,
        state_fn=None,
        process_name: str = "DIALux_x64",
    ):
        self.interval = interval
        self.process_name = process_name
        # 状态识别函数可注入（测试用假状态；默认真机 L2 感知）
        self._state_fn = state_fn or (lambda: recognize_state(process_name))
        self.triggers: List[Trigger] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_obs: Optional[StateObservation] = None

    def add(self, *triggers: Trigger) -> "Dispatcher":
        self.triggers.extend(triggers)
        return self

    # ------------------------------------------------------------ 运行
    def start(self, ctx) -> None:
        """后台启动轮询循环。幂等：已启动则忽略。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        for t in self.triggers:
            if t.enabled:
                try:
                    t.init(ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("触发器 %s init 异常：%s", t.name, exc)
        self._thread = threading.Thread(target=self._loop, args=(ctx,), daemon=True)
        self._thread.start()
        logger.info("调度器启动：%d 个触发器", len(self.triggers))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("调度器停止")

    @property
    def current_state(self) -> UiState:
        return self.last_obs.state if self.last_obs else UiState.UNKNOWN

    # ------------------------------------------------------------ 内部
    def _loop(self, ctx) -> None:
        while not self._stop.is_set():
            self._tick_once(ctx)
            self._stop.wait(self.interval)

    def _tick_once(self, ctx) -> None:
        # 1) 感知：目标程序现在处于什么状态（BetterGI: WhichGameUiForTriggers）
        obs = self._state_fn()
        prev = self.last_obs
        self.last_obs = obs
        state_changed = prev is not None and prev.state is not obs.state

        # 2) 状态变化广播（所有 enabled 触发器都收到，无论是否匹配该状态）
        if state_changed:
            for t in self.triggers:
                if not t.enabled:
                    continue
                try:
                    t.on_state_change(ctx, prev.state, obs.state)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("触发器 %s on_state_change 异常：%s", t.name, exc)

        # 3) 状态路由：只派发 accepts(state) 的触发器（BetterGI: SupportedGameUiCategory）
        exclusive_owner = next(
            (t for t in self.triggers
             if t.enabled and t.exclusive and getattr(t, "_active", False)),
            None,
        )

        fired = False
        for t in sorted(self.triggers, key=lambda x: -x.priority):
            if not t.enabled:
                continue
            if exclusive_owner is not None and t is not exclusive_owner:
                continue  # 独占中，其它暂停
            if not t.accepts(obs.state):
                continue  # 状态不匹配，跳过（BetterGI 的 UI 类别过滤）
            try:
                if t.on_tick(ctx, obs):
                    fired = True
                    if t.exclusive:
                        t._active = True
                    try:
                        t.on_trigger(ctx)
                    finally:
                        if t.exclusive:
                            t._active = False
            except Exception as exc:  # noqa: BLE001 - 单个触发异常不拖垮调度
                logger.warning("触发器 %s 异常：%s", t.name, exc)
            if fired and not getattr(t, "exclusive", False):
                # 非独占：一次 tick 只处理一个命中（避免同轮多次响应）
                break
