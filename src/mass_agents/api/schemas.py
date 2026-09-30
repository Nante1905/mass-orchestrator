"""Les charges utiles des routes.

Les bornes ne sont pas décoratives. `message` est plafonné parce qu'un corps de
requête sans limite se transforme en facture de jetons, et `reason` parce
qu'elle est destinée à un journal qu'on relit — pas à porter un roman.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from mass_agents.persistence import Thread


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class RunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)


class ResumeRequest(BaseModel):
    """La décision humaine sur une écriture engageante.

    `approved` n'a pas de valeur par défaut : une reprise mal formée doit être
    refusée par la validation de schéma, jamais interprétée comme un accord.
    """

    approved: bool
    reason: str | None = Field(default=None, max_length=500)


class ThreadSummary(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, thread: Thread) -> ThreadSummary:
        return cls(
            id=thread.id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )
