"""T023: recording-mode/resampling quality gate.

Pure-logic unit tests against synthetic ``Session``/``Record`` objects
(no FIT-parsing dependency) -- exercises ``quality_gates.apply()``'s
1Hz-uniformity check, <=5s linear interpolation onto a 1s grid, >5s
gap handling (flag, don't fabricate), and the in-place-mutation
contract (``records[:] = ...`` vs. a local rebind that would silently
not propagate back to the caller).
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


def test_uniform_1s_stream_no_smart_recording_flag() -> None:
    session = _session()
    records = [Record(t=float(i), heart_rate=100 + i) for i in range(5)]

    quality_gates.apply(session, records)

    assert "smart_recording" not in session.quality_flags
    assert [r.t for r in records] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert all(r.sample_quality == [] for r in records)


def test_short_gap_is_flagged_and_linearly_interpolated() -> None:
    session = _session()
    # t=0,1,4 -- a 3s gap between the second and third real samples.
    records = [
        Record(t=0.0, heart_rate=100),
        Record(t=1.0, heart_rate=110),
        Record(t=4.0, heart_rate=140),
    ]

    quality_gates.apply(session, records)

    assert "smart_recording" in session.quality_flags
    assert session.quality_flags.count("smart_recording") == 1

    ts = [r.t for r in records]
    assert ts == [0.0, 1.0, 2.0, 3.0, 4.0]

    by_t = {r.t: r for r in records}
    # Real samples are untouched.
    assert by_t[0.0].sample_quality == []
    assert by_t[1.0].sample_quality == []
    assert by_t[4.0].sample_quality == []
    # Synthetic samples are tagged and carry plausible interpolated
    # heart_rate values (linear between 110 @ t=1 and 140 @ t=4).
    assert by_t[2.0].sample_quality == ["interpolated"]
    assert by_t[2.0].heart_rate == 120  # 110 + (140-110)*(1/3)
    assert by_t[3.0].sample_quality == ["interpolated"]
    assert by_t[3.0].heart_rate == 130  # 110 + (140-110)*(2/3)
    # No gap markers on a fully-interpolated short gap.
    assert not any("interpolation_gap" in r.sample_quality for r in records)


def test_long_gap_is_flagged_but_not_interpolated() -> None:
    session = _session()
    # t=0,1,8 -- a 7s gap, over the 5s interpolation ceiling.
    records = [
        Record(t=0.0, heart_rate=100),
        Record(t=1.0, heart_rate=110),
        Record(t=8.0, heart_rate=180),
    ]

    quality_gates.apply(session, records)

    assert "smart_recording" in session.quality_flags
    # No synthetic points were inserted spanning the >5s gap: only the
    # 3 original samples remain.
    assert [r.t for r in records] == [0.0, 1.0, 8.0]

    by_t = {r.t: r for r in records}
    assert by_t[0.0].sample_quality == []
    assert by_t[1.0].sample_quality == []
    assert "interpolation_gap" in by_t[8.0].sample_quality


def test_apply_mutates_the_same_list_object_passed_in() -> None:
    """Regression for the reassignment footgun: `records = new_list`
    inside apply() would rebind only the local name and silently not
    propagate the resample back to the caller's list -- apply() must
    mutate the given list object in place (e.g. `records[:] = ...`).
    """
    session = _session()
    records = [
        Record(t=0.0, heart_rate=100),
        Record(t=1.0, heart_rate=110),
        Record(t=4.0, heart_rate=140),
    ]
    original_list_object = records

    quality_gates.apply(session, records)

    # Same list object (identity, not just equal contents) now holds
    # the resampled records.
    assert records is original_list_object
    assert len(original_list_object) == 5
    assert [r.t for r in original_list_object] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_flag_not_duplicated_across_multiple_gaps() -> None:
    session = _session()
    records = [
        Record(t=0.0),
        Record(t=1.0),
        Record(t=3.0),  # 2s gap -> interpolated
        Record(t=4.0),
        Record(t=11.0),  # 7s gap -> flagged, not interpolated
    ]

    quality_gates.apply(session, records)

    assert session.quality_flags.count("smart_recording") == 1
