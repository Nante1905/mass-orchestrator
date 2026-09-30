"""La forme du graphe, et l'authentification de la façade.

Deux vérifications qui ne demandent ni base ni modèle : que le câblage
corresponde à ce que le plan décrit, et que la façade refuse un appel sans jeton
avec l'en-tête qu'un client conforme sait lire.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mass_agents.agents import ANALYST, EDITOR, OPERATIONS
from mass_agents.auth import read_bearer_token
from mass_agents.domain import AdminAuthError
from mass_agents.graph import build_graph


@pytest.fixture(scope="module")
def graphe():
    return build_graph(InMemorySaver())


def test_les_cinq_noeuds_sont_la(graphe):
    noeuds = set(graphe.get_graph().nodes)

    assert {"superviseur", ANALYST, EDITOR, OPERATIONS, "validation"} <= noeuds


def test_le_run_commence_par_le_superviseur(graphe):
    depart = [
        arete.target
        for arete in graphe.get_graph().edges
        if arete.source == "__start__"
    ]

    assert depart == ["superviseur"]


def test_aucun_specialiste_ne_termine_le_run(graphe):
    """C'est le superviseur qui conclut, et lui seul.

    Un spécialiste qui pourrait atteindre `END` rendrait une réponse rédigée
    sans vue sur le reste du fil.
    """
    aretes = graphe.get_graph().edges

    for specialiste in (ANALYST, EDITOR, OPERATIONS):
        cibles = {a.target for a in aretes if a.source == specialiste}
        assert cibles <= {"superviseur", "validation"}


def test_les_ecritures_engageantes_passent_par_la_validation(graphe):
    cibles = {a.target for a in graphe.get_graph().edges if a.source == OPERATIONS}

    assert "validation" in cibles


def test_la_validation_rend_la_main_au_superviseur(graphe):
    cibles = {a.target for a in graphe.get_graph().edges if a.source == "validation"}

    assert cibles == {"superviseur"}


@pytest.mark.parametrize(
    "entete",
    [None, "", "Basic abc", "Bearer", "Bearer "],
)
def test_un_entete_sans_jeton_ne_donne_rien(entete):
    assert read_bearer_token(entete) is None


def test_un_jeton_est_lu_sans_etre_verifie():
    assert read_bearer_token("Bearer  eyJhbGci.abc  ") == "eyJhbGci.abc"


def test_un_401_dit_quoi_faire():
    """Le message d'expiration est actionnable, pas technique.

    Un 401 au milieu d'une conversation doit se lire « reconnectez-vous » et non
    « panne » — sinon l'interface rejoue la même reprise en boucle.
    """
    from mass_agents.auth.identity import _EXPIRED_MESSAGE

    erreur = AdminAuthError(_EXPIRED_MESSAGE, status=401)

    assert erreur.status == 401
    assert "Reconnectez-vous" in erreur.message
    assert "conversation est conservée" in erreur.message
