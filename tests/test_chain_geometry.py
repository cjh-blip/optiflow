"""_chain 几何后处理单测（房间环平滑：共线合并 + 小凹槽剔除）。

配套真实图断言见 tests/test_real_dxf.py（柜子变墙修复 t_0deee369）。
"""
from __future__ import annotations

import math

import ezdxf
import pytest

from optiflow.adapters.dialux.dxf import ParseConfig, Room, WALL_RING_LAYER, parse_dxf
from optiflow.adapters.dialux._chain import (
    COLLINEAR_TOL_M,
    MAX_NOTCH_AREA_M2,
    count_short_edges,
    dedup_rooms,
    extract_rings_from_line_arc,
    polygon_area,
    remove_small_notches,
    ring_preference_key,
    simplify_collinear,
    smooth_room_ring,
)


def _closed(pts):
    return [list(p) for p in pts] + [list(pts[0])]


# ---------- count_short_edges / ring_preference_key ----------

def test_count_short_edges_counts_only_short():
    square = [(0, 0), (5, 0), (5, 4), (0, 4)]
    assert count_short_edges(square) == 0
    # 北墙上挖一个 0.1m 深、0.15m 宽的凹槽 → 3 条短边（0.15/0.1/0.1... 实际 2 竖 1 横）
    notched = [(0, 0), (5, 0), (5, 4), (3, 4), (3, 3.9), (2.85, 3.9), (2.85, 4), (0, 4)]
    assert count_short_edges(notched) == 3


def test_count_short_edges_ignores_closing_and_zero_length():
    square = _closed([(0, 0), (5, 0), (5, 4), (0, 4)])
    assert count_short_edges(square) == 0
    with_dup = [(0, 0), (5, 0), (5, 0), (5, 4), (0, 4)]  # 零长边不计
    assert count_short_edges(with_dup) == 0


def test_count_short_edges_degenerate_ring():
    assert count_short_edges([(0, 0), (1, 0)]) == 0
    assert count_short_edges([]) == 0


def test_ring_preference_key_prefers_fewer_sawteeth():
    clean = [(0, 0), (5, 0), (5, 4), (0, 4)]
    dirty = [(0, 0), (5, 0), (5, 4), (3, 4), (3, 3.9), (2.85, 3.9), (2.85, 4), (0, 4)]
    assert ring_preference_key(clean) < ring_preference_key(dirty)


def test_ring_preference_key_tie_prefers_more_vertices():
    a = [(0, 0), (5, 0), (5, 4), (0, 4)]
    b = [(0, 0), (5, 0), (5, 2), (5, 4), (0, 4)]  # 同样 0 锯齿，顶点更多
    assert ring_preference_key(b) < ring_preference_key(a)


# ---------- simplify_collinear ----------

def test_simplify_collinear_removes_wall_split_points():
    """一面墙被切成 3 段 → 2 个共线断点应被合并掉。"""
    poly = [(0, 0), (2, 0), (4, 0), (5, 0), (5, 4), (0, 4)]
    out = simplify_collinear(poly)
    assert [tuple(p) for p in out] == [(0, 0), (5, 0), (5, 4), (0, 4)]
    assert polygon_area(out) == pytest.approx(20.0)


def test_simplify_collinear_keeps_closing_point():
    poly = _closed([(0, 0), (2, 0), (5, 0), (5, 4), (0, 4)])
    out = simplify_collinear(poly)
    assert out[0] == out[-1], "闭合环应保持闭合"
    assert len(out) == 5  # 4 顶点 + 闭合点


def test_simplify_collinear_keeps_spike():
    """伸出线段之外的尖刺不能删（删了会改面积）。"""
    poly = [(0, 0), (5, 0), (5, 4), (2, 4), (6, 4), (0, 4)]
    out = simplify_collinear(poly)
    assert [tuple(p) for p in out] == [tuple(p) for p in poly]


def test_simplify_collinear_removes_zero_length_edge():
    poly = [(0, 0), (5, 0), (5, 0), (5, 4), (0, 4)]
    out = simplify_collinear(poly)
    assert len(out) == 4


