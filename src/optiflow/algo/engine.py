"""算法层引擎：TaskSpec → 布灯方案 + 预测指标 + 合规判定 + 诚实性说明。

流程（全部毫秒级，不需要任何软件在场）：

    TaskSpec
      → 每个房间算房间指数 K
      → 查利用系数 UF（或调用方自带厂商表）
      → 光通量法定灯具数 N
      → 居中网格排布（带距高比硬约束）
      → 逐点法算真实网格照度 → 均匀度 U0/U1
      → 合规判定：平均照度达标了吗？均匀度达标了吗？
      → 均匀度不达标时自动加密重排，并如实报出代价（平均照度会超标）

产出的是【预测方案】，不是仿真结论。要结论就得把方案提交给 DIALux 校核——
这正是算法层与软件层的分工（计划文档 Q7）。

一个容易被忽略的事实：**平均照度达标 ≠ 方案合格**。真实项目里 U0 先崩的情况很常见
（灯距太大，两灯之间出暗带）。所以这里把 U0 当一等公民，不达标就明说、并给出加密方案。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ir import Fixture, Metric, Point, Space, TaskSpec
from .layout import (
    DEFAULT_EDGE_FACTOR,
    DEFAULT_SHR_LIMIT,
    GridLayout,
    plan_grid,
    spacing_violation,
)
from .lumen import (
    DEFAULT_MAINTENANCE_FACTOR,
    DEFAULT_WORK_PLANE,
    average_illuminance,
    fixture_count,
    room_index,
    uf_lookup,
)
from .uniformity import Lambertian, PhotoFixture, UniformityReport, evaluate

#: EN 12464-1 对办公场所的均匀度下限（U0 = E_min / E_avg）
DEFAULT_U0_REQUIRED = 0.60

#: 均匀度不达标时，最多把灯数加到基准的几倍再放弃
MAX_FIXTURE_MULTIPLIER = 2.5

#: 自动搜索的最大评估次数（每次评估都要重算整个采样网格，不设上限会卡）
MAX_UNIFORMITY_SEARCH_STEPS = 24

#: 边缘间距的候选值（从常规的 0.5 逐级推向墙）。
#: 这是改善 U0 最有效的旋钮——实测 31 盏灯：0.5→U0 0.52，0.25→U0 0.69，
#: 而把灯数从 31 加到 100 只挪到 0.52。房间角落暗是「边缘灯离墙太远」，不是「灯不够多」。
EDGE_FACTOR_CANDIDATES = (0.5, 0.4, 0.3, 0.25)

#: 直射占比低于这个值时提醒方案过度依赖墙面反射
MIN_DIRECT_FRACTION = 0.5

#: 能力声明里要写的限制——算法层最容易被误当成仿真结果，所以写得直白些。
ALGO_LIMITS = [
    "结果是【预测方案】不是仿真结论：用利用系数法 + 朗伯配光近似算出来的，未做光线追踪",
    "利用系数默认表是工程经验值不是厂商数据；真实项目必须用厂商 UF/IES 覆盖，或用 DIALux 校核",
    "均匀度与直射照度按点光源近似 + 朗伯配光算；真实 LED 面板配光有差异，精确值以实测配光为准",
    "不处理家具遮挡与二次反射的逐点分布：有家具的房间 DIALux 结果会低于本预测",
]


@dataclass
class RoomPlan:
    """单个房间的布灯方案。"""

    space_id: str
    area: float
    room_index: float
    utilization_factor: float
    maintenance_factor: float
    target_lux: float
    u0_required: float
    lumen_method_count: int
    layout: GridLayout
    edge_factor: float
    uniformity: UniformityReport
    full_report: UniformityReport
    predicted_avg_lumen: float
    predicted_avg_initial: float
    flux_per_fixture: float
    fixtures: List[Fixture]
    warnings: List[str] = field(default_factory=list)
    relayout: Optional[Dict[str, Any]] = None

    @property
    def uniformity_ok(self) -> bool:
        return self.uniformity.u0 >= self.u0_required - 1e-9

    @property
    def direct_fraction(self) -> float:
        if self.predicted_avg_initial <= 0:
            return 0.0
        return self.full_report.e_avg / self.predicted_avg_initial


@dataclass
class LayoutPlan:
    """整份方案（可能含多个房间）。"""

    rooms: List[RoomPlan]
    metrics: List[Metric]
    assumptions: List[str]
    warnings: List[str]

    @property
    def fixtures(self) -> List[Fixture]:
        out: List[Fixture] = []
        for room in self.rooms:
            out.extend(room.fixtures)
        return out

    @property
    def meets_illuminance(self) -> bool:
        return all(r.predicted_avg_lumen >= r.target_lux - 1e-6 for r in self.rooms)

    @property
    def meets_uniformity(self) -> bool:
        return all(r.uniformity_ok for r in self.rooms)

    @property
    def compliant(self) -> bool:
        return self.meets_illuminance and self.meets_uniformity

    def metric(self, name: str) -> Optional[float]:
        for m in self.metrics:
            if m.name == name:
                return m.value
        return None


def _polygon_area(polygon: Sequence[Tuple[float, float]]) -> float:
    """鞋带公式（与 IR 的 Space.area 同口径）。"""
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    ring = list(polygon)
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _evaluate_count(count: int, polygon, flux: float, spacing: float, margin: float,
                    shr_limit: float, height_above_plane: float,
                    edge_factor: float = DEFAULT_EDGE_FACTOR,
                    ) -> Tuple[GridLayout, UniformityReport, UniformityReport]:
    """给定灯数算一遍：布局 + 均匀度口径采样 + 全房间口径采样。"""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    length, width = max(xs) - min(xs), max(ys) - min(ys)
    layout = plan_grid(count, length, width, origin=(min(xs), min(ys)),
                       height=height_above_plane, shr_limit=shr_limit,
                       edge_factor=edge_factor)
    distribution = Lambertian(flux=flux)
    photo = [PhotoFixture(x=x, y=y, height=height_above_plane, distribution=distribution)
             for x, y in layout.positions]
    report = evaluate(polygon, photo, spacing=spacing, margin=margin)
    # 交叉校验要用「整个房间」的口径：均匀度那套采样扣掉了贴墙暗带，
    # 拿它跟光通量法的全房间平均值比是无意义的（苹果比橘子）。
    full = evaluate(polygon, photo, spacing=spacing, margin=0.0)
    return layout, report, full


def plan_layout(task: TaskSpec, *,
                flux_per_fixture: Optional[float] = None,
                shr_limit: float = DEFAULT_SHR_LIMIT,
                margin: float = 0.5,
                spacing: float = 0.25,
                u0_required: Optional[float] = None,
                solve_uniformity: bool = True,
                uf_table: Optional[Dict[Tuple[float, float], Dict[float, float]]] = None) -> LayoutPlan:
    """算出整份布灯方案。纯计算，不接触任何外部软件。

    flux_per_fixture 缺省时按：task.extra["flux"] → 第一个 fixture 的 properties["flux"] → 报错。
    光通量是硬信息，猜一个数会让整份方案变成编造，所以宁可报错。

    solve_uniformity=True 时，若 U0 不达标会自动加密重排；加密的代价（平均照度超标）
    会写进 warning，不会假装没发生。
    """
    if not task.spaces:
        raise ValueError("TaskSpec 里没有空间，无法布灯")

    if flux_per_fixture is None:
        flux_per_fixture = task.extra.get("flux")
    if flux_per_fixture is None:
        for f in task.fixtures:
            if "flux" in f.properties:
                flux_per_fixture = f.properties["flux"]
                break
    if flux_per_fixture is None:
        raise ValueError(
            "缺少单灯光通量：请在 task.extra['flux'] 或 fixtures[].properties['flux'] 里给出。"
            "光通量是硬信息，猜一个会让整份方案变成编造。"
        )
    flux_per_fixture = float(flux_per_fixture)

    target_lux = task.target_of("illuminance_avg")
    if target_lux is None:
        target_lux = float(task.extra.get("target_lux", 500.0))

    if u0_required is None:
        u0_required = task.target_of("uniformity_u0")
    if u0_required is None:
        u0_required = float(task.extra.get("uniformity_u0", DEFAULT_U0_REQUIRED))

    maintenance = float(task.extra.get("maintenance_factor", DEFAULT_MAINTENANCE_FACTOR))

    assumptions: List[str] = []
    all_warnings: List[str] = []
    rooms: List[RoomPlan] = []

    for space in task.spaces:
        if space.geometry.kind != "room":
            all_warnings.append(f"跳过非房间空间 {space.id}（kind={space.geometry.kind}）")
            continue
        polygon = [(p.x, p.y) for p in space.geometry.outline]
        area = _polygon_area(polygon)
        if area <= 0:
            all_warnings.append(f"跳过零面积空间 {space.id}")
            continue
        mount_height = space.geometry.height
        if mount_height is None:
            raise ValueError(f"空间 {space.id} 缺 geometry.height，算不出房间指数")
        work_plane = space.work_plane if space.work_plane is not None else DEFAULT_WORK_PLANE
        ceiling_r = float(space.reflectance.get("ceiling", 0.70))
        wall_r = float(space.reflectance.get("wall", 0.50))

        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        length, width = max(xs) - min(xs), max(ys) - min(ys)
        height_above_plane = mount_height - work_plane

        k = room_index(length, width, mount_height, work_plane)
        uf = uf_lookup(k, ceiling_r, wall_r, table=uf_table)
        count = fixture_count(target_lux, area, flux_per_fixture, uf, maintenance)

        # solve_uniformity=False 时必须退回【常规排布】而不是仍然偷偷挑最优——
        # 否则「关掉求解器」和「打开求解器」给出同一个方案，这个开关就是假的。
        edge_candidates = EDGE_FACTOR_CANDIDATES if solve_uniformity else (DEFAULT_EDGE_FACTOR,)

        def best_at(n: int) -> Tuple[GridLayout, UniformityReport, UniformityReport, float]:
            """给定灯数，在允许的边缘间距候选里挑 U0 最好的那个排布。"""
            best = None
            for ef in edge_candidates:
                lay, rep, fl = _evaluate_count(
                    n, polygon, flux_per_fixture, spacing, margin, shr_limit,
                    height_above_plane, ef)
                # 同分时偏好更大的 edge_factor（更接近常规排布，边缘灯不至于贴墙）
                key = (rep.u0, ef)
                if best is None or key > best[0]:
                    best = (key, lay, rep, fl, ef)
            assert best is not None
            _k, lay, rep, fl, ef = best
            return lay, rep, fl, ef

        layout, report, full, edge_factor = best_at(count)

        room_warnings: List[str] = []
        violation = spacing_violation(layout)
        if violation:
            room_warnings.append(violation)

        relayout: Optional[Dict[str, Any]] = None
        if solve_uniformity and report.u0 < u0_required - 1e-9:
            base_count, base_u0 = count, report.u0
            ceil_count = int(count * MAX_FIXTURE_MULTIPLIER)
            step = max(1, count // 8)
            tried = 0
            candidate = count
            while tried < MAX_UNIFORMITY_SEARCH_STEPS and candidate < ceil_count:
                candidate = min(candidate + step, ceil_count)
                tried += 1
                c_layout, c_report, c_full, c_ef = best_at(candidate)
                if c_report.u0 >= u0_required - 1e-9:
                    relayout = {
                        "base_count": base_count,
                        "base_u0": base_u0,
                        "count": candidate,
                        "u0": c_report.u0,
                        "edge_factor": c_ef,
                        "illuminance_avg": average_illuminance(
                            candidate, flux_per_fixture, uf, area, maintenance),
                    }
                    layout, report, full, edge_factor = c_layout, c_report, c_full, c_ef
                    count = candidate
                    break

        if report.u0 < u0_required - 1e-9:
            room_warnings.append(
                f"均匀度 U0={report.u0:.2f} 低于要求 {u0_required:.2f}；已把灯数试到 {count} 盏、"
                f"边缘间距推到 {edge_factor:.2f} 仍不达标，需要换更宽配光的灯具"
            )
        elif relayout is not None:
            over = relayout["illuminance_avg"] / target_lux - 1.0
            note = "" if relayout["count"] == relayout["base_count"] else (
                f"、灯数从 {relayout['base_count']} 加到 {relayout['count']}"
            )
            room_warnings.append(
                f"为满足 U0>={u0_required:.2f}，把边缘间距收到 {relayout['edge_factor']:.2f} 个格距"
                f"{note}（U0 {relayout['base_u0']:.2f}→{relayout['u0']:.2f}）；"
                f"代价是平均照度升到 {relayout['illuminance_avg']:.0f} lx（超目标 {over:.0%}）"
            )
        elif solve_uniformity and edge_factor < DEFAULT_EDGE_FACTOR - 1e-9:
            # 只靠收紧边缘间距就达标了（灯数没动）——这件事同样必须说出来：
            # 用户以为自己拿到的是常规排布，实际上边缘灯已经贴到墙边了。
            conventional_u0 = _evaluate_count(
                count, polygon, flux_per_fixture, spacing, margin, shr_limit,
                height_above_plane, DEFAULT_EDGE_FACTOR)[1].u0
            room_warnings.append(
                f"为满足 U0>={u0_required:.2f}，边缘间距从 {DEFAULT_EDGE_FACTOR:.2f} 收到 "
                f"{edge_factor:.2f} 个格距（U0 {conventional_u0:.2f}→{report.u0:.2f}，灯数不变）；"
                f"边缘灯更贴墙，注意与窗帘盒/墙面的安装冲突"
            )

        predicted_initial = average_illuminance(count, flux_per_fixture, uf, area, 1.0)
        predicted_lumen = average_illuminance(count, flux_per_fixture, uf, area, maintenance)
        if predicted_initial > 0:
            direct_fraction = full.e_avg / predicted_initial
            if direct_fraction > 1.0 + 1e-3:
                room_warnings.append(
                    f"仅直射照度 {full.e_avg:.1f} lx 已超过含反射的初始总量 "
                    f"{predicted_initial:.1f} lx（直射占比 {direct_fraction:.0%}）："
                    f"内置利用系数表对这个房间偏低，灯具数会偏保守；"
                    f"建议用厂商 UF/IES 或直接交 DIALux 校核"
                )
            elif direct_fraction < MIN_DIRECT_FRACTION:
                room_warnings.append(
                    f"直射占比仅 {direct_fraction:.0%}（直射 {full.e_avg:.1f} lx / 初始总量 "
                    f"{predicted_initial:.1f} lx），方案高度依赖墙面反射；"
                    f"墙面吸光或家具遮挡时实际会更暗，务必用 DIALux 校核"
                )

        fixtures = [
            Fixture(
                id=f"{space.id}-L{i + 1}",
                name=str(task.extra.get("fixture_name", "灯具")),
                position=Point(x=round(x, 3), y=round(y, 3), z=round(mount_height, 3)),
                properties={"flux": flux_per_fixture},
            )
            for i, (x, y) in enumerate(layout.positions)
        ]

        rooms.append(RoomPlan(
            space_id=space.id, area=area, room_index=k, utilization_factor=uf,
            maintenance_factor=maintenance, target_lux=target_lux, u0_required=u0_required,
            lumen_method_count=count, layout=layout, uniformity=report, full_report=full,
            edge_factor=edge_factor,
            predicted_avg_lumen=predicted_lumen, predicted_avg_initial=predicted_initial,
            flux_per_fixture=flux_per_fixture, fixtures=fixtures,
            warnings=room_warnings, relayout=relayout,
        ))
        all_warnings.extend(f"{space.id}: {w}" for w in room_warnings)

    if not rooms:
        raise ValueError("没有任何可布灯的房间（全部被跳过）")

    assumptions.append(
        f"利用系数取【{'调用方提供的表' if uf_table else '内置工程默认表'}】，"
        f"维护系数取 {maintenance:.2f}"
    )
    assumptions.append("配光按半球朗伯近似（I(γ)=(Φ/π)cosγ）；真实面板需换实测 IES 配光")
    assumptions.append(
        "illuminance_avg 是含二次反射的设计值（乘了维护系数）；"
        "illuminance_direct_avg 是仅直射、初始光通量的值，两者口径不同，不可直接相减"
    )
    assumptions.append(f"均匀度按 {spacing} m 网格、距墙 {margin} m 边距采样")
    assumptions.append(
        "U0 是【逐点法仅直射】算出来的，不含墙面二次反射；二次反射会先照亮最暗的角落，"
        "所以真实 U0 只会比它更高——拿它当设计门槛是偏保守的"
    )
    assumptions.append(
        "边缘间距因子：" + "、".join(
            f"{r.space_id}={r.edge_factor:.2f}" for r in rooms
        ) + "（0.5 = 常规贴墙半格距）"
    )
    assumptions.append("点光源近似，不处理家具遮挡与二次反射的逐点分布")

    total_area = sum(r.area for r in rooms)
    total_fixtures = sum(r.layout.count for r in rooms)
    design_avg = sum(r.predicted_avg_lumen * r.area for r in rooms) / total_area
    direct_avg = sum(r.full_report.e_avg * r.area for r in rooms) / total_area
    worst_u0 = min(r.uniformity.u0 for r in rooms)
    worst_u1 = min(r.uniformity.u1 for r in rooms)
    e_min = min(r.uniformity.e_min for r in rooms)
    e_max = max(r.uniformity.e_max for r in rooms)

    if design_avg < target_lux:
        all_warnings.append(
            f"预测平均照度 {design_avg:.1f} lx 低于目标 {target_lux:.0f} lx"
            f"（向上取整后仍欠，检查光通量或利用系数）"
        )

    metrics = [
        Metric(name="fixture_count", value=float(total_fixtures), unit="盏"),
        Metric(name="rooms", value=float(len(rooms)), unit="间"),
        Metric(name="area", value=round(total_area, 2), unit="m2"),
        Metric(name="illuminance_avg", value=round(design_avg, 1), unit="lx", target=target_lux),
        Metric(name="illuminance_direct_avg", value=round(direct_avg, 1), unit="lx"),
        Metric(name="illuminance_min", value=round(e_min, 1), unit="lx"),
        Metric(name="illuminance_max", value=round(e_max, 1), unit="lx"),
        Metric(name="uniformity_u0", value=round(worst_u0, 3), unit="", target=u0_required),
        Metric(name="uniformity_u1", value=round(worst_u1, 3), unit=""),
        Metric(name="room_index", value=round(rooms[0].room_index, 3), unit=""),
        Metric(name="utilization_factor", value=round(rooms[0].utilization_factor, 3), unit=""),
        Metric(name="uniformity_ok", value=1.0 if all(r.uniformity_ok for r in rooms) else 0.0, unit=""),
        Metric(name="compliant", value=1.0 if all(
            r.uniformity_ok and r.predicted_avg_lumen >= r.target_lux for r in rooms) else 0.0, unit=""),
    ]
    return LayoutPlan(rooms=rooms, metrics=metrics, assumptions=assumptions, warnings=all_warnings)
