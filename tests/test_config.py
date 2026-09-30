"""La configuration, et l'isolement du schéma.

Le point vérifié ici est celui du lot 1 : les tables de conversation ne doivent
jamais atterrir dans `public`, où les migrations TypeORM de `mass-backend`
passent. Comme le checkpointer de LangGraph crée ses tables sans les qualifier,
c'est la chaîne de connexion qui porte toute la garantie.
"""

from __future__ import annotations

from mass_agents.config import DatabaseConfig


def _database(**overrides) -> DatabaseConfig:
    return DatabaseConfig(
        **{
            "host": "localhost",
            "port": 5433,
            "user": "postgres",
            "password": "motdepasse",
            "name": "mass_db_seed_test",
            "checkpoint_schema": "agents",
            **overrides,
        }
    )


def test_la_connexion_des_checkpoints_epingle_le_schema():
    dsn = _database().checkpoint_dsn

    assert "search_path" in dsn
    assert "agents" in dsn


def test_la_connexion_d_identite_reste_sur_public():
    """La relecture du rôle lit les tables métier, qui sont dans `public`."""
    assert "search_path" not in _database().identity_dsn


def test_les_identifiants_speciaux_sont_echappes():
    """Un mot de passe avec un `@` ou un `/` couperait la chaîne en deux."""
    dsn = _database(password="mot@de/passe:1").identity_dsn

    assert "mot@de/passe" not in dsn
    assert dsn.endswith("/mass_db_seed_test")


def test_les_origines_sont_lues_en_liste():
    from mass_agents.config import _split_origins

    assert _split_origins("http://a , http://b,") == ("http://a", "http://b")
    assert _split_origins("") == ()
