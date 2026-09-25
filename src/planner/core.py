"""规则引擎：IR 校验 + 灯具归属到房间 + 生成 ActionPlan。

规则全部代码化 (见 docs/architecture.md)，模型不介入主链路。

兼容说明（Task 3 兼容层）：
- validate_ir(ir) 已迁移到 src.validator.validate_ir；此处保留 deprecated 薄封装。
- assign_luminaires_to_spaces 改为 join_luminaires 的代理。
"""
from __future__ import annotations

import warnings as _warnings
from typing import List, Tuple

from src.validator import validate_ir as _validator_validate_ir  # noqa  # 同名但包内

CLOSE_TOLERANCE = 1e-6  # 米


def _is_closed(polygon) -> bool:
    if len(polygon) < 3:
        return False
    a = polygon[0][:2]
    b = polygon[-1][:2]
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 < CLOSE_TOLERANCE


def point_in_polygon(point: Tuple[float, float], polygon) -> bool:
    """射线法判断点是否在多边形内（在边上返回 True，见 AC-3 规则3 注释）。"""
    x, y = point
    n = len(polygon)
    if n < 3:
        return False
    # 先快速 bbox reject
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    if x < min(xs) - 1e-9 or x > max(xs) + 1e-9 or y < min(ys) - 1e-9 or y > max(ys) + 1e-9:
        return False
    inside = False
    on_edge = False
    for i in range(n):
        x1, y1 = polygon[i][:2]
        x2, y2 = polygon[(i + 1) % n][:2]
        # 判断是否在边上
        # - 若 y 正好等于 y1 或 y2：射线方向会翻转的经典浮点坑，直接用 on_edge 特判
        if abs((y2 - y1) * (x - x1) - (x2 - x1) * (y - y1)) < 1e-12:
            # 共线，再判断 x 是否在线段 bbox 内，y 同理
            if (min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
                    and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9):
                on_edge = True
                break
        if ((y1 > y) != (y2 > y)):
            denom = (y2 - y1)
            if abs(denom) < 1e-12:
                continue
            x_intersect = (x2 - x1) * (y - y1) / denom + x1
            if x < x_intersect:
                inside = not inside
    return inside or on_edge


def assign_luminaires_to_spaces(ir: dict, lumis: list) -> List[str]:
    """把灯具按坐标归入所属 space；返回告警列表（对 src.planner.join.join_luminaires 的代理）。"""
    from .join import join_luminaires
    return join_luminaires(ir, lumis)


def validate_ir(ir: dict) -> List[str]:
    """DEPRECATED 薄封装：旧返回 list[str]（仅 message 扁平化），新代码用 src.validator.validate_ir。"""
    _warnings.warn(
        "planner.core.validate_ir 已废弃，请改用 src.validator.validate_ir（返回 list[Violation]）",
        DeprecationWarning, stacklevel=2,
    )
    violations = _validator_validate_ir(ir)
    return [v["message"] for v in violations]


