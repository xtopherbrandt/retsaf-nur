"""T157 -- ``disagreed_with`` names any dataset with a computable band that reads the
other way from the selected one, judgeable or not (F006 AC10/AC11; ``research/00`` §5.4
amended 2026-09-18 (iii): "any dataset with a computable band whose judged-week mean reads
below that band is named as disagreeing, in either direction, never overriding").

**The predicate under test** is ``hrv_trend.disagreed_with``, exposed on
``Selection.disagreed_with`` (and so on ``SingleDatasetView.selection``). It reads, per
dataset, exactly two things ``judge`` already computes for that dataset -- the band over its
baseline (``build_band``, ``None`` under two readings) and the judged-week mean
(``fmean`` of ``ln rMSSD`` over its week, ``None`` on an empty week) -- and calls the
dataset *below* when the mean is **strictly less** than ``band.lo``, the same comparison
``judge`` makes for ``hrv_suppressed``. A dataset disagrees when its own ``below`` differs
from the selected dataset's. Nothing **in this predicate** consults judgeability:
``established`` and ``MIN_WINDOW_READINGS`` decide which datasets are *candidates* for
selection, never which are named here, so an unjudgeable dataset with a band is named like
any other -- AC10 taken literally.

**``withheld`` is a different matter, and the rule changed (T167, ``B-CR-002``, 2026-09-21).**
This docstring read "``established``, ``MIN_WINDOW_READINGS`` **and ``withheld``** gate the
verdict, not the report", and that is no longer true of the *served* report.
``research/00`` §5.4 (iii) as amended now states one condition for the whole of
``disagreed_with``: wherever the served ``verdict`` is ``hrv_unavailable``, for any cause,
nothing is named, because a disagreement is a claim *about* a verdict and a withheld verdict
makes no claim to contradict. A withheld dataset's verdict **is** ``hrv_unavailable``
(``week_not_representative``), so the served list is empty there. That rule lives at the
rendering seam (``main._disagreed_with``) and is pinned at the served body by
``test_hrv_dataset_populations.test_a_withheld_verdict_names_no_dissenter_and_a_conferred_one_still_does``;
the module-layer predicate this file tests is unchanged and still consults none of the three,
which is why every pin below still reads ``Selection.disagreed_with`` and not the endpoint.

**Not rendered until T159.** Every pin reads the ``hrv_trend`` layer directly
(``build_series`` -> ``select_dataset``), never the endpoint. T159 carries the rendering
assertion; T152 owns the five pinned suites, so this file is new rather than an addition
to one of them (``SCOPED_HRV_SUITES`` is an explicit path list; ``SCOPED_SUITE_COLLECTED``
does not move).

**Axes held constant** (``a-sweep-must-name-the-axes-it-holds-constant``): one target day
``D`` = 2026-09-08, zone ``Pacific/Auckland``, capture density daily on every dataset
unless a test says otherwise, baseline values alternating 38/44 ms (a real, unfloored band
of about ``+/-0.037 ln`` around ``ln 40.9``), judged-week values 40 ms (within) or 25 ms
(below). Every witness prints ``Selection.disagreement()`` -- the slice it compared
(``a-witness-must-print-the-slice-it-compared``).

**Adversarial probes** (``adversarial-input-probes-are-a-task-deliverable``) are the
``test_probe_*`` functions at the end; each states what it asserts and why the value is
degenerate, and prints the slice.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")
D = date(2026, 9, 8)

STRAP = "chest_strap_raw"
OVERNIGHT = "health_api_overnight"
SNAPSHOT = "health_snapshot"

WITHIN = 40.0  # ln 3.689, inside the 38/44 band (lo about 3.674)
BELOW = 25.0  # ln 3.219, far below it


# ---------------------------------------------------------------------------
# row builders (the shape test_hrv_trend_series.py uses; restated because the
# workspace runs pytest with --import-mode=importlib, under which no test module
# is importable by name)
# ---------------------------------------------------------------------------


def local(day: date, hh: int, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str, value: float) -> dict:
    return {
        "session_id": f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def baseline_days(n: int, target: date = D) -> list[date]:
    """``n`` consecutive local days ending on ``target - 7``, the window's last day."""
    end = target - timedelta(days=7)
    return [end - timedelta(days=i) for i in range(n)][::-1]


