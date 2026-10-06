"""La classification des outils, par ce que leur appel engage.

Trois familles, et c'est elles — pas une répartition entre agents — qui décident
du chemin dans le graphe :

- **lecture** : rien n'est modifié, l'appel s'exécute directement ;
- **brouillon** : écrit, mais tout reste invisible du public et se défait ;
- **engageant** : sort de l'association et ne se défait pas. L'appel ne
  s'exécute jamais depuis la boucle de l'agent : il passe par l'aperçu, puis
  par la validation humaine.

La liste est une donnée, lue par le constructeur de l'outillage. Un outil ajouté
à `mass-mcp` n'atteint pas l'agent tant qu'il n'a pas été rangé ici.
"""

from __future__ import annotations

from typing import Final

from mass_agents.domain import ENGAGING_TOOLS

READ_TOOLS: Final[tuple[str, ...]] = (
    "list_events",
    "get_event_details",
    "get_event_feedbacks",
    "list_members",
    "get_member_details",
    "get_attendance_stats",
    "get_finance_summary",
    "query_analytics",
    "generate_graph",
)
"""Rien n'est modifié.

`generate_graph` ne lit rien non plus : il met en forme des chiffres déjà lus.
Il est rangé ici parce qu'il n'a aucun effet de bord — c'est ce critère, et non
la lecture au sens strict, qui décide qu'un appel s'exécute sans détour.
"""

DRAFT_TOOLS: Final[tuple[str, ...]] = (
    "create_event_draft",
    "update_draft",
    "create_email_template",
)
"""Écrit, mais tout reste en brouillon.

`mass-mcp` force `status: draft` à l'écriture et refuse de modifier ce qui n'est
plus un brouillon : l'agent ne peut pas publier même s'il le voulait.
"""

#: Les outils engageants, dans un ordre fixe. Le `frozenset` du domaine n'en a
#: pas, et l'ordre des outils fait partie du préfixe de chaque requête : un
#: ordre qui varierait d'un processus à l'autre invaliderait le cache de prompt.
ENGAGING_TOOL_ORDER: Final[tuple[str, ...]] = tuple(sorted(ENGAGING_TOOLS))

#: Ce que l'agent détient, dans l'ordre où il le découvre : lire, préparer,
#: engager — une écriture bien informée commence par une lecture.
AGENT_TOOLS: Final[tuple[str, ...]] = READ_TOOLS + DRAFT_TOOLS + ENGAGING_TOOL_ORDER

#: Tous les outils dont un run a besoin. Sert à l'appel unique au serveur MCP et
#: au contrôle de complétude au démarrage du run.
REQUIRED_TOOLS: Final[frozenset[str]] = frozenset(AGENT_TOOLS)

#: Le paramètre par lequel `mass-mcp` distingue l'aperçu de l'écriture.
#:
#: Il n'est **jamais** montré au modèle. `mass-mcp` ne peut pas savoir qui le
#: pose : un `confirmed: true` écrit par le modèle — par erreur ou sous
#: l'influence d'un texte injecté dans les données lues — ferait partir le
#: courriel sans relecture. Seul le nœud de validation le pose, après décision
#: humaine.
CONFIRMATION_PARAM: Final[str] = "confirmed"

#: Les descriptions que le modèle lit pour les outils engageants.
#:
#: Elles remplacent celles de `mass-mcp`, écrites pour un client qui confirme
#: lui-même (« rappeler avec `confirmed: true` »). Ici, l'agent ne confirme
#: jamais : il propose, et le résultat de l'outil lui dit ce qu'un humain a
#: décidé.
ENGAGING_DESCRIPTIONS: Final[dict[str, str]] = {
    "send_email": (
        "Écrit à une adresse (`to`), ou à tous les comptes d'une liste de "
        "diffusion (`group_id`) — l'un ou l'autre, jamais les deux.\n\n"
        "L'appel ne fait pas partir le courriel : il le soumet à "
        "l'administrateur, qui en relit l'aperçu — destinataires résolus, "
        "objet, corps intégral — puis approuve ou refuse. Le résultat de "
        "l'outil dit ce qui a été décidé et, en cas d'envoi, ce qui est "
        "parti.\n\n"
        "Un courriel envoyé ne se rappelle pas : il part de la boîte de "
        "l'association et engage son nom. Le corps est interprété en HTML — "
        "les retours à la ligne doivent être des balises. Pour partir d'un "
        "modèle enregistré, le relire d'abord avec `query_analytics` sur "
        "`email_template` et y substituer les {{variables}}."
    ),
    "mark_attendance": (
        "Marque un ou plusieurs inscrits présents à un évènement, ou annule "
        "leur pointage. Les identifiants attendus sont ceux des inscriptions "
        "(`EMR…` pour un membre, `EPR…` pour un visiteur), rendus par "
        "`get_event_details`.\n\n"
        "L'appel n'écrit rien : il soumet le pointage à l'administrateur, qui "
        "en relit la liste nominative puis approuve ou refuse. Le résultat de "
        "l'outil dit ce qui a été décidé.\n\n"
        "Chaque personne nouvellement pointée présente reçoit par courriel son "
        "lien d'avis sur l'évènement : un pointage par erreur envoie un vrai "
        "message."
    ),
}
