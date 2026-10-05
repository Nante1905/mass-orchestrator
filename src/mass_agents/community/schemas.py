"""Ce que la page de community management envoie, et ce qu'elle reçoit.

Deux formes de sortie, et c'est voulu. `Suggestions` est ce que le modèle
remplit : des références de sources (`n3`, `s1`…) et aucune contrainte de
taille, que les sorties structurées des fournisseurs ne savent pas toutes
porter. `SuggestionsResult` est ce que l'API rend : les références résolues en
titres et liens, après une vérification faite par le code (voir `service.py`).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal[
    "actualite_spatiale",
    "decouverte",
    "observation",
    "vulgarisation",
    "evenement_mass",
]

#: Les libellés affichés par le formulaire, dans l'ordre où il les propose.
CATEGORY_LABELS: Final[dict[Category, str]] = {
    "actualite_spatiale": "Actualité spatiale",
    "decouverte": "Découverte scientifique",
    "observation": "Observation du ciel",
    "vulgarisation": "Vulgarisation",
    "evenement_mass": "Évènement MASS",
}

#: Au-delà, la réponse dépasse ce qu'un plafond de sortie raisonnable contient,
#: et la page devient une liste qu'on ne lit plus.
MAX_POSTS: Final[int] = 10

VisualFormat = Literal[
    "carre_1_1",
    "portrait_4_5",
    "carrousel",
    "story_9_16",
    "reel",
]


class SuggestionRequest(BaseModel):
    """Le formulaire : combien de posts, et dans quelles catégories.

    Sans catégorie, le modèle compose un mélange.
    """

    count: int = Field(ge=1, le=MAX_POSTS)
    categories: list[Category] = Field(default_factory=list, max_length=5)

    @field_validator("categories")
    @classmethod
    def _unique(cls, value: list[Category]) -> list[Category]:
        return list(dict.fromkeys(value))


class Visual(BaseModel):
    """Une proposition de visuel, en texte : de quoi briefer un graphiste."""

    format: VisualFormat = Field(
        description="Format Instagram/Facebook le plus adapté au contenu"
    )
    concept: str = Field(description="Ce que montre le visuel, concrètement")
    overlay_text: str = Field(
        description="Le texte incrusté sur le visuel, court ; vide s'il n'y en a pas"
    )
    style: str = Field(description="Ambiance, palette, typographie")
    slides: list[str] = Field(
        description="Pour un carrousel, le contenu de chaque vue ; vide sinon"
    )


class Post(BaseModel):
    """Un post tel que le modèle le rédige."""

    category: Category
    hook: str = Field(
        description="La première ligne, celle qui s'affiche avant « plus »"
    )
    text: str = Field(description="Le texte complet du post, accroche comprise")
    hashtags: list[str]
    visual: Visual
    source_ids: list[str] = Field(
        description="Les identifiants des éléments de contexte utilisés (n1, s2, e1…)"
    )


class Suggestions(BaseModel):
    posts: list[Post]


class SourceRef(BaseModel):
    """Une source citée, résolue pour l'affichage."""

    title: str
    publisher: str
    url: str | None
    date: str | None


class PostSuggestion(BaseModel):
    category: Category
    hook: str
    text: str
    hashtags: list[str]
    visual: Visual
    sources: list[SourceRef]


class SuggestionsResult(BaseModel):
    posts: list[PostSuggestion]
    #: Ce qui a manqué sans empêcher la génération : un flux injoignable,
    #: l'agenda MASS indisponible. La page peut l'afficher en discret.
    warnings: list[str]
