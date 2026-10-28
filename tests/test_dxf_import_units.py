"""`service.import_dxf` 的单位出处与判据（2026-10-28 缺陷 5）。

同一份 `tests/fixtures/sample_room.dxf`：header 写 `$INSUNITS=4`（mm），几何实际按 cm 画。
- 不给 config 时以前读到 0 房间（全被当家具），还照样报「单位识别 ✓」；
- 现在解析器按几何兜底到 cm，读回那间房，并把「header 与几何不符」写进判据。
"""
import base64
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from optiflow.plan_summary import units_check
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


def _unit_check(out: Dict[str, Any]) -> Dict[str, Any]:
    return next(c for c in out["checks"] if c["name"] == "单位识别")


def test_without_config_recovers_room_and_says_so(service: PlatformService) -> None:
    """缺省配置不再静默：兜底读回房间，同时把「header 撒谎 + 用的是默认配置」写在判据里。"""
    b64 = base64.b64encode((FIXTURES / "sample_room.dxf").read_bytes()).decode("ascii")
    out = service.import_dxf("sample_room.dxf", b64)

    assert out["ok"] is True, out["checks"]
    assert out["config_source"] == "default"
    assert out["summary"]["rooms"] == 1
    assert out["summary"]["units"] == "cm"
    assert out["summary"]["units_source"] == "geometry_fallback"
    assert out["summary"]["config_source"] == "default"

    detail = _unit_check(out)["detail"]
    assert "兜底" in detail
    assert "未给解析配置" in detail
    assert out["rooms"][0]["area_m2"] > 100


def test_explicit_config_is_reported_as_caller(service: PlatformService,
                                               real_dxf: Tuple[str, Dict[str, Any]]) -> None:
    """调用方给了 config 就不许兜底：出处必须是 caller / config。"""
    b64, cfg = real_dxf
    out = service.import_dxf("sample_room.dxf", b64, cfg)

    assert out["ok"] is True, out["checks"]
    assert out["config_source"] == "caller"
    assert out["summary"]["units_source"] == "config"
    detail = _unit_check(out)["detail"]
    assert "配置指定" in detail
    assert "未给解析配置" not in detail


def test_unit_check_fails_when_units_unresolved(service: PlatformService) -> None:
    """header 没声明单位、又读不出房间时，「单位识别」不许再假绿。"""
    minimal = "0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    b64 = base64.b64encode(minimal.encode("ascii")).decode("ascii")
    out = service.import_dxf("empty.dxf", b64)

    assert out["ok"] is False
    assert out["summary"]["units_source"] == "config_fallback"
    assert _unit_check(out)["passed"] is False


@pytest.mark.parametrize("meta,expected", [
    ({"dxf_units": "cm", "units_source": "config", "rooms": 1}, True),
    ({"dxf_units": "mm", "units_source": "header", "rooms": 1}, True),
    ({"dxf_units": "cm", "units_source": "geometry_fallback",
      "units_header": "mm", "rooms": 1}, True),
    ({"dxf_units": "cm", "units_source": "geometry_fallback",
      "units_header": "mm", "rooms": 0}, False),
    ({"dxf_units": "mm", "units_source": "config_fallback", "rooms": 0}, False),
    ({"dxf_units": None, "units_source": "unknown", "rooms": 0}, False),
])
def test_units_check_口径(meta: Dict[str, Any], expected: bool) -> None:
    """`units_check` 是七项检查与平台四项检查的共用口径：看单位能不能对上几何，不看有没有单位。"""
    assert units_check(meta)["passed"] is expected


def test_units_check_carries_caller_note() -> None:
    """平台侧把「用的是默认配置」塞进 detail，缺省配置就不再是静默的。"""
    check = units_check({"dxf_units": "mm", "units_source": "header", "rooms": 1},
                        "（调用方未给解析配置，用的是 ParseConfig 默认）")
    assert check["passed"] is True
    assert "未给解析配置" in check["detail"]
    assert "header" in check["detail"]
