"""Journal des décisions humaines sur les écritures engageantes.

Ce qui est tracé n'est pas l'activité de l'agent — les lectures ne regardent
personne, les brouillons se défont. C'est ce qu'un humain a approuvé, et
l'aperçu qu'il avait sous les yeux au moment de le faire.

Conserver l'aperçu et pas seulement les arguments est le point de la table :
six mois plus tard, « `group_id: G0003` » ne dit pas à qui le courriel est
parti, alors que l'aperçu porte les adresses résolues et le corps intégral.

Un refus est enregistré au même titre qu'une approbation. C'est ce qui permet de
distinguer, en relisant, une écriture qui n'a pas eu lieu parce que personne ne
l'a demandée d'une écriture qu'on a explicitement refusée.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from mass_agents.auth.identity import AdminIdentity
from mass_agents.domain import PendingAction

logger = logging.getLogger(__name__)

_INSERT = """
    insert into approval_log (
        thread_id, decided_by_user_id, decided_by_email,
        tool_name, approved, reason, preview, arguments, outcome
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class ApprovalLog:
    """Écriture seule, depuis le nœud de validation."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(
        self,
        *,
        thread_id: str,
        admin: AdminIdentity,
        action: PendingAction,
        approved: bool,
        reason: str | None,
        outcome: dict[str, Any] | None,
    ) -> None:
        """Trace la décision. Un échec ici n'annule pas ce qui a été fait.

        L'ordre importe : le journal est écrit **après** l'appel confirmé, donc
        `outcome` dit ce qui s'est réellement passé. Faire échouer le run parce
        que la trace n'a pas pu s'écrire laisserait l'utilisateur croire que le
        courriel n'est pas parti alors qu'il l'est — on préfère un journal
        troué et signalé à un fil qui ment.
        """
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    _INSERT,
                    (
                        thread_id,
                        admin.user_id,
                        admin.email,
                        action.tool_name,
                        approved,
                        reason,
                        Jsonb(action.preview),
                        Jsonb(action.arguments),
                        Jsonb(outcome) if outcome is not None else None,
                    ),
                )
        except Exception:
            logger.exception(
                "Décision non tracée : thread=%s outil=%s approuvé=%s par=%s",
                thread_id,
                action.tool_name,
                approved,
                admin.user_id,
            )
