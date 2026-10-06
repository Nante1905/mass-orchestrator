"""La route d'analyse des avis, appelée par `mass-backend`.

Le back-office ne l'appelle pas lui-même : le backend la sollicite avec le
jeton de l'administrateur, enregistre le résultat et le lui rend. Derrière
`CurrentCaller` comme les autres : l'analyse coûte des jetons, et elle lit des
avis nominatifs avec le jeton de l'appelant. Elle n'écrit rien — l'écriture est
l'affaire du backend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from mass_agents.api.dependencies import CurrentCaller, FeedbacksDep
from mass_agents.api.responses import ApiResponse
from mass_agents.feedbacks import SENTIMENT_LABELS

router = APIRouter(prefix="/events", tags=["feedbacks"])

#: `events.id` est un varchar(50) ; au-delà, rien ne peut correspondre.
EventId = Annotated[str, Path(min_length=1, max_length=50)]


@router.post("/{event_id}/feedback-analysis")
async def analyse(
    event_id: EventId, caller: CurrentCaller, feedbacks: FeedbacksDep
) -> ApiResponse:
    """Analyse les avis d'un évènement : synthèse, tons, thèmes, pistes.

    Compter 10 à 60 secondes : un appel au modèle, deux s'il faut corriger.
    """
    result = await feedbacks.analyse(event_id, caller.token)
    return ApiResponse.success(
        {
            **result.model_dump(mode="json", by_alias=True),
            "sentimentLabels": SENTIMENT_LABELS,
        }
    )
