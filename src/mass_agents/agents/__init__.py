"""Les agents : leurs modèles, leurs prompts, leurs outils de transfert.

Ce paquet ignore tout du graphe. Il décrit *qui* sait faire *quoi* ; le câblage
— qui parle après qui, où s'arrête un run — est le sujet de `graph/`.
"""

from mass_agents.agents.handoff import (
    HANDOFF_TOOLS,
    destination_of,
    handoff_tool_name,
)
from mass_agents.agents.models import Effort, build_model
from mass_agents.agents.specialists import (
    ANALYST,
    EDITOR,
    OPERATIONS,
    SPECIALISTS,
    SPECIALISTS_BY_NAME,
    Specialist,
    build_specialist,
)

__all__ = [
    "ANALYST",
    "EDITOR",
    "HANDOFF_TOOLS",
    "OPERATIONS",
    "SPECIALISTS",
    "SPECIALISTS_BY_NAME",
    "Effort",
    "Specialist",
    "build_model",
    "build_specialist",
    "destination_of",
    "handoff_tool_name",
]
