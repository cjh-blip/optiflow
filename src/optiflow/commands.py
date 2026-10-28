# -*- coding: utf-8 -*-
"""动作注册表 —— 平台层的唯一动作入口。

**为什么要有它**：现在四个前端（`api.py` 的 HTTP、`mcp.py`、`plan_summary` 与
`report_assembler` 两个 `-m` CLI、以及外部壳）各自直接调 core 函数，加第五个前端
（Hana / DSH / agent 直连）就得多写一遍。这里把动作登记成一张表，前端一律**查表派发**。

契约（唯一权威文本：`docs/action-contract.md`，本表是它的可执行版本）：

- 每个动作一条：`id / handler / params / returns / description`
- `params` 用轻量声明（名 / 类型 / 必填 / 默认 / 说明），不引 pydantic：
  这张表要能被 CLI、文档生成、以及别的语言直接渲染，越素越好
- **失败一律抛异常**，由调用方决定怎么呈现（与 `plan_summary` 的库函数同一纪律：
  库不管用户界面，壳才管）
- 路径类参数由**调用方**给绝对路径；本表不做 cwd 猜测

CLI：

    python -m optiflow.commands list
    python -m optiflow.commands show plan.summary
    python -m optiflow.commands call plan.summary --dxf X.dxf --config C.json
    python -m optiflow.commands call report.assemble --params '{"package": {...}}'
"""
from __future__ import annotations

import argparse
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple


# --------------------------------------------------------------------- 声明
@dataclass(frozen=True)
class Param:
    name: str
    type: str                 # str | int | float | bool | json | path | markdown
    required: bool = True
    default: Any = None
    help: str = ""


@dataclass(frozen=True)
class Command:
    id: str
    handler: Callable[..., Any]
    params: Tuple[Param, ...] = ()
    returns: str = ""
    description: str = ""
    group: str = "core"

    def spec(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "group": self.group,
            "description": self.description,
            "returns": self.returns,
            "params": [
                {"name": p.name, "type": p.type, "required": p.required,
                 "default": p.default, "help": p.help}
                for p in self.params
            ],
        }


class CommandError(ValueError):
    """注册表层的错误（未知 id、参数不对）。

    继承 `ValueError` 是有意的：HTTP 层把 ValueError 映射成 **400**（参数/请求错），
    而不是 409（状态冲突）。带可行动信息，不扔裸 KeyError。
    """


# ----------------------------------------------------------------- service 单例
#: `PlatformService` 要拿在手里反复用（它带输出根与适配器注册表）。惰性建一个，
#: 免得每次调用都重建；要换输出根就清掉它。
_SERVICE: Optional[Any] = None


def _service() -> Any:
    global _SERVICE
    if _SERVICE is None:
        from optiflow.service import build_default_service

        _SERVICE = build_default_service()
    return _SERVICE


# ----------------------------------------------------------------- 适配函数
def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cmd_plan_summary(*, dxf: str, config: str, dxf_lum: Optional[str] = None,
                      ceil_h: Optional[float] = None) -> Dict[str, Any]:
    from optiflow.plan_summary import summarize_plan

    data = summarize_plan(dxf, dxf_lum, config, ceil_h)
    # 回执约定（见 docs/action-contract.md）：state 给关键中间量，evidence 给输入出处
    return {
        **data,
        "state": {
            "room_count": data.get("room_count"),
            "vertices_total": data.get("vertices_total"),
            "luminaires_total": data.get("luminaires_total"),
            "checks_passed": data.get("checks_passed"),
        },
        "evidence": {"inputs": {"dxf": dxf, "dxf_lum": dxf_lum, "config": config, "ceil_h": ceil_h}},
    }


def _cmd_plan_light_backcalc(*, area_m2: float, target_lux: float,
                             cu: Optional[float] = None, mf: Optional[float] = None,
                             lpd_cap: Optional[float] = None,
                             count: Optional[int] = None) -> Dict[str, Any]:
    """选灯反推（库函数 `optiflow.light_selection.backcalc` 的注册表入口）。

    给面积与目标照度，算回所需总光通量、功率天花板、单灯区间；
    不排灯位、不推荐型号（护栏写在模块 docstring 与 description 里）。
    """
    from optiflow.light_selection import backcalc

    return backcalc(area_m2=area_m2, target_lux=target_lux, cu=cu, mf=mf,
                    lpd_cap=lpd_cap, count=count)


