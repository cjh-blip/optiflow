"""UIA 驱动：把 ActionPlan 动作分发到 DIALux 通道（实现 kernel.Driver 协议）。

计划文档 P2 step 2：每个 action type 一个 handler。当前接线：

- ``create_project`` / ``create_storey`` / ``create_space`` → **STF 批量通道**：
  STF 是一次导入全部房间的（不是逐个建），所以由第一个 ``create_space`` 触发
  整批 ``ir_to_stf`` + ``run_import``，后续房间动作幂等返回 OK（房间已整批落地）。
- ``place_luminaire`` → **UIA 真通道**（``luminaire_channel="arrangement"``）：
  调 ``src.executor.uia.luminaire.place_luminaires`` 导入该灯具型号的 IES 并
  ArrangementFromSpace 自动排布（2026-09-07 真机闭环）。默认 ``"stub"`` 仍为
  安全模式（只收集坐标不碰真机，供测试/离线用）。
- ``run_calculation`` / ``export_report`` → OK（占位；KANBAN：这两类从未真跑过，
  weight 1.0 是占位，别当实测）。

用法::

    driver = UiaDriver(ir=ir, stf_path="build/demo_room.stf")
    events = execute_plan(plan, driver, on_step=...)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from optiflow.adapters.dialux.stf import ir_to_stf, select_rooms
from ..actions import ActionResult

logger = logging.getLogger(__name__)


class UiaDriver:
    """DIALux UIA 驱动：按 action type 分发，批量通道走 STF。

    ``luminaire_channel`` 取值：
    - ``"stub"``（默认）：place_luminaire 只收集坐标，不碰真机（测试/离线安全）。
    - ``"arrangement"``：真通道——调 ``luminaire.place_luminaires`` 导入 IES 并自动排布
      （2026-09-07 真机闭环，OPPLE Re295 已验证）。
    """

    def __init__(
        self,
        ir: dict,
        *,
        stf_path: str | Path = "build/demo_room.stf",
        luminaire_channel: str = "stub",
        import_fn=None,
        select_rooms_fn=None,
        ir_to_stf_fn=None,
        place_fn=None,
    ):
        self.ir = ir
        self.stf_path = Path(stf_path)
        self.luminaire_channel = luminaire_channel
        # 依赖注入：测试可替换真通道
        self._import = import_fn
        self._select_rooms = select_rooms_fn or select_rooms
        self._ir_to_stf = ir_to_stf_fn or ir_to_stf
        self._place_fn = place_fn

        self._stf_imported = False
        self.luminaires: List[dict] = []      # 收集 place_luminaire 的输入
        self.created_spaces: List[str] = []   # 已确认落地的房间 id

    # ------------------------------------------------------------ Driver 协议
    def execute(self, action: dict) -> ActionResult:
        atype = action.get("type")
        inputs = action.get("inputs") or {}

        if atype in ("create_project", "create_storey"):
            return "OK"  # STF 整批导入隐含了工程/楼层

        if atype == "create_space":
            return self._on_create_space(inputs)

        if atype == "place_luminaire":
            return self._on_place_luminaire(inputs)

        if atype == "human_confirm":
            # UIA 通道不弹 CLI；由上层人工确认。这里视为已确认（demo 语义），
            # 与 ProgramExecutor 的交互式确认区分。真要强制人工就把这个 handler 换掉。
            logger.warning("UiaDriver 跳过 human_confirm（视为已确认，id=%s）",
                           action.get("id"))
            return "OK"

        if atype in ("run_calculation", "export_report"):
            logger.info("动作 %s 为占位实现（从未真跑过，别当实测）", atype)
            return "OK"

        raise NotImplementedError(f"UiaDriver 未实现动作类型: {atype}")

    # ------------------------------------------------------------ handlers
    def _on_create_space(self, inputs: dict) -> ActionResult:
        """第一个 create_space 触发整批 STF 导入；后续幂等 OK。"""
        sid = inputs.get("space_id")
        if self._stf_imported:
            # 房间已经整批落地（STF 一次导全部）
            if sid:
                self.created_spaces.append(sid)
            return "OK"

        # 只导真房间（与 MVP2 口径一致：家具伪 space 不进 STF）
        try:
            rooms = [s for s in self._select_rooms(self.ir) if not self._is_furniture(s)]
        except ValueError as exc:
            # select_rooms 对「全为家具伪 space」抛 ValueError（需要 --include-furniture）。
            # 在 ActionPlan 语义里这就是「没有要导的房间」：幂等返回 OK，不炸链。
            logger.info("无可导出真房间（%s），create_space 幂等 OK", exc)
            self._stf_imported = True
            if sid:
                self.created_spaces.append(sid)
            return "OK"
        if not rooms:
            logger.warning("IR 没有可导出的真房间，create_space 返回 OK（空）")
            self._stf_imported = True
            return "OK"

        stf_text = self._ir_to_stf(self.ir)
        self.stf_path.parent.mkdir(parents=True, exist_ok=True)
        self.stf_path.write_text(stf_text, encoding="utf-8", newline="\n")
        logger.info("STF 生成 %d 个房间 → %s", len(rooms), self.stf_path)

        if self._import is not None:
            events = self._import(self.stf_path)
            if not events:
                # 空列表 = 调用方没真正导入（测试/demo 桩），与未注入同语义
                logger.info("import_fn 返回空，跳过真机导入（测试/demo 模式）")
            else:
                ok = bool(events) and all(e.ok for e in events) and events[-1].name == "done"
                if not ok:
                    logger.error("STF 导入未完成（最后一个事件: %s）",
                                 events[-1].name if events else "无输出")
                    return "RETRY"
        else:
            logger.info("未注入 import_fn，跳过真机导入（测试/demo 模式）")

        self._stf_imported = True
        if sid:
            self.created_spaces.append(sid)
        return "OK"

    def _on_place_luminaire(self, inputs: dict) -> ActionResult:
        """place_luminaire 分发：stub 收集 vs arrangement 真通道。

        arrangement 通道：按 IR 灯具的 symbol 推断 IES 文件（先查 build/ies/ 与
        build/ies/fixed/，再查 build/ies/linear/），调 ``place_luminaires`` 真机导入排布。
        找不到对应文件时记告警并返回 OK（该盏跳过，不阻断整批）。
        """
        if self.luminaire_channel == "stub":
            self.luminaires.append(dict(inputs))
            logger.info("place_luminaire（stub，未落地）: %s", inputs.get("luminaire_id"))
            return "OK"
        if self.luminaire_channel != "arrangement":
            raise NotImplementedError(
                f"luminaire_channel={self.luminaire_channel} 未实现（用 stub 或 arrangement）"
            )

        symbol = str(inputs.get("symbol") or inputs.get("luminaire_id") or "")
        ies = self._resolve_luminaire_ies(symbol, inputs)
        if ies is None:
            logger.warning("place_luminaire 找不到 IES 文件（symbol=%s），跳过该盏", symbol)
            self.luminaires.append(dict(inputs))
            return "OK"

        if self._place_fn is not None:
            # 依赖注入（测试/离线桩）：返回 (ok, detail)
            ok, detail = self._place_fn(ies, inputs)
            self.luminaires.append(dict(inputs))
            if not ok:
                logger.error("place_luminaire 失败：%s（%s）", symbol, detail)
                return "RETRY"
            logger.info("place_luminaire 落地：%s", symbol)
            return "OK"

        from .luminaire import place_luminaires

        events = place_luminaires(
            ies,
            prototype_name=str(inputs.get("mount") or ""),
            count_x=0, count_y=0,   # 让 DIALux 按空间自动排
            process_name="DIALux_x64",
        )
        self.luminaires.append(dict(inputs))
        if not events or not events[-1].ok:
            detail = events[-1].detail if events else "无输出"
            logger.error("place_luminaire 失败：%s（%s）", symbol, detail)
            return "RETRY"
        logger.info("place_luminaire 落地：%s（%d 盏）", symbol, len(events))
        return "OK"

    @staticmethod
    def _resolve_luminaire_ies(symbol: str, inputs: dict) -> Optional[Path]:
        """从 build/ies/ 目录按 symbol 推理 IES 文件路径。

        匹配顺序（严格优先，避免短 symbol 子串误伤）：
        1. inputs 里显式 ies_path；
        2. build/ies/fixed/<symbol>.IES、build/ies/<symbol>.IES（精确文件名）；
        3. build/ies/linear/*<symbol>* 子串匹配——仅当 symbol 长度 ≥4
           （防止 "L1" 这类短 id 撞上 LEDLima-L12 之类）；找不到返回 None。
        """
        import glob as _glob

        explicit = inputs.get("ies_path")
        if explicit:
            p = Path(str(explicit))
            if p.exists() and p.suffix.lower() in (".ies", ".ldt", ".gldf", ".uld"):
                return p

        base = Path("build/ies")
        # 精确匹配：symbol 可能已带扩展名（NPTLED351_NVC.IES）或不带（NPTLED351）
        ext = Path(symbol).suffix.lower()
        if ext in (".ies", ".ldt", ".gldf", ".uld"):
            # 已带扩展名：直接精确找
            for p in (base / "fixed" / symbol, base / symbol):
                if p.exists():
                    return p
            if len(symbol) >= 4:
                hits = sorted(_glob.glob(str(base / f"linear/*{symbol}*")))
                for c in hits:
                    p = Path(c)
                    if p.exists():
                        return p
            return None
        # 不带扩展名：精确匹配常见大小写
        for pat in (f"fixed/{symbol}.IES", f"fixed/{symbol}.ies",
                    f"{symbol}.IES", f"{symbol}.ies"):
            p = Path(base / pat)
            if p.exists():
                return p
        # 子串兜底（短 id 不做子串，防误伤）
        if len(symbol) >= 4:
            hits = sorted(_glob.glob(str(base / f"linear/*{symbol}*")))
            for c in hits:
                p = Path(c)
                if p.exists() and p.suffix.lower() in (".ies", ".ldt", ".gldf", ".uld"):
                    return p
        return None

    @staticmethod
    def _is_furniture(space: dict) -> bool:
        name = (space.get("name") or "")
        return name.startswith("家具_") or space.get("kind") == "furniture"
