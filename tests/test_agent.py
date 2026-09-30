"""Le nœud de l'agent, et les plafonds qu'il porte.

Le modèle est remplacé : ce qui est vérifié n'est pas qu'Anthropic réponde
bien, mais ce que le nœud lui envoie, et qu'il s'arrête **avant** l'appel quand
un plafond est atteint — un dépassement constaté après coup a déjà été facturé.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from tests.fakes import ScriptedModel, run_config

from mass_agents.graph.context import run_context
from mass_agents.graph.limits import consumed_tokens, model_calls
from mass_agents.graph.nodes import agent as module
from mass_agents.graph.nodes.agent import agent_node


def _config():
    return run_config({"list_events": object()})


def _installe(monkeypatch, *reponses: AIMessage) -> ScriptedModel:
    modele = ScriptedModel(*reponses)
    monkeypatch.setattr(module, "build_model", lambda: modele)
    return modele


def _limites():
    return module.get_config().limits


def _appel(tokens: int = 10) -> AIMessage:
    return AIMessage(
        content="…",
        usage_metadata={
            "input_tokens": tokens,
            "output_tokens": 0,
            "total_tokens": tokens,
        },
    )


async def test_le_modele_recoit_le_prompt_puis_le_fil(monkeypatch):
    modele = _installe(monkeypatch, AIMessage(content="42 présents."))

    await agent_node(
        {"messages": [HumanMessage(content="combien de présents ?")]}, _config()
    )

    recu = modele.received[0]
    assert isinstance(recu[0], SystemMessage)
    assert recu[1].content == "combien de présents ?"


async def test_les_outils_lies_sont_ceux_de_l_outillage_du_run(monkeypatch):
    """Et non une liste figée au démarrage : l'outillage porte le jeton de
    l'appelant."""
    modele = _installe(monkeypatch, AIMessage(content="…"))
    config = _config()

    await agent_node({"messages": [HumanMessage(content="…")]}, config)

    assert modele.bound == run_context(config).toolset.agent_schemas()


async def test_la_reponse_du_modele_est_ajoutee_au_fil(monkeypatch):
    reponse = AIMessage(content="42 présents.")
    _installe(monkeypatch, reponse)

    update = await agent_node({"messages": [HumanMessage(content="…")]}, _config())

    assert update["messages"] == [reponse]


async def test_le_plafond_d_etapes_arrete_le_run_avant_l_appel(monkeypatch):
    modele = _installe(monkeypatch, AIMessage(content="encore"))
    plafond = _limites().max_steps
    fil = [HumanMessage(content="recommence"), *(_appel() for _ in range(plafond))]

    update = await agent_node({"messages": fil}, _config())

    assert "Je m'arrête ici" in update["messages"][0].content
    assert modele.received == []


async def test_le_plafond_de_jetons_arrete_le_run_avant_l_appel(monkeypatch):
    modele = _installe(monkeypatch, AIMessage(content="encore"))
    plafond = _limites().max_tokens_per_request
    fil = [HumanMessage(content="et ensuite ?"), _appel(tokens=plafond)]

    update = await agent_node({"messages": fil}, _config())

    assert "volume maximal" in update["messages"][0].content
    assert modele.received == []


# -- ce que « par demande » veut dire -----------------------------------------


def test_un_nouveau_message_repart_d_un_budget_entier():
    """Un plafond sur la vie du fil rendrait celui-ci muet au bout de quelques
    questions."""
    fil = [
        HumanMessage(content="première question"),
        _appel(tokens=1000),
        _appel(tokens=1000),
        HumanMessage(content="seconde question"),
    ]

    assert model_calls(fil) == 0
    assert consumed_tokens(fil) == 0


def test_une_reprise_ne_remet_pas_le_budget_a_zero():
    """La reprise après validation n'ajoute pas de message de l'utilisateur :
    faire valider une action ne donne pas un budget neuf."""
    fil = [
        HumanMessage(content="écris à la commission"),
        _appel(tokens=500),
        ToolMessage(content="Approuvé…", tool_call_id="e1", name="send_email"),
        _appel(tokens=700),
    ]

    assert model_calls(fil) == 2
    assert consumed_tokens(fil) == 1200
