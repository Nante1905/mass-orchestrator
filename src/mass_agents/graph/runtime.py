"""Le service d'orchestration : ouvrir un fil, le faire avancer, le reprendre.

C'est la couche que l'API appelle, et la seule. Elle traduit les tuples
hétérogènes de LangGraph en évènements nommés, borne la durée d'un run, et
transforme les échecs d'infrastructure en messages qu'on peut afficher.

La frontière est nette et elle a une raison : l'API ne doit pas connaître
LangGraph, et le graphe ne doit pas connaître HTTP. C'est ce qui permet de
changer l'un sans rouvrir l'autre — et, accessoirement, de piloter
l'orchestrateur depuis un script de test sans monter de serveur.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.pregel import Pregel
from langgraph.types import Command

from mass_agents.auth import AdminIdentity
from mass_agents.config import LimitsConfig, McpConfig
from mass_agents.domain import (
    ApprovalDecision,
    ApprovalPendingError,
    MassAgentsError,
    NoPendingApprovalError,
)
from mass_agents.graph.context import RunContext, build_run_config, build_thread_config
from mass_agents.graph.events import (
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    GraphEvent,
    MessageEvent,
    RunStatus,
    ThreadState,
    TokenEvent,
    ToolCallEvent,
)
from mass_agents.graph.limits import steps_exhausted_message
from mass_agents.persistence import ApprovalLog, RunLease, Thread, ThreadRepository
from mass_agents.tools import build_mass_toolset

logger = logging.getLogger(__name__)

#: Longueur du titre déduit du premier message. Assez pour reconnaître un fil
#: dans une liste, trop court pour qu'on croie y lire la demande entière.
_TITLE_LENGTH = 80

#: Supersteps par appel de modèle, au plus : `agent`, `outils`, `apercu`,
#: `validation`. Multiplié par le plafond d'étapes, cela donne une limite de
#: récursion qui ne se déclenche que si ce plafond était contourné.
_SUPERSTEPS_PER_STEP = 4

#: Marge ajoutée à la durée maximale d'un run avant de juger son verrou
#: abandonné : l'exécution confirmée d'un geste va à son terme même après
#: l'expiration du délai (voir le nœud de validation).
_LEASE_MARGIN_S = 60.0


class Orchestrator:
    """Le graphe, ses fils, et la traduction de l'un vers l'autre."""

    def __init__(
        self,
        graph: Pregel,
        threads: ThreadRepository,
        approvals: ApprovalLog,
        mcp: McpConfig,
        limits: LimitsConfig,
    ) -> None:
        self._graph = graph
        self._threads = threads
        self._approvals = approvals
        self._mcp = mcp
        self._limits = limits

    # -- fils ---------------------------------------------------------------

    async def create_thread(
        self, admin: AdminIdentity, title: str | None = None
    ) -> Thread:
        return await self._threads.create(admin.user_id, title)

    async def list_threads(
        self, admin: AdminIdentity, *, limit: int = 20, offset: int = 0
    ) -> list[Thread]:
        return await self._threads.list_by_owner(
            admin.user_id, limit=limit, offset=offset
        )

    async def get_state(self, thread_id: str, admin: AdminIdentity) -> ThreadState:
        """L'état d'un fil, après contrôle de propriété.

        Le contrôle passe par le dépôt et non par le checkpointer : ce dernier
        ne connaît que des identifiants opaques et servirait n'importe quel fil
        à n'importe qui.

        La validation en attente est lue dans l'interruption elle-même, et non
        reconstruite : c'est la même charge utile que l'évènement
        `approval_request` du flux.
        """
        await self._threads.get_owned(thread_id, admin.user_id)

        snapshot = await self._graph.aget_state(build_thread_config(thread_id))
        values: dict[str, Any] = snapshot.values or {}
        pending = next(
            (dict(i.value) for i in snapshot.interrupts if isinstance(i.value, dict)),
            None,
        )

        return ThreadState(
            thread_id=thread_id,
            messages=[
                _serialize(message)
                for message in values.get("messages", [])
                if _is_visible(message)
            ],
            status="awaiting_approval" if pending else "completed",
            pending_approval=pending,
        )

    # -- runs ---------------------------------------------------------------
    #
    # Les deux points d'entrée font leurs contrôles **avant** de rendre le
    # flux : propriété, verrou, état du fil. Une fois les en-têtes SSE envoyés,
    # il n'y a plus de code HTTP à rendre, et un refus se lirait comme une
    # conversation vide.

    async def open_start(
        self, thread_id: str, admin: AdminIdentity, token: str, message: str
    ) -> AsyncIterator[GraphEvent]:
        """Un tour de conversation, à partir d'un message de l'utilisateur.

        Refusé si une validation attend sa décision : le message l'abandonnerait
        en silence, avec un appel d'outil resté sans réponse dans le fil.
        """
        lease = await self._acquire(thread_id, admin, title=message[:_TITLE_LENGTH])
        await self._check_state(
            lease,
            refuse_if_pending=ApprovalPendingError(
                "Une validation attend votre décision sur cette conversation : "
                "approuvez-la ou refusez-la avant d'envoyer un nouveau message."
            ),
        )
        return self._stream(
            lease, admin, token, {"messages": [HumanMessage(content=message)]}
        )

    async def open_resume(
        self,
        thread_id: str,
        admin: AdminIdentity,
        token: str,
        decision: ApprovalDecision,
    ) -> AsyncIterator[GraphEvent]:
        """La reprise après une validation humaine.

        Le jeton est celui de la reprise et non celui du run suspendu. C'est ce
        qui rend une validation différée possible : une conversation laissée en
        attente hier soir reprend ce matin avec la session du jour, sans que
        rien n'ait à être migré ni rafraîchi.
        """
        lease = await self._acquire(thread_id, admin)
        await self._check_state(
            lease,
            refuse_if_idle=NoPendingApprovalError(
                "Aucune validation n'est en attente sur cette conversation."
            ),
        )
        return self._stream(
            lease, admin, token, Command(resume=decision.model_dump())
        )

    async def _acquire(
        self, thread_id: str, admin: AdminIdentity, *, title: str | None = None
    ) -> RunLease:
        """Le verrou du fil. Il est jugé abandonné au-delà de la durée maximale
        d'un run, construction de l'outillage comprise."""
        return await self._threads.acquire_run(
            thread_id,
            admin.user_id,
            stale_after_s=(
                self._limits.run_timeout_s + self._mcp.timeout_s + _LEASE_MARGIN_S
            ),
            title=title,
        )

    async def _check_state(
        self,
        lease: RunLease,
        *,
        refuse_if_pending: MassAgentsError | None = None,
        refuse_if_idle: MassAgentsError | None = None,
    ) -> None:
        """Refuse le run si le fil n'est pas dans l'état attendu — et rend alors
        le verrou, que personne d'autre ne rendrait."""
        try:
            snapshot = await self._graph.aget_state(
                build_thread_config(lease.thread_id)
            )
            pending = bool(snapshot.interrupts)
            if pending and refuse_if_pending is not None:
                raise refuse_if_pending
            if not pending and refuse_if_idle is not None:
                raise refuse_if_idle
        except BaseException:
            await self._threads.release_run(lease)
            raise

    async def _stream(
        self, lease: RunLease, admin: AdminIdentity, token: str, payload: Any
    ) -> AsyncIterator[GraphEvent]:
        """Le run, diffusé — et le verrou rendu à la fin, quelle qu'elle soit.

        Un flux jamais consommé ne passe pas par le `finally` : son verrou
        expire alors de lui-même (voir `_acquire`).
        """
        try:
            async for event in self._run(lease.thread_id, admin, token, payload):
                yield event
        finally:
            await self._threads.release_run(lease)

    async def _run(
        self, thread_id: str, admin: AdminIdentity, token: str, payload: Any
    ) -> AsyncIterator[GraphEvent]:
        try:
            toolset = await build_mass_toolset(token, self._mcp)
        except MassAgentsError as error:
            logger.error("Outillage indisponible : %s", error)
            yield ErrorEvent(message=str(error), recoverable=True)
            yield DoneEvent(status="failed", thread_id=thread_id)
            return

        config = build_run_config(
            thread_id,
            RunContext(admin=admin, toolset=toolset, approvals=self._approvals),
            recursion_limit=self._limits.max_steps * _SUPERSTEPS_PER_STEP,
        )

        status: RunStatus = "completed"
        try:
            async with asyncio.timeout(self._limits.run_timeout_s):
                async for event in self._consume(payload, config):
                    if isinstance(event, ApprovalEvent):
                        status = "awaiting_approval"
                    yield event
        except TimeoutError:
            logger.warning("Run interrompu par le délai : thread=%s", thread_id)
            status = "stopped"
            yield ErrorEvent(
                message=(
                    "La demande a dépassé le temps maximal prévu et a été "
                    "interrompue. Ce qui a été fait est conservé ; reprenez "
                    "sur une demande plus étroite."
                ),
                recoverable=True,
            )
        except GraphRecursionError:
            # Le plafond d'étapes du nœud `agent` aurait dû arrêter le run
            # avant : y arriver signale un chemin qui le contourne.
            logger.error("Limite de récursion atteinte : thread=%s", thread_id)
            status = "stopped"
            yield ErrorEvent(
                message=steps_exhausted_message(self._limits), recoverable=True
            )
        except Exception as error:
            logger.exception("Échec du run : thread=%s", thread_id)
            status = "failed"
            yield ErrorEvent(message=_readable(error))

        yield DoneEvent(status=status, thread_id=thread_id)

    async def _consume(self, payload: Any, config: Any) -> AsyncIterator[GraphEvent]:
        """Traduit le flux de LangGraph en évènements du service.

        Deux modes : `messages` pour les fragments de texte au fil de l'eau,
        `updates` pour ce que chaque nœud a produit une fois terminé — messages
        complets, appels d'outils, interruption.
        """
        async for mode, chunk in self._graph.astream(
            payload, config, stream_mode=["updates", "messages"]
        ):
            if mode == "messages":
                if (event := _token_event(chunk)) is not None:
                    yield event
            elif mode == "updates":
                for event in _update_events(chunk):
                    yield event


