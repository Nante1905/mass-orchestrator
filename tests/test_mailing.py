"""La rédaction des courriels : ce que le code garantit quoi que le modèle écrive.

Ni réseau ni modèle. L'agenda passe par un outillage simulé, le modèle par un
double qui rend des `EmailDraft` écrits à la main. Ce qui est vérifié : aucun
évènement inventé, aucune variable `{{…}}` laissée au destinataire, et les
informations à compléter relevées dans le texte.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from mass_agents.config import McpConfig
from mass_agents.domain import EmailDraftGenerationError
from mass_agents.mailing.schemas import CurrentDraft, EmailDraft, EmailDraftRequest
from mass_agents.mailing.service import EmailDraftService

MAINTENANT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
MCP = McpConfig(url="http://mcp.test/mcp", timeout_s=1.0)

SOIREE_MEMBRES = {
    "id": "E1",
    "title": "Soirée lunaire",
    "location": "Ambohidempona",
    "dateEvent": "2026-10-17T15:00:00.000Z",
    "isPublic": False,
    "tickets": [{"price": 0}],
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
        assert name == "list_events"
        return self.outil


def _agenda(outil: _Outil):
    async def fabrique(token: str, mcp: McpConfig) -> _Outillage:
        assert token == "jeton"
        return _Outillage(outil)

    return fabrique


async def _panne(token: str, mcp: McpConfig):
    raise ConnectionError("injoignable")


class _Structure:
    def __init__(self, modele: _Modele) -> None:
        self.modele = modele

    async def ainvoke(self, messages):
        self.modele.received.append(list(messages))
        return self.modele.reponses.pop(0)


class _Modele:
    def __init__(self, *reponses: EmailDraft) -> None:
        self.reponses = list(reponses)
        self.received: list[list[Any]] = []
        self.options: dict[str, Any] = {}

    def with_structured_output(self, schema, **options):
        assert schema is EmailDraft
        self.options = options
        return _Structure(self)


def _brouillon(
    subject: str = "Soirée lunaire le 17 octobre",
    body: str = "Bonjour,\n\nRendez-vous le **17/10**.\n\nL'équipe MASS",
    events: tuple[str, ...] = ("e1",),
) -> EmailDraft:
    return EmailDraft(subject=subject, body=body, event_ids=list(events))


def _service(modele: _Modele, *, toolset=None) -> EmailDraftService:
    outil = _Outil(json.dumps({"events": [SOIREE_MEMBRES]}))
    return EmailDraftService(
        MCP,
        timeout_s=5.0,
        model_factory=lambda: modele,  # type: ignore[arg-type,return-value]
        toolset_factory=toolset or _agenda(outil),
        clock=lambda: MAINTENANT,
    )


DEMANDE = EmailDraftRequest(brief="Inviter les membres à la soirée lunaire.")


async def test_les_evenements_cites_sont_resolus():
    modele = _Modele(_brouillon())

    resultat = await _service(modele).draft(DEMANDE, "jeton")

    assert resultat.subject == "Soirée lunaire le 17 octobre"
    assert [e.title for e in resultat.events] == ["Soirée lunaire"]
    assert resultat.events[0].date == "2026-10-17"
    assert resultat.missing == []
    assert resultat.warnings == []
    assert modele.options == {"method": "json_schema"}


async def test_l_agenda_lit_aussi_les_soirees_reservees_aux_membres():
    """Un courriel aux adhérents peut annoncer ce qu'un post ne montrerait pas ;
    le modèle doit alors savoir que l'évènement est réservé."""
    outil = _Outil(json.dumps({"events": [SOIREE_MEMBRES]}))
    modele = _Modele(_brouillon())

    await _service(modele, toolset=_agenda(outil)).draft(DEMANDE, "jeton")

    assert outil.args is not None
    assert "is_public" not in outil.args
    assert outil.args["status"] == "published"
    contexte = modele.received[0][1].content
    assert "[e1] Soirée lunaire" in contexte
    assert "Réservé aux membres." in contexte


async def test_un_agenda_injoignable_n_empeche_pas_la_redaction():
    modele = _Modele(_brouillon(events=()))

    resultat = await _service(modele, toolset=_panne).draft(DEMANDE, "jeton")

    assert resultat.events == []
    assert resultat.warnings == ["L'agenda MASS est indisponible"]
    assert "Aucun évènement à venir" in modele.received[0][1].content


async def test_les_informations_manquantes_sont_relevees_dans_le_texte():
    modele = _Modele(
        _brouillon(
            body=(
                "Bonjour,\n\nRendez-vous à [à compléter : heure du rendez-vous] "
                "devant [à compléter : lieu exact]. Rappel : "
                "[à compléter : heure du rendez-vous].\n\nL'équipe MASS"
            )
        )
    )

    resultat = await _service(modele).draft(DEMANDE, "jeton")

    assert resultat.missing == ["heure du rendez-vous", "lieu exact"]


async def test_un_evenement_invente_est_refuse_puis_corrige():
    modele = _Modele(_brouillon(events=("e1", "e9")), _brouillon())

    resultat = await _service(modele).draft(DEMANDE, "jeton")

    assert [e.title for e in resultat.events] == ["Soirée lunaire"]
    relance = modele.received[1][-1].content
    assert "évènements inconnus e9" in relance


async def test_une_variable_de_gabarit_est_refusee():
    """Le même texte part à tous : `{{prenom}}` arriverait tel quel."""
    fautif = _brouillon(body="Bonjour {{prenom}},\n\nÀ bientôt.")
    modele = _Modele(fautif, fautif)

    with pytest.raises(EmailDraftGenerationError, match="variable"):
        await _service(modele).draft(DEMANDE, "jeton")


async def test_un_objet_trop_long_pour_sa_colonne_est_refuse():
    trop_long = _brouillon(subject="x" * 251)
    modele = _Modele(trop_long, trop_long)

    with pytest.raises(EmailDraftGenerationError, match="250"):
        await _service(modele).draft(DEMANDE, "jeton")


async def test_le_brouillon_a_reprendre_est_transmis():
    modele = _Modele(_brouillon())
    demande = EmailDraftRequest(
        brief="Raccourcir et rendre plus formel.",
        tone="formel",
        current=CurrentDraft(subject="Ancien objet", body="Ancien corps"),
    )

    await _service(modele).draft(demande, "jeton")

    contexte = modele.received[0][1].content
    assert "## Brouillon à reprendre" in contexte
    assert "Objet : Ancien objet" in contexte
    assert "Ton : formel." in contexte


async def test_un_brouillon_vide_n_est_pas_transmis():
    """Le formulaire envoie ses champs même vides : ce n'est pas une reprise."""
    modele = _Modele(_brouillon())
    demande = EmailDraftRequest(brief=DEMANDE.brief, current=CurrentDraft())

    await _service(modele).draft(demande, "jeton")

    assert "Brouillon à reprendre" not in modele.received[0][1].content


def test_une_consigne_trop_courte_est_refusee():
    with pytest.raises(ValueError):
        EmailDraftRequest(brief="Salut")