def week_days(n: int, target: date = D) -> list[date]:
    """``n`` consecutive local days ending on ``target``."""
    return [target - timedelta(days=i) for i in range(n)][::-1]


def alternating(tier: str, days: list[date], lo: float = 38.0, hi: float = 44.0) -> list[dict]:
    """A baseline with a real dispersion: ``lo``/``hi`` on alternate days."""
    return [row(local(day, 6), tier, lo if i % 2 == 0 else hi) for i, day in enumerate(days)]


def flat(tier: str, days: list[date], value: float) -> list[dict]:
    return [row(local(day, 6), tier, value) for day in days]


def dataset(tier: str, baseline_n: int, week_value: float, week_n: int = 7) -> list[dict]:
    """One tier: ``baseline_n`` alternating baseline days ending on ``D-7`` and
    ``week_n`` days of ``week_value`` ending on ``D``."""
    return alternating(tier, baseline_days(baseline_n)) + flat(tier, week_days(week_n), week_value)


def select(rows: list[dict]) -> hrv_trend.Selection:
    selection = hrv_trend.select_dataset(hrv_trend.build_series(rows, AUCKLAND, D))
    print(selection.describe())
    print(selection.disagreement())
    return selection


def reading(selection: hrv_trend.Selection, tier: str) -> hrv_trend.BandReading:
    return next(r for r in selection.band_readings if r.tier == tier)


# ---------------------------------------------------------------------------
# the first failing test: a non-judgeable dataset with a band disagrees
# ---------------------------------------------------------------------------


def test_a_selected_strap_within_its_band_and_a_snapshot_with_a_band_reading_below_names_the_snapshot() -> None:
    """The first failing test (T157). The strap is judgeable (14 baseline days, 7
    week days) and reads within its band; the snapshot holds a band from **8** baseline
    readings -- computable (``build_band`` needs two) but not established (< 14), so
    not judgeable -- and its week reads below it. AC10 taken literally: it is named.
    Red before this task because nothing computed the losing dataset's reading."""
    selection = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 8, BELOW))

    assert selection.selected is not None and selection.selected.tier == STRAP
    assert selection.judgeable == (STRAP,)
    assert selection.disagreed_with == (SNAPSHOT,)
    snapshot = reading(selection, SNAPSHOT)
    assert snapshot.n == 8 and snapshot.band is not None and snapshot.below is True
    assert reading(selection, STRAP).below is False


# ---------------------------------------------------------------------------
# AC11 -- both directions, and never overriding
# ---------------------------------------------------------------------------


def test_the_reverse_direction_the_selected_strap_below_while_the_snapshot_reads_within_names_the_snapshot() -> None:
    """AC11's other direction: the selected dataset reads below its band and the other
    reads within. The other is named exactly as in the first direction -- disagreement
    is a difference of sides, not "someone reads below"."""
    selection = select(dataset(STRAP, 14, BELOW) + dataset(SNAPSHOT, 14, WITHIN))

    assert selection.selected is not None and selection.selected.tier == STRAP
    assert reading(selection, STRAP).below is True
    assert reading(selection, SNAPSHOT).below is False
    assert selection.disagreed_with == (SNAPSHOT,)


@pytest.mark.parametrize("week_value", [WITHIN, BELOW], ids=["both_within", "both_below"])
def test_two_datasets_on_the_same_side_of_their_own_bands_do_not_disagree(week_value: float) -> None:
    """Agreement in either direction is not a disagreement. ``both_below`` is the case a
    naive reading of AC10 ("any dataset reading below is named") gets wrong: the snapshot
    reads below, and it is *not* named, because the selected strap reads below too."""
    selection = select(dataset(STRAP, 14, week_value) + dataset(SNAPSHOT, 14, week_value))

    assert reading(selection, STRAP).below == reading(selection, SNAPSHOT).below
    assert selection.disagreed_with == ()


