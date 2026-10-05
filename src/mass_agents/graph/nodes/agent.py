"""Le nœud de l'agent : vérifier les plafonds, puis appeler le modèle.

C'est le seul nœud qui appelle le modèle, donc le seul endroit où un arrêt doit
survenir **avant** l'appel et non après. Il est traversé à chaque tour de la
boucle, y compris après une validation humaine.

Les outils sont liés à chaque passage, à partir de l'outillage du run : le
modèle est partagé entre les administrateurs, leurs droits ne le sont pas. Ce
que le modèle voit des outils engageants est la définition sans `confirmed`
(voir `MassToolset.agent_schemas`).
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from mass_agents.agents import AGENT_PROMPT, build_model
from mass_agents.config import get_config
from mass_agents.graph.context import run_context
from mass_agents.graph.limits import (
    budget_exhausted,
    budget_exhausted_message,
    steps_exhausted,
    steps_exhausted_message,
)
from mass_agents.graph.state import OrchestratorState

logger = logging.getLogger(__name__)


async def agent_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    limits = get_config().limits
    messages = state["messages"]

    if steps_exhausted(messages, limits):
        logger.warning("Plafond d'étapes atteint (%d)", limits.max_steps)
        return {"messages": [AIMessage(content=steps_exhausted_message(limits))]}

    if budget_exhausted(messages, limits):
        logger.warning("Plafond de jetons atteint")
        return {"messages": [AIMessage(content=budget_exhausted_message())]}

    toolset = run_context(config).toolset
    model = build_model().bind_tools(toolset.agent_schemas(), strict=False)

    response = await model.ainvoke(
        [SystemMessage(content=AGENT_PROMPT), *messages], config
    )
    return {"messages": [response]}
