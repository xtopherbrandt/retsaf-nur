"""What an archive import's night costs, counted, and the zone its saved load is dated in.

The measurement this pins was taken on copies of the athlete's store: a night cost about 0.11 s
with no stored nights and 0.14-0.21 s with 1,261, and the growth sat in four places, each a
read over every stored session: the HR-anchor sync (``init_schema``'s, run once per file, and
``persist_night``'s, each reading every session's profile columns), the three run-date
lookups of a night's saved load (a scan and sort of the sessions per HR field, since a night
carries no HR field), and the session insert's next ``upload_order`` (``MAX`` over every
session).

``test_a_night_import_meets_the_bound`` asserts what does not depend on the machine: per
imported night, the SQL statements run, the statements whose plan scans the sessions table, the
``init_schema`` calls and the SQLite VM steps, in a synthetic store with 0 stored nights and
with 1,261, the same 30 runs carrying HR fields in both, importing the same 50 nights through
the job's per-night path. Counts are equal in both stores and no statement scans the sessions;
the VM steps per night stay within 1.25x. Wall time per night is printed (``per item``) as
evidence, never asserted: the test runs in the full suite and on WSL, where timing flakes.

The stored nights are written with minimal rows (the session row and a saved load, no series),
which is what the per-night reads touch.

A night's anchor sync reads the profile narrowly (``db._resolve_profile_latest``, the latest
carrying session per field). Four stores written through ``persist`` and ``persist_night`` pin it
equal to ``profile.resolve`` over every session and entry, with the values each must give: a
start-time tie between two watches, a later ride carrying max and threshold HR, fields split
across runs, and entered values written through ``write_profile_entries`` beside a run.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api import db, profile
from runcoach_api.config import AppConfig
from runcoach_api.imports import job
from runcoach_api.ingestion import overnight
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.models import Session

SUPPORT = Path(__file__).parent / "support"
NIGHTS = Path(__file__).parent / "fixtures" / "nights"

# The slow case of the measurement: stored nights in the store copy it imported into.
STORED_NIGHTS_SLOW = 1261
NIGHTS_IMPORTED = 50
RUNS_WITH_HR = 30
# The measurement's bound, applied here to the deterministic VM-step count.
BOUND = 1.25
# The statements one imported night runs after the job's first: its 57 series-value inserts, its 5
# sidecar inserts and 28 others (the transaction, the duplicate and re-summary checks, the insert,
# the profile and anchor reads, the load's reads and write, the report row and the progress).
STATEMENTS_PER_NIGHT = 90
UTC_ZONE = ZoneInfo("UTC")
DRAIN_TIMEOUT_S = 60.0
# SQLite VM instructions between two progress-handler calls.
VM_STEP_GRAIN = 100


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


IA = _load_support("import_archive")


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _template() -> overnight.Night:
    mapped = overnight.map_night((NIGHTS / "night_recent.fit").read_bytes())
    assert isinstance(mapped, overnight.Night)
    return mapped


def _shifted(night: overnight.Night, days: int) -> overnight.Night:
    """The same night ``days`` later: ``start_time``, ``series_start`` and the id move with it."""
    session = night.session
    start = (datetime.fromisoformat(session.start_time) + timedelta(days=days)).isoformat()
    series_start = (
        datetime.fromisoformat(session.summary["series_start"]) + timedelta(days=days)
    ).isoformat()
    moved = dataclasses.replace(
        session,
        start_time=start,
        session_id=derive_session_id(session.source_device, start),
        summary={**session.summary, "series_start": series_start},
    )
    return dataclasses.replace(night, session=moved)


def _run_with_hr(night: overnight.Night, index: int):
    """A synthetic stored run carrying every HR field, dated before every night."""
    start = (datetime(2019, 1, 1, 7, tzinfo=UTC_ZONE) + timedelta(days=index)).isoformat()
    return dataclasses.replace(
        night.session,
        session_id=derive_session_id("synthetic watch", start),
        sport="running",
        activity_tag=None,
        source_device="synthetic watch",
        start_time=start,
        summary={"duration_s": 1800.0},
        resting_hr_bpm=48 + index % 3,
        max_hr_bpm=190,
        threshold_hr_bpm=170,
        sex="male",
        body_mass_kg=70.0,
        height_cm=180.0,
    )


def _seed(data_dir: Path, stored_nights: int) -> None:
    """``RUNS_WITH_HR`` runs and ``stored_nights`` nights (before the imported ones), each with
    a saved load, so the job's own ``init_schema`` has nothing to fill."""
    template = _template()
    conn = db.get_connection(data_dir)
    try:
        db.init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        runs = [_run_with_hr(template, i) for i in range(RUNS_WITH_HR)]
        for run in runs:
            db._insert_session(conn, run)
        nights = [_shifted(template, -(i + 1)).session for i in range(stored_nights)]
        for night in nights:
            db._insert_session(conn, night)
        resolved = db._sync_anchor_versions(conn)
        for run in runs:
            db._save_session_load(conn, run.session_id, resolved=resolved, zone=UTC_ZONE)
        if nights:
            body = db._session_load_body(conn, nights[0].session_id, resolved=resolved, zone=UTC_ZONE)
            for night in nights:
                db._write_session_load(conn, night.session_id, body)
        conn.commit()
    finally:
        conn.close()


