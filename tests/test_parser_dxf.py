"""TR-2a: ParseConfig roundtrip + header_insunits_to_units。"""
from pathlib import Path

from optiflow.adapters.dialux.dxf import ParseConfig, header_insunits_to_units


def test_parse_config_roundtrip(tmp_path: Path):
    d = {
        "units": "mm",
        "join_tolerance_mm": 0.2,
        "luminaire_circle_radius_range": [7.0, 8.5],
        "room_edge_layers": ["0", "WALL"],
        "default_ceil_h": 2.8,
        "origin": [10.0, 20.0],
    }
    cfg = ParseConfig.from_dict(d)
    assert cfg.units == "mm"
    assert cfg.join_tolerance_mm == 0.2
    assert cfg.luminaire_circle_radius_range == (7.0, 8.5)
    assert cfg.default_ceil_h == 2.8
    assert cfg.origin == (10.0, 20.0)
    # 来回转换
    back = ParseConfig.from_dict(cfg.to_dict())
    assert back.luminaire_circle_radius_range == (7.0, 8.5)
    assert back.origin == (10.0, 20.0)
    # to_json/from_json
    f = tmp_path / "cfg.json"
    cfg.to_json(f)
    cfg2 = ParseConfig.from_json(f)
    assert cfg2.room_edge_layers == ["0", "WALL"]
    assert cfg2.luminaire_circle_radius_range == (7.0, 8.5)
    # 读取真实 fixture
    fix = Path(__file__).parent / "fixtures" / "sample_parse_config.json"
    real = ParseConfig.from_json(str(fix))
    assert real.units == "cm"
    assert real.units_from_header is False
    assert real.default_ceil_h == 2.8
    assert real.luminaire_circle_radius_range == (7.0, 8.5)
    assert real.join_tolerance_mm == 2.0
    assert real.auto_close_tolerance_mm == 4.0
    assert real.room_layers == ["0"]
    assert real.room_edge_layers == ["0"]


def test_header_insunits_mapping():
    assert header_insunits_to_units(4) == "mm"
    assert header_insunits_to_units(6) == "m"
    assert header_insunits_to_units(1) == "inch"
    assert header_insunits_to_units(5) == "cm"
    assert header_insunits_to_units(None) is None
    assert header_insunits_to_units(999) is None
