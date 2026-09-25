"""TR-3.1~3.4：src.validator 单元测试。"""
from __future__ import annotations

import ezdxf

from src.validator import load_schema, validate_ir


def _make_ir(rooms, luminaires_per_room=None, project_name="t"):
    """生成最小 IR。rooms: [{id, polygon, ceil_h?}...]；luminaires_per_room: [ [{x,y,z,symbol,catalog_match?}, ...], ...]"""
    stores_spaces = []
    for i, r in enumerate(rooms):
        space = {
            "id": r.get("id", f"R{i}"),
            "name": r.get("id", f"R{i}"),
            "polygon": r["polygon"],
            "luminaires": (luminaires_per_room or [[]])[i] if (luminaires_per_room and i < len(luminaires_per_room)) else [],
        }
        if "ceil_h" in r:
            space["ceil_h"] = r["ceil_h"]
        stores_spaces.append(space)
    return {
        "schema_version": "0.1",
        "project": {"name": project_name, "source": {"dwg": "fake.dxf"}, "units": "m"},
        "storeys": [{"level": 1, "spaces": stores_spaces}],
    }


def test_load_schema_ok():
    s = load_schema()
    assert s["$schema"].startswith("http://json-schema.org/")


def test_ir_jsonschema_pass():
    # 最小合法 IR
    ir = _make_ir([{
        "id": "R1",
        "ceil_h": 2.8,
        "polygon": [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]],
    }], [[{
        "symbol": "L1", "x": 5, "y": 4, "z": 2.79, "catalog_match": True,
    }]])
    viols = validate_ir(ir)
    err = [v for v in viols if v.get("severity") in ("ERROR", "HALT")]
    assert err == [], [v for v in err]


def test_catalog_false_halt():
    ir = _make_ir([{
        "id": "R1", "ceil_h": 2.8,
        "polygon": [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]],
    }], [[{
        "symbol": "BAD", "x": 5, "y": 4, "z": 2.79, "catalog_match": False,
    }]])
    viols = validate_ir(ir)
    halts = [v for v in viols if v.get("severity") == "HALT"
             and v.get("code") == "CATALOG_MATCH_UNSET"]
    assert len(halts) >= 1, viols


def test_mount_recessed_overflow():
    ir = _make_ir([{
        "id": "R1", "ceil_h": 2.8,
        "polygon": [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]],
    }], [[{
        "symbol": "L1", "x": 5, "y": 4, "z": 3.1, "catalog_match": True, "mount": "recessed",
    }]])
    viols = validate_ir(ir)
    errs = [v for v in viols if v.get("severity") == "ERROR"
            and v.get("code") == "LUM_RECESSED_Z_OVER_CEIL"]
    assert len(errs) >= 1, viols


def test_sample_room_no_halt():
    """真实 sample_room + 灯具解析后 join，不应产生 HALT。"""
    from optiflow.adapters.dialux.dxf import ParseConfig, parse_dxf, extract_luminaires
    from src.planner.join import join_luminaires
    import pathlib
    cfg = ParseConfig.from_json(str(
        pathlib.Path(__file__).parent / "fixtures" / "sample_parse_config.json"))
    room_dxf = pathlib.Path(__file__).parent / "fixtures" / "sample_room.dxf"
    light_dxf = pathlib.Path(__file__).parent / "fixtures" / "sample_lighting.dxf"
    ir = parse_dxf(str(room_dxf), cfg)
    doc = ezdxf.readfile(str(light_dxf))
    lumis = extract_luminaires(doc, cfg)
    join_luminaires(ir, lumis)
    viols = validate_ir(ir)
    halts = [v for v in viols if v.get("severity") == "HALT"]
    assert halts == [], f"HALT 违规：{halts}"
