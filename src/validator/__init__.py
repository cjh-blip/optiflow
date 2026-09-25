"""Validator 模块：IR schema 校验 + 六条业务规则。

对外：
- load_schema() -> dict
- validate_ir(ir) -> list[Violation]
- Violation TypedDict：{code, message, severity, space_id?, lum_id?}
- severity 字面量：INFO / WARNING / ERROR / HALT
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

try:  # jsonschema 在 requirements.txt 声明
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore

logger = logging.getLogger(__name__)

Severity = Literal["INFO", "WARNING", "ERROR", "HALT"]


class Violation(TypedDict, total=False):
    code: str
    message: str
    severity: Severity
    space_id: Optional[str]
    lum_id: Optional[str]


def _violation(code: str, message: str, severity: Severity,
               space_id: Optional[str] = None, lum_id: Optional[str] = None) -> Violation:
    v: Violation = {"code": code, "message": message, "severity": severity}
    if space_id is not None:
        v["space_id"] = space_id
    if lum_id is not None:
        v["lum_id"] = lum_id
    return v


def load_schema() -> Dict[str, Any]:
    """加载 spec/ir.schema.json（Draft-07）。"""
    from src.core.env import resource_path
    p = resource_path("spec/ir.schema.json")
    if not p.exists():
        # 支持测试时的工作目录
        p2 = Path("spec/ir.schema.json")
        if not p2.exists():
            raise FileNotFoundError(f"找不到 ir.schema.json，期望路径：{p} 或 {p2.resolve()}")
        p = p2
    return json.loads(p.read_text(encoding="utf-8"))


# ------- 六条规则 -------

def _rule1_polygon_min_and_closed(ir: Dict[str, Any]) -> List[Violation]:
    out: List[Violation] = []
    close_tol_m = 1e-3  # 1mm
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            poly = space.get("polygon", [])
            if len(poly) < 3:
                out.append(_violation("POLY_TOO_FEW_VERTS",
                                      f"多边形顶点不足（{len(poly)}<3）",
                                      "ERROR", space_id=sid))
                continue
            a = poly[0][:2]
            b = poly[-1][:2]
            gap = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            if gap > close_tol_m:
                out.append(_violation("POLY_NOT_CLOSED",
                                      f"多边形未闭合（gap={gap*1000:.2f} mm）",
                                      "ERROR", space_id=sid))
    return out


def _rule2_area_range(ir: Dict[str, Any]) -> List[Violation]:
    from optiflow.adapters.dialux._chain import polygon_area  # 懒加载
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            area = polygon_area(space.get("polygon", []))
            if not (0.5 <= area <= 10000):
                out.append(_violation("AREA_OUT_OF_RANGE",
                                      f"面积 {area:.2f} m² 不在 [0.5, 10000] 内",
                                      "WARNING", space_id=space.get("id")))
    return out


def _point_in_polygon(point, poly) -> bool:
    # 复用 planner.core 的射线法；避免循环 import，保持独立
    x, y = point[0], point[1]
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][:2]
        x2, y2 = poly[(i + 1) % n][:2]
        if ((y1 > y) != (y2 > y)):
            denom = (y2 - y1)
            if abs(denom) < 1e-12:
                continue
            x_intersect = (x2 - x1) * (y - y1) / denom + x1
            if x < x_intersect:
                inside = not inside
    return inside


def _rule3_luminaire_inside(ir: Dict[str, Any]) -> List[Violation]:
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            poly = space.get("polygon", [])
            for i, lum in enumerate(space.get("luminaires", [])):
                p = (lum.get("x", 0.0), lum.get("y", 0.0))
                if not poly or not _point_in_polygon(p, poly):
                    out.append(_violation(
                        "LUM_OUTSIDE_SPACE",
                        f"灯具 {lum.get('symbol', i)} ({p[0]:.2f},{p[1]:.2f}) 不在空间内",
                        "ERROR", space_id=sid, lum_id=lum.get("symbol") or str(i),
                    ))
    return out


def _rule4_mount_z_overflow(ir: Dict[str, Any]) -> List[Violation]:
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            ceil_h = space.get("ceil_h")
            if ceil_h is None:
                continue
            for i, lum in enumerate(space.get("luminaires", [])):
                mount = lum.get("mount", "recessed")
                z = float(lum.get("z", 0.0))
                lid = lum.get("symbol") or str(i)
                if mount == "recessed":
                    if z > ceil_h + 1e-6:  # recessed 不应高于天花
                        out.append(_violation(
                            "LUM_RECESSED_Z_OVER_CEIL",
                            f"吸顶灯具 z={z:.2f}m > 天花 ceil_h={ceil_h:.2f}m",
                            "ERROR", space_id=sid, lum_id=lid,
                        ))
                elif mount == "surface":
                    if z > ceil_h + 0.3:
                        out.append(_violation(
                            "LUM_SURFACE_Z_TOO_HIGH",
                            f"明装灯具 z={z:.2f}m > ceil_h+0.3={ceil_h + 0.3:.2f}m",
                            "ERROR", space_id=sid, lum_id=lid,
                        ))
    return out


def _rule5_catalog_match_false(ir: Dict[str, Any]) -> List[Violation]:
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            for i, lum in enumerate(space.get("luminaires", [])):
                if lum.get("catalog_match") is False:
                    out.append(_violation(
                        "CATALOG_MATCH_UNSET",
                        f"灯具 {lum.get('symbol', i)} 未成功匹配灯具目录，禁止静默执行",
                        "HALT", space_id=sid, lum_id=lum.get("symbol") or str(i),
                    ))
    return out


def _rule7_mount_z_unset(ir: Dict[str, Any]) -> List[Violation]:
    """规则 7：灯具挂载高度未回填（z 仍趴在楼面）。

    2026-09-05 evo 真机验证暴露的洞：解析 2D DWG 得到的灯具 z 全是 0，而规则 4 只查
    「z 是否高过天花」，z=0 反而静默通过 —— 28 盏灯全趴在地板上却零告警。
    本规则补上另一侧：吊顶类灯具（recessed/surface/pendant）的 z 不应停留在 0。

    严重级取 ERROR 而非 HALT：z=0 是「还没跑挂载高度回填」的正常中间态，
    修法是调 ``src.planner.mount.assign_mount_heights``，不是拦停流水线。
    """
    out: List[Violation] = []
    ceiling_mounts = ("recessed", "surface", "pendant")
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            ceil_h = space.get("ceil_h")
            if ceil_h is None:
                continue  # 没有净高时无从判断，规则 4 同样跳过
            for i, lum in enumerate(space.get("luminaires", [])):
                mount = lum.get("mount", "recessed")
                if mount not in ceiling_mounts:
                    continue
                try:
                    z = float(lum.get("z", 0.0))
                except (TypeError, ValueError):
                    continue
                if abs(z) <= 1e-6:
                    out.append(_violation(
                        "LUM_MOUNT_Z_UNSET",
                        f"{mount} 灯具 z=0（趴在楼面），天花 ceil_h={float(ceil_h):.2f}m；"
                        f"需先跑 src.planner.mount.assign_mount_heights 回填挂载高度",
                        "ERROR", space_id=sid, lum_id=lum.get("symbol") or str(i),
                    ))
    return out


def _rule8_kind_dims_consistency(ir: Dict[str, Any]) -> List[Violation]:
    """规则 8：灯具 kind 与 dims 不自洽。

    ``kind`` 是解析器对 DXF 图元的显式声明，``dims`` 是选型依据。两者对不上说明
    解析或搬运环节出了错，会让 MVP3 的目录匹配拿错尺寸。级别取 WARNING：
    数据仍可用，只是选型可能退化到按 symbol 猜。
    """
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            for i, lum in enumerate(space.get("luminaires", [])):
                kind = lum.get("kind")
                if kind is None:
                    continue  # kind 是可选字段，缺失不报
                dims = lum.get("dims") or {}
                lid = lum.get("symbol") or str(i)
                if kind == "point" and "radius_mm" not in dims:
                    out.append(_violation(
                        "LUM_KIND_DIMS_MISMATCH",
                        f"kind=point 但 dims 缺 radius_mm（dims={dims}），选型将无尺寸可用",
                        "WARNING", space_id=sid, lum_id=lid,
                    ))
                elif kind in ("linear", "area") and not {"w_mm", "h_mm"} <= set(dims):
                    out.append(_violation(
                        "LUM_KIND_DIMS_MISMATCH",
                        f"kind={kind} 但 dims 缺 w_mm/h_mm（dims={dims}），选型将无尺寸可用",
                        "WARNING", space_id=sid, lum_id=lid,
                    ))
    return out


def validate_with_luminaire_xlsx(ir: Dict[str, Any], specs_by_symbol: Dict[str, Any]) -> List[Violation]:
    """(可选) 按 xlsx 型号精确匹配一致性；未匹配 → catalog_match=False + WARNING（不产生 HALT）。

    HALT 仅当「已显式 catalog_match=False」时由规则 5 产生。
    """
    out: List[Violation] = []
    for storey in ir.get("storeys", []):
        for space in storey.get("spaces", []):
            sid = space.get("id")
            for i, lum in enumerate(space.get("luminaires", [])):
                sym = lum.get("symbol")
                if sym and sym not in specs_by_symbol:
                    lid = sym or str(i)
                    out.append(_violation(
                        "LUM_SYMBOL_NOT_IN_XLSX",
                        f"灯具 {sym} 未在 xlsx 灯具表找到匹配；已标记 catalog_match=False",
                        "WARNING", space_id=sid, lum_id=lid,
                    ))
                    lum["catalog_match"] = False
    return out


# ------- 主入口 -------

def validate_ir(ir: Dict[str, Any]) -> List[Violation]:
    """顺序：jsonschema 校验 → 7 条业务规则 → 返回 list[Violation]。

    实际 extend 的规则（编号即函数名后缀，**不连续**）：

    ==  ================================  ========  ==================================
    #   检查                              级别      函数
    ==  ================================  ========  ==================================
    1   多边形顶点数与闭合                ERROR     ``_rule1_polygon_min_and_closed``
    2   面积区间                          WARNING   ``_rule2_area_range``
    3   灯具是否落在房间内                ERROR     ``_rule3_luminaire_inside``
    4   挂载高度上溢（z 高过天花）        ERROR     ``_rule4_mount_z_overflow``
    5   catalog_match=False               HALT      ``_rule5_catalog_match_false``
    7   挂载高度未回填（z=0 趴楼面）      ERROR     ``_rule7_mount_z_unset``
    8   kind 与 dims 不自洽               WARNING   ``_rule8_kind_dims_consistency``
    ==  ================================  ========  ==================================

    **规则 6 不在这里**：它是「灯具 params 与灯具表 xlsx 一致」，实现在
    ``validate_with_luminaire_xlsx``，需要额外传入 xlsx 规格字典，因此不属于
    单参数的 ``validate_ir``。它目前全仓无调用者，接线与否见 ``KANBAN.md``「遗留与待决」。
    """
    out: List[Violation] = []
    schema = None
    try:
        schema = load_schema()
    except Exception as e:  # pragma: no cover - 文件已写入
        out.append(_violation("SCHEMA_LOAD_FAIL", f"加载 ir.schema.json 失败：{e}", "ERROR"))
        return out

    if jsonschema is None:
        out.append(_violation("JSONSCHEMA_MISSING",
                              "缺少 jsonschema 依赖，请 pip install -r requirements.txt", "ERROR"))
    else:
        try:
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)
            v = validator_cls(schema)
            for err in sorted(v.iter_errors(ir), key=lambda e: list(e.path)):
                path = "/".join(str(p) for p in err.absolute_path) or "<root>"
                out.append(_violation(
                    "JSONSCHEMA",
                    f"[{path}] {err.message}",
                    "ERROR",
                ))
        except Exception as e:
            out.append(_violation("JSONSCHEMA_RUNTIME",
                                  f"jsonschema 运行时异常：{e}", "ERROR"))

    out.extend(_rule1_polygon_min_and_closed(ir))
    out.extend(_rule2_area_range(ir))
    out.extend(_rule3_luminaire_inside(ir))
    out.extend(_rule4_mount_z_overflow(ir))
    out.extend(_rule5_catalog_match_false(ir))
    out.extend(_rule7_mount_z_unset(ir))
    out.extend(_rule8_kind_dims_consistency(ir))
    return out
