"""动作注册表：表本身自洽、派发会跑、错法都带可行动信息。

口径：
- 表是「唯一动作入口」的可执行版本 —— 每条都要能列出、能调用、能说清返回什么；
- 参数多给/少给都必须报错（不静默忽略、不静默补默认）；
- 失败抛异常，CLI 收成 `{ok:false, error}` 并给非 0 退出码（shell 层判得出）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from optiflow.commands import (COMMANDS, CommandError, call, get, list_commands, main)

FIX = Path(__file__).resolve().parent / "fixtures"
ROOM = FIX / "sample_room.dxf"
LUM = FIX / "sample_lighting.dxf"
CFG = FIX / "sample_parse_config.json"


# ------------------------------------------------------------------ 表自洽
def test_table_is_self_describing():
    specs = list_commands()
    assert len(specs) >= 5
    for s in specs:
        assert s["id"] and s["description"] and s["returns"], s
        assert s["params"] is not None


def test_ids_sorted_and_unique():
    ids = [s["id"] for s in list_commands()]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)) == len(COMMANDS)


def test_every_param_declares_type():
    allowed = {"str", "int", "float", "bool", "json", "path", "markdown"}
    for cmd in COMMANDS.values():
        for p in cmd.params:
            assert p.type in allowed, f"{cmd.id}.{p.name} 类型 {p.type} 不在白名单"


# ------------------------------------------------------------------ 派发
def test_plan_summary_via_registry():
    r = call("plan.summary", {"dxf": str(ROOM), "dxf_lum": str(LUM), "config": str(CFG), "ceil_h": 4.0})
    assert r["room_count"] == 1
    assert r["vertices_total"] == 17
    assert r["area_total_m2"] == 116.03
    assert r["luminaires_total"] == 28
    assert r["checks_passed"] == 6


def test_report_assemble_via_registry():
    pkg = {"meta": {}, "rooms": [{"id": "R1", "area_m2": 116.03, "height_m": 3.0}], "luminaires": []}
    r = call("report.assemble", {"package": pkg})
    assert r["chars"] > 200
    assert "1. 灯具选型列表" in r["markdown"]


def test_ir_validate_via_registry():
    r = call("ir.validate", {"ir": {"storeys": []}})
    assert "count" in r and "violations" in r


def test_service_capabilities_lists_commands():
    r = call("service.capabilities")
    ids = {c["id"] for c in r["commands"]}
    assert {"plan.summary", "report.assemble"} <= ids


def test_render_docx_reports_missing_pandoc_clearly(tmp_path):
    out = tmp_path / "x.docx"
    try:
        r = call("report.render_docx", {"markdown": "# 标题\n正文", "out": str(out)})
        assert Path(r["docx"]).is_file()          # 装了 pandoc 就走这条
    except Exception as exc:                      # 没装就要求报错说人话
        assert "pandoc" in str(exc).lower() or "pypandoc" in str(exc).lower(), str(exc)


# ------------------------------------------------------------------ 错法
def test_unknown_command_lists_available():
    with pytest.raises(CommandError) as e:
        call("nope.nope")
    assert "可用" in str(e.value)
    assert "plan.summary" in str(e.value)


def test_missing_required_param_names_it():
    with pytest.raises(CommandError) as e:
        call("plan.summary", {"config": str(CFG)})
    assert "dxf" in str(e.value)


def test_extra_param_is_rejected_not_ignored():
    with pytest.raises(CommandError) as e:
        call("ir.validate", {"ir": {"storeys": []}, "typo": 1})
    assert "typo" in str(e.value)


def test_param_type_error_is_clear():
    with pytest.raises(CommandError) as e:
        call("plan.summary", {"dxf": str(ROOM), "config": str(CFG), "ceil_h": "四米"})
    assert "ceil_h" in str(e.value)


def test_action_failure_propagates():
    with pytest.raises(Exception):
        call("plan.summary", {"dxf": str(CFG), "config": str(CFG)})  # 拿 JSON 当 DXF


# -------------------------------------------------------------------- CLI
def _cli(*args: str):
    return subprocess.run([sys.executable, "-m", "optiflow.commands", *args],
                          capture_output=True, text=True)


def test_cli_list():
    out = _cli("list")
    assert out.returncode == 0
    ids = [c["id"] for c in json.loads(out.stdout)]
    assert "plan.summary" in ids


def test_cli_show():
    out = _cli("show", "plan.summary")
    assert out.returncode == 0
    spec = json.loads(out.stdout)
    assert spec["id"] == "plan.summary"
    assert {p["name"] for p in spec["params"]} >= {"dxf", "config"}


def test_cli_call_with_kv():
    out = _cli("call", "plan.summary", f"dxf={ROOM}", f"config={CFG}", "ceil_h=4.0")
    assert out.returncode == 0, out.stdout + out.stderr
    payload = json.loads(out.stdout)
    assert payload["ok"] is True
    assert payload["result"]["room_count"] == 1


def test_cli_call_failure_exit_code():
    out = _cli("call", "plan.summary", f"dxf={ROOM}")   # 缺 config
    assert out.returncode != 0
    payload = json.loads(out.stdout)
    assert payload["ok"] is False and "config" in payload["error"]


def test_get_returns_command_object():
    cmd = get("plan.summary")
    assert cmd.id == "plan.summary" and callable(cmd.handler)


# ------------------------------------------------- 与 MCP 工具表同源（不做第二份手写表）
def test_mcp_tools_include_registry_commands():
    from optiflow.mcp import TOOLS, TOOLS_BY_NAME

    ids = {c["id"] for c in list_commands()}
    names = {t["name"] for t in TOOLS}
    assert ids <= names, f"MCP 表缺：{sorted(ids - names)}"
    # 生成的工具带 handler，且 schema 里有必填项
    tool = TOOLS_BY_NAME["plan.summary"]
    assert callable(tool["handler"])
    assert set(tool["inputSchema"]["required"]) == {"dxf", "config"}


def test_mcp_tool_names_are_unique():
    from optiflow.mcp import TOOLS

    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names)), "MCP 工具名重复（手写表与注册表撞名了）"


def test_mcp_registry_tool_dispatches(tmp_path):
    """走 MCP 的 tools/call 调注册表动作，真的能出结果。"""
    from optiflow.mcp import handle_message
    from optiflow.service import build_default_service

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "plan.summary",
                      "arguments": {"dxf": str(ROOM), "config": str(CFG)}}}
    reply = handle_message(build_default_service(output_root=tmp_path), msg)
    assert reply is not None and "result" in reply
    text = reply["result"]["content"][0]["text"]
    assert "room_count" in text or "checks" in text


# ------------------------------------------------- 指标板作为数据（Windows 视图与断言都吃它）
#: 内置合成样本：不依赖任何外部数据文件，也让测试能在任何机器上跑
SAMPLE_PKG = {
    "meta": {},
    "rooms": [{"id": "R1", "area_m2": 100.0, "height_m": 3.0}],
    "workplane": {"illuminance": {"avg_lx": 825.0, "uniformity_u0": 0.4}, "ugr": None},
    "metrics_extra": {"ra": 100.0, "cct_k": 3000.0, "lpd_w_m2": 13.53},
    "luminaires": [],
}
SAMPLE_CRIT = {"scenes": {"S1": {"criteria": [
    {"metric": "illuminance_avg", "op": ">=", "value": 750, "unit": "lx",
     "standard": "GB/T 50034-2024", "table": "表 5.3.2"},
    {"metric": "ugr", "op": "<=", "value": 19, "unit": "—",
     "standard": "GB/T 50034-2024", "table": "表 5.3.2"},
    {"metric": "uniformity_u0", "op": ">=", "value": 0.6, "unit": "—",
     "standard": "GB/T 50034-2024", "table": "表 5.3.2"},
    {"metric": "ra", "op": ">=", "value": 80, "unit": "—",
     "standard": "GB/T 50034-2024", "table": "表 5.3.2"},
    {"metric": "lpd_target", "op": "<=", "value": 6.5, "unit": "W/m²",
     "standard": "GB/T 50034-2024", "table": "表 6.3.5"},
]}}}


def test_metrics_rows_shape_and_order():
    from optiflow.report_assembler import metrics_rows

    pkg, crit = SAMPLE_PKG, SAMPLE_CRIT
    rows = metrics_rows(pkg, crit)
    assert [r["label"] for r in rows][0] == "维持平均照度 Ē"
    assert len(rows) == 6
    first = rows[0]
    assert first["valueText"] == "825 lx" and first["verdict"] == "达标"
    assert "GB/T 50034-2024" in first["source"]
    # 无判据表时：阈值/判定措辞与 JS 侧一致（这条是 2026-10-13 断言抓出的差异的回归钉）
    no_crit = metrics_rows(pkg, None)
    assert {r["threshold"] for r in no_crit} == {"—（标准未规定）"}


def test_report_metrics_command():
    r = call("report.metrics", {"package": SAMPLE_PKG})
    assert r["count"] == 6
    assert r["rows"][1]["label"] == "统一眩光值 UGR"
    assert r["rows"][1]["valueText"] in ("未取证",) or isinstance(r["rows"][1]["value"], float)


# --------------------------------------------------- 外部注册（配套壳把自己那几步放进同一张表）
def test_register_external_command():
    from optiflow.commands import Command, CommandError, Param, register

    def handler(*, path: str) -> dict:
        return {"echo": path}

    cmd = Command(id="test.echo", handler=handler, group="test",
                  description="测试用", returns="{echo}",
                  params=(Param("path", "path", help="随便一个路径"),))
    register(cmd)
    try:
        assert "test.echo" in {c["id"] for c in list_commands()}
        assert call("test.echo", {"path": "/tmp/x"}) == {"echo": "/tmp/x"}
        # 默认不许覆盖
        try:
            register(cmd)
            raise AssertionError("重复注册竟然成功了")
        except CommandError as e:
            assert "已存在" in str(e)
        # 显式 replace 才能覆盖
        register(Command(id="test.echo", handler=lambda **kw: {"replaced": True},
                         description="覆盖", returns="{}"), replace=True)
        assert call("test.echo", {}) == {"replaced": True}
    finally:
        COMMANDS.pop("test.echo", None)


def test_registered_command_visible_in_cli_list():
    out = _cli("list")
    assert out.returncode == 0
    assert "plan.summary" in [c["id"] for c in json.loads(out.stdout)]
