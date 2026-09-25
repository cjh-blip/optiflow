"""P2 算法层测试：光通量法 / 布灯排布 / 逐点法 / 引擎。

这些测试刻意钉的是【物理与数学事实】，不是实现的当前行为：

- 朗伯配光的光通量积分必须等于灯具光通量（能量守恒）；
- 无限工作面收到的总光通量必须等于灯具光通量；
- 房间指数、灯具数、均匀度都是可手算复现的闭式；
- 房间角落暗、边缘灯推向墙能救 U0，是实测出来的建模结论，不容许悄悄回退。

凡「坏了没人会知道」的地方都配了反向用例。
"""
from __future__ import annotations

import math

import pytest

from optiflow.algo.layout import (
    DEFAULT_EDGE_FACTOR,
    GridLayout,
    plan_grid,
    spacing_violation,
)
from optiflow.algo.lumen import (
    DEFAULT_MAINTENANCE_FACTOR,
    ROOM_INDEX_STOPS,
    UtilizationOutOfRange,
    average_illuminance,
    fixture_count,
    room_index,
    uf_lookup,
)
from optiflow.algo.uniformity import (
    Lambertian,
    PhotoFixture,
    distance_to_boundary,
    evaluate,
    is_convex,
    point_illuminance,
    sampling_grid,
    uniformity_report,
)

ROOM = [(0.0, 0.0), (9.57, 0.0), (9.57, 12.97), (0.0, 12.97)]
FLUX = 3000.0
MOUNT = 3.0
WORK_PLANE = 0.75
HM = MOUNT - WORK_PLANE  # 2.25


# ============================================================ 光通量法


def test_room_index_matches_closed_form():
    """K = (L·W)/(Hm·(L+W))，可手算。"""
    k = room_index(9.57, 12.97, MOUNT, WORK_PLANE)
    assert k == pytest.approx(9.57 * 12.97 / (HM * (9.57 + 12.97)))
    assert k == pytest.approx(2.4475, abs=1e-4)


def test_room_index_rejects_impossible_geometry():
    with pytest.raises(ValueError, match="房间尺寸必须为正"):
        room_index(0.0, 5.0, MOUNT)
    with pytest.raises(ValueError, match="必须高于工作面"):
        room_index(5.0, 5.0, 0.5, WORK_PLANE)


def test_room_index_grows_with_room_size():
    """房间越大（相对高度）K 越大——这是 K 的物理含义，不是实现细节。"""
    assert room_index(20.0, 15.0, MOUNT, WORK_PLANE) > room_index(5.0, 4.0, MOUNT, WORK_PLANE)


def test_uf_table_node_is_returned_verbatim():
    """落在采样点上时必须原样返回表值，不能因为插值被改动。"""
    uf = uf_lookup(2.0, 0.70, 0.50)
    assert uf == pytest.approx(0.69)


def test_uf_increases_with_room_index():
    """K 越大反射利用越充分，UF 必须单调不减。"""
    values = [uf_lookup(k, 0.70, 0.50) for k in ROOM_INDEX_STOPS]
    assert values == sorted(values)
    assert values[-1] > values[0]
    assert 0.0 < values[0] < 1.0


def test_uf_bilinear_interpolation_between_nodes():
    """反射率落在表的两档之间时做双线性插值，结果应落在两档之间。"""
    low = uf_lookup(2.0, 0.50, 0.30)
    high = uf_lookup(2.0, 0.70, 0.50)
    mid = uf_lookup(2.0, 0.60, 0.40)
    assert low < mid < high
    assert mid == pytest.approx((low + high) / 2, rel=1e-9)


def test_uf_out_of_range_raises_instead_of_extrapolating():
    """宁可报错也不外推——外推出来的是一个看起来精确但没依据的数。"""
    with pytest.raises(UtilizationOutOfRange):
        uf_lookup(2.0, 0.95, 0.50)
    with pytest.raises(UtilizationOutOfRange):
        uf_lookup(2.0, 0.70, 0.90)


def test_uf_accepts_caller_supplied_table():
    custom = {(0.7, 0.5): {1.0: 0.9, 2.0: 0.9}}
    assert uf_lookup(1.5, 0.70, 0.50, table=custom) == pytest.approx(0.9)


def test_fixture_count_ceils_so_the_target_is_actually_met():
    """向上取整：宁可多一盏，也不能算出一个达不到目标的数。"""
    area = 9.57 * 12.97
    uf = uf_lookup(room_index(9.57, 12.97, MOUNT, WORK_PLANE), 0.7, 0.5)
    n = fixture_count(500.0, area, FLUX, uf, DEFAULT_MAINTENANCE_FACTOR)
    raw = 500.0 * area / (FLUX * uf * DEFAULT_MAINTENANCE_FACTOR)
    assert n == math.ceil(raw) == 36
    achieved = average_illuminance(n, FLUX, uf, area, DEFAULT_MAINTENANCE_FACTOR)
    assert achieved >= 500.0
    assert average_illuminance(n - 1, FLUX, uf, area, DEFAULT_MAINTENANCE_FACTOR) < 500.0


