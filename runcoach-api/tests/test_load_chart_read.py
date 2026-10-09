"""``db.read_session_load_rows``: every stored session's saved load in one statement (F018).

Integration against the isolated temp store: sessions go in through ``persist_sessions``
(``db.persist``, which saves a load row for each), and ``tests/support/session_loads.py``'s
``plant_load`` replaces a session's saved row with a chosen counted value or uncounted reason.

What the rows must show:

- **Order** is ``start_time`` then ``session_id``, whatever order the sessions were inserted in;
  a later-inserted earlier session and two sessions sharing a ``start_time`` pin both keys.
- **The LEFT JOIN null row**: a session whose ``session_loads`` row is gone reads back with both
  load columns null rather than disappearing (an inner join would hide it, and the route needs
  to see it to name its 500). Every persist saves a row, so the test deletes one in SQL.
- **Planted values** read back as planted: a counted value with a null reason, an uncounted
  reason with a null value.
- **A deleted session** (``db.delete_session``) is absent from the rows.
- ``db._write_session_load`` stays a plain INSERT: a second save for one session raises
  ``sqlite3.IntegrityError``, so ``plant_load``'s delete-then-write is the only way to replace one.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from runcoach_api import db
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.models import Session

SUPPORT = Path(__file__).parent / "support"


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


session_loads = _load_support("session_loads")


def _run(start_time: str, device: str = "fr945") -> Session:
    return Session(
        session_id=derive_session_id(device, start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device=device,
    )


def _rows() -> list[sqlite3.Row]:
    conn = db.get_connection()
    try:
        return db.read_session_load_rows(conn)
    finally:
        conn.close()


def _keys(rows: list[sqlite3.Row]) -> list[tuple[str, str]]:
    return [(row["start_time"], row["session_id"]) for row in rows]


def test_rows_come_back_in_start_time_then_session_id_order(persist_sessions) -> None:
    """Inserted out of order, with two sessions sharing a start_time; read back sorted."""
    third = _run("2026-03-03T07:00:00+00:00")
    first = _run("2026-03-01T07:00:00+00:00")
    same_time_a = _run("2026-03-02T07:00:00+00:00", device="fr945")
    same_time_b = _run("2026-03-02T07:00:00+00:00", device="fr955")
    inserted = [third, same_time_b, first, same_time_a]
    persist_sessions(inserted)

    rows = _rows()
    got = _keys(rows)
    want = sorted((s.start_time, s.session_id) for s in inserted)
    print(f"inserted {[s.session_id[:8] for s in inserted]} read {[k[1][:8] for k in got]}")
    assert got == want
    assert got != [(s.start_time, s.session_id) for s in inserted]
    assert [row["session_id"] for row in rows[1:3]] == sorted([same_time_a.session_id, same_time_b.session_id])
    for row in rows:
        assert set(row.keys()) >= {"session_id", "start_time", "load_value", "load_reason"}


def test_a_session_with_no_saved_row_reads_both_load_columns_null(persist_sessions) -> None:
    """The LEFT JOIN: the session stays in the rows with load_value and load_reason both null."""
    kept = _run("2026-03-01T07:00:00+00:00")
    stripped = _run("2026-03-02T07:00:00+00:00")
    persist_sessions([kept, stripped])
    conn = db.get_connection()
    try:
        with conn:
            conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (stripped.session_id,))
        rows = db.read_session_load_rows(conn)
    finally:
        conn.close()

    by_id = {row["session_id"]: row for row in rows}
    assert set(by_id) == {kept.session_id, stripped.session_id}
    assert by_id[stripped.session_id]["load_value"] is None
    assert by_id[stripped.session_id]["load_reason"] is None
    # The persisted row is the one persist saved: a session with no records has no HR.
    assert by_id[kept.session_id]["load_reason"] == "no_hr"
    assert by_id[kept.session_id]["load_value"] is None


def test_planted_values_read_back(persist_sessions) -> None:
    counted = _run("2026-03-01T07:00:00+00:00")
    uncounted = _run("2026-03-02T07:00:00+00:00")
    persist_sessions([counted, uncounted])
    conn = db.get_connection()
    try:
        session_loads.plant_load(conn, counted.session_id, value=60.0)
        session_loads.plant_load(conn, uncounted.session_id, reason="wrist_hr_at_threshold")
        rows = db.read_session_load_rows(conn)
        # Planting again replaces the row rather than failing on the primary key.
        session_loads.plant_load(conn, counted.session_id, value=45.5)
        again = db.read_session_load_rows(conn)
    finally:
        conn.close()

    by_id = {row["session_id"]: row for row in rows}
    assert by_id[counted.session_id]["load_value"] == 60.0
    assert by_id[counted.session_id]["load_reason"] is None
    assert by_id[uncounted.session_id]["load_value"] is None
    assert by_id[uncounted.session_id]["load_reason"] == "wrist_hr_at_threshold"
    assert {row["session_id"]: row["load_value"] for row in again}[counted.session_id] == 45.5
    assert len(again) == 2


def test_a_second_save_through_write_session_load_fails_loudly(persist_sessions) -> None:
    """``_write_session_load`` is a plain INSERT: persist already saved this session's row."""
    session = _run("2026-03-01T07:00:00+00:00")
    persist_sessions([session])
    conn = db.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db._write_session_load(conn, session.session_id, {"session_load": {"value": 1.0, "unavailable": None}})
    finally:
        conn.close()


def test_a_deleted_sessions_row_is_gone(persist_sessions) -> None:
    kept = _run("2026-03-01T07:00:00+00:00")
    gone = _run("2026-03-02T07:00:00+00:00")
    persist_sessions([kept, gone])
    conn = db.get_connection()
    try:
        session_loads.plant_load(conn, gone.session_id, value=50.0)
        assert db.delete_session(conn, gone.session_id) is True
        rows = db.read_session_load_rows(conn)
        orphan = conn.execute(
            f"SELECT COUNT(*) FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (gone.session_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert [row["session_id"] for row in rows] == [kept.session_id]
    assert orphan == 0
