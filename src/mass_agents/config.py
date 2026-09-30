"""Configuration du service, lue et validée une fois au démarrage.

Même patron que `mass-mcp/src/config.ts` : une variable manquante ou aberrante
arrête le processus ici plutôt qu'au premier appel. Un `SECRET` vide se lirait
autrement comme une salve de jetons invalides, et on chercherait la panne du
côté du back-office.

L'environnement est plat — c'est ce qu'un `.env` sait porter — mais ce que le
reste du code consomme est un objet gelé, groupé par responsabilité. Un module
qui a besoin du plafond de tours ne se voit pas offrir le secret de signature.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Le niveau d'effort demandé au modèle (`output_config.effort`).
Effort = Literal["low", "medium", "high"]


class _Env(BaseSettings):
    """Les variables telles qu'elles arrivent, avec leurs contraintes."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    PORT: int = Field(default=4100, ge=1, le=65_535)
    HOST: str = "0.0.0.0"
    ALLOWED_ORIGINS: str = ""

    SECRET: str = Field(min_length=1)

    MCP_URL: str = "http://localhost:4000/mcp"
    MCP_TIMEOUT_S: float = Field(default=30.0, gt=0)

    DB_HOST: str = "localhost"
    DB_PORT: int = Field(default=5432, ge=1, le=65_535)
    DB_USER: str = Field(min_length=1)
    DB_PASSWORD: str = ""
    DB_NAME: str = Field(min_length=1)
    CHECKPOINT_SCHEMA: str = Field(default="agents", pattern=r"^[a-z_][a-z0-9_]*$")

    ANTHROPIC_API_KEY: str = Field(min_length=1)
    MODEL: str = "claude-opus-5"
    MODEL_MAX_TOKENS: int = Field(default=8_192, ge=1)
    AGENT_EFFORT: Effort = "high"

    MAX_STEPS: int = Field(default=15, ge=1)
    MAX_TOKENS_PER_REQUEST: int = Field(default=400_000, ge=1)
    RUN_TIMEOUT_S: float = Field(default=300.0, gt=0)


@dataclass(frozen=True, slots=True)
class HttpConfig:
    host: str
    port: int
    allowed_origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """De quoi vérifier un jeton, et rien de plus.

    Le secret sert uniquement à contrôler une signature : ce service n'émet
    aucun jeton et n'a pas de route de connexion. Émettre depuis ici créerait un
    second endroit où l'identité se décide.
    """

    secret: str


@dataclass(frozen=True, slots=True)
class McpConfig:
    url: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    name: str
    checkpoint_schema: str

    @property
    def identity_dsn(self) -> str:
        """Connexion de relecture du rôle, sur le schéma `public` de MASS."""
        return self._dsn()

    @property
    def checkpoint_dsn(self) -> str:
        """Connexion des checkpoints, épinglée sur le schéma dédié.

        Le checkpointer de LangGraph crée ses tables sans les qualifier d'un
        schéma : c'est donc le `search_path` qui décide où elles atterrissent.
        Épingler la connexion est la seule manière de garantir qu'aucun
        `checkpoints` n'apparaisse dans `public`, à côté des tables métier.
        """
        options = quote(f"-c search_path={self.checkpoint_schema}")
        return f"{self._dsn()}?options={options}"

    def _dsn(self) -> str:
        return (
            f"postgresql://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{quote(self.name, safe='')}"
        )


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    model: str
    max_tokens: int
    effort: Effort


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """Plafonds durs, par demande de l'utilisateur.

    Ils ne sont pas là pour régler la qualité mais pour borner la dépense : un
    graphe qui boucle facture un appel de modèle à chaque tour, et personne ne
    s'en aperçoit avant la facture.

    « Par demande » et non par fil : un plafond sur la vie du fil confondrait la
    protection contre une boucle avec la longueur d'une conversation, et rendrait
    un fil définitivement muet au bout de quelques questions.
    """

    max_steps: int
    max_tokens_per_request: int
    run_timeout_s: float


@dataclass(frozen=True, slots=True)
class AppConfig:
    http: HttpConfig
    auth: AuthConfig
    mcp: McpConfig
    database: DatabaseConfig
    llm: LlmConfig
    limits: LimitsConfig


def _split_origins(raw: str) -> tuple[str, ...]:
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())


def _build(env: _Env) -> AppConfig:
    return AppConfig(
        http=HttpConfig(
            host=env.HOST,
            port=env.PORT,
            allowed_origins=_split_origins(env.ALLOWED_ORIGINS),
        ),
        auth=AuthConfig(secret=env.SECRET),
        mcp=McpConfig(url=env.MCP_URL, timeout_s=env.MCP_TIMEOUT_S),
        database=DatabaseConfig(
            host=env.DB_HOST,
            port=env.DB_PORT,
            user=env.DB_USER,
            password=env.DB_PASSWORD,
            name=env.DB_NAME,
            checkpoint_schema=env.CHECKPOINT_SCHEMA,
        ),
        llm=LlmConfig(
            api_key=env.ANTHROPIC_API_KEY,
            model=env.MODEL,
            max_tokens=env.MODEL_MAX_TOKENS,
            effort=env.AGENT_EFFORT,
        ),
        limits=LimitsConfig(
            max_steps=env.MAX_STEPS,
            max_tokens_per_request=env.MAX_TOKENS_PER_REQUEST,
            run_timeout_s=env.RUN_TIMEOUT_S,
        ),
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """La configuration, construite une fois.

    Lève `ValidationError` ; c'est le point d'entrée qui la traduit en message
    lisible et en code de sortie — une trace pydantic dans un log de démarrage
    ne dit pas quelle variable renseigner.
    """
    return _build(_Env()) # type: ignore


def load_config_or_exit() -> AppConfig:
    """Variante pour le point d'entrée : un message, puis on s'arrête."""
    try:
        return get_config()
    except ValidationError as error:
        details = "\n".join(
            f"  {'.'.join(str(part) for part in issue['loc'])} : {issue['msg']}"
            for issue in error.errors()
        )
        print(f"Configuration invalide :\n{details}", file=sys.stderr)
        raise SystemExit(1) from error
