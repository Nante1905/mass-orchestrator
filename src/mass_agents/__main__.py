"""Point d'entrée : configuration, journalisation, écoute.

    python -m mass_agents

La configuration est lue **avant** de construire quoi que ce soit : une variable
manquante doit arrêter le processus avec le nom de la variable, pas provoquer
une trace au premier appel.
"""

from __future__ import annotations

import logging

import uvicorn

from mass_agents.api import create_app
from mass_agents.config import load_config_or_exit
from mass_agents.eventloop import uvicorn_loop_options


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    config = load_config_or_exit()

    uvicorn.run(
        create_app(config),
        host=config.http.host,
        port=config.http.port,
        # Les journaux d'accès de uvicorn diraient l'URL de chaque run et rien
        # de ce qui compte. Ce qui doit être tracé l'est par le service :
        # `agents.approval_log` pour les écritures, le journal applicatif pour
        # le reste.
        access_log=False,
        # Sur Windows, impose la boucle que psycopg sait utiliser ; ailleurs,
        # laisse uvicorn choisir — et prendre uvloop s'il est là.
        **uvicorn_loop_options(), # type: ignore
    )


if __name__ == "__main__":
    main()
