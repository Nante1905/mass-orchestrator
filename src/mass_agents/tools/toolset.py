"""L'outillage MCP d'un run, lié au jeton de celui qui l'a lancé.

**Interdit : une instance au niveau du module.** Un `TOOLS = await
get_tools()` évalué au démarrage figerait le jeton du premier utilisateur pour
tout le monde — exactement ce que le mode sans session de `mass-mcp` a été conçu
pour empêcher. Le serveur MCP ne détient aucune identité : il relaie le jeton
qu'on lui présente, et c'est le backend qui tranche en relisant le rôle en base.
Partager un outillage entre deux administrateurs reviendrait donc à leur donner
les mêmes droits.

D'où la forme : une fonction qui construit, et un objet qui ne se met pas en
cache. Le coût est un aller-retour HTTP par run, ce qui est le prix de la
séparation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_mcp_adapters.client import MultiServerMCPClient

from mass_agents.config import McpConfig, get_config
from mass_agents.domain import ENGAGING_TOOLS, ToolsetError
from mass_agents.tools.catalog import (
    AGENT_TOOLS,
    CONFIRMATION_PARAM,
    ENGAGING_DESCRIPTIONS,
    REQUIRED_TOOLS,
)

logger = logging.getLogger(__name__)

_SERVER_NAME = "mass"


class MassToolset:
    """Les outils d'un run, indexés par nom.

    Deux vues sur les mêmes outils : ce que le modèle voit (`agent_schemas`) et
    ce que les nœuds exécutent (`get`). C'est l'écart entre les deux qui tient
    `confirmed` hors de portée du modèle.
    """

    def __init__(self, tools: Iterable[BaseTool]) -> None:
        self._by_name: Mapping[str, BaseTool] = {tool.name: tool for tool in tools}

    def agent_schemas(self) -> list[BaseTool | dict[str, Any]]:
        """Ce qu'on lie au modèle, dans l'ordre du catalogue.

        Lectures et brouillons sont liés tels que `mass-mcp` les décrit. Les
        outils engageants sont remplacés par une définition au format Anthropic
        — même nom, description réécrite, schéma **sans `confirmed`** — que
        `bind_tools` transmet sans la retoucher.

        Retirer le paramètre du schéma ne suffit pas à lui seul : un modèle peut
        produire un argument hors schéma. La garantie est posée à l'exécution,
        par le nœud d'aperçu qui force `confirmed` à faux. Le retrait sert à ne
        pas mettre sous les yeux du modèle un levier qu'il n'a pas à toucher.
        """
        return [
            _engaging_schema(tool) if tool.name in ENGAGING_TOOLS else tool
            for tool in map(self.get, AGENT_TOOLS)
        ]

    def get(self, name: str) -> BaseTool:
        """L'outil MCP tel qu'il est, pour les nœuds qui l'exécutent.

        C'est lui, et non la définition liée au modèle, que l'aperçu et la
        validation appellent : `confirmed` y existe, et c'est eux qui le posent.
        """
        try:
            return self._by_name[name]
        except KeyError as error:
            raise ToolsetError(
                f"L'outil {name} n'est pas exposé par le serveur MCP"
            ) from error

    def __len__(self) -> int:
        return len(self._by_name)


async def build_mass_toolset(
    admin_token: str, config: McpConfig | None = None
) -> MassToolset:
    """L'outillage du run, portant l'`Authorization` de l'appelant.

    Un jeton invalide n'échoue pas ici : `mass-mcp` ne vérifie pas les
    signatures, il relaie. L'échec arrive au premier appel d'outil, avec le
    message « inutile de réessayer » que le serveur rend déjà — et que les
    prompts des agents savent lire comme une fin de partie plutôt que comme une
    erreur de paramètre.
    """
    mcp = config or get_config().mcp

    client = MultiServerMCPClient(
        {
            _SERVER_NAME: {
                "transport": "streamable_http",
                "url": mcp.url,
                "headers": {"Authorization": f"Bearer {admin_token}"},
                "timeout": mcp.timeout_s,
            }
        }
    )

    try:
        tools = await client.get_tools()
    except Exception as error:
        raise ToolsetError(
            f"Serveur MCP injoignable sur {mcp.url} : {error}"
        ) from error

    toolset = MassToolset(tools)
    _assert_complete(toolset, {tool.name for tool in tools})
    return toolset


def _engaging_schema(tool: BaseTool) -> dict[str, Any]:
    """La définition d'un outil engageant, telle que le modèle la voit.

    Seuls le nom et le schéma viennent de `mass-mcp` ; le schéma perd
    `confirmed`. La description est la nôtre (voir `ENGAGING_DESCRIPTIONS`).
    """
    parameters = _portable(convert_to_openai_tool(tool)["function"]["parameters"])

    return {
        "name": tool.name,
        "description": ENGAGING_DESCRIPTIONS[tool.name],
        "input_schema": {
            **parameters,
            "properties": {
                key: value
                for key, value in parameters.get("properties", {}).items()
                if key != CONFIRMATION_PARAM
            },
            "required": [
                key
                for key in parameters.get("required", [])
                if key != CONFIRMATION_PARAM
            ],
            "additionalProperties": False,
        },
    }


#: Les assertions de regex (`(?=`, `(?!`, `(?<=`, `(?<!`), que l'API OpenAI
#: refuse dans un schéma d'outil (`invalid_json_schema`).
_LOOKAROUND = re.compile(r"\(\?<?[=!]")


def _portable(schema: Any) -> Any:
    """Le schéma sans les `pattern` à assertions, que tous les fournisseurs lisent.

    `z.email()` produit dans `mass-mcp` un motif à lookahead : Anthropic le
    tolère, OpenAI rejette toute la requête. Le retirer ne relâche rien —
    `format: email` reste sous les yeux du modèle, et `mass-mcp` revalide
    l'adresse à l'aperçu comme à l'envoi.
    """
    if isinstance(schema, dict):
        return {
            key: _portable(value)
            for key, value in schema.items()
            if not (
                key == "pattern"
                and isinstance(value, str)
                and _LOOKAROUND.search(value)
            )
        }
    if isinstance(schema, list):
        return [_portable(item) for item in schema]
    return schema


def _assert_complete(toolset: MassToolset, exposed: set[str]) -> None:
    """Refuse un outillage incomplet, bruyamment.

    Un décalage de version entre les deux services se voit ici ou pas du tout :
    sans ce contrôle, l'agent découvrirait au troisième tour qu'il lui manque un
    outil et improviserait avec ce qui reste. Un run qui ne peut pas commencer
    correctement vaut mieux qu'un run qui répond à côté.
    """
    missing = sorted(REQUIRED_TOOLS - exposed)
    if missing:
        raise ToolsetError(
            "Le serveur MCP n'expose pas les outils attendus : "
            f"{', '.join(missing)}. Les deux services sont probablement "
            "désynchronisés."
        )

    logger.debug("Outillage construit : %d outils", len(toolset))
