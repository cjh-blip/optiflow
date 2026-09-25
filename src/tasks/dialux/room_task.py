"""自动房间布置：DWG → IR → STF → 导入 DIALux 建壳。

对应 BetterGI 的「秘境/自动任务」——把已验证的 demo_run 建壳链路包成 Task，
供 Flow（一条龙）复用。参数（params）：
- dwg / dwg_lighting / config：图纸与解析配置路径
- ir_out / stf_out：中间产物路径（默认 build/）
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from optiflow.workbench.task import Task, TaskContext, TaskResult

logger = logging.getLogger(__name__)

DEFAULT_IR = "build/demo_ir.json"
DEFAULT_STF = "build/demo_room.stf"


class RoomTask(Task):
    """自动房间布置（建壳链路）。"""

    name = "自动房间布置"
    priority = 60

    def run(self, ctx: TaskContext) -> TaskResult:
        p = ctx.config  # WorkbenchConfig.to_dict() 或直接 dict
        dwg = (ctx.params or {}).get("dwg") or p.get("dwg", "")
        dwg_lighting = (ctx.params or {}).get("dwg_lighting") or p.get("dwg_lighting", "")
        config = (ctx.params or {}).get("config") or p.get("parse_config", "")
        ir_out = (ctx.params or {}).get("ir_out", DEFAULT_IR)
        stf_out = (ctx.params or {}).get("stf_out", DEFAULT_STF)

        if not dwg:
            return TaskResult(False, "缺 dwg 路径（配置或 params）")

        ctx.log("解析图纸 → IR…")
        rc = self._run_cli([
            sys.executable, "src/main.py",
            "--dwg", dwg, "--dwg-lighting", dwg_lighting or dwg,
            "--config", config, "--out", ir_out,
        ], ctx)
        if rc != 0:
            return TaskResult(False, f"解析失败（退出码 {rc}）")
        if ctx.cancelled():
            return TaskResult(False, "已停止")

        ctx.log("IR → STF…")
        rc = self._run_cli([
            sys.executable, "-m", "optiflow.adapters.dialux.stf", "--validate", ir_out, stf_out,
        ], ctx)
        if rc != 0:
            return TaskResult(False, f"STF 导出失败（退出码 {rc}）")
        if ctx.cancelled():
            return TaskResult(False, "已停止")

        ctx.log("导入 STF 建房间…")
        from src.executor.uia import run_import, succeeded
        events = run_import(stf_out, on_step=lambda ev: ctx.log(ev.title),
                            cancel_check=ctx.cancelled)
        if not succeeded(events):
            bad = [e for e in events if not e.ok]
            return TaskResult(False, bad[-1].detail if bad else "导入未完成")
        return TaskResult(True, f"房间已建（{stf_out}）")

    @staticmethod
    def _run_cli(cmd: list, ctx: TaskContext) -> int:
        """跑子命令，逐行转发日志到 ctx.log。"""
        try:
            proc = subprocess.Popen(
                cmd, cwd=Path.cwd(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as exc:
            ctx.log(f"起不了进程：{exc}")
            return 2
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                ctx.log(line.strip()[:120])
        return proc.wait()
