"""照度口径探针：同一张图、三档目标，逐盏打印布灯结果与反推系数。

目的不是修，是把「照度为什么对不上」摊开：
- 每档算出多少盏、每盏落在哪里、有没有被剔出房间
- 流明法反推的 CU×MF 到底是不是常数
- fixture_count 与真正参与照度计算的盏数是不是同一个数

跑法（仓库根目录）：
    python scripts/probe_illuminance.py
    python scripts/probe_illuminance.py --dxf <布局图.dxf> --targets 300 400 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from optiflow.algo.engine import plan_layout  # noqa: E402
from optiflow.geometry import point_in_polygon  # noqa: E402
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec  # noqa: E402

DEFAULT_DXF = Path(r"D:\homework\optiflow-app\standalone\samples\布局图.dxf")


def _task_from_dxf(dxf: Path, target_lux: float, flux: float,
                   ceil_h: float | None = 4.0) -> TaskSpec:
    """用服务层同一条路把 DXF 解析成 IR，再把 IR 收成 TaskSpec。

    刻意走 ``PlatformService.import_dxf``，保证探针看到的就是产品看到的。
    """
    import os

    os.environ.setdefault("GUANGSHU_REPO", r"D:\dev\dialux-compiler")
    from optiflow.adapters.dialux.dxf import ParseConfig, parse_dxf

    ir = parse_dxf(str(dxf), ParseConfig())
    spaces = []
    for storey in ir.get("storeys") or []:
        for item in storey.get("spaces") or []:
            poly = item.get("polygon") or []
            if len(poly) < 3 or str(item.get("name", "")).startswith("家具_"):
                continue
            spaces.append(Space(
                id=str(item.get("id") or f"space-{len(spaces)}"),
                name=str(item.get("name") or ""),
                geometry=Geometry(kind="room",
                                  height=float(ceil_h if ceil_h is not None
                                               else (item.get("ceil_h") or 4.0)),
                                  outline=[Point(x=float(p[0]), y=float(p[1])) for p in poly]),
                work_plane=0.75,
                reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
            ))
    return TaskSpec(kind="layout", spaces=spaces,
                    constraints=[Metric(name="illuminance_avg", target=target_lux, unit="lx")],
                    extra={"flux": flux, "fixture_name": "探针灯"})

def probe(task: TaskSpec, target_lux: float, flux: float, verbose: bool = True) -> None:
    plan = plan_layout(task)
    print(f"\n{'=' * 72}\n目标 {target_lux:.0f} lx   flux={flux:.0f} lm   "
          f"fixture_count={plan.metric('fixture_count'):.0f}  "
          f"illuminance_avg={plan.metric('illuminance_avg')} lx")
    for room in plan.rooms:
        poly = [(p.x, p.y) for p in next(
            s for s in task.spaces if s.id == room.space_id).geometry.outline]
        n_actual = len(room.fixtures)
        inside = sum(1 for f in room.fixtures if point_in_polygon(f.position.x, f.position.y, poly))
        print(f"  房间 {room.space_id}  area={room.area:.2f} m2  "
              f"room_index={room.room_index:.3f}  UF={room.utilization_factor:.4f}  "
              f"MF={room.maintenance_factor:.2f}")
        print(f"  lumen_method_count={room.lumen_method_count}  "
              f"layout.count={room.layout.count}  "
              f"实际 export 盏数={n_actual}  落在轮廓内={inside}  "
              f"被剔={n_actual - inside}")
        print(f"  照度: lumen={room.predicted_avg_lumen:.1f}  "
              f"直射={room.full_report.e_avg:.1f}  U0={room.uniformity.u0:.3f}")
        implied = room.predicted_avg_lumen * room.area / (
            max(n_actual, 1) * room.flux_per_fixture)
        print(f"  反推 CU×MF = E×A/(N×Φ) = "
              f"{room.predicted_avg_lumen:.1f}×{room.area:.2f}/"
              f"({n_actual}×{room.flux_per_fixture:.0f}) = {implied:.4f}")
        print(f"  理论值 UF×MF = {room.utilization_factor * room.maintenance_factor:.4f}")
        if room.relayout:
            print(f"  重排: base_count={room.relayout['base_count']} → "
                  f"count={room.relayout['count']}  U0 {room.relayout['base_u0']:.3f}→"
                  f"{room.relayout['u0']:.3f}")
        if room.warnings:
            for w in room.warnings:
                print(f"  ! {w}")
        print(f"  逐盏坐标（房间边界 x∈[{min(p[0] for p in poly):.2f}, "
              f"{max(p[0] for p in poly):.2f}] y∈[{min(p[1] for p in poly):.2f}, "
              f"{max(p[1] for p in poly):.2f}]）:")
        if not verbose:
            print(f"    （略，{len(room.fixtures)} 盏；加 --verbose 列出每一盏）")
            continue
        for f in room.fixtures:
            ok = "内" if point_in_polygon(f.position.x, f.position.y, poly) else "外!"
            print(f"    {f.id:>12}  x={f.position.x:8.3f}  y={f.position.y:8.3f}  {ok}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="照度口径探针")
    ap.add_argument("--dxf", default=str(DEFAULT_DXF))
    ap.add_argument("--flux", type=float, default=3000.0)
    ap.add_argument("--targets", type=float, nargs="+", default=[300, 400, 500])
    #: 层高。产品壳 app.py 的 _task_from_ir 默认传 4.0，探针默认跟它对齐，
    #: 否则两处算出的房间指数不同、灯数差一盏，看起来像玄学。
    ap.add_argument("--ceil-h", type=float, default=4.0)
    ap.add_argument("--verbose", action="store_true", help="逐盏坐标全部打印")
    args = ap.parse_args(argv)

    dxf = Path(args.dxf)
    print(f"图纸: {dxf}  flux={args.flux}  ceil_h={args.ceil_h}")
    for target in args.targets:
        task = _task_from_dxf(dxf, target, args.flux, args.ceil_h)
        probe(task, target, args.flux, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
