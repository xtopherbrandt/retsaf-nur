"""F021 AC2 through the store, AC3 and AC4: a saved load borrows the HR setting in effect on the run's date.

Every row builds its own store (the autouse ``isolated_data_dir``): dated ``models.Session`` rows go
through ``db.persist`` (the ``persist_sessions`` fixture), dated entries through
``db.write_hr_history_entry``, and the saved body is read back with ``db.read_session_load_body``.
The sessions carry no records, so every load is ``no_hr``; the inputs are still reported, which is
what these rows assert (source, value, ``entry_id``, ``effective_from``, ``out_of_period``).

The expectations were written from F021's AC2 seam rows and AC4's source rule, not from the code.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.metrics.session_load import compute_session_load
from runcoach_api.models import Session

NEW_KEY_DEFAULTS = {"entry_id": None, "effective_from": None, "out_of_period": False}
HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")


def _session(session_id: str, start: str, sport: str = "running", **hr) -> Session:
    """A stored run with only the HR values given; each session its own device, so starts never clash."""
    return Session(
        session_id=session_id,
        sport=sport,
        source_vendor="garmin",
        start_time=start,
        source_device=f"device-{session_id}",
        **hr,
    )


def _entry(field: str, value: int, effective_from: str) -> dict:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        entry, created = db.write_hr_history_entry(conn, field, value, effective_from)
    finally:
        conn.close()
    assert created
    return entry


def _saved(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
    finally:
        conn.close()
    assert body is not None, session_id
    return body


def _max(session_id: str) -> dict:
    block = _saved(session_id)["inputs"]["max_hr_bpm"]
    print(f"  {session_id} max_hr_bpm {block}")
    return block


def _anchor_version(field: str) -> int:
    conn = db.get_connection()
    try:
        return db.read_hr_anchors(conn)[field].version
    finally:
        conn.close()


# --- AC2 seam rows ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("run_start", "expected_value", "expected_from"),
    [("2018-05-01T07:00:00+00:00", 195, "2016-01-01"), ("2022-05-01T07:00:00+00:00", 191, "2021-01-01")],
    ids=["2018-run", "2022-run"],
)
def test_dated_entries_serve_the_run_by_its_date(
    persist_sessions, run_start, expected_value, expected_from
) -> None:
    entries = {
        e["effective_from"]: e
        for e in (_entry("max_hr_bpm", 195, "2016-01-01"), _entry("max_hr_bpm", 191, "2021-01-01"))
    }
    persist_sessions([_session("run", run_start)])
    assert _max("run") == {
        "value": expected_value,
        "source": "history",
        "session_id": None,
        "anchor_version": None,
        "anchor_unavailable": None,
        "entry_id": entries[expected_from]["entry_id"],
        "effective_from": expected_from,
        "out_of_period": False,
    }


def test_a_2018_run_borrows_the_2023_anchor_out_of_period(persist_sessions) -> None:
    persist_sessions([_session("file-2023", "2023-03-01T07:00:00+00:00", max_hr_bpm=188)])
    persist_sessions([_session("run-2018", "2018-05-01T07:00:00+00:00")])
    assert _max("run-2018") == {
        "value": 188,
        "source": "anchor",
        "session_id": None,
        "anchor_version": _anchor_version("max_hr_bpm"),
        "anchor_unavailable": None,
        "entry_id": None,
        "effective_from": None,
        "out_of_period": True,
    }


def test_a_2018_run_borrows_the_2016_entry(persist_sessions) -> None:
    entry = _entry("max_hr_bpm", 195, "2016-01-01")
    persist_sessions([_session("file-2023", "2023-03-01T07:00:00+00:00", max_hr_bpm=188)])
    persist_sessions([_session("run-2018", "2018-05-01T07:00:00+00:00")])
    block = _max("run-2018")
    assert block["value"] == 195
    assert block["source"] == "history"
    assert block["entry_id"] == entry["entry_id"]
    assert block["effective_from"] == "2016-01-01"
    assert block["out_of_period"] is False


def test_the_latest_date_wins_so_a_later_entry_beats_an_earlier_file(persist_sessions) -> None:
    entry = _entry("max_hr_bpm", 191, "2023-06-01")
    persist_sessions([_session("file-2023", "2023-03-01T07:00:00+00:00", max_hr_bpm=188)])
    persist_sessions([_session("run-2024", "2024-05-01T07:00:00+00:00")])
    block = _max("run-2024")
    assert (block["value"], block["source"], block["entry_id"]) == (191, "history", entry["entry_id"])


def test_an_undated_patch_me_entry_is_the_anchor_and_not_out_of_period(persist_sessions) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.write_profile_entries(conn, {"max_hr_bpm": 190})
    finally:
        conn.close()
    persist_sessions([_session("run-2018", "2018-05-01T07:00:00+00:00")])
    block = _max("run-2018")
    assert (block["value"], block["source"], block["out_of_period"]) == (190, "anchor", False)
    assert block["anchor_version"] == _anchor_version("max_hr_bpm")


def test_a_file_and_an_entry_on_the_same_day_the_file_wins(persist_sessions) -> None:
    """The file is not the anchor's (a 2025 file is), so it is reported as ``session_file_dated``."""
    _entry("max_hr_bpm", 191, "2023-03-01")
    persist_sessions(
        [
            _session("file-2023", "2023-03-01T07:00:00+00:00", max_hr_bpm=188),
            _session("file-2025", "2025-03-01T07:00:00+00:00", max_hr_bpm=185),
        ]
    )
    persist_sessions([_session("run-2024", "2024-05-01T07:00:00+00:00")])
    assert _max("run-2024") == {
        "value": 188,
        "source": "session_file_dated",
        "session_id": "file-2023",
        "anchor_version": None,
        "anchor_unavailable": None,
        "entry_id": None,
        "effective_from": None,
        "out_of_period": False,
    }


