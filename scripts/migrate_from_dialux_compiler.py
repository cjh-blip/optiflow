"""一次性搬运：dialux-compiler → OptiFlow（单向复制，只改 import 与包名）。

源仓 D:\dev\dialux-compiler 全程只读；本脚本只写入 OptiFlow。
不变量：除 import / 包名引用行外，被搬文件与源文件逐行一致（可用 verify 模式自检）。
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

SRC = Path(r"D:\dev\dialux-compiler")
DST = Path(r"D:\dev\OptiFlow")

# 有序替换表：旧 → 新（都是「import 或包名引用」）
RULES = [
    ("src.parser._chain", "optiflow.adapters.dialux._chain"),
    ("src.parser.dxf", "optiflow.adapters.dialux.dxf"),
    ("src.exporter.stf", "optiflow.adapters.dialux.stf"),
    ("from ...exporter.stf import", "from optiflow.adapters.dialux.stf import"),
    ("src.workbench", "optiflow.workbench"),
]

# (源相对路径, 目标相对路径)
PY_FILES = [
    # --- 任务 1：workbench（→ src/optiflow/workbench/，无任何替换） ---
    ("src/workbench/__init__.py", "src/optiflow/workbench/__init__.py"),
    ("src/workbench/config.py", "src/optiflow/workbench/config.py"),
    ("src/workbench/dispatcher.py", "src/optiflow/workbench/dispatcher.py"),
    ("src/workbench/flow.py", "src/optiflow/workbench/flow.py"),
    ("src/workbench/recognition.py", "src/optiflow/workbench/recognition.py"),
    ("src/workbench/task.py", "src/optiflow/workbench/task.py"),
    ("src/workbench/trigger.py", "src/optiflow/workbench/trigger.py"),
    # --- 任务 2：解析与 STF 导出（→ src/optiflow/adapters/dialux/） ---
    ("src/parser/_chain.py", "src/optiflow/adapters/dialux/_chain.py"),
    ("src/parser/dxf.py", "src/optiflow/adapters/dialux/dxf.py"),
    ("src/parser/xlsx.py", "src/optiflow/adapters/dialux/xlsx.py"),
    ("src/exporter/stf.py", "src/optiflow/adapters/dialux/stf.py"),
    # --- 支持件（被搬测试的 import 闭包；保持原包路径，见 BLOCKED.md B-2） ---
    ("src/core/__init__.py", "src/core/__init__.py"),
    ("src/core/env.py", "src/core/env.py"),
    ("src/planner/__init__.py", "src/planner/__init__.py"),
    ("src/planner/core.py", "src/planner/core.py"),
    ("src/planner/join.py", "src/planner/join.py"),
    ("src/validator/__init__.py", "src/validator/__init__.py"),
    ("src/executor/__init__.py", "src/executor/__init__.py"),
    ("src/executor/actions.py", "src/executor/actions.py"),
    ("src/executor/kernel.py", "src/executor/kernel.py"),
    ("src/executor/uia/__init__.py", "src/executor/uia/__init__.py"),
    ("src/executor/uia/driver.py", "src/executor/uia/driver.py"),
    ("src/executor/uia/driver_plan.py", "src/executor/uia/driver_plan.py"),
    ("src/executor/uia/luminaire.py", "src/executor/uia/luminaire.py"),
    ("src/tasks/__init__.py", "src/tasks/__init__.py"),
    ("src/tasks/dialux/__init__.py", "src/tasks/dialux/__init__.py"),
    ("src/tasks/dialux/autosave_trigger.py", "src/tasks/dialux/autosave_trigger.py"),
    ("src/tasks/dialux/calc_watch_trigger.py", "src/tasks/dialux/calc_watch_trigger.py"),
    ("src/tasks/dialux/room_task.py", "src/tasks/dialux/room_task.py"),
    ("src/tasks/dialux/luminaires_task.py", "src/tasks/dialux/luminaires_task.py"),
    ("src/tasks/dialux/report_task.py", "src/tasks/dialux/report_task.py"),
    ("src/tasks/dialux/furniture_task.py", "src/tasks/dialux/furniture_task.py"),
]

# 需要搬运的测试（目标目录固定 tests/，只改 import 行）
TEST_FILES = [
    "test_workbench.py",
    "test_perception.py",
    "test_progress_contract.py",
    "test_parser_dxf.py",
    "test_chain_geometry.py",
    "test_exporter_stf.py",
    "test_validator.py",
    "test_project_hygiene.py",
]

# 非 Python 资源：原样复制（二进制安全）
RAW_FILES = [
    ("src/executor/uia/autosave.ps1", "src/executor/uia/autosave.ps1"),
    ("src/executor/uia/dialux_driver.ps1", "src/executor/uia/dialux_driver.ps1"),
    ("src/executor/uia/furniture.ps1", "src/executor/uia/furniture.ps1"),
    ("src/executor/uia/luminaire.ps1", "src/executor/uia/luminaire.ps1"),
    ("spec/ir.schema.json", "spec/ir.schema.json"),
    ("build/room_layout.json", "build/room_layout.json"),
    ("build/test_room_1.stf", "build/test_room_1.stf"),
    ("tests/fixtures/README.md", "tests/fixtures/README.md"),
    ("tests/fixtures/mini_ir_2rooms.json", "tests/fixtures/mini_ir_2rooms.json"),
    ("tests/fixtures/sample_lighting.dxf", "tests/fixtures/sample_lighting.dxf"),
    ("tests/fixtures/sample_parse_config.json", "tests/fixtures/sample_parse_config.json"),
    ("tests/fixtures/sample_room.dxf", "tests/fixtures/sample_room.dxf"),
]


def rewrite(text: str) -> str:
    for old, new in RULES:
        text = text.replace(old, new)
    return text


def changed_lines(src_text: str, dst_text: str) -> list[tuple[int, str, str]]:
    a, b = src_text.splitlines(), dst_text.splitlines()
    if len(a) != len(b):
        return [(-1, f"<行数不同 {len(a)}→{len(b)}>", "")]
    return [(i + 1, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "copy"
    report: list[str] = []
    for rel_src, rel_dst in PY_FILES + [("tests/" + t, "tests/" + t) for t in TEST_FILES]:
        s = SRC / rel_src
        d = DST / rel_dst
        src_text = s.read_text(encoding="utf-8")
        new_text = rewrite(src_text)
        if mode == "copy":
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text(new_text, encoding="utf-8", newline="\n")
        if mode == "verify":
            got = d.read_text(encoding="utf-8")
            if got != new_text:
                report.append(f"!! {rel_dst} 与「源文件+仅替换 import」不一致")
        for ln, x, y in changed_lines(src_text, new_text):
            report.append(f"   {rel_dst}:{ln}  - {x.strip()}\n   {rel_dst}:{ln}  + {y.strip()}")
    for rel_src, rel_dst in RAW_FILES:
        if mode == "copy":
            d = DST / rel_dst
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC / rel_src, d)
        elif not filecmp.cmp(SRC / rel_src, DST / rel_dst, shallow=False):
            report.append(f"!! 资源文件不一致：{rel_dst}")
    print("\n".join(report) if report else "(无差异)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
