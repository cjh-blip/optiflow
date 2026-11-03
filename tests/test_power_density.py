"""房间类型 → 判据表 → 对标结论。

三条纪律：
1. 房间类型留空**不许默认套会议室**，只给实测值 + 「类型未指定，未做标准对标」。
2. 每个结论都要能追到标准号 + 表号（判据表每格带 sources）。
3. 400 lx 不是标准照度档（GB/T 50034-2024 第 4.1.1 条），要提示最近的是 300 与 500。

判据表本体在 src/optiflow/algo/standards.py，这里测的是「它被正确用上了」。
"""

from __future__ import annotations

import pytest

from optiflow.algo import standards
from optiflow.algo.engine import plan_layout
from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec

ROOM = [(0, 0), (8, 0), (8, 6), (0, 6)]      # 48 m²
FLUX = 3000.0


def _task(target_lux: float = 300.0, *, room_type: str | None = "meeting_room",
          extra=None, fixtures=None) -> TaskSpec:
    base_extra = {"flux": FLUX, "fixture_name": "测试灯"}
    base_extra.update(extra or {})
    return TaskSpec(
        kind="layout",
        spaces=[Space(
            id="r1", name="测试房间", room_type=room_type,
            geometry=Geometry(kind="room", height=4.0,
                              outline=[Point(x=float(x), y=float(y)) for x, y in ROOM]),
            work_plane=0.75,
            reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
        )],
        fixtures=fixtures or [],
        constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
        extra=base_extra,
    )


# ---------------------------------------------------------------- 判据表本体

def test_table_covers_five_categories_with_sources():
    for key in ("office", "office_high", "meeting_room", "corridor", "service_hall"):
        std = standards.ROOM_STANDARDS[key]
        for fld in ("illuminance", "ugr", "u0", "ra", "lpd_current", "lpd_target"):
            assert fld in std.sources, f"{key} 的 {fld} 缺出处"
            src = std.sources[fld]
            assert any(tag in src for tag in ("表", "条")), f"{key}.{fld} 出处没有表号/条号：{src}"


def test_unknown_type_returns_none_not_a_lookalike():
    assert standards.lookup(None) is None
    assert standards.lookup("") is None
    assert standards.lookup("不存在的地方") is None


def test_chinese_alias_resolves():
    assert standards.lookup("会议室").key == "meeting_room"
    assert standards.lookup("普通办公室").key == "office"
    assert standards.lookup("走廊").key == "corridor"


def test_illuminance_levels_exclude_400():
    assert standards.is_standard_lux(300) and standards.is_standard_lux(500)
    assert not standards.is_standard_lux(400)
    assert standards.nearest_levels(400) == (300, 500)


# ---------------------------------------------------------------- 类型留空

def test_no_room_type_gives_no_compliance_verdict():
    plan = plan_layout(_task(300.0, room_type=None))
    room = plan.rooms[0]
    assert room.standard_key is None
    assert room.lpd_current_limit is None and room.lpd_target_limit is None
    assert room.lpd_compliant is None and room.lpd_meets_target is None
    assert "未指定房间类型" in room.standard_basis
    assert not [s for s in room.shortfalls if s["metric"] == "power_density"], \
        "没类型就不该出 LPD 合规结论"
    assert room.power_density is not None, "实测值还是要给"


def test_unknown_room_type_is_reported_as_not_covered():
    plan = plan_layout(_task(300.0, room_type="机房"))
    room = plan.rooms[0]
    assert room.standard_key is None
    assert "未收录" in room.standard_basis


# ---------------------------------------------------------------- 类型区分

def test_meeting_room_uses_its_own_limits():
    of = plan_layout(_task(300.0, room_type="meeting_room")).rooms[0]
    assert of.standard_key == "meeting_room"
    assert of.lpd_current_limit == pytest.approx(8.0)
    assert of.lpd_target_limit == pytest.approx(6.5)
    assert "表 3.3.7-3" in of.standard_basis
    assert "表 6.3.5" in of.standard_basis


