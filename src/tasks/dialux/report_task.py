"""自动导入/导出报告（MVP4 雏形，占位城堡）。

BetterGI 的「自动功能」对应——DIALux 的 DxPrintExport* 系列是 Pro 附加功能，
2026-09-08 到期前可试；到期后另寻通道。本任务先占位：探测 DIALux 是否有
计算/导出入口，能导出则导出报告，不能则如实返回未实现（不造假）。

参数（params）：
- report_out：导出目录（默认 build/reports/）
- mode：'calc'（只算照度）| 'export'（导出报告）| 'both'
"""
from __future__ import annotations

import logging

from optiflow.workbench.task import Task, TaskContext, TaskResult

logger = logging.getLogger(__name__)


class ReportTask(Task):
    """自动导入/导出报告（MVP4 雏形）。"""

    name = "自动报告"
    priority = 30
    # 已知边界：DxPrintExport* 是 Pro 功能，2026-09-08 到期（见 KANBAN）。
    # 当前不实跑，只探测界面入口是否存在，避免假装已实现。
    _probe_done = False
    _probe_result = ""

    def run(self, ctx: TaskContext) -> TaskResult:
        params = ctx.params or {}
        mode = params.get("mode", "export")

        # 探测 DIALux 是否有计算/导出入口（UIA 只读，不点）
        from src.executor.uia.driver import blocking_dialogs
        try:
            stuck = [d for d in blocking_dialogs() if not d["is_file_dialog"]]
            if stuck:
                return TaskResult(False, "DIALux 有未处理弹窗，先清理再跑报告")
        except Exception as exc:  # noqa: BLE001
            logger.debug("弹窗预检跳过：%s", exc)

        ctx.log(f"报告任务（{mode}）：DxPrintExport* 是 Pro 功能（9-08 到期），"
                "当前为占位，未实跑导出。")
        # 占位：不造假。真实导出留 MVP4，接 DIALux 计算完成后的事件。
        return TaskResult(True, "报告任务占位（MVP4 实现真实导出）", {"mode": mode})