def test_max_and_threshold_borrow_only_from_running_files(persist_sessions) -> None:
    persist_sessions(
        [
            _session("run-file-2019", "2019-03-01T07:00:00+00:00", max_hr_bpm=190, resting_hr_bpm=50),
            _session(
                "bike-file-2020",
                "2020-03-01T07:00:00+00:00",
                sport="cycling",
                max_hr_bpm=180,
                resting_hr_bpm=48,
            ),
            _session("anchor-2025", "2025-03-01T07:00:00+00:00", max_hr_bpm=185, resting_hr_bpm=45),
        ]
    )
    persist_sessions([_session("run-2021", "2021-05-01T07:00:00+00:00")])
    inputs = _saved("run-2021")["inputs"]
    print(f"  max {inputs['max_hr_bpm']} resting {inputs['resting_hr_bpm']}")
    assert (inputs["max_hr_bpm"]["value"], inputs["max_hr_bpm"]["session_id"]) == (190, "run-file-2019")
    assert (inputs["resting_hr_bpm"]["value"], inputs["resting_hr_bpm"]["session_id"]) == (
        48,
        "bike-file-2020",
    )
    assert inputs["max_hr_bpm"]["source"] == inputs["resting_hr_bpm"]["source"] == "session_file_dated"


def test_the_zone_sets_the_runs_local_date(persist_sessions) -> None:
    """03:00 UTC on 2021-01-01 is still 2020-12-31 in New York: the 2021 entry is not yet in effect
    there. ``persist``'s ``zone`` keyword reaches the lookup; without it the configured zone (UTC
    in the tests) is used."""
    old = _entry("max_hr_bpm", 195, "2016-01-01")
    new = _entry("max_hr_bpm", 191, "2021-01-01")
    conn = db.get_connection()
    try:
        db.persist(
            conn,
            _session("run-ny", "2021-01-01T03:00:00+00:00"),
            [],
            [],
            {},
            zone=ZoneInfo("America/New_York"),
        )
    finally:
        conn.close()
    persist_sessions([_session("run-utc", "2021-01-01T03:00:01+00:00")])
    assert (_max("run-ny")["value"], _max("run-ny")["entry_id"]) == (195, old["entry_id"])
    assert (_max("run-utc")["value"], _max("run-utc")["entry_id"]) == (191, new["entry_id"])


