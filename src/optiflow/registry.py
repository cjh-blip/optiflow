"""适配器注册表：能力查询的唯一入口。"""

from __future__ import annotations

from .adapter import Adapter, CapabilityDecl


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: list[Adapter] = []

    def register(self, adapter: Adapter) -> None:
        self._adapters.append(adapter)

    def all(self) -> list[Adapter]:
        return list(self._adapters)

    def capabilities(self) -> list[CapabilityDecl]:
        return [a.capabilities() for a in self._adapters]

    def find(self, kind: str) -> Adapter | None:
        """按任务类型找适配器；先声明者先得。"""
        for adapter in self._adapters:
            if kind in adapter.capabilities().kinds:
                return adapter
        return None
