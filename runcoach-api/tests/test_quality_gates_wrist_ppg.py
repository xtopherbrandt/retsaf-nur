"""T028: HR-source inference and wrist-PPG cadence-lock quality gate.

Pure-logic unit tests against synthetic ``Session``/``Record`` objects
(no FIT-parsing dependency) -- exercises the two independent sub-checks
added to ``quality_gates.apply()`` by T028:

- ``session.hr_source`` defaults to ``"wrist_ppg"`` unless a
  chest-strap RR stream has already set it (a not-yet-implemented
  upstream task) -- must never clobber that value.
- ``"cadence_lock"`` is flagged on any span of 30+ consecutive records
  (by index, post-resampling, ~1 per second) where ``heart_rate``
  stays within 3bpm of ``cadence`` -- a known wrist-PPG artefact where
  the sensor locks onto cadence instead of true heart rate.

Computes no HR-derived metric here -- this only infers the HR source
and flags raw samples, per T028's scope.
"""

from __future__ import annotations

from runcoach_api.ingestion import quality_gates
from runcoach_api.models import Record, Session


def _session(session_id: str = "s-1", hr_source: str | None = None) -> Session:
    return Session(
        session_id=session_id,
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        hr_source=hr_source,
    )


def test_hr_source_defaults_to_wrist_ppg_when_unset() -> None:
    session = _session()
    records = [
        Record(t=0.0, heart_rate=140, cadence=80),
        Record(t=1.0, heart_rate=142, cadence=81),
    ]

    quality_gates.apply(session, records)

    assert session.hr_source == "wrist_ppg"


def test_hr_source_pre_set_to_chest_strap_is_not_overwritten() -> None:
    session = _session(hr_source="chest_strap")
    records = [
        Record(t=0.0, heart_rate=140, cadence=80),
        Record(t=1.0, heart_rate=142, cadence=81),
    ]

    quality_gates.apply(session, records)

    assert session.hr_source == "chest_strap"


def test_cadence_lock_flagged_for_30_plus_consecutive_records() -> None:
    session = _session()
    # 32 records where heart_rate stays within 3bpm of cadence (150 vs
    # 148, diff=2), followed by 8 records where it clearly doesn't
    # (160 vs 90) to terminate the run.
    locked = [Record(t=float(i), heart_rate=150, cadence=148) for i in range(32)]
    unlocked = [Record(t=float(i), heart_rate=160, cadence=90) for i in range(32, 40)]
    records = locked + unlocked

    quality_gates.apply(session, records)

    assert all("cadence_lock" in r.sample_quality for r in records[:32])
    assert all("cadence_lock" not in r.sample_quality for r in records[32:])


def test_cadence_lock_not_flagged_below_30_consecutive_records() -> None:
    session = _session()
    # Only 15 consecutive records track cadence closely -- below the
    # 30-record/30s threshold, so no flag should appear anywhere.
    records = [Record(t=float(i), heart_rate=150, cadence=148) for i in range(15)]

    quality_gates.apply(session, records)

    assert all(r.sample_quality == [] for r in records)


def test_cadence_lock_run_exactly_at_threshold_is_flagged() -> None:
    session = _session()
    # Exactly 30 consecutive matching records -- the boundary case for
    # the "30+ consecutive seconds" requirement.
    records = [Record(t=float(i), heart_rate=150, cadence=148) for i in range(30)]

    quality_gates.apply(session, records)

    assert all("cadence_lock" in r.sample_quality for r in records)
