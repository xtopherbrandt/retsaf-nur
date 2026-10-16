"""F025 AC1 (the POST path) and AC2 (the upload-path forms): laps, workout steps and ``laps_read_at`` stored at ingest.

``pipeline.ingest_fit_bytes`` maps a file's ``lap`` and ``workout_step`` messages with
``mapping.to_laps`` and ``db.persist`` stores them through ``db.replace_session_laps`` in the
session's own transaction (F025 reference sections 2 and 3). Everything here goes through the real
``POST /sessions`` under ``TestClient``, against the per-test store ``conftest.isolated_data_dir``
points ``RUNCOACH_DATA_DIR`` at.

**The oracle.** Expected rows come from ``tests/support/lap_oracle.py``, which reads the bytes with
``fitdecode`` alone and imports nothing from ``runcoach_api``; its ``start_t``/``end_t`` are
whole-second differences of the raw ``uint32`` timestamps. Counts are the oracle's, read
independently from each file. Fixtures cited as runs have run proof ``yes`` in
``tests/fixtures/README.md``: ``sample_run``, ``hilly_run_8k_fr945`` and ``strap_run_hrv``.

**The clock seam.** ``db.laps_read_now`` is the one clock read for ``laps_read_at``; it is fixed
here so the stored value is asserted exactly.

**Upload-path forms** (reference section 2), each a patched ``sample_run.fit`` uploaded for real:

| form | patch | stored |
|---|---|---|
| lap with no ``start_time`` | lap 3 field 2 = 0xFFFFFFFF | that lap kept, ``start_t`` null |
| invalid ``total_timer_time`` | lap 4 field 8 = 0xFFFFFFFF | ``timer_s`` null |
| unnamed intensity | lap 2 field 23 = 99 | ``intensity`` null |
| step with no target | step 1 field 3 = 255 | ``target_type`` null, low/high as fitdecode returns them |
| no lap and no step message | both message types removed | zero rows, ``laps_read_at`` set |
| the repeat workout | ``repeat_workout.build_repeat_workout()`` | five steps, the repeat step as written |

Record and RR row counts of every form equal the unpatched file's (``strap_run_hrv.fit`` carries
the RR stream, so its forms make that count non-trivial).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

LAP, WORKOUT_STEP = 19, 27
LAP_START_TIME, LAP_TIMER, LAP_INTENSITY = 2, 8, 23
STEP_TARGET_TYPE = 3
UINT32_INVALID = 0xFFFFFFFF
ENUM_INVALID = 0xFF

FIXED_READ_AT = "2026-10-16T12:34:56+00:00"

STRUCTURED = ("sample_run", "hilly_run_8k_fr945", "strap_run_hrv")

# The real clock seam, taken before the autouse fixture below replaces it on ``db``.
_REAL_LAPS_READ_NOW = getattr(db, "laps_read_now", None)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
lap_oracle = _load_support("lap_oracle")
repeat_workout = _load_support("repeat_workout")


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "laps_read_now", lambda: FIXED_READ_AT)


def _bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _named(value):
    """The oracle's enum value as stored: a name, or None for an int fitdecode cannot name."""
    return value if isinstance(value, str) else None


LAP_COLUMNS = (
    "lap_index",
    "start_t",
    "end_t",
    "timer_s",
    "elapsed_s",
    "distance_m",
    "intensity",
    "wkt_step_index",
    "lap_trigger",
)
STEP_COLUMNS = (
    "step_index",
    "workout_name",
    "step_name",
    "intensity",
    "duration_type",
    "duration_value",
    "target_type",
    "target_value",
    "target_low",
    "target_high",
)


