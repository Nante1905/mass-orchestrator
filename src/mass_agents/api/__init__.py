"""La façade HTTP : l'unique frontière d'authentification du service."""

from mass_agents.api.app import create_app
from mass_agents.api.responses import ApiResponse

__all__ = ["ApiResponse", "create_app"]
