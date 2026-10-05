"""Les routes de la page de community management.

Derrière `CurrentCaller` comme les autres : la génération coûte des jetons, et
elle lit l'agenda de l'association avec le jeton de l'appelant.
"""

from __future__ import annotations

from fastapi import APIRouter

from mass_agents.api.dependencies import CommunityDep, CurrentCaller
from mass_agents.api.responses import ApiResponse
from mass_agents.community import CATEGORY_LABELS, MAX_POSTS, SuggestionRequest

router = APIRouter(prefix="/community", tags=["community"])


@router.get("/categories")
async def categories(_: CurrentCaller) -> ApiResponse:
    """Ce que le formulaire propose, pour qu'il n'ait pas sa propre liste."""
    return ApiResponse.success(
        {
            "maxPosts": MAX_POSTS,
            "categories": [
                {"id": key, "label": label} for key, label in CATEGORY_LABELS.items()
            ],
        }
    )


@router.post("/suggestions")
async def suggestions(
    body: SuggestionRequest, caller: CurrentCaller, community: CommunityDep
) -> ApiResponse:
    """Génère `count` posts, dans les catégories demandées ou en mélange.

    Compter 10 à 60 secondes : un seul appel au modèle, mais il rédige tout.
    """
    result = await community.suggest(body, caller.token)
    return ApiResponse.success(result.model_dump(mode="json"))
