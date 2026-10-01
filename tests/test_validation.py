"""La validation humaine, jouée avec un vrai `interrupt()`.

Le test monte un vrai graphe LangGraph avec un vrai checkpointer en mémoire —
seul le serveur MCP est remplacé. C'est ce qui rend le résultat significatif :
`interrupt()` et `Command(resume=…)` sont exercés pour de bon, pas simulés.

La décision revient à l'agent comme le résultat de son propre appel : un
`ToolMessage` rattaché à l'identifiant de l'appel d'origine.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from tests.fakes import (
    ADMIN,
    EMAIL_ARGUMENTS,
    EMAIL_PREVIEW,
    EMAIL_SENT,
    BrokenTool,
    FakeApprovalLog,
    SlowTool,
    run_config,
)

from mass_agents.domain import CONSEQUENCES, ApprovalRequest, PendingAction
from mass_agents.graph.nodes.validation import validation_node
from mass_agents.graph.state import OrchestratorState

EN_ATTENTE = PendingAction(
    tool_name="send_email",
    arguments=EMAIL_ARGUMENTS,
    preview=EMAIL_PREVIEW,
    tool_call_id="e1",
)


def _graphe():
    """Le nœud de validation, entre un départ et un agent inerte.

    L'agent est réduit à un nœud vide : ce qui est testé est la validation, et
    un vrai agent appellerait un modèle.
    """
    builder = StateGraph(OrchestratorState)
    builder.add_node("validation", validation_node)
    builder.add_node("agent", lambda state: {})
    builder.add_edge(START, "validation")
    builder.add_edge("validation", "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=InMemorySaver())


async def _suspendu(config):
    graphe = _graphe()
    await graphe.ainvoke({"messages": [], "pending_action": EN_ATTENTE}, config)
    return graphe


async def test_le_graphe_s_arrete_et_l_etat_est_checkpointe(
    send_email_tool, approval_log
):
    graphe = _graphe()
    config = run_config({"send_email": send_email_tool}, approval_log)

    resultat = await graphe.ainvoke(
        {"messages": [], "pending_action": EN_ATTENTE}, config
    )

    # Le graphe est suspendu, et l'aperçu remonte tel que `mass-mcp` l'a produit.
    interruption = resultat["__interrupt__"][0]
    assert interruption.value["tool_name"] == "send_email"
    assert interruption.value["preview"] == EMAIL_PREVIEW
    assert "ne se rappelle pas" in interruption.value["consequence"]

    # Rien n'est parti.
    assert send_email_tool.calls == []

    # L'état est persisté avec l'action en attente : c'est ce qui permet de
    # retrouver la validation après un redémarrage.
    etat = await graphe.aget_state(config)
    assert etat.interrupts
    assert etat.values["pending_action"].tool_name == "send_email"


async def test_la_reprise_approuvee_execute_le_meme_appel(
    send_email_tool, approval_log
):
    config = run_config({"send_email": send_email_tool}, approval_log)
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    # Le même appel, augmenté du seul `confirmed`.
    assert send_email_tool.calls == [{**EMAIL_ARGUMENTS, "confirmed": True}]

    # L'agent reçoit le résultat de son propre appel, avec la réponse du serveur.
    reponse = resultat["messages"][-1]
    assert reponse.tool_call_id == "e1"
    assert reponse.status == "success"
    assert "Approuvé par l'administrateur" in reponse.content
    assert "tracés dans l'historique" in reponse.content

    # L'ardoise est effacée : la même action ne sera pas revalidée.
    assert resultat["pending_action"] is None

    # La décision est tracée, avec l'aperçu qui a été relu.
    trace = approval_log.entries[0]
    assert trace["approved"] is True
    assert trace["admin"] == ADMIN
    assert trace["action"].preview["groupTitle"] == "Commission observation"
    assert trace["outcome"]["sent"] is True


async def test_la_reprise_refusee_n_execute_rien(send_email_tool, approval_log):
    config = run_config({"send_email": send_email_tool}, approval_log)
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(
        Command(resume={"approved": False, "reason": "mauvais destinataires"}), config
    )

    assert send_email_tool.calls == []
    reponse = resultat["messages"][-1]
    assert reponse.tool_call_id == "e1"
    assert "Refusé par l'administrateur" in reponse.content
    assert "mauvais destinataires" in reponse.content
    assert resultat["pending_action"] is None

    # Un refus se trace au même titre qu'une approbation : c'est ce qui
    # distingue « refusé » de « jamais demandé ».
    assert approval_log.entries[0]["approved"] is False


async def test_une_decision_illisible_n_execute_rien(send_email_tool, approval_log):
    """Aucun repli sur « approuvé ».

    C'est le scénario qu'on ne veut à aucun prix : une reprise mal formée qui
    ferait partir un courriel. L'échec laisse le fil au dernier checkpoint, donc
    une reprise correcte reste possible.
    """
    config = run_config({"send_email": send_email_tool}, approval_log)
    graphe = await _suspendu(config)

    with pytest.raises(Exception, match="illisible"):
        await graphe.ainvoke(Command(resume="oui, vas-y"), config)

    assert send_email_tool.calls == []
    assert (await graphe.aget_state(config)).interrupts


async def test_un_echec_de_l_outil_est_rendu_a_l_agent(approval_log):
    """L'utilisateur a approuvé : il doit savoir ce qui s'est passé ensuite."""
    config = run_config(
        {"send_email": BrokenTool("send_email", "mass-mcp injoignable")}, approval_log
    )
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    reponse = resultat["messages"][-1]
    assert reponse.status == "error"
    assert "mass-mcp injoignable" in reponse.content
    # On ne prétend pas que rien n'est parti : une coupure peut suivre l'envoi.
    assert "vérifier" in reponse.content
    assert approval_log.entries[0]["outcome"] == {"error": "mass-mcp injoignable"}


async def test_sans_action_en_attente_le_noeud_ne_suspend_rien(
    send_email_tool, approval_log
):
    graphe = _graphe()
    config = run_config({"send_email": send_email_tool}, approval_log)

    resultat = await graphe.ainvoke({"messages": [], "pending_action": None}, config)

    assert "__interrupt__" not in resultat
    assert send_email_tool.calls == []


# -- une exécution au plus par appel ------------------------------------------


async def test_une_reprise_rejouee_n_execute_pas_une_seconde_fois(
    send_email_tool, approval_log
):
    """Le service s'arrête après l'envoi, avant d'avoir checkpointé : la
    validation est toujours « en attente », et l'administrateur la réapprouve.
    Le journal, lui, sait que c'est fait."""
    config = run_config({"send_email": send_email_tool}, approval_log)
    graphe = await _suspendu(config)
    await graphe.ainvoke(Command(resume={"approved": True}), config)

    # Un nouveau graphe, qui repart du checkpoint d'avant l'envoi.
    rejoue = await _suspendu(config)
    resultat = await rejoue.ainvoke(Command(resume={"approved": True}), config)

    assert len(send_email_tool.calls) == 1
    reponse = resultat["messages"][-1]
    assert "Déjà exécuté" in reponse.content
    assert reponse.status == "success"