@pytest.mark.parametrize(
    ("strap_value", "expected"),
    [(WITHIN, hrv_trend.VERDICT_NORMAL), (BELOW, hrv_trend.VERDICT_SUPPRESSED)],
    ids=["normal_over_a_dissenting_snapshot", "suppressed_over_a_dissenting_snapshot"],
)
def test_disagreement_never_overrides_the_selected_datasets_verdict(strap_value: float, expected: str) -> None:
    """AC11: ``hrv_status`` is the selected dataset's verdict, unchanged. Pinned two ways:
    the verdict through ``selected_view`` equals the verdict of the same strap rows
    judged **with the dissenting snapshot removed from the series entirely**, and it is
    the value the strap's own reading says it is. The ``hrv_normal`` row is the §1.7
    exposure F006 accepts (reference §4): promoted while contrary evidence exists, and
    the contrary evidence is reported beside it, not acted on."""
    strap_rows = dataset(STRAP, 14, strap_value)
    other_value = BELOW if strap_value == WITHIN else WITHIN
    with_dissent = hrv_trend.build_series(strap_rows + dataset(SNAPSHOT, 14, other_value), AUCKLAND, D)
    alone = hrv_trend.build_series(strap_rows, AUCKLAND, D)

    view = hrv_trend.selected_view(with_dissent)
    assert view.selection is not None
    print(view.selection.disagreement())
    assert view.selection.disagreed_with == (SNAPSHOT,)

    assert hrv_trend.judge(view).verdict == expected
    assert hrv_trend.judge(view).verdict == hrv_trend.judge(hrv_trend.selected_view(alone)).verdict
    assert hrv_trend.select_dataset(alone).disagreed_with == ()


def test_the_predicate_reads_the_band_and_mean_judge_computes_not_a_second_derivation() -> None:
    """The reading against the band is ``judge``'s own ``band`` and ``ln_rmssd_7d_mean``
    for that dataset, and the band is the one ``build_series`` already carries on it --
    one arithmetic, three readers. If any of the three drift, this reds."""
    series = hrv_trend.build_series(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 8, BELOW), AUCKLAND, D)
    selection = hrv_trend.select_dataset(series)
    print(selection.disagreement())

    for ds in series.datasets:
        verdict = hrv_trend.judge(ds)
        against = reading(selection, ds.tier)
        assert against.band == verdict.band == ds.band
        assert against.week_mean == verdict.ln_rmssd_7d_mean
        assert against.n == ds.n == verdict.baseline_n
        assert against.week_days == len({r.date for r in ds.window}) == verdict.readings_in_window


def test_disagreed_with_is_in_fidelity_order_and_never_names_the_selected_dataset() -> None:
    """Three datasets (N = 3 is reachable only by hand-built rows: ``health_api_overnight``
    is never written by the classifier, reference §10). The two dissenters are named in
    ``TIER_FIDELITY`` order, and the selected one is never in its own list."""
    selection = select(dataset(STRAP, 14, WITHIN) + dataset(OVERNIGHT, 5, BELOW) + dataset(SNAPSHOT, 14, BELOW))

    assert selection.selected is not None and selection.selected.tier == STRAP
    assert hrv_trend.TIER_FIDELITY == (STRAP, SNAPSHOT, OVERNIGHT)
    assert selection.disagreed_with == (SNAPSHOT, OVERNIGHT)
    assert STRAP not in selection.disagreed_with


# ---------------------------------------------------------------------------
# the 2-reading boundary (deliverable 4) and the bandless dataset (deliverable 2)
# ---------------------------------------------------------------------------


def test_one_baseline_reading_yields_no_band_and_no_entry_but_a_visible_n_and_week_count() -> None:
    """Deliverable 2/4: one baseline reading -> ``build_band`` returns ``None``
    (``stdev`` needs two) -> ``below`` is ``None`` -> not named, however low the week
    reads. The dataset is still visible on ``band_readings`` with ``n`` 1 and its
    judged-week count, which is what T159 renders in its place."""
    selection = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 1, BELOW, week_n=5))

    snapshot = reading(selection, SNAPSHOT)
    assert snapshot.band is None and snapshot.below is None
    assert snapshot.n == 1 and snapshot.week_days == 5
    assert snapshot.week_mean == pytest.approx(math.log(BELOW))
    assert selection.disagreed_with == ()