def _token_event(chunk: Any) -> TokenEvent | None:
    """Un fragment de texte, quand il y en a un.

    La plupart des fragments ne portent pas de texte — ils construisent un appel
    d'outil bloc par bloc. Les diffuser ferait clignoter une réponse vide.

    Seuls les messages de l'assistant sont diffusés : le mode `messages` relaie
    aussi ceux que les nœuds renvoient, dont les résultats d'outils — du JSON
    brut, adresses comprises, qui n'a rien à faire dans une bulle.
    """
    try:
        message, metadata = chunk
    except (TypeError, ValueError):
        return None

    if not isinstance(message, AIMessage):
        return None

    text = _text_of(message.content)
    if not text:
        return None

    node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
    return TokenEvent(text=text, node=node)


def _update_events(chunk: Any) -> list[GraphEvent]:
    if not isinstance(chunk, dict):
        return []

    # Le graphe est suspendu : c'est l'évènement que l'interface attend pour
    # afficher l'aperçu et les deux boutons.
    if interrupts := chunk.get("__interrupt__"):
        return [
            ApprovalEvent(payload=dict(item.value))
            for item in interrupts
            if isinstance(getattr(item, "value", None), dict)
        ]

    events: list[GraphEvent] = []
    for node, update in chunk.items():
        if not isinstance(update, dict):
            continue

        # Seuls les messages de l'assistant ont un sens pour l'interface : les
        # résultats d'outils sont de la matière pour le modèle, pas une réponse.
        for message in update.get("messages", []):
            if not isinstance(message, AIMessage):
                continue
            if text := _text_of(message.content):
                events.append(MessageEvent(role="assistant", content=text, node=node))
            events.extend(ToolCallEvent(name=c["name"]) for c in message.tool_calls)

    return events


