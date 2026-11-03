"""剔除后补足：外接网格筛掉的灯要被补回来。

背景（2026-11-03 归因）：`plan_grid` 在外接矩形上排布，`_evaluate_count` 把落在
房间轮廓外的点剔掉，照度按剔完的实布盏数算——不补回来，实际照度永远低于目标。
实测 97.66 m² 那间：名义 20/26/32 盏，实布只有 16/22/26 盏，照度恒为目标的 0.81~0.83。
"""

from __future__ import annotations

import pytest

from optiflow.algo.engine import MAX_SUPPLEMENT_ROUNDS, plan_layout
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec

#: L 形房间：外接矩形 10×10，真实面积 65，四个空角会筛掉不少格点。
L_SHAPE = [(0, 0), (10, 0), (10, 4), (5, 4), (5, 10), (0, 10)]
#: 规整矩形：一盏都不会被筛，用来钉住「不需要补时不补」。
RECT = [(0, 0), (10, 0), (10, 10), (0, 10)]


def _task(outline, target_lux: float = 500.0, *, height: float = 4.0,
          assumptions=None) -> TaskSpec:
    return TaskSpec(
        kind="layout",
        spaces=[Space(
            id="r1", name="测试房间",
            geometry=Geometry(kind="room", height=height,
                              outline=[Point(x=float(x), y=float(y)) for x, y in outline]),
            work_plane=0.75,
            reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
        )],
        constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
        extra={"flux": 3000.0, "fixture_name": "测试灯",
               "assumptions": assumptions or []},
    )


def test_rectangular_room_needs_no_supplement():
    """规整矩形：没有灯被筛，就不该有补灯记录。"""
    plan = plan_layout(_task(RECT, 400.0))
    room = plan.rooms[0]
    assert room.supplement is None
    assert room.layout.count == room.lumen_method_count


def test_concave_room_supplement_reaches_nominal():
    """凹形房间：被筛掉的灯要补回来，实布不少于名义。"""
    plan = plan_layout(_task(L_SHAPE, 400.0))
    room = plan.rooms[0]
    assert room.supplement is not None, "L 形房间一定有灯落在外接矩形的空角外"
    sup = room.supplement
    assert sup["actual_before"] < sup["nominal_count"], "前提：一开始确实被筛掉了"
    assert room.layout.count >= sup["nominal_count"], "补完实布应不少于名义盏数"
    assert sup["rounds"] <= MAX_SUPPLEMENT_ROUNDS


def test_supplement_rounds_are_bounded():
    """补灯轮数有上限，不许死循环。"""
    for target in (300.0, 500.0, 800.0, 1200.0):
        plan = plan_layout(_task(L_SHAPE, target))
        sup = plan.rooms[0].supplement
        if sup is not None:
            assert 0 < sup["rounds"] <= MAX_SUPPLEMENT_ROUNDS, f"target={target}"


def test_supplement_brings_illuminance_up_to_target():
    """补灯之后照度要回到目标附近（≥95%）。"""
    for target in (300.0, 400.0, 500.0):
        plan = plan_layout(_task(L_SHAPE, target))
        room = plan.rooms[0]
        ratio = room.predicted_avg_lumen / target
        assert ratio >= 0.95, (
            f"目标 {target} 只做到 {room.predicted_avg_lumen:.1f}（{ratio:.0%}）；"
            f"补灯记录 {room.supplement}")


def test_supplement_is_reported_honestly_when_insufficient():
    """补不足时要如实写进 warning，而不是硬塞。"""
    plan = plan_layout(_task(L_SHAPE, 3000.0))   # 目标高到外接矩形都排不下
    room = plan.rooms[0]
    if room.supplement is not None and room.layout.count < room.supplement["nominal_count"]:
        joined = " ".join(room.warnings)
        assert "补不足" in joined
        assert "换更窄配光的灯具" in joined or "改布灯方式" in joined


def test_assumptions_from_task_are_passed_through():
    """调用方给的假设（如层高按 X m）必须出现在结果的 assumptions 里。"""
    note = "空间 r1 未指定层高，按 4.0 m 计算"
    plan = plan_layout(_task(RECT, 400.0, assumptions=[note]))
    assert note in plan.assumptions


def test_supplement_does_not_change_calibration():
    """补灯只加盏数，不动系数：反推 CU×MF 必须等于 UF×MF。"""
    plan = plan_layout(_task(L_SHAPE, 500.0))
    room = plan.rooms[0]
    implied = room.predicted_avg_lumen * room.area / (
        len(room.fixtures) * room.flux_per_fixture)
    assert implied == pytest.approx(room.utilization_factor * room.maintenance_factor,
                                    rel=1e-6)
