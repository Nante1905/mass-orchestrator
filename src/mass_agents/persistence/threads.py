"""Propriété et index des conversations.

Le checkpointer sait tout de l'état d'un fil et rien de son propriétaire : pour
lui, un `thread_id` est une clé opaque. Cette table est ce qui manque pour
répondre à deux questions que le checkpointer ne sait pas traiter — « quels
fils sont les miens ? » et, plus important, « ce fil est-il le vôtre ? ».

Sans elle, un administrateur qui devinerait un identifiant lirait la
conversation d'un collègue : les données personnelles qui transitent par
l'agent sont exactement celles que le back-office protège.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from mass_agents.domain import ThreadBusyError, ThreadNotFoundError

_INSERT = """
    insert into thread (id, owner_user_id, title)
    values (%s, %s, %s)
    returning id, owner_user_id, title, created_at, updated_at
"""

# Le propriétaire fait partie de la clause `where` et non d'un contrôle en
# Python : une autorisation qui se vérifie après la lecture est une
# autorisation qu'on peut oublier de vérifier.
_SELECT_OWNED = """
    select id, owner_user_id, title, created_at, updated_at
    from thread
    where id = %s and owner_user_id = %s
"""

_SELECT_BY_OWNER = """
    select id, owner_user_id, title, created_at, updated_at
    from thread
    where owner_user_id = %s
    order by updated_at desc
    limit %s offset %s
"""

# Prend le verrou du fil, s'il est libre ou abandonné, en une seule instruction :
# deux requêtes simultanées ne peuvent pas le prendre toutes les deux.
#
# `coalesce` sur le titre : il est posé une fois, au premier message, et les
# tours suivants ne doivent pas l'écraser — surtout pas par le texte d'une
# relance de deux mots.
_ACQUIRE = """
    update thread
    set run_started_at = now(), updated_at = now(), title = coalesce(title, %s)
    where id = %s and owner_user_id = %s
      and (run_started_at is null
           or run_started_at < now() - make_interval(secs => %s))
    returning run_started_at
"""

# Ne rend que le verrou qu'on détient : si le nôtre a été jugé abandonné et
# repris par un autre run, celui-là ne doit pas perdre le sien.
_RELEASE = """
    update thread
    set run_started_at = null
    where id = %s and run_started_at = %s
"""


@dataclass(frozen=True, slots=True)
class RunLease:
    """Le verrou d'un fil, tel qu'un run le détient.

    L'heure de prise sert de jeton : c'est elle qui permet de ne rendre que son
    propre verrou.
    """

    thread_id: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Thread:
    id: str
    owner_user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class ThreadRepository:
    """Accès aux fils, toujours cadré par leur propriétaire."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, owner_user_id: str, title: str | None = None) -> Thread:
        thread_id = str(uuid.uuid4())
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_INSERT, (thread_id, owner_user_id, title))
            row = await cursor.fetchone()
        return _to_thread(row) # type: ignore

    async def get_owned(self, thread_id: str, owner_user_id: str) -> Thread:
        """Le fil, à condition qu'il appartienne à cet administrateur.

        Lève `ThreadNotFoundError` dans les deux cas de refus : l'inexistence et
        l'appartenance à un autre se ressemblent volontairement, sinon l'erreur
        elle-même dirait quels identifiants sont réels.
        """
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_SELECT_OWNED, (thread_id, owner_user_id))
            row = await cursor.fetchone()
        if row is None:
            raise ThreadNotFoundError(f"Aucune conversation {thread_id} pour ce compte")
        return _to_thread(row) # type: ignore

    async def list_by_owner(
        self, owner_user_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[Thread]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                _SELECT_BY_OWNER, (owner_user_id, limit, offset)
            )
            rows = await cursor.fetchall()
        return [_to_thread(row) for row in rows] # type: ignore

    async def acquire_run(
        self,
        thread_id: str,
        owner_user_id: str,
        *,
        stale_after_s: float,
        title: str | None = None,
    ) -> RunLease:
        """Le verrou du fil, pour la durée d'un run.

        Sert aussi d'autorisation : on ne prend que le verrou d'un fil qu'on
        possède. Un verrou plus ancien que `stale_after_s` est considéré comme
        abandonné — un processus arrêté en plein run ne bloque pas le fil pour
        toujours.

        Lève `ThreadNotFoundError` si le fil n'est pas à l'appelant,
        `ThreadBusyError` si un run y est déjà en cours.
        """
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                _ACQUIRE, (title, thread_id, owner_user_id, stale_after_s)
            )
            row = await cursor.fetchone()

        if row is None:
            # Rien de pris : soit le fil n'est pas à l'appelant, soit il est
            # occupé. La propriété se vérifie en premier, pour ne pas dire à un
            # curieux qu'un identifiant existe.
            await self.get_owned(thread_id, owner_user_id)
            raise ThreadBusyError(
                "Une demande est déjà en cours sur cette conversation. "
                "Attendez sa fin avant d'en envoyer une autre."
            )

        return RunLease(thread_id=thread_id, started_at=row["run_started_at"])

    async def release_run(self, lease: RunLease) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(_RELEASE, (lease.thread_id, lease.started_at))


def _to_thread(row: dict) -> Thread:
    return Thread(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