def _cmd_plan_route_intent(*, text: str, context: Any = None) -> Dict[str, Any]:
    """一句话 → 动作名＋参数＋缺项（对话层语义路由的注册表入口）。

    为什么要有这个：壳侧那套路由器只出命令名、参数恒空；对话层要让「20 平米、4000K」
    这类话里的条件被听进去，口径得有一处。参数来源逐项记在 `sources` 里，
    路径类参数永不从话里猜（进 `missing`）。
    """
    from optiflow.agent_router import route

    ctx = _read_json(context) if isinstance(context, str) else context
    return route(text, ctx if isinstance(ctx, dict) else None)


def _cmd_plan_select_luminaire(*, area_m2: float, target_lux: float,
                               count: Optional[int] = None,
                               cct_k: Optional[int] = None,
                               max_power_w: Optional[float] = None,
                               cu: Optional[float] = None, mf: Optional[float] = None,
                               lpd_cap: Optional[float] = None,
                               index_path: Optional[str] = None,
                               limit: int = 5) -> Dict[str, Any]:
    """选灯（库函数 `optiflow.luminaire_selection.select_for_room` 的注册表入口）。

    反推单灯光通量后到灯具库里挑候选；不排灯位、不送灯（送灯走 dialux.real.*）。
    """
    from optiflow.luminaire_selection import select_for_room

    return select_for_room(area_m2=area_m2, target_lux=target_lux, count=count,
                           cct_k=cct_k, max_power_w=max_power_w, cu=cu, mf=mf,
                           lpd_cap=lpd_cap, index_path=index_path, limit=limit)


def _cmd_report_assemble(*, package: Any, criteria: Any = None, price: Any = None,
                         project_name: str = "会议室照明设计",
                         generated_on: Optional[str] = None) -> Dict[str, Any]:
    from optiflow.report_assembler import assemble_report

    pkg = _read_json(package) if isinstance(package, str) else package
    crit = (_read_json(criteria) if isinstance(criteria, str) else criteria) if criteria else None
    prc = (_read_json(price) if isinstance(price, str) else price) if price else None
    text = assemble_report(pkg, criteria=crit, price=prc, project_name=project_name,
                           generated_on=generated_on)
    blocks = [b for b in ("1. 灯具选型列表", "2. 照明效果", "3. 渲染效果", "4. 对标国标",
                          "5. 价格", "6. 节能", "7. 结论与未取证项") if b in text]
    return {
        "markdown": text,
        "chars": len(text),
        "state": {"blocks": len(blocks), "chars": len(text)},
        "evidence": {"inputs": {"package": str(package) if not isinstance(package, dict) else "(object)",
                                "criteria": str(criteria) if criteria and not isinstance(criteria, dict) else
                                            ("(object)" if criteria else None)}},
    }


def _cmd_report_metrics(*, package: Any, criteria: Any = None) -> Dict[str, Any]:
    """六项指标板（**数据**，不是 markdown）—— 界面、工具、跳语言断言都吃它。"""
    from optiflow.report_assembler import metrics_rows

    pkg = _read_json(package) if isinstance(package, str) else package
    crit = (_read_json(criteria) if isinstance(criteria, str) else criteria) if criteria else None
    rows = metrics_rows(pkg, crit)
    unverified = [r["label"] for r in rows if r["value"] is None]
    return {
        "rows": rows,
        "count": len(rows),
        "state": {"rows": len(rows), "unverified": unverified},
        "evidence": {"inputs": {"package": str(package) if not isinstance(package, dict) else "(object)",
                                "criteria": str(criteria) if criteria and not isinstance(criteria, dict) else
                                            ("(object)" if criteria else None)}},
    }


def _cmd_report_render_docx(*, markdown: str, out: str,
                            reference: Optional[str] = None) -> Dict[str, Any]:
    from optiflow.report_assembler import markdown_to_docx

    text = markdown
    if not text.lstrip().startswith("#") and Path(markdown).is_file():
        text = Path(markdown).read_text(encoding="utf-8")
    path = markdown_to_docx(text, Path(out), reference=Path(reference) if reference else None)
    return {"docx": str(path), "bytes": Path(path).stat().st_size}


def _cmd_ir_validate(*, ir: Any) -> Dict[str, Any]:
    from optiflow.validator import validate_ir

    data = _read_json(ir) if isinstance(ir, str) else ir
    viols = validate_ir(data)
    return {
        "count": len(viols),
        "halt": [v for v in viols if v.get("severity") == "HALT"],
        "violations": viols,
    }


