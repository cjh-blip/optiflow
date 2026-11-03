"""出一份报告数据包（report_package）并落盘。

跑两次：
  ① 单房间（会议室 300 lx，产品默认路径）
  ② 多房间（会议室 300 lx + 走廊 50 lx，各按自己类型判定）

用法：
    python scripts/report_package.py --repo D:\\dev\\dialux-compiler --out build\\report_package
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from optiflow.algo.engine import plan_layout  # noqa: E402
from optiflow.ir import TaskSpec  # noqa: E402
from optiflow.report_package import build_report_package  # noqa: E402


def _task(dxf: Path, repo: Path, *, target: float, flux: float,
          ceil_h: float, room_type: str | None, multi: bool) -> TaskSpec:
    from probe_illuminance import _task_from_dxf
    return _task_from_dxf(dxf, target, flux, ceil_h, room_type, multi_room=multi)


def _dump(title: str, pkg: dict, out_dir: Path) -> None:
    path = out_dir / f"{title}.json"
    path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 78}\n{title}  -> {path}  ({path.stat().st_size} 字节)\n{'=' * 78}")

    print("\n【房间清单】")
    for r in pkg["rooms"]:
        lpd = r["lpd"]
        cur = "—" if lpd["current_limit"] is None else f"{lpd['current_limit']:g}"
        tgt = "—" if lpd["target_limit"] is None else f"{lpd['target_limit']:g}"
        verdict = ("不判" if lpd["compliant"] is None
                   else ("合规" if lpd["compliant"] else "不合规"))
        print(f"  {r['space_id']}  类型={r['room_type'] or '未指定'}  面积={r['area_m2']} m²")
        print(f"    照度 目标={r['target_lux']:.0f}（{r['target_basis']}）  "
              f"实测={r['actual_lux']}  达标={r['illuminance_ok']}")
        print(f"    U0   要求={r['u0_required']:.2f}（{r['u0_basis']}）  "
              f"实测={r['u0']}  达标={r['u0_ok']}")
        print(f"    LPD  实测={lpd['actual']}  现行值≤{cur} → {verdict}  "
              f"目标值≤{tgt} → {'—' if lpd['meets_target'] is None else ('达到' if lpd['meets_target'] else '未达到')}")
        print(f"    指标状态: {r['metric_status']}")

    print("\n【判据出处】")
    for row in pkg["standards_cited"]:
        print(f"  {row['room_type']:<14} {row['label']:<10} = {row['value']:<6} "
              f"({row['unit'] or '-':<5})  {row['standard']}")

    print("\n【缺口与建议】")
    if not pkg["shortfalls"]:
        print("  （无）")
    for s in pkg["shortfalls"]:
        print(f"  × {s['space_id']} {s['label']}：实测 {s['actual']} / 目标 {s['target']}"
              f"（{s['gap_pct']:+.1f}%，direction={s.get('direction', 'under')}）")
        print(f"    建议：{s['action'][:80]}…")

    print("\n【灯具清单】")
    lum = pkg["luminaire"]
    print(f"  model={lum['model']}  flux={lum['flux_lm']} lm  power={lum['power_w']} W  "
          f"count={lum['count']}  total={lum['total_power_w']} W")
    print(f"  power_source={lum['power_source']}  ({lum['power_source_detail']})")
    if lum["note"]:
        print(f"  ⚠ {lum['note']}")

    print("\n【未覆盖项】")
    for u in pkg["uncovered"]:
        print(f"  [{u['status']:<17}] {u['label']}")
        print(f"      缺：{u['what_is_missing']}")

    print("\n【合计】", pkg["totals"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dxf", default=None, help="布局图 dxf；不传用样例")
    ap.add_argument("--repo", default=None, help="解析器仓库（dialux-compiler）")
    ap.add_argument("--flux", type=float, default=3000.0)
    ap.add_argument("--ceil-h", type=float, default=4.0)
    ap.add_argument("--out", default="build/report_package")
    args = ap.parse_args()

    dxf = Path(args.dxf) if args.dxf else (
        Path("D:/homework/optiflow-app/standalone/samples/布局图.dxf"))
    if not dxf.is_file():
        print(f"找不到图纸：{dxf}")
        return 2
    repo = Path(args.repo) if args.repo else (ROOT / "parser")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ① 单房间：会议室 300 lx
    t1 = _task(dxf, repo, target=300.0, flux=args.flux, ceil_h=args.ceil_h,
               room_type="会议室", multi=False)
    pkg1 = build_report_package(plan_layout(t1), t1,
                                pipeline={"job_id": None, "steps": [], "source": "cli"})
    _dump("单房间_会议室300", pkg1, out_dir)

    # ② 多房间：会议室 300 + 走廊 50
    t2 = _task(dxf, repo, target=300.0, flux=args.flux, ceil_h=args.ceil_h,
               room_type=None, multi=True)
    pkg2 = build_report_package(plan_layout(t2), t2,
                                pipeline={"job_id": None, "steps": [], "source": "cli"})
    _dump("多房间_会议室300_走廊50", pkg2, out_dir)

    print("\n数据包已落盘。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
