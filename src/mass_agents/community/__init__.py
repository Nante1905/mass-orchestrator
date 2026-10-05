"""Les suggestions de posts pour les réseaux sociaux de l'association.

Indépendant du graphe conversationnel : la page dédiée envoie un nombre et des
catégories, et reçoit une liste. Seuls le modèle (`build_model`) et l'accès à
`mass-mcp` sont partagés avec l'agent.
"""

from mass_agents.community.schemas import (
    CATEGORY_LABELS,
    MAX_POSTS,
    SuggestionRequest,
    SuggestionsResult,
)
from mass_agents.community.service import CommunityService
from mass_agents.community.sources import FeedReader

__all__ = [
    "CATEGORY_LABELS",
    "MAX_POSTS",
    "CommunityService",
    "FeedReader",
    "SuggestionRequest",
    "SuggestionsResult",
]