def test_simplify_collinear_does_not_mutate_input():
    poly = [[0, 0], [2, 0], [5, 0], [5, 4], [0, 4]]
    snapshot = [list(p) for p in poly]
    simplify_collinear(poly)
    assert poly == snapshot


def test_simplify_collinear_keeps_triangle():
    tri = [(0, 0), (5, 0), (0, 4)]
    assert len(simplify_collinear(tri)) == 3


# ---------- remove_small_notches ----------

def _wall_notch(depth: float, width: float):
    """5×4 房间，北墙 y=4 上一个 depth×width 的凹槽（模拟贴墙柜）。"""
    return [(0, 0), (5, 0), (5, 4), (3, 4), (3, 4 - depth),
            (3 - width, 4 - depth), (3 - width, 4), (0, 4)]


def test_remove_small_notches_fills_furniture_notch():
    poly = _wall_notch(0.15, 1.0)  # 0.15 m² < 0.5 阈值
    out, removed = remove_small_notches(poly)
    assert len(removed) == 1
    assert removed[0]["area"] == pytest.approx(0.15, abs=1e-3)
    assert polygon_area(out) == pytest.approx(20.0)
    assert count_short_edges(out) == 0


def test_remove_small_notches_keeps_large_alcove():
    """1.5 m² 的真实壁龛（> 0.5 阈值）必须保留。"""
    poly = _wall_notch(0.75, 2.0)
    out, removed = remove_small_notches(poly)
    assert removed == []
    assert polygon_area(out) == pytest.approx(20.0 - 1.5)


def test_remove_small_notches_respects_threshold_argument():
    poly = _wall_notch(0.75, 2.0)
    out, removed = remove_small_notches(poly, max_area_m2=2.0)
    assert len(removed) == 1
    assert polygon_area(out) == pytest.approx(20.0)


def test_remove_small_notches_keeps_outward_bump():
    """向房间外凸出的小台（填掉会让面积变小）不能删 —— 那条「墙」才可能是家具边。"""
    poly = [(0, 0), (5, 0), (5, 4), (3, 4), (3, 4.15), (2, 4.15), (2, 4), (0, 4)]
    area_before = polygon_area(poly)
    out, removed = remove_small_notches(poly)
    assert removed == []
    assert polygon_area(out) == pytest.approx(area_before)


def test_remove_small_notches_handles_cw_ring():
    """CW 绕向（真实 IR 就是 CW）同样只填「往里凹」的槽。"""
    poly = list(reversed(_wall_notch(0.15, 1.0)))
    out, removed = remove_small_notches(poly)
    assert len(removed) == 1
    assert polygon_area(out) == pytest.approx(20.0)


def test_remove_small_notches_keeps_corner_step():
    """房间自身的转角台阶（两端落在不同墙线上）不能被吃掉。"""
    poly = [(0, 0), (5, 0), (5, 3.8), (4.8, 3.8), (4.8, 4), (0, 4)]
    out, removed = remove_small_notches(poly)
    assert removed == []
    assert len(out) == len(poly)


def test_remove_small_notches_multiple_notches():
    """一面墙上多个柜子 → 全部填平。"""
    poly = [(0, 0), (5, 0), (5, 4),
            (4, 4), (4, 3.9), (3.5, 3.9), (3.5, 4),
            (2, 4), (2, 3.9), (1.5, 3.9), (1.5, 4),
            (0, 4)]
    out, removed = remove_small_notches(poly)
    assert len(removed) == 2
    assert polygon_area(out) == pytest.approx(20.0)


def test_remove_small_notches_noop_on_clean_square():
    poly = [(0, 0), (5, 0), (5, 4), (0, 4)]
    out, removed = remove_small_notches(poly)
    assert removed == []
    assert [tuple(p) for p in out] == [tuple(p) for p in poly]


def test_remove_small_notches_does_not_mutate_input():
    poly = [list(p) for p in _wall_notch(0.15, 1.0)]
    snapshot = [list(p) for p in poly]
    remove_small_notches(poly)
    assert poly == snapshot


