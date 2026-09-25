"""IR → DIALux STF 生成器（MVP2：只建空房间几何，不放灯具/家具）。

S0 风险验证结论（不再重新研究）：DIALux evo 14.0 免费版可导入 STF 并建出房间，
参考文件 `build/test_room_1.stf`。本模块刻意保持与该文件同样的骨架——段名、键名、键顺序、
LF 换行、紧凑数字（`Point1=0 0 0` 而非 `0.000 0.000 0.000`）；变的只有取值：房间数量与顶点
来自 IR，`Progname` 改成本工具名，`[PROJECT] Description` 留空，`[ROOM] Description` 写 space.id。

用法：
    python -m optiflow.adapters.dialux.stf --validate build/room_layout.json build/mvp2_out.stf

边界（MVP2）：
- 每个 IR space → 一个 `[ROOM.Rn]` 段；`NrStruct/NrFurns` 恒为 0（家具留 MVP3）。
  `NrLums` 自 2026-09-04 起按 `space["luminaires"]` 实写，格式仍是占位实现（见 `_luminaire_lines`）
- 房间地板固定 Z=0：STF Point 是 `X Y Z`，IR 顶点若带第三维（schema 的 Point2 允许
  maxItems=3）会被**有意丢弃**，MVP2 不支持抬高楼层
- 家具伪 space（`name` 以「家具_」开头，由 src/parser/dxf.py 为 SVG 预览而生成）默认跳过，
  否则会在 DIALux 里建出 22 个假房间；需要时用 include_furniture=True（配合 default_ceil_h）

**已在 DIALux 里实测**（架构师 2026-09-02 验收，evo 14.0 免费版，KANBAN.md 已同步）：
1. 闭合形态：`close_polygon=True`（默认）实测导入成功，无报错。保留默认。
2. 绕向：默认保留 IR 原始绕向实测导入成功，无墙体朝向问题。保留默认（不自动翻转）。
   实测当时真实 IR 是 CW；parser 修掉「柜子变墙」后（t_0deee369）主房间换成墙线环候选，
   绕向变成 CCW —— 本模块仍不翻转。
3. 54 边非凸多边形：evo 14.0 免费版实测导入成功，无顶点数/复杂度限制。
4. 中文房间名：UTF-8 写出实测不乱码。
另：当时观察到的「房间轮廓混入家具边（柜子被识别成墙壁）」是 src/parser 的环搜索问题，
已在 t_0deee369 修复（墙线环 pass + 锯齿择优 + 房间环平滑），非本模块行为。

**CCW 复验回填（2026-09-05，computer use 驱动 evo 14.0 真机，非人眼）**：
导 `build/mvp2_fixed.stf`（NrPoints=17 / 16 唯一顶点 / CCW）→ evo 建工程成功 → 另存
`build/mvp2_fixed.evo`（69112 bytes，ZIP）。回环校验：
- 内嵌 `Project/STF/*.tmp` 与源 STF **逐字节相同**（591 bytes，sha256 前 16 位 5d8001dfaea2f7bb）
- 全部 11 个唯一坐标值（0.75/0.77/2.55/7.5/8.05/8.26/8.3/8.78/9.74/11.9 + 层高 2.8）
  以 **float64、单位米** 出现在 `Project/ScenegraphScene`，无毫米单位命中
  → **evo 内部按米存，本模块的米口径正确，无需换算**
- 出现次数与 STF 内复用次数成正比（8.78→84=2×42、0.75→126=3×42、其余唯一值各 42）
⚠ 未验证：墙体实际法向（CCW 在 evo 里朝内还是朝外）无法从坐标断言，需人眼看渲染；
   evo 自动套的 profile 是「5.1.4 标准（室外交通区域）」而非室内空间，对照度计算的影响未验。


闭合 × 绕向共 4 种组合，都可由 `--no-close` / `--ccw` 自由组合，不止预生成的那两个文件。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
from collections.abc import Sequence as ABCSequence
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

STF_FORMAT_VERSION = "1.0"
PROG_NAME = "dialux-compiler"
PROG_VERS = "0.1"

#: 家具伪 space 名称前缀，与 src/parser/dxf.py::parse_dxf 和 scripts/render_preview_svg.py 一致
FURNITURE_NAME_PREFIX = "家具_"
#: 坐标/高度四舍五入位数
COORD_NDIGITS = 3
#: 无 project.name 时的兜底工程名（write_stf 另有「退回输出文件名」的兜底）
DEFAULT_PROJECT_NAME = "dialux-project"
#: 面积下限：≤ 此值视为退化多边形（共线/零面积），直接报错
DEGENERATE_AREA_M2 = 1e-6
#: 面积告警下限，与 src/validator 规则 2 的 [0.5, 10000] 区间一致
MIN_SANE_AREA_M2 = 0.5

Ring = List[Tuple[float, float]]
PathLike = Union[str, Path]
#: 归一化后的房间：(ROOM 段 key, 原 space, 顶点环, 净高)
Prepared = List[Tuple[str, Dict[str, Any], Ring, float]]


# ---------- 基础格式化 ----------

def _fmt(value: float) -> str:
    """数字 → STF 紧凑写法：round 到 3 位小数后去掉尾部无意义的 0。

    2.8 → "2.8"；0.0 → "0"；11.450000000000001 → "11.45"；-1.4e-16 → "0"（不是 "-0"）。
    与 S0 验证文件 build/test_room_1.stf（`Point1=0 0 0`）逐字节一致。
    """
    v = round(float(value), COORD_NDIGITS)
    if v == 0:  # 干掉 round 产生的 -0.0（真实 IR 里有 -1.4e-16 这种坐标）
        v = 0.0
    text = f"{v:.{COORD_NDIGITS}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _clean(text: Any) -> str:
    """STF 是一行一个 key=value，值里不能带换行；顺手折叠空白（防伪造出多余 STF 行）。"""
    s = "" if text is None else str(text)
    return " ".join(s.split())


def _ring_signed_area(pts: Sequence[Tuple[float, float]]) -> float:
    """鞋带公式带符号面积（m²）：> 0 = CCW，< 0 = CW。自动忽略闭合重复点。"""
    ring = list(pts)
    if len(ring) > 2 and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        return 0.0
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


# ---------- IR 读取与校验 ----------

def _project(ir: Dict[str, Any]) -> Dict[str, Any]:
    """取 ir.project，缺失当空 dict；类型不对抛 ValueError（而非 AttributeError）。"""
    proj = ir.get("project")
    if proj is None:
        return {}
    if not isinstance(proj, dict):
        raise ValueError(f"IR.project 必须是对象（dict），实际是 {type(proj).__name__}：{proj!r}")
    return proj


def is_furniture_space(space: Dict[str, Any],
                       furniture_ids: Optional[Collection[str]] = None) -> bool:
    """判断 space 是否为家具伪 space（parser 为 SVG 预览额外塞进 spaces 的环）。

    两个信号（任一命中即为家具）：
    1. `name` 以「家具_」开头 —— src/parser/dxf.py::parse_dxf 的命名约定；
    2. `id` 命中某房间的 `furniture[].id`，**且** 该 space 既无 `ceil_h` 也无 `furniture` 键
       —— 这正是 parser 伪 space 的结构特征。加这道门槛是为了避免手写/多楼层 IR 里
       房间与家具 id 撞名时误杀真房间。
    """
    name = space.get("name") or ""
    if str(name).startswith(FURNITURE_NAME_PREFIX):
        return True
    if furniture_ids and space.get("ceil_h") is None and "furniture" not in space:
        sid = space.get("id")
        return sid is not None and str(sid) in furniture_ids
    return False


def _collect_furniture_ids(spaces: Sequence[Dict[str, Any]]) -> Set[str]:
    """汇总所有房间 furniture[].id（家具伪 space 与之同名）；统一转 str 防不可哈希类型。"""
    ids: Set[str] = set()
    for space in spaces:
        furns = space.get("furniture")
        if not isinstance(furns, list):
            continue
        for furn in furns:
            if isinstance(furn, dict) and furn.get("id") is not None:
                ids.add(str(furn["id"]))
    return ids


def _warn_elevation(label: str, owner: Dict[str, Any]) -> None:
    """非零 elevation 会被忽略（MVP2 地板固定 Z=0），出 WARNING 而不是静默出错图。"""
    val = owner.get("elevation")
    if isinstance(val, (int, float)) and not isinstance(val, bool) and val:
        logger.warning("%s.elevation=%s 被忽略：MVP2 房间地板固定 Z=0", label, val)


def _iter_spaces(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    """摊平 storeys[].spaces[]；结构不合法时抛 ValueError。

    MVP2 只建单层：多楼层或非零 elevation 会被忽略（房间全落在 Z=0），此处出 WARNING，
    避免静默产出「几个房间叠在一起」的错图。
    """
    if not isinstance(ir, dict):
        raise ValueError(f"IR 必须是 JSON 对象（dict），实际是 {type(ir).__name__}")
    storeys = ir.get("storeys")
    if storeys is None:
        raise ValueError("IR.storeys 缺失：至少需要 1 个楼层")
    if not isinstance(storeys, list):
        raise ValueError(f"IR.storeys 不是数组（实际 {type(storeys).__name__}）：{storeys!r}")
    if not storeys:
        raise ValueError("IR.storeys 为空：至少需要 1 个楼层")
    if len(storeys) > 1:
        logger.warning("IR 含 %d 个楼层，MVP2 只建单层几何：所有房间地板都落在 Z=0，"
                       "跨层房间会在 DIALux 里重叠（多层支持留后续 MVP）", len(storeys))

    spaces: List[Dict[str, Any]] = []
    for si, storey in enumerate(storeys):
        if not isinstance(storey, dict):
            raise ValueError(f"IR.storeys[{si}] 不是对象：{storey!r}")
        raw = storey.get("spaces") or []
        if not isinstance(raw, list):
            raise ValueError(f"IR.storeys[{si}].spaces 不是数组：{raw!r}")
        _warn_elevation(f"storeys[{si}]", storey)
        for pi, space in enumerate(raw):
            if not isinstance(space, dict):
                raise ValueError(f"IR.storeys[{si}].spaces[{pi}] 不是对象：{space!r}")
            _warn_elevation(f"space {space.get('id')}", space)
            spaces.append(space)
    return spaces


def _space_label(space: Dict[str, Any]) -> str:
    """日志/错误消息里的 space 标识：优先 id，其次 name。"""
    return _clean(space.get("id") or space.get("name") or "<未命名 space>")


def _space_label_verbose(space: Dict[str, Any]) -> str:
    """`name（id）` 形式，用于「跳过了哪些家具」这类需要看名字的日志。"""
    name = _clean(space.get("name"))
    sid = _clean(space.get("id"))
    if name and sid and name != sid:
        return f"{name}（{sid}）"
    return name or sid or "<未命名 space>"


def _resolve_project_name(ir: Dict[str, Any], override: Optional[str],
                          out_path: Optional[Path]) -> str:
    """[PROJECT] Name 的取值顺序：显式覆盖 → ir.project.name → 输出文件名 → 常量兜底。

    有输出路径时退回文件名（与 S0 参考文件 `Name=test_room_1` 一致）；纯内存调用
    （ir_to_stf）没有文件名上下文，落到 DEFAULT_PROJECT_NAME。
    """
    for candidate in (override, _clean(_project(ir).get("name")) if isinstance(ir, dict) else "",
                      out_path.stem if out_path is not None else ""):
        name = _clean(candidate)
        if name:
            return name
    return DEFAULT_PROJECT_NAME


def _parse_polygon(space: Dict[str, Any]) -> Tuple[Ring, int]:
    """space.polygon → (3 位小数顶点列表, 丢弃的重复顶点数)。只做坐标解析，不管环卫生。

    顶点第三维（schema Point2 允许 maxItems=3）在此**有意丢弃**：房间地板固定 Z=0。
    """
    sid = _space_label(space)
    poly = space.get("polygon")
    if not isinstance(poly, ABCSequence) or isinstance(poly, (str, bytes)):
        raise ValueError(f"空间 {sid}：polygon 缺失或不是数组（实际 {type(poly).__name__}）")

    pts: Ring = []
    dropped = 0
    for i, p in enumerate(poly):
        if isinstance(p, (str, bytes)) or not isinstance(p, ABCSequence) or len(p) < 2:
            raise ValueError(f"空间 {sid}：polygon[{i}] 不是 [x, y] 坐标：{p!r}")
        try:
            x = round(float(p[0]), COORD_NDIGITS)
            y = round(float(p[1]), COORD_NDIGITS)
        except (TypeError, ValueError):
            raise ValueError(f"空间 {sid}：polygon[{i}] 坐标不是数字：{p!r}") from None
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"空间 {sid}：polygon[{i}] 坐标不是有限数：{p!r}")
        if pts and (x, y) == pts[-1]:
            dropped += 1  # 3 位小数下重复 → 零长墙段，DIALux 不需要
            continue
        pts.append((x, y))
    return pts, dropped


def _room_ring(space: Dict[str, Any], *, close: bool, normalize_ccw: bool = False) -> Ring:
    """space.polygon → STF 顶点环：去零长边、面积体检、可选 CCW 归一化、按需补首点闭合。"""
    sid = _space_label(space)
    pts, dropped = _parse_polygon(space)
    n_raw = len(space.get("polygon") or [])

    # 首尾重复（IR 里房间环通常已闭合）先摘掉，闭合与否由 close 决定
    while len(pts) > 2 and pts[0] == pts[-1]:
        pts.pop()
        dropped += 1

    if len(pts) < 3:
        raise ValueError(
            f"空间 {sid}：多边形有效顶点 {len(pts)} 个 < 3（原始 {n_raw} 个），无法生成房间"
        )
    if dropped:
        logger.info("空间 %s：丢弃 %d 个重复顶点（3 位小数下重合），保留 %d 个",
                    sid, dropped, len(pts))

    area = _ring_signed_area(pts)
    if abs(area) <= DEGENERATE_AREA_M2:
        raise ValueError(
            f"空间 {sid}：多边形退化（面积 {abs(area):.6f} m² ≈ 0，{len(pts)} 个顶点共线或自相重叠），"
            f"DIALux 无法建出房间"
        )
    if abs(area) < MIN_SANE_AREA_M2:
        logger.warning("空间 %s：面积仅 %.3f m²（< %.1f m²，validator 规则 2 的合理下限），"
                       "确认不是解析噪声环？", sid, abs(area), MIN_SANE_AREA_M2)
    if area < 0:
        if normalize_ccw:
            pts.reverse()
            logger.info("空间 %s：绕向 CW（signed_area=%.3f）→ 已翻转为 CCW", sid, area)
        else:
            logger.info("空间 %s：绕向 CW（signed_area=%.3f），保留 IR 原始顺序"
                        "（如需 CCW 用 --ccw）", sid, area)
    if close:
        return pts + [pts[0]]
    return pts


def _room_height(space: Dict[str, Any], default: Optional[float] = None) -> float:
    """space.ceil_h → 房间净高，必须 > 0；缺失时用 default 兜底（仍需 > 0）。"""
    sid = _space_label(space)
    raw = space.get("ceil_h")
    if raw is None:
        if default is None:
            raise ValueError(f"空间 {sid}：缺少 ceil_h（净高），无法生成 STF ROOM 段"
                             f"（可用 --ceil-h 指定默认净高）")
        raw = default
        logger.info("空间 %s：缺少 ceil_h，用默认净高 %s m", sid, raw)
    if isinstance(raw, bool):  # bool 是 int 子类，float(True)=1.0 会悄悄建 1m 房间
        raise ValueError(f"空间 {sid}：ceil_h 不能是布尔值：{raw!r}")
    try:
        h = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"空间 {sid}：ceil_h 不是数字：{raw!r}") from None
    if not math.isfinite(h):
        raise ValueError(f"空间 {sid}：ceil_h={raw!r} 不是有限数")
    if h <= 0:
        raise ValueError(f"空间 {sid}：ceil_h={raw!r} 必须 > 0")
    return h


# ---------- 文本拼装 ----------

def _header_lines(project_name: str, date: str, room_keys: Sequence[str]) -> List[str]:
    """[VERSION] + [PROJECT] 两段。"""
    lines = [
        "[VERSION]",
        f"STFF={STF_FORMAT_VERSION}",
        f"Progname={PROG_NAME}",
        f"Progvers={PROG_VERS}",
        "",
        "[PROJECT]",
        f"Name={project_name}",
        f"Date={date}",
        f"Planer={PROG_NAME}",
        "Description=",
        f"NrRooms={len(room_keys)}",
    ]
    lines += [f"Room{i}=ROOM.{key}" for i, key in enumerate(room_keys, start=1)]
    return lines


def _luminaire_lines(luminaires: Sequence[Dict[str, Any]]) -> List[str]:
    """生成 STF 灯具行（格式已按 Revit STF-Exporter 修正；evo 14.0 仍忽略灯具段）。

    格式对照（GitHub `kmorin/STF-Exporter`，`STF Exporter/Command.cs` 的
    `writeLumenairs` + ROOM 段，约 290-309 行）：

        Lum1=FixtureName
        Lum1.Pos=X Y Z
        Lum1.Rot=0 0 0

    每盏灯 3 行：``LumN=名称`` + ``LumN.Pos=坐标`` + ``LumN.Rot=旋转``（旧占位实现
    ``Lum{i}=x y z symbol`` 一行全塞是错的）。

    **2026-09-06 真机验证（双重证据）：格式修正后 evo 14.0 依然 0 灯具落地。**
    - 旧格式（一行塞坐标）→ 0 落地（2026-09-05 已判）
    - 新格式（对照 STF-Exporter 三行式，28 盏）→ 导入执行了（ProjectData +24B）但
      ``LuminaireElement=0``，灯具段仍被忽略
    - 官方文档：STF 支持灯具位置（"specified luminaire positions"），但 **evo 的 STF
      export 标注 "in preparation"**——evo 的 STF 导入很可能只实现了房间几何。
      STF-Exporter（2014，针对老 DIALux）的灯具段在 evo 无效。
    → **灯具落地继续走 computer use 通道**（ArrangementFromSpace 已通），本函数
    保留正确格式供老 DIALux / 未来 evo 版本使用，不作为 evo 14.0 的落地出口。
    """
    lines = []
    for i, lum in enumerate(luminaires, start=1):
        x = _fmt(float(lum.get("x", 0.0)))
        y = _fmt(float(lum.get("y", 0.0)))
        z = _fmt(float(lum.get("z", 0.0)))
        name = _clean(str(lum.get("symbol") or f"Lum{i}"))
        lines.append(f"Lum{i}={name}")
        lines.append(f"Lum{i}.Pos={x} {y} {z}")
        lines.append(f"Lum{i}.Rot=0 0 0")
    return lines


def _room_lines(key: str, space: Dict[str, Any], ring: Ring, height: float) -> List[str]:
    """单个 [ROOM.Rn] 段。Description 写 space.id，方便在 DIALux 里回溯到 IR。"""
    lines = [
        "",
        f"[ROOM.{key}]",
        f"Name={_clean(space.get('name') or space.get('id') or key)}",
        f"Description={_clean(space.get('id'))}",
        f"Height={_fmt(height)}",
        f"NrPoints={len(ring)}",
    ]
    # 房间地板固定在 Z=0（MVP2 单层；多层抬高留后续）
    lines += [f"Point{i}={_fmt(x)} {_fmt(y)} 0" for i, (x, y) in enumerate(ring, start=1)]
    # MVP3：灯具行在前、计数在后（对照 STF-Exporter Command.cs：先 Lum 行，
    # 后 NrLums/NrStruct/NrFurns）。旧实现把 NrLums 放前面且 Lum 行格式错误。
    luminaires = space.get("luminaires", []) or []
    nr_lums = len(luminaires)
    if nr_lums:
        lines += _luminaire_lines(luminaires)
    lines += ["NrStruct=0", f"NrLums={nr_lums}", "NrFurns=0"]
    return lines


# ---------- 主逻辑 ----------

def _validate_rooms(ir: Dict[str, Any], prepared: Prepared) -> List[Dict[str, Any]]:
    """对「即将导出的房间几何」复用 optiflow.validator.validate_ir，不自己重写规则。

    两个刻意的取舍：
    1. 校验对象是**归一化后**的环（已补闭合 / 去零长边 / 解析好 ceil_h），不是原始 space：
       否则「IR 里未闭合、由生成器补闭合」这种合法输入会被 POLY_NOT_CLOSED 误拦。
    2. 只保留几何相关信息：家具伪 space 已在 select_rooms 阶段滤掉（否则一堆
       AREA_OUT_OF_RANGE 假阳性），`furniture` / `luminaires` 都被摘掉 —— MVP2 既不导出灯具
       （NrLums 恒为 0），就不该让灯具规则（LUM_OUTSIDE_SPACE / CATALOG_MATCH_UNSET / 挂载高度）
       挡住空房间导出。MVP3 开始导灯具时，这里要连灯具一起校验。

    仍然跑的是 jsonschema 全量 + validator 的几何规则（规则 1/2），所以缺 `id` 之类的
    schema 违规也会被拦 —— 「IR schema 是宪法」，这是有意的。
    """
    from optiflow.validator import validate_ir  # 懒加载：不用 --validate 时不牵 jsonschema

    spaces = []
    for _key, space, ring, height in prepared:
        norm = dict(space)
        norm["polygon"] = [[x, y] for x, y in ring]
        if norm["polygon"][0] != norm["polygon"][-1]:  # validator 规则 1 要求闭合
            norm["polygon"].append(list(norm["polygon"][0]))
        norm["ceil_h"] = height
        norm["luminaires"] = []       # MVP2 不导灯具，灯具规则与本次导出无关
        norm.pop("furniture", None)   # 家具不是 schema 字段，也与几何校验无关
        spaces.append(norm)

    subset = {
        "schema_version": ir.get("schema_version", "0.1"),
        "project": {**{"name": "", "source": {}, "units": "m"}, **_project(ir)},
        "storeys": [{"level": 1, "spaces": spaces}],
    }
    return list(validate_ir(subset))


def select_rooms(ir: Dict[str, Any], *, include_furniture: bool = False) -> List[Dict[str, Any]]:
    """摊平 + 过滤家具伪 space，返回将被导出的房间 space 列表（供 CLI 校验复用）。

    Raises:
        ValueError: IR 结构非法，或过滤后没有任何房间。
    """
    spaces = _iter_spaces(ir)
    furniture_ids = _collect_furniture_ids(spaces)
    rooms, skipped = [], []
    for space in spaces:
        if include_furniture or not is_furniture_space(space, furniture_ids):
            rooms.append(space)
        else:
            skipped.append(_space_label_verbose(space))
    if skipped:
        preview = "、".join(skipped[:5]) + ("…" if len(skipped) > 5 else "")
        logger.info("跳过 %d 个家具伪 space（%s），导出 %d 个房间", len(skipped), preview, len(rooms))
    if not rooms:
        if spaces:
            raise ValueError(
                f"IR 中没有可导出的房间：共 {len(spaces)} 个 space，全部被判为家具伪 space"
                f"（name 以 {FURNITURE_NAME_PREFIX!r} 开头，或 id 命中 furniture[] 且无 ceil_h）；"
                f"可用 --include-furniture --ceil-h 2.8 强制导出"
            )
        raise ValueError("IR 中没有可导出的房间：storeys[].spaces 为空")
    return rooms


def prepare_rooms(ir: Dict[str, Any], *,
                  include_furniture: bool = False,
                  close_polygon: bool = True,
                  normalize_ccw: bool = False,
                  default_ceil_h: Optional[float] = None) -> Prepared:
    """过滤 + 归一化：返回 [(ROOM key, 原 space, 顶点环, 净高), ...]。

    先全量走完再返回：任一 space 非法就抛 ValueError，不会留半截输出。
    """
    rooms = select_rooms(ir, include_furniture=include_furniture)
    return [
        (f"R{idx}", space,
         _room_ring(space, close=close_polygon, normalize_ccw=normalize_ccw),
         _room_height(space, default_ceil_h))
        for idx, space in enumerate(rooms, start=1)
    ]


def ir_to_stf(ir: Dict[str, Any], *,
              project_name: Optional[str] = None,
              include_furniture: bool = False,
              close_polygon: bool = True,
              normalize_ccw: bool = False,
              default_ceil_h: Optional[float] = None,
              date: Optional[str] = None) -> str:
    """IR dict → STF 文本（以 "\\n" 结尾，换行统一 LF）。

    Args:
        ir: IR 字典（见 spec/ir-schema.md）。
        project_name: [PROJECT] Name；缺省取 ir.project.name，仍为空则 DEFAULT_PROJECT_NAME。
        include_furniture: True 时家具伪 space 也当房间导出（默认跳过）。parser 产出的
            家具伪 space 没有 ceil_h，所以通常要配 default_ceil_h 一起用。
        close_polygon: True（默认，任务书要求）时补首点闭合；False 则输出与
            S0 验证文件 test_room_1.stf 一致的「不重复首点」写法。
        normalize_ccw: True 时把 CW 环翻转成 CCW（与 S0 参考文件一致）；默认保留 IR 原序。
        default_ceil_h: space 缺 ceil_h 时的兜底净高（> 0）。
        date: [PROJECT] Date；缺省用今天（YYYY-MM-DD）。

    Raises:
        ValueError: IR 结构非法、无可导出房间、多边形顶点 < 3 或退化、ceil_h <= 0 等。
    """
    prepared = prepare_rooms(ir, include_furniture=include_furniture,
                             close_polygon=close_polygon, normalize_ccw=normalize_ccw,
                             default_ceil_h=default_ceil_h)
    return prepared_to_stf(ir, prepared, project_name=project_name, date=date)


def prepared_to_stf(ir: Dict[str, Any], prepared: Prepared, *,
                    project_name: Optional[str] = None,
                    out_path: Optional[Path] = None,
                    date: Optional[str] = None) -> str:
    """已归一化的房间列表 → STF 文本（供 CLI 在 --validate 后复用同一份 prepared）。

    out_path 只用于 [PROJECT] Name 的兜底（不写文件）。
    """
    name = _resolve_project_name(ir, project_name, out_path)
    day = _clean(date) or datetime.date.today().strftime("%Y-%m-%d")

    lines = _header_lines(name, day, [key for key, _s, _r, _h in prepared])
    for key, space, ring, height in prepared:
        lines += _room_lines(key, space, ring, height)

    logger.info("STF 生成：%d 个房间，共 %d 个顶点",
                len(prepared), sum(len(r) for _k, _s, r, _h in prepared))
    return "\n".join(lines) + "\n"


def write_stf(ir: Dict[str, Any], path: PathLike, *,
              project_name: Optional[str] = None,
              include_furniture: bool = False,
              close_polygon: bool = True,
              normalize_ccw: bool = False,
              default_ceil_h: Optional[float] = None,
              date: Optional[str] = None,
              encoding: str = "utf-8") -> Path:
    """把 ir_to_stf 的结果写到 path（LF 换行），返回写出的 Path。

    project_name 缺省时：ir.project.name 为空则退回输出文件名（不含扩展名），
    与 S0 参考文件 test_room_1.stf 的 `Name=test_room_1` 一致
    （直接调 ir_to_stf 时的兜底是 DEFAULT_PROJECT_NAME，因为那里没有文件名上下文）。
    """
    out = Path(path)
    prepared = prepare_rooms(ir, include_furniture=include_furniture,
                             close_polygon=close_polygon, normalize_ccw=normalize_ccw,
                             default_ceil_h=default_ceil_h)
    text = prepared_to_stf(ir, prepared, project_name=project_name, out_path=out, date=date)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding=encoding, newline="\n")
    return out


# ---------- CLI ----------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m optiflow.adapters.dialux.stf",
        description="IR JSON → DIALux STF（MVP2：只建空房间几何，不放灯具/家具）",
    )
    ap.add_argument("ir_json", help="IR JSON 路径，例如 build/room_layout.json")
    ap.add_argument("out_stf", help="输出 STF 路径，例如 build/mvp2_out.stf")
    ap.add_argument("--project-name", help="覆盖 [PROJECT] Name（缺省取 ir.project.name 或输出文件名）")
    ap.add_argument("--include-furniture", action="store_true",
                    help=f"把家具伪 space（name 以 {FURNITURE_NAME_PREFIX} 开头）也导成房间"
                         f"（默认跳过；parser 产出的家具没有 ceil_h，需配 --ceil-h）")
    ap.add_argument("--ceil-h", type=float, default=None, metavar="M",
                    help="space 缺 ceil_h 时的兜底净高（米，> 0）")
    ap.add_argument("--no-close", action="store_true",
                    help="不补首点闭合，输出与 S0 验证文件 test_room_1.stf 一致的写法")
    ap.add_argument("--ccw", action="store_true",
                    help="把 CW 房间环翻转成 CCW（与 S0 参考文件绕向一致）；默认保留 IR 原序")
    ap.add_argument("--validate", action="store_true",
                    help="导出前对「过滤后的房间集」跑 src.validator（ERROR/HALT 拦下，WARNING 打日志）")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.ceil_h is not None and not (math.isfinite(args.ceil_h) and args.ceil_h > 0):
        logger.error("--ceil-h 必须 > 0，实际 %s", args.ceil_h)
        return 2

    ir_path = Path(args.ir_json)
    try:
        # utf-8-sig：吃掉记事本/PowerShell 存出来的 BOM
        ir = json.loads(ir_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        logger.error("找不到 IR JSON：%s", ir_path)
        return 2
    except UnicodeDecodeError as e:
        logger.error("IR JSON 不是 UTF-8 编码：%s（%s）；请转成 UTF-8 后重试", ir_path, e)
        return 2
    except OSError as e:
        logger.error("IR JSON 读取失败：%s（%s）", ir_path, e)
        return 2
    except json.JSONDecodeError as e:
        logger.error("IR JSON 解析失败：%s（%s）", ir_path, e)
        return 2

    out_path = Path(args.out_stf)
    try:
        prepared = prepare_rooms(ir, include_furniture=args.include_furniture,
                                 close_polygon=not args.no_close,
                                 normalize_ccw=args.ccw,
                                 default_ceil_h=args.ceil_h)

        if args.validate:
            viols = _validate_rooms(ir, prepared)
            blocking = [v for v in viols if v.get("severity") in ("ERROR", "HALT")]
            for v in viols:
                log = logger.error if v.get("severity") in ("ERROR", "HALT") else logger.warning
                log("validator %s [%s] %s", v.get("severity"), v.get("code"), v.get("message"))
            if blocking:
                logger.error("validator 拦下 %d 条 ERROR/HALT，未生成 STF", len(blocking))
                return 2

        project_name = args.project_name
        text = prepared_to_stf(ir, prepared, project_name=project_name, out_path=out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8", newline="\n")
    except ValueError as e:
        logger.error("STF 生成失败：%s", e)
        return 2
    except OSError as e:
        logger.error("STF 写入失败：%s（%s）", args.out_stf, e)
        return 2

    logger.info("STF → %s（%d 字节）", out_path, out_path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