def _imported() -> dict[bytes, overnight.Night]:
    """The 50 nights the job imports, after every stored one, keyed by the file bytes the job reads."""
    template = _template()
    return {f"night {i}".encode(): _shifted(template, i + 1) for i in range(NIGHTS_IMPORTED)}


def _create_job(data_dir: Path) -> str:
    conn = db.get_connection(data_dir)
    try:
        return job.create_job(conn, "export.zip", 0)
    finally:
        conn.close()


def _latest_index_walk(conn: sqlite3.Connection, detail: str) -> bool:
    """Whether a plan row walks a ``sessions_latest_<field>`` index keyed ``(start_time, upload_order)``."""
    named = re.search(r"USING (?:COVERING )?INDEX (sessions_latest_\w+)", detail)
    if named is None:
        return False
    columns = [r[2] for r in conn.execute(f"PRAGMA index_info({named.group(1)})").fetchall()]
    return columns == ["start_time", "upload_order"]


def _sessions_scans(statements: list[str], data_dir: Path) -> list[str]:
    """The statements whose ``EXPLAIN QUERY PLAN`` scans the sessions table (by name or alias).

    A walk of a ``sessions_latest_<field>`` index is not counted when that index's columns are
    ``(start_time, upload_order)``: the partial index holds only the sessions carrying the field
    (runs, never a night), and the read stops at its first row (``LIMIT 1`` in index order), so it
    does not grow with the nights stored. A walk of one keyed otherwise is counted, since the
    read then sorts or filters what it walks."""
    scanning = []
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        for sql in statements:
            if not re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", sql, re.IGNORECASE):
                continue
            aliases = {"sessions"} | set(re.findall(r"\bsessions\s+(?:AS\s+)?(\w+)", sql, re.IGNORECASE))
            plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
            for row in plan:
                scanned = re.match(r"SCAN (\w+)", row[3])
                if scanned and scanned.group(1) in aliases and not _latest_index_walk(conn, row[3]):
                    scanning.append(sql)
                    break
    finally:
        conn.close()
    return scanning


def _import_counted(data_dir: Path, stored_nights: int, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Seed, then import the 50 nights through ``_Job.ingest_night``, counting per night.

    ``init_schema`` is counted over the whole job; statements, scans and VM steps over the nights
    after the first, whose ingest is the one that runs the job's ``init_schema``."""
    _seed(data_dir, stored_nights)
    import_id = _create_job(data_dir)
    nights = _imported()
    statements: list[str] = []
    steps = [0]
    init_calls = [0]
    real_connect = db.get_connection
    real_init = db.init_schema

    def counted_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        conn.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, VM_STEP_GRAIN)
        return conn

    def counted_init(conn):
        init_calls[0] += 1
        real_init(conn)

    with monkeypatch.context() as patch:
        patch.setattr(db, "get_connection", counted_connect)
        patch.setattr(db, "init_schema", counted_init)
        patch.setattr(overnight, "map_night", lambda raw: nights[raw])
        work = job._Job(import_id, data_dir, [], UTC_ZONE)
        for seq, raw in enumerate(nights, start=1):
            if seq == 2:
                statements.clear()
                steps[0] = 0
                started = time.perf_counter()
            work.ingest_night(seq, f"night_{seq}.fit", None, raw)
        wall = time.perf_counter() - started
    measured = NIGHTS_IMPORTED - 1

    assert work.counts["stored"] == NIGHTS_IMPORTED, work.counts
    conn = real_connect(data_dir)
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE activity_tag = 'overnight_hrv'"
        ).fetchone()[0]
        loads = conn.execute("SELECT COUNT(*) FROM session_loads").fetchone()[0]
    finally:
        conn.close()
    assert stored == stored_nights + NIGHTS_IMPORTED
    assert loads == RUNS_WITH_HR + stored_nights + NIGHTS_IMPORTED
    per_item_ms = wall * 1000 / measured
    result = {
        "statements": len(statements) / measured,
        "sessions_scans": len(_sessions_scans(statements, data_dir)) / measured,
        "init_schema": init_calls[0],
        "vm_steps": steps[0] * VM_STEP_GRAIN / measured,
    }
    print(
        f"per item {per_item_ms:.1f} ms at {stored_nights} stored nights;"
        f" statements {result['statements']:.2f}, sessions scans {result['sessions_scans']:.2f},"
        f" init_schema {result['init_schema']} per job, vm steps {result['vm_steps']:.0f} per night"
    )
    conn = real_connect(data_dir)
    try:
        narrow = db._resolve_profile_latest(conn)
        full = profile.resolve(*db.read_profile_inputs(conn))
    finally:
        conn.close()
    assert narrow == full, "the narrow profile read resolves differently from the full one"
    return result


