"""Le modèle, décliné par niveau d'effort.

Le même modèle partout : c'est `output_config.effort` qui différencie les
agents, pas la référence. Rétrograder le routage vers un petit modèle pour
économiser est une optimisation à mesurer, pas à supposer — un superviseur qui
route mal fait payer un tour complet à un agent, ce qui coûte plus cher que
l'appel qu'on aurait économisé.

Vérifié plutôt que supposé : `ChatAnthropic.reasoning_effort` alimente bien
`output_config.effort` dans la requête envoyée à l'API. Le risque identifié au
plan — un effort avalé par l'intégration, tout tournant au défaut du modèle —
ne se matérialise pas.

Contrairement à l'outillage, ces objets **sont** partagés entre les runs, et
c'est sans danger : un modèle ne porte ni jeton ni identité, seulement une clé
d'API qui appartient au service.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_anthropic import ChatAnthropic

from mass_agents.config import get_config

Effort = Literal["low", "medium", "high"]


@lru_cache(maxsize=len(("low", "medium", "high")))
def build_model(effort: Effort) -> ChatAnthropic:
    """Le modèle réglé pour ce niveau d'effort.

    - `low` pour le routage : choisir entre trois destinations ne demande pas de
      raisonnement, et c'est l'appel le plus fréquent du graphe.
    - `medium` pour la rédaction : produire un brouillon relu ensuite par un
      humain ne justifie pas l'effort maximal.
    - `high` pour l'analyse et les opérations : la première enchaîne des
      lectures et des agrégats, la seconde manipule des identifiants dont une
      confusion enverrait un vrai courriel à la mauvaise personne.
    """
    llm = get_config().llm
    return ChatAnthropic(
        model=llm.model,
        api_key=llm.api_key,
        max_tokens=llm.max_tokens,
        reasoning_effort=effort,
    )
