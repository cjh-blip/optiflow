"""DIALux 适配器（文件层）：DXF 解析 + IR → STF 导出。

解析层（dxf/_chain/xlsx）自 2026-10-19 起是**薄壳**：实现以「受控收编」在 `src/parser/`，
真相源 = `dialux-compiler/src/parser/`（登记见 AGENTS.md「目录关系」，跨仓同图断言进推送门）。
STF 导出（stf.py）仍是本仓实现，搬运自 dialux-compiler（一次性单向复制），算法逻辑未改。

对外：
- dxf.py   ：DWG/DXF → IR（薄壳 → `parser.dxf`，依赖 ezdxf）
- _chain.py：房间环几何后处理（薄壳 → `parser._chain`：共线合并 / 小凹槽剔除）
- xlsx.py  ：XLSX 解析（薄壳 → `parser.xlsx`，依赖 pandas/openpyxl，可选）
- stf.py   ：IR → DIALux STF 文本（本步驱动 DIALux 的唯一通道）
- adapter.py：OptiFlow 平台契约实现（DialuxAdapter）
"""

from .adapter import DialuxAdapter

__all__ = ["DialuxAdapter"]
