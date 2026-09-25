"""STF 基准守护：搬过来的 stf.py 必须逐字节复现源仓导出器的输出。

背景（BLOCKED.md B-1）：任务书原本指定的「黄金文件」build/mvp3_lums.stf 是 MVP3 之前的
过期产物（灯具段是旧的一行占位格式，Z 写死 0），源仓自己也生不出它。判卷标准因此改为
「迁移等价性」：把源仓 src/exporter/stf.py（blob 9857ee16fba786974fb7b2f5a8a43c4ac269574e，
最后改动 commit e06d9dc）在 tests/fixtures/mvp3_ir.json 上的输出，固化成
tests/fixtures/mvp3_lums_baseline.stf。此后搬进来的导出器必须逐字节复现它。

没有这一条，「迁移等价性」会随着源仓漂移而失去意义——所以基准必须落在本仓，
且跑测试时**不许**去读冻结仓（源仓一改就会假红或悄悄漂移）。

重新冻结基准是刻意动作：python scripts/freeze_stf_baseline.py --write
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from optiflow.adapters.dialux.stf import write_stf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
IR_FIXTURE = FIXTURES / "mvp3_ir.json"
BASELINE = FIXTURES / "mvp3_lums_baseline.stf"

RE_OLD_FORMAT = re.compile(r"^Lum\d+=\S+ \S+ \S+ \S+$", flags=re.MULTILINE)


def _baseline_text() -> str:
    return BASELINE.read_text(encoding="utf-8")


def _kv(text: str, key: str) -> str:
    m = re.search(r"^" + re.escape(key) + r"=(.*)$", text, flags=re.MULTILINE)
    assert m, "基准文件里找不到 " + key + "="
    return m.group(1)


def _load_ir() -> dict:
    return json.loads(IR_FIXTURE.read_text(encoding="utf-8"))


def _regenerate(ir: dict, out_dir: Path) -> bytes:
    """按基准自己声明的 Name/Date 重新生成，避免测试里硬编码常量与基准漂移。"""
    text = _baseline_text()
    out = out_dir / "regen.stf"
    write_stf(ir, out, project_name=_kv(text, "Name"), date=_kv(text, "Date"))
    return out.read_bytes()


def test_baseline_fixture_exists_and_is_the_post_mvp3_format() -> None:
    """基准必须是 MVP3 之后的三行灯具格式——否则守护的是错的格式。"""
    assert BASELINE.exists(), "基准文件缺失: " + str(BASELINE)
    assert IR_FIXTURE.exists(), "基准 IR 缺失: " + str(IR_FIXTURE)
    text = _baseline_text()
    assert "Lum1.Pos=" in text and "Lum1.Rot=" in text, "基准不是三行灯具格式"
    assert not RE_OLD_FORMAT.search(text), "基准仍是 MVP3 之前的一行占位格式"


def test_baseline_has_the_expected_shape() -> None:
    """基准覆盖真实几何 + 灯具：1 房间 / 28 盏 / 房间环闭合。"""
    text = _baseline_text()
    assert _kv(text, "STFF") == "1.0"
    assert _kv(text, "NrRooms") == "1"
    assert "NrLums=28" in text
    pts = re.findall(r"^Point\d+=(.*)$", text, flags=re.MULTILINE)
    assert len(pts) >= 4 and pts[0] == pts[-1], "基准房间环未闭合"


def test_migrated_exporter_reproduces_frozen_baseline_bytes(tmp_path: Path) -> None:
    """核心断言：搬过来的导出器输出与冻结基准**逐字节**一致。"""
    got = _regenerate(_load_ir(), tmp_path)
    want = BASELINE.read_bytes()
    if got != want:
        a = want.decode("utf-8").splitlines()
        b = got.decode("utf-8").splitlines()
        diff = [(i + 1, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y][:3]
        pytest.fail("与冻结基准不一致，首个差异 (行号, 基准, 实际): " + repr(diff))


def test_baseline_check_catches_a_perturbed_coordinate(tmp_path: Path) -> None:
    """反向验证：坐标挪 1e-3 米，这条检查必须变红（否则它是死的）。"""
    ir = _load_ir()
    ir["storeys"][0]["spaces"][0]["polygon"][1][0] += 0.001
    assert _regenerate(ir, tmp_path) != BASELINE.read_bytes(), \
        "扰动坐标后仍与基准一致——这条检查是死的"


def test_baseline_check_catches_a_perturbed_luminaire_height(tmp_path: Path) -> None:
    """反向验证：灯具挂高改动同样必须被发现（守护的是整个文件，不只房间几何）。"""
    ir = _load_ir()
    ir["storeys"][0]["spaces"][0]["luminaires"][0]["z"] = 2.9
    assert _regenerate(ir, tmp_path) != BASELINE.read_bytes(), \
        "扰动灯具挂高后仍与基准一致——这条检查是死的"


def test_baseline_check_is_self_contained() -> None:
    """基准不许依赖冻结仓：测试代码里不出现源仓路径，也不 shell out。"""
    body = Path(__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    # 针在运行时拼出来，免得断言字符串自己把自己搜到
    repo_name = "dialux" + "-compiler"
    shell_out = "sub" + "process"
    assert repo_name not in body, "测试代码引用了源仓，会随源仓漂移"
    assert shell_out not in body, "测试不该起子进程"
