"""F020 end to end: the import against one-file uploads (AC1), and ``runcoach import`` against the app (AC10).

This module imports both workspace members (``runcoach_api`` and ``runcoach_cli``), so it runs
under ``uv run --all-packages``; ``pytest.importorskip`` skips it where the CLI is absent, and the
release gate's no-SKIPPED check catches a run where that happened.

**AC1 equality.** The reference section 9 archive (``tests/support/import_archive.py``, built in
``tmp_path`` from corpus fixtures) is imported into store A. The files the import stored, and the
ones it failed, are then uploaded with ``POST /sessions`` in the import's own processing order
into store B, which has the same prior contents (none) and the same config. Every ``sessions``
column and every ``GET /sessions/{id}/load`` body must be equal, with exactly these exclusions:
``session_id``, ``upload_order`` and ``import_id`` (columns), ``context.ingested_at`` (a
wall-clock stamp written by ``mapping.to_canonical``), and every session id nested in a load body,
which is compared after mapping store A's ids to store B's by ``(source_device, start_time)``.
The ``skipped_sport`` file is not uploaded to store B: the import wrote nothing for it, and a
one-file upload would store it as ``other``.

Fixtures cited by the provenance table's kind: ``sample_run`` and ``strap_run_hrv`` (real runs),
``strap_hrv_capture`` (an HRV capture on the "HRV Snapshot" profile), ``strap_hrv_sample_run``
(an HRV capture recorded on the Run profile, kept as a run, not run proof) and
``sample_health_snapshot`` (a health snapshot).

**AC10 against the app.** ``runcoach import`` runs through ``CliRunner`` with
``runcoach_cli.api_client.client`` set to the app's ``TestClient`` and
``runcoach_cli.config.CONFIG_PATH`` pointed at a ``cli.toml`` in ``tmp_path`` (the CLI package's
own autouse isolation does not reach this package's tests). The CLI's poll sleep is a real
0.05 s sleep, never a no-op, so the poll loop does not compete with the worker's commits. The
system temp root is a per-test directory, so a zip the CLI leaves behind is visible.

Every test that starts an import drains the worker with ``job.wait_idle`` before it asserts on
the finished job, and the autouse ``_drain_workers`` drains again before teardown.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("runcoach_cli")

from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.config import AppConfig
from runcoach_api.imports import job
from runcoach_api.main import app
from runcoach_cli import api_client
from runcoach_cli import config as cli_config
from runcoach_cli.commands import imports as cli_imports
from runcoach_cli.main import app as cli_app
from typer.testing import CliRunner

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive
FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE_PROFILE = "HRV Snapshot"
DRAIN_TIMEOUT_S = 60.0
CLI_POLL_SLEEP_S = 0.05
API_URL = "http://testserver"

# The only columns AC1 lets differ between the two stores, and the only context key.
EXCLUDED_COLUMNS = {"session_id", "upload_order", "import_id"}
EXCLUDED_CONTEXT_KEYS = {"ingested_at"}

runner = CliRunner()


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _drain() -> None:
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"


def _use_store(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    """Point the API's config at ``data_dir``, with the capture profile declared."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=data_dir,
        resting_hrv_profile_names=[CAPTURE_PROFILE],
        athlete_timezone="UTC",
    )
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: config)
    db._load_config_cached.cache_clear()
    assert db._load_config_cached().data_dir == data_dir


def _session_rows(data_dir: Path) -> dict[tuple[str, str], dict]:
    """Every ``sessions`` row keyed by ``(source_device, start_time)``, every column included."""
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM sessions")]
    finally:
        conn.close()
    keyed = {(r["source_device"], r["start_time"]): r for r in rows}
    assert len(keyed) == len(rows)
    return keyed


def _comparable(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in EXCLUDED_COLUMNS}
    context = json.loads(out["context"]) if out["context"] is not None else None
    if isinstance(context, dict):
        for key in EXCLUDED_CONTEXT_KEYS:
            assert key in context, f"context has no {key}: the exclusion would be vacuous"
            context.pop(key)
    out["context"] = context
    return out


