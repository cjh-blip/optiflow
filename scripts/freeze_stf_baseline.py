"""重新冻结 STF 基准（**刻意动作**，只在明确要换基准时跑）。

基准 = 源仓 dialux-compiler 的 src/exporter/stf.py 在 tests/fixtures/mvp3_ir.json 上的输出，
固化为 tests/fixtures/mvp3_lums_baseline.stf。

为什么需要它：任务书原本的「黄金文件」build/mvp3_lums.stf 是 MVP3 之前的过期产物，
源仓自己也生不出它（见 BLOCKED.md B-1）。判卷标准改为「迁移等价性」后，
必须有一份**本地冻结**的参照，否则这条检查会随源仓漂移而失去意义。

用法：
    python scripts/freeze_stf_baseline.py            # 只核对（默认，dry-run）
    python scripts/freeze_stf_baseline.py --write    # 真的重写基准文件
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_REPO = Path(r"D:/dev/dialux-compiler")
IR_FIXTURE = ROOT / "tests" / "fixtures" / "mvp3_ir.json"
BASELINE = ROOT / "tests" / "fixtures" / "mvp3_lums_baseline.stf"
PROJECT_NAME = "mvp3_lums"
BASELINE_DATE = "2026-09-13"


def provenance() -> list:
    """源仓出处：HEAD、stf.py blob、stf.py 最后一次改动。"""
    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=str(SOURCE_REPO), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip()

    return [
        "HEAD            : " + git("rev-parse", "HEAD"),
        "stf.py blob hash: " + git("hash-object", "src/exporter/stf.py"),
        "stf.py 最后改动 : " + git("log", "-1", "--format=%h %s", "--", "src/exporter/stf.py"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真的重写基准文件")
    args = ap.parse_args()

    print("源仓出处:")
    for line in provenance():
        print("  " + line)

    tmp = BASELINE.with_suffix(".regen.tmp")
    cmd = [sys.executable, str(ROOT / "scripts" / "run_source_exporter.py"),
           str(IR_FIXTURE), str(tmp), PROJECT_NAME, BASELINE_DATE]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("源仓导出器执行失败: " + r.stdout + r.stderr)
        return 1

    same = BASELINE.exists() and BASELINE.read_bytes() == tmp.read_bytes()
    if same:
        print("当前基准与重新生成的结果逐字节一致，无需改动。")
        tmp.unlink()
        return 0

    print("当前基准与重新生成的结果不一致。")
    if not args.write:
        print("（dry-run；确认要换基准就加 --write）新结果留在: " + str(tmp))
        return 1

    tmp.replace(BASELINE)
    print("基准已重写: " + str(BASELINE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