async def test_une_execution_a_l_issue_inconnue_n_est_pas_rejouee(send_email_tool):
    """Une ligne restée à `executing` : le service est tombé pendant l'appel.
    Le courriel est peut-être parti — on ne le renvoie pas à l'aveugle."""
    journal = FakeApprovalLog()
    journal.seed("fil-1", "e1", "executing")
    config = run_config({"send_email": send_email_tool}, journal)
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    assert send_email_tool.calls == []
    reponse = resultat["messages"][-1]
    assert reponse.status == "error"
    assert "vérifier dans le back-office" in reponse.content


async def test_un_refus_apres_execution_ne_pretend_pas_que_rien_n_est_parti(
    send_email_tool,
):
    """Le geste a été exécuté puis le service est tombé ; l'administrateur,
    revenu, refuse. Répondre « refusé, rien n'est parti » serait faux."""
    journal = FakeApprovalLog()
    journal.seed("fil-1", "e1", "done", outcome={"sent": True})
    config = run_config({"send_email": send_email_tool}, journal)
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": False}), config)

    assert "Déjà exécuté" in resultat["messages"][-1].content


async def test_sans_journal_rien_n_est_execute(send_email_tool):
    """Un geste qui engage l'association ne part pas sans trace."""
    config = run_config(
        {"send_email": send_email_tool}, FakeApprovalLog(unavailable=True)
    )
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    assert send_email_tool.calls == []
    reponse = resultat["messages"][-1]
    assert reponse.status == "error"
    assert "journal des validations est indisponible" in reponse.content


async def test_sans_journal_un_refus_reste_un_refus(send_email_tool):
    config = run_config(
        {"send_email": send_email_tool}, FakeApprovalLog(unavailable=True)
    )
    graphe = await _suspendu(config)

    resultat = await graphe.ainvoke(Command(resume={"approved": False}), config)

    assert send_email_tool.calls == []
    assert "Refusé" in resultat["messages"][-1].content


async def test_un_run_interrompu_pendant_l_envoi_le_laisse_aboutir(approval_log):
    """Délai dépassé ou navigateur fermé pendant l'appel confirmé : l'envoi et
    son inscription au journal vont à leur terme. Une réapprobation ensuite
    retrouve l'envoi fait, au lieu de le refaire."""
    lent = SlowTool(
        "send_email", preview=EMAIL_PREVIEW, outcome=EMAIL_SENT, delay_s=0.05
    )
    config = run_config({"send_email": lent}, approval_log)
    graphe = await _suspendu(config)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            graphe.ainvoke(Command(resume={"approved": True}), config), timeout=0.01
        )
    await asyncio.sleep(0.1)

    assert len(lent.calls) == 1
    assert approval_log.entries[0]["status"] == "done"

    # Le nœud n'a pas abouti : la validation est toujours en attente.
    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    assert len(lent.calls) == 1
    assert "Déjà exécuté" in resultat["messages"][-1].content


def test_l_apercu_du_serveur_est_repris_sans_retouche():
    """Ce qui est montré doit être ce qui a été calculé.

    Reformater l'aperçu ferait diverger la relecture de l'envoi — c'est le corps
    intégral et les destinataires résolus qui engagent le nom de l'association.
    """
    demande = ApprovalRequest(
        tool_name="send_email",
        title="Envoyer ce courriel ?",
        consequence=CONSEQUENCES["send_email"],
        preview=EMAIL_PREVIEW,
    )

    assert demande.preview == EMAIL_PREVIEW
    assert (
        json.loads(demande.model_dump_json())["preview"]["body"]
        == EMAIL_PREVIEW["body"]
    )
