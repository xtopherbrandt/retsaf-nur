"""The against-app harness: ``runcoach`` commands answered by the served app, with a fake Garmin.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are (pytest runs ``--import-mode=importlib`` and ``tests/`` has no ``__init__.py``, so test
modules cannot import each other). A test module binds ``cli_against_app`` as a module global so
pytest finds the fixture; it needs the autouse ``isolated_data_dir`` from ``conftest.py``.

- ``cli_against_app``: a ``TestClient(app)`` installed as ``runcoach_cli.api_client.client``, a
  ``cli.toml`` under the test's tmp dir naming it (so ``garmin.token_dir()`` never resolves to the
  athlete's home), the Garmin library reported present and the CLI's today fixed at ``TODAY``.
- ``FakeGarmin``: serves listed activities, their ORIGINAL zips and each day's snapshot zip; 404
  otherwise. Nothing reaches the network and no token is read. ``_serve`` installs one.
- ``_activity``, ``_zip``, ``_run`` and ``_counts`` build a listed summary, a zip, run one CLI
  command and spell the race tags counts line.
- ``_stored_laps`` reads one store's ``laps`` and ``workout_steps`` rows for comparison.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_cli import api_client, garmin
from runcoach_cli import config as cli_config
from runcoach_cli.commands import sync
from runcoach_cli.main import app as cli_app
from typer.testing import CliRunner

BASE_URL = "http://testserver"
TODAY = date(2026, 10, 12)

runner = CliRunner()


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _activity(
    activity_id: int,
    local: str,
    type_key: str,
    type_id: int,
    parent: int,
    manual: bool = False,
    gmt: str | None = None,
) -> dict[str, Any]:
    """A listed summary. ``startTimeGMT`` is ``gmt`` when given, else it repeats the local start:
    as no stored file starts then, the race tags step reads every such listed run as not stored,
    and none is a race."""
    return {
        "activityId": activity_id,
        "activityName": f"activity {activity_id}",
        "startTimeLocal": local,
        "startTimeGMT": local if gmt is None else gmt,
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": parent},
        "eventType": {"typeKey": "uncategorized", "typeId": 9},
        "manualActivity": manual,
    }


class FakeGarmin:
    """Serves listed activities, their ORIGINAL zips and each day's snapshot zip; 404 otherwise."""

    def __init__(
        self, activities: list[dict[str, Any]], originals: dict[int, bytes], snapshots: dict[date, bytes]
    ) -> None:
        self.activities = activities
        self.originals = originals
        self.snapshots = snapshots
        self.downloads: list[tuple[str, Any]] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        self.downloads.append(("original", activity_id))
        if activity_id not in self.originals:
            raise garmin.NotFound(f"no original for {activity_id}")
        return self.originals[activity_id]

    def download_snapshot(self, day: date) -> bytes:
        self.downloads.append(("snapshot", day))
        if day not in self.snapshots:
            raise garmin.NotFound(f"no snapshot on {day}")
        return self.snapshots[day]


@pytest.fixture
def cli_against_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path):
    """A TestClient on the app, installed as the CLI's HTTP client, and a tmp ``cli.toml`` naming it."""
    data_dir = Path(db._load_config_cached().data_dir).resolve()
    assert data_dir.is_relative_to(tmp_path.resolve()), data_dir
    config_path = tmp_path / "cli-home" / "cli.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'api_url = "{BASE_URL}"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "CONFIG_PATH", config_path)
    assert garmin.token_dir().resolve().is_relative_to(tmp_path.resolve())
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    with TestClient(app, base_url=BASE_URL) as client:
        monkeypatch.setattr(api_client, "client", client)
        yield client


def _serve(monkeypatch: pytest.MonkeyPatch, fake: FakeGarmin) -> None:
    monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))


def _run(*args: str):
    result = runner.invoke(cli_app, list(args))
    print(f"$ runcoach {' '.join(args)} -> exit {result.exit_code}\n{result.output}")
    return result


def _counts(**outcomes: int) -> str:
    """The race tags counts line with the named outcomes set and every other count 0."""
    words = {
        "tagged": 0,
        "retagged": 0,
        "cleared": 0,
        "unchanged": 0,
        "not_stored": 0,
        "mismatch": 0,
        "ambiguous": 0,
        "workouts_tagged": 0,
    }
    assert set(outcomes) <= set(words), outcomes
    words.update(outcomes)
    return (
        f"Race tags: tagged {words['tagged']}, retagged {words['retagged']}, cleared {words['cleared']}, "
        f"unchanged {words['unchanged']}, not stored {words['not_stored']}, mismatch {words['mismatch']}, "
        f"ambiguous {words['ambiguous']}, other tag 0, skipped 0, workouts tagged {words['workouts_tagged']}"
    )


def _stored_laps(data_dir: Path) -> dict[str, Any]:
    """The store's only session's ``laps_read_at``, and its ``laps`` (by ``lap_index``) and
    ``workout_steps`` (by ``seq``) rows with every column but ``session_id``."""
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    conn.row_factory = sqlite3.Row
    try:
        sessions = [dict(r) for r in conn.execute("SELECT session_id, laps_read_at FROM sessions")]
        assert len(sessions) == 1, sessions
        session_id = sessions[0]["session_id"]

        def rows(sql: str) -> list[dict[str, Any]]:
            out = [dict(r) for r in conn.execute(sql, (session_id,))]
            for row in out:
                assert row.pop("session_id") == session_id
            return out

        laps = rows("SELECT * FROM laps WHERE session_id = ? ORDER BY lap_index")
        steps = rows("SELECT * FROM workout_steps WHERE session_id = ? ORDER BY seq")
    finally:
        conn.close()
    return {"laps_read_at": sessions[0]["laps_read_at"], "laps": laps, "workout_steps": steps}
