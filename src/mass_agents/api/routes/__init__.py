"""Les routes, une par domaine."""

from mass_agents.api.routes.community import router as community_router
from mass_agents.api.routes.health import router as health_router
from mass_agents.api.routes.threads import router as threads_router

__all__ = ["community_router", "health_router", "threads_router"]
