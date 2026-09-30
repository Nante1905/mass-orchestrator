"""L'aperçu d'un geste engageant, et la garantie qu'il porte.

Le test central est le premier : un `confirmed: true` posé par le modèle — par
erreur, ou parce qu'un texte injecté dans les données le lui a suggéré — ne
doit jamais atteindre `mass-mcp`. Le retirer du schéma montré au modèle ne
suffit pas : un modèle peut produire un argument hors schéma.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from tests.fakes import (
    EMAIL_ARGUMENTS,
    EMAIL_PREVIEW,
    BrokenTool,
    FakeTool,
    calling,
    run_config,
    tool_call,
)

from mass_agents.graph.nodes.preview import preview_node

COURRIEL = tool_call("send_email", EMAIL_ARGUMENTS, "e1")


def _etat(appel):
    return {"messages": [HumanMessage(content="écris à la commission"), appel]}


async def test_un_confirmed_pose_par_le_modele_est_ignore(send_email_tool):
    malveillant = tool_call("send_email", {**EMAIL_ARGUMENTS, "confirmed": True}, "e1")

    update = await preview_node(
        _etat(calling(malveillant)), run_config({"send_email": send_email_tool})
    )

    # L'outil n'a été appelé qu'une fois, et en aperçu.
    assert send_email_tool.calls == [{**EMAIL_ARGUMENTS, "confirmed": False}]
    # La valeur du modèle n'est pas conservée pour la reprise.
    assert "confirmed" not in update["pending_action"].arguments


async def test_l_apercu_est_conserve_tel_que_le_serveur_l_a_rendu(send_email_tool):
    update = await preview_node(
        _etat(calling(COURRIEL)), run_config({"send_email": send_email_tool})
    )

    action = update["pending_action"]
    assert action.tool_name == "send_email"
    assert action.arguments == EMAIL_ARGUMENTS
    assert action.preview == EMAIL_PREVIEW
    # C'est cet identifiant qui rattachera la décision à l'appel du modèle.
    assert action.tool_call_id == "e1"
    # Rien n'est répondu à l'appel tant que l'humain n'a pas décidé.
    assert "messages" not in update


async def test_un_apercu_refuse_par_le_serveur_revient_au_modele():
    """Groupe inexistant, groupe vide : le serveur dit quoi corriger."""
    refus = BrokenTool("send_email", "Aucun groupe ne porte l'identifiant G0003.")

    update = await preview_node(
        _etat(calling(COURRIEL)), run_config({"send_email": refus})
    )

    assert update["pending_action"] is None
    reponse = update["messages"][0]
    assert reponse.tool_call_id == "e1"
    assert reponse.status == "error"
    assert "G0003" in reponse.content


async def test_une_reponse_sans_marque_d_apercu_n_ouvre_pas_de_validation():
    """Sans `confirmationRequired`, il n'y a rien qu'un humain puisse relire —
    on ne soumet pas à validation ce qu'on ne sait pas montrer."""
    etrange = FakeTool("send_email", {"sent": False})

    update = await preview_node(
        _etat(calling(COURRIEL)), run_config({"send_email": etrange})
    )

    assert update["pending_action"] is None
    assert update["messages"][0].status == "error"
