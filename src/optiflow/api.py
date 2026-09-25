r"""HTTP 接口 —— 把平台暴露成 AI agent 能调的工具集。

用标准库 http.server，不引 FastAPI：

- 对外契约就是下面那几条路由，与传输实现无关；计划文档说「壳最后定，接口对了换壳不疼」，
  换 FastAPI 时改的只是本文件，service.py 一行不用动；
- 零新增依赖，干净 venv 里也能起，测试可以在进程内用真 socket 打真请求；
- 本项目现在只需要「提交/查询/取结果」这几个动作，用不上 FastAPI 的依赖注入与校验层。
  （如果将来要挂鉴权、限流、OpenAPI 文档，再换成 FastAPI 是划算的——那时说一声就换。）

安全边界（写在这儿也写进 README）：

- 默认只绑回环地址；host 不是回环时必须显式 allow_remote=True，否则直接报错。
- **没有鉴权**。它是给本机 AI agent 用的工具接口，不是公网服务。
  绑到 0.0.0.0 就等于把「能读写你本机文件路径」的能力开放出去。

运行：

    D:\dev\anaconda3\python.exe -m optiflow.api --port 8765
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from .service import PlatformService, build_default_service
from .shell import content_type_for, page_html

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

MAX_BODY_BYTES = 4 * 1024 * 1024

#: 超限时最多再读掉这么多字节，好让客户端读到错误原因（见 _drain_body）
DRAIN_CAP = 1 * 1024 * 1024


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "OptiFlow/0.0.1"

    # 由 create_server 注入
    service: PlatformService = None  # type: ignore[assignment]
    max_body_bytes: int = MAX_BODY_BYTES

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
        """默认实现往 stderr 打每一行请求；测试里刷屏，关掉。"""

    # -------------------------------------------------- 工具
    def _send(self, status: int, payload: Any) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self, limit: int) -> None:
        """把客户端声明要发的这一段读掉（有上限）。

        为什么不是「发现超限就直接回 400」：那样客户端还在发就被掐断连接，
        它拿到的是 ConnectionAborted 而不是错误原因——护栏变成了「坏了没人知道」。
        读掉小超限的部分，客户端至少能读到为什么被拒；
        真正巨大的请求体只读 DRAIN_CAP 就不再读，直接断开。
        """
        remaining = min(limit, DRAIN_CAP)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                return
            remaining -= len(chunk)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("请求体为空：需要 JSON 对象")
        if length > self.max_body_bytes:
            self._drain_body(length)
            raise ValueError(
                f"请求体过大（{length} 字节），上限 {self.max_body_bytes}")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求体不是合法 UTF-8 JSON：{exc}") from None
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # -------------------------------------------------- 路由
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定的名字
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/":
                # 根路径给人看（单页壳），机器要的清单在 /endpoints
                self._send_html(page_html())
            elif path == "/endpoints":
                self._send(200, {
                    "name": "光枢 / OptiFlow",
                    "endpoints": ["GET /  (单页壳)", "GET /health", "GET /capabilities",
                                  "GET /pipelines", "POST /jobs", "GET /jobs/{id}",
                                  "GET /jobs/{id}/result", "GET /jobs/{id}/artifacts/{key}",
                                  "POST /jobs/{id}/cancel", "POST /run"],
                })
            elif path == "/health":
                self._send(200, {"status": "ok"})
            elif path == "/capabilities":
                self._send(200, {"capabilities": self.service.capabilities()})
            elif path == "/pipelines":
                self._send(200, {"pipelines": self.service.pipelines()})
            elif path.startswith("/jobs/"):
                self._get_job(path)
            else:
                self._send(404, {"error": f"没有这个路径：{path}"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成响应，不能让连接挂住
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(path))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _get_job(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2:
            self._send(200, self.service.status(parts[1]))
        elif len(parts) == 3 and parts[2] == "result":
            self._send(200, self.service.result(parts[1]))
        elif len(parts) == 4 and parts[2] == "artifacts":
            # 只放行该任务自己登记的产物，调用方给不了任意路径
            self._send_file(self.service.artifact_path(parts[1], parts[3]))
        else:
            self._send(404, {"error": f"没有这个路径：{path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            # 只有需要请求体的路由才去读它：cancel 不带 body，
            # 硬读会把一个完全正常的请求判成「请求体为空」。
            if path == "/jobs":
                body = self._read_json()
                job_id = self.service.submit(
                    str(body.get("pipeline", "")), body.get("task") or {})
                self._send(202, {"job_id": job_id, "status": "queued"})
            elif path == "/run":
                body = self._read_json()
                self._send(200, self.service.run_sync(
                    str(body.get("pipeline", "")), body.get("task") or {}))
            elif path.startswith("/jobs/") and path.endswith("/cancel"):
                job_id = [p for p in path.split("/") if p][1]
                self._send(200, self.service.cancel(job_id))
            else:
                self._drain_body(int(self.headers.get("Content-Length") or 0))
                self._send(404, {"error": f"没有这个路径：{path}"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()


def create_server(service: PlatformService, host: str = "127.0.0.1", port: int = 8765,
                  allow_remote: bool = False,
                  max_body_bytes: int = MAX_BODY_BYTES) -> ThreadingHTTPServer:
    """建一个服务实例。port=0 表示由系统分配（测试用）。

    host 不是回环地址时必须显式 allow_remote=True —— 这个接口没有鉴权，
    默认绑出去等于把「按路径读文件」的能力开放给整个网段。
    """
    if host not in LOOPBACK_HOSTS and not allow_remote:
        raise ValueError(
            f"拒绝绑定到非回环地址 {host!r}：本接口没有鉴权。"
            f"确实要对外暴露就显式传 allow_remote=True，并自行加反向代理与鉴权。"
        )

    handler = type("_BoundHandler", (_Handler,),
                   {"service": service, "max_body_bytes": max_body_bytes})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(service: PlatformService, host: str = "127.0.0.1", port: int = 0,
                    ) -> Tuple[ThreadingHTTPServer, threading.Thread, str]:
    """后台起服务，返回 (server, thread, base_url)。测试与内嵌用。"""
    server = create_server(service, host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound_host, bound_port = server.server_address[:2]
    return server, thread, f"http://{bound_host}:{bound_port}"


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m optiflow.api",
                                     description="光枢/OptiFlow HTTP 接口（本机工具接口，无鉴权）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-remote", action="store_true",
                        help="允许绑定非回环地址（危险：无鉴权）")
    args = parser.parse_args(argv)

    service = build_default_service()
    server = create_server(service, host=args.host, port=args.port,
                           allow_remote=args.allow_remote)
    print(f"光枢 / OptiFlow 接口已启动：http://{args.host}:{args.port}")
    print("  可调：GET /capabilities  /pipelines  POST /run  /jobs  ...")
    print("  注意：本接口没有鉴权，仅供本机使用。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
