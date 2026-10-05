"""Le modèle de l'agent.

Construit une fois et partagé entre les runs, et c'est sans danger : un modèle
ne porte ni jeton ni identité, seulement une clé d'API qui appartient au
service. Les outils, eux, sont liés à chaque run (voir le nœud `agent`).

Le fournisseur se choisit dans la configuration (`LLM_PROVIDER`, ou
`DEFAULT_PROVIDER` dans `config.py`). Le reste du graphe ne voit qu'un
`BaseChatModel` : les définitions d'outils au format Anthropic sont converties
par `ChatOpenAI.bind_tools`, et `_text_of` lit le contenu plat comme en blocs.

Vérifié plutôt que supposé : `ChatAnthropic.reasoning_effort` alimente bien
`output_config.effort` dans la requête envoyée à l'API.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from mass_agents.config import LlmConfig, get_config


@lru_cache(maxsize=1)
def build_model() -> BaseChatModel:
    """Le modèle, réglé à l'effort configuré (`AGENT_EFFORT`).

    Un seul réglage pour tout le run : l'agent enchaîne lectures, agrégats et
    identifiants dont une confusion enverrait un vrai courriel à la mauvaise
    personne. `high` par défaut ; le baisser est une économie à mesurer sur des
    demandes réelles, pas à supposer.
    """
    llm = get_config().llm
    if llm.provider == "openai":
        return _openai(llm)
    return _anthropic(llm)


def _anthropic(llm: LlmConfig) -> ChatAnthropic:
    # `model_validate` plutôt que le constructeur : Pylance ne connaît que les
    # alias pydantic (`model_name`, `effort`…) et signalerait les noms de
    # champs, que pydantic accepte pourtant (`populate_by_name`).
    return ChatAnthropic.model_validate(
        {
            "model": llm.model,
            "api_key": llm.api_key,
            "max_tokens": llm.max_tokens,
            "reasoning_effort": llm.effort,
        }
    )


def _openai(llm: LlmConfig) -> ChatOpenAI:
    """`stream_usage` est explicite : le plafond de jetons se calcule sur
    `usage_metadata`, et le graphe diffusé appelle le modèle en streaming.
    Sans lui, le budget compterait zéro et ne s'arrêterait jamais.

    `reasoning_effort` suppose un modèle de raisonnement (gpt-5, o-series).
    """
    return ChatOpenAI(
        model=llm.model,
        api_key=SecretStr(llm.api_key),
        max_completion_tokens=llm.max_tokens,
        reasoning_effort=llm.effort,
        stream_usage=True,
    )