def _pt_xy(pt: Any) -> Tuple[float, float]:
    """polygon 点可能是 [x, y] 或带 x/y 属性的对象，统一取二维。"""
    if hasattr(pt, "x") and hasattr(pt, "y"):
        return float(pt.x), float(pt.y)
    return float(pt[0]), float(pt[1])


def _cmd_furniture_from_ir(*, ir: Any) -> Dict[str, Any]:
    """IR 家具记录 → dc 真机链 `_validate_furniture` 认的四字段参数。

    为什么要平台统一做：真机实测（2026-10-28）发现 IR 家具字段与 dc 校验
    要求对不上，当时是现场手拼。若每个前端各拼一套，bbox 口径（起点、
    顺序、是否外接矩形）会十人十样。所以口径收敛到这里：

    - ``bbox`` 有就用，没有就从 ``polygon`` 算 ``[xmin, ymin, xmax, ymax]``；
    - ``room_id`` 空（解析时未归属到房间）直接报错点名，不静默跳过；
    - 输出只留 dc 认的四字段，多余字段剥掉。
    """
    data = _read_json(ir) if isinstance(ir, str) else ir
    if not isinstance(data, dict):
        raise CommandError("furniture.from_ir：IR 必须是对象")
    entries: list = []
    for storey in data.get("storeys") or []:
        for space in (storey or {}).get("spaces") or []:
            for f in (space or {}).get("furniture") or []:
                if isinstance(f, dict):
                    entries.append(f)
    if not entries:
        raise CommandError("furniture.from_ir：IR 里没有家具记录（storeys[*].spaces[*].furniture 为空）")

    items: list = []
    for f in entries:
        fid = f.get("id")
        if not fid:
            raise CommandError("furniture.from_ir：家具记录缺 id")
        room_id = f.get("room_id")
        if not room_id:
            raise CommandError(
                f"furniture.from_ir：家具 {fid} 未归属到任何房间（room_id 为空），"
                "请指定房间或从输入剔除")
        bbox = f.get("bbox")
        if not bbox:
            pts = [_pt_xy(p) for p in (f.get("polygon") or [])]
            if len(pts) < 3:
                raise CommandError(f"furniture.from_ir：家具 {fid} 既无 bbox 也无可用 polygon")
            bbox = [min(p[0] for p in pts), min(p[1] for p in pts),
                    max(p[0] for p in pts), max(p[1] for p in pts)]
        if len(bbox) != 4:
            raise CommandError(f"furniture.from_ir：家具 {fid} 的 bbox 必须有 4 个值")
        h = f.get("height_m")
        if h is None or float(h) <= 0:
            raise CommandError(f"furniture.from_ir：家具 {fid} 的 height_m 必须为正")
        items.append({
            "id": fid,
            "room_id": room_id,
            "bbox": [float(v) for v in bbox],
            "height_m": float(h),
        })
    return {"count": len(items), "items": items}


def _cmd_service_capabilities() -> Dict[str, Any]:
    """能力目录：动作表 ＋ 适配器能力声明，给 agent 用来枚举「这副牌有什么」。"""
    return {"adapters": _service().capabilities(), "commands": list_commands()}


def _cmd_service_import_dxf_b64(*, filename: str, content_base64: str,
                                config: Any = None) -> Dict[str, Any]:
    """与 HTTP `/import-dxf` **同形**（收到的是 base64 正文）。
    存在的理由：HTTP 与 CLI/agent 两种传输给的输入形状天然不同，与其在端点里
    再写一遍 service 调用，不如让两条路都落到同一个 handler 上。
    """
    cfg = _read_json(config) if isinstance(config, str) else config
    return _service().import_dxf(filename, content_base64, cfg)


def _cmd_report_assemble_from_pdf(*, report_pdf: str, criteria: Any = None,
                                  criteria_path: Optional[str] = None, price: Any = None,
                                  images: Optional[list] = None,
                                  project_name: str = "会议室照明设计") -> Dict[str, Any]:
    """与 HTTP `/assemble-report` **同形**：从 DIALux 报表 PDF 抽数并出报告。"""
    crit = (_read_json(criteria) if isinstance(criteria, str) else criteria) if criteria else None
    prc = (_read_json(price) if isinstance(price, str) else price) if price else None
    return _service().assemble_report_pdf(
        report_pdf, criteria=crit, criteria_path=criteria_path or None,
        price=prc, render_images=images or None, project_name=project_name)


def _cmd_service_import_dxf(*, path: str, config: Optional[str] = None,
                            name: Optional[str] = None) -> Dict[str, Any]:
    """DXF 文件 → IR ＋ 完整性检查（替调用方读文件并转 base64）。"""
    content = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    cfg = _read_json(config) if config else None
    return _service().import_dxf(name or Path(path).name, content, cfg)


