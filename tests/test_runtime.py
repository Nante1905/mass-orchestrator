"""Ce que le service rend à l'API : le flux d'évènements et l'état d'un fil.

Le vrai graphe tourne derrière, avec un modèle aux réponses écrites d'avance.
Ce qui est vérifié est la traduction — ce qui atteint le navigateur, et ce qui
ne doit pas l'atteindre —, et la discipline autour d'un run : un seul à la fois
par fil, rien qui abandonne une validation en silence, et un verrou toujours
rendu.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from tests.fakes import (
    ADMIN,
    EMAIL_ARGUMENTS,
    FakeApprovalLog,
    FakeThreads,
    FakeTool,
    FakeToolset,
    ScriptedModel,
    calling,
    tool_call,
)

from mass_agents.api.errors import register_error_handlers
from mass_agents.config import LimitsConfig, McpConfig
from mass_agents.domain import (
    ApprovalDecision,
    ApprovalPendingError,
    NoPendingApprovalError,
    ThreadBusyError,
)
from mass_agents.graph import (
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    Orchestrator,
    TokenEvent,
    ToolCallEvent,
    build_graph,
)
from mass_agents.graph import runtime as module
from mass_agents.graph.nodes import agent as agent_module

LECTURE = tool_call("query_analytics", {"sql": "select 1"}, "l1")
COURRIEL = tool_call("send_email", EMAIL_ARGUMENTS, "e1")
OUI = ApprovalDecision(approved=True)


def _orchestrateur(
    monkeypatch,
    send_email_tool,
    *reponses: AIMessage,
    max_steps: int = 15,
    threads: FakeThreads | None = None,
) -> Orchestrator:
    modele = ScriptedModel(*reponses)
    monkeypatch.setattr(agent_module, "build_model", lambda: modele)

    outillage = FakeToolset(
        {
            "query_analytics": FakeTool("query_analytics", {"rows": [{"n": 12}]}),
            "send_email": send_email_tool,
        }
    )

    async def _outillage(token, config):
        return outillage

    monkeypatch.setattr(module, "build_mass_toolset", _outillage)

    return Orchestrator(
        graph=build_graph(InMemorySaver()),
        threads=threads or FakeThreads(),  # type: ignore[arg-type]
        approvals=FakeApprovalLog(),  # type: ignore[arg-type]
        mcp=McpConfig(url="http://mcp.invalid", timeout_s=1),
        limits=LimitsConfig(
            max_steps=max_steps, max_tokens_per_request=400_000, run_timeout_s=10
        ),
    )


def _demande(orchestrateur: Orchestrator, message: str = "écris"):
    return orchestrateur.open_start("fil-1", ADMIN, "jeton", message)


def _reprise(orchestrateur: Orchestrator, decision: ApprovalDecision = OUI):
    return orchestrateur.open_resume("fil-1", ADMIN, "jeton", decision)


async def _evenements(ouverture) -> list:
    """Ouvre le flux, puis le consomme jusqu'au bout."""
    return [event async for event in await ouverture]


# -- le flux d'un run ---------------------------------------------------------


async def test_un_envoi_se_diffuse_jusqu_a_la_validation(monkeypatch, send_email_tool):
    orchestrateur = _orchestrateur(
        monkeypatch, send_email_tool, calling(COURRIEL, text="Je prépare l'envoi.")
    )

    events = await _evenements(_demande(orchestrateur))

    annonce = MessageEvent(
        role="assistant", content="Je prépare l'envoi.", node="agent"
    )
    assert annonce in events
    assert ToolCallEvent(name="send_email") in events
    # Le texte précède l'appel qu'il annonce.
    assert events.index(annonce) < events.index(ToolCallEvent(name="send_email"))
    approbation = next(e for e in events if isinstance(e, ApprovalEvent))
    assert approbation.payload["tool_name"] == "send_email"
    assert events[-1] == DoneEvent(status="awaiting_approval", thread_id="fil-1")


async def test_les_resultats_d_outils_n_atteignent_pas_le_navigateur(
    monkeypatch, send_email_tool
):
    """Ni en fragments, ni en messages : ils contiennent du JSON brut et des
    adresses, et ne sont pas une réponse."""
    orchestrateur = _orchestrateur(
        monkeypatch,
        send_email_tool,
        calling(COURRIEL),
        AIMessage(content="C'est parti."),
    )
    await _evenements(_demande(orchestrateur))

    events = await _evenements(_reprise(orchestrateur))

    textes = [e.text for e in events if isinstance(e, TokenEvent)] + [
        e.content for e in events if isinstance(e, MessageEvent)
    ]
    assert textes and all("a@example.org" not in t for t in textes)
    assert events[-1] == DoneEvent(status="completed", thread_id="fil-1")


async def test_la_limite_de_recursion_se_lit_comme_un_plafond(
    monkeypatch, send_email_tool
):
    """Si le plafond d'étapes était contourné, le filet de LangGraph ne doit pas
    se lire comme une panne."""
    orchestrateur = _orchestrateur(
        monkeypatch,
        send_email_tool,
        *(calling(tool_call("query_analytics", {}, f"l{i}")) for i in range(10)),
        max_steps=1,
    )

    events = await _evenements(_demande(orchestrateur, "compte"))

    erreur = next(e for e in events if isinstance(e, ErrorEvent))
    assert "Je m'arrête ici" in erreur.message
    assert events[-1] == DoneEvent(status="stopped", thread_id="fil-1")


# -- un run à la fois, et le verrou toujours rendu ----------------------------


