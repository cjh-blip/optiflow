"""剔除后补足：外接网格筛掉的灯要被补回来。

背景（2026-11-03 归因）：`plan_grid` 在外接矩形上排布，`_evaluate_count` 把落在
房间轮廓外的点剔掉，照度按剔完的实布盏数算——不补回来，实际照度永远低于目标。
实测 97.66 m² 那间：名义 20/26/32 盏，实布只有 16/22/26 盏，照度恒为目标的 0.81~0.83。

样本选型说明：用「10×10 缺一个 3×3 角」而不是 L 形。缺角足够让外接网格剔掉
一两盏（触发补灯），又不会剔到需要补满上限——补灯每轮都要重算整个采样网格，
样本太狠会让这组测试从二十秒拖到一百秒，那是拿测试速度换心理安慰。
"""

from __future__ import annotations

import pytest

from optiflow.algo.engine import (
    MAX_SUPPLEMENT_ROUNDS,
    SUPPLEMENT_DROP_THRESHOLD,
    plan_layout,
)
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec

#: 小 L 形（面积 27，外接 6×6）：外接网格的空角一定会剔掉几盏，
#: 而且房间小、采样点少，一次 plan_layout 只要几十毫秒。
#: 不用 10×10 那种大房间：采样网格是 0.25 m 间距，大房间一次重排就慢一个量级。
NOTCHED = [(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]
#: 规整矩形：一盏都不会被剔，用来钉住「不需要补时不补」。
RECT = [(0, 0), (6, 0), (6, 6), (0, 6)]
#: 真实会议室尺寸（11.9×8.78），给 U0 相关断言用。
BIG = [(0, 0), (11.9, 0), (11.9, 8.78), (0, 8.78)]
#: 中等房间（10×6 = 60 m²）。目前没用到：实测这个尺寸下算法总能补到达标，
#: 造不出「不达标」样本，留着备查。
MID = [(0, 0), (10, 0), (10, 6), (0, 6)]


def _task(outline, target_lux: float = 400.0, *, height: float = 4.0,
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
    plan = plan_layout(_task(RECT, 300.0))
    room = plan.rooms[0]
    assert room.supplement is None
    assert room.layout.count == room.lumen_method_count


def test_notched_room_triggers_supplement():
    """缺角房间：被筛掉的灯要补回来，实布不少于名义。"""
    plan = plan_layout(_task(NOTCHED, 400.0))
    room = plan.rooms[0]
    assert room.supplement is not None, "缺角房间一定有灯落在外接矩形的空角外"
    sup = room.supplement
    assert sup["dropped_ratio"] > SUPPLEMENT_DROP_THRESHOLD
    assert sup["actual_before"] < sup["nominal_count"], "前提：一开始确实被筛掉了"
    assert room.layout.count >= sup["nominal_count"], "补完实布应不少于名义盏数"


def test_supplement_rounds_are_bounded():
    """补灯轮数有上限，不许死循环。"""
    plan = plan_layout(_task(NOTCHED, 300.0))
    sup = plan.rooms[0].supplement
    if sup is not None:
        assert 0 < sup["rounds"] <= MAX_SUPPLEMENT_ROUNDS


def test_supplement_brings_illuminance_up_to_target():
    """补灯之后照度要回到目标附近（≥95%）。"""
    target = 400.0
    plan = plan_layout(_task(NOTCHED, target))
    room = plan.rooms[0]
    ratio = room.predicted_avg_lumen / target
    assert ratio >= 0.95, (
        f"目标 {target} 只做到 {room.predicted_avg_lumen:.1f}（{ratio:.0%}）；"
        f"补灯记录 {room.supplement}")


def test_supplement_does_not_change_calibration():
    """补灯只加盏数，不动系数：反推 CU×MF 必须等于 UF×MF。"""
    plan = plan_layout(_task(NOTCHED, 400.0))
    room = plan.rooms[0]
    implied = room.predicted_avg_lumen * room.area / (
        len(room.fixtures) * room.flux_per_fixture)
    assert implied == pytest.approx(room.utilization_factor * room.maintenance_factor,
                                    rel=1e-6)


def test_assumptions_from_task_are_passed_through():
    """调用方给的假设（如层高按 X m）必须出现在结果的 assumptions 里。"""
    note = "空间 r1 未指定层高，按 4.0 m 计算"
    plan = plan_layout(_task(RECT, 300.0, assumptions=[note]))
    assert note in plan.assumptions


def test_shortfall_reports_metric_actual_target_cause_action():
    """不达标要有结构化输出：哪项、实测、目标、差多少、原因、建议动作。

    用「目标高到补灯也补不足」来造不达标，而不是靠某个房间的 U0 刚好低：
    U0 会随 edge_factor 收到 0.25 而变好，赌它不绿很脆。目标也不取太大：
    名义盏数直接决定采样计算量，3000 lx（名义 200+ 盏）会让这条测试吃掉十几秒。
    """
    plan = plan_layout(_task(BIG, 1500.0))
    room = plan.rooms[0]
    assert room.shortfalls, "104 m² 要 1500 lx 必然补不足"
    for item in room.shortfalls:
        for key in ("metric", "label", "actual", "target", "unit",
                    "gap", "gap_pct", "cause", "action"):
            assert key in item, f"缺字段 {key}：{item}"
        assert item["cause"] and item["action"], "原因与建议动作不能空着"
        assert item["gap"] < 0, "不达标项的实际值应该小于目标值"


def test_uniformity_shortfall_says_it_is_not_a_lamp_count_problem():
    """U0 不达标时，原因要指向配光而不是「灯不够多」。"""
    plan = plan_layout(_task(BIG, 500.0))
    room = plan.rooms[0]
    u0_items = [s for s in room.shortfalls if s["metric"] == "uniformity_u0"]
    if not u0_items:
        pytest.skip("这份样本的 U0 达标了")
    item = u0_items[0]
    assert "配光" in item["cause"] or "不够宽" in item["cause"]
    assert "换半峰角更大的灯具" in item["action"] or "更宽配光" in item["action"]
