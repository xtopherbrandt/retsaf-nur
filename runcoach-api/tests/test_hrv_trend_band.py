"""T084 -- ``metrics/hrv_trend.py``: the SWC band, the thin-data guards and the verdict (F005).

The second half of the trend: T083's clean per-day series in, a band and a
verdict out. Everything here is a pure unit test over hand-built rows fed
through ``build_series`` and then ``judge`` -- no database, no config, no
clock -- which is what the module's purity buys.

**The contract table below was authored before the implementation**, from
the task's Scope and F005's acceptance criteria alone (construction
reference "The band"; decision log rows "Thin and degenerate data" and
"Baseline window placement"):

- no band at all when the baseline holds fewer than 2 readings (SD is
  undefined at N=1), and the verdict is unavailable;
- ``established`` is true at 14 baseline readings and false below;
- the verdict is unavailable when the judged window holds fewer than 3
  readings, whatever the baseline;
- otherwise ``suppressed`` when the window mean is strictly below ``band.lo``
  *and* the baseline is established; ``normal`` when the mean is inside or
  above the band *and* the baseline is established;
- **every verdict but ``hrv_unavailable`` requires an established baseline**
  (T116, 2026-09-15, a behaviour change). Neither cell the spec's text left
  unnamed -- below the band on an unestablished baseline, and inside or above
  it on one -- reads as a verdict: both are **unavailable**. spec/03 §3.7.3 says either
  position on an unestablished baseline is ``hrv_unavailable`` (``baseline_unestablished``), and
  ``hrv_normal`` would tell Section 6 that readiness is intact on the
  strength of a baseline the same response reports unestablished -- up-
  regulating on weak evidence, which ``research/00`` PRIN-14 forbids.
  ``hrv_unavailable`` makes the readiness logic widen its guardrails instead.
- **The asymmetry was the defect** ([[IDEA-062]]). Until T116 this docstring
  argued the cautious reading (PRIN-14) for the below-band cell alone and the table returned ``normal``
  regardless of establishment for its two neighbours, so a week judged
  against a band built from 2 to 13 readings reported ``hrv_normal`` -- and
  that is reachable after a **coverage-gap** reset, which collapses the
  baseline, so the athlete then traverses 20 unestablished days, 12 of which
  used to read ``hrv_normal`` (``R+8 .. R+19``; the first eight had no band)
  -- **corrected in place 2026-09-18 ([[T142]]), following ``research/00``
  HRV-34: until now this said the cell was reachable after *every* reset, naming
  a gap reset and a tier change alike as collapsing the baseline, and that is
  false of a tier change. A clean source-tier change does not collapse the
  baseline at all (``established`` stays true, ``n`` decays 60 -> 47), so it
  has zero unestablished days and this cell is unreachable through it; its
  18-day silence is week coverage on the abandoned tier, a different
  mechanism ([[T138]]).** Eight of the 90 rows below
  moved with the
  rule; the direct pin is
  ``test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``.

The table enumerates ``{baseline n: 0, 1, 2, 13, 14, 41} x {window n: 0, 1,
2, 3, 7} x {mean: below, inside, above}`` exhaustively -- 6 x 5 x 3 = **90**
rows, the count ``test_the_contract_table_is_exhaustive_over_its_axes``
asserts and the count the paragraph above uses -- boring rows included, so an
omitted cell would be visible (contract-tables-need-an-independent-oracle).
The window axis is five values, not four: ``window n: 1`` was dropped from
this sentence while ``CONTRACT_TABLE`` kept it, which made the enumeration
describe a 72-row table this module has never had (corrected 2026-09-15,
T121, in ``6502da7``; that correction was filed against T122 until review
cycle 8 traced both notes back to the commit that wrote them).
**All three axes of this enumeration are asserted as sets**, not just
counted: the window axis since ``2745b12``, the baseline axis since
``acfebae`` -- which found that substituting ``41`` for ``40`` here left all
122 tests in this module green while this sentence went on naming 41 -- and
the position axis since review cycle 8 iteration 3, this sentence having
said "both axes" over a table built from three.

Perturbation evidence for the four published claims (IDEA-034) is recorded
in the docstring of the test that pins each claim.
"""

from __future__ import annotations

import ast
import math
import statistics
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend
from runcoach_api.metrics.hrv_trend import judge

AUCKLAND = ZoneInfo("Pacific/Auckland")

# The target date F005's canonical response example uses: baseline
# [2026-07-04, 2026-09-01], judged week [2026-09-02, 2026-09-08].
D = date(2026, 9, 8)

STRAP = "chest_strap_raw"

NORMAL = "hrv_normal"
SUPPRESSED = "hrv_suppressed"
UNAVAILABLE = "hrv_unavailable"


# ---------------------------------------------------------------------------
# row builders
# ---------------------------------------------------------------------------