def test_a_night_import_meets_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = _import_counted(tmp_path / "zero", 0, monkeypatch)
    full = _import_counted(tmp_path / "full", STORED_NIGHTS_SLOW, monkeypatch)

    assert empty["init_schema"] == full["init_schema"] == 1
    assert empty["sessions_scans"] == full["sessions_scans"] == 0
    assert empty["statements"] == full["statements"] == STATEMENTS_PER_NIGHT
    ratio = full["vm_steps"] / empty["vm_steps"]
    print(f"per item vm steps ratio {ratio:.3f} ({full['vm_steps']:.0f} / {empty['vm_steps']:.0f})")
    assert ratio <= BOUND


# ---------------------------------------------------------------------------
# the narrow profile read against the full one
# ---------------------------------------------------------------------------


def _profile_session(
    session_id: str, start: str, *, sport: str = "running", device: str = "watch a", **fields
):
    return Session(
        session_id=session_id,
        sport=sport,
        source_vendor="garmin",
        start_time=start,
        source_device=device,
        **fields,
    )


def _narrow_after_writes(
    data_dir: Path, sessions: list[Session], entries: list[dict[str, object]] | None = None
) -> dict[str, profile.FieldValue]:
    """Store ``sessions`` through ``persist`` in the order given, each mapping of ``entries`` through
    ``write_profile_entries`` (as ``PATCH /me`` does), then a night through ``persist_night`` (dated
    after all of them, carrying no profile field), and return the narrow read, asserted equal to
    ``profile.resolve`` over every stored session and entry."""
    conn = db.get_connection(data_dir)
    try:
        db.init_schema(conn)
        for session in sessions:
            db.persist(conn, session, [], [], {}, zone=UTC_ZONE)
        for n, values in enumerate(entries or []):
            db.write_profile_entries(conn, values, set_at=f"2025-06-0{n + 1}T08:00:00+00:00")
        db.persist_night(conn, _template(), zone=UTC_ZONE)
        narrow = db._resolve_profile_latest(conn)
        full = profile.resolve(*db.read_profile_inputs(conn))
    finally:
        conn.close()
    assert narrow == full, "the narrow profile read resolves differently from the full one"
    return narrow


def _picked(resolved: dict[str, profile.FieldValue], field: str) -> tuple[object, str | None]:
    return resolved[field].value, resolved[field].session_id


def test_the_later_upload_wins_a_start_time_tie(tmp_path: Path) -> None:
    """Two runs from two watches at the same start time: the one stored second wins every field
    both carry, whichever value is larger."""
    start = "2025-03-01T07:00:00+00:00"
    resolved = _narrow_after_writes(
        tmp_path,
        [
            _profile_session("tie-first", start, device="watch a", max_hr_bpm=191, resting_hr_bpm=47),
            _profile_session("tie-second", start, device="watch b", max_hr_bpm=186, resting_hr_bpm=49),
        ],
    )
    assert _picked(resolved, "max_hr_bpm") == (186, "tie-second")
    assert _picked(resolved, "resting_hr_bpm") == (49, "tie-second")


def test_a_later_non_running_session_sets_no_running_only_field(tmp_path: Path) -> None:
    """A ride after the run carries max and threshold HR and a resting HR: max and threshold come
    from the run, which is stored as running; resting HR, which any sport may carry, from the ride."""
    resolved = _narrow_after_writes(
        tmp_path,
        [
            _profile_session(
                "run", "2025-01-01T07:00:00+00:00", max_hr_bpm=188, threshold_hr_bpm=168, resting_hr_bpm=46
            ),
            _profile_session(
                "ride",
                "2025-02-01T07:00:00+00:00",
                sport="cycling",
                max_hr_bpm=199,
                threshold_hr_bpm=181,
                resting_hr_bpm=41,
            ),
        ],
    )
    assert _picked(resolved, "max_hr_bpm") == (188, "run")
    assert _picked(resolved, "threshold_hr_bpm") == (168, "run")
    assert _picked(resolved, "resting_hr_bpm") == (41, "ride")


