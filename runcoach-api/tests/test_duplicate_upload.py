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

import sqlite3
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


def test_null_sport_not_null_violation_raises_integrity_error_not_duplicate() -> None:
    """Regression (code-review Fix 2): ``persist()``'s ``IntegrityError``
    handler used to assume every ``IntegrityError`` was the
    ``UNIQUE(source_device, start_time)`` dedup constraint -- but
    ``sessions.sport TEXT NOT NULL`` can also raise ``IntegrityError``
    (e.g. a FIT file with no session/sport message reaching mapping
    with ``sport=None``). Nothing is actually committed in that case,
    so the post-failure dedup SELECT finds no row -- that must surface
    the real ``sqlite3.IntegrityError``, not a fabricated
    ``DuplicateSessionError`` referencing a session_id that was never
    inserted.
    """
    session = Session(
        session_id="s-null-sport-1",
        sport=None,  # type: ignore[arg-type]
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="null-sport-device",
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.persist(conn, session, [], [], {})

        assert not isinstance(exc_info.value, DuplicateSessionError)

        # Nothing was committed -- confirm no partial row landed.
        cur = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("s-null-sport-1",)
        )
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_unresolvable_source_device_maps_to_sentinel_not_null() -> None:
    """Regression (code-review Fix 3): SQLite's
    ``UNIQUE(source_device, start_time)`` treats every ``NULL`` as
    distinct from every other ``NULL``, so a real ``None`` value in
    ``source_device`` would silently defeat dedup for two uploads with
    the same ``start_time`` and no identifiable device. ``mapping._build_source_device``
    must use a defined sentinel string instead of ``None`` when no
    source device can be determined.
    """
    from runcoach_api.ingestion import mapping

    class _NoDeviceMsg:
        name = "file_id"

        def get_value(self, name, fallback=None):
            return fallback

    result = mapping._build_source_device(mapping._group_by_name([_NoDeviceMsg()]))

    assert result is not None
    assert isinstance(result, str)


def test_two_persists_with_unresolvable_source_device_and_same_start_time_dedup() -> None:
    """The sentinel from ``_build_source_device`` must actually let the
    existing ``UNIQUE(source_device, start_time)`` constraint catch
    same-``start_time`` duplicates from devices with no identifiable
    ``source_device`` -- exercised directly at the ``db.persist()``
    layer against whatever sentinel value ``mapping`` now produces for
    "no device", rather than a real FIT fixture (none of this repo's
    fixtures lack device info).
    """
    from runcoach_api.ingestion import mapping

    class _NoDeviceMsg:
        name = "file_id"

        def get_value(self, name, fallback=None):
            return fallback

    sentinel_device = mapping._build_source_device(mapping._group_by_name([_NoDeviceMsg()]))

    session = Session(
        session_id="s-no-device-1",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device=sentinel_device,
    )
    session_retry = Session(
        session_id="s-no-device-2",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device=sentinel_device,
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, [], [], {})

        with pytest.raises(DuplicateSessionError) as exc_info:
            db.persist(conn, session_retry, [], [], {})

        assert exc_info.value.existing_session_id == "s-no-device-1"
    finally:
        conn.close()


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
