"""Les trois agents spécialistes, et ce qui les distingue.

Chacun est décrit par une ligne de données — son nom, ses outils, son prompt,
son effort — plutôt que par une fonction de construction dédiée. Ajouter un
quatrième agent devient alors une entrée dans `SPECIALISTS` : ni le graphe ni le
superviseur n'ont à changer de forme.

Ces objets sont construits **par run**, parce qu'ils se ferment sur l'outillage
de l'appelant. C'est peu coûteux — aucun appel réseau — et c'est ce qui garantit
qu'un agent ne peut pas se servir du jeton du voisin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from langgraph.prebuilt import create_react_agent
from langgraph.pregel import Pregel

from mass_agents.agents.models import Effort, build_model
from mass_agents.agents.prompts import (
    ANALYST_PROMPT,
    EDITOR_PROMPT,
    OPERATIONS_PROMPT,
)
from mass_agents.tools import (
    ANALYST_TOOLS,
    EDITOR_TOOLS,
    OPERATIONS_TOOLS,
    MassToolset,
)

#: Noms des nœuds. Ils servent de destination aux outils de transfert, de clé
#: dans le graphe et d'étiquette dans le flux SSE : les fixer une fois évite
#: qu'un renommage laisse une arête pointer dans le vide.
ANALYST = "analyste"
EDITOR = "redacteur"
OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class Specialist:
    """Ce qui suffit à décrire un agent : ses outils, son prompt, son effort."""

    name: str
    tools: tuple[str, ...]
    prompt: str
    effort: Effort
    #: Ce que le superviseur lit pour décider de lui transférer la main.
    handoff_description: str


SPECIALISTS: Final[tuple[Specialist, ...]] = (
    Specialist(
        name=ANALYST,
        tools=ANALYST_TOOLS,
        prompt=ANALYST_PROMPT,
        effort="high",
        handoff_description=(
            "Transférer à l'analyste : toute question qui se répond en lisant "
            "— agenda, adhérents, fréquentation, finances, statistiques. "
            "Lecture seule, aucune écriture n'est possible depuis ce nœud."
        ),
    ),
    Specialist(
        name=EDITOR,
        tools=EDITOR_TOOLS,
        prompt=EDITOR_PROMPT,
        effort="medium",
        handoff_description=(
            "Transférer au rédacteur : préparer un évènement, un modèle de "
            "courriel, ou modifier un brouillon. Tout reste en brouillon et "
            "invisible du public ; rien n'est publié ni envoyé."
        ),
    ),
    Specialist(
        name=OPERATIONS,
        tools=OPERATIONS_TOOLS,
        prompt=OPERATIONS_PROMPT,
        effort="high",
        handoff_description=(
            "Transférer aux opérations : envoyer un courriel, pointer des "
            "présences. Ce sont les deux seuls gestes qui engagent "
            "l'association ; ils passeront par une validation humaine."
        ),
    ),
)

SPECIALISTS_BY_NAME: Final[dict[str, Specialist]] = {
    specialist.name: specialist for specialist in SPECIALISTS
}


def build_specialist(specialist: Specialist, toolset: MassToolset) -> Pregel:
    """L'agent, muni de son seul sous-ensemble d'outils.

    Sans checkpointer : le fil est persisté par le graphe parent, et donner un
    checkpointer à un sous-graphe dupliquerait l'état sans que personne le
    relise.
    """
    return create_react_agent(
        model=build_model(specialist.effort),
        tools=toolset.subset(specialist.tools),
        prompt=specialist.prompt,
        name=specialist.name,
    )
