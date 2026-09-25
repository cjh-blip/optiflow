"""感知层：DIALux 状态识别（L2：窗口枚举 + 文本特征）。

移植 BetterGI 的 `Bv.WhichGameUiForTriggers`——每轮先「认出目标程序处于什么
状态」，Dispatcher 再把状态广播给触发器（触发器按 supported_states 路由）。

不依赖截图/OCR（那是 L3，二期实验）；纯 Win32 枚举 + 文本特征，快且可测。
识别失败降级为 UNKNOWN，Dispatcher 按现状行为跑（不比改造前差）。
"""
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class UiState(Enum):
    """DIALux 目标状态（同 BetterGI GameUiCategory 的角色）。"""

    NO_WINDOW = "no_window"        # 进程没开
    IDLE = "idle"                  # 工程/主页开着，无模态
    DIALOG = "dialog"              # 有模态弹窗（保存确认/向导/付费墙…）
    CALCULATING = "calculating"    # 计算进行中
    UNKNOWN = "unknown"            # 识别失败（降级）


@dataclass
class StateObservation:
    """一次状态识别的完整观察（供触发器/诊断用，不只是结论）。"""

    state: UiState
    windows: List[dict] = field(default_factory=list)  # [{hwnd, cls, title, dialog}]

    def __bool__(self) -> bool:  # 方便 if obs == 判断
        return self.state is not UiState.UNKNOWN


# Win32 枚举所需（进程内缓存 type，避免重复 Add-Type 开销）
_VB = None


def _vb():
    """懒加载 Win32 帮助类（ctypes 实现，纯标准库）。"""
    global _VB
    if _VB is None:
        import win32gui  # pywin32，overlay 已依赖

        _VB = win32gui
    return _VB


def _enum_process_windows(pid: int) -> List[dict]:
    """枚举进程全部可见顶层窗口：[{hwnd, cls, title, dialog}]。"""
    import win32process

    win32gui = _vb()
    found: List[dict] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        if wpid != pid:
            return True
        found.append({
            "hwnd": hwnd,
            "cls": win32gui.GetClassName(hwnd),
            "title": win32gui.GetWindowText(hwnd),
        })
        return True

    win32gui.EnumWindows(_cb, None)
    for w in found:
        w["dialog"] = _is_dialog(w["hwnd"])
    return found


def _is_dialog(hwnd: int) -> bool:
    """窗口是否 #32770 对话框类（Win32 标准对话框类名）。"""
    try:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value == "#32770"
    except Exception:  # noqa: BLE001
        return False


def find_dialux_pid(process_name: str = "DIALux_x64") -> Optional[int]:
    """返回 DIALux 主界面进程 pid（有可见主窗口的），没有返回 None。"""
    import win32process

    win32gui = _vb()
    pid: Optional[int] = None

    def _cb(hwnd, _):
        nonlocal pid
        if pid is not None or not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            import win32gui as _w
            if _w.GetClassName(hwnd).startswith(f"HwndWrapper[{process_name}"):
                pid = wpid
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return pid


def recognize_state(process_name: str = "DIALux_x64") -> StateObservation:
    """识别 DIALux 当前状态（L2 感知，每轮 Dispatcher 调一次）。

    判定优先级：无进程 > 模态弹窗 > 计算中 > IDLE。
    计算中目前用「标题含进度特征」粗判，L3（OCR 读状态栏）二期接入。
    """
    try:
        pid = find_dialux_pid(process_name)
        if pid is None:
            return StateObservation(UiState.NO_WINDOW)
        wins = _enum_process_windows(pid)
        obs = StateObservation(UiState.IDLE, windows=wins)

        # 模态弹窗：非主窗口的 #32770（主窗口自身是 HwndWrapper 不是 32770）
        dialogs = [w for w in wins if w["dialog"]]
        if dialogs:
            obs.state = UiState.DIALOG
            return obs

        # 计算中：标题出现 DIALux 的计算进度特征（暂用「标题含 计算或 %」，L3 补精确）
        for w in wins:
            t = w.get("title", "")
            if ("计算" in t and "%" in t) or t.startswith("DIALux") and "%" in t:
                obs.state = UiState.CALCULATING
                return obs
        return obs
    except Exception as exc:  # noqa: BLE001 - 感知失败不致命，降级 UNKNOWN
        logger.debug("状态识别异常：%s", exc)
        return StateObservation(UiState.UNKNOWN)