def test_the_lookup_reads_no_profile_inputs_beyond_the_sync(persist_sessions, monkeypatch) -> None:
    """``persist`` reads the profile once (its anchor sync); the lookup adds no read of its own."""
    _entry("max_hr_bpm", 195, "2016-01-01")
    persist_sessions([_session("file-2023", "2023-03-01T07:00:00+00:00", max_hr_bpm=188)])
    calls = []
    real = db.read_profile_inputs
    monkeypatch.setattr(db, "read_profile_inputs", lambda conn: calls.append(1) or real(conn))
    persist_sessions([_session("run-2018", "2018-05-01T07:00:00+00:00")])
    print(f"  profile reads for one persist after init_schema: {len(calls)}")
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        before = len(calls)
        db.persist(conn, _session("run-2019", "2019-05-01T07:00:00+00:00"), [], [], {})
    finally:
        conn.close()
    assert len(calls) - before == 1
    assert _max("run-2019")["source"] == "history"


# --- AC3: a run uploaded today is saved as before ---------------------------------------------


def test_a_run_uploaded_today_saves_the_same_values_as_before(persist_sessions) -> None:
    """No entries and a run after every stored file: the body equals what F017 saved, built here
    from F016's anchors exactly as F017's writer built it (five-key ``anchor`` blocks)."""
    persist_sessions(
        [
            _session(
                "file-2023",
                "2023-03-01T07:00:00+00:00",
                max_hr_bpm=188,
                resting_hr_bpm=45,
                threshold_hr_bpm=170,
                sex="male",
            ),
            _session("file-2024", "2024-03-01T07:00:00+00:00", resting_hr_bpm=47),
        ]
    )
    persist_sessions([_session("run-today", "2026-10-01T07:00:00+00:00")])
    saved = _saved("run-today")

    conn = db.get_connection()
    try:
        anchors = db.read_hr_anchors(conn)
        inputs = db.read_session_load_inputs(conn, "run-today")
    finally:
        conn.close()
    for field_name in (*HR_FIELDS, "sex"):
        anchor = anchors[field_name]
        inputs[field_name] = {
            "value": anchor.value,
            "source": "anchor",
            "session_id": None,
            "anchor_version": anchor.version,
            "anchor_unavailable": None,
        }
    before = compute_session_load(inputs)
    print(f"  saved inputs {[saved['inputs'][f] for f in HR_FIELDS]}")
    assert saved == before
    for field_name in (*HR_FIELDS, "sex"):
        block = saved["inputs"][field_name]
        assert block["source"] == "anchor", field_name
        assert {k: block[k] for k in NEW_KEY_DEFAULTS} == NEW_KEY_DEFAULTS, field_name
    assert saved["inputs"]["resting_hr_bpm"]["value"] == 47


def test_a_pre_f021_body_reads_back_with_defaults(persist_sessions) -> None:
    """A body saved before F021 has five-key value blocks; the route serves it as a 200 with the
    three new keys at their defaults and every saved key unchanged."""
    persist_sessions([_session("old", "2023-03-01T07:00:00+00:00", max_hr_bpm=188, resting_hr_bpm=45)])
    saved = _saved("old")
    for field_name in (*HR_FIELDS, "sex"):
        saved["inputs"][field_name] = {
            k: v for k, v in saved["inputs"][field_name].items() if k not in NEW_KEY_DEFAULTS
        }
        assert len(saved["inputs"][field_name]) == 5
    conn = db.get_connection()
    try:
        with conn:
            conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", ("old",))
            db._write_session_load(conn, "old", saved)
    finally:
        conn.close()
    with TestClient(app) as client:
        response = client.get("/sessions/old/load")
    assert response.status_code == 200, response.text
    served = response.json()
    print(f"  served max {served['inputs']['max_hr_bpm']}")
    for field_name in (*HR_FIELDS, "sex"):
        assert served["inputs"][field_name] == {**saved["inputs"][field_name], **NEW_KEY_DEFAULTS}, field_name
    served["inputs"] = {k: v for k, v in served["inputs"].items() if k not in (*HR_FIELDS, "sex")}
    saved["inputs"] = {k: v for k, v in saved["inputs"].items() if k not in (*HR_FIELDS, "sex")}
    assert served == saved