# --------------------------------------------------------------------- 注册表
#: 只收「纯函数、无外部依赖副作用」的动作。service 实例类与真机链动作在
#: `docs/action-contract.md` 的「待收编」一节列着 —— 收编它们要先定「谁持有 service」。
_COMMANDS: Tuple[Command, ...] = (
    Command(
        id="plan.summary",
        handler=_cmd_plan_summary,
        group="plan",
        description="房间 DXF（＋可选灯具图 DXF ＋解析配置）→ 摘要四数 ＋ 七项完整性检查（六项并集＋轮廓点≥3）＋ 家具双口径/单主房 ＋ 校验分桶",
        returns="{room_count, vertices_total, area_total_m2, furniture_candidates, furniture{count,adopted,excluded,...}, main_room{points,area_m2,bbox_m,ceiling_h_m,luminaires{}}, luminaires_total, luminaires_by_kind, luminaires_by_shape, checks[]（七项）, checks_passed, validation{...}}",
        params=(
            Param("dxf", "path", help="房间图 DXF 绝对路径"),
            Param("config", "path", help="解析配置 JSON 绝对路径"),
            Param("dxf_lum", "path", required=False, help="灯具图 DXF（不给则灯位为 0 并在检查里标注）"),
            Param("ceil_h", "float", required=False, help="净高兜底（米）"),
        ),
    ),
    Command(
        id="plan.light_backcalc", handler=_cmd_plan_light_backcalc, group="plan",
        description="选灯反推：面积＋目标照度（＋CU/MF/LPD 上限/灯数）→ 总光通量、功率天花板、单灯区间；不排灯位、不推荐型号",
        returns="{total_flux_lm, power_budget_w, per_lamp{flux_lm,power_w}, params_used, state, evidence}",
        params=(
            Param("area_m2", "float", help="房间面积（㎡）"),
            Param("target_lux", "float", help="目标维持平均照度（lx，取项目判据值）"),
            Param("cu", "float", required=False, help="利用系数 0~1（缺省 0.6，宽配光浅色顶棚可上取）"),
            Param("mf", "float", required=False, help="维护系数 0~1（缺省 0.8）"),
            Param("lpd_cap", "float", required=False, help="LPD 上限 W/㎡（给了才算功率天花板）"),
            Param("count", "int", required=False, help="预计灯数（给了才摊单灯光通量/功率）"),
        ),
    ),
    Command(
        id="plan.select_luminaire",
        handler=_cmd_plan_select_luminaire,
        group="plan",
        description=("选灯：面积＋目标照度（＋灯数/色温/功率上限）→ 按单灯光通量匹配的灯具候选；"
                     "色温与功率是硬筛，流明差距只排序，不排灯位、不送灯"),
        returns="{candidates[], backcalc, state, evidence}",
        params=(
            Param("area_m2", "float", help="房间面积（㎡）"),
            Param("target_lux", "float", help="目标维持平均照度（lx，取项目判据值）"),
            Param("count", "int", required=False, help="预计灯数（选单灯规格必需）"),
            Param("cct_k", "int", required=False, help="色温硬筛（K，如 4000）"),
            Param("max_power_w", "float", required=False, help="单灯功率上限（W）"),
            Param("cu", "float", required=False, help="利用系数 0~1（缺省 0.6）"),
            Param("mf", "float", required=False, help="维护系数 0~1（缺省 0.8）"),
            Param("lpd_cap", "float", required=False, help="LPD 上限 W/㎡"),
            Param("index_path", "path", required=False, help="灯具库索引 JSON（缺省查环境变量/包内）"),
            Param("limit", "int", required=False, default=5, help="候选数量上限"),
        ),
    ),
    Command(
        id="plan.route_intent",
        handler=_cmd_plan_route_intent,
        group="plan",
        description=("对话层语义路由：一句话 → 动作名＋抽到的参数＋缺项；参数来源逐项交代"
                     "（text/context/context.ir/context.criteria），路径类参数不猜；无命中给命令清单"),
        returns=("{command, params, missing, confidence, sources, available, note}"),
        params=(
            Param("text", "str", help="用户原话"),
            Param("context", "json", required=False,
                  help="上下文：可直接给 area_m2/target_lux/cct_k，或给 {ir, criteria} 让我推"),
        ),
    ),
    Command(
        id="report.assemble",
        handler=_cmd_report_assemble,
        group="report",
        description="结果包（＋判据表/价格）→ 七块 Markdown 报告正文",
        returns="{markdown, chars}",
        params=(
            Param("package", "json", help="结果包：绝对路径或已解析的对象"),
            Param("criteria", "json", required=False, help="判据表：绝对路径或对象"),
            Param("price", "json", required=False, help="价格表：绝对路径或对象"),
            Param("project_name", "str", required=False, default="会议室照明设计"),
            Param("generated_on", "str", required=False, help="生成日期（缺省用今天）"),
        ),
    ),
    Command(
        id="report.metrics",
        handler=_cmd_report_metrics,
        group="report",
        description="六项指标板（数据）：实测值/判据阈值/判定/出处；UGR 取最不利值",
        returns="{rows[], count}｜每行含 evidence{standard,table,clause,page_pdf,page_print,quote,status}（无判据或三者不全时为 null）",
        params=(
            Param("package", "json", help="结果包：绝对路径或已解析的对象"),
            Param("criteria", "json", required=False, help="判据表：绝对路径或对象"),
        ),
    ),
    Command(
        id="report.render_docx",
        handler=_cmd_report_render_docx,
        group="report",
        description="Markdown → DOCX（需要 pandoc；缺了会明确报错，Markdown 仍可用）",
        returns="{docx, bytes}",
        params=(
            Param("markdown", "markdown", help="Markdown 正文，或一份 .md 的路径"),
            Param("out", "path", help="输出 DOCX 绝对路径"),
            Param("reference", "path", required=False, help="中文 reference 模板（可选）"),
        ),
    ),
    Command(
        id="ir.validate",
        handler=_cmd_ir_validate,
        group="plan",
        description="IR 校验：jsonschema ＋ 七条业务规则，返回逐条违规",
        returns="{count, halt[], violations[]}",
        params=(Param("ir", "json", help="IR：绝对路径或已解析的对象"),),
    ),
    Command(
        id="furniture.from_ir",
        handler=_cmd_furniture_from_ir,
        group="furniture",
        description="IR 家具记录 → 真机链四字段参数（id/room_id/bbox/height_m）；bbox 缺失从 polygon 算，room_id 为空直接报错点名；口径只此一处",
        returns="{count, items[{id,room_id,bbox,height_m}]}",
        params=(Param("ir", "json", help="IR：绝对路径或已解析的对象"),),
    ),
    Command(
        id="service.import_dxf_b64",
        handler=_cmd_service_import_dxf_b64,
        group="plan",
        description="DXF 正文（base64）→ IR ＋ 完整性检查；与 HTTP `/import-dxf` 同形",
        returns="PlatformService.import_dxf 的返回",
        params=(
            Param("filename", "str", help="文件名（写进 IR 的来源标记）"),
            Param("content_base64", "str", help="DXF 文件的 base64 正文"),
            Param("config", "json", required=False, help="解析配置：对象或路径"),
        ),
    ),
    Command(
        id="report.assemble_from_pdf",
        handler=_cmd_report_assemble_from_pdf,
        group="report",
        description="DIALux 报表 PDF → 抽数 → 七块报告；与 HTTP `/assemble-report` 同形",
        returns="PlatformService.assemble_report_pdf 的返回",
        params=(
            Param("report_pdf", "path", help="报表 PDF 绝对路径"),
            Param("criteria", "json", required=False, help="判据表：对象或路径"),
            Param("criteria_path", "path", required=False, help="判据表路径（与 criteria 二选一）"),
            Param("price", "json", required=False, help="价格：对象或路径"),
            Param("images", "json", required=False, help="渲染图列表（路径或 {path,caption}）"),
            Param("project_name", "str", required=False, default="会议室照明设计"),
        ),
    ),
    Command(
        id="service.capabilities",
        handler=_cmd_service_capabilities,
        group="meta",
        description="能力目录：本表 ＋ 适配器能力声明（agent 用来枚举可做什么）",
        returns="{adapters[], commands[]}",
    ),
    Command(
        id="service.import_dxf",
        handler=_cmd_service_import_dxf,
        group="plan",
        description="DXF 文件 → IR ＋ 完整性检查（读文件并转 base64，替调用方省一步）",
        returns="{...PlatformService.import_dxf 的返回}",
        params=(
            Param("path", "path", help="DXF 绝对路径"),
            Param("config", "path", required=False, help="解析配置 JSON（可选）"),
            Param("name", "str", required=False, help="文件名（缺省取路径末段）"),
        ),
    ),
)

