"""La validation humaine.

Le déroulé tient en trois temps :

1. Le nœud d'aperçu a appelé `send_email` ou `mark_attendance` avec
   `confirmed: false`. `mass-mcp` n'a rien écrit : il a rendu l'aperçu.
2. Ce nœud suspend le graphe par `interrupt(aperçu)`. L'état est checkpointé ;
   le service peut redémarrer entre-temps sans que la validation soit perdue.
3. La reprise arrive par `Command(resume=décision)`. Approuvé, le nœud rejoue
   **le même** outil avec `confirmed: true`. Refusé, il n'appelle rien.

Dans les deux cas, la décision revient à l'agent comme **le résultat de son
propre appel** : un `ToolMessage` rattaché à l'identifiant de l'appel d'origine.
Le modèle lit ce qui s'est réellement passé, et le fil reste valide pour l'API —
chaque `tool_use` y a son `tool_result`.

**Une exécution au plus par appel.** La décision est inscrite au journal avant
l'exécution, sous l'identifiant de l'appel d'outil ; une reprise rejouée
retrouve la décision déjà prise au lieu d'exécuter à nouveau. Voir
`persistence/approvals.py`.

**L'aperçu n'est pas reformaté.** Celui de `mass-mcp` est déjà la charge utile :
destinataires résolus, corps intégral, liste nominative des pointages. Le
réécrire ici ferait diverger ce qui est montré de ce qui a été calculé.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from mass_agents.domain import (
    CONSEQUENCES,
    ApprovalDecision,
    ApprovalRequest,
    PendingAction,
)
from mass_agents.graph.context import RunContext, run_context
from mass_agents.graph.state import OrchestratorState
from mass_agents.persistence import Claim
from mass_agents.tools import CONFIRMATION_PARAM, parse_tool_payload, tool_text

logger = logging.getLogger(__name__)

_TITLES = {
    "send_email": "Envoyer ce courriel ?",
    "mark_attendance": "Enregistrer ce pointage ?",
}


async def validation_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    pending = state.get("pending_action")
    if pending is None:
        # Le routage n'envoie ici qu'avec une action en attente : y arriver sans
        # signalerait un décalage entre `routing.py` et ce nœud.
        logger.error("Nœud de validation atteint sans action en attente")
        return {}

    context = run_context(config)
    thread_id = config["configurable"]["thread_id"]  # type: ignore[index]

    request = ApprovalRequest(
        tool_name=pending.tool_name,
        title=_TITLES.get(pending.tool_name, "Confirmer cette action ?"),
        consequence=CONSEQUENCES.get(pending.tool_name, ""),
        preview=pending.preview,
    )

    # Tout ce qui suit ne s'exécute qu'à la reprise. Le nœud est rejoué depuis
    # le début à ce moment-là : c'est pourquoi il ne doit produire aucun effet
    # de bord avant cette ligne.
    decision = _decision_of(interrupt(request.model_dump()))

    # La décision est écrite avant toute exécution, et c'est elle qui
    # l'autorise : une ligne par appel d'outil, jamais deux.
    try:
        claim = await context.approvals.claim(
            thread_id=thread_id,
            admin=context.admin,
            action=pending,
            approved=decision.approved,
            reason=decision.reason,
        )
    except Exception:
        logger.exception("Journal des validations indisponible : thread=%s", thread_id)
        if not decision.approved:
            return _answer(pending, _refusal_text(decision))
        return _answer(pending, _UNTRACEABLE, failed=True)

    if not claim.acquired:
        # Cet appel a déjà été décidé — reprise rejouée, double clic, arrêt du
        # service après coup. La décision d'alors fait foi.
        logger.warning(
            "Décision déjà prise sur %s (%s) : pas de réexécution",
            pending.tool_call_id,
            claim.status,
        )
        return _answer(pending, _replay_text(claim), failed=claim.status != "done")

    if not decision.approved:
        logger.info(
            "Écriture refusée : %s par %s", pending.tool_name, context.admin.user_id
        )
        return _answer(pending, _refusal_text(decision))

    # Protégé de l'annulation : si le run est interrompu pendant l'appel — délai
    # dépassé, navigateur fermé —, l'exécution et son inscription au journal
    # vont quand même à leur terme. Sans cela, un courriel parti resterait noté
    # `executing`, et l'utilisateur ne saurait pas qu'il est parti.
    text, failed = await asyncio.shield(_execute(context, pending, claim))
    return _answer(pending, text, failed=failed)


#: Rendu quand le journal ne peut pas enregistrer une approbation.
_UNTRACEABLE = (
    "Approuvé par l'administrateur, mais non exécuté : le journal des "
    "validations est indisponible, et un geste qui engage l'association ne part "
    "pas sans trace. Rien n'a été fait ; l'utilisateur pourra le reproposer "
    "plus tard."
)


async def _execute(
    context: RunContext, pending: PendingAction, claim: Claim
) -> tuple[str, bool]:
    """Rejoue le même outil, confirmé, puis inscrit l'issue au journal.

    « Le même » est littéral : les arguments conservés par l'aperçu, augmentés
    du seul `confirmed`. Les recalculer, ou laisser un modèle les reformuler,
    ferait partir un message qui n'est pas celui qui a été relu.

    Rend le texte destiné à l'agent, et si l'exécution a échoué.
    """
    tool = context.toolset.get(pending.tool_name)

    try:
        raw = await tool.ainvoke({**pending.arguments, CONFIRMATION_PARAM: True})
    except Exception as error:
        # L'échec est rendu à l'agent et non levé : l'utilisateur a approuvé, il
        # doit savoir ce qui s'est passé ensuite. On ne peut pas affirmer que
        # rien n'est parti — une coupure réseau peut survenir après l'envoi.
        logger.exception("Échec de l'exécution confirmée de %s", pending.tool_name)
        await context.approvals.complete(
            claim, outcome={"error": str(error)}, failed=True
        )
        return (
            f"Approuvé par l'administrateur, mais l'exécution a échoué : "
            f"{error}. Il n'est pas certain que rien n'ait été fait : invite "
            "l'utilisateur à vérifier dans le back-office avant de reproposer "
            "ce geste.",
            True,
        )

    logger.info("Écriture confirmée exécutée : %s", pending.tool_name)
    await context.approvals.complete(
        claim, outcome=parse_tool_payload(raw), failed=False
    )
    return (
        f"Approuvé par l'administrateur et exécuté. Réponse du serveur : "
        f"{tool_text(raw)}",
        False,
    )


def _refusal_text(decision: ApprovalDecision) -> str:
    reason = f" Motif indiqué : {decision.reason}" if decision.reason else ""
    return f"Refusé par l'administrateur : le geste n'est pas exécuté.{reason}"


def _replay_text(claim: Claim) -> str:
    """Ce qu'on dit d'un appel déjà décidé, selon ce que la décision a donné."""
    outcome = (
        json.dumps(claim.outcome, ensure_ascii=False) if claim.outcome else "aucune"
    )
    match claim.status:
        case "done":
            return (
                "Déjà exécuté après une validation précédente — pas de seconde "
                f"exécution. Réponse du serveur à l'époque : {outcome}"
            )
        case "failed":
            return (
                "Une exécution précédente de ce geste a échoué ; il n'est pas "
                f"rejoué. Erreur d'alors : {outcome}. Invite l'utilisateur à "
                "vérifier dans le back-office avant de le reproposer."
            )
        case "refused":
            return "Ce geste a déjà été refusé ; il n'est pas exécuté."
        case _:
            return (
                "Une exécution de ce geste a déjà commencé sans que son issue "
                "soit connue — le service a probablement été interrompu. Il "
                "n'est pas rejoué, pour ne pas l'exécuter deux fois : invite "
                "l'utilisateur à vérifier dans le back-office (historique des "
                "courriels, pointages) ce qui a été fait."
            )


def _answer(pending: PendingAction, content: str, *, failed: bool = False) -> dict:
    """La décision, rendue à l'agent comme le résultat de son appel."""
    return {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=pending.tool_call_id,
                name=pending.tool_name,
                status="error" if failed else "success",
            )
        ],
        "pending_action": None,
    }


def _decision_of(raw: Any) -> ApprovalDecision:
    """La décision reprise, lue strictement.

    Aucune valeur par défaut, aucune tolérance : une reprise qu'on ne sait pas
    lire doit échouer. Retomber sur « approuvé » ferait envoyer un courriel sur
    une charge utile mal formée, ce qui est précisément l'accident que ce nœud
    existe pour empêcher. L'échec laisse le fil au dernier checkpoint : une
    reprise correcte reste possible.
    """
    if isinstance(raw, bool):
        return ApprovalDecision(approved=raw)
    if isinstance(raw, ApprovalDecision):
        return raw
    if isinstance(raw, dict):
        return ApprovalDecision.model_validate(raw)

    raise ValueError(
        "Décision de validation illisible : attendu un objet "
        "{'approved': bool, 'reason': str | null}."
    )
