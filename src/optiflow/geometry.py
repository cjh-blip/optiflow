"""共享几何原语。

为什么单独立一个模块：射线法（判断点是否在多边形内）同时被两处需要——

- `algo/uniformity.py`：布灯采样网格要剔除房间外的点；
- `adapters/dialux/adapter.py`：把灯具归到包含它的房间里。

以前适配器是从搬运件 `src.planner.core` 里 import 的。那条路只在
「仓库根恰好在 sys.path 上」时成立（测试里靠 conftest 兜住了），
一旦从别处启动就 `ModuleNotFoundError: No module named 'src'`——
真实路径上炸过一次（scripts/shell_journey.py），所以提成平台自己的原语。

口径（与搬运件保持一致，不许悄悄改）：在边上算【内】。
改这个口径会让边界灯具有时算进房间有时不算，比看上去影响大。
"""
from __future__ import annotations

from typing import Sequence, Tuple

Point2 = Tuple[float, float]


def point_in_polygon(px: float, py: float, polygon: Sequence[Point2]) -> bool:
    """射线法：点是否在多边形内（在边上返回 True）。"""
    n = len(polygon)
    if n < 3:
        return False
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    if px < min(xs) or px > max(xs) or py < min(ys) or py > max(ys):
        return False
    inside = False
    for i in range(n):
        x1, y1 = polygon[i][0], polygon[i][1]
        x2, y2 = polygon[(i + 1) % n][0], polygon[(i + 1) % n][1]
        # 落在线段上（含端点）算内
        if abs((x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)) < 1e-12 \
                and min(x1, x2) - 1e-12 <= px <= max(x1, x2) + 1e-12 \
                and min(y1, y2) - 1e-12 <= py <= max(y1, y2) + 1e-12:
            return True
        if (y1 > py) != (y2 > py):
            crossing_x = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < crossing_x:
                inside = not inside
    return inside


def polygon_area(polygon: Sequence[Point2]) -> float:
    """鞋带公式算多边形面积（首尾不必闭合）。"""
    if len(polygon) < 3:
        return 0.0
    ring = list(polygon)
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


__all__ = ["Point2", "point_in_polygon", "polygon_area"]
