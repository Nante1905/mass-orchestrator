"""L'analyse des avis : ce que le code garantit quoi que le modèle écrive.

Ni réseau ni modèle. Les avis passent par un outillage simulé, le modèle par un
double qui rend des `FeedbackAnalysis` écrites à la main. Ce qui est vérifié :
chaque avis reçoit un ton et un seul, aucun thème ne cite d'avis inventé, les
chiffres sont comptés par le code, et les noms des auteurs ne partent pas au
modèle.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mass_agents.config import McpConfig
from mass_agents.domain import (
    FeedbackAnalysisGenerationError,
    FeedbackAnalysisRequestError,
    ToolsetError,
)
from mass_agents.feedbacks.schemas import (
    FeedbackAnalysis,
    FeedbackSentiment,
    Theme,
)
from mass_agents.feedbacks.service import FeedbackAnalysisService

MCP = McpConfig(url="http://mcp.test/mcp", timeout_s=1.0)

AVIS = [
    {
        "id": "EFB0001",
        "audience": "member",
        "author": "Hanta Rakotoarisoa",
        "rating": 4,
        "content": "Soirée réussie, attente courte aux télescopes.",
    },
    {
        "id": "EFB0002",
        "audience": "member",
        "author": "Tojo Randrianasolo",
        "rating": 2,
        "content": "Beaucoup de monde pour peu de télescopes après 21 h.",
    },
    {
        "id": "EFB0003",
        "audience": "public",
        "author": "Soa Randria",
        "rating": None,
        "content": "Bénévoles patients, mais le fléchage était invisible.",
    },
]


def _lecture(records=AVIS, total: int | None = None) -> dict[str, Any]:
    return {
        "event": {
            "id": "EVE0001",
            "title": "Nuit des étoiles 2025",
            "location": "Ambohidempona",
            "dateEvent": "2025-11-08T16:00:00.000Z",
            "status": "published",
        },
        "ratings": {
            "total": 3,
            "rated": 2,
            "averageRating": 3,
            "distribution": {"1": 0, "2": 1, "3": 0, "4": 1, "5": 0},
        },
        "feedbacks": {
            "total": len(records) if total is None else total,
            "reported": len(records),
            "records": list(records),
        },
    }


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
        assert name == "get_event_feedbacks"
        return self.outil


def _avis(outil: _Outil):
    async def fabrique(token: str, mcp: McpConfig) -> _Outillage:
        assert token == "jeton"
        return _Outillage(outil)

    return fabrique


class _Structure:
    def __init__(self, modele: _Modele) -> None:
        self.modele = modele

    async def ainvoke(self, messages):
        self.modele.received.append(list(messages))
        return self.modele.reponses.pop(0)


class _Modele:
    def __init__(self, *reponses: FeedbackAnalysis) -> None:
        self.reponses = list(reponses)
        self.received: list[list[Any]] = []

    def with_structured_output(self, schema, **options):
        assert schema is FeedbackAnalysis
        assert options == {"method": "json_schema"}
        return _Structure(self)


def _analyse(
    tons: dict[str, str] | None = None,
    themes: list[Theme] | None = None,
) -> FeedbackAnalysis:
    tons = tons or {"f1": "positif", "f2": "negatif", "f3": "neutre"}
    return FeedbackAnalysis(
        summary="Une soirée appréciée, limitée par le manque de télescopes.",
        sentiments=[
            FeedbackSentiment(feedback_id=fid, sentiment=ton)  # type: ignore[arg-type]
            for fid, ton in tons.items()
        ],
        themes=themes
        or [
            Theme(
                label="Signalétique",
                sentiment="negatif",
                summary="Le fléchage depuis l'entrée était invisible.",
                feedback_ids=["f3"],
            ),
            Theme(
                label="Télescopes",
                sentiment="negatif",
                summary="Trop peu de postes après 21 h.",
                feedback_ids=["f2", "f1", "f2"],
            ),
        ],
        strengths=["Des bénévoles patients ", " "],
        improvements=["Prévoir un quatrième télescope après 21 h."],
    )


def _service(modele: _Modele, outil: _Outil) -> FeedbackAnalysisService:
    return FeedbackAnalysisService(
        MCP,
        timeout_s=5.0,
        model_factory=lambda: modele,  # type: ignore[arg-type,return-value]
        toolset_factory=_avis(outil),
    )


async def test_les_chiffres_sont_comptes_par_le_code():
    outil = _Outil(json.dumps(_lecture()))
    modele = _Modele(_analyse())

    resultat = await _service(modele, outil).analyse("EVE0001", "jeton")

    assert outil.args == {"event_id": "EVE0001", "limit": 200}
    assert resultat.analysed == 3
    assert resultat.total == 3
    assert resultat.sentiment.model_dump() == {"positif": 1, "neutre": 1, "negatif": 1}
    # Classés par mentions, doublons écartés : « Télescopes » cite f2 et f1.
    assert [t.label for t in resultat.themes] == ["Télescopes", "Signalétique"]
    assert resultat.themes[0].mentions == 2
    # L'extrait d'abord choisi a le ton du thème : f2, négatif.
    assert resultat.themes[0].examples[0].text.startswith("Beaucoup de monde")
    assert resultat.themes[0].examples[0].rating == 2
    assert resultat.strengths == ["Des bénévoles patients"]
    assert resultat.event.date == "2025-11-08"
    assert resultat.ratings is not None
    assert resultat.ratings.average_rating == 3
    assert resultat.warnings == []


async def test_les_noms_des_auteurs_ne_partent_pas_au_modele():
    outil = _Outil(json.dumps(_lecture()))
    modele = _Modele(_analyse())

    await _service(modele, outil).analyse("EVE0001", "jeton")

    contexte = modele.received[0][1].content
    assert "Hanta" not in contexte
    assert "Randrianasolo" not in contexte
    assert "[f1] 4/5 · membre" in contexte
    assert "[f3] sans note · visiteur" in contexte
    assert "Note moyenne : 3,0/5 sur 2 avis notés" in contexte


async def test_un_avis_oublie_est_redemande_puis_refuse():
    outil = _Outil(json.dumps(_lecture()))
    incomplete = _analyse(tons={"f1": "positif", "f2": "negatif"})
    modele = _Modele(incomplete, incomplete)

    with pytest.raises(FeedbackAnalysisGenerationError, match="avis sans ton : f3"):
        await _service(modele, outil).analyse("EVE0001", "jeton")

    assert len(modele.received) == 2
    assert "avis sans ton : f3" in modele.received[1][-1].content


async def test_un_theme_qui_cite_un_avis_invente_est_corrige():
    outil = _Outil(json.dumps(_lecture()))
    inventee = _analyse(
        themes=[
            Theme(
                label="Buffet",
                sentiment="positif",
                summary="Le thé a plu.",
                feedback_ids=["f9"],
            )
        ]
    )
    modele = _Modele(inventee, _analyse())

    resultat = await _service(modele, outil).analyse("EVE0001", "jeton")

    assert "avis inconnus f9" in modele.received[1][-1].content
    assert [t.label for t in resultat.themes] == ["Télescopes", "Signalétique"]


async def test_sans_avis_rien_n_est_demande_au_modele():
    outil = _Outil(json.dumps(_lecture(records=[])))
    modele = _Modele()

    with pytest.raises(FeedbackAnalysisRequestError) as error:
        await _service(modele, outil).analyse("EVE0001", "jeton")

    assert error.value.status == 409
    assert modele.received == []


async def test_un_evenement_inconnu_rend_le_404_de_l_api():
    outil = _Outil(json.dumps({"error": "Événement introuvable", "status": 404}))

    with pytest.raises(FeedbackAnalysisRequestError) as error:
        await _service(_Modele(), outil).analyse("EVE9999", "jeton")

    assert error.value.status == 404
    assert str(error.value) == "Événement introuvable"


async def test_un_serveur_mcp_injoignable_est_une_panne_de_dependance():
    async def panne(token: str, mcp: McpConfig):
        raise ConnectionError("injoignable")

    service = FeedbackAnalysisService(
        MCP,
        timeout_s=5.0,
        model_factory=lambda: _Modele(),  # type: ignore[arg-type,return-value]
        toolset_factory=panne,
    )

    with pytest.raises(ToolsetError):
        await service.analyse("EVE0001", "jeton")


async def test_la_portee_de_l_analyse_est_signalee():
    peu = AVIS[:2]
    outil = _Outil(json.dumps(_lecture(records=peu, total=250)))
    modele = _Modele(
        _analyse(
            tons={"f1": "positif", "f2": "negatif"},
            themes=[
                Theme(
                    label="Télescopes",
                    sentiment="negatif",
                    summary="Trop peu de postes.",
                    feedback_ids=["f2"],
                )
            ],
        )
    )

    resultat = await _service(modele, outil).analyse("EVE0001", "jeton")

    assert resultat.total == 250
    assert resultat.warnings == [
        "Seuls les 2 avis les plus récents, sur 250, ont été analysés.",
        "2 avis seulement : la synthèse est indicative.",
    ]
