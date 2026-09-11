"""T083 -- ``metrics/hrv_trend.py``: local-day bucketing and series construction (F005).

The pure half of the trend: stored rows in, a clean one-reading-per-local-day
series for one source tier out. Nothing here touches the band or the verdict
(T084) or reset detection (T092).

The tests are pure unit tests over hand-built row dicts, which is what the
module's purity (no ``config``, no ``db``, no ``fastapi``) buys -- the same
seam ``pipeline.py`` documents for ``hrv_classification``. The one exception
is the quality-gate probe near the end, which drives the real
``mapping -> classify -> db.persist -> db.read_hrv_rows`` chain because the
claim it makes ("a capture that failed F004's gates is already absent, and
this module does not re-gate it") is a claim about that chain, not about a
dict the test author shaped.

**The contract table below was authored before the implementation**, from the
spec's rule alone (F005 "One reading per local day, chosen within the baseline
tier by time"; construction reference "Series construction", steps 3-4): the
chosen reading is the *earliest capture on the baseline tier*, every other
capture that day is excluded with a reason, and a day with no on-tier capture
contributes nothing. F005's Scenario Outline gives three rows; the table
enumerates ``{baseline tier} x {captures present that day}`` exhaustively so an
omitted row would be visible (contract-tables-need-an-independent-oracle).
"""

from __future__ import annotations

import ast
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api import db
from runcoach_api.metrics import hrv_trend
from runcoach_api.models import RRInterval

AUCKLAND = ZoneInfo("Pacific/Auckland")
KOLKATA = ZoneInfo("Asia/Kolkata")

# The target date F005's canonical response example uses: baseline
# [2026-07-04, 2026-09-01], judged week [2026-09-02, 2026-09-08].
D = date(2026, 9, 8)

STRAP = "chest_strap_raw"
OVERNIGHT = "health_api_overnight"
SNAPSHOT = "health_snapshot"


# ---------------------------------------------------------------------------
# row builders
# ---------------------------------------------------------------------------


