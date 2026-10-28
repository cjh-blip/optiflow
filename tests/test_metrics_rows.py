# -*- coding: utf-8 -*-
"""metrics_rows 的判据溯源：每行带 evidence（标准号/表号/条款号/双页码/条文原文）。

三条硬约束（2026-10-28 立）：
① 无判据表时 `evidence` 是 None，不是空对象——半条依据比没有依据更容易误导；
② `standard`/`table`/`clause` 三者缺一，整条 `evidence` 置 None，不半条凑数；
③ 页码缺失时不编造数字，填 None。
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from optiflow.report_assembler import metrics_rows  # noqa: E402

_RULE = {
    "metric": "illuminance_avg", "label": "维持平均照度 Ē", "op": ">=", "value": 300,
    "unit": "lx", "standard": "GB/T 50034-2024", "table": "表 5.3.2", "clause": "5.3.2",
    "page_pdf": 37, "page_print": 28,
    "quote": "办公建筑照明标准值应符合表 5.3.2 的规定。", "status": "verified",
}
_PKG = {"metrics": {"illuminance_avg": 350.0}, "rooms": [{"id": "R1", "area_m2": 97.66}]}


def _rows(rules):
    return metrics_rows(_PKG, {"scenes": {"meeting_room": {"criteria": rules}}})


def test_metrics_rows_carry_evidence() -> None:
    row = next(r for r in _rows([_RULE]) if r["key"] == "illuminance_avg")
    ev = row["evidence"]
    assert ev["standard"] == "GB/T 50034-2024"
    assert ev["table"] == "表 5.3.2" and ev["clause"] == "5.3.2"
    assert ev["page_pdf"] == 37 and ev["page_print"] == 28
    assert ev["quote"].startswith("办公建筑照明标准值")


def test_metrics_rows_without_criteria_have_no_evidence() -> None:
    rows = metrics_rows(_PKG, None)
    assert rows and all(r["evidence"] is None for r in rows)
    assert all("source" in r for r in rows)          # 既有键一个不少


def test_incomplete_evidence_is_dropped() -> None:
    """缺 standard 或 clause → 整条 None，不拿表号当条款号顶上。"""
    no_clause = {k: v for k, v in _RULE.items() if k != "clause"}
    assert next(r for r in _rows([no_clause]) if r["key"] == "illuminance_avg")["evidence"] is None

    no_standard = {k: v for k, v in _RULE.items() if k != "standard"}
    assert next(r for r in _rows([no_standard]) if r["key"] == "illuminance_avg")["evidence"] is None


def test_evidence_tolerates_missing_pages_and_quote() -> None:
    """页码与原文可以让位给 None，但三者齐全时 evidence 仍成立。"""
    sparse = {k: v for k, v in _RULE.items() if k not in ("page_pdf", "page_print", "quote")}
    ev = next(r for r in _rows([sparse]) if r["key"] == "illuminance_avg")["evidence"]
    assert ev is not None
    assert ev["page_pdf"] is None and ev["page_print"] is None and ev["quote"] is None


def test_real_criteria_table_flows_through() -> None:
    """真实判据表（若本机有 App 仓）走一遍：四个 5.3.2、一个 6.3.5。

    路径从仓库位置推，不写死本机目录：本仓是公开仓，硬编码带项目目录名的
    路径会撞推送门的软词表（2026-10-28 实撞过一次），也会把开发机布局泄进仓里。
    """
    import json

    import pytest

    from optiflow.adapters.dialux.report import _f  # noqa: F401  确认平台包已就位

    projects = Path(__file__).resolve().parents[3]      # …/projects（与 optiflow-app 同级）
    crit_path = projects / "optiflow-app" / "standalone" / "criteria" / "criteria_meeting_room.json"
    if not crit_path.is_file():
        pytest.skip(f"同级仓里没有判据表，跳过：{crit_path.name}")
    criteria = json.loads(crit_path.read_text(encoding="utf-8"))
    rows = metrics_rows(_PKG, criteria)
    got = {r["key"]: (r["evidence"] or {}).get("clause") for r in rows}
    assert got.get("illuminance_avg") == "5.3.2"
    assert got.get("ugr") == "5.3.2"
    assert got.get("lpd_target") == "6.3.5"
