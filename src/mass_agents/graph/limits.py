"""Les plafonds, et ce qu'on dit quand ils sont atteints.

Ils valent **par demande** : tout ce qui suit le dernier message de
l'utilisateur. Un nouveau message repart d'un budget entier ; une reprise après
validation n'ajoute pas de message de l'utilisateur, donc elle continue le
budget de la demande en cours — faire valider une action ne remet rien à zéro.

Ce module ne décide pas, il mesure et il formule. C'est le nœud `agent` qui
arrête le run, avant le prochain appel de modèle et non après : un dépassement
constaté après coup a déjà été facturé.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from mass_agents.config import LimitsConfig


def request_messages(messages: Sequence[AnyMessage]) -> Sequence[AnyMessage]:
    """Les messages de la demande en cours : ce qui suit le dernier de
    l'utilisateur."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return messages


def model_calls(messages: Sequence[AnyMessage]) -> int:
    """Le nombre d'appels de modèle déjà faits pour cette demande."""
    return sum(isinstance(m, AIMessage) for m in request_messages(messages))


def consumed_tokens(messages: Sequence[AnyMessage]) -> int:
    """Ce que la demande a coûté, en jetons cumulés.

    On additionne les totaux rapportés par chaque réponse, entrée comprise —
    donc le contexte est recompté à chaque appel. C'est volontaire : ce plafond
    borne une dépense, et c'est bien la facture qui recompte le contexte.
    """
    return sum(
        m.usage_metadata.get("total_tokens", 0)
        for m in request_messages(messages)
        if isinstance(m, AIMessage) and m.usage_metadata
    )


def steps_exhausted(messages: Sequence[AnyMessage], limits: LimitsConfig) -> bool:
    return model_calls(messages) >= limits.max_steps


def budget_exhausted(messages: Sequence[AnyMessage], limits: LimitsConfig) -> bool:
    return consumed_tokens(messages) >= limits.max_tokens_per_request


def steps_exhausted_message(limits: LimitsConfig) -> str:
    """Le message rendu à l'utilisateur, pas une trace technique.

    Il dit ce qui s'est passé et ce qu'on peut faire. « Limite de récursion
    atteinte » laisserait croire à une panne et ferait réessayer la même demande
    à l'identique.
    """
    return (
        f"Je m'arrête ici : cette demande a déjà demandé {limits.max_steps} "
        "étapes sans aboutir. C'est le signe qu'elle est trop large ou trop "
        "ambiguë pour être traitée d'un bloc. Reprenons-la en plusieurs "
        "demandes plus étroites — dites-moi par quoi commencer."
    )


def budget_exhausted_message() -> str:
    return (
        "Je m'arrête ici : cette demande a atteint le volume maximal prévu. "
        "Ce qui a été fait est conservé — reformulez plus étroitement ce qui "
        "reste à faire."
    )