def local(day: date, hh: int, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    """The stored ``start_time`` of a capture taken at local wall time
    ``hh:mm`` on ``day`` in ``zone`` -- i.e. converted to UTC and spelled
    ``+00:00`` exactly as ``mapping.py`` stores it."""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str | None, value: float | None, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def readings(tier: str, days: list[date], value: float = 40.0, hh: int = 6) -> list[dict]:
    return [row(local(day, hh), tier, value) for day in days]


def baseline_days(n: int, target: date = D) -> list[date]:
    """``n`` consecutive local days ending at the last day of the baseline
    window, ``target - 7``."""
    end = target - timedelta(days=7)
    return [end - timedelta(days=i) for i in range(n)][::-1]


def days_between(first: date, last: date) -> list[date]:
    """Every local day of the closed interval ``[first, last]``."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def build(rows, zone: ZoneInfo = AUCKLAND, target: date = D) -> hrv_trend.HrvSeries:
    return hrv_trend.build_series(rows, zone, target)


def excluded_reasons(result: hrv_trend.HrvSeries) -> dict[str, str]:
    return {entry.session_id: entry.reason for entry in result.excluded}


# ---------------------------------------------------------------------------
# the first failing test: filter, THEN collapse
# ---------------------------------------------------------------------------


def test_the_series_is_filtered_to_the_baseline_tier_before_days_are_collapsed() -> None:
    """A local day carrying both a ``chest_strap_raw`` and a ``health_snapshot``
    capture, with the baseline established on ``health_snapshot``, contributes
    its **snapshot** reading.

    This is the ordering case where the two plausible implementations differ:
    collapse-then-filter picks the day's highest-fidelity capture (the strap),
    then drops it as off-tier, and the day vanishes from the series. Both
    would pass a test where the day carries a single capture. Perturbation:
    swap the filter and collapse steps and this goes red.
    """
    snapshot_baseline = readings(SNAPSHOT, baseline_days(20))
    day = D - timedelta(days=2)
    strap_first = row(local(day, 6, 12), STRAP, 55.0, "strap-first")
    snapshot_later = row(local(day, 7, 40), SNAPSHOT, 38.0, "snapshot-later")

    result = build(snapshot_baseline + [strap_first, snapshot_later])

    assert result.tier == SNAPSHOT
    by_day = {reading.date: reading for reading in result.series}
    assert day in by_day, "the day must not be dropped merely because its best capture is off-tier"
    assert by_day[day].session_id == "snapshot-later"
    assert by_day[day].rmssd_ms == 38.0
    assert excluded_reasons(result)["strap-first"] == "off_baseline_tier: chest_strap_raw"


# ---------------------------------------------------------------------------
# the contract table: {baseline tier} x {captures present that day}
#
# Authored before the implementation from the spec's rule. Each row is
# (baseline tier, captures in time order, index of the chosen capture or
# None, {index: exclusion reason} for every other capture). Capture times
# are 06:05, 06:12, 07:40 in list order, so "earliest" is list order.
# ---------------------------------------------------------------------------

OFF_STRAP = "off_baseline_tier: chest_strap_raw"
OFF_OVERNIGHT = "off_baseline_tier: health_api_overnight"
OFF_SNAPSHOT = "off_baseline_tier: health_snapshot"
LATER = "same_day_later_capture"

CAPTURE_TIMES = ((6, 5), (6, 12), (7, 40))

CONTRACT_TABLE = [
    # -- baseline on chest_strap_raw ---------------------------------------
    (STRAP, [STRAP], 0, {}),
    (STRAP, [OVERNIGHT], None, {0: OFF_OVERNIGHT}),
    (STRAP, [SNAPSHOT], None, {0: OFF_SNAPSHOT}),
    (STRAP, [STRAP, OVERNIGHT], 0, {1: OFF_OVERNIGHT}),
    (STRAP, [OVERNIGHT, STRAP], 1, {0: OFF_OVERNIGHT}),
    (STRAP, [STRAP, SNAPSHOT], 0, {1: OFF_SNAPSHOT}),  # F005 outline row 2
    (STRAP, [SNAPSHOT, STRAP], 1, {0: OFF_SNAPSHOT}),
    (STRAP, [OVERNIGHT, SNAPSHOT], None, {0: OFF_OVERNIGHT, 1: OFF_SNAPSHOT}),
    (STRAP, [SNAPSHOT, OVERNIGHT], None, {0: OFF_SNAPSHOT, 1: OFF_OVERNIGHT}),
    (STRAP, [STRAP, OVERNIGHT, SNAPSHOT], 0, {1: OFF_OVERNIGHT, 2: OFF_SNAPSHOT}),
    (STRAP, [STRAP, STRAP], 0, {1: LATER}),  # F005 outline row 1
    (STRAP, [OVERNIGHT, OVERNIGHT], None, {0: OFF_OVERNIGHT, 1: OFF_OVERNIGHT}),
    (STRAP, [SNAPSHOT, SNAPSHOT], None, {0: OFF_SNAPSHOT, 1: OFF_SNAPSHOT}),
    # -- baseline on health_api_overnight ----------------------------------
    (OVERNIGHT, [STRAP], None, {0: OFF_STRAP}),
    (OVERNIGHT, [OVERNIGHT], 0, {}),
    (OVERNIGHT, [SNAPSHOT], None, {0: OFF_SNAPSHOT}),
    (OVERNIGHT, [STRAP, OVERNIGHT], 1, {0: OFF_STRAP}),
    (OVERNIGHT, [OVERNIGHT, STRAP], 0, {1: OFF_STRAP}),
    (OVERNIGHT, [STRAP, SNAPSHOT], None, {0: OFF_STRAP, 1: OFF_SNAPSHOT}),
    (OVERNIGHT, [SNAPSHOT, STRAP], None, {0: OFF_SNAPSHOT, 1: OFF_STRAP}),
    (OVERNIGHT, [OVERNIGHT, SNAPSHOT], 0, {1: OFF_SNAPSHOT}),
    (OVERNIGHT, [SNAPSHOT, OVERNIGHT], 1, {0: OFF_SNAPSHOT}),
    (OVERNIGHT, [STRAP, OVERNIGHT, SNAPSHOT], 1, {0: OFF_STRAP, 2: OFF_SNAPSHOT}),
    (OVERNIGHT, [STRAP, STRAP], None, {0: OFF_STRAP, 1: OFF_STRAP}),
    (OVERNIGHT, [OVERNIGHT, OVERNIGHT], 0, {1: LATER}),
    (OVERNIGHT, [SNAPSHOT, SNAPSHOT], None, {0: OFF_SNAPSHOT, 1: OFF_SNAPSHOT}),
    # -- baseline on health_snapshot ---------------------------------------
    (SNAPSHOT, [STRAP], None, {0: OFF_STRAP}),
    (SNAPSHOT, [OVERNIGHT], None, {0: OFF_OVERNIGHT}),
    (SNAPSHOT, [SNAPSHOT], 0, {}),
    (SNAPSHOT, [STRAP, OVERNIGHT], None, {0: OFF_STRAP, 1: OFF_OVERNIGHT}),
    (SNAPSHOT, [OVERNIGHT, STRAP], None, {0: OFF_OVERNIGHT, 1: OFF_STRAP}),
    (SNAPSHOT, [STRAP, SNAPSHOT], 1, {0: OFF_STRAP}),  # F005 outline row 3
    (SNAPSHOT, [SNAPSHOT, STRAP], 0, {1: OFF_STRAP}),
    (SNAPSHOT, [OVERNIGHT, SNAPSHOT], 1, {0: OFF_OVERNIGHT}),
    (SNAPSHOT, [SNAPSHOT, OVERNIGHT], 0, {1: OFF_OVERNIGHT}),
    (SNAPSHOT, [STRAP, OVERNIGHT, SNAPSHOT], 2, {0: OFF_STRAP, 1: OFF_OVERNIGHT}),
    (SNAPSHOT, [STRAP, STRAP], None, {0: OFF_STRAP, 1: OFF_STRAP}),
    (SNAPSHOT, [OVERNIGHT, OVERNIGHT], None, {0: OFF_OVERNIGHT, 1: OFF_OVERNIGHT}),
    (SNAPSHOT, [SNAPSHOT, SNAPSHOT], 0, {1: LATER}),
]


def _table_id(entry) -> str:
    tier, captures, chosen, _ = entry
    return f"{tier}|{'+'.join(captures)}|{chosen}"


@pytest.mark.parametrize("entry", CONTRACT_TABLE, ids=_table_id)
def test_one_reading_per_local_day_chosen_within_the_baseline_tier_by_time(entry) -> None:
    baseline_tier, captures, chosen, reasons = entry
    day = D - timedelta(days=3)
    baseline = readings(baseline_tier, baseline_days(20))
    day_rows = [
        row(local(day, *CAPTURE_TIMES[i]), tier, 30.0 + i, f"capture-{i}") for i, tier in enumerate(captures)
    ]

    result = build(baseline + day_rows)

    assert result.tier == baseline_tier
    by_day = {reading.date: reading for reading in result.series}
    if chosen is None:
        assert day not in by_day
    else:
        assert by_day[day].session_id == f"capture-{chosen}"
    actual = {sid: reason for sid, reason in excluded_reasons(result).items() if sid.startswith("capture-")}
    assert actual == {f"capture-{i}": reason for i, reason in reasons.items()}


# ---------------------------------------------------------------------------
# local days, not UTC days
# ---------------------------------------------------------------------------


def test_days_are_the_athletes_local_days_not_utc_days() -> None:
    """F005: ``2026-01-07T17:00:00+00:00`` in Pacific/Auckland is local day
    2026-01-08. Slicing ``start_time[:10]`` gives the UTC day and is the
    defect ``athlete_timezone`` exists to prevent."""
    target = date(2026, 1, 8)
    result = build([row("2026-01-07T17:00:00+00:00", STRAP, 40.0, "evening-utc")], target=target)

    assert [reading.date for reading in result.series] == [date(2026, 1, 8)]
    assert result.timezone == "Pacific/Auckland"


def test_the_stored_offset_form_is_parsed_not_matched_against_a_suffix() -> None:
    """``+00:00`` (what the writer produces) and ``Z`` bucket identically;
    neither is recognised by string-matching its suffix."""
    target = date(2026, 1, 8)
    plus = build([row("2026-01-07T17:00:00+00:00", STRAP, 40.0)], target=target)
    zulu = build([row("2026-01-07T17:00:00Z", STRAP, 40.0)], target=target)

    assert [r.date for r in plus.series] == [r.date for r in zulu.series] == [date(2026, 1, 8)]


def test_a_naive_start_time_is_a_defect_not_a_guess() -> None:
    """``fromisoformat`` accepts a naive string and ``astimezone`` on a naive
    value silently assumes the *machine's* zone. The writer never produces
    one (``mapping.py``), so the module raises rather than bucketing it
    into whatever zone the server happens to run in."""
    with pytest.raises(ValueError) as excinfo:
        build([row("2026-01-07T17:00:00", STRAP, 40.0, "naive-row")])

    assert "naive-row" in str(excinfo.value)
    assert "naive" in str(excinfo.value).lower()


def test_bucketing_is_per_row_so_a_dst_transition_does_not_shift_a_day() -> None:
    """Pacific/Auckland leaves NZST (+12) for NZDT (+13) at 02:00 local on
    2026-09-27. A capture at 23:30 NZST on the 26th and one at 00:30 NZDT on
    the 28th straddle that change: a single precomputed offset gets one of
    them onto the wrong day whichever offset it picks.

    Measured: 2026-09-26T11:30Z is 23:30 +12 (the 26th); 2026-09-27T11:30Z is
    00:30 +13 (the 28th) but 23:30 under a stale +12 (the 27th).
    """
    target = date(2026, 9, 28)
    result = build(
        [
            row("2026-09-26T11:30:00+00:00", STRAP, 40.0, "before-dst"),
            row("2026-09-27T11:30:00+00:00", STRAP, 41.0, "after-dst"),
        ],
        target=target,
    )

    by_id = {reading.session_id: reading.date for reading in result.series}
    assert by_id == {"before-dst": date(2026, 9, 26), "after-dst": date(2026, 9, 28)}


def test_the_dst_fall_back_day_is_one_local_day_with_two_0230s() -> None:
    """On 2026-04-05 Auckland's clocks go from 03:00 NZDT back to 02:00 NZST,
    so 02:30 happens twice (2026-04-04T13:30Z and T14:30Z). Both captures are
    the same local day, and the earlier instant is the earlier capture."""
    target = date(2026, 4, 5)
    result = build(
        [
            row("2026-04-04T14:30:00+00:00", STRAP, 41.0, "second-0230"),
            row("2026-04-04T13:30:00+00:00", STRAP, 40.0, "first-0230"),
        ],
        target=target,
    )

    assert [(r.date, r.session_id) for r in result.series] == [(date(2026, 4, 5), "first-0230")]
    assert excluded_reasons(result) == {"second-0230": "same_day_later_capture"}


def test_a_half_hour_offset_zone_buckets_correctly() -> None:
    """Asia/Kolkata is UTC+5:30: 2026-03-01T18:45Z is 00:15 on the 2nd."""
    target = date(2026, 3, 2)
    result = build([row("2026-03-01T18:45:00+00:00", STRAP, 40.0)], zone=KOLKATA, target=target)

    assert [r.date for r in result.series] == [date(2026, 3, 2)]
    assert result.timezone == "Asia/Kolkata"


def test_29_february_is_an_ordinary_local_day_inside_the_windows() -> None:
    """2028 is a leap year. A capture at 07:00 local on 2028-02-29 is bucketed
    onto that day, and the closed windows around a target the following week
    are counted in calendar days across it."""
    target = date(2028, 3, 5)
    result = build([row(local(date(2028, 2, 29), 7), STRAP, 40.0, "leap-day")], target=target)

    assert [r.date for r in result.series] == [date(2028, 2, 29)]
    assert result.judged_window == (date(2028, 2, 28), date(2028, 3, 5))
    assert result.baseline_window == (target - timedelta(days=66), date(2028, 2, 27))
    assert [r.session_id for r in result.window] == ["leap-day"]


# ---------------------------------------------------------------------------
# the windows
# ---------------------------------------------------------------------------


def test_the_windows_are_closed_disjoint_local_date_intervals() -> None:
    """Baseline ``[D-66, D-7]``, judged ``[D-6, D]``; a reading on each
    boundary day lands in exactly one of them, and a reading outside both is
    excluded as ``outside_windows``."""
    rows = [
        row(local(D - timedelta(days=67), 6), STRAP, 40.0, "before-baseline"),
        row(local(D - timedelta(days=66), 6), STRAP, 40.0, "baseline-first"),
        # Two fillers so no silence exceeds 21 local days: since T092 a 58-day
        # gap between the boundary readings would (correctly) reset the baseline.
        row(local(D - timedelta(days=46), 6), STRAP, 40.0, "filler-46"),
        row(local(D - timedelta(days=26), 6), STRAP, 40.0, "filler-26"),
        row(local(D - timedelta(days=7), 6), STRAP, 40.0, "baseline-last"),
        row(local(D - timedelta(days=6), 6), STRAP, 40.0, "window-first"),
        row(local(D, 6), STRAP, 40.0, "window-last"),
        row(local(D + timedelta(days=1), 6), STRAP, 40.0, "after-target"),
    ]

    result = build(rows)

    assert result.baseline_window == (date(2026, 7, 4), date(2026, 9, 1))
    assert result.judged_window == (date(2026, 9, 2), date(2026, 9, 8))
    assert [r.session_id for r in result.baseline] == ["baseline-first", "filler-46", "filler-26", "baseline-last"]
    assert [r.session_id for r in result.window] == ["window-first", "window-last"]
    assert excluded_reasons(result) == {
        "before-baseline": "outside_windows",
        "after-target": "outside_windows",
    }
    assert not {r.session_id for r in result.baseline} & {r.session_id for r in result.window}


def test_a_future_capture_from_clock_skew_is_outside_the_windows() -> None:
    """A row timestamped after the target's local day is not a reading for
    that day -- no verdict is asserted about a day that has not happened."""
    result = build(
        readings(STRAP, baseline_days(14)) + [row(local(D + timedelta(days=30), 6), STRAP, 40.0, "skewed")]
    )

    assert excluded_reasons(result)["skewed"] == "outside_windows"
    assert "skewed" not in {r.session_id for r in result.series}


def test_no_rows_at_all_is_an_empty_series_with_no_tier() -> None:
    result = build([])

    assert result.tier is None
    assert result.series == ()
    assert result.baseline == ()
    assert result.window == ()
    assert result.excluded == ()
    assert result.target_date == D
    assert result.timezone == "Pacific/Auckland"


# ---------------------------------------------------------------------------
# the exclusion step
# ---------------------------------------------------------------------------


def test_the_pre_amendment_window_is_excluded_with_its_reason() -> None:
    """``hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL`` -- F004's
    published predicate. Such a row is a stored row, not a reading: it
    contributes to nothing and is listed as excluded naming the window."""
    pre = row(local(D - timedelta(days=3), 6), STRAP, None, "pre-amendment")
    result = build(readings(STRAP, baseline_days(14)) + [pre])

    assert excluded_reasons(result)["pre-amendment"] == "pre_amendment_window"
    assert "pre-amendment" not in {r.session_id for r in result.series}


def test_the_in_python_window_predicate_agrees_with_the_published_sql() -> None:
    """The companion of ``db.PRE_AMENDMENT_WINDOW_PREDICATE``, checked against
    the SQL itself rather than against a restated copy: every ``(tier, value)``
    combination is evaluated by SQLite under the published predicate and by
    the module's predicate, and the two selections must be identical."""
    combinations = [
        (None, None),
        (None, 41.0),
        (None, 0.0),
        (STRAP, None),
        (STRAP, 41.0),
        (STRAP, 0.0),
        (SNAPSHOT, None),
        (SNAPSHOT, 37.0),
    ]
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE sessions (id INTEGER, hrv_source_tier TEXT, resting_rmssd_ms REAL)")
        conn.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            [(i, tier, value) for i, (tier, value) in enumerate(combinations)],
        )
        selected_by_sql = {
            r[0] for r in conn.execute(f"SELECT id FROM sessions WHERE {db.PRE_AMENDMENT_WINDOW_PREDICATE}")
        }
    finally:
        conn.close()

    selected_by_python = {
        i
        for i, (tier, value) in enumerate(combinations)
        if hrv_trend.is_pre_amendment_window(row("2026-09-01T18:00:00+00:00", tier, value))
    }

    assert selected_by_sql == selected_by_python == {3, 6}


