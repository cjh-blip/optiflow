# -*- coding: utf-8 -*-
"""图纸 → 可核对摘要。

吃「房间 DXF（＋可选灯具 DXF ＋ ParseConfig ＋净高）」出结构化 dict：房间/顶点/面积/
家具候选/灯位（按 kind 与 shape 双维度）/校验分桶/六项完整性检查。

为什么在库里而不是在某个壳里：两个宿主（Hana App 走子进程、独立桌面版走进程内）都要它，
一份实现一份口径。库函数只管算，失败直接抛；CLI 负责把失败收成 `{ok:false,error}`。

堵住的七处坑（2026-10-12 侦察 + 实跑确认）：
  1. 仓库根与 `src` 都必须在 sys.path 上（`planner/core.py` 模块级 `from src.validator import`）
     —— 现在由 `_ensure_importable()` 自己办，调用方不必再设 PYTHONPATH；
  2. fixture/配置路径相对 **cwd**（`ParseConfig.from_json` 是裸 read_text），调用方给绝对路径最稳；
  3. 解析错误不包装 → CLI 统一收口，带上下文；
  4. `_meta.luminaires` 语义在 join 前后不同 → 只报 join 之后的数；
  5. 家具伪房间（`家具_` 前缀）必须剔除，否则房间数/顶点/灯位全错；
  6. `join_luminaires` 先清后挂（幂等），别手工塞灯位；
  7. 单位：配置里 `*_mm` 字段按 DXF 原始单位比较，不做换算（照原样，不猜）。
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _ensure_importable() -> None:
    """让 `planner.*` 与 `src.*` 都能被 import：仓库根与 `src` 都在 sys.path 上。

    冻结包（PyInstaller 等）里这两个目录可能不存在，那就不用加 —— 此时模块应当
    已经被打包进同一个包路径。
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] if len(here.parents) > 2 else None,
                      here.parents[1] / "src" if len(here.parents) > 1 else None):
        if candidate and candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def shape_of(symbol: str) -> str:
    if symbol.startswith("CIRCLE-"):
        return "circle"
    if symbol.startswith("RECT-"):
        return "rect"
    if symbol.startswith("LINEARC-"):
        return "linearc"
    return "block"