def test_exactly_two_baseline_readings_yield_a_band_and_can_disagree() -> None:
    """Deliverable 4, the other side of the boundary: two readings are the minimum
    ``build_band`` accepts, and a dataset holding exactly that many disagrees like any
    other. Two is nowhere near established (14); judgeability is not consulted."""
    selection = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 2, BELOW))

    snapshot = reading(selection, SNAPSHOT)
    assert snapshot.n == 2 and snapshot.band is not None and snapshot.below is True
    assert selection.disagreed_with == (SNAPSHOT,)


def test_a_dataset_with_a_band_but_no_judged_week_reading_cannot_disagree() -> None:
    """No week mean -> nothing to read against the band -> ``below`` ``None`` -> not
    named. The empty week is the degenerate form of the *other* input the predicate
    reads (the boundary probes cover the baseline's)."""
    selection = select(dataset(STRAP, 14, WITHIN) + alternating(SNAPSHOT, baseline_days(14)))

    snapshot = reading(selection, SNAPSHOT)
    assert snapshot.band is not None and snapshot.week_mean is None and snapshot.week_days == 0
    assert snapshot.below is None
    assert selection.disagreed_with == ()


# ---------------------------------------------------------------------------
# adversarial probes -- the degenerate set the task file requires
# ---------------------------------------------------------------------------


def test_probe_exactly_two_and_exactly_one_baseline_readings_straddle_the_band_minimum() -> None:
    """Asserts: the boundary of ``build_band`` is the boundary of the predicate -- a
    snapshot with one baseline reading is bandless and unnamed, and the very next
    reading gives it a band and an entry. Degenerate because ``n`` = 2 is the smallest
    sample ``stdev`` accepts and the band it yields is a two-point dispersion, which the
    predicate treats as no less a band than fourteen points."""
    one = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 1, BELOW))
    two = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 2, BELOW))

    assert reading(one, SNAPSHOT).band is None and one.disagreed_with == ()
    assert reading(two, SNAPSHOT).band is not None and two.disagreed_with == (SNAPSHOT,)


def _ln_exactly(target_ln: float) -> float:
    """A positive float ``v`` with ``math.log(v) == target_ln`` **exactly**, found by
    walking ``nextafter`` from ``exp(target_ln)``; the test needs the week mean to sit on
    ``band.lo`` to the last bit, and ``exp``/``log`` are not exact inverses."""
    v = math.exp(target_ln)
    for _ in range(64):
        if math.log(v) == target_ln:
            return v
        v = math.nextafter(v, 0.0 if math.log(v) > target_ln else math.inf)
    raise AssertionError(f"no float logs exactly to {target_ln!r}")


def _first_under(start: float, edge: float, mean_of: Callable[[float], float]) -> float:
    """The first float below ``start`` (walking ``nextafter`` toward 0) whose ``mean_of``
    is strictly less than ``edge``."""
    v = start
    for _ in range(256):
        v = math.nextafter(v, 0.0)
        if mean_of(v) < edge:
            return v
    raise AssertionError(f"no float under {start!r} means under {edge!r}")


def _three_that_mean_exactly(target_ln: float) -> float:
    """A positive float ``v`` such that ``fmean([log(v)] * 3) == target_ln`` exactly --
    ``fmean`` sums then divides, so three equal terms need not mean to themselves to
    the last bit; walk ``nextafter`` until they do."""
    v = math.exp(target_ln)
    for _ in range(256):
        mean = statistics.fmean([math.log(v)] * 3)
        if mean == target_ln:
            return v
        v = math.nextafter(v, 0.0 if mean > target_ln else math.inf)
    raise AssertionError(f"no three equal readings mean exactly to {target_ln!r}")


