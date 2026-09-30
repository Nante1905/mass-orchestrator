"""La lecture d'un résultat d'outil `mass-mcp`.

`mass-mcp` rend un JSON indenté dans un bloc de texte plutôt que d'utiliser le
`structuredContent` du protocole — un choix assumé côté serveur, tous les
clients ne rendant pas ce dernier au modèle. On défait donc ici ce que
`jsonResult` a fait là-bas.

Un seul endroit pour cette lecture : l'aperçu et l'exécution confirmée relisent
tous deux des résultats d'outil, et deux lectures séparées finiraient par
diverger sur le traitement des blocs de contenu.
"""

from __future__ import annotations

import json
from typing import Any


def tool_text(raw: Any) -> str:
    """Le texte d'un résultat d'outil, qu'il soit plat, en blocs ou déjà lu.

    C'est ce qu'on rend au modèle dans un `ToolMessage` quand le résultat ne
    vient pas de la boucle d'exécution ordinaire — l'aperçu, l'appel confirmé.
    """
    if isinstance(raw, str):
        return raw

    if isinstance(raw, list):
        return "".join(
            block.get("text", "")
            for block in raw
            if isinstance(block, dict) and block.get("type") == "text"
        )

    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)

    return "" if raw is None else str(raw)


def parse_tool_payload(raw: Any) -> dict[str, Any] | None:
    """Le contenu d'un résultat d'outil, lu comme l'objet JSON qu'il porte.

    Un contenu illisible n'est pas une erreur : c'est simplement un résultat qui
    n'est pas un objet JSON, et il y en a à chaque lecture.
    """
    if isinstance(raw, dict):
        return raw

    text = tool_text(raw)
    if not text.strip():
        return None

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    return parsed if isinstance(parsed, dict) else None
