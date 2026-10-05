"""Les évènements MASS à venir, lus par `mass-mcp` avec le jeton de l'appelant.

Le même chemin que l'agent, et pour la même raison : `mass-mcp` relaie le jeton
et c'est le backend qui tranche. Cette lecture n'a pas de passe-droit.

Elle est facultative. Un serveur MCP injoignable ne doit pas priver la page de
suggestions sur l'actualité : l'échec devient un avertissement, et la catégorie
« Évènement MASS » est retirée de ce que le modèle peut produire.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Final

from mass_agents.community.place import LOCAL_TZ
from mass_agents.community.sources import ContextItem
from mass_agents.config import McpConfig
from mass_agents.tools.payload import parse_tool_payload
from mass_agents.tools.toolset import MassToolset, build_mass_toolset

logger = logging.getLogger(__name__)

#: Seuls les évènements publiés et publics : un post ne fait pas la promotion
#: d'un brouillon, ni d'une soirée réservée aux membres.
LIST_ARGS: Final[dict[str, Any]] = {
    "period": "upcoming",
    "status": "published",
    "is_public": True,
    "sort_by": "dateEvent",
    "sort_order": "asc",
    "limit": 10,
}

ToolsetFactory = Callable[[str, McpConfig], Awaitable[MassToolset]]


async def upcoming_mass_events(
    admin_token: str,
    mcp: McpConfig,
    build_toolset: ToolsetFactory = build_mass_toolset,
) -> tuple[list[ContextItem], str | None]:
    """Les évènements à venir, ou la raison pour laquelle on s'en passe."""
    try:
        toolset = await build_toolset(admin_token, mcp)
        raw = await toolset.get("list_events").ainvoke(LIST_ARGS)
    except Exception as error:
        logger.warning("Agenda MASS indisponible : %s", error)
        return [], "L'agenda MASS est indisponible"

    payload = parse_tool_payload(raw) or {}
    return [
        item
        for event in payload.get("events", [])
        if isinstance(event, dict) and (item := _item(event)) is not None
    ], None


def _item(event: dict[str, Any]) -> ContextItem | None:
    title = event.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    when = _parse(event.get("dateEvent"))
    location = event.get("location") or "lieu à préciser"
    prices = sorted(
        {
            ticket["price"]
            for ticket in event.get("tickets", [])
            if isinstance(ticket, dict) and ticket.get("price") is not None
        }
    )
    if not prices:
        price = "tarif non renseigné"
    elif prices == [0]:
        price = "gratuit"
    else:
        # Comme le back-office (`format-amount.ts`) : « 20 000 Ar ».
        price = f"à partir de {round(prices[0]):,} Ar".replace(",", " ")

    return ContextItem(
        kind="event",
        title=title.strip(),
        summary=f"Le {_format(when)}, {location}. Entrée : {price}.",
        publisher="MASS",
        url=None,
        published=when,
    )


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format(when: datetime | None) -> str:
    """En heure de Madagascar : une date rendue en UTC annoncerait 15h pour 18h."""
    if when is None:
        return "date à préciser"
    if when.tzinfo is not None:
        when = when.astimezone(LOCAL_TZ)
    return when.strftime("%d/%m/%Y à %Hh%M")
