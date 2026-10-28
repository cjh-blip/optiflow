"""TR-2a: ParseConfig roundtrip + header_insunits_to_units。"""
from pathlib import Path

from optiflow.adapters.dialux.dxf import ParseConfig, header_insunits_to_units, parse_dxf


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


# ── 弧形回归（2026-10-28）：平台仓与真相源同图同判据 ──


def _area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                   - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly))) / 2.0)


def _write_ring(tmp_path, ring, name):
    """ring: list[(x, y, bulge)]，一条闭合 LWPOLYLINE，画在图层 0（与真相源同名同义）。"""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # mm
    doc.modelspace().add_lwpolyline(
        [(x, y, 0, 0, b) for (x, y, b) in ring],
        format="xyseb", close=True, dxfattribs={"layer": "0"},
    )
    path = tmp_path / name
    doc.saveas(path)
    return path


def test_bulge_arc_room_area_平台仓(tmp_path: Path):
    """8m×4m + 顶边半圆（半径 4m）：真实面积 32 + π·4²/2 = 57.13 m²；按弦只有 32.00 m²。"""
    import math

    p = _write_ring(tmp_path, [(0, 0, 0), (8000, 0, 0), (8000, 4000, 1.0), (0, 4000, 0)], "d.dxf")
    ir = parse_dxf(str(p), ParseConfig())
    rooms = [s for s in ir["storeys"][0]["spaces"]
             if not str(s.get("name", "")).startswith("家具_")]
    assert len(rooms) == 1
    assert len(rooms[0]["polygon"]) > 50, "弧段没被采样成折线"
    assert abs(_area(rooms[0]["polygon"]) - (32.0 + math.pi * 4.0 ** 2 / 2)) < 0.2


def test_bulge_full_circle_room_平台仓(tmp_path: Path):
    """4 段 bulge 拼成正圆（半径 4m）：面积 π·4² = 50.27 m²；按弦只有 32.00 m²。"""
    import math

    b = math.tan(math.radians(90) / 4)
    ring = [(5000 + 4000 * math.cos(math.radians(a)),
             5000 + 4000 * math.sin(math.radians(a)), b) for a in (0, 90, 180, 270)]
    p = _write_ring(tmp_path, ring, "c.dxf")
    ir = parse_dxf(str(p), ParseConfig())
    rooms = [s for s in ir["storeys"][0]["spaces"]
             if not str(s.get("name", "")).startswith("家具_")]
    assert len(rooms) == 1
    assert abs(_area(rooms[0]["polygon"]) - math.pi * 4.0 ** 2) < 0.3


def test_no_bulge_polyline_unchanged_平台仓(tmp_path: Path):
    """无 bulge 时必须与旧行为逐点一致：矩形仍是 4 个顶点、面积不变。"""
    p = _write_ring(tmp_path, [(0, 0, 0), (8000, 0, 0), (8000, 4000, 0), (0, 4000, 0)], "r.dxf")
    ir = parse_dxf(str(p), ParseConfig())
    rooms = [s for s in ir["storeys"][0]["spaces"]
             if not str(s.get("name", "")).startswith("家具_")]
    assert len(rooms) == 1
    assert len(rooms[0]["polygon"]) == 4
    assert abs(_area(rooms[0]["polygon"]) - 32.0) < 1e-6