def test_a_null_tier_row_is_excluded_with_its_reason() -> None:
    """An ordinary run (tier and value both null) inside the range is listed,
    not silently absent -- ``research/00`` §1.6 wants the verdict reproducible
    from what the response reports."""
    run = row(local(D - timedelta(days=1), 17), None, None, "plain-run")
    result = build(readings(STRAP, baseline_days(14)) + [run])

    assert excluded_reasons(result)["plain-run"] == "null_tier"


def test_an_unknown_tier_string_is_excluded_not_ranked() -> None:
    """A tier the enum does not name cannot be placed in the fidelity order,
    so it is neither a baseline candidate nor merged into one -- §2.4.5's
    anti-mixing rule applied to a value the classifier never writes."""
    odd = row(local(D - timedelta(days=1), 6), "wrist_ppg_guess", 40.0, "odd-tier")
    result = build(readings(STRAP, baseline_days(14)) + [odd])

    assert excluded_reasons(result)["odd-tier"] == "unknown_tier: wrist_ppg_guess"
    assert result.tier == STRAP


@pytest.mark.parametrize(
    "value",
    [0.0, -5.0, -0.0, math.inf, -math.inf, math.nan],
    ids=["zero", "negative", "negative-zero", "inf", "-inf", "nan"],
)
def test_a_value_ln_cannot_take_is_excluded_with_its_reason(value: float) -> None:
    """F004's write-side invariant says a tiered row carries a strictly
    positive finite value, but IDEA-034 found that invariant false for two
    sprints; this module is the consumer that would evaluate ``ln`` on it.
    Flag, never hide: the row is excluded and the reason names the value, so
    a broken writer surfaces in ``excluded[]`` rather than as a 500."""
    bad = row(local(D - timedelta(days=1), 6), STRAP, value, "bad-value")
    result = build(readings(STRAP, baseline_days(14)) + [bad])

    assert excluded_reasons(result)["bad-value"] == f"unusable_value: {value!r}"
    assert "bad-value" not in {r.session_id for r in result.series}


