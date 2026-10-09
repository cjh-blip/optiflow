"""报告组装器：7 块齐全、数值不漂移、缺项显式标注。

铁律：数值只从结果包取，缺的写「未取证」（对齐功能规格 ④.5 的 A4-01/02/04/05/08）。
"""
from __future__ import annotations

import copy

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


def test_missing_ugr_marks_unevidenced() -> None:
    pkg = copy.deepcopy(PACKAGE)
    pkg["workplane"]["ugr"] = None
    text = assemble_report(pkg, criteria=CRITERIA)
    assert "无实测值" in text or "未取证" in text


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
