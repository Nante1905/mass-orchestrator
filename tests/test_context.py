"""Le jeton d'administrateur ne doit jamais atteindre les checkpoints.

C'est le risque le plus sérieux identifié au plan, et il ne se vérifie pas en
relisant la documentation de LangGraph : ce test appelle la vraie fonction qui
construit les métadonnées persistées, et regarde ce qu'elle en fait.

Il vaut aussi comme test de non-régression sur LangGraph lui-même. Si une montée
de version changeait la règle d'exclusion, c'est ici qu'on l'apprendrait — et
non en trouvant des jetons en clair dans une table.
"""

from __future__ import annotations

from langgraph.checkpoint.base import get_checkpoint_metadata

from mass_agents.auth import AdminIdentity
from mass_agents.graph.context import (
    RUN_CONTEXT_KEY,
    RunContext,
    build_run_config,
    run_context,
)

ADMIN = AdminIdentity(
    user_id="USR0001", email="admin@example.org", administrator_id="ADM1"
)
TOKEN = "eyJhbGciOiJIUzI1NiJ9.charge-utile.signature"


def _context() -> RunContext:
    return RunContext(admin=ADMIN, toolset=object(), approvals=object())


def test_le_contexte_de_run_ne_fuit_pas_dans_les_metadonnees():
    config = build_run_config("fil-1", _context())

    metadata = get_checkpoint_metadata(config, {"source": "loop", "step": 1})

    assert RUN_CONTEXT_KEY not in metadata
    assert TOKEN not in repr(metadata)
    # Le fil, lui, doit rester lisible : c'est ce qui permet de retrouver un
    # checkpoint. Son exclusion des métadonnées est le fait de LangGraph, qui le
    # range ailleurs.
    assert metadata["source"] == "loop"


def test_un_jeton_place_naivement_fuiterait():
    """La démonstration du danger, pour que la parade ne passe pas pour du zèle.

    Ce test échouerait — au sens où le jeton serait présent — si l'on rangeait
    le jeton sous une clé ordinaire. Il documente donc pourquoi `RUN_CONTEXT_KEY`
    commence par deux tirets bas et pourquoi la valeur n'est pas une chaîne.
    """
    naif = {"configurable": {"thread_id": "fil-1", "admin_token": TOKEN}}

    metadata = get_checkpoint_metadata(naif, {"source": "loop", "step": 1})

    assert metadata["admin_token"] == TOKEN


def test_une_valeur_non_scalaire_ne_fuit_pas_meme_sans_prefixe():
    """La seconde parade, indépendante de la convention de nommage.

    Seuls `str`, `int`, `bool` et `float` sont recopiés. Un objet ne l'est pas,
    quel que soit le nom de sa clé — c'est ce qui fait tenir la garantie même si
    LangGraph changeait un jour sa liste d'exclusions.
    """
    sans_prefixe = {"configurable": {"thread_id": "fil-1", "contexte": _context()}}

    metadata = get_checkpoint_metadata(sans_prefixe, {"source": "loop", "step": 1})

    assert "contexte" not in metadata


def test_le_contexte_est_relu_par_les_noeuds():
    config = build_run_config("fil-1", _context())

    assert run_context(config).admin == ADMIN


def test_un_noeud_invoque_hors_run_echoue_bruyamment():
    """Pas de repli silencieux : un agent sans outillage ne doit pas démarrer."""
    import pytest

    with pytest.raises(RuntimeError, match="Contexte de run absent"):
        run_context({"configurable": {"thread_id": "fil-1"}})
