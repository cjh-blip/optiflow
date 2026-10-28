# -*- coding: utf-8 -*-
"""对话层语义路由：一句话 → 动作名 ＋ **带参数**（壳侧 command_router 的短板补在这里）。

现状（2026-10-28 核过）：壳侧 `standalone/command_router.route()` 只出一句命令名，
`params` 恒为 `{}`——因为它的用途是「少点两下按钮」，参数让人在界面上填。对话层不一样：
话里说过的数字与条件得被听进去，否则用户说「这间 20 平米的会议室布灯，4000K」，
系统回了「好的，选灯」然后四个格子全空，那不叫智能体。

四条硬约束（都不许松）：
① **路径类参数不从话里猜**。「这张图纸」「那个结果包」映射不到文件，进 `missing` 点名要。
② **参数来源必须交代**。`sources` 记录每项来自 text / context / context.ir / context.criteria，
   让人（和 AI）能查「这个 300 lx 是你说过的还是系统自己加的」。
③ 抽不到就是抽不到，不填默认值冒充用户意图；默认值由动作自身在调用时补。
④ 无命中给命令清单，不猜。
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from optiflow.agent_router import route  # noqa: E402


def test_extracts_area_and_cct_from_sentence() -> None:
    """话里说过的数字要被听进去，目标照度从上下文补。"""
    out = route("给这间 20 平米的会议室布灯，4000K", context={"target_lux": 300})
    assert out["command"] == "plan.select_luminaire"
    assert out["params"]["area_m2"] == 20.0
    assert out["params"]["cct_k"] == 4000
    assert out["params"]["target_lux"] == 300
    assert out["sources"]["area_m2"] == "text"
    assert out["sources"]["cct_k"] == "text"
    assert out["sources"]["target_lux"] == "context"
    assert out["missing"] == []


def test_missing_required_params_named() -> None:
    """光说「选灯」不给数：必填项进 missing，一个都不许静默补。"""
    out = route("帮我选灯")
    assert out["command"] == "plan.select_luminaire"
    assert "area_m2" in out["missing"]
    assert "target_lux" in out["missing"]
    assert out["params"] == {}


def test_area_inferred_from_ir_when_not_spoken() -> None:
    """上下文里有 IR：面积从主房算，来源标 context.ir。"""
    ir = {"storeys": [{"spaces": [
        {"id": "r", "name": "2F", "polygon": [[0, 0], [4, 0], [4, 5], [0, 5]], "luminaires": []},
        {"id": "f", "name": "家具_CHAIR", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
         "luminaires": []},
    ]}]}
    out = route("给主房布灯", context={"ir": ir, "target_lux": 300})
    assert out["params"]["area_m2"] == 20.0
    assert out["sources"]["area_m2"] == "context.ir"


def test_target_lux_from_criteria_context() -> None:
    """没有任何手填上下文时，从判据表取目标照度（会议室 5.3.2 → 300 lx）。"""
    crit = {"scenes": {"meeting_room": {"criteria": [
        {"metric": "illuminance_avg", "op": ">=", "value": 300, "unit": "lx"},
    ]}}}
    out = route("20 平米的房间要布灯", context={"criteria": crit})
    assert out["params"]["target_lux"] == 300
    assert out["sources"]["target_lux"] == "context.criteria"


def test_paths_are_never_guessed() -> None:
    """「这张图纸」不映射到任何文件：进 missing，别猜。"""
    out = route("解析这张图纸")
    assert out["command"] == "dialux.parse"
    assert "dxf" in out["missing"]


def test_no_match_returns_command_list() -> None:
    out = route("今天天气不错")
    assert out["command"] is None
    assert out["available"]
    assert "params" not in out or out["params"] == {}


def test_empty_text_is_not_a_crash() -> None:
    out = route("")
    assert out["command"] is None and out["available"]
