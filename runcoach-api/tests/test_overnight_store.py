"""``db.persist_night``: a night is stored in one transaction, a later re-summary replacing it.

The nights come from the real HRV_STATUS fixtures under ``fixtures/nights/``
(provenance rows in ``fixtures/README.md``: real nights from the wellness FIT,
run proof "no") mapped through ``ingestion.overnight.map_night``, so every
stored shape here is the file's own. The literals (57 values on 2026-10-10;
106 and 104 values on the 2024-01-01 re-summary pair, whose later summary is
at 15:30:53 UTC) are read from a decode of each fixture and written as
numbers, so the tests cannot agree with themselves.

What is pinned:

- a night, its 5-minute series and its quarantined sidecar are written
  together, with the saved load every stored session carries, and a fault
  in the series insert leaves nothing (the chaos seam is
  ``db._insert_overnight_values``, as ``persist``'s insert helpers are);
- the same night again is ``DuplicateSessionError`` naming the stored id;
- a re-summary (same ``source_device`` and ``series_start``) with a later
  summary timestamp replaces the stored night, whatever its value count,
  and reports the id it replaced; an earlier one is a duplicate naming the
  later, stored id; so either upload order of the real pair leaves the
  later file;
- a second sleep on the same day (another ``series_start``) is its own row,
  and a night from another device is never a replacement;
- ``delete_session`` removes the values and the sidecar through
  ``_child_tables``, and the values table's ``REFERENCES`` clause refuses a
  value under a session the store does not hold.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runcoach_api import db
from runcoach_api.ingestion import overnight
from runcoach_api.ingestion.exceptions import DuplicateSessionError
from runcoach_api.ingestion.mapping import derive_session_id

NIGHTS = Path(__file__).parent / "fixtures" / "nights"

# Read from a decode of each fixture (``test_overnight_mapping.py`` pins the
# mapping itself); the store tests assert against these, not against the
# mapper's return for the same file.
RECENT_START = "2026-10-10T11:33:29+00:00"
RECENT_VALUES = 57
RECENT_SIDECAR_FIELDS = 5
EARLIER_START = "2024-01-01T14:52:53+00:00"
EARLIER_VALUES = 106
LATER_START = "2024-01-01T15:30:53+00:00"
LATER_VALUES = 104

VALUES_TABLE = "overnight_hrv_values"


def _night(name: str) -> overnight.Night:
    mapped = overnight.map_night((NIGHTS / f"{name}.fit").read_bytes())
    assert isinstance(mapped, overnight.Night), name
    return mapped


def _conn() -> sqlite3.Connection:
    conn = db.get_connection()
    db.init_schema(conn)
    return conn


def _count(conn: sqlite3.Connection, table: str, session_id: str | None = None) -> int:
    if session_id is None:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,)).fetchone()[0]


def _store_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: _count(conn, table) for table in ("sessions", VALUES_TABLE, "quarantine_sidecar", "session_loads")}


def _nights_stored(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """``(session_id, start_time)`` of every stored night, oldest first."""
    rows = conn.execute(
        "SELECT session_id, start_time FROM sessions WHERE activity_tag = 'overnight_hrv' ORDER BY start_time"
    ).fetchall()
    return [(row["session_id"], row["start_time"]) for row in rows]


def _shifted(night: overnight.Night, hours: int) -> overnight.Night:
    """The same night ``hours`` later: ``start_time``, ``series_start`` and the session id move with it."""
    session = night.session
    start = (datetime.fromisoformat(session.start_time) + timedelta(hours=hours)).isoformat()
    series_start = (datetime.fromisoformat(session.summary["series_start"]) + timedelta(hours=hours)).isoformat()
    moved = dataclasses.replace(
        session,
        start_time=start,
        session_id=derive_session_id(session.source_device, start),
        summary={**session.summary, "series_start": series_start},
    )
    return dataclasses.replace(night, session=moved)


# ---------------------------------------------------------------------------
# one transaction
# ---------------------------------------------------------------------------


def test_a_night_its_values_and_sidecar_are_written_together() -> None:
    night = _night("night_recent")
    seen: list[tuple[sqlite3.Connection, db.NightWrite]] = []
    conn = _conn()
    try:
        write = db.persist_night(conn, night, after_write=lambda c, w: seen.append((c, w)))

        row = conn.execute(
            "SELECT start_time, activity_tag, hrv_source_tier, rr_source, resting_rmssd_ms, summary "
            "FROM sessions WHERE session_id = ?",
            (night.session.session_id,),
        ).fetchone()
        values = conn.execute(
            f"SELECT t, rmssd_ms FROM {VALUES_TABLE} WHERE session_id = ? ORDER BY t",
            (night.session.session_id,),
        ).fetchall()
        sidecar = conn.execute(
            "SELECT field_name FROM quarantine_sidecar WHERE session_id = ?", (night.session.session_id,)
        ).fetchall()
        loads = _count(conn, "session_loads", night.session.session_id)
    finally:
        conn.close()

    assert write == db.NightWrite(session_id=night.session.session_id, replaced_session_id=None)
    assert seen == [(conn, write)]
    assert row is not None
    assert row["start_time"] == RECENT_START
    assert (row["activity_tag"], row["hrv_source_tier"], row["rr_source"]) == (
        "overnight_hrv",
        "health_api_overnight",
        "overnight_ppg",
    )
    assert row["resting_rmssd_ms"] == night.session.resting_rmssd_ms > 0
    assert sorted((t, v) for t, v in values) == sorted(night.values)
    assert len(values) == RECENT_VALUES
    assert sorted(r["field_name"] for r in sidecar) == sorted(night.sidecar)
    assert len(sidecar) == RECENT_SIDECAR_FIELDS
    assert loads == 1


def test_a_failed_values_insert_leaves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    night = _night("night_recent")
    calls: list[str] = []

    def _boom(conn, session_id, values):
        raise RuntimeError("values insert failed")

    conn = _conn()
    try:
        before = _store_counts(conn)
        monkeypatch.setattr(db, "_insert_overnight_values", _boom)
        with pytest.raises(RuntimeError, match="values insert failed"):
            db.persist_night(conn, night, after_write=lambda c, w: calls.append(w.session_id))
        assert conn.in_transaction is False
        after = _store_counts(conn)
    finally:
        conn.close()

    assert before == after == {"sessions": 0, VALUES_TABLE: 0, "quarantine_sidecar": 0, "session_loads": 0}
    assert calls == []


def test_a_fault_in_the_row_hook_rolls_the_night_back() -> None:
    """``after_write`` runs inside the transaction: its fault leaves no night."""
    night = _night("night_recent")

    def _boom(conn, write):
        raise RuntimeError("hook failed")

    conn = _conn()
    try:
        with pytest.raises(RuntimeError, match="hook failed"):
            db.persist_night(conn, night, after_write=_boom)
        assert conn.in_transaction is False
        counts = _store_counts(conn)
    finally:
        conn.close()

    assert counts == {"sessions": 0, VALUES_TABLE: 0, "quarantine_sidecar": 0, "session_loads": 0}


# ---------------------------------------------------------------------------
# duplicates and re-summaries
# ---------------------------------------------------------------------------


def test_the_same_night_again_is_a_duplicate_naming_it() -> None:
    night = _night("night_recent")
    conn = _conn()
    try:
        first = db.persist_night(conn, night)
        with pytest.raises(DuplicateSessionError) as info:
            db.persist_night(conn, night)
        assert conn.in_transaction is False
        counts = _store_counts(conn)
    finally:
        conn.close()

    assert info.value.existing_session_id == first.session_id
    assert counts == {"sessions": 1, VALUES_TABLE: RECENT_VALUES, "quarantine_sidecar": RECENT_SIDECAR_FIELDS, "session_loads": 1}


def test_a_later_resummary_replaces_the_stored_night() -> None:
    earlier, later = _night("night_resummary_earlier"), _night("night_resummary_later")
    assert earlier.session.summary["series_start"] == later.session.summary["series_start"]
    assert earlier.session.source_device == later.session.source_device
    seen: list[db.NightWrite] = []
    conn = _conn()
    try:
        first = db.persist_night(conn, earlier)
        assert _count(conn, VALUES_TABLE, first.session_id) == EARLIER_VALUES

        second = db.persist_night(conn, later, after_write=lambda c, w: seen.append(w))

        stored = _nights_stored(conn)
        counts = _store_counts(conn)
        old_children = {table: _count(conn, table, first.session_id) for table in db._child_tables()}
    finally:
        conn.close()

    assert second == db.NightWrite(session_id=later.session.session_id, replaced_session_id=first.session_id)
    assert seen == [second]
    assert stored == [(later.session.session_id, LATER_START)]
    assert counts == {"sessions": 1, VALUES_TABLE: LATER_VALUES, "quarantine_sidecar": RECENT_SIDECAR_FIELDS, "session_loads": 1}
    assert old_children == {table: 0 for table in db._child_tables()}


def test_an_earlier_resummary_after_the_later_is_a_duplicate_naming_it() -> None:
    earlier, later = _night("night_resummary_earlier"), _night("night_resummary_later")
    conn = _conn()
    try:
        first = db.persist_night(conn, later)
        with pytest.raises(DuplicateSessionError) as info:
            db.persist_night(conn, earlier)
        assert conn.in_transaction is False
        stored = _nights_stored(conn)
        counts = _store_counts(conn)
    finally:
        conn.close()

    assert info.value.existing_session_id == first.session_id == later.session.session_id
    assert stored == [(later.session.session_id, LATER_START)]
    assert counts == {"sessions": 1, VALUES_TABLE: LATER_VALUES, "quarantine_sidecar": RECENT_SIDECAR_FIELDS, "session_loads": 1}


@pytest.mark.parametrize(
    "order",
    [("night_resummary_earlier", "night_resummary_later"), ("night_resummary_later", "night_resummary_earlier")],
    ids=["earlier-then-later", "later-then-earlier"],
)
def test_the_resummary_pair_leaves_the_later_file_in_either_order(order: tuple[str, str]) -> None:
    conn = _conn()
    try:
        for name in order:
            try:
                db.persist_night(conn, _night(name))
            except DuplicateSessionError:
                pass
        stored = _nights_stored(conn)
        values = _count(conn, VALUES_TABLE)
    finally:
        conn.close()

    assert [start for _, start in stored] == [LATER_START]
    assert values == LATER_VALUES


def test_a_resummary_from_another_device_is_its_own_row() -> None:
    """The replacement rule keys on the device too: the same series start under
    another ``source_device`` is a second night, not a replacement."""
    earlier, later = _night("night_resummary_earlier"), _night("night_resummary_later")
    other = dataclasses.replace(
        later,
        session=dataclasses.replace(
            later.session,
            source_device="garmin-other-watch",
            session_id=derive_session_id("garmin-other-watch", later.session.start_time),
        ),
    )
    conn = _conn()
    try:
        db.persist_night(conn, earlier)
        write = db.persist_night(conn, other)
        stored = _nights_stored(conn)
        values = _count(conn, VALUES_TABLE)
    finally:
        conn.close()

    assert write.replaced_session_id is None
    assert stored == [(earlier.session.session_id, EARLIER_START), (other.session.session_id, LATER_START)]
    assert values == EARLIER_VALUES + LATER_VALUES


def test_a_second_sleep_the_same_day_is_its_own_row() -> None:
    first_sleep = _night("night_recent")
    second_sleep = _shifted(first_sleep, 9)
    conn = _conn()
    try:
        one = db.persist_night(conn, first_sleep)
        two = db.persist_night(conn, second_sleep)
        stored = _nights_stored(conn)
        values = _count(conn, VALUES_TABLE)
    finally:
        conn.close()

    assert (one.replaced_session_id, two.replaced_session_id) == (None, None)
    assert stored == [
        (first_sleep.session.session_id, RECENT_START),
        (second_sleep.session.session_id, "2026-10-10T20:33:29+00:00"),
    ]
    assert values == 2 * RECENT_VALUES


# ---------------------------------------------------------------------------
# the way out, and the boundary the table declares
# ---------------------------------------------------------------------------


def test_delete_removes_a_nights_values_and_sidecar() -> None:
    night = _night("night_recent")
    conn = _conn()
    try:
        db.persist_night(conn, night)
        assert _count(conn, VALUES_TABLE, night.session.session_id) == RECENT_VALUES
        assert _count(conn, "quarantine_sidecar", night.session.session_id) == RECENT_SIDECAR_FIELDS

        assert db.delete_session(conn, night.session.session_id) is True

        children = {table: _count(conn, table, night.session.session_id) for table in db._child_tables()}
        assert db.session_exists(conn, night.session.session_id) is False
    finally:
        conn.close()

    assert VALUES_TABLE in children
    assert children == {table: 0 for table in db._child_tables()}


def test_a_value_under_an_absent_session_is_refused() -> None:
    """The ``REFERENCES sessions(session_id)`` clause bites on a connection with
    foreign keys on, which ``get_connection`` turns on."""
    conn = _conn()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {VALUES_TABLE} (session_id, t, rmssd_ms) VALUES (?, ?, ?)",
                ("no-such-night", "2026-10-10T04:16:00+00:00", 43.0),
            )
        assert _count(conn, VALUES_TABLE) == 0
    finally:
        conn.close()
