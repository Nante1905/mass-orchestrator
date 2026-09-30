"""Reconnaître une écriture qui attend une relecture humaine.

L'approche : lire la transcription plutôt qu'instrumenter les outils. Après le
passage de l'agent des opérations, on cherche dans ce qu'il vient de produire un
appel à un outil engageant dont le résultat porte `confirmationRequired`.

Pourquoi de cette manière plutôt qu'en interceptant l'appel d'outil. D'abord
parce que la marque est posée par `mass-mcp` lui-même : c'est le serveur qui
décide qu'une écriture exige une confirmation, et le lire revient à faire
confiance à la seule autorité en la matière. Ensuite parce que le résultat est
une fonction pure d'une liste de messages : elle se teste sans modèle, sans
réseau et sans serveur MCP, ce qui est exactement ce qu'on veut du code qui
garde la porte.

L'agent agit, ce module lit, le nœud de validation décide. Les trois
responsabilités restent séparables.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from mass_agents.domain import ENGAGING_TOOLS, PendingAction

logger = logging.getLogger(__name__)

#: La marque posée par `mass-mcp` sur un aperçu. Voir `sendEmail.ts` et
#: `markAttendance.ts` : sans `confirmed`, l'outil n'écrit rien et rend cet
#: objet.
_CONFIRMATION_FLAG = "confirmationRequired"


def detect_pending_action(messages: Sequence[AnyMessage]) -> PendingAction | None:
    """La dernière écriture engageante restée en attente, s'il y en a une.

    La *dernière* et non la première : si l'agent a préparé deux gestes malgré
    la consigne, c'est le plus récent qui correspond à l'intention courante.
    L'autre ne sera pas exécuté — et c'est le comportement voulu, un aperçu
    empilé sous un autre n'aurait été lu par personne.
    """
    previews = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        for call in reversed(message.tool_calls or []):
            if call["name"] not in ENGAGING_TOOLS:
                continue

            result = previews.get(call["id"])
            if result is None:
                continue

            preview = parse_tool_payload(result.content)
            if preview is None or preview.get(_CONFIRMATION_FLAG) is not True:
                continue

            return PendingAction(
                tool_name=call["name"],
                arguments=dict(call["args"]),
                preview=preview,
                tool_call_id=str(call["id"]),
            )

    return None


def parse_tool_payload(raw: Any) -> dict[str, Any] | None:
    """Le contenu d'un résultat d'outil, lu comme l'objet JSON qu'il porte.

    `mass-mcp` rend un JSON indenté dans un bloc de texte plutôt que d'utiliser
    le `structuredContent` du protocole — un choix assumé côté serveur, tous les
    clients ne rendant pas ce dernier au modèle. On défait donc ici ce que
    `jsonResult` a fait là-bas.

    Publique parce que le nœud de validation en a besoin lui aussi, pour relire
    le résultat de l'appel confirmé. Deux lectures séparées finiraient par
    diverger sur le traitement des blocs de contenu.

    Un contenu illisible n'est pas une erreur : c'est simplement un résultat qui
    n'est pas un aperçu, et il y en a à chaque lecture.
    """
    if isinstance(raw, dict):
        return raw

    if isinstance(raw, list):
        raw = "".join(
            block.get("text", "")
            for block in raw
            if isinstance(block, dict) and block.get("type") == "text"
        )

    if not isinstance(raw, str) or not raw.strip():
        return None

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    return parsed if isinstance(parsed, dict) else None
