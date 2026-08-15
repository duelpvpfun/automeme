"""Shared pytest fixtures.

Each test gets an isolated temp data dir + SQLite DB and DRY-RUN mode, so no
network posting ever happens and tests never touch a developer's real config.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AUTOMEME_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTOMEME_DRY_RUN", "true")
    monkeypatch.setenv("AUTOMEME_PANEL_PASSWORD", "test-password")
    monkeypatch.setenv("AUTOMEME_SECRET_KEY", "test-secret-key-123456789")

    # Isolate tests from any real .env / X credentials in the environment.
    for key in list(os.environ):
        if key.startswith("AUTOMEME_X_") or key.startswith("X_"):
            monkeypatch.delenv(key, raising=False)

    # Reset cached config + engine so they pick up the temp env. Disable .env
    # loading so a developer's real credentials never bleed into tests.
    import automeme.config as config
    monkeypatch.setattr(config.Config, "model_config",
                        {**config.Config.model_config, "env_file": None})
    config.get_config.cache_clear()

    import automeme.db as db
    db._engine = None
    db._SessionFactory = None
    db.init_db()

    import automeme.settings_store as settings_store
    settings_store.ensure_defaults()

    # Fresh publishing client bound to dry-run config.
    from automeme.publishing import x_client
    x_client.reset_client_for_tests()

    yield {"data_dir": data_dir}


@pytest.fixture()
def png_bytes():
    """A small valid PNG (>=200x200) for image tests."""
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (400, 400), (120, 90, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
