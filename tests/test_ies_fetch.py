"""IES 取件：本地优先、在线兜底、离线给人话（Task 3）。"""

from __future__ import annotations

import pytest

from optiflow.ies_fetch import ensure_ies


def test_returns_local_file_when_present(tmp_path):
    ies = tmp_path / "a.ies"
    ies.write_text("IESNA:LM-63-2002", encoding="utf-8")
    rec = {"id": "a", "file": "a.ies", "download_url": "/browse/download/ies/hashA"}
    assert ensure_ies(rec, cache_dir=tmp_path) == ies


def test_raises_human_readable_when_offline_and_absent(tmp_path, monkeypatch):
    def boom(url, dest):
        raise OSError("no net")

    monkeypatch.setattr("optiflow.ies_fetch._download", boom)
    rec = {"id": "zz", "file": "zz.ies", "download_url": "/browse/download/ies/hashZZ"}
    with pytest.raises(RuntimeError, match="需要联网"):
        ensure_ies(rec, cache_dir=tmp_path)


def test_rejects_record_without_file(tmp_path):
    with pytest.raises(ValueError, match="file"):
        ensure_ies({"id": "x", "download_url": "/d/x"}, cache_dir=tmp_path)


def test_download_writes_then_renames(tmp_path, monkeypatch):
    """下载走 .part 再改名，避免半截文件被当成缓存命中。"""
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        seen["dest"] = dest
        dest.write_bytes(b"IESNA")

    monkeypatch.setattr("optiflow.ies_fetch._download", fake_download)
    rec = {"id": "b", "file": "b.ies", "download_url": "/browse/download/ies/hashB"}
    out = ensure_ies(rec, cache_dir=tmp_path, base_url="https://example.test")
    assert out == tmp_path / "b.ies" and out.read_bytes() == b"IESNA"
    assert seen["url"] == "https://example.test/browse/download/ies/hashB"
    assert str(seen["dest"]).endswith(".part")
    assert not (tmp_path / "b.ies.part").exists()
