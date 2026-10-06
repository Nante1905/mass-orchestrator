"""L'analyse des avis d'un évènement : lire, demander une fois, vérifier, compter.

Le même parti que les posts et les courriels : pas d'agent, pas de fil. La page
demande l'analyse d'un évènement ; le code lit lui-même ses avis par
`mass-mcp`, avec le jeton de l'appelant, fait **un** appel au modèle, et
vérifie ce qui peut l'être — chaque avis a reçu un ton et un seul, aucun thème
ne cite un avis qui n'existe pas.

Ce que la page affiche en chiffres, le code le compte : la répartition des tons
et le nombre de mentions d'un thème viennent des références, et les extraits
sont tirés du texte des avis. Le modèle ne fournit que le classement et la
rédaction.

Les noms des auteurs ne partent pas au modèle : il n'en a pas besoin pour
analyser, et le prompt lui interdit de nommer qui que ce soit.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Final

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from mass_agents.agents import build_model
from mass_agents.community.agenda import ToolsetFactory
from mass_agents.community.place import LOCAL_TZ
from mass_agents.config import McpConfig
from mass_agents.domain import (
    FeedbackAnalysisGenerationError,
    FeedbackAnalysisRequestError,
    ToolsetError,
)
from mass_agents.feedbacks.prompt import FEEDBACK_PROMPT, FeedbackItem, render_request
from mass_agents.feedbacks.schemas import (
    EXAMPLE_LENGTH,
    EXAMPLES_PER_THEME,
    MAX_CONTENT,
    MAX_FEEDBACKS,
    MAX_IMPROVEMENTS,
    MAX_STRENGTHS,
    MAX_THEMES,
    EventInfo,
    FeedbackAnalysis,
    FeedbackAnalysisResult,
    RatingSummary,
    Sentiment,
    SentimentCounts,
    Theme,
    ThemeExample,
    ThemeResult,
)
from mass_agents.tools.payload import parse_tool_payload
from mass_agents.tools.toolset import build_mass_toolset

logger = logging.getLogger(__name__)

TOOL: Final[str] = "get_event_feedbacks"

#: En deçà, la synthèse tient de l'anecdote : elle reste utile, mais on le dit.
FEW_FEEDBACKS: Final[int] = 3

#: Une seconde chance, avec ce qui n'allait pas. Au-delà, mieux vaut le dire.
ATTEMPTS = 2

AUDIENCE_LABELS: Final[dict[str, str]] = {"member": "membre", "public": "visiteur"}


class FeedbackAnalysisService:
    def __init__(
        self,
        mcp: McpConfig,
        *,
        timeout_s: float,
        model_factory: Callable[[], BaseChatModel] = build_model,
        toolset_factory: ToolsetFactory = build_mass_toolset,
    ) -> None:
        self._mcp = mcp
        self._timeout_s = timeout_s
        self._model_factory = model_factory
        self._toolset_factory = toolset_factory

    async def analyse(self, event_id: str, admin_token: str) -> FeedbackAnalysisResult:
        payload = await self._read(event_id, admin_token)

        event = payload.get("event") or {}
        feedbacks = payload.get("feedbacks") or {}
        records = [
            row
            for row in feedbacks.get("records", [])
            if isinstance(row, dict) and str(row.get("content") or "").strip()
        ]
        if not records:
            raise FeedbackAnalysisRequestError(
                "Aucun avis n'a encore été déposé pour cet évènement : rien à "
                "analyser.",
                status=409,
            )

        total = max(int(feedbacks.get("total") or 0), len(records))
        ratings = _ratings(payload.get("ratings"))
        items = [_item(index, row) for index, row in enumerate(records, start=1)]
        by_id = {item.id: item for item in items}

        warnings: list[str] = []
        if total > len(items):
            warnings.append(
                f"Seuls les {len(items)} avis les plus récents, sur {total}, "
                "ont été analysés."
            )
        if len(items) < FEW_FEEDBACKS:
            warnings.append(
                f"{len(items)} avis seulement : la synthèse est indicative."
            )
        if any(len(str(row.get("content"))) > MAX_CONTENT for row in records):
            warnings.append(
                f"Les avis les plus longs ont été lus jusqu'à {MAX_CONTENT} caractères."
            )

        title = str(event.get("title") or event_id)
        messages: list[BaseMessage] = [
            SystemMessage(content=FEEDBACK_PROMPT),
            HumanMessage(
                content=render_request(
                    title=title,
                    when=_format(event.get("dateEvent")),
                    location=event.get("location"),
                    ratings=ratings,
                    items=items,
                    total=total,
                )
            ),
        ]

        try:
            async with asyncio.timeout(self._timeout_s):
                analysis = await self._generate(messages, by_id)
        except TimeoutError as error:
            raise FeedbackAnalysisGenerationError(
                "Le modèle n'a pas répondu dans les délais : réessayer."
            ) from error

        tones = {entry.feedback_id: entry.sentiment for entry in analysis.sentiments}
        counts = Counter(tones.values())

        return FeedbackAnalysisResult(
            event=EventInfo(
                id=str(event.get("id") or event_id),
                title=title,
                date=_date(event.get("dateEvent")),
            ),
            analysed=len(items),
            total=total,
            ratings=ratings,
            sentiment=SentimentCounts(
                positif=counts["positif"],
                neutre=counts["neutre"],
                negatif=counts["negatif"],
            ),
            summary=analysis.summary.strip(),
            themes=sorted(
                (
                    _theme_result(theme, by_id, records, tones)
                    for theme in analysis.themes
                ),
                key=lambda theme: theme.mentions,
                reverse=True,
            ),
            strengths=_clean(analysis.strengths),
            improvements=_clean(analysis.improvements),
            warnings=warnings,
        )

    async def _read(self, event_id: str, admin_token: str) -> dict[str, Any]:
        """Les avis et les notes de l'évènement, lus par `mass-mcp`."""
        try:
            toolset = await self._toolset_factory(admin_token, self._mcp)
            raw = await toolset.get(TOOL).ainvoke(
                {"event_id": event_id, "limit": MAX_FEEDBACKS}
            )
        except ToolsetError:
            raise
        except Exception as error:
            logger.warning("Avis illisibles : %s", error)
            raise ToolsetError(
                "Les avis n'ont pas pu être lus : le serveur MCP est injoignable."
            ) from error

        payload = parse_tool_payload(raw)
        if payload is None:
            raise ToolsetError("Le serveur MCP a rendu une réponse illisible.")

        # Un échec de l'outil revient en contenu, avec le statut de l'API.
        if "error" in payload:
            status = payload.get("status")
            raise FeedbackAnalysisRequestError(
                str(payload["error"]),
                status=status if status in (400, 404) else 502,
            )

        return payload

    async def _generate(
        self, messages: list[BaseMessage], by_id: dict[str, FeedbackItem]
    ) -> FeedbackAnalysis:
        model = self._model_factory().with_structured_output(
            FeedbackAnalysis, method="json_schema"
        )

        problems: list[str] = []
        for _ in range(ATTEMPTS):
            try:
                analysis = await model.ainvoke(messages)
            except (OutputParserException, ValidationError) as error:
                logger.warning("Analyse illisible : %s", error)
                analysis, problems = None, ["la réponse ne suit pas le schéma"]
            else:
                problems = _problems(analysis, by_id)

            if isinstance(analysis, FeedbackAnalysis) and not problems:
                return analysis

            logger.warning("Analyse rejetée : %s", "; ".join(problems))
            messages = [
                *messages,
                HumanMessage(
                    content=(
                        "Ton analyse ne respecte pas la demande : "
                        + "; ".join(problems)
                        + ". Recommence-la en entier."
                    )
                ),
            ]

        raise FeedbackAnalysisGenerationError(
            "Le modèle n'a pas rendu d'analyse conforme ("
            + "; ".join(problems)
            + "). Réessayer."
        )


