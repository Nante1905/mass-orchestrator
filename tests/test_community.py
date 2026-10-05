"""Les suggestions de posts : lecture des sources, et vérification de la sortie.

Ni réseau ni modèle. Les flux passent par un transport `httpx` simulé, le
modèle par un double qui rend des `Suggestions` écrites à la main. Ce qui est
vérifié, c'est ce que le code garantit quoi que le modèle écrive : le nombre de
posts, les catégories, et qu'aucune source citée ne soit inventée.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from mass_agents.community import agenda
from mass_agents.community.place import LATITUDE
from mass_agents.community.schemas import (
    Post,
    SuggestionRequest,
    Suggestions,
    Visual,
)
from mass_agents.community.service import CommunityService
from mass_agents.community.sky import METEOR_SHOWERS, upcoming_sky_events
from mass_agents.community.sources import ContextItem, Feed, FeedReader, parse_feed
from mass_agents.config import McpConfig
from mass_agents.domain import SuggestionGenerationError, SuggestionRequestError

MAINTENANT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
MCP = McpConfig(url="http://mcp.test/mcp", timeout_s=1.0)
FLUX = Feed("Ciel & Espace", "https://flux.test/rss", "fr")

RSS = (
    b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Un ovale d&#8217;aurores</title>
    <link>https://flux.test/aurores</link>
    <description>&lt;p&gt;L&amp;rsquo;ESA a """
    b"""d&amp;eacute;voil&amp;eacute; des images.&lt;/p&gt;</description>
    <pubDate>Thu, 01 Oct 2026 12:28:00 +0200</pubDate>
  </item>
  <item>
    <title>Vieille nouvelle</title>
    <link>https://flux.test/vieille</link>
    <description>Il y a longtemps.</description>
    <pubDate>Mon, 01 Jun 2026 12:00:00 +0000</pubDate>
  </item>
</channel></rss>"""
)

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Webb voit loin</title>
    <link href="https://flux.test/webb"/>
    <summary>Une galaxie lointaine.</summary>
    <updated>2026-10-03T10:00:00Z</updated>
  </entry>
