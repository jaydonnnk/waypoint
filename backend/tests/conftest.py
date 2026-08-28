"""Shared test fixtures — auto-applied to every test in the suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_bot_token(monkeypatch):
    """M8 fix: guarantee WAYPOINT_BOT_TOKEN is unset for every test so
    TestClient(app) never spawns real Telegram polling. Tests that need
    a token set it explicitly after this fixture runs."""
    monkeypatch.delenv("WAYPOINT_BOT_TOKEN", raising=False)
