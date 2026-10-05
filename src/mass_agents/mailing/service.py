"""La rédaction d'un courriel : lire l'agenda, demander une fois, vérifier.

Le même parti que les suggestions de posts : pas d'agent, pas de fil. L'écran
envoie une consigne et attend un brouillon ; le code lit lui-même l'agenda,
fait **un** appel au modèle, et vérifie ce qui peut l'être — l'objet tient dans
sa colonne, aucun évènement cité n'est inventé, aucune variable `{{…}}` ne
partira telle quelle chez le destinataire.

Rien n'est envoyé ici. Le brouillon revient dans le formulaire, l'administrateur
le relit, et c'est le backend qui envoie.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Final

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from mass_agents.agents import build_model
from mass_agents.community.agenda import ToolsetFactory, upcoming_mass_events
from mass_agents.community.place import LOCAL_TZ
from mass_agents.community.sources import ContextItem
from mass_agents.config import McpConfig
from mass_agents.domain import EmailDraftGenerationError
from mass_agents.mailing.prompt import EMAIL_PROMPT, render_request
from mass_agents.mailing.schemas import (
    MAX_SUBJECT,
    EmailDraft,
    EmailDraftRequest,
    EmailDraftResult,
    EventRef,
)
from mass_agents.tools.toolset import build_mass_toolset

logger = logging.getLogger(__name__)

#: Tout l'agenda publié à venir, réservé aux membres compris : un courriel aux
#: adhérents peut annoncer une soirée qui leur est réservée. Le prompt dit de
#: ne pas l'annoncer au grand public, et l'évènement le dit dans son résumé.
LIST_ARGS: Final[dict[str, Any]] = {
    "period": "upcoming",
    "status": "published",
    "sort_by": "dateEvent",
    "sort_order": "asc",
    "limit": 10,
}

#: Une seconde chance, avec ce qui n'allait pas. Au-delà, mieux vaut le dire.
ATTEMPTS = 2

#: `[à compléter : heure du rendez-vous]` → « heure du rendez-vous ».
_MISSING = re.compile(r"\[à compléter\s*:\s*([^\]]+)\]", re.IGNORECASE)
#: Une variable de gabarit : rien ne la remplacerait à l'envoi.
_VARIABLE = re.compile(r"\{\{.*?\}\}")


class EmailDraftService:
    def __init__(
        self,
        mcp: McpConfig,
        *,
        timeout_s: float,
        model_factory: Callable[[], BaseChatModel] = build_model,
        toolset_factory: ToolsetFactory = build_mass_toolset,
        clock: Callable[[], datetime] = lambda: datetime.now(LOCAL_TZ),
    ) -> None:
        self._mcp = mcp
        self._timeout_s = timeout_s
        self._model_factory = model_factory
        self._toolset_factory = toolset_factory
        self._clock = clock

    async def draft(
        self, request: EmailDraftRequest, admin_token: str
    ) -> EmailDraftResult:
        now = self._clock()

        events, agenda_warning = await upcoming_mass_events(
            admin_token, self._mcp, self._toolset_factory, LIST_ARGS
        )
        events = _numbered(events)
        warnings = [agenda_warning] if agenda_warning else []

        messages: list[BaseMessage] = [
            SystemMessage(content=EMAIL_PROMPT),
            HumanMessage(
                content=render_request(today=now.date(), request=request, events=events)
            ),
        ]
        by_id = {item.id: item for item in events}

        try:
            async with asyncio.timeout(self._timeout_s):
                draft = await self._generate(messages, by_id)
        except TimeoutError as error:
            raise EmailDraftGenerationError(
                "Le modèle n'a pas répondu dans les délais : réessayer."
            ) from error

        return EmailDraftResult(
            subject=draft.subject.strip(),
            body=draft.body.strip(),
            events=[
                EventRef(
                    title=item.title,
                    date=item.published.date().isoformat() if item.published else None,
                )
                for item in (by_id[eid] for eid in dict.fromkeys(draft.event_ids))
            ],
            missing=_missing(draft),
            warnings=warnings,
        )

    async def _generate(
        self, messages: list[BaseMessage], by_id: dict[str, ContextItem]
    ) -> EmailDraft:
        model = self._model_factory().with_structured_output(
            EmailDraft, method="json_schema"
        )

        problems: list[str] = []
        for _ in range(ATTEMPTS):
            try:
                draft = await model.ainvoke(messages)
            except (OutputParserException, ValidationError) as error:
                logger.warning("Brouillon illisible : %s", error)
                draft, problems = None, ["la réponse ne suit pas le schéma"]
            else:
                problems = _problems(draft, by_id)

            if isinstance(draft, EmailDraft) and not problems:
                return draft

            logger.warning("Brouillon rejeté : %s", "; ".join(problems))
            messages = [
                *messages,
                HumanMessage(
                    content=(
                        "Ton courriel ne respecte pas la demande : "
                        + "; ".join(problems)
                        + ". Réécris-le en entier."
                    )
                ),
            ]

        raise EmailDraftGenerationError(
            "Le modèle n'a pas rendu de courriel conforme ("
            + "; ".join(problems)
            + "). Réessayer."
        )


def _numbered(events: Sequence[ContextItem]) -> list[ContextItem]:
    return [replace(item, id=f"e{index}") for index, item in enumerate(events, start=1)]


def _problems(draft: object, by_id: dict[str, ContextItem]) -> list[str]:
    if not isinstance(draft, EmailDraft):
        return ["la réponse ne suit pas le schéma"]

    problems: list[str] = []
    if not draft.subject.strip():
        problems.append("l'objet est vide")
    if len(draft.subject.strip()) > MAX_SUBJECT:
        problems.append(f"l'objet dépasse {MAX_SUBJECT} caractères")
    if not draft.body.strip():
        problems.append("le corps est vide")
    if _VARIABLE.search(draft.subject) or _VARIABLE.search(draft.body):
        problems.append("le texte contient une variable `{{…}}` que rien ne remplira")
    unknown = [eid for eid in draft.event_ids if eid not in by_id]
    if unknown:
        problems.append(f"évènements inconnus {', '.join(unknown)}")

    return problems


def _missing(draft: EmailDraft) -> list[str]:
    """Les informations marquées à compléter, dans l'ordre, sans doublon."""
    found = _MISSING.findall(f"{draft.subject}\n{draft.body}")
    return list(dict.fromkeys(item.strip() for item in found))
