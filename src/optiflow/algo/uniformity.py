"""逐点照度法 —— 均匀度只能靠它算，光通量法结构上算不出来。

单盏灯对一个点的照度（点光源近似）：

    E = I(γ) · cos(γ) / d²  =  I(γ) · h / d³

其中 γ 是「灯具下射轴」与「灯具到该点方向」的夹角，h 是灯具距工作面的净高，
d 是灯具到该点的距离。多点照度 = 各灯贡献之和。

性能上有两个关键优化（都被真实跑爆过）：

1. 朗伯配光有闭式解：E = (Φ/π)·h²/(r²+h²)²，不必逐点算反三角函数。
   采样网格动辄上千点 x 几十盏灯，省掉三角函数是数量级的差别。
2. 墙体遮挡只在【凹多边形】房间才算。凸房间内任意两点连线必在室内，
   逐个采样判断纯属浪费（大房间会因此慢上几百倍）。

均匀度指标（EN 12464-1 口径）：U0 = E_min/E_avg，U1 = E_min/E_max。
注意 E_min 是【工作面上采样点】的最小值：网格越密、边距越小，U0 越低。
所以报告必须带采样参数，否则数字没法复现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..geometry import point_in_polygon

Point2 = Tuple[float, float]

# 遮挡判定已改为「线段与房间边求交」的精确算法（O(边数)），
# 原沿线段采样所用的 OCCLUSION_STEP / OCCLUSION_MAX_SAMPLES 随之退役。

#: 采样间距（米）
DEFAULT_SPACING = 0.25

#: 均匀度采样距墙边距（米）。EN 12464-1 的均匀度按「扣除边缘带」的工作面算。
DEFAULT_MARGIN = 0.5


class Distribution:
    """配光。子类至少实现 intensity()，可覆盖 direct_illuminance() 求性能。"""

    def intensity(self, gamma_deg: float, c_plane_deg: float = 0.0) -> float:
        raise NotImplementedError

    def direct_illuminance(self, r: float, h: float) -> float:
        """水平距离 r、净高 h 处由本灯具单独产生的照度（lx）。"""
        d = math.hypot(r, h)
        cos_gamma = h / d
        gamma = math.degrees(math.atan2(r, h))
        return self.intensity(gamma) * cos_gamma / (d * d)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__}>"


@dataclass(frozen=True)
class Lambertian(Distribution):
    """半球朗伯配光：I(γ) = (Φ/π)·cos(γ)。

    总光通量校验：∫I dΩ = 2π·(Φ/π)·∫cosγ·sinγ dγ = Φ，正好等于灯具光通量。
    真实 LED 面板不是纯朗伯（边缘更亮/更暗都有），拿它算均匀度是【近似】，
    报告里必须注明；要精确就换 IES 实测配光。
    """

    flux: float

    def __post_init__(self) -> None:
        if self.flux <= 0:
            raise ValueError(f"光通量必须为正：{self.flux}")

    def intensity(self, gamma_deg: float, c_plane_deg: float = 0.0) -> float:
        if gamma_deg < 0 or gamma_deg > 90.0:
            return 0.0  # 下射型：上半球不发光
        return (self.flux / math.pi) * math.cos(math.radians(gamma_deg))

    def direct_illuminance(self, r: float, h: float) -> float:
        """闭式解：E = (Φ/π)·h²/(r²+h²)²。等价于 I(γ)cos(γ)/d²，但没有三角函数。"""
        d2 = r * r + h * h
        return (self.flux / math.pi) * h * h / (d2 * d2)


@dataclass(frozen=True)
class PhotoFixture:
    """计算用的灯具：位置 + 距【工作面】的净高 + 配光。"""

    x: float
    y: float
    height: float
    distribution: Distribution

    def __post_init__(self) -> None:
        if self.height <= 0:
            raise ValueError(f"灯具距工作面的净高必须为正：{self.height}")


# 射线法提到 optiflow/geometry.py（适配器也要用，不能再各自实现一份）


def is_convex(polygon: Sequence[Point2]) -> bool:
    """多边形是否凸。凸房间内任意两点连线必在室内，遮挡判断可以整段跳过。"""
    ring = list(polygon)
    if len(ring) < 3:
        return False
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    n = len(ring)
    if n < 3:
        return False
    signs = set()
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        x3, y3 = ring[(i + 2) % n]
        cross = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)
        if abs(cross) > 1e-12:
            signs.add(cross > 0)
        if len(signs) > 1:
            return False
    return True


def _segment_blocked(ax: float, ay: float, bx: float, by: float,
                     polygon: Sequence[Point2]) -> bool:
    """灯具到计算点的水平连线是否被墙挡住（中途离开房间即视为挡光）。

    只在凹房间需要调用。点光源 + 凸房间时，连线必在室内，光路上没有墙。

    判定用「线段与房间边求交」的精确算法：连线上存在落在线段内部（非端点）的
    边界交点 → 挡光。原先的实现是沿线段按 OCCLUSION_STEP 采样 + 点在多边形内
    判定，两者语义等价，但采样法在复杂多边形上是 O(采样点×边数)，慢两个数量级
    （2026-10-08 实测：17 顶点房间单次 evaluate 12.1 s，求交法后 <1 s）。
    """
    ring = list(polygon)
    if len(ring) < 3:
        return False
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    abx, aby = bx - ax, by - ay
    eps = 1e-9
    for (cx, cy), (dx, dy) in zip(ring, ring[1:]):
        cdx, cdy = dx - cx, dy - cy
        denom = abx * cdy - aby * cdx
        if abs(denom) < 1e-12:
            continue  # 平行（含共线）：此情形对结果的影响可忽略
        acx, acy = cx - ax, cy - ay
        t = (acx * cdy - acy * cdx) / denom
        if t <= eps or t >= 1.0 - eps:
            continue  # 交点落在连线端点（灯/计算点贴边）：不算挡光
        s = (acx * aby - acy * abx) / denom
        if -eps <= s <= 1.0 + eps:
            return True
    return False


def point_illuminance(px: float, py: float, fixtures: Sequence[PhotoFixture],
                      walls: Optional[Sequence[Point2]] = None) -> float:
    """工作面上一点的总照度（lx）= 各灯贡献之和。walls 非空时剔除被墙挡住的灯。"""
    total = 0.0
    for f in fixtures:
        r = math.hypot(px - f.x, py - f.y)
        if walls is not None and _segment_blocked(f.x, f.y, px, py, walls):
            continue
        total += f.distribution.direct_illuminance(r, f.height)
    return total


def distance_to_boundary(px: float, py: float, polygon: Sequence[Point2]) -> float:
    """点到多边形边界的最短距离（米）。点在多边形外时也返回正值（不区分内外）。"""
    if len(polygon) < 2:
        return float("inf")
    best = float("inf")
    ring = list(polygon)
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        if seg2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
        cx, cy = x1 + t * dx, y1 + t * dy
        best = min(best, math.hypot(px - cx, py - cy))
    return best


def sampling_grid(polygon: Sequence[Point2], spacing: float = DEFAULT_SPACING,
                  margin: float = DEFAULT_MARGIN) -> List[Point2]:
    """按网格采样工作面：落在房间内、且距墙不小于 margin 的点。

    margin 不是可有可无的装饰——EN 12464-1 规定的均匀度是按「扣除边缘带」的工作面算的，
    贴墙的点照度极低，算进去 U0 会毫无意义地掉下来。
    """
    if spacing <= 0:
        raise ValueError(f"采样间距必须为正：{spacing}")
    if margin < 0:
        raise ValueError(f"边距不能为负：{margin}")
    if len(polygon) < 3:
        raise ValueError(f"多边形至少需要 3 个顶点，实际 {len(polygon)}")

    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    nx = int(math.floor((max(xs) - min(xs)) / spacing)) + 1
    ny = int(math.floor((max(ys) - min(ys)) / spacing)) + 1
    pts: List[Point2] = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            px = min(xs) + i * spacing
            py = min(ys) + j * spacing
            if px > max(xs) or py > max(ys):
                continue
            if not point_in_polygon(px, py, polygon):
                continue
            if margin > 0 and distance_to_boundary(px, py, polygon) < margin - 1e-9:
                continue
            pts.append((px, py))
    return pts


@dataclass
class UniformityReport:
    """逐点法结果。采样参数一并带上，否则数字不可复现。"""

    e_avg: float
    e_min: float
    e_max: float
    u0: float
    u1: float
    sample_count: int
    spacing: float
    margin: float
    samples: List[Tuple[float, float, float]] = field(default_factory=list)

    def to_metrics(self) -> List[Tuple[str, float, str]]:
        return [
            ("illuminance_avg", self.e_avg, "lx"),
            ("illuminance_min", self.e_min, "lx"),
            ("illuminance_max", self.e_max, "lx"),
            ("uniformity_u0", self.u0, ""),
            ("uniformity_u1", self.u1, ""),
        ]


def uniformity_report(samples: Sequence[Tuple[float, float, float]],
                      spacing: float, margin: float) -> UniformityReport:
    """把 (x, y, E) 采样点汇总成均匀度报告。"""
    if not samples:
        raise ValueError("没有采样点，无法给出均匀度——请检查房间轮廓与边距设置")
    values = [e for _x, _y, e in samples]
    e_avg = sum(values) / len(values)
    e_min = min(values)
    e_max = max(values)
    if e_avg <= 0:
        raise ValueError(f"平均照度为 {e_avg}，均匀度无意义（检查灯具光通量与布灯）")
    return UniformityReport(
        e_avg=e_avg, e_min=e_min, e_max=e_max,
        u0=e_min / e_avg,
        u1=(e_min / e_max) if e_max > 0 else 0.0,
        sample_count=len(values), spacing=spacing, margin=margin,
        samples=list(samples),
    )


def evaluate(polygon: Sequence[Point2], fixtures: Sequence[PhotoFixture],
             spacing: float = DEFAULT_SPACING, margin: float = DEFAULT_MARGIN,
             occlusion: bool = True) -> UniformityReport:
    """一次算完：采样 → 逐点照度 → 均匀度报告。

    凸房间自动跳过墙体遮挡判断（几何上不可能挡光，算了纯属浪费）。
    """
    pts = sampling_grid(polygon, spacing=spacing, margin=margin)
    walls = polygon if (occlusion and not is_convex(polygon)) else None
    samples = [(x, y, point_illuminance(x, y, fixtures, walls=walls)) for x, y in pts]
    return uniformity_report(samples, spacing=spacing, margin=margin)
