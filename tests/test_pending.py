"""La détection d'une écriture en attente de relecture.

Ces cas sont écrits à partir des charges utiles réelles de `mass-mcp`
(`sendEmail.ts`, `markAttendance.ts`). Ce qui est vérifié n'est pas qu'un
dictionnaire se lit, mais que la porte est gardée : une lecture ordinaire ne
déclenche pas de validation, et un envoi préparé en déclenche une.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from mass_agents.graph.pending import detect_pending_action, parse_tool_payload

APERCU_COURRIEL = {
    "sent": False,
    "confirmationRequired": True,
    "mode": "group",
    "groupId": "G0003",
    "groupTitle": "Commission observation",
    "recipientCount": 12,
    "recipients": ["a@example.org", "b@example.org"],
    "subject": "Sortie de samedi",
    "body": "<p>Rendez-vous à 20h.</p>",
    "bodyIsHtml": True,
}

ARGUMENTS = {
    "group_id": "G0003",
    "subject": "Sortie de samedi",
    "body": "<p>Rendez-vous à 20h.</p>",
}


def _tool_call(nom: str, args: dict, call_id: str) -> dict:
    return {"name": nom, "args": args, "id": call_id, "type": "tool_call"}


def _appel(nom: str, args: dict, resultat: dict, call_id: str = "call-1"):
    return [
        AIMessage(content="", tool_calls=[_tool_call(nom, args, call_id)]),
        ToolMessage(content=json.dumps(resultat), tool_call_id=call_id, name=nom),
    ]


def test_un_apercu_de_courriel_est_detecte():
    action = detect_pending_action(_appel("send_email", ARGUMENTS, APERCU_COURRIEL))

    assert action is not None
    assert action.tool_name == "send_email"
    # Les arguments sont conservés tels quels : la reprise rejoue le même appel.
    assert action.arguments == ARGUMENTS
    # L'aperçu n'est pas retouché — c'est lui qui sera relu.
    assert action.preview["recipientCount"] == 12


def test_une_lecture_ne_declenche_aucune_validation():
    messages = _appel("list_events", {"page": 1}, {"total": 3, "records": []})

    assert detect_pending_action(messages) is None


def test_une_ecriture_deja_confirmee_ne_redemande_rien():
    """Sans la marque du serveur, il n'y a rien à valider.

    C'est le cas qui suit une reprise approuvée : l'outil a écrit, son résultat
    ne porte pas `confirmationRequired`, et le fil doit continuer sans
    redemander la même approbation.
    """
    resultat = {"sent": True, "receiver": "a@example.org", "historyId": "EMH1"}

    assert detect_pending_action(_appel("send_email", ARGUMENTS, resultat)) is None


def test_un_outil_non_engageant_marque_par_erreur_est_ignore():
    """La liste des outils engageants fait autorité, pas la charge utile.

    Un outil de lecture qui rendrait `confirmationRequired` — par accident ou
    par une réponse forgée — ne doit pas pouvoir ouvrir un nœud de validation.
    """
    messages = _appel("list_events", {}, {"confirmationRequired": True})

    assert detect_pending_action(messages) is None


def test_le_dernier_apercu_prime():
    """Deux gestes préparés : c'est le plus récent qui correspond à l'intention.

    L'autre ne sera pas exécuté, et c'est voulu — un aperçu empilé sous un autre
    n'aurait été lu par personne.
    """
    premier = _appel(
        "mark_attendance",
        {"event_id": "EVT1"},
        {"applied": False, "confirmationRequired": True},
        "c1",
    )
    second = _appel("send_email", ARGUMENTS, APERCU_COURRIEL, "c2")

    action = detect_pending_action([*premier, *second])

    assert action is not None
    assert action.tool_name == "send_email"


def test_un_appel_sans_resultat_n_ouvre_rien():
    """Un `tool_use` dont le `tool_result` manque : rien n'a encore été calculé."""
    appel = AIMessage(
        content="", tool_calls=[_tool_call("send_email", ARGUMENTS, "c1")]
    )

    assert detect_pending_action([HumanMessage(content="écris-leur"), appel]) is None


def test_les_blocs_de_contenu_sont_lus_comme_du_texte():
    """Anthropic rend une liste de blocs dès qu'il y a autre chose que du texte."""
    blocs = [{"type": "text", "text": json.dumps(APERCU_COURRIEL)}]

    assert parse_tool_payload(blocs) == APERCU_COURRIEL


def test_un_contenu_illisible_n_est_pas_une_erreur():
    assert parse_tool_payload("12 évènements trouvés") is None
    assert parse_tool_payload("") is None
    assert parse_tool_payload(None) is None
