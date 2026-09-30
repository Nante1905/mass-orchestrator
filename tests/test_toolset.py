"""La classification des outils, et ce que le modèle en voit.

Deux garanties sont vérifiées ici sans modèle ni serveur. La première tient à la
classification : chaque outil appartient à une famille et une seule. La seconde
tient à l'écart entre ce que le modèle voit et ce que les nœuds exécutent :
`confirmed` n'apparaît jamais dans la requête envoyée à l'API, mais reste
disponible pour le nœud qui, seul, a le droit de le poser.
"""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import StructuredTool

from mass_agents.domain import ENGAGING_TOOLS, ToolsetError
from mass_agents.tools import (
    AGENT_TOOLS,
    CONFIRMATION_PARAM,
    DRAFT_TOOLS,
    ENGAGING_TOOL_ORDER,
    READ_TOOLS,
    REQUIRED_TOOLS,
    MassToolset,
)
from mass_agents.tools.toolset import _assert_complete

#: Les schémas de `mass-mcp` pour les deux écritures engageantes, tels que
#: l'adaptateur MCP les rend : un JSON Schema en dictionnaire.
_SCHEMAS = {
    "send_email": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "format": "email"},
            "group_id": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            CONFIRMATION_PARAM: {"type": "boolean", "default": False},
        },
        "required": ["subject", "body"],
    },
    "mark_attendance": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "registration_ids": {"type": "array", "items": {"type": "string"}},
            "attendance": {"type": "string", "enum": ["present", "registered"]},
            CONFIRMATION_PARAM: {"type": "boolean", "default": False},
        },
        "required": ["event_id", "registration_ids"],
    },
}


async def _inerte(**_: object) -> str:
    return "{}"


def _outil(nom: str) -> StructuredTool:
    return StructuredTool(
        name=nom,
        description=(
            f"Description de {nom} par mass-mcp : rappeler avec confirmed: true."
        ),
        args_schema=_SCHEMAS.get(nom, {"type": "object", "properties": {}}),
        coroutine=_inerte,
    )


def _outillage(noms=REQUIRED_TOOLS) -> MassToolset:
    return MassToolset([_outil(nom) for nom in noms])


def _engageants(schemas) -> dict[str, dict]:
    return {s["name"]: s for s in schemas if isinstance(s, dict)}


# -- la classification --------------------------------------------------------


def test_les_trois_familles_sont_disjointes():
    lectures, brouillons, engageants = (
        set(READ_TOOLS),
        set(DRAFT_TOOLS),
        set(ENGAGING_TOOL_ORDER),
    )

    assert lectures & brouillons == set()
    assert lectures & engageants == set()
    assert brouillons & engageants == set()


def test_chaque_outil_de_l_agent_apparait_une_seule_fois():
    assert len(AGENT_TOOLS) == len(set(AGENT_TOOLS))
    assert set(AGENT_TOOLS) == REQUIRED_TOOLS


def test_les_engageants_du_catalogue_sont_ceux_du_domaine():
    """Le domaine fait autorité : un outil engageant oublié au catalogue
    n'atteindrait pas l'agent, un outil en trop passerait sans validation."""
    assert set(ENGAGING_TOOL_ORDER) == ENGAGING_TOOLS


def test_l_ordre_des_outils_est_stable():
    """L'ordre fait partie du préfixe de chaque requête : il ne doit pas
    dépendre du hachage d'un ensemble, sous peine d'invalider le cache."""
    assert tuple(sorted(ENGAGING_TOOLS)) == ENGAGING_TOOL_ORDER
    assert AGENT_TOOLS == READ_TOOLS + DRAFT_TOOLS + ENGAGING_TOOL_ORDER


# -- ce que le modèle voit ----------------------------------------------------


def test_les_outils_sont_lies_dans_l_ordre_du_catalogue():
    schemas = _outillage().agent_schemas()

    noms = [s["name"] if isinstance(s, dict) else s.name for s in schemas]
    assert noms == list(AGENT_TOOLS)


def test_lectures_et_brouillons_sont_lies_tels_quels():
    outillage = _outillage()

    for schema in outillage.agent_schemas():
        if not isinstance(schema, dict):
            assert schema is outillage.get(schema.name)


def test_aucun_outil_engageant_n_expose_confirmed_au_modele():
    engageants = _engageants(_outillage().agent_schemas())

    assert set(engageants) == ENGAGING_TOOLS
    for schema in engageants.values():
        entree = schema["input_schema"]
        assert CONFIRMATION_PARAM not in entree["properties"]
        assert CONFIRMATION_PARAM not in entree["required"]
        assert entree["additionalProperties"] is False


def test_les_autres_parametres_sont_conserves():
    """Retirer `confirmed` ne doit rien retirer d'autre : un paramètre perdu
    ferait échouer l'aperçu sur un appel pourtant correct."""
    courriel = _engageants(_outillage().agent_schemas())["send_email"]

    assert set(courriel["input_schema"]["properties"]) == {
        "to",
        "group_id",
        "subject",
        "body",
    }
    assert courriel["input_schema"]["required"] == ["subject", "body"]


def test_la_description_de_mass_mcp_est_remplacee():
    """Celle du serveur dit « rappeler avec `confirmed: true` » : la montrer au
    modèle l'inviterait à poser un paramètre qu'il n'a plus."""
    for schema in _engageants(_outillage().agent_schemas()).values():
        assert CONFIRMATION_PARAM not in schema["description"]
        assert "mass-mcp" not in schema["description"]


def test_la_requete_envoyee_a_l_api_ne_porte_pas_confirmed():
    """Au-delà de notre fonction : ce que `ChatAnthropic` met réellement dans
    la requête. Aucun appel réseau — la liaison ne fait que préparer les
    paramètres."""
    modele = ChatAnthropic(model="claude-opus-5", api_key="sk-ant-test")

    lie = modele.bind_tools(_outillage().agent_schemas())

    for outil in lie.kwargs["tools"]:
        if outil["name"] in ENGAGING_TOOLS:
            assert CONFIRMATION_PARAM not in outil["input_schema"]["properties"]


def test_l_outil_execute_garde_confirmed():
    """Les nœuds d'aperçu et de validation appellent l'outil brut : c'est eux,
    et eux seuls, qui posent `confirmed`."""
    outil = _outillage().get("send_email")

    assert CONFIRMATION_PARAM in outil.args


# -- l'outillage et le serveur ------------------------------------------------


def test_un_outil_absent_echoue_avec_son_nom():
    outillage = _outillage(["list_events"])

    with pytest.raises(ToolsetError, match="send_email"):
        outillage.get("send_email")


def test_un_serveur_incomplet_est_refuse_au_demarrage():
    """Un décalage de version se voit ici ou pas du tout.

    Sans ce contrôle, l'agent découvrirait au troisième tour qu'il lui manque un
    outil et improviserait avec ce qui reste.
    """
    partiel = REQUIRED_TOOLS - {"query_analytics", "send_email"}

    with pytest.raises(ToolsetError, match="query_analytics"):
        _assert_complete(_outillage(partiel), set(partiel))


def test_un_serveur_complet_passe():
    _assert_complete(_outillage(), set(REQUIRED_TOOLS))
