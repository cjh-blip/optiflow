"""DIALux 适配器（文件层）：DXF 解析 + IR → STF 导出。

搬运自 dialux-compiler（一次性单向复制），算法逻辑未改，只改了 import 与包名。

对外：
- dxf.py   ：DWG/DXF → IR（依赖 ezdxf）
- _chain.py：房间环几何后处理（共线合并 / 小凹槽剔除）
- xlsx.py  ：XLSX 解析（依赖 pandas/openpyxl，可选）
- stf.py   ：IR → DIALux STF 文本（本步驱动 DIALux 的唯一通道）
- adapter.py：OptiFlow 平台契约实现（DialuxAdapter）
"""

from .adapter import DialuxAdapter

__all__ = ["DialuxAdapter"]
