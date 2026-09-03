"""Shared pytest fixtures for runcoach-api tests.

``isolated_data_dir`` is an autouse fixture that redirects
``db.get_connection()``'s config lookup to a per-test ``tmp_path``, so
no test -- here or in any later F003 task that touches the DB -- ever
opens or writes the real ``~/.runcoach/runcoach.db``. It works by
monkeypatching ``runcoach_api.db.config_module.load_config`` to return
a fake ``AppConfig`` pointed at a tmp_path-derived data dir; it's
autouse so every test gets isolation for free without needing to
request it explicitly.

``db.get_connection()`` wraps ``config_module.load_config`` in an
``lru_cache`` (maxsize=1) so the config is only read from disk once per
process. That cache is cleared here, before each test's monkeypatch
takes effect, so a stale cached config from a previous test never
leaks into this one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runcoach_api import db as db_module
from runcoach_api.config import AppConfig


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    fake_config = AppConfig(host="127.0.0.1", port=8000, data_dir=data_dir)
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: fake_config)
    db_module._load_config_cached.cache_clear()
    return data_dir