def test_corridor_limits_differ_from_meeting_room():
    """走廊的限值必须与会议室不同——套错类别结论会离谱。"""
    mr = plan_layout(_task(300.0, room_type="meeting_room")).rooms[0]
    cr = plan_layout(_task(300.0, room_type="corridor")).rooms[0]
    assert cr.standard_key == "corridor"
    assert cr.lpd_current_limit == pytest.approx(2.0)
    assert cr.lpd_target_limit == pytest.approx(1.5)
    assert "表 3.3.7-12" in cr.standard_basis
    assert cr.lpd_current_limit != mr.lpd_current_limit


def test_office_high_has_higher_limits():
    hi = plan_layout(_task(500.0, room_type="office_high")).rooms[0]
    assert hi.lpd_current_limit == pytest.approx(13.5)
    assert hi.lpd_target_limit == pytest.approx(9.5)


def test_same_room_three_types_can_yield_different_verdicts():
    """同一间房：留空 / 会议室 / 走廊，结论必须能看出差别。"""
    kw = {"extra": {"fixture_power_w": 30.0}}
    a = plan_layout(_task(300.0, room_type=None, **kw)).rooms[0]
    b = plan_layout(_task(300.0, room_type="meeting_room", **kw)).rooms[0]
    c = plan_layout(_task(300.0, room_type="corridor", **kw)).rooms[0]
    assert a.lpd_compliant is None
    assert b.lpd_compliant is True and c.lpd_compliant is False, \
        f"会议室 {b.power_density:.2f}/{b.lpd_current_limit} 应合规；" \
        f"走廊 {c.power_density:.2f}/{c.lpd_current_limit} 应不合规"


# ---------------------------------------------------------------- 档位提示

def test_non_standard_target_lux_is_flagged():
    plan = plan_layout(_task(400.0))
    assert any("不是标准照度档" in w and "300" in w and "500" in w for w in plan.warnings), \
        "400 lx 必须提示非标准档，并给出最近的两档"


def test_standard_target_lux_is_not_flagged():
    plan = plan_layout(_task(300.0))
    assert not any("不是标准照度档" in w for w in plan.warnings)


def test_above_standard_lux_explains_basis():
    room = plan_layout(_task(500.0, room_type="meeting_room")).rooms[0]
    assert "超标准设计" in room.lpd_basis
    assert "折算" in room.lpd_basis and "不折算" in room.lpd_basis


# ---------------------------------------------------------------- 功率与冲突

def test_power_from_ir_fixture_entry():
    fx = [Fixture(id="L0", name="灯", properties={"power": 25.0})]
    room = plan_layout(_task(extra={"fixture_power_w": 99.0}, fixtures=fx)).rooms[0]
    assert "灯具条目" in room.power_source
    assert room.power_total_w == pytest.approx(25.0 * len(room.fixtures), rel=1e-6)


def test_power_derived_from_efficacy_is_declared():
    room = plan_layout(_task()).rooms[0]
    assert "光效" in room.power_source and "反推" in room.power_source


def test_between_two_limits_is_compliant_but_not_on_target():
    room = plan_layout(_task(300.0, extra={"fixture_power_w": 60.0})).rooms[0]
    lpd = room.power_density
    if not (6.5 < lpd <= 8.0):
        pytest.skip(f"样本 LPD={lpd:.2f} 没落在两限值之间")
    assert room.lpd_compliant is True and room.lpd_meets_target is False
    assert not [s for s in room.shortfalls if s["metric"] == "power_density"]
    assert any("未达到目标值" in w for w in room.warnings)


def test_over_current_limit_is_non_compliant_shortfall():
    room = plan_layout(_task(300.0, extra={"fixture_power_w": 400.0})).rooms[0]
    assert room.lpd_compliant is False
    item = next(s for s in room.shortfalls if s["metric"] == "power_density")
    for key in ("metric", "label", "actual", "target", "unit", "gap", "gap_pct",
                "cause", "action", "standard", "direction", "severity"):
        assert key in item, f"缺字段 {key}"
    assert item["severity"] == "non_compliant" and item["gap"] > 0
    assert "3.3.7-3" in item["standard"]