COMMANDS: Dict[str, Command] = {c.id: c for c in _COMMANDS}
if len(COMMANDS) != len(_COMMANDS):  # 注册期就把重复 id 拦下来，别等调用时才发现
    raise RuntimeError("命令 id 重复：" + ", ".join(sorted(COMMANDS)))


def register(command: Command, *, replace: bool = False) -> Command:
    """外部（应用侧壳）注册动作 —— 让配套壳把自己那几步也放进**同一张表**。

    为什么要有它：平台不该依赖某个壳（依赖方向是 app → 平台）；但"动作只定义一次"要求
    壳的动作也进这张表。于是平台只给接口，壳自己注册：

        from optiflow.commands import Command, Param, register
        register(Command(id="dialux.parse", handler=..., group="dialux", ...))

    默认**不允许覆盖**已有 id（防手滑把平台动作换了实现）；要换实现显式 `replace=True`。
    """
    if not replace and command.id in COMMANDS:
        raise CommandError(f"动作 {command.id} 已存在；要覆盖请显式 replace=True")
    COMMANDS[command.id] = command
    return command


# --------------------------------------------------------------------- 查询
def list_commands() -> Sequence[Dict[str, Any]]:
    """全部动作的规格（CLI / agent / 文档生成 / 外部壳都吃它）。含运行时注册进来的。"""
    return [COMMANDS[k].spec() for k in sorted(COMMANDS)]


