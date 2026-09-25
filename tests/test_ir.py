"""IR 契约测试：序列化往返、面积估算、逃生舱、软件名守卫。"""

from __future__ import annotations

import json

import pytest

from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec

SOFTWARE_NAMES = ("dialux", "zemax", "creo", "opticstudio", "evo")


def _room_task() -> TaskSpec:
    outline = [
        Point(x=0.0, y=0.0),
        Point(x=9.57, y=0.0),
        Point(x=9.57, y=12.97),
        Point(x=0.0, y=12.97),
    ]
    room = Space(
        id="room-1",
        name="会议室",
        geometry=Geometry(kind="room", outline=outline, height=3.0),
        work_plane=0.75,
        reflectance={"ceiling": 0.7},
    )
    return TaskSpec(
        kind="layout",
        spaces=[room],
        fixtures=[Fixture(id="L1", name="筒灯", position=Point(x=1.0, y=1.0, z=3.0), properties={"flux": 3000})],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"utilization_factor": 0.7},
    )


def test_json_round_trip_is_lossless() -> None:
    task = _room_task()
    payload = task.model_dump_json()
    restored = TaskSpec.model_validate_json(payload)
    assert restored == task
    assert json.loads(payload)["spaces"][0]["geometry"]["kind"] == "room"


def test_extra_escape_hatch_is_preserved() -> None:
    task = _room_task()
    task.extra["adapter_private"] = {"anything": [1, 2, 3]}
    restored = TaskSpec.model_validate_json(task.model_dump_json())
    assert restored.extra["adapter_private"] == {"anything": [1, 2, 3]}


def test_space_area_is_computed_from_outline() -> None:
    room = _room_task().spaces[0]
    assert room.area == pytest.approx(9.57 * 12.97, rel=1e-9)


def test_target_lookup() -> None:
    task = _room_task()
    assert task.target_of("illuminance_avg") == 500.0
    assert task.target_of("mtf_50") is None


def test_ir_contains_no_software_specific_field_names() -> None:
    """守卫：IR 出现软件名字段即为设计错误。"""
    for model in (Point, Geometry, Space, Fixture, Metric, TaskSpec):
        for field in model.model_fields:
            lowered = field.lower()
            for banned in SOFTWARE_NAMES:
                assert banned not in lowered, f"{model.__name__}.{field} 含软件名 {banned!r}"
