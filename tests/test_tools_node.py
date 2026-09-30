"""L'exécution des lectures et des brouillons.

Deux garanties : un geste engageant ne s'exécute jamais ici, et chaque appel
reçoit un résultat — l'échec compris, le refus compris. Un `tool_use` sans
`tool_result` fait rejeter la requête suivante par l'API, et l'erreur se
manifeste un tour plus tard, loin de sa cause.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage
from tests.fakes import (
    EMAIL_ARGUMENTS,
    BrokenTool,
    FakeTool,
    calling,
    run_config,
    tool_call,
)

from mass_agents.graph.nodes.tools import tools_node

LECTURE = tool_call("list_events", {"page": 1}, "l1")
FICHE = tool_call("get_event_details", {"event_id": "EVT1"}, "l2")
COURRIEL = tool_call("send_email", EMAIL_ARGUMENTS, "e1")
POINTAGE = tool_call("mark_attendance", {"event_id": "EVT1"}, "e2")


def _etat(appel):
    return {"messages": [HumanMessage(content="…"), appel]}


def _par_appel(update) -> dict[str, ToolMessage]:
    return {m.tool_call_id: m for m in update["messages"]}


async def test_les_lectures_sont_executees_et_repondues():
    agenda = FakeTool("list_events", {"total": 3})
    fiche = FakeTool("get_event_details", {"id": "EVT1"})
    config = run_config({"list_events": agenda, "get_event_details": fiche})

    update = await tools_node(_etat(calling(LECTURE, FICHE)), config)

    assert set(_par_appel(update)) == {"l1", "l2"}
    assert agenda.calls == [{"page": 1}]
    assert fiche.calls == [{"event_id": "EVT1"}]


async def test_un_geste_engageant_n_est_jamais_execute_ici(send_email_tool):
    """Il reste sans réponse, pour le nœud d'aperçu."""
    agenda = FakeTool("list_events", {"total": 3})
    config = run_config({"list_events": agenda, "send_email": send_email_tool})

    update = await tools_node(_etat(calling(LECTURE, COURRIEL)), config)

    assert send_email_tool.calls == []
    assert set(_par_appel(update)) == {"l1"}


async def test_un_second_geste_engageant_est_refuse(send_email_tool):
    """Deux aperçus empilés feraient approuver le second sans l'avoir lu."""
    pointage = FakeTool("mark_attendance", {})
    config = run_config({"send_email": send_email_tool, "mark_attendance": pointage})

    update = await tools_node(_etat(calling(COURRIEL, POINTAGE)), config)

    reponses = _par_appel(update)
    assert set(reponses) == {"e2"}
    assert reponses["e2"].status == "error"
    assert "un seul geste engageant" in reponses["e2"].content
    assert send_email_tool.calls == [] and pointage.calls == []


async def test_un_echec_d_outil_est_rendu_au_modele():
    """Levé, il interromprait le run ; rendu, le modèle sait le lire — un
    identifiant inconnu se corrige, un jeton expiré se dit à l'utilisateur."""
    config = run_config({"list_events": BrokenTool("list_events", "jeton expiré")})

    update = await tools_node(_etat(calling(LECTURE)), config)

    reponse = _par_appel(update)["l1"]
    assert reponse.status == "error"
    assert "jeton expiré" in reponse.content


async def test_un_outil_inconnu_recoit_une_erreur():
    update = await tools_node(_etat(calling(LECTURE)), run_config({}))

    assert _par_appel(update)["l1"].status == "error"
