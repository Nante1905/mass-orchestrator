"""Le superviseur : il route, puis il conclut.

`create_supervisor()` n'existe qu'en Python côté LangGraph et ferait l'affaire.
On ne l'utilise pas, et pour une raison qui vaut d'être écrite : ce nœud porte
les deux plafonds durs du service. Les loger dans une abstraction qui les ignore
obligerait à les rattraper ailleurs — dans un `pre_model_hook`, ou pire, après
coup. À ce niveau, une trentaine de lignes se relisent mieux qu'une dépendance
qu'il faudrait contourner.

C'est le seul nœud traversé à chaque tour, donc le seul endroit où un arrêt
survient **avant** le prochain appel de modèle et non après.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

from mass_agents.agents import (
    HANDOFF_TOOLS,
    SPECIALISTS_BY_NAME,
    build_model,
    destination_of,
)
from mass_agents.agents.prompts import SUPERVISOR_PROMPT
from mass_agents.config import get_config
from mass_agents.graph.limits import (
    budget_exhausted,
    budget_exhausted_message,
    turns_exhausted,
    turns_exhausted_message,
)
from mass_agents.graph.state import OrchestratorState

logger = logging.getLogger(__name__)

SUPERVISOR = "superviseur"

Destination = Literal["analyste", "redacteur", "operations", "__end__"]

#: Le tour de parole qui rend la main au superviseur.
#:
#: Nécessaire, et pas seulement utile : quand un spécialiste vient de terminer,
#: la transcription se clôt sur un message d'assistant. L'API d'Anthropic lit
#: alors la requête comme une amorce de réponse à compléter — « assistant
#: prefill » — et la refuse pour ce modèle. Le graphe échouait au moment précis
#: où il allait conclure, c'est-à-dire après avoir déjà tout payé.
#:
#: Ce message n'est **pas** ajouté à l'état : il n'existe que le temps de l'appel
#: de routage. L'écrire dans le fil ferait apparaître une fausse intervention de
#: l'utilisateur dans l'historique du back-office.
_DECISION_TURN = (
    "Le travail des agents est ci-dessus. Deux options, et deux seulement : "
    "transfère à un agent si quelque chose reste à faire, ou rédige la réponse "
    "finale à l'utilisateur si la demande est satisfaite."
)


async def supervisor_node(
    state: OrchestratorState, config: RunnableConfig
) -> Command[Destination]:
    limits = get_config().limits
    messages = state["messages"]
    turns = state.get("turns", 0)

    # Les plafonds sont contrôlés avant l'appel de modèle, pas après : un
    # dépassement constaté a posteriori a déjà été facturé.
    if turns_exhausted(turns, limits):
        logger.warning("Plafond de tours atteint (%d)", turns)
        return _stop(turns_exhausted_message(limits))

    if budget_exhausted(messages, limits):
        logger.warning("Plafond de jetons atteint")
        return _stop(budget_exhausted_message())

    router = build_model("low").bind_tools(HANDOFF_TOOLS)
    response = await router.ainvoke(_router_prompt(messages), config)

    calls = response.tool_calls or []
    if not calls:
        # Pas de transfert : c'est la réponse finale, ou la question qui manquait
        # pour pouvoir router. Dans les deux cas le run s'arrête et rend la main.
        return Command(goto=END, update={"messages": [response], "next": None}) # type: ignore

    # Chaque appel d'outil doit recevoir son résultat, y compris ceux qu'on
    # n'honore pas : un `tool_use` laissé sans `tool_result` rend la
    # transcription invalide pour l'API et fait échouer le tour suivant.
    chosen, *ignored = calls
    destination = destination_of(chosen["name"])

    acknowledgements = [
        ToolMessage(
            content=f"Transfert accepté vers {destination}.",
            tool_call_id=chosen["id"],
            name=chosen["name"],
        ),
        *(
            ToolMessage(
                content=(
                    "Transfert ignoré : un seul agent travaille à la fois. "
                    "Reformule au tour suivant si c'est encore nécessaire."
                ),
                tool_call_id=call["id"],
                name=call["name"],
            )
            for call in ignored
        ),
    ]

    if destination not in SPECIALISTS_BY_NAME:
        # Le modèle n'a que les trois outils de transfert : y arriver signalerait
        # un décalage entre `HANDOFF_TOOLS` et les spécialistes déclarés.
        logger.error("Destination inconnue rendue par le superviseur : %r", destination)
        return Command(
            goto=END,
            update={
                "messages": [
                    response,
                    *acknowledgements,
                    AIMessage(
                        content=(
                            "Je n'arrive pas à orienter cette demande. "
                            "Pouvez-vous la reformuler ?"
                        )
                    ),
                ],
                "next": None,
            },
        ) # type: ignore

    return Command(
        goto=destination,
        update={
            "messages": [response, *acknowledgements],
            "next": destination,
            "turns": turns + 1,
        },
    ) # type: ignore


def _router_prompt(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Le fil, tel qu'on le soumet au routage.

    Le tour de décision n'est ajouté que si la conversation ne se termine pas
    déjà par l'utilisateur — au premier message, il ferait double emploi et
    dirait « le travail des agents est ci-dessus » alors qu'aucun n'a travaillé.
    """
    prompt: list[AnyMessage] = [SystemMessage(content=SUPERVISOR_PROMPT), *messages]

    if messages and not isinstance(messages[-1], HumanMessage):
        prompt.append(HumanMessage(content=_DECISION_TURN))

    return prompt


def _stop(message: str) -> Command[Destination]:
    """Fin de run sur plafond : un message lisible, et l'ardoise remise à plat.

    `pending_action` est effacée parce qu'un run arrêté ne doit pas laisser
    derrière lui une écriture en attente que personne ne validera — elle
    ressurgirait au prochain message de l'utilisateur, sans son contexte.
    """
    return Command(
        goto=END,
        update={
            "messages": [AIMessage(content=message)],
            "next": None,
            "pending_action": None,
        },
    ) # type: ignore
