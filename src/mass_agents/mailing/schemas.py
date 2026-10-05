"""Ce que l'écran de communication envoie, et ce qu'il reçoit.

Comme pour les posts, deux formes de sortie. `EmailDraft` est ce que le modèle
remplit : un objet, un corps, et les références (`e1`…) des évènements qu'il a
utilisés. `EmailDraftResult` est ce que l'API rend : les références résolues,
et les informations à compléter relevées par le code dans le texte même.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field

Tone = Literal["chaleureux", "formel", "enthousiaste"]
Audience = Literal["membres", "participants", "public"]

#: Les libellés affichés par le formulaire, dans l'ordre où il les propose.
TONE_LABELS: Final[dict[Tone, str]] = {
    "chaleureux": "Chaleureux",
    "formel": "Formel",
    "enthousiaste": "Enthousiaste",
}

AUDIENCE_LABELS: Final[dict[Audience, str]] = {
    "membres": "Les adhérents",
    "participants": "Les inscrits à un évènement",
    "public": "Le grand public",
}

#: La colonne `subject` d'`email_history` et d'`email_template` : un objet plus
#: long serait refusé par le backend à l'envoi.
MAX_SUBJECT: Final[int] = 250

#: Le marqueur d'une information que le modèle n'a pas et ne doit pas inventer.
#: Le back-office refuse d'envoyer tant qu'il en reste un.
MISSING_MARKER: Final[str] = "[à compléter"


class CurrentDraft(BaseModel):
    """Le brouillon en cours, quand on demande de le reprendre plutôt que
    d'en écrire un nouveau."""

    subject: str = Field(default="", max_length=MAX_SUBJECT)
    body: str = Field(default="", max_length=20_000)


class EmailDraftRequest(BaseModel):
    """Le formulaire : ce que le courriel doit dire, à qui, sur quel ton."""

    brief: str = Field(min_length=10, max_length=2_000)
    tone: Tone = "chaleureux"
    audience: Audience = "membres"
    current: CurrentDraft | None = None


class EmailDraft(BaseModel):
    """Un courriel tel que le modèle le rédige."""

    subject: str = Field(description="L'objet du courriel, court et précis")
    body: str = Field(
        description=(
            "Le corps en texte brut : paragraphes séparés par une ligne vide, "
            "**gras** permis, ni HTML ni Markdown d'aucune autre sorte"
        )
    )
    event_ids: list[str] = Field(
        description="Les identifiants des évènements MASS utilisés (e1, e2…)"
    )


class EventRef(BaseModel):
    """Un évènement cité, résolu pour l'affichage."""

    title: str
    date: str | None


class EmailDraftResult(BaseModel):
    subject: str
    body: str
    events: list[EventRef]
    #: Les informations à fournir avant l'envoi, telles que le modèle les a
    #: marquées dans le texte (`[à compléter : …]`).
    missing: list[str]
    #: Ce qui a manqué sans empêcher la rédaction : l'agenda indisponible.
    warnings: list[str]
