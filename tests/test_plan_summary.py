"""图纸摘要（`optiflow.plan_summary`）：四数不漂移、六项检查逐条给结论、坏图纸报错带上下文。

口径三条（对齐壳侧的既有约定）：
- 灯位只报 `join_luminaires` **之后**的数；
- 家具伪房间（`家具_` 前缀）必须剔除，否则房间数/顶点/灯位全错；
- 挂高缺失是**设计态**（生成 DIALux 输入时以净高兜底），不进 HALT、不进真问题。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from optiflow.plan_summary import summarize_plan

FIX = Path(__file__).resolve().parent / "fixtures"
ROOM = FIX / "sample_room.dxf"
LUM = FIX / "sample_lighting.dxf"
CFG = FIX / "sample_parse_config.json"


def test_four_numbers_stable():
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    assert r["room_count"] == 1
    assert r["vertices_total"] == 17
    assert r["area_total_m2"] == 116.03
    assert r["luminaires_total"] == 28


def test_luminaires_split_by_kind_and_shape():
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    assert r["luminaires_by_kind"] == {"point": 16, "linear": 12}
    assert r["luminaires_by_shape"] == {"circle": 16, "rect": 12}


def test_six_checks_each_gives_verdict():
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    assert len(r["checks"]) == 6
    assert r["checks_passed"] == 6
    assert all(c["passed"] and c["detail"] for c in r["checks"])


def test_without_luminaire_dxf_zero_and_lum_check_fails():
    r = summarize_plan(str(ROOM), None, str(CFG), 4.0)
    assert r["luminaires_total"] == 0
    assert r["checks_passed"] == 5
    lum = next(c for c in r["checks"] if c["name"] == "识别到灯位")
    assert lum["passed"] is False
    assert "未提供灯具图" in lum["detail"]


def test_mount_pending_counts_as_design_state_not_error():
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    assert r["validation"]["mount_pending_count"] > 0
    assert all(v.get("code") != "LUM_MOUNT_Z_UNSET" for v in r["validation"]["real"])
    assert r["validation"]["HALT"] == []
    assert any("待下游回填挂载高度" in n for n in r["notes"] if n)


def test_broken_dxf_raises_with_context():
    bad = FIX / "sample_parse_config.json"  # 拿 JSON 当 DXF 喂进去：格式必然不对
    with pytest.raises(Exception):
        summarize_plan(str(bad), None, str(CFG), 4.0)


def test_cli_prints_json_and_exits_zero():
    out = subprocess.run(
        [sys.executable, "-m", "optiflow.plan_summary", "--dxf", str(ROOM), "--config", str(CFG)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr[-400:]
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["room_count"] == 1
    assert data["checks_passed"] == 5  # 没给灯具图 → 「识别到灯位」不通过


def test_cli_reports_failure_with_nonzero_exit():
    out = subprocess.run(
        [sys.executable, "-m", "optiflow.plan_summary", "--dxf", str(CFG), "--config", str(CFG)],
        capture_output=True, text=True,
    )
    assert out.returncode != 0
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["ok"] is False
    assert "常见原因" in data["error"]
