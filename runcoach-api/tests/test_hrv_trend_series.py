"""T083 -- ``metrics/hrv_trend.py``: local-day bucketing and series construction (F005).

The pure half of the trend: stored rows in, a clean one-reading-per-local-day
dataset per source tier out (N datasets since F006/T151; the pins below read one
through T151's bridge). Nothing here touches the band or the verdict
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
import dataclasses
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
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


def build(rows, zone: ZoneInfo = AUCKLAND, target: date = D) -> hrv_trend.SingleDatasetView:
    """The series through F006's selection (T155): the selected dataset --
    the highest-fidelity judgeable one not skipped for baseline-window
    staleness, or the presentation fallback when none is judgeable -- on
    the F005 series shape, so every pin below reads what it read before the
    N-way partition. The pins on the partition and on the selection itself
    read ``hrv_trend.build_series`` / ``select_dataset`` directly (end of
    file)."""
    return hrv_trend.selected_view(hrv_trend.build_series(rows, zone, target))


def excluded_reasons(result: hrv_trend.SingleDatasetView) -> dict[str, str]:
    return {entry.session_id: entry.reason for entry in result.excluded}


def in_dataset(result: hrv_trend.SingleDatasetView, tier: str) -> set[str]:
    """The session ids of ``tier``'s own dataset's series -- where a reading
    of a tier other than the presented one lives under F006's per-dataset
    partition (T152, AC15), instead of in ``excluded``."""
    return {r.session_id for d in result.datasets if d.tier == tier for r in d.series}


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
    # F006 (T152): the strap capture feeds the strap's own dataset; shipped
    # F005 listed it ``off_baseline_tier: chest_strap_raw``.
    assert "strap-first" not in excluded_reasons(result)
    assert "strap-first" in in_dataset(result, STRAP)


# ---------------------------------------------------------------------------
# the contract table: {baseline tier} x {captures present that day}
#
# Authored before the implementation from the spec's rule. Each row is
# (baseline tier, captures in time order, index of the chosen capture or
# None, {index: disposition} for every other capture). Capture times are
# 06:05, 06:12, 07:40 in list order, so "earliest" is list order.
#
# Re-derived under F006's per-dataset partition (T152, AC3/AC4/AC15). A
# capture of another tier is no longer *excluded* from anything: it feeds
# that tier's own dataset (the ``FEEDS_*`` dispositions, asserted as
# membership of that dataset's series and absence from ``excluded``), and
# a second capture of that other tier on the same day is that dataset's
# own ``same_day_later_capture``. Shipped F005 listed every such capture
# ``off_baseline_tier: <tier>``; the chosen index is unchanged on every row.
# ---------------------------------------------------------------------------

FEEDS_STRAP = "feeds: chest_strap_raw"
FEEDS_OVERNIGHT = "feeds: health_api_overnight"
FEEDS_SNAPSHOT = "feeds: health_snapshot"
LATER = "same_day_later_capture"

CAPTURE_TIMES = ((6, 5), (6, 12), (7, 40))

CONTRACT_TABLE = [
    # -- baseline on chest_strap_raw ---------------------------------------
    (STRAP, [STRAP], 0, {}),
    (STRAP, [OVERNIGHT], None, {0: FEEDS_OVERNIGHT}),
    (STRAP, [SNAPSHOT], None, {0: FEEDS_SNAPSHOT}),
    (STRAP, [STRAP, OVERNIGHT], 0, {1: FEEDS_OVERNIGHT}),
    (STRAP, [OVERNIGHT, STRAP], 1, {0: FEEDS_OVERNIGHT}),
    (STRAP, [STRAP, SNAPSHOT], 0, {1: FEEDS_SNAPSHOT}),  # F005 outline row 2
    (STRAP, [SNAPSHOT, STRAP], 1, {0: FEEDS_SNAPSHOT}),
    (STRAP, [OVERNIGHT, SNAPSHOT], None, {0: FEEDS_OVERNIGHT, 1: FEEDS_SNAPSHOT}),
    (STRAP, [SNAPSHOT, OVERNIGHT], None, {0: FEEDS_SNAPSHOT, 1: FEEDS_OVERNIGHT}),
    (STRAP, [STRAP, OVERNIGHT, SNAPSHOT], 0, {1: FEEDS_OVERNIGHT, 2: FEEDS_SNAPSHOT}),
    (STRAP, [STRAP, STRAP], 0, {1: LATER}),  # F005 outline row 1
    (STRAP, [OVERNIGHT, OVERNIGHT], None, {0: FEEDS_OVERNIGHT, 1: LATER}),
    (STRAP, [SNAPSHOT, SNAPSHOT], None, {0: FEEDS_SNAPSHOT, 1: LATER}),
    # -- baseline on health_api_overnight ----------------------------------
    (OVERNIGHT, [STRAP], None, {0: FEEDS_STRAP}),
    (OVERNIGHT, [OVERNIGHT], 0, {}),
    (OVERNIGHT, [SNAPSHOT], None, {0: FEEDS_SNAPSHOT}),
    (OVERNIGHT, [STRAP, OVERNIGHT], 1, {0: FEEDS_STRAP}),
    (OVERNIGHT, [OVERNIGHT, STRAP], 0, {1: FEEDS_STRAP}),
    (OVERNIGHT, [STRAP, SNAPSHOT], None, {0: FEEDS_STRAP, 1: FEEDS_SNAPSHOT}),
    (OVERNIGHT, [SNAPSHOT, STRAP], None, {0: FEEDS_SNAPSHOT, 1: FEEDS_STRAP}),
    (OVERNIGHT, [OVERNIGHT, SNAPSHOT], 0, {1: FEEDS_SNAPSHOT}),
    (OVERNIGHT, [SNAPSHOT, OVERNIGHT], 1, {0: FEEDS_SNAPSHOT}),
    (OVERNIGHT, [STRAP, OVERNIGHT, SNAPSHOT], 1, {0: FEEDS_STRAP, 2: FEEDS_SNAPSHOT}),
    (OVERNIGHT, [STRAP, STRAP], None, {0: FEEDS_STRAP, 1: LATER}),
    (OVERNIGHT, [OVERNIGHT, OVERNIGHT], 0, {1: LATER}),
    (OVERNIGHT, [SNAPSHOT, SNAPSHOT], None, {0: FEEDS_SNAPSHOT, 1: LATER}),
    # -- baseline on health_snapshot ---------------------------------------
    (SNAPSHOT, [STRAP], None, {0: FEEDS_STRAP}),
    (SNAPSHOT, [OVERNIGHT], None, {0: FEEDS_OVERNIGHT}),
    (SNAPSHOT, [SNAPSHOT], 0, {}),
    (SNAPSHOT, [STRAP, OVERNIGHT], None, {0: FEEDS_STRAP, 1: FEEDS_OVERNIGHT}),
    (SNAPSHOT, [OVERNIGHT, STRAP], None, {0: FEEDS_OVERNIGHT, 1: FEEDS_STRAP}),
    (SNAPSHOT, [STRAP, SNAPSHOT], 1, {0: FEEDS_STRAP}),  # F005 outline row 3
    (SNAPSHOT, [SNAPSHOT, STRAP], 0, {1: FEEDS_STRAP}),
    (SNAPSHOT, [OVERNIGHT, SNAPSHOT], 1, {0: FEEDS_OVERNIGHT}),
    (SNAPSHOT, [SNAPSHOT, OVERNIGHT], 0, {1: FEEDS_OVERNIGHT}),
    (SNAPSHOT, [STRAP, OVERNIGHT, SNAPSHOT], 2, {0: FEEDS_STRAP, 1: FEEDS_OVERNIGHT}),
    (SNAPSHOT, [STRAP, STRAP], None, {0: FEEDS_STRAP, 1: LATER}),
    (SNAPSHOT, [OVERNIGHT, OVERNIGHT], None, {0: FEEDS_OVERNIGHT, 1: LATER}),
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
    fed = {f"capture-{i}": reason for i, reason in reasons.items() if reason.startswith("feeds: ")}
    for session_id, disposition in fed.items():
        assert session_id in in_dataset(result, disposition.removeprefix("feeds: ")), (session_id, disposition)
    actual = {sid: reason for sid, reason in excluded_reasons(result).items() if sid.startswith("capture-")}
    assert actual == {f"capture-{i}": reason for i, reason in reasons.items() if not reason.startswith("feeds: ")}


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
    not silently absent -- ``research/00`` PRIN-12 wants the verdict reproducible by
    hand from its response, less its OPEN exceptions PRIN-24 and PRIN-27."""
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
    # F006 (T152): the 16 strap readings are the strap's own dataset, not
    # exclusions; shipped F005 listed all 16 ``off_baseline_tier``.
    assert not any(STRAP in sid for sid in excluded_reasons(result))
    assert len(in_dataset(result, STRAP)) == 16


