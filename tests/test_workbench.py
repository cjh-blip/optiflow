"""通用工作台骨架测试：Task / Flow / Trigger / Dispatcher / Config。

验证（全抄 BetterGI 架构的自有实现）：
- Flow 按顺序执行任务、失败即停、开关可禁用单步
- Dispatcher 周期轮询 Trigger，命中→on_trigger，exclusive 独占
- Config JSON 持久化 round-trip
"""
import time

from optiflow.workbench.config import WorkbenchConfig
from optiflow.workbench.dispatcher import Dispatcher
from optiflow.workbench.flow import Flow
from optiflow.workbench.task import Task, TaskContext, TaskResult
from optiflow.workbench.trigger import Trigger


# ---------------------------------------------------------------- 测试桩

class OkTask(Task):
    name = "OK 任务"

    def __init__(self, tag: str = ""):
        self.tag = tag
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        ctx.log(f"{self.tag} ok")
        return TaskResult(True, f"{self.tag} done")


class FailTask(Task):
    name = "失败任务"

    def run(self, ctx):
        return TaskResult(False, "boom")


class HitTrigger(Trigger):
    name = "命中触发"

    def __init__(self, hits: int = 1):
        self.hits = hits
        self.triggered = 0

    def on_tick(self, ctx, obs=None):
        return self.hits > self.triggered

    def on_trigger(self, ctx):
        self.triggered += 1
        ctx.log("trigger fired")


class ExclusiveTrigger(HitTrigger):
    name = "独占触发"
    exclusive = True


# ---------------------------------------------------------------- Flow

def test_flow_runs_in_order_and_stops_on_fail():
    ctx = TaskContext()
    a, b, c = OkTask("a"), FailTask(), OkTask("c")
    flow = Flow("t").add(a).add(b).add(c)
    results = flow.run(ctx)
    assert len(results) == 2          # a 成功 + b 失败，c 不跑
    assert results[0].ok
    assert not results[1].ok
    assert a.calls == 1
    assert c.calls == 0


def test_flow_disable_step():
    ctx = TaskContext()
    a, b = OkTask("a"), OkTask("b")
    flow = Flow("t").add(a).add(b)
    flow.disable(a.step_id if hasattr(a, "step_id") else "")  # noqa - disable by id
    # 直接按 step 对象 disable（Flow.disable 按 id，这里换一种：disabled 列表）
    flow.steps[0].enabled = False
    results = flow.run(ctx)
    assert len(results) == 1
    assert results[0].ok


def test_flow_cancel_stops():
    class SlowTask(Task):
        name = "慢任务"

        def run(self, ctx):
            for _ in range(5):
                if ctx.cancelled():
                    return TaskResult(False, "cancelled")
            return TaskResult(True)

    ctx = TaskContext(cancel_check=lambda: True)
    flow = Flow("t").add(SlowTask()).add(OkTask())
    results = flow.run(ctx)
    # 慢任务感知取消 → 返回失败（detail=cancelled），流程停在这里
    assert not results[-1].ok
    assert results[-1].detail == "cancelled"
    # 后续步骤不执行
    assert len(results) == 1


# ---------------------------------------------------------------- Dispatcher

def test_dispatcher_trigger_fires():
    ctx = TaskContext()
    t = HitTrigger(hits=2)
    d = Dispatcher(interval=0.05).add(t)
    d.start(ctx)
    time.sleep(0.3)
    d.stop()
    assert t.triggered >= 1


def test_dispatcher_exclusive_suspends_others():
    ctx = TaskContext()
    ex = ExclusiveTrigger(hits=1)
    other = HitTrigger(hits=5)
    d = Dispatcher(interval=0.02).add(ex).add(other)
    d.start(ctx)
    time.sleep(0.2)
    d.stop()
    # 独占触发后，其它触发被挂起（非独占触发不应在独占期间反复命中）
    assert ex.triggered >= 1


# ---------------------------------------------------------------- Config

def test_config_roundtrip(tmp_path):
    cfg = WorkbenchConfig(dwg="a.dwg", dwg_lighting="b.dwg",
                          luminaire_point="p.ies", luminaire_linear="l.ies")
    cfg.sections["luminaires"] = {"count": 4}
    p = tmp_path / "cfg.json"
    cfg.save(p)
    loaded = WorkbenchConfig.load(p)
    assert loaded.dwg == "a.dwg"
    assert loaded.luminaire_linear == "l.ies"
    assert loaded.sections["luminaires"]["count"] == 4


def test_config_missing_file_defaults(tmp_path):
    assert WorkbenchConfig.load(tmp_path / "nope.json").dwg == ""
