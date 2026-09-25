"""validator 迁入 optiflow 的验证（BLOCKED.md B-10 跨包耦合收敛）。

三条线：
1. 等价性：同 IR 下，optiflow.validator 与本仓 src/validator 输出逐条一致；
2. 自包含：--validate 全链在「只有 src/ 在 PYTHONPATH」时跑通（仓库根不参与）；
3. schema 同步：包内 spec/ 与仓库根 spec/ 当前同内容（「暂时两份」的显式记录）。

改动前 `--validate` 会 `ModuleNotFoundError: No module named 'src'`（离开 pytest 即露馅）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


# ---------- 1. 等价性（搬得准的直接证据） ----------

@pytest.mark.parametrize("fixture", ["mini_ir_2rooms.json", "mvp3_ir.json"])
def test_migrated_validator_matches_source_validator(fixture: str) -> None:
    """同一 IR：搬进 optiflow 的 validator 与 src/validator 输出逐条一致。

    两边都吃独立深拷贝，避免 validator 内部的 in-place 修改串味。
    """
    from optiflow.validator import validate_ir as migrated
    from src.validator import validate_ir as source

    ir = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    assert migrated(json.loads(json.dumps(ir))) == source(json.loads(json.dumps(ir)))


def test_migrated_validator_matches_source_on_violating_ir() -> None:
    """故意造违规 IR（space 缺 id + 多边形未闭合），两边输出仍逐条一致。"""
    from optiflow.validator import validate_ir as migrated
    from src.validator import validate_ir as source

    bad = {
        "schema_version": "0.1",
        "project": {"name": "bad", "source": {}, "units": "m"},
        "storeys": [{"level": 1, "spaces": [
            {"name": "缺id且未闭合", "polygon": [[0, 0], [2, 0], [2, 2]],
             "ceil_h": 3.0, "luminaires": []},
        ]}],
    }
    new_viols = migrated(json.loads(json.dumps(bad)))
    old_viols = source(json.loads(json.dumps(bad)))
    assert new_viols == old_viols
    assert new_viols  # 且真的拦到了东西（否则这条测试是空转）


# ---------- 2. 自包含 ----------

def test_validate_cli_is_self_contained(tmp_path: Path) -> None:
    """--validate 全链在「只有 src/ 在 PYTHONPATH」时跑通（仓库根不参与）。"""
    ir_src = FIXTURES / "mini_ir_2rooms.json"
    out_stf = tmp_path / "out.stf"

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-m", "optiflow.adapters.dialux.stf", "--validate",
         str(ir_src), str(out_stf)],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out_stf.exists()
    assert "[VERSION]" in out_stf.read_text(encoding="utf-8")


def test_load_schema_resolves_from_package() -> None:
    """load_schema 走包内 spec/（不经过 src.core.env）。"""
    from optiflow.validator import load_schema

    schema = load_schema()
    assert isinstance(schema, dict) and schema  # 真的读到了东西


# ---------- 3. schema 同步（暂时两份的显式记录） ----------

def test_validator_schema_stays_in_sync_with_root_spec() -> None:
    """包内 spec/ 与仓库根 spec/ 同内容。

    将来若废弃仓库根那份，本测试一并删除；在那之前，任何一边漂移都会先红，提醒同步。
    """
    pkg_schema = ROOT / "src" / "optiflow" / "spec" / "ir.schema.json"
    root_schema = ROOT / "spec" / "ir.schema.json"
    assert pkg_schema.exists(), "包内 schema 缺失：--validate 在别处启动会找不到"
    assert root_schema.exists()
    assert pkg_schema.read_bytes() == root_schema.read_bytes()