</feed>"""


# --- Les flux -------------------------------------------------------------------


def test_un_article_rss_est_rendu_en_texte_brut():
    """Les résumés arrivent en HTML échappé : ni balise ni entité ne doivent
    atteindre le modèle, elles coûtent des jetons et ne disent rien."""
    article = parse_feed(RSS, FLUX)[0]

    assert article.title == "Un ovale d\u2019aurores"
    assert article.summary == "L\u2019ESA a dévoilé des images."
    assert article.url == "https://flux.test/aurores"
    assert article.published == datetime(2026, 10, 1, 10, 28, tzinfo=UTC)
    assert article.publisher == "Ciel & Espace"


def test_un_flux_atom_est_lu_aussi():
    article = parse_feed(ATOM, FLUX)[0]

    assert article.title == "Webb voit loin"
    assert article.url == "https://flux.test/webb"
    assert article.published == datetime(2026, 10, 3, 10, 0, tzinfo=UTC)


def _transport(appels: list[str], reponses: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        appels.append(str(request.url))
        return reponses[str(request.url)]

    return httpx.MockTransport(handler)


async def test_les_articles_trop_anciens_sont_ecartes():
    lecteur = FeedReader(
        [FLUX], transport=_transport([], {FLUX.url: httpx.Response(200, content=RSS)})
    )

    articles, avertissements = await lecteur.read(MAINTENANT)

    assert [a.title for a in articles] == ["Un ovale d\u2019aurores"]
    assert avertissements == []


async def test_un_flux_en_panne_devient_un_avertissement():
    """Une rédaction injoignable ne prive pas la page des autres."""
    autre = Feed("ESO", "https://autre.test/rss", "en")
    lecteur = FeedReader(
        [FLUX, autre],
        transport=_transport(
            [],
            {
                FLUX.url: httpx.Response(503),
                autre.url: httpx.Response(200, content=ATOM),
            },
        ),
    )

    articles, avertissements = await lecteur.read(MAINTENANT)

    assert [a.title for a in articles] == ["Webb voit loin"]
    assert avertissements == ["Flux Ciel & Espace indisponible : HTTPStatusError"]


async def test_un_flux_lu_est_servi_depuis_le_cache_pendant_une_heure():
    appels: list[str] = []
    horloge = [0.0]
    lecteur = FeedReader(
        [FLUX],
        transport=_transport(appels, {FLUX.url: httpx.Response(200, content=RSS)}),
        clock=lambda: horloge[0],
    )

    await lecteur.read(MAINTENANT)
    horloge[0] = 3_000.0
    await lecteur.read(MAINTENANT)
    assert len(appels) == 1

    horloge[0] = 3_700.0
    await lecteur.read(MAINTENANT)
    assert len(appels) == 2


# --- Le ciel --------------------------------------------------------------------


def test_une_pluie_invisible_depuis_l_hemisphere_sud_n_est_pas_proposee():
    """Le radiant des Ursides ne se lève jamais à Antananarivo."""
    ursides = next(s for s in METEOR_SHOWERS if s.name == "Ursides")
    assert ursides.max_altitude(LATITUDE) < 0

    titres = [e.title for e in upcoming_sky_events(date(2026, 12, 1))]
    assert any("Géminides" in t for t in titres)
    assert not any("Ursides" in t for t in titres)


def test_la_visibilite_locale_est_dite():
    orionides = next(
        e for e in upcoming_sky_events(date(2026, 10, 5)) if "Orionides" in e.title
    )

    assert orionides.title == "Pic des Orionides vers le 21/10"
    assert "Antananarivo" in orionides.summary
    assert "bonnes conditions" in orionides.summary


def test_un_radiant_bas_est_annonce_comme_mediocre():
    draconides = next(
        e for e in upcoming_sky_events(date(2026, 10, 1)) if "Draconides" in e.title
    )
    assert "médiocres" in draconides.summary


def test_le_ciel_passe_le_nouvel_an():
    titres = [e.title for e in upcoming_sky_events(date(2026, 12, 30))]
    assert "Pic des Quadrantides vers le 3/01" in titres


# --- L'agenda -------------------------------------------------------------------


class _Outil:
    def __init__(self, resultat: Any) -> None:
        self.resultat = resultat
        self.args: dict[str, Any] | None = None

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        self.args = args
        return self.resultat


class _Outillage:
    def __init__(self, outil: _Outil) -> None:
        self.outil = outil

    def get(self, name: str) -> _Outil:
        assert name == "list_events"
        return self.outil


def _fabrique(outil: _Outil):
    async def fabrique(token: str, mcp: McpConfig) -> _Outillage:
        assert token == "jeton"
        return _Outillage(outil)

    return fabrique


async def _panne(token: str, mcp: McpConfig):
    raise ConnectionError("injoignable")


EVENEMENT = {
    "id": "E1",
    "title": "Nuit des étoiles",
    "location": "Tsimbazaza",
    "dateEvent": "2026-10-10T15:00:00.000Z",
    "tickets": [{"price": 20000}, {"price": 5000}],
}


async def test_l_agenda_ne_lit_que_le_public_publie_a_venir():
    outil = _Outil(json.dumps({"events": [EVENEMENT]}))

    evenements, avertissement = await agenda.upcoming_mass_events(
        "jeton", MCP, _fabrique(outil)
    )

    assert outil.args is not None
    assert outil.args["period"] == "upcoming"
    assert outil.args["status"] == "published"
    assert outil.args["is_public"] is True
    assert avertissement is None
    assert evenements[0].title == "Nuit des étoiles"
    # 15h UTC, 18h à Madagascar ; le tarif le plus bas, au format du back-office.
    assert evenements[0].summary == (
        "Le 10/10/2026 à 18h00, Tsimbazaza. Entrée : à partir de 5 000 Ar."
    )


async def test_un_agenda_injoignable_n_est_pas_une_erreur():
    evenements, avertissement = await agenda.upcoming_mass_events(
        "jeton", MCP, _panne
    )
    assert evenements == []
    assert avertissement == "L'agenda MASS est indisponible"


# --- Le service -----------------------------------------------------------------


class _Lecteur:
    def __init__(self, articles: list[ContextItem]) -> None:
        self.articles = articles

    async def read(self, now: datetime):
        return self.articles, []


class _Structure:
    def __init__(self, modele: _Modele) -> None:
        self.modele = modele

    async def ainvoke(self, messages):
        self.modele.received.append(list(messages))
        return self.modele.reponses.pop(0)


class _Modele:
    def __init__(self, *reponses: Suggestions) -> None:
        self.reponses = list(reponses)
        self.received: list[list[Any]] = []
        self.options: dict[str, Any] = {}

    def with_structured_output(self, schema, **options):
        assert schema is Suggestions
        self.options = options
        return _Structure(self)


ARTICLE = ContextItem(
    kind="news",
    title="Webb voit loin",
    summary="Une galaxie lointaine.",
    publisher="ESA",
    url="https://flux.test/webb",
    published=MAINTENANT - timedelta(days=1),
)


def _post(category: str = "decouverte", sources: tuple[str, ...] = ("n1",)) -> Post:
    return Post(
        category=category,  # type: ignore[arg-type]
        hook="Jusqu'où voit Webb ?",
        text="…",
        hashtags=["JWST", "#MASS", "#MASS", " astro "],
        visual=Visual(
            format="carre_1_1",
            concept="Image de la galaxie",
            overlay_text="",
            style="sombre",
            slides=[],
        ),
        source_ids=list(sources),
    )


def _service(modele: _Modele, *, toolset=_panne) -> CommunityService:
    return CommunityService(
        _Lecteur([ARTICLE]),  # type: ignore[arg-type]
        MCP,
        timeout_s=5.0,
        model_factory=lambda: modele,  # type: ignore[arg-type,return-value]
        toolset_factory=toolset,
        clock=lambda: MAINTENANT,
    )


async def test_les_sources_citees_sont_resolues_et_les_hashtags_nettoyes():
    modele = _Modele(Suggestions(posts=[_post()]))

    resultat = await _service(modele).suggest(
        SuggestionRequest(count=1, categories=["decouverte"]), "jeton"
    )

    post = resultat.posts[0]
    assert post.sources[0].url == "https://flux.test/webb"
    assert post.sources[0].date == "2026-10-04"
    assert post.hashtags == ["#JWST", "#MASS", "#astro"]
    # La sortie structurée native : la forme forcée d'un outil est refusée par
    # les modèles Claude récents.
    assert modele.options == {"method": "json_schema"}
    contexte = modele.received[0][1].content
    assert "[n1] Webb voit loin" in contexte


async def test_une_source_inventee_fait_reessayer_avec_le_motif():
    modele = _Modele(
        Suggestions(posts=[_post(sources=("n9",))]),
        Suggestions(posts=[_post()]),
    )

    resultat = await _service(modele).suggest(
        SuggestionRequest(count=1, categories=["decouverte"]), "jeton"
    )

    assert len(resultat.posts) == 1
    relance = modele.received[1][-1].content
    assert "sources inconnues n9" in relance


async def test_deux_echecs_rendent_une_erreur_de_generation():
    modele = _Modele(
        Suggestions(posts=[_post(), _post()]),
        Suggestions(posts=[_post(category="observation")]),
    )

    with pytest.raises(SuggestionGenerationError, match="non demandée"):
        await _service(modele).suggest(
            SuggestionRequest(count=1, categories=["decouverte"]), "jeton"
        )


async def test_l_agenda_n_est_pas_lu_quand_les_evenements_ne_sont_pas_demandes():
    async def interdit(token: str, mcp: McpConfig):
        raise AssertionError("mass-mcp ne devait pas être appelé")

    modele = _Modele(Suggestions(posts=[_post()]))
    resultat = await _service(modele, toolset=interdit).suggest(
        SuggestionRequest(count=1, categories=["decouverte"]), "jeton"
    )
    assert resultat.warnings == []


async def test_seulement_des_evenements_sans_agenda_est_refuse_avant_le_modele():
    modele = _Modele()

    with pytest.raises(SuggestionRequestError, match="agenda MASS est indisponible"):
        await _service(modele).suggest(
            SuggestionRequest(count=1, categories=["evenement_mass"]), "jeton"
        )
    assert modele.received == []


async def test_un_agenda_en_panne_retire_la_categorie_et_le_dit():
    modele = _Modele(Suggestions(posts=[_post()]))

    resultat = await _service(modele).suggest(
        SuggestionRequest(count=1, categories=["evenement_mass", "decouverte"]),
        "jeton",
    )

    assert resultat.warnings == [
        "L'agenda MASS est indisponible : la catégorie « Évènement MASS » est "
        "ignorée."
    ]
    contexte = modele.received[0][1].content
    assert "evenement_mass" not in contexte


async def test_un_post_evenement_doit_citer_un_evenement():
    outil = _Outil(json.dumps({"events": [EVENEMENT]}))
    modele = _Modele(
        Suggestions(posts=[_post(category="evenement_mass", sources=("n1",))]),
        Suggestions(posts=[_post(category="evenement_mass", sources=("e1", "n1"))]),
    )

    resultat = await _service(modele, toolset=_fabrique(outil)).suggest(
        SuggestionRequest(count=1, categories=["evenement_mass"]), "jeton"
    )

    assert "évènement MASS sans élément" in modele.received[1][-1].content
    assert [s.publisher for s in resultat.posts[0].sources] == ["MASS", "ESA"]


def test_la_demande_borne_le_nombre_et_dedoublonne_les_categories():
    demande = SuggestionRequest(count=3, categories=["observation", "observation"])
    assert demande.categories == ["observation"]

    with pytest.raises(ValueError):
        SuggestionRequest(count=11)
