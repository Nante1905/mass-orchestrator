"""Le nœud qui exécute les lectures et les brouillons.

Écrit plutôt que repris de `ToolNode`, pour une raison précise : ce nœud doit
**trier** les appels. Les lectures et les brouillons s'exécutent ici, en
parallèle ; un geste engageant ne s'exécute jamais ici — il est laissé sans
réponse pour le nœud d'aperçu, qui seul sait le traiter. `ToolNode` exécuterait
tout ce que le modèle a demandé.

Chaque appel reçoit son résultat, y compris ceux qu'on refuse : un `tool_use`
laissé sans `tool_result` fait rejeter la requête suivante par l'API.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.runnables import RunnableConfig

from mass_agents.domain import ENGAGING_TOOLS
from mass_agents.graph.context import run_context
from mass_agents.graph.routing import unanswered_calls
from mass_agents.graph.state import OrchestratorState
from mass_agents.tools import MassToolset

logger = logging.getLogger(__name__)

_ONE_AT_A_TIME = (
    "Non exécuté : un seul geste engageant à la fois. Attends le résultat du "
    "premier, puis propose celui-ci."
)


async def tools_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    toolset = run_context(config).toolset
    calls = unanswered_calls(state["messages"])

    runnable = [c for c in calls if c["name"] not in ENGAGING_TOOLS]
    engaging = [c for c in calls if c["name"] in ENGAGING_TOOLS]

    results = await asyncio.gather(*(_run(toolset, c, config) for c in runnable))

    # Le premier geste engageant reste sans réponse : le routage l'envoie à
    # l'aperçu. Les suivants sont refusés ici, pour qu'aucun ne s'empile sous un
    # autre et soit approuvé sans avoir été lu.
    refused = [_error(call, _ONE_AT_A_TIME) for call in engaging[1:]]

    return {"messages": [*results, *refused]}


async def _run(
    toolset: MassToolset, call: ToolCall, config: RunnableConfig
) -> ToolMessage:
    """Un appel, et toujours un résultat — l'échec compris.

    Une erreur d'outil est rendue au modèle plutôt que levée : c'est lui qui
    saura la lire (un identifiant inconnu se corrige, un jeton expiré se dit à
    l'utilisateur), et une exception ici interromprait le run entier.
    """
    try:
        tool = toolset.get(call["name"])
        result = await tool.ainvoke({**call, "type": "tool_call"}, config)
    except Exception as error:
        logger.warning("Échec de l'outil %s : %s", call["name"], error)
        return _error(call, f"Échec de l'outil {call['name']} : {error}")

    return result


def _error(call: ToolCall, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=call["id"],
        name=call["name"],
        status="error",
    )
