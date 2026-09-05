"""T036 item 4: FIT file with a valid session ``start_time`` and zero
``record`` messages.

No existing test exercised this structural/count edge case. Traced
and confirmed (per T036's task notes) it already persists cleanly:

- ``mapping.to_canonical()`` -- with ``start_time`` present on the
  session message it never falls back to record timestamps, so
  ``record_msgs = by_name.get("record", [])`` being ``[]`` yields
  ``records == []`` and no exception.
- ``quality_gates.apply()`` -- the ``len(records) < 2`` branch sets
  ``recording_interval = "irregular"`` directly and skips resampling
  and cadence-lock; ``_flag_gps_degraded([])`` and
  ``_smooth_altitude([])`` are documented no-ops.

Synthetic input is correct here (not a real-fixture violation of
``.claude/rules/project-testing.md``) -- this is a structural/count
edge case, not FIT-parsing behaviour, and no synthetic-FIT-bytes
encoder exists in the suite. Follows
``test_mapping_missing_start_time.py``'s ``_FakeMsg`` pattern, calling
``mapping.to_canonical()`` directly rather than round-tripping through
a real ``.fit`` byte stream.
"""

from __future__ import annotations

from datetime import datetime, timezone

from runcoach_api.ingestion import mapping, quality_gates


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage`` -- same shape
    as ``test_mapping_missing_start_time.py``'s helper: only implements
    ``.name``, ``get_value(name, fallback=None)``, and ``.fields``."""

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def test_session_with_valid_start_time_and_zero_records_maps_cleanly() -> None:
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [_FakeMsg("session", {"sport": "running", "start_time": ts})]

    session, records = mapping.to_canonical(messages)

    assert records == []
    assert session.start_time == ts.isoformat()


def test_session_with_zero_records_gets_irregular_recording_interval() -> None:
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [_FakeMsg("session", {"sport": "running", "start_time": ts})]

    session, records = mapping.to_canonical(messages)
    quality_gates.apply(session, records)

    assert records == []
    assert session.recording_interval == "irregular"
