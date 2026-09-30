"""Le nœud d'aperçu : ce qu'un geste engageant ferait, sans le faire.

Il appelle l'outil de `mass-mcp` avec `confirmed` **forcé à faux**. C'est ici,
et non dans le schéma montré au modèle, que tient la garantie : un modèle peut
produire un argument hors schéma — par erreur, ou sous l'influence d'un texte
injecté dans les données qu'il a lues. La valeur qu'il aurait posée est retirée
des arguments conservés, puis écrasée dans l'appel.

Il est séparé du nœud de validation pour une raison de reprise : un nœud qui
contient `interrupt()` est rejoué depuis le début quand on le reprend. Calculer
l'aperçu dans le même nœud le ferait recalculer à la reprise, et ce qui serait
exécuté pourrait différer de ce qui a été relu. Ici, l'aperçu est checkpointé
avant que le graphe ne se suspende.
"""

from __future__ import annotations

import logging

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from mass_agents.domain import PendingAction
from mass_agents.graph.context import run_context
from mass_agents.graph.routing import pending_engaging_call
from mass_agents.graph.state import OrchestratorState
from mass_agents.tools import CONFIRMATION_PARAM, parse_tool_payload, tool_text

logger = logging.getLogger(__name__)

#: La marque posée par `mass-mcp` sur un aperçu (`sendEmail.ts`,
#: `markAttendance.ts`) : sans `confirmed`, l'outil n'écrit rien et rend cet
#: objet.
_CONFIRMATION_FLAG = "confirmationRequired"


async def preview_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    call = pending_engaging_call(state["messages"])
    if call is None:
        # Le routage n'envoie ici qu'avec un geste en attente : y arriver sans
        # signalerait un décalage entre `routing.py` et ce nœud.
        logger.error("Nœud d'aperçu atteint sans geste engageant en attente")
        return {"pending_action": None}

    arguments = {k: v for k, v in call["args"].items() if k != CONFIRMATION_PARAM}
    if CONFIRMATION_PARAM in call["args"]:
        logger.warning(
            "Le modèle a posé %s sur %s : valeur ignorée",
            CONFIRMATION_PARAM,
            call["name"],
        )

    def answer(content: str) -> dict:
        """Le geste n'ira pas en validation : le dire au modèle, qui corrigera."""
        return {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
            ],
            "pending_action": None,
        }

    try:
        tool = run_context(config).toolset.get(call["name"])
        raw = await tool.ainvoke({**arguments, CONFIRMATION_PARAM: False})
    except Exception as error:
        # Groupe inexistant, groupe vide, identifiant inconnu : `mass-mcp`
        # refuse l'aperçu avec un message qui dit quoi corriger.
        logger.info("Aperçu refusé pour %s : %s", call["name"], error)
        return answer(f"Aperçu impossible : {error}")

    preview = parse_tool_payload(raw)
    if preview is None or preview.get(_CONFIRMATION_FLAG) is not True:
        logger.error("Réponse de %s sans aperçu : %r", call["name"], raw)
        return answer(
            "Le serveur n'a pas rendu d'aperçu pour ce geste ; rien n'a été "
            f"soumis à validation. Réponse reçue : {tool_text(raw)}"
        )

    return {
        "pending_action": PendingAction(
            tool_name=call["name"],
            arguments=arguments,
            preview=preview,
            tool_call_id=str(call["id"]),
        )
    }
