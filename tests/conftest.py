"""pytest 全局配置：让被搬测试里的 src.* 与 optiflow.* 都能 import。

被搬测试（dialux-compiler 原版）里有一批**不可改的断言**钉死了包路径
（例如 test_progress_contract 断言驱动脚本位于 <repo>/src/executor/uia），
所以搬运后两类顶层包并存：
- optiflow  ← 平台与 DIALux 适配器（src/optiflow，pyproject 的 pythonpath 已含 src）
- src       ← 被搬测试的 import 闭包（src/core、src/planner、src/validator、
              src/executor、src/tasks），保持原包路径不动

另外把仓库根与 src/ 写进 PYTHONPATH，让测试里 "python -m <pkg>" 的子进程
（test_exporter_stf 的 CLI 用例）也能找到包。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_parts = [str(ROOT / "src"), str(ROOT)]
_existing = os.environ.get("PYTHONPATH", "")
if _existing:
    _parts.append(_existing)
os.environ["PYTHONPATH"] = os.pathsep.join(_parts)
