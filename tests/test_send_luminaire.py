"""送灯 action：IES 取件 → 既有真机步骤（Task 4）。

三条输入：正常转调、非映射拒绝、下游失败原样上抛（Review Focus ④：
送灯失败绝不能静默成「成功」）。

壳层定位（2026-10-28 Mac 侧适配）：`app_real` 不在平台包的 src 下。单仓布局下它在
`<仓根>/standalone/`（打包快照则是 `standalone/optiflow_repo/tests` 往上两层）；
Mac 是拆分仓，壳在相邻的 `optiflow-app/standalone`。三个候选按序试，也可用
`GUANGSHU_SHELL` 显式指定；全找不到就 skip —— 平台仓单独检出时本来就不覆盖壳动作。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _shell_dir() -> Path | None:
    here = Path(__file__).resolve()
    cands = []
    env = os.environ.get("GUANGSHU_SHELL")
    if env:
        cands.append(Path(env))
    cands.append(here.parents[2])                                            # 打包快照布局
    cands.append(here.parents[3] / "optiflow-app" / "standalone")            # Mac 拆分仓
    for c in cands:
        if (c / "app_real.py").is_file():
            return c
    return None


_SHELL = _shell_dir()
if _SHELL is None:
    pytest.skip("壳层 app_real 不在本机（平台仓单测不覆盖壳动作）", allow_module_level=True)
if str(_SHELL) not in sys.path:
    sys.path.insert(0, str(_SHELL))


def test_send_luminaire_resolves_ies_then_places(monkeypatch, tmp_path):
    ies = tmp_path / "a.ies"
    ies.write_text("x", encoding="utf-8")
    monkeypatch.setattr("optiflow.ies_fetch.ensure_ies", lambda rec, **kw: ies)
    calls = {}

    def fake_place(*, ies, prototype_name=None, count_x=None, count_y=None):
        calls.update(ies=str(ies), count_x=count_x, count_y=count_y,
                     prototype=prototype_name)
        return {"steps": 3, "ies": str(ies)}

    monkeypatch.setattr("app_real._cmd_place_luminaires", fake_place)
    from app_real import _cmd_send_luminaire

    out = _cmd_send_luminaire(
        luminaire={"id": "a", "file": "a.ies", "download_url": "/d/a", "luminaire": "LEDPanel-30W"},
        count_x=3, count_y=3,
    )
    assert out["steps"] == 3
    assert calls["count_x"] == 3 and calls["count_y"] == 3
    assert calls["prototype"] == "LEDPanel-30W"


def test_send_luminaire_rejects_non_mapping():
    from app_real import _cmd_send_luminaire

    with pytest.raises(ValueError, match="灯具记录"):
        _cmd_send_luminaire(luminaire=["not", "a", "dict"])


def test_send_luminaire_surfaces_place_failure(monkeypatch, tmp_path):
    ies = tmp_path / "a.ies"
    ies.write_text("x", encoding="utf-8")
    monkeypatch.setattr("optiflow.ies_fetch.ensure_ies", lambda rec, **kw: ies)

    def boom(**kw):
        raise RuntimeError("真机忙：另一个动作正在跑")

    monkeypatch.setattr("app_real._cmd_place_luminaires", boom)
    from app_real import _cmd_send_luminaire

    with pytest.raises(RuntimeError, match="真机忙"):
        _cmd_send_luminaire(luminaire={"id": "a", "file": "a.ies", "download_url": "/d/a"})
