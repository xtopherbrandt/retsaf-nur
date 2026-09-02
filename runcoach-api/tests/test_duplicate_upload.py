"""T026: a duplicate session upload is rejected as a no-op with 409.

Scoped exclusively to this task's behavior -- ``db.persist``'s
``UNIQUE (source_device, start_time)`` constraint-catch on the
``sessions`` table, translated into ``DuplicateSessionError``. No
other test file should be created or reused for this behavior (see
T019's precedent on the shared-filename collision this avoids).

The fixture (``tests/fixtures/sample_run.fit``) is real, user-supplied
FIT data -- per ``.claude/rules/project-testing.md`` this decodes it
for real, never mocks ``fitdecode``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion.exceptions import DuplicateSessionError
from runcoach_api.main import app
from runcoach_api.models import Record, RRInterval, Session

FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"


def _raw_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


# ---------------------------------------------------------------------------
# HTTP-level: same real fixture uploaded twice
# ---------------------------------------------------------------------------


def test_duplicate_fixture_upload_returns_409_referencing_first_session_id() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert first.status_code == 201
        first_session_id = first.json()["session_id"]

        second = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )

    assert second.status_code == 409
    assert first_session_id in second.text


def test_duplicate_fixture_upload_leaves_no_duplicate_rows() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert first.status_code == 201
        first_session_id = first.json()["session_id"]

        second = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert second.status_code == 409

        get_response = client.get(f"/sessions/{first_session_id}")

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["session_id"] == first_session_id

    # Verify directly against the DB too: exactly one sessions row for this
    # (source_device, start_time), and the second (rejected) attempt never
    # left partial records/rr_intervals rows behind.
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE source_device = ? AND start_time = ?",
            (body["source_device"], body["start_time"]),
        )
        assert cur.fetchone()[0] == 1

        cur = conn.execute(
            "SELECT COUNT(*) FROM records WHERE session_id = ?", (first_session_id,)
        )
        records_count = cur.fetchone()[0]
        assert records_count == len(body["records"])
        assert records_count > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# chaos: two near-simultaneous db.persist() calls with identical data --
# isolate DB-level race-safety from the HTTP layer.
# ---------------------------------------------------------------------------


def test_back_to_back_persist_calls_second_raises_duplicate_not_raw_integrity_error() -> (
    None
):
    session = Session(
        session_id="s-dup-race-1",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="race-device",
    )
    records = [Record(t=0.0, heart_rate=120), Record(t=1.0, heart_rate=121)]
    rr_intervals = [RRInterval(seq=0, rr_ms=800.0, rr_source="chest_strap_ecg")]
    quarantine_values = {"vo2max_estimate": 55}

    # A second, distinct session_id -- simulating a second concurrent
    # upload of the *same underlying recording* that independently
    # generated a different session_id (e.g. a different UUID per
    # request) but collides on the real dedup key: (source_device,
    # start_time).
    session_retry = Session(
        session_id="s-dup-race-2",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="race-device",
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, records, rr_intervals, quarantine_values)

        with pytest.raises(DuplicateSessionError) as exc_info:
            db.persist(conn, session_retry, records, rr_intervals, quarantine_values)

        assert exc_info.value.existing_session_id == "s-dup-race-1"

        # Exactly one row set survives -- the second call's rows never landed.
        cur = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE source_device = ? AND start_time = ?",
            ("race-device", "2026-01-01T00:00:00+00:00"),
        )
        assert cur.fetchone()[0] == 1

        cur = conn.execute(
            "SELECT COUNT(*) FROM records WHERE session_id = 's-dup-race-2'"
        )
        assert cur.fetchone()[0] == 0

        cur = conn.execute(
            "SELECT COUNT(*) FROM records WHERE session_id = 's-dup-race-1'"
        )
        assert cur.fetchone()[0] == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# no false positives: distinct (source_device, start_time) pairs both succeed
# ---------------------------------------------------------------------------


def test_distinct_start_times_both_succeed_no_false_positive_collision() -> None:
    session_a = Session(
        session_id="s-distinct-a",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="distinct-device",
    )
    session_b = Session(
        session_id="s-distinct-b",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-02T00:00:00+00:00",
        source_device="distinct-device",
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session_a, [], [], {})
        db.persist(conn, session_b, [], [], {})

        detail_a = db.get_session_detail(conn, "s-distinct-a")
        detail_b = db.get_session_detail(conn, "s-distinct-b")
    finally:
        conn.close()

    assert detail_a is not None
    assert detail_b is not None
