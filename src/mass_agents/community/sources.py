"""Les actualités scientifiques, lues dans des flux choisis.

Des flux RSS plutôt que la recherche web d'un fournisseur de modèle : la liste
est la même quel que soit le fournisseur, les sources sont des institutions et
des rédactions identifiées, et chaque article arrive avec son lien — ce qui
permet au code, et pas seulement au prompt, de vérifier qu'un post cite une
source réelle.

La liste se modifie ici. Chaque flux a été vérifié à l'ajout (réponse 200, RSS
valide, articles datés) ; un flux qui tombe ne fait pas échouer la génération,
il est signalé dans les avertissements.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final, Literal

import httpx

logger = logging.getLogger(__name__)

ItemKind = Literal["news", "sky", "event"]


@dataclass(frozen=True, slots=True)
class Feed:
    publisher: str
    url: str
    lang: Literal["fr", "en"]


FEEDS: Final[tuple[Feed, ...]] = (
    # Français
    Feed("Ciel & Espace", "https://www.cieletespace.fr/rss", "fr"),
    Feed(
        "Futura Sciences — Espace",
        "https://www.futura-sciences.com/rss/espace/actualites.xml",
        "fr",
    ),
    Feed(
        "Sciences et Avenir — Espace",
        "https://www.sciencesetavenir.fr/espace/rss.xml",
        "fr",
    ),
    Feed("CNRS Le journal", "https://lejournal.cnrs.fr/rss", "fr"),
    # Anglais
    Feed("NASA", "https://www.nasa.gov/news-release/feed/", "en"),
    Feed(
        "ESA — Space Science",
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "en",
    ),
    Feed("ESO", "https://www.eso.org/public/news/feed/", "en"),
)

#: Un article plus ancien n'est plus de l'actualité pour un réseau social.
MAX_AGE: Final[timedelta] = timedelta(days=21)

#: Par flux : sans plafond, le flux le plus prolixe occuperait tout le contexte.
PER_FEED: Final[int] = 6

#: Au total. Chaque article lu par le modèle se paie en jetons d'entrée.
MAX_NEWS: Final[int] = 30

SUMMARY_CHARS: Final[int] = 400

CACHE_TTL_S: Final[float] = 3_600.0
FETCH_TIMEOUT_S: Final[float] = 10.0

_ATOM = "{http://www.w3.org/2005/Atom}"
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ContextItem:
    """Un élément que le modèle peut citer, quelle que soit son origine.

    `id` est attribué au moment d'assembler le contexte (`n1`, `s1`, `e1`) :
    c'est la seule chose que le modèle renvoie pour désigner une source, et la
    seule que le code vérifie.
    """

    kind: ItemKind
    title: str
    summary: str
    publisher: str
    url: str | None
    published: datetime | None
    id: str = ""


def parse_feed(raw: bytes, feed: Feed) -> list[ContextItem]:
    """Les articles d'un flux RSS 2.0 ou Atom.

    `ElementTree` ne résout pas les entités externes, et l'expat livré avec
    Python borne l'expansion des entités internes : un flux malveillant ne fait
    pas exploser la mémoire.
    """
    root = ET.fromstring(raw)
    nodes = root.iter("item") if root.find(".//item") is not None else root.iter(
        f"{_ATOM}entry"
    )
    return [item for node in nodes if (item := _item(node, feed)) is not None]


def _item(node: ET.Element, feed: Feed) -> ContextItem | None:
    title = _clean(_text(node, "title", f"{_ATOM}title"))
    if not title:
        return None

    link = _text(node, "link")
    if not link and (atom_link := node.find(f"{_ATOM}link")) is not None:
        link = atom_link.get("href", "")

    return ContextItem(
        kind="news",
        title=title,
        summary=_truncate(
            _clean(_text(node, "description", f"{_ATOM}summary", f"{_ATOM}content"))
        ),
        publisher=feed.publisher,
        url=link.strip() or None,
        published=_date(_text(node, "pubDate", f"{_ATOM}updated", f"{_ATOM}published")),
    )


def _text(node: ET.Element, *tags: str) -> str:
    for tag in tags:
        child = node.find(tag)
        if child is not None and child.text:
            return child.text
    return ""


def _clean(value: str) -> str:
    """Du texte brut : sans balises, entités décodées, espaces resserrés."""
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", value))).strip()


def _truncate(value: str) -> str:
    if len(value) <= SUMMARY_CHARS:
        return value
    return value[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"


def _date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class FeedReader:
    """Lit les flux, avec un cache en mémoire par flux.

    Le cache sert les rédactions autant que nous : régénérer cinq fois des
    suggestions dans l'heure ne doit pas faire cinq fois le tour des serveurs.
    Un flux en échec n'est pas mis en cache, pour être retenté au prochain
    appel.
    """

    def __init__(
        self,
        feeds: Sequence[Feed] = FEEDS,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._feeds = tuple(feeds)
        self._transport = transport
        self._clock = clock
        self._cache: dict[str, tuple[float, list[ContextItem]]] = {}

    async def read(self, now: datetime) -> tuple[list[ContextItem], list[str]]:
        """Les articles récents, du plus récent au plus ancien, et les échecs."""
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "mass-agents/0.1 (+community suggestions)"},
        ) as client:
            results = await asyncio.gather(
                *(self._read_one(client, feed) for feed in self._feeds)
            )

        items: list[ContextItem] = []
        warnings: list[str] = []
        for feed, result in zip(self._feeds, results, strict=True):
            if isinstance(result, str):
                warnings.append(f"Flux {feed.publisher} indisponible : {result}")
                continue
            recent = [
                item
                for item in result
                if item.published is None or now - item.published <= MAX_AGE
            ]
            items.extend(_newest(recent)[:PER_FEED])

        return _newest(items)[:MAX_NEWS], warnings

    async def _read_one(
        self, client: httpx.AsyncClient, feed: Feed
    ) -> list[ContextItem] | str:
        cached = self._cache.get(feed.url)
        if cached is not None and self._clock() - cached[0] < CACHE_TTL_S:
            return cached[1]

        try:
            response = await client.get(feed.url)
            response.raise_for_status()
            items = parse_feed(response.content, feed)
        except (httpx.HTTPError, ET.ParseError) as error:
            logger.warning("Flux %s en échec : %s", feed.url, error)
            return type(error).__name__

        self._cache[feed.url] = (self._clock(), items)
        return items


def _newest(items: list[ContextItem]) -> list[ContextItem]:
    oldest = datetime.min.replace(tzinfo=UTC)
    return sorted(items, key=lambda item: item.published or oldest, reverse=True)
