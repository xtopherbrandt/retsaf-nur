"""``GET /sessions`` speed: a page of 50 from 600 long sessions within 2 s (F019 AC6).

Run alone. The budget leaves under a second of margin on the dev machine, so a
run beside parallel agents can miss it for reasons that are not the code's;
the release gate's full suite runs it alone too.

The store is constructed, not uploaded: 600 running sessions of 6,000 records
each (3.6 M rows, about 0.4-0.5 GB) through ``db.init_schema`` (the real DDL,
the ``records (session_id, t)`` index and the ``name`` column), one
``executemany`` for the sessions and one ``WITH RECURSIVE ... INSERT INTO
records SELECT ... CROSS JOIN`` for the records, which builds in a few seconds
where a per-session ``executemany`` took 25 s. Every record is 1 s after the
last with the distance 3 m further on, so every ``dt`` is counted by
``segments.recorded_totals`` and each row's numbers are known exactly: the
assertion on them is what proves the page read the records, not a short cut.

The index is asserted through ``PRAGMA index_list`` before anything is timed,
so a store without it fails on a named message rather than only on a slow time.
``TestClient(app)`` is used without its lifespan: the app's startup
``init_schema`` would fill a saved load for all 600 sessions, which is F017's
one-time fill and not the list's cost; the schema the page needs is already
in place from the construction connection.

Teardown closes nothing that is still open (every connection is closed where it
was opened) and then deletes the store file, asserting it is gone: pytest
keeps three ``basetemp`` trees, and three half-gigabyte stores are not worth
keeping.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app

SESSIONS = 600
RECORDS_PER_SESSION = 6_000
LIMIT = 50
BUDGET_S = 2.0
# Each record is this many metres past the last, one second later.
STEP_M = 3.0
EXPECTED_DURATION_S = float(RECORDS_PER_SESSION - 1)
EXPECTED_DISTANCE_M = STEP_M * (RECORDS_PER_SESSION - 1)


def _store_path() -> Path:
    return db_module._load_config_cached().data_dir / db_module.DB_FILENAME


def _session_rows() -> list[tuple[str, str, str, str, str]]:
    first = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
    rows = []
    for n in range(SESSIONS):
        start_time = (first + timedelta(hours=n)).isoformat()
        device = f"device-{n:04d}"
        rows.append((derive_session_id(device, start_time), start_time, "running", "garmin", device))
    return rows


@pytest.fixture
def long_store(isolated_data_dir: Path) -> Path:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device)"
            " VALUES (?, ?, ?, ?, ?)",
            _session_rows(),
        )
        conn.execute(
            """
            WITH RECURSIVE k(n) AS (
              SELECT 0 UNION ALL SELECT n + 1 FROM k WHERE n + 1 < :per_session
            )
            INSERT INTO records (session_id, t, distance)
            SELECT s.session_id, k.n, k.n * :step_m FROM sessions s CROSS JOIN k
            """,
            {"per_session": RECORDS_PER_SESSION, "step_m": STEP_M},
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == SESSIONS * RECORDS_PER_SESSION
    finally:
        conn.close()
    path = _store_path()
    yield path
    for suffix in ("", "-journal", "-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            side.unlink()
    assert not path.exists(), f"the {SESSIONS}-session store was not deleted: {path}"


def _records_indexes() -> dict[str, list[str]]:
    conn = db_module.get_connection()
    try:
        return {
            row["name"]: [c["name"] for c in conn.execute(f"PRAGMA index_info({row['name']})")]
            for row in conn.execute("PRAGMA index_list(records)").fetchall()
        }
    finally:
        conn.close()


def _timed_page(client: TestClient, label: str, **params) -> dict:
    started = time.perf_counter()
    response = client.get("/sessions", params=params)
    elapsed = time.perf_counter() - started
    print(f"\n{label}: {elapsed:.3f} seconds for {LIMIT} of {SESSIONS} sessions x {RECORDS_PER_SESSION} records")
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["items"]) == LIMIT
    assert elapsed <= BUDGET_S, f"{label} took {elapsed:.3f} s, over the {BUDGET_S:.1f} s budget"
    return page


def test_a_page_of_50_from_600_long_sessions_returns_within_2_s(long_store: Path) -> None:
    indexes = _records_indexes()
    assert ["session_id", "t"] in indexes.values(), f"records has no (session_id, t) index: {indexes}"
    assert long_store.stat().st_size > 100_000_000, long_store.stat().st_size

    client = TestClient(app)
    first = _timed_page(client, "page 1", limit=LIMIT)
    assert first["next_cursor"] is not None
    second = _timed_page(client, "page 2", limit=LIMIT, since=first["next_cursor"])

    for item in first["items"] + second["items"]:
        assert item["duration_s"] == EXPECTED_DURATION_S, item
        assert item["distance_m"] == EXPECTED_DISTANCE_M, item
    ids = [item["session_id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 2 * LIMIT
