"""对外接口测试：服务门面 / HTTP / MCP。

三层分开测，因为它们是三件事：

- **服务门面**（service.py）：语义在这层，与传输无关；
- **HTTP**（api.py）：真起 socket、真发请求，不 mock urlopen，也不 mock handler；
- **MCP**（mcp.py）：协议处理是纯函数，直接喂消息；传输用 StringIO 走一遍真循环。

另外钉住两条安全边界：非回环地址默认拒绑、请求体有上限。
凡是「坏了没人会知道」的护栏，这里都配了会变红的用例。
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from optiflow.api import create_server, serve_in_thread
from optiflow.job import JobStatus
from optiflow.mcp import TOOLS_BY_NAME, _public_tools, handle_message, lighting_task, serve_stdio
from optiflow.service import PlatformService, build_default_registry, build_default_service
from optiflow.shell import PAGE_PATH, content_type_for, page_html

TASK = {
    "kind": "layout",
    "spaces": [{
        "id": "room-208", "name": "208 会议室",
        "geometry": {"kind": "room", "height": 3.0, "outline": [
            {"x": 0, "y": 0}, {"x": 11.9, "y": 0},
            {"x": 11.9, "y": 8.78}, {"x": 0, "y": 8.78}]},
        "work_plane": 0.75,
        "reflectance": {"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
    }],
    "constraints": [{"name": "illuminance_avg", "target": 500.0, "unit": "lx"}],
    "extra": {"flux": 3000.0},
}


@pytest.fixture()
def service(tmp_path: Path) -> PlatformService:
    return PlatformService(build_default_registry(tmp_path), project=None)


# ============================================================ 服务门面


def test_capabilities_carry_limits(service: PlatformService) -> None:
    caps = {c["name"]: c for c in service.capabilities()}
    assert set(caps) == {"lumen", "dialux"}
    assert caps["lumen"]["tags"] == ["algorithm"]
    assert caps["dialux"]["tags"] == ["file"]
    for cap in caps.values():
        assert cap["limits"], f"{cap['name']} 的 limits 为空——不声明限制就是虚假承诺"


def test_pipelines_expose_steps_and_channels(service: PlatformService) -> None:
    pipelines = {p["name"]: p for p in service.pipelines()}
    assert "lighting_plan" in pipelines
    steps = pipelines["lighting_plan"]["steps"]
    assert [s["name"] for s in steps] == ["plan", "export"]
    assert steps[0]["requires"] == ["algorithm"]
    assert steps[1]["requires"] == ["file"]


def test_run_sync_produces_validated_result(service: PlatformService) -> None:
    result = service.run_sync("lighting_plan", TASK)
    assert result["ok"] is True and result["completed"] is True
    assert [s["adapter"] for s in result["steps"]] == ["lumen", "dialux"]
    assert all(c["passed"] for c in result["checks"])
    assert Path(result["steps"][1]["artifacts"]["stf"]).exists()


def test_submit_is_async_then_resolves(service: PlatformService) -> None:
    started = time.monotonic()
    job_id = service.submit("lighting_plan", TASK)
    assert time.monotonic() - started < 0.5
    assert job_id.startswith("job-")
    deadline = time.monotonic() + 60
    while service.status(job_id)["status"] not in ("done", "failed", "cancelled"):
        assert time.monotonic() < deadline, "超时"
        time.sleep(0.02)
    status = service.status(job_id)
    assert status["status"] == "done" and status["percent"] == 100.0
    assert service.result(job_id)["ok"] is True


def test_unknown_pipeline_is_rejected_at_submit(service: PlatformService) -> None:
    """名字不对就当场报错，别等后台线程里才炸。"""
    with pytest.raises(KeyError, match="没有名为"):
        service.submit("nope", TASK)


def test_unknown_job_raises(service: PlatformService) -> None:
    with pytest.raises(KeyError, match="未知任务"):
        service.status("job-does-not-exist")


def test_cancel_before_start_marks_cancelled(service: PlatformService) -> None:
    job_id = service.submit("lighting_plan", TASK)
    service.cancel(job_id)
    assert service.status(job_id)["status"] == "cancelled"


def test_cancel_documents_its_granularity(service: PlatformService) -> None:
    """取消粒度是【步】——提示语必须说清楚，不能让人以为能瞬间中断。"""
    job_id = service.submit("lighting_plan", TASK)
    payload = service.cancel(job_id)
    assert "步" in payload["message"]


def test_task_missing_spaces_fails_the_job_with_a_readable_reason(service) -> None:
    # TaskSpec 的字段形状没问题（spaces 默认空列表），所以提交能过；
    # 语义问题在跑的时候暴露——关键是【原因要能被读到】，不能只说「失败了」。
    job_id = service.submit("lighting_plan", {"kind": "layout"})
    deadline = time.monotonic() + 60
    while service.status(job_id)["status"] not in ("done", "failed", "cancelled"):
        assert time.monotonic() < deadline, "超时"
        time.sleep(0.02)
    payload = service.status(job_id)
    assert payload["status"] == "failed"
    assert "没有空间" in payload["message"], payload["message"]


def test_default_service_builds(tmp_path: Path) -> None:
    svc = build_default_service(output_root=tmp_path)
    assert {c["name"] for c in svc.capabilities()} == {"lumen", "dialux"}


# ============================================================ HTTP


def _request(base: str, method: str, path: str,
             body: Optional[Any] = None) -> Tuple[int, Dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw) if raw else {}


def _request_raw(base: str, method: str, path: str) -> Tuple[int, str, str]:
    """取原始响应体（页面、产物这类不是 JSON 的东西用）。"""
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return (response.status,
                    response.read().decode("utf-8"),
                    response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8"), ""


@pytest.fixture()
def http(tmp_path: Path):
    service = PlatformService(build_default_registry(tmp_path))
    server, thread, base = serve_in_thread(service, port=0)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_health(http: str) -> None:
    status, payload = _request(http, "GET", "/health")
    assert status == 200 and payload["status"] == "ok"


def test_http_root_serves_the_shell_page(http: str) -> None:
    """根路径给人看：单页壳。非技术用户就是从这里开始一次任务的。"""
    status, body, content_type = _request_raw(http, "GET", "/")
    assert status == 200
    assert content_type.startswith("text/html")
    for marker in ['id="width"', 'id="depth"', 'id="flux"', 'id="target"',
                   'id="go"', 'id="checks"', 'id="artifacts"', 'id="caps"']:
        assert marker in body, f"壳里缺少 {marker}，用户没法完成任务"
    assert "光枢" in body


def test_http_endpoints_index(http: str) -> None:
    """机器要的清单挪到 /endpoints，免得和界面抢根路径。"""
    status, payload = _request(http, "GET", "/endpoints")
    assert status == 200
    assert any("artifacts" in e for e in payload["endpoints"])


def test_http_artifact_download(http: str) -> None:
    """产物能直接下载——用户拿到的最后一样东西就是那个 STF。"""
    _, payload = _request(http, "POST", "/run",
                          {"pipeline": "lighting_plan", "task": TASK})
    job_id = payload["job_id"]
    status, body, content_type = _request_raw(
        http, "GET", f"/jobs/{job_id}/artifacts/stf")
    assert status == 200
    assert body.startswith("[VERSION]")
    assert "attachment" in content_type or content_type.startswith("text/plain")

    status, payload = _request(http, "GET", f"/jobs/{job_id}/artifacts/plan")
    assert status == 200 and "fixtures" in payload


def test_http_unknown_artifact_key_is_404(http: str) -> None:
    _, payload = _request(http, "POST", "/run",
                          {"pipeline": "lighting_plan", "task": TASK})
    job_id = payload["job_id"]
    status, body, _ = _request_raw(http, "GET", f"/jobs/{job_id}/artifacts/nope")
    assert status == 404
    assert "实际有" in body


def test_http_artifact_for_unknown_job_is_404(http: str) -> None:
    assert _request_raw(http, "GET", "/jobs/nope/artifacts/stf")[0] == 404


def test_http_capabilities_and_pipelines(http: str) -> None:
    status, payload = _request(http, "GET", "/capabilities")
    assert status == 200
    assert {c["name"] for c in payload["capabilities"]} == {"lumen", "dialux"}
    assert all(c["limits"] for c in payload["capabilities"])
    status, payload = _request(http, "GET", "/pipelines")
    assert status == 200
    assert [p["name"] for p in payload["pipelines"]] == ["lighting_plan"]


def test_http_run_sync(http: str) -> None:
    status, payload = _request(http, "POST", "/run",
                               {"pipeline": "lighting_plan", "task": TASK})
    assert status == 200 and payload["ok"] is True
    assert all(c["passed"] for c in payload["checks"])


def test_http_async_job_lifecycle(http: str) -> None:
    status, payload = _request(http, "POST", "/jobs",
                               {"pipeline": "lighting_plan", "task": TASK})
    assert status == 202 and payload["status"] == "queued"
    job_id = payload["job_id"]
    deadline = time.monotonic() + 90
    while True:
        _, status_payload = _request(http, "GET", f"/jobs/{job_id}")
        if status_payload["status"] in ("done", "failed", "cancelled"):
            break
        assert time.monotonic() < deadline, "超时"
        time.sleep(0.05)
    assert status_payload["status"] == "done"
    status, result = _request(http, "GET", f"/jobs/{job_id}/result")
    assert status == 200 and result["ok"] is True


def test_http_cancel(http: str) -> None:
    _, payload = _request(http, "POST", "/jobs",
                          {"pipeline": "lighting_plan", "task": TASK})
    status, cancelled = _request(http, "POST", f"/jobs/{payload['job_id']}/cancel")
    assert status == 200 and "步" in cancelled["message"]


def test_http_unknown_job_is_404(http: str) -> None:
    assert _request(http, "GET", "/jobs/nope")[0] == 404
    assert _request(http, "GET", "/jobs/nope/result")[0] == 404


def test_http_unknown_path_is_404(http: str) -> None:
    assert _request(http, "GET", "/nope")[0] == 404
    assert _request(http, "POST", "/nope", {})[0] == 404


def test_http_bad_body_is_400(http: str) -> None:
    request = urllib.request.Request(http + "/run", data=b"", method="POST")
    try:
        urllib.request.urlopen(request, timeout=20)
        raise AssertionError("空请求体应当被拒")
    except urllib.error.HTTPError as error:
        assert error.code == 400
        assert "空" in json.loads(error.read().decode("utf-8"))["error"]


def test_http_non_json_body_is_400(http: str) -> None:
    request = urllib.request.Request(http + "/run", data="{not json".encode("utf-8"),
                                     method="POST")
    try:
        urllib.request.urlopen(request, timeout=20)
        raise AssertionError("非 JSON 请求体应当被拒")
    except urllib.error.HTTPError as error:
        assert error.code == 400


def test_http_body_size_limit_is_enforced(tmp_path: Path) -> None:
    """护栏要能被触发，否则它只是装饰。这里把上限调到 512 字节来验证。"""
    service = PlatformService(build_default_registry(tmp_path))
    server, thread, base = serve_in_thread(service, port=0)
    server.RequestHandlerClass.max_body_bytes = 512
    try:
        big = {"pipeline": "lighting_plan", "task": TASK,
               "padding": "x" * 2000}
        status, payload = _request(base, "POST", "/run", big)
        assert status == 400
        assert "过大" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_server_refuses_to_bind_non_loopback(service: PlatformService) -> None:
    """没有鉴权的接口不许默认绑出去。"""
    with pytest.raises(ValueError, match="拒绝绑定到非回环地址"):
        create_server(service, host="0.0.0.0", port=0)


def test_server_can_bind_remote_only_when_asked(service: PlatformService) -> None:
    server = create_server(service, host="0.0.0.0", port=0, allow_remote=True)
    try:
        assert server.server_address[1] > 0
    finally:
        server.server_close()


# ============================================================ MCP


def _call(service: PlatformService, method: str, params: Optional[Dict[str, Any]] = None,
          request_id: Any = 1) -> Optional[Dict[str, Any]]:
    message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return handle_message(service, message)


def _tool(service: PlatformService, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    response = _call(service, "tools/call", {"name": name, "arguments": arguments})
    assert response is not None
    result = response["result"]
    return {"isError": result["isError"],
            "payload": json.loads(result["content"][0]["text"])}


def test_mcp_initialize_echoes_protocol_version(service: PlatformService) -> None:
    response = _call(service, "initialize", {"protocolVersion": "2024-11-05"})
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "optiflow"
    assert "tools" in response["result"]["capabilities"]


def test_mcp_notifications_get_no_response(service: PlatformService) -> None:
    assert handle_message(service, {"jsonrpc": "2.0",
                                    "method": "notifications/initialized"}) is None


def test_mcp_ping(service: PlatformService) -> None:
    assert _call(service, "ping")["result"] == {}


def test_mcp_tools_list_is_well_formed(service: PlatformService) -> None:
    tools = _call(service, "tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "optiflow_plan_lighting" in names
    assert "optiflow_capabilities" in names
    for tool in tools:
        # description 必须是字符串：写成元组会被序列化成 JSON 数组，违反 MCP schema，
        # 而 MCP 客户端只会告诉你「工具有问题」，不会告诉你哪里有问题。
        assert isinstance(tool["description"], str), tool["name"]
        assert tool["description"], f"{tool['name']} 没有描述，agent 不知道什么时候用"
        assert isinstance(tool["inputSchema"], dict)
        assert tool["inputSchema"]["type"] == "object"
        assert "handler" not in tool, "对外清单不该泄露内部 handler"
    # 整个清单必须能原样过一遍 JSON（元组/自定义对象都会在这里露馅）
    assert json.loads(json.dumps({"tools": tools}, ensure_ascii=False))["tools"] == tools


def test_mcp_capabilities_tool_surfaces_limits(service: PlatformService) -> None:
    payload = _tool(service, "optiflow_capabilities", {})["payload"]
    for cap in payload["capabilities"]:
        assert cap["limits"]


def test_mcp_plan_lighting_tool_end_to_end(service: PlatformService) -> None:
    outcome = _tool(service, "optiflow_plan_lighting",
                    {"width": 11.9, "depth": 8.78, "flux": 3000.0,
                     "target_lux": 500.0})
    assert outcome["isError"] is False
    payload = outcome["payload"]
    assert payload["ok"] is True
    assert [s["adapter"] for s in payload["steps"]] == ["lumen", "dialux"]
    plan_step = payload["steps"][0]
    metrics = {m["name"]: m["value"] for m in plan_step["metrics"]}
    assert metrics["uniformity_u0"] >= 0.6
    assert plan_step["raw"]["assumptions"]
    assert Path(payload["steps"][1]["artifacts"]["stf"]).exists()


def test_mcp_async_tools_round_trip(service: PlatformService) -> None:
    submitted = _tool(service, "optiflow_submit",
                      {"pipeline": "lighting_plan", "task": TASK})["payload"]
    job_id = submitted["job_id"]
    deadline = time.monotonic() + 90
    while True:
        status = _tool(service, "optiflow_job_status", {"job_id": job_id})["payload"]
        if status["status"] in ("done", "failed", "cancelled"):
            break
        assert time.monotonic() < deadline, "超时"
        time.sleep(0.05)
    assert status["status"] == "done"
    result = _tool(service, "optiflow_job_result", {"job_id": job_id})["payload"]
    assert result["ok"] is True


def test_mcp_unknown_tool_is_an_error_content(service: PlatformService) -> None:
    """工具名错了返回 isError 的 content，不是 JSON-RPC 错误——
    这样 agent 能读到「有哪些工具可用」并自己纠正。"""
    outcome = _tool(service, "no_such_tool", {})
    assert outcome["isError"] is True
    assert "available" in outcome["payload"]


def test_mcp_bad_arguments_are_error_content(service: PlatformService) -> None:
    outcome = _tool(service, "optiflow_plan_lighting", {"width": 5.0})
    assert outcome["isError"] is True
    assert "error" in outcome["payload"]


def test_mcp_nonphysical_arguments_are_rejected(service: PlatformService) -> None:
    outcome = _tool(service, "optiflow_plan_lighting",
                    {"width": -3.0, "depth": 4.0, "flux": 3000.0})
    assert outcome["isError"] is True
    assert "必须为正" in outcome["payload"]["error"]


def test_mcp_unknown_method_is_jsonrpc_error(service: PlatformService) -> None:
    response = _call(service, "bogus/method")
    assert response["error"]["code"] == -32601
    assert "bogus/method" in response["error"]["message"]


def test_mcp_bad_jsonrpc_version_is_rejected(service: PlatformService) -> None:
    response = handle_message(service, {"jsonrpc": "1.0", "id": 1, "method": "ping"})
    assert response["error"]["code"] == -32600


def test_mcp_non_object_message_is_rejected(service: PlatformService) -> None:
    response = handle_message(service, ["not", "an", "object"])
    assert response["error"]["code"] == -32600


def test_mcp_stdio_round_trip(service: PlatformService) -> None:
    """真的走一遍 stdio 循环：喂两行、收官一行、通知不回。"""
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    assert serve_stdio(service, stdin=stdin, stdout=stdout) == 0
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    assert [r["id"] for r in responses] == [1, 2]


def test_mcp_stdio_survives_a_malformed_line(service: PlatformService) -> None:
    """一行坏消息不能杀掉服务。"""
    stdin = io.StringIO("{not json\n" +
                        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    serve_stdio(service, stdin=stdin, stdout=stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == 7 and responses[1]["result"] == {}


def test_mcp_tool_registry_matches_public_list() -> None:
    public = {t["name"] for t in _public_tools()}
    assert public == set(TOOLS_BY_NAME)


def test_lighting_task_translation() -> None:
    task = lighting_task({"width": 6.0, "depth": 4.0, "flux": 2500.0})
    assert task["kind"] == "layout"
    space = task["spaces"][0]
    assert space["geometry"]["outline"] == [
        {"x": 0.0, "y": 0.0}, {"x": 6.0, "y": 0.0},
        {"x": 6.0, "y": 4.0}, {"x": 0.0, "y": 4.0}]
    assert space["reflectance"]["ceiling"] == 0.70
    assert task["constraints"][0]["target"] == 500.0
    assert task["extra"]["flux"] == 2500.0
    with pytest.raises(ValueError, match="必须为正"):
        lighting_task({"width": 0.0, "depth": 4.0, "flux": 2500.0})


def test_jobstatus_values_are_stable() -> None:
    """对外契约里的状态字符串不能随手改——agent 与壳都按它们判断。"""
    assert [s.value for s in JobStatus] == [
        "queued", "running", "done", "failed", "cancelled"]
# ============================================================ 壳


def test_shell_page_file_exists_and_is_self_contained() -> None:
    """壳必须是自包含的：不引 CDN、不引外部 CSS/JS，否则离线就打不开。"""
    assert PAGE_PATH.exists(), f"壳的页面文件缺失：{PAGE_PATH}"
    html = page_html()
    assert html.lstrip().startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "http://" not in html and "https://" not in html, "壳里引了外部资源"
    assert "<script src" not in html and "<link rel=\"stylesheet\"" not in html


def test_shell_page_surfaces_limits_not_just_numbers() -> None:
    """界面必须把「这份方案的边界」摆在结果旁边。

    只给漂亮数字不给边界的界面，比没有界面更危险——用户会拿它当结论用。
    """
    html = page_html()
    assert "适用边界" in html
    assert "assumptions" in html and "warnings" in html
    assert "做不到什么" in html


def test_shell_page_posts_to_the_run_endpoint() -> None:
    """页面调的就是对外契约那几条路由，不是另开的后门。"""
    html = page_html()
    assert 'fetch("/run"' in html
    assert 'fetch("/capabilities")' in html
    assert "/artifacts/" in html


def test_missing_shell_page_raises_instead_of_serving_blank(monkeypatch) -> None:
    """文件缺失要报错，不能回一个空页面——空页面没人看得出哪里坏了。"""
    from optiflow import shell
    monkeypatch.setattr(shell, "PAGE_PATH", Path("no/such/index.html"))
    with pytest.raises(FileNotFoundError, match="壳的页面文件缺失"):
        shell.page_html()


def test_content_type_mapping() -> None:
    assert content_type_for(Path("a.html")).startswith("text/html")
    assert content_type_for(Path("a.stf")).startswith("text/plain")
    assert content_type_for(Path("a.json")).startswith("application/json")
    assert content_type_for(Path("a.bin")) == "application/octet-stream"


