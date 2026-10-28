"""选灯：反推 + 筛库 → 候选（Task 2）。

链路：`light_selection.backcalc` 算单灯目标光通量 → `luminaire_library` 按目标挑候选。
本模块只做编排与索引定位，不重算几何、不改反推公式。

索引查找顺序：显式 `index_path` → 环境变量 `GUANGSHU_LUM_INDEX` → 包内
`data/luminaire_index.json` → 报人话错误。**找不到就报错指路，不静默返回空候选**
——测评里用户最怕的就是点了按钮什么都不发生。

灯数（count）是选灯的硬前提：选的是「单灯规格」，没有灯数就无法把总光通量摊到单灯。
缺 count 时直接报错并指向 `plan.light_backcalc`，不用臆造的默认灯数糊过去。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from optiflow.light_selection import backcalc
from optiflow.luminaire_library import load_index, select_candidates

_INDEX_ENV = "GUANGSHU_LUM_INDEX"
_PACKAGED_INDEX = Path(__file__).resolve().parent / "data" / "luminaire_index.json"


def _resolve_index(explicit: Optional[str]) -> Path:
    """定位灯具库索引文件；找不到时抛带指路的 ValueError。"""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise ValueError(f"灯具库索引不存在：{p}")
        return p
    env = os.environ.get(_INDEX_ENV)
    if env:
        p = Path(env)
        if not p.exists():
            raise ValueError(f"环境变量 {_INDEX_ENV} 指向的灯具库索引不存在：{p}")
        return p
    if _PACKAGED_INDEX.exists():
        return _PACKAGED_INDEX
    raise ValueError(
        "需要先导入灯具库索引："
        f"用 index_path 指定，或设环境变量 {_INDEX_ENV}，或把索引放到 {_PACKAGED_INDEX}"
    )


def select_for_room(
    *,
    area_m2: float,
    target_lux: float,
    count: Optional[int] = None,
    cct_k: Optional[int] = None,
    max_power_w: Optional[float] = None,
    cu: Optional[float] = None,
    mf: Optional[float] = None,
    lpd_cap: Optional[float] = None,
    index_path: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """按房间参数挑灯具候选。

    返回 `{candidates, backcalc, state, evidence}`。`state.area_m2` 回显给界面，
    用于判断「用户改了面积但没重选」（Review Focus ⑤）。
    """
    idx = _resolve_index(index_path)
    calc = backcalc(area_m2=area_m2, target_lux=target_lux, cu=cu, mf=mf,
                    lpd_cap=lpd_cap, count=count)
    per_lamp = calc.get("per_lamp") or {}
    flux = per_lamp.get("flux_lm")
    if not flux:
        raise ValueError("选灯需要灯数：请给出 count（可先用 plan.light_backcalc 反推灯数区间）")

    records = load_index(idx)
    candidates = select_candidates(
        records,
        per_lamp_flux_lm=float(flux),
        cct_k=cct_k,
        max_power_w=max_power_w,
        limit=limit,
    )
    return {
        "candidates": candidates,
        "backcalc": calc,
        "state": {
            "area_m2": float(area_m2),
            "target_lux": float(target_lux),
            "count": int(count) if count is not None else None,
            "cct_k": cct_k,
            "per_lamp_flux_lm": float(flux),
            "index": str(idx),
            "index_size": len(records),
        },
        "evidence": {
            "inputs": {
                "area_m2": area_m2,
                "target_lux": target_lux,
                "count": count,
                "cct_k": cct_k,
                "max_power_w": max_power_w,
                "limit": limit,
            },
            "index": str(idx),
            "rank_rule": "色温/功率硬筛，光通量差距排序（索引无流明时按功率升序）",
        },
    }
