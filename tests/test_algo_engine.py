"""P2 算法层引擎测试：TaskSpec → 布灯方案 + 合规判定 + 诚实性说明。

引擎是算法层对外的门面，这里钉三件事：

1. 端到端能给出【达标】的方案（208 会议室：500 lx 目标下 U0 也要过 0.60）；
2. 求解器真的会为了 U0 去调排布，而不是只会加灯；
3. 拿不到硬信息（光通量）时宁可报错，不许猜一个数把方案变成编造。
"""
from __future__ import annotations

import pytest

from optiflow.algo.engine import ALGO_LIMITS, DEFAULT_U0_REQUIRED, plan_layout
from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec

FLUX = 3000.0


def _room(space_id: str = "room-208", w: float = 11.9, h: float = 8.78,
          height: float = 3.0, reflectance=None) -> Space:
    outline = [Point(x=0, y=0), Point(x=w, y=0), Point(x=w, y=h), Point(x=0, y=h)]
    return Space(
        id=space_id, name="测试房间",
        geometry=Geometry(kind="room", outline=outline, height=height),
        work_plane=0.75,
        reflectance=reflectance if reflectance is not None else {
            "ceiling": 0.7, "wall": 0.5, "floor": 0.2},
    )


def _task(spaces=None, target: float = 500.0, flux: float = FLUX, extra=None) -> TaskSpec:
    kwargs = {"flux": flux}
    if extra:
        kwargs.update(extra)
    return TaskSpec(
        kind="layout",
        spaces=spaces if spaces is not None else [_room()],
        constraints=[Metric(name="illuminance_avg", target=target, unit="lx")],
        extra=kwargs,
    )


def test_meeting_room_plan_is_compliant():
    """208 会议室：目标 500 lx，求解器要同时把平均照度和均匀度都做达标。"""
    plan = plan_layout(_task())
    assert plan.meets_illuminance
    assert plan.meets_uniformity
    assert plan.compliant
    assert plan.metric("illuminance_avg") >= 500.0
    assert plan.metric("uniformity_u0") >= DEFAULT_U0_REQUIRED
    assert plan.metric("fixture_count") > 0
    # metric 里的面积保留两位小数（给人看的数）
    assert plan.metric("area") == pytest.approx(11.9 * 8.78, abs=0.01)


def test_plan_is_deterministic():
    """同样的输入必须给同样的方案——否则评审时数字对不上。"""
    a = plan_layout(_task())
    b = plan_layout(_task())
    assert [f.position for f in a.fixtures] == [f.position for f in b.fixtures]
    assert a.metrics == b.metrics


def test_fixtures_and_metrics_agree():
    """结果里的灯具数、metric、以及实际 Fixture 列表必须一致。"""
    plan = plan_layout(_task())
    assert len(plan.fixtures) == int(plan.metric("fixture_count"))
    assert len(plan.fixtures) == sum(r.layout.count for r in plan.rooms)
    for f in plan.fixtures:
        assert f.properties["flux"] == pytest.approx(FLUX)
        assert f.position.z == pytest.approx(3.0)
        assert f.id


def test_fixtures_inside_the_room():
    plan = plan_layout(_task())
    for f in plan.fixtures:
        assert 0.0 <= f.position.x <= 11.9
        assert 0.0 <= f.position.y <= 8.78

def test_solver_improves_uniformity_instead_of_only_adding_fixtures():
    """核心行为：常规排布 U0 不达标时，先把边缘灯推向墙，而不是只加灯。

    实测：31 盏灯 edge_factor=0.5 时 U0 约 0.52，收紧到 0.25 后约 0.69；
    而单纯把灯加到 100 盏也只能到 0.52。这条测试守住这个取舍不被退化。
    """
    naive = plan_layout(_task(), solve_uniformity=False)
    solved = plan_layout(_task())
    assert naive.rooms[0].uniformity.u0 < DEFAULT_U0_REQUIRED
    assert solved.metric("uniformity_u0") >= DEFAULT_U0_REQUIRED
    assert solved.metric("fixture_count") == naive.metric("fixture_count")
    assert solved.rooms[0].edge_factor < naive.rooms[0].edge_factor
    assert any("边缘间距" in w for w in solved.warnings)


