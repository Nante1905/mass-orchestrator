"""Les routes de conversation.

Toutes passent par `CurrentCaller`, donc par la relecture du rôle en base. Aucune
n'est publique, y compris celles qui ne font que lire : une conversation avec
l'agent porte des noms, des adresses et des chiffres que rien d'autre ne
protège.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from mass_agents.api.dependencies import CurrentCaller, OrchestratorDep
from mass_agents.api.responses import ApiResponse
from mass_agents.api.schemas import (
    CreateThreadRequest,
    ResumeRequest,
    RunRequest,
    ThreadSummary,
)
from mass_agents.api.streaming import sse_response
from mass_agents.domain import ApprovalDecision

router = APIRouter(prefix="/threads", tags=["threads"])


@router.post("")
async def create_thread(
    body: CreateThreadRequest, caller: CurrentCaller, orchestrator: OrchestratorDep
) -> ApiResponse:
    """Ouvre un fil.

    Aucun jeton n'est mémorisé ici : le fil ne retient que son propriétaire. Le
    jeton repart à chaque run, ce qui évite qu'une conversation ouverte hier
    porte une session qui n'a plus cours.
    """
    thread = await orchestrator.create_thread(caller.admin, body.title)
    return ApiResponse.success(ThreadSummary.of(thread).model_dump(mode="json"))


@router.get("")
async def list_threads(
    caller: CurrentCaller,
    orchestrator: OrchestratorDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse:
    threads = await orchestrator.list_threads(caller.admin, limit=limit, offset=offset)
    return ApiResponse.success(
        [ThreadSummary.of(thread).model_dump(mode="json") for thread in threads]
    )


@router.get("/{thread_id}/state")
async def thread_state(
    thread_id: str, caller: CurrentCaller, orchestrator: OrchestratorDep
) -> ApiResponse:
    """L'état du fil : ses messages, et la validation en attente s'il y en a une.

    C'est cette route qui permet de rouvrir une conversation suspendue et de
    retrouver la validation qui l'attendait — y compris après un redémarrage du
    service. `pendingApproval` a la forme de l'évènement `approval_request`.
    """
    state = await orchestrator.get_state(thread_id, caller.admin)
    return ApiResponse.success(
        {
            "threadId": state.thread_id,
            "status": state.status,
            "messages": state.messages,
            "pendingApproval": state.pending_approval,
        }
    )


@router.post("/{thread_id}/runs/stream")
async def run_stream(
    thread_id: str,
    body: RunRequest,
    caller: CurrentCaller,
    orchestrator: OrchestratorDep,
):
    """Un tour de conversation, diffusé au fil de l'eau.

    Propriété, verrou et état du fil sont contrôlés **avant** d'ouvrir le flux :
    une fois les en-têtes SSE envoyés, il n'y a plus de code HTTP à rendre. 409
    si un run est déjà en cours, ou si une validation attend sa décision.
    """
    events = await orchestrator.open_start(
        thread_id, caller.admin, caller.token, body.message
    )
    return sse_response(events, thread_id)


@router.post("/{thread_id}/runs/resume")
async def run_resume(
    thread_id: str,
    body: ResumeRequest,
    caller: CurrentCaller,
    orchestrator: OrchestratorDep,
):
    """Reprend un fil suspendu par une validation humaine.

    C'est le jeton de *cette* requête qui servira à l'écriture confirmée, et
    c'est ce qui donne son sens à la trace : l'action est exécutée sous
    l'identité de la personne qui vient de l'approuver. 409 si un run est déjà
    en cours — une seconde reprise concurrente —, ou si rien n'est en attente.
    """
    decision = ApprovalDecision(approved=body.approved, reason=body.reason)
    events = await orchestrator.open_resume(
        thread_id, caller.admin, caller.token, decision
    )
    return sse_response(events, thread_id)
