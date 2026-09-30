"""L'assemblage du graphe.

Il est compilé **une fois**, au démarrage, et partagé par tous les
administrateurs. Ce qui rend ce partage sûr est que rien de personnel n'y est
figé : les outils sont liés au modèle à chaque passage, à partir du contexte du
run.

La forme tient en une boucle et un détour :

    START ─▶ agent ─┬─▶ END                         (réponse finale)
               ▲    ├─▶ outils ─┬─▶ agent          (lectures, brouillons)
               │    │           └─▶ apercu
               │    └─▶ apercu ─┬─▶ validation ─▶ agent   (geste engageant)
               └────────────────┴─▶ agent               (aperçu refusé)

Toutes les décisions d'aiguillage sont des fonctions pures, dans `routing.py`.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from mass_agents.graph.nodes import (
    agent_node,
    preview_node,
    tools_node,
    validation_node,
)
from mass_agents.graph.routing import (
    AGENT,
    PREVIEW,
    TOOLS,
    VALIDATION,
    after_agent,
    after_preview,
    after_tools,
)
from mass_agents.graph.state import OrchestratorState


def build_graph(checkpointer: BaseCheckpointSaver) -> Pregel:
    """Le graphe compilé, prêt à être invoqué avec un contexte de run."""
    builder = StateGraph(OrchestratorState)

    builder.add_node(AGENT, agent_node)
    builder.add_node(TOOLS, tools_node)
    builder.add_node(PREVIEW, preview_node)
    builder.add_node(VALIDATION, validation_node)

    builder.add_edge(START, AGENT)
    builder.add_conditional_edges(AGENT, after_agent, [TOOLS, PREVIEW, END])
    builder.add_conditional_edges(TOOLS, after_tools, [PREVIEW, AGENT])
    builder.add_conditional_edges(PREVIEW, after_preview, [VALIDATION, AGENT])
    builder.add_edge(VALIDATION, AGENT)

    return builder.compile(checkpointer=checkpointer)
