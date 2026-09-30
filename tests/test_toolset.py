"""La répartition des outils, et ce qu'elle garantit.

Le découpage est fait par niveau d'engagement : ce qui est vérifié ici est
qu'aucun agent ne détient un outil qu'il n'est pas censé pouvoir appeler. C'est
une garantie de construction — un agent ne « choisit » pas de ne pas envoyer de
courriel, il n'en a pas le moyen.
"""

from __future__ import annotations

import pytest

from mass_agents.domain import ENGAGING_TOOLS, ToolsetError
from mass_agents.tools import (
    ANALYST_TOOLS,
    EDITOR_TOOLS,
    OPERATIONS_TOOLS,
    REQUIRED_TOOLS,
    MassToolset,
)
from mass_agents.tools.toolset import _assert_complete


class FauxOutil:
    def __init__(self, name: str) -> None:
        self.name = name


def _outillage(noms) -> MassToolset:
    return MassToolset([FauxOutil(nom) for nom in noms])


def test_l_analyste_n_a_aucune_ecriture():
    ecritures = {
        "create_event_draft",
        "update_draft",
        "create_email_template",
        *ENGAGING_TOOLS,
    }

    assert set(ANALYST_TOOLS) & ecritures == set()


def test_le_redacteur_n_a_aucun_outil_engageant():
    """Il écrit, mais tout reste en brouillon et rien ne sort de l'association."""
    assert set(EDITOR_TOOLS) & ENGAGING_TOOLS == set()


def test_les_operations_detiennent_les_deux_outils_engageants():
    assert set(OPERATIONS_TOOLS) >= ENGAGING_TOOLS


def test_les_operations_peuvent_lire_avant_d_agir():
    """Le recouvrement est voulu : résoudre « samedi » en identifiants `EMR…`
    doit être possible depuis le nœud qui agit."""
    assert "get_event_details" in OPERATIONS_TOOLS
    assert "list_members" in OPERATIONS_TOOLS


def test_le_sous_ensemble_respecte_l_ordre_declare():
    """L'ordre est celui dans lequel le modèle découvre les outils : lectures
    d'abord, écritures ensuite."""
    outillage = _outillage(REQUIRED_TOOLS)

    assert [o.name for o in outillage.subset(EDITOR_TOOLS)] == list(EDITOR_TOOLS)


def test_un_outil_absent_echoue_avec_son_nom():
    outillage = _outillage(["list_events"])

    with pytest.raises(ToolsetError, match="send_email"):
        outillage.get("send_email")


def test_un_serveur_incomplet_est_refuse_au_demarrage():
    """Un décalage de version se voit ici ou pas du tout.

    Sans ce contrôle, l'agent découvrirait au troisième tour qu'il lui manque un
    outil et improviserait avec ce qui reste.
    """
    partiel = REQUIRED_TOOLS - {"query_analytics", "send_email"}

    with pytest.raises(ToolsetError, match="query_analytics"):
        _assert_complete(_outillage(partiel), set(partiel))


def test_un_serveur_complet_passe():
    _assert_complete(_outillage(REQUIRED_TOOLS), set(REQUIRED_TOOLS))