def test_probe_a_week_mean_exactly_on_band_lo_is_not_below_strictly_less_matching_judge() -> None:
    """Asserts: "below" is **strictly less than** ``band.lo`` -- a mean sitting on the
    edge to the last bit is *within*, exactly as ``judge`` reads ``hrv_normal`` on it --
    and the first float whose mean falls under the edge is below (``log`` is many-to-one
    near 40 ms, so "one ulp under" in rMSSD is not always one ulp under in ``ln``). Degenerate because the edge is the one value
    where ``<`` and ``<=`` differ, and a predicate that disagreed with ``judge`` there
    would name a dataset as reading below a band its own verdict calls normal. The
    week is a single reading so that ``fmean`` returns the value itself (a three-reading
    mean of equal values is not bit-exact in general)."""
    band = hrv_trend.judge(hrv_trend.build_series(alternating(SNAPSHOT, baseline_days(14)), AUCKLAND, D).datasets[0]).band
    assert band is not None
    on_the_edge = _ln_exactly(band.lo)
    assert math.log(on_the_edge) == band.lo
    just_under = _first_under(on_the_edge, band.lo, lambda v: math.log(v))

    on = select(dataset(STRAP, 14, WITHIN) + alternating(SNAPSHOT, baseline_days(14)) + flat(SNAPSHOT, [D], on_the_edge))
    under = select(
        dataset(STRAP, 14, WITHIN) + alternating(SNAPSHOT, baseline_days(14)) + flat(SNAPSHOT, [D], just_under)
    )

    assert reading(on, SNAPSHOT).week_mean == band.lo
    assert reading(on, SNAPSHOT).below is False and on.disagreed_with == ()
    assert reading(under, SNAPSHOT).week_mean < band.lo
    assert reading(under, SNAPSHOT).below is True and under.disagreed_with == (SNAPSHOT,)
    # the same two values through judge, on a judgeable copy of the snapshot whose three
    # week readings mean to the edge exactly: normal on it, suppressed one ulp under it
    on_edge_three = _three_that_mean_exactly(band.lo)
    under_three = _first_under(on_edge_three, band.lo, lambda v: statistics.fmean([math.log(v)] * 3))
    for value, expected in ((on_edge_three, hrv_trend.VERDICT_NORMAL), (under_three, None)):
        week = alternating(SNAPSHOT, baseline_days(14)) + flat(SNAPSHOT, week_days(3), value)
        verdict = hrv_trend.judge(hrv_trend.build_series(week, AUCKLAND, D).datasets[0])
        print(f"judge on the edge: value={value!r} mean={verdict.ln_rmssd_7d_mean!r} lo={band.lo!r} -> {verdict.verdict}")
        if expected is not None:
            assert verdict.ln_rmssd_7d_mean == band.lo and verdict.verdict == expected
        else:
            assert verdict.ln_rmssd_7d_mean < band.lo and verdict.verdict == hrv_trend.VERDICT_SUPPRESSED


@pytest.mark.parametrize("selected_value", [WITHIN, BELOW], ids=["selected_within", "selected_below"])
def test_probe_both_directions_at_once_names_only_the_dataset_on_the_other_side(selected_value: float) -> None:
    """Asserts: with two non-selected datasets on **opposite** sides of their own bands,
    exactly the one on the other side from the selected dataset is named -- so both
    directions are exercised in one series and the rule is "differs from the selected",
    not "reads below". Degenerate because a "names whoever reads below" predicate and a
    "names whoever reads within while the selected reads below" predicate each pass one
    parametrisation and fail the other."""
    selection = select(
        dataset(STRAP, 14, selected_value) + dataset(OVERNIGHT, 14, BELOW) + dataset(SNAPSHOT, 14, WITHIN)
    )

    assert selection.selected is not None and selection.selected.tier == STRAP
    expected = SNAPSHOT if selected_value == BELOW else OVERNIGHT
    assert selection.disagreed_with == (expected,)


def test_probe_every_dataset_disagreeing_names_all_of_them_in_fidelity_order() -> None:
    """Asserts: when every other dataset dissents, all are named, in ``TIER_FIDELITY``
    order, and the selected one is not. Degenerate because it is the maximum the list
    can hold (N - 1) and the case where a consumer reading ``hrv_status`` alone is
    most wrong -- the §1.7 exposure at full strength, reported and still not acted on:
    the verdict is ``hrv_normal``."""
    rows = dataset(STRAP, 14, WITHIN) + dataset(OVERNIGHT, 14, BELOW) + dataset(SNAPSHOT, 3, BELOW)
    selection = select(rows)

    assert selection.disagreed_with == (SNAPSHOT, OVERNIGHT)
    assert hrv_trend.judge(hrv_trend.selected_view(hrv_trend.build_series(rows, AUCKLAND, D))).verdict == (
        hrv_trend.VERDICT_NORMAL
    )


