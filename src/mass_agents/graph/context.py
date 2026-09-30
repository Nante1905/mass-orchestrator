"""Ce qu'un run transporte sans que l'état ne le retienne.

C'est le module où se joue le risque le plus sérieux du projet : **le jeton
d'administrateur ne doit jamais atterrir dans les checkpoints.**

Le danger est réel et vérifiable. `get_checkpoint_metadata` de LangGraph
(`langgraph/checkpoint/base/__init__.py`) recopie dans les métadonnées
persistées toute entrée de `config["configurable"]` dont la valeur est un
scalaire — `str`, `int`, `bool`, `float` — sauf si sa clé figure dans une liste
d'exclusions ou commence par deux tirets bas. Autrement dit, un
`configurable["admin_token"] = "eyJ…"` finit littéralement dans la colonne
`metadata` de la table `checkpoints`, avec la durée de rétention du fil et non
celle du jeton.

Deux parades, posées ensemble parce qu'elles ne protègent pas contre la même
chose :

1. **La clé commence par `__`**, donc l'exclusion explicite s'applique. C'est la
   parade documentée, mais elle repose sur une convention interne de LangGraph
   qui pourrait changer.
2. **La valeur n'est pas un scalaire** mais un objet. Seuls `str`, `int`, `bool`
   et `float` sont recopiés : un `RunContext` ne l'est pas, quel que soit son
   nom de clé. C'est la parade structurelle, celle qui tient même si la première
   cède.

Le test `tests/test_context.py` vérifie la conjonction sur le vrai code de
LangGraph, pas sur notre lecture de sa documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from langchain_core.runnables import RunnableConfig

from mass_agents.auth import AdminIdentity
from mass_agents.persistence import ApprovalLog
from mass_agents.tools import MassToolset

#: Clé sous laquelle voyage le contexte. Le préfixe `__` n'est pas décoratif :
#: voir l'en-tête du module.
RUN_CONTEXT_KEY: Final[str] = "__mass_run_context"


@dataclass(frozen=True, slots=True)
class RunContext:
    """Ce dont les nœuds disposent, le temps d'un run.

    Rassemblé en un objet plutôt que dispersé en clés de configuration pour deux
    raisons. La première est la garantie ci-dessus : un objet ne se sérialise
    pas dans les métadonnées. La seconde est qu'un nœud qui lirait trois clés
    séparées pourrait en oublier une et travailler avec un contexte incomplet.
    """

    #: Qui a lancé ce run, tel que la base le décrivait au moment de l'appel.
    admin: AdminIdentity

    #: Le jeton porté par cet appel, reconstruit à chaque run et jamais persisté.
    #: Il n'est pas relu par les nœuds — l'outillage le porte déjà — mais le
    #: nœud de validation en a besoin pour rejouer un outil hors de la boucle.
    toolset: MassToolset

    #: Le journal des décisions humaines. Injecté plutôt qu'importé : c'est ce
    #: qui permet de faire tourner le graphe sans base dans les tests.
    approvals: ApprovalLog


def build_run_config(thread_id: str, context: RunContext) -> RunnableConfig:
    """La configuration d'un run : le fil, et le contexte de l'appelant.

    Le contexte est reconstruit **à chaque run**, jamais à la création du fil.
    C'est ce qui rend l'expiration du jeton supportable : une conversation
    ouverte hier reprend avec le jeton d'aujourd'hui, sans qu'aucun état n'ait
    à être migré.
    """
    return {
        "configurable": {
            "thread_id": thread_id,
            RUN_CONTEXT_KEY: context,
        }
    }


def build_thread_config(thread_id: str) -> RunnableConfig:
    """La configuration d'une lecture d'état.

    Sans contexte de run : relire un fil ne fait tourner aucun nœud, donc ne
    demande ni outillage ni identité. En exiger un obligerait à joindre le
    serveur MCP pour afficher une conversation, ce qui rendrait l'historique
    indisponible chaque fois que `mass-mcp` l'est.
    """
    return {"configurable": {"thread_id": thread_id}}


def run_context(config: RunnableConfig) -> RunContext:
    """Le contexte du run en cours.

    L'absence est une faute de câblage et non un cas limite : elle signifie
    qu'un nœud a été invoqué hors d'un run construit par `build_run_config`.
    Échouer bruyamment vaut mieux qu'un repli silencieux, qui ferait tourner un
    agent sans outillage.
    """
    context = config.get("configurable", {}).get(RUN_CONTEXT_KEY)
    if not isinstance(context, RunContext):
        raise RuntimeError(
            "Contexte de run absent : le graphe doit être invoqué avec la "
            "configuration produite par build_run_config()."
        )
    return context
