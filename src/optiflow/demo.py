"""P0 演示：提交任务 → 查进度 → 取结构化结果 → 归档版本。

运行：
    $env:PYTHONPATH = "src"
    D:\\dev\\anaconda3\\python.exe -m optiflow.demo
"""

from __future__ import annotations

import time
from pathlib import Path

from .adapters.dialux import DialuxAdapter
from .adapters.fake import FakeAdapter
from .adapters.lumen import LumenPlannerAdapter
from .dispatcher import Dispatcher
from .orchestrator import Orchestrator
from .pipelines import lighting_plan_pipeline
from .ir import Fixture, Geometry, Metric, Point, Space, TaskSpec
from .project import Project
from .registry import AdapterRegistry


def build_meeting_room_task() -> TaskSpec:
    """样例会议室量级的一个任务：9.57m × 12.97m，目标平均照度 500lx。"""
    outline = [
        Point(x=0.0, y=0.0),
        Point(x=9.57, y=0.0),
        Point(x=9.57, y=12.97),
        Point(x=0.0, y=12.97),
    ]
    room = Space(
        id="room-1",
        name="会议室",
        geometry=Geometry(kind="room", outline=outline, height=3.0),
        work_plane=0.75,
        reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
    )
    return TaskSpec(
        kind="layout",
        spaces=[room],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"utilization_factor": 0.70, "maintenance_factor": 0.80},
    )


def main() -> None:
    print("=== 光枢 / OptiFlow P0 演示（假适配器，不接真软件）===")

    registry = AdapterRegistry()
    registry.register(FakeAdapter(duration=0.4))
    project = Project(id="demo-project", name="P0 端到端演示")
    dispatcher = Dispatcher(registry, project=project)

    print("\n[1/3] 提交任务")
    task = build_meeting_room_task()
    print(f"      房间: {task.spaces[0].name}  面积估算: {task.spaces[0].area:.2f} m²")
    job_id = dispatcher.dispatch(task)
    print(f"      job_id = {job_id}")

    print("\n[2/3] 查进度")
    seen: list[str] = []
    while True:
        progress = dispatcher.progress(job_id)
        line = f"      {progress.status.value:<8} {progress.percent:>5.1f}%  {progress.message}"
        if line not in seen:
            seen.append(line)
            print(line)
        if progress.status.value != "running":
            break
        import time

        time.sleep(0.15)

    print("\n[3/3] 取结构化结果")
    result = dispatcher.wait(job_id)
    for metric in result.metrics:
        target = f"（目标 {metric.target}{metric.unit}）" if metric.target is not None else ""
        print(f"      {metric.name:<18} {metric.value:>8} {metric.unit}{target}")

    version = dispatcher.archive(job_id, note="P0 演示版本")
    print(f"\n归档：项目 {project.name} → 版本 v{version.version}（{version.metrics_summary}）")
    print("\nP0 端到端通过：提交 → 进度 → 结构化结果 → 版本归档")

    _dialux_file_layer_demo()
    _algorithm_layer_demo()


def _dialux_file_layer_demo() -> None:
    """P1：DIALux 文件层（STF）端到端——平台第一次接上真软件。

    只产文件、不驱动真机：跑完拿到的是 DIALux evo 可导入的 .stf。
    """
    print("\n" + "=" * 62)
    print("=== P1 演示：DIALux 文件层（DialuxAdapter，只产 STF 不驱动真机）===")

    registry = AdapterRegistry()
    registry.register(DialuxAdapter())
    project = Project(id="demo-dialux", name="P1 文件层演示")
    dispatcher = Dispatcher(registry, project=project)

    declared = registry.find("layout").capabilities()
    print("\n[1/3] 能力声明（先声明后实现）")
    print(f"      name={declared.name}  kinds={declared.kinds}  fragile={declared.fragile}")
    for limit in declared.limits:
        print(f"      limits: {limit}")

    print("\n[2/3] 提交任务（含一盏灯，验证灯具段）")
    task = build_meeting_room_task()
    task.fixtures = [
        Fixture(id="L1", name="筒灯", position=Point(x=1.0, y=1.0, z=3.0)),
    ]
    started = time.monotonic()
    job_id = dispatcher.dispatch(task)
    submit_cost = time.monotonic() - started
    print(f"      job_id = {job_id}（submit 立即返回，耗时 {submit_cost * 1000:.1f} ms）")

    print("\n[3/3] 等结果 → 取 Metric 与产物")
    result = dispatcher.wait(job_id, timeout=30.0)
    for metric in result.metrics:
        print(f"      {metric.name:<18} {metric.value:>10} {metric.unit}")
    stf_path = Path(result.artifacts["stf"])
    print(f"      artifact: {stf_path}")
    print(f"      说明: {result.raw['note']}")

    print("\n      —— 生成的 STF 全文 ——")
    for line in stf_path.read_text(encoding="utf-8").splitlines():
        print(f"      | {line}")

    version = dispatcher.archive(job_id, note="P1 文件层演示版本")
    print(f"\n归档：项目 {project.name} → 版本 v{version.version}（{version.metrics_summary}）")
    print("\nP1 文件层端到端通过：TaskSpec → 异步 STF 导出 → Metric + 产物 → 版本归档")


