"""灯具归属 Join：把解析出的灯具按坐标归入 IR 的对应 space。

与 planner/core 解耦：外层按「灯具维度」循环，命中一个 space 就 break；命中多个 space 取第一个并 WARNING。
"""
from __future__ import annotations

from typing import List

from .core import point_in_polygon  # 保持射线法实现


def _dims_from_attrs(attrs) -> dict:
    """从 parser 的 Luminaire.attrs 提取选型需要的尺寸，转成 IR 的 dims 字段。

    此前 join 只写 symbol/x/y/z/catalog_match，把 attrs 整个丢掉，导致选型拿不到尺寸
    （筒灯半径、灯带长宽），只能从 symbol 字符串里反解 —— 那是脆的。这里显式搬运。

    单位统一 mm，与 parser 的 attrs 一致。找不到任何尺寸时返回空 dict（调用方不写该字段）。

    尺寸经浮点换算会带出 ``1555.0000000000014`` 这类噪声，圆到 3 位小数（微米级）——
    对灯具选型远够，也让同型号灯具的 dims 真正相等，便于按 dims 分组统计与去重。
    """
    if not isinstance(attrs, dict):
        return {}
    dims: dict = {}
    for src_key, dst_key in (
        ("radius_mm", "radius_mm"),
        ("rect_w_mm", "w_mm"),
        ("rect_h_mm", "h_mm"),
    ):
        val = attrs.get(src_key)
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if fval > 0:
            dims[dst_key] = round(fval, 3)
    return dims


def join_luminaires(ir: dict, lumis: list) -> List[str]:
    """把 lumis（optiflow.adapters.dialux.dxf.Luminaire 对象列表）按坐标归入 IR.spaces[*].luminaires。

    返回 warnings 列表（每盏未归属 / 多归属仅 1 条告警，总量 ≤ len(lumis)）。
    """
    warnings: List[str] = []

    # 先重置所有 space 的 luminaires 列表，避免重复 append（spec AC-4 要求不重复）
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            space["luminaires"] = []

    for lum in lumis:
        matched_spaces: list = []  # [(storey_idx, space_idx)]
        lp = (lum.x, lum.y)
        for si, storey in enumerate(ir.get("storeys", [])):
            for pi, space in enumerate(storey.get("spaces", [])):
                poly = space.get("polygon", [])
                if not poly:
                    continue
                if point_in_polygon(lp, poly):
                    matched_spaces.append((si, pi))
                    break  # 单层内找到一个即 break
            if matched_spaces:
                break  # 首层命中即停止遍历楼层

        if not matched_spaces:
            warnings.append(
                f"灯具 {lum.symbol} ({lum.x:.3f},{lum.y:.3f}) 不在任何房间内"
            )
            continue

        if len(matched_spaces) > 1:
            warnings.append(
                f"灯具 {lum.symbol} 命中多个 space，取第一个；重叠空间可能需要 review"
            )

        si, pi = matched_spaces[0]
        entry = {
            "symbol": lum.symbol,
            "x": lum.x, "y": lum.y, "z": lum.z,
            "catalog_match": True,
            # kind 由解析器按 DXF 图元显式声明，下游禁止靠 symbol 前缀反推（spec/ir.schema.json）
            "kind": getattr(lum, "kind", "unknown") or "unknown",
        }
        dims = _dims_from_attrs(getattr(lum, "attrs", None))
        if dims:
            entry["dims"] = dims
        ir["storeys"][si]["spaces"][pi]["luminaires"].append(entry)

    # 回填 _meta.luminaires 聚合计数（parser 阶段写的是房间图 DXF 自身的灯具数，
    # 实际灯具由本函数挂到 space 上，需重新统计）
    total_luminaires = sum(
        len(space.get("luminaires", []))
        for storey in ir.get("storeys", [])
        for space in storey.get("spaces", [])
    )
    ir.setdefault("_meta", {})["luminaires"] = total_luminaires

    return warnings
