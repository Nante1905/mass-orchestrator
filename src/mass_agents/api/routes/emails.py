"""Les routes de rédaction assistée de l'écran de communication.

Derrière `CurrentCaller` comme les autres : la rédaction coûte des jetons, et
elle lit l'agenda de l'association avec le jeton de l'appelant. Aucune n'envoie
rien — l'envoi reste au backend, après relecture.
"""

from __future__ import annotations

from fastapi import APIRouter

from mass_agents.api.dependencies import CurrentCaller, MailingDep
from mass_agents.api.responses import ApiResponse
from mass_agents.mailing import AUDIENCE_LABELS, TONE_LABELS, EmailDraftRequest

router = APIRouter(prefix="/emails", tags=["emails"])


@router.get("/options")
async def options(_: CurrentCaller) -> ApiResponse:
    """Ce que le formulaire propose, pour qu'il n'ait pas sa propre liste."""
    return ApiResponse.success(
        {
            "tones": [
                {"id": key, "label": label} for key, label in TONE_LABELS.items()
            ],
            "audiences": [
                {"id": key, "label": label} for key, label in AUDIENCE_LABELS.items()
            ],
        }
    )


@router.post("/drafts")
async def drafts(
    body: EmailDraftRequest, caller: CurrentCaller, mailing: MailingDep
) -> ApiResponse:
    """Rédige un courriel, ou reprend celui qu'on lui passe.

    Compter 5 à 30 secondes : un appel au modèle, deux s'il faut corriger.
    """
    result = await mailing.draft(body, caller.token)
    return ApiResponse.success(result.model_dump(mode="json"))
