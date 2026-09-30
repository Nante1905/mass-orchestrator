"""Le chemin dans le graphe, décidé par des fonctions pures.

Tout le routage tient ici, en lisant l'état et rien d'autre : ni modèle, ni
réseau, ni effet de bord. C'est ce qui permet de vérifier chaque aiguillage sur
une liste de messages construite à la main.

La règle d'ensemble :

- l'agent n'a rien demandé → le run se termine ;
- l'agent a demandé exactement un geste engageant, seul → l'aperçu ;
- tout autre cas → l'exécution des outils, qui répond aux lectures et aux
  brouillons, et refuse les gestes engageants surnuméraires. S'il reste alors
  un geste engageant sans réponse, il passe à l'aperçu.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from langchain_core.messages import AIMessage, AnyMessage, ToolCall, ToolMessage
from langgraph.graph import END

from mass_agents.domain import ENGAGING_TOOLS
from mass_agents.graph.state import OrchestratorState

AGENT = "agent"
TOOLS = "outils"
PREVIEW = "apercu"
VALIDATION = "validation"


def last_tool_calls(messages: Sequence[AnyMessage]) -> list[ToolCall]:
    """Les appels d'outil du dernier message de l'agent."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return list(message.tool_calls or [])
    return []


def unanswered_calls(messages: Sequence[AnyMessage]) -> list[ToolCall]:
    """Les appels du dernier message de l'agent qui n'ont pas encore de
    résultat. Chacun doit en recevoir un avant le prochain appel de modèle :
    un `tool_use` sans `tool_result` fait rejeter la requête par l'API."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [call for call in last_tool_calls(messages) if call["id"] not in answered]


def pending_engaging_call(messages: Sequence[AnyMessage]) -> ToolCall | None:
    """Le geste engageant qui attend son aperçu, s'il y en a un."""
    return next(
        (c for c in unanswered_calls(messages) if c["name"] in ENGAGING_TOOLS),
        None,
    )


def after_agent(state: OrchestratorState) -> Literal["outils", "apercu", "__end__"]:
    calls = last_tool_calls(state["messages"])
    if not calls:
        return END
    if len(calls) == 1 and calls[0]["name"] in ENGAGING_TOOLS:
        return PREVIEW
    return TOOLS


def after_tools(state: OrchestratorState) -> Literal["apercu", "agent"]:
    return PREVIEW if pending_engaging_call(state["messages"]) else AGENT


def after_preview(state: OrchestratorState) -> Literal["validation", "agent"]:
    """Un aperçu calculé attend sa validation ; un aperçu en échec a déjà été
    rendu à l'agent sous forme de résultat d'outil."""
    return VALIDATION if state.get("pending_action") else AGENT
