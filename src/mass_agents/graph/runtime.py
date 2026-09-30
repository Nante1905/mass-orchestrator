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
from langgraph.pregel import Pregel
from langgraph.types import Command

from mass_agents.auth import AdminIdentity
from mass_agents.config import LimitsConfig, McpConfig
from mass_agents.domain import ApprovalDecision, MassAgentsError, PendingAction
from mass_agents.graph.context import RunContext, build_run_config, build_thread_config
from mass_agents.graph.events import (
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    GraphEvent,
    HandoffEvent,
    MessageEvent,
    RunStatus,
    ThreadState,
    TokenEvent,
)
from mass_agents.persistence import ApprovalLog, Thread, ThreadRepository
from mass_agents.tools import build_mass_toolset

logger = logging.getLogger(__name__)

#: Longueur du titre déduit du premier message. Assez pour reconnaître un fil
#: dans une liste, trop court pour qu'on croie y lire la demande entière.
_TITLE_LENGTH = 80


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

    async def ensure_owner(self, thread_id: str, admin: AdminIdentity) -> None:
        """Vérifie la propriété du fil sans rien faire d'autre.

        Existe pour les routes en flux : une fois les en-têtes SSE envoyés, il
        n'y a plus de code HTTP à rendre, et un refus se lirait comme une
        conversation vide. Le contrôle doit donc précéder l'ouverture du flux.
        """
        await self._threads.get_owned(thread_id, admin.user_id)

    async def get_state(self, thread_id: str, admin: AdminIdentity) -> ThreadState:
        """L'état d'un fil, après contrôle de propriété.

        Le contrôle passe par le dépôt et non par le checkpointer : ce dernier
        ne connaît que des identifiants opaques et servirait n'importe quel fil
        à n'importe qui.
        """
        await self._threads.get_owned(thread_id, admin.user_id)

        snapshot = await self._graph.aget_state(build_thread_config(thread_id))
        values: dict[str, Any] = snapshot.values or {}

        pending: PendingAction | None = values.get("pending_action")
        awaiting = bool(snapshot.interrupts)

        return ThreadState(
            thread_id=thread_id,
            messages=[_serialize(message) for message in values.get("messages", [])],
            status="awaiting_approval" if awaiting else "completed",
            pending_approval=pending.preview if awaiting and pending else None,
            turns=values.get("turns", 0),
        )

    # -- runs ---------------------------------------------------------------

    def start(
        self, thread_id: str, admin: AdminIdentity, token: str, message: str
    ) -> AsyncIterator[GraphEvent]:
        """Un tour de conversation, à partir d'un message de l'utilisateur."""
        return self._stream(
            thread_id,
            admin,
            token,
            {"messages": [HumanMessage(content=message)]},
            title=message[:_TITLE_LENGTH],
        )

    def resume(
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
        return self._stream(
            thread_id, admin, token, Command(resume=decision.model_dump())
        )

    async def _stream(
        self,
        thread_id: str,
        admin: AdminIdentity,
        token: str,
        payload: Any,
        *,
        title: str | None = None,
    ) -> AsyncIterator[GraphEvent]:
        # Sert d'autorisation autant que d'horodatage : une mise à jour qui ne
        # touche aucune ligne signifie que le fil n'est pas à cet appelant, et
        # le run ne doit pas commencer.
        await self._threads.touch(thread_id, admin.user_id, title=title)

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
        except Exception as error:
            logger.exception("Échec du run : thread=%s", thread_id)
            status = "failed"
            yield ErrorEvent(message=_readable(error))

        yield DoneEvent(status=status, thread_id=thread_id)

    async def _consume(self, payload: Any, config: Any) -> AsyncIterator[GraphEvent]:
        """Traduit le flux de LangGraph en évènements du service.

        `subgraphs=True` est indispensable : les agents spécialistes tournent
        dans des sous-graphes, et sans cela leurs jetons n'atteindraient jamais
        le navigateur — seul le superviseur, qui ne produit presque pas de
        texte, serait diffusé.
        """
        async for namespace, mode, chunk in self._graph.astream(
            payload,
            config,
            stream_mode=["updates", "messages"],
            subgraphs=True,
        ):
            if mode == "messages":
                event = _token_event(chunk)
                if event is not None:
                    yield event
            elif mode == "updates":
                for event in _update_events(chunk, namespace): # type: ignore
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

    text = _text_of(getattr(message, "content", None))
    if not text:
        return None

    node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
    return TokenEvent(text=text, node=node)


def _update_events(chunk: Any, namespace: tuple[str, ...]) -> list[GraphEvent]:
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

    # Les mises à jour des sous-graphes sont ignorées : leurs messages
    # remontent déjà dans la mise à jour du nœud parent, et les diffuser deux
    # fois afficherait chaque réponse en double.
    if namespace:
        return []

    events: list[GraphEvent] = []
    for node, update in chunk.items():
        if not isinstance(update, dict):
            continue

        if (destination := update.get("next")) and destination != node:
            events.append(HandoffEvent(to=destination))

        for message in update.get("messages", []):
            text = _text_of(getattr(message, "content", None))
            if isinstance(message, AIMessage) and text:
                events.append(MessageEvent(role="assistant", content=text, node=node))

    return events


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
        "name": getattr(message, "name", None),
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
