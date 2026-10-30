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

from optiflow.commands import (COMMANDS, CommandError, call, get, list_commands)

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
    assert r["checks_passed"] == 7                      # 并集：六项 ＋ 轮廓点≥3（刀B）
    assert r["furniture"]["count"] == 26 and r["main_room"]["points"] == 17  # 刀B 三块语义进契约


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


def test_param_type_error_names_action_and_param():
    """报错要说清「哪个动作的哪个参数」。

    以前写成 `动作 {param.name} 参数类型不对`，参数名占着动作名的位置，模型读到的
    是「有个叫 package 的动作」——连是在哪一步填错都不知道。
    """
    with pytest.raises(CommandError) as e:
        call("plan.summary", {"dxf": str(ROOM), "config": str(CFG), "ceil_h": "四米"})
    msg = str(e.value)
    assert "plan.summary" in msg and "ceil_h" in msg


def test_missing_json_file_is_command_error_with_action_and_param(tmp_path):
    """json 参数指向不存在的文件：必须是 CommandError（ValueError 系），不是裸 OSError。

    裸 FileNotFoundError 会穿过工具层兜底，变成 JSON-RPC -32603，模型以为服务器崩了。
    """
    ghost = tmp_path / "not_here.json"
    with pytest.raises(CommandError) as e:
        call("report.assemble", {"package": str(ghost)})
    msg = str(e.value)
    assert isinstance(e.value, ValueError)          # HTTP 层靠这个映射 400
    assert "report.assemble" in msg and "package" in msg
    assert "not_here.json" in msg
    assert "绝对路径" in msg or "已解析的对象" in msg   # 教它下一步能怎么填


def test_unreadable_json_param_is_command_error(tmp_path):
    """读不了的文件（这里拿目录当文件）：同样是 CommandError，带动作、参数、路径。"""
    with pytest.raises(CommandError) as e:
        call("report.metrics", {"package": str(tmp_path)})
    msg = str(e.value)
    assert "report.metrics" in msg and str(tmp_path) in msg


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


def test_mcp_missing_path_param_is_tool_error_not_protocol_error(tmp_path):
    """`path` 类参数指向不存在的文件，也得是 isError 的工具结果，不能是 -32603。

    MCP 约定：工具自己能说清的错走 result ＋ isError，模型读到原因还能自己改正；
    -32603 是协议故障信号，很多客户端看到就当「工具崩了」放弃重试。
    """
    from optiflow.mcp import handle_message
    from optiflow.service import build_default_service

    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
           "params": {"name": "plan.summary",
                      "arguments": {"dxf": str(tmp_path / "ghost.dxf"), "config": str(CFG)}}}
    reply = handle_message(build_default_service(output_root=tmp_path), msg)
    assert reply is not None and "error" not in reply, "落进协议层了：-32603 会让模型以为工具崩了"
    assert reply["result"]["isError"] is True
    text = reply["result"]["content"][0]["text"]
    assert "ghost.dxf" in text, text


# ------------------------------------------- 参数声明（default / enum / json 形状）要发出去
def test_mcp_schema_carries_param_default():
    """注册表里声明的 default 必须落到 MCP schema，否则模型看不见，就无法「只说变的那部分」。"""
    from optiflow.mcp import TOOLS_BY_NAME

    props = TOOLS_BY_NAME["plan.select_luminaire"]["inputSchema"]["properties"]
    assert props["limit"]["default"] == 5
    props = TOOLS_BY_NAME["report.assemble"]["inputSchema"]["properties"]
    assert props["project_name"]["default"] == "会议室照明设计"


def test_mcp_schema_carries_enum_for_closed_sets():
    from optiflow.commands import Command, Param
    from optiflow.mcp import _json_schema

    cmd = Command(id="probe.enum", handler=lambda **_: {}, group="meta",
                  params=(Param("mode", "str", help="模式", enum=("a", "b")),))
    assert _json_schema(cmd)["properties"]["mode"]["enum"] == ["a", "b"]


def test_spec_shape_unchanged_by_enum():
    """enum 只进 MCP schema；`spec()` 是 HTTP/CLI 的对外形状，不能多/少字。"""
    from optiflow.commands import Command, Param

    cmd = Command(id="probe.shape", handler=lambda **_: {}, group="meta",
                  params=(Param("mode", "str", help="模式", enum=("a", "b")),))
    assert set(cmd.spec()["params"][0]) == {"name", "type", "required", "default", "help"}


def test_mcp_schema_json_accepts_object_array_or_path_string():
    """json 参数实情上吃对象、数组（如 images）、以及 JSON 文件路径。

    只写 object 会把后两种在 schema 上判成非法，与 help 文案和 `_coerce` 自相矛盾。
    """
    from optiflow.mcp import TOOLS_BY_NAME

    props = TOOLS_BY_NAME["report.assemble_from_pdf"]["inputSchema"]["properties"]
    assert set(props["package" if "package" in props else "criteria"]["type"]) >= {"object", "string"}
    assert "string" in props["images"]["type"], "images 能给路径字符串"
    assert "array" in props["images"]["type"], "images 能直接给列表"


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
