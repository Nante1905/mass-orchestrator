"""L'application HTTP, et le cycle de vie de ce qu'elle porte.

La façade coûte peu de lignes et rend quatre choses : le même contrôle
d'administration que `mass-backend`, la maîtrise de la liste CORS, le format
d'erreur `ApiResponse` que le back-office sait déjà lire, et la liberté de
changer d'orchestration sans toucher au front.

Le graphe tourne **dans ce processus**. C'est le point où l'implémentation
Python s'écarte du plan d'origine, et la raison est que la justification du plan
tombait : la façade y était nécessaire parce que l'authentification de LangGraph
Server est peu mûre en JavaScript. En Python, elle l'est — mais un second
serveur à surveiller, à configurer et à relayer n'apporterait alors plus rien
que cette façade ne fasse déjà. Un processus, un port, et le flux SSE produit là
où il est consommé.

L'assemblage se fait ici et nulle part ailleurs : c'est le seul endroit qui
connaisse à la fois la base, le graphe et HTTP. Chacun des trois s'ignore.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mass_agents.api.errors import register_error_handlers
from mass_agents.api.routes import community_router, health_router, threads_router
from mass_agents.auth import AdminAuthenticator
from mass_agents.community import CommunityService, FeedReader
from mass_agents.config import AppConfig, get_config
from mass_agents.graph import Orchestrator, build_graph
from mass_agents.persistence import (
    ApprovalLog,
    Database,
    ThreadRepository,
    apply_migrations,
    create_checkpointer,
)

logger = logging.getLogger(__name__)


def create_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Monte les dépendances au démarrage, les ferme à l'arrêt.

        L'ordre n'est pas indifférent : le schéma avant le checkpointer, qui
        crée ses tables dedans, et le checkpointer avant le graphe, qui s'y
        adosse. Un échec ici doit empêcher le démarrage — un service qui écoute
        sans pouvoir persister accepterait des conversations qu'il perdrait.
        """
        database = Database.create(app_config.database)
        await database.open()

        await apply_migrations(database.identity)
        checkpointer = await create_checkpointer(database.agents)

        app.state.authenticator = AdminAuthenticator(
            app_config.auth, database.identity
        )
        app.state.orchestrator = Orchestrator(
            graph=build_graph(checkpointer),
            threads=ThreadRepository(database.agents),
            approvals=ApprovalLog(database.agents),
            mcp=app_config.mcp,
            limits=app_config.limits,
        )
        # Hors du graphe : la page de suggestions ne converse pas, et ne
        # touche ni aux fils ni aux checkpoints. Le lecteur de flux vit ici
        # pour que son cache dure autant que le processus.
        app.state.community = CommunityService(
            FeedReader(),
            app_config.mcp,
            timeout_s=app_config.limits.run_timeout_s,
        )

        logger.info(
            "mass-agents prêt — MCP %s, checkpoints dans le schéma %s",
            app_config.mcp.url,
            app_config.database.checkpoint_schema,
        )

        try:
            yield
        finally:
            await database.close()

    app = FastAPI(
        title="mass-agents",
        summary="Orchestrateur conversationnel de la plateforme MASS",
        lifespan=lifespan,
    )

    # La liste est celle de `mass-backend/src/index.ts`, qui est en dur là-bas.
    # Les deux doivent rester alignées : le back-office qui joint l'API sans
    # joindre l'agent est le symptôme le plus fréquent d'un oubli ici.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.http.allowed_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=True,
    )

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(threads_router)
    app.include_router(community_router)

    return app