@pytest.mark.parametrize("value", [1.0, 1e-6, 5000.0], ids=["one-ms", "tiny", "very-large"])
def test_any_positive_finite_value_is_a_reading(value: float) -> None:
    """``ln(1) == 0`` and a very large value are both defined; the module
    excludes what ``ln`` cannot take and nothing else."""
    edge = row(local(D - timedelta(days=1), 6), STRAP, value, "edge-value")
    result = build(readings(STRAP, baseline_days(14)) + [edge])

    assert "edge-value" in {r.session_id for r in result.series}


# ---------------------------------------------------------------------------
# baseline-tier resolution
# ---------------------------------------------------------------------------


def test_the_highest_tier_with_at_least_14_baseline_readings_wins() -> None:
    """Fourteen strap readings sustain a baseline against 45 snapshot ones
    -- provided the strap also covers the judged week (T093, 2026-09-10:
    this fixture originally gave the strap no week at all, which is the
    trial-then-abandon shape the amended rule refuses; it gained three
    strap readings in ``[D-6, D]``, recorded as an existing-line edit)."""
    week = days_between(D - timedelta(days=2), D)
    result = build(
        readings(STRAP, baseline_days(14)) + readings(STRAP, week) + readings(SNAPSHOT, baseline_days(45), hh=7)
    )

    assert result.tier == STRAP
    assert len(result.baseline) == 14
    assert len(result.window) == 3


def test_thirteen_strap_readings_are_not_a_candidate_even_when_the_strap_covers_the_week() -> None:
    """The 13 side of the boundary the test above pins at 14 (sprint-005
    review, S3). Thirteen strap readings in the baseline and three in the
    week, beside 45 snapshot readings that also cover the week: the strap
    is not a candidate (rule 1), so rule 2 never consults its week coverage
    and hands the baseline to the snapshot -- the only candidate that
    covers the week. Perturbation: a candidacy bound of 13, or a rule 2
    that lets any week-covering tier win without candidacy, turns this
    red on ``tier``."""
    week = days_between(D - timedelta(days=6), D)
    result = build(
        readings(STRAP, baseline_days(13))
        + readings(STRAP, week[-3:])
        + readings(SNAPSHOT, baseline_days(45), hh=7)
        + readings(SNAPSHOT, week, hh=7)
    )

    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 45
    assert len(result.window) == 7
    strap_reasons = {sid: why for sid, why in excluded_reasons(result).items() if STRAP in sid}
    assert len(strap_reasons) == 16
    assert set(strap_reasons.values()) == {"off_baseline_tier: chest_strap_raw"}


def test_an_occasional_higher_tier_capture_does_not_demote_an_established_baseline() -> None:
    """45 snapshots and one borrowed strap: the baseline stays on
    ``health_snapshot`` (n=45), and the strap reading is corroboration, listed
    as off-tier -- §3.7.3's "never merged into the same band"."""
    days = baseline_days(45)
    borrowed = row(local(days[20], 6), STRAP, 55.0, "borrowed-strap")
    result = build(readings(SNAPSHOT, days, hh=7) + [borrowed])

    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 45
    assert excluded_reasons(result)["borrowed-strap"] == "off_baseline_tier: chest_strap_raw"


def test_below_14_everywhere_the_tier_with_the_most_readings_wins_not_the_highest_present() -> None:
    """The fallback is by count, so a thin strap history does not outrank a
    fuller snapshot one merely by fidelity."""
    result = build(readings(STRAP, baseline_days(3)) + readings(SNAPSHOT, baseline_days(10), hh=7))

    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 10


def test_an_equal_count_below_14_resolves_to_the_higher_fidelity_tier() -> None:
    result = build(readings(STRAP, baseline_days(9)) + readings(SNAPSHOT, baseline_days(9), hh=7))

    assert result.tier == STRAP


def test_among_candidates_read_last_on_the_same_day_the_tie_falls_to_count_then_fidelity() -> None:
    """Rule 3's tie order among candidates (T094: recency, then count in the
    window, then fidelity), pinned on ``resolve_baseline_tier`` directly
    with an explicit ``last_read`` so the term order itself is under test.
    Two candidates, neither covering the week, both read last on the same
    day: the denser one wins even though it is the lower-fidelity tier, and
    an equal count falls to fidelity. Recency outranks both: the candidate
    read later wins on fewer readings. Perturbation: swapping the count and
    fidelity terms (``(last_read, -fidelity, count)``) or dropping the count
    term hands the first case to the strap and turns this red."""
    same_day = {STRAP: D - timedelta(days=10), SNAPSHOT: D - timedelta(days=10)}
    no_week = {STRAP: 1, SNAPSHOT: 2}

    assert hrv_trend.resolve_baseline_tier({STRAP: 14, SNAPSHOT: 20}, no_week, same_day) == SNAPSHOT
    assert hrv_trend.resolve_baseline_tier({STRAP: 20, SNAPSHOT: 20}, no_week, same_day) == STRAP

    later_snapshot = {STRAP: D - timedelta(days=10), SNAPSHOT: D - timedelta(days=9)}
    assert hrv_trend.resolve_baseline_tier({STRAP: 40, SNAPSHOT: 14}, no_week, later_snapshot) == SNAPSHOT


