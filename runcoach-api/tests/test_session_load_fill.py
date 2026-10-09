"""The one-time fill: every session stored before F017 gets its saved load at the next startup (F017 AC8).

User rulings 2026-10-08 and 2026-10-09: a session stored before this feature is
not re-uploaded; ``db.init_schema`` (which the app's lifespan runs at startup,
and every upload and test seeder runs too) computes and saves a load for each
session without one, as an upload would, with the anchors current then, after
copying ``timer_time_s`` from the session's stored ``summary.duration_s`` (the
FIT timer time its upload saved) when the column is null. A fault while filling
one session raises ``db.SessionLoadFillError`` naming that session and stops
startup; the whole startup transaction rolls back.

**The real-store shape is built by deleting rows, not by a second schema.** Every
upload saves a load, so a store with sessions and no loads cannot be reached
through the API; the tests upload the fixtures through ``POST /sessions``, then
``DELETE FROM session_loads`` and null ``timer_time_s`` directly. That is the
exact state a store from before F017 is in after ``_reconcile_columns`` lands
the column and the table: the rows the fill reads are the ones an older
version wrote, untouched.

**The oracle for "as an upload would"** is a fresh upload in that store: after
the fill, each session is deleted and its file uploaded again with every other
session present, and the fresh saved body and ``timer_time_s`` must equal the
filled ones. A running fixture carries all four HR values, so its body borrows
nothing; a capture or snapshot that lacks one borrows the anchor, and the
control shows the fill borrowed the same value and version a fresh upload
borrows, because deleting a session that lacks a field cannot move that
field's anchor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.models import Session

FIXTURES = Path(__file__).parent / "fixtures"
ALL_FIXTURES = tuple(sorted(p.name for p in FIXTURES.glob("*.fit")))
SOME_FIXTURES = ("sample_run.fit", "strap_hrv_capture.fit", "sample_health_snapshot.fit")


def _upload(client: TestClient, name: str) -> str:
    response = client.post("/sessions", files={"file": (name, (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _rows() -> dict[str, dict]:
    """Every stored session as ``{session_id: {timer_time_s, load_value, load_reason, body}}``,
    ``body`` the stored JSON text (undecoded) or None when the session has no saved load."""
    conn = db.get_connection()
    try:
        return {
            r["session_id"]: {
                "timer_time_s": r["timer_time_s"],
                "load_value": r["load_value"],
                "load_reason": r["load_reason"],
                "body": r["body"],
            }
            for r in conn.execute(
                f"SELECT s.session_id, s.timer_time_s, l.load_value, l.load_reason, l.body FROM sessions s "
                f"LEFT JOIN {db.SESSION_LOADS_TABLE} l USING (session_id) ORDER BY s.session_id"
            )
        }
    finally:
        conn.close()


def _null_out(session_ids: tuple[str, ...] | None = None) -> None:
    """The real-store shape: the saved loads deleted and ``timer_time_s`` nulled, for every
    session or for the named ones."""
    conn = db.get_connection()
    try:
        with conn:
            if session_ids is None:
                conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE}")
                conn.execute("UPDATE sessions SET timer_time_s = NULL")
            else:
                for session_id in session_ids:
                    conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
                    conn.execute("UPDATE sessions SET timer_time_s = NULL WHERE session_id = ?", (session_id,))
    finally:
        conn.close()


def _assert_nulled(rows: dict[str, dict], session_ids) -> None:
    for session_id in session_ids:
        assert rows[session_id]["body"] is None, session_id
        assert rows[session_id]["timer_time_s"] is None, session_id


# --- AC8: the fill, against a fresh upload in the same store ------------------------------------


def test_startup_fills_a_load_for_a_session_without_one() -> None:
    with TestClient(app) as client:
        uploaded = {name: _upload(client, name) for name in ALL_FIXTURES}
    _null_out()
    _assert_nulled(_rows(), uploaded.values())

    with TestClient(app):
        pass  # the lifespan's init_schema is the startup under test
    filled = _rows()

    for name, session_id in uploaded.items():
        row = filled[session_id]
        assert row["body"] is not None, f"{name}: no saved load after startup"
        assert row["timer_time_s"] is not None, f"{name}: timer_time_s not filled"
        body = json.loads(row["body"])
        assert body["session_id"] == session_id
        assert body["inputs"]["timer_time_s"] == row["timer_time_s"]

    # The oracle: a fresh upload of the same file in that store.
    with TestClient(app) as client:
        for name, session_id in uploaded.items():
            assert client.delete(f"/sessions/{session_id}").status_code == 204
            assert _upload(client, name) == session_id
    fresh = _rows()
    for name, session_id in uploaded.items():
        print(
            f"{name}: filled load {filled[session_id]['load_value']}/{filled[session_id]['load_reason']} "
            f"timer {filled[session_id]['timer_time_s']} | fresh upload load "
            f"{fresh[session_id]['load_value']}/{fresh[session_id]['load_reason']} timer {fresh[session_id]['timer_time_s']}"
        )
        assert filled[session_id]["timer_time_s"] == fresh[session_id]["timer_time_s"], name
        assert json.loads(filled[session_id]["body"]) == json.loads(fresh[session_id]["body"]), name
        assert (filled[session_id]["load_value"], filled[session_id]["load_reason"]) == (
            fresh[session_id]["load_value"],
            fresh[session_id]["load_reason"],
        ), name


def test_a_second_startup_changes_no_row_and_only_a_session_without_a_load_is_filled() -> None:
    """Idempotent by selection: the fill reads only sessions with no ``session_loads`` row. A
    second start leaves every row byte-identical, and nulling one session's load alone fills that
    one and touches no other."""
    with TestClient(app) as client:
        uploaded = {name: _upload(client, name) for name in SOME_FIXTURES}
    _null_out()
    with TestClient(app):
        pass
    first = _rows()
    assert all(row["body"] is not None for row in first.values())

    with TestClient(app):
        pass
    assert _rows() == first

    one = uploaded["sample_run.fit"]
    _null_out((one,))
    partial = _rows()
    assert partial[one]["body"] is None and partial[one]["timer_time_s"] is None
    with TestClient(app):
        pass
    again = _rows()
    print(f"second start: {len(first)} rows unchanged; re-filled {one}: {again[one]['load_value']}")
    assert again == first


# --- AC8: the forced fault names the session and stops startup ----------------------------------


def test_a_fault_while_filling_names_the_session_and_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
    _null_out()
    fired: list[str] = []

    def boom(conn, session_id, *, resolved=None):
        fired.append(session_id)
        raise RuntimeError("simulated fault computing the load")

    with monkeypatch.context() as m:
        m.setattr(db, "_save_session_load", boom)
        with pytest.raises(db.SessionLoadFillError) as excinfo:
            with TestClient(app):
                pass
    print(f"startup stopped: {excinfo.value}")
    assert session_id in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert fired == [session_id]
    # The startup transaction rolled back: no load, and the timer-time copy is undone with it.
    rows = _rows()
    assert rows[session_id]["body"] is None
    assert rows[session_id]["timer_time_s"] is None

    # Control: unpatched, the next start fills it.
    with TestClient(app):
        pass
    rows = _rows()
    assert rows[session_id]["load_value"] == pytest.approx(47.36, abs=0.01)
    assert rows[session_id]["timer_time_s"] == pytest.approx(2816.667)


# --- the timer-time copy: from the stored summary, only when the column is null ----------------


def _stored_session(session_id: str, *, summary, timer_time_s=None, sport: str = "running") -> Session:
    return Session(
        session_id=session_id,
        sport=sport,
        source_vendor="garmin",
        start_time="2026-09-01T06:00:00+00:00",
        source_device=f"device-{session_id}",  # distinct, so the UNIQUE (device, start) slot is free
        summary=summary,
        timer_time_s=timer_time_s,
    )


def test_the_fill_copies_timer_time_s_from_the_stored_summary_only_when_the_column_is_null() -> None:
    """Rows written by ``_insert_session`` alone (no ``persist``, so no load), then one
    ``init_schema``: ``summary.duration_s`` is copied when ``timer_time_s`` is null and is a finite
    number; a column that already holds a value keeps it; a summary without a number leaves the
    column null and the body's ``timer_time_s`` null, as ``mapping`` reads such a field. No
    records, so every body is ``no_hr``; the fill saves it anyway."""
    rows = {
        "copied": _stored_session("copied", summary={"duration_s": 150.797}),
        "kept": _stored_session("kept", summary={"duration_s": 150.797}, timer_time_s=99.0),
        "no-summary": _stored_session("no-summary", summary=None),
        "no-duration": _stored_session("no-duration", summary={"distance_m": 5.0}),
        "unparseable": _stored_session("unparseable", summary={"duration_s": "150"}),
        "other-sport": _stored_session("other-sport", summary={"duration_s": 120.0}, sport="other"),
    }
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        for session in rows.values():
            db._insert_session(conn, session)
        conn.commit()
        assert conn.execute(f"SELECT COUNT(*) FROM {db.SESSION_LOADS_TABLE}").fetchone()[0] == 0

        db.init_schema(conn)

        stored = {
            r["session_id"]: (r["timer_time_s"], r["load_reason"], json.loads(r["body"]))
            for r in conn.execute(
                f"SELECT s.session_id, s.timer_time_s, l.load_reason, l.body FROM sessions s "
                f"JOIN {db.SESSION_LOADS_TABLE} l USING (session_id)"
            )
        }
    finally:
        conn.close()

    assert set(stored) == set(rows)
    for session_id, (timer, reason, body) in stored.items():
        print(f"{session_id}: timer_time_s {timer!r} reason {reason} body timer {body['inputs']['timer_time_s']!r}")
        assert body["inputs"]["timer_time_s"] == timer, session_id
    assert stored["copied"][0] == 150.797
    assert stored["kept"][0] == 99.0
    assert stored["no-summary"][0] is None
    assert stored["no-duration"][0] is None
    assert stored["unparseable"][0] is None
    assert stored["other-sport"][0] == 120.0
    assert stored["other-sport"][1] == "sport_not_running"
    assert all(stored[s][1] == "no_hr" for s in rows if s != "other-sport")
