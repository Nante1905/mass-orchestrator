"""Configuration minimale et fixtures partagées.

La configuration du service est validée à l'import de plusieurs modules : les
variables obligatoires sont renseignées ici, avant que quoi que ce soit ne la
lise. Aucune ne pointe vers une ressource réelle — la suite ne joint ni base, ni
serveur MCP, ni API de modèle.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SECRET", "secret-de-test")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_NAME", "mass_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")

from tests.fakes import FakeApprovalLog, FakeTool


@pytest.fixture
def send_email_tool() -> FakeTool:
    return FakeTool(
        "send_email",
        {
            "sent": True,
            "receiver": "commission@example.org",
            "subject": "Sortie de samedi",
            "historyId": "EMH0042",
            "message": "Courriel remis et tracé dans l'historique.",
        },
    )


@pytest.fixture
def approval_log() -> FakeApprovalLog:
    return FakeApprovalLog()
