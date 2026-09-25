"""具体流水线：把「说目标」拆成有序步骤。

目前只有一条，也是平台当前能跑通的最长链路：

    照明方案（lighting_plan）
      ① plan   —— 算法通道：算布灯方案（毫秒级，不需要任何软件在场）
      ② export —— 文件通道：把方案落成 DIALux 能导入的 STF

两段之间靠【产物文件】衔接，不是靠内存里的对象：
export 步读 plan 步写出的 plan.json，重建 Fixture 列表。
这样每一步都可以单独重跑、单独检查，也能被人拿着文件核对。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .ir import Fixture, TaskSpec
from .orchestrator import (
    Pipeline,
    Step,
    artifact_exists,
    cross_step_metric_matches,
    metric_targets_met,
)

LIGHTING_GOAL = "给房间出照明布灯方案，并落成 DIALux 可导入的 STF"


def task_with_planned_fixtures(task: TaskSpec, plan_path: str | Path) -> TaskSpec:
    """读算法层写出的 plan.json，把它规划的灯具套回任务。

    这是「先算后验」的接缝：下游拿到的是上一步【真实产出的文件】，
    不是内存里传过来的对象——文件可以被人打开核对，内存对象不行。
    """
    data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    fixtures = [Fixture.model_validate(f) for f in data["fixtures"]]
    return task.model_copy(update={"fixtures": fixtures})


def lighting_plan_pipeline(goal: str = LIGHTING_GOAL) -> Pipeline:
    """照明方案流水线：算法层出方案 → 文件层落 STF。"""
    return Pipeline(
        goal=goal,
        steps=[
            Step(
                name="plan",
                kind="layout",
                requires=("algorithm",),
                build=lambda ctx: ctx.task,
                checks=(
                    metric_targets_met(),
                    artifact_exists("plan", min_bytes=2),
                ),
            ),
            Step(
                name="export",
                kind="layout",
                requires=("file",),
                build=lambda ctx: task_with_planned_fixtures(
                    ctx.task, ctx.result_of("plan").artifacts["plan"]),
                checks=(
                    artifact_exists("stf", min_bytes=64),
                    cross_step_metric_matches(
                        "plan", "fixture_count", "export", "fixtures"),
                ),
            ),
        ],
    )


def lighting_plan_steps(**overrides) -> Pipeline:  # pragma: no cover - 兼容占位
    """预留：将来按参数裁剪流水线（如跳过 export 只出方案）。"""
    raise NotImplementedError("按参数裁剪流水线尚未实现")


DEFAULT_PIPELINES = {
    "lighting_plan": lighting_plan_pipeline,
}


def get_pipeline(name: str, **kwargs) -> Pipeline:
    """按名字取流水线（对外接口层用它把「目标」映射成流程）。"""
    try:
        factory = DEFAULT_PIPELINES[name]
    except KeyError:
        raise KeyError(
            f"没有名为 {name!r} 的流水线；可用: {sorted(DEFAULT_PIPELINES)}"
        ) from None
    return factory(**kwargs)

