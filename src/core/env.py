"""运行时路径归一化（ODA / DIALux / OCR）。

学 Mrite 的 PATH 治理思路：在启动/调用外部可执行文件前，统一到一个入口函数，
避免 DEFAULT_ODA 这种写死版本号散落在各调用点。

优先级：
1. 调用时显式 override
2. 环境变量（ODA_PATH / DIALUX_PATH / OCR_PATH）
3. 默认安装路径
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

DEFAULT_ODA = r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"

# DIALux 启动器路径（launcher GUI 自动打开用）。安装位置本机固定，
# 参考 get_oda_path 的优先级：override > 环境变量 DIALUX_PATH > 默认路径。
DEFAULT_DIALUX = r"C:\Program Files\DIAL GmbH\DIALux\DIALux.exe"


def get_oda_path(override: Optional[str] = None) -> Path:
    """返回 ODA File Converter exe 路径，不存在则抛 RuntimeError。

    优先级：override > 环境变量 ODA_PATH > 默认安装路径。
    """
    raw = override or os.environ.get("ODA_PATH") or DEFAULT_ODA
    p = Path(raw)
    if not p.exists():
        raise RuntimeError(
            f"未找到 ODA File Converter：{p}\n"
            f"请从 https://www.opendesign.com/guestfiles/oda_file_converter 安装，"
            f"或设置环境变量 ODA_PATH，或在调用时传 override。默认安装路径：{DEFAULT_ODA}"
        )
    return p


def get_dialux_path(override: Optional[str] = None) -> Path:
    """返回 DIALux evo 可执行路径，不存在则抛 RuntimeError。

    优先级：override > 环境变量 DIALUX_PATH > 默认安装路径。
    launcher.py 自动打开 DIALux 时调用（不再各自硬编码）。
    """
    raw = override or os.environ.get("DIALUX_PATH") or DEFAULT_DIALUX
    p = Path(raw)
    if not p.exists():
        raise RuntimeError(
            f"未找到 DIALux 可执行文件：{p}\n"
            f"请设置环境变量 DIALUX_PATH，或确认安装路径。默认：{DEFAULT_DIALUX}"
        )
    return p


def get_ocr_runtime_path() -> Path:
    """返回 OCR 运行时路径，待实现。"""
    raise NotImplementedError("OCR runtime 路径未实现，待 MVP5 视觉自愈接入")


# ---------------------------------------------------------------- PyInstaller 兼容
def resource_path(rel: str | Path) -> Path:
    """把仓库内相对路径解析为运行时可用的绝对路径（兼容 PyInstaller 冻结）。

    冻结（打包成 exe）时，datas 会被解到临时目录 ``sys._MEIPASS``；未冻结时
    直接相对仓库根解析。调用方不要再对路径做 ``resolve()``——冻结后的
    ``_MEIPASS`` 在 ``%TEMP%`` 下，resolve 也没意义。

    用法::

        ps1 = resource_path("src/executor/uia/dialux_driver.ps1")
    """
    frozen = getattr(sys, "_MEIPASS", None)
    base = Path(frozen) if frozen else Path(__file__).resolve().parent.parent.parent
    return base / rel
