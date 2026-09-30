"""Les connexions à la base, et leur cycle de vie.

Deux pools plutôt qu'un, et la raison n'est pas la performance : ils ne visent
pas le même schéma. Celui de l'identité lit `public` — les tables métier de
MASS, en lecture seule de fait. Celui de l'orchestrateur écrit dans `agents`,
son schéma réservé.

Les mélanger obligerait à qualifier chaque table à la main, et surtout
rendrait possible qu'un checkpoint atterrisse dans `public` le jour où le
`search_path` change : les tables du checkpointer de LangGraph sont créées sans
qualification, c'est la connexion qui décide de leur emplacement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from mass_agents.config import DatabaseConfig
from mass_agents.eventloop import running_loop_is_usable

logger = logging.getLogger(__name__)

#: Réglages du pool des checkpoints, tels que LangGraph les attend.
#:
#: `autocommit` parce que le checkpointer gère ses propres transactions.
#: `prepare_threshold=0` prépare chaque instruction dès son premier usage — ses
#: requêtes sont peu nombreuses et rejouées à chaque superstep, c'est le cas
#: pour lequel la préparation existe.
_CHECKPOINT_POOL_KWARGS = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}

#: Réglages du pool d'identité — et `None` n'est pas `0`.
#:
#: `prepare_threshold=None` **désactive** la préparation, là où `0` l'impose dès
#: le premier appel. La distinction compte ici : une instruction préparée ne peut
#: porter qu'une seule commande, et les scripts de `sql/` en contiennent
#: plusieurs. C'est ce pool qui les applique au démarrage.
#:
#: Rien n'est perdu par ailleurs : sa seule requête récurrente est une lecture
#: indexée sur la clé primaire, que la préparation n'accélérerait pas assez pour
#: justifier d'occuper un emplacement d'instruction sur chaque connexion du pool.
_IDENTITY_POOL_KWARGS = {
    "autocommit": True,
    "prepare_threshold": None,
    "row_factory": dict_row,
}


@dataclass(slots=True)
class Database:
    """Les deux pools, ouverts et fermés ensemble."""

    identity: AsyncConnectionPool
    agents: AsyncConnectionPool

    @classmethod
    def create(cls, config: DatabaseConfig) -> Database:
        """Construit les pools sans les ouvrir.

        L'ouverture est différée à `open()` : un constructeur qui se connecte
        rend le service impossible à instancier dans un test, et transforme une
        base absente en erreur d'import.
        """
        return cls(
            identity=AsyncConnectionPool(
                config.identity_dsn,
                kwargs=_IDENTITY_POOL_KWARGS,
                open=False,
                max_size=10,
            ),
            agents=AsyncConnectionPool(
                config.checkpoint_dsn,
                kwargs=_CHECKPOINT_POOL_KWARGS,
                open=False,
                max_size=10,
            ),
        )

    async def open(self) -> None:
        # Contrôlé avant d'ouvrir quoi que ce soit. Sans cela, psycopg tenterait
        # de se connecter pendant dix secondes et l'échec final parlerait d'une
        # base injoignable — on chercherait Postgres alors que le problème est
        # la boucle d'évènements du processus.
        if not running_loop_is_usable():
            raise RuntimeError(
                "Boucle d'évènements incompatible avec psycopg : sur Windows, "
                "lancer le service par `python -m mass_agents`, ou passer "
                "`--loop mass_agents.eventloop:selector_loop` à la commande "
                "uvicorn."
            )

        await self.identity.open(wait=True, timeout=10)
        await self.agents.open(wait=True, timeout=10)
        logger.info("Pools de connexion ouverts")

    async def close(self) -> None:
        await self.identity.close()
        await self.agents.close()
