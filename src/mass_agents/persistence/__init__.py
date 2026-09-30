"""Tout ce qui touche la base : connexions, schéma, fils, journal.

Le reste du code ne connaît que les classes exportées ici. Aucun module de
graphe ou d'API n'écrit de SQL — c'est ce qui permet de tester le graphe sans
Postgres, en substituant un dépôt en mémoire.
"""

from mass_agents.persistence.approvals import ApprovalLog
from mass_agents.persistence.checkpointer import apply_migrations, create_checkpointer
from mass_agents.persistence.database import Database
from mass_agents.persistence.threads import Thread, ThreadRepository

__all__ = [
    "ApprovalLog",
    "Database",
    "Thread",
    "ThreadRepository",
    "apply_migrations",
    "create_checkpointer",
]
