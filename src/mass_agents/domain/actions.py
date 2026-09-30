"""Le vocabulaire de la validation humaine.

C'est le contrat entre trois endroits qui ne se connaissent pas : le nœud qui
détecte qu'une écriture attend une relecture, l'interface qui la présente, et le
nœud qui exécute la décision. Le poser ici plutôt que de faire circuler des
dictionnaires évite qu'un renommage de champ passe inaperçu jusqu'à la
production.

Ces objets traversent le checkpointer : ils sont sérialisés avec l'état du
graphe et relus après un redémarrage. D'où le choix de modèles pydantic, que le
sérialiseur de LangGraph sait rendre et reconstruire.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PendingAction(BaseModel):
    """Une écriture engageante, préparée mais pas faite.

    `arguments` et `preview` sont conservés ensemble et c'est essentiel : la
    reprise rejoue **le même** appel avec `confirmed: true`, et l'aperçu est ce
    qu'un humain a réellement lu. Les dissocier ouvrirait la porte à un envoi
    dont le corps n'est plus celui qui a été approuvé.
    """

    tool_name: str
    arguments: dict[str, Any]
    preview: dict[str, Any]
    tool_call_id: str


class ApprovalRequest(BaseModel):
    """Ce que `interrupt()` remonte à l'appelant.

    L'aperçu rendu par `mass-mcp` est repris tel quel : destinataires résolus,
    corps intégral, nombre de courriels. Le reformater ici ferait diverger ce
    qui est montré de ce qui a été calculé — et c'est justement l'aperçu, et non
    nos arguments, qui engage le nom de l'association.
    """

    kind: Literal["approval_request"] = "approval_request"
    tool_name: str
    title: str
    consequence: str
    preview: dict[str, Any]


class ApprovalDecision(BaseModel):
    """Ce que l'appelant renvoie dans `Command(resume=…)`.

    `approved` n'a pas de valeur par défaut : une reprise dont on ne saurait pas
    lire la décision doit échouer à la validation, jamais retomber sur « oui ».
    """

    approved: bool
    reason: str | None = Field(default=None, max_length=500)


#: Les outils qui exigent une relecture humaine, par leur nom MCP.
#:
#: La liste est une donnée et non une convention de nommage : un outil ajouté
#: côté `mass-mcp` qui écrirait sans figurer ici passerait la validation sans
#: s'arrêter. `mass-mcp` refuse toujours d'écrire sans `confirmed`, ce qui
#: transforme l'oubli en échec visible plutôt qu'en écriture silencieuse — mais
#: la liste reste le premier endroit à mettre à jour.
ENGAGING_TOOLS: frozenset[str] = frozenset({"send_email", "mark_attendance"})

#: Ce que chaque écriture engage, dit en une phrase à la personne qui relit.
CONSEQUENCES: dict[str, str] = {
    "send_email": (
        "Un courriel envoyé ne se rappelle pas : il part de la boîte de "
        "l'association et engage son nom."
    ),
    "mark_attendance": (
        "Chaque personne nouvellement pointée présente reçoit son lien d'avis "
        "par courriel. Un pointage par erreur envoie un vrai message."
    ),
}
