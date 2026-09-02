"""T019: happy-path ingestion of a well-formed, 1Hz FIT file.

Scoped exclusively to this task's behavior -- decode -> map -> persist
-> GET round trip, plus the transactional-write chaos test. No other
ingestion test file should be created or reused for this behavior (see
T019's task notes on the shared-filename collision this avoids).

The fixture (``tests/fixtures/sample_run.fit``) is real, user-supplied
FIT data -- per ``.claude/rules/project-testing.md`` this decodes it
for real, never mocks ``fitdecode``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.main import app
from runcoach_api.models import Record, RRInterval, Session

FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"


def _raw_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


# ---------------------------------------------------------------------------
# decode + map against the real fixture
# ---------------------------------------------------------------------------


def test_decode_real_fixture_produces_data_messages() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())

    assert len(messages) > 0
    names = {m.name for m in messages}
    assert "record" in names
    assert "session" in names


def test_to_canonical_produces_plausible_field_values() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())
    session, records = mapping.to_canonical(messages)

    assert session.source_vendor == "garmin"
    assert session.sport == "running"
    assert len(records) > 0

    # lat/lon: fitdecode does NOT convert semicircles on its own (verified
    # via one-off inspection -- position_lat's .value equalled .raw_value),
    # so these must land in valid WGS-84 degree ranges only because our
    # own conversion ran, not raw semicircle integers (which are ~5-6
    # orders of magnitude out of range).
    first_with_position = next(r for r in records if r.lat is not None)
    assert -90 <= first_with_position.lat <= 90
    assert -180 <= first_with_position.lon <= 180
    # Known-good reference for this fixture's first GPS sample (Vancouver
    # area), computed independently from the raw semicircle value.
    assert first_with_position.lat == pytest.approx(49.3472, abs=1e-3)
    assert first_with_position.lon == pytest.approx(-123.2496, abs=1e-3)

    # altitude/distance/cadence: fitdecode's DefaultDataProcessor DOES
    # already apply scale/offset for these (verified: enhanced_altitude's
    # raw 3069 / value 113.8 matches raw/5 - 500 exactly), so our code
    # must pass them through, not double-convert.
    first_with_altitude = next(r for r in records if r.altitude is not None)
    assert 0 < first_with_altitude.altitude < 9000  # plausible metres, not a raw int

    first_with_distance = next(r for r in records if r.distance is not None)
    assert 0 <= first_with_distance.distance < 100  # first sample, metres not cm

    # cadence: fitdecode leaves this as raw rpm with no x2 folding, so our
    # code must apply (cadence + fractional_cadence) * 2.
    first_with_cadence = next(r for r in records if r.cadence is not None)
    assert first_with_cadence.cadence >= 20  # plausible spm, not raw ~half rpm
    assert first_with_cadence.cadence % 2 == 0  # always even: folded via x2

    # distance is cumulative -- later samples should not be smaller than
    # earlier ones for a well-formed recording.
    distances = [r.distance for r in records if r.distance is not None]
    assert distances == sorted(distances)


def test_sport_field_stored_verbatim_for_fixtures_actual_sport() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())
    session, _records = mapping.to_canonical(messages)

    assert session.sport == "running"


def test_non_running_sport_is_stored_verbatim_not_rejected() -> None:
    # No non-running real fixture is available (T019's scope is the
    # well-formed running fixture only) -- per the task's own fallback,
    # this is asserted directly against a constructed Session instead.
    session = Session(
        session_id="s-cycling-1",
        sport="cycling",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="edge-1030",
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, [], [], {})
        detail = db.get_session_detail(conn, "s-cycling-1")
    finally:
        conn.close()

    assert detail is not None
    assert detail["sport"] == "cycling"


# ---------------------------------------------------------------------------
# full ingest -> SQLite -> GET round trip
# ---------------------------------------------------------------------------


def test_full_ingest_to_get_round_trip() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert response.status_code == 201
        session_id = response.json()["session_id"]

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["session_id"] == session_id
    assert body["sport"] == "running"
    assert body["source_vendor"] == "garmin"
    assert len(body["records"]) > 0
    assert body["records"][0]["distance"] is not None
    assert body["context"] is not None
    assert body["context"]["ingested_at"] is not None
    assert body["context"]["provenance"]["adapter"] == "garmin_fit"


def test_get_unknown_session_returns_404() -> None:
    with TestClient(app) as client:
        response = client.get("/sessions/does-not-exist")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# chaos: mid-transaction failure must roll back completely
# ---------------------------------------------------------------------------


def test_mid_transaction_failure_rolls_back_all_four_tables(monkeypatch) -> None:
    session = Session(
        session_id="s-chaos-1",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="chaos-device",
    )
    records = [Record(t=0.0, heart_rate=120), Record(t=1.0, heart_rate=121)]
    rr_intervals = [RRInterval(seq=0, rr_ms=800.0, rr_source="chest_strap_ecg")]
    quarantine_values = {"vo2max_estimate": 55}

    def boom(*args, **kwargs):
        raise RuntimeError("simulated mid-transaction failure")

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        # Scoped via monkeypatch.context() rather than the shared `monkeypatch`
        # fixture directly -- an explicit .undo() on the shared fixture would
        # also revert the autouse isolated_data_dir patch from conftest.py,
        # since fixture-injected monkeypatch is one instance per test.
        with monkeypatch.context() as m:
            m.setattr(db, "_insert_quarantine_sidecar", boom)
            with pytest.raises(RuntimeError):
                db.persist(conn, session, records, rr_intervals, quarantine_values)

        for table in ("sessions", "records", "rr_intervals", "quarantine_sidecar"):
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session.session_id,)
            )
            assert cur.fetchone()[0] == 0, f"{table} has leftover rows after rollback"
    finally:
        conn.close()

    # A clean retry of the same upload must now succeed.
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, records, rr_intervals, quarantine_values)
        detail = db.get_session_detail(conn, session.session_id)
    finally:
        conn.close()

    assert detail is not None
    assert len(detail["records"]) == 2
    assert len(detail["rr_intervals"]) == 1
