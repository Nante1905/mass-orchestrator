"""L'authentification des routes, et l'accès au service.

C'est ici que se matérialise la décision d'architecture : la façade est
**l'unique frontière d'authentification**. Le graphe ne vérifie rien, et
`mass-mcp` non plus — il relaie. Un appel qui passe cette dépendance est un
appel dont le rôle administrateur vient d'être relu en base.

Le jeton brut est conservé à côté de l'identité, et il le faut : c'est lui qui
partira dans l'en-tête `Authorization` des appels d'outils. Il ne va pas plus
loin — ni dans l'état du graphe, ni dans les checkpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from mass_agents.auth import AdminAuthenticator, AdminIdentity, read_bearer_token
from mass_agents.community import CommunityService
from mass_agents.domain import AdminAuthError
from mass_agents.graph import Orchestrator
from mass_agents.mailing import EmailDraftService


@dataclass(frozen=True, slots=True)
class Caller:
    """Qui appelle, et avec quel jeton."""

    admin: AdminIdentity
    token: str


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_authenticator(request: Request) -> AdminAuthenticator:
    return request.app.state.authenticator


def get_community(request: Request) -> CommunityService:
    return request.app.state.community


def get_mailing(request: Request) -> EmailDraftService:
    return request.app.state.mailing


async def current_caller(
    authenticator: Annotated[AdminAuthenticator, Depends(get_authenticator)],
    authorization: Annotated[str | None, Header()] = None,
) -> Caller:
    token = read_bearer_token(authorization)
    if token is None:
        raise AdminAuthError(
            "Authentification requise : joindre le jeton d'administrateur du "
            "back-office dans l'entête Authorization (Bearer …).",
            status=401,
        )

    return Caller(admin=await authenticator.authenticate(token), token=token)


CurrentCaller = Annotated[Caller, Depends(current_caller)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
CommunityDep = Annotated[CommunityService, Depends(get_community)]
MailingDep = Annotated[EmailDraftService, Depends(get_mailing)]
