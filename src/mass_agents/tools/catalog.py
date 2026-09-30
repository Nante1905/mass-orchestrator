"""La répartition des outils entre les agents.

Le découpage est fait par **niveau d'engagement**, pas par domaine métier. Un
agent qui ne détient pas `send_email` ne peut pas l'appeler, quelle que soit la
formulation de la demande : c'est une garantie de construction, pas une
consigne au modèle.

Le recouvrement des outils de lecture est voulu. `opérations` doit pouvoir
résoudre « les présents de samedi » en identifiants `EMR…` avant de proposer
quoi que ce soit — lui retirer `get_event_details` au nom d'une répartition
propre l'obligerait à agir sur des clés qu'il n'a pas vérifiées.

La liste est une donnée, lue par le constructeur de l'outillage. Un outil ajouté
à `mass-mcp` n'atteint aucun agent tant qu'il n'a pas été rangé ici.
"""

from __future__ import annotations

from typing import Final

ANALYST_TOOLS: Final[tuple[str, ...]] = (
    "list_events",
    "get_event_details",
    "list_members",
    "get_member_details",
    "get_attendance_stats",
    "get_finance_summary",
    "query_analytics",
)
"""Lecture seule. Aucune écriture n'est atteignable depuis ce nœud."""

EDITOR_TOOLS: Final[tuple[str, ...]] = (
    "create_event_draft",
    "update_draft",
    "create_email_template",
    "list_events",
    "get_event_details",
)
"""Écrit, mais tout reste en brouillon et invisible du public.

`mass-mcp` force `status: draft` à l'écriture et refuse de modifier ce qui n'est
plus un brouillon : le rédacteur ne peut pas publier même s'il le voulait.
"""

OPERATIONS_TOOLS: Final[tuple[str, ...]] = (
    "mark_attendance",
    "send_email",
    "get_event_details",
    "list_members",
)
"""Engage l'association — d'où le nœud de validation obligatoire en aval."""

#: Tous les outils dont un run peut avoir besoin, sans doublon.
#:
#: Sert à l'appel unique au serveur MCP : les trois sous-ensembles se découpent
#: ensuite en mémoire, sans aller-retour supplémentaire.
REQUIRED_TOOLS: Final[frozenset[str]] = frozenset(
    ANALYST_TOOLS + EDITOR_TOOLS + OPERATIONS_TOOLS
)
