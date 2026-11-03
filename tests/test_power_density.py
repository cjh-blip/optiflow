"""照明功率密度（LPD）：两层限值 + 档位口径。

**必须分成两层判，不能合成一条线：**

| 来源 | 限值 | 性质 | 用途 |
|---|---|---|---|
| GB 55015-2021 表 3.3.7-3（普通办公室/会议室 300 lx） | ≤ 8.0 W/m² | 强制性规范现行值 | **合规判定看这条** |
| GB/T 50034-2024 表 6.3.5（普通办公室、会议室） | ≤ 6.5 W/m² | 推荐性标准目标值 | 达标优化看这条 |

出处核对记录：02_礼堂推广/_工作文件/礼堂方案修正记录_20260811.md（表 3.3.7 系列原表复核）。

另一个要钉的点：会议室标准照度档是 300 lx（GB/T 50034-2024 表 5.3.2）。
目标定到 400/500 属于超标准设计，套 300 lx 那行限值本身就是口径选择，必须写出来。
"""

from __future__ import annotations

import pytest

from optiflow.algo.engine import (
    DEFAULT_LUMINOUS_EFFICACY,
    LPD_CURRENT_LIMIT_W_PER_M2,
    LPD_STANDARD_CURRENT,
    LPD_STANDARD_TARGET,
    LPD_TARGET_LIMIT_W_PER_M2,
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


# ---------------------------------------------------------------- 功率来源

def test_power_from_ir_fixture_entry():
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
    plan = plan_layout(_task())
    room = plan.rooms[0]
    assert "光效" in room.power_source and "反推" in room.power_source
    expect = FLUX / DEFAULT_LUMINOUS_EFFICACY * len(room.fixtures)
    assert room.power_total_w == pytest.approx(expect, rel=1e-6)


# ---------------------------------------------------------------- 两层限值

def test_two_limits_are_separate_fields():
    """现行值与目标值必须是两个独立字段，不能合成一个。"""
    plan = plan_layout(_task())
    room = plan.rooms[0]
    assert room.lpd_current_limit == pytest.approx(8.0)
    assert room.lpd_target_limit == pytest.approx(6.5)
    assert LPD_CURRENT_LIMIT_W_PER_M2 > LPD_TARGET_LIMIT_W_PER_M2


def test_between_two_limits_is_compliant_but_not_on_target():
    """过了现行值、没到目标值：合规，但不算达标——不进 shortfalls。"""
    # 48 m²，目标 300 lx；功率调到让 LPD 落在 6.5~8.0 之间
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 60.0}))
    room = plan.rooms[0]
    lpd = room.power_density
    if not (LPD_TARGET_LIMIT_W_PER_M2 < lpd <= LPD_CURRENT_LIMIT_W_PER_M2):
        pytest.skip(f"这份样本 LPD={lpd:.2f} 没落在两限值之间")
    assert room.lpd_compliant is True
    assert room.lpd_meets_target is False
    assert not [s for s in room.shortfalls if s["metric"] == "power_density"], \
        "合规但未达目标值，不该进 shortfalls"
    assert any("未达到目标值" in w for w in room.warnings)


def test_over_current_limit_is_non_compliant_shortfall():
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 400.0}))
    room = plan.rooms[0]
    assert room.lpd_compliant is False
    items = [s for s in room.shortfalls if s["metric"] == "power_density"]
    assert items
    item = items[0]
    for key in ("metric", "label", "actual", "target", "unit", "gap", "gap_pct",
                "cause", "action", "standard", "direction", "severity"):
        assert key in item, f"缺字段 {key}"
    assert item["direction"] == "over"
    assert item["severity"] == "non_compliant"
    assert item["gap"] > 0
    assert "3.3.7-3" in item["standard"], "现行值出处必须带表号"


def test_current_standard_ref_names_the_table():
    """出处要写成「GB 55015-2021 表 3.3.7-3」，不能只写「国标」。"""
    assert "GB 55015-2021" in LPD_STANDARD_CURRENT
    assert "3.3.7-3" in LPD_STANDARD_CURRENT
    assert "GB/T 50034-2024" in LPD_STANDARD_TARGET
    assert "6.3.5" in LPD_STANDARD_TARGET


# ---------------------------------------------------------------- 档位口径

def test_standard_lux_basis_is_declared_for_normal_design():
    plan = plan_layout(_task(300.0))
    room = plan.rooms[0]
    assert "300 lx" in room.lpd_basis
    assert "超标准设计" not in room.lpd_basis


def test_above_standard_lux_must_explain_the_basis():
    """目标高于标准档时，口径说明必须写出来（含折算参考值，并声明本结果不折算）。"""
    plan = plan_layout(_task(500.0))
    room = plan.rooms[0]
    assert "超标准设计" in room.lpd_basis
    assert "500 lx" in room.lpd_basis
    assert "折算" in room.lpd_basis
    assert "不折算" in room.lpd_basis


def test_scaled_reference_value_is_computed_right():
    """折算参考值 = 现行值 × 目标/标准档。"""
    plan = plan_layout(_task(600.0))
    room = plan.rooms[0]
    expect = LPD_CURRENT_LIMIT_W_PER_M2 * 600.0 / 300.0
    assert f"{expect:.2f}" in room.lpd_basis


def test_custom_standard_lux_is_honoured():
    plan = plan_layout(_task(500.0, extra={"standard_lux": 500.0}))
    room = plan.rooms[0]
    assert "超标准设计" not in room.lpd_basis, "标准档就是 500 时不该算超标准"


# ---------------------------------------------------------------- 冲突结论

def test_conflict_is_stated_when_illuminance_ok_but_lpd_over():
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 400.0}))
    room = plan.rooms[0]
    assert room.predicted_avg_lumen >= room.target_lux - 1e-6, "前提：照度得先达标"
    assert room.lpd_compliant is False
    joined = " ".join(room.warnings)
    assert "无法同时满足" in joined
    assert "光效" in joined


def test_needed_efficacy_matches_current_limit():
    """建议动作里的光效门槛按**现行值**算，不是目标值。"""
    plan = plan_layout(_task(300.0, extra={"fixture_power_w": 400.0}))
    room = plan.rooms[0]
    item = next(s for s in room.shortfalls if s["metric"] == "power_density")
    need = len(room.fixtures) * room.flux_per_fixture / (
        LPD_CURRENT_LIMIT_W_PER_M2 * room.area)
    assert f"{need:.0f}" in item["action"]