def _stored(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        laps = [
            dict(r)
            for r in conn.execute(
                f"SELECT {', '.join(LAP_COLUMNS)} FROM laps WHERE session_id = ? ORDER BY lap_index",
                (session_id,),
            )
        ]
        steps = [
            dict(r)
            for r in conn.execute(
                f"SELECT {', '.join(STEP_COLUMNS)} FROM workout_steps WHERE session_id = ? ORDER BY seq",
                (session_id,),
            )
        ]
        read_at = conn.execute("SELECT laps_read_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
        records = conn.execute("SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)).fetchone()[0]
        rr = conn.execute("SELECT COUNT(*) FROM rr_intervals WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()
    return {"laps": laps, "steps": steps, "laps_read_at": read_at, "records": records, "rr": rr}


def _upload(client: TestClient, raw: bytes, filename: str = "upload.fit") -> str:
    response = client.post("/sessions", files={"file": (filename, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _upload_and_read(client: TestClient, raw: bytes) -> dict:
    """Upload ``raw``, read its stored rows, then delete it so the next form can reuse the slot."""
    session_id = _upload(client, raw)
    stored = _stored(session_id)
    assert client.delete(f"/sessions/{session_id}").status_code == 204
    return stored


def _expected(raw: bytes) -> tuple[list[dict], list[dict]]:
    """The rows the oracle reads from ``raw``, in the stored column shape."""
    oracle = lap_oracle.read_laps_and_steps(raw)
    laps = [
        {
            "lap_index": index,
            "start_t": lap.start_t,
            "end_t": lap.end_t,
            "timer_s": lap.timer_s,
            "elapsed_s": lap.elapsed_s,
            "distance_m": lap.distance_m,
            "intensity": _named(lap.intensity),
            "wkt_step_index": lap.wkt_step_index,
            "lap_trigger": _named(lap.lap_trigger),
        }
        for index, lap in enumerate(oracle.laps)
    ]
    steps = [
        {
            "step_index": step.step_index,
            "workout_name": oracle.workout_name,
            "step_name": step.step_name,
            "intensity": _named(step.intensity),
            "duration_type": _named(step.duration_type),
            "duration_value": step.duration_value,
            "target_type": _named(step.target_type),
            "target_value": step.target_value,
            "target_low": step.target_low,
            "target_high": step.target_high,
        }
        for step in oracle.steps
    ]
    return laps, steps


def _assert_matches_oracle(stored: dict, raw: bytes) -> None:
    laps, steps = _expected(raw)
    assert len(stored["laps"]) == len(laps)
    assert len(stored["steps"]) == len(steps)
    assert stored["laps"] == laps
    assert stored["steps"] == steps


# --- AC1 -------------------------------------------------------------------------------------


def test_upload_stores_laps_steps_and_laps_read_at():
    raw = _bytes("sample_run")
    with TestClient(app) as client:
        session_id = _upload(client, raw, "sample_run.fit")
    stored = _stored(session_id)

    assert len(stored["laps"]) == 10
    assert len(stored["steps"]) == 4
    _assert_matches_oracle(stored, raw)
    assert stored["laps_read_at"] == FIXED_READ_AT
    # Reference section 1's table, literally.
    assert [s["intensity"] for s in stored["steps"]] == ["warmup", "active", "active", "cooldown"]
    assert [s["step_index"] for s in stored["steps"]] == [0, 1, 2, 3]
    assert [s["duration_value"] for s in stored["steps"]] == [300.0, 1200.0, 900.0, 300.0]
    assert [(s["target_low"], s["target_high"]) for s in stored["steps"]] == [
        (None, None),
        (2.901, 3.253),
        (2.901, 3.253),
        (None, None),
    ]
    assert [lap["wkt_step_index"] for lap in stored["laps"]] == [0, 1, 1, 1, 1, 2, 2, 2, 3, None]
    assert stored["laps"][0]["start_t"] == 0
    assert stored["laps"][-1]["lap_trigger"] == "session_end"
    # AC2: every lap's end_t is its timestamp minus the session start (the oracle's end_t).
    assert all(lap["end_t"] is not None for lap in stored["laps"])


@pytest.mark.parametrize("name", STRUCTURED)
def test_upload_matches_the_oracle_for_all_three_structured_fixtures(name):
    raw = _bytes(name)
    with TestClient(app) as client:
        session_id = _upload(client, raw, f"{name}.fit")
    stored = _stored(session_id)
    oracle = lap_oracle.read_laps_and_steps(raw)

    assert len(oracle.laps) > 0 and len(oracle.steps) > 0, name
    print(name, "laps", len(stored["laps"]), "steps", len(stored["steps"]))
    _assert_matches_oracle(stored, raw)
    assert stored["laps"][0]["start_t"] == 0
    assert stored["laps_read_at"] == FIXED_READ_AT


def test_a_file_without_laps_stores_none_and_sets_laps_read_at():
    raw = fit_patch.remove_messages(fit_patch.remove_messages(_bytes("sample_run"), LAP), WORKOUT_STEP)
    oracle = lap_oracle.read_laps_and_steps(raw)
    assert oracle.laps == [] and oracle.steps == []

    with TestClient(app) as client:
        session_id = _upload(client, raw)
    stored = _stored(session_id)

    assert stored["laps"] == []
    assert stored["steps"] == []
    assert stored["laps_read_at"] == FIXED_READ_AT


# --- AC2: the upload-path forms --------------------------------------------------------------


def _no_start_time(data: bytes) -> bytes:
    return fit_patch.set_field_at(data, LAP, 3, LAP_START_TIME, UINT32_INVALID)


def _invalid_timer(data: bytes) -> bytes:
    return fit_patch.set_field_at(data, LAP, 4, LAP_TIMER, UINT32_INVALID)


def _unnamed_intensity(data: bytes) -> bytes:
    return fit_patch.set_field_at(data, LAP, 2, LAP_INTENSITY, 99)


def _no_target(data: bytes) -> bytes:
    return fit_patch.set_field_at(data, WORKOUT_STEP, 1, STEP_TARGET_TYPE, ENUM_INVALID)


def _no_laps_or_steps(data: bytes) -> bytes:
    return fit_patch.remove_messages(fit_patch.remove_messages(data, LAP), WORKOUT_STEP)


def _repeat(data: bytes) -> bytes:
    return repeat_workout.build_repeat_workout(data)


FORMS = {
    "no_start_time": _no_start_time,
    "invalid_timer": _invalid_timer,
    "unnamed_intensity": _unnamed_intensity,
    "no_target": _no_target,
    "no_laps_or_steps": _no_laps_or_steps,
    "repeat_workout": _repeat,
}


@pytest.mark.parametrize("form", sorted(FORMS))
def test_patched_lap_fields_store_as_section_2_says(form):
    raw = FORMS[form](_bytes("sample_run"))
    with TestClient(app) as client:
        stored = _upload_and_read(client, raw)

    _assert_matches_oracle(stored, raw)
    assert stored["laps_read_at"] == FIXED_READ_AT
    laps, steps = stored["laps"], stored["steps"]
    if form == "no_start_time":
        assert len(laps) == 10
        assert laps[3]["start_t"] is None
        assert laps[3]["end_t"] is not None
        assert all(lap["start_t"] is not None for i, lap in enumerate(laps) if i != 3)
    elif form == "invalid_timer":
        assert laps[4]["timer_s"] is None
        assert laps[4]["elapsed_s"] is not None
    elif form == "unnamed_intensity":
        assert laps[2]["intensity"] is None
        assert laps[1]["intensity"] == "active"
    elif form == "no_target":
        assert steps[1]["target_type"] is None
        assert steps[2]["target_type"] == "speed"
    elif form == "no_laps_or_steps":
        assert laps == [] and steps == []
    elif form == "repeat_workout":
        assert len(steps) == 5
        assert [s["step_index"] for s in steps] == [0, 1, 2, 3, 4]
        assert [s["intensity"] for s in steps] == ["warmup", "active", "recovery", "cooldown", "cooldown"]
        repeat = steps[3]
        assert repeat["duration_type"] == "repeat_until_steps_cmplt"
        assert repeat["duration_value"] == 1
        assert repeat["target_value"] == 3
        assert steps[1]["duration_value"] == 600.0
        assert steps[2]["duration_value"] == 120.0
        assert steps[2]["target_type"] == "open"
    else:  # pragma: no cover - every form has a row above
        raise AssertionError(form)


# Every form on both files, except the repeat workout, which is built from sample_run.fit's four steps.
COUNT_CASES = [
    (form, name)
    for form in sorted(FORMS)
    for name in ("sample_run", "strap_run_hrv")
    if not (form == "repeat_workout" and name != "sample_run")
]


@pytest.mark.parametrize(("form", "name"), COUNT_CASES)
def test_record_and_rr_counts_unchanged_by_patched_laps(form, name):
    original = _bytes(name)
    patched = FORMS[form](original)
    assert patched != original
    with TestClient(app) as client:
        base = _upload_and_read(client, original)
        after = _upload_and_read(client, patched)

    print(name, form, "records", base["records"], after["records"], "rr", base["rr"], after["rr"])
    assert base["records"] > 0
    if name == "strap_run_hrv":
        assert base["rr"] > 0
    assert after["records"] == base["records"]
    assert after["rr"] == base["rr"]


# --- delete and schema -----------------------------------------------------------------------


def test_deleting_a_session_deletes_its_laps_and_steps():
    with TestClient(app) as client:
        keep = _upload(client, _bytes("hilly_run_8k_fr945"))
        drop = _upload(client, _bytes("sample_run"))
        assert client.delete(f"/sessions/{drop}").status_code == 204

    conn = db.get_connection()
    try:
        for table in ("laps", "workout_steps"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (drop,)).fetchone()[0] == 0
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (keep,)).fetchone()[0] > 0
    finally:
        conn.close()


def _without_f025(ddl: str) -> str:
    """``_SCHEMA_DDL`` as it stood before laps: no ``laps_read_at`` column, no laps tables."""
    old = ddl.replace("laps_read_at TEXT,", "")
    assert old != ddl
    for table in ("laps", "workout_steps"):
        old, count = re.subn(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n    \);", "", old, flags=re.S)
        assert count == 1, table
        old, count = re.subn(rf"CREATE INDEX IF NOT EXISTS {table}_\w+ ON {table} \([^)]*\);", "", old)
        assert count == 1, table
    return old


def test_an_old_store_gains_laps_read_at_on_init():
    conn = db.get_connection()
    try:
        conn.executescript(_without_f025(db._SCHEMA_DDL))
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        assert "laps_read_at" not in columns
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"laps", "workout_steps"} & tables
        conn.execute(
            "INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device) "
            "VALUES ('old-1', '2026-01-01T00:00:00+00:00', 'running', 'garmin', 'fr945')"
        )
        conn.commit()

        db.init_schema(conn)

        info = {row["name"]: row for row in conn.execute("PRAGMA table_info(sessions)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        row = conn.execute("SELECT laps_read_at, sport FROM sessions WHERE session_id = 'old-1'").fetchone()
    finally:
        conn.close()

    assert info["laps_read_at"]["type"] == "TEXT"
    assert info["laps_read_at"]["notnull"] == 0
    assert info["laps_read_at"]["dflt_value"] is None
    assert {"laps", "workout_steps"} <= tables
    assert row["laps_read_at"] is None
    assert row["sport"] == "running"


def test_the_writer_replaces_rows_and_sets_laps_read_at():
    """``replace_session_laps`` deletes both tables' rows for the session before inserting."""
    with TestClient(app) as client:
        session_id = _upload(client, _bytes("sample_run"))
    conn = db.get_connection()
    try:
        with conn:
            db.replace_session_laps(conn, session_id, [], [], "2026-10-17T00:00:00+00:00")
        laps = conn.execute("SELECT COUNT(*) FROM laps WHERE session_id = ?", (session_id,)).fetchone()[0]
        steps = conn.execute("SELECT COUNT(*) FROM workout_steps WHERE session_id = ?", (session_id,)).fetchone()[0]
        read_at = conn.execute("SELECT laps_read_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()
    assert (laps, steps, read_at) == (0, 0, "2026-10-17T00:00:00+00:00")


def test_laps_read_now_is_an_aware_utc_iso_instant():
    """The seam itself (the autouse fixture replaces it on ``db``; the original is kept at import)."""
    assert _REAL_LAPS_READ_NOW is not None
    parsed = datetime.fromisoformat(_REAL_LAPS_READ_NOW())
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0
