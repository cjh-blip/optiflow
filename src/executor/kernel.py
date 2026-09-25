"""执行器内核：遍历 ActionPlan、按 type 派发 handler、统一重试、统一进度上报。

计划文档 `docs/plan-mvp3-luminaire.md` P2：照 BGI 三层切法，把重试与进度从
各 handler 里抽出来放内核，handler 只负责「一条动作怎么干」。

与旧 `src/executor/key_mouse.py` 的关系
--------------------------------------
- ``key_mouse.py`` 的 ``BaseExecutor`` 是 MVP1 的占位实现（dry-run 打日志、
  ProgramExecutor 全返回 OK、KeyMouseExecutor 定位恒 None），保留作为参考/降级档。
- ``kernel.py`` 是现役入口：``execute_plan(actions, driver)`` 把每条动作交给
  ``driver.execute(action)``（driver 实现 handler 分发），内核只做四件事：
  顺序遍历、统一重试（RETRY）、HALT 上抛、进度回调（title/weight 语义）。

handler 约定（在 driver 侧实现，见 ``src/executor/uia/driver.py`` 扩展点）::

    def execute(self, action: dict) -> ActionResult:
        # OK / RETRY / HALT
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol

from .actions import ActionResult

logger = logging.getLogger(__name__)

MAX_RETRY = 3


class ExecutorHalted(RuntimeError):
    """human_confirm 被拒或 handler 返回 HALT 时抛出（不是失败，是人工叫停）。"""


class Driver(Protocol):
    """handler 分发的最小接口。真实实现见 ``src.executor.uia`` 的驱动。"""

    def execute(self, action: dict) -> ActionResult: ...


@dataclass
class PlanEvent:
    """一条动作执行完后的进度事件。``progress`` 按权重累计 0~100。"""

    index: int          # 第几条（1 起）
    total: int          # 共几条
    action: dict
    ok: bool
    detail: str
    title: str          # 动作的显示名（ActionPlan 已带 title/weight，见 ACTION_META）
    weight: float
    progress: float


def execute_plan(
    actions: List[dict],
    driver: Driver,
    *,
    on_step: Optional[Callable[[PlanEvent], None]] = None,
    retry: int = MAX_RETRY,
    halt_on_unknown: bool = True,
) -> List[PlanEvent]:
    """按顺序执行 ActionPlan。

    - 每条动作先取 title/weight（缺失退化成 type/1.0，与 ``_stamp_progress`` 一致）。
    - ``driver.execute`` 返回 RETRY → 按 ``retry`` 次重试；HALT → 抛 ``ExecutorHalted``；
      超过次数 → 抛 ``RuntimeError`` 并 dump 动作上下文。
    - ``halt_on_unknown``：driver 抛 ``NotImplementedError``（未知 type）时，True 上抛
      （不静默跳过，符合计划 P2「未知 type 报错不静默跳过」），False 记日志后继续。
    - 每条动作完成后回调 ``on_step(PlanEvent)``，即使失败也回调（ok=False）。
    """
    if not actions:
        return []
    total = len(actions)
    total_weight = sum(float(a.get("weight", 1.0)) for a in actions) or 1.0
    done_weight = 0.0
    events: List[PlanEvent] = []

    for idx, action in enumerate(actions, start=1):
        title = str(action.get("title") or action.get("type") or "unknown")
        weight = float(action.get("weight", 1.0))

        result: ActionResult = "OK"
        detail = ""
        skipped = False
        for attempt in range(1, retry + 1):
            try:
                result = driver.execute(action)
            except NotImplementedError:
                if halt_on_unknown:
                    _dump_context(action)
                    raise
                logger.warning("未知动作类型 %s（id=%s），跳过",
                               action.get("type"), action.get("id"))
                result, detail, skipped = "OK", f"unknown-type:{action.get('type')}", True
                break
            except ExecutorHalted:
                raise
            except Exception as exc:  # noqa: BLE001 - handler 内部错误统一按 RETRY 处理
                logger.warning("动作 %s 第 %d 次异常：%s", action.get("type"), attempt, exc)
                if attempt >= retry:
                    _dump_context(action)
                    raise RuntimeError(
                        f"动作 {action.get('type')}（id={action.get('id')}）"
                        f"异常 {retry} 次仍失败"
                    ) from exc
                continue

            if result == "OK":
                break
            if result == "HALT":
                raise ExecutorHalted(
                    f"动作 {action.get('type')} 返回 HALT（id={action.get('id')}）"
                )
            # RETRY
            if attempt >= retry:
                _dump_context(action)
                raise RuntimeError(
                    f"executor aborted after {retry} retries at {action.get('type')}"
                )
            logger.warning("动作 %s 第 %d 次返回 RETRY，重试中…",
                           action.get("type"), attempt)

        done_weight += weight
        ev = PlanEvent(
            index=idx,
            total=total,
            action=action,
            ok=(result == "OK" and not skipped),
            detail=detail,
            title=title,
            weight=weight,
            progress=min(100.0, done_weight / total_weight * 100.0),
        )
        events.append(ev)
        logger.info("[%5.1f%%] %d/%d %s %s%s",
                    ev.progress, idx, total, title,
                    "OK" if ev.ok else "失败",
                    f"（{detail}）" if detail else "")
        if on_step is not None:
            on_step(ev)

    return events


def _dump_context(action: dict) -> None:
    logger.error("CONTEXT: %s", json.dumps(action, ensure_ascii=False, default=str))