def local(day: date, hh: int = 6, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    """The stored ``start_time`` of a capture taken at local ``hh:mm`` on
    ``day`` -- converted to UTC and spelled ``+00:00`` as ``mapping.py``
    stores it."""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(day: date, value: float, tier: str = STRAP, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{day.isoformat()}-{tier}-{value}",
        "start_time": local(day),
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def baseline_days(n: int, target: date = D) -> list[date]:
    """``n`` consecutive local days ending on ``target - 7``, the last day of
    the baseline window; the earliest is ``target - 7 - (n - 1)``."""
    end = target - timedelta(days=7)
    return [end - timedelta(days=i) for i in range(n)][::-1]


def window_days(n: int, target: date = D) -> list[date]:
    """``n`` consecutive local days ending on ``target``."""
    return [target - timedelta(days=i) for i in range(n)][::-1]


def baseline_rows(values: list[float], target: date = D) -> list[dict]:
    """One reading per day, the last landing on ``target - 7``."""
    return [row(day, value) for day, value in zip(baseline_days(len(values), target), values, strict=True)]


def window_rows(values: list[float], target: date = D) -> list[dict]:
    return [row(day, value) for day, value in zip(window_days(len(values), target), values, strict=True)]


def alternating(n: int, a: float = 40.0, b: float = 44.0) -> list[float]:
    """``a, b, a, b, ...`` -- a baseline whose dispersion is known and small,
    so ``inside``/``below``/``above`` window values can be placed by hand."""
    return [a if i % 2 == 0 else b for i in range(n)]


def build_series(
    rows: list[dict], zone: ZoneInfo, target: date, earliest_start_time: str | None = None
) -> hrv_trend.SingleDatasetView:
    """``hrv_trend.build_series`` through F006's selection (T155): the
    selected dataset -- or the presentation fallback when none is judgeable
    -- on the F005 series shape, so every band and verdict pin here reads
    what it read before the N-way partition."""
    return hrv_trend.selected_view(hrv_trend.build_series(rows, zone, target, earliest_start_time))


def verdict_for(baseline: list[float], window: list[float], target: date = D) -> hrv_trend.HrvVerdict:
    return judge(build_series(baseline_rows(baseline, target) + window_rows(window, target), AUCKLAND, target))


def expected_band(values: list[float]) -> tuple[float, float, float]:
    """``(mean, half_width, floored)`` recomputed here from the rule in the
    construction reference -- sample SD of the log series, halved, floored
    at 0.01 -- so a test can state what the band must be without reading it
    back from the module."""
    logs = [math.log(v) for v in values]
    mean = statistics.fmean(logs)
    computed = 0.5 * statistics.stdev(logs)
    return mean, max(computed, 0.01), computed < 0.01


# ---------------------------------------------------------------------------
# the first failing test: the band is unit-invariant
# ---------------------------------------------------------------------------


def test_the_band_is_unit_invariant() -> None:
    """The same 20 readings expressed in **seconds** instead of milliseconds
    give an identical half-width, and ``lo <= hi`` in both.

    This is the test the domain spec's literal ``0.5 * CV(ln rMSSD)`` fails by
    construction: ``CV = SD / mean`` is a ratio to the origin of a log scale,
    which is arbitrary; on the seconds series ``mean(ln)`` is negative and the
    band inverts (``lo > hi``). ``SD(ln)`` is unchanged by the unit change
    (``ln(x / 1000) = ln(x) - ln(1000)``, a shift). Perturbation: implement
    ``0.5 * stdev(logs) / fmean(logs)`` and this goes red on the seconds
    branch with ``lo > hi``.
    """
    ms = [38.0, 45.0, 52.0, 41.0, 47.0, 39.0, 55.0, 44.0, 48.0, 42.0, 50.0, 37.0, 46.0, 43.0]
    ms += [49.0, 40.0, 53.0, 45.0, 41.0, 47.0]
    seconds = [v / 1000.0 for v in ms]

    in_ms = verdict_for(ms, [45.0, 45.0, 45.0])
    in_s = verdict_for(seconds, [0.045, 0.045, 0.045])

    assert in_ms.band is not None and in_s.band is not None
    assert in_ms.band.half_width == pytest.approx(in_s.band.half_width, abs=1e-12)
    assert in_ms.band.lo <= in_ms.band.hi
    assert in_s.band.lo <= in_s.band.hi
    # And the shift is the unit change and nothing else.
    assert in_ms.band.mean - in_s.band.mean == pytest.approx(math.log(1000.0), abs=1e-9)


# ---------------------------------------------------------------------------
# the band statistic
# ---------------------------------------------------------------------------


def test_the_half_width_is_half_the_sample_sd_of_the_log_baseline() -> None:
    """A 14-reading baseline whose half-width was computed by hand (sample SD,
    ``n - 1``) outside this module: ``0.5 * stdev(ln)`` = 0.05890... and the
    population estimator would give 0.05676... -- a 3.8% gap that a test with
    a loose tolerance would not see. Perturbation: swap ``stdev`` for
    ``pstdev`` and this goes red.
    """
    ms = [38.0, 45.0, 52.0, 41.0, 47.0, 39.0, 55.0, 44.0, 48.0, 42.0, 50.0, 37.0, 46.0, 43.0]
    logs = [math.log(v) for v in ms]

    result = verdict_for(ms, [45.0, 45.0, 45.0])

    assert result.band is not None
    assert result.band.half_width == pytest.approx(0.5 * statistics.stdev(logs), abs=1e-12)
    assert result.band.half_width != pytest.approx(0.5 * statistics.pstdev(logs), abs=1e-4)
    assert result.band.mean == pytest.approx(statistics.fmean(logs), abs=1e-12)
    assert result.band.lo == pytest.approx(result.band.mean - result.band.half_width, abs=1e-12)
    assert result.band.hi == pytest.approx(result.band.mean + result.band.half_width, abs=1e-12)
    assert result.band.floored is False


def test_the_band_uses_the_sample_estimator_not_the_population_one() -> None:
    """Pinned on the call graph as well as numerically: ``pstdev`` returns
    ``0.0`` for a single reading and would manufacture a floored band from
    nothing. Asserted over the AST, not the source text, so the docstring may
    name ``pstdev`` to say why it is never called."""
    tree = ast.parse(Path(hrv_trend.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "pstdev" not in called
    assert "stdev" in called


def test_the_floor_fires_for_a_degenerate_baseline() -> None:
    """Fourteen identical readings: SD is 0, so the computed half-width is
    below the floor and the floor is the band. Perturbation: delete the
    ``max(..., BAND_FLOOR)`` and this goes red (half_width 0.0, floored
    False)."""
    result = verdict_for([40.0] * 14, [40.0, 40.0, 40.0])

    assert result.band is not None
    assert result.band.half_width == hrv_trend.BAND_FLOOR == 0.01
    assert result.band.floored is True
    assert result.band.lo == pytest.approx(math.log(40.0) - 0.01)
    assert result.band.hi == pytest.approx(math.log(40.0) + 0.01)


def test_a_just_sub_floor_dispersion_is_floored_and_a_just_super_floor_one_is_not() -> None:
    """The floor boundary from both sides. Alternating ``40 / 40.4`` gives
    ``0.5 * SD(ln)`` = 0.00258 (floored); alternating ``40 / 41.8`` gives
    0.01142 (not floored). Both branches, so the discriminator has a reachable
    negative case."""
    floored = verdict_for(alternating(14, 40.0, 40.4), [40.0, 40.0, 40.0])
    computed = verdict_for(alternating(14, 40.0, 41.8), [40.0, 40.0, 40.0])

    assert floored.band is not None and floored.band.floored is True
    assert floored.band.half_width == 0.01
    assert computed.band is not None and computed.band.floored is False
    _, expected_half, _ = expected_band(alternating(14, 40.0, 41.8))
    assert computed.band.half_width == pytest.approx(expected_half, abs=1e-12)
    assert computed.band.half_width > 0.01


def test_a_dispersion_exactly_at_the_floor_is_not_floored() -> None:
    """The exact-floor boundary (review cycle 3, G16: ``computed <=
    BAND_FLOOR`` survived). ``half_width = max(computed, BAND_FLOOR)``: at
    equality the two branches give the same width, and ``floored`` -- the
    response's account of which branch fired -- must say the computed
    value was used, since it was not below the floor. The ln values
    ``-0.02, 0, 0.02`` have a sample SD of exactly ``0.02`` in floating
    point (the variance is an exact square), so ``0.5 * SD`` is exactly
    ``0.01``. Perturbation (``<=``): ``floored`` is ``True`` -- red."""
    band = hrv_trend.build_band([-0.02, 0.0, 0.02])

    assert band is not None
    assert band.half_width == hrv_trend.BAND_FLOOR == 0.01
    assert band.floored is False


def test_the_floor_does_not_fire_for_an_ordinary_athlete() -> None:
    """The construction reference's realistic athlete (rMSSD ~45 ms, day-to-day
    SD ~9 ms): ``0.5 * SD(ln)`` is about 0.05, five times the floor, so the
    computed value is the band. A floor at 0.05 -- the value the first draft
    proposed -- would *be* the band for this athlete and the computed branch
    would never be exercised. Perturbation: set ``BAND_FLOOR = 0.05`` and this
    goes red."""
    ms = [38.0, 45.0, 52.0, 41.0, 47.0, 39.0, 55.0, 44.0, 48.0, 42.0, 50.0, 37.0, 46.0, 43.0]
    ms += [49.0, 40.0, 53.0, 45.0, 41.0, 47.0, 36.0, 54.0, 44.0, 46.0, 39.0, 51.0, 43.0, 48.0]
    _, expected_half, expected_floored = expected_band(ms)
    assert expected_floored is False, "the fixture must have an ordinary spread"
    assert 0.04 < expected_half < 0.07, "the fixture must sit near the reference's 0.0526"

    result = verdict_for(ms, [45.0, 45.0, 45.0])

    assert result.band is not None
    assert result.band.floored is False
    assert result.band.half_width == pytest.approx(expected_half, abs=1e-12)
    assert result.band.half_width > hrv_trend.BAND_FLOOR


def test_lo_is_never_above_hi() -> None:
    """Over a spread of baselines -- two readings, identical readings, wide
    readings, a seconds-scaled series -- the band never inverts."""
    for values in (
        [40.0, 44.0],
        [40.0] * 14,
        [10.0, 200.0, 15.0, 150.0, 30.0, 90.0, 12.0, 180.0, 20.0, 60.0, 11.0, 170.0, 25.0, 80.0],
        [v / 1000.0 for v in alternating(20)],
        [1.0] * 14,
    ):
        result = verdict_for(values, [40.0, 40.0, 40.0])
        assert result.band is not None
        assert result.band.lo <= result.band.hi, values
        assert result.band.half_width >= hrv_trend.BAND_FLOOR


# ---------------------------------------------------------------------------
# no band below two readings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1])
def test_no_band_is_asserted_below_two_baseline_readings(n: int) -> None:
    """``statistics.stdev`` raises ``StatisticsError`` below two readings and
    that is caught at the one place the band is built. No band object at all
    -- not a band with ``None`` bounds -- and the verdict is unavailable
    however full the judged week is. Perturbation: ``pstdev`` returns ``0.0``
    at ``n = 1`` and would manufacture a floored band; the ``n = 1`` row goes
    red."""
    result = verdict_for([40.0] * n, [30.0] * 7)

    assert result.band is None
    assert result.verdict == UNAVAILABLE
    assert result.established is False
    assert result.baseline_n == n
    assert result.readings_in_window == 7
    assert result.below_by is None


def test_two_baseline_readings_are_enough_for_a_band_but_not_for_a_verdict_to_suppress() -> None:
    """At exactly two readings SD is defined, so a band exists (T091's chart
    draws it) -- but ``established`` is false, so a below-band week is
    withheld rather than called suppressed."""
    result = verdict_for([40.0, 44.0], [30.0, 30.0, 30.0])

    assert result.band is not None
    assert result.established is False
    assert result.baseline_n == 2
    assert result.verdict == UNAVAILABLE
    assert result.below_by is None


def test_build_band_is_the_one_place_that_catches_the_statistics_error() -> None:
    """The band builder itself, with the log values in hand."""
    assert hrv_trend.build_band([]) is None
    assert hrv_trend.build_band([math.log(40.0)]) is None
    band = hrv_trend.build_band([math.log(40.0), math.log(44.0)])
    assert band is not None
    mean, half, floored = expected_band([40.0, 44.0])
    assert band.mean == pytest.approx(mean, abs=1e-12)
    assert band.half_width == pytest.approx(half, abs=1e-12)
    assert band.floored is floored


# ---------------------------------------------------------------------------
# the windows are disjoint
# ---------------------------------------------------------------------------


def test_the_windows_are_disjoint_so_a_suppressed_week_cannot_lower_its_own_band() -> None:
    """A 41-reading baseline at ~44 ms and a judged week at 25 ms. The band
    must equal the band of the baseline alone: none of the week's readings
    contribute to the baseline mean or SD. Perturbation: extend the baseline
    to ``[D-66, D]`` and the mean drops, the SD widens, and this goes red.
    """
    baseline = alternating(41, 42.0, 46.0)
    week = [25.0] * 7
    only_baseline = expected_band(baseline)
    with_week_inside = expected_band(baseline + week)
    assert only_baseline[0] != pytest.approx(with_week_inside[0], abs=1e-3)

    result = verdict_for(baseline, week)

    assert result.band is not None
    assert result.band.mean == pytest.approx(only_baseline[0], abs=1e-12)
    assert result.band.half_width == pytest.approx(only_baseline[1], abs=1e-12)
    assert result.baseline_n == 41
    assert result.readings_in_window == 7
    assert result.verdict == SUPPRESSED


def test_d_minus_7_is_the_last_baseline_day_and_d_minus_6_the_first_window_day() -> None:
    """Closed intervals, adjacent, no reading in both: ``[D-66, D-7]`` and
    ``[D-6, D]``. A reading on ``D-7`` is counted in ``baseline_n`` and not in
    ``readings_in_window``; ``D-6`` the other way; ``D-67`` in neither."""
    rows = [row(D - timedelta(days=67), 40.0, session_id="before"), row(D - timedelta(days=66), 40.0, session_id="first")]
    # Two fillers so no silence exceeds GAP_RESET_DAYS (21) local days: since
    # T092 a 59-day gap between "first" and "last-baseline" would (correctly)
    # reset the baseline on D-7 and leave baseline_n == 1.
    rows += [row(D - timedelta(days=46), 40.0, session_id="filler-46"), row(D - timedelta(days=26), 40.0, session_id="filler-26")]
    rows += [row(D - timedelta(days=7), 40.0, session_id="last-baseline")]
    rows += [row(D - timedelta(days=6), 40.0, session_id="first-window"), row(D, 40.0, session_id="last-window")]

    series = build_series(rows, AUCKLAND, D)
    result = judge(series)

    assert series.reset_on is None  # the fixture is gap-free, so the boundary is T083's, unclipped
    assert result.baseline_n == 4  # "first", "filler-46", "filler-26", "last-baseline"
    assert result.readings_in_window == 2
    assert result.baseline_n + result.readings_in_window == 6  # "before" is in neither


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------


def test_a_settled_athlete_inside_the_band_reads_normal() -> None:
    """F005's first scenario: 41 chest-strap readings, a full week, the mean
    inside the band."""
    result = verdict_for(alternating(41), [42.0] * 7)

    assert result.verdict == NORMAL
    assert result.band is not None
    assert result.band.lo < result.ln_rmssd_7d_mean < result.band.hi
    assert result.ln_rmssd_7d_mean == pytest.approx(math.log(42.0), abs=1e-12)
    assert result.established is True
    assert result.baseline_n == 41
    assert result.readings_in_window == 7
    assert result.below_by is None


def test_a_mean_above_the_band_is_normal_not_unavailable() -> None:
    """Strictly above ``band.hi`` is normal -- readiness intact, not "out of
    band". Perturbation: make the verdict ``suppressed iff mean outside the
    band`` (a symmetric test) and this goes red."""
    result = verdict_for(alternating(41), [60.0] * 7)

    assert result.band is not None
    assert result.ln_rmssd_7d_mean > result.band.hi
    assert result.verdict == NORMAL
    assert result.below_by is None


def test_a_multi_day_decline_below_the_band_reads_suppressed_with_how_far_below() -> None:
    """``below_by`` is ``lo - mean``, positive, and stated on the verdict."""
    result = verdict_for(alternating(41), [30.0] * 5)

    assert result.band is not None
    assert result.verdict == SUPPRESSED
    assert result.ln_rmssd_7d_mean == pytest.approx(math.log(30.0), abs=1e-12)
    assert result.below_by == pytest.approx(result.band.lo - math.log(30.0), abs=1e-12)
    assert result.below_by > 0
    assert result.readings_in_window == 5


def test_a_mean_exactly_on_the_lower_bound_is_normal() -> None:
    """``suppressed`` is *strictly* below ``lo``. The window is built from
    the log-space bound so the mean lands on it exactly."""
    baseline = alternating(41)
    mean, half, _ = expected_band(baseline)
    on_the_bound = math.exp(mean - half)

    result = verdict_for(baseline, [on_the_bound] * 3)

    assert result.band is not None
    assert result.ln_rmssd_7d_mean == pytest.approx(result.band.lo, abs=1e-12)
    assert result.verdict == NORMAL


def test_a_thin_baseline_can_never_produce_a_suppression_verdict() -> None:
    """Thirteen readings -- one short -- and a week far below the band: not
    suppressed, ``established`` false, and the size that fell short is on
    the result. Perturbation: delete the ``established`` gate from the verdict
    and this goes red (``hrv_suppressed``)."""
    result = verdict_for(alternating(13), [25.0] * 7)

    assert result.band is not None, "the band exists at 13; only the verdict is withheld"
    assert result.ln_rmssd_7d_mean < result.band.lo
    assert result.verdict != SUPPRESSED
    assert result.verdict == UNAVAILABLE
    assert result.established is False
    assert result.baseline_n == 13
    assert result.below_by is None


def test_established_flips_at_exactly_fourteen_baseline_readings() -> None:
    thin = verdict_for(alternating(13), [25.0] * 7)
    enough = verdict_for(alternating(14), [25.0] * 7)

    assert (thin.established, thin.verdict) == (False, UNAVAILABLE)
    assert (enough.established, enough.verdict) == (True, SUPPRESSED)
    assert hrv_trend.MIN_BASELINE_READINGS == 14


def test_a_thin_baseline_inside_the_band_is_unavailable_not_normal() -> None:
    """T116 (2026-09-15, behaviour change; [[IDEA-062]]): the establishment
    gate is symmetric, so the ``normal`` is withheld on a thin baseline
    exactly as the suppression is. Five baseline readings and a week sitting
    inside their band read ``hrv_unavailable`` -- ``hrv_normal`` here would
    tell Section 6 readiness is intact on a baseline the same response
    reports unestablished, the up-regulating direction ``research/00`` PRIN-14
    forbids, and it is reachable after every **coverage-gap** reset the
    feature performs -- corrected in place 2026-09-18 ([[T142]]) from "after
    every reset the feature performs", which is false of a tier change: a
    clean source-tier change leaves ``established`` true (``n`` decays 60 ->
    47) and traverses zero unestablished days, so it never reaches this cell
    ([[T138]]).

    Until T116 this same series asserted ``hrv_normal``; the band is still
    reported, because the band is a property of the baseline and only the
    verdict is withheld. Perturbation: drop the ``established`` gate from
    ``judge``'s inside-or-above arm and this goes red (``hrv_normal``)."""
    result = verdict_for(alternating(5), [42.0] * 7)

    assert result.verdict == UNAVAILABLE
    assert result.verdict != NORMAL
    assert result.established is False
    assert result.baseline_n == 5
    assert result.band is not None, "the band is still reported; only the verdict is withheld"
    assert result.band.lo <= result.ln_rmssd_7d_mean <= result.band.hi
    assert result.below_by is None


def test_a_thin_baseline_above_the_band_is_unavailable_too() -> None:
    """The third cell of the same rule, and a distinct failure mode from the
    one above: a gate written as ``if window_mean <= band.hi`` would keep this
    one red while the inside cell passed. Seven window readings well above a
    five-reading baseline's band still read ``hrv_unavailable``."""
    result = verdict_for(alternating(5), [60.0] * 7)

    assert result.band is not None
    assert result.ln_rmssd_7d_mean > result.band.hi
    assert result.verdict == UNAVAILABLE
    assert result.established is False


def test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings() -> None:
    """The companion of ``test_established_flips_at_exactly_fourteen_baseline_readings``
    on the other side of the band, and the input that **catches** a gate
    written as ``>= 13``: 13 readings inside the band are unavailable, 14 are
    normal, and nothing but the baseline size moved. Measured under that
    mutation (``MIN_BASELINE_READINGS`` = 13, 2026-09-15): ``thin.established``
    becomes ``True`` and ``thin.verdict`` becomes ``hrv_normal``, so the first
    assertion below goes **red** -- which is the whole of this test's
    discriminating power, and the reverse of what this docstring claimed
    until T121 ("the input that would stay green"; the note said T122 until
    review cycle 8 re-attributed it to ``6502da7``, the commit that actually
    wrote it)."""
    thin = verdict_for(alternating(13), [42.0] * 7)
    enough = verdict_for(alternating(14), [42.0] * 7)

    assert (thin.established, thin.verdict) == (False, UNAVAILABLE)
    assert (enough.established, enough.verdict) == (True, NORMAL)
    assert thin.band is not None and enough.band is not None


def test_too_few_readings_this_week_is_unavailable_not_normal() -> None:
    """An established baseline but two window readings: unavailable, with
    ``readings_in_window`` reported. The band still exists. Perturbation:
    lower ``MIN_WINDOW_READINGS`` to 2 and this goes red."""
    result = verdict_for(alternating(41), [42.0, 42.0])

    assert result.verdict == UNAVAILABLE
    assert result.readings_in_window == 2
    assert result.band is not None
    assert result.established is True
    assert result.below_by is None


def test_two_window_readings_far_below_the_band_are_still_unavailable() -> None:
    """The window minimum is spec/03 §3.7.4's trends-not-single-readings rule: two
    bad mornings are not a trend, however bad."""
    result = verdict_for(alternating(41), [20.0, 20.0])

    assert result.verdict == UNAVAILABLE
    assert result.below_by is None


def test_three_window_readings_are_the_minimum() -> None:
    assert verdict_for(alternating(41), [42.0] * 3).verdict == NORMAL
    assert hrv_trend.MIN_WINDOW_READINGS == 3


def test_an_empty_window_over_an_established_baseline_is_unavailable_and_keeps_its_band() -> None:
    """No reading in the week: unavailable, no mean, ``readings_in_window``
    0. The band is a property of the baseline, so it is still asserted --
    T091's chart draws a band on a day with no reading."""
    result = verdict_for(alternating(41), [])

    assert result.verdict == UNAVAILABLE
    assert result.readings_in_window == 0
    assert result.ln_rmssd_7d_mean is None
    assert result.band is not None
    assert result.established is True


def test_no_reading_of_any_tier_is_unavailable_with_no_band() -> None:
    """The empty series: nothing to build a band from, nothing to judge."""
    result = judge(build_series([], AUCKLAND, D))

    assert result.verdict == UNAVAILABLE
    assert result.band is None
    assert result.baseline_n == 0
    assert result.readings_in_window == 0
    assert result.ln_rmssd_7d_mean is None
    assert result.established is False


def test_the_mean_is_over_the_collapsed_series_not_the_stored_rows() -> None:
    """Two captures on one window day: only the earliest (T083's collapse)
    enters the mean, so a second capture cannot double-weight a morning."""
    day = D - timedelta(days=1)
    rows = baseline_rows(alternating(41))
    rows += window_rows([42.0, 42.0, 42.0], target=D - timedelta(days=2))
    rows += [
        {**row(day, 42.0, session_id="early"), "start_time": local(day, 6, 0)},
        {**row(day, 90.0, session_id="late"), "start_time": local(day, 6, 30)},
    ]

    result = judge(build_series(rows, AUCKLAND, D))

    assert result.readings_in_window == 4
    assert result.ln_rmssd_7d_mean == pytest.approx(math.log(42.0), abs=1e-12)


# ---------------------------------------------------------------------------
# what the module does not do
# ---------------------------------------------------------------------------


def test_ln_is_not_guarded_here_so_a_non_positive_value_surfaces_as_a_defect() -> None:
    """T083 guarantees every ``Reading`` is ``ln``-safe. A second, silent
    guard here would mask a T083 regression, so a non-positive value that
    reaches the band raises rather than being skipped. Driven by editing a
    reading on the built series directly, because ``build_series`` will not
    let one through. Perturbation: add ``if value > 0`` around the log and
    this goes red."""
    series = build_series(baseline_rows(alternating(14)) + window_rows([42.0] * 3), AUCKLAND, D)
    poisoned = replace(series.baseline[0], rmssd_ms=0.0)
    broken = replace(series, baseline=(poisoned, *series.baseline[1:]))

    with pytest.raises(ValueError):
        judge(broken)

    poisoned_window = replace(series.window[0], rmssd_ms=-5.0)
    broken_window = replace(series, window=(poisoned_window, *series.window[1:]))
    with pytest.raises(ValueError):
        judge(broken_window)


def test_the_thresholds_the_response_echoes_are_the_constants_the_verdict_uses() -> None:
    """``research/00`` PRIN-12, less its OPEN exceptions PRIN-24 and PRIN-27: the verdict is
    reproducible by hand from its response, so the constants are module-level and named."""
    assert hrv_trend.SWC_FACTOR == 0.5
    assert hrv_trend.BAND_FLOOR == 0.01
    assert hrv_trend.MIN_WINDOW_READINGS == 3
    assert hrv_trend.MIN_BASELINE_READINGS == 14
    assert hrv_trend.BASELINE_DAYS == 60
    assert hrv_trend.RECENCY_TOLERANCE_DAYS == 28  # served since T220 (F010, C33)
    assert (hrv_trend.VERDICT_NORMAL, hrv_trend.VERDICT_SUPPRESSED, hrv_trend.VERDICT_UNAVAILABLE) == (
        NORMAL,
        SUPPRESSED,
        UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# the contract table: {baseline n} x {window n} x {mean position}
#
# Authored before the implementation from the spec (module docstring). Each
# row is (baseline n, window n, position) -> (verdict, band asserted,
# established, below_by reported). Window values: below = 25 ms, inside =
# 42 ms, above = 60 ms against an alternating 40/44 ms baseline, whose band
# lies inside [3.70, 3.78] for every n >= 2 (checked in the test; ln 25 =
# 3.22, ln 42 = 3.74, ln 60 = 4.09).
# ---------------------------------------------------------------------------

BELOW, INSIDE, ABOVE = "below", "inside", "above"
WINDOW_VALUE = {BELOW: 25.0, INSIDE: 42.0, ABOVE: 60.0}


def expected_row(baseline_n: int, window_n: int, position: str) -> tuple[str, bool, bool, bool]:
    band = baseline_n >= 2
    established = baseline_n >= 14
    if not band or window_n < 3:
        return UNAVAILABLE, band, established, False
    if not established:
        # T116: the establishment gate is symmetric. Below the band and inside
        # or above it alike, an unestablished baseline is ``hrv_unavailable``
        # (``baseline_unestablished``) -- ``hrv_normal`` on a 2-to-13-reading
        # baseline asserts intact readiness on evidence the same response
        # calls unestablished, the up-regulating direction ``research/00``
        # PRIN-14 forbids. Neither verdict is asserted, and the band is still
        # reported so the consumer can see the band no verdict was taken from.
        return UNAVAILABLE, True, False, False
    if position == BELOW:
        return SUPPRESSED, True, True, True
    return NORMAL, True, True, False


CONTRACT_TABLE = [
    (baseline_n, window_n, position, expected_row(baseline_n, window_n, position))
    for baseline_n in (0, 1, 2, 13, 14, 41)
    for window_n in (0, 1, 2, 3, 7)
    for position in (BELOW, INSIDE, ABOVE)
]


def test_the_contract_table_is_exhaustive_over_its_axes() -> None:
    assert len(CONTRACT_TABLE) == 6 * 5 * 3
    assert len({(b, w, p) for b, w, p, _ in CONTRACT_TABLE}) == 90
    # The single-bad-morning row is on the axis (construction reference,
    # "Degenerate inputs": 0, 1, 2, 3 in window; review S3).
    assert {w for _, w, _, _ in CONTRACT_TABLE} == {0, 1, 2, 3, 7}
    # And the baseline axis, which the module docstring enumerates and until
    # `acfebae` nothing asserted: the count above cannot see a substitution,
    # and measured 2026-09-15, replacing 41 with 40 left all 122 tests in this
    # module green while the docstring still named 41. The four load-bearing
    # values are 1 (no band), 2 (the smallest band), 13 and 14 (either side of
    # MIN_BASELINE_READINGS); 0 and 41 are the empty and the ordinary ends.
    assert {b for b, _, _, _ in CONTRACT_TABLE} == {0, 1, 2, 13, 14, 41}
    # And the third axis, which the docstring's "both axes" left out. Neither
    # the count nor the row-uniqueness check can see a position *substituted*
    # for another -- the table stays 90 distinct rows -- and WINDOW_VALUE and
    # the band-position lookup only catch it while they carry exactly these
    # three keys. Measured 2026-09-15: with a fourth key added to both and
    # ABOVE swapped out of the comprehension, every other test in this module
    # stays green and this is the one assertion that reddens.
    assert {p for _, _, p, _ in CONTRACT_TABLE} == {BELOW, INSIDE, ABOVE}
    # And the table is not degenerate: every verdict appears, both established
    # states appear, and below_by appears in both states.
    assert {r[3][0] for r in CONTRACT_TABLE} == {NORMAL, SUPPRESSED, UNAVAILABLE}
    assert {r[3][3] for r in CONTRACT_TABLE} == {True, False}


@pytest.mark.parametrize(
    ("baseline_n", "window_n", "position", "expected"),
    CONTRACT_TABLE,
    ids=[f"b{b}-w{w}-{p}" for b, w, p, _ in CONTRACT_TABLE],
)
def test_contract_table(baseline_n: int, window_n: int, position: str, expected: tuple) -> None:
    result = verdict_for(alternating(baseline_n), [WINDOW_VALUE[position]] * window_n)

    verdict, band_asserted, established, below_by_reported = expected
    assert result.baseline_n == baseline_n
    assert result.readings_in_window == window_n
    assert (result.band is not None) is band_asserted
    assert result.established is established
    assert result.verdict == verdict
    assert (result.below_by is not None) is below_by_reported
    if result.band is not None:
        # The fixture places the window where the row says it does.
        assert 3.70 < result.band.lo < result.band.hi < 3.78
        if window_n:
            mean = math.log(WINDOW_VALUE[position])
            assert {BELOW: mean < result.band.lo, INSIDE: result.band.lo < mean < result.band.hi, ABOVE: mean > result.band.hi}[
                position
            ]
    if window_n:
        assert result.ln_rmssd_7d_mean == pytest.approx(math.log(WINDOW_VALUE[position]), abs=1e-12)
    else:
        assert result.ln_rmssd_7d_mean is None


# ---------------------------------------------------------------------------
# T127: the device return, walked morning by morning through ``judge``
#
# The suite's one database-backed section, and it is deliberate. Everything
# above is a pure unit test over hand-built rows; this walk's series comes
# from ``tests/support/seed_hrv_series.py``'s generator (``conftest``'s
# ``_seed_hrv_series``, reached through the ``seed_hrv_series`` fixture) so
# that the geometry [[T125]] reproduces -- stated there as three ``--era``
# flags -- cannot drift from the fixture the reproduction was measured on.
# One seeding per geometry, then ``build_series``/``judge`` at each target in
# turn: the walk is over the athlete's mornings, not over the store.
#
# Why this exists. Rule 1's recency gate is pinned exhaustively in
# ``test_hrv_trend_series.py`` and its *consequence* was pinned on one series
# at one geometry. The population it never reached is the mirror one: the
# carrier tier stops because the athlete went back to the other device. There
# the gate evicts the tier he is currently using and the verdict is computed
# from the surviving tier's last pre-return days -- which is [[T125]].
# ---------------------------------------------------------------------------

SNAPSHOT = "health_snapshot"
PROFILE = "HRV Snapshot"

#: [[T125]]'s reproduction geometry, as three eras. An established era on one
#: tier; a 39-day silence on it while the other tier carries the series; then
#: the first tier RESUMES. ``RETURN_FIRST`` is the athlete's first morning
#: back, and the walk's target date steps across the return era, so step ``r``
#: is his ``r``-th morning back -- the critic's 724-geometry sweep row ``r``,
#: which fixes ``D`` and moves the return day instead.
ERA_A_END = date(2026, 7, 31)
CARRIER_END = date(2026, 9, 8)
RETURN_FIRST = date(2026, 9, 9)
RETURN_DAYS = 8

#: Which tier plays which part. Both orders are walked: the returning tier is
#: the **higher**-fidelity one in the first (rule 2 hands it the week the
#: moment it covers it) and the **lower**-fidelity one in the second (rule 2
#: can only reach it because the carrier has no week day left at all). The
#: two arrive at the same place by different routes, and neither route was
#: pinned at the verdict.
RETURN_GEOMETRIES = (
    ("strap-returns", STRAP, SNAPSHOT),
    ("snapshot-returns", SNAPSHOT, STRAP),
)


def _seed_return_series(seed_hrv_series, home_tier: str, carrier_tier: str, suppressed: bool) -> list[dict]:
    """The three eras above, seeded through the real ingestion path, as the
    four-column rows ``build_series`` takes.

    The two tiers are captured at different local hours, which two eras on the
    same mornings need (T096): ``session_id`` is derived from
    ``(source_device, start_time)`` and the synthetic device is one string.
    A Tier-1 era additionally needs a declared profile name to resolve as
    ``chest_strap_raw`` at all.
    """
    rows: list[dict] = []
    for tier, end, days, suppress_last, hour in (
        (home_tier, ERA_A_END, 80, 0, 6),
        (carrier_tier, CARRIER_END, 39, 0, 7),
        (home_tier, RETURN_FIRST + timedelta(days=RETURN_DAYS - 1), RETURN_DAYS, RETURN_DAYS if suppressed else 0, 6),
    ):
        kwargs = {
            "end": end,
            "days": days,
            "suppress_last": suppress_last,
            "tier": tier,
            "zone": AUCKLAND,
            "local_hour": hour,
        }
        if tier == STRAP:
            kwargs["profile_names"] = [PROFILE]
        rows += seed_hrv_series(**kwargs).rows
    return rows


@pytest.mark.parametrize("suppressed", [True, False], ids=["suppressed-return", "healthy-return"])
@pytest.mark.parametrize(
    ("name", "home_tier", "carrier_tier"), RETURN_GEOMETRIES, ids=[geometry[0] for geometry in RETURN_GEOMETRIES]
)
def test_the_device_return_is_walked_morning_by_morning_through_judge(
    seed_hrv_series, name: str, home_tier: str, carrier_tier: str, suppressed: bool
) -> None:
    """[[T125]]'s geometry at the verdict, every morning of the return, both
    tiers, both value levels (T127).

    **The expectations are derived by hand from the eras and then confirmed
    against the shipped code at ``3f1430c``; they are not a capture of it.**
    The derivation, per morning ``r`` (target ``CARRIER_END + r``):

    * **Which tier.** For ``r`` in 1..7 the carrier holds ``32 + r`` days of
      the baseline window and the returning tier's era-A days are 33 or more
      behind the carrier's latest -- past ``RECENCY_TOLERANCE_DAYS`` -- so the
      gate strikes the tier the athlete is *using* and the carrier resolves.
      At ``r = 8`` the return's first morning (``2026-09-09``) has entered the
      baseline window, so the returning tier's ``last_read`` is current, it is
      a candidate again on 21 days (20 from era A plus that one), and rule 2
      hands it the week -- which it now covers alone, the carrier having
      stopped on ``CARRIER_END``.
    * **What fed the mean.** The carrier stopped on ``CARRIER_END``, so while
      it owns the baseline the judged week holds ``7 - r`` of its days and the
      athlete's own ``r`` return mornings are in the returning tier's own
      dataset, not in the window (shipped F005 excluded them as
      ``off_baseline_tier``; T152). **That is the defect this walk was written to make
      visible**: at ``r = 4`` -- [[T125]]'s reproduced day, ``2026-09-12`` --
      the mean is computed from ``09-06``, ``09-07`` and ``09-08``, three
      mornings that all predate his return. The tier, the band and the
      excluded list still read exactly that way; **T125 changed what is said
      about it, not what it is**, which is why every assertion in this walk
      except the verdict is unmoved.
    * **The verdict.** ``r = 1`` and ``r = 2`` leave five and four carrier
      days in the week against one and two return mornings, on an established
      baseline of ordinary alternating values, so the week mean sits inside
      its own band: ``hrv_normal``. From ``r = 3`` the athlete holds
      ``MIN_WINDOW_READINGS`` week mornings of his own, every one later than
      every day that fed the mean, and **T125's withhold fires**: the week is
      not a fair sample of the tier being judged, so no verdict is asserted --
      ``hrv_unavailable`` at ``r = 3`` and ``r = 4``, where this walk pinned
      ``hrv_normal`` before the fix, and at ``r = 5..7``, where fewer than
      three carrier days remain and the answer was already ``hrv_unavailable``
      for the ordinary reason. At ``r = 8`` the week is the return's own seven
      mornings against a 21-day band -- the carrier has no week day left and
      nothing is withheld -- so ``hrv_suppressed`` on a suppressed return and
      ``hrv_normal`` on a healthy one: the first day of the walk on which the
      value level of the athlete's actual mornings changes anything he is
      told.
    * **``r = 1`` and ``r = 2`` are not fixed, and no measured form fixes
      them.** One and two return mornings are below ``MIN_WINDOW_READINGS``,
      so the verdict there still comes from pre-return days and still reads
      ``hrv_normal``. The constant that hides those two rows is the same one
      that keeps the withhold from firing on a stray capture; F005's Negative
      Class carries the residual.
    * **The reset.** ``tier_change`` is reported on ``r = 1`` and ``r = 2``
      and withdrawn from ``r = 3``, by clause (c)'s week half: the boundary is
      era A's last day against the carrier's first, and the return mornings
      sit on the far side of it, so once three of them fall inside the judged
      week they are no longer isolated and the report lapses. Nothing else
      about the answer moves on that day, which is why it took a verdict-level
      walk to notice that it moves at all.

    Spot-checked by hand at ``r = 1`` (six carrier days, ``hrv_normal``),
    ``r = 4`` (the reproduction: ``health_snapshot``, ``readings_in_window``
    3, ``09-06``/``09-07``/``09-08`` -- field for field what [[T125]] reports,
    and since T125 ``hrv_unavailable`` rather than ``hrv_normal``), and
    ``r = 8`` suppressed (a band over ten 38.0s, ten 44.0s and one 25.0 gives
    ``lo`` near 3.62 against a week mean of ``ln 25`` = 3.22, so
    ``hrv_suppressed``) and healthy (``lo`` near 3.67 against a week mean near
    3.72, so ``hrv_normal``).

    **The four expectations this walk changed at T125, and why.** It was
    written at T127 to pin the *defect* -- its own derivation above said
    ``hrv_normal`` at ``r = 3`` and ``r = 4`` "whatever the return mornings
    said" -- so that any candidate fix would redden it and be seen. T125's
    form 2 is that fix, and those two targets in each of the four parametrised
    cases are the sanctioned change. Nothing else in the walk moved: tier,
    ``fed``, ``readings_in_window``, ``baseline_n``, ``established`` and the
    reset tuple are byte-identical to their pre-fix values at every ``r``,
    which is the measured claim that form 2 changes only what is said.

    Perturbation, re-run 2026-09-16 after the fix: deleting ``not
    series.withheld`` from ``judge``'s verdict branch reds this walk at
    ``r = 3`` and ``r = 4`` in all four cases, and nothing else in the five
    suites.

    **Re-derived at F006 (T155, 2026-09-19): ``r = 5``, ``6`` and ``7``
    changed tier and verdict; nothing else moved.** Shipped F005 kept the
    *carrier* on those three mornings -- rule 3, the candidate read last,
    once no candidate covered the week -- and answered ``hrv_unavailable``
    (``week_too_thin``: ``readings_in_window`` ``7 - r``, ``baseline_n``
    ``32 + r``, the shipped values this pin recorded). F006 selects among
    **judgeable** datasets (``select_dataset``): from ``r = 5`` the carrier
    holds two judged-week days and is not judgeable, while the returning
    tier is established over its era-A days (23, 22, 21) and holds ``r``
    mornings of the week -- it is the only judgeable dataset, so it is its
    own recency reference and is selected, and the verdict is the return's
    own three mornings earlier than at ``r = 8``. That is ``research/00``
    HRV-71: a return to a dataset the athlete established before is
    **free**, its band was never destroyed. ``r = 1..4`` are unchanged --
    there the carrier is judgeable and the returning tier, once judgeable at
    ``r = 3``, is 33+ days behind it in the baseline window and skipped (AC6,
    the reference §9 shape) -- and so is the withhold on ``r = 3`` and
    ``r = 4``. Where the shipped and F006 verdicts differ is recorded in the
    branch below rather than overwritten, so T162 can measure the rate.

    **Re-pointed at T158 (F006 AC24, 2026-09-19): the withhold on ``r = 3``
    and ``r = 4`` is now ``verdict_withheld`` at dataset scope.** The set the
    order clause is asked of is every dataset that could not have been
    selected -- not judgeable, or **skipped** by the recency gate -- and the
    returning tier here is the skipped arm: established on era A, judgeable
    from ``r = 3``, 33+ days behind the carrier in the baseline window. Its
    ``r`` return mornings are all later than every carrier day (the carrier
    stopped on ``CARRIER_END``), so the carrier's verdict is withheld; the
    values this walk pins on those two mornings are unchanged
    (``hrv_unavailable``, tier carrier, ``readings_in_window`` ``7 - r``).
    This is T125's own population, and it is why the set is not "not
    judgeable" alone (IDEA-083).

    **Re-pointed at T164 (F006 AC7, 2026-09-20): ``r = 5``, ``6`` and ``7``
    move back to shipped F005's answer, and the walk now reads the same on
    every morning but ``r = 8``.** The recency reference is taken over every
    **established** dataset rather than the judgeable ones alone, and the
    carrier is established on all of ``32 + r`` baseline-window days however
    thin its judged week has become. It therefore holds the reference at
    ``CARRIER_END`` (or the window's end, whichever is earlier) at every
    ``r``, and the returning tier's era-A days sit 37 or more behind it, so
    the returning tier is **skipped** from ``r = 5`` exactly as it was from
    ``r = 3`` -- nothing is judgeable, and the AC9 fallback presents the
    carrier, read last. *Shipped F006 (T155): tier home, ``fed`` the ``r``
    return mornings, ``readings_in_window`` ``r``, ``baseline_n`` ``28 - r``
    (23 / 22 / 21), ``hrv_suppressed`` on a suppressed return and
    ``hrv_normal`` on a healthy one from the fifth morning back.* That last
    value is the row T162 priced: ``hrv_normal`` on a band every reading of
    which is 36 to 66 days old, PRIN-14's forbidden direction, and it is the
    walk's own contribution to the 96 -> 254 stale-band regression AC21
    blocked release on. ``research/00`` HRV-71's "a return is free" is
    unchanged in itself -- the return's band was never destroyed -- but it is
    not free *of the recency gate* while a dataset the athlete is still being
    read on is established.
    """
    rows = _seed_return_series(seed_hrv_series, home_tier, carrier_tier, suppressed)
    era_a = [ERA_A_END - timedelta(days=i) for i in range(80)]
    return_days = [RETURN_FIRST + timedelta(days=i) for i in range(RETURN_DAYS)]

    for r in range(1, RETURN_DAYS + 1):
        target = CARRIER_END + timedelta(days=r)
        series = build_series(rows, AUCKLAND, target)
        verdict = judge(series)
        fed_by_carrier = [day for day in window_days(7, target) if day <= CARRIER_END]
        returned = [day for day in window_days(7, target) if day > CARRIER_END]

        if r < RETURN_DAYS:
            # r = 1..7 (re-pointed at T164; r = 1..4 before it). The carrier
            # is established at every r, so it holds the recency reference at
            # every r, and the returning tier -- judgeable from r = 3 -- is
            # 37+ days behind it in the baseline window and skipped (F006
            # AC6/AC7; F005's own rule-1 gate struck it for the same reason
            # over the same population). Through r = 4 the carrier is
            # judgeable and selected; from r = 5 it holds fewer than
            # MIN_WINDOW_READINGS week days, nothing is judgeable, and the AC9
            # fallback presents it -- the established dataset read last.
            # Either way the answer is the carrier's, which is what shipped
            # F005 said on all seven mornings.
            #
            # Shipped F006 (T155), r = 5..7 only: tier home, fed the r return
            # mornings, readings_in_window r, baseline_n 28 - r, and
            # hrv_suppressed / hrv_normal rather than hrv_unavailable.
            expected_tier = carrier_tier
            fed = fed_by_carrier
            # [[T125]] form 2. The athlete's own mornings inside the judged
            # week are every week day the carrier did not capture, and each of
            # them is later than every day that fed the mean (the carrier
            # stopped on ``CARRIER_END``). Once ``MIN_WINDOW_READINGS`` of them
            # are in the week, the week is not a fair sample of the tier the
            # verdict would be computed on, and no verdict is asserted.
            withheld = len(returned) >= hrv_trend.MIN_WINDOW_READINGS
            expected_verdict = NORMAL if not withheld else UNAVAILABLE
            expected_baseline_n = 32 + r
        else:
            # r = 8. The derivation the returning tier would have here if it
            # were selected -- its era-A days inside the window plus the one
            # return morning that has entered it -- is kept and asserted,
            # because the r == RETURN_DAYS block below turns on that dataset's
            # shape. Under T164 r = 5..7 no longer reach this branch: the
            # carrier holds the reference and the returning tier is skipped
            # there (shipped F006 selected it, with baseline_n 28 - r).
            first, last = hrv_trend.baseline_window(target)
            expected_tier = home_tier
            fed = returned
            expected_verdict = SUPPRESSED if suppressed else NORMAL
            expected_baseline_n = len([day for day in era_a + return_days if first <= day <= last])
            assert expected_baseline_n == 21 and r == RETURN_DAYS, r

        if r == RETURN_DAYS:
            # Re-derived at T153 (2026-09-19, F006 AC17). Shipped through
            # 785f89c: home tier, baseline_n 21 (20 era-A days plus the
            # return's first morning), established, hrv_suppressed /
            # hrv_normal -- the values derived above. On this morning the
            # return's first day (RETURN_FIRST) enters the baseline window
            # 39 silent days after era A's last, so the home dataset's own
            # internal hole is inside its window and its band is clipped to
            # that one reading: n 1, no band, not established, not
            # judgeable, nothing reported. The carrier holds no week day
            # either, so nothing is judgeable and the presentation fallback
            # shows the carrier (established, read last: n 39) with
            # hrv_unavailable / week_too_thin. The return is therefore not
            # free across a layoff longer than GAP_RESET_DAYS: the band is
            # rebuilt from the post-layoff mornings from here (IDEA-084).
            (home,) = [d for d in series.datasets if d.tier == home_tier]
            assert home.baseline_window == (RETURN_FIRST, RETURN_FIRST), r
            assert (home.n, home.established, home.band) == (1, False, None), r
            assert (home.reset_on, home.reset_reason) == (None, None), r
            assert series.selection is not None and series.selection.selected is None, r
            expected_tier = carrier_tier
            fed = []
            expected_baseline_n = 39
            expected_verdict = UNAVAILABLE
            assert verdict.unavailable_reason == "week_too_thin", r

        assert series.tier == expected_tier, r
        assert [reading.date for reading in series.window] == fed, r
        assert verdict.readings_in_window == len(fed), r
        assert verdict.baseline_n == expected_baseline_n, r
        assert verdict.established is True, r
        assert verdict.verdict == expected_verdict, r
        expected_reset = ("tier_change", date(2026, 8, 1)) if r <= 2 else (None, None)
        assert (series.reset_reason, series.reset_on) == expected_reset, r

    # The athlete's own four mornings are excluded, by name, on the day
    # [[T125]] reproduces -- every judged-week day of 2026-09-12 that is not
    # one of the three the verdict was computed from. Under F006 (T152) they
    # are the returning tier's own dataset's week, excluded nowhere; shipped
    # F005 listed the four ``off_baseline_tier``.
    reproduction = build_series(rows, AUCKLAND, date(2026, 9, 12))
    week = set(window_days(7, date(2026, 9, 12)))
    (home,) = [d for d in reproduction.datasets if d.tier == home_tier]
    off_tier = {reading.date for reading in home.window if reading.date in week}
    assert off_tier == {RETURN_FIRST + timedelta(days=i) for i in range(4)}
    assert not any(entry.date in off_tier for entry in reproduction.excluded)


# ---------------------------------------------------------------------------
# T147: the residual's axis is the SPACING of the captures, not their count
# ---------------------------------------------------------------------------

#: Weekly capture patterns for the return era -- local-day offsets from
#: ``RETURN_FIRST``, repeating every seven days -- with the two counts T145
#: measured and this walk re-measures against the shipped code:
#:
#: * ``retired_band_mornings``: how many of his opening mornings back are
#:   still answered ``hrv_normal`` from a judged week fed **entirely** by
#:   pre-return days -- the residual F005's Negative Class prices, whose
#:   closed form is ``min(WINDOW_DAYS - MIN_WINDOW_READINGS, k3)``;
#: * ``attributable_days``: how many days of the ensuing silence T125/T132's
#:   withhold actually decides, i.e. carry ``week_not_representative`` rather
#:   than the ``week_too_thin`` that would have silenced them anyway.
#:
#: **The two 4/wk rows are the point of this table.** They hold the same
#: number of captures per week and disagree on both counts, while the
#: *clustered* one agrees with daily throughout -- so neither count is a
#: function of weekly density, and a bound glossed as a property of how many
#: mornings a week he captures is false of half the 4/wk athletes. The axis
#: is how far apart the captures sit: ``k3``, the offset at which his
#: ``MIN_WINDOW_READINGS``-th distinct return day enters the judged week, is
#: 2 at daily and at 4/wk-clustered alike and 4 or more at the three spread
#: patterns.
RETURN_DENSITIES = (
    ("daily", (0, 1, 2, 3, 4, 5, 6), 2, 2),
    ("4/wk clustered", (0, 1, 2, 4), 2, 2),
    ("4/wk spread", (0, 2, 4, 6), 4, 0),
    ("3/wk", (0, 2, 4), 4, 0),
    ("2/wk", (0, 3), 4, 0),
)

#: How far past ``RETURN_FIRST`` the walk below runs. Through ``k = 3`` the
#: carrier still owns the baseline under every pattern in
#: ``RETURN_DENSITIES`` (asserted, not assumed), which is the whole stretch
#: either count can be non-zero over: the residual is capped at
#: ``WINDOW_DAYS - MIN_WINDOW_READINGS`` = 4 and the withhold has stopped
#: mattering well before the handover. From ``k = 4`` the carrier holds two
#: judged-week days and is no longer judgeable, and F006's selection (T155)
#: hands the week to the returning strap wherever it holds
#: ``MIN_WINDOW_READINGS`` of the week's days -- shipped F005 kept the
#: carrier through ``k = 6`` on every pattern (rule 3), which is the value
#: this walk pinned until 2026-09-19; neither count below moves, because
#: those days were ``week_too_thin`` on the carrier and are the return's own
#: mornings on the strap.
DENSITY_WALK_DAYS = 7

#: The return era's seeded length, long enough that every pattern's third
#: distinct capture day falls inside it (2/wk reaches it on ``RETURN_FIRST +
#: 7``, one day past the walk).
DENSITY_RETURN_DAYS = 21


def _seed_density_eras(seed_hrv_series, home_tier: str, carrier_tier: str) -> tuple[list[dict], list[dict]]:
    """``_seed_return_series``'s three eras, seeded **once**, with the return
    era handed back separately so each pattern can thin it.

    The store is one isolated database per test and ``session_id`` is derived
    from ``(source_device, start_time)``, so re-seeding the same eras per
    pattern would collide rather than repeat: the eras are seeded once here
    and only the row *list* is filtered below. The return era is contiguous,
    and ``seed_hrv_series`` places its ``i``-th reading on ``first_day + i``,
    so row ``i`` is ``RETURN_FIRST + i`` and the thinning needs no timestamp
    arithmetic.
    """
    eras: list[list[dict]] = []
    for tier, end, days, hour in (
        (home_tier, ERA_A_END, 80, 6),
        (carrier_tier, CARRIER_END, 39, 7),
        (home_tier, RETURN_FIRST + timedelta(days=DENSITY_RETURN_DAYS - 1), DENSITY_RETURN_DAYS, 6),
    ):
        kwargs = {
            "end": end,
            "days": days,
            "suppress_last": 0,
            "tier": tier,
            "zone": AUCKLAND,
            "local_hour": hour,
        }
        if tier == STRAP:
            kwargs["profile_names"] = [PROFILE]
        eras.append(seed_hrv_series(**kwargs).rows)
    era_a, carrier, return_era = eras
    assert len(return_era) == DENSITY_RETURN_DAYS
    return era_a + carrier, return_era


def test_the_return_residual_turns_on_capture_spacing_not_weekly_count(seed_hrv_series) -> None:
    """[[T147]], review cycle 10, G-C10-11/12: the walk above's geometry at
    five capture densities.

    Every fixture this feature has measured the device return over seeds
    **contiguous daily mornings** -- ``_seed_return_series`` above, and all
    2050 rows of ``spec/references/T125-fix-form-measurements.md`` -- so the
    non-daily half of both the residual bound and T132's priced cost was
    stated and restated with no pin under it. That is how the bound came to
    be glossed as a property of how many mornings a week the athlete
    captures, when both counts are measured to turn on **how far apart** they
    sit: a 4/wk clustered return agrees with the daily one on both counts,
    and 4/wk spread with 3/wk and 2/wk.

    What is asserted, per pattern, over ``k = 0 .. 6``:

    * the carrier owns the baseline through ``k = 3`` under every pattern, so
      both counts are read off the same phase and the walk is not silently
      comparing different stretches; **the carrier owns it at every ``k`` and
      under every pattern** (re-pointed at T164, 2026-09-20), because it is
      established throughout and so holds the recency reference, which leaves
      the returning strap skipped wherever it is a candidate at all -- *shipped
      F006 (T155) selected the STRAP from ``k = 4`` under every pattern
      holding ``MIN_WINDOW_READINGS`` judged-week days (daily, 4/wk
      clustered, 4/wk spread, 3/wk) and presented the carrier by the fallback
      at 2/wk*; shipped F005 kept the carrier at every ``k``, which is where
      this is again. The strap's candidacy and the strike are both asserted
      below, so "the carrier owns it" cannot pass by the strap merely being
      absent;
    * ``retired_band_mornings`` -- ``hrv_normal`` from a week fed entirely by
      days at or before ``CARRIER_END`` -- equals the table **and** equals
      ``min(WINDOW_DAYS - MIN_WINDOW_READINGS, k3)``, with ``k3`` derived
      from the seeded capture days rather than restated beside them;
    * ``attributable_days`` -- ``unavailable_reason ==
      "week_not_representative"`` -- equals the table, which is **zero** at
      every spread pattern: there the withhold flips only on days
      ``week_too_thin`` already decides, and ``week_too_thin`` precedes it in
      ``_unavailable_reason``'s fixed order;
    * and the cross-pattern claim itself: the two 4/wk rows disagree while
      carrying the same weekly count.

    Perturbation, both run and reverted 2026-09-18 ([[T147]]): moving
    ``week_not_representative`` ahead of ``week_too_thin`` in
    ``_unavailable_reason``'s fixed order reds this walk at the first row it
    reaches, ``attributable_days`` 2 -> 5 at daily -- the order is what makes
    the spread patterns' attributable count zero, so it is load-bearing for
    the whole right-hand column. Relaxing ``verdict_withheld``'s ``len(days)
    >= MIN_WINDOW_READINGS`` to ``>`` reds it on the other count,
    ``retired_band_mornings`` 2 -> 3 at daily, and the closed form with it.
    """
    before_return, return_era = _seed_density_eras(seed_hrv_series, STRAP, SNAPSHOT)

    measured: dict[str, tuple[int, int]] = {}
    for name, weekly_offsets, expected_retired, expected_attributable in RETURN_DENSITIES:
        rows = before_return + [row for i, row in enumerate(return_era) if i % 7 in weekly_offsets]
        captured = [RETURN_FIRST + timedelta(days=i) for i in range(DENSITY_RETURN_DAYS) if i % 7 in weekly_offsets]

        retired_band_mornings = 0
        attributable_days = 0
        for k in range(DENSITY_WALK_DAYS):
            target = RETURN_FIRST + timedelta(days=k)
            series = build_series(rows, AUCKLAND, target)
            verdict = judge(series)

            strap_week = [day for day in captured if target - timedelta(days=6) <= day <= target]
            carrier_week = [day for day in window_days(7, target) if day <= CARRIER_END]
            # T164: the carrier is presented at every k under every pattern
            # -- selected while it is judgeable, and by the AC9 fallback once
            # its week thins -- because it is established throughout and holds
            # the recency reference. Shipped F006 read STRAP wherever the
            # elif below fires.
            expected_tier = SNAPSHOT
            selection = series.selection
            assert selection is not None, (name, k)
            assert series.tier == expected_tier, (name, k)
            if len(carrier_week) >= hrv_trend.MIN_WINDOW_READINGS:
                assert selection.selected is not None, (name, k, selection.describe())
                assert selection.selected.tier == SNAPSHOT, (name, k, selection.describe())
            else:
                # Nothing is judgeable here, and the strap is not merely
                # absent from the candidates: it IS one wherever it holds
                # MIN_WINDOW_READINGS week days, and it is SKIPPED. Shipped
                # F006 selected it on exactly these rows.
                assert selection.selected is None, (name, k, selection.describe())
                a_candidate = len(strap_week) >= hrv_trend.MIN_WINDOW_READINGS
                assert (STRAP in selection.judgeable) is a_candidate, (name, k, selection.describe())
                assert (STRAP in selection.skipped) is a_candidate, (name, k, selection.describe())
            assert k >= 4 or expected_tier == SNAPSHOT, (name, k)
            fed = [reading.date for reading in series.window]
            if verdict.verdict == NORMAL and fed and max(fed) <= CARRIER_END:
                retired_band_mornings += 1
            if verdict.unavailable_reason == "week_not_representative":
                assert verdict.verdict == UNAVAILABLE, (name, k)
                attributable_days += 1

        # ``k3``: the offset at which his ``MIN_WINDOW_READINGS``-th distinct
        # return day enters the judged week, read off the days actually
        # seeded rather than restated from the table above.
        k3 = (captured[hrv_trend.MIN_WINDOW_READINGS - 1] - RETURN_FIRST).days
        closed_form = min(hrv_trend.WINDOW_DAYS - hrv_trend.MIN_WINDOW_READINGS, k3)

        assert retired_band_mornings == expected_retired, name
        assert retired_band_mornings == closed_form, (name, k3)
        assert attributable_days == expected_attributable, name
        measured[name] = (retired_band_mornings, attributable_days)

    # The claim the gloss got wrong, asserted directly: the same weekly count
    # with different spacing gives a different answer, and the clustered
    # return is the daily one's twin rather than the spread ones'.
    assert len(RETURN_DENSITIES[1][1]) == len(RETURN_DENSITIES[2][1]) == 4
    assert measured["4/wk clustered"] == measured["daily"]
    assert measured["4/wk clustered"] != measured["4/wk spread"]
    assert measured["4/wk spread"] == measured["3/wk"] == measured["2/wk"]


# ---------------------------------------------------------------------------
# T132: the withhold is blind to a device the athlete has never used before
# ---------------------------------------------------------------------------


def test_the_withhold_reaches_a_never_used_tier_bought_this_week() -> None:
    """T132 (review cycle 9, G-C9-1; task file, reproduction verbatim).

    [[T125]]'s form 2 (``verdict_withheld``) can only withhold on a tier the
    recency gate **struck** -- and a struck tier must first be a
    *candidate*, which requires ``>= MIN_BASELINE_READINGS`` distinct days in
    the (gap-clipped) baseline window. A tier whose first-ever reading falls
    **inside the judged week** has zero baseline-window days, so it is never
    a candidate, never struck, and the withhold could never fire for it --
    however many judged-week days it holds and however cleanly they are
    ordered after the resolved tier's own.

    Reproduction: ``chest_strap_raw`` daily 2026-07-04..2026-09-04 @ 40.0 ms
    (the athlete's long-standing habit; 60 distinct baseline-window days,
    ``[2026-07-04, 2026-09-01]``), then ``health_snapshot`` -- a tier he has
    **never used before** -- on 2026-09-05..2026-09-07 @ 15.0 ms (three
    deeply-suppressed mornings, all strictly later than every strap day),
    judged at D = 2026-09-08, Pacific/Auckland.

    Shipped: ``chest_strap_raw`` resolves (it still covers the week's first
    three days and the snapshot cannot be a candidate at all), ``withheld``
    is ``False``, ``readings_in_window`` is 3 (the three *stale* strap
    mornings 09-02..09-04), ``baseline_n`` is 60, ``established`` is
    ``True`` -- ``hrv_normal``. The athlete is told readiness is intact on a
    mean of three mornings he did not live, while his own three
    brand-new-device mornings -- the ones actually suppressed -- are
    silently excluded as ``off_baseline_tier``. ``research/00`` PRIN-14's
    forbidden direction: up-regulating (staying silent about suppression)
    on weak evidence.

    T132 (form B, user decision, review cycle 9, against the measured table
    in ``spec/references/T125-fix-form-measurements.md``): union today's
    ``struck`` with any tier holding **zero** baseline-window days and
    ``>= MIN_WINDOW_READINGS`` distinct judged-week days. The strict
    day-order clause is unchanged -- every one of that tier's week days must
    still be later than every judged-week day of the resolved tier, exactly
    as T125 already requires of a struck tier. After the fix:
    ``withheld=True``, and ``judge`` reports ``hrv_unavailable`` -- the
    tier, ``readings_in_window``, ``baseline_n`` and the fed days are
    unchanged, matching T125's own "the withhold changes only what is said"
    finding one axis over.

    Perturbation: reverting ``verdict_withheld`` to the shipped candidate
    gate (``struck`` alone, no zero-baseline-and-full-week union) reds this
    test; restoring it goes green again.

    **Re-pointed at T158 (F006 AC24, 2026-09-19).** ``verdict_withheld`` is
    now asked at dataset scope, of every dataset that could not have been
    selected: not judgeable, or skipped. The never-used snapshot (zero
    baseline days) is the definitional non-judgeable dataset, so this
    reproduction withholds exactly as before -- every value pinned below is
    unchanged. What widened: a snapshot with 1..13 baseline days, which
    T132's form B deliberately left as shipped, is not judgeable either and
    withholds too (``test_hrv_trend_series.py::test_probe_zero_baseline_days_versus_one_at_dataset_scope``).
    """
    strap_days = [date(2026, 7, 4) + timedelta(days=i) for i in range((date(2026, 9, 4) - date(2026, 7, 4)).days + 1)]
    snapshot_days = [date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)]
    target = date(2026, 9, 8)

    rows = [row(day, 40.0, STRAP) for day in strap_days]
    rows += [row(day, 15.0, SNAPSHOT) for day in snapshot_days]

    series = build_series(rows, AUCKLAND, target)
    verdict = judge(series)

    # The judged-week days that fed the mean -- the three stale strap
    # mornings, per T127's shape: assert the day set, not just its count.
    fed = [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]

    assert series.tier == STRAP
    assert series.withheld is True
    assert [reading.date for reading in series.window] == fed
    assert verdict.readings_in_window == 3
    assert verdict.baseline_n == 60
    assert verdict.established is True
    assert verdict.verdict == UNAVAILABLE
