"""An HR-TRIMP oracle outside the code: fitdecode, the F017 time basis and the formulas, nothing else.

Written from the F017 reference ("Time basis" and "The formulas", spec/03
section 3.4.1 and 3.4.2 step 1), not from ``metrics.session_load``. It
imports nothing from ``runcoach_api`` and ``test_session_load_formulas.py``
pins that by reading ``sys.modules`` after the import, so the expected values
it produces cannot share code with the module they check
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``).

What it does, per file:

- **records**: every ``record`` message with a timestamp, in timestamp order;
  ``t`` is seconds from the earliest record. HR is the decoded ``heart_rate``
  (a FIT invalid decodes as ``None``).
- **time basis**: consecutive records form segments; a segment counts when
  ``0 < dt <= 5`` s (a watch pause writes no records, so its gap is a break
  and never counted); a counted segment is *usable* when the HR at its start
  is present and > 0. ``hr_time_s`` is the sum of ``dt`` over usable
  segments and ``avg_hr_bpm`` their ``dt``-weighted mean HR. A strap run
  carries no ``cadence_lock`` tag, so the oracle does not model that tag.
- **settings**: the first ``user_profile`` (``resting_heart_rate``,
  ``gender`` 0 female / 1 male) and the first ``zones_target``
  (``max_heart_rate``, ``threshold_heart_rate``); ``session.total_timer_time``
  is the timer time.
- **formulas**: ``r = (HR_avg - HR_rest) / (HR_max - HR_rest)``;
  ``TRIMP = (hr_time_s / 60) * r * k * e^(c r)`` with ``(k, c)`` = (0.64,
  1.92) for men and (0.86, 1.67) for women; the threshold-hour reference is
  ``60 * r_thr * k * e^(c r_thr)`` with the same pair and
  ``r_thr = (HR_thr - HR_rest) / (HR_max - HR_rest)``; ``session_load =
  TRIMP / reference * 100``.

``trimp_for_file`` takes an optional ``sex`` override so a test can ask for
the women's pair on a file whose profile says male (F017 AC4's 93.29 /
189.38 / 49.26 row on ``sample_run``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import fitdecode

SEGMENT_MAX_DT_S = 5.0

COEFFICIENTS = {"male": (0.64, 1.92), "female": (0.86, 1.67)}


@dataclass(frozen=True)
class DecodedRun:
    """What the oracle read from one file before any formula."""

    records: list[tuple[float, float | None]]  # (t, heart_rate) in t order
    resting_hr_bpm: int | None
    max_hr_bpm: int | None
    threshold_hr_bpm: int | None
    sex: str | None
    timer_time_s: float | None


@dataclass(frozen=True)
class OracleLoad:
    """The oracle's time basis and the three numbers AC1 compares."""

    hr_time_s: float
    avg_hr_bpm: float
    r: float
    trimp: float
    threshold_hour_reference: float
    session_load: float
    coefficients: str
    timer_time_s: float | None


def decode_run(path: Path) -> DecodedRun:
    """Decode one FIT file with fitdecode: the records with HR, the HR settings and the timer time."""
    stamped: list[tuple[object, float | None]] = []
    profile = None
    zones = None
    timer_time_s = None
    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                ts = frame.get_value("timestamp", fallback=None)
                if ts is None:
                    continue
                stamped.append((ts, frame.get_value("heart_rate", fallback=None)))
            elif frame.name == "user_profile" and profile is None:
                profile = frame
            elif frame.name == "zones_target" and zones is None:
                zones = frame
            elif frame.name == "session" and timer_time_s is None:
                timer_time_s = frame.get_value("total_timer_time", fallback=None)
    stamped.sort(key=lambda pair: pair[0])
    start = stamped[0][0] if stamped else None
    records = [((ts - start).total_seconds(), hr) for ts, hr in stamped]  # type: ignore[operator]
    gender = _raw_int(profile, "gender")
    return DecodedRun(
        records=records,
        resting_hr_bpm=_raw_int(profile, "resting_heart_rate"),
        max_hr_bpm=_raw_int(zones, "max_heart_rate"),
        threshold_hr_bpm=_raw_int(zones, "threshold_heart_rate"),
        sex={0: "female", 1: "male"}.get(gender) if gender is not None else None,
        timer_time_s=float(timer_time_s) if timer_time_s is not None else None,
    )


def _raw_int(message, field_name: str) -> int | None:
    """The field's raw integer (``gender`` decodes to ``'male'``, its raw value is 1); None when absent."""
    if message is None:
        return None
    raw = message.get_value(field_name, fallback=None, raw_value=True)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def time_basis(records: list[tuple[float, float | None]]) -> tuple[float, float | None]:
    """``(hr_time_s, avg_hr_bpm)`` over counted segments whose start HR is present and > 0."""
    hr_time_s = 0.0
    weighted = 0.0
    for (t0, hr), (t1, _) in pairwise(records):
        dt = t1 - t0
        if not (0.0 < dt <= SEGMENT_MAX_DT_S):
            continue
        if hr is None or hr <= 0:
            continue
        hr_time_s += dt
        weighted += dt * float(hr)
    return hr_time_s, (weighted / hr_time_s if hr_time_s > 0.0 else None)


def trimp(duration_min: float, r: float, sex: str) -> float:
    k, c = COEFFICIENTS[sex]
    return duration_min * r * k * math.exp(c * r)


def threshold_hour_reference(r_thr: float, sex: str) -> float:
    return trimp(60.0, r_thr, sex)


def trimp_for_file(path: Path, *, sex: str | None = None) -> OracleLoad:
    """The oracle's numbers for one running file whose own profile carries resting, max and threshold."""
    run = decode_run(path)
    pair = sex or run.sex or "male"
    hr_time_s, avg_hr = time_basis(run.records)
    if avg_hr is None or run.resting_hr_bpm is None or run.max_hr_bpm is None or run.threshold_hr_bpm is None:
        raise ValueError(f"{path.name}: the oracle needs usable HR and the three HR settings from the file")
    span = float(run.max_hr_bpm - run.resting_hr_bpm)
    r = (avg_hr - run.resting_hr_bpm) / span
    r_thr = (run.threshold_hr_bpm - run.resting_hr_bpm) / span
    value = trimp(hr_time_s / 60.0, r, pair)
    reference = threshold_hour_reference(r_thr, pair)
    return OracleLoad(
        hr_time_s=hr_time_s,
        avg_hr_bpm=avg_hr,
        r=r,
        trimp=value,
        threshold_hour_reference=reference,
        session_load=value / reference * 100.0,
        coefficients=pair,
        timer_time_s=run.timer_time_s,
    )
