"""DIALux 报表 PDF → 照明结果包（schema v1）抽取器。

通道：DIALux「报表 → 打印 → Microsoft Print to PDF」→ PyMuPDF 文本层。
口径依据：`交接区/_给Windows_20261006_照明结果包schema_v1.md`。

两条入口：

- :func:`extract` —— 坐标感知。摘要/结果段走文本层正则，**灯具列表走 span 坐标**：
  列按表头 span 的 x 起点切分、折行的单元格按列拼回。真机样本实测，只有这条路能
  还原被列宽折断的单元格（制造商名、发光效率、表头 `R` + `UG` 都属此类）。
- :func:`parse_report_text` —— 纯文本退化路径，只吃文本层字符串时用（老摘录、单测）。

已知边界（2026-10-07/08 在真机样本上实测）：

- **两个报表版本**：2 页版（摘要 + 结果）与 29 页版（全量导出）。29 页版额外带来
  灯具位置图的逐灯 X/Y/安装高度表（→ `luminaires[].positions`）、产品数据表
  （Ra / CCT / Φ灯具）、计算元件页（E最小 / E最大 / g2）、灯具列表页汇总行
  （Φ总数 / P总数）；上述字段在 2 页版为 None / 空列表。
- 页眉房名乱码来自 DIALux 按 CP936 读 UTF-8，且中间夹私用区码位（U+E185）与丢失的
  半字节，**无法无损逆向**；schema v1 的 rooms 不含 name，故不影响结果包，原样保留。
- 产品编号列宽不够时会折行（8 位编号被切成 8+1 两段落进文本流），坐标路按列拼回
  9 位；schema 定稿文档记的是 8 位，原值待 Windows 侧在 DIALux 里核。
- **UGR**：报表勾选眩光计算后，「计算点 n (RUG)」块可取（2026-10-08 UGR 样本实测）→
  `workplane.ugr` 为**点记录列表**（point / index / ugr_max / max_at_deg / target /
  range_from / range_to / step / height_m），多观察点就绪、不压成标量；**`ugr_max`
  是唯一必需值**，其余字段缺一个只置 None（块锚点只有「点名行 + 最大」两处）。
  未勾选眩光计算的报表为 null（schema v1 §三允许 null，报告层标未取证；抽取层
  不记 `_missing`）。逐点网格仍只有等值线矢量图 → 固定 None。UIA 面板回传的单点
  文本走 :func:`parse_ugr_panel`（中/英两形态，锚定 Windows 侧 test_ugr_task）。
  *已知边界*：报表块锚点写的是中文报表文案（英文界面导出的报表未取，需样本再补）；
  面板路径按单点单行文本处理。

数值一律从文本层抽，不手抄；抽不到的字段置 None 并记入 `_missing`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "pdf_text",
    "pdf_text_by_page",
    "pdf_spans",
    "parse_report_text",
    "parse_ugr_panel",
    "extract",
]

_NUM = r"[-+]?\d+(?:[.,]\d+)?"

# 行聚类容差（pt）：同表行的 span 会因上下标有 4pt 级别的基线差，3.5 刚好并住
_ROW_TOL = 3.5
# 同一列内相邻 span 的间隙阈值：小于它视为一个单元格的连续文字（如 `R` + `UG`）
_COL_GAP = 1.5
# span 归属到某列起点的容差
_COL_TOL = 1.5

_PANEL_HEADER_KEYS = ("件数", "品名")


# ---------------------------------------------------------------- 数值工具

def _f(raw: str) -> float:
    return float(raw.replace(",", "."))


def _num_in(text: Optional[str]) -> Optional[float]:
    """抓单元格里的第一个数（`30.0 W` → 30.0）。"""
    if text is None:
        return None
    m = re.search(_NUM, text)
    return _f(m.group(0)) if m else None


def _cell_text(raw: Optional[str]) -> Optional[str]:
    """单元格占位符归一：`–` / `-` / 空 → None。"""
    if raw is None:
        return None
    s = raw.strip()
    return s if s and s not in {"–", "-", "—", "N/A"} else None


def _first(pattern: str, text: str, group: int = 1) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(group) if m else None


def _first_of(*candidates: Optional[str]) -> Optional[str]:
    for c in candidates:
        if c is not None:
            return c
    return None


# ---------------------------------------------------------------- PDF 读取

def pdf_text_by_page(path: str | Path) -> List[str]:
    """每页文本层，一页一项。多页/多房间排障时比整段拼接好定位。"""
    import fitz  # PyMuPDF，延迟导入：无此依赖时不影响本模块被安全导入

    doc = fitz.open(str(path))
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def pdf_text(path: str | Path) -> str:
    """把 PDF 文本层抽成一整段文本（按页拼接）。"""
    return "\n".join(pdf_text_by_page(path))


def pdf_spans(path: str | Path) -> List[List[Dict[str, Any]]]:
    """每页的 span 列表（含 x / x1 / 基线中心 yc / text），供表格行列还原。"""
    import fitz

    doc = fitz.open(str(path))
    try:
        pages: List[List[Dict[str, Any]]] = []
        for page in doc:
            spans: List[Dict[str, Any]] = []
            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for sp in line.get("spans", []):
                        if not sp["text"].strip():
                            continue
                        x0, y0, x1, y1 = sp["bbox"]
                        spans.append(
                            {"x": x0, "x1": x1, "yc": (y0 + y1) / 2, "text": sp["text"]}
                        )
            pages.append(spans)
        return pages
    finally:
        doc.close()


# ---------------------------------------------------------------- 坐标路：表格

def _rows_from_spans(spans: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """span → 视觉行：按基线中心聚类，行内按 x 排序拼成行文本。"""
    rows: List[Dict[str, Any]] = []
    for sp in sorted(spans, key=lambda s: (s["yc"], s["x"])):
        if rows and abs(sp["yc"] - rows[-1]["yc"]) <= _ROW_TOL:
            rows[-1]["spans"].append(sp)
        else:
            rows.append({"yc": sp["yc"], "spans": [sp]})
    for r in rows:
        r["spans"].sort(key=lambda s: s["x"])
        r["text"] = "".join(s["text"] for s in r["spans"]).strip()
    return rows


def _split_header_columns(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """表头行 → 列定义（x 起点 + 列名）。间隙小的相邻 span 归为同一列名。"""
    cols: List[Dict[str, Any]] = []
    for sp in row["spans"]:
        if cols and sp["x"] - cols[-1]["x1"] <= _COL_GAP:
            cols[-1]["text"] += sp["text"]
            cols[-1]["x1"] = sp["x1"]
        else:
            cols.append({"x": sp["x"], "x1": sp["x1"], "text": sp["text"]})
    for c in cols:
        c["text"] = c["text"].strip()
    return cols


def _assign_cells(row: Dict[str, Any], cols: Sequence[Dict[str, Any]]) -> List[str]:
    """把一个数据行按列定义切成单元格（span 文本自带空格，直接串接）。"""
    cells = [""] * len(cols)
    for sp in row["spans"]:
        idx = 0
        for i, c in enumerate(cols):
            if sp["x"] >= c["x"] - _COL_TOL:
                idx = i
            else:
                break
        cells[idx] += sp["text"]
    return [c.strip() for c in cells]


def _panel_rows(spans: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """灯具列表表 → 行字典；折行的续行按列拼回上一行。"""
    rows = _rows_from_spans(spans)
    header_at = None
    for i, r in enumerate(rows):
        if all(k in r["text"] for k in _PANEL_HEADER_KEYS):
            header_at = i
            break
    if header_at is None:
        return []

    cols = _split_header_columns(rows[header_at])
    out: List[Dict[str, str]] = []
    for r in rows[header_at + 1:]:
        cells = _assign_cells(r, cols)
        if not any(cells):
            continue
        if out and not cells[0]:
            # 首列（件数）为空的后续行 = 上一行单元格的折行，按列并回
            prev = out[-1]
            for i, c in enumerate(cols):
                if cells[i]:
                    prev[c["text"]] = prev.get(c["text"], "") + cells[i]
            continue
        out.append({c["text"]: cells[i] for i, c in enumerate(cols)})
    return out


def _luminaires_from_panel(panel: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in panel:
        items.append(
            {
                "count": (
                    int(_num_in(row.get("件数"))) if _num_in(row.get("件数")) is not None else None
                ),
                "manufacturer": _cell_text(row.get("制造商")),
                "sku": _cell_text(row.get("产品编号")),
                "model": _cell_text(row.get("品名")),
                "flux_lm": _num_in(row.get("Φ")),
                "power_w": _num_in(row.get("P")),
                "efficacy_lm_w": _num_in(row.get("发光效率")),
                "ugr": _num_in(row.get("RUG")),
                "pos": None,
                "rot": None,
                "positions": [],
            }
        )
    return items


# ---------------------------------------------------------------- 文本路：逐灯坐标

_POS_ROW = re.compile(r"([\d.]+) m\s*\n\s*([\d.]+) m\s*\n\s*([\d.]+) m\s*\n\s*(\d+)\s*\n")


def _fixture_positions(text: str) -> List[Dict[str, Any]]:
    """「灯具位置图」页的 X / Y / 安装高度 表 → 逐灯坐标（29 页版特有）。

    该表的文本流是规整的四元组（X, Y, Z, 编号），按页分段解析，避免跨页拼接。
    2 页版报表没有位置表，返回空列表。
    """
    if "灯具位置图" not in text:
        return []
    out: List[Dict[str, Any]] = []
    for seg in text.split("灯具位置图")[1:]:
        for m in _POS_ROW.finditer(seg):
            out.append(
                {
                    "no": int(m.group(4)),
                    "x_m": _f(m.group(1)),
                    "y_m": _f(m.group(2)),
                    "height_m": _f(m.group(3)),
                }
            )
    return out


def _single_type_from_datasheet(text: str) -> Dict[str, Any]:
    """「产品数据表」页的单型号信息（文本路后备，无坐标时补灯具字段）。

    标题行形如「<制造商> - <品名>」，其后紧接产品编号。
    """
    if "产品数据表" not in text:
        return {}
    ds = text.split("产品数据表", 1)[-1]
    title = re.search(r"\n([^\n]+?)\s+-\s+([^\n]+)\n产品编号\s*\n\s*(\d{6,})", ds)
    if not title:
        return {}
    return {
        "manufacturer": _cell_text(title.group(1)),
        "model": _cell_text(title.group(2)),
        "sku": title.group(3),
        "power_w": _num_in(_first(rf"P\s*\n\s*({_NUM})\s*W", ds)),
        "flux_lm": _num_in(_first(rf"Φ灯具\s*\n\s*({_NUM})\s*lm", ds)),
        "efficacy_lm_w": _num_in(_first(rf"发光效率\s*\n\s*({_NUM})\s*lm/W", ds)),
    }


# ---------------------------------------------------------------- 文本路：字段

def _rooms(text: str, missing: List[str]) -> List[Dict[str, Any]]:
    area = _first_of(
        _first(rf"地面\s*\n\s*({_NUM})\s*m²", text),
        _first(rf"({_NUM})\s*m²", text),
    )
    height = _first(rf"安装高度\s*\n\s*({_NUM})\s*m", text)
    if area is None:
        missing.append("rooms.area_m2")
    if height is None:
        missing.append("rooms.height_m")
    return [
        {
            "id": "R1",
            "area_m2": _f(area) if area else None,
            "height_m": _f(height) if height else None,
        }
    ]


# ---------------------------------------------------------------- 文本路：UGR

# 报表「计算点 n (RUG)」眩光块的**锚点**（DIALux evo 14 中文报表，2026-10-08 样本实测形态）：
# 点名行 → [最大眩光值在(角度)] → 最大(值)。只有这两处是必需，其余字段各自独立抽：
# 缺一个只丢一个字段，ugr_max 仍是唯一必需值（Windows 侧 2026-10-09 要求 3）。
_UGR_ANCHOR = re.compile(
    rf"(?P<point>[^\n]*\(RUG\))\s*"
    rf"(?:最大眩光值在\s*(?P<at>{_NUM})°\s*)?"
    rf"最大\s*(?P<max>{_NUM})"
)
_UGR_RANGE = re.compile(rf"观察范围\s*({_NUM})°\s*-\s*({_NUM})°")

# UIA 面板回传文本（单点），中/英两形态由 Windows 侧 test_ugr_task 锚定：
#   中：计算点 1 (RUG) 最大 16.2 目标 <= 19.0
#   英：Calculation point Maximum 12.7 target 19
# 以「计算点 / Calculation point」关键字起首，避开把报表多行文本里的数字串误当点名；
# 目标可缺（缺则 None）。
_UGR_PANEL = (
    re.compile(
        rf"(?P<point>(?:计算点|Calculation point)[^\n]{{0,30}}?)\s*最大\s*(?P<max>{_NUM})"
        rf"\s*(?:目标\s*(?:≤|<=|<)?\s*(?P<target>{_NUM}))?"
    ),
    re.compile(
        rf"(?P<point>(?:计算点|Calculation point)[^\n]{{0,30}}?)\s*Maximum\s*(?P<max>{_NUM})"
        rf"\s*(?:[Tt]arget\s*(?:≤|<=|<)?\s*(?P<target>{_NUM}))?"
    ),
)


def _ugr_record(
    point: str,
    ugr_max: str,
    *,
    index: Optional[str] = None,
    at: Optional[str] = None,
    target: Optional[str] = None,
    range_from: Optional[str] = None,
    range_to: Optional[str] = None,
    step: Optional[str] = None,
    height: Optional[str] = None,
) -> Dict[str, Any]:
    """UGR 点记录（schema v1 §三 转 A 档）。`ugr_max` 唯一必需，其余有则留、无则 None。"""
    return {
        "point": point,
        "index": index,
        "ugr_max": _f(ugr_max),
        "max_at_deg": _f(at) if at is not None else None,
        "target": _f(target) if target is not None else None,
        "range_from": _f(range_from) if range_from is not None else None,
        "range_to": _f(range_to) if range_to is not None else None,
        "step": _f(step) if step is not None else None,
        "height_m": _f(height) if height is not None else None,
    }


def _ugr_points(text: str) -> Optional[List[Dict[str, Any]]]:
    """报表全文 → UGR 点记录列表；无眩光块返回 None（schema v1 §三允许 null）。

    每块范围止于下一个 `(RUG)` 锚点，多观察点之间不串字段；块内除 `ugr_max` 外
    都可缺（缺则 None），不整块连坐。
    """
    anchors = list(_UGR_ANCHOR.finditer(text))
    if not anchors:
        return None
    out: List[Dict[str, Any]] = []
    for i, m in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
        region = text[m.end():end]
        rng = _UGR_RANGE.search(region)
        out.append(
            _ugr_record(
                m.group("point").strip(),
                m.group("max"),
                index=_first(r"索引\s*(\S+)", region),
                at=m.group("at"),
                target=_first(rf"目标\s*(?:≤|<=|<)?\s*({_NUM})", region),
                range_from=rng.group(1) if rng else None,
                range_to=rng.group(2) if rng else None,
                step=_first(rf"间距\s*({_NUM})°", region),
                height=_first(rf"高度\s*({_NUM})\s*m", region),
            )
        )
    return out


def parse_ugr_panel(text: str) -> Optional[Dict[str, Any]]:
    """UIA 面板回传文本（中/英两形态）→ 单点 UGR 记录；取不到返回 None。"""
    for rx in _UGR_PANEL:
        m = rx.search(text)
        if m:
            return _ugr_record(
                m.group("point").strip(), m.group("max"), target=m.group("target")
            )
    return None


def _workplane(text: str, missing: List[str]) -> Dict[str, Any]:
    h = _first(rf"高度工作面\s*\n\s*({_NUM})\s*m", text)
    edge = _first(rf"边缘区工作面\s*\n\s*({_NUM})\s*m", text)
    # 29 页版「计算元件」页给出同一对参数（带单位的那一行）
    dims = re.search(rf"高度:\s*({_NUM})\s*m,\s*边缘区:\s*({_NUM})\s*m", text)
    if dims:
        h = h if h is not None else dims.group(1)
        edge = edge if edge is not None else dims.group(2)

    # 照度值与目标值必须成对出现：避开 29 页版「1.22 W/m²/100 lx」这类干扰
    pair = re.search(rf"({_NUM})\s*lx\s*\n\s*\(?≥\s*({_NUM})\s*lx\)?", text)
    avg = pair.group(1) if pair else None
    target = pair.group(2) if pair else None
    if avg is None:
        avg = _first(rf"Ē\S*\s*\(工作面\)\s*\n\s*({_NUM})\s*lx", text)

    # 29 页版「计算元件」页的整行序列：Ē / (≥目标) / E最小 / E最大 / Uo / (≥目标) / g2
    calc = re.search(
        rf"({_NUM})\s*lx\s*\n\s*\(≥\s*({_NUM})\s*lx\)\s*\n\s*({_NUM})\s*lx\s*\n"
        rf"\s*({_NUM})\s*lx\s*\n\s*({_NUM})\s*\n\s*\(≥\s*({_NUM})\)\s*\n\s*({_NUM})",
        text,
    )
    emin = emax = g2 = u0_target = None
    if calc:
        avg = avg if avg is not None else calc.group(1)
        target = target if target is not None else calc.group(2)
        emin, emax = calc.group(3), calc.group(4)
        u0, u0_target = calc.group(5), calc.group(6)
        g2 = calc.group(7)
    else:
        u0 = _first(rf"Uo\s*\(g1\)\s*\n\s*({_NUM})", text)
        u0_target = _first(r"Uo\s*\(g1\)[\s\S]{0,120}?≥\s*(0\.\d+)", text)

    for name, val in (
        ("workplane.height_m", h),
        ("workplane.edge_zone_m", edge),
        ("workplane.illuminance.avg_lx", avg),
        ("workplane.illuminance.uniformity_u0", u0),
    ):
        if val is None:
            missing.append(name)

    return {
        "height_m": _f(h) if h else None,
        "edge_zone_m": _f(edge) if edge else None,
        "illuminance": {
            "avg_lx": _f(avg) if avg else None,
            "target_lx": _f(target) if target else None,
            "uniformity_u0": _f(u0) if u0 else None,
            "target_u0": _f(u0_target) if u0_target else None,
            # 29 页版「计算元件」页新增；2 页版缺席
            "emin_lx": _f(emin) if emin else None,
            "emax_lx": _f(emax) if emax else None,
            "uniformity_g2": _f(g2) if g2 else None,
        },
        # UGR：含眩光块 → 点记录列表（多样本就绪）；未勾选的报表 → null
        "ugr": _ugr_points(text),
        # 逐点网格仍只有等值线矢量图，固定 None
        "grid": None,
    }


def _metrics_extra(text: str, missing: List[str]) -> Dict[str, Any]:
    lpd = _first(rf"照明功率密度\s*\n\s*({_NUM})\s*W/m²", text)
    energy = _first(rf"({_NUM})\s*kWh/a", text)
    energy_max = _first(rf"最大\.\s*({_NUM})\s*kWh/a", text)
    mf = _first(rf"维护系数\s*\n\s*({_NUM})", text)
    refl = re.search(
        r"天花板:\s*([\d.]+)\s*%[\s\S]*?墙壁:\s*([\d.]+)\s*%[\s\S]*?地板:\s*([\d.]+)\s*%",
        text,
    )

    # Ra / CCT 来自「产品数据表」页（报表需勾选该页才有）；限定在该段落内抽，
    # 避开词汇表里「色温」「CRI」的解释性文字
    ds = text.split("产品数据表", 1)[-1] if "产品数据表" in text else ""
    ra = _first(r"\nCRI\s*\n\s*(\d{2,3})", ds) if ds else None
    cct = _first(r"色温\s*\n\s*(\d{4})\s*K", ds) if ds else None
    if ra is None:
        ra = _first_of(
            _first(r"\bRa\s*[:：]?\s*(\d{2,3})", text),
            _first(r"显色指数\s*[:：]?\s*(\d{2,3})", text),
        )
    if cct is None:
        cct = _first(r"色温\s*[:：]?\s*(\d{4})\s*K", text)

    # 灯具列表页的汇总行（用于与件数交叉校验）
    flux_total = _first(rf"Φ总数\s*\n\s*({_NUM})\s*lm", text)
    power_total = _first(rf"P总数\s*\n\s*({_NUM})\s*W", text)

    for name, val in (
        ("metrics_extra.lpd_w_m2", lpd),
        ("metrics_extra.energy_kwh_a", energy),
        ("metrics_extra.maintenance_factor", mf),
    ):
        if val is None:
            missing.append(name)

    return {
        "lpd_w_m2": _f(lpd) if lpd else None,
        "ra": float(ra) if ra else None,
        "cct_k": float(cct) if cct else None,
        "energy_kwh_a": _f(energy) if energy else None,
        "energy_max_kwh_a": _f(energy_max) if energy_max else None,
        "maintenance_factor": _f(mf) if mf else None,
        "reflectance": (
            {"ceiling": _f(refl.group(1)), "wall": _f(refl.group(2)), "floor": _f(refl.group(3))}
            if refl
            else None
        ),
        # 灯具列表页汇总行（29 页版与 2 页版均含）
        "flux_total_lm": _f(flux_total) if flux_total else None,
        "power_total_w": _f(power_total) if power_total else None,
    }


# ---------------------------------------------------------------- 主流程

def _parse_pages(pages: Sequence[Dict[str, Any]], meta_extra: Dict[str, Any]) -> Dict[str, Any]:
    text = "\n".join(p["text"] for p in pages)
    missing: List[str] = []

    rooms = _rooms(text, missing)
    workplane = _workplane(text, missing)
    metrics = _metrics_extra(text, missing)

    luminaires: List[Dict[str, Any]] = []
    for p in pages:
        if p.get("spans"):
            luminaires = _luminaires_from_panel(_panel_rows(p["spans"]))
            if luminaires:
                break
    if not luminaires:
        # 纯文本退化：无坐标时表格列序不可靠，用产品数据表页补单型号字段，
        # 列序相关的字段（件数等）仍记缺项而不是猜
        missing.append("luminaires.table")
        base: Dict[str, Any] = {
            "count": None,
            "manufacturer": None,
            "sku": None,
            "model": None,
            "flux_lm": None,
            "power_w": None,
            "efficacy_lm_w": None,
            "ugr": None,
            "pos": None,
            "rot": None,
            "positions": [],
        }
        base.update(_single_type_from_datasheet(text))
        luminaires = [base]

    # 逐灯坐标表（29 页版特有）：挂到首个灯具类型下
    positions = _fixture_positions(text)
    if positions and luminaires:
        luminaires[0]["positions"] = positions

    count = luminaires[0].get("count") if luminaires else None
    if count is None:
        missing.append("luminaires.count")

    meta = {"channel": "report_print_to_pdf + pymupdf_text"}
    meta.update({k: v for k, v in meta_extra.items() if v is not None})

    return {
        "meta": meta,
        "rooms": rooms,
        "workplane": workplane,
        "metrics_extra": metrics,
        "luminaires": luminaires,
        "artifacts": {},
        "integrity": {
            "luminaire_count": count,
            # 逐灯位置表条数；与件数同源同表时两者应相等
            "positions_count": len(positions) or None,
            # 与选型表 / .evo 交叉才能判，本层不猜
            "checks": {"selection_vs_ir": None, "ir_vs_evo": None},
        },
        "_missing": missing,
    }


def parse_report_text(text: str) -> Dict[str, Any]:
    """纯文本层 → 结果包 dict。无坐标，灯具列表只能记缺项。"""
    return _parse_pages([{"text": text, "spans": []}], {})


def extract(
    pdf_path: str | Path,
    *,
    project: Optional[str] = None,
    captured_at: Optional[str] = None,
    dialux_version: Optional[str] = None,
    source_evo: Optional[str] = None,
    source_stf: Optional[str] = None,
) -> Dict[str, Any]:
    """报表 PDF → 结果包（schema v1）。"""
    path = Path(pdf_path)
    pages = [
        {"text": t, "spans": s}
        for t, s in zip(pdf_text_by_page(path), pdf_spans(path))
    ]
    out = _parse_pages(
        pages,
        {
            "project": project,
            "captured_at": captured_at,
            "dialux_version": dialux_version,
            "source_evo": source_evo,
            "source_stf": source_stf,
        },
    )
    out["artifacts"]["report_pdf"] = str(path)
    return out
