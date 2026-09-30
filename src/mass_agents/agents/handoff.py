"""Les outils de transfert du superviseur.

Ce sont les seuls outils qu'il détient : il ne lit rien, n'écrit rien, il route.
Un superviseur qui aurait `list_events` « pour vérifier » referait le travail de
l'analyste avec un effort réglé pour du routage.

Ces outils ne sont **jamais exécutés**. Le superviseur lit l'appel que le modèle
a produit et le traduit en destination ; il n'y a pas de `ToolNode` derrière.
D'où l'implémentation vide : le corps ne sert qu'à donner à LangChain un schéma
et une description, qui sont la vraie matière — c'est cette description que le
modèle lit pour choisir.

Passer par des outils plutôt que par une sortie structurée n'est pas un détail :
un appel d'outil laisse dans le fil une trace lisible de la décision de routage,
avec sa justification, que la relecture d'un incident retrouve telle quelle.
"""

from __future__ import annotations

from typing import Final

from langchain_core.tools import BaseTool, tool

from mass_agents.agents.specialists import SPECIALISTS, Specialist

#: Préfixe des outils de transfert. Le nom du nœud s'en déduit par retrait, ce
#: qui évite une seconde table de correspondance à tenir à jour.
HANDOFF_PREFIX = "transfer_to_"


def handoff_tool_name(specialist_name: str) -> str:
    return f"{HANDOFF_PREFIX}{specialist_name}"


def destination_of(tool_name: str) -> str | None:
    """Le nœud visé par un appel d'outil, ou `None` si ce n'en est pas un."""
    if not tool_name.startswith(HANDOFF_PREFIX):
        return None
    return tool_name[len(HANDOFF_PREFIX) :]


def _build_handoff_tool(specialist: Specialist) -> BaseTool:
    @tool(
        handoff_tool_name(specialist.name),
        description=specialist.handoff_description,
    )
    def transfer(tache: str) -> str:
        """Jamais appelée : le superviseur lit l'appel, il ne l'exécute pas."""
        return f"Transfert vers {specialist.name} : {tache}"

    return transfer


#: Les outils de transfert, un par spécialiste, construits une fois.
#:
#: Ils ne se ferment sur aucun jeton — contrairement aux outils MCP — donc les
#: partager entre les runs est sans conséquence.
HANDOFF_TOOLS: Final[tuple[BaseTool, ...]] = tuple(
    _build_handoff_tool(specialist) for specialist in SPECIALISTS
)
