"""选灯 action：反推 + 筛库 → 候选（Task 2）。

三类输入：正常出候选、注册表可调、非法面积给人话错误。
索引用临时文件构造，不依赖本机路径。
"""
from __future__ import annotations

import json

import pytest

from optiflow.luminaire_selection import select_for_room


@pytest.fixture
def index_file(tmp_path):
    recs = [
        {"file": f"{name}.ies", "luminaire": f"LEDPanel-{name}", "lumcat": "", "manufacturer": "OPPLE",
         "hash": f"h_{name}", "download_url": f"/browse/download/ies/h_{name}", "source": "t"}
        for name in ("L1200-30W-4000", "L1200-36W-4000", "L1200-36W-3000")
    ]
    p = tmp_path / "idx.json"
    p.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_select_for_room_returns_candidates_bound_to_area(index_file):
    out = select_for_room(area_m2=16.0, target_lux=500, count=9, cct_k=4000,
                          limit=3, index_path=index_file)
    assert out["backcalc"]["total_flux_lm"] > 0
    assert 0 < len(out["candidates"]) <= 3
    assert all("id" in c and "gap" in c for c in out["candidates"])
    assert out["state"]["area_m2"] == 16.0
    assert all(c["cct_k"] == 4000 for c in out["candidates"])


def test_call_registered_action(index_file):
    from optiflow.commands import call

    r = call("plan.select_luminaire", {"area_m2": 16.0, "target_lux": 500,
                                       "count": 9, "index_path": index_file})
    assert r["candidates"]
    assert r["backcalc"]["state"]["target_lux"] == 500


def test_invalid_area_raises_human_readable(index_file):
    with pytest.raises(ValueError, match="面积"):
        select_for_room(area_m2=0, target_lux=500, index_path=index_file)


def test_missing_index_raises_human_readable(tmp_path, monkeypatch):
    monkeypatch.delenv("GUANGSHU_LUM_INDEX", raising=False)
    with pytest.raises(ValueError, match="灯具库索引"):
        select_for_room(area_m2=16.0, target_lux=500, count=9)
