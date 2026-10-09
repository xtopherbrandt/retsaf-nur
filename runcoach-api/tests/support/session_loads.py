"""``tests/support/session_loads.py``: plant a chosen saved load for a stored session (F018 tests).

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are.

Every ``db.persist`` saves the session's load row inside the upload's transaction (F017), so a
test that wants a session to carry a chosen counted value or uncounted reason cannot simply
insert one: ``db._write_session_load`` is a plain INSERT and the primary key is taken.
``plant_load`` deletes the session's existing ``session_loads`` row and writes the chosen body
through ``db._write_session_load`` itself, so the production writer stays a plain INSERT (a
double save in production still fails loudly) and the planted row has the shape the writer
gives every row: ``load_value`` and ``load_reason`` mirror the body's ``session_load.value``
and ``session_load.unavailable``.

The body is the minimum the writer reads plus the session id; it is not a full F017 load body,
and nothing in the chart reads the body column.
"""

from __future__ import annotations

import sqlite3

from runcoach_api import db


def plant_load(conn: sqlite3.Connection, session_id: str, value: float | None = None, reason: str | None = None) -> None:
    """Replace ``session_id``'s saved load with a counted ``value`` or an uncounted ``reason``.

    One transaction: the existing row (if any) is deleted and the chosen one written through
    ``db._write_session_load``. Give a value or a reason, not both: a counted run has no reason
    and an uncounted run has no value.
    """
    if value is not None and reason is not None:
        raise ValueError("a planted load is counted (value) or uncounted (reason), not both")
    body = {"session_id": session_id, "session_load": {"value": value, "unavailable": reason}}
    with conn:
        conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
        db._write_session_load(conn, session_id, body)
