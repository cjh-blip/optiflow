"""用**源仓 dialux-compiler 的** stf.py 生成 STF（只在 OptiFlow 侧落盘）。

单独成文件是为了避免在子进程里拼 Python 源码（引号/转义易错）。
用法：python scripts/run_source_exporter.py <ir.json> <out.stf> <project_name> <date>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_REPO = Path(r"D:/dev/dialux-compiler")

sys.path.insert(0, str(SOURCE_REPO))

from src.exporter.stf import write_stf  # noqa: E402


def main() -> int:
    ir_path, out_path, project_name, date = sys.argv[1:5]
    ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    write_stf(ir, Path(out_path), project_name=project_name, date=date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
