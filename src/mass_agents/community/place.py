"""Où se trouve l'association, pour ce qui en dépend : l'heure et le ciel.

Antananarivo est dans l'hémisphère sud. Ce n'est pas un détail pour un post
d'observation : les Ursides n'y sont jamais visibles, les Perséides à peine, et
les Êta Aquarides y sont bien meilleures qu'en Europe — alors que la plupart des
sources lues sont écrites depuis l'hémisphère nord.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Final

PLACE: Final[str] = "Antananarivo, Madagascar"
LATITUDE: Final[float] = -18.9

#: Madagascar n'a pas d'heure d'été : un décalage fixe suffit, et évite de
#: dépendre de la base `tzdata`, absente par défaut sous Windows.
LOCAL_TZ: Final[timezone] = timezone(timedelta(hours=3), "EAT")