def test_remove_small_notches_short_ring_is_noop():
    tri = [(0, 0), (5, 0), (0, 4)]
    out, removed = remove_small_notches(tri)
    assert removed == []
    assert len(out) == 3


def test_remove_small_notches_zero_threshold_disables():
    poly = _wall_notch(0.15, 1.0)
    out, removed = remove_small_notches(poly, max_area_m2=0.0)
    assert removed == []
    assert len(out) == len(poly)


def test_remove_small_notches_keeps_min_three_vertices():
    """不能把环削到 3 顶点以下。"""
    poly = [(0, 0), (5, 0), (5, 0.05), (0, 0.05)]
    out, _ = remove_small_notches(poly)
    assert len(out) >= 3


# ---------- smooth_room_ring ----------

def test_smooth_room_ring_combines_both_steps():
    """凹槽剔除 + 共线合并：柜子槽消失，且不留下多余共线断点。"""
    poly = _closed([(0, 0), (2.5, 0), (5, 0), (5, 4), (3, 4), (3, 3.9),
                    (2, 3.9), (2, 4), (0, 4)])
    out, removed = smooth_room_ring(poly)
    assert len(removed) == 1
    ring = [tuple(p) for p in out[:-1]]
    assert ring == [(0, 0), (5, 0), (5, 4), (0, 4)]
    assert out[0] == out[-1]
    assert polygon_area(out) == pytest.approx(20.0)
    assert count_short_edges(out) == 0


def test_smooth_room_ring_preserves_real_shape():
    """L 形房间（真实形状）不能被平滑改形。"""
    L = _closed([(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)])
    out, removed = smooth_room_ring(L)
    assert removed == []
    assert polygon_area(out) == pytest.approx(polygon_area(L))
    assert len(out) == len(L)


def test_smooth_room_ring_threshold_pass_through():
    poly = _wall_notch(0.75, 2.0)
    out, removed = smooth_room_ring(poly, max_notch_area_m2=2.0)
    assert len(removed) == 1
    assert polygon_area(out) == pytest.approx(20.0)


def test_constants_match_validator_min_sane_area():
    """凹槽阈值必须与 validator 规则 2 的面积下限 0.5 m² 一致（任务书要求）。"""
    assert MAX_NOTCH_AREA_M2 == 0.5
    assert COLLINEAR_TOL_M <= 1e-3


def test_smooth_room_ring_idempotent():
    poly = _wall_notch(0.15, 1.0)
    once, _ = smooth_room_ring(poly)
    twice, removed2 = smooth_room_ring(once)
    assert removed2 == []
    assert [tuple(p) for p in once] == [tuple(p) for p in twice]


# ---------- dedup_rooms 择优 ----------

def _room(rid, poly, layer="LINE+ARC"):
    return Room(id=rid, name=rid, polygon=[list(p) for p in poly], layer=layer)


# dedup_rooms 判「重复」用的是顶点算术平均重心（polygon_centroid），对顶点分布很敏感，
# 默认 rel_tol_centroid=1e-3 只认几乎逐点一致的环。这里显式放宽，专测「判定重复之后
# 留哪个」的择优逻辑（真实图上的房间择优发生在 parse_dxf 的 bbox 去重里，见下面的合成图测试）。
DEDUP_LOOSE = dict(rel_tol_centroid=1.0, rel_tol_area=5e-2)


def test_dedup_rooms_prefers_fewer_sawteeth():
    """面积相近的两个候选：锯齿少的胜出（旧逻辑会挑顶点多的那个脏环）。"""
    dirty = _room("dirty", _wall_notch(0.15, 1.0))          # 8 顶点，3 锯齿
    clean = _room("clean", [(0, 0), (5, 0), (5, 4), (0, 4)])  # 4 顶点，0 锯齿
    assert [r.id for r in dedup_rooms([dirty, clean], **DEDUP_LOOSE)] == ["clean"]
    # 输入顺序不影响结果
    assert [r.id for r in dedup_rooms([clean, dirty], **DEDUP_LOOSE)] == ["clean"]


