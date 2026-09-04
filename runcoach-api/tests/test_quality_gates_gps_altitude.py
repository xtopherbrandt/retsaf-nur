"""T029: GPS-accuracy flag + barometric-altitude smoothing quality gate.

Pure-logic unit tests against synthetic ``Session``/``Record`` objects
(no FIT-parsing dependency) -- exercises the two independent sub-checks
added to ``quality_gates.apply()`` by T029:

- ``"gps_degraded"`` is appended to ``sample_quality`` for any record
  where ``record.gps_degraded is True``. The flag itself is expected
  to already be populated on the ``Record`` by the time ``apply()``
  runs (e.g. set during FIT mapping from a ``gps_accuracy``-like
  field) -- this gate only reads it and translates it into the
  ``sample_quality`` marker; no GPS-accuracy heuristic is invented
  here.
- ``record.altitude`` is smoothed in place via a small centered moving
  average, so a single obvious outlier sample is pulled toward its
  neighbors while a flat/uniform stream is left essentially unchanged.
  A single-record stream is a no-op (the window can't extend).

Computes no grade-adjusted pace or Minetti-curve calculation here --
this only flags GPS-degraded samples and smooths raw altitude, per
T029's scope.
"""

from __future__ import annotations

from runcoach_api.ingestion import quality_gates
from runcoach_api.models import Record, Session


def _session(session_id: str = "s-1") -> Session:
    return Session(
        session_id=session_id,
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
    )


def test_gps_degraded_true_is_flagged() -> None:
    session = _session()
    records = [
        Record(t=0.0, gps_degraded=True),
        Record(t=1.0, gps_degraded=False),
    ]

    quality_gates.apply(session, records)

    assert "gps_degraded" in records[0].sample_quality


def test_gps_degraded_none_or_false_is_not_flagged() -> None:
    session = _session()
    records = [
        Record(t=0.0, gps_degraded=None),
        Record(t=1.0, gps_degraded=False),
    ]

    quality_gates.apply(session, records)

    assert "gps_degraded" not in records[0].sample_quality
    assert "gps_degraded" not in records[1].sample_quality


def test_altitude_outlier_is_smoothed_toward_neighbors() -> None:
    session = _session()
    raw = [100.0, 150.0, 101.0, 102.0, 99.0]
    records = [Record(t=float(i), altitude=alt) for i, alt in enumerate(raw)]

    quality_gates.apply(session, records)

    smoothed_outlier = records[1].altitude
    assert smoothed_outlier is not None
    # No longer the raw spike value...
    assert smoothed_outlier != 150.0
    # ...and pulled substantially toward its flat-ish neighbors (100,
    # 101, 102, 99) rather than staying anywhere near the raw spike.
    assert smoothed_outlier < 130.0


def test_flat_altitude_stream_is_essentially_unchanged() -> None:
    session = _session()
    records = [Record(t=float(i), altitude=100.0) for i in range(5)]

    quality_gates.apply(session, records)

    for record in records:
        assert record.altitude == 100.0


def test_single_record_stream_altitude_unchanged() -> None:
    session = _session()
    records = [Record(t=0.0, altitude=100.0, gps_degraded=None)]

    quality_gates.apply(session, records)

    assert records[0].altitude == 100.0


def test_single_record_stream_still_flags_gps_degraded() -> None:
    """Sprint-002 re-review, Stage 0 code-review finding: apply()'s
    ``len(records) < 2`` early return (needed for resampling/
    cadence-lock, which require a delta) used to skip gps_degraded
    flagging and altitude smoothing too, even though both operate
    correctly on a single record on their own."""
    session = _session()
    records = [Record(t=0.0, gps_degraded=True)]

    quality_gates.apply(session, records)

    assert "gps_degraded" in records[0].sample_quality


def test_empty_record_stream_does_not_raise() -> None:
    session = _session()
    records: list[Record] = []

    quality_gates.apply(session, records)

    assert records == []


def test_smoothed_altitude_samples_are_flagged_altitude_smoothed() -> None:
    """T034 item 4: the raw barometric sample is overwritten in place
    with no marker that smoothing occurred -- E003's GAP consumes this
    smoothed value, and without a flag there is no way to tell a
    smoothed sample from a genuinely-measured one, or recompute if the
    3-sample window proves wrong, without re-ingesting.
    """
    session = _session()
    raw = [100.0, 150.0, 101.0, 102.0, 99.0]
    records = [Record(t=float(i), altitude=alt) for i, alt in enumerate(raw)]

    quality_gates.apply(session, records)

    assert all("altitude_smoothed" in r.sample_quality for r in records)


