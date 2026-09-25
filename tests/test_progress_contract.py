"""进度契约与 UIA 驱动层的测试。

进度契约（P1）：ActionPlan 每条动作必须带 title 与 weight，界面才能显示
「第 17／55 条，正在布灯」这种真进度。
UIA 驱动（P0）：不碰真机也要能钉住那些让我们付出真机代价的约束——
.ps1 必须纯 ASCII、STEPS 表与脚本里 Emit 的名字必须一一对应。
"""
import io
import re
from pathlib import Path

import pytest

from src.executor.uia.driver import (
    AUTOSAVE_PS1,
    DRIVER_PS1,
    STEPS,
    TOTAL_WEIGHT,
    StepEvent,
    _parse,
    auto_answer_save_prompt,
    succeeded,
)
from src.planner.core import ACTION_META, build_action_plan, plan_total_weight


def _ir(n_lums=2, n_spaces=1):
    return {
        "project": {"name": "T"},
        "storeys": [{
            "level": 1,
            "elevation": 0.0,
            "spaces": [{
                "id": f"S{i}",
                "name": f"房间{i}",
                "polygon": [[0, 0], [6, 0], [6, 6], [0, 6]],
                "ceil_h": 2.8,
                "luminaires": [
                    {"symbol": f"L{j}", "x": 1.0 + j, "y": 1.0, "z": 2.8,
                     "catalog_match": True, "kind": "point"}
                    for j in range(n_lums)
                ],
            } for i in range(n_spaces)],
        }],
    }


# ---------------------------------------------------------------- 进度契约

def test_every_action_has_title_and_weight():
    plan = build_action_plan(_ir())
    assert plan
    for a in plan:
        assert a["title"], f"{a['id']} {a['type']} 缺 title"
        assert isinstance(a["weight"], float), f"{a['id']} weight 不是 float"
        assert a["weight"] >= 0.0


def test_title_is_human_readable_not_the_raw_type():
    """title 是给人看的，不能等于内部类型名（否则界面上全是英文下划线）。"""
    plan = build_action_plan(_ir())
    for a in plan:
        if a["type"] in ACTION_META:
            assert a["title"] != a["type"], f"{a['type']} 的 title 没翻译"


def test_total_weight_positive_and_matches_sum():
    plan = build_action_plan(_ir(n_lums=5))
    total = plan_total_weight(plan)
    assert total > 0
    assert total == pytest.approx(sum(a["weight"] for a in plan))


def test_human_confirm_weighs_zero():
    """等人确认不算机器干的活，权重必须是 0，否则进度条会因为等人而虚涨。"""
    halt = [{"severity": "HALT", "code": "X", "message": "m"}]
    plan = build_action_plan(_ir(), halt_violations=halt)
    hc = [a for a in plan if a["type"] == "human_confirm"]
    assert hc, "构造了 HALT 却没插入 human_confirm"
    assert all(a["weight"] == 0.0 for a in hc)


def test_progress_reaches_exactly_100_over_the_whole_plan():
    """按权重累加走完整个计划必须正好 100%，不能 99.7 也不能 100.3。"""
    plan = build_action_plan(_ir(n_lums=7, n_spaces=3))
    total = plan_total_weight(plan)
    done = 0.0
    for a in plan:
        done += a["weight"]
    assert done / total * 100.0 == pytest.approx(100.0)


def test_unknown_action_type_degrades_instead_of_crashing():
    from src.planner.core import _stamp_progress
    actions = [{"id": "a1", "type": "brand_new_op"}]
    _stamp_progress(actions)
    assert actions[0]["title"] == "brand_new_op"
    assert actions[0]["weight"] == 1.0


def test_kernel_plan_driven_progress_contract():
    """P2：进度由 ActionPlan 权重驱动（kernel.execute_plan 的 PlanEvent），
    不再依赖手写步骤表。真实 55 条量级 plan：progress 单调 0→100。"""
    from src.executor.kernel import execute_plan
    from src.executor.uia.driver_plan import UiaDriver

    ir = {
        "project": {"name": "T"},
        "storeys": [{"level": 1, "elevation": 0.0, "spaces": [{
            "id": "S1", "name": "房间1",
            "polygon": [[0, 0], [6, 0], [6, 6], [0, 6]],
            "ceil_h": 2.8,
            "luminaires": [
                {"symbol": f"L{j}", "x": 1.0 + j, "y": 1.0, "z": 2.8,
                 "catalog_match": True, "kind": "point"}
                for j in range(28)
            ],
        }]}],
    }
    plan = build_action_plan(ir)
    assert len(plan) >= 32  # 28 盏 → 32+ 条

    def fake_import(stf_path):
        return []

    driver = UiaDriver(ir, import_fn=fake_import)
    events = execute_plan(plan, driver)

    assert len(events) == len(plan)
    # 进度单调不减，最后正好 100
    progresses = [e.progress for e in events]
    assert all(b >= a for a, b in zip(progresses, progresses[1:]))
    assert progresses[-1] == pytest.approx(100.0)
    # 每条事件都带 title 与 weight（界面显示「第 N 条，正在做什么」）
    for e in events:
        assert e.title, f"{e.action['type']} 缺 title"
        assert e.weight >= 0.0
        assert e.total == len(plan)
    # 28 盏灯全被派发到 place_luminaire stub
    assert len(driver.luminaires) == 28


