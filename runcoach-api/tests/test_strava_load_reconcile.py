"""F031 AC3 and AC4's counts: after a Strava import, every linked run whose saved load disagrees with
the rule is recomputed and counted (user ruling 2026-10-25: recompute on disagreement).

``db.reconcile_strava_load`` runs once a Strava import's rows have all committed and before the job
is ``completed``. One SQL pass reads every running session (and any session whose saved reason is
``strava_not_running``) with its saved load and its live Strava link, and selects the sessions that
disagree: the saved reason is ``strava_not_running`` while ``session_load.strava_exclusion`` says
no, or the other way round, or the saved excluded body names another row than the live link. Each
one is recomputed through ``db.recompute_session_load``; a recompute that moved the body into or out
of ``strava_not_running`` is one ``imports.load_changes`` entry and one ``excluded_from_load`` or
``restored_to_load`` count. Whole bodies are never compared, so a body naming a superseded anchor
version is not rewritten.

Every test posts archives built in ``tmp_path`` with ``tests/support/strava_archive.py`` to the
real app (the store is the autouse ``isolated_data_dir``, zone UTC unless a test sets another) and links rows by their CSV
start (step 1), so no archive carries an activity file. Sessions are ``sample_run`` (README
provenance: real run, run proof yes) and copies re-dated by whole days. The expectations are
written from F031's AC3, AC4 and the task text before the reconcile; the same author wrote both.

**Adversarial probe table** (rule adversarial-input-probes-are-a-task-deliverable): what the
selection reads, and what each degenerate form does.

| input | result | why |
|---|---|---|
| counted body, link typed ``Run`` | not recomputed (``test_a_counted_linked_run_is_not_rewritten``) | agrees; its stale anchor version is kept |
| counted body, link typed ``Alpine Ski`` | recomputed, ``excluded`` | the rule says exclude |
| ``no_hr`` body, link typed non-run | recomputed, ``excluded``, ``old_load`` null | the gate runs before ``no_hr`` |
| ``declared_capture`` body, link typed non-run | never recomputed | gate 2 wins, so the saved reason agrees |
| excluded body, link now ``Run`` | recomputed, ``restored`` | the rule says count |
| excluded body, row ``deleted_in_strava`` (unlinked) | recomputed, ``restored``, ``strava_type`` null | no live link |
| excluded body, link moved to another session | old session ``restored``; new one ``excluded`` | the link is read from the session's side |
| excluded body naming another row than the live non-run link | recomputed, no entry | still excluded; the body names the live row |
| a body that is not JSON | its ``strava_activity_id`` reads null, so an excluded one is recomputed | the recompute writes a valid body |
| a session with no saved load row | not selected (``test_a_running_session_with_no_saved_load_row_is_not_reconciled``) | only a saved load can disagree; the upload and the one-time fill save one |
| a run near midnight UTC, athlete in America/Vancouver | its change dated on the Vancouver day | ``local_date`` is the athlete's day, read in the job's zone |
| a Garmin archive import over a disagreeing body | nothing recomputed, ``load_changes`` empty | only a Strava import reconciles |
| the same archive again | nothing recomputed, ``session_loads`` byte-equal | every saved reason already agrees |
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.config import AppConfig
from runcoach_api.imports import job
from runcoach_api.main import app


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "support" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")
IA = _load("import_archive")

DRAIN_TIMEOUT_S = 120.0
REASON = "strava_not_running"
SKI_ID = 9_100_000_001
OTHER_ID = 9_100_000_002
UNRELATED_ID = 9_100_000_003


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


@pytest.fixture
def recomputes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every session id ``db.recompute_session_load`` is called for, in order."""
    calls: list[str] = []
    real = db.recompute_session_load

    def spy(conn, session_id, *args, **kwargs):
        calls.append(session_id)
        return real(conn, session_id, *args, **kwargs)

    monkeypatch.setattr(db, "recompute_session_load", spy)
    return calls


