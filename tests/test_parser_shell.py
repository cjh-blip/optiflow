# -*- coding: utf-8 -*-
"""第三刀 · 刀A：薄壳（adapters/dialux）与受控收编实现（src/parser）的面对齐。

薄壳的职责只有重导出——本文件把「公开面不动」变成可跑的断言：
① 壳 `__all__` 里每个名字与实现是**同一个对象**（壳不许自己长出第二套逻辑）；
② 既有调用方实际 import 的公开名一个不少（签名不动的机器证据）；
③ `python -m optiflow.adapters.dialux.dxf <file>` 抽查入口照旧出 IR JSON。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import optiflow.adapters.dialux._chain as shell_chain
import optiflow.adapters.dialux.dxf as shell_dxf
import optiflow.adapters.dialux.xlsx as shell_xlsx
import parser._chain as impl_chain
import parser.dxf as impl_dxf
import parser.xlsx as impl_xlsx

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "sample_room.dxf"

PAIRS = (
    (shell_dxf, impl_dxf),
    (shell_chain, impl_chain),
    (shell_xlsx, impl_xlsx),
)

# 既有调用方（plan_summary / service / validator / 测试）实际 import 的名字，一个不许少
LEGACY_SURFACE = (
    ("dxf", ("ParseConfig", "parse_dxf", "extract_luminaires", "extract_rooms",
             "header_insunits_to_units", "Room", "Luminaire", "WALL_RING_LAYER",
             "convert_to_meters", "classify_rect_luminaire", "Point",
             "CLOSE_TOLERANCE_METERS", "LINEAR_ASPECT_MIN", "MIN_AREA_M2",
             "FURNITURE_AREA_M2")),
    ("_chain", ("COLLINEAR_TOL_M", "MAX_NOTCH_AREA_M2", "MAX_NOTCH_VERTICES",
                "SAWTOOTH_EDGE_M", "Point2", "arc_to_points", "count_short_edges",
                "dedup_rooms", "extract_rings_from_line_arc", "polygon_area",
                "polygon_centroid", "remove_small_notches", "ring_preference_key",
                "simplify_collinear", "smooth_room_ring")),
    ("xlsx", ("LuminaireSpec", "parse_xlsx")),
)


def test_shell_all_names_are_the_impl_objects() -> None:
    """壳导出的每个名字必须 is 实现里的那个对象（壳里多写一份 = 第二套口径）。"""
    for shell, impl in PAIRS:
        assert shell.__all__, f"{shell.__name__} 没有 __all__"
        for name in shell.__all__:
            assert hasattr(impl, name), f"实现缺 {impl.__name__}.{name}"
            assert getattr(shell, name) is getattr(impl, name), (
                f"{shell.__name__}.{name} 不是 {impl.__name__} 里的同一对象")


def test_legacy_surface_still_reachable() -> None:
    """既有调用方 import 的名字（含小写常量与类型别名）必须还能从壳里拿到。"""
    shells = {"dxf": shell_dxf, "_chain": shell_chain, "xlsx": shell_xlsx}
    for mod_name, names in LEGACY_SURFACE:
        shell = shells[mod_name]
        for name in names:
            assert hasattr(shell, name), f"薄壳 {shell.__name__} 丢了公开名 {name}"


def test_impl_lives_under_src_parser() -> None:
    """实现必须住在受控收编的 src/parser/（防止有人把逻辑搬回薄壳）。"""
    for shell, impl in PAIRS:
        impl_posix = Path(impl.__file__).as_posix()
        shell_posix = Path(shell.__file__).as_posix()
        assert "/src/parser/" in impl_posix, impl.__file__
        assert shell_posix != impl_posix, f"壳与实现成了同一个文件：{shell_posix}"


def test_dxf_cli_entry_still_prints_ir() -> None:
    """薄壳保留原实现的命令行抽查入口（默认配置解析，只验吐得出 IR 结构）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "optiflow.adapters.dialux.dxf", str(FIXTURE)],
        capture_output=True, text=True, timeout=120,
        env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin",
             "SYSTEMROOT": "C:\\Windows" if sys.platform == "win32" else ""},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    ir = json.loads(proc.stdout)
    # 默认 cfg 下 sample_room.dxf 会 0 间（配置才带 room_layers/units），
    # 所以这里只验结构：原实现也是这一行为，不额外加要求。
    assert "_meta" in ir and "storeys" in ir and "dxf_units" in ir["_meta"], ir.keys()
    # 与实现侧同参双跑必须逐字相同（壳不出第二套口径）
    impl_proc = subprocess.run(
        [sys.executable, "-m", "parser.dxf", str(FIXTURE)],
        capture_output=True, text=True, timeout=120,
        env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin",
             "SYSTEMROOT": "C:\\Windows" if sys.platform == "win32" else ""},
    )
    assert impl_proc.returncode == 0, impl_proc.stderr[-2000:]
    assert proc.stdout == impl_proc.stdout, "薄壳 CLI 与实现 CLI 输出不同"
