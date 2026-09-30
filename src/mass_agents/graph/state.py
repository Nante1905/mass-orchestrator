"""L'état du graphe. Deux champs, et pas un de plus.

Ce qui n'est **pas** ici est aussi important que ce qui y est : le jeton de
l'administrateur n'y figure pas, et n'y figurera pas. L'état est sérialisé dans
Postgres à chaque superstep ; y placer un jeton reviendrait à le persister en
clair, avec une durée de vie qui n'est plus la sienne. Le jeton voyage par la
configuration du run — voir `context.py`.

Aucun compteur non plus : les plafonds se lisent dans les messages eux-mêmes
(voir `limits.py`), ce qui leur évite de dériver de ce qui s'est réellement
passé.
"""

from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from mass_agents.domain import PendingAction


class OrchestratorState(TypedDict):
    """Ce que les nœuds se passent."""

    messages: Annotated[list[AnyMessage], add_messages]

    #: L'écriture engageante dont l'aperçu est calculé et qui attend une
    #: décision humaine. Posée par le nœud d'aperçu, remise à `None` dès que la
    #: décision est appliquée.
    pending_action: NotRequired[PendingAction | None]
