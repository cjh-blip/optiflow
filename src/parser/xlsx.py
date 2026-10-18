"""灯具表 (Excel) 解析：标准化为 IR luminaire params。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class LuminaireSpec:
    symbol: str  # 型号，用于与 DXF 块名匹配
    power: Optional[float] = None
    flux: Optional[float] = None
    cct: Optional[int] = None
    quantity: int = 1
    mount: str = "recessed"
    install_height: Optional[float] = None


def parse_xlsx(path: str) -> List[LuminaireSpec]:
    """读取灯具表。列名容错：功率/power、光通量/flux、色温/cct..."""
    # 延迟导入：避免无办公环境时整体不可用
    import pandas as pd

    df = pd.read_excel(path)
    specs: List[LuminaireSpec] = []
    for _, row in df.iterrows():
        def pick(*keys, default=None):
            for k in keys:
                for col in df.columns:
                    if str(col).strip().lower() == k.lower():
                        return row[col]
            return default

        specs.append(
            LuminaireSpec(
                symbol=str(row.get("型号") or row.get("symbol")),
                power=float(pick("功率", "power") or 0) or None,
                flux=float(pick("光通量", "flux") or 0) or None,
                cct=int(pick("色温", "cct") or 0) or None,
                quantity=int(pick("数量", "qty", "quantity") or 1) or 1,
                mount=str(pick("安装方式", "mount") or "recessed"),
                install_height=float(pick("安装高度", "height") or 0) or None,
            )
        )
    return specs
