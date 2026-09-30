"""La lecture des résultats d'outil de `mass-mcp`."""

from __future__ import annotations

import json

from mass_agents.tools import parse_tool_payload

APERCU = {"sent": False, "confirmationRequired": True, "recipientCount": 12}


def test_un_texte_json_est_lu_comme_un_objet():
    assert parse_tool_payload(json.dumps(APERCU)) == APERCU


def test_les_blocs_de_contenu_sont_lus_comme_du_texte():
    """Anthropic rend une liste de blocs dès qu'il y a autre chose que du texte."""
    blocs = [{"type": "text", "text": json.dumps(APERCU)}]

    assert parse_tool_payload(blocs) == APERCU


def test_un_contenu_illisible_n_est_pas_une_erreur():
    assert parse_tool_payload("12 évènements trouvés") is None
    assert parse_tool_payload("") is None
    assert parse_tool_payload(None) is None
    assert parse_tool_payload("[1, 2]") is None
