"""产品级布灯通道：导入灯具 IES + ArrangementFromSpace 自动排布 + 保存。

这是「软件真跑放灯」的执行入口——把此前真机手工验证过的链路（导入型号 →
切灯具布置工具 → 选原型 → 自动排布 → 保存）固化为可被 demo_run.py 调用的
产品代码。调用方只需给一个合规的会员厂商 IES 文件路径。

用法::

    from src.executor.uia.luminaire import place_luminaires
    events = place_luminaires(
        "build/ies/fixed/NPTLED351_NVC.IES",
        prototype_name="NPTLED351", count_x=4, count_y=4,
    )
    ok = succeeded(events)

依赖: ``luminaire.ps1``（纯 ASCII，PowerShell UIA 驱动，同 driver.ps1 约束）。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from .driver import StepEvent, _parse
from src.core.env import resource_path

logger = logging.getLogger(__name__)

LUMINAIRE_PS1 = resource_path("src/executor/uia/luminaire.ps1")

# STEP 顺序（与 .ps1 Emit 对应）。权重平均，因为每步都是秒级 UI 操作。
_LUM_STEPS = {
    "lum-import": "导入灯具型号",
    "lum-tool": "切换灯具工具",
    "lum-select": "选定当前灯具",
    "lum-arrange": "自动排布灯具",
    "lum-save": "保存工程",
    "lum-done": "完成",
}


def place_luminaires(
    ies_path: str | Path,
    *,
    prototype_name: str = "",
    count_x: int = 0,
    count_y: int = 0,
    process_name: str = "DIALux_x64",
    timeout_s: int = 180,
    cancel_check: Optional[callable] = None,
) -> List[StepEvent]:
    """把指定灯具 IES 导入当前 DIALux 工程并按空间自动排布、保存。

    参数:
        ies_path: 合规会员厂商 IES/LDT/GLDF 文件路径（MANUFAC 有厂商名，避免 paywall）。
        prototype_name: CatalogListBox 里选定的原型名；空则从文件名推导。
        count_x/count_y: 排布网格行列数；0 = 让 DIALux 按空间自动决定。
        cancel_check: 每读一行调用，返回 True 时中止（停在步与步之间，DIALux 内是半成品）。

    返回: StepEvent 列表；``succeeded(events)`` 判断是否跑完。
    抛 FileNotFoundError 当 .ps1 或 ies 缺失；RuntimeError 当 PowerShell 起不来。
    """
    if not LUMINAIRE_PS1.exists():
        raise FileNotFoundError(f"布灯驱动脚本缺失：{LUMINAIRE_PS1}")
    ies = Path(ies_path)
    if not ies.exists():
        raise FileNotFoundError(f"灯具文件不存在：{ies}")

    # 预检模态框：复用 driver.py 的保存弹窗自动应答，避免导入到一半被确认框卡住
    try:
        from .driver import auto_answer_save_prompt
        auto_answer_save_prompt(process_name)
    except Exception:  # pragma: no cover - 预检失败不应阻塞主流程
        logger.debug("保存弹窗预检跳过")

    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(LUMINAIRE_PS1),
        "-IesPath", str(ies.resolve()),
        "-ProcessName", process_name,
    ]
    if prototype_name:
        cmd += ["-PrototypeName", prototype_name]
    if count_x > 0:
        cmd += ["-CountX", str(count_x)]
    if count_y > 0:
        cmd += ["-CountY", str(count_y)]

    logger.info("布灯驱动启动：%s（%s）", ies.name,
                f"{count_x}x{count_y}" if count_x > 0 else "自动网格")

    events: List[StepEvent] = []
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
            logger.warning("收到停止请求，布灯中止（DIALux 内是半成品）")
            break
        parsed = _parse(line)
        if parsed is None:
            if line.strip():
                logger.debug("布灯输出（非 STEP）：%s", line.rstrip())
            continue
        name, ok, detail = parsed
        ev = StepEvent(
            name=name,
            title=_LUM_STEPS.get(name, name),
            ok=ok,
            detail=detail,
            progress=min(100.0, len(events) / len(_LUM_STEPS) * 100.0),
        )
        events.append(ev)
        logger.info("[%5.1f%%] %s %s%s", ev.progress, ev.title,
                    "OK" if ok else "失败", f"（{detail}）" if detail else "")
        if not ok:
            # 失败即停：脚本已在失败处 exit 1
            break
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.error("布灯驱动超时 %ds，已杀掉", timeout_s)
    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if stderr.strip():
        logger.warning("布灯 stderr：%s", stderr.strip()[:300])
    if cancelled:
        events.append(StepEvent("cancelled", "已停止", False,
                                "停在布灯中途，DIALux 里是半成品",
                                events[-1].progress if events else 0.0))
    return events


def _last_ok(events: List[StepEvent]) -> bool:
    """是否跑到 lum-done 且全 ok。"""
    return bool(events) and all(e.ok for e in events) and events[-1].name == "lum-done"