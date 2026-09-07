"""Shared fixtures. The API client lives here so any module can drive the app."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app against a throwaway database, reloaded so the module-level
    stores pick up RECUT_DB rather than writing into the repo."""
    monkeypatch.setenv("RECUT_DB", str(tmp_path / "api.db"))
    import importlib

    from recut import api

    importlib.reload(api)
    return TestClient(api.app)
