"""报告组装器：7 块齐全、数值不漂移、缺项显式标注。

铁律：数值只从结果包取，缺的写「未取证」（对齐功能规格 ④.5 的 A4-01/02/04/05/08）。
"""
from __future__ import annotations

import copy
import json

from optiflow.report_assembler import assemble_report

PACKAGE = {
    "meta": {"channel": "report_print_to_pdf + pymupdf_text", "dialux_version": "evo 14"},
    "rooms": [{"id": "R1", "area_m2": 116.03, "height_m": 3.0}],
    "workplane": {
        "height_m": 0.75,
        "edge_zone_m": 0.0,
        "illuminance": {
            "avg_lx": 512.0, "target_lx": 300.0, "uniformity_u0": 0.72, "target_u0": 0.6,
            "emin_lx": 344.0, "emax_lx": 615.0, "uniformity_g2": 0.61,
        },
        "ugr": 16.2,
        "grid": None,
    },
    "metrics_extra": {
        "lpd_w_m2": 6.1, "ra": 90.0, "cct_k": 4000.0,
        "energy_kwh_a": 2100.0, "energy_max_kwh_a": 3600.0, "maintenance_factor": 0.8,
        "flux_total_lm": 480000.0, "power_total_w": 3600.0,
    },
    "luminaires": [{
        "count": 48, "manufacturer": "Acme 艾克米照明", "sku": "200078912",
        "model": "LEDPanelXX-S-36W-5700-WH-U19", "flux_lm": 4200.0, "power_w": 36.0,
        "efficacy_lm_w": 124.5, "ugr": None, "pos": None, "rot": None,
        "positions": [{"no": i, "x_m": 1.0, "y_m": 1.0, "height_m": 3.0} for i in range(1, 49)],
    }],
    "artifacts": {"report_pdf": "build/report.pdf"},
    "integrity": {"luminaire_count": 48, "positions_count": 48, "checks": {}},
    "_missing": [],
}

CRITERIA = {
    "version": "v1",
    "scenes": {"meeting_room": {"label": "会议室", "criteria": [
        {"metric": "illuminance_avg", "op": ">=", "value": 300, "unit": "lx",
         "standard": "GB/T 50034-2024", "table": "表 5.3.2", "page_pdf": 37, "page_print": 28},
        {"metric": "ugr", "op": "<=", "value": 19, "unit": "-",
         "standard": "GB/T 50034-2024", "table": "表 5.3.2", "page_pdf": 37, "page_print": 28},
        {"metric": "uniformity_u0", "op": ">=", "value": 0.6, "unit": "-",
         "standard": "GB/T 50034-2024", "table": "表 5.3.2", "page_pdf": 37, "page_print": 28},
        {"metric": "ra", "op": ">=", "value": 80, "unit": "-",
         "standard": "GB/T 50034-2024", "table": "表 5.3.2", "page_pdf": 37, "page_print": 28},
        {"metric": "lpd_target", "op": "<=", "value": 6.5, "unit": "W/m²",
         "standard": "GB/T 50034-2024", "table": "表 6.3.5", "page_pdf": 65, "page_print": 56},
    ]}},
    "unevidenced": [{"item": "LPD 现行值（GB 55015 口径）", "reason": "本库未收录全文"}],
}


def test_seven_blocks_present() -> None:
    text = assemble_report(PACKAGE, criteria=CRITERIA)
    for title in ("1. 灯具选型列表", "2. 照明效果", "3. 渲染效果", "4. 对标国标",
                  "5. 价格", "6. 节能", "7. 结论与未取证项"):
        assert title in text, title


def test_metric_values_come_from_package_verbatim() -> None:
    text = assemble_report(PACKAGE, criteria=CRITERIA)
    assert "512 lx" in text
    assert "16.2" in text
    assert "0.72" in text
    assert "90" in text
    assert "4000 K" in text
    assert "6.1 W/m²" in text


def test_judgement_and_missing_marked() -> None:
    text = assemble_report(PACKAGE, criteria=CRITERIA)
    assert "达标" in text
    assert "未取证" in text
    assert "渲染图" in text


def test_criteria_page_anchors_present() -> None:
    text = assemble_report(PACKAGE, criteria=CRITERIA)
    assert "表 5.3.2" in text and "印刷 28" in text
    assert "表 6.3.5" in text and "印刷 56" in text


def test_rerun_is_stable() -> None:
    a = assemble_report(PACKAGE, criteria=CRITERIA, generated_on="2026-10-09")
    b = assemble_report(copy.deepcopy(PACKAGE), criteria=copy.deepcopy(CRITERIA), generated_on="2026-10-09")
    assert a == b


