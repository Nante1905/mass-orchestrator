"""La forme du graphe, et l'authentification de la façade.

Deux vérifications qui ne demandent ni base ni modèle : que le câblage
corresponde à ce que `builder.py` décrit, et que la façade refuse un appel sans
jeton avec l'en-tête qu'un client conforme sait lire.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mass_agents.auth import read_bearer_token
from mass_agents.domain import AdminAuthError
from mass_agents.graph import build_graph


@pytest.fixture(scope="module")
def graphe():
    return build_graph(InMemorySaver())


def _cibles(graphe, source: str) -> set[str]:
    return {a.target for a in graphe.get_graph().edges if a.source == source}


def test_les_quatre_noeuds_sont_la(graphe):
    noeuds = set(graphe.get_graph().nodes)

    assert {"agent", "outils", "apercu", "validation"} <= noeuds


def test_le_run_commence_par_l_agent(graphe):
    assert _cibles(graphe, "__start__") == {"agent"}


def test_seul_l_agent_termine_le_run(graphe):
    """La réponse finale est un message de l'agent, rédigé en connaissance de
    tout ce que les outils ont rendu."""
    sources_de_fin = {
        a.source for a in graphe.get_graph().edges if a.target == "__end__"
    }

    assert sources_de_fin == {"agent"}


def test_l_execution_des_outils_ne_mene_pas_a_la_validation(graphe):
    """Un geste engageant passe toujours par l'aperçu avant la validation :
    c'est là que `confirmed` est forcé à faux."""
    assert _cibles(graphe, "outils") == {"agent", "apercu"}
    assert _cibles(graphe, "agent") == {"outils", "apercu", "__end__"}


def test_seul_l_apercu_mene_a_la_validation(graphe):
    sources = {
        a.source for a in graphe.get_graph().edges if a.target == "validation"
    }

    assert sources == {"apercu"}


def test_la_validation_rend_la_main_a_l_agent(graphe):
    assert _cibles(graphe, "validation") == {"agent"}


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
