"""照明结果包抽取器：报表 PDF → schema v1（`adapters/dialux/report.py`）。

三层验证：

1. **坐标路**（纯函数）：span → 视觉行 → 列 → 单元格。钉住三件真机样本上的事实：
   折行的单元格按列拼回、上下标不拆行、表头多 span 列名合并（`R` + `UG`）。
2. **文本路**：`fixtures/dialux_report_sample_text.txt`（结构与真机报表同形、数值全部
   虚构）→ rooms / workplane / metrics_extra 字段级断言；29 页版新字段（计算元件 /
   产品数据表 / 灯具位置图）另用内联片段验证。
3. **端到端**：真机报表 PDF 不入库；设 `DIALUX_REPORT_PDF` 指向它才跑，缺席自动跳过。

红线：真机数据（面积、照度、灯具品牌型号、房名）不进本仓库，fixture 一律虚构。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from optiflow.adapters.dialux.report import (
    _luminaires_from_panel,
    _panel_rows,
    _rows_from_spans,
    parse_report_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_TEXT = FIXTURES / "dialux_report_sample_text.txt"

# 真机样本的 DIALUX_REPORT_PDF 指向；不设则端到端用例跳过
REPORT_PDF = os.environ.get("DIALUX_REPORT_PDF")


def _sp(x: float, yc: float, text: str, w: float | None = None) -> dict:
    """造一个 span：x1 按字数粗估，只求列间间隙量级正确。"""
    return {"x": x, "x1": x + (w if w is not None else 4.2 * len(text)), "yc": yc, "text": text}


# ---------------------------------------------------------------- 坐标路

def test_rows_cluster_by_baseline_and_keep_subscripts_together() -> None:
    """同一视觉行的上下标（U 与 o）不拆成两行。"""
    spans = [
        _sp(174.5, 254.1, "U"),
        _sp(180.3, 256.5, "o", w=2.0),
        _sp(183.5, 254.1, " (g", w=8.0),
        _sp(192.3, 256.5, "1", w=2.0),
        _sp(195.2, 254.1, ")", w=2.0),
        _sp(276.4, 254.1, "0.68", w=16.0),
    ]
    rows = _rows_from_spans(spans)
    assert len(rows) == 1
    assert rows[0]["text"].replace(" ", "") == "Uo(g1)0.68"


def test_header_column_name_merges_adjacent_spans() -> None:
    """表头 `R` + `UG` 是一个列名（间隙 1.0pt），不能切成两列。"""
    spans = [
        _sp(61.0, 453.3, "件数"),
        _sp(409.8, 453.3, "R", w=4.2),
        _sp(415.0, 453.3, "UG", w=7.7),
        _sp(438.0, 453.3, "P"),
    ]
    rows = _rows_from_spans(spans)
    panel = _panel_rows(spans)
    assert panel == []  # 只有表头没有数据行
    assert "RUG" in rows[0]["text"]


def test_panel_rows_glue_wrapped_cells_back() -> None:
    """列宽折断的单元格按列拼回：制造商 / 产品编号 / 发光效率。"""
    header_y, data_y, cont_y = 453.3, 478.8, 490.9
    spans = [
        _sp(61.0, header_y, "件数"), _sp(94.0, header_y, "制造商"),
        _sp(150.7, header_y, "产品编号"), _sp(193.2, header_y, "品名"),
        _sp(409.8, header_y, "R", w=4.2), _sp(415.0, header_y, "UG", w=7.7),
        _sp(438.0, header_y, "P"), _sp(480.5, header_y, "Φ"),
        _sp(523.1, header_y, "发光效率"),
        # 数据行
        _sp(61.0, data_y, "48"), _sp(94.0, data_y, "Acme 艾克米照"),
        _sp(150.7, data_y, "20007891"), _sp(193.2, data_y, "LEDPanelXX-S-36W-5700-WH-U19"),
        _sp(409.7, data_y, "–"), _sp(438.0, data_y, "36.0 W"),
        _sp(480.5, data_y, "4200 lm"), _sp(523.1, data_y, "124.5 lm/"),
        # 折行续行：首列（件数）为空
        _sp(94.0, cont_y, "明"), _sp(150.7, cont_y, "2"), _sp(523.1, cont_y, "W"),
    ]
    panel = _panel_rows(spans)
    assert len(panel) == 1
    row = panel[0]
    assert row["制造商"] == "Acme 艾克米照明"
    assert row["产品编号"] == "200078912"
    assert row["发光效率"] == "124.5 lm/W"
    assert row["RUG"] == "–"

    lums = _luminaires_from_panel(panel)
    assert lums == [
        {
            "count": 48,
            "manufacturer": "Acme 艾克米照明",
            "sku": "200078912",
            "model": "LEDPanelXX-S-36W-5700-WH-U19",
            "flux_lm": 4200.0,
            "power_w": 36.0,
            "efficacy_lm_w": 124.5,
            "ugr": None,  # RUG 列为 `–`
            "pos": None,
            "rot": None,
            "positions": [],
        }
    ]


def test_panel_rows_without_header_returns_empty() -> None:
    assert _panel_rows([_sp(61.0, 100.0, "无关行")]) == []


# ---------------------------------------------------------------- 文本路

@pytest.fixture(scope="module")
def parsed() -> dict:
    return parse_report_text(SAMPLE_TEXT.read_text(encoding="utf-8"))


def test_rooms_and_workplane(parsed: dict) -> None:
    assert parsed["rooms"] == [{"id": "R1", "area_m2": 98.79, "height_m": 3.2}]
    wp = parsed["workplane"]
    assert wp["height_m"] == 0.75
    assert wp["edge_zone_m"] == 0.0
    assert wp["illuminance"] == {
        "avg_lx": 512.0,
        "target_lx": 300.0,
        "uniformity_u0": 0.72,
        "target_u0": 0.6,
        # 2 页版没有「计算元件」页，三项缺席
        "emin_lx": None,
        "emax_lx": None,
        "uniformity_g2": None,
    }
    # 无眩光计算页的报表：ugr 为 null（schema v1 §三允许）；逐点网格固定 None
    assert wp["ugr"] is None and wp["grid"] is None


def test_metrics_extra(parsed: dict) -> None:
    me = parsed["metrics_extra"]
    assert me["lpd_w_m2"] == 7.4
    assert me["energy_kwh_a"] == 2100.0
    assert me["energy_max_kwh_a"] == 3600.0
    assert me["maintenance_factor"] == 0.8
    assert me["reflectance"] == {"ceiling": 70.0, "wall": 50.0, "floor": 20.0}
    # Ra / CCT 只在报表勾了产品数据表页才有，本通道不保证
    assert me["ra"] is None and me["cct_k"] is None


def test_text_only_path_reports_luminaire_table_as_missing(parsed: dict) -> None:
    """纯文本没有坐标，灯具列序不可靠：记缺项而不是猜。"""
    assert "luminaires.table" in parsed["_missing"]
    assert parsed["luminaires"][0]["count"] is None
    assert parsed["integrity"]["luminaire_count"] is None


def test_mojibake_room_name_does_not_break_parsing(parsed: dict) -> None:
    """房名乱码（DIALux 按 CP936 读 UTF-8）不影响其他字段；rooms 不含 name。"""
    assert "浼氳" not in str(parsed["rooms"])
    assert parsed["meta"]["channel"] == "report_print_to_pdf + pymupdf_text"


# ---------------------------------------------------------------- 29 页版新字段

# 2 页版（摘要 + 结果）之外的页面：计算元件、产品数据表、灯具位置图。
# 下面各片段按真机文本层的实测形态构造，数值虚构。

_CALC_ROW = (
    "工作面\n直角照度 (自适应)\n高度: 0.750 m, 边缘区: 0.000 m\n"
    "512 lx\n(≥ 300 lx)\n344 lx\n615 lx\n0.72\n(≥ 0.60)\n0.61\nWP1\n"
)
_DATASHEET = (
    "产品数据表\n7\n"
    "Acme 艾克米照明 - LEDPanelXX-S-36W-5700-WH-U19\n"
    "产品编号\n200078912\nP\n36.0 W\nΦ光源\n4200 lm\nΦ灯具\n4200 lm\n"
    "η\n100.00 %\n发光效率\n124.5 lm/W\n色温\n4000 K\nCRI\n90\n"
)
_POS_TABLE = (
    "灯具位置图\n16\nX\nY\n安装高度\n灯具\n"
    "0.598 m\n12.565 m\n3.000 m\n1\n"
    "3.389 m\n12.565 m\n3.000 m\n2\n"
)


def test_calc_element_row_gives_emin_emax_and_g2() -> None:
    """计算元件页整行序列：Ē / (≥目标) / E最小 / E最大 / Uo / (≥目标) / g2。"""
    out = parse_report_text(_CALC_ROW)
    ill = out["workplane"]["illuminance"]
    assert ill["avg_lx"] == 512.0
    assert ill["target_lx"] == 300.0
    assert ill["emin_lx"] == 344.0
    assert ill["emax_lx"] == 615.0
    assert ill["uniformity_u0"] == 0.72
    assert ill["target_u0"] == 0.6
    assert ill["uniformity_g2"] == 0.61


def test_datasheet_supplies_luminaire_fields_without_coordinates() -> None:
    """无坐标时用产品数据表页补单型号字段与 Ra / CCT。"""
    out = parse_report_text(_DATASHEET)
    lum = out["luminaires"][0]
    assert lum["manufacturer"] == "Acme 艾克米照明"
    assert lum["model"] == "LEDPanelXX-S-36W-5700-WH-U19"
    assert lum["sku"] == "200078912"
    assert lum["power_w"] == 36.0
    assert lum["flux_lm"] == 4200.0
    assert lum["efficacy_lm_w"] == 124.5
    assert out["metrics_extra"]["ra"] == 90.0
    assert out["metrics_extra"]["cct_k"] == 4000.0


def test_fixture_positions_from_position_table() -> None:
    """灯具位置图页的四元组流（X, Y, 安装高度, 编号）。"""
    out = parse_report_text(_POS_TABLE + _DATASHEET)
    lum = out["luminaires"][0]
    assert lum["positions"] == [
        {"no": 1, "x_m": 0.598, "y_m": 12.565, "height_m": 3.0},
        {"no": 2, "x_m": 3.389, "y_m": 12.565, "height_m": 3.0},
    ]
    assert out["integrity"]["positions_count"] == 2


def test_total_rows_are_read_but_never_inferred_into_count() -> None:
    """Φ总数 / P总数可读；件数仍需表格列，位置表条数不作为件数的推定值。"""
    text = "灯具列表\nΦ总数\n480000 lm\nP总数\n3600.0 W\n" + _POS_TABLE
    out = parse_report_text(text)
    assert out["metrics_extra"]["flux_total_lm"] == 480000.0
    assert out["metrics_extra"]["power_total_w"] == 3600.0
    assert out["luminaires"][0]["count"] is None
    assert out["integrity"]["positions_count"] == 2


# ---------------------------------------------------------------- UGR

# 形态照 10-08 UGR 样本（_ugr_probe 真机报表），数值虚构（红线：真机数据不入库）
_UGR_BLOCK = (
    "计算点 2 (RUG)\n最大眩光值在\n135°\n最大\n17.4\n目标\n≤ 19.0\n"
    "观察范围\n0° - 360°\n间距\n15°\n高度\n1.200 m\n索引\nCP2\n"
)


def test_ugr_block_gives_full_point_record() -> None:
    """眩光块 → 点记录：计算点标识与评估条件全保留（Windows 三条要求 1/2），ugr_max 必需（3）。"""
    out = parse_report_text(_UGR_BLOCK)
    assert out["workplane"]["ugr"] == [
        {
            "point": "计算点 2 (RUG)",
            "index": "CP2",
            "ugr_max": 17.4,
            "max_at_deg": 135.0,
            "target": 19.0,
            "range_from": 0.0,
            "range_to": 360.0,
            "step": 15.0,
            "height_m": 1.2,
        }
    ]


def test_ugr_multiple_points_stay_separate_records() -> None:
    """多观察点各成一条记录，不压成标量——多点是最近的下一站。"""
    block2 = (
        "计算点 3 (RUG)\n最大眩光值在\n240°\n最大\n12.1\n目标\n≤ 19.0\n"
        "观察范围\n0° - 360°\n间距\n15°\n高度\n1.200 m\n索引\nCP3\n"
    )
    out = parse_report_text(_UGR_BLOCK + "\n" + block2)
    pts = out["workplane"]["ugr"]
    assert [p["index"] for p in pts] == ["CP2", "CP3"]
    assert [p["ugr_max"] for p in pts] == [17.4, 12.1]


def test_ugr_panel_text_zh_and_en() -> None:
    """UIA 面板回传文本两形态（Windows 侧锚定）都能取，其余字段缺省 None。"""
    from optiflow.adapters.dialux.report import parse_ugr_panel

    zh = parse_ugr_panel("计算点 1 (RUG) 最大 17.4 目标 <= 19.0")
    assert zh["point"] == "计算点 1 (RUG)"
    assert zh["ugr_max"] == 17.4 and zh["target"] == 19.0
    assert zh["index"] is None and zh["step"] is None

    en = parse_ugr_panel("Calculation point Maximum 12.3 target 19")
    assert en["point"] == "Calculation point"
    assert en["ugr_max"] == 12.3 and en["target"] == 19.0

    assert parse_ugr_panel("这行没有眩光数据") is None


# ---------------------------------------------------------------- 端到端（真机样本）

@pytest.mark.skipif(not REPORT_PDF, reason="需 DIALUX_REPORT_PDF 指向真机报表 PDF")
def test_extract_real_report_pdf() -> None:
    from optiflow.adapters.dialux.report import extract

    out = extract(REPORT_PDF, project="sample", dialux_version="evo 14")
    assert out["_missing"] == []
    assert out["rooms"][0]["area_m2"] > 0
    assert out["workplane"]["illuminance"]["avg_lx"] > 0
    assert out["workplane"]["illuminance"]["uniformity_u0"] is not None
    assert out["metrics_extra"]["lpd_w_m2"] is not None
    lum = out["luminaires"][0]
    assert lum["count"] == out["integrity"]["luminaire_count"]
    for key in ("manufacturer", "sku", "model", "flux_lm", "power_w", "efficacy_lm_w"):
        assert lum[key] is not None, f"真机样本应抽到 {key}"
    assert out["artifacts"]["report_pdf"] == str(REPORT_PDF)
