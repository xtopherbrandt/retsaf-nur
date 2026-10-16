"""F025 AC5 end to end: ``runcoach sync laps`` against the served app, not a mock.

``runcoach sync laps`` runs through Typer's ``CliRunner`` on the harness in
``tests/support/against_app.py``: ``runcoach_cli.api_client.client`` is a ``TestClient(app)``, so the
capability check's ``/openapi.json`` read, ``POST /laps/candidates`` and
``PUT /sessions/{session_id}/laps`` are answered by the real routes on the isolated store (the
autouse ``isolated_data_dir``). ``FakeGarmin`` stands in for Garmin Connect and counts every
download. Nothing reaches the network: the autouse ``_no_network`` below refuses every socket
connection off the loopback and every ``curl_cffi`` entry point, and no token is read.

The files, by their rows in ``fixtures/README.md``'s provenance table: ``sample_run`` and
``hilly_run_8k_fr945`` are real runs (run proof yes), recorded on the same FR945 at different
starts, so the second is another run's file for the first's session.

A *pre-feature run* is ``sample_run`` uploaded with ``POST /sessions`` and then given the state a
run stored before F025 has: its ``laps`` and ``workout_steps`` rows deleted and its
``laps_read_at`` null. The Connect listing gives it the fixture's real UTC start, so the
candidates call matches it by start second.
"""

from __future__ import annotations

import copy
import importlib.util
import socket
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db

pytest.importorskip("runcoach_cli")

from runcoach_cli.commands import sync_laps

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


against_app = _load_support("against_app")

# The harness; cli_against_app is a fixture pytest finds here.
cli_against_app = against_app.cli_against_app
FakeGarmin = against_app.FakeGarmin
_activity = against_app._activity
_zip = against_app._zip
_run = against_app._run
_serve = against_app._serve
BASE_URL = against_app.BASE_URL

# sample_run's real start: UTC, and local as Connect lists it.
SAMPLE_RUN_GMT = "2026-09-01 12:34:21"
SAMPLE_RUN_LOCAL = "2026-09-01 14:34:21"
SAMPLE_RUN_STORED = "2026-09-01T12:34:21+00:00"
SINCE = "2026-09-01"
SAMPLE_RUN_LAPS = 10
SAMPLE_RUN_LISTED = 801
OTHER_RUN_LISTED = 901


def _laps_line(**outcomes: int) -> str:
    """The sync laps counts line with the named outcomes set and every other count 0."""
    counts = dict.fromkeys(sync_laps.OUTCOMES, 0)
    counts.update(outcomes)
    return "Laps: " + ", ".join(f"{sync_laps.OUTCOMES[key]} {n}" for key, n in counts.items())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Refuse every socket connection to an address off the loopback (the event loop's own
    socketpair stays allowed) and every ``curl_cffi`` entry point the Garmin library uses."""
    seen: list[Any] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    loopback = {"127.0.0.1", "::1", "localhost"}

    def _host(address: Any) -> Any:
        return address[0] if isinstance(address, tuple) else address

    def connect(self: socket.socket, address: Any) -> Any:
        if _host(address) not in loopback:
            seen.append(address)
            raise OSError(f"network blocked in tests: {address!r}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if _host(address) not in loopback:
            seen.append(address)
            raise OSError(f"network blocked in tests: {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    try:
        from curl_cffi import requests as cffi_requests
        from curl_cffi.requests.session import AsyncSession, Session
    except ImportError:
        return seen

    def refuse(*args: object, **kwargs: object) -> None:
        seen.append(kwargs.get("url") or args)
        raise OSError("curl_cffi blocked in tests")

    for name in ("request", "get", "post", "put", "patch", "delete", "head", "options"):
        if hasattr(cffi_requests, name):
            monkeypatch.setattr(cffi_requests, name, refuse)
    monkeypatch.setattr(Session, "request", refuse)
    monkeypatch.setattr(AsyncSession, "request", refuse)
    return seen


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _pre_feature_sample_run(client: TestClient, data_dir: Path) -> str:
    """Upload ``sample_run``, then delete its laps and workout steps and null ``laps_read_at``."""
    response = client.post(
        "/sessions",
        files={"file": ("sample_run.fit", _fixture("sample_run"), "application/octet-stream")},
        data={"runs_and_captures_only": "true"},
    )
    assert response.status_code == 201, response.text
    session_id = response.json()["session_id"]
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        with conn:
            conn.execute("DELETE FROM laps WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM workout_steps WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE sessions SET laps_read_at = NULL WHERE session_id = ?", (session_id,))
    finally:
        conn.close()
    before = against_app._stored_laps(data_dir)
    assert before == {"laps_read_at": None, "laps": [], "workout_steps": []}, before
    stored = client.get("/sessions", params={"limit": 200}).json()["items"]
    assert [(row["session_id"], row["start_time"]) for row in stored] == [(session_id, SAMPLE_RUN_STORED)]
    return session_id


def _listing(activity_id: int, original: bytes) -> FakeGarmin:
    """One listed run at ``sample_run``'s real start whose ORIGINAL holds ``original``."""
    return FakeGarmin(
        [_activity(activity_id, SAMPLE_RUN_LOCAL, "running", 1, 17, gmt=SAMPLE_RUN_GMT)],
        {activity_id: _zip([(f"{activity_id}_ACTIVITY.fit", original)])},
        {},
    )


