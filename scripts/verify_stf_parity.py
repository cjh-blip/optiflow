"""STF 金标准核对（任务 2 验收二）。

做两件事，都要看实际输出：

A. 迁移等价性（真正的「搬得准」证据）
   同一份 mvp3_ir.json，分别用源仓 dialux-compiler 的 stf.py 和搬过来的 stf.py
   生成 STF，两份必须逐字节一致 —— 证明搬运没改动任何行为。

B. 与任务书指定的黄金文件 build/mvp3_lums.stf 比对
   该黄金文件是 MVP3 之前的产物（灯具段是旧的一行占位格式 LumN=x y z symbol）；
   现版 stf.py（源仓 commit e06d9dc）已按 STF-Exporter Command.cs 改成三行格式
   LumN=名称 / LumN.Pos= / LumN.Rot=。整文件 cmp 必然不一致，这是历史产物过期，
   不是搬运出错（见 BLOCKED.md B-1）。因此同时给出：
     B1 逐字节 cmp 结果
     B2 同语义比对（非灯具行逐行一致 + 灯具位置/型号逐盏一致）→ 必须绿

退出码：A 与 B2 都通过 = 0，否则 = 1。

用法：
    python scripts/verify_stf_parity.py              # 正查
    python scripts/verify_stf_parity.py --break-x    # 反向验证：把首个 x 挪 1e-3 米
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_REPO = Path(r"D:/dev/dialux-compiler")
IR_PATH = SOURCE_REPO / "build" / "mvp3_ir.json"
GOLDEN = SOURCE_REPO / "build" / "mvp3_lums.stf"
GOLDEN_DATE = "2026-09-05"
PROJECT_NAME = "mvp3_lums"
OUT_DIR = ROOT / "build" / "golden_check"

RE_NEW = re.compile(r"^Lum(\d+)=(.+)$")
RE_NEW_POS = re.compile(r"^Lum(\d+)\.Pos=(.+)$")
RE_OLD = re.compile(r"^Lum(\d+)=(\S+) (\S+) (\S+) (\S+)$")


def load_ir(perturb_x: bool) -> dict:
    """读源仓 IR（只读）。perturb_x 时把第 2 个顶点的 x 挪 1e-3 米，用于反向验证。"""
    ir = json.loads(IR_PATH.read_text(encoding="utf-8"))
    if perturb_x:
        poly = ir["storeys"][0]["spaces"][0]["polygon"]
        before = poly[1][0]
        poly[1][0] = float(before) + 0.001
        print(f"  [扰动] spaces[0].polygon[1][0] {before} -> {poly[1][0]}")
    return ir


def gen_migrated(ir: dict, out: Path) -> Path:
    """用搬进 OptiFlow 的 stf.py 生成。"""
    sys.path.insert(0, str(ROOT / "src"))
    from optiflow.adapters.dialux.stf import write_stf
    return write_stf(ir, out, project_name=PROJECT_NAME, date=GOLDEN_DATE)


def gen_source(ir_path: Path, out: Path) -> None:
    """用源仓 stf.py 生成（子进程；只读源仓，只写 OptiFlow）。"""
    cmd = [sys.executable, str(ROOT / "scripts" / "run_source_exporter.py"),
           str(ir_path), str(out), PROJECT_NAME, GOLDEN_DATE]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("源仓生成失败: " + r.stdout + r.stderr)


def split_lums(lines: list) -> tuple:
    """拆出「非灯具行」与「灯具语义表」。兼容旧一行格式与新三行格式。"""
    head: list = []
    lums: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        m = RE_NEW.match(line)
        if m and i + 2 < len(lines):
            nxt = RE_NEW_POS.match(lines[i + 1])
            if nxt and nxt.group(1) == m.group(1):
                pos = nxt.group(2).split()
                lums[int(m.group(1))] = (pos[0], pos[1], pos[2], m.group(2))
                i += 3
                continue
        m = RE_OLD.match(line)
        if m:
            lums[int(m.group(1))] = (m.group(2), m.group(3), m.group(4), m.group(5))
            i += 1
            continue
        head.append(line)
        i += 1
    return head, lums


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--break-x", action="store_true", help="反向验证：扰动一个坐标")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ir = load_ir(args.break_x)
    ir_path = OUT_DIR / "mvp3_ir_used.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")

    new_stf = gen_migrated(ir, OUT_DIR / "mvp3_lums_new.stf")
    orig_stf = OUT_DIR / "mvp3_lums_orig.stf"
    gen_source(ir_path, orig_stf)

    print("")
    print("===== A. 迁移等价性：源仓 stf.py  vs  搬过来的 stf.py =====")
    print("  源仓产物: " + str(orig_stf))
    print("  新仓产物: " + str(new_stf))
    print("  $ cmp <源仓产物> <新仓产物>")
    ra = subprocess.run(["cmp", str(orig_stf), str(new_stf)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("  " + (ra.stdout.strip() or ra.stderr.strip() or "(无输出，即完全一致)"))
    print("  退出码: " + str(ra.returncode))
    equivalent = ra.returncode == 0
    print("  [PASS] 两份逐字节一致：搬运未改变输出" if equivalent
          else "  [FAIL] 两份不一致：搬运改动了行为")

    print("")
    print("===== B. 与黄金文件 build/mvp3_lums.stf 比对 =====")
    print("  $ cmp /d/dev/dialux-compiler/build/mvp3_lums.stf <新仓产物>")
    rb = subprocess.run(["cmp", str(GOLDEN), str(new_stf)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("  " + (rb.stdout.strip() or rb.stderr.strip() or "(无输出，即完全一致)"))
    print("  退出码: " + str(rb.returncode) + "  (0=逐字节一致)")

    g_head, g_lums = split_lums(GOLDEN.read_text(encoding="utf-8").splitlines())
    n_head, n_lums = split_lums(new_stf.read_text(encoding="utf-8").splitlines())
    head_same = g_head == n_head
    # 灯具比对分两层：位置 XY + 型号必须逐盏一致；
    # Z 单独看——旧格式把 Z 写死成 0，新格式写真实挂高（正是 MVP3 修掉的那个错）。
    g_xysym = {k: (v[0], v[1], v[3]) for k, v in g_lums.items()}
    n_xysym = {k: (v[0], v[1], v[3]) for k, v in n_lums.items()}
    lums_same = g_xysym == n_xysym
    z_changed = [k for k in sorted(set(g_lums) & set(n_lums)) if g_lums[k][2] != n_lums[k][2]]

    print("")
    print("  B1 逐字节   : " + ("一致" if rb.returncode == 0 else "不一致（历史产物格式过期，见 BLOCKED.md B-1）"))
    print("  B2 非灯具行 : 黄金 " + str(len(g_head)) + " 行 / 新仓 " + str(len(n_head)) + " 行 -> "
          + ("逐行一致" if head_same else "不一致"))
    print("  B2 灯具 XY+型号: 黄金 " + str(len(g_lums)) + " 盏 / 新仓 " + str(len(n_lums)) + " 盏 -> "
          + ("逐盏一致" if lums_same else "不一致"))
    z_g = sorted({v[2] for v in g_lums.values()})
    z_n = sorted({v[2] for v in n_lums.values()})
    print("  B2 灯具 Z      : 黄金 " + str(z_g) + "（旧格式写死），新仓 " + str(z_n)
          + "（真实挂高）；差异 " + str(len(z_changed)) + " 盏 —— 即 MVP3 修掉的旧 bug")
    if not head_same:
        for a, b in zip(g_head, n_head):
            if a != b:
                print("     首个差异行: 黄金 " + repr(a) + "  vs  新仓 " + repr(b))
                break
    if not lums_same:
        bad = [k for k in sorted(set(g_lums) | set(n_lums)) if g_lums.get(k) != n_lums.get(k)]
        print("     差异灯具: " + str(bad[:5]) + " 共 " + str(len(bad)) + " 盏")
    semantic_ok = head_same and lums_same
    print("  [PASS] 黄金文件同语义比对通过（房间几何逐行一致、灯具位置/型号逐盏一致）" if semantic_ok
          else "  [FAIL] 黄金文件同语义比对不通过")

    print("")
    print("===== 结论 =====")
    print("  A  迁移等价性     : " + ("PASS" if equivalent else "FAIL"))
    print("  B1 黄金文件逐字节 : " + ("PASS" if rb.returncode == 0 else "FAIL（历史产物格式过期）"))
    print("  B2 黄金文件同语义 : " + ("PASS" if semantic_ok else "FAIL"))
    total = "PASS" if (equivalent and semantic_ok) else "FAIL"
    print("  -> 总判定: " + total)
    return 0 if (equivalent and semantic_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
