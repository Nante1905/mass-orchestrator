"""Le vocabulaire du métier : ce qui s'échange, et ce qui échoue.

Ce paquet ne dépend d'aucune infrastructure — ni base, ni HTTP, ni LangGraph.
C'est ce qui permet de tester la détection d'une écriture en attente ou la forme
d'une décision sans monter quoi que ce soit.
"""

from mass_agents.domain.actions import (
    CONSEQUENCES,
    ENGAGING_TOOLS,
    ApprovalDecision,
    ApprovalRequest,
    PendingAction,
)
from mass_agents.domain.errors import (
    AdminAuthError,
    ApprovalPendingError,
    MassAgentsError,
    NoPendingApprovalError,
    RunLimitError,
    ThreadBusyError,
    ThreadConflictError,
    ThreadNotFoundError,
    ToolsetError,
)

__all__ = [
    "CONSEQUENCES",
    "ENGAGING_TOOLS",
    "AdminAuthError",
    "ApprovalDecision",
    "ApprovalPendingError",
    "ApprovalRequest",
    "MassAgentsError",
    "NoPendingApprovalError",
    "PendingAction",
    "RunLimitError",
    "ThreadBusyError",
    "ThreadConflictError",
    "ThreadNotFoundError",
    "ToolsetError",
]
