"""The goals table (F032 AC1, storage half; reference section 2).

One active goal at a time: a unique expression index over ``superseded_at IS NULL``, filtered to the
active rows, refuses a second active row (a plain unique index on the nullable column would admit two
nulls, SQLite treating nulls as distinct). ``write_goal`` supersedes the active row and inserts the new
one in one ``BEGIN IMMEDIATE`` transaction, all or nothing; ``read_active_goal`` serves the active row
or None; ``read_session_names`` joins race names by session id. Every store here is a per-test
``tmp_path`` store: nothing touches the real one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from runcoach_api import db
from runcoach_api.models import Session

_NOW_1 = "2026-10-29T10:00:00+00:00"
_NOW_2 = "2026-10-29T11:00:00+00:00"


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.get_connection(tmp_path)
    db.init_schema(connection)
    yield connection
    connection.close()


def _raw_insert(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "INSERT INTO goals (name, race_date, distance_m, target_time_s, created_at, superseded_at)"
        " VALUES (?, '2027-05-02', 42195.0, NULL, ?, NULL)",
        (name, _NOW_1),
    )


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(rows, active rows)."""
    total = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM goals WHERE superseded_at IS NULL").fetchone()[0]
    return total, active


def test_a_second_active_goal_fails_the_unique_index(conn):
    """Two raw inserts with ``superseded_at`` null: the second is refused by the index itself."""
    _raw_insert(conn, "first")
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, "second")
    assert _counts(conn) == (1, 1)


def test_write_goal_supersedes_the_active_row(conn):
    first = db.write_goal(
        conn, name="Spring marathon", race_date="2027-05-02", distance_m=42195.0, target_time_s=None, now=_NOW_1
    )
    second = db.write_goal(
        conn, name="Autumn half", race_date="2027-10-10", distance_m=21097.5, target_time_s=5400.0, now=_NOW_2
    )
    assert first["superseded_at"] is None and first["created_at"] == _NOW_1
    assert second["superseded_at"] is None and second["created_at"] == _NOW_2
    assert second["id"] != first["id"]
    stored_first = conn.execute("SELECT * FROM goals WHERE id = ?", (first["id"],)).fetchone()
    assert stored_first["superseded_at"] == _NOW_2
    assert _counts(conn) == (2, 1)
    active = db.read_active_goal(conn)
    assert active == second
    assert active["name"] == "Autumn half"
    assert active["race_date"] == "2027-10-10"
    assert active["distance_m"] == 21097.5
    assert active["target_time_s"] == 5400.0


def test_a_raising_write_stores_nothing(conn):
    """The insert fails after the supersede: the supersede is rolled back too, and the store is as it
    was. ``sqlite3.Connection.execute`` is read-only, so a trigger makes the insert raise."""
    before = db.write_goal(
        conn, name="Spring marathon", race_date="2027-05-02", distance_m=42195.0, target_time_s=None, now=_NOW_1
    )
    conn.execute(
        "CREATE TRIGGER goals_refuse BEFORE INSERT ON goals BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="refused"):
        db.write_goal(
            conn, name="Autumn half", race_date="2027-10-10", distance_m=21097.5, target_time_s=5400.0, now=_NOW_2
        )
    assert conn.in_transaction is False
    assert _counts(conn) == (1, 1)
    assert db.read_active_goal(conn) == before
    assert conn.execute("SELECT superseded_at FROM goals WHERE id = ?", (before["id"],)).fetchone()[0] is None


def test_read_active_goal_is_none_on_an_empty_store(conn):
    assert db.read_active_goal(conn) is None


def test_a_superseded_only_store_has_no_active_goal(conn):
    _raw_insert(conn, "old")
    conn.execute("UPDATE goals SET superseded_at = ?", (_NOW_2,))
    assert db.read_active_goal(conn) is None


def test_session_names_are_read_by_id(conn):
    for session_id, name in (("race-a", "Spring Half"), ("race-b", None)):
        db.persist(
            conn,
            Session(
                session_id=session_id,
                sport="running",
                source_vendor="garmin",
                start_time="2026-04-05T08:00:00+00:00",
                source_device=f"device-{session_id}",
                name=name,
            ),
            [],
            [],
            {},
        )
    assert db.read_session_names(conn, ["race-a", "race-b", "unknown"]) == {"race-a": "Spring Half", "race-b": None}
    assert db.read_session_names(conn, []) == {}


def test_goals_is_not_a_child_table():
    """``goals`` carries no ``session_id``: ``delete_session`` never sweeps it."""
    assert "goals" in db._expected_schema()
    assert "goals" not in db._child_tables()
