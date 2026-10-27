#!/usr/bin/env python3
"""跨仓同图断言（第三刀 · 刀A，2026-10-19）：收编副本与真相源，同 fixtures 双跑逐字相同。

用法：
    GUANGSHU_DC=<dialux-compiler 仓根> python scripts/cross_parser_parity.py

为什么有它：`src/parser/` 是从 `dialux-compiler/src/parser` 受控收编来的副本——
「今天还没漂」不等于「一直一致」，所以把双跑比对写进推送门（见 `pre_push_check.sh` ⑤），
两令断言一红就推不动。dc 侧真相源按分工**不动**，发现分叉＝平台侧去同步。

约定（与 metrics_parity 同款）：
- 未给 `GUANGSHU_DC` → 打印跳过并退出码 0（环境没给就不冒充通过，输出里会明说没跑）；
- 给了但路径不存在 / 双跑不一致 / 任一侧异常 → 退出码 1，打印第一处分叉。
- 本脚本不写死任何本机路径（公开仓红线）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # optiflow 仓根
FIXTURE_NAMES = ("sample_room.dxf", "sample_lighting.dxf")
CFG_NAME = "sample_parse_config.json"

# 同一段抽数姿势，只有 import 行不同（平台 = 受控收编副本；dc = 真相源）
_RUNNER = '''
import json, sys
from pathlib import Path
{import_line}
dxf_path, cfg_path = sys.argv[1], sys.argv[2]
cfg = ParseConfig(**json.loads(Path(cfg_path).read_text(encoding="utf-8")))
ir = parse_dxf(dxf_path, cfg)
print(json.dumps(ir, ensure_ascii=False, sort_keys=True))
'''


def _run(import_line: str, cwd: Path, pythonpath: str, dxf: Path, cfg: Path) -> str:
    """在独立子进程里跑一侧解析器，返回 IR 的 JSON 文本（stdout）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = pythonpath
    # Windows 陷阱：剥掉 PYTHON* 后子进程按系统 locale（本机 GBK）输出，而父端 text=True
    # 默认按本机 UTF-8 解 → ReaderThread 解码失败，stdout 直接变 None，下游 strip()
    # 报 `AttributeError: 'NoneType' object has no attribute 'strip'`，完全看不出是编码问题。
    # 显式把子进程输出钉死 UTF-8，父端也按 UTF-8 解（两侧用同一编码，逐字节比较仍公平）。
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    code = _RUNNER.format(import_line=import_line)
    proc = subprocess.run(
        [sys.executable, "-c", code, str(dxf), str(cfg)],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cwd.name} 侧解析器退出码 {proc.returncode}\n{(proc.stderr or '').strip()[-2000:]}")
    return (proc.stdout or "").strip()


def _first_divergence(a: str, b: str) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            lo = max(0, i - 60)
            return (f"位置 {i}：本仓 …{a[lo:i + 60]!r}…\n"
                    f"        真相 …{b[lo:i + 60]!r}…")
    return f"长度不同：本仓 {len(a)} 字符 / 真相 {len(b)} 字符；末段：本仓 …{a[-80:]!r} vs 真相 …{b[-80:]!r}"


def main() -> int:
    dc_env = os.environ.get("GUANGSHU_DC", "").strip()
    if not dc_env:
        print("⚠ 跳过：未给 GUANGSHU_DC（收编副本与真相源的同图比对本次没跑）")
        return 0
    dc_root = Path(dc_env).expanduser().resolve()
    if not (dc_root / "src" / "parser" / "dxf.py").is_file():
        print(f"✗ GUANGSHU_DC 指向的仓不对：{dc_root} 下没有 src/parser/dxf.py")
        return 1

    print("════ 跨仓同图断言：src/parser（受控收编） vs dialux-compiler/src/parser（真相源）")
    print(f"  本仓 = {HERE.name}    真相源 = {dc_root.name}")

    failures = 0
    checked = 0
    # 两侧 fixture 各比一轮：每轮喂同一个物理文件给两台解析器，路径相同 → IR 可逐字节比
    for label, fix_dir, dc_side in (
        ("本仓 fixture", HERE / "tests" / "fixtures", False),
        ("真相源 fixture", dc_root / "tests" / "fixtures", True),
    ):
        for name in FIXTURE_NAMES:
            dxf = fix_dir / name
            cfg = fix_dir / CFG_NAME
            if not dxf.is_file() or not cfg.is_file():
                print(f"  · {label} 缺 {name} 或 {CFG_NAME}，跳过该组")
                continue
            ours = _run("from parser.dxf import ParseConfig, parse_dxf",
                        HERE, str(HERE / "src"), dxf, cfg)
            theirs = _run("from src.parser.dxf import ParseConfig, parse_dxf",
                          dc_root, str(dc_root), dxf, cfg)
            checked += 1
            if ours == theirs:
                rooms = json.loads(ours).get("_meta", {}).get("rooms")
                print(f"  ✓ {label} · {name}：逐字相同（含 room id；{rooms} 间）")
            else:
                failures += 1
                print(f"  ✗ {label} · {name}：分叉！")
                print("    " + _first_divergence(ours, theirs))

    # fixture 自身是否漂移：信息级（上面每轮喂同一文件，比的是解析器；这里提示两仓底稿差异）
    for name in FIXTURE_NAMES + (CFG_NAME,):
        a = HERE / "tests" / "fixtures" / name
        b = dc_root / "tests" / "fixtures" / name
        if a.is_file() and b.is_file() and a.read_bytes() != b.read_bytes():
            print(f"  ⚠ 底稿差异：{name} 两仓 fixture 字节不同（不影响上面逐字比对，但要知道）")

    if checked == 0:
        print("✗ 一组都没比成（fixtures 缺失）")
        return 1
    print(f"════ {checked} 组双跑全部逐字相同" if not failures
          else f"════ {checked} 组里 {failures} 组分叉——先同步真相源再推")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