def _map_ids(value, id_map: dict[str, str]):
    """``value`` with every string that is a store-A session id replaced by its store-B id."""
    if isinstance(value, dict):
        return {k: _map_ids(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_ids(v, id_map) for v in value]
    if isinstance(value, str):
        return id_map.get(value, value)
    return value


def test_an_import_stores_what_one_file_uploads_store(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    store_a, store_b = isolated_data_dir, tmp_path / "store_b"
    source = tmp_path / "source"
    source.mkdir()
    archive = IA.build_archive(source)
    inner = dict(IA.inner_entries())

    _use_store(monkeypatch, store_a)
    with TestClient(app) as client:
        with archive.open("rb") as fh:
            started = client.post("/imports", files={"file": (archive.name, fh, "application/zip")})
        assert started.status_code == 202, started.text
        import_id = started.json()["import_id"]
        _drain()
        done = client.get(f"/imports/{import_id}").json()
        assert done["status"] == "completed", done["error"]
        files = done["files"]
        stored = [row for row in files if row["outcome"] == "stored"]
        assert len(stored) == 6, files
        for row in stored:
            assert client.get(f"/sessions/{row['session_id']}").json()["import_id"] == import_id
        loads_a = {row["session_id"]: client.get(f"/sessions/{row['session_id']}/load") for row in stored}
    assert all(r.status_code == 200 for r in loads_a.values()), loads_a
    rows_a = _session_rows(store_a)
    assert len(rows_a) == 6
    assert all(r["import_id"] == import_id for r in rows_a.values())

    # The same files, in the import's processing order, through POST /sessions into store B.
    uploaded = [row for row in files if row["outcome"] != "skipped_sport"]
    assert {row["outcome"] for row in uploaded} == {"stored", "failed"}, uploaded
    _use_store(monkeypatch, store_b)
    with TestClient(app) as client:
        for row in uploaded:
            outer, _, name = row["entry"].partition("!")
            assert outer == IA.INNER_ZIP, row["entry"]
            response = post_fit_bytes(client, name, inner[name], data={"resting_capture": "false"})
            if row["outcome"] == "stored":
                assert response.status_code == 201, (name, response.text)
                assert client.get(f"/sessions/{response.json()['session_id']}").json()["import_id"] is None
            else:
                # AC6: a failed row carries the status and detail POST /sessions gives that file.
                assert (response.status_code, response.json()["detail"]) == (row["status"], row["detail"]), (
                    name
                )
        rows_b = _session_rows(store_b)
        assert set(rows_b) == set(rows_a)
        loads_b = {r["session_id"]: client.get(f"/sessions/{r['session_id']}/load") for r in rows_b.values()}
    assert all(r.status_code == 200 for r in loads_b.values()), loads_b
    assert all(r["import_id"] is None for r in rows_b.values())

    columns = set(next(iter(rows_a.values())))
    # Exactly AC1's exclusions: a widened list would hide a real difference between the stores.
    assert EXCLUDED_COLUMNS == {"session_id", "upload_order", "import_id"}
    assert EXCLUDED_CONTEXT_KEYS == {"ingested_at"}
    assert EXCLUDED_COLUMNS <= columns
    assert {"name", "timer_time_s", "max_hr_bpm", "resting_hr_bpm", "threshold_hr_bpm", "context"} <= columns
    print("compared columns:", sorted(columns - EXCLUDED_COLUMNS))
    id_map = {rows_a[key]["session_id"]: rows_b[key]["session_id"] for key in rows_a}
    for key in sorted(rows_a, key=str):
        assert _comparable(rows_a[key]) == _comparable(rows_b[key]), key
        load_a = _map_ids(loads_a[rows_a[key]["session_id"]].json(), id_map)
        load_b = loads_b[rows_b[key]["session_id"]].json()
        assert load_a == load_b, key


# --- AC10: runcoach import against the app --------------------------------------------------------


@pytest.fixture
def cli_against_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The CLI's config, client, poll sleep and temp root, pointed at the app and ``tmp_path``.

    Yields the private temp root. The ``TestClient`` is entered here, so the app's lifespan runs
    once for the test.
    """
    config_path = tmp_path / "cli-home" / "cli.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'api_url = "{API_URL}"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "CONFIG_PATH", config_path)
    temp_root = tmp_path / "tmp-root"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(cli_imports, "sleep", lambda _seconds: time.sleep(CLI_POLL_SLEEP_S))
    with TestClient(app, base_url=API_URL) as client:
        monkeypatch.setattr(api_client, "client", client)
        yield temp_root, client


def _newest_import(client: TestClient) -> dict:
    items = client.get("/imports").json()["items"]
    assert len(items) == 1, items
    return client.get(f"/imports/{items[0]['import_id']}").json()


def test_the_cli_imports_a_directory_through_the_app(tmp_path: Path, cli_against_app) -> None:
    temp_root, client = cli_against_app
    runs = tmp_path / "my_runs"
    (runs / "nested").mkdir(parents=True)
    (runs / "sample_run.fit").write_bytes((FIXTURES / "sample_run.fit").read_bytes())
    (runs / "nested" / "strap_run_hrv.fit").write_bytes((FIXTURES / "strap_run_hrv.fit").read_bytes())
    (runs / "nested" / "activity.tcx").write_bytes(b"<TrainingCenterDatabase/>\n")
    before = sorted(p.relative_to(runs).as_posix() for p in runs.rglob("*"))

    result = runner.invoke(cli_app, ["import", str(runs)])
    _drain()
    print(result.output)
    assert result.exit_code == 0, result.output
    done = _newest_import(client)
    assert result.output.startswith(f"Import {done['import_id']} started (my_runs).")
    assert "Status: completed" in result.output
    assert "Stored: 2 runs, 0 resting captures" in result.output
    assert "Not FIT: 1 (tcx 1)" in result.output
    assert done["status"] == "completed", done["error"]
    assert done["archive_name"] == "my_runs"
    assert [row["outcome"] for row in done["files"]] == ["stored", "stored"]
    assert sorted(row["entry"] for row in done["files"]) == ["nested/strap_run_hrv.fit", "sample_run.fit"]
    assert done["counts"]["not_fit"] == {"tcx": 1}
    assert sorted(p.relative_to(runs).as_posix() for p in runs.rglob("*")) == before
    assert list(temp_root.rglob("*")) == [], "the CLI left its temp zip behind"


def test_a_plain_zip_of_fit_files_imports(tmp_path: Path, cli_against_app) -> None:
    temp_root, client = cli_against_app
    plain = tmp_path / "fit_files.zip"
    with zipfile.ZipFile(plain, "w") as out:
        for name in ("sample_run.fit", "strap_run_hrv.fit"):
            out.writestr(name, (FIXTURES / name).read_bytes())

    result = runner.invoke(cli_app, ["import", str(plain)])
    _drain()
    print(result.output)
    assert result.exit_code == 0, result.output
    assert "Stored: 2 runs, 0 resting captures" in result.output
    done = _newest_import(client)
    assert done["status"] == "completed", done["error"]
    assert done["archive_name"] == "fit_files.zip"
    assert [row["outcome"] for row in done["files"]] == ["stored", "stored"]
    assert done["counts"]["not_fit"] == {}
    assert plain.exists()
    assert list(temp_root.rglob("*")) == []


def test_import_status_of_an_interrupted_import_exits_1(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job the previous process left ``running`` is ``interrupted`` by the next startup; the CLI exits 1."""
    with TestClient(app):
        pass  # the schema
    conn = db.get_connection(isolated_data_dir)
    try:
        import_id = job.create_job(conn, "export.zip", 1)
        with conn:
            conn.execute("UPDATE imports SET status = 'running' WHERE import_id = ?", (import_id,))
    finally:
        conn.close()

    config_path = tmp_path / "cli-home" / "cli.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'api_url = "{API_URL}"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "CONFIG_PATH", config_path)
    with TestClient(app, base_url=API_URL) as client:  # the startup marks it interrupted
        monkeypatch.setattr(api_client, "client", client)
        result = runner.invoke(cli_app, ["import-status", import_id])
    print(result.output)
    assert result.exit_code == 1, result.output
    assert "Status: interrupted" in result.output
    assert f"import {import_id} was interrupted" in result.output
