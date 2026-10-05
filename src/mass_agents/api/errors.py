"""La traduction des échecs en réponses HTTP.

Un seul endroit décide du code et de l'enveloppe. Éparpiller ces `try/except`
dans les routes ferait diverger le format d'erreur d'une route à l'autre, ce qui
est exactement ce que l'enveloppe `ApiResponse` existe pour éviter.

Le choix du code suit celui de `mass-backend` : 401 quand le jeton ne vaut rien,
403 quand il est authentique mais que le compte n'est plus administrateur, 404
quand le fil n'est pas à l'appelant — qu'il existe ou non. 409 quand le fil
n'est pas dans l'état que la requête suppose : un run déjà en cours, une
validation en attente, ou aucune à trancher.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mass_agents.api.responses import ApiResponse
from mass_agents.domain import (
    AdminAuthError,
    EmailDraftGenerationError,
    MassAgentsError,
    SuggestionGenerationError,
    ThreadConflictError,
    ThreadNotFoundError,
    ToolsetError,
)

logger = logging.getLogger(__name__)


def _failure(status: int, error: str, headers: dict[str, str] | None = None):
    return JSONResponse(
        status_code=status,
        content=ApiResponse.failure(error).model_dump(),
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AdminAuthError)
    async def _auth(_: Request, error: AdminAuthError):
        # `WWW-Authenticate` sur les seuls 401 : sur un 403 il inviterait à se
        # réauthentifier alors que la session est valide et que c'est le rôle
        # qui manque.
        headers = (
            {"WWW-Authenticate": 'Bearer realm="mass-agents"'}
            if error.status == 401
            else None
        )
        return _failure(error.status, error.message, headers)

    @app.exception_handler(ThreadNotFoundError)
    async def _thread(_: Request, error: ThreadNotFoundError):
        return _failure(404, str(error))

    @app.exception_handler(ThreadConflictError)
    async def _conflict(_: Request, error: ThreadConflictError):
        # 409 : la requête est bien formée, c'est l'état du fil qui l'empêche.
        # Le message dit quoi faire — attendre, ou trancher la validation.
        return _failure(409, str(error))

    @app.exception_handler(ToolsetError)
    async def _toolset(_: Request, error: ToolsetError):
        # 503 et non 500 : le service est en état de marche, c'est sa dépendance
        # qui manque. La distinction dit au client que réessayer plus tard a du
        # sens.
        logger.error("Outillage indisponible : %s", error)
        return _failure(503, str(error))

    @app.exception_handler(SuggestionGenerationError)
    async def _generation(_: Request, error: SuggestionGenerationError):
        # 502 : la demande était bonne, c'est le modèle qui a mal répondu.
        # Réessayer a du sens, ce que dit aussi le message.
        logger.warning("Suggestions non générées : %s", error)
        return _failure(502, str(error))

    @app.exception_handler(EmailDraftGenerationError)
    async def _draft(_: Request, error: EmailDraftGenerationError):
        # 502 pour la même raison que les posts : réessayer a du sens.
        logger.warning("Courriel non rédigé : %s", error)
        return _failure(502, str(error))

    @app.exception_handler(MassAgentsError)
    async def _domain(_: Request, error: MassAgentsError):
        return _failure(400, str(error))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, error: RequestValidationError):
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'][1:])} : {issue['msg']}"
            for issue in error.errors()
        )
        return _failure(422, f"Requête invalide — {details}")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, error: Exception):
        logger.exception("Erreur non gérée")
        return _failure(500, "Erreur interne du service d'orchestration")
