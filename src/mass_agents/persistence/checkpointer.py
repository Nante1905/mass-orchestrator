"""Le checkpointer, et le schéma qui l'accueille.

Un run interrompu par `interrupt()` doit se retrouver intact après un
redémarrage complet du service : c'est toute la raison d'être de la persistance
ici. Une validation humaine qui se perdrait au premier `docker restart`
obligerait à tout recommencer, et le courriel resterait à écrire.

Point de vigilance, vérifié plutôt que supposé : `get_checkpoint_metadata` de
LangGraph recopie dans les métadonnées persistées **toute valeur scalaire**
trouvée dans `config["configurable"]`. Un jeton d'administrateur passé
naïvement sous une clé ordinaire finirait donc en clair dans `checkpoints`. Les
deux parades sont posées dans `graph/context.py` ; ce module se contente de ne
pas en ajouter une troisième au mauvais endroit.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "sql"


async def apply_migrations(pool: AsyncConnectionPool) -> None:
    """Crée le schéma et les tables de ce service, dans l'ordre des fichiers.

    Les scripts sont idempotents (`if not exists`) et rejoués à chaque
    démarrage : c'est ce qui permet de déployer sans étape manuelle, et ce qui
    rend le premier lancement identique aux suivants.

    Ils sont exécutés sur le pool d'identité, qui pointe sur `public` : les
    ordres sont qualifiés du schéma, et créer `agents` depuis une connexion dont
    le `search_path` est déjà `agents` marcherait par chance, pas par
    construction.
    """
    scripts = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not scripts:
        logger.warning("Aucun script SQL trouvé dans %s", _MIGRATIONS_DIR)
        return

    async with pool.connection() as conn:
        for script in scripts:
            await conn.execute(script.read_text(encoding="utf-8"))
            logger.info("Script appliqué : %s", script.name)


async def create_checkpointer(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    """Le checkpointer, ses tables créées si elles manquent.

    `setup()` crée `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` et
    `checkpoint_migrations` sans les qualifier d'un schéma : elles atterrissent
    donc là où pointe le `search_path` du pool — `agents`, épinglé dans la
    chaîne de connexion. Ces tables ne sont pas dans nos scripts SQL parce que
    leur définition suit la version de LangGraph, pas la nôtre : les recopier
    serait un doublon qui divergerait à la première montée de version.
    """
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    logger.info("Checkpointer prêt")
    return checkpointer
