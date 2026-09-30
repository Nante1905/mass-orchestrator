"""Le vocabulaire du flux rendu à l'appelant.

LangGraph diffuse des tuples hétérogènes dont la forme dépend des modes de
streaming demandés et de la présence de sous-graphes. Traduire cela en une
poignée d'évènements nommés a deux effets : le front n'a pas à connaître
LangGraph, et une montée de version qui changerait la forme des chunks se
répare ici plutôt que dans le navigateur.

Le vocabulaire est délibérément court. Chaque évènement ajouté est un cas de
plus à traiter côté interface, et un `switch` qui oublie une branche affiche du
vide sans rien signaler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RunStatus = Literal["completed", "awaiting_approval", "stopped", "failed"]


@dataclass(frozen=True, slots=True)
class TokenEvent:
    """Un fragment de texte, tel que le modèle le produit."""

    text: str
    node: str | None = None
    kind: Literal["token"] = "token"


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """Un message complet, une fois le nœud terminé.

    Redondant avec les fragments, et volontairement : un client simple ignore
    les `token` et n'affiche que les messages complets, sans perdre de contenu.
    """

    role: Literal["assistant"]
    content: str
    node: str | None = None
    kind: Literal["message"] = "message"


@dataclass(frozen=True, slots=True)
class HandoffEvent:
    """Le superviseur vient de passer la main."""

    to: str
    kind: Literal["handoff"] = "handoff"


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    """Le graphe est suspendu : une écriture attend une relecture humaine.

    `payload` est l'aperçu rendu par `mass-mcp`, non retouché. L'interface
    l'affiche tel quel — c'est ce qui garantit que ce qui est approuvé est bien
    ce qui a été calculé.
    """

    payload: dict[str, Any]
    kind: Literal["approval_request"] = "approval_request"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """Un échec, avec ce qu'il faut pour savoir s'il sert de réessayer.

    `recoverable` est la distinction utile : une session expirée demande de se
    reconnecter, un serveur MCP absent demande d'attendre, et rien ne s'arrange
    en rejouant la même requête dans la seconde.
    """

    message: str
    recoverable: bool = False
    kind: Literal["error"] = "error"


@dataclass(frozen=True, slots=True)
class DoneEvent:
    """Fin du run, et dans quel état il laisse le fil."""

    status: RunStatus
    thread_id: str
    kind: Literal["done"] = "done"


@dataclass(frozen=True, slots=True)
class ThreadState:
    """L'état d'un fil, tel que `GET /threads/:id/state` le rend."""

    thread_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: RunStatus = "completed"
    pending_approval: dict[str, Any] | None = None
    turns: int = 0


GraphEvent = (
    TokenEvent | MessageEvent | HandoffEvent | ApprovalEvent | ErrorEvent | DoneEvent
)
