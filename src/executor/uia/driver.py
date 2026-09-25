"""UIA 通道：用 PowerShell + UI Automation 驱动 DIALux evo。

为什么是 PowerShell 而不是 Python 原生
------------------------------------
Python 侧要跑 UIA 需要 ``comtypes``（``uiautomation`` / ``pywinauto`` 都依赖它），
本机三个都没装，而已批准的范围是不加新依赖。PowerShell 的
``UIAutomationClient`` + ``UIAutomationTypes`` 是 Windows 自带的，
且这条链已经在 DIALux evo 14.0 上真机验证跑通过。所以驱动层保持 PowerShell，
Python 只做编排与进度解析。

.ps1 必须是纯 ASCII
-------------------
PowerShell 5.1 会把 UTF-8 的 .ps1 按 GBK 解码，脚本里任何一个中文字符都会让它
在后面某个随机的花括号处报 ``ParserError: UnexpectedToken``。所以中文说明写在
这个 .py 里，.ps1 里一个中文都不许有（有测试钉住这一点）。
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.core.env import resource_path

logger = logging.getLogger(__name__)

DRIVER_PS1 = resource_path("src/executor/uia/dialux_driver.ps1")

# 进度契约：UIA 通道的步骤表。
# name 与 .ps1 里 Emit 的第一个字段严格对应，改一边必须改另一边（有测试钉住）。
# weight 是实测量级的相对耗时——等权重会让进度条很跳：菜单展开是毫秒级，
# 导入要等 DIALux 重建场景，保存要写 69 KB 的 zip。
STEPS: List[tuple] = [
    ("attach", "挂到 DIALux 进程", 1.0),
    ("activate", "唤起窗口到前台", 1.0),
    ("menu-file", "展开 文件 菜单", 1.0),
    ("menu-import", "展开 导入 子菜单", 1.0),
    ("menu-import-stf", "点击 STF 文件", 1.0),
    ("dialog", "等文件对话框", 2.0),
    ("dialog-filename", "填入 STF 路径", 1.0),
    ("import", "确认导入", 2.0),
    ("import-settled", "等 DIALux 重建场景", 8.0),
    ("save", "保存为 .evo", 6.0),
    ("done", "完成", 0.5),
]

TOTAL_WEIGHT = sum(w for _, _, w in STEPS)
_TITLES = {name: title for name, title, _ in STEPS}
_WEIGHTS = {name: w for name, _, w in STEPS}


@dataclass
class StepEvent:
    """驱动脚本吐出的一步。``progress`` 是按权重累计的 0~100。"""

    name: str
    title: str
    ok: bool
    detail: str
    progress: float


def _parse(line: str) -> Optional[tuple]:
    """解析一行 ``STEP <name> <ok|fail> [detail]``；不是 STEP 行返回 None。"""
    parts = line.strip().split(None, 3)
    if len(parts) < 3 or parts[0] != "STEP":
        return None
    return parts[1], parts[2] == "ok", (parts[3] if len(parts) > 3 else "")


def _pids_of(process_name: str) -> set:
    """按进程名取 pid 集合。用 tasklist 而不是 psutil——不加新依赖。"""
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {process_name}.exe", "/NH", "/FO", "CSV"],
        capture_output=True, text=True,
    ).stdout
    pids = set()
    for line in out.splitlines():
        cols = [c.strip('" ') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].isdigit():
            pids.add(int(cols[1]))
    return pids


def blocking_dialogs(process_name: str = "DIALux_x64") -> List[dict]:
    """列出 DIALux 进程里所有可见的 ``#32770`` 模态框。

    为什么必须先查这个
    ------------------
    2026-09-05 实测：只要有一个可见的模态框挂在 DIALux 上，主窗口的 UIA 后代数
    就会从 310 塌成 **3**，于是 ``File``/``Save`` 全部找不到，报出来的却是
    ``menu-file not-found`` 这种完全指错方向的错。上一轮就是被一个从更早的会话
    遗留下来的「重命名」框卡住，白跑了两次真机。

    返回的每一项带 ``is_file_dialog``：带控件 1148（文件名 ComboBoxEx32）的就是
    文件对话框，那是我们自己漏下的，取消掉没有副作用；其它的可能是「是否保存
    更改」这类真问题，**不许自动点**。
    """
    try:
        import win32gui
        import win32process
    except ImportError:  # pragma: no cover - 本机已装，仅防御
        logger.warning("pywin32 不可用，跳过模态框预检")
        return []

    pids = _pids_of(process_name)
    if not pids:
        return []

    found: List[dict] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetClassName(hwnd) != "#32770":
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in pids:
                return True
            found.append({
                "hwnd": hwnd,
                "title": win32gui.GetWindowText(hwnd),
                "is_file_dialog": bool(win32gui.GetDlgItem(hwnd, 1148)),
            })
        except Exception:  # noqa: BLE001 - 枚举期间窗口可能正在销毁
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return found


def cancel_file_dialogs(process_name: str = "DIALux_x64") -> int:
    """取消遗留的文件对话框（BM_CLICK 到「取消」，控件 id 2），返回取消了几个。

    只动文件对话框。其它模态框留给人判断。
    """
    import time

    import win32gui

    n = 0
    for d in blocking_dialogs(process_name):
        if not d["is_file_dialog"]:
            continue
        cancel = win32gui.GetDlgItem(d["hwnd"], 2)
        if cancel:
            win32gui.SendMessage(cancel, 0x00F5, 0, 0)  # BM_CLICK
            n += 1
            logger.info("取消遗留文件对话框：%s", d["title"])
    if n:
        time.sleep(1.0)
    return n



def run_import(
    stf_path: str | Path,
    *,
    on_step: Optional[Callable[[StepEvent], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    process_name: str = "DIALux_x64",
    timeout_s: int = 180,
) -> List[StepEvent]:
    """把 STF 导入正在运行的 DIALux 并保存，返回逐步事件。

    不负责启动 DIALux——脚本挂的是已经开着的进程。DIALux 冷启动要十几秒且会弹
    欢迎页，那是另一件事，不塞进这里。

    ``cancel_check`` 每读到一行就问一次；返回 True 就杀掉 PowerShell。注意：这只
    能停在**步与步之间**，DIALux 里已经建好的东西不会回滚——停在第 8 步就是一个
    半成品项目。这是明知的取舍，界面上必须如实说，不许假装干净退出。

    抛 ``FileNotFoundError`` 当 .ps1 不见了，``RuntimeError`` 当 PowerShell 起不来。
    脚本自己失败不抛异常——最后一个 StepEvent 的 ``ok`` 是 False，调用方自己判。
    """
    if not DRIVER_PS1.exists():
        raise FileNotFoundError(f"驱动脚本缺失：{DRIVER_PS1}")
    stf = Path(stf_path).resolve()

    # 预检模态框。见 blocking_dialogs 的说明：漏一个可见模态框，后面每一步都会
    # 报「找不到菜单」，把人往完全错的方向带。
    cancel_file_dialogs(process_name)
    # 保存确认框是 WPF 弹窗（不在 blocking_dialogs 的 #32770 枚举里），
    # 先自动应答掉（点「是」=保存），避免它把主窗口 UIA 后代压成 3。
    auto_answer_save_prompt(process_name)
    stuck = [d for d in blocking_dialogs(process_name) if not d["is_file_dialog"]]
    if stuck:
        titles = "、".join(d["title"] or f"hwnd={d['hwnd']}" for d in stuck)
        raise RuntimeError(
            f"DIALux 上挂着未知模态框，先人工处理掉再跑：{titles}。"
            "不自动点是因为它可能是「是否保存更改」这类会丢工作的问题。"
        )

    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(DRIVER_PS1),
        "-StfPath", str(stf),
        "-ProcessName", process_name,
    ]
    logger.info("UIA 驱动启动：%s", stf.name)

    events: List[StepEvent] = []
    done_weight = 0.0
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise RuntimeError(f"起不了 PowerShell：{exc}") from exc

    assert proc.stdout is not None
    cancelled = False
    for line in proc.stdout:
        if cancel_check is not None and cancel_check():
            proc.kill()
            cancelled = True
            logger.warning("收到停止请求，已杀掉驱动进程（DIALux 里是半成品）")
            break
        parsed = _parse(line)
        if parsed is None:
            if line.strip():
                logger.debug("驱动输出（非 STEP）：%s", line.rstrip())
            continue
        name, ok, detail = parsed
        done_weight += _WEIGHTS.get(name, 1.0)
        ev = StepEvent(
            name=name,
            title=_TITLES.get(name, name),
            ok=ok,
            detail=detail,
            progress=min(100.0, done_weight / TOTAL_WEIGHT * 100.0),
        )
        events.append(ev)
        logger.info("[%5.1f%%] %s %s%s", ev.progress, ev.title,
                    "OK" if ok else "失败", f"（{detail}）" if detail else "")
        if on_step is not None:
            on_step(ev)

    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.error("UIA 驱动超时 %ds，已杀掉", timeout_s)

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if stderr.strip():
        logger.warning("驱动 stderr：%s", stderr.strip()[:500])
    if cancelled:
        events.append(StepEvent("cancelled", "已停止", False,
                                f"停在第 {len(events)} 步，DIALux 里是半成品",
                                events[-1].progress if events else 0.0))
    return events


def succeeded(events: List[StepEvent]) -> bool:
    """全部步骤 ok 且跑到了 done 才算成功。"""
    return bool(events) and all(e.ok for e in events) and events[-1].name == "done"


# ---------------------------------------------------------------- 保存弹窗自动应答
#
# BetterGI 思路的适配：它「识别小红框 → 自动响应」，靠视觉模板。DIALux 是 WPF，
# UIA 树可读（本模块全程靠它），所以识别保存弹窗用 UIA 特征匹配，比截图稳：
#   popup 含 AutomationId=Yes 与 No 两个按钮 + 至少一个 Edit = 保存/覆盖确认框
#   （"save file X?"）。命中就点 Yes（保留工作），No 会丢工作。
# 护栏与 cancel_file_dialogs 同源：只认这一种形状，其它模态框一律不碰。
AUTOSAVE_PS1 = resource_path("src/executor/uia/autosave.ps1")


def auto_answer_save_prompt(
    process_name: str = "DIALux_x64",
    timeout_s: int = 60,
) -> int:
    """扫描 DIALux 的保存确认框并自动点「是」（保存），返回处理了几个。

    无模态框/无保存框时返回 0，不抛异常（幂等，可频繁调用）。
    这是 BetterGI「识别→自动响应」的 DIALux 版：不给用户铺路，只在它弹
    「是否保存」时替用户点 Yes，避免每次真机流程被保存框卡住。
    """
    if not AUTOSAVE_PS1.exists():
        raise FileNotFoundError(f"自动保存脚本缺失：{AUTOSAVE_PS1}")
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(AUTOSAVE_PS1),
        "-ProcessName", process_name,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("自动保存脚本执行异常：%s", exc)
        return 0
    n = 0
    for line in (r.stdout or "").splitlines():
        m = re.match(r'^STEP find-save-dialog ok hwnd=\d+ saved=(\d+)$', line.strip())
        if m:
            n = int(m.group(1))
    if n:
        logger.info("自动应答保存确认框 %d 个（已点「是」）", n)
    return n