def test_health_api_overnight_ranks_between_the_strap_and_the_snapshot() -> None:
    """``health_api_overnight`` is in the enum but never written by the
    classifier; the ordering still ranks it. The order follows the authority
    (``research/00`` §3.3 and register row: chest-strap raw RR, then Health
    Snapshot, then Health API overnight), which is also §2.4.5's own numbered
    list."""
    assert hrv_trend.TIER_FIDELITY == (STRAP, SNAPSHOT, OVERNIGHT)

    overnight_vs_snapshot = build(
        readings(OVERNIGHT, baseline_days(14)) + readings(SNAPSHOT, baseline_days(14), hh=7)
    )
    assert overnight_vs_snapshot.tier == SNAPSHOT

    strap_vs_overnight = build(
        readings(STRAP, baseline_days(14)) + readings(OVERNIGHT, baseline_days(14), hh=7)
    )
    assert strap_vs_overnight.tier == STRAP

    overnight_alone = build(readings(OVERNIGHT, baseline_days(14)))
    assert overnight_alone.tier == OVERNIGHT
    assert len(overnight_alone.baseline) == 14


def test_tier_counts_are_taken_over_the_baseline_window_only() -> None:
    """Fourteen strap readings inside the judged week do not establish a
    strap baseline; the tier is resolved over ``[D-66, D-7]``."""
    week = [D - timedelta(days=i) for i in range(7)]
    result = build(
        readings(SNAPSHOT, baseline_days(10), hh=7) + readings(STRAP, week) + readings(STRAP, week, hh=8)
    )

    assert result.tier == SNAPSHOT


def test_with_no_baseline_readings_the_tier_falls_back_to_the_judged_week() -> None:
    """A brand-new athlete's first week has readings but no baseline. The
    series still carries them (T091's ``points[]`` shows ``ln_rmssd`` for
    a day with a reading, band or no band), resolved on the week's own
    most-read tier; the verdict for such a day is T084's ``unavailable``."""
    week = [D - timedelta(days=i) for i in range(5)]
    result = build(readings(SNAPSHOT, week, hh=7) + readings(STRAP, week[:2]))

    assert result.tier == SNAPSHOT
    assert result.baseline == ()
    assert len(result.window) == 5


# ---------------------------------------------------------------------------
# T093: the baseline tier must also cover the judged week
#
# The population between F005's two poles -- "an occasional chest-strap
# capture" (corroboration) and "a sustained switch" (tier change) -- that no
# scenario, task or probe row named (sprint-005 review, critic stage 4.6;
# F005 "Negative Class"). MIN_BASELINE_READINGS is 14 in 60 days, 1.6 a week;
# MIN_WINDOW_READINGS is 3 a week; so a tier can sustain a baseline by count
# while never sustaining a judged week. Each test here went red against the
# baseline-only rule (T093's Delivered note records the run).
# ---------------------------------------------------------------------------


def trial_then_abandon() -> list[dict]:
    """The critic's series (``CRITIC-F005.md``): a daily Health Snapshot for
    200 days ending ``D``, 38/44 ms alternating, genuinely suppressed at 25 ms
    on 2026-08-20 .. 2026-09-02; a chest strap used daily 2026-07-03 ..
    2026-07-16 and never again."""
    snapshot_days = days_between(D - timedelta(days=199), D)
    suppressed = days_between(date(2026, 8, 20), date(2026, 9, 2))
    rows = [
        row(local(day, 7), SNAPSHOT, 25.0 if day in suppressed else 38.0 + 6.0 * (i % 2))
        for i, day in enumerate(snapshot_days)
    ]
    rows += readings(STRAP, days_between(date(2026, 7, 3), date(2026, 7, 16)), 79.0)
    return rows


def test_a_higher_tier_that_does_not_cover_the_judged_week_does_not_own_the_baseline() -> None:
    """The strap trial holds 14 readings in ``[D-66, D-7]`` for every target
    from 2026-07-23 to 2026-09-07 and none in ``[D-6, D]``; the snapshot
    covers every week. The snapshot owns the baseline on every one of those
    days, the real two-week suppression is reported, and nothing resets."""
    rows = trial_then_abandon()

    for target in days_between(date(2026, 7, 23), D):
        result = build(rows, target=target)
        assert result.tier == SNAPSHOT, target
        assert result.reset_reason is None and result.reset_on is None, target
        assert len(result.window) == 7, target

    verdict = hrv_trend.judge(build(rows, target=date(2026, 9, 2)))
    assert verdict.verdict == "hrv_suppressed"
    assert verdict.below_by is not None and verdict.below_by > 0
    assert verdict.established is True


def test_a_two_day_a_week_strap_is_corroboration_not_the_baseline() -> None:
    """A daily-snapshot athlete who keeps a strap on Tuesdays and Saturdays:
    the strap sustains a baseline by count (>= 14 in 60 days) but holds only
    two readings in any week, so it can never judge one. The snapshot owns
    the baseline and every strap reading is ``off_baseline_tier``."""
    every_day = baseline_days(60) + days_between(D - timedelta(days=6), D)
    strap_days = [day for day in every_day if day.weekday() in (1, 5)]  # Tue, Sat
    in_baseline = [day for day in strap_days if day <= D - timedelta(days=7)]
    in_week = [day for day in strap_days if day > D - timedelta(days=7)]
    assert len(in_baseline) >= hrv_trend.MIN_BASELINE_READINGS
    assert len(in_week) == 2

    result = build(readings(SNAPSHOT, every_day, hh=7) + readings(STRAP, strap_days, 79.0))

    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 60
    assert len(result.window) == 7
    strap_reasons = {sid: why for sid, why in excluded_reasons(result).items() if STRAP in sid}
    assert len(strap_reasons) == len(strap_days)
    assert set(strap_reasons.values()) == {"off_baseline_tier: chest_strap_raw"}
    assert hrv_trend.judge(result).verdict == "hrv_normal"


def test_a_week_with_no_readings_of_any_tier_keeps_the_tier_stable() -> None:
    """Illness week: an established strap baseline and nothing captured in
    ``[D-6, D]``. No candidate covers the week, so the only candidate --
    the strap, the tier the athlete was read on last (T094; the densest
    under T093) -- keeps the baseline; the verdict is ``hrv_unavailable``
    with ``readings_in_window`` 0 and no reset."""
    result = build(readings(STRAP, baseline_days(60)))

    assert result.tier == STRAP
    assert len(result.baseline) == 60
    assert result.window == ()
    assert result.reset_reason is None
    verdict = hrv_trend.judge(result)
    assert verdict.verdict == "hrv_unavailable"
    assert verdict.readings_in_window == 0
    assert verdict.established is True