def _is_visible(message: AnyMessage) -> bool:
    """Ce qu'une conversation rouverte montre : les demandes et les réponses.

    Pas les résultats d'outils ni les appels sans texte — la même règle que le
    flux, pour qu'un fil relu ressemble au fil vécu.
    """
    if isinstance(message, HumanMessage):
        return True
    return isinstance(message, AIMessage) and bool(_text_of(message.content))


def _text_of(content: Any) -> str:
    """Le texte d'un contenu de message, qu'il soit plat ou en blocs.

    Anthropic rend une liste de blocs dès qu'il y a autre chose que du texte —
    un appel d'outil, un bloc de raisonnement. Ne garder que les blocs `text`
    évite de recracher du JSON d'outil dans l'interface.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )

    return ""


def _serialize(message: AnyMessage) -> dict[str, Any]:
    """Un message, dans la forme que l'API rend.

    On expose le type et le texte, pas l'objet LangChain : le back-office n'a
    pas à connaître `AIMessage`, et le jour où l'on changera de bibliothèque le
    contrat du front ne bougera pas.
    """
    return {
        "id": message.id,
        "role": message.type,
        "content": _text_of(message.content),
    }


def _readable(error: Exception) -> str:
    """Ce qu'on montre d'une exception inattendue.

    Le message de l'exception et rien d'autre : une trace complète dans une
    bulle de conversation n'aide personne, et le journal du serveur la porte
    déjà avec son contexte.
    """
    if isinstance(error, MassAgentsError):
        return str(error)
    return (
        "Une erreur inattendue a interrompu la demande. "
        "Elle est enregistrée dans les journaux du service."
    )
