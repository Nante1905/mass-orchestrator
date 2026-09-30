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

from mass_agents.domain import ThreadNotFoundError

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

# `coalesce` sur le titre : il est posé une fois, au premier message, et les
# tours suivants ne doivent pas l'écraser — surtout pas par le texte d'une
# relance de deux mots.
_TOUCH = """
    update thread
    set updated_at = now(), title = coalesce(title, %s)
    where id = %s and owner_user_id = %s
    returning id
"""


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

    async def touch(
        self, thread_id: str, owner_user_id: str, *, title: str | None = None
    ) -> None:
        """Marque le fil comme actif, et lui donne un titre s'il n'en a pas.

        Sert aussi d'autorisation pour un run : une mise à jour qui ne touche
        aucune ligne signifie que le fil n'est pas à cet appelant, et le run ne
        doit pas commencer.
        """
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_TOUCH, (title, thread_id, owner_user_id))
            row = await cursor.fetchone()
        if row is None:
            raise ThreadNotFoundError(f"Aucune conversation {thread_id} pour ce compte")


def _to_thread(row: dict) -> Thread:
    return Thread(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
