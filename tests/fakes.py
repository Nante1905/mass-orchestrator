"""Les doublures, volontairement minces.

Ce qui doit être testé est notre logique, pas la capacité d'un faux à imiter un
serveur MCP. Chaque doublure se réduit à ce qu'un test doit pouvoir observer :
comment l'outil a été appelé, et ce qui a été tracé.
"""

from __future__ import annotations

import json
from typing import Any


class FakeTool:
    """Un outil qui note comment on l'a appelé."""

    def __init__(self, name: str, result: dict[str, Any]) -> None:
        self.name = name
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> str:
        self.calls.append(dict(args))
        return json.dumps(self._result)


class FakeToolset:
    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def get(self, name: str) -> Any:
        return self._tools[name]

    def subset(self, names) -> list[Any]:
        return [self._tools[name] for name in names]


class FakeApprovalLog:
    """Le journal, réduit à ce qu'un test doit pouvoir relire."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)