@pytest.mark.parametrize("kwargs", [
    {"target_lux": 0.0},
    {"area_m2": 0.0},
    {"flux_per_fixture": 0.0},
    {"uf": 0.0},
    {"uf": 1.5},
    {"mf": 0.0},
])
def test_fixture_count_rejects_nonphysical_input(kwargs):
    base = {"target_lux": 500.0, "area_m2": 10.0, "flux_per_fixture": 3000.0,
            "uf": 0.7, "mf": 0.8}
    base.update(kwargs)
    with pytest.raises(ValueError):
        fixture_count(**base)


# ============================================================ 配光与逐点法


def test_lambertian_flux_integrates_to_the_rated_flux():
    """能量守恒：∫I(γ)dΩ 必须等于灯具光通量。这条错了整个照度计算就没意义。"""
    d = Lambertian(flux=FLUX)
    total = 0.0
    n_gamma = 2000
    dg = (math.pi / 2) / n_gamma
    # 只在 γ 上做数值积分，φ 方向解析积掉（旋转对称，乘 2π）。
    # 注意别把 2π 写成 dphi——那样会漏掉 φ 方向的循环，结果差 N_phi 倍。
    for i in range(n_gamma):
        gamma = (i + 0.5) * dg
        total += d.intensity(math.degrees(gamma)) * math.sin(gamma) * dg * (2 * math.pi)
    assert total == pytest.approx(FLUX, rel=1e-3)


def test_lambertian_goes_dark_above_the_horizon():
    """下射型灯具上半球不发光。"""
    d = Lambertian(flux=FLUX)
    assert d.intensity(0.0) == pytest.approx(FLUX / math.pi)
    assert d.intensity(90.0) == pytest.approx(0.0, abs=1e-9)
    assert d.intensity(120.0) == 0.0
    assert Lambertian(flux=FLUX).intensity(60.0) < d.intensity(0.0)


def test_lambertian_direct_illuminance_matches_the_trig_formula():
    """闭式解 (Φ/π)h²/(r²+h²)² 必须与 I(γ)cos(γ)/d² 一致（快路径不能算错）。"""
    d = Lambertian(flux=FLUX)
    for r in (0.0, 0.5, 2.0, 6.0):
        h = HM
        dist = math.hypot(r, h)
        cos_gamma = h / dist
        gamma = math.degrees(math.atan2(r, h))
        slow = d.intensity(gamma) * cos_gamma / (dist * dist)
        assert d.direct_illuminance(r, h) == pytest.approx(slow, rel=1e-12)


def test_single_fixture_lux_directly_below():
    """正下方 E = (Φ/π)/h²。这是可手算的锚点。"""
    f = PhotoFixture(x=0.0, y=0.0, height=HM, distribution=Lambertian(flux=FLUX))
    e = point_illuminance(0.0, 0.0, [f])
    assert e == pytest.approx((FLUX / math.pi) / HM ** 2, rel=1e-12)
    assert e == pytest.approx(188.6, abs=0.1)


def test_total_flux_landing_on_an_unbounded_work_plane_equals_rated_flux():
    """把工作面做大到边界可忽略，Σ E·dA 必须收敛到灯具光通量。

    这是比「公式对公式」强得多的检查：它同时验证配光归一化、几何因子和求和口径。
    """
    f = PhotoFixture(x=0.0, y=0.0, height=HM, distribution=Lambertian(flux=FLUX))
    step = 0.05
    half = 25.0
    n = int(2 * half / step)
    total = 0.0
    for i in range(n):
        x = -half + (i + 0.5) * step
        for j in range(n):
            y = -half + (j + 0.5) * step
            total += point_illuminance(x, y, [f]) * step * step
    # 容差 2%：理论截断损失约 0.8%（±25 m 之外漏掉的光）+ 离散化误差。
    # 不放到更松——漏掉一个 1/r² 因子这类错误会让结果差几个百分点，必须还抓得住。
    assert total == pytest.approx(FLUX, rel=2e-2)


def test_two_fixtures_add_up_linearly():
    """多灯是线性叠加（照明计算的基本前提）。"""
    a = PhotoFixture(x=1.0, y=1.0, height=HM, distribution=Lambertian(flux=FLUX))
    b = PhotoFixture(x=3.0, y=2.0, height=HM, distribution=Lambertian(flux=FLUX))
    assert point_illuminance(2.0, 4.0, [a, b]) == pytest.approx(
        point_illuminance(2.0, 4.0, [a]) + point_illuminance(2.0, 4.0, [b]), rel=1e-12)


