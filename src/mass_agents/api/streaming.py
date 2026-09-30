"""Le relais SSE : des évènements du service vers le navigateur.

Le nom de l'évènement SSE est le `kind` de l'évènement, et sa donnée est
l'évènement sérialisé. Un client peut donc s'abonner finement
(`addEventListener("approval_request", …)`) sans avoir à inspecter chaque charge
utile — ce qui compte pour la validation humaine, le seul évènement qui demande
une réaction de l'interface et non un simple affichage.

Le flux se termine toujours par un `done`, y compris après une erreur. Un client
qui attend cet évènement pour ranger son indicateur de chargement ne reste donc
jamais bloqué.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict

from sse_starlette.sse import EventSourceResponse

from mass_agents.graph import DoneEvent, ErrorEvent, GraphEvent

logger = logging.getLogger(__name__)


def sse_response(
    events: AsyncIterator[GraphEvent], thread_id: str
) -> EventSourceResponse:
    return EventSourceResponse(_encode(events, thread_id))


async def _encode(
    events: AsyncIterator[GraphEvent], thread_id: str
) -> AsyncIterator[dict]:
    try:
        async for event in events:
            yield _frame(event)
    except Exception:
        # Le flux a commencé : impossible de rendre un code HTTP d'erreur. On
        # termine proprement plutôt que de couper la connexion, sans quoi le
        # navigateur tenterait une reconnexion automatique sur une requête qui
        # relancerait le même run.
        logger.exception("Échec pendant le relais SSE : thread=%s", thread_id)
        yield _frame(
            ErrorEvent(message="Le flux a été interrompu par une erreur du service")
        )
        yield _frame(DoneEvent(status="failed", thread_id=thread_id))


def _frame(event: GraphEvent) -> dict:
    return {
        "event": event.kind,
        # `ensure_ascii=False` : les accents partent tels quels, la réponse est
        # déclarée en UTF-8 et les échapper alourdirait chaque fragment de texte.
        "data": json.dumps(asdict(event), ensure_ascii=False),
    }
