"""Le routage, et les plafonds que le superviseur porte.

Critère d'acceptation du lot 4 : trois requêtes types routent vers les trois
agents. Le modèle est remplacé — ce qui est vérifié ici n'est pas qu'Anthropic
choisisse bien, mais que le nœud traduise fidèlement un choix en destination,
et qu'il laisse derrière lui une transcription valide.

Ce dernier point n'est pas cosmétique : un `tool_use` sans `tool_result`
correspondant fait rejeter la requête suivante par l'API, et l'erreur se
manifeste un tour plus tard, loin de sa cause.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from mass_agents.agents import handoff_tool_name
from mass_agents.graph.nodes import supervisor as module
from mass_agents.graph.nodes.supervisor import supervisor_node


class FauxModele:
    """Rend la réponse qu'on lui a donnée, et note ce qu'on lui a lié."""

    def __init__(self, reponse: AIMessage) -> None:
        self._reponse = reponse
        self.outils_lies: list[str] = []

    def bind_tools(self, tools):
        self.outils_lies = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages, config=None):
        self._messages_recus = messages
        return self._reponse


def _transfert(destination: str, tache: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": handoff_tool_name(destination),
                "args": {"tache": tache},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _installe(monkeypatch, reponse: AIMessage) -> FauxModele:
    modele = FauxModele(reponse)
    monkeypatch.setattr(module, "build_model", lambda effort: modele)
    return modele


def _etat(texte: str, turns: int = 0):
    return {"messages": [HumanMessage(content=texte)], "turns": turns}


async def test_une_question_de_lecture_route_vers_l_analyste(monkeypatch):
    _installe(monkeypatch, _transfert("analyste", "compter les présents"))

    commande = await supervisor_node(
        _etat("combien de présents à la Nuit des étoiles ?"), {"configurable": {}}
    )

    assert commande.goto == "analyste"
    assert commande.update["next"] == "analyste"
    assert commande.update["turns"] == 1


async def test_une_preparation_route_vers_le_redacteur(monkeypatch):
    _installe(monkeypatch, _transfert("redacteur", "créer un brouillon"))

    commande = await supervisor_node(
        _etat("prépare un évènement pour samedi"), {"configurable": {}}
    )

    assert commande.goto == "redacteur"


async def test_un_envoi_route_vers_les_operations(monkeypatch):
    _installe(monkeypatch, _transfert("operations", "écrire au groupe"))

    commande = await supervisor_node(
        _etat("écris à la commission observation"), {"configurable": {}}
    )

    assert commande.goto == "operations"


async def test_le_superviseur_ne_detient_que_des_outils_de_transfert(monkeypatch):
    modele = _installe(monkeypatch, _transfert("analyste", "lire"))

    await supervisor_node(_etat("bonjour"), {"configurable": {}})

    assert modele.outils_lies == [
        "transfer_to_analyste",
        "transfer_to_redacteur",
        "transfer_to_operations",
    ]


async def test_une_reponse_sans_transfert_termine_le_run(monkeypatch):
    """Pas d'outil appelé : c'est la réponse finale, ou la question qui manquait."""
    _installe(monkeypatch, AIMessage(content="Il y avait 42 présents."))

    commande = await supervisor_node(_etat("et alors ?"), {"configurable": {}})

    assert commande.goto == END
    assert commande.update["next"] is None
    assert commande.update["messages"][0].content == "Il y avait 42 présents."


async def test_chaque_appel_recoit_son_resultat(monkeypatch):
    """Y compris ceux qu'on n'honore pas : sans quoi la transcription est invalide."""
    def _call(nom: str, call_id: str) -> dict:
        return {
            "name": nom,
            "args": {"tache": "…"},
            "id": call_id,
            "type": "tool_call",
        }

    double = AIMessage(
        content="",
        tool_calls=[
            _call("transfer_to_analyste", "c1"),
            _call("transfer_to_operations", "c2"),
        ],
    )
    _installe(monkeypatch, double)

    commande = await supervisor_node(_etat("compte puis écris"), {"configurable": {}})

    resultats = [m for m in commande.update["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id for m in resultats} == {"c1", "c2"}
    # Un seul agent travaille à la fois.
    assert commande.goto == "analyste"
    assert "ignoré" in resultats[1].content


async def test_un_fil_finissant_par_l_assistant_recoit_un_tour_de_decision(monkeypatch):
    """Sans ce tour, l'API lit la requête comme une amorce de réponse.

    Le cas survient à chaque retour de spécialiste : la transcription se clôt
    sur un message d'assistant, et Anthropic refuse — « assistant prefill ».
    Le graphe échouait donc au moment précis où il allait conclure, après avoir
    déjà tout payé.
    """
    modele = _installe(monkeypatch, AIMessage(content="Voilà."))

    await supervisor_node(
        {
            "messages": [
                HumanMessage(content="combien de présents ?"),
                AIMessage(content="Il y en avait 42.", name="analyste"),
            ],
            "turns": 1,
        },
        {"configurable": {}},
    )

    dernier = modele._messages_recus[-1]
    assert isinstance(dernier, HumanMessage)
    assert "réponse finale" in dernier.content


async def test_le_premier_message_ne_recoit_pas_de_tour_de_decision(monkeypatch):
    """Il ferait double emploi, et parlerait d'agents qui n'ont pas travaillé."""
    modele = _installe(monkeypatch, _transfert("analyste", "compter"))

    await supervisor_node(_etat("combien de présents ?"), {"configurable": {}})

    assert modele._messages_recus[-1].content == "combien de présents ?"


async def test_le_plafond_de_tours_arrete_le_run_avant_l_appel(monkeypatch):
    """Le plafond est contrôlé avant le modèle : un dépassement constaté après
    coup a déjà été facturé."""
    modele = _installe(monkeypatch, _transfert("analyste", "encore"))
    plafond = module.get_config().limits.max_turns

    commande = await supervisor_node(
        _etat("recommence", turns=plafond), {"configurable": {}}
    )

    assert commande.goto == END
    assert "transferts entre agents" in commande.update["messages"][0].content
    assert commande.update["pending_action"] is None
    assert not hasattr(modele, "_messages_recus")


async def test_le_plafond_de_jetons_arrete_le_run(monkeypatch):
    modele = _installe(monkeypatch, _transfert("analyste", "encore"))
    plafond = module.get_config().limits.max_tokens_per_thread

    lourd = AIMessage(
        content="…",
        usage_metadata={
            "input_tokens": plafond,
            "output_tokens": 0,
            "total_tokens": plafond,
        },
    )

    commande = await supervisor_node(
        {"messages": [HumanMessage(content="et ensuite ?"), lourd], "turns": 1},
        {"configurable": {}},
    )

    assert commande.goto == END
    assert "volume maximal" in commande.update["messages"][0].content
    assert not hasattr(modele, "_messages_recus")
