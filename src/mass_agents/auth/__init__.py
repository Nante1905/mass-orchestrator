"""L'unique frontière d'authentification du service."""

from mass_agents.auth.identity import (
    ADMIN_ROLE_CODE,
    AdminAuthenticator,
    AdminIdentity,
    read_bearer_token,
)

__all__ = [
    "ADMIN_ROLE_CODE",
    "AdminAuthenticator",
    "AdminIdentity",
    "read_bearer_token",
]
