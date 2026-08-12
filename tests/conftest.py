"""Shared fixtures: network guard, env scrubber, tmp config dir.

Mock mode is not a convention unless something enforces it. This file is what
makes "the suite runs offline with no keys" true starting now, rather than
aspirational once collectors and LLM calls exist.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import pytest

_ENV_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET")


def _blocked_connect(self, *args, **kwargs):
    raise RuntimeError("network access attempted during tests -- mock mode is mandatory")


@pytest.fixture(autouse=True, scope="session")
def _network_guard():
    """Raise on any socket connect attempt for the whole test session."""
    original_connect = socket.socket.connect
    socket.socket.connect = _blocked_connect
    try:
        yield
    finally:
        socket.socket.connect = original_connect


@pytest.fixture(autouse=True)
def _scrub_secret_env(monkeypatch):
    """Remove every *_KEY, *_TOKEN, *_SECRET from the environment before each
    test. A test that only passes because a real key happens to be present in
    the CI environment is not proving what it claims to prove."""
    for name in list(os.environ):
        if name.endswith(_ENV_SECRET_SUFFIXES):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """A temp config/ directory: the minimal settings fixture + the repo's real
    credibility.yaml (already hand-authored and expected to validate clean)."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    dest = tmp_path / "config"
    dest.mkdir()
    shutil.copy(fixtures_dir / "settings_minimal.yaml", dest / "settings.yaml")
    repo_root = Path(__file__).parent.parent
    shutil.copy(repo_root / "config" / "credibility.yaml", dest / "credibility.yaml")
    return dest
