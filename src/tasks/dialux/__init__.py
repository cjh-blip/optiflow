"""DIALux 自动化任务包。

每个能力一个模块，全部实现 optiflow.workbench.task.Task 基类：
- room_task.py   自动房间布置（DWG→IR→STF→导入建壳）
- luminaires_task.py 自动布灯（导入型号 + ArrangementFromSpace 排布）
- autosave_task.py 自动保存应答（实时 Trigger，见 autosave_trigger.py）
- report_task.py 自动导入/导出报告（MVP4 雏形，占位）

未来 Zemax / Transport 各自建 tasks/<app>/，复用 workbench 骨架。
"""
from .room_task import RoomTask  # noqa: F401
from .luminaires_task import LuminairesTask  # noqa: F401
from .report_task import ReportTask  # noqa: F401
from .furniture_task import FurnitureTask  # noqa: F401

__all__ = ["RoomTask", "LuminairesTask", "FurnitureTask", "ReportTask"]
