"""图纸摘要（`optiflow.plan_summary`）：四数不漂移、七项检查逐条给结论、坏图纸报错带上下文。

刀B（2026-10-19）后：`summarize_ir` 是 IR→摘要的唯一口径入口（壳侧 core.summarize 薄壳转调），
并集后检查项 = 平台六项 ＋ Windows 独有的「房间轮廓点 ≥ 3」（已拍板）；三块补进来的语义
（家具双口径 / bbox 与净高 / 单主房）各有一条断言。

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


def test_union_seven_checks_each_gives_verdict():
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    assert len(r["checks"]) == 7                      # 并集：六项 ＋ 轮廓点≥3
    assert r["checks_passed"] == 7
    assert all(c["passed"] and c["detail"] for c in r["checks"])
    assert any(c["name"] == "房间轮廓点 ≥ 3" for c in r["checks"])


def test_without_luminaire_dxf_zero_and_lum_check_fails():
    r = summarize_plan(str(ROOM), None, str(CFG), 4.0)
    assert r["luminaires_total"] == 0
    assert r["checks_passed"] == 6
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
    assert data["checks_passed"] == 6  # 没给灯具图 → 「识别到灯位」不通过（七项里挂 1 项）


def test_cli_reports_failure_with_nonzero_exit():
    out = subprocess.run(
        [sys.executable, "-m", "optiflow.plan_summary", "--dxf", str(CFG), "--config", str(CFG)],
        capture_output=True, text=True,
    )
    assert out.returncode != 0
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["ok"] is False
    assert "常见原因" in data["error"]


# ── 刀B 新增：三块语义 ＋ IR 纯入口（实测值 2026-10-19，fixture 实跑钉死）────────

def test_furniture_double_caliber_excluded_linearc():
    """① 家具双口径：候选 / 采用 / 排除（柜架 id 清单），与家具候选数同源。"""
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    f = r["furniture"]
    assert f["count"] == r["furniture_candidates"] == 26
    assert f["excluded"] == 7
    assert f["adopted"] == f["count"] - f["excluded"] == 19
    assert f["excluded_ids"] == ["001", "002", "003", "012", "014", "016", "018"]
    assert len(f["names"]) == 26


def test_room_bbox_and_main_room_blocks():
    """② 每房 bbox/净高 ③ 单主房块（含主房口径的灯位三数）。"""
    r = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    room = r["rooms"][0]
    assert room["bbox_m"] == [9.57, 12.971]
    assert "ceiling_h_m" in room
    m = r["main_room"]
    assert m["name"] == "WALLRING_000_E18_2D1F"
    assert m["points"] == 17 and m["area_m2"] == 116.035
    assert m["bbox_m"] == [9.57, 12.971]
    assert m["luminaires"] == {"total": 28, "linear": 12, "point": 16}
    # 净高口径：灯位 z=0 视为未赋值 → 回落 space.ceil_h（本组 fixture 配置 default_ceil_h=2.8）
    assert m["ceiling_h_m"] == 2.8
    assert room["ceiling_h_m"] == 2.8


def test_summarize_ir_is_pure_entry_and_agrees_with_plan():
    """IR 纯入口：不读文件、不跑校验（viols=[]）也能出摘要，关键块与计划入口逐字一致。"""
    from optiflow.adapters.dialux.dxf import ParseConfig, parse_dxf
    from optiflow.plan_summary import summarize_ir

    cfg = ParseConfig.from_json(str(CFG))
    ir = parse_dxf(str(ROOM), cfg)
    pure = summarize_ir(ir, viols=[])
    planned = summarize_plan(str(ROOM), str(LUM), str(CFG), 4.0)
    # 挂灯前后灯位数不同（纯入口没跑 join），房间/家具/主房口径必须同源
    assert pure["furniture"] == planned["furniture"]
    assert pure["rooms"] == planned["rooms"]
    # 主房选择：先排除家具伪空间（name 前缀「家具_」/ id 前缀 INSERT_、furniture_），
    # 再按面积最大挑。2026-10-28 变更（原口径为「全部 space 里顶点最多」，会把 22 点的
    # 家具伪空间 LINEARC_022 当主房；半圆两室一厅实测主房被选成 6.24 m² 的半圆房，
    # 界面看着像另一间房丢了）。挂灯前后现在同口径，都指向真房。
    assert pure["main_room"]["points"] == 17
    assert pure["main_room"]["name"] == "WALLRING_000_E18_2D1F"
    # 挂灯后的主房必须是真房间（与计划入口同口径）
    assert planned["main_room"]["name"] == "WALLRING_000_E18_2D1F"
    assert pure["validation"] == {"counts": {}, "HALT": [], "real": [],
                                  "mount_pending_count": 0, "furniture_noise_count": 0}
    assert {c["name"] for c in pure["checks"]} == {c["name"] for c in planned["checks"]}