def _runs(n: int) -> list[tuple[bytes, object]]:
    """``n`` copies of ``sample_run``, each on its own day, with their starts."""
    sample = SA.fixture("sample_run")
    out = []
    for i in range(n):
        fit = sample if i == 0 else SA.shifted(sample, -i * SA.DAY_S)
        out.append((fit, SA.session_start(fit)))
    return out


def _upload(client: TestClient, post_fit_bytes, fit: bytes, name: str) -> str:
    response = post_fit_bytes(client, name, fit)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _row(activity_id: int, start, activity_type: str) -> dict[str, str]:
    return SA.row(**{
        "Activity ID": str(activity_id),
        "Activity Date": SA.strava_date(start),
        "Activity Name": f"row {activity_id}",
        "Activity Type": activity_type,
    })


def _unrelated(start) -> dict[str, str]:
    """A ``Run`` row on a day no session holds, with no file: ``no_file``, linking nothing."""
    return _row(UNRELATED_ID, start - timedelta(days=400), "Run")


def _import(client: TestClient, path: Path, rows) -> dict:
    SA.strava_zip(path, rows)
    with path.open("rb") as fh:
        started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
    assert started.status_code == 202, started.text
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    report = done.json()
    assert report["status"] == "completed", report["error"]
    strava = report["counts"]["strava"]
    print(f"  {path.name}: outcomes {[(r['strava_activity_id'], r['outcome'], r['session_id']) for r in report['files']]}")
    print(f"  {path.name}: excluded_from_load {strava['excluded_from_load']}"
          f" restored_to_load {strava['restored_to_load']} load_changes {report['load_changes']}")
    return report


