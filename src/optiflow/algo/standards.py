"""照明设计判据表（第一批五类）。

这个文件将来要长成「标准库」，所以两条纪律写死在这里：

1. **每格都带出处**（标准号 + 表号/条号）。给不出出处的格子留 None，不填空话。
2. **不许拿相邻类别顶替**。会议室就是会议室，走廊就是走廊；没收录的类别返回 None，
   由调用方标成「未指定/未收录」，而不是拿最像的那个凑一个结论。

数据来源（全部工作区内核对过）：
- 照度/UGR/U0/Ra：GB/T 50034-2024 表 5.3.2（办公建筑）、表 5.5.1（通用房间）。
  索引文件：02_礼堂推广/建筑照明设计标准_GBT50034-2024_知识库.md
- LPD 现行值：GB 55015-2021 表 3.3.7-3（办公）、表 3.3.7-12（通用房间）。
  核对记录：02_礼堂推广/_工作文件/礼堂方案修正记录_20260811.md
  （原表 3.3.7 系列逐表 OCR 复核）。
- LPD 目标值：GB/T 50034-2024 表 6.3.5（办公）、表 6.3.15（通用房间）。
- 照度分级：GB/T 50034-2024 第 4.1.1 条。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------- 照度分级

#: GB/T 50034-2024 第 4.1.1 条：照度标准值应按这些档分级。
#: 用途：目标照度落在两档之间时，提示「这不是标准档」——400 lx 就不在这串里。
ILLUMINANCE_LEVELS: Tuple[float, ...] = (
    0.5, 1, 2, 3, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500, 750,
    1000, 1500, 2000, 3000, 5000,
)
ILLUMINANCE_LEVELS_REF = "GB/T 50034-2024 第 4.1.1 条"


def is_standard_lux(value: float, tol: float = 1e-6) -> bool:
    return any(abs(value - lv) <= tol for lv in ILLUMINANCE_LEVELS)


def nearest_levels(value: float) -> Tuple[Optional[float], Optional[float]]:
    """比给定值小的最大档与大的最小档。超出范围的一侧返回 None。"""
    lower = [lv for lv in ILLUMINANCE_LEVELS if lv <= value + 1e-9]
    upper = [lv for lv in ILLUMINANCE_LEVELS if lv >= value - 1e-9]
    return (max(lower) if lower else None), (min(upper) if upper else None)


# ---------------------------------------------------------------- 判据表

@dataclass(frozen=True)
class RoomStandard:
    """一个房间类型的全部判据。None ＝ 本工作区未收录，不是 0。"""

    key: str
    label: str
    illuminance_lux: Optional[float] = None
    ugr_limit: Optional[float] = None
    u0_limit: Optional[float] = None
    ra_limit: Optional[float] = None
    lpd_current: Optional[float] = None
    lpd_target: Optional[float] = None
    #: 每格出处。键是字段名，值是「标准号 表号/条号」。
    sources: Dict[str, str] = field(default_factory=dict)


_OFFICE_STD = "GB/T 50034-2024 表 5.3.2"
_GENERIC_STD = "GB/T 50034-2024 表 5.5.1"
_LPD_CUR_OFFICE = "GB 55015-2021 表 3.3.7-3"
_LPD_CUR_GENERIC = "GB 55015-2021 表 3.3.7-12"
_LPD_TGT_OFFICE = "GB/T 50034-2024 表 6.3.5"
_LPD_TGT_GENERIC = "GB/T 50034-2024 表 6.3.15"
#: Ra 有双重身份：GB/T 50034 给推荐值，GB 55016《建筑环境通用规范》是**强制**要求。
#: 两本都写进去，报告里才知道这一格不是可选项。
_RA_OFFICE = "GB/T 50034-2024 表 5.3.2；GB 55016-2021（建筑环境通用规范，显色指数为强制要求）"
_RA_GENERIC = "GB/T 50034-2024 表 5.5.1；GB 55016-2021（建筑环境通用规范，显色指数为强制要求）"

ROOM_STANDARDS: Dict[str, RoomStandard] = {
    "office": RoomStandard(
        key="office", label="普通办公室",
        illuminance_lux=300, ugr_limit=19, u0_limit=0.60, ra_limit=80,
        lpd_current=8.0, lpd_target=6.5,
        sources={
            "illuminance": _OFFICE_STD, "ugr": _OFFICE_STD, "u0": _OFFICE_STD,
            "ra": _RA_OFFICE, "lpd_current": _LPD_CUR_OFFICE,
            "lpd_target": _LPD_TGT_OFFICE,
        },
    ),
    "office_high": RoomStandard(
        key="office_high", label="高档办公室/设计室",
        illuminance_lux=500, ugr_limit=19, u0_limit=0.60, ra_limit=80,
        lpd_current=13.5, lpd_target=9.5,
        sources={
            "illuminance": _OFFICE_STD, "ugr": _OFFICE_STD, "u0": _OFFICE_STD,
            "ra": _RA_OFFICE, "lpd_current": _LPD_CUR_OFFICE,
            "lpd_target": _LPD_TGT_OFFICE,
        },
    ),
    "meeting_room": RoomStandard(
        key="meeting_room", label="会议室",
        illuminance_lux=300, ugr_limit=19, u0_limit=0.60, ra_limit=80,
        lpd_current=8.0, lpd_target=6.5,
        sources={
            "illuminance": _OFFICE_STD, "ugr": _OFFICE_STD, "u0": _OFFICE_STD,
            "ra": _RA_OFFICE, "lpd_current": _LPD_CUR_OFFICE,
            "lpd_target": _LPD_TGT_OFFICE,
        },
    ),
    "corridor": RoomStandard(
        key="corridor", label="走廊（普通）",
        illuminance_lux=50, ugr_limit=25, u0_limit=0.40, ra_limit=60,
        lpd_current=2.0, lpd_target=1.5,
        sources={
            "illuminance": _GENERIC_STD, "ugr": _GENERIC_STD, "u0": _GENERIC_STD,
            "ra": _RA_GENERIC, "lpd_current": _LPD_CUR_GENERIC,
            "lpd_target": _LPD_TGT_GENERIC,
        },
    ),
    "service_hall": RoomStandard(
        key="service_hall", label="服务大厅",
        illuminance_lux=300, ugr_limit=22, u0_limit=0.40, ra_limit=80,
        lpd_current=10.0, lpd_target=8.0,
        sources={
            "illuminance": _OFFICE_STD, "ugr": _OFFICE_STD, "u0": _OFFICE_STD,
            "ra": _RA_OFFICE, "lpd_current": _LPD_CUR_OFFICE,
            "lpd_target": _LPD_TGT_OFFICE,
        },
    ),
}

#: 中文别名 → 判据键。IR 里写中文或写键都能查到，但不做模糊匹配。
ROOM_TYPE_ALIASES: Dict[str, str] = {
    "办公室": "office", "普通办公室": "office",
    "高档办公室": "office_high", "设计室": "office_high",
    "会议室": "meeting_room", "视频会议室": "meeting_room",
    "走廊": "corridor", "走道": "corridor",
    "服务大厅": "service_hall", "营业厅": "service_hall",
}


def lookup(room_type: Optional[str]) -> Optional[RoomStandard]:
    """按房间类型查判据。查不到返回 None——**不许拿别的类别顶替**。"""
    if not room_type:
        return None
    key = str(room_type).strip()
    if key in ROOM_STANDARDS:
        return ROOM_STANDARDS[key]
    alias = ROOM_TYPE_ALIASES.get(key)
    if alias:
        return ROOM_STANDARDS.get(alias)
    return None


__all__ = [
    "ILLUMINANCE_LEVELS",
    "ILLUMINANCE_LEVELS_REF",
    "ROOM_STANDARDS",
    "ROOM_TYPE_ALIASES",
    "RoomStandard",
    "is_standard_lux",
    "lookup",
    "nearest_levels",
]