def get(command_id: str) -> Command:
    cmd = COMMANDS.get(command_id)
    if cmd is None:
        raise CommandError(f"没有这个动作：{command_id}；可用：" + "、".join(sorted(COMMANDS)))
    return cmd


def _coerce(param: Param, value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        if param.type == "int":
            return int(value)
        if param.type == "float":
            return float(value)
        if param.type == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
        if param.type == "json" and isinstance(value, str):
            return _read_json(value)
    except (TypeError, ValueError):
        raise CommandError(f"动作 {param.name} 参数类型不对（期望 {param.type}，拿到 {value!r}）") from None
    return value


def call(command_id: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """查表派发。参数多给会报错（不静默忽略），少给必填也会报错（说清缺哪个）。"""
    cmd = get(command_id)
    provided = dict(params or {})
    known = {p.name for p in cmd.params}
    extra = sorted(set(provided) - known)
    if extra:
        raise CommandError(f"动作 {command_id} 不认识参数：{'、'.join(extra)}；它认：" + "、".join(sorted(known)))
    kwargs: Dict[str, Any] = {}
    for p in cmd.params:
        if p.name in provided:
            kwargs[p.name] = _coerce(p, provided[p.name])
        elif p.required:
            raise CommandError(f"动作 {command_id} 缺必填参数：{p.name}（{p.help or p.type}）")
        elif p.default is not None:
            kwargs[p.name] = p.default
    return cmd.handler(**kwargs)


# ----------------------------------------------------------------------- CLI
def _parse_kv(pairs: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in pairs:
        if "=" not in item:
            raise CommandError(f"参数要写成 key=value：{item!r}")
        k, v = item.split("=", 1)
        try:                      # 先按 JSON 解（数字/布尔/对象），解不动就当字符串
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optiflow.commands",
                                 description="光枢动作注册表：列出 / 查看 / 调用")
    sub = ap.add_subparsers(dest="verb", required=True)

    sub.add_parser("list", help="列出全部动作")

    p_show = sub.add_parser("show", help="看一个动作的规格")
    p_show.add_argument("id")

    p_call = sub.add_parser("call", help="调用一个动作")
    p_call.add_argument("id")
    p_call.add_argument("--params", help="参数字典（JSON 字符串）")
    p_call.add_argument("kv", nargs="*", help="或写成 key=value（值能当 JSON 解就当 JSON）")

    args = ap.parse_args(argv)

    try:
        if args.verb == "list":
            print(json.dumps(list_commands(), ensure_ascii=False, indent=2))
            return 0
        if args.verb == "show":
            print(json.dumps(get(args.id).spec(), ensure_ascii=False, indent=2))
            return 0

        params: Dict[str, Any] = {}
        if args.params:
            params.update(json.loads(args.params))
        params.update(_parse_kv(args.kv))
        result = call(args.id, params)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except CommandError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # 动作自己抛的：如实报，退出码非 0（shell 层也判得出）
        raw = str(exc).strip()
        msg = f"{type(exc).__name__}：{raw}" if raw else f"{type(exc).__name__}（无错误详情）"
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
