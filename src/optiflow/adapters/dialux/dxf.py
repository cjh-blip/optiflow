"""DWG/DXF 解析的**薄壳**（第三刀 · 刀A，2026-10-19）：只重导出公开面与命令行入口。

- 真相源 = `dialux-compiler` 仓 `src/parser/`；本仓以「**受控收编**」自带一份实现于
  `src/parser/`（与真相源逐字节相同，唯一差异 = 5 行 `_chain` 绝对导入改相对导入，
  登记见 AGENTS.md「目录关系」）。
- 本文件**不含解析逻辑**：业务代码一律 `from optiflow.adapters.dialux.dxf import ...`（走本壳），
  改解析先改真相源再同步收编副本；跨仓同图断言 `scripts/cross_parser_parity.py` 进推送门，
  两令断言不过即红（分叉从口头检查变成每次推送都跑）。
- 公开函数签名与既有测试不动；`python -m optiflow.adapters.dialux.dxf <file>` 照旧出 IR JSON。
"""
from parser.dxf import (  # noqa: F401  薄壳的全部工作就是重导出
    CLOSE_TOLERANCE_METERS,
    FURNITURE_AREA_M2,
    LINEAR_ASPECT_MIN,
    MIN_AREA_M2,
    Luminaire,
    ParseConfig,
    Point,
    Room,
    WALL_RING_LAYER,
    classify_rect_luminaire,
    convert_to_meters,
    extract_luminaires,
    extract_rooms,
    header_insunits_to_units,
    parse_dxf,
)

__all__ = [
    "CLOSE_TOLERANCE_METERS",
    "FURNITURE_AREA_M2",
    "LINEAR_ASPECT_MIN",
    "MIN_AREA_M2",
    "Luminaire",
    "ParseConfig",
    "Point",
    "Room",
    "WALL_RING_LAYER",
    "classify_rect_luminaire",
    "convert_to_meters",
    "extract_luminaires",
    "extract_rooms",
    "header_insunits_to_units",
    "parse_dxf",
]

if __name__ == "__main__":  # pragma: no cover - 与原实现同一段抽查入口
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/sample.dxf"
    print(json.dumps(parse_dxf(target), ensure_ascii=False, indent=2))