def test_dedup_rooms_prefer_layers_wins_over_vertex_count():
    dirty = _room("dirty", _wall_notch(0.15, 1.0))
    wall = _room("wall", [(0, 0), (5, 0), (5, 4), (0, 4)], layer=WALL_RING_LAYER)
    kept = dedup_rooms([dirty, wall], prefer_layers={WALL_RING_LAYER}, **DEDUP_LOOSE)
    assert [r.id for r in kept] == ["wall"]
    kept2 = dedup_rooms([wall, dirty], prefer_layers={WALL_RING_LAYER}, **DEDUP_LOOSE)
    assert [r.id for r in kept2] == ["wall"]


def test_dedup_rooms_tie_keeps_more_vertices():
    """锯齿数相同 → 保留顶点多者（原行为不变）。"""
    few = _room("few", [(0, 0), (5, 0), (5, 4), (0, 4)])
    many = _room("many", [(0, 0), (2.5, 0), (5, 0), (5, 4), (2.5, 4), (0, 4)])
    assert [r.id for r in dedup_rooms([few, many], **DEDUP_LOOSE)] == ["many"]


def test_dedup_rooms_keeps_distinct_rooms():
    a = _room("a", [(0, 0), (5, 0), (5, 4), (0, 4)])
    b = _room("b", [(10, 0), (15, 0), (15, 4), (10, 4)])
    assert len(dedup_rooms([a, b])) == 2


# ---------- 环搜索：关掉 T 形分割 = 墙线环 ----------

