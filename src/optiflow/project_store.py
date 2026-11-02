"""项目文件：一个项目一个 JSON，原子写，版本不自动升级。

为什么不是 SQLite：这个规模（一个项目几条输入、几十条结果、几个版本）用不上
数据库的任何一项好处，却要背上连接、迁移、并发三件事。一个能被拷走、能进 git、
能用记事本打开的文件，比一个数据库有用得多。

文件形状（顶层分节，输入与派生物理隔开）::

    {
      "schema_version": 1,
      "saved_at": "2026-11-02T03:20:11+00:00",
      "project": {"id": ..., "name": ..., "created_at": ...},
      "inputs":  [ TaskSpec, ... ],                      # 原始输入，重算不许覆盖
      "derived": {"artifacts": ..., "results": [...], "versions": [...]}
    }

两件事是刻意的：

1. **不静默升级版本**。读到旧版本不自动迁移，读到新版本不尝试解释，一律报错并
   列出三条出路。学 InstaPunch 三类提示的做法：自动升级会把「我还读得懂」这个
   假象留到更后面才爆。
2. **原子写**。先写 ``.tmp`` 再 ``os.replace``。宁可留一份旧的完整文件，也不要
   一份新的半截文件——半截文件会让下一次打开直接失败，而旧文件至少能打开。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ir import TaskSpec
from .job import ResultSet
from .project import Project, ProjectVersion

#: 文件格式版本。改动 inputs / derived 的字段含义时才 +1。
SCHEMA_VERSION = 1

#: 项目文件后缀。带 .json 尾巴，双击能认出是 JSON，又不撞普通 json。
SUFFIX = ".gsproj.json"

#: 业务根下的项目目录名。
PROJECTS_DIR = "projects"


class ProjectFileError(Exception):
    """项目文件读写错误。``code`` 是给调用方分支用的，不是给人读的。

    code 取值：
    - ``missing``  文件不存在
    - ``corrupt``  内容坏了或缺节
    - ``too_old``  文件版本低于本应用
    - ``too_new``  文件版本高于本应用
    """

    def __init__(self, code: str, message: str, *, path: Optional[Path] = None,
                 hint: str = "") -> None:
        self.code = code
        self.path = path
        self.hint = hint
        super().__init__(message)


def project_path(root: str | Path, name: str) -> Path:
    """项目文件路径：``<业务根>/projects/<name>.gsproj.json``。

    永远落在调用方给的业务根下，不碰程序目录——程序目录可能只读、
    可能在 Program Files、也可能是 PyInstaller 解包的临时目录。
    """
    safe = (name or "").strip()
    if not safe:
        raise ProjectFileError("corrupt", "项目名不能为空")
    for bad in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        if bad in safe:
            raise ProjectFileError(
                "corrupt", f"项目名不能含路径分隔符或通配符：{safe!r}（含 {bad!r}）")
    return Path(root) / PROJECTS_DIR / f"{safe}{SUFFIX}"


def to_document(project: Project) -> Dict[str, Any]:
    """Project → 分节文档。

    没有直接 ``model_dump_json()`` 直出整个 Project，是因为要把
    「原始输入」与「派生结果」拆成两节：整体 dump 会让它们平铺在同一个对象里，
    下一个人改结构时很容易把输入一起重写掉。逐节 dump 用的是同一个序列化机制。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "id": project.id,
            "name": project.name,
            "created_at": project.created_at.isoformat(),
        },
        "inputs": [t.model_dump(mode="json") for t in project.inputs],
        "derived": {
            "artifacts": dict(project.artifacts),
            "results": [r.model_dump(mode="json") for r in project.results],
            "versions": [v.model_dump(mode="json") for v in project.versions],
        },
    }


def dumps(project: Project) -> str:
    return json.dumps(to_document(project), ensure_ascii=False, indent=2)


def _version_error(raw: Any, version: int, path: Optional[Path]) -> ProjectFileError:
    options = (
        "没有自动升级，也不做静默降级。目前真能做的三条：\n"
        "  ① 备份这个文件，用能读它的那版光枢打开后另存一份，再回来打开；\n"
        "  ② 文件先放着不动，用原始图纸和参数重跑一遍（推荐：重跑不依赖旧文件）；\n"
        "  ③ 把版本号和需求报上来等应用升级——注意这是升级应用，不是升级文件，"
        "本项目目前没有迁移工具。"
    )
    if raw < version:
        code = "too_old"
        message = (f"项目文件是旧格式（schema_version={raw}，当前应用要 {version}）："
                   f"{path}")
    else:
        code = "too_new"
        message = (f"项目文件版本高于当前应用（schema_version={raw}，当前应用是 {version}）："
                   f"{path}")
    return ProjectFileError(code, message, path=path, hint=options)