def test_luminaire_basis_column_from_backcalc() -> None:
    """选型表「依据」列走 plan.light_backcalc 同一口径（2026-10-16 选灯入口②）。

    期望值手算后与 light_selection.backcalc 核对：
      Φ = 300 lx × 116.03 ㎡ ÷ (0.6 × 0.8) = 72,519 lm；配 48×4200 = 201,600 lm ≥ 需 → ✓
      功率天花板 = 6.5 W/㎡ × 116.03 ㎡ = 754.2 W；配 48×36 = 1728 W > 上限 → ✗（如实标）
    """
    text = assemble_report(PACKAGE, criteria=CRITERIA)
    assert "依据（选灯反推）" in text
    assert "需 Φ 72519 lm；配 48×4200＝201600 lm ✓" in text
    assert "P 1728 W > 上限 754.2 W ✗" in text
    assert ("选灯反推：面积 116.03 ㎡ × 目标 300 lx，CU 0.6 × MF 0.8"
            " → 需总光通量 72519 lm；LPD 上限 6.5 W/㎡ → 功率天花板 754.2 W"
            "（口径＝plan.light_backcalc：不排灯位、不推荐型号）" in text)


def test_basis_unevidenced_without_criteria() -> None:
    """不给判据表就没有目标照度：依据列如实写未取证，不编数。"""
    text = assemble_report(PACKAGE)
    assert "未取证（缺判据表照度目标）" in text
    assert "选灯反推：面积" not in text  # 没依据就不写依据行


def test_basis_unevidenced_without_rooms() -> None:
    """结果包没房间面积同理（抽取器换 schema 时的防线）。"""
    pkg = copy.deepcopy(PACKAGE)
    pkg["rooms"] = []
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "未取证（缺结果包房间面积）" in text


def test_missing_ugr_marks_unevidenced() -> None:
    pkg = copy.deepcopy(PACKAGE)
    pkg["workplane"]["ugr"] = None
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "无实测值" in text or "未取证" in text


def test_ugr_point_list_worst_case_and_point_detail() -> None:
    """指标表取最不利值判定；逐点标识与评估条件落到报告里（需求 1 的报告层落点）。"""
    pkg = copy.deepcopy(PACKAGE)
    pkg["workplane"]["ugr"] = [
        {"point": "计算点 1 (RUG)", "index": "CP1", "ugr_max": 17.4, "target": 19.0,
         "max_at_deg": 135.0, "range_from": 0.0, "range_to": 360.0, "step": 15.0,
         "height_m": 1.2},
        {"point": "计算点 2 (RUG)", "index": "CP2", "ugr_max": 12.1, "target": 19.0},
    ]
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "| 统一眩光值 UGR | 17.4 | ≤ 19 | 达标 |" in text  # 最不利值参与判定
    assert "UGR 观察点：" in text
    assert "CP1 计算点 1 (RUG) 17.4" in text
    assert "CP2 计算点 2 (RUG) 12.1" in text
    assert "未取证" in text  # 渲染图/价格等其他缺项仍正常标注


def test_render_block_accepts_captions(tmp_path) -> None:
    """图件可带图注（dict 形式），字符串形式仍兼容（交付件要能说清图是什么）。"""
    text = assemble_report(PACKAGE, criteria=CRITERIA, render_images=[
        {"path": "a.png", "caption": "工作面照度网格（同源报表页）"},
        "b.png",
    ])
    assert "![工作面照度网格（同源报表页）](a.png)" in text
    assert "*图：工作面照度网格（同源报表页）*" in text
    assert "![渲染效果](b.png)" in text


def test_ugr_absent_marks_unevidenced_without_point_line() -> None:
    pkg = copy.deepcopy(PACKAGE)
    pkg["workplane"]["ugr"] = None
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "UGR 观察点：" not in text
    assert "UGR 实测值（报表未含眩光计算页）" in text


def test_markdown_to_docx_with_borders(tmp_path) -> None:
    """Markdown → DOCX（pandoc）：产出文件，且每个表格都补上了边框。"""
    import pytest

    pytest.importorskip("pypandoc")
    from optiflow.report_assembler import markdown_to_docx

    md = assemble_report(PACKAGE, criteria=CRITERIA, generated_on="2026-10-09")
    out = markdown_to_docx(md, tmp_path / "report.docx")
    assert out.exists() and out.stat().st_size > 0

    import docx
    from docx.oxml.ns import qn

    d = docx.Document(str(out))
    assert d.tables, "报告应至少有一个表"
    for table in d.tables:
        assert table._tbl.tblPr.find(qn("w:tblBorders")) is not None, "每个表都应有边框"


# ============================================================ 图件透传（服务层）