def test_each_field_comes_from_its_own_latest_carrying_session(tmp_path: Path) -> None:
    """Resting HR and the body fields from the first run, max HR from the second, threshold HR from
    the third: the profile joins all three, and the later runs' missing fields do not clear the
    earlier ones."""
    resolved = _narrow_after_writes(
        tmp_path,
        [
            _profile_session(
                "body",
                "2025-01-01T07:00:00+00:00",
                resting_hr_bpm=45,
                sex="female",
                body_mass_kg=58.4,
                height_cm=166.0,
            ),
            _profile_session("max", "2025-02-01T07:00:00+00:00", max_hr_bpm=190),
            _profile_session("threshold", "2025-03-01T07:00:00+00:00", threshold_hr_bpm=170),
        ],
    )
    assert _picked(resolved, "resting_hr_bpm") == (45, "body")
    assert _picked(resolved, "sex") == ("female", "body")
    assert _picked(resolved, "body_mass_kg") == (58.4, "body")
    assert _picked(resolved, "height_cm") == (166.0, "body")
    assert _picked(resolved, "max_hr_bpm") == (190, "max")
    assert _picked(resolved, "threshold_hr_bpm") == (170, "threshold")


def test_entered_values_resolve_in_the_narrow_read_as_in_the_full_one(tmp_path: Path) -> None:
    """Entered values beside a run that carries max HR: the entered max HR is shadowed by the
    file's and still reported as ``entered_value``; the entered threshold HR, which no session
    carries, is the value; the birth date, which no file can carry, comes only from its entry; and
    a later clear of the entered resting HR leaves the file's. A narrow read that dropped the
    entries would lose all three entered figures."""
    resolved = _narrow_after_writes(
        tmp_path,
        [_profile_session("run", "2025-01-01T07:00:00+00:00", max_hr_bpm=190, resting_hr_bpm=46)],
        [
            {"max_hr_bpm": 194, "threshold_hr_bpm": 171, "birth_date": "1985-04-02", "resting_hr_bpm": 44},
            {"resting_hr_bpm": None},
        ],
    )
    print(f"  resolved {resolved}")
    assert _picked(resolved, "max_hr_bpm") == (190, "run")
    assert resolved["max_hr_bpm"].entered_value == 194
    assert (resolved["threshold_hr_bpm"].value, resolved["threshold_hr_bpm"].source) == (171, "entered")
    assert (resolved["birth_date"].value, resolved["birth_date"].source) == ("1985-04-02", "entered")
    assert _picked(resolved, "resting_hr_bpm") == (46, "run")
    assert resolved["resting_hr_bpm"].entered_value is None


# ---------------------------------------------------------------------------
# the zone the job captured
# ---------------------------------------------------------------------------


def _config(monkeypatch: pytest.MonkeyPatch, data_dir: Path, zone: str) -> None:
    zoned = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=zone,
    )
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: zoned)
    db._load_config_cached.cache_clear()


def test_a_night_is_dated_in_the_zone_the_job_captured(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The night starts 2026-10-10T11:33:29Z: 2026-10-10 in UTC, the zone the job captured, and
    2026-10-09 in ``Etc/GMT+12``, the zone the config names once the job has started. A resting
    HR entry effective from 2026-10-10 is in effect on the night's date only in the captured zone,
    so the saved load borrows it (``history``) only when the night is dated there."""
    night = _template()
    start = night.session.start_time
    later_zone = ZoneInfo("Etc/GMT+12")
    captured_day = datetime.fromisoformat(start).astimezone(UTC_ZONE).date().isoformat()
    assert datetime.fromisoformat(start).astimezone(later_zone).date().isoformat() < captured_day

    conn = db.get_connection(isolated_data_dir)
    try:
        db.init_schema(conn)
        entry, _ = db.write_hr_history_entry(conn, "resting_hr_bpm", 44, captured_day)
    finally:
        conn.close()
    archive = tmp_path / "export.zip"
    inner = IA._zip_bytes([("night_a.fit", (NIGHTS / "night_recent.fit").read_bytes())])
    archive.write_bytes(IA._zip_bytes([("DI_CONNECT/Wellness-Folder/Part1.zip", inner)]))
    import_id = _create_job(isolated_data_dir)
    target = job.archive_path(isolated_data_dir, import_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, target)

    _config(monkeypatch, isolated_data_dir, "Etc/GMT+12")
    job.run_job(import_id, isolated_data_dir, [], UTC_ZONE)

    conn = db.get_connection(isolated_data_dir)
    try:
        report = job.read_import(conn, import_id)
        row = conn.execute(
            "SELECT body FROM session_loads WHERE session_id = ?", (night.session.session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert report is not None and report["counts"]["stored"] == 1, report
    assert row is not None
    resting = json.loads(row["body"])["inputs"]["resting_hr_bpm"]
    assert resting["source"] == "history", resting
    assert resting["entry_id"] == entry["entry_id"]
    assert resting["value"] == 44
