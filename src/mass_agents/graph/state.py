"""L'état du graphe. Quatre champs, et pas un de plus.

Ce qui n'est **pas** ici est aussi important que ce qui y est : le jeton de
l'administrateur n'y figure pas, et n'y figurera pas. L'état est sérialisé dans
Postgres à chaque superstep ; y placer un jeton reviendrait à le persister en
clair, avec une durée de vie qui n'est plus la sienne. Le jeton voyage par la
configuration du run — voir `context.py`, où la précaution est détaillée.

`turns` mérite un mot : il est dans l'état plutôt que dans une variable de run
parce qu'un fil repris après un `interrupt()` doit retrouver son compteur. Le
remettre à zéro à chaque reprise donnerait un plafond qu'il suffit de faire
valider une fois pour contourner.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from mass_agents.domain import PendingAction


class OrchestratorState(TypedDict):
    """Ce que les nœuds se passent."""

    messages: Annotated[list[AnyMessage], add_messages]

    #: Le nœud vers lequel le superviseur vient de router, à titre de trace.
    #: Le routage lui-même se fait par `Command(goto=…)` ; ce champ sert à
    #: l'interface, qui affiche quel agent a la main.
    next: NotRequired[str | None]

    #: L'écriture engageante préparée et pas encore faite, s'il y en a une.
    #: Remise à `None` dès que la décision est appliquée : la laisser traîner
    #: ferait revalider la même action au tour suivant.
    pending_action: NotRequired[PendingAction | None]

    #: Nombre de transferts effectués depuis le début du fil.
    turns: NotRequired[int]


def new_messages(
    before: Sequence[AnyMessage], after: Sequence[AnyMessage]
) -> list[AnyMessage]:
    """Ce qu'un sous-graphe a ajouté, et lui seul.

    Les agents reçoivent le fil complet et le rendent complet : renvoyer leur
    sortie telle quelle ferait recopier tout l'historique à chaque tour, que le
    réducteur `add_messages` dédoublonnerait par identifiant — mais au prix
    d'une charge utile qui double à chaque passage.
    """
    return list(after[len(before) :])
