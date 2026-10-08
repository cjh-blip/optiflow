"""评价点①：DXF 导入（自动化 + 完整性检查 + 确认闸门的数据面）。

钉住四件：

1. 真图纸：IR + 检查清单全过，房间概要可直接摆给人确认；
2. 坏输入：空内容 / 非法 base64 / 不是 DXF → 明确报错，绝不返回半个结果；
3. 读不到房间：ok=False 且「读到房间」这条变红——不假装成功；
4. HTTP：POST /import-dxf 的 200 与 400 两条路。
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from optiflow.api import serve_in_thread
from optiflow.service import PlatformService, build_default_registry

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def service(tmp_path: Path) -> PlatformService:
    return PlatformService(build_default_registry(tmp_path))


@pytest.fixture()
def real_dxf() -> Tuple[str, Dict[str, Any]]:
    raw = (FIXTURES / "sample_room.dxf").read_bytes()
    cfg = json.loads((FIXTURES / "sample_parse_config.json").read_text(encoding="utf-8"))
    return base64.b64encode(raw).decode("ascii"), cfg


# ============================================================ 服务门面


def test_import_real_dxf_gives_ir_and_checks(service: PlatformService,
                                             real_dxf: Tuple[str, Dict[str, Any]]) -> None:
    b64, cfg = real_dxf
    out = service.import_dxf("sample_room.dxf", b64, cfg)

    assert out["ok"] is True
    assert out["summary"]["rooms"] == 1
    assert out["summary"]["rooms_closed"] == 1
    assert out["summary"]["furniture"] == 26
    room = out["rooms"][0]
    assert room["area_m2"] > 100 and room["vertices"] >= 3
    assert room["width_m"] > 0 and room["depth_m"] > 0
    assert room["furniture"] == 26
    assert all(c["passed"] for c in out["checks"]), out["checks"]
    # 完整 IR 可供确认后直接喂下游
    assert out["ir"]["storeys"][0]["spaces"]


def test_empty_content_rejected(service: PlatformService) -> None:
    with pytest.raises(ValueError):
        service.import_dxf("x.dxf", "")


def test_bad_base64_rejected(service: PlatformService) -> None:
    with pytest.raises(ValueError):
        service.import_dxf("x.dxf", "这不是 base64!!")


def test_not_a_dxf_rejected(service: PlatformService) -> None:
    b64 = base64.b64encode("hello world, not a dxf".encode()).decode("ascii")
    with pytest.raises(ValueError):
        service.import_dxf("x.dxf", b64)


def test_valid_dxf_without_rooms_reports_failure(service: PlatformService) -> None:
    """合法 DXF 但读不到房间：「读到房间」必须变红，ok=False，不假装成功。"""
    minimal = "0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    b64 = base64.b64encode(minimal.encode("ascii")).decode("ascii")
    out = service.import_dxf("empty.dxf", b64)
    assert out["ok"] is False
    assert out["summary"]["rooms"] == 0
    failed = [c["name"] for c in out["checks"] if not c["passed"]]
    assert "读到房间" in failed


# ============================================================ HTTP 层


def _post(url: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_import_dxf_ok_and_bad(tmp_path: Path,
                                    real_dxf: Tuple[str, Dict[str, Any]]) -> None:
    b64, cfg = real_dxf
    service = PlatformService(build_default_registry(tmp_path))
    server, _thread, base = serve_in_thread(service, port=0)
    try:
        status, payload = _post(f"{base}/import-dxf", {
            "filename": "sample_room.dxf", "content_base64": b64, "config": cfg})
        assert status == 200
        assert payload["ok"] is True
        assert payload["summary"]["rooms"] == 1
        assert payload["rooms"][0]["area_m2"] > 0

        status, payload = _post(f"{base}/import-dxf",
                                {"filename": "x.dxf", "content_base64": ""})
        assert status == 400
        assert "error" in payload
    finally:
        server.shutdown()
        server.server_close()
