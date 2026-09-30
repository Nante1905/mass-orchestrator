"""Les doublures, volontairement minces.

Ce qui doit être testé est notre logique, pas la capacité d'un faux à imiter un
serveur MCP. Chaque doublure se réduit à ce qu'un test doit pouvoir observer :
comment l'outil a été appelé, et ce qui a été tracé.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from mass_agents.auth import AdminIdentity
from mass_agents.domain import ToolsetError
from mass_agents.graph.context import RunContext, build_run_config

# -- Charges utiles de `mass-mcp` ---------------------------------------------
# Reprises de `sendEmail.ts` : ce que l'outil rend sans, puis avec, `confirmed`.

EMAIL_ARGUMENTS: dict[str, Any] = {
    "group_id": "G0003",
    "subject": "Sortie de samedi",
    "body": "<p>Rendez-vous à 20h.</p>",
}

EMAIL_PREVIEW: dict[str, Any] = {
    "sent": False,
    "confirmationRequired": True,
    "mode": "group",
    "groupId": "G0003",
    "groupTitle": "Commission observation",
    "recipientCount": 12,
    "recipients": ["a@example.org", "b@example.org"],
    "subject": "Sortie de samedi",
    "body": "<p>Rendez-vous à 20h.</p>",
    "bodyIsHtml": True,
}

EMAIL_SENT: dict[str, Any] = {
    "sent": True,
    "groupId": "G0003",
    "sentTo": ["a@example.org", "b@example.org"],
    "failed": [],
    "message": "12 envoi(s) remis et tracés dans l'historique.",
}


def _as_mcp_text(payload: dict[str, Any]) -> str:
    """Comme `jsonResult` côté `mass-mcp` : du JSON en UTF-8, accents compris."""
    return json.dumps(payload, ensure_ascii=False)


class FakeTool:
    """Un outil qui note comment on l'a appelé.

    Il accepte les deux formes d'appel d'un `BaseTool` : un appel d'outil
    complet — ce que fait le nœud d'exécution, et il rend alors un
    `ToolMessage` — ou de simples arguments, ce que font l'aperçu et la
    validation.
    """

    def __init__(self, name: str, result: dict[str, Any]) -> None:
        self.name = name
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any], config: Any = None) -> Any:
        if args.get("type") == "tool_call":
            self.calls.append(dict(args["args"]))
            return ToolMessage(
                content=_as_mcp_text(self._respond(args["args"])),
                tool_call_id=args["id"],
                name=self.name,
            )

        self.calls.append(dict(args))
        return _as_mcp_text(self._respond(args))

    def _respond(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._result


class FakeEngagingTool(FakeTool):
    """Un outil engageant : l'aperçu sans `confirmed`, l'écriture avec."""

    def __init__(
        self, name: str, preview: dict[str, Any], outcome: dict[str, Any]
    ) -> None:
        super().__init__(name, outcome)
        self._preview = preview

    def _respond(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._result if args.get("confirmed") is True else self._preview


class BrokenTool(FakeTool):
    """Un outil dont chaque appel échoue."""

    def __init__(self, name: str, error: str) -> None:
        super().__init__(name, {})
        self._error = error

    async def ainvoke(self, args: dict[str, Any], config: Any = None) -> Any:
        self.calls.append(dict(args.get("args", args)))
        raise RuntimeError(self._error)


class FakeToolset:
    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def get(self, name: str) -> Any:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolsetError(f"L'outil {name} n'est pas exposé") from error

    def agent_schemas(self) -> list[Any]:
        return list(self._tools.values())


class FakeApprovalLog:
    """Le journal, réduit à ce qu'un test doit pouvoir relire."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class ScriptedModel:
    """Un modèle qui rend, dans l'ordre, les réponses qu'on lui a données.

    Il note ce qu'on lui a lié et ce qu'il a reçu : c'est ce qu'un test veut
    vérifier d'un appel de modèle, pas la qualité de la réponse.
    """

    def __init__(self, *responses: AIMessage) -> None:
        self._responses = list(responses)
        self.bound: list[Any] = []
        self.received: list[list[Any]] = []

    def bind_tools(self, tools: Any, **_: Any) -> ScriptedModel:
        self.bound = list(tools)
        return self

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        self.received.append(list(messages))
        return self._responses.pop(0)


ADMIN = AdminIdentity(
    user_id="USR0001", email="admin@example.org", administrator_id="ADM1"
)


def run_config(
    tools: dict[str, Any],
    approvals: FakeApprovalLog | None = None,
    thread_id: str = "fil-1",
) -> RunnableConfig:
    """La configuration d'un run, avec ces outils et ce journal."""
    return build_run_config(
        thread_id,
        RunContext(
            admin=ADMIN,
            toolset=FakeToolset(tools),  # type: ignore[arg-type]
            approvals=approvals or FakeApprovalLog(),  # type: ignore[arg-type]
        ),
    )


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def calling(*calls: dict[str, Any], text: str = "") -> AIMessage:
    """Une réponse de l'agent qui appelle ces outils."""
    return AIMessage(content=text, tool_calls=list(calls))