def _item(index: int, row: dict[str, Any]) -> FeedbackItem:
    content = str(row.get("content")).strip()
    if len(content) > MAX_CONTENT:
        content = content[:MAX_CONTENT].rstrip() + "…"
    rating = row.get("rating")

    return FeedbackItem(
        id=f"f{index}",
        audience=AUDIENCE_LABELS.get(str(row.get("audience")), "participant"),
        rating=rating if isinstance(rating, int) else None,
        content=content,
    )


def _ratings(raw: Any) -> RatingSummary | None:
    if not isinstance(raw, dict):
        return None
    try:
        return RatingSummary.model_validate(raw)
    except ValidationError:
        logger.warning("Notes illisibles : %s", raw)
        return None


def _problems(analysis: object, by_id: dict[str, FeedbackItem]) -> list[str]:
    if not isinstance(analysis, FeedbackAnalysis):
        return ["la réponse ne suit pas le schéma"]

    problems: list[str] = []
    if not analysis.summary.strip():
        problems.append("la synthèse est vide")

    rated = [entry.feedback_id for entry in analysis.sentiments]
    unknown = sorted(set(rated) - by_id.keys())
    missing = [fid for fid in by_id if fid not in rated]
    twice = sorted(fid for fid, n in Counter(rated).items() if n > 1)
    if unknown:
        problems.append(f"tons donnés à des avis inconnus : {', '.join(unknown)}")
    if missing:
        problems.append(f"avis sans ton : {', '.join(missing)}")
    if twice:
        problems.append(f"avis classés deux fois : {', '.join(twice)}")

    if not 1 <= len(analysis.themes) <= MAX_THEMES:
        problems.append(f"{len(analysis.themes)} thèmes au lieu de 1 à {MAX_THEMES}")
    for number, theme in enumerate(analysis.themes, start=1):
        if not theme.label.strip():
            problems.append(f"thème {number} : libellé vide")
        if not theme.feedback_ids:
            problems.append(f"thème {number} : aucun avis cité")
        strangers = [fid for fid in theme.feedback_ids if fid not in by_id]
        if strangers:
            problems.append(f"thème {number} : avis inconnus {', '.join(strangers)}")

    if len(analysis.strengths) > MAX_STRENGTHS:
        problems.append(f"plus de {MAX_STRENGTHS} points forts")
    if len(analysis.improvements) > MAX_IMPROVEMENTS:
        problems.append(f"plus de {MAX_IMPROVEMENTS} recommandations")

    return problems


