# -*- coding: utf-8 -*-
"""选灯反推：面积 ＋ 目标照度 → 全屋所需光通量、功率天花板、单灯区间。

一把尺子，两条护栏：

- 只算「多大」，不排「放哪」（布灯归 planner / 真机链，最终进 DIALux 判真伪）；
- 不推荐具体型号（没有产品库，具体选型是人拿区间去对厂商目录）。

公式（利用系数法）：

    工作面照度  E = Φ_total × CU × MF / A   →   反推  Φ_total = E × A / (CU × MF)
    功率天花板  P_budget = LPD_cap × A            （给了 LPD 上限才算）
    单灯（给了灯数 N）  Φ_lamp = Φ_total / N ；  P_lamp = P_budget / N

返回里 `params_used` 标明哪些用了缺省值，`evidence.formula` 带公式，
供报告「灯具选型列表」引作依据；数字取整到工程说得过去的精度。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_CU = 0.6    # 宽配光、浅色顶棚的常见起点；拿不准就让 DIALux 校
DEFAULT_MF = 0.8    # 一般清洁房间的维护系数常见取值
FORMULA = "Φ_total = E_target × A / (CU × MF)"


def backcalc(*, area_m2: float, target_lux: float,
             cu: Optional[float] = None, mf: Optional[float] = None,
             lpd_cap: Optional[float] = None,
             count: Optional[int] = None) -> Dict[str, Any]:
    """反推选灯规格区间。参数非法一律抛 ValueError 且带人话（注册表纪律：失败抛异常，壳负责呈现）。"""

    def _positive(value: Any, name: str, label: str) -> float:
        if value is None or float(value) <= 0:
            raise ValueError(f"{label}必须为正（收到 {name}={value!r}）")
        return float(value)

    area = _positive(area_m2, "area_m2", "面积")
    lux = _positive(target_lux, "target_lux", "目标照度")
    cu_v = DEFAULT_CU if cu is None else float(cu)
    mf_v = DEFAULT_MF if mf is None else float(mf)
    if not 0 < cu_v <= 1:
        raise ValueError(f"利用系数应在 (0, 1]（收到 cu={cu_v!r}）")
    if not 0 < mf_v <= 1:
        raise ValueError(f"维护系数应在 (0, 1]（收到 mf={mf_v!r}）")
    lpd: Optional[float] = None
    if lpd_cap is not None:
        lpd = _positive(lpd_cap, "lpd_cap", "LPD 上限")
    if count is not None and int(count) < 1:
        raise ValueError(f"灯数至少为 1（收到 count={count!r}）")

    total_lm = round(lux * area / (cu_v * mf_v))
    budget_w = round(lpd * area, 1) if lpd is not None else None

    per_lamp: Optional[Dict[str, Any]] = None
    if count is not None:
        n = int(count)
        per_lamp = {
            "flux_lm": round(total_lm / n),
            "power_w": round(budget_w / n, 1) if budget_w is not None else None,
        }

    return {
        "total_flux_lm": total_lm,
        "power_budget_w": budget_w,
        "per_lamp": per_lamp,
        "params_used": {
            "cu": cu_v,
            "mf": mf_v,
            "cu_is_default": cu is None,
            "mf_is_default": mf is None,
        },
        "state": {
            "area_m2": area,
            "target_lux": lux,
            "total_flux_lm": total_lm,
            "power_budget_w": budget_w,
            "count": int(count) if count is not None else None,
            "flux_per_lamp": per_lamp["flux_lm"] if per_lamp else None,
        },
        "evidence": {
            "inputs": {"area_m2": area, "target_lux": lux, "cu": cu_v, "mf": mf_v,
                       "lpd_cap": lpd, "count": int(count) if count is not None else None},
            "formula": FORMULA,
        },
    }
