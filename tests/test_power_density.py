"""照明功率密度（LPD）与国标对标。

要钉住四件事：
1. 功率从哪来必须说清楚（IR → 调用方 → 按光效假定），不许默默拿一个 30 W；
2. LPD 超标进 shortfalls，且与照度那条同构（多一个 direction=over，因为方向相反）；
3. 标准号 + 表号必须出现在产出里；
4. 照度达标而 LPD 超标时，把「两者打架」这句话直接写出来，不取其一。

限值出处：GB/T 50034-2024 第 6.3.5 条 + 表 6.3.5
（普通办公室、会议室 ≤ 6.5 W/m²，目标值）。
同条另写「现行值应符合 GB 55015 的规定」，但工作区资料未收录那行具体数值，
所以只对标目标值——这条在 LPD_CURRENT_VALUE_NOTE 里写明了。
"""

from __future__ import annotations

import pytest

from optiflow.algo.engine import (
    DEFAULT_LUMINOUS_EFFICACY,
    LPD_LIMIT_W_PER_M2,
    LPD_STANDARD_REF,
    plan_layout,
)
from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec

ROOM = [(0, 0), (8, 0), (8, 6), (0, 6)]      # 48 m²
FLUX = 3000.0


def _task(target_lux: float = 300.0, *, extra=None, fixtures=None) -> TaskSpec:
    base_extra = {"flux": FLUX, "fixture_name": "测试灯"}
    base_extra.update(extra or {})
    return TaskSpec(
        kind="layout",
        spaces=[Space(
            id="r1", name="会议室",
            geometry=Geometry(kind="room", height=4.0,
                              outline=[Point(x=float(x), y=float(y)) for x, y in ROOM]),
            work_plane=0.75,
            reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
        )],
        fixtures=fixtures or [],
        constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
        extra=base_extra,
    )


def test_power_from_ir_fixture_entry():
    """IR 里灯具带 power 就用它，并说明来源。"""
    fx = [Fixture(id="L0", name="灯", properties={"power": 25.0})]
    plan = plan_layout(_task(extra={"fixture_power_w": 99.0}, fixtures=fx))
    room = plan.rooms[0]
    assert "灯具条目" in room.power_source
    assert room.power_total_w == pytest.approx(25.0 * len(room.fixtures), rel=1e-6)


def test_power_from_caller_when_ir_has_none():
    plan = plan_layout(_task(extra={"fixture_power_w": 40.0}))
    room = plan.rooms[0]
    assert "调用方" in room.power_source
    assert room.power_total_w == pytest.approx(40.0 * len(room.fixtures), rel=1e-6)


def test_power_derived_from_efficacy_is_declared():
    """IR 没功率时按光效反推，且必须写明「按光效 X lm/W 反推」。"""
    plan = plan_layout(_task())
    room = plan.rooms[0]
    assert "光效" in room.power_source and "反推" in room.power_source
    expect = FLUX / DEFAULT_LUMINOUS_EFFICACY * len(room.fixtures)
    assert room.power_total_w == pytest.approx(expect, rel=1e-6)


def test_custom_efficacy_is_honoured():
    plan = plan_layout(_task(extra={"luminous_efficacy": 150.0}))
    room = plan.rooms[0]
    expect = FLUX / 150.0 * len(room.fixtures)
    assert room.power_total_w == pytest.approx(expect, rel=1e-6)


def test_power_density_formula():
    plan = plan_layout(_task())
    room = plan.rooms[0]
    assert room.power_density == pytest.approx(
        room.power_total_w / room.area, rel=1e-3)


def test_lpd_over_limit_goes_into_shortfalls_with_standard():
    """功率拉大 → LPD 超标 → 进 shortfalls，带标准号与 suggestion。"""
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 200.0}))
    room = plan.rooms[0]
    assert room.lpd_ok is False
    items = [s for s in room.shortfalls if s["metric"] == "power_density"]
    assert items, f"LPD 超标必须进 shortfalls，实际 {[s['metric'] for s in room.shortfalls]}"
    item = items[0]
    for key in ("metric", "label", "actual", "target", "unit",
                "gap", "gap_pct", "cause", "action", "standard", "direction"):
        assert key in item, f"缺字段 {key}"
    assert item["direction"] == "over"
    assert item["gap"] > 0, "超标时实际值应大于目标值"
    assert LPD_STANDARD_REF in item["cause"] or LPD_STANDARD_REF in item["standard"]
    assert "6.3.5" in LPD_STANDARD_REF, "引用必须带表号，不许含糊成「国标要求」"


def test_lpd_within_limit_adds_no_shortfall():
    """光效够高的灯（同光通量更小功率）就不该有 LPD 条目。"""
    plan = plan_layout(_task(300.0, extra={"luminous_efficacy": 220.0}))
    room = plan.rooms[0]
    assert room.lpd_ok is True
    assert not [s for s in room.shortfalls if s["metric"] == "power_density"]


def test_conflict_is_stated_when_illuminance_ok_but_lpd_over():
    """照度达标而 LPD 超标时，结论里要写明「两者无法同时满足」。"""
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 200.0}))
    room = plan.rooms[0]
    assert room.predicted_avg_lumen >= room.target_lux - 1e-6, "前提：照度得先达标"
    assert room.lpd_ok is False
    joined = " ".join(room.warnings)
    assert "无法同时满足" in joined
    assert "光效" in joined, "冲突结论要给出光效门槛这条出路"


def test_needed_efficacy_is_actionable():
    """建议动作里给出的「需光效 ≥ X lm/W」要算得对。"""
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 200.0}))
    room = plan.rooms[0]
    item = next(s for s in room.shortfalls if s["metric"] == "power_density")
    need = len(room.fixtures) * room.flux_per_fixture / (LPD_LIMIT_W_PER_M2 * room.area)
    assert f"{need:.0f}" in item["action"]
