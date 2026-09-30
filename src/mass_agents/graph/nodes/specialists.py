"""Le nœud qui fait tourner un spécialiste, et lit ce qu'il en ressort.

L'agent est construit **à l'appel** et non à la compilation du graphe : il se
ferme sur l'outillage du run, donc sur le jeton de l'appelant. Compiler le
graphe une fois et reconstruire les agents à chaque passage est ce qui permet de
partager le graphe entre tous les administrateurs sans partager leurs droits.
La construction ne coûte aucun appel réseau — l'outillage, lui, a déjà été
récupéré une fois pour le run entier.

Après le passage de l'agent, on relit sa transcription pour savoir s'il a laissé
une écriture en attente. Le contrôle est fait pour tous les spécialistes et pas
seulement pour les opérations : seuls les outils engageants peuvent produire un
aperçu, et seul l'agent des opérations les détient — mais si cette répartition
changeait un jour, la porte resterait gardée.
"""

from __future__ import annotations

import logging
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from mass_agents.agents import Specialist, build_specialist
from mass_agents.graph.context import run_context
from mass_agents.graph.nodes.supervisor import SUPERVISOR
from mass_agents.graph.pending import detect_pending_action
from mass_agents.graph.state import OrchestratorState, new_messages

logger = logging.getLogger(__name__)

VALIDATION = "validation"

SpecialistDestination = Literal["superviseur", "validation"]


def build_specialist_node(specialist: Specialist):
    """Le nœud d'un spécialiste, pour le graphe."""

    async def node(
        state: OrchestratorState, config: RunnableConfig
    ) -> Command[SpecialistDestination]:
        toolset = run_context(config).toolset
        agent = build_specialist(specialist, toolset)

        # Le fil complet, et pas seulement la dernière demande : « les présents
        # de samedi » ne se résout qu'avec ce qui a été dit avant.
        before = list(state["messages"])
        result = await agent.ainvoke({"messages": before}, config)
        produced = new_messages(before, result["messages"])

        pending = detect_pending_action(produced)
        if pending is not None:
            logger.info(
                "Écriture en attente de validation : %s (agent %s)",
                pending.tool_name,
                specialist.name,
            )
            return Command(
                goto=VALIDATION,
                update={
                    "messages": produced,
                    "next": VALIDATION,
                    "pending_action": pending,
                },
            )

        return Command(
            goto=SUPERVISOR,
            update={"messages": produced, "next": SUPERVISOR, "pending_action": None},
        )

    node.__name__ = f"{specialist.name}_node"
    return node