def _body(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
    finally:
        conn.close()
    assert body is not None
    return body


def _loads(data_dir: Path) -> list[tuple]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return list(conn.execute(f"SELECT * FROM {db.SESSION_LOADS_TABLE} ORDER BY session_id"))
    finally:
        conn.close()


def _write_body(data_dir: Path, session_id: str, body: dict) -> None:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        with conn:
            conn.execute(
                f"UPDATE {db.SESSION_LOADS_TABLE} SET load_value = ?, load_reason = ?, body = ? WHERE session_id = ?",
                (body["session_load"]["value"], body["session_load"]["unavailable"], json.dumps(body), session_id),
            )
    finally:
        conn.close()


def _execute(data_dir: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        with conn:
            conn.execute(sql, params)
    finally:
        conn.close()


def _change(session_id: str, start, strava_type: str | None, old_load: float | None, change: str) -> dict:
    return {
        "session_id": session_id,
        "local_date": start.date().isoformat(),
        "strava_type": strava_type,
        "old_load": old_load,
        "change": change,
    }


def _counts(report: dict) -> tuple[int, int]:
    strava = report["counts"]["strava"]
    return strava["excluded_from_load"], strava["restored_to_load"]


# ---------------------------------------------------------------------------
# AC1 through the import, and AC4's counts
# ---------------------------------------------------------------------------


def test_a_strava_import_excludes_a_run_typed_alpine_ski_and_counts_it(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A counted run linked by the import to an ``Alpine Ski`` row is recomputed to
    ``strava_not_running`` naming the row, and the report lists it as excluded with its old load."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        before = _body(sid)
        old = before["session_load"]["value"]
        print(f"  before: session_load {before['session_load']}")
        assert old is not None and before["session_load"]["unavailable"] is None
        report = _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
        after = _body(sid)
        served = client.get(f"/sessions/{sid}/load").json()
    print(f"  after: session_load {after['session_load']} strava_activity_id {after['strava_activity_id']}")
    assert report["files"][0]["outcome"] == "attached" and report["files"][0]["session_id"] == sid
    assert after["session_load"]["value"] is None and after["session_load"]["unavailable"] == REASON
    assert after["strava_activity_id"] == SKI_ID
    assert served["session_load"]["unavailable"] == REASON and served["strava_activity_id"] == SKI_ID
    assert recomputes == [sid]
    assert _counts(report) == (1, 0)
    assert report["load_changes"] == [_change(sid, start, "Alpine Ski", old, "excluded")]


# ---------------------------------------------------------------------------
# AC3: undo, and self-healing
# ---------------------------------------------------------------------------


def test_a_second_archive_typing_it_run_restores_the_body_and_counts_it(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """AC3: a second archive typing the same activity ``Run`` restores the body saved before the
    first import, equal to it (nothing else changed on the test store), and lists it as restored."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        saved = _body(sid)
        first = _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
        assert _body(sid)["session_load"]["unavailable"] == REASON
        second = _import(client, tmp_path / "run.zip", [_row(SKI_ID, start, "Run")])
        restored = _body(sid)
    print(f"  saved before: {saved['session_load']}; restored: {restored['session_load']}")
    assert _counts(first) == (1, 0)
    assert second["files"][0]["outcome"] == "updated"
    assert restored == saved
    assert restored["strava_activity_id"] is None
    assert _counts(second) == (0, 1)
    assert second["load_changes"] == [_change(sid, start, "Run", None, "restored")]


def test_a_deleted_in_strava_row_restores_its_session(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A row absent from a newer archive is marked ``deleted_in_strava`` and unlinked, and the
    session it excluded is restored, its Strava type null (no longer linked)."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        saved = _body(sid)
        _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
        second = _import(client, tmp_path / "other.zip", [_unrelated(start)])
        restored = _body(sid)
    outcomes = {r["strava_activity_id"]: r["outcome"] for r in second["files"]}
    assert outcomes == {UNRELATED_ID: "no_file", SKI_ID: "deleted_in_strava"}
    assert restored == saved
    assert _counts(second) == (0, 1)
    assert second["load_changes"] == [_change(sid, start, None, None, "restored")]


def test_a_link_moved_to_another_session_restores_the_old_one(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A link written to another session in the store (an outside edit; imports keep live links)
    restores the session it left and excludes the one it reaches, on the next Strava import."""
    (fit_a, start_a), (fit_b, start_b) = _runs(2)
    with TestClient(app) as client:
        sid_a = _upload(client, post_fit_bytes, fit_a, "a.fit")
        sid_b = _upload(client, post_fit_bytes, fit_b, "b.fit")
        saved_a, saved_b = _body(sid_a), _body(sid_b)
        rows = [_row(SKI_ID, start_a, "Alpine Ski")]
        _import(client, tmp_path / "ski.zip", rows)
        assert _body(sid_a)["strava_activity_id"] == SKI_ID
        _execute(isolated_data_dir, "UPDATE strava_activities SET linked_session_id = ? WHERE activity_id = ?",
                 (sid_b, SKI_ID))
        recomputes.clear()
        second = _import(client, tmp_path / "again.zip", rows)
        after_a, after_b = _body(sid_a), _body(sid_b)
    print(f"  a: {after_a['session_load']}; b: {after_b['session_load']} names {after_b['strava_activity_id']}")
    assert second["files"][0]["outcome"] == "unchanged" and second["files"][0]["session_id"] == sid_b
    assert after_a == saved_a
    assert after_b["session_load"]["unavailable"] == REASON and after_b["strava_activity_id"] == SKI_ID
    assert sorted(recomputes) == sorted([sid_a, sid_b])
    assert _counts(second) == (1, 1)
    expected = [
        _change(sid_a, start_a, None, None, "restored"),
        _change(sid_b, start_b, "Alpine Ski", saved_b["session_load"]["value"], "excluded"),
    ]
    assert sorted(second["load_changes"], key=lambda c: c["session_id"]) == sorted(
        expected, key=lambda c: c["session_id"]
    )


def test_a_stale_body_is_recomputed_on_any_strava_import(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A job interrupted between a row's commit and the reconcile, simulated by writing the counted
    body back over the excluded one: the next Strava import, its row unchanged and an unrelated row
    added, recomputes the body and counts it as excluded."""
    [(fit, start)] = _runs(1)
    ski = _row(SKI_ID, start, "Alpine Ski")
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        saved = _body(sid)
        _import(client, tmp_path / "ski.zip", [ski])
        _write_body(isolated_data_dir, sid, saved)
        assert _body(sid)["session_load"]["unavailable"] is None
        recomputes.clear()
        second = _import(client, tmp_path / "more.zip", [ski, _unrelated(start)])
        after = _body(sid)
    outcomes = {r["strava_activity_id"]: r["outcome"] for r in second["files"]}
    assert outcomes == {SKI_ID: "unchanged", UNRELATED_ID: "no_file"}
    assert after["session_load"]["unavailable"] == REASON and after["strava_activity_id"] == SKI_ID
    assert recomputes == [sid]
    assert _counts(second) == (1, 0)
    assert second["load_changes"] == [_change(sid, start, "Alpine Ski", saved["session_load"]["value"], "excluded")]


# ---------------------------------------------------------------------------
# AC4: a re-import changes nothing; what the reconcile must leave alone
# ---------------------------------------------------------------------------


def test_a_reimport_excludes_and_restores_nothing_and_writes_no_load_row(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """The same archive imported twice: the second recomputes nothing, counts nothing and leaves
    ``session_loads`` byte-equal."""
    (fit_a, start_a), (fit_b, start_b) = _runs(2)
    rows = [_row(SKI_ID, start_a, "Alpine Ski"), _row(OTHER_ID, start_b, "Run")]
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, fit_a, "a.fit")
        _upload(client, post_fit_bytes, fit_b, "b.fit")
        first = _import(client, tmp_path / "a.zip", rows)
        before = _loads(isolated_data_dir)
        recomputes.clear()
        second = _import(client, tmp_path / "b.zip", rows)
    after = _loads(isolated_data_dir)
    print(f"  session_loads rows compared: {len(before)}")
    assert _counts(first) == (1, 0)
    assert len(before) == 2
    assert after == before
    assert recomputes == []
    assert _counts(second) == (0, 0)
    assert second["load_changes"] == []


def test_a_declared_capture_linked_to_a_non_run_is_never_recomputed(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A running session tagged as a declared capture and linked to a ``Hike`` row keeps
    ``declared_capture`` (gate 2 wins), so neither import recomputes or excludes it."""
    [(fit, start)] = _runs(1)
    rows = [_row(SKI_ID, start, "Hike")]
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        _execute(isolated_data_dir, "UPDATE sessions SET activity_tag = 'health_snapshot' WHERE session_id = ?", (sid,))
        conn = db.get_connection()
        try:
            db.recompute_session_load(conn, sid)
        finally:
            conn.close()
        assert _body(sid)["session_load"]["unavailable"] == "declared_capture"
        recomputes.clear()
        first = _import(client, tmp_path / "a.zip", rows)
        assert recomputes == []
        second = _import(client, tmp_path / "b.zip", rows)
        after = _body(sid)
    assert first["files"][0]["session_id"] == sid
    assert recomputes == []
    assert _counts(first) == _counts(second) == (0, 0)
    assert after["session_load"]["unavailable"] == "declared_capture" and after["strava_activity_id"] is None


def test_a_counted_linked_run_is_not_rewritten(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A counted run linked to a ``Run`` row whose saved body names a superseded anchor version
    (as 996 of the real store's bodies do) agrees with the rule, so it is not rewritten: whole
    bodies are never compared."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        body = _body(sid)
        body["inputs"]["max_hr_bpm"]["anchor_version"] = 999_999
        _write_body(isolated_data_dir, sid, body)
        before = _loads(isolated_data_dir)
        report = _import(client, tmp_path / "run.zip", [_row(SKI_ID, start, "Run")])
    assert report["files"][0]["session_id"] == sid
    assert _loads(isolated_data_dir) == before
    assert recomputes == []
    assert _counts(report) == (0, 0) and report["load_changes"] == []


def test_an_excluded_body_naming_another_row_is_recomputed_to_name_the_live_one(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """An excluded body whose ``strava_activity_id`` is not the live link's (written here directly)
    is recomputed to name the live row; it stays excluded, so the import counts no change."""
    [(fit, start)] = _runs(1)
    rows = [_row(SKI_ID, start, "Alpine Ski")]
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        _import(client, tmp_path / "a.zip", rows)
        body = _body(sid)
        body["strava_activity_id"] = OTHER_ID
        _write_body(isolated_data_dir, sid, body)
        recomputes.clear()
        second = _import(client, tmp_path / "b.zip", rows)
        after = _body(sid)
    assert recomputes == [sid]
    assert after["session_load"]["unavailable"] == REASON and after["strava_activity_id"] == SKI_ID
    assert _counts(second) == (0, 0) and second["load_changes"] == []


def test_a_running_session_with_no_saved_load_row_is_not_reconciled(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A running session linked to an ``Alpine Ski`` row whose ``session_loads`` row was deleted
    first is not selected: only a saved load can disagree, so the import writes no load row for it
    and lists no change."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        _execute(isolated_data_dir, f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (sid,))
        assert _loads(isolated_data_dir) == []
        report = _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
    rows = _loads(isolated_data_dir)
    print(f"  session_loads rows after: {len(rows)}")
    assert report["files"][0]["outcome"] == "attached" and report["files"][0]["session_id"] == sid
    assert rows == []
    assert recomputes == []
    assert _counts(report) == (0, 0) and report["load_changes"] == []


def test_a_change_is_dated_in_the_athletes_zone_not_utc(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the athlete in America/Vancouver, a run starting 2024-06-02 03:30 UTC (20:30 on
    2024-06-01 there) is listed as excluded on its local date, 2024-06-01, not its UTC date."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone="America/Vancouver",
    )
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: config)
    db._load_config_cached.cache_clear()
    assert db._load_config_cached().athlete_timezone == "America/Vancouver"
    sample = SA.fixture("sample_run")
    target = datetime(2024, 6, 2, 3, 30, tzinfo=UTC)
    fit = SA.shifted(sample, int((target - SA.session_start(sample)).total_seconds()))
    start = SA.session_start(fit)
    assert start == target
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        report = _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
    print(f"  start {start.isoformat()}; load_changes {report['load_changes']}")
    assert [(c["session_id"], c["change"]) for c in report["load_changes"]] == [(sid, "excluded")]
    assert report["load_changes"][0]["local_date"] == "2024-06-01"


def test_a_garmin_archive_import_never_runs_the_reconcile(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, recomputes: list[str]
) -> None:
    """A linked run whose saved load disagrees with the rule (the counted body written back over
    the excluded one) is left as it is by a Garmin archive import, which lists no load change; only
    a Strava import reconciles."""
    [(fit, start)] = _runs(1)
    with TestClient(app) as client:
        sid = _upload(client, post_fit_bytes, fit, "run.fit")
        saved = _body(sid)
        _import(client, tmp_path / "ski.zip", [_row(SKI_ID, start, "Alpine Ski")])
        _write_body(isolated_data_dir, sid, saved)
        recomputes.clear()
        path = IA.build_archive(tmp_path)
        with path.open("rb") as fh:
            started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
        assert started.status_code == 202, started.text
        assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"
        report = client.get(f"/imports/{started.json()['import_id']}").json()
        after = _body(sid)
    print(f"  garmin: status {report['status']} source {report['source']} load_changes {report['load_changes']}")
    print(f"  after: session_load {after['session_load']}")
    assert report["source"] == "garmin" and report["status"] == "completed", report["error"]
    assert after == saved
    assert sid not in recomputes
    assert report["load_changes"] == []
    assert _counts(report) == (0, 0)
