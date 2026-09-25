"""光通量法（利用系数法）—— P2 算法层的核心公式。

回答一个问题：让一个房间达到目标平均照度，需要几盏灯？

    E_avg = N · Φ · UF · MF / A
    → N = E_target · A / (Φ · UF · MF)

三个输入各有讲究：

- 房间指数 K（CIE）：K = (L·W) / (Hm·(L+W))，Hm = 灯具安装高度 − 工作面高度。
  K 越大表示房间相对越矮胖（光在里面反射得越多，利用系数越高）。
- 利用系数 UF：由 K 和顶棚/墙面反射率查表。见 DEFAULT_UF_TABLE 的诚实说明。
- 维护系数 MF：LED 灯具常规取 0.80（光衰 + 积尘）。

诚实边界：光通量法只给【平均照度】，它结构上给不出均匀度——均匀度必须靠逐点法
（见 uniformity.py）。拿平均照度冒充均匀度是照明计算里最常见的假承诺。
"""
from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Tuple

#: 维护系数默认值（LED 灯具常规取值：光衰 + 积尘）
DEFAULT_MAINTENANCE_FACTOR = 0.80

#: 工作面默认高度（米）——办公桌/桌面
DEFAULT_WORK_PLANE = 0.75

#: 利用系数表的房间指数采样点（CIE 常用档位）
ROOM_INDEX_STOPS = (0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)

_UF_COMMON = (0.37, 0.45, 0.52, 0.58, 0.63, 0.69, 0.74, 0.77, 0.80, 0.82)

#: 内置利用系数表。键是 (顶棚反射率, 墙面反射率)，值是 K → UF。
#:
#: 【这不是厂商数据，也不是标准表】——它是「典型下射式 LED 面板」的工程默认值，
#: 只用来让平台在没有厂商数据时也能出第一版方案。真实项目的做法是拿厂商 UF/IES 数据
#: 通过 uf_lookup(..., table=...) 传进来覆盖。误差量级：UF 每差 0.05，灯具数约差 6~10%，
#: 所以任何报出去的方案都必须用厂商数据复核，或直接提交 DIALux 校核。
DEFAULT_UF_TABLE: Dict[Tuple[float, float], Dict[float, float]] = {
    (0.80, 0.70): dict(zip(ROOM_INDEX_STOPS, (0.43, 0.52, 0.59, 0.66, 0.71, 0.76, 0.80, 0.83, 0.86, 0.88))),
    (0.70, 0.50): dict(zip(ROOM_INDEX_STOPS, _UF_COMMON)),
    (0.50, 0.30): dict(zip(ROOM_INDEX_STOPS, (0.30, 0.37, 0.43, 0.48, 0.53, 0.59, 0.64, 0.67, 0.71, 0.73))),
    (0.30, 0.10): dict(zip(ROOM_INDEX_STOPS, (0.22, 0.28, 0.33, 0.37, 0.41, 0.46, 0.51, 0.54, 0.58, 0.60))),
}

UF_TABLE_RANGE = "顶棚反射率 0.30~0.80 / 墙面反射率 0.10~0.70 / 房间指数 0.6~5.0"


class UtilizationOutOfRange(ValueError):
    """利用系数表的插值范围之外，且调用方没有提供外推策略。"""


def room_index(length: float, width: float, mount_height: float,
               work_plane: float = DEFAULT_WORK_PLANE) -> float:
    """CIE 房间指数 K = (L·W) / (Hm·(L+W))。

    mount_height 是灯具距【地面】的安装高度；Hm 是灯具到工作面的距离。
    """
    if length <= 0 or width <= 0:
        raise ValueError(f"房间尺寸必须为正：length={length}, width={width}")
    hm = mount_height - work_plane
    if hm <= 0:
        raise ValueError(
            f"灯具安装高度 {mount_height} 必须高于工作面 {work_plane}（否则没有下射空间）"
        )
    return (length * width) / (hm * (length + width))


def _interp_k(table: Mapping[float, float], k: float) -> float:
    """在 K 上线性插值；超出范围按端点夹住。"""
    stops = sorted(table)
    if k <= stops[0]:
        return table[stops[0]]
    if k >= stops[-1]:
        return table[stops[-1]]
    for lo, hi in zip(stops, stops[1:]):
        if lo <= k <= hi:
            t = (k - lo) / (hi - lo)
            return table[lo] + t * (table[hi] - table[lo])
    return table[stops[-1]]  # pragma: no cover - 上面的分支已覆盖全部区间