def test_a_thin_tier_that_alone_covers_the_week_does_not_take_the_baseline() -> None:
    """Rule 3's third trigger, "the only week-covering tier is thin"
    (sprint-005 review, S1): 60 strap readings in the baseline and none in
    the week; a snapshot device with five readings in the baseline -- below
    ``MIN_BASELINE_READINGS``, so not a candidate -- that covers all seven
    days of the week. No *candidate* covers the week, so the only
    candidate, the strap, keeps the baseline (recency and density agree
    here); the week is
    ``hrv_unavailable`` with ``readings_in_window`` 0, nothing resets, and
    every snapshot reading is ``off_baseline_tier``. Perturbation: letting
    any week-covering tier win without candidacy hands the baseline to the
    snapshot on five readings and turns this red."""
    week = days_between(D - timedelta(days=6), D)
    thin_snapshot_days = baseline_days(60)[::12]  # five of the sixty baseline days
    result = build(
        readings(STRAP, baseline_days(60)) + readings(SNAPSHOT, thin_snapshot_days + week, 38.0, hh=7)
    )

    assert result.tier == STRAP
    assert len(result.baseline) == 60
    assert result.window == ()
    assert result.reset_reason is None
    snapshot_reasons = {sid: why for sid, why in excluded_reasons(result).items() if SNAPSHOT in sid}
    assert len(snapshot_reasons) == 12
    assert set(snapshot_reasons.values()) == {"off_baseline_tier: health_snapshot"}
    verdict = hrv_trend.judge(result)
    assert verdict.verdict == "hrv_unavailable"
    assert verdict.readings_in_window == 0
    assert verdict.established is True


def oscillating_strap(target: date = D) -> list[dict]:
    """A daily snapshot from ``target-126``, plus a strap on three days of
    one week and two of the next, alternating, back to ``target-126``.
    Week ``k`` is ``[target-7k-6, target-7k]``; even weeks hold three strap
    days, odd weeks two -- so the week judged at ``target`` has three and
    the week judged at ``target-7`` has two."""
    rows = readings(SNAPSHOT, days_between(target - timedelta(days=126), target), hh=7)
    for k in range(18):
        week_first = target - timedelta(days=7 * k + 6)
        offsets = (0, 2, 4) if k % 2 == 0 else (0, 2)
        rows += readings(STRAP, [week_first + timedelta(days=o) for o in offsets], 79.0)
    return rows


def test_the_documented_cost_a_two_to_three_day_strap_alternates_the_tier_on_the_week_boundary() -> None:
    """Pinned as the accepted cost (decision log 2026-09-10; F005 "Negative
    Class"): the strap sustains a baseline in both windows, so a judged
    week that holds three strap readings is judged on the strap and one
    that holds two on the snapshot. Sampled here at ``D`` and ``D-7``; the
    name's "week boundary" is where those two samples sit, not where the
    flip lands -- the judged week slides daily, so the tier flips whenever
    the strap count in the sliding week crosses 3, and the test below walks
    the days and pins the flip day. Neither sample is a reset (the reset
    half is in ``test_hrv_trend_reset.py``)."""
    rows = oscillating_strap()

    three_strap_days = build(rows, target=D)
    two_strap_days = build(rows, target=D - timedelta(days=7))

    assert three_strap_days.tier == STRAP
    assert len(three_strap_days.window) == 3
    assert len(three_strap_days.baseline) >= hrv_trend.MIN_BASELINE_READINGS
    assert two_strap_days.tier == SNAPSHOT
    assert len(two_strap_days.window) == 7
    assert three_strap_days.reset_reason is None and two_strap_days.reset_reason is None


def test_the_tier_flips_on_the_day_the_strap_count_in_the_sliding_week_crosses_3() -> None:
    """The oscillation walked day by day (sprint-005 review, S4). The judged
    week ``[D-6, D]`` slides one day at a time, so the flip lands on the day
    the strap count inside it crosses 3, not on a week boundary. Fixture
    strap days around the walk: D-20, D-18, D-16 (three), D-13, D-11 (two),
    D-6, D-4, D-2 (three). Through D-10 the window still holds three
    (D-16, D-13, D-11); on D-9 the D-16 reading leaves and the count is two
    until D-3; on D-2 the third strap reading of this week enters and the
    strap takes the baseline -- four days after the fixture's week boundary
    ``D-6``, not on it as the ``D`` / ``D-7`` samples above might suggest.
    No day resets. Perturbation: ignoring the week keeps the strap on every
    day."""
    rows = oscillating_strap()
    targets = days_between(D - timedelta(days=13), D)
    expected = [STRAP] * 4 + [SNAPSHOT] * 7 + [STRAP] * 3

    observed = [build(rows, target=target) for target in targets]

    assert [r.tier for r in observed] == expected
    assert all(r.reset_reason is None and r.reset_on is None for r in observed)
    flips = [later for before, later in pairwise(observed) if before.tier != later.tier]
    assert [r.target_date for r in flips] == [D - timedelta(days=9), D - timedelta(days=2)]
    assert [len(r.window) for r in flips] == [7, 3]


# ---------------------------------------------------------------------------
# T094: the fallback is recency, a reset needs non-interleaved eras, and a
# candidate can be stale
#
# Sprint-005 review cycle 2 (critic stage 4.6; F005 verdict G1/G2/G6/G7)
# found four populations between the poles T093's rule named, all inside
# the region no pinned row discriminated: rule 3's "densest tier" fallback
# hands a switched athlete's thin week back to the abandoned device (G1) and
# asserts a reset on an empty week both neighbours withdraw (G2); rule 4's
# "sustained tier changed and owns the baseline" fires a phantom reset on
# alternate weeks of a young 2/3-day strap habit (G6); and rule 2's
# candidacy has no recency, so a stale trial plus three strap days re-owns
# the baseline on a two-month-old band (G7 -- the accepted, named cost:
# "stale candidacy" in F005's Negative Class). Rule 3 is now the candidate
# whose latest baseline-window reading is most recent (ties by count, then
# fidelity; no candidate at all still falls to the densest tier), and rule 4
# fires only when the eras do not interleave -- over both windows together,
# no reading of the resolved tier falls between the previous tier's first
# and last (sprint-005 review cycle 3; T094 judged the current window
# alone). Each test went red against cf48c3a (T094's Delivered note
# records the run).
# ---------------------------------------------------------------------------


def young_oscillating_strap(weeks: int = 9, target: date = D) -> list[dict]:
    """``oscillating_strap`` begun ``weeks`` weeks ago instead of 18, over a
    daily snapshot from ``target-199``: the previous window ``[D-126,
    D-67]`` is all snapshot on every walked day, which is the branch the
    18-week fixture sits beside."""
    rows = readings(SNAPSHOT, days_between(target - timedelta(days=199), target), hh=7)
    for k in range(weeks):
        week_first = target - timedelta(days=7 * k + 6)
        offsets = (0, 2, 4) if k % 2 == 0 else (0, 2)
        rows += readings(STRAP, [week_first + timedelta(days=o) for o in offsets], 79.0)
    return rows


