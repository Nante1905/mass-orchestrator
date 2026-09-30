"""L'enveloppe de réponse, celle que le back-office sait déjà lire.

Copie conforme de `mass-backend/src/modules/shared/types/ApiReponse.ts`, y
compris ses libellés par défaut. C'est un des quatre bénéfices de la façade : le
front n'a pas un second format d'erreur à apprendre, et le code qui affiche une
erreur de l'API affiche aussi celles de l'agent.

Diverger d'un champ suffirait à casser cette propriété — d'où la copie littérale
plutôt qu'une variante « améliorée ».
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    ok: bool
    payload: Any = None
    message: str = ""
    error: str = ""

    @classmethod
    def success(cls, payload: Any, message: str = "Request successful") -> ApiResponse:
        return cls(ok=True, payload=payload, message=message, error="")

    @classmethod
    def failure(cls, error: str, message: str = "Request failed") -> ApiResponse:
        return cls(ok=False, payload=None, message=message, error=error)