def test_probe_a_degenerate_band_at_the_floor_still_disagrees_and_still_has_an_inside() -> None:
    """Asserts: a metronomic baseline (fourteen identical readings, SD 0) gets the
    ``BAND_FLOOR`` band of ``+/-0.01 ln`` -- ``floored`` is ``True`` -- and the
    predicate reads against it like any other: a week ``0.02 ln`` under the mean is
    below and named; a week *on* the mean is within and not. Degenerate because the
    computed half-width is zero, so without the floor ``lo == mean`` and any week not
    bit-identical to the baseline would read below (``discriminators`` need a reachable
    inside: ``BAND_FLOOR``'s own docstring)."""
    floored_baseline = flat(SNAPSHOT, baseline_days(14), 40.0)
    just_under = 40.0 * math.exp(-0.02)

    under = select(dataset(STRAP, 14, WITHIN) + floored_baseline + flat(SNAPSHOT, week_days(3), just_under))
    on_mean = select(dataset(STRAP, 14, WITHIN) + floored_baseline + flat(SNAPSHOT, week_days(3), 40.0))

    band = reading(under, SNAPSHOT).band
    assert band is not None and band.floored and band.half_width == hrv_trend.BAND_FLOOR
    assert reading(under, SNAPSHOT).below is True and under.disagreed_with == (SNAPSHOT,)
    assert reading(on_mean, SNAPSHOT).below is False and on_mean.disagreed_with == ()


def test_probe_no_selected_dataset_means_nothing_to_disagree_with() -> None:
    """Asserts: when nothing is judgeable (``selected`` ``None``, the illness week --
    both established, neither with three week days) ``disagreed_with`` is empty even
    though a dataset reads below its band, while ``band_readings`` still carries both
    readings for T159. Degenerate because the presentation fallback (AC9) presents a
    dataset without conferring a verdict, and a disagreement is a disagreement with a
    **verdict**; naming a dissenter against ``hrv_unavailable`` would report a
    contradiction of a claim that was never made."""
    selection = select(dataset(STRAP, 14, WITHIN, week_n=2) + dataset(SNAPSHOT, 14, BELOW, week_n=2))

    assert selection.selected is None and selection.judgeable == ()
    assert reading(selection, SNAPSHOT).below is True
    assert reading(selection, STRAP).below is False
    assert selection.disagreed_with == ()


def test_probe_a_single_judged_week_reading_is_a_mean_and_can_disagree() -> None:
    """Asserts: a dataset with one judged-week reading has a week mean (``judge``
    reports ``ln_rmssd_7d_mean`` on any non-empty week) and is named when it reads
    below. Degenerate because §3.7.4's "fewer than three readings is unavailable,
    whatever they say" is a rule about the **verdict** and is honoured there
    (``MIN_WINDOW_READINGS``); AC10 says "whether or not judgeable" and a one-reading
    week is the least judgeable a week with a mean can be. Recorded as the argument,
    so the next reader does not re-derive the doubt (T159 renders the week count
    beside the name, which is how a consumer weighs it)."""
    selection = select(dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 14, BELOW, week_n=1))

    snapshot = reading(selection, SNAPSHOT)
    assert snapshot.week_days == 1 and snapshot.below is True
    assert SNAPSHOT not in selection.judgeable
    assert selection.disagreed_with == (SNAPSHOT,)


def test_probe_disagreed_with_is_a_function_of_the_selection_not_of_the_view() -> None:
    """Asserts: ``selected_view`` exposes the same ``disagreed_with`` as
    ``select_dataset`` -- one computation, read through the shape the route holds
    until T159 -- and that a series with one dataset has an empty list (nothing to
    compare against). Degenerate because N = 1 is the shape every pre-F006 fixture
    has, and the predicate must be inert on it."""
    rows = dataset(STRAP, 14, WITHIN) + dataset(SNAPSHOT, 14, BELOW)
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    view = hrv_trend.selected_view(series)
    assert view.selection is not None
    assert view.selection.disagreed_with == hrv_trend.select_dataset(series).disagreed_with == (SNAPSHOT,)

    lone = select(dataset(STRAP, 14, BELOW))
    assert lone.selected is not None and lone.disagreed_with == () and len(lone.band_readings) == 1