def uf_lookup(room_index_value: float,
              ceiling_reflectance: float,
              wall_reflectance: float,
              table: Optional[Mapping[Tuple[float, float], Mapping[float, float]]] = None) -> float:
    """按房间指数 + 顶棚/墙面反射率插值取利用系数。

    插值方式：【最近两个锚点的距离反比加权】。

    为什么不用双线性：内置表的反射率组合是沿经验对角线分布的稀疏锚点，不是完整网格，
    强行双线性会因为缺格子直接报错（实测：查 (0.6, 0.4) 需要 (0.5, 0.5) 这一格，
    而它不存在）。

    为什么不用「全部锚点反距离加权」：远处的高反射率锚点会把结果拽偏，破坏单调性
    ——实测把 (0.6, 0.4) 算到 0.703，比它两个最近邻（0.59 / 0.69）都高。
    只取最近两个锚点，既稳定又保证结果落在两者之间。

    超出表范围会抛 UtilizationOutOfRange——宁可报错，也不要悄悄外推一个
    看起来很精确但其实没依据的数。
    """
    tbl = DEFAULT_UF_TABLE if table is None else table
    if not tbl:
        raise ValueError("利用系数表为空")
    ceilings = [c for c, _w in tbl]
    walls = [w for _c, w in tbl]
    if not (min(ceilings) - 1e-9 <= ceiling_reflectance <= max(ceilings) + 1e-9):
        raise UtilizationOutOfRange(
            f"顶棚反射率 {ceiling_reflectance} 超出利用系数表范围"
            f"（{min(ceilings)}~{max(ceilings)}）；表覆盖范围：{UF_TABLE_RANGE}。"
            "请提供 table= 覆盖，或改用厂商数据。"
        )
    if not (min(walls) - 1e-9 <= wall_reflectance <= max(walls) + 1e-9):
        raise UtilizationOutOfRange(
            f"墙面反射率 {wall_reflectance} 超出利用系数表范围"
            f"（{min(walls)}~{max(walls)}）；表覆盖范围：{UF_TABLE_RANGE}。"
            "请提供 table= 覆盖，或改用厂商数据。"
        )

    anchors = []
    for (c, w), curve in tbl.items():
        d2 = (c - ceiling_reflectance) ** 2 + (w - wall_reflectance) ** 2
        anchors.append((d2, _interp_k(curve, room_index_value)))
    anchors.sort(key=lambda item: item[0])
    if anchors[0][0] < 1e-18:  # 精确命中锚点
        return anchors[0][1]
    if len(anchors) == 1:
        return anchors[0][1]
    (d2_a, uf_a), (d2_b, uf_b) = anchors[0], anchors[1]
    w_a, w_b = 1.0 / d2_a, 1.0 / d2_b
    return (uf_a * w_a + uf_b * w_b) / (w_a + w_b)


def fixture_count(target_lux: float, area_m2: float, flux_per_fixture: float,
                  uf: float, mf: float = DEFAULT_MAINTENANCE_FACTOR) -> int:
    """N = E_target · A / (Φ · UF · MF)，向上取整（少一盏就一定不达标）。"""
    if target_lux <= 0:
        raise ValueError(f"目标照度必须为正：{target_lux}")
    if area_m2 <= 0:
        raise ValueError(f"面积必须为正：{area_m2}")
    if flux_per_fixture <= 0:
        raise ValueError(f"单灯光通量必须为正：{flux_per_fixture}")
    if not 0 < uf <= 1:
        raise ValueError(f"利用系数应在 (0, 1]：{uf}")
    if not 0 < mf <= 1:
        raise ValueError(f"维护系数应在 (0, 1]：{mf}")
    return math.ceil(target_lux * area_m2 / (flux_per_fixture * uf * mf))


def average_illuminance(count: int, flux_per_fixture: float, uf: float,
                        area_m2: float, mf: float = DEFAULT_MAINTENANCE_FACTOR) -> float:
    """E_avg = N · Φ · UF · MF / A（工作面上的平均照度，单位 lx）。"""
    if area_m2 <= 0:
        raise ValueError(f"面积必须为正：{area_m2}")
    return count * flux_per_fixture * uf * mf / area_m2
