"""结果包 → 照明设计报告（Markdown 组装器）。

铁律（功能规格 ④.4）：**指标数值必须来自 DIALux 导出**（结果包就是它的机读副本），
本组装器只做组织与文字，不生成任何数值；缺的写「未取证」，绝不补齐。

报告 7 块（对齐第一次答辩 PPT 要素）：
1. 灯具选型列表  2. 照明效果（6 项指标）  3. 渲染效果  4. 对标国标
5. 价格          6. 节能                7. 结论与未取证项

六项指标口径固定，不增不减：维持平均照度 / UGR / U₀ / Ra / CCT / LPD。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["assemble_report"]

STANDARD = "GB/T 50034-2024"

#: 六项指标：(结果包取值路径, 判据表 metric 名, 显示名, 单位)
_METRICS: List[Dict[str, Any]] = [
    {"label": "维持平均照度 Ē", "unit": "lx", "pkg": ("workplane", "illuminance", "avg_lx"), "crit": "illuminance_avg"},
    {"label": "统一眩光值 UGR", "unit": "—", "pkg": ("workplane", "ugr"), "crit": "ugr"},
    {"label": "照度均匀度 U₀", "unit": "—", "pkg": ("workplane", "illuminance", "uniformity_u0"), "crit": "uniformity_u0"},
    {"label": "显色指数 Ra", "unit": "—", "pkg": ("metrics_extra", "ra"), "crit": "ra"},
    {"label": "相关色温 CCT", "unit": "K", "pkg": ("metrics_extra", "cct_k"), "crit": "cct_k"},
    {"label": "照明功率密度 LPD", "unit": "W/m²", "pkg": ("metrics_extra", "lpd_w_m2"), "crit": "lpd_target"},
]


def _dig(data: Dict[str, Any], path: Sequence[str]) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _criteria_index(criteria: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """判据表 → {metric 名: 判据行}（取第一个场景，即会议室）。"""
    if not criteria:
        return {}
    scenes = criteria.get("scenes") or {}
    for scene in scenes.values():
        return {c.get("metric"): c for c in scene.get("criteria", []) if c.get("metric")}
    return {}


def _fmt_num(value: Any, unit: str = "") -> str:
    if value is None:
        return "未取证"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = f"{value}"
    return f"{text} {unit}".strip() if unit and unit not in ("—", "-") else text


_OP_SYM = {">=": "≥", "<=": "≤"}


def _crit_text(rule: Dict[str, Any]) -> str:
    """判据行的阈值文本：`≤ 19` 这种规范写法。"""
    op = _OP_SYM.get(rule.get("op") or "", rule.get("op") or "")
    return f"{op} {_fmt_num(rule.get('value'), rule.get('unit', ''))}".strip()


def _judge(value: Any, op: Optional[str], threshold: Any) -> str:
    if value is None:
        return "—（无实测值）"
    if threshold is None or not op:
        return "—（标准未规定）"
    try:
        ok = float(value) >= float(threshold) if op == ">=" else float(value) <= float(threshold)
    except (TypeError, ValueError):
        return "—"
    return "达标" if ok else "未达标"


def _block_header(title: str) -> str:
    return f"## {title}\n"


def _luminaire_block(pkg: Dict[str, Any]) -> str:
    luminaires = pkg.get("luminaires") or []
    lines = [_block_header("1. 灯具选型列表"), ""]
    if not luminaires or luminaires[0].get("count") is None:
        lines.append("> 未取证：结果包中没有可用的灯具列表。")
        return "\n".join(lines)
    lines.append("| 型号 | 制造商 | 编号 | 数量 | 单灯功率 | 光通量 | 发光效率 |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for item in luminaires:
        lines.append(
            f"| {item.get('model') or '—'} | {item.get('manufacturer') or '—'} | {item.get('sku') or '—'} "
            f"| {_fmt_num(item.get('count'), '件')} | {_fmt_num(item.get('power_w'), 'W')} "
            f"| {_fmt_num(item.get('flux_lm'), 'lm')} | {_fmt_num(item.get('efficacy_lm_w'), 'lm/W')} |"
        )
    positions = (luminaires[0].get("positions") or []) if luminaires else []
    if positions:
        lines.append("")
        lines.append(f"逐灯位置表：{len(positions)} 盏（X / Y / 安装高度，完整数据见结果包 `luminaires[].positions`）。")
    total_flux = _dig(pkg, ("metrics_extra", "flux_total_lm"))
    total_power = _dig(pkg, ("metrics_extra", "power_total_w"))
    if total_flux is not None or total_power is not None:
        lines.append("")
        lines.append(f"合计：Φ {_fmt_num(total_flux, 'lm')} ／ P {_fmt_num(total_power, 'W')}。")
    return "\n".join(lines)


def _ugr_point_line(pkg: Dict[str, Any]) -> Optional[str]:
    """UGR 观察点明细行（需求 1 的报告层落点）：点名 + 索引 + 最不利值 + 评估条件。

    指标表只放最不利值；逐点明细靠这一行落到报告里，否则观察点标识在交付物中无痕。
    """
    points = _dig(pkg, ("workplane", "ugr"))
    if not isinstance(points, list):
        return None
    segs: List[str] = []
    for p in points:
        if not isinstance(p, dict) or p.get("ugr_max") is None:
            continue
        name = " ".join(str(x) for x in (p.get("index"), p.get("point")) if x) or "观察点"
        seg = f"{name} {_fmt_num(p['ugr_max'])}"
        cond: List[str] = []
        if p.get("max_at_deg") is not None:
            cond.append(f"最不利 {_fmt_num(p['max_at_deg'])}°")
        if p.get("target") is not None:
            cond.append(f"目标 ≤ {_fmt_num(p['target'])}")
        if p.get("range_from") is not None and p.get("range_to") is not None:
            cond.append(f"观察范围 {_fmt_num(p['range_from'])}°–{_fmt_num(p['range_to'])}°")
        if p.get("step") is not None:
            cond.append(f"步进 {_fmt_num(p['step'])}°")
        if p.get("height_m") is not None:
            cond.append(f"高度 {_fmt_num(p['height_m'], 'm')}")
        if cond:
            seg += "（" + "，".join(cond) + "）"
        segs.append(seg)
    return "UGR 观察点：" + "；".join(segs) + "。" if segs else None


def _effect_block(pkg: Dict[str, Any], crit: Dict[str, Dict[str, Any]]) -> str:
    lines = [_block_header("2. 照明效果（六项指标）"), ""]
    lines.append("| 指标 | 实测值 | 判据阈值 | 判定 |")
    lines.append("|---|---:|---:|---|")
    for metric in _METRICS:
        value = _dig(pkg, metric["pkg"])
        if isinstance(value, list):
            # UGR 点记录列表（多样本就绪，见抽取器）：报告取最不利值参与判定
            value = max(
                (
                    p.get("ugr_max")
                    for p in value
                    if isinstance(p, dict) and p.get("ugr_max") is not None
                ),
                default=None,
            )
        rule = crit.get(metric["crit"]) or {}
        threshold = _crit_text(rule) if rule else "—"
        lines.append(
            f"| {metric['label']} | {_fmt_num(value, metric['unit'])} "
            f"| {threshold or '—'} | {_judge(value, rule.get('op'), rule.get('value'))} |"
        )
    ugr_line = _ugr_point_line(pkg)
    if ugr_line:
        lines.append("")
        lines.append(ugr_line)
    illum = _dig(pkg, ("workplane", "illuminance")) or {}
    extra_bits = []
    if illum.get("emin_lx") is not None and illum.get("emax_lx") is not None:
        extra_bits.append(f"E最小 {_fmt_num(illum['emin_lx'], 'lx')} / E最大 {_fmt_num(illum['emax_lx'], 'lx')}")
    if illum.get("uniformity_g2") is not None:
        extra_bits.append(f"g2 {illum['uniformity_g2']}")
    if extra_bits:
        lines.append("")
        lines.append("补充：" + "；".join(extra_bits) + "。")
    return "\n".join(lines)


def _render_block(images: Optional[Sequence[str]]) -> str:
    lines = [_block_header("3. 渲染效果"), ""]
    if not images:
        lines.append("> 未取证：渲染图/伪彩照度图尚未导出（待真机单点出图后补入）。")
        return "\n".join(lines)
    for image in images:
        lines.append(f"![渲染效果]({image})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _standard_block(crit: Dict[str, Dict[str, Any]], criteria_raw: Optional[Dict[str, Any]]) -> str:
    lines = [_block_header("4. 对标国标"), ""]
    if not crit:
        lines.append("> 未取证：未提供判据表。")
        return "\n".join(lines)
    lines.append("| 指标 | 阈值 | 出处 |")
    lines.append("|---|---:|---|")
    for metric in _METRICS:
        rule = crit.get(metric["crit"])
        if not rule:
            continue
        source = f"{rule.get('standard', STANDARD)} {rule.get('table', '—')}"
        bits = []
        if rule.get("page_print"):
            bits.append(f"印刷 {rule['page_print']}")
        if rule.get("page_pdf"):
            bits.append(f"PDF {rule['page_pdf']}")
        if bits:
            source += "（" + " / ".join(bits) + "）"
        lines.append(
            f"| {metric['label']} | {_crit_text(rule)} | {source} |"
        )
    cct = crit.get("cct_k")
    if cct is None:
        lines.append(f"| 相关色温 CCT | —（{STANDARD} 未对会议室规定色温限值） | — |")
    if criteria_raw and criteria_raw.get("unevidenced"):
        lines.append("")
        lines.append("未取证项（不参与判定）：")
        for item in criteria_raw["unevidenced"]:
            lines.append(f"- {item.get('item')}：{item.get('reason')}")
    return "\n".join(lines)


def _price_block(price: Optional[Dict[str, Any]]) -> str:
    lines = [_block_header("5. 价格"), ""]
    if not price:
        lines.append("> 未取证：灯具采购价与安装费明细待团队补充；对标口径见《详细计算过程文档》。")
        return "\n".join(lines)
    source = price.get("source")
    rows = price.get("items") or []
    if rows:
        lines.append("| 项目 | 单价 | 数量 | 小计 |")
        lines.append("|---|---:|---:|---:|")
        for item in rows:
            lines.append(
                f"| {item.get('name')} | {_fmt_num(item.get('unit_price'), '元')} "
                f"| {item.get('qty') or '—'} | {_fmt_num(item.get('subtotal'), '元')} |"
            )
    if price.get("total") is not None:
        lines.append("")
        lines.append(f"含税总价：**{_fmt_num(price['total'], '元')}**（估算范围 {price.get('range') or '—'}）。")
    if source:
        lines.append("")
        lines.append(f"> 口径说明：{source}")
    return "\n".join(lines)


def _energy_block(pkg: Dict[str, Any], price: Optional[Dict[str, Any]]) -> str:
    metrics = pkg.get("metrics_extra") or {}
    lines = [_block_header("6. 节能"), ""]
    energy = metrics.get("energy_kwh_a")
    energy_max = metrics.get("energy_max_kwh_a")
    if energy is None:
        lines.append("> 能耗估算未取证（报表未含该页）。")
        return "\n".join(lines)
    lines.append(f"- 年能耗估算：**{_fmt_num(energy, 'kWh/a')}**"
                 + (f"（上限 {_fmt_num(energy_max, 'kWh/a')}）" if energy_max is not None else ""))
    lpd = metrics.get("lpd_w_m2")
    if lpd is not None:
        lines.append(f"- 照明功率密度：{_fmt_num(lpd, 'W/m²')}")
    if price and price.get("electricity_price") and price.get("annual_saving_kwh"):
        saving_kwh = price["annual_saving_kwh"]
        cost = saving_kwh * price["electricity_price"]
        lines.append(f"- 年节电量估算：{_fmt_num(saving_kwh, 'kWh/a')} → 年节电费约 {_fmt_num(round(cost), '元')}"
                     f"（电价 {price['electricity_price']} 元/kWh，估算口径）")
    else:
        lines.append("- 节电对比：未取证（需对照方案能耗与电价口径）。")
    return "\n".join(lines)


def _missing_block(pkg: Dict[str, Any], price: Optional[Dict[str, Any]],
                   images: Optional[Sequence[str]]) -> str:
    lines = [_block_header("7. 结论与未取证项"), ""]
    missing = list(pkg.get("_missing") or [])
    if price is None:
        missing.append("价格数据（灯具采购价 / 安装费）")
    if not images:
        missing.append("渲染图 / 伪彩照度图")
    if _dig(pkg, ("workplane", "ugr")) is None:
        missing.append("UGR 实测值（报表未含眩光计算页）")
    lines.append("**结论**：以上指标均来自 DIALux 导出（机读结果包），AI 只做组织与文字。")
    lines.append("")
    if missing:
        lines.append("**未取证项**：")
        for item in missing:
            lines.append(f"- {item}：待补充，不参与判定。")
    else:
        lines.append("**未取证项**：无。")
    return "\n".join(lines)


def assemble_report(
    package: Dict[str, Any],
    *,
    criteria: Optional[Dict[str, Any]] = None,
    price: Optional[Dict[str, Any]] = None,
    render_images: Optional[Sequence[str]] = None,
    project_name: str = "会议室照明设计",
    generated_on: Optional[str] = None,
) -> str:
    """结果包（＋判据表/价格/图件）→ 报告 Markdown。"""
    crit = _criteria_index(criteria)
    meta = package.get("meta") or {}
    source_pdf = (package.get("artifacts") or {}).get("report_pdf")
    lines: List[str] = [
        f"# {project_name} · 照明设计报告",
        "",
        f"> 生成日期：{generated_on or date.today().isoformat()} ｜ 数据来源：DIALux 报表"
        + (f"（`{source_pdf}`）" if source_pdf else "")
        + " ｜ 机读通道：报表 PDF → 结果包（schema v1）",
        "> 口径：六项指标固定（维持平均照度 / UGR / U₀ / Ra / CCT / LPD）；数值全部来自 DIALux 导出，未取证项显式标注。",
        "",
        "---",
        "",
    ]
    lines.append(_luminaire_block(package))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_effect_block(package, crit))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_render_block(render_images))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_standard_block(crit, criteria))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_price_block(price))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_energy_block(package, price))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_missing_block(package, price, render_images))
    lines.append("")
    return "\n".join(lines)


def markdown_to_docx(markdown_text: str, out_path: Path, *,
                     reference: Optional[Path] = None) -> Path:
    """Markdown → DOCX（pandoc，带中文 reference 模板），并给表格补边框。

    依赖：pypandoc_binary（自带 pandoc）。缺依赖时给出明确安装提示。
    """
    from pathlib import Path as _Path

    try:
        import pypandoc
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError("导出 DOCX 需要 pandoc：pip install pypandoc_binary") from exc

    out = _Path(out_path)
    ref = reference if reference is not None else (_Path(__file__).parent / "assets" / "reference_zh.docx")
    args = [f"--reference-doc={ref}"] if ref and _Path(ref).exists() else []
    pypandoc.convert_text(markdown_text, "docx", format="gfm",
                          outputfile=str(out), extra_args=args)

    # 后处理：直接给每个表格加边框（样式继承在部分渲染器里不稳，直接格式化最可靠）
    try:
        import docx
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        document = docx.Document(str(out))
        border_xml = (
            '<w:tblBorders %s>'
            '<w:top w:val="single" w:sz="4" w:color="808080"/>'
            '<w:left w:val="single" w:sz="4" w:color="808080"/>'
            '<w:bottom w:val="single" w:sz="4" w:color="808080"/>'
            '<w:right w:val="single" w:sz="4" w:color="808080"/>'
            '<w:insideH w:val="single" w:sz="4" w:color="808080"/>'
            '<w:insideV w:val="single" w:sz="4" w:color="808080"/>'
            '</w:tblBorders>' % nsdecls('w'))
        for table in document.tables:
            table._tbl.tblPr.append(parse_xml(border_xml))
        document.save(str(out))
    except ImportError:  # pragma: no cover - 没 python-docx 就跳过打磨
        pass
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(
        prog="python -m optiflow.report_assembler",
        description="结果包 JSON → 照明设计报告（Markdown）",
    )
    ap.add_argument("package", help="结果包 JSON 路径")
    ap.add_argument("-o", "--out", help="输出 Markdown 路径（缺省打印到 stdout）")
    ap.add_argument("--criteria", help="判据表 JSON 路径")
    ap.add_argument("--price", help="价格 JSON 路径（可选）")
    ap.add_argument("--image", action="append", default=None, help="渲染图路径（可多次）")
    ap.add_argument("--docx", help="同时导出 DOCX（需要 pypandoc_binary）")
    ap.add_argument("--project-name", default="会议室照明设计")
    args = ap.parse_args(argv)

    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    criteria = json.loads(Path(args.criteria).read_text(encoding="utf-8")) if args.criteria else None
    price = json.loads(Path(args.price).read_text(encoding="utf-8")) if args.price else None
    text = assemble_report(
        package, criteria=criteria, price=price,
        render_images=args.image, project_name=args.project_name,
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已写入：{args.out}")
    else:
        print(text)
    if args.docx:
        docx_path = markdown_to_docx(text, Path(args.docx))
        print(f"DOCX 已写入：{docx_path}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
