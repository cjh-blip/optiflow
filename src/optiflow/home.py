"""业务根：光枢往哪儿写东西。

解析顺序：显式参数 > 环境变量 ``GUANGSHU_HOME`` > None。

``None`` 是一个合法结果，不是错误：测试、临时调用、以及「只想算个数」的场景
都不该往磁盘写东西。返回 None 时所有持久化能力静默关闭——这是设计，不是遗漏。
但产品壳必须显式给根，否则用户跑完什么都留不下。

两个刻意的做法：

1. **读环境变量的动作放在函数里，不放模块级**。import 的时机不受调用方控制，
   模块级读到的值可能比调用方设置的值还早。
2. **不做路径猜测**。不回退到 ``~/.optiflow``、不回退到当前目录、不回退到程序
   目录。程序目录可能只读、可能在 Program Files、打包后还可能是解包的临时目录。
   拿不到根就不落盘，比落在一个谁也找不到的地方好。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

#: 业务根环境变量。与其它 GUANGSHU_* 前缀保持一致。
ENV_KEY = "GUANGSHU_HOME"


def resolve(explicit: str | Path | None = None) -> Optional[Path]:
    """返回业务根；拿不到就返回 None（调用方据此关闭持久化）。"""
    raw = explicit if explicit is not None else os.environ.get(ENV_KEY)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return Path(text).expanduser()


def describe(root: Optional[Path]) -> str:
    """给人看的一句话：这次到底存不存、存在哪。"""
    if root is None:
        return f"未设业务根（{ENV_KEY} 未设且未显式传入）→ 本次不落盘"
    return f"业务根 {root} → 项目与任务记录会写在这里"


__all__ = ["ENV_KEY", "describe", "resolve"]
