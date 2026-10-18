"""房间环几何后处理的**薄壳**（第三刀 · 刀A，2026-10-19）：只重导出公开面。

实现受控收编在 `src/parser/_chain.py`（真相源 = `dialux-compiler/src/parser/_chain.py`，
逐字节相同），本文件与实现的对应关系由 `scripts/cross_parser_parity.py` 在推送门把关。
旧几何逻辑已从本路径归零——业务代码仍 `from optiflow.adapters.dialux._chain import ...`（走本壳）。
"""
from parser._chain import (  # noqa: F401  薄壳的全部工作就是重导出
    COLLINEAR_TOL_M,
    MAX_NOTCH_AREA_M2,
    MAX_NOTCH_VERTICES,
    Point2,
    SAWTOOTH_EDGE_M,
    arc_to_points,
    count_short_edges,
    dedup_rooms,
    extract_rings_from_line_arc,
    polygon_area,
    polygon_centroid,
    remove_small_notches,
    ring_preference_key,
    simplify_collinear,
    smooth_room_ring,
)

__all__ = [
    "COLLINEAR_TOL_M",
    "MAX_NOTCH_AREA_M2",
    "MAX_NOTCH_VERTICES",
    "Point2",
    "SAWTOOTH_EDGE_M",
    "arc_to_points",
    "count_short_edges",
    "dedup_rooms",
    "extract_rings_from_line_arc",
    "polygon_area",
    "polygon_centroid",
    "remove_small_notches",
    "ring_preference_key",
    "simplify_collinear",
    "smooth_room_ring",
]