def _stub_service(tmp_path, monkeypatch):
    """服务实例 ＋ 被测的 extract 桩（只验透传，不重测抽取）。"""
    from optiflow import service as service_mod
    from optiflow.adapters.dialux import report as report_mod

    monkeypatch.setattr(
        report_mod, "extract",
        lambda path, **kw: {"meta": {}, "workplane": {}, "artifacts": {}, "_missing": []},
    )
    svc = service_mod.PlatformService(service_mod.build_default_registry(tmp_path), project=None)
    return svc


def test_service_passes_render_images_into_report(tmp_path, monkeypatch) -> None:
    """图件要能透到报告里（HTTP 接口原先没这个参数，壳第 4 块演示会缺图）。"""
    svc = _stub_service(tmp_path, monkeypatch)
    img = tmp_path / "render.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    out = svc.assemble_report_pdf(str(pdf), render_images=[str(img)])
    assert out["render_images"] == [str(img)]
    assert str(img) in out["markdown"]
    assert "未取证：渲染图" not in out["markdown"]


def test_service_missing_render_image_fails_with_the_path(tmp_path, monkeypatch) -> None:
    """图件缺失要当场报出是哪一张（对齐 A5-05 的失败诊断口径）。"""
    import pytest

    svc = _stub_service(tmp_path, monkeypatch)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    missing = tmp_path / "没有这张图.png"

    with pytest.raises(ValueError, match="渲染图不存在"):
        svc.assemble_report_pdf(str(pdf), render_images=[str(missing)])


# ============================================================ HTTP 层


