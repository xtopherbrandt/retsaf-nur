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
  it on one -- reads as a verdict: both are **unavailable**. §3.7.3 says the
  suppression is *withheld* until the baseline is established, and
  ``hrv_normal`` would tell Section 6 that readiness is intact on the
  strength of a baseline the same response reports unestablished -- up-
  regulating on weak evidence, which ``research/00`` §1.7 forbids.
  ``hrv_unavailable`` makes the readiness logic widen its guardrails instead.
- **The asymmetry was the defect** ([[IDEA-062]]). Until T116 this docstring
  argued §1.7 for the below-band cell alone and the table returned ``normal``
  regardless of establishment for its two neighbours, so a week judged
  against a band built from 2 to 13 readings reported ``hrv_normal`` -- and
  that is reachable after **every** reset this feature performs, since a gap
  reset or a tier change collapses the baseline and the athlete then
  traverses ~12 unestablished days. Eight of the 90 rows below moved with the
  rule; the direct pin is
  ``test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``.

The table enumerates ``{baseline n: 0, 1, 2, 13, 14, 41} x {window n: 0, 1,
2, 3, 7} x {mean: below, inside, above}`` exhaustively -- 6 x 5 x 3 = **90**
rows, the count ``test_the_contract_table_is_exhaustive`` asserts and the
count the paragraph above uses -- boring rows included, so an omitted cell
would be visible (contract-tables-need-an-independent-oracle). The window
axis is five values, not four: ``window n: 1`` was dropped from this sentence
while ``CONTRACT_TABLE`` kept it, which made the enumeration describe a
72-row table this module has never had (corrected 2026-09-15, T122).

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
from runcoach_api.metrics.hrv_trend import build_series, judge

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
    reports unestablished, the up-regulating direction ``research/00`` §1.7
    forbids, and it is reachable after every reset the feature performs.

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
    until T122 ("the input that would stay green")."""
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
    """The window minimum is §3.7.4's trends-not-single-readings rule: two
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
    """``research/00`` §1.6: the verdict must be reproducible from what the
    response reports, so the constants are module-level and named."""
    assert hrv_trend.SWC_FACTOR == 0.5
    assert hrv_trend.BAND_FLOOR == 0.01
    assert hrv_trend.MIN_WINDOW_READINGS == 3
    assert hrv_trend.MIN_BASELINE_READINGS == 14
    assert hrv_trend.BASELINE_DAYS == 60
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
        # T116: the establishment gate is symmetric. Below the band the
        # suppression is withheld; inside or above it the ``normal`` is
        # withheld for the same reason -- ``hrv_normal`` on a 2-to-13-reading
        # baseline asserts intact readiness on evidence the same response
        # calls unestablished, the up-regulating direction ``research/00``
        # §1.7 forbids. Both are ``hrv_unavailable``, and the band is still
        # reported so the consumer can see what was withheld.
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
