"""自动布灯：导入灯具型号 + ArrangementFromSpace 排布（两批：筒灯+线性灯）。

对应 BetterGI 的「自动功能」。参数（params）：
- point_ies / linear_ies：筒灯/线性灯 IES 路径（默认用 WorkbenchConfig 的
  luminaire_point / luminaire_linear；空则跳过该批）
- count_x / count_y：排布网格（0 = 让 DIALux 自动）
"""
from __future__ import annotations

import logging
from pathlib import Path

from optiflow.workbench.task import Task, TaskContext, TaskResult

logger = logging.getLogger(__name__)

DEFAULT_POINT = "build/ies/fixed/NPTLED351_NVC.IES"
DEFAULT_LINEAR = "build/ies/linear/opple_LEDPanelRc-S-Re295-30W-4000-WH-U19.ies"


class LuminairesTask(Task):
    """自动布灯（导入型号 + 自动排布）。"""

    name = "自动布灯"
    priority = 50

    def run(self, ctx: TaskContext) -> TaskResult:
        cfg = ctx.config or {}
        params = ctx.params or {}
        point_ies = params.get("point_ies") or cfg.get("luminaire_point") or DEFAULT_POINT
        linear_ies = params.get("linear_ies") or cfg.get("luminaire_linear") or DEFAULT_LINEAR

        from src.executor.uia.luminaire import _last_ok, place_luminaires

        results = []
        for kind, ies in (("筒灯", point_ies), ("线性灯", linear_ies)):
            p = Path(ies)
            if not p.exists():
                ctx.log(f"跳过 {kind}：文件不存在 {p}")
                continue
            ctx.log(f"布灯 {kind}：{p.name}")
            events = place_luminaires(p, prototype_name=p.stem,
                                      cancel_check=ctx.cancelled)
            if not _last_ok(events):
                bad = [e for e in events if not e.ok]
                return TaskResult(False, f"{kind}布灯失败："
                                         f"{bad[-1].detail if bad else '无输出'}")
            results.append(kind)
        if not results:
            return TaskResult(False, "没有可布的灯（缺 IES 文件）")
        return TaskResult(True, f"布灯完成：{'、'.join(results)}")
