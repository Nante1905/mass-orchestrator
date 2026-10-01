"""Journal des décisions humaines sur les écritures engageantes — et verrou
contre leur double exécution.

Ce qui est tracé n'est pas l'activité de l'agent — les lectures ne regardent
personne, les brouillons se défont. C'est ce qu'un humain a décidé, et l'aperçu
qu'il avait sous les yeux au moment de le faire. Conserver l'aperçu et pas
seulement les arguments est le point de la table : six mois plus tard,
« `group_id: G0003` » ne dit pas à qui le courriel est parti.

La décision est écrite **avant** l'exécution, et c'est elle qui l'autorise. La
ligne est unique par appel d'outil : une reprise rejouée — double clic, deux
onglets, reprise après un arrêt du service — retrouve la décision déjà prise au
lieu d'exécuter une seconde fois. Conséquence assumée : si le journal est
injoignable, rien n'est exécuté. Un geste qui engage l'association ne part pas
sans trace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from mass_agents.auth.identity import AdminIdentity
from mass_agents.domain import PendingAction

logger = logging.getLogger(__name__)

ApprovalStatus = Literal["refused", "executing", "done", "failed"]

_CLAIM = """
    insert into approval_log (
        thread_id, tool_call_id, decided_by_user_id, decided_by_email,
        tool_name, approved, reason, preview, arguments, status
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (thread_id, tool_call_id) where tool_call_id is not null
    do nothing
    returning id
"""

_EXISTING = """
    select id, status, outcome
    from approval_log
    where thread_id = %s and tool_call_id = %s
"""

_COMPLETE = """
    update approval_log
    set status = %s, outcome = %s
    where id = %s
"""


@dataclass(frozen=True, slots=True)
class Claim:
    """La décision sur un appel d'outil, telle que le journal la connaît.

    `acquired` dit si cette décision vient d'être prise ici. Sinon, `status` et
    `outcome` sont ceux de la décision prise plus tôt, et c'est elle qui fait
    foi.
    """

    id: int
    acquired: bool
    status: ApprovalStatus
    outcome: dict[str, Any] | None = None


class ApprovalLog:
    """Écrit depuis le nœud de validation, et lui seul."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def claim(
        self,
        *,
        thread_id: str,
        admin: AdminIdentity,
        action: PendingAction,
        approved: bool,
        reason: str | None,
    ) -> Claim:
        """Enregistre la décision, ou retrouve celle déjà prise sur cet appel.

        Une approbation est enregistrée à l'état `executing` : l'exécution ne
        commence qu'une fois cette ligne écrite. Les erreurs de base sont
        levées — c'est à l'appelant de ne rien exécuter dans ce cas.
        """
        status: ApprovalStatus = "executing" if approved else "refused"

        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                _CLAIM,
                (
                    thread_id,
                    action.tool_call_id,
                    admin.user_id,
                    admin.email,
                    action.tool_name,
                    approved,
                    reason,
                    Jsonb(action.preview),
                    Jsonb(action.arguments),
                    status,
                ),
            )
            row = await cursor.fetchone()
            if row is not None:
                return Claim(id=row["id"], acquired=True, status=status)

            cursor = await conn.execute(_EXISTING, (thread_id, action.tool_call_id))
            existing = await cursor.fetchone()

        if existing is None:
            # Le conflit a eu lieu, la ligne n'est plus là : ne peut arriver que
            # si quelqu'un la supprime entre les deux lectures.
            raise RuntimeError(
                f"Décision introuvable après conflit : {action.tool_call_id}"
            )

        return Claim(
            id=existing["id"],
            acquired=False,
            status=existing["status"],
            outcome=existing["outcome"],
        )

    async def complete(
        self, claim: Claim, *, outcome: dict[str, Any] | None, failed: bool
    ) -> None:
        """Ce que l'exécution a donné.

        Un échec ici n'annule pas ce qui a été fait, et n'est pas levé : le
        geste est parti, et faire échouer le run laisserait croire le contraire.
        La ligne reste à `executing`, ce qui empêche toute réexécution — on
        préfère un journal incomplet et signalé à un courriel envoyé deux fois.
        """
        status: ApprovalStatus = "failed" if failed else "done"
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    _COMPLETE,
                    (status, Jsonb(outcome) if outcome is not None else None, claim.id),
                )
        except Exception:
            logger.exception(
                "Issue non tracée : approval_log.id=%s statut=%s", claim.id, status
            )