def _algorithm_layer_demo() -> None:
    """P2：算法层出方案 → DIALux 文件层落成 STF（「先算后验」的最长链路）。

    第一步是纯计算，不需要任何软件在场；第二步才产出 DIALux 能导入的文件。
    """
    print("\n" + "=" * 62)
    print("=== P2 演示：算法层布灯 → DIALux 文件层（先算后验）===")

    planner = LumenPlannerAdapter()
    registry = AdapterRegistry()
    registry.register(planner)
    registry.register(DialuxAdapter())
    project = Project(id="demo-algo", name="P2 算法层演示")
    orchestrator = Orchestrator(registry, project=project)
    pipeline = lighting_plan_pipeline()

    print("\n[1/5] 编排层：这条流水线分几步、每步走哪条通道")
    print(f"      goal：{pipeline.goal}")
    for step in pipeline.steps:
        chosen = orchestrator.resolve(step)
        print(f"      {step.name:<8} kind={step.kind} requires={list(step.requires)}"
              f"  →  {chosen.capabilities().name}  (tags={chosen.capabilities().tags})")
    print("      注：lumen 与 dialux 都声明 kind=layout，靠 tags 消歧；"
          "不写 tags 编排层会直接报错，不按注册顺序猜。")

    print("\n[2/5] 算法层能力声明（自曝其短）")
    declared = planner.capabilities()
    print(f"      name={declared.name}  kinds={declared.kinds}  fragile={declared.fragile}")
    for limit in declared.limits:
        print(f"      limits: {limit}")

    task = build_meeting_room_task()
    task.extra["flux"] = 3000.0        # 单灯光通量：硬信息，不给就算不了
    task.extra["fixture_name"] = "LED 面板 600x600"

    print("\n[3/5] 跑流水线（每步都用能力声明选通道，选不唯一就报错）")
    run = orchestrator.run(pipeline, task)
    for step_run in run.steps:
        print(f"      {step_run.name:<8} adapter={step_run.adapter:<8} job={step_run.job_id}")
    plan_result = run.result_of("plan")
    print(f"      算法层出方案耗时已含在内；整条流水线 {run.elapsed * 1000:.0f} ms")
    for metric in plan_result.metrics:
        target = f"（目标 {metric.target}）" if metric.target is not None else ""
        print(f"      {metric.name:<24} {metric.value:>10} {metric.unit}{target}")

    print("\n[4/5] 校验结果（带 target 的指标逐条对账 + 跨步骤一致性）")
    for check in run.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"      [{mark}] {check.name}：{check.detail}")

    print("\n[5/5] 产物与归档")
    stf_path = Path(run.result_of("export").artifacts["stf"])
    print(f"      方案: {run.result_of('plan').artifacts['plan']}")
    print(f"      STF : {stf_path}（{stf_path.stat().st_size} 字节）")
    print(f"      模型边界：")
    for note in plan_result.raw["assumptions"][:3]:
        print(f"        · {note}")
    for note in plan_result.raw["warnings"]:
        print(f"        ! {note}")
    for version in project.versions:
        print(f"      版本 v{version.version}（{version.note}）")
    print(f"\n整体结论：completed={run.completed} ok={run.ok}")
    print("\nP2 端到端通过：说目标 → 编排层分步选通道 → 算法层出方案"
          " → 文件层落 STF → 校验对账 → 版本归档")

if __name__ == "__main__":
    main()
