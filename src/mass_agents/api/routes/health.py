"""La sonde de disponibilité.

Publique, et elle ne dit rien qu'un attaquant puisse exploiter : ni version, ni
identifiants, ni détail de la base. Elle rappelle en revanche l'adresse du
serveur MCP, qui est ce qu'on veut lire en premier quand l'agent ne répond plus.
"""

from __future__ import annotations

from fastapi import APIRouter

from mass_agents.config import get_config

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    config = get_config()
    return {
        "status": "ok",
        "mcp": config.mcp.url,
        "checkpointSchema": config.database.checkpoint_schema,
    }