def build_action_plan(ir: dict, halt_violations=None) -> list:
    """IR → 结构化 ActionPlan（每条必含 id/type/inputs/preconditions/postconditions）。

    - 若 halt_violations 为空，会调用 validator 自行收集 HALT。
    - 有 HALT 时，在 place_luminaire 之前插入 human_confirm。
    - 末尾固定 run_calculation + export_report。
    - 返回前统一补 title/weight（进度契约，见 ACTION_META）。
    """
    if halt_violations is None:
        try:
            halt_violations = [v for v in _validator_validate_ir(ir) if v.get("severity") == "HALT"]
        except Exception:
            halt_violations = []

    actions: list = []
    counter = 0

    def new_id():
        nonlocal counter
        counter += 1
        return f"a{counter:04d}"

    project_name = (ir.get("project") or {}).get("name", "untitled")
    actions.append({
        "id": new_id(),
        "type": "create_project",
        "inputs": {"name": project_name},
        "preconditions": [],
        "postconditions": ["project_created"],
    })

    for storey in ir.get("storeys", []):
        level = storey.get("level", 1)
        actions.append({
            "id": new_id(),
            "type": "create_storey",
            "inputs": {"level": level, "elevation": storey.get("elevation")},
            "preconditions": ["project_created"],
            "postconditions": [f"storey_level_{level}_exists"],
        })
        for space in storey.get("spaces", []):
            sid = space.get("id")
            actions.append({
                "id": new_id(),
                "type": "create_space",
                "inputs": {
                    "space_id": sid,
                    "name": space.get("name"),
                    "level": level,
                    "polygon": space.get("polygon"),
                    "ceil_h": space.get("ceil_h", 2.8),
                },
                "preconditions": [f"storey_level_{level}_exists"],
                "postconditions": [f"space_{sid}_created"],
            })

            # HALT: 在 place_luminaire 之前插入 human_confirm
            # 合并为一条：列出所有 HALT 的 message
            if halt_violations:
                msgs = [v.get("message", "") for v in halt_violations]
                actions.append({
                    "id": new_id(),
                    "type": "human_confirm",
                    "inputs": {"violations": halt_violations, "message": "\n".join(msgs)},
                    "preconditions": [f"space_{sid}_created"],
                    "postconditions": [f"halt_confirmed_for_{sid}"],
                })

            for li, lum in enumerate(space.get("luminaires", [])):
                lid = lum.get("symbol") or f"lum{li}"
                actions.append({
                    "id": new_id(),
                    "type": "place_luminaire",
                    "inputs": {
                        "space_id": sid,
                        "luminaire_id": lid,
                        "symbol": lum.get("symbol"),
                        "x": lum.get("x"), "y": lum.get("y"), "z": lum.get("z"),
                        "mount": lum.get("mount", "recessed"),
                        "catalog_match": lum.get("catalog_match", True),
                    },
                    "preconditions": [f"space_{sid}_created"] + (
                        [f"halt_confirmed_for_{sid}"] if halt_violations else []
                    ),
                    "postconditions": [f"lum_{lid}_placed_in_{sid}"],
                })

    actions.append({
        "id": new_id(),
        "type": "run_calculation",
        "inputs": {},
        "preconditions": [],
        "postconditions": ["calculation_done"],
    })
    actions.append({
        "id": new_id(),
        "type": "export_report",
        "inputs": {},
        "preconditions": ["calculation_done"],
        "postconditions": ["report_exported"],
    })
    _stamp_progress(actions)
    return actions


# ---------------------------------------------------------------- 进度契约
#
# 每种动作的显示名与相对权重。界面要的是「第 17 条／共 55 条，正在布灯」这种真
# 进度——我们的计划条数是确定的，不像 LLM agent 那样步数未知，所以不需要
# Mrite 那条 5%→95% 的信心曲线（renderer/agent-events.js:81-130），照抄反而是降级。
#
# 权重说明（别把它当实测数据）：当前 ActionPlan 是走 STF 落地的，每条动作实际就
# 是往文本里写几行，耗时都在毫秒级，所以权重基本均匀，只把 human_confirm 记 0
# ——等人的时间不该算进「机器干完了多少」。真正有量级差异的是 UIA 那一段，那批
# 权重在 src/executor/uia/driver.py 的 STEPS 里，是实测的。
ACTION_META = {
    "create_project": ("新建项目", 1.0),
    "create_storey": ("新建楼层", 1.0),
    "create_space": ("建房间", 1.0),
    "human_confirm": ("等人确认", 0.0),
    "place_luminaire": ("布灯", 1.0),
    "run_calculation": ("跑照度计算", 1.0),
    "export_report": ("导出报告", 1.0),
}


def _stamp_progress(actions: list) -> None:
    """给每条动作补 title 与 weight（原地改）。未登记的类型退化成类型名 + 1.0。"""
    for a in actions:
        title, weight = ACTION_META.get(a["type"], (a["type"], 1.0))
        a["title"] = title
        a["weight"] = weight


def plan_total_weight(actions: list) -> float:
    """计划总权重。进度 = 已完成权重 / 总权重；总权重为 0 时调用方自己兜底。"""
    return sum(float(a.get("weight", 1.0)) for a in actions)

