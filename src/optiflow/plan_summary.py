# -*- coding: utf-8 -*-
"""图纸 → 可核对摘要。

吃「房间 DXF（＋可选灯具 DXF ＋ ParseConfig ＋净高）」出结构化 dict：房间/顶点/面积/
家具候选/灯位（按 kind 与 shape 双维度）/校验分桶/七项完整性检查（六项并集＋轮廓点≥3，刀B 拍定）。

刀B（2026-10-19）起本模块两个入口：
- `summarize_plan(...)`：解析步（DXF → IR）＋ 摘要；
- `summarize_ir(ir, ...)`：**IR → 摘要纯函数**，摘要与检查项的唯一口径（壳侧 `core.summarize`
  薄壳转调它，exe 里 vendor 本模块即可、不必引整个平台）。

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


# ── 刀B 补进来的三块语义（原口径在壳侧 core.summarize，依据见其注释与 App 侧 plan.mjs）──
#: 家具范围裁剪：本链只做桌子＋白板，排除 9 件柜/架（候选 22 / 采用 13 / 排除 9 的双口径）
EXCLUDED_LINEARC = {"001", "002", "003", "006", "012", "014", "016", "018", "021"}


def linearc_short(name: Any) -> str:
    """从家具伪空间名里取 LINEARC 数字 id（`LINEARC_001` → `001`；不是则空串）。"""
    import re

    m = re.search(r"LINEARC_(\d+)", str(name or ""))
    return m.group(1) if m else ""


def _bbox(polygon: Any) -> list:
    """外包尺寸 [宽, 高]（米，三位小数）；空多边形回 [0.0, 0.0]（与壳侧原口径一致）。"""
    if not polygon:
        return [0.0, 0.0]
    xs = [float(p[0] or 0) for p in polygon]
    ys = [float(p[1] or 0) for p in polygon]
    return [round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)]


def _ceiling_h(space: Dict[str, Any]) -> Optional[float]:
    """净高：灯位 z 优先，但 **z=0 视为未赋值**（解析器在平图上就写 0；平台 `planner.mount`
    的 UNSET_Z、校验器的 LUM_MOUNT_Z_UNSET、壳侧 JS 的 `z || null` 同此口径），
    再回落 space.ceil_h（解析配置默认净高），都没有则 null。
    与壳侧原式 `lamps[0].z if lamps else None` 的唯一差异：z 未赋值时我们给配置净高、
    它给 0.0（壳侧 summary_line 把 0 当 falsy 渲染成 `?`，所以两边都显示不出错误高度）。"""
    lamps = space.get("luminaires") or []
    z = lamps[0].get("z") if lamps else None
    if z:
        return z
    return space.get("ceil_h") or None


def _furniture_block(spaces: list) -> Dict[str, Any]:
    """家具双口径：候选 / 采用 / 排除（EXCLUDED_LINEARC 柜、架）＋ id 与名字清单。"""
    furn = [s for s in spaces if str(s.get("name", "")).startswith("家具_")]
    excluded = [s for s in furn if linearc_short(s.get("name")) in EXCLUDED_LINEARC]
    return {
        "count": len(furn),
        "adopted": len(furn) - len(excluded),
        "excluded": len(excluded),
        "excluded_ids": sorted(linearc_short(s.get("name")) for s in excluded),
        "names": [str(s.get("name")) for s in furn],
    }


def _is_furniture_space(s: Dict[str, Any]) -> bool:
    """家具伪空间：parser 把小面积环也写成 space，环来的家具 name 前缀「家具_」，
    块引用来的家具 id 前缀 INSERT_/furniture_。主房不能选它们。
    与壳侧 `core._is_furniture_space` 一字不改地一致。"""
    name = str(s.get("name") or "")
    sid = str(s.get("id") or "")
    return name.startswith("家具_") or sid.startswith(("INSERT_", "furniture_"))


def _main_room(ir: Dict[str, Any]) -> Dict[str, Any]:
    """单主房选择（口径＝壳侧 `room_of`，一字不改）：**只看第一层**，先挑有灯位的 space，
    没有则**先排除家具伪空间、再按面积最大**选（面积同则比顶点数）；空 IR 回 0 值 dict，
    保证壳侧 `room` 字段形状不变。另附它自己的灯位三数（壳侧 `luminaires` 块原本就是主房口径）。

    2026-10-28 变更（原口径「全部 space 里顶点最多」）：产品级实测「半圆两室一厅」的主房
    被选成 6.24 m² 的半圆房（18 个采样点压过 16.00 m² 的矩形房），界面看着像另一间房丢了；
    无灯位时还会挑中 22 点的家具伪空间 LINEARC_022。挂灯前后现在同口径，都指向真房
    （WALLRING_000_E18_2D1F）。"""
    from optiflow.adapters.dialux._chain import polygon_area

    spaces0 = (ir.get("storeys") or [{}])[0].get("spaces") or []
    main = None
    for s in spaces0:
        if s.get("luminaires"):
            main = s
            break
    if main is None and spaces0:
        # 没有灯位时：先排除家具伪空间，再按面积最大选主房（与壳侧 `room_of` 同口径）。
        # 旧口径「顶点最多」会让弧形房（一圈采样点）压过面积更大的矩形房。
        real = [s for s in spaces0 if not _is_furniture_space(s)]
        pool = real or spaces0
        main = max(pool, key=lambda s: (polygon_area(s.get("polygon") or []),
                                        len(s.get("polygon") or [])))
    main = main or {}
    poly = main.get("polygon") or []
    lamps = main.get("luminaires") or []
    area = round(polygon_area(poly), 3) if len(poly) >= 3 else 0.0
    return {
        "id": main.get("id"),
        "name": main.get("name"),
        "points": len(poly),
        "area_m2": area,
        "bbox_m": _bbox(poly),
        "ceiling_h_m": _ceiling_h(main),
        "luminaires": {
            "total": len(lamps),
            "linear": sum(1 for x in lamps if x.get("kind") == "linear"),
            "point": sum(1 for x in lamps if x.get("kind") == "point"),
        },
    }


def summarize_plan(dxf: str, dxf_lum: Optional[str] = None,
                   config: Optional[str] = None,
                   ceil_h: Optional[float] = None) -> Dict[str, Any]:
    """解析图纸并给出摘要。解析失败直接抛异常，由调用方决定怎么呈现。"""
    _ensure_importable()

    from optiflow.adapters.dialux.dxf import ParseConfig, parse_dxf, extract_luminaires
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

    return summarize_ir(ir, join_warnings=join_warnings, lum_note=lum_note,
                        viols=validate_ir(ir))


def units_check(meta: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """「单位识别」判据的**唯一口径**（两个宿主都调它，不各自写一份）。

    旧口径是 ``bool(meta["dxf_units"])``：只要有单位就算过。于是「header 写 mm、
    几何按 cm 画」的图被按 mm 读、读到 0 个房间，这一项照样打勾（实测 2026-10-28）。
    现在的口径看**单位与几何对不对得上**，判据取解析器给的 ``_meta.units_source``：

    - ``config`` / ``header``：单位有明确出处，过；
    - ``geometry_fallback``：header 撒谎、已按几何兜底，兜底后读出房间才算过；
    - ``config_fallback``：header 没声明、退回配置默认值，读出房间才算过；
    - 其他 / 缺失：不过。

    ``note`` 是调用方补的上下文（比如平台侧要标「用的是默认配置」）。
    """
    units = meta.get("dxf_units")
    source = meta.get("units_source") or "unknown"
    rooms = int(meta.get("rooms", 0) or 0)

    if source == "geometry_fallback":
        passed = rooms > 0
        detail = (f"header 声明 {meta.get('units_header')} 与几何量级不符，"
                  f"已按 {units} 兜底解析（已折算为米）{note}")
    elif source == "config_fallback":
        passed = rooms > 0
        detail = (f"DXF 没声明单位，按配置 {units} 解析（已折算为米）；"
                  f"读不出房间时请显式给 units{note}")
    elif source in ("config", "header"):
        where = "配置指定" if source == "config" else "header $INSUNITS 声明"
        passed = bool(units)
        detail = f"按 {units} 解析（{where}，已折算为米）{note}"
    else:
        passed = False
        detail = f"单位未知（{units!r}）{note}"
    return {"name": "单位识别", "passed": passed, "detail": detail}


def summarize_ir(ir: Dict[str, Any], *,
                 join_warnings: Optional[list] = None,
                 lum_note: Optional[str] = None,
                 viols: Optional[list] = None) -> Dict[str, Any]:
    """IR → 摘要（纯函数：不读文件、不解析——解析步在 `summarize_plan` 或壳侧）。

    刀B（2026-10-19）收编后的**唯一摘要口径**：壳侧 `core.summarize` 薄壳转调本入口。
    三块从壳侧补进来的语义（一次带测试，不偷换）：
    ① 家具排除双口径 `furniture`（`EXCLUDED_LINEARC` 排柜/架：候选/采用/排除）；
    ② 每房 `bbox_m` 与 `ceiling_h_m`（灯位 z → space.ceil_h → null）；
    ③ 单主房 `main_room`（口径＝那边 `room_of`，含它自己的灯位三数）。
    `viols=None` → 自己调 `optiflow.validator.validate_ir`；vendor 方不想带校验就传 `[]`。
    """
    _ensure_importable()
    from optiflow.adapters.dialux._chain import polygon_area

    join_warnings = list(join_warnings or [])
    if viols is None:
        from optiflow.validator import validate_ir
        viols = validate_ir(ir)

    spaces = [s for st in ir.get("storeys", []) for s in st.get("spaces", [])]
    rooms = [s for s in spaces if not str(s.get("name", "")).startswith("家具_")]
    lums = [lm for s in spaces for lm in s.get("luminaires", [])]

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

    # 七项完整性检查（逐条给结论，不打包成一个布尔）：平台原六项 ＋ 并集进来的「房间轮廓点 ≥ 3」（已拍板）
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
         "detail": f"{len(lums)} 盏" + ("" if lum_note is None else "（未提供灯具图）")},
        {"name": "单位识别", **units_check(meta)},
        # 第七项＝并集进来的（Windows 独有、平台原缺）：退化多边形守卫，扩到全体真房间
        {"name": "房间轮廓点 ≥ 3",
         "passed": bool(rooms) and min(len(r.get("polygon") or []) for r in rooms) >= 3,
         "detail": (f"最少 {min(len(r.get('polygon') or []) for r in rooms)} 点"
                    if rooms else "无房间")},
    ]

    return {
        "room_count": len(rooms),
        "rooms": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "vertices": len(r.get("polygon") or []),
                "area_m2": round(polygon_area(r["polygon"]), 2) if r.get("polygon") else None,
                "bbox_m": _bbox(r.get("polygon") or []),
                "ceiling_h_m": _ceiling_h(r),
            }
            for r in rooms
        ],
        "vertices_total": sum(len(r.get("polygon") or []) for r in rooms),
        "area_total_m2": area_total,
        "furniture_candidates": meta.get("furniture"),
        "furniture": _furniture_block(spaces),
        "main_room": _main_room(ir),
        "luminaires_total": len(lums),
        "luminaires_by_kind": dict(collections.Counter(lm.get("kind", "unknown") for lm in lums)),
        "luminaires_by_shape": dict(collections.Counter(shape_of(lm.get("symbol", "")) for lm in lums)),
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
