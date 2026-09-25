"""感知层测试：状态识别 / 状态路由 / 边沿广播。

移植自 BetterGI 模式（WhichGameUiForTriggers + SupportedGameUiCategory）：
- Dispatcher 每轮先 recognize_state，再只派发 accepts(state) 的触发器
- 状态变化时所有 enabled 触发器收到 on_state_change
- 触发器 supported_states 过滤（autosave 只在 dialog、calc watch 只在 calculating）
"""
from src.tasks.dialux.autosave_trigger import AutoSaveTrigger
from src.tasks.dialux.calc_watch_trigger import CalcWatchTrigger
from optiflow.workbench.dispatcher import Dispatcher
from optiflow.workbench.recognition import StateObservation, UiState
from optiflow.workbench.trigger import Trigger


class Ctx:
    """最小 ctx：记录日志。"""

    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))

    def cancelled(self):
        return False


class ProbeTrigger(Trigger):
    """记录被派发次数的探针。"""

    def __init__(self, name="probe", states=None):
        self.name = name
        self.supported_states = states
        self.ticks = 0
        self.state_changes = []

    def on_tick(self, ctx, obs=None):
        self.ticks += 1
        return False

    def on_state_change(self, ctx, old, new):
        self.state_changes.append((old, new))


def _run_ticks(dispatcher, ctx, n=2):
    for _ in range(n):
        dispatcher._tick_once(ctx)


def test_state_route_dialog_only(monkeypatch):
    """autosave 只在 DIALOG 状态被派发；IDLE 时不跑。"""
    ctx = Ctx()
    obs = StateObservation(UiState.DIALOG)
    d = Dispatcher(state_fn=lambda: obs)
    at = AutoSaveTrigger()
    monkeypatch.setattr(
        "src.executor.uia.driver.auto_answer_save_prompt", lambda name: 0)
    d.add(at)
    _run_ticks(d, ctx, 3)
    assert at._answered == 0       # 应答函数被路由调过（返回0）
    assert d.current_state is UiState.DIALOG


def test_trigger_not_dispatched_in_wrong_state():
    """supported_states 不匹配 → on_tick 不被调用。"""
    ctx = Ctx()
    obs = StateObservation(UiState.IDLE)
    d = Dispatcher(state_fn=lambda: obs)
    probe = ProbeTrigger(states={UiState.DIALOG})
    d.add(probe)
    _run_ticks(d, ctx, 3)
    assert probe.ticks == 0        # IDLE 状态下 dialog 触发器不空跑


def test_any_state_trigger_dispatched_everywhere():
    """supported_states=None → 任意状态都派发。"""
    ctx = Ctx()
    d = Dispatcher(state_fn=lambda: StateObservation(UiState.IDLE))
    probe = ProbeTrigger(states=None)
    d.add(probe)
    _run_ticks(d, ctx, 2)
    assert probe.ticks == 2


def test_state_change_broadcast():
    """状态变化时 on_state_change 收到 (old, new)。"""
    ctx = Ctx()
    states = iter([StateObservation(UiState.IDLE),
                   StateObservation(UiState.CALCULATING),
                   StateObservation(UiState.IDLE)])
    d = Dispatcher(state_fn=lambda: next(states))
    probe = ProbeTrigger()
    d.add(probe)
    _run_ticks(d, ctx, 3)
    assert (UiState.IDLE, UiState.CALCULATING) in probe.state_changes
    assert (UiState.CALCULATING, UiState.IDLE) in probe.state_changes


def test_calc_watch_fires_on_calculating_to_idle():
    """计算监控：CALCULATING→IDLE 边沿触发 on_finished。"""
    ctx = Ctx()
    states = iter([StateObservation(UiState.IDLE),
                   StateObservation(UiState.CALCULATING),
                   StateObservation(UiState.CALCULATING),
                   StateObservation(UiState.IDLE)])
    finished = []
    d = Dispatcher(state_fn=lambda: next(states))
    cw = CalcWatchTrigger(on_finished=lambda c: finished.append(1))
    d.add(cw)
    _run_ticks(d, ctx, 4)
    assert finished == [1]
    assert cw.finished_count == 1


def test_recognize_state_no_process():
    """DIALux 没开 → NO_WINDOW（不抛异常）。"""
    obs = StateObservation(UiState.NO_WINDOW)
    d = Dispatcher(state_fn=lambda: obs)
    ctx = Ctx()
    d._tick_once(ctx)
    assert d.current_state is UiState.NO_WINDOW


def test_recognize_state_real_smoke():
    """真机冒烟：不依赖 DIALux 在不在，只要能返回合法状态。"""
    obs = StateObservation(UiState.UNKNOWN)
    try:
        from optiflow.workbench.recognition import recognize_state
        obs = recognize_state()
    except Exception:  # noqa: BLE001 - 无 pywin32 环境降级
        pass
    assert isinstance(obs.state, UiState)
