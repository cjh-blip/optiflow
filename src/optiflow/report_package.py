"""报告数据包（report_package）。

评审要看的是报告，报告要的是一份能直接吃的结构化数据，不是散在 plan.json 里的字段。
这个模块把那堆字段收成一件东西，字段名一旦定下就被界面与文档两条线同时消费，
所以**先定字段再填值**（字段表见 docs/report_package_fields.md）。

两条纪律：

1. **每个数字都要能追溯**：要么指向某次 pipeline 结果（room / step），
   要么指向判据表的某一行（standards_cited 里的 standard + 表号）。
   来历不明的数一个都不许有。
2. **未覆盖项不许留白**：UGR / Ra / CCT / 价格 / 节能 / 渲染效果，每一项都要有一条记录，
   写清「未实现」还是「缺数据」，以及缺什么才能做。评审第一眼就是找缺什么。

功率的来源格外标死：现在是从光通量按光效反推的，`power_source = "derived"`，
依据写在 `power_basis`。将来灯具条目带真实额定功率时改成 `"measured"`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .algo.engine import LayoutPlan, RoomPlan
from .ir import TaskSpec

SCHEMA_VERSION = 1

#: 未覆盖项清单。每项都要有：做什么、现在什么状态、缺什么。
#: **删条目要写理由**——留白等于骗评审。
UNCOVERED_ITEMS: tuple = (
    ("ugr", "统一眩光值 UGR",
     "not_implemented",
     "缺灯具配光曲线（IES/LDT 逐角度光强分布）、房间表面反射率实测值、观察点位置；"
     "当前 IR 只有光通量一个标量。配光曲线解析在灯库那条线，接入后才能算"),
    ("ra", "显色指数 Ra",
     "not_implemented",
     "缺光源光谱功率分布（SPD）；当前 IR 没有色度学数据。需要灯具条目带 SPD 或至少带额定 Ra"),
    ("cct", "色温 CCT",
     "not_implemented",
     "缺光源 SPD 或色温参数；当前 IR 的 fixtures 只带光通量与功率，没有色温字段"),
    ("price", "造价",
     "missing_data",
     "缺灯具报价表 / 供应商价格库；算法侧不知道单价，也没有安装与辅材费用口径"),
    ("energy", "节能评估",
     "not_implemented",
     "缺基准能耗模型（对照方案或现行标准允许值）与年运行小时数；没有基准就算不出节能量"),
    ("rendering", "渲染效果",
     "not_implemented",
     "缺 3D 场景几何与材质；渲染由 DIALux 侧出图，本工具只到几何与参数层"),
)


def _luminaire_section(plan: LayoutPlan, task: TaskSpec) -> Dict[str, Any]:
    """灯具清单。功率来源如实标注，不许默认成实测。"""
    rooms: List[RoomPlan] = list(plan.rooms)
    fixtures = [fx for r in rooms for fx in r.fixtures]
    sample = fixtures[0] if fixtures else None

    flux = rooms[0].flux_per_fixture if rooms else None
    power_per = None
    for r in rooms:
        if r.power_total_w is not None and r.fixtures:
            power_per = round(r.power_total_w / len(r.fixtures), 2)
            break

    sources = {r.power_source for r in rooms if r.power_source}
    if len(sources) == 1:
        source = next(iter(sources))
    elif not sources:
        source = "unknown"
    else:
        source = "mixed"

    # 归一成三个词：measured = 灯具条目带的额定功率；derived = 从光通量反推；
    # declared = 调用方直接给的 fixture_power_w。界面按这个判断要不要加"假定"标记。
    if source == "unknown":
        kind = "unknown"
    elif "属性" in source or "properties" in source or "灯具条目" in source:
        kind = "measured"
    elif "fixture_power_w" in source or "指定" in source:
        kind = "declared"
    else:
        kind = "derived"

    total_power = sum(r.power_total_w or 0.0 for r in rooms)
    return {
        "model": (sample.name if sample else None) or task.extra.get("fixture_name"),
        "flux_lm": flux,
        "power_w": power_per,
        "power_source": kind,
        "power_source_detail": source if sources else "无法判定",
        "power_basis": (rooms[0].power_source if rooms else ""),
        "count": len(fixtures),
        "total_power_w": round(total_power, 1),
        "note": ("功率由光通量按假定光效反推，不是灯具额定功率；"
                 "换灯具型号或光效后 LPD 与合规结论都会变"
                 if kind == "derived" else None),
    }


def _standards_cited(plan: LayoutPlan) -> List[Dict[str, Any]]:
    """判据出处表。每个用到的限值一行，报告里直接当附录。"""
    from .algo import standards as stdlib

    rows: List[Dict[str, Any]] = []
    seen = set()
    for room in plan.rooms:
        if not room.standard_key:
            continue
        std = stdlib.ROOM_STANDARDS.get(room.standard_key)
        if std is None:
            continue
        for field_name, label, value in (
            ("illuminance", "照度标准值", std.illuminance_lux),
            ("ugr", "UGR 限值", std.ugr_limit),
            ("u0", "U0 限值", std.u0_limit),
            ("ra", "Ra 限值", std.ra_limit),
            ("lpd_current", "LPD 现行值", std.lpd_current),
            ("lpd_target", "LPD 目标值", std.lpd_target),
        ):
            key = (room.standard_key, field_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "room_type": std.label,
                "field": field_name,
                "label": label,
                "value": value,
                "unit": {"illuminance": "lx", "lpd_current": "W/m2",
                         "lpd_target": "W/m2"}.get(field_name, ""),
                "standard": std.sources.get(field_name, ""),
            })
    return rows


def _verdict(room: RoomPlan) -> Dict[str, Any]:
    """三层判据。**「合规」与「达标」不是一回事**，不能挤成一个布尔。

    - compulsory_ok：**三态** pass / fail / unevaluated。判定集合是全部**强制性**要求：
        LPD 现行值（GB 55015-2021，建筑节能通用规范，表 3.3.7 系列）
        Ra 与 SVM（GB 55016-2021，建筑环境通用规范）
      后两项当前算不了，所以只要有未计算项且没有 fail，整格就是 **unevaluated** 并列出
      missing。**绝不因为「没算就是没超」滑成 pass**——那等于告诉读者
      「这间房符合强制规范」，而实际上我们只查了三分之一。
    - standard_pass：照度 / U0 / LPD 目标值按 GB/T 50034 逐项判（推荐性国标），已计算项全过才 true。
    - target_met：LPD 目标值这类更严的推荐线。
    """
    # 强制项：值 = None 表示未计算（不是「没超」）。
    compulsory: Dict[str, Optional[bool]] = {
        "lpd_current": (None if room.lpd_compliant is None else bool(room.lpd_compliant)),
        "ra": None,   # 要 SPD 或额定 Ra，当前 IR 没有
        "svm": None,  # 要频闪参数（SVM），当前 IR 没有
    }
    failed = [k for k, v in compulsory.items() if v is False]
    missing = [k for k, v in compulsory.items() if v is None]
    if failed:
        compulsory_state = "fail"
    elif missing:
        compulsory_state = "unevaluated"
    else:
        compulsory_state = "pass"

    evaluated: Dict[str, bool] = {
        "illuminance_avg": room.predicted_avg_lumen >= room.target_lux - 1e-6,
        "uniformity_u0": room.uniformity_ok,
    }
    if room.lpd_current_limit is not None and room.lpd_compliant is not None:
        evaluated["power_density"] = bool(room.lpd_compliant)
    unevaluated = sorted(k for k, v in room.metric_status.items()
                         if v in ("not_computed", "not_specified", "not_in_standard"))
    return {
        "compulsory_ok": compulsory_state,
        "compulsory_detail": compulsory,
        "compulsory_missing": missing,
        "standard_pass": (None if room.standard_key is None
                          else all(evaluated.values())),
        "target_met": (None if room.lpd_meets_target is None
                       else bool(room.lpd_meets_target)),
        "evaluated": evaluated,
        "unevaluated": unevaluated,
    }


def build_report_package(plan: LayoutPlan, task: TaskSpec,
                         pipeline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一份 LayoutPlan 收成报告数据包。

    pipeline：可选，来自 ServiceJob / PipelineRun 的摘要（job_id / steps / artifacts），
    用来让包里每个数都能指回某次真实运行。没跑过流水线就传 None。
    """
    rooms_out: List[Dict[str, Any]] = []
    shortfalls_all: List[Dict[str, Any]] = []
    for room in plan.rooms:
        rooms_out.append({
            "space_id": room.space_id,
            "room_type": room.room_type or None,
            "standard_key": room.standard_key,
            "standard_basis": room.standard_basis,
            "area_m2": round(room.area, 2),
            "target_lux": room.target_lux,
            "target_basis": room.target_basis,
            "actual_lux": round(room.predicted_avg_lumen, 1),
            "illuminance_ok": room.predicted_avg_lumen >= room.target_lux - 1e-6,
            "u0": round(room.uniformity.u0, 3),
            "u0_required": room.u0_required,
            "u0_basis": room.u0_basis,
            "u0_ok": room.uniformity_ok,
            "lpd": {
                "actual": room.power_density,
                "current_limit": room.lpd_current_limit,
                "target_limit": room.lpd_target_limit,
                "compliant": room.lpd_compliant,
                "meets_target": room.lpd_meets_target,
                "basis": room.lpd_basis,
            },
            "fixture_count": len(room.fixtures),
            "nominal_count": room.lumen_method_count,
            "supplement": room.supplement,
            "metric_status": dict(room.metric_status),
            "verdict": _verdict(room),
            "warnings": list(room.warnings),
        })
        for item in room.shortfalls:
            entry = dict(item)
            entry["space_id"] = room.space_id
            shortfalls_all.append(entry)

    uncovered = [
        {
            "item": key,
            "label": label,
            "status": status,
            "what_is_missing": missing,
        }
        for key, label, status, missing in UNCOVERED_ITEMS
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rooms": rooms_out,
        "standards_cited": _standards_cited(plan),
        "shortfalls": shortfalls_all,
        "luminaire": _luminaire_section(plan, task),
        "uncovered": uncovered,
        "assumptions": list(plan.assumptions),
        "warnings": list(plan.warnings),
        "pipeline": pipeline,
        "totals": {
            "room_count": len(rooms_out),
            "fixture_count": sum(r["fixture_count"] for r in rooms_out),
            "area_m2": round(sum(r["area_m2"] for r in rooms_out), 2),
            "total_power_w": round(sum(r.power_total_w or 0.0 for r in plan.rooms), 1),
            # 三层分开数，**不再给一个冒充全过的 "fully_compliant"**。
            "rooms_compulsory_pass": sum(1 for r in rooms_out
                                          if r["verdict"]["compulsory_ok"] == "pass"),
            "rooms_compulsory_fail": sum(1 for r in rooms_out
                                          if r["verdict"]["compulsory_ok"] == "fail"),
            "rooms_compulsory_unevaluated": sum(
                1 for r in rooms_out if r["verdict"]["compulsory_ok"] == "unevaluated"),
            "rooms_standard_pass": sum(1 for r in rooms_out
                                       if r["verdict"]["standard_pass"] is True),
            "rooms_target_met": sum(1 for r in rooms_out
                                    if r["verdict"]["target_met"] is True),
            "rooms_unevaluated": {
                key: sum(1 for r in rooms_out if key in r["verdict"]["unevaluated"])
                for key in ("ugr", "ra", "uniformity_u0", "illuminance_avg",
                            "power_density")
                if any(key in r["verdict"]["unevaluated"] for r in rooms_out)
            },
        },
    }


__all__ = ["SCHEMA_VERSION", "UNCOVERED_ITEMS", "build_report_package"]
