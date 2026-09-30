"""La validation humaine. C'est le nœud qui justifie tout le reste.

Le déroulé tient en trois temps :

1. L'agent des opérations a appelé `send_email` ou `mark_attendance` **sans**
   `confirmed`. `mass-mcp` n'a donc rien écrit : il a rendu l'aperçu.
2. Ce nœud suspend le graphe par `interrupt(aperçu)`. L'état est checkpointé ;
   le service peut redémarrer entre-temps sans que la validation soit perdue.
3. La reprise arrive par `Command(resume=décision)`. Approuvé, le nœud rejoue
   **le même** outil avec `confirmed: true`. Refusé, il n'appelle rien et le dit
   dans le fil.

Deux points méritent d'être explicités.

**L'aperçu n'est pas reformaté.** Celui de `mass-mcp` est déjà la charge utile :
destinataires résolus, corps intégral, nombre de courriels, aperçu nominatif des
pointages. Le réécrire ici ferait diverger ce qui est montré de ce qui a été
calculé — et c'est l'aperçu, pas nos arguments, qui engage le nom de
l'association.

**La garantie est double par construction.** Même si le superviseur routait mal,
même si ce nœud était contourné, `mass-mcp` refuserait d'écrire sans
`confirmed`. Ce nœud n'est pas la sécurité : il est ce qui permet à la sécurité
d'être franchie légitimement, avec une trace de qui a relu quoi.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, interrupt

from mass_agents.domain import (
    CONSEQUENCES,
    ApprovalDecision,
    ApprovalRequest,
    PendingAction,
)
from mass_agents.graph.context import run_context
from mass_agents.graph.nodes.supervisor import SUPERVISOR
from mass_agents.graph.state import OrchestratorState
from mass_agents.tools.payload import parse_tool_payload

logger = logging.getLogger(__name__)

VALIDATION = "validation"

ValidationDestination = Literal["superviseur"]

_TITLES = {
    "send_email": "Envoyer ce courriel ?",
    "mark_attendance": "Enregistrer ce pointage ?",
}


async def validation_node(
    state: OrchestratorState, config: RunnableConfig
) -> Command[ValidationDestination]:
    pending = state.get("pending_action")
    if pending is None:
        # Le nœud a été atteint sans écriture en attente : rien à valider, et
        # surtout rien à inventer. On rend la main plutôt que d'échouer, le fil
        # reste cohérent.
        logger.warning("Nœud de validation atteint sans action en attente")
        return Command(goto=SUPERVISOR, update={"next": SUPERVISOR})

    context = run_context(config)
    thread_id = config["configurable"]["thread_id"] # type: ignore

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

    if not decision.approved:
        await context.approvals.record(
            thread_id=thread_id,
            admin=context.admin,
            action=pending,
            approved=False,
            reason=decision.reason,
            outcome=None,
        )
        logger.info(
            "Écriture refusée : %s par %s", pending.tool_name, context.admin.user_id
        )
        return Command(
            goto=SUPERVISOR,
            update={
                "messages": [AIMessage(content=_refusal_text(pending, decision))],
                "next": SUPERVISOR,
                "pending_action": None,
            },
        )

    outcome, text = await _execute(context, pending)

    await context.approvals.record(
        thread_id=thread_id,
        admin=context.admin,
        action=pending,
        approved=True,
        reason=decision.reason,
        outcome=outcome,
    )

    return Command(
        goto=SUPERVISOR,
        update={
            "messages": [AIMessage(content=text)],
            "next": SUPERVISOR,
            "pending_action": None,
        },
    )


async def _execute(
    context, pending: PendingAction
) -> tuple[dict[str, Any] | None, str]:
    """Rejoue le même outil, confirmé.

    « Le même » est littéral : les arguments d'origine, augmentés du seul
    `confirmed`. Les recalculer, ou laisser un modèle les reformuler, ferait
    partir un message qui n'est pas celui qui a été relu.
    """
    tool = context.toolset.get(pending.tool_name)

    try:
        raw = await tool.ainvoke({**pending.arguments, "confirmed": True})
    except Exception as error:
        # L'échec est rendu dans le fil et non levé : le run continue, le
        # superviseur pourra l'expliquer. Une exception ici sortirait de la
        # boucle et laisserait l'utilisateur sans réponse après avoir approuvé.
        logger.exception("Échec de l'exécution confirmée de %s", pending.tool_name)
        return (
            {"error": str(error)},
            f"L'action n'a pas pu être exécutée : {error}. Rien n'a été "
            "enregistré ; la demande est à reprendre.",
        )

    outcome = parse_tool_payload(raw)
    logger.info("Écriture confirmée exécutée : %s", pending.tool_name)
    return outcome, _outcome_text(pending, outcome)


def _refusal_text(pending: PendingAction, decision: ApprovalDecision) -> str:
    action = "L'envoi" if pending.tool_name == "send_email" else "Le pointage"
    reason = f" Motif indiqué : {decision.reason}" if decision.reason else ""
    return (
        f"{action} a été refusé et n'a pas eu lieu — rien n'a été modifié.{reason} "
        "Dites-moi ce qu'il faut changer si vous voulez le reprendre."
    )


def _outcome_text(pending: PendingAction, outcome: dict[str, Any] | None) -> str:
    """Ce qu'on annonce après coup, en s'appuyant sur ce que l'outil a rapporté.

    On préfère le message du serveur au nôtre quand il existe : c'est lui qui
    sait combien d'envois ont abouti et lesquels ont échoué, et un résumé écrit
    ici finirait par mentir le jour où l'API changera.
    """
    if outcome is None:
        return "Action exécutée après votre validation."

    if error := outcome.get("error"):
        return f"L'action a échoué après validation : {error}"

    reported = outcome.get("message")
    prefix = (
        "Envoi effectué après votre validation."
        if pending.tool_name == "send_email"
        else "Pointage enregistré après votre validation."
    )
    return f"{prefix} {reported}" if reported else prefix


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