def summarize_plan(dxf: str, dxf_lum: Optional[str] = None,
                   config: Optional[str] = None,
                   ceil_h: Optional[float] = None) -> Dict[str, Any]:
    """解析图纸并给出摘要。解析失败直接抛异常，由调用方决定怎么呈现。"""
    _ensure_importable()

    from optiflow.adapters.dialux.dxf import ParseConfig, parse_dxf, extract_luminaires
    from optiflow.adapters.dialux._chain import polygon_area
    from planner.join import join_luminaires
    from optiflow.validator import validate_ir

    cfg = ParseConfig.from_json(config) if config else ParseConfig()
    ir = parse_dxf(dxf, cfg)

    join_warnings: list = []
    lum_note = None
    if dxf_lum:
        import ezdxf

        lumis = extract_luminaires(ezdxf.readfile(dxf_lum), cfg)
        join_warnings = join_luminaires(ir, lumis)
    else:
        lum_note = "未提供灯具图（dxf_lum），灯位为 0"

    if ceil_h is not None:
        for st in ir.get("storeys", []):
            for sp in st.get("spaces", []):
                sp.setdefault("ceil_h", ceil_h)

    viols = validate_ir(ir)
    spaces = [s for st in ir.get("storeys", []) for s in st.get("spaces", [])]
    rooms = [s for s in spaces if not str(s.get("name", "")).startswith("家具_")]
    lums = [l for s in spaces for l in s.get("luminaires", [])]

    def count_by(sev: str) -> list:
        return [v for v in viols if v.get("severity") == sev]

    # 分桶（按事实，不把设计态当错误）：
    # - mount_pending：LUM_MOUNT_Z_UNSET 属设计态 —— 挂高在生成 DIALux 输入时以 ceil_h
    #   兜底（adapters/dialux/adapter.py），校验器那句提示指向了一个本仓不存在的模块；
    # - furniture_noise：命中的是家具伪 space（名以 家具_ 开头），不是真房间的问题。
    def is_furniture(v: Dict[str, Any]) -> bool:
        sid = v.get("space_id") or ""
        return any(str(s.get("id")) == sid and str(s.get("name", "")).startswith("家具_")
                   for s in spaces)

    mount_pending = [v for v in viols if v.get("code") == "LUM_MOUNT_Z_UNSET"]
    others = [v for v in viols if v.get("code") != "LUM_MOUNT_Z_UNSET"]
    furniture_noise = [v for v in others if is_furniture(v)]
    real = [v for v in others if not is_furniture(v)]

    meta = ir.get("_meta") or {}
    area_total = round(sum(polygon_area(r["polygon"]) for r in rooms if r.get("polygon")), 2)
    area_ok = 0 < area_total < 10000

    # 六项完整性检查（逐条给结论，不打包成一个布尔）
    checks = [
        {"name": "读到房间", "passed": len(rooms) >= 1, "detail": f"{len(rooms)} 个房间"},
        {"name": "房间环闭合",
         "passed": bool(meta.get("rooms_closed", 0)) and meta.get("rooms_closed") == len(rooms),
         "detail": f"闭合 {meta.get('rooms_closed')} / 房间 {len(rooms)}"},
        {"name": "面积为正且在合理区间", "passed": area_ok,
         "detail": f"合计 {area_total} m²（阈值 0~10000）"},
        {"name": "识别到家具", "passed": (meta.get("furniture") or 0) > 0,
         "detail": f"候选 {meta.get('furniture')} 件"},
        {"name": "识别到灯位", "passed": len(lums) > 0,
         "detail": f"{len(lums)} 盏" + ("" if dxf_lum else "（未提供灯具图）")},
        {"name": "单位识别", "passed": bool(meta.get("dxf_units")), "detail": f"units={meta.get('dxf_units')}"},
    ]

    return {
        "room_count": len(rooms),
        "rooms": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "vertices": len(r.get("polygon") or []),
                "area_m2": round(polygon_area(r["polygon"]), 2) if r.get("polygon") else None,
            }
            for r in rooms
        ],
        "vertices_total": sum(len(r.get("polygon") or []) for r in rooms),
        "area_total_m2": area_total,
        "furniture_candidates": meta.get("furniture"),
        "luminaires_total": len(lums),
        "luminaires_by_kind": dict(collections.Counter(l.get("kind", "unknown") for l in lums)),
        "luminaires_by_shape": dict(collections.Counter(shape_of(l.get("symbol", "")) for l in lums)),
        "join_warnings": join_warnings,
        "lum_note": lum_note,
        "meta": meta,
        "checks": checks,
        "checks_passed": sum(1 for ck in checks if ck["passed"]),
        "validation": {
            "counts": dict(collections.Counter(v.get("severity") for v in viols)),
            "HALT": count_by("HALT"),
            "real": real,
            "mount_pending_count": len(mount_pending),
            "furniture_noise_count": len(furniture_noise),
        },
        "notes": [
            f"待下游回填挂载高度：{len(mount_pending)} 处（本仓无挂高步骤；生成 DIALux 输入时以净高兜底）"
            if mount_pending else None,
            f"家具伪空间上的告警：{len(furniture_noise)} 处（不计入房间问题）"
            if furniture_noise else None,
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optiflow.plan_summary",
                                 description="图纸 → 可核对摘要（JSON 到 stdout）")
    ap.add_argument("--dxf", required=True, help="房间图 DXF")
    ap.add_argument("--dxf-lum", default=None, help="灯具图 DXF（缺省则灯位为 0 并标注）")
    ap.add_argument("--config", default=None, help="ParseConfig JSON")
    ap.add_argument("--ceil-h", type=float, default=None, help="净高兜底（米）")
    args = ap.parse_args(argv)

    try:
        out = summarize_plan(args.dxf, args.dxf_lum, args.config, args.ceil_h)
    except Exception as exc:
        raw = str(exc).strip()
        msg = f"{type(exc).__name__}：{raw}" if raw else f"{type(exc).__name__}（无错误详情）"
        where = f"房间图 {args.dxf}" + (f"、灯具图 {args.dxf_lum}" if args.dxf_lum else "")
        print(json.dumps(
            {"ok": False,
             "error": f"{msg}；输入：{where}。常见原因：DXF 被截断或损坏、图层名与解析配置不匹配、或单位设置不对"},
            ensure_ascii=False,
        ))
        return 2  # 非 0：shell 层用 $? 也能判出失败（JSON 仍在 stdout，调用方择优）

    print(json.dumps({"ok": True, **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
