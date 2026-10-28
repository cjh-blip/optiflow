"""IES 取件：本地优先、在线兜底、离线给人话（Task 3）。

为什么需要这层：灯具库索引只是清单（38 条元数据），IES 文件本身要么本地已有，
要么按 `download_url` 从 ieslibrary 取。产品里不能裸跑 curl，也不该让用户在
「选好灯了却送不进 DIALux」这一步卡死。

纪律：
- 本地命中优先于任何网络动作（离线可用是 v0.4 的卖点，不能退）。
- 下载先写 `.part` 再改名，半截文件绝不冒充缓存。
- 失败一律带指路的人话（哪款、缺哪、怎么补），不抛裸 OSError。
"""
from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

_DEFAULT_BASE = "https://ieslibrary.com"
_TIMEOUT_S = 10.0
_ENV_DIR = "GUANGSHU_IES_DIR"


def _download(url: str, dest: Path) -> None:
    """把 url 下到 dest（被测试替换的接缝）。"""
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def _cache_dir(explicit: Optional[str | Path]) -> Path:
    if explicit:
        p = Path(explicit)
    elif os.environ.get(_ENV_DIR):
        p = Path(os.environ[_ENV_DIR])
    else:
        p = Path.cwd() / "ies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_ies(record: Mapping[str, Any], *, cache_dir: Optional[str | Path] = None,
               base_url: str = _DEFAULT_BASE) -> Path:
    """确保某款灯具的 IES 在本地，返回路径。

    `record` 至少要有 `file`；有 `download_url` 才能在线取件。
    """
    if not isinstance(record, Mapping):
        raise ValueError(f"灯具记录应为映射（收到 {type(record).__name__}）")
    name = str(record.get("file") or "").strip()
    if not name:
        raise ValueError(f"灯具记录缺 file 字段，无法确定 IES 文件名：{dict(record)}")

    folder = _cache_dir(cache_dir)
    local = folder / name
    if local.exists() and local.stat().st_size > 0:
        return local

    rel = str(record.get("download_url") or "").strip()
    if not rel:
        raise RuntimeError(
            f"本地没有 {name}，且该记录没有 download_url，无法在线取件。"
            f"请把 IES 文件放到 {folder} 后重试"
        )

    url = rel if rel.startswith(("http://", "https://")) else base_url.rstrip("/") + "/" + rel.lstrip("/")
    part = folder / (name + ".part")
    try:
        _download(url, part)
        part.replace(local)
    except Exception as exc:  # noqa: BLE001 - 网络/权限错误统一转人话
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"取件 {name} 需要联网：下载 {url} 失败（{exc}）。"
            f"离线环境下请把该 IES 手工放到 {folder} 再重试"
        ) from exc
    return local