# ---------------------------------------------------------------- UIA 驱动

def test_driver_ps1_is_pure_ascii():
    """PowerShell 5.1 把 UTF-8 的 .ps1 当 GBK 读，一个中文字符就会在后面某个
    花括号处炸出 ParserError。这条测试是那次事故的护栏。"""
    raw = DRIVER_PS1.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    assert not bad, f"dialux_driver.ps1 含 {len(bad)} 个非 ASCII 字节，首个在偏移 {bad[0][0]}"


def test_steps_table_matches_what_the_script_emits():
    """STEPS 与 .ps1 里 Emit 的步骤名必须一一对应，否则进度条会漏步或算错分母。"""
    src = io.open(DRIVER_PS1, encoding="ascii").read()
    emitted = set(re.findall(r"(?:Emit|Fail)\s+'([a-z0-9-]+)'", src))
    declared = {name for name, _, _ in STEPS}
    assert emitted == declared, (
        f"脚本发出但 STEPS 没登记：{sorted(emitted - declared)}；"
        f"STEPS 登记但脚本不发：{sorted(declared - emitted)}"
    )


def test_steps_weights_sum_to_total():
    assert TOTAL_WEIGHT == pytest.approx(sum(w for _, _, w in STEPS))
    assert TOTAL_WEIGHT > 0


def test_parse_step_lines():
    assert _parse("STEP attach ok pid=123") == ("attach", True, "pid=123")
    assert _parse("STEP save fail button-not-found") == ("save", False, "button-not-found")
    assert _parse("STEP done ok") == ("done", True, "")
    assert _parse("") is None
    assert _parse("随便一行日志") is None
    assert _parse("STEP incomplete") is None


def test_succeeded_requires_done_as_last_step():
    ok = [StepEvent("attach", "挂上", True, "", 50.0),
          StepEvent("done", "完成", True, "", 100.0)]
    assert succeeded(ok)
    # 全 ok 但没跑到 done —— 半途而废不算成功
    assert not succeeded([StepEvent("attach", "挂上", True, "", 50.0)])
    # 跑到 done 但中间有失败
    bad = [StepEvent("save", "保存", False, "x", 90.0),
           StepEvent("done", "完成", True, "", 100.0)]
    assert not succeeded(bad)
    assert not succeeded([])


def test_driver_ps1_ships_with_the_package():
    """脚本必须跟包一起走，不能只在开发机上存在。"""
    assert DRIVER_PS1.exists()
    assert DRIVER_PS1.parent == Path(__file__).resolve().parent.parent / "src" / "executor" / "uia"


# ---------------------------------------------------------------- 保存弹窗自动应答

def test_autosave_ps1_is_pure_ascii():
    """autosave.ps1 同 dialux_driver.ps1 的约束：PS 5.1 按 GBK 读，禁 CJK。"""
    raw = AUTOSAVE_PS1.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    assert not bad, f"autosave.ps1 含 {len(bad)} 个非 ASCII 字节，首个在偏移 {bad[0][0]}"


def test_autosave_ps1_ships_with_the_package():
    assert AUTOSAVE_PS1.exists()
    assert AUTOSAVE_PS1.parent == DRIVER_PS1.parent


def test_auto_answer_parses_saved_count(monkeypatch):
    """脚本输出 'STEP find-save-dialog ok hwnd=.. saved=N' 时返回 N。"""
    import subprocess

    class FakeProc:
        stdout = "STEP find-save-dialog ok hwnd=123 saved=2\nSTEP answer-save-dialog ok\n"

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: FakeProc(),
    )
    assert auto_answer_save_prompt() == 2


def test_auto_answer_zero_when_no_popup(monkeypatch):
    """无保存框（脚本输出无 saved=）→ 返回 0，不抛。"""
    import subprocess

    class FakeProc:
        stdout = "STEP answer-save-dialog ok\n"

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: FakeProc(),
    )
    assert auto_answer_save_prompt() == 0


def test_auto_answer_returns_zero_on_script_error(monkeypatch):
    """脚本抛异常（进程没起/超时）→ 返回 0，不把整个流程带崩。"""
    import subprocess

    def boom(*a, **k):
        raise OSError("powershell missing")

    monkeypatch.setattr(subprocess, "run", boom)
    assert auto_answer_save_prompt() == 0
