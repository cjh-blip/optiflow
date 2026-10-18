# -*- coding: utf-8 -*-
"""灯库读档与比对（光枢灯库 v0）。

护栏与 `plan.light_backcalc` 同：**只校验、不推荐、不排灯位**——
报告拿灯库逐行核对数值、缺值时按库值补依据，但从不排序、不点名「买哪个」。

库文件是外部数据（JSON，schema 见库内 ``schema`` 段），平台不内置副本：
路径解析链＝调用参数 ``lamp_lib`` > 环境变量 ``GUANGSHU_LAMP_LIB`` > 未接入。
读档任何失败都**不抛进报告**：返回 ``(None, 人话原因)``，报告如实写「未接入/不可读」，
沿用组装器铁律——拿不到的就标未取证，不编数。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ENV_KEY = "GUANGSHU_LAMP_LIB"


def load_lamp_library(explicit: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """解析灯库路径并读档。

    返回 ``(库, 状态)``：成功时库为
    ``{"items": [...], "index": {sku: 行}, "version": …, "origin": 路径}``，状态 ``"ok"``；
    失败时库为 ``None``，状态是给人看的原因（含修复提示）。
    """
    raw = (explicit or os.environ.get(ENV_KEY) or "").strip()
    if not raw:
        return None, "未接入（可设环境变量 GUANGSHU_LAMP_LIB=<灯库 JSON 路径>）"
    path = Path(raw).expanduser()
    if not path.is_file():
        return None, f"未接入（路径不存在：{path}）"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"不可读（{type(exc).__name__}：{exc}）"
    if not isinstance(doc, dict):
        return None, "不可用（顶层不是对象）"
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        return None, "不可用（items 缺失或为空）"
    index: Dict[str, Dict[str, Any]] = {}
    for row in items:
        if isinstance(row, dict):
            sku = row.get("sku")
            if isinstance(sku, str) and sku:
                index.setdefault(sku, row)      # sku 唯一键：重了取首条，不抛错
    if not index:
        return None, "不可用（没有带 sku 的条目）"
    return {"items": items, "index": index,
            "version": doc.get("version"), "origin": str(path)}, "ok"


def lookup(lib: Optional[Dict[str, Any]], sku: Any) -> Optional[Dict[str, Any]]:
    """按 sku 查库行；未接入/未命中返回 None。"""
    if not lib or not isinstance(sku, str) or not sku:
        return None
    return lib["index"].get(sku)
