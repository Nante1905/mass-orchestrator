"""Le choix de la boucle d'évènements, qui n'en est un que sur Windows.

psycopg refuse de fonctionner en mode asynchrone sur `ProactorEventLoop` — il a
besoin de `SelectorEventLoop`. Or c'est précisément `ProactorEventLoop` que
Python, et donc uvicorn, choisit par défaut sur Windows.

Sans ce module, le service démarre, accepte le port, puis échoue à ouvrir ses
pools : dix secondes de tentatives et un message de psycopg qui parle de boucle
d'évènements là où l'on cherchait une base injoignable. Le symptôme ne désigne
pas sa cause, d'où le fichier plutôt qu'une ligne perdue dans le point d'entrée.

Ailleurs que sur Windows la question ne se pose pas, et on laisse uvicorn
choisir — c'est ce qui lui permet de prendre uvloop quand il est disponible.
"""

from __future__ import annotations

import asyncio
import sys


def selector_loop() -> asyncio.AbstractEventLoop:
    """La boucle que psycopg sait utiliser.

    Passée à uvicorn par son nom d'import (`mass_agents.eventloop:selector_loop`)
    plutôt que par un `set_event_loop_policy` global : la politique globale est
    dépréciée depuis Python 3.14, et uvicorn crée sa boucle lui-même de toute
    façon.
    """
    return asyncio.SelectorEventLoop()


def uvicorn_loop_options() -> dict[str, str]:
    """Ce qu'il faut passer à uvicorn, et rien sur les autres plateformes."""
    if sys.platform == "win32":
        return {"loop": "mass_agents.eventloop:selector_loop"}
    return {}


def running_loop_is_usable() -> bool:
    """Dit si la boucle en cours convient à psycopg.

    Sert au contrôle posé à l'ouverture des pools : le seul moment où l'on peut
    encore expliquer le problème avant qu'il ne se déguise en base absente.
    """
    if sys.platform != "win32":
        return True

    return not isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop)
