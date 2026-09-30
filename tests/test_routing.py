"""Le routage : chaque aiguillage, sur des messages construits à la main.

Aucun modèle, aucun outil : ce qui est vérifié est qu'un geste engageant ne
peut atteindre la validation qu'en passant par l'aperçu, et qu'aucun appel ne
reste sans résultat avant le prochain appel de modèle.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END
from tests.fakes import EMAIL_ARGUMENTS, calling, tool_call

from mass_agents.domain import PendingAction
from mass_agents.graph.routing import (
    after_agent,
    after_preview,
    after_tools,
    pending_engaging_call,
    unanswered_calls,
)

LECTURE = tool_call("list_events", {}, "l1")
COURRIEL = tool_call("send_email", EMAIL_ARGUMENTS, "e1")
POINTAGE = tool_call("mark_attendance", {"event_id": "EVT1"}, "e2")


def _etat(*messages, pending=None):
    return {
        "messages": [HumanMessage(content="…"), *messages],
        "pending_action": pending,
    }


def _resultat(call) -> ToolMessage:
    return ToolMessage(content="{}", tool_call_id=call["id"], name=call["name"])


# -- après l'agent ------------------------------------------------------------


def test_une_reponse_sans_appel_termine_le_run():
    assert after_agent(_etat(AIMessage(content="Il y avait 42 présents."))) == END


def test_une_lecture_part_a_l_execution():
    assert after_agent(_etat(calling(LECTURE))) == "outils"


def test_un_geste_engageant_seul_part_a_l_apercu():
    assert after_agent(_etat(calling(COURRIEL))) == "apercu"


def test_un_geste_engageant_accompagne_passe_d_abord_par_l_execution():
    """Les lectures s'exécutent d'abord ; le geste engageant attend son tour."""
    assert after_agent(_etat(calling(LECTURE, COURRIEL))) == "outils"


def test_deux_gestes_engageants_passent_par_l_execution():
    """C'est elle qui refuse le second : l'aperçu n'en traite qu'un."""
    assert after_agent(_etat(calling(COURRIEL, POINTAGE))) == "outils"


# -- après l'exécution --------------------------------------------------------


def test_apres_les_lectures_la_main_revient_a_l_agent():
    etat = _etat(calling(LECTURE), _resultat(LECTURE))

    assert after_tools(etat) == "agent"


def test_un_geste_engageant_sans_resultat_part_a_l_apercu():
    etat = _etat(calling(LECTURE, COURRIEL), _resultat(LECTURE))

    assert after_tools(etat) == "apercu"


def test_un_geste_engageant_deja_repondu_ne_repart_pas():
    etat = _etat(calling(COURRIEL, POINTAGE), _resultat(COURRIEL), _resultat(POINTAGE))

    assert after_tools(etat) == "agent"


# -- après l'aperçu -----------------------------------------------------------


def test_un_apercu_calcule_part_en_validation():
    action = PendingAction(
        tool_name="send_email",
        arguments=EMAIL_ARGUMENTS,
        preview={"confirmationRequired": True},
        tool_call_id="e1",
    )

    assert after_preview(_etat(calling(COURRIEL), pending=action)) == "validation"


def test_un_apercu_refuse_revient_a_l_agent():
    etat = _etat(calling(COURRIEL), _resultat(COURRIEL))

    assert after_preview(etat) == "agent"


# -- lectures du fil ----------------------------------------------------------


def test_seuls_les_appels_du_dernier_message_comptent():
    """Un appel d'un tour précédent, déjà répondu, ne doit pas ressurgir."""
    ancien = [calling(COURRIEL), _resultat(COURRIEL)]
    messages = [*ancien, HumanMessage(content="et ensuite ?"), calling(LECTURE)]

    assert [c["id"] for c in unanswered_calls(messages)] == ["l1"]
    assert pending_engaging_call(messages) is None


def test_le_geste_engageant_en_attente_est_retrouve():
    messages = [calling(LECTURE, COURRIEL), _resultat(LECTURE)]

    assert pending_engaging_call(messages)["id"] == "e1"