def test_unreachable_uniformity_is_reported_not_hidden():
    """要求高到做不到时，必须如实说不达标，不能悄悄给一个看起来达标的数。"""
    plan = plan_layout(_task(), u0_required=0.95)
    assert plan.meets_uniformity is False
    assert plan.compliant is False
    assert plan.metric("uniformity_ok") == 0.0
    assert any("仍不达标" in w for w in plan.warnings)


def test_u0_requirement_can_come_from_task_constraints():
    task = _task()
    task.constraints.append(Metric(name="uniformity_u0", target=0.35, unit=""))
    plan = plan_layout(task)
    assert plan.rooms[0].u0_required == pytest.approx(0.35)
    assert plan.meets_uniformity


def test_missing_flux_raises_instead_of_guessing():
    """光通量是硬信息。猜一个数会让整份方案变成编造，必须报错。"""
    task = TaskSpec(kind="layout", spaces=[_room()],
                    constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")])
    with pytest.raises(ValueError, match="缺少单灯光通量"):
        plan_layout(task)


def test_flux_can_come_from_a_fixture_template():
    task = TaskSpec(
        kind="layout", spaces=[_room()],
        fixtures=[Fixture(id="tpl", name="模板灯", properties={"flux": 4200.0})],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
    )
    plan = plan_layout(task)
    assert plan.rooms[0].flux_per_fixture == pytest.approx(4200.0)


def test_assumptions_declare_the_model_boundaries():
    """假设必须写清楚，尤其是这不是仿真结果这件事。"""
    plan = plan_layout(_task())
    joined = " ".join(plan.assumptions)
    assert "朗伯" in joined
    assert "维护系数" in joined
    assert "二次反射" in joined
    assert "仅直射" in joined


def test_capability_limits_are_honest():
    joined = " ".join(ALGO_LIMITS)
    assert "预测方案" in joined and "不是仿真结论" in joined
    assert "厂商" in joined


def test_multi_room_plan_sums_area_and_reports_worst_uniformity():
    plan = plan_layout(_task(spaces=[_room("A", 11.9, 8.78), _room("B", 6.0, 5.0)]))
    assert plan.metric("rooms") == 2.0
    # metric 里的面积保留两位小数（给人看的数），容差按 0.01 取
    assert plan.metric("area") == pytest.approx(11.9 * 8.78 + 6.0 * 5.0, abs=0.01)
    worst = min(r.uniformity.u0 for r in plan.rooms)
    assert plan.metric("uniformity_u0") == pytest.approx(worst, abs=1e-3)


def test_non_room_space_is_skipped_with_a_warning():
    polyline = Space(id="px", name="线",
                     geometry=Geometry(kind="polyline",
                                       outline=[Point(x=0, y=0), Point(x=1, y=0)]))
    plan = plan_layout(_task(spaces=[_room(), polyline]))
    assert plan.metric("rooms") == 1.0
    assert any("跳过非房间空间" in w for w in plan.warnings)


def test_room_without_height_is_rejected():
    bad = Space(id="bad", name="没净高",
                geometry=Geometry(kind="room", outline=[
                    Point(x=0, y=0), Point(x=4, y=0), Point(x=4, y=3)]))
    with pytest.raises(ValueError, match="缺 geometry.height"):
        plan_layout(_task(spaces=[bad]))


def test_empty_spaces_are_rejected():
    with pytest.raises(ValueError, match="没有空间"):
        plan_layout(TaskSpec(kind="layout", spaces=[], extra={"flux": FLUX}))


def test_plan_is_fast_enough_to_be_useful():
    """算法层的卖点是毫秒级出方案——慢到几十秒就失去意义了。"""
    import time
    start = time.monotonic()
    plan = plan_layout(_task())
    elapsed = time.monotonic() - start
    assert plan.metric("fixture_count") > 0
    assert elapsed < 5.0, f"布灯方案耗时 {elapsed:.2f}s，太慢"


