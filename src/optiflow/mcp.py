r"""MCP server —— 把平台挂成 AI agent 的工具集。

实现范围（说清楚，不含糊）：

- **传输**：stdio，一行一条 JSON-RPC 2.0 消息（MCP 的 stdio 传输约定）；
- **方法**：`initialize` / `notifications/initialized` / `ping` / `tools/list` / `tools/call`。

这是 MCP 的 tools 子集，**不等于覆盖全部规范**（没有 resources / prompts / sampling /
roots，也没有 stdio 之外的传输）。够 agent 拿工具用了；要更多再补。

协议处理与传输分开：`handle_message(service, message)` 是纯函数，
所以测试不需要起 stdin/stdout 管道就能把协议行为钉住。

运行（MCP 客户端通常自己拉起来）：

    D:\dev\anaconda3\python.exe -m optiflow.mcp
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from .service import PlatformService, build_default_service

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "optiflow"
SERVER_VERSION = "0.0.1"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _rectangle_outline(width: float, depth: float) -> List[Dict[str, float]]:
    return [{"x": 0.0, "y": 0.0}, {"x": width, "y": 0.0},
            {"x": width, "y": depth}, {"x": 0.0, "y": depth}]


def lighting_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """把「矩形房间 + 目标照度」这种人的说法，翻成平台的 TaskSpec。

    工具层允许比 IR 更啰嗦、更贴近自然语言——IR 要保持最小共享语义，
    翻译的活在这一层做。
    """
    width = float(args["width"])
    depth = float(args["depth"])
    flux = float(args["flux"])
    height = float(args.get("height", 3.0))
    target = float(args.get("target_lux", 500.0))
    work_plane = float(args.get("work_plane", 0.75))
    if width <= 0 or depth <= 0:
        raise ValueError(f"房间尺寸必须为正：width={width}, depth={depth}")
    if flux <= 0:
        raise ValueError(f"单灯光通量必须为正：flux={flux}")

    constraints = [{"name": "illuminance_avg", "target": target, "unit": "lx"}]
    if args.get("uniformity_u0") is not None:
        constraints.append({"name": "uniformity_u0",
                            "target": float(args["uniformity_u0"]), "unit": ""})

    return {
        "kind": "layout",
        "spaces": [{
            "id": str(args.get("space_id", "room-1")),
            "name": str(args.get("space_name", "房间")),
            "geometry": {"kind": "room", "height": height,
                         "outline": _rectangle_outline(width, depth)},
            "work_plane": work_plane,
            "reflectance": {
                "ceiling": float(args.get("ceiling_reflectance", 0.70)),
                "wall": float(args.get("wall_reflectance", 0.50)),
                "floor": float(args.get("floor_reflectance", 0.20)),
            },
        }],
        "fixtures": [],
        "constraints": constraints,
        "extra": {"flux": flux,
                  "fixture_name": str(args.get("fixture_name", "灯具"))},
    }


def _tool_list_capabilities(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"capabilities": service.capabilities()}


def _tool_list_pipelines(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"pipelines": service.pipelines()}


def _tool_plan_lighting(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return service.run_sync("lighting_plan", lighting_task(args))


def _tool_submit(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    job_id = service.submit(str(args["pipeline"]), args["task"])
    return {"job_id": job_id, "status": "queued"}


def _tool_job_status(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return service.status(str(args["job_id"]))


def _tool_job_result(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return service.result(str(args["job_id"]))


def _tool_job_cancel(service: PlatformService, args: Dict[str, Any]) -> Dict[str, Any]:
    return service.cancel(str(args["job_id"]))


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "optiflow_capabilities",
        "description": ("列出平台装了哪些适配器，以及各自【做不到什么】。"
                        "limits 是能力声明的一部分，规划任务前先读它，别把做不到的当承诺。"),
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
        "handler": _tool_list_capabilities,
    },
    {
        "name": "optiflow_pipelines",
        "description": "列出可用的流水线，以及每一步声明要走哪条通道（kind + tags）。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
        "handler": _tool_list_pipelines,
    },
    {
        "name": "optiflow_plan_lighting",
        "description": ("给一个矩形房间和目标平均照度，出布灯方案并落成 DIALux 可导入的 STF。"
                        "返回里含逐条校验结论、方案假设与模型边界；"
                        "结果是【预测方案】不是仿真结论，需要结论请再走 DIALux 校核。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "description": "房间宽度（米，X 方向）"},
                "depth": {"type": "number", "description": "房间进深（米，Y 方向）"},
                "flux": {"type": "number", "description": "单灯光通量（lm）"},
                "height": {"type": "number", "description": "灯具安装高度（米，距地面），默认 3.0"},
                "target_lux": {"type": "number", "description": "目标平均照度，默认 500"},
                "work_plane": {"type": "number", "description": "工作面高度，默认 0.75"},
                "uniformity_u0": {"type": "number",
                                  "description": "均匀度下限，默认 0.60（EN 12464-1 办公场所）"},
                "ceiling_reflectance": {"type": "number"},
                "wall_reflectance": {"type": "number"},
                "floor_reflectance": {"type": "number"},
                "fixture_name": {"type": "string"},
                "space_id": {"type": "string"},
                "space_name": {"type": "string"},
            },
            "required": ["width", "depth", "flux"],
        },
        "handler": _tool_plan_lighting,
    },
    {
        "name": "optiflow_submit",
        "description": "异步提交一条流水线，立刻返回 job_id；再用 optiflow_job_result 取结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pipeline": {"type": "string", "description": "流水线名，见 optiflow_pipelines"},
                "task": {"type": "object", "description": "TaskSpec（IR）"},
            },
            "required": ["pipeline", "task"],
        },
        "handler": _tool_submit,
    },
    {
        "name": "optiflow_job_status",
        "description": "查任务进度（status / percent / message）。",
        "inputSchema": {"type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"]},
        "handler": _tool_job_status,
    },
    {
        "name": "optiflow_job_result",
        "description": "取任务结果：每步的指标、产物路径、以及逐条校验结论。",
        "inputSchema": {"type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"]},
        "handler": _tool_job_result,
    },
    {
        "name": "optiflow_job_cancel",
        "description": ("请求取消任务。**粒度是步**：能在流水线的步与步之间停下，"
                        "停不下正在跑的那一步。"),
        "inputSchema": {"type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"]},
        "handler": _tool_job_cancel,
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _public_tools() -> List[Dict[str, Any]]:
    """tools/list 的对外形状：不带 handler。"""
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]} for t in TOOLS]


def _ok(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _text_result(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text",
                     "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def handle_message(service: PlatformService, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 消息；返回 None 表示这是通知、不该有响应。"""
    if not isinstance(message, dict):
        return _err(None, INVALID_REQUEST, "消息必须是 JSON 对象")
    if message.get("jsonrpc") != "2.0":
        return _err(message.get("id"), INVALID_REQUEST, "jsonrpc 必须是 \"2.0\"")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _err(request_id, INVALID_PARAMS, "params 必须是对象")

    if method is None:
        return _err(request_id, INVALID_REQUEST, "缺少 method")

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _ok(request_id, {
            "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return _ok(request_id, {})

    if method == "tools/list":
        return _ok(request_id, {"tools": _public_tools()})

    if method == "tools/call":
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _ok(request_id, _text_result(
                {"error": f"没有工具 {name!r}",
                 "available": sorted(TOOLS_BY_NAME)}, is_error=True))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _ok(request_id, _text_result(
                {"error": "arguments 必须是对象"}, is_error=True))
        try:
            payload = tool["handler"](service, arguments)
        except KeyError as exc:
            return _ok(request_id, _text_result(
                {"error": f"缺少参数或对象不存在：{exc}"}, is_error=True))
        except (ValueError, RuntimeError) as exc:
            return _ok(request_id, _text_result(
                {"error": f"{type(exc).__name__}: {exc}"}, is_error=True))
        return _ok(request_id, _text_result(payload))

    return _err(request_id, METHOD_NOT_FOUND, f"未实现的方法：{method}")


def serve_stdio(service: PlatformService, stdin=None, stdout=None) -> int:
    """stdio 传输：一行一条消息，读一条答一条（通知不回）。"""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[Dict[str, Any]] = _err(None, PARSE_ERROR,
                                                      f"JSON 解析失败：{exc}")
        else:
            try:
                response = handle_message(service, message)
            except Exception as exc:  # noqa: BLE001 - 兜底，不能让一行坏消息杀掉服务
                response = _err(message.get("id") if isinstance(message, dict) else None,
                                INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m optiflow.mcp",
                                     description="光枢/OptiFlow MCP server（stdio）")
    parser.add_argument("--self-check", action="store_true",
                        help="打印工具清单后退出（不进入 stdio 循环）")
    args = parser.parse_args(argv)
    service = build_default_service()
    if args.self_check:
        for tool in _public_tools():
            print(f"{tool['name']}: {tool['description']}")
        return 0
    return serve_stdio(service)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
