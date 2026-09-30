"""Les plafonds, et ce qu'on dit quand ils sont atteints.

Ils sont posés dès la construction du graphe et non repoussés à un lot de
durcissement : un graphe qui boucle en production facture un appel de modèle à
chaque tour, et personne ne s'en aperçoit avant la facture. Un plafond ajouté
après coup arrive toujours après le premier incident.

Ce module ne décide pas, il mesure et il formule. C'est le superviseur qui
arrête le run — lui seul est traversé à chaque tour, et l'arrêt doit se produire
avant le prochain appel de modèle, pas après.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage

from mass_agents.config import LimitsConfig


def consumed_tokens(messages: Sequence[AnyMessage]) -> int:
    """Ce que le fil a coûté, en jetons cumulés.

    On additionne les totaux rapportés par chaque réponse, entrée comprise —
    donc le contexte est recompté à chaque tour. C'est volontaire : ce plafond
    borne une dépense, pas une quantité de texte produit, et c'est bien la
    facture qui recompte le contexte à chaque appel.
    """
    total = 0
    for message in messages:
        if isinstance(message, AIMessage) and message.usage_metadata:
            total += message.usage_metadata.get("total_tokens", 0)
    return total


def turns_exhausted(turns: int, limits: LimitsConfig) -> bool:
    return turns >= limits.max_turns


def budget_exhausted(messages: Sequence[AnyMessage], limits: LimitsConfig) -> bool:
    return consumed_tokens(messages) >= limits.max_tokens_per_thread


def turns_exhausted_message(limits: LimitsConfig) -> str:
    """Le message rendu à l'utilisateur, pas une trace technique.

    Il dit ce qui s'est passé et ce qu'on peut faire — reprendre en plus étroit.
    « Limite de récursion atteinte » laisserait croire à une panne et ferait
    réessayer la même demande à l'identique.
    """
    return (
        f"Je m'arrête ici : cette demande a déjà mobilisé {limits.max_turns} "
        "transferts entre agents sans aboutir. C'est le signe qu'elle est trop "
        "large ou trop ambiguë pour être traitée d'un bloc. Reprenons-la en "
        "plusieurs demandes plus étroites — dites-moi par quoi commencer."
    )


def budget_exhausted_message() -> str:
    return (
        "Je m'arrête ici : cette conversation a atteint le volume maximal prévu. "
        "Rien n'est perdu — ouvrez une nouvelle conversation en y reprenant "
        "seulement ce qui reste à faire, elle repartira avec un budget entier."
    )
