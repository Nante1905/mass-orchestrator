"""L'assemblage du graphe.

Il est compilé **une fois**, au démarrage, et partagé par tous les
administrateurs. Ce qui rend ce partage sûr est que rien de personnel n'y est
figé : les agents et leurs outils sont reconstruits à chaque passage à partir du
contexte du run. Compiler un graphe par utilisateur reviendrait à payer la
compilation pour rien et à multiplier les checkpointers.

La forme du graphe tient en une phrase : le superviseur route vers un
spécialiste, un spécialiste rend la main au superviseur — sauf s'il a laissé une
écriture en attente, auquel cas il passe par la validation. Aucun spécialiste ne
peut terminer le run de lui-même : c'est le superviseur qui conclut, et c'est ce
qui garantit que la réponse finale a été rédigée en connaissance de tout le fil.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from mass_agents.agents import SPECIALISTS
from mass_agents.graph.nodes import (
    SUPERVISOR,
    VALIDATION,
    build_specialist_node,
    supervisor_node,
    validation_node,
)
from mass_agents.graph.state import OrchestratorState


def build_graph(checkpointer: BaseCheckpointSaver) -> Pregel:
    """Le graphe compilé, prêt à être invoqué avec un contexte de run.

    Les `destinations` sont déclarées explicitement bien que le routage se fasse
    par `Command(goto=…)` : sans elles, le graphe compilé n'a aucune arête à
    dessiner et un `get_graph()` rend une image qui ne ressemble pas à ce que le
    code fait.
    """
    specialist_names = tuple(specialist.name for specialist in SPECIALISTS)

    builder = StateGraph(OrchestratorState)

    builder.add_node(
        SUPERVISOR, supervisor_node, destinations=(*specialist_names, END)
    )

    for specialist in SPECIALISTS:
        builder.add_node(
            specialist.name,
            build_specialist_node(specialist),
            destinations=(SUPERVISOR, VALIDATION),
        )

    builder.add_node(VALIDATION, validation_node, destinations=(SUPERVISOR,))

    builder.add_edge(START, SUPERVISOR)

    return builder.compile(checkpointer=checkpointer)