def _theme_result(
    theme: Theme,
    by_id: dict[str, FeedbackItem],
    records: Sequence[dict[str, Any]],
    tones: dict[str, Sentiment],
) -> ThemeResult:
    cited = list(dict.fromkeys(theme.feedback_ids))
    # Les extraits illustrent le ton du thème : un avis du même ton d'abord.
    ordered = sorted(cited, key=lambda fid: tones.get(fid) != theme.sentiment)

    return ThemeResult(
        label=theme.label.strip(),
        sentiment=theme.sentiment,
        summary=theme.summary.strip(),
        mentions=len(cited),
        examples=[
            ThemeExample(
                text=_excerpt(str(records[int(fid[1:]) - 1].get("content"))),
                rating=by_id[fid].rating,
            )
            for fid in ordered[:EXAMPLES_PER_THEME]
        ],
    )


def _excerpt(text: str) -> str:
    """Le début de l'avis, coupé sur un mot."""
    text = " ".join(text.split())
    if len(text) <= EXAMPLE_LENGTH:
        return text
    return text[:EXAMPLE_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _clean(points: Sequence[str]) -> list[str]:
    return [point.strip() for point in points if point.strip()]


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.astimezone(LOCAL_TZ) if when.tzinfo is not None else when


def _format(value: Any) -> str:
    when = _parse(value)
    return when.strftime("%d/%m/%Y") if when else "date non renseignée"


def _date(value: Any) -> str | None:
    when = _parse(value)
    return when.date().isoformat() if when else None
