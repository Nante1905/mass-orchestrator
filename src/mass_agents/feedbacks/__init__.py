"""L'analyse des avis laissés après un évènement.

Indépendante du graphe conversationnel, comme les suggestions de posts et la
rédaction des courriels : la page des avis demande une analyse, et reçoit une
synthèse. Seuls le modèle (`build_model`) et l'accès à `mass-mcp` sont
partagés avec l'agent.
"""

from mass_agents.feedbacks.schemas import (
    SENTIMENT_LABELS,
    FeedbackAnalysisResult,
)
from mass_agents.feedbacks.service import FeedbackAnalysisService

__all__ = ["SENTIMENT_LABELS", "FeedbackAnalysisResult", "FeedbackAnalysisService"]
