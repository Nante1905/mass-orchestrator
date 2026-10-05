"""Les suggestions de posts : collecter, demander une fois, vérifier.

Pas d'agent ici, et c'est délibéré. La page ne converse pas : elle envoie un
nombre et des catégories, et attend une liste. Le code sait d'avance quoi lire
— les flux, le ciel, l'agenda —, il le lit donc lui-même, en parallèle, puis
fait **un** appel au modèle. Le coût est borné et prévisible ; il n'y a ni
boucle d'outils, ni fil, ni checkpoint.

Ce que le prompt demande, le code le vérifie : le nombre de posts, les
catégories, et surtout que chaque source citée existe. Un post sur une
« découverte » inventée ne passe pas, quoi que le modèle ait écrit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import get_args

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from mass_agents.agents import build_model
from mass_agents.community.agenda import ToolsetFactory, upcoming_mass_events
from mass_agents.community.place import LOCAL_TZ
from mass_agents.community.prompt import COMMUNITY_PROMPT, render_request
from mass_agents.community.schemas import (
    Category,
    Post,
    PostSuggestion,
    SourceRef,
    SuggestionRequest,
    Suggestions,
    SuggestionsResult,
)
from mass_agents.community.sky import upcoming_sky_events
from mass_agents.community.sources import ContextItem, FeedReader
from mass_agents.config import McpConfig
from mass_agents.domain import SuggestionGenerationError, SuggestionRequestError
from mass_agents.tools.toolset import build_mass_toolset

logger = logging.getLogger(__name__)

ALL_CATEGORIES: tuple[Category, ...] = get_args(Category)
EVENT_CATEGORY: Category = "evenement_mass"

#: Une seconde chance, avec ce qui n'allait pas. Au-delà, on paie des appels
#: pour un modèle qui ne suit pas la demande : mieux vaut le dire.
ATTEMPTS = 2


class CommunityService:
    def __init__(
        self,
        reader: FeedReader,
        mcp: McpConfig,
        *,
        timeout_s: float,
        model_factory: Callable[[], BaseChatModel] = build_model,
        toolset_factory: ToolsetFactory = build_mass_toolset,
        clock: Callable[[], datetime] = lambda: datetime.now(LOCAL_TZ),
    ) -> None:
        self._reader = reader
        self._mcp = mcp
        self._timeout_s = timeout_s
        self._model_factory = model_factory
        self._toolset_factory = toolset_factory
        self._clock = clock

    async def suggest(
        self, request: SuggestionRequest, admin_token: str
    ) -> SuggestionsResult:
        now = self._clock()
        wants_events = not request.categories or EVENT_CATEGORY in request.categories

        (news, warnings), (events, agenda_warning) = await asyncio.gather(
            self._reader.read(now),
            self._events(admin_token) if wants_events else _nothing(),
        )
        allowed = list(request.categories or ALL_CATEGORIES)
        if wants_events and not events:
            # Indisponible ou vide, la conséquence est la même : rien à
            # promouvoir. Seul le message change, pour dire où regarder.
            reason = agenda_warning or "Aucun évènement MASS public n'est à venir"
            allowed.remove(EVENT_CATEGORY)
            if not allowed:
                raise SuggestionRequestError(
                    f"{reason} : choisir une autre catégorie, ou publier "
                    "l'évènement dans le back-office."
                )
            if request.categories or agenda_warning:
                warnings = [
                    *warnings,
                    f"{reason} : la catégorie « Évènement MASS » est ignorée.",
                ]

        items = _numbered(news, upcoming_sky_events(now.date()), events)
        if not items:
            raise SuggestionRequestError(
                "Aucune actualité n'a pu être lue : réessayer dans quelques "
                "minutes."
            )

        messages: list[BaseMessage] = [
            SystemMessage(content=COMMUNITY_PROMPT),
            HumanMessage(
                content=render_request(
                    today=now.date(),
                    count=request.count,
                    categories=allowed if request.categories else (),
                    items=items,
                )
            ),
        ]
        by_id = {item.id: item for item in items}

        try:
            async with asyncio.timeout(self._timeout_s):
                suggestions = await self._generate(
                    messages, request.count, allowed, by_id
                )
        except TimeoutError as error:
            raise SuggestionGenerationError(
                "Le modèle n'a pas répondu dans les délais : réessayer, ou "
                "demander moins de posts."
            ) from error

        return SuggestionsResult(
            posts=[_resolved(post, by_id) for post in suggestions.posts],
            warnings=warnings,
        )

    async def _events(self, admin_token: str) -> tuple[list[ContextItem], str | None]:
        return await upcoming_mass_events(admin_token, self._mcp, self._toolset_factory)

    async def _generate(
        self,
        messages: list[BaseMessage],
        count: int,
        allowed: Sequence[Category],
        by_id: dict[str, ContextItem],
    ) -> Suggestions:
        model = self._model_factory().with_structured_output(
            Suggestions, method="json_schema"
        )

        problems: list[str] = []
        for _ in range(ATTEMPTS):
            try:
                suggestions = await model.ainvoke(messages)
            except (OutputParserException, ValidationError) as error:
                logger.warning("Suggestions illisibles : %s", error)
                suggestions, problems = None, ["la réponse ne suit pas le schéma"]
            else:
                problems = _problems(suggestions, count, allowed, by_id)

            if isinstance(suggestions, Suggestions) and not problems:
                return suggestions

            logger.warning("Suggestions rejetées : %s", "; ".join(problems))
            messages = [
                *messages,
                HumanMessage(
                    content=(
                        "Ta proposition ne respecte pas la demande : "
                        + "; ".join(problems)
                        + ". Recommence la liste en entier."
                    )
                ),
            ]

        raise SuggestionGenerationError(
            "Le modèle n'a pas rendu de suggestions conformes ("
            + "; ".join(problems)
            + "). Réessayer."
        )


async def _nothing() -> tuple[list[ContextItem], str | None]:
    return [], None


def _numbered(*groups: Sequence[ContextItem]) -> list[ContextItem]:
    """Les éléments, avec un identifiant par origine : `n1`, `s1`, `e1`."""
    prefixes = {"news": "n", "sky": "s", "event": "e"}
    return [
        replace(item, id=f"{prefixes[item.kind]}{index}")
        for group in groups
        for index, item in enumerate(group, start=1)
    ]


def _problems(
    suggestions: object,
    count: int,
    allowed: Sequence[Category],
    by_id: dict[str, ContextItem],
) -> list[str]:
    if not isinstance(suggestions, Suggestions):
        return ["la réponse ne suit pas le schéma"]

    problems: list[str] = []
    if len(suggestions.posts) != count:
        problems.append(f"{len(suggestions.posts)} posts au lieu de {count}")

    for number, post in enumerate(suggestions.posts, start=1):
        if post.category not in allowed:
            problems.append(f"post {number} : catégorie `{post.category}` non demandée")
        if not post.source_ids:
            problems.append(f"post {number} : aucune source citée")
        unknown = [sid for sid in post.source_ids if sid not in by_id]
        if unknown:
            problems.append(f"post {number} : sources inconnues {', '.join(unknown)}")
        if post.category == EVENT_CATEGORY and not any(
            sid.startswith("e") and sid in by_id for sid in post.source_ids
        ):
            problems.append(f"post {number} : évènement MASS sans élément `e…`")

    return problems


def _resolved(post: Post, by_id: dict[str, ContextItem]) -> PostSuggestion:
    return PostSuggestion(
        category=post.category,
        hook=post.hook,
        text=post.text,
        hashtags=_hashtags(post.hashtags),
        visual=post.visual,
        sources=[
            SourceRef(
                title=item.title,
                publisher=item.publisher,
                url=item.url,
                date=item.published.date().isoformat() if item.published else None,
            )
            for item in (by_id[sid] for sid in dict.fromkeys(post.source_ids))
        ],
    )


def _hashtags(tags: Sequence[str]) -> list[str]:
    cleaned = ("#" + tag.strip().lstrip("#").replace(" ", "") for tag in tags)
    return list(dict.fromkeys(tag for tag in cleaned if len(tag) > 1))