def test_a_young_oscillation_habit_alternates_the_tier_and_never_resets() -> None:
    """G6. The habit is nine weeks old, so the strap sustains the current
    window (15..20 readings on the walk) but not the previous one, which
    is snapshot-only: the sustained tier *did* change. The tier still
    alternates on the days the strap count in the sliding week crosses 3,
    exactly as the 18-week walk pins, and no day is a reset -- the snapshot
    readings inside the window interleave with the strap's, so no era
    ended before the other began. Perturbation (compare the sustained
    tiers alone, cf48c3a): ``tier_change`` on every strap day."""
    rows = young_oscillating_strap()
    strap_days = [hrv_trend.local_day(r["session_id"], r["start_time"], AUCKLAND)[0] for r in rows if r["hrv_source_tier"] == STRAP]
    assert min(strap_days) > D - timedelta(days=67), "the previous window must hold no strap reading"

    targets = days_between(D - timedelta(days=14), D)
    observed = [build(rows, target=target) for target in targets]

    assert [r.tier for r in observed] == [STRAP] * 5 + [SNAPSHOT] * 7 + [STRAP] * 3
    assert all(len(r.baseline) >= hrv_trend.MIN_BASELINE_READINGS for r in observed if r.tier == STRAP)
    assert all(r.reset_reason is None and r.reset_on is None for r in observed), [
        (r.target_date, r.reset_reason) for r in observed if r.reset_reason
    ]


def test_stale_candidacy_a_july_trial_plus_three_strap_days_owns_the_week_on_the_july_band() -> None:
    """G7, pinned as the named cost (F005 Negative Class, "stale candidacy";
    the candidacy question is an IDEA, not this task). The abandoned July
    trial is still a candidate on 2026-09-06 and 09-07 (its 14 readings sit
    inside ``[D-66, D-7]``), and three strap days this week cover the week,
    so rule 2 hands the strap the baseline and the week is judged against
    a band whose every reading is from July. No reset: the snapshot
    readings interleave with the trial. On 09-08 the first trial day ages
    out, the strap holds 13, and the snapshot takes the baseline back."""
    this_week = [D - timedelta(days=6), D - timedelta(days=4), D - timedelta(days=2)]
    rows = trial_then_abandon() + readings(STRAP, this_week, 79.0)

    for target in (date(2026, 9, 6), date(2026, 9, 7)):
        result = build(rows, target=target)
        assert result.tier == STRAP, target
        assert len(result.baseline) == 14, target
        assert max(r.date for r in result.baseline) == date(2026, 7, 16), target
        assert len(result.window) == 3, target
        assert result.reset_reason is None and result.reset_on is None, target
        verdict = hrv_trend.judge(result)
        assert verdict.established is True and verdict.verdict == "hrv_normal", target

    back = build(rows, target=date(2026, 9, 8))
    assert back.tier == SNAPSHOT
    assert back.reset_reason is None and back.reset_on is None


def test_the_fallback_keeps_the_device_the_athlete_used_last_through_a_thin_week() -> None:
    """G1 (F005 "The fallback keeps the device the athlete used last"). A
    daily snapshot to D-26, a daily strap D-25..D-8, nothing since. Both
    tiers are candidates; from D-3 no candidate covers the week (two strap
    readings, then one, then none). The strap, read last at D-8, keeps the
    baseline on every day D-5..D with the same ``tier_change`` on D-25,
    and the thin days read ``hrv_unavailable``. Perturbation (densest
    fallback, cf48c3a): D-3 reverts to the snapshot on 44 readings,
    withdraws the reset, and reports ``readings_in_window`` 0."""
    rows = readings(SNAPSHOT, days_between(D - timedelta(days=126), D - timedelta(days=26)), hh=7)
    rows += readings(STRAP, days_between(D - timedelta(days=25), D - timedelta(days=8)), 79.0)

    for target in days_between(D - timedelta(days=5), D):
        result = build(rows, target=target)
        assert result.tier == STRAP, target
        assert result.reset_reason == "tier_change", target
        assert result.reset_on == D - timedelta(days=25), target
        assert result.baseline_window == (D - timedelta(days=25), target - timedelta(days=7)), target

    thin = [hrv_trend.judge(build(rows, target=D - timedelta(days=n))) for n in (3, 2, 1, 0)]
    assert [v.readings_in_window for v in thin] == [2, 1, 0, 0]
    assert {v.verdict for v in thin} == {"hrv_unavailable"}
    assert all(v.established for v in thin)


def test_an_empty_week_on_a_mixed_baseline_keeps_the_tier_the_athlete_used_last_and_does_not_reset() -> None:
    """G2. A snapshot era through the previous window, a strap era
    D-66..D-34 (short enough never to sustain the previous window on the
    three targets -- that flip is rule 4(b)'s, pinned in the reset suite),
    the snapshot again D-33..D-7, and nothing in ``[D-6, D]``. At D both
    tiers are candidates (33 strap, 27 snapshot) and neither covers the
    empty week; the snapshot was read last, so it keeps the baseline it
    already held at D-7 and will hold at D+7, with no reset and
    ``hrv_unavailable``. Perturbation (densest fallback, cf48c3a): the
    empty week alone flips to the strap and asserts a ``tier_change`` both
    neighbours withdraw."""
    rows = readings(SNAPSHOT, days_between(D - timedelta(days=126), D - timedelta(days=67)), hh=7)
    rows += readings(STRAP, days_between(D - timedelta(days=66), D - timedelta(days=34)), 79.0)
    rows += readings(SNAPSHOT, days_between(D - timedelta(days=33), D - timedelta(days=7)), hh=7)

    before, empty, after = (build(rows, target=D + timedelta(days=n)) for n in (-7, 0, 7))

    strap_in_window = [r for r in empty.readings if r.tier == STRAP and r.date <= D - timedelta(days=7)]
    assert len(strap_in_window) == 33, "the strap still sustains the window"
    assert (before.tier, empty.tier, after.tier) == (SNAPSHOT, SNAPSHOT, SNAPSHOT)
    assert all(r.reset_reason is None and r.reset_on is None for r in (before, empty, after))
    assert empty.window == ()
    verdict = hrv_trend.judge(empty)
    assert verdict.verdict == "hrv_unavailable" and verdict.readings_in_window == 0
    assert len(empty.baseline) == 27


def test_rule_3_reads_the_day_each_candidate_was_read_last_not_first_or_most() -> None:
    """Rule 3's direction, reached through ``build_series`` (sprint-005
    review cycle 3, M4: with ``_last_read`` returning each tier's
    *earliest* day, none of the 72 pure tests went red; the only term-order
    pin drives ``resolve_baseline_tier`` with an explicit dict). A strap on
    every fourth day ``D-64``..``D-8`` (15 readings) and a daily snapshot
    ``D-50``..``D-10`` (41), nothing in ``[D-6, D]``: both are candidates
    and neither covers the week. Read last -> the strap (``D-8`` after
    ``D-10``); read first (``D-64`` before ``D-50``) or densest (15
    against 41) -> the snapshot. The week is empty, so the verdict is
    ``hrv_unavailable`` on the strap's 15. No reset: the previous window
    ``[D-126, D-67]`` holds nothing, so no tier sustained it and rule 4(b)
    has nothing to differ from -- the strap baseline is the athlete's
    first established one, not a change. Perturbation (``_last_read``
    returning the earliest day per tier): ``health_snapshot`` -- red."""
    strap_days = days_between(D - timedelta(days=64), D - timedelta(days=8))[::4]
    snapshot_days = days_between(D - timedelta(days=50), D - timedelta(days=10))
    rows = readings(STRAP, strap_days, 79.0) + readings(SNAPSHOT, snapshot_days, hh=7)

    result = build(rows)

    assert (len(strap_days), len(snapshot_days)) == (15, 41)
    assert (strap_days[-1], snapshot_days[-1]) == (D - timedelta(days=8), D - timedelta(days=10))
    assert result.tier == STRAP
    assert len(result.baseline) == 15
    assert result.window == ()
    assert hrv_trend.judge(result).verdict == "hrv_unavailable"
    assert result.reset_reason is None and result.reset_on is None


