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
from collections.abc import Iterable, Mapping

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from mass_agents.config import McpConfig, get_config
from mass_agents.domain import ToolsetError
from mass_agents.tools.catalog import REQUIRED_TOOLS

logger = logging.getLogger(__name__)

_SERVER_NAME = "mass"


class MassToolset:
    """Les outils d'un run, indexés par nom.

    Le découpage par sous-ensemble se fait ici et pas dans les agents : c'est ce
    qui permet de vérifier la répartition en lisant un seul fichier, et de la
    tester sans monter de modèle.
    """

    def __init__(self, tools: Iterable[BaseTool]) -> None:
        self._by_name: Mapping[str, BaseTool] = {tool.name: tool for tool in tools}

    def subset(self, names: Iterable[str]) -> list[BaseTool]:
        """Les outils demandés, dans l'ordre où ils sont déclarés.

        L'ordre compte : c'est celui dans lequel le modèle les découvre, et les
        lectures sont déclarées avant les écritures parce qu'une écriture bien
        informée commence par une lecture.
        """
        return [self._by_name[name] for name in names]

    def get(self, name: str) -> BaseTool:
        """Un outil par son nom, pour un appel hors de la boucle d'un agent.

        C'est ce dont le nœud de validation a besoin : il rejoue **le même**
        outil avec `confirmed: true`, sans repasser par un modèle.
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
