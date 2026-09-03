"""Regression (code-review Fix 1): ``record.gps_degraded`` is dead
wiring end-to-end.

Before this fix, ``mapping.py``'s ``_build_record`` never read a
GPS-accuracy signal from the decoded FIT message at all -- every real
ingest left ``Record.gps_degraded`` as ``None``, so
``quality_gates._flag_gps_degraded``'s check on
``record.gps_degraded is True`` could never fire on real data (only
unit tests that hand-construct ``Record(gps_degraded=True)`` directly
exercised it). Additionally the ``records`` table had no
``gps_degraded`` column, so even a correctly-mapped value would not
have survived a ``persist()``/``get_session_detail()`` round trip.

No real fixture in this repo's test corpus (``sample_run.fit``,
``dev_fields_run.fit``, ``sample_health_snapshot.fit``) populates a
``gps_accuracy`` field on any ``record`` message -- confirmed via a
one-off ``fitdecode`` inspection before writing this file (every
record's ``gps_accuracy`` came back absent on all three fixtures).
``mapping.py``'s mapping logic is therefore exercised here against a
lightweight fake FIT message object exposing the same
``get_value()``/``fields`` surface ``_build_record`` actually reads,
per the fallback the task description allows when no real fixture has
the signal.
"""

from __future__ import annotations

from datetime import datetime, timezone

from runcoach_api import db
from runcoach_api.ingestion import mapping
from runcoach_api.models import Record, Session


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage`` ``record``.

    Only implements what ``_build_record`` actually reads:
    ``get_value(name, fallback=None)`` and ``.fields`` (iterated by
    ``_resolve_developer_fields`` for developer-field data, which this
    fake never carries).
    """

    def __init__(self, values: dict) -> None:
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _msg(timestamp=_START, **extra):
    values = {"timestamp": timestamp}
    values.update(extra)
    return _FakeMsg(values)


def test_gps_accuracy_above_threshold_maps_to_gps_degraded_true() -> None:
    record, _ = mapping._build_record(_msg(gps_accuracy=15), _START)

    assert record.gps_degraded is True


def test_gps_accuracy_below_threshold_maps_to_gps_degraded_false() -> None:
    record, _ = mapping._build_record(_msg(gps_accuracy=2), _START)

    assert record.gps_degraded is False


def test_gps_accuracy_absent_maps_to_gps_degraded_none_not_fabricated() -> None:
    record, _ = mapping._build_record(_msg(), _START)

    assert record.gps_degraded is None


def test_gps_degraded_round_trips_through_persist_and_get_session_detail() -> None:
    session = Session(
        session_id="s-gps-degraded-roundtrip",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="gps-roundtrip-device",
    )
    records = [
        Record(t=0.0, gps_degraded=True),
        Record(t=1.0, gps_degraded=False),
        Record(t=2.0, gps_degraded=None),
    ]

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, records, [], {})
        detail = db.get_session_detail(conn, session.session_id)
    finally:
        conn.close()

    assert detail is not None
    by_t = {r["t"]: r for r in detail["records"]}
    assert by_t[0.0]["gps_degraded"] is True
    assert by_t[1.0]["gps_degraded"] is False
    assert by_t[2.0]["gps_degraded"] is None
