"""执行器 action 通用定义：ActionResult 字面量 + 加载 JSONL plan。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Literal

ActionResult = Literal["OK", "RETRY", "HALT"]


def load_plan(path) -> List[dict]:
    """按 JSONL 读取 plan（每行一个 action dict）。"""
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def dump_plan(plan: List[dict], path) -> None:
    """把 plan 按 JSONL 写入 path（每行一条 action）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for a in plan:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