# ---------------------------------------------------------------------------
# the collapse
# ---------------------------------------------------------------------------


def test_the_series_is_one_reading_per_local_day_in_date_order() -> None:
    days = baseline_days(14)
    extra = [row(local(days[3], 9), STRAP, 45.0, "later-same-day")]
    shuffled = list(reversed(readings(STRAP, days))) + extra

    result = build(shuffled)

    assert [r.date for r in result.series] == days
    assert len({r.date for r in result.series}) == len(result.series)
    assert excluded_reasons(result) == {"later-same-day": "same_day_later_capture"}


def test_two_devices_at_the_same_instant_collapse_deterministically() -> None:
    """``UNIQUE (source_device, start_time)`` allows two devices to share a
    ``start_time``. The tie is broken on ``session_id`` so the same rows
    always yield the same series."""
    day = D - timedelta(days=1)
    twins = [
        row(local(day, 6), STRAP, 40.0, "device-b"),
        row(local(day, 6), STRAP, 41.0, "device-a"),
    ]

    forward = build(readings(STRAP, baseline_days(14)) + twins)
    backward = build(readings(STRAP, baseline_days(14)) + twins[::-1])

    chosen = [r.session_id for r in forward.window]
    assert chosen == [r.session_id for r in backward.window] == ["device-a"]
    assert excluded_reasons(forward)["device-b"] == "same_day_later_capture"


def test_readings_carry_what_the_response_needs_to_be_reproducible() -> None:
    day = D - timedelta(days=1)
    result = build(readings(STRAP, baseline_days(14)) + [row(local(day, 6, 5), STRAP, 41.2, "today")])

    (reading,) = result.window
    assert reading.session_id == "today"
    assert reading.date == day
    assert reading.tier == STRAP
    assert reading.rmssd_ms == 41.2
    assert reading.start_time == datetime.fromisoformat(local(day, 6, 5))
    assert reading.start_time.tzinfo is not None


def test_all_post_exclusion_readings_of_every_tier_are_exposed_for_the_gap_rule() -> None:
    """T092 measures the coverage gap on the *post-exclusion* series -- every
    tier, before the tier filter -- so the result carries that set too."""
    day = D - timedelta(days=2)
    result = build(
        readings(STRAP, baseline_days(14))
        + [row(local(day, 6), SNAPSHOT, 38.0, "off-tier"), row(local(day, 7), None, None, "run")]
    )

    assert {r.session_id for r in result.readings} == {r.session_id for r in result.series} | {"off-tier"}
    assert "run" not in {r.session_id for r in result.readings}


# ---------------------------------------------------------------------------
# what the module does not do
# ---------------------------------------------------------------------------


def _imports_of(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imported.add(module_name)
            imported.update(f"{module_name}.{alias.name}" for alias in node.names)
    return imported


def test_the_module_is_pure() -> None:
    """No ``config``, ``db`` or ``fastapi`` import -- the seam ``pipeline.py``
    documents for ``hrv_classification``: it is what lets these tests drive
    the computation with dicts and no database. Asserted over the import
    graph, not the source text, so the docstring may name ``db`` on purpose."""
    imported = _imports_of(hrv_trend)

    assert not any(name.startswith("fastapi") for name in imported), imported
    assert not any(name.endswith((".config", ".db")) or name in {"config", "db"} for name in imported), (
        imported
    )


def test_the_module_does_not_re_gate_on_rr_valid_fraction() -> None:
    """A row carrying a tier, a reading *and* a sub-threshold
    ``rr_valid_fraction`` is a reading here: ingestion discharged the gate
    (such a row cannot exist post-amendment), and re-applying it would
    describe a row this series never sees. This test goes red if anyone adds
    the gate."""
    gated_looking = {
        **row(local(D - timedelta(days=1), 6), STRAP, 40.0, "low-fraction"),
        "rr_valid_fraction": 0.1,
    }
    result = build(readings(STRAP, baseline_days(14)) + [gated_looking])

    assert "low-fraction" in {r.session_id for r in result.series}
    assert "rr_valid_fraction" not in Path(hrv_trend.__file__).read_text(encoding="utf-8")


def _strap_beats(n: int = 6) -> list[RRInterval]:
    values = [800.0, 900.0, 850.0, 950.0, 870.0, 920.0]
    return [
        RRInterval(seq=i, rr_ms=values[i % len(values)], rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(n)
    ]


def test_a_capture_that_failed_f004s_quality_gates_is_already_absent(synthetic, classified) -> None:
    """Driven through the real ``mapping -> classify -> db.persist ->
    db.read_hrv_rows`` chain: a declared strap capture whose beat stream
    survived artefact filtering at only 50% carries no tier and no value, so
    the series excludes it as ``null_tier`` without ever reading
    ``rr_valid_fraction`` -- which ``read_hrv_rows`` does not even select.
    """
    profile = "HRV Snapshot"
    good_at = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)  # 06:00 on 2026-09-02 in Auckland
    gated_at = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)  # 06:00 on 2026-09-03

    def declared(at):
        return synthetic(total_timer_time=150.0, avg_heart_rate=60, sport_profile_name=profile, start_time=at)

    good = classified(declared(good_at), _strap_beats(), 1.0, [profile])
    gated = classified(declared(gated_at), _strap_beats(), 0.5, [profile])
    assert good.hrv_source_tier == STRAP and good.resting_rmssd_ms > 0
    assert gated.hrv_source_tier is None and gated.resting_rmssd_ms is None
    assert gated.rr_valid_fraction == 0.5, "the gate's input is on the row; the series never reads it"

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        for session in (good, gated):
            db.persist(conn, session, [], _strap_beats(), {})
        rows = db.read_hrv_rows(
            conn,
            (datetime(2026, 7, 4, tzinfo=UTC) - timedelta(hours=26)).isoformat(),
            (datetime(2026, 9, 8, tzinfo=UTC) + timedelta(hours=26)).isoformat(),
        )
    finally:
        conn.close()

    result = build(rows)

    assert [r.session_id for r in result.series] == [good.session_id]
    assert result.series[0].date == date(2026, 9, 2)
    assert excluded_reasons(result) == {gated.session_id: "null_tier"}
