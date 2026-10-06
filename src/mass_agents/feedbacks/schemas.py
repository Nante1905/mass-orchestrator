"""Ce que la page des avis reçoit de l'analyse.

Comme pour les posts et les courriels, deux formes. `FeedbackAnalysis` est ce
que le modèle remplit : des références d'avis (`f1`, `f2`…) plutôt que du texte
recopié, pour que le code puisse vérifier chaque affirmation. Le résultat de
l'API, lui, est compté et illustré par le code à partir de ces références : le
nombre de mentions d'un thème et ses extraits ne viennent pas du modèle.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

Sentiment = Literal["positif", "neutre", "negatif"]

#: Les libellés affichés par la page, dans l'ordre où elle les présente.
SENTIMENT_LABELS: Final[dict[Sentiment, str]] = {
    "positif": "Positif",
    "neutre": "Mitigé",
    "negatif": "Négatif",
}

#: Au-delà, la requête grossit sans que la synthèse gagne en justesse : on
#: analyse les plus récents et on le dit.
MAX_FEEDBACKS: Final[int] = 200

#: Un avis fait 2000 caractères au plus ; on en garde assez pour le comprendre.
MAX_CONTENT: Final[int] = 1500

MAX_THEMES: Final[int] = 6
MAX_STRENGTHS: Final[int] = 5
MAX_IMPROVEMENTS: Final[int] = 6

#: Extraits montrés sous un thème, et leur longueur.
EXAMPLES_PER_THEME: Final[int] = 2
EXAMPLE_LENGTH: Final[int] = 180


class FeedbackSentiment(BaseModel):
    feedback_id: str = Field(description="L'identifiant de l'avis (f1, f2…)")
    sentiment: Sentiment


class Theme(BaseModel):
    label: str = Field(description="Le sujet, en quelques mots (« Accueil »)")
    sentiment: Sentiment = Field(
        description="Ce qu'en disent les avis qui l'abordent, dans l'ensemble"
    )
    summary: str = Field(description="Une phrase : ce que les participants en disent")
    feedback_ids: list[str] = Field(
        description="Les identifiants des avis qui abordent ce sujet"
    )


class FeedbackAnalysis(BaseModel):
    """L'analyse telle que le modèle la rédige."""

    summary: str = Field(
        description="La synthèse en trois à cinq phrases, en texte brut"
    )
    sentiments: list[FeedbackSentiment] = Field(
        description="Le ton de chaque avis, un et un seul par avis"
    )
    themes: list[Theme]
    strengths: list[str] = Field(description="Ce qui a plu, une phrase par point")
    improvements: list[str] = Field(
        description="Ce qu'il faudrait changer, une recommandation concrète par point"
    )


class EventInfo(BaseModel):
    id: str
    title: str
    date: str | None


class RatingSummary(BaseModel):
    """Les notes globales, telles que l'API les compte."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    total: int
    rated: int
    average_rating: float | None = Field(alias="averageRating")
    distribution: dict[str, int]


class ThemeExample(BaseModel):
    text: str
    rating: int | None


class ThemeResult(BaseModel):
    label: str
    sentiment: Sentiment
    summary: str
    #: Compté par le code sur les références, pas déclaré par le modèle.
    mentions: int
    examples: list[ThemeExample]


class SentimentCounts(BaseModel):
    positif: int
    neutre: int
    negatif: int


class FeedbackAnalysisResult(BaseModel):
    event: EventInfo
    #: Les avis lus par le modèle, et tous ceux de l'évènement.
    analysed: int
    total: int
    ratings: RatingSummary | None
    sentiment: SentimentCounts
    summary: str
    themes: list[ThemeResult]
    strengths: list[str]
    improvements: list[str]
    #: Ce qui limite la portée de l'analyse : peu d'avis, avis tronqués.
    warnings: list[str]
