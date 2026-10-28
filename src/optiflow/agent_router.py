# -*- coding: utf-8 -*-
"""对话层语义路由：一句话 → 动作名 ＋ **参数** ＋ 缺项 ＋ 每项来源。

与壳侧 `standalone/command_router.py` 的分工：那边是「少点两下按钮」的粗路由，
只出命令名、`params` 恒空；这里是带参数的细路由，话里说过的数字与条件要被听进去。
两处命令名一致，都指向同一张注册表。

四条硬约束（改之前先读）：
① **路径类参数不从话里猜**。「这张图纸」「那个结果包」映射不到文件，进 `missing` 点名要。
② **每项参数要交代来源**（`sources`：text / context / context.ir / context.criteria），
   让人能查「这个 300 lx 是用户说的，还是系统自己加的」。
③ 抽不到就是抽不到，不填默认值冒充用户意图；默认值由动作自己在调用时补。
④ 无命中给命令清单，不猜。

不接模型：规则可离线、可解释、零延迟。要接模型是上层的事，这一层只做确定性部分。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 动作词表（顺序即优先级：越具体的动作越前）。与壳侧同一套命令名。
_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("dialux.real.send_luminaire", ("送灯", "送进 dialux", "送进dialux", "导入 dialux")),
    ("dialux.real.ugr", ("眩光", "ugr")),
    ("dialux.real.export_report", ("导出报表", "打印报表")),
    ("plan.select_luminaire", ("布灯", "选灯", "排灯", "挑灯", "灯具数量", "几盏灯", "灯光布置")),
    ("plan.light_backcalc", ("反推", "光通量", "功率天花板")),
    ("report.assemble_from_pdf", ("抽数", "报表 pdf", "报表pdf", "pdf 抽")),
    ("report.metrics", ("指标", "数据看板")),
    ("report.assemble", ("报告", "说明书", "出方案", "导出方案")),
    ("dialux.one_shot", ("一条龙", "全流程", "一键", "从头到尾")),
    ("dialux.overlay", ("核对图", "叠加图", "overlay")),
    ("dialux.inputs", ("stf", "建模输入", "输入文件", "dialux 输入")),
    ("plan.summary", ("摘要", "完整性检查", "房间检查")),
    ("dialux.parse", ("解析", "识别", "读图", "导入图纸", "图纸导入")),
)

_AVAILABLE: Tuple[str, ...] = tuple(cmd for cmd, _ in _RULES)

#: 每个动作的参数清单（必填 / 可选），与 `commands.py` 的 `Param(required=...)` 同口径。
#: 不在这张表里的动作：只做路由、不抽参数，`note` 里说明「参数待执行前补齐」。
_PARAMS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "dialux.parse": (("dxf",), ("config", "dxf_lum", "ceil_h")),
    "plan.summary": (("dxf", "config"), ("dxf_lum", "ceil_h")),
    "plan.light_backcalc": (("area_m2", "target_lux"), ("cu", "mf", "lpd_cap", "count")),
    "plan.select_luminaire": (
        ("area_m2", "target_lux"),
        ("count", "cct_k", "max_power_w", "cu", "mf", "lpd_cap", "index_path", "limit"),
    ),
    "report.metrics": (("package",), ("criteria",)),
    "report.assemble": (("package",), ("criteria", "price", "project_name", "generated_on")),
    "report.assemble_from_pdf": (("report_pdf",), ("criteria",)),
    "dialux.real.send_luminaire": (("luminaire",), ("count_x", "count_y")),
}

#: 能从文本抽的参数。路径类（dxf/config/package/...) 一律不在——见模块 docstring ①。
_AREA = re.compile(r"(\d+(?:\.\d+)?)\s*(?:平米|平方米|平方|㎡|m2|m²)", re.I)
_LUX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:lx|勒克斯|勒)", re.I)
_CCT = re.compile(r"(\d{4})\s*k\b", re.I)
_COUNT = re.compile(r"(\d+)\s*(?:盏|个灯|只灯|颗灯)")
_POWER = re.compile(r"(\d+(?:\.\d+)?)\s*w\b", re.I)


def _params_from_text(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if m := _AREA.search(text):
        out["area_m2"] = float(m.group(1))
    if m := _LUX.search(text):
        out["target_lux"] = float(m.group(1))
    if m := _CCT.search(text):
        out["cct_k"] = int(m.group(1))
    if m := _COUNT.search(text):
        out["count"] = int(m.group(1))
    if m := _POWER.search(text):
        out["max_power_w"] = float(m.group(1))
    return out


def _main_room_area(ir: Dict[str, Any]) -> Optional[float]:
    """IR 主房面积（与 `plan_summary._main_room` 同口径：先排家具伪空间、再按面积最大）。"""
    from optiflow.geometry import polygon_area
    from optiflow.plan_summary import _is_furniture_space

    spaces = (ir.get("storeys") or [{}])[0].get("spaces") or []
    real = [s for s in spaces if not _is_furniture_space(s)]
    pool = real or spaces
    best = max(pool, key=lambda s: polygon_area(s.get("polygon") or []), default=None)
    if not best:
        return None
    area = polygon_area(best.get("polygon") or [])
    return round(area, 2) if area > 0 else None


def _target_lux_from_criteria(criteria: Dict[str, Any]) -> Optional[float]:
    """判据表里的维持平均照度标准值（按场景顺序取第一个有值的）。"""
    for scene in ((criteria or {}).get("scenes") or {}).values():
        for rule in scene.get("criteria") or []:
            if rule.get("metric") == "illuminance_avg" and rule.get("value") is not None:
                return float(rule["value"])
    return None


def _from_context(context: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """上下文补全：直接给的值优先，其次从 IR 算面积、从判据取目标照度。"""
    got: Dict[str, Any] = {}
    src: Dict[str, str] = {}
    if not context:
        return got, src
    for key in ("area_m2", "target_lux", "cct_k", "count", "max_power_w"):
        if context.get(key) is not None:
            got[key] = context[key]
            src[key] = "context"
    if context.get("ir"):
        area = _main_room_area(context["ir"])
        if area is not None and "area_m2" not in got:
            got["area_m2"] = area
            src["area_m2"] = "context.ir"
    if context.get("criteria"):
        lux = _target_lux_from_criteria(context["criteria"])
        if lux is not None and "target_lux" not in got:
            got["target_lux"] = lux
            src["target_lux"] = "context.criteria"
    return got, src


def route(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一句话 → {command, params, missing, confidence, sources, available, note}。

    `command` 为 None 表示没听出要做什么，此时 `available` 给命令清单。
    `missing` 只列**该动作的必填项**里没拿到的；路径类必填项永远会出现在这里。
    """
    raw = (text or "").strip()
    empty = {"command": None, "params": {}, "missing": [], "confidence": 0.0,
             "sources": {}, "available": list(_AVAILABLE)}
    if not raw:
        return {**empty, "note": "没听到要做的事"}

    low = raw.lower()
    hits: List[Tuple[str, int]] = []
    for cmd, words in _RULES:
        matched = sum(1 for w in words if w.lower() in low)
        if matched:
            hits.append((cmd, matched))
    if not hits:
        return {**empty, "note": "没匹配到命令，下面是可以做的事"}
    hits.sort(key=lambda item: -item[1])
    command = hits[0][0]
    matched = hits[0][1]

    from_text = _params_from_text(raw)
    from_ctx, ctx_src = _from_context(context)

    spec = _PARAMS.get(command)
    if spec is None:
        return {
            "command": command, "params": {}, "missing": [], "sources": {},
            "confidence": round(min(0.6 + 0.1 * matched, 0.95), 2),
            "available": list(_AVAILABLE),
            "note": "该动作的参数没做规则抽取，执行前请按命令表补齐",
        }

    required, optional = spec
    allowed = set(required) | set(optional)
    params: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for key in allowed:
        if key in from_text:                      # 用户说过的优先
            params[key] = from_text[key]
            sources[key] = "text"
        elif key in from_ctx:
            params[key] = from_ctx[key]
            sources[key] = ctx_src.get(key, "context")

    missing = [k for k in required if k not in params]
    confidence = min(0.6 + 0.1 * matched + (0.1 if not missing else 0.0), 0.95)
    note_bits = []
    if missing:
        note_bits.append("还缺：" + "、".join(missing))
    if "area_m2" in params and sources.get("area_m2") == "context.ir":
        note_bits.append("面积取自 IR 主房")
    if "target_lux" in params and sources.get("target_lux") == "context.criteria":
        note_bits.append("目标照度取自判据表")
    return {
        "command": command,
        "params": params,
        "missing": missing,
        "confidence": round(confidence, 2),
        "sources": sources,
        "available": list(_AVAILABLE),
        "note": "；".join(note_bits),
    }


def describe(commands: Sequence[str]) -> List[str]:
    """给界面的命令清单（一行一条，含命令名）。`available` 用它拼展示文案。"""
    return [f"- {c}" for c in commands]
