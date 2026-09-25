"""通用自动化工作台：配置持久化（Config）。

全抄 BetterGI Core/Config（抄架构不抄代码）：配置是 ObservableObject，UI
绑定后改动即存盘。我们用 dataclass + JSON，UI 层再绑定。

约定：
- 配置根目录：``~/.dialux-workbench/config.json``（跨项目共享，未来换软件同用）
- 每类任务一个 section（如 ``luminaires`` / ``room`` / ``report``），
  工作台读全部、任务只取自己的 section
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".dialux-workbench"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"


@dataclass
class WorkbenchConfig:
    """工作台配置。``to_dict/from_dict`` 支持任意嵌套 dataclass。"""

    # 工作区：默认图纸/灯具/报告目录（参考 Mrite：运行时确定，路径可空让 UI 选）
    workspace_dir: str = ""
    dwg: str = ""
    dwg_lighting: str = ""
    parse_config: str = ""

    # 灯具默认两件套（放灯任务用）
    luminaire_point: str = ""
    luminaire_linear: str = ""

    # 各任务的 section（未来软件 / 新任务各自往这里加字段）
    sections: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 流程断点记忆（一条龙从哪续跑）
    flow_resume: Dict[str, str] = field(default_factory=dict)

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkbenchConfig":
        known = {f: d.get(f) for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**known)

    def save(self, path: Path = DEFAULT_CONFIG_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_FILE) -> "WorkbenchConfig":
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                return cls.from_dict(d)
            except Exception as exc:  # noqa: BLE001 - 配置损坏不致命，回默认
                logger.warning("配置读取失败（%s），用默认：%s", path, exc)
        return cls()
