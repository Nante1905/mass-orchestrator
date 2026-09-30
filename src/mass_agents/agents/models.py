"""Le modèle de l'agent.

Construit une fois et partagé entre les runs, et c'est sans danger : un modèle
ne porte ni jeton ni identité, seulement une clé d'API qui appartient au
service. Les outils, eux, sont liés à chaque run (voir le nœud `agent`).

Vérifié plutôt que supposé : `ChatAnthropic.reasoning_effort` alimente bien
`output_config.effort` dans la requête envoyée à l'API.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from mass_agents.config import get_config


@lru_cache(maxsize=1)
def build_model() -> ChatAnthropic:
    """Le modèle, réglé à l'effort configuré (`AGENT_EFFORT`).

    Un seul réglage pour tout le run : l'agent enchaîne lectures, agrégats et
    identifiants dont une confusion enverrait un vrai courriel à la mauvaise
    personne. `high` par défaut ; le baisser est une économie à mesurer sur des
    demandes réelles, pas à supposer.
    """
    llm = get_config().llm
    return ChatAnthropic(
        model=llm.model,
        api_key=llm.api_key,
        max_tokens=llm.max_tokens,
        reasoning_effort=llm.effort,
    )
