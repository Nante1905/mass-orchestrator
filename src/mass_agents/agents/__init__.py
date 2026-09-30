"""L'agent : son modèle et son prompt.

Ce paquet ignore tout du graphe. Le câblage — quel nœud suit lequel, où
s'arrête un run — est le sujet de `graph/`.
"""

from mass_agents.agents.models import build_model
from mass_agents.agents.prompts import AGENT_PROMPT

__all__ = ["AGENT_PROMPT", "build_model"]
