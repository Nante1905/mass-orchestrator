"""Les rendez-vous du ciel à venir : les grandes pluies d'étoiles filantes.

Une table, pas un calcul d'éphémérides : les pics reviennent aux mêmes dates à
un jour près d'une année sur l'autre, ce qui suffit pour annoncer « vers le 21
octobre ». Les phases de la Lune, les éclipses et les conjonctions demanderaient
des éphémérides ; elles sont laissées de côté plutôt qu'approchées, parce
qu'une date fausse dans un post engage l'association.

Les taux sont les ZHR de l'International Meteor Organization : un maximum
théorique par ciel parfait, radiant au zénith. La hauteur maximale du radiant
est, elle, calculée pour la latitude de l'association (voir `place.py`) : c'est
elle qui dit si une pluie vaut un post d'observation, ou seulement une mention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from mass_agents.community.place import LATITUDE, LOCAL_TZ, PLACE
from mass_agents.community.sources import ContextItem

IMO_CALENDAR: Final[str] = "https://www.imo.net/resources/calendar/"

#: Jusqu'où regarder : un post s'anticipe de quelques semaines, pas de mois.
HORIZON: Final[timedelta] = timedelta(days=45)

#: En dessous, le radiant reste trop bas sur l'horizon pour qu'on voie grand-chose.
LOW_RADIANT_DEG: Final[float] = 25.0


@dataclass(frozen=True, slots=True)
class MeteorShower:
    name: str
    month: int
    day: int
    zhr: int
    #: Avec sa préposition : « du Lion », « de la Lyre », « d'Orion ».
    radiant: str
    #: Déclinaison du radiant au pic, en degrés.
    declination: float

    def max_altitude(self, latitude: float) -> float:
        """La hauteur du radiant à son passage au méridien."""
        return 90.0 - abs(latitude - self.declination)


METEOR_SHOWERS: Final[tuple[MeteorShower, ...]] = (
    MeteorShower("Quadrantides", 1, 3, 80, "du Bouvier", 49),
    MeteorShower("Lyrides", 4, 22, 18, "de la Lyre", 34),
    MeteorShower("Êta Aquarides", 5, 6, 50, "du Verseau", -1),
    MeteorShower("Delta Aquarides du Sud", 7, 30, 25, "du Verseau", -16),
    MeteorShower("Perséides", 8, 12, 100, "de Persée", 58),
    MeteorShower("Draconides", 10, 8, 10, "du Dragon", 54),
    MeteorShower("Orionides", 10, 21, 20, "d'Orion", 16),
    MeteorShower("Léonides", 11, 17, 15, "du Lion", 22),
    MeteorShower("Géminides", 12, 14, 150, "des Gémeaux", 33),
    MeteorShower("Ursides", 12, 22, 10, "de la Petite Ourse", 75),
)


def upcoming_sky_events(today: date, latitude: float = LATITUDE) -> list[ContextItem]:
    """Les pics des semaines à venir, du plus proche au plus lointain.

    Une pluie dont le radiant ne se lève jamais à cette latitude n'est pas
    proposée : il n'y a rien à observer, et l'annoncer serait une erreur.
    """
    events: list[tuple[date, MeteorShower]] = []
    for shower in METEOR_SHOWERS:
        peak = date(today.year, shower.month, shower.day)
        if peak < today:
            peak = date(today.year + 1, shower.month, shower.day)
        if peak - today <= HORIZON and shower.max_altitude(latitude) > 0:
            events.append((peak, shower))

    return [
        ContextItem(
            kind="sky",
            title=f"Pic des {shower.name} vers le {peak.day}/{peak.month:02d}",
            summary=_summary(shower, latitude),
            publisher="International Meteor Organization",
            url=IMO_CALENDAR,
            published=datetime(peak.year, peak.month, peak.day, tzinfo=LOCAL_TZ),
        )
        for peak, shower in sorted(events, key=lambda pair: pair[0])
    ]


def _summary(shower: MeteorShower, latitude: float) -> str:
    altitude = round(shower.max_altitude(latitude))
    visibility = (
        "radiant bas sur l'horizon : conditions médiocres, peu de météores"
        if altitude < LOW_RADIANT_DEG
        else "bonnes conditions si le ciel est sombre"
    )
    return (
        f"Pluie d'étoiles filantes, radiant dans la constellation "
        f"{shower.radiant}. Taux horaire zénithal (ZHR) d'environ {shower.zhr} "
        f"météores par heure : un maximum théorique par ciel parfait, en "
        f"pratique moins. Depuis {PLACE}, le radiant culmine vers {altitude}° "
        f"de hauteur — {visibility}."
    )