def test_http_assemble_report_reports_missing_file(tmp_path) -> None:
    """报表路径不存在 → 400 与明确错误，不静默。"""
    import json
    import urllib.error
    import urllib.request

    from optiflow.api import serve_in_thread
    from optiflow.service import PlatformService, build_default_registry

    service = PlatformService(build_default_registry(tmp_path), project=None)
    server, _thread, base = serve_in_thread(service, port=0)
    try:
        req = urllib.request.Request(
            f"{base}/assemble-report",
            data=json.dumps({"report_pdf": "/nonexistent/report.pdf"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, payload = resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, json.loads(exc.read().decode("utf-8"))
        assert status == 400
        assert "不存在" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


# ============================================================ 灯库接入（队列 D，2026-10-18）
#
# 护栏：只校验、不推荐、不排灯位；未接入/坏档如实标未取证，不编数。

def _write_lamp_lib(tmp_path, items, version="lamp-library-v0"):
    path = tmp_path / "灯库_v0.json"
    path.write_text(json.dumps({"version": version, "items": items}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def test_lamp_check_column_and_status(tmp_path, monkeypatch) -> None:
    """接入点 A·有结果包：sku 命中 → 新增「灯库校验」列逐行标 ✓，状态行给计数。"""
    lib = _write_lamp_lib(tmp_path, [{
        "sku": "200078912", "manufacturer": "Acme", "model": "X", "kind": "point",
        "flux_lm": 4200.0, "power_w": 36.0, "efficacy_lm_w": 124.5,
        "source": "目录册 P12", "notes": ""}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", lib)
    text = assemble_report(copy.deepcopy(PACKAGE), criteria=CRITERIA)
    assert "| 依据（选灯反推） | 灯库校验 |" in text
    assert "✓ 库内（Φ 4200 lm／P 36 W）" in text
    assert "灯库校验：✓ 库内 1 行 ／ 库外 0 行" in text
    assert "只校验不推荐、不排灯位" in text
    # 重跑逐字稳定（灯库接入不引入随机性）
    assert text == assemble_report(copy.deepcopy(PACKAGE), criteria=CRITERIA)


def test_lamp_mismatch_and_outside_flagged(tmp_path, monkeypatch) -> None:
    """数值差 >1% 标⚠带两边数；sku 不在库 → 库外（如实，不判错）。"""
    mismatch = _write_lamp_lib(tmp_path, [{
        "sku": "200078912", "flux_lm": 4000.0, "power_w": 36.0,
        "source": "目录册 P12"}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", mismatch)
    text = assemble_report(copy.deepcopy(PACKAGE), criteria=CRITERIA)
    assert "⚠ 库内值不符：Φ 库 4000 lm vs 包 4200 lm" in text

    outside = _write_lamp_lib(tmp_path, [{"sku": "别的货号", "flux_lm": 100.0,
                                           "power_w": 10.0, "source": "目录册 P1"}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", outside)
    text = assemble_report(copy.deepcopy(PACKAGE), criteria=CRITERIA)
    assert "库外（无据可校）" in text
    assert "灯库校验：✓ 库内 0 行 ／ 库外 1 行" in text


def test_lamp_absent_keeps_old_table_and_marks(tmp_path, monkeypatch) -> None:
    """未接入：表头不加列、内容与旧版同构，只多一行未接入状态（可发现、不打扰）。"""
    monkeypatch.delenv("GUANGSHU_LAMP_LIB", raising=False)
    text = assemble_report(copy.deepcopy(PACKAGE), criteria=CRITERIA)
    assert "| 依据（选灯反推） | 灯库校验 |" not in text
    assert "灯库校验：未接入" in text
    assert "需 Φ 72519 lm；配 48×4200＝201600 lm ✓" in text   # 旧依据列逐字不动


def test_lamp_basis_fallback_uses_library_values(tmp_path, monkeypatch) -> None:
    """接入点 B：结果包缺 Φ/P 而库有据 → 依据列改用灯库额定值并标来源。"""
    lib = _write_lamp_lib(tmp_path, [{
        "sku": "200078912", "flux_lm": 4200.0, "power_w": 36.0, "source": "目录册 P12"}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", lib)
    pkg = copy.deepcopy(PACKAGE)
    pkg["luminaires"][0].pop("flux_lm")
    pkg["luminaires"][0].pop("power_w")
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "配 48×4200＝201600 lm ✓〔灯库额定Φ＋灯库额定P〕" in text
    assert "P 1728 W > 上限 754.2 W ✗" in text                # 库值参与功率对照
    # 库也补不上 → 仍写未取证（不编数）
    assert "灯库校验：✓ 库内 1 行" in text


def test_lamp_preselect_with_library(tmp_path, monkeypatch) -> None:
    """接入点 A·无灯具列表：灯库预选型＝候选×反推需求，行序不排序、盏数=⌈需Φ÷Φ⌉。"""
    lib = _write_lamp_lib(tmp_path, [
        {"sku": "LMP-A", "manufacturer": "Acme", "model": "Panel-36", "kind": "point",
         "flux_lm": 4200.0, "power_w": 36.0, "efficacy_lm_w": 116.7, "source": "目录册 P12"},
        {"sku": "LMP-B", "manufacturer": "Bee", "model": "Tube-18", "kind": "linear",
         "flux_lm": 1800.0, "power_w": 18.0, "efficacy_lm_w": 100.0, "source": "实测链"},
    ])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", lib)
    pkg = copy.deepcopy(PACKAGE)
    pkg["luminaires"] = []
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "灯库预选型" in text and "按需最少盏数" in text
    assert "不排序、不推荐、不排灯位" in text
    assert "| 18 盏 |" in text                                # ⌈72519÷4200⌉ = 18
    assert "648 W ≤ 天花板 754.2 W ✓" in text                 # 18×36 ≤ 6.5×116.03
    assert "| 41 盏 |" in text                                # ⌈72519÷1800⌉ = 41
    assert "738 W ≤ 天花板 754.2 W ✓" in text                 # 41×18
    assert "出处：" in text and "目录册 P12" in text           # 证据链进报告


def test_lamp_preselect_without_library_keeps_original(tmp_path, monkeypatch) -> None:
    """无库：保留原未取证行，另标未接入（预选型不硬撑）。"""
    monkeypatch.delenv("GUANGSHU_LAMP_LIB", raising=False)
    pkg = copy.deepcopy(PACKAGE)
    pkg["luminaires"] = []
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "> 未取证：结果包中没有可用的灯具列表。" in text
    assert "灯库校验：未接入" in text
    assert "灯库预选型" not in text


def test_lamp_preselect_basis_missing_is_unevidenced(tmp_path, monkeypatch) -> None:
    """缺面积 → 无法比需求：预选型标未取证并说清原因（库在也不编数）。"""
    lib = _write_lamp_lib(tmp_path, [{"sku": "LMP-A", "flux_lm": 4200.0,
                                       "power_w": 36.0, "source": "目录册 P12"}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", lib)
    pkg = copy.deepcopy(PACKAGE)
    pkg["luminaires"] = []
    pkg["rooms"] = []
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "灯库预选型：未取证（缺结果包房间面积" in text


def test_lamp_preselect_boundaries(tmp_path, monkeypatch) -> None:
    """边界：单灯通量非正 → 盏数未取证；没给 LPD → 功率对照给占位不给数。"""
    lib = _write_lamp_lib(tmp_path, [{"sku": "LMP-0", "flux_lm": 0, "power_w": 5.0,
                                       "source": "目录册"}])
    monkeypatch.setenv("GUANGSHU_LAMP_LIB", lib)
    crit_no_lpd = copy.deepcopy(CRITERIA)
    scene = crit_no_lpd["scenes"]["meeting_room"]
    scene["criteria"] = [c for c in scene["criteria"] if c["metric"] != "lpd_target"]
    pkg = copy.deepcopy(PACKAGE)
    pkg["luminaires"] = []
    text = assemble_report(pkg, criteria=crit_no_lpd)
    assert "未取证（缺单灯通量）" in text
    assert "—（未给 LPD 上限）" in text
