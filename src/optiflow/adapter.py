"""适配器统一契约 —— 骨架的核心。

能力先声明、后实现。做不了的事必须写进 CapabilityDecl.limits，
DIALux 这类没有正式接口的软件尤其要诚实（否则平台会给出虚假承诺）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .ir import TaskSpec
from .job import Progress, ResultSet


class CapabilityDecl(BaseModel):
    """能力声明：这个适配器【能做什么】、【不能做什么】、【是哪一类】。

    kinds 回答「处理哪类任务」，tags 回答「用哪条通道做」——两者正交：
    LumenPlannerAdapter 与 DialuxAdapter 都处理 kind="layout"，但一个走
    algorithm 通道、一个走 file 通道。编排层靠 tags 消歧，不靠猜注册顺序。
    """

    name: str
    kinds: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    fragile: bool = False


@runtime_checkable
class Adapter(Protocol):
    def capabilities(self) -> CapabilityDecl: ...

    def submit(self, task: TaskSpec) -> str: ...

    def status(self, job_id: str) -> Progress: ...

    def result(self, job_id: str) -> ResultSet: ...

    def cancel(self, job_id: str) -> None: ...
