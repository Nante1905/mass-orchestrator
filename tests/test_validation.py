"""La validation humaine, jouée de bout en bout.

C'est le critère d'acceptation du lot 5 : « `send_email` s'arrête et l'état est
checkpointé ; la reprise approuvée envoie ; la reprise refusée n'envoie rien ;
le fil reste cohérent dans les deux cas. »

Le test monte un vrai graphe LangGraph avec un vrai checkpointer en mémoire —
seul le serveur MCP est remplacé. C'est ce qui rend le résultat significatif :
`interrupt()` et `Command(resume=…)` sont exercés pour de bon, pas simulés.
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from tests.fakes import FakeToolset

from mass_agents.auth import AdminIdentity
from mass_agents.domain import PendingAction
from mass_agents.graph.context import RunContext, build_run_config
from mass_agents.graph.nodes.validation import validation_node
from mass_agents.graph.state import OrchestratorState

ADMIN = AdminIdentity(
    user_id="USR0001", email="admin@example.org", administrator_id="ADM1"
)

ARGUMENTS = {
    "group_id": "G0003",
    "subject": "Sortie de samedi",
    "body": "<p>Rendez-vous à 20h.</p>",
}

APERCU = {
    "sent": False,
    "confirmationRequired": True,
    "groupTitle": "Commission observation",
    "recipientCount": 12,
    **ARGUMENTS,
}

EN_ATTENTE = PendingAction(
    tool_name="send_email", arguments=ARGUMENTS, preview=APERCU, tool_call_id="c1"
)


def _graphe():
    """Le nœud de validation, entre un départ et un superviseur inerte.

    Le superviseur est réduit à un nœud vide : ce qui est testé est la
    validation, et un vrai superviseur appellerait un modèle.
    """
    builder = StateGraph(OrchestratorState)
    builder.add_node("validation", validation_node, destinations=("superviseur",))
    builder.add_node("superviseur", lambda state: {"next": None})
    builder.add_edge(START, "validation")
    builder.add_edge("superviseur", END)
    return builder.compile(checkpointer=InMemorySaver())


def _config(send_email_tool, approval_log):
    return build_run_config(
        "fil-1",
        RunContext(
            admin=ADMIN,
            toolset=FakeToolset({"send_email": send_email_tool}),
            approvals=approval_log,
        ),
    )


async def test_l_envoi_s_arrete_et_l_etat_est_checkpointe(
    send_email_tool, approval_log
):
    graphe = _graphe()
    config = _config(send_email_tool, approval_log)

    resultat = await graphe.ainvoke(
        {"messages": [], "pending_action": EN_ATTENTE}, config
    )

    # Le graphe est suspendu, et l'aperçu remonte tel que `mass-mcp` l'a produit.
    interruption = resultat["__interrupt__"][0]
    assert interruption.value["tool_name"] == "send_email"
    assert interruption.value["preview"]["recipientCount"] == 12
    assert "ne se rappelle pas" in interruption.value["consequence"]

    # Rien n'est parti.
    assert send_email_tool.calls == []

    # L'état est bien persisté, avec l'action toujours en attente : c'est ce qui
    # permet de retrouver la validation après un redémarrage.
    etat = await graphe.aget_state(config)
    assert etat.interrupts
    assert etat.values["pending_action"].tool_name == "send_email"


async def test_la_reprise_approuvee_envoie(send_email_tool, approval_log):
    graphe = _graphe()
    config = _config(send_email_tool, approval_log)
    await graphe.ainvoke({"messages": [], "pending_action": EN_ATTENTE}, config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    # Le même appel, augmenté du seul `confirmed`.
    assert send_email_tool.calls == [{**ARGUMENTS, "confirmed": True}]

    # Le fil dit ce qui s'est passé, en reprenant le message du serveur.
    assert "après votre validation" in resultat["messages"][-1].content
    assert "tracé dans l'historique" in resultat["messages"][-1].content

    # L'ardoise est effacée : la même action ne sera pas revalidée au tour suivant.
    assert resultat["pending_action"] is None

    # La décision est tracée, avec l'aperçu qui a été relu.
    assert len(approval_log.entries) == 1
    trace = approval_log.entries[0]
    assert trace["approved"] is True
    assert trace["admin"] == ADMIN
    assert trace["action"].preview["groupTitle"] == "Commission observation"
    assert trace["outcome"]["sent"] is True


async def test_la_reprise_refusee_n_envoie_rien(send_email_tool, approval_log):
    graphe = _graphe()
    config = _config(send_email_tool, approval_log)
    await graphe.ainvoke({"messages": [], "pending_action": EN_ATTENTE}, config)

    resultat = await graphe.ainvoke(
        Command(resume={"approved": False, "reason": "mauvais destinataires"}), config
    )

    assert send_email_tool.calls == []
    assert "n'a pas eu lieu" in resultat["messages"][-1].content
    assert "mauvais destinataires" in resultat["messages"][-1].content
    assert resultat["pending_action"] is None

    # Un refus se trace au même titre qu'une approbation : c'est ce qui
    # distingue « refusé » de « jamais demandé ».
    assert approval_log.entries[0]["approved"] is False


async def test_une_decision_illisible_n_envoie_rien(send_email_tool, approval_log):
    """Aucun repli sur « approuvé ».

    C'est le scénario qu'on ne veut à aucun prix : une reprise mal formée qui
    ferait partir un courriel. L'échec laisse le fil au dernier checkpoint, donc
    une reprise correcte reste possible.
    """
    graphe = _graphe()
    config = _config(send_email_tool, approval_log)
    await graphe.ainvoke({"messages": [], "pending_action": EN_ATTENTE}, config)

    with pytest.raises(Exception, match="illisible"):
        await graphe.ainvoke(Command(resume="oui, vas-y"), config)

    assert send_email_tool.calls == []
    assert (await graphe.aget_state(config)).interrupts


async def test_un_echec_de_l_outil_ne_sort_pas_de_la_boucle(approval_log):
    """L'utilisateur a approuvé : il doit savoir ce qui s'est passé ensuite."""

    class OutilEnPanne:
        name = "send_email"

        def __init__(self) -> None:
            self.calls: list = []

        async def ainvoke(self, args):
            raise RuntimeError("mass-mcp injoignable")

    graphe = _graphe()
    config = build_run_config(
        "fil-1",
        RunContext(
            admin=ADMIN,
            toolset=FakeToolset({"send_email": OutilEnPanne()}),
            approvals=approval_log,
        ),
    )
    await graphe.ainvoke({"messages": [], "pending_action": EN_ATTENTE}, config)

    resultat = await graphe.ainvoke(Command(resume={"approved": True}), config)

    assert "n'a pas pu être exécutée" in resultat["messages"][-1].content
    assert approval_log.entries[0]["outcome"] == {"error": "mass-mcp injoignable"}


async def test_sans_action_en_attente_le_noeud_rend_la_main(
    send_email_tool, approval_log
):
    """Atteint par erreur, il ne doit ni échouer ni inventer une validation."""
    graphe = _graphe()
    config = _config(send_email_tool, approval_log)

    resultat = await graphe.ainvoke({"messages": [], "pending_action": None}, config)

    assert "__interrupt__" not in resultat
    assert send_email_tool.calls == []


def test_l_apercu_du_serveur_est_repris_sans_retouche():
    """Ce qui est montré doit être ce qui a été calculé.

    Reformater l'aperçu ferait diverger la relecture de l'envoi — c'est le corps
    intégral et les destinataires résolus qui engagent le nom de l'association.
    """
    from mass_agents.domain import CONSEQUENCES, ApprovalRequest

    demande = ApprovalRequest(
        tool_name="send_email",
        title="Envoyer ce courriel ?",
        consequence=CONSEQUENCES["send_email"],
        preview=APERCU,
    )

    assert demande.preview == APERCU
    assert json.loads(demande.model_dump_json())["preview"]["body"] == APERCU["body"]