def from_document(doc: Any, *, path: Optional[Path] = None) -> Project:
    """分节文档 → Project。版本不符一律抛，不猜。"""
    if not isinstance(doc, dict):
        raise ProjectFileError(
            "corrupt", f"项目文件顶层不是对象（读到 {type(doc).__name__}）：{path}",
            path=path, hint="文件可能被别的程序改写过，或根本不是项目文件。")

    raw = doc.get("schema_version")
    if not isinstance(raw, int):
        raise ProjectFileError(
            "corrupt",
            f"项目文件缺 schema_version，或它不是整数（读到 {raw!r}）：{path}",
            path=path, hint="缺版本号就无法判断该不该读，按坏文件处理。")
    if raw != SCHEMA_VERSION:
        raise _version_error(raw, SCHEMA_VERSION, path)

    missing = [k for k in ("project", "inputs", "derived") if k not in doc]
    if missing:
        raise ProjectFileError(
            "corrupt", f"项目文件缺节：{missing}（路径 {path}）",
            path=path, hint="结构不完整的文件不做部分恢复，避免半份数据被当真。")

    head = doc["project"]
    if not isinstance(head, dict) or not head.get("id"):
        raise ProjectFileError("corrupt", f"project 节缺 id：{path}", path=path,
                               hint="项目 id 是身份，缺了就没法归属。")

    try:
        inputs: List[TaskSpec] = [TaskSpec.model_validate(t) for t in doc["inputs"]]
    except Exception as exc:  # noqa: BLE001 - 统一翻成项目文件错误
        raise ProjectFileError(
            "corrupt", f"inputs 节校验失败：{type(exc).__name__}: {exc}",
            path=path, hint="原始输入坏了就不能重算，先备份再查。") from None

    derived = doc["derived"]
    if not isinstance(derived, dict):
        raise ProjectFileError("corrupt", f"derived 节不是对象：{path}", path=path)

    try:
        results = [ResultSet.model_validate(r) for r in derived.get("results") or []]
        versions = [ProjectVersion.model_validate(v) for v in derived.get("versions") or []]
    except Exception as exc:  # noqa: BLE001
        raise ProjectFileError(
            "corrupt", f"derived 节校验失败：{type(exc).__name__}: {exc}",
            path=path) from None

    created = head.get("created_at")
    return Project(
        id=str(head["id"]),
        name=str(head.get("name") or ""),
        created_at=datetime.fromisoformat(created) if created else datetime.now(timezone.utc),
        inputs=inputs,
        artifacts=dict(derived.get("artifacts") or {}),
        results=results,
        versions=versions,
    )


def loads(text: str, *, path: Optional[Path] = None) -> Project:
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProjectFileError(
            "corrupt", f"项目文件不是合法 JSON：{type(exc).__name__}: {exc}",
            path=path, hint="先备份再查；不要拿坏文件去覆盖好文件。") from None
    return from_document(doc, path=path)


def save(project: Project, path: str | Path) -> Path:
    """原子写：``.tmp`` → ``os.replace``。

    直接写目标文件的风险不在写失败，而在**写到一半时进程被杀**：目标文件
    变成半截 JSON，下一次打开直接失败，而原本那份好文件已经没了。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    text = dumps(project)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target


def load(path: str | Path) -> Project:
    p = Path(path)
    if not p.is_file():
        raise ProjectFileError(
            "missing", f"项目文件不存在：{p}", path=p,
            hint=f"项目文件应放在 <业务根>/{PROJECTS_DIR}/ 下，后缀 {SUFFIX}。")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectFileError(
            "corrupt", f"项目文件读不出来：{type(exc).__name__}: {exc}", path=p) from None
    return loads(text, path=p)


__all__ = [
    "PROJECTS_DIR",
    "ProjectFileError",
    "SCHEMA_VERSION",
    "SUFFIX",
    "dumps",
    "from_document",
    "load",
    "loads",
    "project_path",
    "save",
    "to_document",
]
