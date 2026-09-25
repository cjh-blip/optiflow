"""自动家具任务：把 IR 家具记录逐件交给 DIALux 家具通道。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from optiflow.workbench.task import Task, TaskContext, TaskResult

logger = logging.getLogger(__name__)

DEFAULT_IR = "build/demo_ir.json"
PlaceFn = Callable[[dict], Tuple[bool, str]]


class FurnitureTask(Task):
    """家具放置任务；首个真机切片默认只放一件。"""

    name = "自动家具"
    priority = 55

    def __init__(
        self,
        *,
        ir: Optional[dict] = None,
        ir_path: str | Path = DEFAULT_IR,
        furniture_id: Optional[str] = None,
        limit: int = 1,
        place_fn: Optional[PlaceFn] = None,
    ):
        self.ir = ir
        self.ir_path = Path(ir_path)
        self.furniture_id = furniture_id
        self.limit = limit
        self._place_fn = place_fn

    def run(self, ctx: TaskContext) -> TaskResult:
        if ctx.cancelled():
            return TaskResult(False, "家具任务开始前已停止")
        if self.limit <= 0:
            return TaskResult(False, f"家具任务 limit 必须大于 0，当前为 {self.limit}")

        try:
            ir = self.ir if self.ir is not None else self._load_ir()
            furniture = self._select_furniture(ir)
        except (OSError, ValueError, KeyError) as exc:
            return TaskResult(False, f"家具数据不可用：{exc}")

        if not furniture:
            return TaskResult(False, "IR 中没有可放置家具")

        placed_ids: List[str] = []
        for record in furniture[: self.limit]:
            if ctx.cancelled():
                return TaskResult(False, "家具任务已停止（DIALux 可能保留已生成半成品）",
                                  {"placed_ids": placed_ids})
            try:
                ok, detail = self._place_one(record)
            except (OSError, RuntimeError, ValueError) as exc:
                ok, detail = False, str(exc)
            if not ok:
                return TaskResult(
                    False,
                    f"家具 {record['id']} 放置失败：{detail}",
                    {"placed_ids": placed_ids, "failed_id": record["id"]},
                )
            placed_ids.append(record["id"])
            ctx.log(f"家具 {record['id']} 已放置")

        return TaskResult(True, f"家具放置完成：{len(placed_ids)} 件",
                          {"placed_ids": placed_ids})

    def _load_ir(self) -> dict:
        if not self.ir_path.exists():
            raise FileNotFoundError(f"IR 文件不存在：{self.ir_path}")
        return json.loads(self.ir_path.read_text(encoding="utf-8"))

    def _select_furniture(self, ir: dict) -> List[dict]:
        records: List[dict] = []
        seen = set()
        for storey in ir.get("storeys", []):
            for space in storey.get("spaces", []):
                for record in space.get("furniture", []):
                    record_id = record.get("id")
                    if record_id in seen:
                        continue
                    seen.add(record_id)
                    self._validate_record(record)
                    if self.furniture_id is None or record_id == self.furniture_id:
                        records.append(record)
        if self.furniture_id is not None and not records:
            raise ValueError(f"找不到家具 id：{self.furniture_id}")
        return records

    @staticmethod
    def _validate_record(record: dict) -> None:
        record_id = record.get("id") or "<无 id>"
        required = ("room_id", "polygon", "bbox", "area_m2", "height_m")
        missing = [key for key in required if key not in record]
        if missing:
            raise ValueError(f"家具 {record_id} 缺少字段：{','.join(missing)}")
        if not record["room_id"]:
            raise ValueError(f"家具 {record_id} 缺少 room_id")
        if len(record["polygon"]) < 3:
            raise ValueError(f"家具 {record_id} polygon 顶点不足")
        if len(record["bbox"]) != 4:
            raise ValueError(f"家具 {record_id} bbox 必须有 4 个值")
        if float(record["area_m2"]) <= 0 or float(record["height_m"]) <= 0:
            raise ValueError(f"家具 {record_id} 尺寸/高度必须为正")

    def _place_one(self, record: dict) -> Tuple[bool, str]:
        if self._place_fn is not None:
            return self._place_fn(record)
        from src.executor.uia.furniture import place_furniture

        events = place_furniture(record)
        if (not events or not all(event.ok for event in events)
                or events[-1].name != "furniture-done"):
            detail = events[-1].detail if events else "无 UIA 事件"
            return False, detail
        return True, "ok"