def test_an_occasional_higher_tier_capture_does_not_demote_an_established_baseline() -> None:
    """45 snapshots and one borrowed strap: the baseline stays on
    ``health_snapshot`` (n=45), and the strap reading is corroboration, listed
    as off-tier -- spec/03 §3.7.3's "never merged into the same band"."""
    days = baseline_days(45)
    borrowed = row(local(days[20], 6), STRAP, 55.0, "borrowed-strap")
    result = build(readings(SNAPSHOT, days, hh=7) + [borrowed])

    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 45
    # F006 (T152): the borrowed strap reading is a one-reading strap dataset
    # (n 1, no band), not an exclusion; shipped F005 listed it off-tier.
    assert "borrowed-strap" not in excluded_reasons(result)
    assert in_dataset(result, STRAP) == {"borrowed-strap"}


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

    **Re-pointed, not deleted (T160, F006 AC18).** What F006 retires is this
    function's *role* -- "one tier owns the only band", the cross-tier
    arbitration ``build_series`` no longer asks it for at all
    (``test_hrv_tier_change_per_dataset.py::test_the_one_call_site_is_inside_the_per_dataset_loop_and_hands_it_the_loops_tier``
    pins the absent call; the three-valued pin for the retirement is
    ``test_hrv_three_valued_retirements.py``). The *tie order asserted here*
    is retained and redeployed: ``_presentation_fallback`` (AC9, T156) is
    F005's rule 3 restated over datasets -- the established dataset read last,
    ties by n then fidelity -- and it is pinned term by term at dataset scope
    by ``test_the_fallback_presents_the_established_dataset_read_last_ties_by_n_then_fidelity``
    below. This pin stays because it is the only one that drives the three
    terms through an explicit ``last_read`` dict rather than through a
    corpus, and because a deleted pin is indistinguishable from a pin that
    never existed.
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
    (``research/00`` HRV-01 and register row: chest-strap raw RR, then Health
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
    the baseline and every strap reading is in the strap's own dataset
    (shipped F005 listed each ``off_baseline_tier``; T152)."""
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
    # F006 (T152): every strap reading is in the strap's own dataset --
    # established, but never judgeable on two week days -- and none is
    # excluded; shipped F005 listed them all ``off_baseline_tier``.
    assert not any(STRAP in sid for sid in excluded_reasons(result))
    assert len(in_dataset(result, STRAP)) == len(strap_days)
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
    every snapshot reading is in the snapshot's own dataset (shipped F005
    listed each ``off_baseline_tier``; T152). Perturbation: letting
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
    # F006 (T152): the twelve snapshot readings are the snapshot's own
    # dataset (n 5, unestablished), none excluded; shipped F005 listed all
    # twelve ``off_baseline_tier``.
    assert not any(SNAPSHOT in sid for sid in excluded_reasons(result))
    assert len(in_dataset(result, SNAPSHOT)) == 12
    verdict = hrv_trend.judge(result)
    assert verdict.verdict == "hrv_unavailable"
    assert verdict.readings_in_window == 0
    assert verdict.established is True


def oscillating_strap(target: date = D, weeks: int = 18, snapshot_days: int = 126) -> list[dict]:
    """A daily snapshot from ``target-snapshot_days``, plus a strap on three
    days of one week and two of the next, alternating, for ``weeks`` weeks
    -- by default back to ``target-126``. Week ``k`` is
    ``[target-7k-6, target-7k]``; even weeks hold three strap days, odd
    weeks two -- so the week judged at ``target`` has three and the week
    judged at ``target-7`` has two."""
    rows = readings(SNAPSHOT, days_between(target - timedelta(days=snapshot_days), target), hh=7)
    for k in range(weeks):
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
# candidacy had no recency at all, so a stale trial plus three strap days
# re-owned the baseline on a two-month-old band (G7 -- named in F005's
# Negative Class as "stale candidacy" and accepted as a cost in cycle 2,
# re-opened in cycle 6 by T110 once the forbidden error direction was
# named, and **closed by change** in cycle 7 by T117, which gave rule 1
# the relative recency condition ``RECENCY_TOLERANCE_DAYS``; the two
# tests below carry the new expectation and the constant's brackets).
# Rule 3 is now the candidate
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
    return oscillating_strap(target, weeks, snapshot_days=199)


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
    strap_days = [
        hrv_trend.local_day(r["session_id"], r["start_time"], AUCKLAND)[0]
        for r in rows
        if r["hrv_source_tier"] == STRAP
    ]
    assert min(strap_days) > D - timedelta(days=67), "the previous window must hold no strap reading"

    targets = days_between(D - timedelta(days=14), D)
    observed = [build(rows, target=target) for target in targets]

    assert [r.tier for r in observed] == [STRAP] * 5 + [SNAPSHOT] * 7 + [STRAP] * 3
    assert all(len(r.baseline) >= hrv_trend.MIN_BASELINE_READINGS for r in observed if r.tier == STRAP)
    assert all(r.reset_reason is None and r.reset_on is None for r in observed), [
        (r.target_date, r.reset_reason) for r in observed if r.reset_reason
    ]


def test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band() -> None:
    """G7, **closed by T117** (IDEA-064; user decision 2026-09-15) -- the
    series is the reproduction and is kept verbatim; only the expectation
    moved. The abandoned July trial still holds its 14 days inside
    ``[D-66, D-7]`` on 2026-09-06 and 09-07, and three strap days still
    cover each of those weeks, so rules 1 and 2 alone would hand the strap
    the baseline and judge the week against a band whose every reading is
    seven weeks old. Rule 1's recency condition refuses it: the strap was
    last read on 2026-07-16 and the snapshot -- the other candidate, and
    the one read most recently -- on ``D-7``, 45 and 46 days later, both
    beyond ``RECENCY_TOLERANCE_DAYS``. The strap is struck from the
    candidate set before rule 2 is asked, the snapshot keeps its own
    60-day band on all three days, and the flip the athlete used to see on
    09-06 and back on 09-08 does not happen.

    **What moves for the athlete, measured 2026-09-15.** On 2026-09-06 the
    verdict changes from ``hrv_normal`` to ``hrv_suppressed``: the week
    holds three of the series' fourteen genuinely suppressed days
    (08-31..09-02 at 25 ms) and the snapshot's own band calls them what
    they are, where the July strap band called the same week normal. That
    is exactly the forbidden direction IDEA-064 named -- a genuinely
    suppressed week reading normal on a stale band -- and this assertion
    is where it is now enforced. On 09-07 the verdict stays ``hrv_normal``,
    for a different and correct reason: only two suppressed days remain in
    that week, 7-day mean 3.5598 against the snapshot band's ``lo``
    3.5079, so the athlete has recovered. Both days are now judged against
    the 60-day snapshot window ending ``D-7``, not against 14 July strap
    readings.

    Before T117 the first two targets read ``tier == STRAP``, ``len(baseline)
    == 14``, ``max(baseline day) == 2026-07-16`` and ``hrv_normal`` on a
    band built entirely in July; that is what this test asserted, and it is
    what goes red if the recency filter in ``resolve_baseline_tier`` is
    deleted. The failure modes this walk separates, each run: the filter
    absent (09-06/09-07 revert to the strap); the filter applied with the
    comparison reversed, so the *most* recent candidate is struck (09-08
    loses the snapshot); and the tolerance widened to 45 days (09-06
    reverts alone at 45, 09-07 following at 46 -- 09-06's gap is 45 and
    09-07's is 46, stated correctly two paragraphs above, so the *smaller*
    gap is re-admitted first; measured 2026-09-15, T121, which found the two
    dates swapped here and "past 46" written for "to 45")."""
    this_week = [D - timedelta(days=6), D - timedelta(days=4), D - timedelta(days=2)]
    rows = trial_then_abandon() + readings(STRAP, this_week, 79.0)

    verdicts = {}
    for target in (date(2026, 9, 6), date(2026, 9, 7)):
        result = build(rows, target=target)
        in_baseline = [r for r in result.readings if r.date <= target - timedelta(days=7)]
        strap_days = {r.date for r in in_baseline if r.tier == STRAP}
        snapshot_last = max(r.date for r in in_baseline if r.tier == SNAPSHOT)
        assert len(strap_days) == 14, target
        assert max(strap_days) == date(2026, 7, 16), target
        assert (snapshot_last - max(strap_days)).days > hrv_trend.RECENCY_TOLERANCE_DAYS, target
        week = [r for r in result.readings if r.date > target - timedelta(days=7)]
        assert len({r.date for r in week if r.tier == STRAP}) == 3, target

        assert result.tier == SNAPSHOT, target
        assert len(result.baseline) == 60, target
        assert max(r.date for r in result.baseline) == target - timedelta(days=7), target
        assert result.reset_reason is None and result.reset_on is None, target
        assert len(result.window) == 7, target
        verdict = hrv_trend.judge(result)
        assert verdict.established is True, target
        verdicts[target] = verdict.verdict

    assert verdicts == {date(2026, 9, 6): "hrv_suppressed", date(2026, 9, 7): "hrv_normal"}

    back = build(rows, target=date(2026, 9, 8))
    assert back.tier == SNAPSHOT
    assert back.reset_reason is None and back.reset_on is None


def stale_trial_gap(gap: int, target: date = D) -> list[dict]:
    """A daily snapshot over ``[target-199, target]``, a 14-day strap trial
    whose last day sits ``gap`` days before the snapshot's last day in the
    baseline window ``target-7``, and three strap days in the judged week.

    The strap is a candidate by count and covers the week on every ``gap``
    the helper is called with, so the only thing that separates the walk's
    rows is rule 1's recency condition.

    ``gap`` is in the unit rule 1's gate compares -- a difference between two
    ``last_read`` days -- which is **not** the unit ``_silence_between``
    reports: a ``gap`` of ``g`` is a silence of ``g - 1`` whole days. Read the
    walk's rows against ``GAP_RESET_DAYS`` with that in hand (T121)."""
    end = target - timedelta(days=7) - timedelta(days=gap)
    rows = readings(SNAPSHOT, days_between(target - timedelta(days=199), target), hh=7)
    rows += readings(STRAP, days_between(end - timedelta(days=13), end), 79.0)
    week_days = [target - timedelta(days=6), target - timedelta(days=4), target - timedelta(days=2)]
    rows += readings(STRAP, week_days, 79.0)
    return rows


#: The ``gap`` rows both recency tests below walk, in one name so the onset
#: pin cannot fall out of step with the walk it describes (review cycle 8,
#: ``acfebae``).
RECENCY_WALK = (hrv_trend.GAP_RESET_DAYS, hrv_trend.GAP_RESET_DAYS + 1, 27, 28, 29)

#: What the walk reports at the shipped tolerance, per row: ``(tier, baseline
#: n, reset_reason)``. Both tests below read it -- the walk test as its
#: expectations, the onset test as the external literal its matrix's shipped
#: column is checked against. The onset test's own ``reddens_at`` cannot check
#: that column: it is defined against it, so it reports green over it whatever
#: ``build`` does (review cycle 8, iteration 3).
RECENCY_WALK_AT_SHIPPED = {
    hrv_trend.GAP_RESET_DAYS: (STRAP, 14, None),
    # The seam row: a silence of exactly GAP_RESET_DAYS whole days, which the
    # coverage-gap rule does not call a break, so candidacy may not strike it.
    hrv_trend.GAP_RESET_DAYS + 1: (STRAP, 14, None),
    27: (STRAP, 14, None),
    28: (STRAP, 14, None),
    29: (SNAPSHOT, 60, None),
}

#: The judged-week days each of the two possible outcomes feeds into
#: ``ln_rmssd_7d_mean``. The fixture gives the strap exactly three week days
#: and the snapshot all seven, so these two tuples are the whole reachable
#: population of "which readings the athlete's verdict was computed from".
RECENCY_TRIAL_WEEK_DAYS = (D - timedelta(days=6), D - timedelta(days=4), D - timedelta(days=2))
RECENCY_SNAPSHOT_WEEK_DAYS = tuple(days_between(D - timedelta(days=6), D))

#: **The consequence of the gate, which nothing in this file asserted before
#: T127.** ``(verdict, readings_in_window, the days that fed
#: ``ln_rmssd_7d_mean``, that mean)`` -- the two rows the fixture can produce,
#: derived by hand from it rather than captured from a run. Every strap
#: reading is 79.0 and every snapshot reading 40.0, and each tier's readings
#: are identical to each other, so whichever tier survives the gate its
#: baseline SD is zero, its band is floored on its own mean, and the judged
#: week's mean sits exactly on it: ``hrv_normal`` either way, on an
#: established baseline (14 or 60), from three week days or from seven. **The
#: verdict is the same and everything behind it is different** -- which is the
#: point. ``RECENCY_WALK_AT_SHIPPED`` says which tier the gate leaves
#: standing; this says what the athlete is then told, and what it was computed
#: from. A change that moves the second without moving the first -- the shape
#: of [[T125]]'s form 5a -- is invisible to the triple and red here.
RECENCY_ON_THE_TRIAL = ("hrv_normal", 3, RECENCY_TRIAL_WEEK_DAYS, math.log(79.0))
RECENCY_ON_THE_SNAPSHOT = ("hrv_normal", 7, RECENCY_SNAPSHOT_WEEK_DAYS, math.log(40.0))

#: Row by row, keyed like ``RECENCY_WALK_AT_SHIPPED``: ``gap`` 21..28 admit
#: the trial and the athlete's week is judged from its three days; ``gap`` 29
#: strikes it and the week is judged from the snapshot's seven.
RECENCY_WALK_CONSEQUENCE_AT_SHIPPED = {
    hrv_trend.GAP_RESET_DAYS: RECENCY_ON_THE_TRIAL,
    hrv_trend.GAP_RESET_DAYS + 1: RECENCY_ON_THE_TRIAL,
    27: RECENCY_ON_THE_TRIAL,
    28: RECENCY_ON_THE_TRIAL,
    29: RECENCY_ON_THE_SNAPSHOT,
}


def test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it() -> None:
    """T117's constant, at both of its edges and at the seam with the
    coverage-gap rule. One series shape, one moving part: how far the
    strap trial's last baseline-window day sits behind the snapshot's.

    ``gap`` 28 admits the strap and it takes the week by fidelity;
    ``gap`` 29 strikes it and the snapshot keeps its own 60-day band.

    **The seam row is ``GAP_RESET_DAYS + 1``, not ``GAP_RESET_DAYS``, and
    the difference is a unit** (corrected and measured 2026-09-15, T121;
    until then this docstring and ``RECENCY_TOLERANCE_DAYS``'s own comment
    both named the ``GAP_RESET_DAYS`` row as the pin, and both were false).
    ``gap`` here is a difference between two ``last_read`` **days**, while
    ``_silence_between`` is ``(later - earlier).days - 1``, so a ``gap`` of
    ``g`` is a silence of ``g - 1`` whole days: ``gap`` 21 is a **20-day**
    silence, one short of the longest silence the coverage-gap rule does not
    call a break. The seam ``RECENCY_TOLERANCE_DAYS > GAP_RESET_DAYS`` says
    no candidate may be struck for a silence of 21 days or fewer, and the
    row that carries that is ``gap == GAP_RESET_DAYS + 1`` (22): measured
    across tolerances 19..30, it is admitted at a tolerance of 22 and struck
    at 21. **It is not the only row that goes red at 21** -- ``gap`` 27 and
    ``gap`` 28 are red there too, and so is every row below its own gap; that
    "only row" claim was measured wrong by T121 and is corrected here (review
    cycle 8, ``acfebae``). What singles this row out is its red *onset*: it is the
    only row in the walk that is green at a tolerance of 22 and red at 21, so
    it and nothing else pins the seam at ``GAP_RESET_DAYS``. The
    ``GAP_RESET_DAYS`` row itself is ordinary: it survives a tolerance of 21
    and reds only at 20, pinning ``>= GAP_RESET_DAYS`` and not the seam. It
    is kept as the neighbour that shows the boundary is between the two.
    The whole onset relation, and the measured red set at every tolerance in
    19..30, is asserted by
    ``test_the_seam_row_is_the_only_one_whose_red_onset_is_at_gap_reset_days``
    below, so neither this paragraph nor the list further down can be the
    only thing carrying it again.
    Both rows stand behind the ordering claim in ``research/00`` HRV-16 and in
    ``resolve_baseline_tier``'s docstring.

    Distinct failure modes, each run before this was kept: the filter
    deleted (``gap`` 29 reverts to the strap); the comparison written
    ``<`` rather than ``<=`` (``gap`` 28 flips); the tolerance set to
    ``GAP_RESET_DAYS`` (the ``gap`` 22, ``gap`` 27 **and** ``gap`` 28 rows
    flip -- three, not the two this list named until review cycle 8; ``gap`` 21 does
    **not**, which is why it could never have been the seam pin); and
    the filter applied to the raw day counts rather than to ``last_read``
    (every row flips, the strap never being the denser tier here). None of
    the five rows carries a reset: the snapshot runs daily through the
    trial, so the eras interleave.

    **This test never called ``judge`` until T127.** It pinned the gate's
    arithmetic exhaustively -- tier, baseline ``n``, ``reset_reason`` -- and
    said nothing about what the athlete is told, which is the only reason
    T117 exists. It now asserts ``RECENCY_WALK_CONSEQUENCE_AT_SHIPPED`` per
    row as well: the verdict, ``readings_in_window``, and the judged-week days
    that fed ``ln_rmssd_7d_mean``. The verdict alone does not discriminate
    here (both outcomes are ``hrv_normal``), and that is exactly the finding
    worth pinning: **the gate changes which readings the answer is computed
    from, and on this fixture it does not change the answer.** The other two
    fields are what move -- three strap days at ``ln 79`` against seven
    snapshot days at ``ln 40``."""
    assert hrv_trend.RECENCY_TOLERANCE_DAYS == 28
    assert hrv_trend.RECENCY_TOLERANCE_DAYS > hrv_trend.GAP_RESET_DAYS

    observed = {}
    for gap in RECENCY_WALK:
        result = build(stale_trial_gap(gap))
        in_baseline = [r for r in result.readings if r.date <= D - timedelta(days=7)]
        assert len({r.date for r in in_baseline if r.tier == STRAP}) == 14, gap
        week = [r for r in result.readings if r.date > D - timedelta(days=7)]
        assert len({r.date for r in week if r.tier == STRAP}) == 3, gap
        observed[gap] = (result.tier, len(result.baseline), result.reset_reason)
        # T127: the consequence, per row, in the same loop that walks the
        # gate. ``verdict`` is the athlete-visible half and the two fields
        # after it are what it was computed from.
        judged = hrv_trend.judge(result)
        expected = RECENCY_WALK_CONSEQUENCE_AT_SHIPPED[gap]
        fed = tuple(reading.date for reading in result.window)
        assert (judged.verdict, judged.readings_in_window, fed) == expected[:3], gap
        assert judged.ln_rmssd_7d_mean == pytest.approx(expected[3], abs=1e-12), gap

    assert observed == RECENCY_WALK_AT_SHIPPED


def test_the_seam_row_is_the_only_one_whose_red_onset_is_at_gap_reset_days(monkeypatch) -> None:
    """The claim the docstring above rests on, as an assertion rather than as
    a sentence (review cycle 8, ``acfebae``: the sentence was measured wrong
    twice).

    Both prior spellings named a *singleton*. T121's said the seam row is
    "the only row here that goes red the moment the tolerance is lowered to
    ``GAP_RESET_DAYS``"; the sibling list nine lines below it named a *pair*,
    "the ``gap`` 22 and ``gap`` 27 rows flip"; and ``RECENCY_TOLERANCE_DAYS``'
    own comment named a different singleton again, "the hardcoded 27 row".
    All three are false and they disagree with each other. Measured here, over
    the identical fixture, by the set of walk rows whose admission differs from
    its admission at the shipped tolerance -- i.e. the rows that would go red:

        tolerance   19..20          21          22..26      27      28   29..30
        reddens     21,22,27,28     22,27,28    27,28       28      --   29

    So **three** rows go red at a tolerance of ``GAP_RESET_DAYS``, not one and
    not two. What actually singles the seam row out is not how many rows are
    red there but where each row's red *onset* is: every admitted row ``g``
    is red at every tolerance below ``g``, so lowering the tolerance one step
    past 28 reddens one more row each time. Only ``gap == GAP_RESET_DAYS + 1``
    has its onset at ``GAP_RESET_DAYS`` itself -- green at 22, red at 21 --
    and that is what makes it, and not its neighbours, the pin for
    ``RECENCY_TOLERANCE_DAYS > GAP_RESET_DAYS``. The ``GAP_RESET_DAYS`` row's
    onset is one step later still, at 20, which is why it cannot be the seam.

    Nothing about ``hrv_trend`` moves here: the tolerance is monkeypatched on
    the module for the duration of the matrix and restored, and the gate reads
    the module global at call time (``resolve_baseline_tier``). The onset
    assertions are the discriminating ones -- with the seam claim written as
    the singleton the docstrings published, this test is red on
    ``{22, 27, 28} == {22}``.

    **T127.** The matrix is built by an explicit nested walk rather than the
    dict comprehension it was written as, so that every one of its 60 cells
    also asserts its *consequence* -- the verdict, ``readings_in_window`` and
    the days that fed ``ln_rmssd_7d_mean`` -- inside the loop, and asserts it
    against the cell's own tier: on the trial when the gate admits it, on the
    snapshot when it strikes it. Neither the matrix nor any assertion over it
    changed. What this adds is that a change which leaves all 60 tier triples
    alone while moving the verdict cannot pass here, which is the shape
    [[T125]]'s form 5a took."""
    shipped = hrv_trend.RECENCY_TOLERANCE_DAYS
    reset = hrv_trend.GAP_RESET_DAYS

    def observe(gap: int, tolerance: int) -> tuple[hrv_trend.SingleDatasetView, tuple[str, int, str | None]]:
        monkeypatch.setattr(hrv_trend, "RECENCY_TOLERANCE_DAYS", tolerance)
        result = build(stale_trial_gap(gap))
        return result, (result.tier, len(result.baseline), result.reset_reason)

    # An explicit walk rather than the comprehension this was written as, so
    # that the verdict assertion below sits **inside** the loop over the
    # matrix's cells and not beside it (T127).
    matrix = {}
    for tol in range(19, 31):
        for gap in RECENCY_WALK:
            result, row = observe(gap, tol)
            matrix[(gap, tol)] = row
            judged = hrv_trend.judge(result)
            expected = RECENCY_ON_THE_TRIAL if row[0] == STRAP else RECENCY_ON_THE_SNAPSHOT
            fed = tuple(reading.date for reading in result.window)
            assert (judged.verdict, judged.readings_in_window, fed) == expected[:3], (gap, tol)
            assert judged.ln_rmssd_7d_mean == pytest.approx(expected[3], abs=1e-12), (gap, tol)
    monkeypatch.setattr(hrv_trend, "RECENCY_TOLERANCE_DAYS", shipped)

    def reddens_at(tolerance: int) -> set[int]:
        """The walk rows this test file's neighbour above would fail on, were
        the tolerance ``tolerance``: those whose row differs from the row the
        neighbour's hardcoded expectations were taken at."""
        return {g for g in RECENCY_WALK if matrix[(g, tolerance)] != matrix[(g, shipped)]}

    # The shipped column, against the literals the neighbour test runs on --
    # tier, baseline ``n`` and ``reset_reason``. ``reddens_at(shipped)`` is
    # empty by construction and is no witness for it.
    assert {gap: matrix[(gap, shipped)] for gap in RECENCY_WALK} == RECENCY_WALK_AT_SHIPPED
    assert reddens_at(reset) == {22, 27, 28}, (
        "three rows go red at a tolerance of GAP_RESET_DAYS, not the one the "
        "docstrings named nor the two the 'distinct failure modes' list named"
    )
    assert reddens_at(reset + 1) == {27, 28}
    assert reddens_at(27) == {28}
    assert reddens_at(29) == {29}

    # The seam: exactly one row is red at GAP_RESET_DAYS and green one step
    # above it, and it is ``gap == GAP_RESET_DAYS + 1``. This -- not a count
    # of red rows -- is what makes that row the pin, and it is the assertion
    # that reddens if a future author swaps the seam row for a neighbour.
    assert reddens_at(reset) - reddens_at(reset + 1) == {reset + 1}
    # Every admitted row's onset is one below its own gap, which is the
    # relation the "one more row each step" reading depends on.
    for gap in RECENCY_WALK:
        if matrix[(gap, shipped)][0] == STRAP:
            assert gap not in reddens_at(gap), gap
            assert gap in reddens_at(gap - 1), gap



def test_the_fallback_keeps_the_device_the_athlete_used_last_through_a_thin_week() -> None:
    """G1 (F005 "The fallback keeps the device the athlete used last"). A
    daily snapshot to D-26, a daily strap D-25..D-8, nothing since. Both
    tiers are candidates; from D-3 no candidate covers the week (two strap
    readings, then one, then none). The strap, read last at D-8, keeps the
    baseline on every day D-5..D with the same ``tier_change`` on D-25,
    and the thin days read ``hrv_unavailable``. This empty week keeps a
    reset *in force* while the one in the mixed-baseline test below
    reports none, and nothing about either week separates them: rule 4
    reads the two windows alone, and here the strap era that followed a
    cleanly-ended snapshot era satisfies (a), (b) and (c) whether or not
    the week has readings -- an empty week begins no reset and ends none
    (T095, review cycle 3 G11). Perturbation (densest fallback, cf48c3a):
    D-3 reverts to the snapshot on 44 readings, withdraws the reset, and
    reports ``readings_in_window`` 0."""
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
    ``hrv_unavailable``. No reset here, against the reset the thin-week
    test above keeps through *its* empty week, because the snapshot
    sustained the previous window too and rule 4(b) fails -- the week
    decides neither; an empty week begins no reset and ends none (T095,
    review cycle 3 G11). Perturbation (densest fallback, cf48c3a): the
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
# T095: the count unit is distinct local days, everywhere
#
# Sprint-005 review cycle 3 (spec review AC 14/15 PARTIAL; critic B1; F005
# verdict G10) found that rules 1-3 counted *captures* while ``judge``
# counted the collapsed one-per-day series, so T093's week-coverage gate
# was defeatable by one re-taken morning and 14 captures on 7 days made a
# tier a candidate whose baseline the same response reported as not
# established. Every fixture before this section is one capture per day
# per tier, which is why the unit was pinned by nothing: mutating
# ``_tier_counts`` to count days left all 337 probe tests green. The two
# pins below discriminate the unit in both directions (decision log
# 2026-09-12, "days everywhere"); each went red at 0891061 (T095's
# Delivered note records the run and the reverse perturbation).
# ---------------------------------------------------------------------------


def suppressed_snapshot(target: date = D) -> list[dict]:
    """A daily snapshot at 07:00 for 200 days ending ``target``, genuinely
    suppressed at 25 ms in the judged week ``[target-6, target]`` and
    alternating 38 / 44 ms before it."""
    every_day = days_between(target - timedelta(days=199), target)
    suppressed = days_between(target - timedelta(days=6), target)
    return [
        row(local(day, 7), SNAPSHOT, 25.0 if day in suppressed else 38.0 + 6.0 * (i % 2), f"snap-{day}")
        for i, day in enumerate(every_day)
    ]


def test_a_re_taken_strap_morning_does_not_hand_the_week_to_the_strap() -> None:
    """G10, the re-taken morning (verdict table rows 1-2). The suppressed
    daily snapshot beside a Tue/Sat strap habit: 18 strap captures on 18
    days in ``[D-66, D-7]`` and 2 strap days in the judged week. With one
    capture per strap morning the snapshot owns the week (the strap does
    not cover it) and the suppression is reported. Re-take **one** of the
    two judged-week mornings -- a second strap capture at 06:12 on the
    Saturday -- and at 0891061 the strap's *capture* count in the week
    reached 3: the tier flipped to the strap, ``hrv_unavailable`` with
    ``established: true`` on 18 readings, and the 60-reading snapshot was
    listed off-tier -- cell 1 of F005's cost table, presented as prevented.
    Rules 1-3 now count distinct local days, so both series read
    ``hrv_suppressed`` on the snapshot identically and the re-take changes
    nothing but its own ``excluded[]`` entry (shipped F005:
    ``off_baseline_tier``, a strap capture on a snapshot baseline; F006/T152:
    ``same_day_later_capture`` within the strap's own dataset, which the
    re-taken morning is a second capture of). Perturbation (``_tier_counts`` back to captures): the re-taken
    series flips to the strap -- red."""
    snapshot = suppressed_snapshot()
    strap_days = [day for day in days_between(D - timedelta(days=66), D) if day.weekday() in (1, 5)]
    in_week = [day for day in strap_days if day > D - timedelta(days=7)]
    assert len(strap_days) - len(in_week) == 18 and len(in_week) == 2
    strap = readings(STRAP, strap_days, 79.0)
    re_take = row(local(in_week[0], 6, 12), STRAP, 80.0, "strap-re-take")

    once = build(snapshot + strap)
    re_taken = build(snapshot + strap + [re_take])

    for result in (once, re_taken):
        assert result.tier == SNAPSHOT
        assert result.reset_reason is None and result.reset_on is None
        verdict = hrv_trend.judge(result)
        assert (verdict.verdict, verdict.established, verdict.baseline_n, verdict.readings_in_window) == (
            "hrv_suppressed",
            True,
            60,
            7,
        )
    assert hrv_trend.judge(once) == hrv_trend.judge(re_taken)
    assert [r.session_id for r in once.series] == [r.session_id for r in re_taken.series]
    assert set(excluded_reasons(re_taken)) - set(excluded_reasons(once)) == {"strap-re-take"}
    # F006 (T152): the re-take is the strap dataset's own
    # ``same_day_later_capture``; shipped F005 listed it ``off_baseline_tier``.
    assert excluded_reasons(re_taken)["strap-re-take"] == "same_day_later_capture"
    assert "strap-re-take" not in in_dataset(re_taken, STRAP)


def test_fourteen_strap_captures_on_seven_days_are_not_a_candidate() -> None:
    """G10, the sibling reach (verdict table row 3). The suppressed daily
    snapshot plus two strap captures on each of ``D-20``..``D-14`` -- 14
    captures on 7 days -- and three strap days in the judged week. At
    0891061 the 14 captures made the strap a candidate, the three week
    days covered the week, and the response reported ``chest_strap_raw``,
    ``n`` 7, ``established: false`` and ``hrv_normal`` -- a verdict in the
    up-regulating direction ``research/00`` PRIN-14 forbids, on a
    baseline the same response said was not established, while the
    snapshot's real suppression went unreported. Seven distinct days are
    not 14: the strap is not a candidate, the snapshot keeps the baseline
    and the suppression is emitted. Perturbation (captures): the strap
    takes it -- red."""
    snapshot = suppressed_snapshot()
    doubled_days = days_between(D - timedelta(days=20), D - timedelta(days=14))
    doubled = readings(STRAP, doubled_days, 79.0) + [
        row(local(day, 6, 12), STRAP, 80.0, f"strap-again-{day}") for day in doubled_days
    ]
    this_week = readings(STRAP, [D - timedelta(days=6), D - timedelta(days=4), D - timedelta(days=2)], 79.0)
    assert len(doubled) == 14 and len(doubled_days) == 7

    result = build(snapshot + doubled + this_week)

    assert result.tier == SNAPSHOT
    assert result.reset_reason is None and result.reset_on is None
    verdict = hrv_trend.judge(result)
    assert (verdict.verdict, verdict.established, verdict.baseline_n, verdict.readings_in_window) == (
        "hrv_suppressed",
        True,
        60,
        7,
    )
    # F006 (T152): the 7 re-takes are the strap dataset's own
    # ``same_day_later_capture``; its 10 first captures are its series.
    # Shipped F005 listed all 17 ``off_baseline_tier``.
    strap_reasons = {sid: why for sid, why in excluded_reasons(result).items() if "strap" in sid}
    assert len(strap_reasons) == 7
    assert set(strap_reasons.values()) == {"same_day_later_capture"}
    assert len(in_dataset(result, STRAP)) == 10


def test_rule_3_recency_is_keyed_on_the_local_day_not_the_instant() -> None:
    """The granularity of rule 3's recency (review cycle 3, G16: M4 pinned
    the direction, not the unit). Two candidates both read last on
    ``D-8``, the strap at 07:00 and the snapshot at 06:00, neither covering
    the empty week; 15 strap days against 43 snapshot days. Read last on
    the *same local day*, the tie falls to count -- the snapshot -- as the
    construction reference's rule 3 says. Keyed on the instant, the strap's
    07:00 outranks the snapshot's 06:00 and the strap wins on 15 readings.
    Perturbation (``_last_read`` keeping ``start_time``): ``chest_strap_raw``
    -- red."""
    strap_days = days_between(D - timedelta(days=64), D - timedelta(days=8))[::4]
    snapshot_days = days_between(D - timedelta(days=50), D - timedelta(days=8))
    rows = readings(STRAP, strap_days, 79.0, hh=7) + readings(SNAPSHOT, snapshot_days, hh=6)

    result = build(rows)

    assert (len(strap_days), len(snapshot_days)) == (15, 43)
    assert strap_days[-1] == snapshot_days[-1] == D - timedelta(days=8)
    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 43
    assert result.window == ()
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


# ---------------------------------------------------------------------------
# F006 / T151 -- the series is N per-tier datasets, one per tier present
# ---------------------------------------------------------------------------


def _two_tier_history() -> list[dict]:
    """AC1's geometry: 41 ``chest_strap_raw`` and 57 ``health_snapshot``
    distinct local days inside ``[D-66, D-7]``, overlapping on 41 of them,
    each tier at its own level (60 ms and 40 ms) so a band that mixed them
    would be visibly neither. The judged week holds three strap mornings
    and seven snapshot ones."""
    rows = readings(STRAP, days_between(D - timedelta(days=47), D - timedelta(days=7)), 60.0)
    rows += readings(SNAPSHOT, days_between(D - timedelta(days=63), D - timedelta(days=7)), 40.0, hh=7)
    rows += readings(STRAP, days_between(D - timedelta(days=6), D - timedelta(days=4)), 60.0)
    rows += readings(SNAPSHOT, days_between(D - timedelta(days=6), D), 40.0, hh=7)
    return rows


def test_build_series_returns_a_dataset_per_tier_each_with_its_own_band_and_n() -> None:
    """F006 AC1/AC2, the walking skeleton's first red: on a two-tier history
    **both** tiers carry a non-null band and an independent ``n``. Under F005
    the resolved tier (the strap: highest fidelity, covers the week) owned the
    only band and the snapshot's 57 days were ``off_baseline_tier`` with no
    band at all. Each band is built from that tier's readings alone (spec/03 §3.7.3
    anti-mixing, honoured by construction): the strap's mean is ``ln 60`` and
    the snapshot's ``ln 40``, and a band over the union would be neither."""
    series = hrv_trend.build_series(_two_tier_history(), AUCKLAND, D)

    by_tier = {dataset.tier: dataset for dataset in series.datasets}
    assert [dataset.tier for dataset in series.datasets] == [STRAP, SNAPSHOT], "one per tier present, fidelity order"

    strap, snapshot = by_tier[STRAP], by_tier[SNAPSHOT]
    assert strap.band is not None and snapshot.band is not None
    assert (strap.n, snapshot.n) == (41, 57)
    assert strap.established is True and snapshot.established is True
    assert strap.band.mean == pytest.approx(math.log(60.0))
    assert snapshot.band.mean == pytest.approx(math.log(40.0))
    assert {r.tier for r in strap.baseline} == {STRAP} and {r.tier for r in snapshot.baseline} == {SNAPSHOT}
    assert (len(strap.window), len(snapshot.window)) == (3, 7)
    assert strap.baseline_window == snapshot.baseline_window == (D - timedelta(days=66), D - timedelta(days=7))


def test_a_days_captures_feed_their_own_datasets_and_collapse_within_each() -> None:
    """A morning with a strap capture at 07:00 and a snapshot at 07:05 puts
    one reading in **each** dataset; two strap captures on one day keep the
    earlier (``same_day_later_capture``) and the day counts once in that
    dataset's ``n`` (AC3, AC4). The exclusions ``build_series`` makes are one
    shared list: the strap's re-take is in it, and nothing of the snapshot's."""
    both = D - timedelta(days=10)
    retaken = D - timedelta(days=20)
    rows = readings(STRAP, baseline_days(20), 60.0) + readings(SNAPSHOT, baseline_days(20), 40.0, hh=7)
    rows += [row(local(both, 7, 5), SNAPSHOT, 41.0, "snapshot-both")]
    rows += [row(local(retaken, 9), STRAP, 61.0, "strap-retake")]

    series = hrv_trend.build_series(rows, AUCKLAND, D)
    by_tier = {dataset.tier: dataset for dataset in series.datasets}

    strap_days = [r.date for r in by_tier[STRAP].series]
    assert strap_days.count(retaken) == 1 and by_tier[STRAP].n == 20
    assert both in strap_days and both in [r.date for r in by_tier[SNAPSHOT].series]
    assert by_tier[SNAPSHOT].n == 20
    assert {e.session_id: e.reason for e in series.excluded} == {
        "strap-retake": "same_day_later_capture",
        "snapshot-both": "same_day_later_capture",
    }
    assert {r.session_id for r in series.readings} >= {"snapshot-both", "strap-retake"}, (
        "the shared readings are the post-exclusion population of every tier"
    )


def test_the_coverage_gap_is_global_and_clips_every_dataset_identically() -> None:
    """AC16 across the N-way partition. One tier silent while the other
    carries the series is **no** gap: both datasets keep ``[D-66, D-7]``.
    Every tier silent for more than ``GAP_RESET_DAYS`` is one gap, found
    before the partition, and both datasets are clipped at the same
    resumption with the same ``coverage_gap`` report.

    **Re-derived at T153 (2026-09-19, AC17).** Shipped 785f89c asserted that
    both datasets of the bridged series keep ``[D-66, D-7]``. The strap's own
    30-day hole (``D-40`` to ``D-9``) lies inside its window, so the
    per-dataset clip now opens its window on ``D-9`` (``n`` 3) -- with no
    global gap and no report, which is the orthogonality this guard exists to
    protect: the gap stays global, and the clip that fires is not it. The
    snapshot's window is unchanged."""
    strap_silent = readings(SNAPSHOT, days_between(D - timedelta(days=66), D), 40.0, hh=7)
    strap_silent += readings(STRAP, days_between(D - timedelta(days=66), D - timedelta(days=40)), 60.0)
    strap_silent += readings(STRAP, days_between(D - timedelta(days=9), D), 60.0)
    bridged = hrv_trend.build_series(strap_silent, AUCKLAND, D)
    assert bridged.gap_reset_on is None
    by_tier = {d.tier: d for d in bridged.datasets}
    assert by_tier[SNAPSHOT].baseline_window == (D - timedelta(days=66), D - timedelta(days=7))
    assert by_tier[STRAP].baseline_window == (D - timedelta(days=9), D - timedelta(days=7)), "T153: its own hole"
    assert by_tier[STRAP].n == 3 and by_tier[SNAPSHOT].n == 60
    assert all((d.reset_on, d.reset_reason) == (None, None) for d in bridged.datasets)

    resumed_on = D - timedelta(days=30)
    everyone_silent = readings(SNAPSHOT, days_between(D - timedelta(days=66), D - timedelta(days=55)), 40.0, hh=7)
    everyone_silent += readings(STRAP, days_between(D - timedelta(days=66), D - timedelta(days=55)), 60.0)
    everyone_silent += readings(SNAPSHOT, days_between(resumed_on, D), 40.0, hh=7)
    everyone_silent += readings(STRAP, days_between(resumed_on, D), 60.0)
    gapped = hrv_trend.build_series(everyone_silent, AUCKLAND, D)
    assert gapped.gap_reset_on == resumed_on
    assert {d.baseline_window for d in gapped.datasets} == {(resumed_on, D - timedelta(days=7))}
    assert {(d.reset_on, d.reset_reason) for d in gapped.datasets} == {(resumed_on, "coverage_gap")}
    assert all(r.date >= resumed_on for r in gapped.readings), "the gap rebinds the shared readings"
    assert {d.n for d in gapped.datasets} == {24}, "each dataset's n is its own post-clip distinct days"


def test_the_selected_view_hands_judge_the_dataset_the_selection_picks() -> None:
    """Re-pointed by T155 (was T151's bridge pin over the retired resolver):
    the single-dataset view is the dataset ``select_dataset`` selects, and
    ``judge`` on it reports that dataset's own band, ``n`` and
    ``established`` -- the same values the dataset carries, not a second
    computation. The other tier's readings are in its own dataset, carried
    on the view's ``datasets``, and the view's ``excluded`` is the series'
    own (T152; the shim that re-listed them ``off_baseline_tier`` on the
    view, as F005 had, is retired)."""
    series = hrv_trend.build_series(_two_tier_history(), AUCKLAND, D)
    view = hrv_trend.selected_view(series)

    assert view.tier == STRAP
    assert view.selection is not None and view.selection.selected is series.datasets[0]
    assert view.selected is series.datasets[0]
    assert view.baseline == view.selected.baseline and view.window == view.selected.window
    verdict = hrv_trend.judge(view)
    assert verdict.band == view.selected.band
    assert (verdict.baseline_n, verdict.established) == (view.selected.n, view.selected.established)
    assert verdict.verdict == "hrv_normal"

    assert in_dataset(view, SNAPSHOT) == {r.session_id for r in series.readings if r.tier == SNAPSHOT}
    assert view.excluded == series.excluded and view.datasets == series.datasets
    assert not any(e.reason.startswith("off_baseline_tier") for e in view.excluded)
    assert view.readings == series.readings and view.target_date == D


# ---------------------------------------------------------------------------
# F006 / T152 -- the included/excluded partition is per dataset (AC3, AC15)
# ---------------------------------------------------------------------------


def test_a_dual_capture_morning_feeds_both_datasets_and_neither_is_excluded_anywhere() -> None:
    """The first failing test of T152 (AC3). A local day carrying a
    ``chest_strap_raw`` capture at 07:00 and a ``health_snapshot`` at 07:05
    contributes one reading to **each** dataset, and neither appears in any
    ``excluded`` list -- not the series' and not the selected view's, which
    is what the route renders. Shipped F005 excluded whichever of the two
    was off the resolved tier as ``off_baseline_tier: <tier>``, and T151/T155
    kept that listing alive on the view alone as a compatibility shim; this
    pin is what retires it. Perturbation: re-listing the non-selected
    dataset's readings on the view reds the ``view.excluded`` assertion."""
    both = D - timedelta(days=30)  # outside the twenty baseline days D-26..D-7, so neither is a re-take
    rows = readings(STRAP, baseline_days(20), 60.0) + readings(SNAPSHOT, baseline_days(20), 40.0, hh=7)
    rows += [row(local(both, 7, 0), STRAP, 61.0, "strap-0700"), row(local(both, 7, 5), SNAPSHOT, 41.0, "snap-0705")]
    rows += readings(STRAP, days_between(D - timedelta(days=6), D - timedelta(days=4)), 60.0)

    series = hrv_trend.build_series(rows, AUCKLAND, D)
    view = hrv_trend.selected_view(series)
    by_tier = {dataset.tier: dataset for dataset in view.datasets}

    assert view.tier == STRAP, "the strap is judgeable and highest fidelity; the snapshot is the other dataset"
    assert by_tier[STRAP].series[[r.date for r in by_tier[STRAP].series].index(both)].session_id == "strap-0700"
    assert by_tier[SNAPSHOT].series[[r.date for r in by_tier[SNAPSHOT].series].index(both)].session_id == "snap-0705"
    for excluded in (series.excluded, view.excluded):
        assert not {"strap-0700", "snap-0705"} & {e.session_id for e in excluded}, excluded
    assert view.excluded == series.excluded, "the view presents the series' own partition, re-listing nothing"
    assert view.datasets == series.datasets


def _every_screen_history() -> list[dict]:
    """A history that exercises every member of the exclusion chain beside a
    two-dataset partition: a snapshot era from ``D-126``, a ten-day strap
    trial, a genuine switch to a daily strap at ``D-39``, one row per screen
    (null tier, unknown tier, unusable value, pre-amendment), a re-taken
    strap morning, and a snapshot capture the era clip drops."""
    rows = readings(SNAPSHOT, days_between(D - timedelta(days=126), D - timedelta(days=40)), 40.0, hh=6)
    rows += readings(STRAP, days_between(D - timedelta(days=60), D - timedelta(days=51)), 25.0)
    rows += readings(STRAP, days_between(D - timedelta(days=39), D), 40.0)
    rows += [
        row(local(D - timedelta(days=20), 9), None, 40.0, "no-tier"),
        row(local(D - timedelta(days=19), 9), "wrist_ppg_guess", 40.0, "odd-tier"),
        row(local(D - timedelta(days=18), 9), STRAP, 0.0, "bad-value"),
        row(local(D - timedelta(days=17), 9), STRAP, None, "pre-amendment"),
        row(local(D - timedelta(days=10), 9), STRAP, 41.0, "later-same-day"),
        row(local(D - timedelta(days=70), 9), SNAPSHOT, 40.0, "before-the-span"),
    ]
    return rows


def test_every_stored_row_in_the_span_is_accounted_for_exactly_once_across_datasets_and_excluded() -> None:
    """AC15 (``research/00`` PRIN-23): every stored row inside ``[D-66, D]`` is
    in exactly one place -- some dataset's ``series`` or the ``excluded``
    list -- and nothing is in two. Under F005 the rows of every non-resolved
    tier were the ``off_baseline_tier`` members of that list; under the
    per-dataset partition they are in their own dataset, so the union of the
    datasets' series and the exclusions is the span's stored rows, once
    each, and the selected view (what the route renders) carries that same
    list unchanged. The witness prints the partition it compared."""
    rows = _every_screen_history()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    view = hrv_trend.selected_view(series)
    first, _ = hrv_trend.baseline_window(D)

    in_span = {r["session_id"] for r in rows if first <= date.fromisoformat(local_day_of(r)) <= D}
    in_datasets = [r.session_id for d in series.datasets for r in d.series]
    listed = [e.session_id for e in series.excluded if first <= e.date <= D]
    print(f"[slice compared] datasets={len(in_datasets)} excluded={len(listed)} span_rows={len(in_span)}")
    print(f"[slice compared] reasons={sorted({e.reason for e in series.excluded if first <= e.date <= D})}")

    assert len(in_datasets) == len(set(in_datasets)), "a row is in two datasets"
    assert len(listed) == len(set(listed)), "a row is excluded twice"
    assert not set(in_datasets) & set(listed), "a row is both in a dataset and excluded"
    assert set(in_datasets) | set(listed) == in_span, "a row in the span is in neither"
    assert not any(e.reason.startswith("off_baseline_tier") for e in series.excluded)
    assert view.excluded == series.excluded
    assert "before-the-span" not in set(in_datasets) | set(listed)


def local_day_of(stored_row: dict) -> str:
    """The Auckland local day of a stored row's ``start_time``, ISO-spelled."""
    return datetime.fromisoformat(stored_row["start_time"]).astimezone(AUCKLAND).date().isoformat()


# ---------------------------------------------------------------------------
# F006 / T155 -- selection: the highest-fidelity judgeable dataset the
# recency gate did not skip (research/00 HRV-14, HRV-15;
# F006 AC5-AC8; reference section 9 for the series a first draft got wrong)
#
# Every pin below prints the slice it compared -- which datasets were
# judgeable, which were skipped and by how many days -- through
# ``Selection.describe()`` (a-witness-must-print-the-slice-it-compared).
# ---------------------------------------------------------------------------


def _carrier(first: date = D - timedelta(days=66), last: date = D) -> list[dict]:
    """A daily ``health_snapshot`` over ``[first, last]`` at 40 ms, 07:00."""
    return readings(SNAPSHOT, days_between(first, last), 40.0, hh=7)


def _strap(days: list[date], value: float = 60.0) -> list[dict]:
    return readings(STRAP, days, value)


def _select(rows: list[dict], target: date = D) -> hrv_trend.Selection:
    selection = hrv_trend.select_dataset(hrv_trend.build_series(rows, AUCKLAND, target))
    print(selection.describe())
    return selection


def test_a_strap_whose_baseline_is_entirely_pre_layoff_is_skipped_for_the_watch() -> None:
    """The first failing test, and reference section 9's reproducing series
    (AC6). The strap is established on ``D-66..D-36`` (31 days), silent
    ``D-35..D-5`` while the watch carries the series (so no coverage gap
    fires, AC16), and back on ``D-4/D-2/D-0`` -- three judged-week days, so
    it is judgeable and the highest fidelity. An unqualified gate reading
    the strap's *latest* reading (``D-0``) selects it and judges the athlete
    against a band whose every reading is 36 to 66 days old. The window is
    normative: the strap's latest reading **within ``[D-66, D-7]``** is
    ``D-36``, the watch's is ``D-7``, and 29 > ``RECENCY_TOLERANCE_DAYS``
    skips it. Perturbation (measured while building): taking ``last_read``
    over ``series.readings`` instead of the baseline-window slice selects the
    strap and reds this."""
    rows = _carrier() + _strap(days_between(D - timedelta(days=66), D - timedelta(days=36)))
    rows += _strap([D - timedelta(days=4), D - timedelta(days=2), D])
    selection = _select(rows)
    slice_ = selection.describe()

    assert selection.judgeable == (STRAP, SNAPSHOT), slice_
    assert selection.skipped == (STRAP,), slice_
    assert selection.last_read[STRAP] == D - timedelta(days=36), slice_
    assert selection.reference == D - timedelta(days=7), slice_
    assert selection.gap(STRAP) == hrv_trend.RECENCY_TOLERANCE_DAYS + 1, slice_
    assert selection.selected is not None and selection.selected.tier == SNAPSHOT, slice_


def test_selection_promotes_the_highest_fidelity_judgeable_dataset() -> None:
    """AC5: a strap judgeable and last read two days before the window's end
    beside a judgeable daily watch selects the **strap** -- fidelity rank
    decides, and a gap of 2 is nowhere near the tolerance. The watch is
    denser (60 vs 20 baseline days) and read later; neither counts."""
    rows = _carrier() + _strap(days_between(D - timedelta(days=28), D - timedelta(days=9)))
    rows += _strap(days_between(D - timedelta(days=6), D - timedelta(days=4)))
    selection = _select(rows)
    slice_ = selection.describe()

    assert selection.judgeable == (STRAP, SNAPSHOT), slice_
    assert selection.skipped == (), slice_
    assert selection.gap(STRAP) == 2 and selection.gap(SNAPSHOT) == 0, slice_
    assert selection.selected is not None and selection.selected.tier == STRAP, slice_


def test_an_established_dataset_with_two_judged_week_days_is_not_a_candidate() -> None:
    """AC8, the week half: 41 strap baseline days but only ``D-6`` and
    ``D-4`` in the judged week -- established, not judgeable, so it is not
    in ``judgeable`` at all, let alone skipped, and the watch is selected.
    Under F005 this was rule 2's "covers the week" clause; here it is a
    precondition of candidacy, stated once."""
    rows = _carrier() + _strap(days_between(D - timedelta(days=47), D - timedelta(days=7)))
    rows += _strap([D - timedelta(days=6), D - timedelta(days=4)])
    selection = _select(rows)
    slice_ = selection.describe()

    assert selection.judgeable == (SNAPSHOT,), slice_
    assert selection.skipped == (), slice_
    assert selection.selected is not None and selection.selected.tier == SNAPSHOT, slice_


def test_judgeability_reads_the_datasets_own_post_clip_established_not_a_recount() -> None:
    """AC8, the baseline half: ``established`` is the dataset's own count
    over its **post-clip** window (T153/T154), never a recount of the shared
    ``series.readings`` over the global window. Built through
    ``build_series`` on the two-tier history (the strap is established, 41
    days), then the strap dataset alone is replaced by one whose post-clip
    ``n`` is 13 and ``established`` False while ``series.readings`` still
    holds every one of its 41 window days. A selector recounting the shared
    readings would still find the strap judgeable and select it; the one
    that reads the dataset selects the watch."""
    series = hrv_trend.build_series(_two_tier_history(), AUCKLAND, D)
    strap, snapshot = series.datasets
    assert strap.tier == STRAP and strap.established
    clipped = dataclasses.replace(strap, baseline=strap.baseline[-13:], n=13, established=False)
    reshaped = dataclasses.replace(series, datasets=(clipped, snapshot))
    assert len({r.date for r in reshaped.readings if r.tier == STRAP and r.date <= D - timedelta(days=7)}) == 41

    selection = hrv_trend.select_dataset(reshaped)
    print(selection.describe())
    assert selection.judgeable == (SNAPSHOT,), selection.describe()
    assert selection.selected is not None and selection.selected.tier == SNAPSHOT, selection.describe()


@pytest.mark.parametrize(
    ("behind", "expect_skipped"),
    [
        (hrv_trend.RECENCY_TOLERANCE_DAYS, False),
        (hrv_trend.RECENCY_TOLERANCE_DAYS + 1, True),
    ],
    ids=["exactly_the_tolerance", "one_past_it"],
)
def test_the_gate_boundary_is_strictly_greater_than_the_tolerance(behind: int, expect_skipped: bool) -> None:
    """AC7's boundary, and the adversarial probe for the gate's one constant:
    a strap whose latest baseline-window reading is exactly
    ``RECENCY_TOLERANCE_DAYS`` behind the watch's is **not** skipped and
    is selected; one day further behind it is skipped and the watch is
    selected. The value is degenerate because it is the only point where
    ``>`` and ``>=`` disagree, and F005's ``_recency_struck`` (reused
    verbatim) is strict. Both rows are judgeable by construction: the
    strap has three ``D-4/D-2/D-0`` week days and >= 14 baseline days."""
    strap_last = D - timedelta(days=7) - timedelta(days=behind)
    rows = _carrier() + _strap(days_between(D - timedelta(days=66), strap_last))
    rows += _strap([D - timedelta(days=4), D - timedelta(days=2), D])
    selection = _select(rows)
    slice_ = selection.describe()

    assert selection.judgeable == (STRAP, SNAPSHOT), slice_
    assert selection.gap(STRAP) == behind, slice_
    assert (STRAP in selection.skipped) is expect_skipped, slice_
    assert selection.selected is not None, slice_
    assert selection.selected.tier == (SNAPSHOT if expect_skipped else STRAP), slice_


def test_the_reference_is_the_latest_established_read_not_the_end_of_the_window() -> None:
    """AC7's reference set, **re-pointed by T164**: the maximum is taken over
    every **established** dataset -- not over the judgeable ones alone, and
    not against the window's end.

    Both series below carry a third tier (``health_api_overnight``) daily so
    no coverage gap fires (AC16). It holds **no** judged-week day, so it is
    established and never a candidate -- and since T164 it **is** in the
    reference set.

    (a) The overnight tier is read to ``D-7``, and it is the reference. The
    strap (last read ``D-45``, 38 behind) and the watch (``D-52``, 45
    behind) are **both** skipped and nothing is selected. *Shipped F006
    (T155, the reference over the judgeable datasets alone): reference
    ``D-45``, ``skipped ()``, ``chest_strap_raw`` selected* -- the two
    judgeable datasets were within 7 days of **each other**, so the stopped
    carrier that shipped F005 would have struck them with had left the
    comparison. That is IDEA-080's mechanism, and this row is it closed.

    (b) The same geometry with the overnight tier stopping at ``D-20`` (47
    days, still established). The reference is ``D-20``: **neither** the
    window's end (``D-7``) **nor** the latest judgeable read (``D-45``). So
    the strap at 25 behind survives, the watch at 32 behind is skipped, and
    the strap is selected. A gate measuring against ``D-7`` skips both; one
    taking the reference over the judgeable datasets alone skips neither;
    only the established population produces this row, which is why it is
    here.
    """
    base = _carrier(D - timedelta(days=66), D - timedelta(days=52)) + _carrier(D - timedelta(days=6), D)
    base += _strap(days_between(D - timedelta(days=66), D - timedelta(days=45)))
    base += _strap([D - timedelta(days=4), D - timedelta(days=2), D])

    to_d7 = base + readings(OVERNIGHT, days_between(D - timedelta(days=66), D - timedelta(days=7)), 40.0, hh=8)
    a = _select(to_d7)
    slice_ = a.describe()
    assert a.last_read[OVERNIGHT] == D - timedelta(days=7), slice_
    assert a.judgeable == (STRAP, SNAPSHOT), slice_
    assert a.reference == D - timedelta(days=7), slice_  # shipped F006: D-45
    assert a.gap(STRAP) == 38 and a.gap(SNAPSHOT) == 45, slice_  # shipped F006: 0 and 7
    assert a.skipped == (STRAP, SNAPSHOT), slice_  # shipped F006: ()
    assert a.selected is None, slice_  # shipped F006: chest_strap_raw

    to_d20 = base + readings(OVERNIGHT, days_between(D - timedelta(days=66), D - timedelta(days=20)), 40.0, hh=8)
    b = _select(to_d20)
    slice_ = b.describe()
    assert b.last_read[OVERNIGHT] == D - timedelta(days=20), slice_
    assert b.judgeable == (STRAP, SNAPSHOT), slice_
    assert b.reference == D - timedelta(days=20), slice_
    assert b.gap(STRAP) == 25 and b.gap(SNAPSHOT) == 32, slice_
    assert b.skipped == (SNAPSHOT,), slice_
    assert b.selected is not None and b.selected.tier == STRAP, slice_


def test_the_reference_maximum_is_taken_once_over_every_established_dataset() -> None:
    """AC7, "once, simultaneously, never iteratively", pinned on the
    source: ``select_dataset`` calls F005's ``_recency_struck`` exactly
    once, with the whole **established** list (T164; the list was the
    judgeable one under shipped F006, and the *call count* this pin asserts
    is unchanged by that widening -- which is the point of re-pointing the
    population without touching the gate), and reads ``_last_read`` over the
    baseline-window slice (AC6 is a reuse of that scope, not a new
    computation -- task technical notes). A loop that re-took the maximum
    after each skip, or a second transcription of the gate, arrives here as
    a second call or none."""
    tree = ast.parse(Path(hrv_trend.__file__).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select_dataset")
    calls = [
        n.func.id
        for n in ast.walk(function)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert calls.count("_recency_struck") == 1, calls
    assert calls.count("_last_read") == 1, calls
    assert not any(isinstance(n, (ast.For, ast.While)) and "_recency_struck" in ast.unparse(n) for n in ast.walk(function))


def test_the_numeric_confidence_weight_never_participates_in_selection() -> None:
    """Deliverable 6 (reference section 3, "the two senses of quality,
    split"): the **fidelity rank** arbitrates and is never confused with the
    numeric per-tier confidence weight section 3.7.1 defines.

    Corrected 2026-09-21 (T159), which is the task that had to decide it: this
    docstring and the message below said the confidence weight "is reported on
    datasets[]", following reference section 3's table. Section 3.7.4 is the
    authority above that table and says no confidence weight is computed in
    Section 3 at all -- the weighting is deferred to Section 6's readiness
    fusion -- so emitting one would have minted a constant, which the same
    reference note forbids in the same breath ("no new constant"). What
    datasets[] renders is `fidelity_rank`, the ordinal asserted below, and the
    split this pin protects is unchanged: the rank arbitrates, and nothing
    that names a weight reaches the choice.
    Pinned two ways. Behaviourally: a sparse, noisy strap (three baseline
    days a week, values swinging 30..90 ms) beside a dense, metronomic
    watch (daily, 40 ms exactly) selects the strap, and swapping which tier
    carries the noise moves nothing -- the reading values and the counts
    are not inputs to the choice. Structurally: ``select_dataset`` orders
    by ``_FIDELITY_RANK`` and reads no name that so much as mentions a
    weight or a confidence, so a weight added to the module later cannot
    reach the choice without reddening this."""
    strap_days = [d for d in days_between(D - timedelta(days=66), D) if d.weekday() in (0, 2, 4)]
    noisy = [row(local(d, 6), STRAP, 30.0 + 60.0 * (i % 2), f"noisy-{d}") for i, d in enumerate(strap_days)]
    steady = _carrier()
    assert len([d for d in strap_days if d >= D - timedelta(days=6)]) >= hrv_trend.MIN_WINDOW_READINGS
    selection = _select(steady + noisy)
    assert selection.selected is not None and selection.selected.tier == STRAP, selection.describe()

    swapped = [row(local(d, 6), STRAP, 40.0, f"steady-{d}") for d in strap_days]
    swapped += [
        row(local(d, 7), SNAPSHOT, 30.0 + 60.0 * (i % 2), f"noisy-{d}")
        for i, d in enumerate(days_between(D - timedelta(days=66), D))
    ]
    again = _select(swapped)
    assert again.selected is not None and again.selected.tier == STRAP, again.describe()
    assert again.judgeable == selection.judgeable == (STRAP, SNAPSHOT)

    tree = ast.parse(Path(hrv_trend.__file__).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select_dataset")
    names = {n.id for n in ast.walk(function) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(function) if isinstance(n, ast.Attribute)}
    assert "_FIDELITY_RANK" in names, names
    offending = {n for n in names if "weight" in n.lower() or "confidence" in n.lower()}
    assert not offending, offending
    assert not any(k for k in vars(hrv_trend) if "confidence" in k.lower() and "weight" in k.lower()), (
        "a confidence weight now exists in the module; spec 03 section 3.7.4 computes none in "
        "Section 3 and defers the weighting to Section 6, and datasets[] renders the fidelity "
        "rank instead (T159) -- so this is a new constant, not a rendering"
    )


# --- the adversarial set (deliverable 7): what was fed, what routed, and why -


def test_probe_zero_judgeable_datasets_selects_nothing_and_says_so() -> None:
    """Degenerate: the candidate list is empty, so the reference maximum is a
    maximum over nothing. Two forms. No rows at all: no dataset exists,
    ``selected`` is ``None``, ``judgeable`` and ``skipped`` are empty and
    ``reference`` is ``None`` -- nothing raised on ``max([])``. Two datasets
    neither covering the week (the illness week): both established, neither
    judgeable, same answer -- and the view still presents the dataset the
    athlete used last so ``judge`` says ``week_too_thin`` on a 60-reading
    baseline (AC9; T156 formalises the fallback)."""
    empty = _select([])
    assert (empty.selected, empty.judgeable, empty.skipped, empty.reference) == (None, (), (), None)
    assert empty.last_read == {}

    rows = _carrier(D - timedelta(days=66), D - timedelta(days=7))
    rows += _strap(days_between(D - timedelta(days=66), D - timedelta(days=9)))
    illness = _select(rows)
    assert (illness.selected, illness.judgeable, illness.skipped) == (None, (), ())
    # T164: the reference population is the ESTABLISHED datasets, so it
    # survives a week in which nothing is judgeable. Shipped F006: ``None``,
    # because the reference was taken over the (empty) judgeable set. Nothing
    # downstream moves -- with no candidate there is nothing to strike.
    assert illness.reference == D - timedelta(days=7), illness.describe()
    assert illness.last_read == {STRAP: D - timedelta(days=9), SNAPSHOT: D - timedelta(days=7)}

    view = hrv_trend.selected_view(hrv_trend.build_series(rows, AUCKLAND, D))
    verdict = hrv_trend.judge(view)
    assert view.tier == SNAPSHOT and view.selection is not None and view.selection.selected is None
    assert (verdict.verdict, verdict.unavailable_reason) == ("hrv_unavailable", "week_too_thin")
    assert verdict.baseline_n == 60 and verdict.established


def test_probe_exactly_one_established_dataset_is_its_own_reference() -> None:
    """Degenerate: a reference set of size one -- **re-pointed by T164**,
    which moved the boundary of the set from *judgeable* to *established*.

    (a) A lone **judgeable** strap is no longer automatically its own
    reference. The strap is last read ``D-50`` and holds three week days;
    an overnight tier read daily to ``D-7`` carries the series (no coverage
    gap, AC16), holds no week day and so is established and **not** a
    candidate -- and it is now the reference. The strap is 43 behind, is
    skipped, and **nothing is selected** although ``judgeable`` is not
    empty. *Shipped F006 (T155): reference ``D-50``, gap 0, ``skipped ()``,
    the strap selected -- its docstring said in as many words that taking
    the reference "over every dataset present rather than every judgeable
    one would skip the athlete's only judgeable instrument here". That is
    now the ruled answer: the carrier the athlete is actually being read on
    is exactly what shipped F005 struck the returning strap with, and the
    stale-band rate T162 measured is what the narrower reading cost.* The
    view falls back and confers **no** verdict (AC9), so nothing is promoted
    on the strap's 36-to-66-day-old band.

    (b) The reference set of size one that remains: the same series with the
    overnight tier unestablished (ten days, ``D-30..D-21``). The strap is
    then the only **established** dataset, is its own reference at gap 0 and
    is selected -- and the overnight tier's ``D-21``, 29 behind, is one day
    past the tolerance, so a reference taken over every dataset *present*
    would skip the strap here and this row would red.
    """
    strap = _strap(days_between(D - timedelta(days=66), D - timedelta(days=50)))
    strap += _strap([D - timedelta(days=4), D - timedelta(days=2), D])

    carried = strap + readings(
        OVERNIGHT, days_between(D - timedelta(days=66), D - timedelta(days=7)), 40.0, hh=8
    )
    a = _select(carried)
    slice_ = a.describe()
    assert a.last_read[OVERNIGHT] == D - timedelta(days=7), slice_
    assert a.judgeable == (STRAP,), slice_
    assert a.reference == D - timedelta(days=7), slice_  # shipped F006: D-50
    assert a.gap(STRAP) == 43 and a.skipped == (STRAP,), slice_  # shipped F006: 0 and ()
    assert a.selected is None, slice_  # shipped F006: chest_strap_raw
    view = hrv_trend.selected_view(hrv_trend.build_series(carried, AUCKLAND, D))
    assert view.selection is not None and view.selection.selected is None, slice_
    assert hrv_trend.judge(view).verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_

    sparse = strap + readings(
        OVERNIGHT, days_between(D - timedelta(days=30), D - timedelta(days=21)), 40.0, hh=8
    )
    b = _select(sparse)
    slice_ = b.describe()
    (overnight,) = [d for d in hrv_trend.build_series(sparse, AUCKLAND, D).datasets if d.tier == OVERNIGHT]
    assert overnight.n == 10 and not overnight.established, slice_
    assert b.last_read[OVERNIGHT] == D - timedelta(days=21), slice_
    assert b.judgeable == (STRAP,), slice_
    assert b.reference == b.last_read[STRAP] == D - timedelta(days=50), slice_
    assert b.gap(STRAP) == 0 and b.skipped == (), slice_
    assert b.selected is not None and b.selected.tier == STRAP, slice_


def test_probe_two_datasets_tied_on_fidelity_rank_is_a_construction_defect() -> None:
    """Degenerate: two datasets with one rank. The dataset key is the tier
    (reference section 1), so ``build_series`` can never produce two
    datasets of one tier, and a hand-built series that does is a defect of
    its caller -- a silent first-wins would hide it behind a plausible
    answer, and there is no tie-break the spec names because the case does
    not exist in it. Raised, naming the tier, like ``local_day`` raises on a
    naive instant rather than guessing."""
    series = hrv_trend.build_series(_two_tier_history(), AUCKLAND, D)
    strap, snapshot = series.datasets
    twinned = dataclasses.replace(series, datasets=(strap, dataclasses.replace(strap), snapshot))
    with pytest.raises(ValueError, match=STRAP):
        hrv_trend.select_dataset(twinned)


def test_probe_a_dataset_whose_only_window_reading_is_the_windows_first_day() -> None:
    """Degenerate: the closed interval's first day, ``D-66``. A strap read
    once on ``D-66`` and on three week days is **inside** the window (its
    ``last_read`` is ``D-66``, so the boundary is inclusive), holds one
    baseline reading, is not established and so is not judgeable -- and
    therefore not in the reference set, where it would sit 59 days behind
    the watch. The same reading one day earlier, ``D-67``, is
    ``outside_windows``: the strap then has no baseline reading at all and
    no ``last_read`` entry. Either way the watch is the only candidate."""
    week = _strap([D - timedelta(days=4), D - timedelta(days=2), D])

    inside = _select(_carrier() + _strap([D - timedelta(days=66)]) + week)
    assert inside.last_read[STRAP] == D - timedelta(days=66), inside.describe()
    assert inside.judgeable == (SNAPSHOT,) and inside.skipped == (), inside.describe()
    assert inside.reference == D - timedelta(days=7), inside.describe()
    assert inside.selected is not None and inside.selected.tier == SNAPSHOT, inside.describe()

    outside = _select(_carrier() + _strap([D - timedelta(days=67)]) + week)
    assert STRAP not in outside.last_read, outside.describe()
    assert outside.judgeable == (SNAPSHOT,), outside.describe()
    assert outside.selected is not None and outside.selected.tier == SNAPSHOT, outside.describe()


def test_probe_every_judgeable_dataset_can_be_skipped_at_once_since_t164() -> None:
    """Degenerate: the empty survivor set -- **re-pointed by T164**, which
    made it reachable. *Shipped F006 (T155) pinned it as unreachable by
    construction* ("a non-empty judgeable set always selects"), and that was
    true only while the reference population and the candidate population
    were the same set: the dataset holding the maximum is 0 days behind
    itself and so is never struck. With the reference taken over every
    **established** dataset the holder of the maximum need not be a
    candidate at all, and then every candidate can be skipped.

    Three pins, in the order the argument runs.

    1. **The gate itself is unchanged.** Over every last-read assignment of
    one to three tiers hypothesis can draw, ``_recency_struck`` never
    strikes its whole input and never strikes the holder of the maximum.
    That is a property of the gate -- reused verbatim, T155 deliverable 3 --
    and it is why the survivor set can only empty when the maximum is held
    by a dataset that is **not a candidate**.

    2. **The near-miss, unmoved.** Strap last read ``D-50``, daily watch to
    ``D-7``, both judgeable: the watch holds the reference, the strap is 43
    behind, exactly one is skipped and the watch is selected. Unchanged from
    shipped F006, because here the reference holder *is* a candidate.

    3. **The reachable case.** The same strap beside an overnight tier that
    is established and holds no judged-week day: the overnight tier holds
    the reference at ``D-7``, the strap -- the only candidate -- is 43
    behind and skipped, and ``selected`` is ``None`` with ``judgeable``
    non-empty. This is the geometry AC6 exists for: the returning strap is
    not promoted on a band 36 to 66 days old, and the AC9 fallback presents
    a dataset verdict-free instead.
    """

    @given(
        st.dictionaries(
            st.sampled_from(hrv_trend.TIER_FIDELITY),
            st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
            min_size=1,
            max_size=3,
        )
    )
    def never_all(last_read: dict[str, date]) -> None:
        reference_population = [t for t in hrv_trend.TIER_FIDELITY if t in last_read]
        struck = hrv_trend._recency_struck(reference_population, last_read)
        assert struck != set(reference_population)
        assert max(reference_population, key=lambda t: last_read[t]) not in struck

    never_all()

    rows = _carrier() + _strap(days_between(D - timedelta(days=66), D - timedelta(days=50)))
    rows += _strap(days_between(D - timedelta(days=6), D))
    selection = _select(rows)
    slice_ = selection.describe()
    assert selection.gap(STRAP) == 43, slice_
    assert selection.judgeable == (STRAP, SNAPSHOT) and selection.skipped == (STRAP,), slice_
    assert len(selection.skipped) < len(selection.judgeable), slice_
    assert selection.selected is not None and selection.selected.tier == SNAPSHOT, slice_

    emptied = _strap(days_between(D - timedelta(days=66), D - timedelta(days=50)))
    emptied += _strap([D - timedelta(days=4), D - timedelta(days=2), D])
    emptied += readings(OVERNIGHT, days_between(D - timedelta(days=66), D - timedelta(days=7)), 40.0, hh=8)
    all_skipped = _select(emptied)
    slice_ = all_skipped.describe()
    assert all_skipped.judgeable == (STRAP,), slice_
    assert all_skipped.skipped == (STRAP,), slice_
    assert set(all_skipped.skipped) == set(all_skipped.judgeable), slice_  # shipped F006: a proper subset
    assert all_skipped.selected is None, slice_  # shipped F006: chest_strap_raw selected
    view = hrv_trend.selected_view(hrv_trend.build_series(emptied, AUCKLAND, D))
    assert view.selection is not None and view.selection.selected is None, slice_
    assert view.presented_by != hrv_trend.PRESENTED_SELECTED, f"{view.presented_by} | {slice_}"
    assert hrv_trend.judge(view).verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_


def test_probe_the_selection_contract_holds_on_arbitrary_hand_built_series() -> None:
    """The contract as a property over hand-built datasets (any tier
    present or not, 0..20 post-clip baseline days, 0..7 week days, any
    last-read day inside the window): ``selected`` is ``None`` exactly when
    nothing is judgeable **or every candidate is skipped**; otherwise it is
    the lowest fidelity rank among the judgeable datasets not skipped;
    ``skipped`` is a subset of ``judgeable``; and ``reference`` is the
    latest last-read over the **established** datasets, non-candidates and
    skipped ones included, ``None`` only when none is established.
    Re-pointed by T164: under shipped F006 the reference was the maximum
    over ``selection.judgeable`` alone, ``skipped`` was a **proper** subset
    of it, and ``selected`` was non-``None`` whenever anything was
    judgeable. Hand-built rather than through ``build_series`` so the
    property reaches geometries the row builders above do not draw; the
    probes above are the real path."""
    first, last = D - timedelta(days=66), D - timedelta(days=7)

    def dataset(tier: str, n: int, week: int, last_read: date) -> hrv_trend.HrvDataset:
        base_days = [last_read - timedelta(days=i) for i in range(n)][::-1] if n else []
        baseline = tuple(
            hrv_trend.Reading(d, f"{tier}-{d}", tier, 40.0, datetime(d.year, d.month, d.day, 6, tzinfo=UTC))
            for d in base_days
        )
        window = tuple(
            hrv_trend.Reading(d, f"{tier}-{d}", tier, 40.0, datetime(d.year, d.month, d.day, 6, tzinfo=UTC))
            for d in days_between(D - timedelta(days=6), D)[:week]
        )
        return hrv_trend.HrvDataset(
            tier=tier,
            baseline_window=(first, last),
            series=baseline + window,
            baseline=baseline,
            window=window,
            band=None,
            n=n,
            established=n >= hrv_trend.MIN_BASELINE_READINGS,
            withheld=False,
        )

    shape = st.tuples(st.integers(0, 20), st.integers(0, 7), st.dates(min_value=first, max_value=last))

    @given(st.dictionaries(st.sampled_from(hrv_trend.TIER_FIDELITY), shape, max_size=3))
    def contract(shapes: dict[str, tuple[int, int, date]]) -> None:
        datasets = tuple(dataset(t, *shapes[t]) for t in hrv_trend.TIER_FIDELITY if t in shapes)
        readings_ = tuple(r for d in datasets for r in d.series)
        series = hrv_trend.HrvSeries(D, "Pacific/Auckland", (first, last), (D - timedelta(days=6), D), readings_, (), datasets)
        selection = hrv_trend.select_dataset(series)

        judgeable = [d for d in datasets if d.established and len({r.date for r in d.window}) >= 3]
        established = [d for d in datasets if d.established]
        assert selection.judgeable == tuple(d.tier for d in judgeable)
        assert set(selection.skipped) <= set(selection.judgeable)
        if not established:
            assert selection.reference is None
        else:
            assert selection.reference == max(shapes[d.tier][2] for d in established)
        if not judgeable:
            assert selection.selected is None
            return
        survivors = [t for t in selection.judgeable if t not in selection.skipped]
        if not survivors:
            assert selection.selected is None
        else:
            assert selection.selected is not None and selection.selected.tier == survivors[0]
        for t in selection.judgeable:
            assert (t in selection.skipped) == ((selection.reference - shapes[t][2]).days > 28)

    contract()


# ---------------------------------------------------------------------------
# T158: the withhold (T125/T132) retained at dataset scope (AC24)
#
# ``research/00`` HRV-31: a dataset that could not be selected -- not
# judgeable, or skipped by the recency gate -- and that holds at least
# MIN_WINDOW_READINGS judged-week days, every one later than every judged-week
# day of the dataset being judged, withholds the verdict. ``build_series`` asks it
# of every dataset as if that dataset were the selected one (``verdict_withheld``),
# ``selected_view`` carries the presented dataset's answer, ``judge`` reads it.
# Every pin here prints the slice it compared: the selection line and each
# dataset's n / week days / withheld.
# ---------------------------------------------------------------------------


def _withheld_slice(series: hrv_trend.HrvSeries) -> str:
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    datasets = " ".join(
        f"{d.tier}:n{d.n}/est{int(d.established)}/week{sorted(str(x) for x in {r.date for r in d.window})}/withheld{int(d.withheld)}"
        for d in series.datasets
    )
    line = f"{selection.describe()} | {datasets} | presented={view.tier} withheld={view.withheld} -> {verdict.verdict} {verdict.unavailable_reason}"
    print(line)
    return line


def _strap_to(last_offset: int, value: float = 40.0) -> list[dict]:
    """A daily strap from ``D-66`` to ``D - last_offset`` -- established, and
    covering the judged week's first ``7 - last_offset`` days."""
    return _strap(days_between(D - timedelta(days=66), D - timedelta(days=last_offset)), value)


def _watch(offsets: list[int], value: float = 15.0) -> list[dict]:
    """A ``health_snapshot`` on ``D - k`` for each ``k`` in ``offsets``, deeply
    suppressed at 15 ms unless told otherwise, 07:00."""
    return readings(SNAPSHOT, [D - timedelta(days=k) for k in offsets], value, hh=7)


def test_a_brand_new_device_on_the_selected_datasets_stale_week_withholds_the_verdict() -> None:
    """The task's first failing test, and AC24's own series: a daily strap
    ``D-66..D-3`` (established on 60, four judged-week days ``D-6..D-3``,
    judgeable, the only candidate, selected) and a watch the athlete bought
    on ``D-2`` -- ``D-2, D-1, D`` at 15 ms, **zero** baseline-window days,
    so never judgeable. Without the withhold the strap's stale four mornings
    promote ``hrv_normal`` while the athlete's own three mornings, the ones
    actually suppressed, sit in a dataset no verdict reads: the sixth
    PRIN-14 forbidden-direction population, which shipped F005 closes (T132) and AC21
    forbids regressing.

    Three-valued (``retiring-a-ratified-behaviour-needs-a-three-valued-pin``,
    asked of a *retention*): green on shipped F005 (T132's form B --
    ``never_used`` reads the watch's zero baseline days); **red with the
    withhold deleted** -- perturbation 2026-09-19: dropping the ``withheld``
    pass out of ``build_series`` (every dataset left at the closed default
    is ``hrv_unavailable`` everywhere, so the perturbation is
    ``withheld=False``) promotes ``hrv_normal`` here, and deleting ``not
    series.withheld`` from ``judge`` does the same; green on F006 with the
    withhold at dataset scope. The strap is the selected dataset, its week
    is the four stale mornings, and the verdict is withheld --
    ``week_not_representative`` -- not promoted.
    """
    series = hrv_trend.build_series(_strap_to(3) + _watch([2, 1, 0]), AUCKLAND, D)
    slice_ = _withheld_slice(series)
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)

    assert selection.judgeable == (STRAP,) and selection.selected is not None, slice_
    assert selection.selected.tier == STRAP, slice_
    (watch,) = [d for d in series.datasets if d.tier == SNAPSHOT]
    assert watch.n == 0 and not watch.established and len(_days_of(watch)) == 3, slice_
    assert [r.date for r in view.window] == days_between(D - timedelta(days=6), D - timedelta(days=3)), slice_
    assert view.withheld is True, slice_
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_
    assert verdict.unavailable_reason == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, slice_
    assert verdict.readings_in_window == 4 and verdict.established is True, slice_


def _days_of(dataset: hrv_trend.HrvDataset) -> set[date]:
    return {r.date for r in dataset.window}


@pytest.mark.parametrize(
    ("baseline_days", "expected_withheld", "why"),
    [
        (0, True, "zero baseline days: T132's never-used device, the definitional case"),
        (1, True, "one baseline day: not judgeable; shipped F005's form B left it as shipped (hrv_normal)"),
        (13, True, "thirteen: the last unestablished count; not judgeable, so in the set"),
        (14, False, "fourteen: established and judgeable, so it could have been selected -- not in the set"),
    ],
    ids=["zero", "one", "thirteen", "fourteen"],
)
def test_probe_zero_baseline_days_versus_one_at_dataset_scope(
    baseline_days: int, expected_withheld: bool, why: str
) -> None:
    """Adversarial: the boundary shipped F005 drew and AC24 moves. T132's form
    B widened the withhold to a tier with **zero** baseline-window days
    only ("zero is not a tuned threshold"), leaving a tier with 1..13 days
    exactly as shipped -- **not** withheld, the strap's stale week promoted
    ``hrv_normal``. Measured on the build before this task (T151 carrying
    F005's predicate), 2026-09-19: one baseline day -> ``withheld=False``,
    ``hrv_normal``; thirteen -> the same. AC24 / ``research/00`` HRV-31
    asks the order clause of every dataset that could not have been
    selected -- **not judgeable**, or skipped by the recency gate -- which
    is the honest dataset-scope restatement: a watch with one baseline
    reading is no more able to carry a verdict than one with none, and the
    athlete's suppressed week is just as unread. So zero, one and thirteen
    all withhold here; **this is the one verdict this task moves**
    (``hrv_normal -> hrv_unavailable``: the ``hrv_unavailable`` that
    ``research/00`` PRIN-14 requires where the evidence is insufficient),
    and the confusion-table pin in ``test_hrv_dataset_populations.py``
    records the same move on T130's era-10 row.

    The far boundary, pinned with the argument: at **fourteen** the watch is
    established, holds three week days, is judgeable and is **not** skipped
    (both datasets read on ``D-7``), so it could have been selected and lost
    only on fidelity rank. It is not in the set: the selected dataset
    decides (``research/00`` HRV-14, HRV-20), the watch's own reading is reported in
    ``disagreed_with`` (AC10), and withholding on a dataset that could have
    spoken would be withhold-on-disagreement, the form the decision log
    rejected. Degenerate because 13 -> 14 is the establishment gate itself
    (T116), one reading apart, and the predicate flips on it.

    Held constant: the strap (daily ``D-66..D-3`` at 40 ms, selected), the
    watch's three week days (``D-2, D-1, D`` at 15 ms) and the target.
    Varied: the watch's baseline-window days, ending on ``D-7``.
    """
    watch_baseline = _watch(list(range(7, 7 + baseline_days)), 40.0)
    series = hrv_trend.build_series(_strap_to(3) + _watch([2, 1, 0]) + watch_baseline, AUCKLAND, D)
    slice_ = f"{why}\n{_withheld_slice(series)}"
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    (watch,) = [d for d in series.datasets if d.tier == SNAPSHOT]

    assert watch.n == baseline_days, slice_
    assert watch.established is (baseline_days >= hrv_trend.MIN_BASELINE_READINGS), slice_
    assert (SNAPSHOT in selection.judgeable) is (baseline_days >= hrv_trend.MIN_BASELINE_READINGS), slice_
    assert selection.skipped == (), slice_
    assert selection.selected is not None and selection.selected.tier == STRAP, slice_
    assert view.withheld is expected_withheld, slice_
    if expected_withheld:
        assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_
        assert verdict.unavailable_reason == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, slice_
    else:
        assert verdict.verdict == hrv_trend.VERDICT_NORMAL, slice_
        assert selection.disagreed_with == (SNAPSHOT,), slice_


@pytest.mark.parametrize(
    ("week_offsets", "expected_withheld"),
    [([2, 1, 0], True), ([1, 0], False)],
    ids=["exactly_min_window_readings", "one_fewer"],
)
def test_probe_the_withhold_needs_exactly_min_window_readings_of_the_other_datasets_week(
    week_offsets: list[int], expected_withheld: bool
) -> None:
    """Adversarial: the count guard at its edge. ``MIN_WINDOW_READINGS`` (3)
    on the non-judgeable dataset is the same constant that keeps a stray
    cross-device capture from withholding a legitimate verdict and the one
    that hides the athlete's opening mornings (T125's residual). Three
    brand-new-watch mornings after the strap's last withhold; two do not,
    and the strap's stale week is promoted ``hrv_normal`` -- the residual
    F005's Negative Class prices, unchanged here (user decision 2026-09-18:
    reaching it means acting on fewer than three readings of the new
    device). Degenerate because the predicate flips on one reading, and a
    ``>`` written for ``>=`` would move it to four.

    Held constant: the strap (daily to ``D-3``), the watch's zero baseline
    days, the target. Varied: the watch's week-day count, 3 -> 2.
    """
    series = hrv_trend.build_series(_strap_to(3) + _watch(week_offsets), AUCKLAND, D)
    slice_ = _withheld_slice(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    (watch,) = [d for d in series.datasets if d.tier == SNAPSHOT]

    assert len(_days_of(watch)) == len(week_offsets) and watch.n == 0, slice_
    assert view.tier == STRAP, slice_
    assert view.withheld is expected_withheld, slice_
    assert verdict.verdict == (
        hrv_trend.VERDICT_UNAVAILABLE if expected_withheld else hrv_trend.VERDICT_NORMAL
    ), slice_


@pytest.mark.parametrize(
    ("week_offsets", "expected_withheld"),
    [([3, 2, 1], False), ([2, 1, 0], True)],
    ids=["tied_on_the_selected_datasets_latest_day", "one_day_later"],
)
def test_probe_a_week_day_tied_with_the_selected_datasets_latest_is_not_later(
    week_offsets: list[int], expected_withheld: bool
) -> None:
    """Adversarial: the strict day-order boundary, kept exactly as shipped
    (deliverable 2). The clause is ``min(other's week days) > max(selected's
    week days)`` -- **strictly** later. The strap's latest week day is
    ``D-3``; a watch on ``D-3, D-2, D-1`` shares that day (a morning with
    two captures feeds both datasets, AC3), so its earliest day is tied,
    not later, and nothing is withheld: the strap's own ``D-3`` is in the
    week the verdict reads, and "every one later than every" is false.
    Shift the watch one day, ``D-2, D-1, D``, and it withholds. Degenerate
    because the tie is the one placement where ``>`` and ``>=`` disagree,
    and ``>=`` here would be the count form T125 measured and rejected
    (it re-admits the abandoned trial).

    Held constant: the strap (daily to ``D-3``), three watch week days,
    zero watch baseline days, the target. Varied: the watch's first week
    day, ``D-3`` (tied) -> ``D-2`` (later).
    """
    series = hrv_trend.build_series(_strap_to(3) + _watch(week_offsets), AUCKLAND, D)
    slice_ = _withheld_slice(series)
    view = hrv_trend.selected_view(series)
    (watch,) = [d for d in series.datasets if d.tier == SNAPSHOT]

    assert view.tier == STRAP and max(_days_of(view.selected)) == D - timedelta(days=3), slice_
    assert min(_days_of(watch)) == D - timedelta(days=max(week_offsets)), slice_
    assert view.withheld is expected_withheld, slice_
    assert hrv_trend.judge(view).verdict == (
        hrv_trend.VERDICT_UNAVAILABLE if expected_withheld else hrv_trend.VERDICT_NORMAL
    ), slice_


@pytest.mark.parametrize("c", [0, 1], ids=["carrier_stops_at_the_return", "carrier_records_one_morning_into_it"])
def test_probe_the_carrier_still_recording_through_the_return_disarms_the_withhold(c: int) -> None:
    """Adversarial, and a **named residual pinned as the current fact, not
    fixed here**: T130's disarming case. The athlete's strap era is
    ``D-66..D-43`` (24 days, established), the watch carried the series
    daily to ``D-3+c``, and the strap is back on ``D-2, D-1, D`` at 25 ms
    against a 79 ms era. Under F006 the strap is judgeable **and skipped**
    (last read ``D-43``, 36 behind the watch's ``D-7``, AC6) -- this is
    T125's own arm of the set, the skipped dataset, which AC24's "not
    judgeable" wording alone would drop (IDEA-083) -- and the watch is
    selected.

    ``c = 0``: the watch stopped on ``D-3``, every strap day is later than
    every watch day, withheld, ``hrv_unavailable``.

    ``c = 1``: the watch also captured ``D-2``. The order clause is false
    on that one morning, nothing is withheld, and the watch's own five
    mornings are judged against the watch's own band: ``hrv_normal`` on a
    week whose strap mornings read 25 ms. That is T130's finding -- the
    order clause is a fact about the judged week, and one carrier morning
    inside the return erases it (0 of 2050 withheld at any ``c >= 1``) --
    and it is **the disarm, pinned as current behaviour**: no predicate of
    this family survives overlap without an unjustified parameter (T130,
    nine candidates measured), the user carried the question to this
    sprint's measurement tasks, and T161/T162 sweep it against shipped
    F005 with capture density varied on both datasets (AC19/AC21). The
    populations suite pins the same two rows on T130's own generator.

    Held constant: the strap era and its values, the strap's three week
    days, the watch's values and density. Varied: ``c``, the watch's last
    day, ``D-3`` -> ``D-2``.
    """
    rows = _carrier(last=D - timedelta(days=3 - c))
    rows += _strap(days_between(D - timedelta(days=66), D - timedelta(days=43)), 79.0)
    rows += _strap([D - timedelta(days=2), D - timedelta(days=1), D], 25.0)
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_ = _withheld_slice(series)
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)

    assert selection.judgeable == (STRAP, SNAPSHOT) and selection.skipped == (STRAP,), slice_
    assert selection.gap(STRAP) == 36, slice_
    assert view.tier == SNAPSHOT, slice_
    if c == 0:
        assert view.withheld is True, slice_
        assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_
        assert verdict.unavailable_reason == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, slice_
    else:
        assert D - timedelta(days=2) in _days_of(view.selected), slice_
        assert view.withheld is False, slice_
        assert verdict.verdict == hrv_trend.VERDICT_NORMAL, slice_
        assert all(r.tier == SNAPSHOT for r in view.window), slice_


def test_the_withhold_reads_the_skipped_set_selection_reads_and_is_asked_of_every_dataset() -> None:
    """The set the order clause is asked about is "every dataset that could
    not have been selected": not judgeable, or skipped by the recency gate.
    T125's lesson is that the struck set must be **the** set, not a second
    transcription -- so ``build_series`` calls the gate ``select_dataset``
    reuses (``_recency_struck``, over ``_last_read`` of the same
    baseline-window slice) and this pin holds the two answers equal on
    every dataset of every geometry above: recomputing ``verdict_withheld``
    with ``select_dataset``'s own ``skipped`` reproduces the ``withheld``
    each dataset carries. On the source, ``build_series`` calls
    ``verdict_withheld`` once and ``_recency_struck`` once, and
    ``select_dataset``'s own count is unchanged (its pin above).
    """
    geometries = {
        "brand new watch": _strap_to(3) + _watch([2, 1, 0]),
        "watch with one baseline day": _strap_to(3) + _watch([2, 1, 0]) + _watch([7], 40.0),
        "tied day": _strap_to(3) + _watch([3, 2, 1]),
        "return, carrier stopped": _carrier(last=D - timedelta(days=3))
        + _strap(days_between(D - timedelta(days=66), D - timedelta(days=43)), 79.0)
        + _strap([D - timedelta(days=2), D - timedelta(days=1), D], 25.0),
        "illness week, nothing judgeable": _carrier(last=D - timedelta(days=7)) + _strap(baseline_days(20)),
        "no baseline at all": _watch([2, 1, 0]) + _strap([D - timedelta(days=5)]),
    }
    for name, rows in geometries.items():
        series = hrv_trend.build_series(rows, AUCKLAND, D)
        selection = hrv_trend.select_dataset(series)
        slice_ = f"{name}: {_withheld_slice(series)}"
        for dataset in series.datasets:
            recomputed = bool(_within_baseline(series)) and hrv_trend.verdict_withheld(
                dataset, series.datasets, selection.skipped
            )
            assert dataset.withheld is recomputed, slice_

    tree = ast.parse(Path(hrv_trend.__file__).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_series")
    calls = [n.func.id for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count("verdict_withheld") == 1, calls
    assert calls.count("_recency_struck") == 1, calls


def _within_baseline(series: hrv_trend.HrvSeries) -> tuple[hrv_trend.Reading, ...]:
    return hrv_trend._within(series.readings, series.baseline_window)


# ---------------------------------------------------------------------------
# T156: the presentation fallback, formalised (F006 AC9; F005's rule 3 over
# datasets, ``research/00`` HRV-59)
#
# When no dataset is judgeable, ``selected_view`` presents one so that
# ``baseline`` / ``band`` carry a value and no verdict is conferred. The
# clause order is T155's provisional one, kept: (1) the established dataset
# read last in the baseline window, ties by n then fidelity; (2) with none
# established, the densest by n, ties to fidelity; (3) with no baseline
# reading of any tier, the densest in the judged week, ties to fidelity.
# Each clause is the shape F005's resolver gave it, which is what keeps the
# three shipped illness-week pins above and the SW+20 reset pin unmoved.
# ``SingleDatasetView.presented_by`` names the clause; every pin prints the
# selection line, each dataset's n / last read / week days, and the clause.
# ---------------------------------------------------------------------------


def _fallback_slice(series: hrv_trend.HrvSeries) -> str:
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    datasets = " ".join(
        f"{d.tier}:n{d.n}/est{int(d.established)}/last{selection.last_read.get(d.tier)}"
        f"/week{len({r.date for r in d.window})}"
        for d in series.datasets
    )
    line = f"{selection.describe()} | {datasets} | presented={view.tier} by={view.presented_by}"
    print(line)
    return line


@pytest.mark.parametrize(
    ("strap_days", "snapshot_days", "expected"),
    [
        (days_between(D - timedelta(days=66), D - timedelta(days=20)), days_between(D - timedelta(days=40), D - timedelta(days=7)), SNAPSHOT),
        (days_between(D - timedelta(days=30), D - timedelta(days=7)), days_between(D - timedelta(days=66), D - timedelta(days=7)), SNAPSHOT),
        (days_between(D - timedelta(days=66), D - timedelta(days=7)), days_between(D - timedelta(days=66), D - timedelta(days=7)), STRAP),
    ],
    ids=["read_last_beats_denser_and_higher_fidelity", "tied_on_read_last_then_n", "tied_on_both_then_fidelity"],
)
def test_the_fallback_presents_the_established_dataset_read_last_ties_by_n_then_fidelity(
    strap_days: list[date], snapshot_days: list[date], expected: str
) -> None:
    """Clause 1, each term in turn, on an empty judged week with both
    datasets established. A strap of 47 days last read ``D-20`` loses to a
    snapshot of 34 last read ``D-7``: read last beats both density and
    fidelity (T094's direction, "the tier the athlete is actually on").
    Both read ``D-7``: the 60-day snapshot beats the 24-day strap on n.
    Tied on both: the strap, on fidelity. The presented dataset carries
    ``presented_by == FALLBACK_ESTABLISHED_READ_LAST`` and ``judge`` says
    ``week_too_thin`` on it -- the fallback presents, it does not judge.
    Perturbation: dropping the read-last term (densest established) reds
    the first row; dropping the n term reds the second.
    """
    series = hrv_trend.build_series(_strap(strap_days, 40.0) + _carrier(snapshot_days[0], snapshot_days[-1]), AUCKLAND, D)
    slice_ = _fallback_slice(series)
    view = hrv_trend.selected_view(series)

    assert all(d.established for d in series.datasets) and hrv_trend.select_dataset(series).selected is None, slice_
    assert view.tier == expected and view.presented_by == hrv_trend.FALLBACK_ESTABLISHED_READ_LAST, slice_
    assert hrv_trend.judge(view).unavailable_reason == hrv_trend.REASON_WEEK_TOO_THIN, slice_
    assert view.selected is not None and view.selected.n == len(view.baseline), slice_


@pytest.mark.parametrize(
    ("strap_days", "snapshot_days", "expected", "clause", "reason"),
    [
        (
            days_between(D - timedelta(days=9), D - timedelta(days=7)),
            days_between(D - timedelta(days=13), D - timedelta(days=7)),
            SNAPSHOT,
            "FALLBACK_DENSEST_BASELINE",
            hrv_trend.REASON_WEEK_TOO_THIN,
        ),
        (
            days_between(D - timedelta(days=11), D - timedelta(days=7)),
            days_between(D - timedelta(days=11), D - timedelta(days=7)),
            STRAP,
            "FALLBACK_DENSEST_BASELINE",
            hrv_trend.REASON_WEEK_TOO_THIN,
        ),
        (
            [D - timedelta(days=1), D],
            [D - timedelta(days=2), D - timedelta(days=1), D],
            SNAPSHOT,
            "FALLBACK_DENSEST_WEEK",
            hrv_trend.REASON_NO_BAND,
        ),
        (
            [D - timedelta(days=1), D],
            [D - timedelta(days=1), D],
            STRAP,
            "FALLBACK_DENSEST_WEEK",
            hrv_trend.REASON_NO_BAND,
        ),
    ],
    ids=["densest_baseline", "densest_baseline_tied_then_fidelity", "densest_week", "densest_week_tied_then_fidelity"],
)
def test_the_fallback_falls_to_the_densest_baseline_then_to_the_densest_week(
    strap_days: list[date], snapshot_days: list[date], expected: str, clause: str, reason: str
) -> None:
    """Clauses 2 and 3. With nothing established, the densest by n (a
    seven-day snapshot over a three-day strap; ties to the strap on
    fidelity), and ``judge`` reports ``week_too_thin`` on a band it could
    build from those few days. With no baseline reading of any tier, the
    densest in the judged week (three snapshot mornings over two strap
    ones; ties to the strap), and the reason is ``no_band`` on a real tier
    -- never ``no_tier_sustains_a_trend``, which only an empty series
    reports (``test_hrv_unavailable_reason.py``, T156). Perturbation:
    clause 3 by fidelity alone reds the third row.
    """
    series = hrv_trend.build_series(_strap(strap_days, 40.0) + _carrier(snapshot_days[0], snapshot_days[-1]), AUCKLAND, D)
    slice_ = _fallback_slice(series)
    view = hrv_trend.selected_view(series)

    assert not any(d.established for d in series.datasets), slice_
    assert hrv_trend.select_dataset(series).selected is None, slice_
    assert view.tier == expected and view.presented_by == getattr(hrv_trend, clause), slice_
    assert hrv_trend.judge(view).unavailable_reason == reason, slice_
