# -*- coding: utf-8 -*-
"""灯库读档：解析链（参数 > 环境变量 > 未接入）、坏档不抛、按 sku 索引。

铁律：读档任何失败都返回 (None, 人话原因)，绝不抛进报告——报告层只负责如实标注。
护栏：只校验不推荐、不排灯位（本模块连「排序」都没有，索引保持录入顺序）。
"""
from __future__ import annotations

import json
from pathlib import Path

from optiflow.lamp_library import ENV_KEY, load_lamp_library, lookup

LIB_DOC = {
    "version": "lamp-library-v0",
    "items": [
        {"sku": "LMP-A", "manufacturer": "Acme", "model": "Panel-36", "kind": "point",
         "flux_lm": 4200.0, "power_w": 36.0, "efficacy_lm_w": 116.7,
         "source": "目录册 P12", "notes": ""},
        {"sku": "LMP-B", "manufacturer": "Bee", "model": "Tube-18", "kind": "linear",
         "flux_lm": 1800.0, "power_w": 18.0, "efficacy_lm_w": 100.0,
         "source": "实测链 2026-10-11", "notes": ""},
    ],
}


def _write(tmp_path: Path, doc, name: str = "lamp.json") -> str:
    path = tmp_path / name
    text = doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_not_configured_is_unevidenced_not_error(monkeypatch) -> None:
    """没配就是「未接入」，提示里带修复入口，不抛错。"""
    monkeypatch.delenv(ENV_KEY, raising=False)
    lib, status = load_lamp_library(None)
    assert lib is None
    assert "未接入" in status and ENV_KEY in status


def test_explicit_beats_env(monkeypatch, tmp_path) -> None:
    """参数优先于环境变量。"""
    a = _write(tmp_path, LIB_DOC, "a.json")
    b = _write(tmp_path, {**LIB_DOC, "version": "other"}, "b.json")
    monkeypatch.setenv(ENV_KEY, b)
    lib, status = load_lamp_library(a)
    assert status == "ok" and lib["version"] == "lamp-library-v0"


def test_missing_path_says_which(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(ENV_KEY, raising=False)
    lib, status = load_lamp_library(str(tmp_path / "不存在.json"))
    assert lib is None and "路径不存在" in status


def test_broken_json_never_raises(monkeypatch) -> None:
    """坏 JSON / 空 items / 无 sku 全部降级为人话，不抛栈。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        lib, status = load_lamp_library(str(bad))
        assert lib is None and "不可读" in status

        empty = Path(d) / "empty.json"
        empty.write_text(json.dumps({"items": []}), encoding="utf-8")
        lib, status = load_lamp_library(str(empty))
        assert lib is None and "items" in status

        nosku = Path(d) / "nosku.json"
        nosku.write_text(json.dumps({"items": [{"flux_lm": 1}]}), encoding="utf-8")
        lib, status = load_lamp_library(str(nosku))
        assert lib is None and "sku" in status


def test_index_lookup_and_order_kept(monkeypatch, tmp_path) -> None:
    """按 sku 命中/未命中；条目顺序＝录入顺序（不排序）；重复 sku 取首条。"""
    doc = {"version": "v0", "items": [
        {"sku": "X1", "flux_lm": 100.0},
        {"sku": "X2", "flux_lm": 200.0},
        {"sku": "X1", "flux_lm": 999.0},
    ]}
    lib, status = load_lamp_library(_write(tmp_path, doc))
    assert status == "ok"
    assert [r["sku"] for r in lib["items"]] == ["X1", "X2", "X1"]   # 原样保留
    assert lookup(lib, "X2")["flux_lm"] == 200.0
    assert lookup(lib, "X1")["flux_lm"] == 100.0                    # 重复取首条
    assert lookup(lib, "NOPE") is None
    assert lookup(lib, None) is None and lookup(None, "X1") is None
