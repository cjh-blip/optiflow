# -*- coding: utf-8 -*-
"""furniture.from_ir 动作测试：IR 家具记录 → dc 真机链需要的四字段参数。"""
from __future__ import annotations

import pytest

from optiflow.commands import CommandError, call

ROOM_ID = "WALLRING_000_E18_2DF7"
FUR_ID = "LINEARC_001_E4_2DCB"


def _ir(furniture):
    return {
        "storeys": [{
            "level": 1,
            "spaces": [{
                "id": ROOM_ID, "name": "R1", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]],
                "luminaires": [], "furniture": furniture,
            }],
        }],
        "_meta": {"rooms": 1, "furniture": len(furniture)},
    }


def test_from_ir_ok_and_shape():
    """真机跑通的那个补全件，转换后就是 dc _validate_furniture 认的四字段，多余字段剥掉。"""
    entry = {
        "id": FUR_ID, "room_id": ROOM_ID,
        "bbox": [11.45, 4.28, 11.9, 5.28], "height_m": 0.75,
        "polygon": [[11.45, 4.28], [11.9, 4.28], [11.9, 5.28], [11.45, 5.28]],
        "area_m2": 0.45, "kind": "unknown", "confidence": 1.0,
    }
    out = call("furniture.from_ir", params={"ir": _ir([entry])})
    assert out["count"] == 1
    assert out["items"] == [{
        "id": FUR_ID, "room_id": ROOM_ID,
        "bbox": [11.45, 4.28, 11.9, 5.28], "height_m": 0.75,
    }]


def test_from_ir_computes_bbox_from_polygon():
    """bbox 缺失时从 polygon 算 [xmin,ymin,xmax,ymax]：口径只有一个，前端不许自己拼。"""
    entry = {
        "id": FUR_ID, "room_id": ROOM_ID, "height_m": 0.75,
        "polygon": [[3.0, 1.0], [3.6, 1.0], [3.6, 2.2], [3.0, 2.2]],
    }
    out = call("furniture.from_ir", params={"ir": _ir([entry])})
    assert out["items"][0]["bbox"] == [3.0, 1.0, 3.6, 2.2]


def test_from_ir_rejects_unowned():
    """room_id 空（解析时没归属到房间）不能静默放行，得点名哪件家具。"""
    entry = {"id": FUR_ID, "room_id": None,
             "bbox": [0, 0, 1, 1], "height_m": 0.75, "confidence": 0.25}
    with pytest.raises(CommandError, match=FUR_ID):
        call("furniture.from_ir", params={"ir": _ir([entry])})


def test_from_ir_rejects_bad_height():
    entry = {"id": FUR_ID, "room_id": ROOM_ID, "bbox": [0, 0, 1, 1]}
    with pytest.raises(CommandError, match="height_m"):
        call("furniture.from_ir", params={"ir": _ir([entry])})


def test_from_ir_rejects_bad_bbox():
    entry = {"id": FUR_ID, "room_id": ROOM_ID, "bbox": [0, 0, 1], "height_m": 0.75}
    with pytest.raises(CommandError, match="bbox"):
        call("furniture.from_ir", params={"ir": _ir([entry])})


def test_from_ir_empty_is_clear():
    with pytest.raises(CommandError, match="furniture"):
        call("furniture.from_ir", params={"ir": _ir([])})