def test_sync_laps_attaches_a_pre_feature_run_then_reports_already_read(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """A pre-feature ``sample_run`` gets its 10 laps from its Connect original; a second run of the
    same command reports it ``already read`` and downloads nothing."""
    session_id = _pre_feature_sample_run(cli_against_app, isolated_data_dir)
    fake = _listing(SAMPLE_RUN_LISTED, _fixture("sample_run"))
    _serve(monkeypatch, fake)

    first = _run("sync", "laps", "--since", SINCE)
    assert first.exit_code == 0, first.output
    assert first.output.splitlines() == [
        f"From {SINCE} to {against_app.TODAY.isoformat()}",
        _laps_line(attached=1),
    ]
    assert fake.downloads == [("original", SAMPLE_RUN_LISTED)]
    attached = against_app._stored_laps(isolated_data_dir)
    print(f"attached: {len(attached['laps'])} laps, {len(attached['workout_steps'])} steps")
    assert len(attached["laps"]) == SAMPLE_RUN_LAPS
    assert [lap["lap_index"] for lap in attached["laps"]] == list(range(SAMPLE_RUN_LAPS))
    assert attached["laps_read_at"] is not None
    detail = cli_against_app.get(f"/sessions/{session_id}")
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["laps"]) == SAMPLE_RUN_LAPS

    second = _run("sync", "laps", "--since", SINCE)
    assert second.exit_code == 0, second.output
    assert second.output.splitlines() == [
        f"From {SINCE} to {against_app.TODAY.isoformat()}",
        _laps_line(already_read=1),
    ]
    assert fake.downloads == [("original", SAMPLE_RUN_LISTED)], fake.downloads
    assert against_app._stored_laps(isolated_data_dir) == attached


def _operation_ids(doc: dict[str, Any]) -> set[str]:
    return {
        operation["operationId"]
        for path in doc["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }


def test_sync_laps_capability_check_passes_on_the_served_document(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The served ``/openapi.json`` lists both operations the command needs, read here
    independently of the command; the command's check passes on it and the run goes on to the
    listing. The same document without ``matchLapCandidates`` stops the run before Garmin is asked."""
    served = cli_against_app.get("/openapi.json")
    assert served.status_code == 200, served.text
    assert set(sync_laps.OPERATIONS) == {"attachSessionLaps", "matchLapCandidates"}
    assert set(sync_laps.OPERATIONS) <= _operation_ids(served.json())
    assert served.json()["paths"]["/laps/candidates"]["post"]["operationId"] == "matchLapCandidates"
    assert served.json()["paths"]["/sessions/{session_id}/laps"]["put"]["operationId"] == "attachSessionLaps"

    sync_laps.check_api(BASE_URL, date(2026, 9, 1))

    listed: list[tuple[date, date]] = []
    fake = FakeGarmin([], {}, {})
    real_list = fake.list_activities

    def list_activities(start: date, end: date) -> list[dict[str, Any]]:
        listed.append((start, end))
        return real_list(start, end)

    fake.list_activities = list_activities  # type: ignore[method-assign]
    _serve(monkeypatch, fake)
    result = _run("sync", "laps", "--since", SINCE)
    assert result.exit_code == 0, result.output
    assert sync_laps.OLD_API_LAPS not in result.output
    assert result.output.splitlines()[-1] == _laps_line()
    assert listed == [(date(2026, 9, 1), against_app.TODAY)]

    without = copy.deepcopy(served.json())
    del without["paths"]["/laps/candidates"]
    monkeypatch.setattr(against_app.app, "openapi_schema", without)
    listed.clear()
    stopped = _run("sync", "laps", "--since", SINCE)
    assert stopped.exit_code == 1, stopped.output
    assert sync_laps.OLD_API_LAPS in stopped.output
    assert listed == []


def test_sync_laps_reports_another_runs_file_as_identity_mismatch(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """A listed activity at the pre-feature run's start whose ORIGINAL is ``hilly_run_8k_fr945``
    is matched by start second, refused by the attach operation's identity check and reported as
    an identity mismatch; the run exits 1 and the session's laps stay unread."""
    session_id = _pre_feature_sample_run(cli_against_app, isolated_data_dir)
    fake = _listing(OTHER_RUN_LISTED, _fixture("hilly_run_8k_fr945"))
    _serve(monkeypatch, fake)

    result = _run("sync", "laps", "--since", SINCE)
    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    assert lines[:2] == [f"From {SINCE} to {against_app.TODAY.isoformat()}", _laps_line(identity_mismatch=1)]
    assert len(lines) == 3, lines
    prefix = f"Identity mismatch: 2026-09-01, activity {OTHER_RUN_LISTED} (activity {OTHER_RUN_LISTED}), "
    assert lines[2].startswith(prefix), lines[2]
    assert f"not session {session_id} (start {SAMPLE_RUN_STORED})" in lines[2]
    assert "nothing was written" in lines[2]
    assert fake.downloads == [("original", OTHER_RUN_LISTED)]
    assert against_app._stored_laps(isolated_data_dir) == {
        "laps_read_at": None,
        "laps": [],
        "workout_steps": [],
    }
