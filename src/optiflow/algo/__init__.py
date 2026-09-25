"""算法层（P2）：先算后验——这里出方案，软件层（DIALux）只做校核。

- lumen.py      光通量法：房间指数 / 利用系数 / 灯具数量 / 平均照度
- layout.py     布灯排布：居中网格 + 距高比约束
- uniformity.py 逐点法：均匀度 U0 / U1（光通量法算不出来的那个指标）
- engine.py     串起来：TaskSpec → 布灯方案 + 预测指标 + 诚实性说明

分层理由（计划文档 Q7）：算法层给方案，软件层做验证。算法层毫秒级出结果，
让用户在等 DIALux 之前先看到一版方案；但算法层的数字是【预测值】，
必须能被软件层校核，也必须把假设写清楚。
"""

from ..geometry import point_in_polygon, polygon_area
from .engine import ALGO_LIMITS, LayoutPlan, plan_layout
from .layout import DEFAULT_SHR_LIMIT, GridLayout, plan_grid, spacing_violation
from .lumen import (
    DEFAULT_MAINTENANCE_FACTOR,
    DEFAULT_WORK_PLANE,
    UtilizationOutOfRange,
    average_illuminance,
    fixture_count,
    room_index,
    uf_lookup,
)
from .uniformity import (
    Lambertian,
    PhotoFixture,
    UniformityReport,
    evaluate,
    point_illuminance,
    sampling_grid,
    uniformity_report,
)

__all__ = [
    "ALGO_LIMITS",
    "DEFAULT_MAINTENANCE_FACTOR",
    "DEFAULT_SHR_LIMIT",
    "DEFAULT_WORK_PLANE",
    "GridLayout",
    "Lambertian",
    "LayoutPlan",
    "PhotoFixture",
    "UniformityReport",
    "UtilizationOutOfRange",
    "average_illuminance",
    "evaluate",
    "fixture_count",
    "plan_grid",
    "plan_layout",
    "point_in_polygon",
    "polygon_area",
    "point_illuminance",
    "room_index",
    "sampling_grid",
    "spacing_violation",
    "uf_lookup",
    "uniformity_report",
]
