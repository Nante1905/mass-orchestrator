"""La rédaction assistée des courriels de l'écran de communication.

Indépendant du graphe conversationnel, comme les suggestions de posts : l'écran
envoie une consigne, et reçoit un brouillon à relire. Seuls le modèle
(`build_model`) et l'accès à `mass-mcp` sont partagés avec l'agent.
"""

from mass_agents.mailing.schemas import (
    AUDIENCE_LABELS,
    TONE_LABELS,
    EmailDraftRequest,
    EmailDraftResult,
)
from mass_agents.mailing.service import EmailDraftService

__all__ = [
    "AUDIENCE_LABELS",
    "TONE_LABELS",
    "EmailDraftRequest",
    "EmailDraftResult",
    "EmailDraftService",
]