async def test_le_verrou_est_rendu_a_la_fin_du_run(monkeypatch, send_email_tool):
    fils = FakeThreads()
    orchestrateur = _orchestrateur(
        monkeypatch, send_email_tool, AIMessage(content="Bonjour."), threads=fils
    )

    await _evenements(_demande(orchestrateur, "bonjour"))

    assert fils.held == set()


async def test_un_second_run_concurrent_est_refuse(monkeypatch, send_email_tool):
    """Deux reprises concurrentes d'une validation exécuteraient deux fois le
    geste approuvé : la seconde est refusée avant d'ouvrir son flux."""
    fils = FakeThreads()
    orchestrateur = _orchestrateur(
        monkeypatch, send_email_tool, AIMessage(content="…"), threads=fils
    )

    premier = await _demande(orchestrateur, "première")

    with pytest.raises(ThreadBusyError):
        await _demande(orchestrateur, "seconde")

    await premier.aclose()


async def test_un_flux_abandonne_rend_son_verrou(monkeypatch, send_email_tool):
    """Le navigateur se ferme en plein run : le fil ne reste pas bloqué."""
    fils = FakeThreads()
    orchestrateur = _orchestrateur(
        monkeypatch, send_email_tool, calling(LECTURE), AIMessage(content="…"),
        threads=fils,
    )

    flux = await _demande(orchestrateur, "combien ?")
    await anext(flux)
    await flux.aclose()

    assert fils.held == set()


async def test_un_message_pendant_une_validation_est_refuse(
    monkeypatch, send_email_tool
):
    """Il abandonnerait la validation en silence, avec un appel d'outil resté
    sans réponse dans le fil."""
    fils = FakeThreads()
    orchestrateur = _orchestrateur(
        monkeypatch, send_email_tool, calling(COURRIEL), threads=fils
    )
    await _evenements(_demande(orchestrateur))

    with pytest.raises(ApprovalPendingError):
        await _demande(orchestrateur, "autre chose")

    # Le refus rend le verrou : la validation reste possible.
    assert fils.held == set()
    assert (await orchestrateur.get_state("fil-1", ADMIN)).pending_approval


async def test_une_decision_sans_validation_en_attente_est_refusee(
    monkeypatch, send_email_tool
):
    fils = FakeThreads()
    orchestrateur = _orchestrateur(monkeypatch, send_email_tool, threads=fils)

    with pytest.raises(NoPendingApprovalError):
        await _reprise(orchestrateur)

    assert fils.held == set()


@pytest.mark.parametrize(
    "erreur", [ThreadBusyError("occupé"), ApprovalPendingError("en attente")]
)
def test_un_conflit_se_rend_en_409(erreur):
    """Rendu avant l'ouverture du flux : après les en-têtes SSE, il n'y aurait
    plus de code HTTP pour le dire."""
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/run")
    async def _run():
        raise erreur

    reponse = TestClient(app).post("/run")

    assert reponse.status_code == 409
    assert reponse.json()["ok"] is False


# -- l'état d'un fil ----------------------------------------------------------


async def test_l_etat_rend_la_validation_sous_la_forme_du_flux(
    monkeypatch, send_email_tool
):
    orchestrateur = _orchestrateur(monkeypatch, send_email_tool, calling(COURRIEL))
    events = await _evenements(_demande(orchestrateur))

    etat = await orchestrateur.get_state("fil-1", ADMIN)

    flux = next(e for e in events if isinstance(e, ApprovalEvent)).payload
    assert etat.status == "awaiting_approval"
    assert etat.pending_approval == flux
    assert {"tool_name", "title", "consequence", "preview"} <= set(flux)


async def test_l_etat_ne_montre_que_les_demandes_et_les_reponses(
    monkeypatch, send_email_tool
):
    orchestrateur = _orchestrateur(
        monkeypatch,
        send_email_tool,
        calling(LECTURE),
        AIMessage(content="Il y a 12 inscrits."),
    )
    await _evenements(_demande(orchestrateur, "combien ?"))

    etat = await orchestrateur.get_state("fil-1", ADMIN)

    assert [(m["role"], m["content"]) for m in etat.messages] == [
        ("human", "combien ?"),
        ("ai", "Il y a 12 inscrits."),
    ]
    assert etat.status == "completed"
    assert etat.pending_approval is None


# -- la traduction, pièce par pièce -------------------------------------------


def test_un_resultat_d_outil_n_est_pas_un_fragment():
    resultat = ToolMessage(content='{"sent": true}', tool_call_id="e1")

    assert module._token_event((resultat, {"langgraph_node": "validation"})) is None


def test_un_fragment_de_l_assistant_est_diffuse():
    fragment = AIMessageChunk(content=[{"type": "text", "text": "Il y a"}])

    event = module._token_event((fragment, {"langgraph_node": "agent"}))

    assert event == TokenEvent(text="Il y a", node="agent")


def test_un_fragment_d_appel_d_outil_n_est_pas_diffuse():
    """Il construit un appel bloc par bloc : le diffuser ferait clignoter du
    vide."""
    fragment = AIMessageChunk(content=[{"type": "tool_use", "id": "l1", "input": {}}])

    assert module._token_event((fragment, {})) is None


@pytest.mark.parametrize(
    "message",
    [
        ToolMessage(content="{}", tool_call_id="l1"),
        AIMessage(content="", tool_calls=[LECTURE]),
    ],
)
def test_un_message_sans_texte_d_assistant_est_invisible(message):
    assert not module._is_visible(message)


def test_une_demande_est_toujours_visible():
    assert module._is_visible(HumanMessage(content="combien ?"))