def test_conflict_is_stated_when_illuminance_ok_but_lpd_over():
    room = plan_layout(_task(300.0, extra={"fixture_power_w": 400.0})).rooms[0]
    assert room.predicted_avg_lumen >= room.target_lux - 1e-6
    joined = " ".join(room.warnings)
    assert "无法同时满足" in joined and "光效" in joined


# ---------------------------------------------------------------- U0 与指标状态

def test_u0_limit_comes_from_standard_table():
    """走廊 U0>=0.40、会议室 0.60——同一套房子里不许两套口径。"""
    mr = plan_layout(_task(300.0, room_type="meeting_room")).rooms[0]
    cr = plan_layout(_task(300.0, room_type="corridor")).rooms[0]
    assert mr.u0_required == pytest.approx(0.60)
    assert cr.u0_required == pytest.approx(0.40)
    assert "5.3.2" in mr.u0_basis and "5.5.1" in cr.u0_basis


def test_u0_basis_names_default_when_type_missing():
    room = plan_layout(_task(300.0, room_type=None)).rooms[0]
    assert room.u0_required == pytest.approx(0.60)
    assert "非按类型" in room.u0_basis


def test_metric_status_marks_what_was_not_computed():
    """UGR / Ra 必须显式标 not_computed，不能和 computed 长一样。"""
    st = plan_layout(_task(300.0, room_type="meeting_room")).rooms[0].metric_status
    assert st["illuminance_avg"] == "computed"
    assert st["uniformity_u0"] == "computed"
    assert st["power_density"] == "computed"
    assert st["ugr"] == "not_computed"
    assert st["ra"] == "not_computed"


def test_metric_status_marks_not_specified_without_room_type():
    st = plan_layout(_task(300.0, room_type=None)).rooms[0].metric_status
    assert st["illuminance_avg"] == "not_specified"
    assert st["power_density"] == "not_specified"
    assert st["ugr"] == "not_computed", "没实现比没标准更根本，优先报 not_computed"


def _two_room_task():
    """一张图两个房间：会议室 300 lx + 走廊 50 lx。"""
    def sp(sid, outline, room_type, target):
        return Space(
            id=sid, name=sid, room_type=room_type, target_lux=target,
            geometry=Geometry(kind="room", height=4.0,
                              outline=[Point(x=float(x), y=float(y)) for x, y in outline]),
            work_plane=0.75,
            reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
        )
    return TaskSpec(
        kind="layout",
        spaces=[
            sp("mtg", [(0, 0), (8, 0), (8, 6), (0, 6)], "meeting_room", 300.0),
            sp("cor", [(10, 0), (14, 0), (14, 6), (10, 6)], "corridor", 50.0),
        ],
        fixtures=[],
        constraints=[],
        extra={"flux": FLUX, "fixture_name": "测试灯"},
    )


def test_two_rooms_each_use_their_own_target_and_standard():
    plan = plan_layout(_two_room_task())
    assert len(plan.rooms) == 2
    by_id = {r.space_id: r for r in plan.rooms}
    mtg, cor = by_id["mtg"], by_id["cor"]
    assert mtg.target_lux == pytest.approx(300.0) and cor.target_lux == pytest.approx(50.0)
    assert "空间自带" in mtg.target_basis and "空间自带" in cor.target_basis
    assert mtg.u0_required == pytest.approx(0.60) and cor.u0_required == pytest.approx(0.40)
    assert mtg.lpd_current_limit == pytest.approx(8.0)
    assert cor.lpd_current_limit == pytest.approx(2.0)
    assert mtg.standard_key == "meeting_room" and cor.standard_key == "corridor"


def test_space_target_falls_back_to_standard_value():
    """Space 不给 target_lux 时，取判据表该类型的标准值。"""
    sp = _two_room_task().spaces[1]
    sp.target_lux = None
    task = _two_room_task()
    task.spaces[1].target_lux = None
    cor = {r.space_id: r for r in plan_layout(task).rooms}["cor"]
    assert cor.target_lux == pytest.approx(50.0)
    assert "判据表" in cor.target_basis and "5.5.1" in cor.target_basis
