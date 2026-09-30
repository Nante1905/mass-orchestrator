"""Qui appelle, et à quel titre.

Transposition directe de `mass-backend/src/modules/auth/middlewares/
adminAuthMiddleware.ts` : vérifier la signature du jeton, puis **relire le rôle
en base**. Les deux temps sont indispensables et pour des raisons différentes.

Le jeton ne porte pas le rôle, et l'y ajouter ne suffirait pas : un
administrateur rétrogradé garderait l'agent ouvert jusqu'à l'expiration de son
jeton, un jour durant. La requête est indexée sur la clé primaire, et sur un
trafic de back-office c'est le prix d'une révocation immédiate.

Ce service refait ce contrôle plutôt que de le déléguer parce qu'il est la seule
frontière : `mass-mcp` ne vérifie rien — il relaie — et le backend n'est
consulté qu'au premier appel d'outil, c'est-à-dire trop tard pour refuser un run.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from psycopg_pool import AsyncConnectionPool

from mass_agents.config import AuthConfig
from mass_agents.domain import AdminAuthError

#: Le code du rôle administrateur, tel que `mass-backend` le nomme.
ADMIN_ROLE_CODE = "ADMIN"

_BEARER_PREFIX = "Bearer "

#: Le message d'un jeton qui n'ouvre plus rien.
#:
#: Il dit quoi faire, et c'est le point : un 401 qui survient au milieu d'une
#: conversation doit se lire comme « reconnectez-vous », pas comme une panne.
#: Un message technique ferait réessayer la même reprise en boucle, sans qu'elle
#: puisse jamais aboutir.
_EXPIRED_MESSAGE = (
    "Votre session a expiré ou votre compte n'est plus administrateur. "
    "Reconnectez-vous au back-office : la conversation est conservée et "
    "reprendra où elle s'est arrêtée."
)

# Repris mot pour mot du middleware du backend, jointures ouvertes comprises :
# la ligne doit remonter même sans rôle ni fiche, pour que le refus vienne du
# contrôle explicite qui suit et non d'une absence qu'on lirait comme un compte
# inconnu.
_ADMIN_IDENTITY_QUERY = """
    select
        users.id as user_id,
        users.email as email,
        roles.code as role_code,
        administrators.id as administrator_id
    from users
    left join roles on roles.id = users.id_role
    left join administrators on administrators.id_user = users.id
    where users.id = %s
"""


@dataclass(frozen=True, slots=True)
class AdminIdentity:
    """L'administrateur derrière un appel, tel que la base le décrit à l'instant."""

    user_id: str
    email: str | None
    administrator_id: str | None


def read_bearer_token(header: str | None) -> str | None:
    """Le jeton porté par l'entête, sans aucune vérification à ce stade.

    Un entête absent, mal formé ou vide donnent tous `None` : ils se valent, la
    requête n'apporte pas de jeton.
    """
    if header is None or not header.startswith(_BEARER_PREFIX):
        return None

    token = header[len(_BEARER_PREFIX) :].strip()
    return token or None


class AdminAuthenticator:
    """Vérifie un jeton, puis relit le rôle. Rien d'autre — il n'en émet aucun."""

    def __init__(self, config: AuthConfig, pool: AsyncConnectionPool) -> None:
        self._secret = config.secret
        self._pool = pool

    async def authenticate(self, token: str) -> AdminIdentity:
        subject = self._verify(token)
        return await self._load_admin(subject)

    def _verify(self, token: str) -> str:
        """Signature et expiration, sans toucher la base.

        `jose` côté backend signe en HS256 par défaut ; l'algorithme est
        épinglé ici plutôt que déduit du jeton — accepter celui que l'en-tête
        annonce laisserait passer un jeton `alg: none`.
        """
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.PyJWTError as error:
            raise AdminAuthError(_EXPIRED_MESSAGE, status=401) from error

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AdminAuthError(_EXPIRED_MESSAGE, status=401)

        return subject

    async def _load_admin(self, user_id: str) -> AdminIdentity:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_ADMIN_IDENTITY_QUERY, (user_id,))
            row = await cursor.fetchone()

        # Jeton authentique dont le compte n'existe plus, ou dont le `sub` ne
        # désigne pas un compte — celui d'un lien d'avis porte une inscription.
        # Dans les deux cas le jeton ne vaut rien : 401, et non 403, qui
        # laisserait croire qu'il ne manque qu'une permission.
        if row is None:
            raise AdminAuthError(_EXPIRED_MESSAGE, status=401)

        if row["role_code"] != ADMIN_ROLE_CODE:
            raise AdminAuthError(
                "Cette action est réservée à l'administration", status=403
            )

        return AdminIdentity(
            user_id=row["user_id"],
            email=row["email"],
            administrator_id=row["administrator_id"],
        )
