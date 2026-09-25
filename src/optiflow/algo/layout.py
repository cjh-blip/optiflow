"""布灯排布 —— 把「需要几盏」变成「摆在哪」。

规则（照明设计的常规做法）：

1. 网格居中对称：两端留 edge_factor 个格距。edge_factor=0.5 是常规做法
   （贴墙留半个格距），调小（如 0.25）会把边缘那排灯推向墙。
2. 间距尽量接近正方形（sx ≈ sy）——长方形的间距会让房间出现明暗带。
3. 距高比约束：max(sx, sy) / Hm <= SHR_limit。破了它纵向均匀度必崩
   （两灯之间会出现明显的暗区），与横向排布多均匀无关。

为什么 edge_factor 值得单独做成旋钮：

实测发现【房间角落暗，不是因为灯不够多，而是因为边缘灯离墙太远】。
把灯数从 31 加到 100，逐点法算出的 U0 只从 0.41 挪到 0.52 就不动了——
因为角落那个灯永远缺一侧的邻居。把边缘灯推向墙却是直接有效的。
所以求解器要同时会调这两个旋钮（见 engine.py）。

SHR（spacing to height ratio）的典型上限：下射式面板 1.5，
窄配光筒灯可以到 2.0 以上。默认取保守的 1.5。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

DEFAULT_SHR_LIMIT = 1.5
DEFAULT_EDGE_FACTOR = 0.5


@dataclass(frozen=True)
class GridLayout:
    """一次布灯排布的结果。"""

    nx: int
    ny: int
    positions: List[Tuple[float, float]]
    spacing_x: float
    spacing_y: float
    height: float
    shr: float
    shr_limit: float
    edge_factor: float = DEFAULT_EDGE_FACTOR

    @property
    def max_spacing(self) -> float:
        return max(self.spacing_x, self.spacing_y)

    @property
    def fits(self) -> bool:
        """距高比是否满足约束。False 表示「按这个间距摆会出暗带」。"""
        return self.shr <= self.shr_limit + 1e-9

    @property
    def count(self) -> int:
        return len(self.positions)


def _axis_positions(n: int, extent: float, origin: float, edge_factor: float) -> List[float]:
    """一维排布：两端各留 edge_factor 个格距，其余均分。

    edge_factor=0.5 时退化成均匀居中网格（贴墙半个格距）。
    """
    if n <= 0:
        return []
    if n == 1:
        return [origin + extent / 2]
    cell = extent / n
    edge = edge_factor * cell
    span = extent - 2 * edge
    if span <= 0:  # 防止把灯推到墙外
        edge, span = extent * 0.1, extent * 0.8
    step = span / (n - 1)
    return [origin + edge + i * step for i in range(n)]


def plan_grid(count: int, length: float, width: float,
              origin: Tuple[float, float] = (0.0, 0.0),
              height: float = 2.25,
              shr_limit: float = DEFAULT_SHR_LIMIT,
              edge_factor: float = DEFAULT_EDGE_FACTOR) -> GridLayout:
    """把 count 盏灯排进 length x width 的房间，返回排布结果。

    nx/ny 的选择：先保证装得下（nx*ny >= count），再在装得下的方案里挑
    「间距最接近正方形」的那个——浪费一两格无所谓，长方形间距才是真问题
    （实测：31 盏灯选 8x4 会让 U0 掉到 0.41，改选 7x5 就回到 0.52）。
    """
    if count <= 0:
        raise ValueError(f"灯具数量必须为正：{count}")
    if length <= 0 or width <= 0:
        raise ValueError(f"房间尺寸必须为正：length={length}, width={width}")
    if height <= 0:
        raise ValueError(f"净高必须为正：{height}")
    if not 0 < edge_factor <= 0.5:
        raise ValueError(f"edge_factor 应在 (0, 0.5]：{edge_factor}")

    best = None
    for nx in range(1, count + 1):
        ny = math.ceil(count / nx)
        sx = length / nx
        sy = width / ny
        # 主目标：间距接近正方形（用长宽比的超出量衡量）
        # 次目标：别浪费太多格
        skew = max(sx, sy) / min(sx, sy) - 1.0
        waste = (nx * ny - count) / count
        score = skew + 0.3 * waste
        if best is None or score < best[0]:
            best = (score, nx, ny)
    assert best is not None
    _score, nx, ny = best

    ox, oy = origin
    ys = _axis_positions(ny, width, oy, edge_factor)
    positions: List[Tuple[float, float]] = []
    remaining = count
    for j in range(ny):
        if remaining <= 0:
            break
        rows_left = ny - j
        n_in_row = math.ceil(remaining / rows_left)
        # 每排（包括没排满的短排）都在【整幅宽度】里均分。
        #
        # 曾经试过「少的那排从已有列里居中取，保持列对齐」——看着更规整，
        # 实测却是灾难：31 盏灯 7 列 5 排时最后一排只放中间 3 盏，
        # 房间最后两个角完全没有灯，U0 从 0.52 掉到 0.23。
        # 边角覆盖比列对齐重要得多。
        for x in _axis_positions(n_in_row, length, ox, edge_factor):
            positions.append((x, ys[j]))
        remaining -= n_in_row

    # 间距取【行内 / 列间相邻距离的最大值】——距高比是约束，必须用最保守的数。
    # 不能拿「所有唯一坐标的最小间隔」当间距：不同排错位时会算出一个假的小值
    #（实测把 1.70 m 的灯距算成 0.14 m，距高比约束直接失效）。
    rows: dict = {}
    for x, y in positions:
        rows.setdefault(round(y, 9), []).append(x)
    row_gaps: List[float] = []
    for xs in rows.values():
        xs = sorted(xs)
        row_gaps.extend(b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-9)
    col_gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1e-9]
    sx = max(row_gaps) if row_gaps else length
    sy = max(col_gaps) if col_gaps else width
    shr = max(sx, sy) / height
    return GridLayout(nx=nx, ny=ny, positions=positions, spacing_x=sx, spacing_y=sy,
                      height=height, shr=shr, shr_limit=shr_limit, edge_factor=edge_factor)


def spacing_violation(layout: GridLayout) -> str | None:
    """距高比不满足时返回给人看的原因，满足返回 None。"""
    if layout.fits:
        return None
    return (
        f"距高比 {layout.shr:.2f} 超过上限 {layout.shr_limit:.2f}"
        f"（最大间距 {layout.max_spacing:.2f} m / 净高 {layout.height:.2f} m）；"
        f"两灯之间会出现暗带，需要加灯或换更宽的配光"
    )


def as_points(layout: GridLayout) -> Sequence[Tuple[float, float]]:
    return layout.positions
