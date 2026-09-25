"""共享语义 IR —— 只表达三家软件的交集。

纪律：
1. 出现 dialux / zemax / creo 字样的字段即为设计错误（tests/test_ir.py 有守卫）。
2. 适配器私有参数一律走 TaskSpec.extra，不为单一软件扩展 IR。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Point(BaseModel):
    # x/y 给了默认值：Fixture.position 声明的是 Field(default_factory=Point)，
    # 若 x/y 必填，那个默认工厂每次都会抛 ValidationError ——
    # 也就是「不传 position 就建不出 Fixture」这个声明本身是坏的。
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Geometry(BaseModel):
    kind: Literal["room", "solid", "surface", "polyline"]
    outline: list[Point] = Field(default_factory=list)
    height: float | None = None


class Space(BaseModel):
    """空间容器：房间 / 场景 / 视场。"""

    id: str
    name: str = ""
    geometry: Geometry
    work_plane: float | None = None
    reflectance: dict[str, float] = Field(default_factory=dict)

    @property
    def area(self) -> float:
        """按平面轮廓的鞋带公式估算面积（骨架期粗算用）。"""
        pts = self.geometry.outline
        if len(pts) < 3:
            return 0.0
        total = 0.0
        for a, b in zip(pts, pts[1:] + pts[:1]):
            total += a.x * b.y - b.x * a.y
        return abs(total) / 2.0


class Material(BaseModel):
    name: str
    reflectance: float | None = None
    transmittance: float | None = None
    refractive_index: float | None = None


class Fixture(BaseModel):
    """被放置物：灯具 / 元器件 / 零件。"""

    id: str
    name: str = ""
    position: Point = Field(default_factory=Point)
    rotation: float | None = None
    properties: dict[str, float] = Field(default_factory=dict)


class Metric(BaseModel):
    """结果指标，或作为任务约束/目标使用。"""

    name: str
    value: float | None = None
    unit: str = ""
    target: float | None = None
    location: Point | None = None


class TaskSpec(BaseModel):
    """任务描述：平台与适配器之间的唯一输入契约。"""

    kind: str
    spaces: list[Space] = Field(default_factory=list)
    fixtures: list[Fixture] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    constraints: list[Metric] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def target_of(self, metric_name: str) -> float | None:
        """取某约束的目标值。"""
        for c in self.constraints:
            if c.name == metric_name:
                return c.target
        return None
