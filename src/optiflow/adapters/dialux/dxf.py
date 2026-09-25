"""DWG/DXF 解析器：抽取房间多边形与灯具块，输出 IR dict。

设计原则 (见 spec/ir-schema.md)：
- 不把整个 DXF 喂给模型，只抽结构化实体。
- 单位统一换算为米；坐标做仿射对齐 (由 caller 提供 transform)。
- 图层/实体类型过滤规则可配置。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ezdxf

logger = logging.getLogger(__name__)

Point = Tuple[float, ...]

# 闭合容差：米制（convert_to_meters 后），1mm
CLOSE_TOLERANCE_METERS = 1e-3

#: 退化噪声环阈值（m²）：小于此面积的环直接丢弃
MIN_AREA_M2 = 0.05
#: 房间/家具分界（m²）：面积 ≤ 此值 → 家具，> 此值 → 房间
FURNITURE_AREA_M2 = 20.0
#: 「墙线环」（关掉 T 形分割拼出的房间轮廓）在 Room.layer 上的标记
WALL_RING_LAYER = "WALL-RING"

_UNIT_MAP = {"mm": 0.001, "cm": 0.01, "m": 1.0, "inch": 0.0254}


def convert_to_meters(value: float, units: str) -> float:
    if units not in _UNIT_MAP:
        raise ValueError(f"未知单位: {units}")
    return value * _UNIT_MAP[units]


def _meters_to_units(value_m: float, units: str) -> float:
    if units not in _UNIT_MAP:
        raise ValueError(f"未知单位: {units}")
    return value_m / _UNIT_MAP[units]


def header_insunits_to_units(code: Optional[int]) -> Optional[str]:
    """1 → inch, 4 → mm, 5 → cm, 6 → m；其他 → None。"""
    if code is None:
        return None
    mapping = {1: "inch", 4: "mm", 5: "cm", 6: "m"}
    return mapping.get(int(code))


@dataclass
class ParseConfig:
    """解析规则。"""

    # 房间轮廓来源：这些图层上的 LWPOLYLINE/POLYLINE 视为房间（分支 A）
    room_layers: List[str] = field(default_factory=lambda: ["ROOM", "SPACE", "房间", "0"])
    # 房间分解轮廓（LINE/ARC）所在图层（分支 B）
    room_edge_layers: List[str] = field(default_factory=lambda: ["0"])
    # 灯具块名 (INSERT 的 block 名) 视为灯具
    luminaire_blocks: List[str] = field(default_factory=lambda: ["LED", "LAMP", "灯具"])
    # 若指定，则按 [r_min, r_max]（绘图单位）匹配 CIRCLE 为灯具
    luminaire_circle_radius_range: Optional[Tuple[float, float]] = None
    # 若指定，则按 ((w_min, h_min), (w_max, h_max)) 绘图单位 mm 匹配闭合矩形环为灯具
    # 典型 LED 面板灯：155×30 mm（绘图单位）→ 默认 ((120, 15), (200, 50))
    luminaire_rect_wh_range_mm: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
    # 单位：dxf 中的单位，"mm" / "cm" / "m" / "inch"
    units: str = "mm"
    # 若 DXF header $INSUNITS 存在，则优先使用
    units_from_header: bool = True
    # LINE+ARC 拼接的端点吸附容差（绘图单位，一般 0.5mm）
    join_tolerance_mm: float = 0.5
    # 环收尾自动闭合容差（绘图单位）；超 join_tolerance 但 < auto_close 则补首点并告警
    auto_close_tolerance_mm: float = 1.0
    # 额外跑一轮「墙线环」搜索（关掉 T 形分割）：贴墙家具的端点不再把墙线切断，
    # 于是环搜索拼出的是真实墙围轮廓，用来盖掉「柜子轮廓混进房间环」的锯齿
    # （见 extract_rooms 的墙线环 pass 与 spec 的 MVP2 遗留项）。
    wall_ring_pass: bool = True
    # 房间环后处理：剔除面积 < notch_max_area_m2 的凹槽/凸台 + 合并共线断点。
    # 兜底用（墙线环 pass 拼不出候选时仍能去锯齿）；家具环不做任何平滑。
    smooth_room_rings: bool = True
    # 凹槽/凸台面积上限（m²），与 validator 规则 2 的下限 0.5 m² 一致
    notch_max_area_m2: float = 0.5
    # 默认净高，若指定则 IR space.ceil_h 自动写入
    default_ceil_h: Optional[float] = 2.8
    # 会议桌默认桌面高度（米）；逐件覆盖能力留给后续家具选型阶段
    furniture_height_m: float = 0.75
    # 坐标变换：先缩放(单位→米)再平移该偏移（米制 world 原点对齐）
    origin: Point = (0.0, 0.0)

    # ---------- 序列化 ----------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # tuple → list（JSON 友好）
        if d.get("origin") is not None:
            d["origin"] = list(d["origin"])
        if d.get("luminaire_circle_radius_range") is not None:
            d["luminaire_circle_radius_range"] = list(d["luminaire_circle_radius_range"])
        if d.get("luminaire_rect_wh_range_mm") is not None:
            r = d["luminaire_rect_wh_range_mm"]
            d["luminaire_rect_wh_range_mm"] = [list(r[0]), list(r[1])]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ParseConfig":
        d = dict(d)
        if "luminaire_circle_radius_range" in d and d["luminaire_circle_radius_range"] is not None:
            r = d["luminaire_circle_radius_range"]
            if isinstance(r, (list, tuple)) and len(r) == 2:
                d["luminaire_circle_radius_range"] = (float(r[0]), float(r[1]))
        if "luminaire_rect_wh_range_mm" in d and d["luminaire_rect_wh_range_mm"] is not None:
            r = d["luminaire_rect_wh_range_mm"]
            if (isinstance(r, (list, tuple)) and len(r) == 2
                    and isinstance(r[0], (list, tuple)) and len(r[0]) == 2
                    and isinstance(r[1], (list, tuple)) and len(r[1]) == 2):
                d["luminaire_rect_wh_range_mm"] = (
                    (float(r[0][0]), float(r[0][1])),
                    (float(r[1][0]), float(r[1][1])),
                )
        if "origin" in d and d["origin"] is not None:
            o = d["origin"]
            if isinstance(o, (list, tuple)) and len(o) >= 2:
                d["origin"] = (float(o[0]), float(o[1]))
        return cls(**d)

    def to_json(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path) -> "ParseConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class Room:
    id: str
    name: str
    polygon: List[Point]
    layer: str

    def is_closed(self, tol: float = CLOSE_TOLERANCE_METERS) -> bool:
        if len(self.polygon) < 3:
            return False
        a = self.polygon[0][:2]
        b = self.polygon[-1][:2]
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 < tol


@dataclass
class Luminaire:
    symbol: str
    x: float
    y: float
    z: float = 0.0
    kind: str = "unknown"
    attrs: dict = field(default_factory=dict)


# 长宽比阈值：>= 该值判为线性灯具（灯带/线槽灯），否则判为面板灯。
# 3.0 的依据：实测图纸的线性灯具是 1555×300 mm（5.18:1），常见面板灯 600×600（1:1）
# 与 1200×300（4:1）—— 4:1 的 1200×300 在 DIALux 目录里也算线性灯具，故阈值取 3。
LINEAR_ASPECT_MIN = 3.0


def classify_rect_luminaire(w_mm: float, h_mm: float) -> str:
    """按长宽比把矩形灯具分成 linear（线性灯具/灯带）或 area（面板灯）。

    单位无关，只看比值；任一边 <= 0 时返回 "unknown"。
    """
    if w_mm <= 0 or h_mm <= 0:
        return "unknown"
    long_side, short_side = max(w_mm, h_mm), min(w_mm, h_mm)
    return "linear" if long_side / short_side >= LINEAR_ASPECT_MIN else "area"


def _entity_layer(entity) -> str:
    return (entity.dxf.layer or "").upper()


def _doc_units(doc, cfg: ParseConfig) -> str:
    """根据 header $INSUNITS 与 cfg.units 决定最终 DXF 绘图单位（"mm"/"cm"/"m"/"inch"）。

    注意：真实 DXF 的 $INSUNITS 不一定代表实际绘图单位。若用户通过 --config 指定 units，
    我们强制信任 cfg.units（cfg.units_from_header=False 即完全由 cfg 决定）。
    当 cfg.units_from_header=True 但实际 bbox 与「典型房间尺寸量级」不符时，
    本函数给出启发式告警，让用户通过 --config 指定单位。
    """
    if cfg.units_from_header:
        raw = None
        try:
            raw = doc.header.get("$INSUNITS")
        except Exception:
            raw = None
        mapped = header_insunits_to_units(raw)
        if mapped is not None:
            return mapped
        logger.warning(
            "header $INSUNITS missing or unknown (%s); using cfg.units=%s (建议用 --config 指定)",
            raw, cfg.units,
        )
    return cfg.units


def extract_rooms(doc: ezdxf.document.Drawing, cfg: ParseConfig) -> List[Room]:
    """抽取房间多边形：分支 A（LWPOLYLINE/POLYLINE）+ 分支 B（LINE+ARC 首尾链拼接）
    + 分支 B2（墙线环 pass，仅当 cfg.wall_ring_pass），合并去重。"""
    # 懒加载：避免 import 副作用
    from ._chain import (  # type: ignore  # _chain 在 T2b 提供
        extract_rings_from_line_arc, dedup_rooms,
    )

    rooms: List[Room] = []
    room_layers = {layer.upper() for layer in cfg.room_layers}
    units = _doc_units(doc, cfg)
    def to_m(v):
        return convert_to_meters(v, units)

    # 分支 A：LWPOLYLINE / POLYLINE
    for entity in doc.modelspace():
        dxf_type = entity.dxftype()
        if dxf_type not in ("LWPOLYLINE", "POLYLINE"):
            continue
        if _entity_layer(entity) not in room_layers:
            continue
        try:
            points = [tuple(p)[:2] for p in entity.get_points("xy")]
        except Exception as e:  # noqa
            logger.debug("取多段线顶点失败 layer=%s handle=%s: %s",
                         _entity_layer(entity), entity.dxf.handle, e)
            continue
        if len(points) < 3:
            continue
        polygon = [
            [float(to_m(x) - cfg.origin[0]), float(to_m(y) - cfg.origin[1])]
            for x, y in points
        ]
        rid = entity.dxf.handle or f"LW{id(entity):x}"
        rooms.append(Room(id=rid, name=rid, polygon=polygon, layer=_entity_layer(entity)))

    # 分支 B：LINE + ARC → 首尾链拼接环（提取后 convert_to_meters 同样处理）
    edge_layers = {layer.upper() for layer in cfg.room_edge_layers}

    def _to_m_poly(poly_units):
        return [
            [float(to_m(x) - cfg.origin[0]), float(to_m(y) - cfg.origin[1])]
            for (x, y) in poly_units
        ]

    polygons_b = extract_rings_from_line_arc(
        doc=doc,
        edge_layers=edge_layers,
        join_tolerance_mm=cfg.join_tolerance_mm,
        auto_close_tolerance_mm=cfg.auto_close_tolerance_mm,
    )
    for idx, (poly_mm, handles_note) in enumerate(polygons_b):
        rid = f"LINEARC_{idx:03d}_{handles_note or ''}"
        rooms.append(Room(id=rid, name=rid, polygon=_to_m_poly(poly_mm), layer="LINE+ARC"))

    # 分支 B2（墙线环 pass）：关掉 T 形分割再拼一轮。
    # 贴墙家具的端点不再把墙线切断 → 环搜索无法在共享端点处拐进柜子轮廓，
    # 拼出的是真实墙围轮廓（房间候选）。
    # 只收「房间量级」的环（面积 > FURNITURE_AREA_M2）：家具环由分支 B 负责，
    # 这一轮的小环是家具的粗合并版本（相邻两个柜子会连成一个大框），收进来只会污染家具表。
    if cfg.wall_ring_pass:
        from ._chain import polygon_area as _poly_area  # 懒加载，与上面同一模块

        polygons_w = extract_rings_from_line_arc(
            doc=doc,
            edge_layers=edge_layers,
            join_tolerance_mm=cfg.join_tolerance_mm,
            auto_close_tolerance_mm=cfg.auto_close_tolerance_mm,
            split_at_t_junctions=False,
        )
        n_wall_kept = 0
        for idx, (poly_mm, handles_note) in enumerate(polygons_w):
            poly_m = _to_m_poly(poly_mm)
            if _poly_area(poly_m) <= FURNITURE_AREA_M2:
                continue
            rid = f"WALLRING_{idx:03d}_{handles_note or ''}"
            rooms.append(Room(id=rid, name=rid, polygon=poly_m, layer=WALL_RING_LAYER))
            n_wall_kept += 1
        logger.info("墙线环 pass：分支 B %d 环；分支 B2 %d 环 → 收房间候选 %d 个",
                    len(polygons_b), len(polygons_w), n_wall_kept)

    # 去重 + 可选 auto_close
    rooms = dedup_rooms(rooms, rel_tol_centroid=1e-3, rel_tol_area=5e-2,
                        prefer_layers={WALL_RING_LAYER})
    auto_close_tol_m = (cfg.auto_close_tolerance_mm or 1.0) * 1e-3
    for r in rooms:
        if not r.is_closed() and len(r.polygon) >= 3:
            a = r.polygon[0][:2]
            b = r.polygon[-1][:2]
            gap = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            if gap <= auto_close_tol_m:
                logger.warning("房间 %s 自动补闭合（gap=%.2f mm）", r.id, gap * 1000.0)
                r.polygon.append([r.polygon[0][0], r.polygon[0][1]])
    return rooms


def extract_luminaires(doc: ezdxf.document.Drawing, cfg: ParseConfig) -> List[Luminaire]:
    """从 INSERT + CIRCLE 抽取灯具（米制）。"""
    lumis: List[Luminaire] = []
    blocks = {b.upper() for b in cfg.luminaire_blocks}
    units = _doc_units(doc, cfg)
    def to_m(v):
        return convert_to_meters(v, units)

    for insert in doc.modelspace().query("INSERT"):
        block_name = (insert.dxf.name or "").upper()
        if not any(block_name.startswith(b) for b in blocks):
            continue
        try:
            x, y, z = insert.dxf.insert
        except Exception:
            p = insert.dxf.insert
            x, y, z = p[0], p[1], 0.0
        lumis.append(Luminaire(
            symbol=insert.dxf.name or block_name,
            x=to_m(x) - cfg.origin[0], y=to_m(y) - cfg.origin[1], z=to_m(z),
            # INSERT 是命名块引用，块名不携带几何 → 无法判定 point/linear/area。
            # 显式写 unknown 表示「解析器确实判不了」，不是漏填；按块名建映射表是 MVP3 选型的事。
            kind="unknown",
            attrs=dict(getattr(insert, "attribs", None) or {}),
        ))

    if cfg.luminaire_circle_radius_range is not None:
        r_min, r_max = cfg.luminaire_circle_radius_range
        seq = 0
        # 与 RECT 分支一致：过滤阈值按 DXF 原始单位比较（cfg 里配的就是原始单位），
        # 但对外暴露的尺寸必须换成真 mm，否则下游选型会拿到差 10 倍的直径。
        circle_doc_units = _doc_units(doc, cfg)
        for entity in doc.modelspace().query("CIRCLE"):
            try:
                r = float(entity.dxf.radius)
            except Exception:
                continue
            if not (r_min <= r <= r_max):
                continue
            center = entity.dxf.center
            symbol = f"CIRCLE-r{r:.1f}-{seq}"
            seq += 1
            R_mm = convert_to_meters(r, circle_doc_units) * 1000.0
            lumis.append(Luminaire(
                symbol=symbol,
                x=to_m(center.x) - cfg.origin[0], y=to_m(center.y) - cfg.origin[1],
                z=to_m(getattr(center, "z", 0.0) or 0.0),
                kind="point",
                attrs={
                    # radius_mm 是换算后的真毫米；radius_raw 保留 DXF 原始单位值，
                    # 供复核过滤阈值用（symbol 里的 r 数字也是原始单位，勿混用）。
                    "radius_mm": R_mm,
                    "radius_raw": r,
                    "raw_units": circle_doc_units,
                    "layer": entity.dxf.layer or "0",
                },
            ))

    # 分支 3：LINE 组成的闭合矩形环（尺寸 ∈ luminaire_rect_wh_range_mm）→ 灯具
    if cfg.luminaire_rect_wh_range_mm is not None:
        # 注意：与 luminaire_circle_radius_range 保持一致 —— 阈值按「DXF 原始绘图单位」比较。
        # 配置字段名带 _mm 只是语义暗示（真实图若以 cm 写进 $INSUNITS 或 units=cm，实际仍按原始数值比）。
        # 例如：灯具图 units=cm，RECT 环原始 W×H=155.5×30（绘图单位），配 [[120,15],[200,50]] 即命中。
        (w_min, h_min), (w_max, h_max) = cfg.luminaire_rect_wh_range_mm
        try:
            from optiflow.adapters.dialux._chain import extract_rings_from_line_arc
            lum_layers = set(layer.upper() for layer in cfg.room_edge_layers)
            lum_layers |= {"0", "LAMP", "LIGHT", "灯具"}
            rings = extract_rings_from_line_arc(
                doc, edge_layers=lum_layers,
                join_tolerance_mm=cfg.join_tolerance_mm,
                auto_close_tolerance_mm=cfg.auto_close_tolerance_mm,
            )
        except Exception as exc:  # noqa
            logger.warning("矩形灯具环抽取失败，跳过：%s", exc)
            rings = []
        rect_seq = 0
        for poly, handles_note in rings:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            W = xmax - xmin
            H = ymax - ymin
            if W <= 0 or H <= 0:
                continue
            # 允许 (W,H) 或 (H,W) 命中
            ok = (w_min <= W <= w_max and h_min <= H <= h_max) \
                 or (w_min <= H <= w_max and h_min <= W <= h_max)
            if not ok:
                continue
            # 面积差判定：bbox vs 实际多边形（允许 10%）
            bbox_area = W * H
            from optiflow.adapters.dialux._chain import polygon_area as _pa
            real_area = _pa([(p[0], p[1]) for p in poly])
            if bbox_area > 0 and abs(real_area - bbox_area) / bbox_area > 0.1:
                continue
            cx = (xmin + xmax) * 0.5
            cy = (ymin + ymax) * 0.5
            # symbol 展示用 mm：假定 W/H 是文档名义 mm（raw 值若为 cm，则此处会被转换展示为 mm）
            doc_units_for_display = _doc_units(doc, cfg)
            W_mm = convert_to_meters(W, doc_units_for_display) * 1000.0
            H_mm = convert_to_meters(H, doc_units_for_display) * 1000.0
            symbol = f"RECT-{W_mm:.1f}x{H_mm:.1f}-{rect_seq}"
            rect_seq += 1
            lumis.append(Luminaire(
                symbol=symbol,
                x=to_m(cx) - cfg.origin[0],
                y=to_m(cy) - cfg.origin[1],
                z=0.0,
                kind=classify_rect_luminaire(W_mm, H_mm),
                attrs={
                    "rect_w_mm": W_mm, "rect_h_mm": H_mm,
                    "ring_handles": handles_note,
                    "layer": "RECT-RING",
                },
            ))
        if rect_seq:
            logger.info(
                "LINE-矩形灯具环匹配：%d 个（DXF 原始单位：W∈[%.2f,%.2f] H∈[%.2f,%.2f]）",
                rect_seq, w_min, w_max, h_min, h_max,
            )

    # 按 (x, y) 1mm 精度去重（CAD 图层常重复绘制同位置灯具）
    DEDUP_TOL_MM = 1.0
    seen: Dict[Tuple[int, int], int] = {}
    dedup: List[Luminaire] = []
    dropped: List[str] = []
    tol = DEDUP_TOL_MM / 1000.0
    for lum in lumis:
        key = (round(lum.x / tol), round(lum.y / tol))
        if key in seen:
            # 逐个记 DEBUG，不记 WARNING。重合去重是这类图纸的常态（同一位置在不同
            # 图层被画了两遍），每个都刷一条 WARNING 会让正常的一次运行看起来像出了
            # 十几个错——2026-09-06 架构师就是被这堆 WARNING 误判成「28 个灯具错误」。
            logger.debug("灯具去重：跳过与 #%d 位置重合的 %s (Δ<%.0fmm)",
                         seen[key], lum.symbol, DEDUP_TOL_MM)
            dropped.append(lum.symbol)
            continue
        seen[key] = len(dedup)
        dedup.append(lum)
    if dropped:
        # 汇总成一条。这是正常清理，不是异常，所以是 INFO。
        head = "、".join(dropped[:5]) + ("…" if len(dropped) > 5 else "")
        logger.info("灯具去重：%d → %d，丢弃 %d 个坐标重合的重复图元（%s）",
                    len(lumis), len(dedup), len(dropped), head)
    return dedup


def parse_dxf(path: str, cfg: Optional[ParseConfig] = None) -> dict:
    """主入口：DXF → IR 字典（符合 ir-schema）。"""
    cfg = cfg or ParseConfig()
    doc = ezdxf.readfile(path)
    raw_rooms = extract_rooms(doc, cfg)
    lumis = extract_luminaires(doc, cfg)

    # ---------- 面积计算 + 分类 ----------
    from optiflow.adapters.dialux._chain import polygon_area, ring_preference_key

    def _bbox(poly):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return (min(xs), min(ys), max(xs), max(ys))

    def _bbox_overlap(ba, bb):
        """两 bbox 重叠面积（m²）。"""
        ix = max(0, min(ba[2], bb[2]) - max(ba[0], bb[0]))
        iy = max(0, min(ba[3], bb[3]) - max(ba[1], bb[1]))
        return ix * iy

    def _room_key(room):
        """房间候选择优键（越小越优先）：墙线环 → 锯齿边少 → 顶点多。

        与 _chain.dedup_rooms 的 prefer_layers 逻辑一致：同一片墙能拼出多个面积相近的环，
        沿墙直走的那个（锯齿少）才是真实房间轮廓，拐进柜子轮廓的是「柜子变墙」。
        """
        saw, neg_nv = ring_preference_key(room.polygon)
        return (0 if (room.layer or "").upper() == WALL_RING_LAYER else 1, saw, neg_nv)

    all_rings = []
    for r in raw_rooms:
        a = polygon_area(r.polygon)
        if a < MIN_AREA_M2:
            logger.warning("丢弃伪房间 %s：面积 %.2f m² < 阈值 %.2f m²", r.id, a, MIN_AREA_M2)
            continue
        all_rings.append((r, a, _bbox(r.polygon)))

    # ---------- 房间去重（大面积环：面积差<10% 且 bbox 重叠>80% → 保留 _room_key 最优）----------
    room_rings = [(r, a, b) for r, a, b in all_rings if a > FURNITURE_AREA_M2]
    furn_rings = [(r, a, b) for r, a, b in all_rings if a <= FURNITURE_AREA_M2]

    room_keep = [True] * len(room_rings)
    for i in range(len(room_rings)):
        if not room_keep[i]:
            continue
        ri, ai, bi = room_rings[i]
        for j in range(i + 1, len(room_rings)):
            if not room_keep[j]:
                continue
            rj, aj, bj = room_rings[j]
            overlap = _bbox_overlap(bi, bj)
            min_area = min(ai, aj)
            if min_area > 0 and overlap / min_area > 0.8 and abs(ai - aj) / max(ai, aj) < 0.10:
                # 保留 _room_key 最优者（墙线环 → 锯齿少 → 顶点多）
                if _room_key(ri) <= _room_key(rj):
                    room_keep[j] = False
                else:
                    room_keep[i] = False
                    break
    kept_rooms = [room_rings[i][0] for i, ok in enumerate(room_keep) if ok]

    def _point_in_polygon(point, polygon) -> bool:
        """射线法判断点是否在多边形内；边界点视为在内。"""
        px, py = point
        inside = False
        for idx, current in enumerate(polygon):
            previous = polygon[idx - 1]
            x1, y1 = float(previous[0]), float(previous[1])
            x2, y2 = float(current[0]), float(current[1])
            cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
            if (abs(cross) <= 1e-9
                    and min(x1, x2) - 1e-9 <= px <= max(x1, x2) + 1e-9
                    and min(y1, y2) - 1e-9 <= py <= max(y1, y2) + 1e-9):
                return True
            if (y1 > py) != (y2 > py):
                crossing_x = (x2 - x1) * (py - y1) / (y2 - y1) + x1
                if px < crossing_x:
                    inside = not inside
        return inside

    # ---------- 家具去重（bbox 完全相同 → 保留1个；面积差>50% 且 bbox 重叠>70% → 保留小的）----------
    furn_keep = [True] * len(furn_rings)
    for i in range(len(furn_rings)):
        if not furn_keep[i]:
            continue
        ri, ai, bi = furn_rings[i]
        for j in range(i + 1, len(furn_rings)):
            if not furn_keep[j]:
                continue
            rj, aj, bj = furn_rings[j]
            overlap = _bbox_overlap(bi, bj)
            min_area = min(ai, aj)
            # 完全相同（bbox 一致 + 面积差<5%）
            if (abs(bi[0]-bj[0]) < 0.05 and abs(bi[1]-bj[1]) < 0.05
                    and abs(bi[2]-bj[2]) < 0.05 and abs(bi[3]-bj[3]) < 0.05
                    and abs(ai - aj) / max(ai, aj, 1e-9) < 0.05):
                furn_keep[j] = False  # 重复，丢弃
                continue
            # 大包含小且面积差>50% → 丢弃大的（保留更精确的小环）
            if (min_area > 0 and overlap / min_area > 0.7
                    and abs(ai - aj) / max(ai, aj) > 0.5):
                if ai < aj:
                    furn_keep[j] = False
                else:
                    furn_keep[i] = False
                    break
    kept_furn = [furn_rings[i] for i, ok in enumerate(furn_keep) if ok]

    logger.info("房间过滤：%d → 房间%d + 家具%d（大面积去重%d，家具去重%d）",
                len(all_rings), len(kept_rooms), len(kept_furn),
                len(room_rings) - len(kept_rooms),
                len(furn_rings) - len(kept_furn))

    # ---------- 房间环平滑（只动房间，不动家具）----------
    # 墙线环 pass 已经能拼出无锯齿轮廓，但它依赖「墙被画成整条 LINE」；
    # 若图纸本身就把墙拆成多段（拼不出墙线环候选），这一步兜底把贴墙家具留下的
    # 小凹槽/凸台补平，并合并共线断点（DIALux 里的多余墙段）。
    notches_removed = 0
    if cfg.smooth_room_rings:
        from optiflow.adapters.dialux._chain import smooth_room_ring

        for r in kept_rooms:
            before_nv = len(r.polygon)
            before_area = polygon_area(r.polygon)
            new_poly, removed = smooth_room_ring(
                r.polygon, max_notch_area_m2=cfg.notch_max_area_m2)
            if len(new_poly) < 3:
                logger.warning("房间 %s 平滑后顶点不足（%d），保留原环", r.id, len(new_poly))
                continue
            if removed or len(new_poly) != before_nv:
                logger.info(
                    "房间 %s 平滑：顶点 %d → %d，面积 %.2f → %.2f m²（剔除凹槽/凸台 %d 个，合计 %.2f m²）",
                    r.id, before_nv, len(new_poly), before_area, polygon_area(new_poly),
                    len(removed), sum(n["area"] for n in removed))
            notches_removed += len(removed)
            r.polygon = new_poly

    # 残留锯齿告警：贴墙家具轮廓混进房间环的特征就是一串 <0.2m 的短边。
    # 墙线环 pass + 平滑都没清掉 → 图纸画法超出当前启发式，需要人看一眼。
    from optiflow.adapters.dialux._chain import count_short_edges, SAWTOOTH_EDGE_M

    rooms_with_saw = 0
    for r in kept_rooms:
        saw = count_short_edges(r.polygon, SAWTOOTH_EDGE_M)
        if saw:
            rooms_with_saw += 1
            logger.warning(
                "房间 %s 环上仍有 %d 条 <%.2fm 短边（面积 %.2f m²，%d 顶点）"
                "——可能仍混入家具轮廓，请核对 build/room_layout.svg",
                r.id, saw, SAWTOOTH_EDGE_M, polygon_area(r.polygon), len(r.polygon))

    # ---------- 组装 IR ----------
    spaces = []
    for r in kept_rooms:
        s = {
            "id": r.id,
            "name": r.name,
            "polygon": r.polygon,
            "luminaires": [],
            "furniture": [],
        }
        if cfg.default_ceil_h is not None:
            s["ceil_h"] = float(cfg.default_ceil_h)
        spaces.append(s)

    # 家具优先按几何中心归属房间；无法唯一归属时保留候选并降低置信度。
    room_by_id = {room.id: room for room in kept_rooms}
    for r, a, b in kept_furn:
        center = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
        candidates = [
            room for room in kept_rooms
            if _point_in_polygon(center, room.polygon)
        ]
        if candidates:
            owner = min(candidates, key=lambda room: polygon_area(room.polygon))
            room_id = owner.id
            confidence = 1.0 if len(candidates) == 1 else 0.75
        else:
            room_id = None
            confidence = 0.25
        furn = {
            "id": r.id,
            "room_id": room_id,
            "polygon": r.polygon,
            "area_m2": round(a, 2),
            "bbox": [round(b[0], 2), round(b[1], 2), round(b[2], 2), round(b[3], 2)],
            "kind": "unknown",
            "height_m": float(cfg.furniture_height_m),
            "rotation": 0.0,
            "confidence": confidence,
            "source_layer": r.layer,
            "source_entity": r.id,
        }
        if room_id in room_by_id:
            next(space for space in spaces if space["id"] == room_id)["furniture"].append(furn)
        # 也加为独立 space（便于 SVG 逐个显示）
        s = {
            "id": r.id,
            "name": f"家具_{r.id}",
            "polygon": r.polygon,
            "luminaires": [],
        }
        spaces.append(s)

    return {
        "schema_version": "0.1",
        "project": {"name": "", "source": {"dwg": path}, "units": "m"},
        "storeys": [{"level": 1, "spaces": spaces}],
        "_meta": {
            "rooms": len(kept_rooms),
            "furniture": len(kept_furn),
            "luminaires": len(lumis),
            "rooms_closed": sum(1 for r in kept_rooms if r.is_closed()),
            "dxf_units": cfg.units,
            # 房间环上被剔除的贴墙家具凹槽/凸台数（0 = 环本来就干净）
            "room_notches_removed": notches_removed,
            # 仍带 <0.2m 短边的房间数（>0 → 可能仍混入家具轮廓，见 WARNING 日志）
            "rooms_with_sawtooth": rooms_with_saw,
        },
    }


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/sample.dxf"
    ir = parse_dxf(target)
    print(json.dumps(ir, ensure_ascii=False, indent=2))