def _cabinet_doc(depth_mm: float = 150.0, partition_x: float | None = None):
    """4 面整墙 + 贴北墙浅柜（柜子顶边借用墙线，与真实图同画法）。

    partition_x 给出时再加一条 T 形内隔墙（端点落在南/北墙中间）。
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    W, H = 10000.0, 8000.0
    for a, b in (((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "0"})
    y = H - depth_mm
    for a, b in (((3000, H), (3000, y)), ((3000, y), (4000, y)), ((4000, y), (4000, H))):
        msp.add_line(a, b, dxfattribs={"layer": "0"})
    if partition_x is not None:
        msp.add_line((partition_x, 0), (partition_x, H), dxfattribs={"layer": "0"})
    return doc


def _rings_m(doc, split: bool):
    rings = extract_rings_from_line_arc(
        doc, edge_layers={"0"}, join_tolerance_mm=2.0, auto_close_tolerance_mm=4.0,
        split_at_t_junctions=split,
    )
    return [([(x / 1000.0, y / 1000.0) for x, y in poly], note) for poly, note in rings]


def test_split_at_t_junctions_true_produces_sawtooth_ring():
    """默认（切 T 形接头）会拼出「柜子变墙」的锯齿环 —— 这正是被修的 bug。"""
    rings = _rings_m(_cabinet_doc(), split=True)
    big = [(poly, polygon_area(poly)) for poly, _ in rings if polygon_area(poly) > 20]
    assert big, "没拼出房间量级的环"
    assert any(count_short_edges(poly) > 0 for poly, _ in big), \
        "预期默认 pass 会产出带锯齿的环（否则这个 fixture 不再复现 bug）"


def test_split_at_t_junctions_false_produces_clean_wall_ring():
    """关掉 T 形分割 → 墙线不被柜子端点切断 → 拼出干净的墙围轮廓。"""
    rings = _rings_m(_cabinet_doc(), split=False)
    big = [poly for poly, _ in rings if polygon_area(poly) > 20]
    assert big
    best = min(big, key=lambda p: count_short_edges(p))
    assert count_short_edges(best) == 0
    assert polygon_area(best) == pytest.approx(80.0, abs=1e-6)
    assert len(_ring_no_close(best)) == 4


def _ring_no_close(poly):
    r = [tuple(p) for p in poly]
    if len(r) > 2 and math.hypot(r[0][0] - r[-1][0], r[0][1] - r[-1][1]) < 1e-9:
        r.pop()
    return r


def test_parse_dxf_synthetic_room_is_clean_and_keeps_cabinet(tmp_path):
    """合成图端到端：房间是干净矩形 80 m²，柜子仍在 furniture 里。"""
    doc = _cabinet_doc()
    path = tmp_path / "cabinet.dxf"
    doc.saveas(str(path))
    cfg = ParseConfig(units="mm", units_from_header=False,
                      room_layers=["0"], room_edge_layers=["0"])
    ir = parse_dxf(str(path), cfg)
    assert ir["_meta"]["rooms"] == 1
    assert ir["_meta"]["furniture"] == 1
    room = next(s for s in ir["storeys"][0]["spaces"]
                if not s["name"].startswith("家具_"))
    assert polygon_area(room["polygon"]) == pytest.approx(80.0, abs=1e-6)
    assert count_short_edges(room["polygon"]) == 0
    assert len(_ring_no_close(room["polygon"])) == 4
    assert room["furniture"][0]["area_m2"] == pytest.approx(0.15, abs=0.01)


def test_parse_dxf_synthetic_multi_room_not_merged(tmp_path):
    """T 形内隔墙分出的子房间不能因为墙线环 pass 而丢失，且各自都不带锯齿。"""
    doc = _cabinet_doc(partition_x=6000.0)
    path = tmp_path / "two_rooms.dxf"
    doc.saveas(str(path))
    cfg = ParseConfig(units="mm", units_from_header=False,
                      room_layers=["0"], room_edge_layers=["0"])
    ir = parse_dxf(str(path), cfg)
    rooms = [s for s in ir["storeys"][0]["spaces"]
             if not s["name"].startswith("家具_")]
    areas = sorted(round(polygon_area(s["polygon"]), 2) for s in rooms)
    assert 80.0 in areas, f"整体房间丢了：{areas}"
    assert any(abs(a - 48.0) < 0.5 for a in areas), f"左子房间（6×8=48）丢了：{areas}"
    assert any(abs(a - 32.0) < 0.5 for a in areas), f"右子房间（4×8=32）丢了：{areas}"
    for s in rooms:
        assert count_short_edges(s["polygon"]) == 0, \
            f"{s['id']} 仍有锯齿：{[[round(c, 3) for c in p] for p in s['polygon']]}"
    assert ir["_meta"]["furniture"] == 1


def test_notch_flush_against_corner_is_known_limitation(tmp_path):
    """已知边界：柜子一侧正好贴到转角/隔墙时，凹槽退化成「转角台阶」。

    此时柜子边与房间转角在几何上不可区分（离开墙面后再也没回到同一条线），
    remove_small_notches 按设计不动它 —— 宁可留一小段台阶，也不能误删真实转角。
    整体房间仍由墙线环 pass 给出干净轮廓；受影响的只有被隔墙切出的子房间。
    真实图（tests/fixtures/sample_room.dxf）不含这种画法，见 test_real_dxf.py。
    """
    doc = _cabinet_doc(partition_x=4000.0)  # 柜子右边界 == 隔墙
    path = tmp_path / "corner_flush.dxf"
    doc.saveas(str(path))
    cfg = ParseConfig(units="mm", units_from_header=False,
                      room_layers=["0"], room_edge_layers=["0"])
    ir = parse_dxf(str(path), cfg)
    rooms = [s for s in ir["storeys"][0]["spaces"]
             if not s["name"].startswith("家具_")]
    areas = sorted(round(polygon_area(s["polygon"]), 2) for s in rooms)
    # 整体房间（墙线环）依然干净
    whole = next(s for s in rooms if abs(polygon_area(s["polygon"]) - 80.0) < 0.01)
    assert count_short_edges(whole["polygon"]) == 0
    # 左子房间保留 0.15 m² 台阶（32 - 0.15）
    assert any(abs(a - 31.85) < 0.01 for a in areas), f"预期左子房间 31.85，实得 {areas}"
    assert ir["_meta"]["furniture"] == 1