def test_altitude_smoothed_flag_not_added_when_no_neighbor_contributes() -> None:
    """An isolated altitude sample with no non-None neighbor in its
    window is a smoothing no-op -- it must not be flagged as smoothed
    when nothing actually changed."""
    session = _session()
    records = [
        Record(t=0.0, altitude=100.0, heart_rate=140.0),
        Record(t=1.0, altitude=None, heart_rate=141.0),
        Record(t=2.0, altitude=None, heart_rate=142.0),
        Record(t=3.0, altitude=None, heart_rate=143.0),
        Record(t=4.0, altitude=99.0, heart_rate=144.0),
    ]

    quality_gates.apply(session, records)

    assert "altitude_smoothed" not in records[0].sample_quality
    assert "altitude_smoothed" not in records[4].sample_quality


def test_gps_degraded_true_on_two_record_stream_altitude_still_smoothed_noop_when_flat() -> None:
    """Sanity check that the two independent sub-checks don't interfere
    with each other on a small stream."""
    session = _session()
    records = [
        Record(t=0.0, altitude=50.0, gps_degraded=True),
        Record(t=1.0, altitude=50.0, gps_degraded=False),
    ]

    quality_gates.apply(session, records)

    assert "gps_degraded" in records[0].sample_quality
    assert "gps_degraded" not in records[1].sample_quality
    assert records[0].altitude == 50.0
    assert records[1].altitude == 50.0


def test_interpolated_record_gps_degraded_is_not_corrupted_to_a_float() -> None:
    """gps_degraded is a boolean flag, not a continuous measurement --
    it must be excluded from the resampler's linear interpolation
    (like the existing power_model exclusion) so a synthetic record
    between two real ones never ends up with a fractional (neither
    True/False/None) gps_degraded value.

    Sprint-002 re-review, Stage 0 code-review finding: excluding it
    from linear interpolation is not the same as leaving it unset --
    a synthetic sample straddling a GPS-degraded stretch is itself
    inside that stretch and must inherit the flag (conservatively, via
    OR of its real neighbours) rather than silently reading as
    "not degraded".
    """
    session = _session()
    # t=0,1,4 -- a 3s gap that gets filled with interpolated records.
    records = [
        Record(t=0.0, altitude=100.0, gps_degraded=True),
        Record(t=1.0, altitude=100.0, gps_degraded=True),
        Record(t=4.0, altitude=100.0, gps_degraded=True),
    ]

    quality_gates.apply(session, records)

    by_t = {r.t: r for r in records}
    for synthetic_t in (2.0, 3.0):
        assert by_t[synthetic_t].gps_degraded is True


def test_interpolated_record_gps_degraded_false_when_neither_neighbor_degraded() -> None:
    session = _session()
    records = [
        Record(t=0.0, altitude=100.0, gps_degraded=False),
        Record(t=1.0, altitude=100.0, gps_degraded=False),
        Record(t=4.0, altitude=100.0, gps_degraded=False),
    ]

    quality_gates.apply(session, records)

    by_t = {r.t: r for r in records}
    for synthetic_t in (2.0, 3.0):
        assert by_t[synthetic_t].gps_degraded is False


def test_interpolated_record_gps_degraded_none_when_neither_neighbor_reported() -> None:
    session = _session()
    records = [
        Record(t=0.0, altitude=100.0, gps_degraded=None),
        Record(t=1.0, altitude=100.0, gps_degraded=None),
        Record(t=4.0, altitude=100.0, gps_degraded=None),
    ]

    quality_gates.apply(session, records)

    by_t = {r.t: r for r in records}
    for synthetic_t in (2.0, 3.0):
        assert by_t[synthetic_t].gps_degraded is None


def test_interpolated_record_gps_degraded_true_when_only_one_neighbor_reported_degraded() -> None:
    """Conservative OR: one neighbour reporting True is enough to flag
    the synthetic sample, even when the other neighbour never reported
    gps_accuracy at all (None, not False)."""
    session = _session()
    records = [
        Record(t=1.0, altitude=100.0, gps_degraded=True),
        Record(t=4.0, altitude=100.0, gps_degraded=None),
    ]

    quality_gates.apply(session, records)

    by_t = {r.t: r for r in records}
    for synthetic_t in (2.0, 3.0):
        assert by_t[synthetic_t].gps_degraded is True
