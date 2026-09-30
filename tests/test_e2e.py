"""Le graphe complet, du message de l'utilisateur à la réponse finale.

Le vrai graphe, compilé par `build_graph`, avec un vrai checkpointer en
mémoire. Seuls le modèle — remplacé par des réponses écrites d'avance — et le
serveur MCP sont des doublures.

Le scénario est celui qui justifie le service : une lecture pour résoudre la
demande, un envoi proposé, une suspension pour validation, puis la reprise. Le
modèle y pose de lui-même `confirmed: true`, pour vérifier que la garantie tient
de bout en bout et pas seulement dans le nœud qui la porte.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from tests.fakes import (
    EMAIL_ARGUMENTS,
    FakeTool,
    ScriptedModel,
    calling,
    run_config,
    tool_call,
)

from mass_agents.graph import build_graph
from mass_agents.graph.nodes import agent as agent_module

LECTURE = tool_call("query_analytics", {"sql": "select id from groups"}, "l1")
COURRIEL = tool_call("send_email", {**EMAIL_ARGUMENTS, "confirmed": True}, "e1")
DEMANDE = {"messages": [HumanMessage(content="écris à la commission observation")]}


def _installe(monkeypatch, *reponses: AIMessage) -> ScriptedModel:
    modele = ScriptedModel(*reponses)
    monkeypatch.setattr(agent_module, "build_model", lambda: modele)
    return modele


def _config(send_email_tool, approval_log):
    groupes = FakeTool("query_analytics", {"rows": [{"id": "G0003"}]})
    return run_config(
        {"query_analytics": groupes, "send_email": send_email_tool}, approval_log
    )


def _sans_resultat(messages) -> set[str]:
    """Les appels d'outil restés sans résultat dans le fil."""
    appels = {
        c["id"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls
    }
    repondus = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return appels - repondus


async def test_un_envoi_s_arrete_puis_part_apres_approbation(
    monkeypatch, send_email_tool, approval_log
):
    modele = _installe(
        monkeypatch,
        calling(LECTURE),
        calling(COURRIEL, text="Je prépare le courriel."),
        AIMessage(content="C'est envoyé aux 12 membres de la commission."),
    )
    graphe = build_graph(InMemorySaver())
    config = _config(send_email_tool, approval_log)

    # 1. Le run s'arrête sur la validation, sans rien avoir envoyé.
    suspendu = await graphe.ainvoke(DEMANDE, config)

    assert suspendu["__interrupt__"][0].value["preview"]["recipientCount"] == 12
    assert send_email_tool.calls == [{**EMAIL_ARGUMENTS, "confirmed": False}]

    # 2. La reprise approuvée exécute l'appel relu, puis l'agent conclut.
    fin = await graphe.ainvoke(Command(resume={"approved": True}), config)

    assert send_email_tool.calls[-1] == {**EMAIL_ARGUMENTS, "confirmed": True}
    assert len(send_email_tool.calls) == 2
    assert fin["messages"][-1].content.startswith("C'est envoyé")
    assert fin["pending_action"] is None

    # Le dernier appel de modèle a vu le résultat de l'envoi, rattaché à son
    # appel ; et le fil ne laisse aucun appel sans résultat.
    vu = modele.received[-1][-1]
    assert isinstance(vu, ToolMessage) and vu.tool_call_id == "e1"
    assert _sans_resultat(fin["messages"]) == set()


async def test_un_envoi_refuse_ne_part_pas_et_l_agent_le_sait(
    monkeypatch, send_email_tool, approval_log
):
    modele = _installe(
        monkeypatch,
        calling(COURRIEL),
        AIMessage(content="Entendu, rien n'est parti."),
    )
    graphe = build_graph(InMemorySaver())
    config = _config(send_email_tool, approval_log)

    await graphe.ainvoke(DEMANDE, config)
    fin = await graphe.ainvoke(
        Command(resume={"approved": False, "reason": "pas ce soir"}), config
    )

    assert all(appel["confirmed"] is False for appel in send_email_tool.calls)
    assert "pas ce soir" in modele.received[-1][-1].content
    assert fin["messages"][-1].content == "Entendu, rien n'est parti."
    assert _sans_resultat(fin["messages"]) == set()


async def test_une_question_de_lecture_ne_suspend_rien(
    monkeypatch, send_email_tool, approval_log
):
    _installe(
        monkeypatch,
        calling(LECTURE),
        AIMessage(content="La commission a l'identifiant G0003."),
    )
    graphe = build_graph(InMemorySaver())

    fin = await graphe.ainvoke(DEMANDE, _config(send_email_tool, approval_log))

    assert "__interrupt__" not in fin
    assert send_email_tool.calls == []
    assert fin["messages"][-1].content.endswith("G0003.")
