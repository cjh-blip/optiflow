"""灯具库统一记录与候选筛选（Task 1）。

覆盖三类输入：
1. 型号名 → 功率/色温解析（型号里 L1563 这类数字不能误当功率）
2. 色温是硬筛选，流明差距是排序键
3. 筛不出候选时给「最接近的」并标 gap，而不是空列表
"""
from __future__ import annotations

from optiflow.luminaire_library import (
    LuminaireRecord,
    parse_attributes,
    select_candidates,
)


def test_parse_attributes_reads_power_and_cct():
    assert parse_attributes("LEDWaterproof-P2-L1563-75W-4000") == (75.0, 4000)
    assert parse_attributes("LEDPanelSp-Z-L1222-75W-BLE-3000-AL") == (75.0, 3000)
    assert parse_attributes("NoNumbersHere") == (None, None)


def test_select_candidates_filters_cct_hard_and_sorts_by_flux_gap():
    recs = [
        LuminaireRecord(id="a", file="a.ies", manufacturer="OPPLE", lumcat="", power_w=30,
                        cct_k=4000, flux_lm=2250, download_url="/d/a", source="t"),
        LuminaireRecord(id="b", file="b.ies", manufacturer="OPPLE", lumcat="", power_w=36,
                        cct_k=4000, flux_lm=3420, download_url="/d/b", source="t"),
        LuminaireRecord(id="c", file="c.ies", manufacturer="OPPLE", lumcat="", power_w=36,
                        cct_k=3000, flux_lm=3420, download_url="/d/c", source="t"),
    ]
    out = select_candidates(recs, per_lamp_flux_lm=3000, cct_k=4000, limit=2)
    assert [o["id"] for o in out] == ["b", "a"]
    assert all(o["cct_k"] == 4000 for o in out)


def test_select_candidates_returns_nearest_when_nothing_matches():
    recs = [LuminaireRecord(id="x", file="x.ies", manufacturer="OPPLE", lumcat="", power_w=10,
                            cct_k=5700, flux_lm=900, download_url="/d/x", source="t")]
    out = select_candidates(recs, per_lamp_flux_lm=3000, cct_k=4000, limit=3)
    assert len(out) == 1 and out[0]["id"] == "x" and out[0]["gap"] is not None