def test_fixture_rejects_nonpositive_height():
    with pytest.raises(ValueError, match="净高必须为正"):
        PhotoFixture(x=0, y=0, height=0.0, distribution=Lambertian(flux=FLUX))


# ============================================================ 采样与均匀度


def test_is_convex_detects_rectangle_and_l_shape():
    assert is_convex(ROOM) is True
    l_shape = [(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]
    assert is_convex(l_shape) is False


def test_occlusion_only_changes_concave_rooms():
    """凸房间跳过遮挡判断必须与开启时逐点一致——这是性能优化，不能改变结果。"""
    f = PhotoFixture(x=2.0, y=2.0, height=HM, distribution=Lambertian(flux=FLUX))
    a = evaluate(ROOM, [f], spacing=0.5, margin=0.5, occlusion=True)
    b = evaluate(ROOM, [f], spacing=0.5, margin=0.5, occlusion=False)
    assert a.samples == b.samples


def test_occlusion_blocks_light_around_a_corner():
    """凹角后面的点必须比无遮挡时暗（否则遮挡逻辑是摆设）。"""
    l_shape = [(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]
    # 灯在右下角，计算点在左上角，连线必须穿过 L 形缺口外侧
    f = PhotoFixture(x=5.0, y=2.0, height=HM, distribution=Lambertian(flux=FLUX))
    blocked = point_illuminance(1.0, 5.0, [f], walls=l_shape)
    free = point_illuminance(1.0, 5.0, [f])
    assert blocked < free
    assert blocked == pytest.approx(0.0, abs=1e-9)  # 绕不过去，全挡


def test_sampling_grid_respects_margin_and_room():
    full = sampling_grid(ROOM, spacing=0.25, margin=0.0)
    inset = sampling_grid(ROOM, spacing=0.25, margin=0.5)
    assert len(inset) < len(full)
    for x, y in inset:
        assert 0.5 - 1e-9 <= x <= 9.57 - 0.5 + 1e-9
        assert 0.5 - 1e-9 <= y <= 12.97 - 0.5 + 1e-9
        assert distance_to_boundary(x, y, ROOM) >= 0.5 - 1e-9


def test_sampling_grid_rejects_bad_input():
    with pytest.raises(ValueError, match="采样间距必须为正"):
        sampling_grid(ROOM, spacing=0.0)
    with pytest.raises(ValueError, match="多边形至少需要"):
        sampling_grid([(0, 0), (1, 1)], spacing=0.5)


def test_distance_to_boundary_handles_inside_and_outside():
    assert distance_to_boundary(4.785, 6.485, ROOM) == pytest.approx(min(4.785, 12.97 - 6.485))
    assert distance_to_boundary(-1.0, 4.0, ROOM) == pytest.approx(1.0)


def test_uniformity_report_math():
    samples = [(0, 0, 100.0), (1, 0, 200.0), (2, 0, 300.0)]
    r = uniformity_report(samples, spacing=0.5, margin=0.0)
    assert r.e_avg == pytest.approx(200.0)
    assert r.e_min == pytest.approx(100.0)
    assert r.e_max == pytest.approx(300.0)
    assert r.u0 == pytest.approx(0.5)
    assert r.u1 == pytest.approx(1 / 3)
    assert r.sample_count == 3


def test_uniformity_report_refuses_empty_or_dark():
    with pytest.raises(ValueError, match="没有采样点"):
        uniformity_report([], spacing=0.5, margin=0.0)
    with pytest.raises(ValueError, match="均匀度无意义"):
        uniformity_report([(0, 0, 0.0)], spacing=0.5, margin=0.0)


def test_uniformity_is_symmetric_for_a_centred_fixture():
    """单灯居中时，工作面四角照度必须相等（对称性检查，能抓出坐标/索引错位）。"""
    f = PhotoFixture(x=4.785, y=6.485, height=HM, distribution=Lambertian(flux=FLUX))
    corners = [
        point_illuminance(1.0, 1.0, [f]),
        point_illuminance(8.57, 1.0, [f]),
        point_illuminance(8.57, 11.97, [f]),
        point_illuminance(1.0, 11.97, [f]),
    ]
    assert max(corners) == pytest.approx(min(corners), rel=1e-9)


# ============================================================ 布灯排布


def test_grid_places_exactly_count_fixtures_inside_the_room():
    layout = plan_grid(31, 9.57, 12.97, height=HM)
    assert layout.count == 31
    assert layout.nx * layout.ny >= 31
    for x, y in layout.positions:
        assert 0.0 <= x <= 9.57 and 0.0 <= y <= 12.97


def test_grid_prefers_square_spacing_over_minimal_waste():
    """31 盏灯要选接近方形的网格；长条组合会让 U0 掉。钉住这个取舍。"""
    layout = plan_grid(31, 9.57, 12.97, height=HM)
    assert layout.nx == 5 and layout.ny == 7
    # 长条组合会给出明显偏斜的间距（skew > 1.4），方形组合接近 1.0。
    # 钉住「两个方向不许差太多」，不钉死相等——短排铺满整幅时行距会略有出入。
    ratio = max(layout.spacing_x, layout.spacing_y) / min(layout.spacing_x, layout.spacing_y)
    assert ratio < 1.3, f"间距太不方：{layout.spacing_x:.2f} x {layout.spacing_y:.2f}"


def test_default_edge_factor_leaves_half_a_cell_at_the_wall():
    layout = plan_grid(2, 10.0, 4.0, height=HM, edge_factor=DEFAULT_EDGE_FACTOR)
    xs = sorted({p[0] for p in layout.positions})
    assert xs[0] == pytest.approx(10.0 / 2 / 2)
    assert xs[-1] == pytest.approx(10.0 - 10.0 / 2 / 2)


def test_smaller_edge_factor_pushes_edge_fixtures_towards_the_wall():
    """这是改善房间角落照度的旋钮，必须真的起作用。"""
    wide = plan_grid(31, 9.57, 12.97, height=HM, edge_factor=0.5)
    tight = plan_grid(31, 9.57, 12.97, height=HM, edge_factor=0.25)
    assert min(p[0] for p in tight.positions) < min(p[0] for p in wide.positions)
    assert min(p[1] for p in tight.positions) < min(p[1] for p in wide.positions)
    # 代价也要钉住：边缘灯推向墙 = 房间内部的可用跨度变大 = 内部灯距【变大】。
    # 所以这两件事是有取舍的，不是「越紧越好」，距高比约束必须同时看。
    assert tight.max_spacing > wide.max_spacing


def test_edge_factor_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="edge_factor"):
        plan_grid(6, 10.0, 8.0, height=HM, edge_factor=0.0)
    with pytest.raises(ValueError, match="edge_factor"):
        plan_grid(6, 10.0, 8.0, height=HM, edge_factor=0.9)


def test_spacing_to_height_ratio_flags_a_sparse_layout():
    """距高比是硬约束：灯距超过 1.5 倍净高就会出暗带。"""
    sparse = plan_grid(4, 12.0, 12.0, height=2.0)
    assert sparse.shr > 1.5
    assert sparse.fits is False
    reason = spacing_violation(sparse)
    assert reason is not None and "距高比" in reason


def test_spacing_to_height_ratio_passes_for_a_dense_layout():
    dense = plan_grid(100, 12.0, 12.0, height=2.25)
    assert dense.fits is True
    assert spacing_violation(dense) is None


def test_every_row_is_spread_across_the_full_width():
    """每一排（含没排满的短排）都要铺满整幅宽度——边角有灯，U0 才立得住。

    反例（曾实现过）：让短排从已有列里居中取，保持列对齐。看着规整，
    实测 31 盏灯时最后一排只放中间 3 盏、房间最后两角无灯，
    U0 从 0.52 掉到 0.23。边角覆盖远比列对齐重要。
    """
    layout = plan_grid(31, 9.57, 12.97, height=HM)
    rows: dict = {}
    for x, y in layout.positions:
        rows.setdefault(round(y, 6), []).append(x)
    assert len(rows) == layout.ny == 7
    # 短排按 cell 间距居中（cell = W/(nx-1)），范围 = (k-1)×cell；该比例随 nx 变化
    # （nx=7 时 83%、nx=5 时 75%），所以阈值取 0.7——钉的是「不许挤在中间」，不是具体比例。
    for xs in rows.values():
        assert max(xs) - min(xs) >= 0.7 * 9.57, "每一排都要铺开，不能挤在中间"
    allx = [p[0] for p in layout.positions]
    half_cell = 9.57 / layout.nx / 2
    assert min(allx) == pytest.approx(half_cell, rel=1e-6)
    assert max(allx) == pytest.approx(9.57 - half_cell, rel=1e-6)


def test_grid_rejects_bad_input():
    with pytest.raises(ValueError, match="灯具数量必须为正"):
        plan_grid(0, 10.0, 10.0)
    with pytest.raises(ValueError, match="房间尺寸必须为正"):
        plan_grid(4, 0.0, 10.0)
    with pytest.raises(ValueError, match="净高必须为正"):
        plan_grid(4, 10.0, 10.0, height=0.0)

