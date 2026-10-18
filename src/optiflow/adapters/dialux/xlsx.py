"""灯具表 (Excel) 解析的**薄壳**（第三刀 · 刀A，2026-10-19）：只重导出公开面。

实现受控收编在 `src/parser/xlsx.py`（真相源 = `dialux-compiler/src/parser/xlsx.py`，逐字节相同）；
pandas/openpyxl 仍是惰性可选依赖（见 pyproject `xlsx` extra），本文件不引入任何新依赖。
"""
from parser.xlsx import LuminaireSpec, parse_xlsx  # noqa: F401  薄壳的全部工作就是重导出

__all__ = ["LuminaireSpec", "parse_xlsx"]
