"""T154 -- ``tier_change_reset`` asks its cross-tier question once per dataset,
with the stray population left global (F006 AC17 second half, AC16).

Under F005 ``build_series`` resolved **one** tier and asked rule 4 about it:
``tier_change_reset(previous_readings, tier, ...)`` took *the* resolved tier,
``None`` when the resolver found nothing. Under F006 there is no such thing --
every tier present has a dataset -- so the same cross-tier question (clauses
(a), (b) and (c), unchanged) is asked **once per dataset ``T`` with
``tier=T``**, from one call site inside ``build_series``'s per-dataset loop
that receives no resolved tier. T151 put that call site in place; this file
pins the property three-valued (green on T151's build, red against a mutant
that hands the selected tier to every dataset, green here) and retires the
``str | None`` half of the signature that only the resolver could supply.

What stays exactly as it was, asserted here rather than assumed:

* **the stray population is every tier's unclipped readings in ``[D-66, D]``**
  (T129) -- not the dataset's own readings, and not the gap-clipped set. Two
  mutants, one geometry each, because neither geometry can see the other's
  mutant: a per-dataset stray set is visible only when another tier's
  readings inside the window decide the boundary, and the gap-clipped set
  only when a gap has clipped something;
* **``coverage_gap_reset`` is global and series-wide** (AC16): asked once,
  before the partition, over every tier together;
* **``_era_boundary``'s three-term ordering key** (week-clear first, fewest
  strays, later ``A_end``), which orders candidates within one ``(old, new)``
  pair and is untouched by asking that pair per dataset.

And one decision, pinned: **the reported reset is per dataset**, so the
``reset_on``/``reset_reason`` the route presents are the **selected**
dataset's own (or the presentation fallback's, or the gap's when no dataset
exists); a non-selected dataset's reported reset is carried in ``datasets``
and never promoted to the view.

Every test prints the slice it compared (``[slice compared] ...``).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")

# The target date the other HRV suites use: baseline [2026-07-04, 2026-09-01],
# judged week [2026-09-02, 2026-09-08], previous window [2026-05-05, 2026-07-03].
D = date(2026, 9, 8)

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"
OVERNIGHT = "health_api_overnight"

TIER_CHANGE = "tier_change"
COVERAGE_GAP = "coverage_gap"
BEFORE_RESET_TIER_CHANGE = "before_reset: tier_change"

#: The real rule, bound at import so a second mutant in one test wraps it and
#: not the first mutant (``monkeypatch`` stacks; measured while this was written).
REAL_TIER_CHANGE_RESET = hrv_trend.tier_change_reset


# ---------------------------------------------------------------------------
# row builders (the same shapes as test_hrv_trend_series.py; duplicated rather
# than imported across test modules)
# ---------------------------------------------------------------------------


def local(day: date, hh: int, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str | None, value: float | None, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def readings(tier: str, days: list[date], value: float, hh: int, prefix: str) -> list[dict]:
    return [row(local(day, hh), tier, value, f"{prefix}-{day}") for day in days]


def parsed(day: date, hh: int, tier: str) -> hrv_trend.Reading:
    """One already-parsed ``Reading`` at local ``hh:00`` on ``day``, for the
    pins that call ``_era_boundary`` directly."""
    return hrv_trend.Reading(day, f"{tier}-{day}-{hh}", tier, 40.0, datetime.fromisoformat(local(day, hh)))


def ago(n: int) -> date:
    return D - timedelta(days=n)


def between(first: date, last: date) -> list[date]:
    """Every local day of the closed interval ``[first, last]``."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def dataset(series: hrv_trend.HrvSeries, tier: str) -> hrv_trend.HrvDataset:
    return next(d for d in series.datasets if d.tier == tier)


def report(d: hrv_trend.HrvDataset) -> tuple[date | None, str | None]:
    return d.reset_on, d.reset_reason


def slice_of(series: hrv_trend.HrvSeries, label: str = "") -> str:
    parts = []
    for d in series.datasets:
        tier_changes = sum(
            1
            for e in series.excluded
            if e.reason == BEFORE_RESET_TIER_CHANGE and e.session_id.startswith(d.tier[:4])
        )
        parts.append(
            f"{d.tier}: reset=({d.reset_on}, {d.reset_reason}) window={d.baseline_window[0]}..{d.baseline_window[1]} "
            f"n={d.n} established={d.established} before_reset_tier_change={tier_changes}"
        )
    line = (
        f"[slice compared]{' ' + label if label else ''} gap_reset_on={series.gap_reset_on} | "
        + " | ".join(parts)
    )
    print(line)
    return line


def accounted_once(rows: list[dict], series: hrv_trend.HrvSeries) -> None:
    """research/00 PRIN-23 under F006 (AC15): every row inside ``[D-66, D]`` is
    in exactly one dataset's ``series`` or in ``excluded``, never both."""
    listed = [r.session_id for d in series.datasets for r in d.series] + [
        e.session_id for e in series.excluded
    ]
    assert sorted(listed) == sorted(r["session_id"] for r in rows), "every row listed exactly once"


def with_forced_tier(monkeypatch: pytest.MonkeyPatch, forced: str) -> None:
    """The mutant the three-valued pin is red against: ``build_series`` hands
    **one** tier -- ``forced``, the selected one -- to every dataset's call,
    which is what a resolver-shaped call site would do. Applied through the
    module attribute the loop calls by name, so ``build_series`` itself is
    untouched and the mutant is exactly the argument."""

    def mutant(previous_readings, tier, *args, **kwargs):
        return REAL_TIER_CHANGE_RESET(previous_readings, forced, *args, **kwargs)

    monkeypatch.setattr(hrv_trend, "tier_change_reset", mutant)


def with_stray_population(monkeypatch: pytest.MonkeyPatch, narrow) -> None:
    """The two stray-population mutants: ``narrow(tier, population)`` returns
    what the mutant hands rule 4 in place of every tier's unclipped
    readings."""

    def mutant(previous_readings, tier, baseline_readings, week_readings, judged, stray_population=None):
        return REAL_TIER_CHANGE_RESET(
            previous_readings,
            tier,
            baseline_readings,
            week_readings,
            judged,
            stray_population=narrow(tier, stray_population, baseline_readings, week_readings),
        )

    monkeypatch.setattr(hrv_trend, "tier_change_reset", mutant)


# ---------------------------------------------------------------------------
# the first failing test: one call site, one answer per dataset
# ---------------------------------------------------------------------------

#: The switch: a daily snapshot era through the previous window and into
#: this one, abandoned on ``D-40``; a daily strap from ``D-39``.
SWITCH = ago(39)


def snapshot_then_strap() -> list[dict]:
    """A two-dataset history with one genuine switch and nothing across the
    boundary: snapshot daily ``D-126..D-40`` at 07:00 (60 ms), strap daily
    ``D-39..D`` at 06:00 (40 ms). For the strap, clauses (a) (33 strap days in
    ``[D-66, D-7]``), (b) (the previous window is the snapshot's) and (c)
    (zero strays) all hold, so its era begins ``D-39`` and is **reported**.
    For the snapshot, (b) short-circuits -- it *is* ``previous_tier`` -- so
    the answer is ``None`` and its band keeps every one of its 27 window
    days (the "known" case of the task's Technical Notes)."""
    rows = readings(SNAPSHOT, between(ago(126), SWITCH - timedelta(days=1)), 60.0, 7, "snap")
    rows += readings(STRAP, between(SWITCH, D), 40.0, 6, "strap")
    return rows


def test_a_two_dataset_history_reports_the_reset_for_the_incoming_dataset_and_none_for_the_outgoing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's first failing test, three-valued.

    **Green on T151's build** for the behaviour: the strap dataset reports
    ``(D-39, tier_change)`` on a window clipped to ``D-39`` and the snapshot
    dataset reports ``(None, None)`` on ``[D-66, D-7]`` with all 27 days --
    T151 already asks the question once per dataset with that dataset's
    tier. **Red on T151's build** for the signature: ``tier`` was still
    typed ``str | None`` with a ``None`` short-circuit that only F005's
    resolver could reach (no dataset has no tier), the shape the task names
    as "still taking a resolved tier". **Red against the mutant** that hands
    the selected tier (the strap's) to every dataset's call: the snapshot
    dataset then inherits the strap's boundary -- ``(D-39, tier_change)``,
    its window clipped to ``D-39`` and all 27 of its readings listed
    ``before_reset: tier_change``, ``n`` 27 -> 0 -- and against the mirror
    mutant (the outgoing tier to every call) the strap loses its report.
    Both measured in-process below.
    """
    rows = snapshot_then_strap()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(series, "real")
    accounted_once(rows, series)

    strap, snapshot = dataset(series, STRAP), dataset(series, SNAPSHOT)
    assert report(strap) == (SWITCH, TIER_CHANGE)
    assert strap.baseline_window == (SWITCH, ago(7))
    assert strap.n == 33 and strap.established
    assert report(snapshot) == (None, None)
    assert snapshot.baseline_window == (ago(66), ago(7))
    assert snapshot.n == 27 and snapshot.established
    assert BEFORE_RESET_TIER_CHANGE not in {e.reason for e in series.excluded}
    assert series.gap_reset_on is None

    # The signature: a dataset's tier, never a resolver's ``None``.
    tier_param = inspect.signature(hrv_trend.tier_change_reset).parameters["tier"]
    assert tier_param.annotation == "str", f"tier is typed {tier_param.annotation!r}: a resolved-tier shape"

    # Red against the mutant: the selected tier to every dataset.
    with_forced_tier(monkeypatch, STRAP)
    mutated = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(mutated, "mutant: tier=chest_strap_raw to every dataset")
    assert report(dataset(mutated, SNAPSHOT)) == (SWITCH, TIER_CHANGE), (
        "the mutant is visible on the snapshot"
    )
    assert dataset(mutated, SNAPSHOT).n == 0
    assert report(dataset(mutated, STRAP)) == report(strap), "the selected dataset itself cannot tell"

    with_forced_tier(monkeypatch, SNAPSHOT)
    mirrored = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(mirrored, "mutant: tier=health_snapshot to every dataset")
    assert report(dataset(mirrored, STRAP)) == (None, None), "the mirror mutant is visible on the strap"
    assert dataset(mirrored, STRAP).baseline_window == (ago(66), ago(7))


def _build_series_ast() -> ast.FunctionDef:
    module = ast.parse(textwrap.dedent(inspect.getsource(hrv_trend.build_series)))
    return next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "build_series"
    )


def _calls_to(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == name
    ]


def test_the_one_call_site_is_inside_the_per_dataset_loop_and_hands_it_the_loops_tier() -> None:
    """The structural half of deliverable 1, read off ``build_series``'s
    source: ``tier_change_reset`` is called exactly once, inside the
    ``for tier in TIER_FIDELITY`` loop, and its ``tier`` argument is that
    loop's own variable -- no resolved tier exists to hand it (nothing in
    ``build_series`` names ``resolve_baseline_tier``), and every dataset's
    call reads the same ``stray_population`` keyword. A second call site, or
    one outside the loop, is the F005 shape coming back."""
    fn = _build_series_ast()
    calls = _calls_to(fn, "tier_change_reset")
    assert len(calls) == 1, f"{len(calls)} call sites"
    (call,) = calls
    assert isinstance(call.args[1], ast.Name) and call.args[1].id == "tier", ast.dump(call.args[1])
    assert {kw.arg for kw in call.keywords} == {"stray_population"}

    loops = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "tier"
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "TIER_FIDELITY"
    ]
    assert len(loops) == 1
    assert call in [node for node in ast.walk(loops[0])], "the call is inside the per-dataset loop"
    assert not _calls_to(fn, "resolve_baseline_tier")
    print(
        "[slice compared] build_series: 1 tier_change_reset call, args[1]=tier, inside `for tier in TIER_FIDELITY`"
    )


# ---------------------------------------------------------------------------
# deliverable 2: the stray population is every tier's unclipped readings
# ---------------------------------------------------------------------------


def interleaved_snapshot_habit() -> list[dict]:
    """The T094 population clause (c) exists for: snapshot daily
    ``D-126..D-40`` at 07:00, strap daily ``D-39..D`` at 06:00 -- the
    switch above -- **plus fourteen snapshot mornings inside the strap era**,
    ``D-30..D-17``. Fourteen distinct days is ``MIN_BASELINE_READINGS``: the
    other device was in use, the eras interleave, and no boundary is
    admitted (every ``A_end`` carries at least 14 strays). Nothing is
    reported and nothing is clipped. Those fourteen are visible to rule 4
    only because the stray population is every tier's readings in
    ``[D-66, D]``; a per-dataset set would hide them."""
    rows = snapshot_then_strap()
    rows += readings(SNAPSHOT, between(ago(30), ago(17)), 60.0, 7, "habit")
    return rows


def test_the_stray_population_is_every_tiers_readings_not_the_datasets_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T094's clause (c) at dataset scope: the strap dataset reports
    **nothing** on ``interleaved_snapshot_habit`` and keeps ``[D-66, D-7]``,
    because the fourteen snapshot mornings inside its era are strays of
    every candidate boundary. Red against the mutant that narrows the stray
    population to the dataset's own tier: the snapshot habit vanishes from
    the strap's view of the history, the previous window's last snapshot
    becomes ``A_end`` with zero strays, and the strap reports
    ``(D-39, tier_change)`` -- G6's rejected shape, a reset on every strap
    week that the athlete's own capture history refuses. The gap-clipped
    mutant is **not** visible here (no gap fired, so clipped equals
    unclipped); the test below carries that one.
    """
    rows = interleaved_snapshot_habit()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(series, "real")
    accounted_once(rows, series)

    strap = dataset(series, STRAP)
    assert report(strap) == (None, None)
    assert strap.baseline_window == (ago(66), ago(7))
    assert strap.n == 33
    assert report(dataset(series, SNAPSHOT)) == (None, None)
    assert dataset(series, SNAPSHOT).n == 27 + 14

    with_stray_population(monkeypatch, lambda tier, population, *_: hrv_trend._of_tier(population, tier))
    narrowed = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(narrowed, "mutant: stray_population narrowed to the dataset's own tier")
    assert report(dataset(narrowed, STRAP)) == (SWITCH, TIER_CHANGE), "the per-dataset stray set reverts T094"
    assert dataset(narrowed, STRAP).baseline_window == (SWITCH, ago(7))


#: The resumption after the global gap in ``gap_then_late_snapshots``.
RESUMPTION = ago(21)


def gap_then_late_snapshots() -> list[dict]:
    """T129's shape, rebuilt at dataset scope: snapshot daily ``D-126..D-45``
    at 07:00; a ten-day strap trial ``D-59..D-50`` inside that era; 23
    silent days (``D-44..D-22``, a global gap, resumption ``D-21``); then
    the strap daily ``D-21..D`` at 06:00 with eleven late snapshot mornings
    ``D-21..D-11`` at 07:00 beside it.

    On the full history every boundary is refused: the last pre-gap
    ``A_end`` (``D-45``) has 21 strays (11 late snapshots + the 10-day
    trial) and every late ``A_end`` has 21 too (the trial + the strap days
    up to it). So the gap's clip is the only clip and the strap keeps
    ``[D-21, D-7]``, fifteen days. On the **gap-clipped** population the
    trial is invisible: every candidate falls to exactly 11 strays, the tie
    goes to the latest ``A_end`` (``D-11``), and the strap's era is dated
    ``D-10`` -- after the resumption, costing eleven on-tier mornings."""
    rows = readings(SNAPSHOT, between(ago(126), ago(45)), 60.0, 7, "snap")
    rows += readings(STRAP, between(ago(59), ago(50)), 40.0, 6, "trial")
    rows += readings(STRAP, between(RESUMPTION, D), 40.0, 6, "strap")
    rows += readings(SNAPSHOT, between(RESUMPTION, ago(11)), 60.0, 7, "late")
    return rows


def test_the_stray_population_is_the_unclipped_set_not_the_gap_clipped_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T129 at dataset scope: the strap dataset reports the gap
    (``D-21, coverage_gap``) on a window clipped at the resumption, ``n``
    15, and nothing is listed ``before_reset: tier_change``. Red against
    the mutant that hands rule 4 the gap-clipped readings (T129 reverted):
    the strap's window opens ``D-10``, ``n`` 4, unestablished, with eleven
    on-tier mornings ``D-21..D-11`` listed ``before_reset: tier_change`` --
    the G-C7-3 damage T129 closed, and the report still reads
    ``coverage_gap``, so nothing in the response says the band moved. The
    per-dataset mutant is **not** visible here: with only the strap's own
    readings the boundary is the trial's first day, ``D-59``, which the
    gap's later clip swallows -- the test above carries that one.
    """
    rows = gap_then_late_snapshots()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(series, "real")
    accounted_once(rows, series)

    strap = dataset(series, STRAP)
    assert series.gap_reset_on == RESUMPTION
    assert report(strap) == (RESUMPTION, COVERAGE_GAP)
    assert strap.baseline_window == (RESUMPTION, ago(7))
    assert strap.n == 15 and strap.established
    assert BEFORE_RESET_TIER_CHANGE not in {e.reason for e in series.excluded}

    with_stray_population(monkeypatch, lambda tier, population, baseline, week: (*baseline, *week))
    clipped = hrv_trend.build_series(rows, AUCKLAND, D)
    slice_of(clipped, "mutant: stray_population = the gap-clipped readings")
    mutated = dataset(clipped, STRAP)
    assert mutated.baseline_window == (ago(10), ago(7)), (
        "the gap-clipped stray set dates the era after the resumption"
    )
    assert mutated.n == 4 and not mutated.established
    assert report(mutated) == (RESUMPTION, COVERAGE_GAP), "and the report does not say so"
    assert {e.session_id for e in clipped.excluded if e.reason == BEFORE_RESET_TIER_CHANGE} == {
        f"strap-{day}" for day in between(RESUMPTION, ago(11))
    }


# ---------------------------------------------------------------------------
# deliverable 3: coverage_gap_reset stays global (AC16)
# ---------------------------------------------------------------------------


def test_the_coverage_gap_is_asked_once_over_every_tier_before_the_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC16, asserted and not changed. A strap silent for 27 local days
    (``D-29..D-3``) while a daily snapshot carries the series: no global gap
    fires, no dataset reports one, and the strap's band is not clipped
    either -- the hole's resumption ``D-2`` is in the judged week, so it is
    not internal to the baseline window (T153's boundary). The three-valued
    shape without a mutant: ``coverage_gap_reset`` over the strap's own
    readings **would** return ``D-2`` (what a per-dataset gap would do) and
    the twin series with the snapshot removed does report
    ``(D-2, coverage_gap)``; the series with the snapshot reports nothing.
    Structurally: ``build_series`` calls ``coverage_gap_reset`` exactly once,
    outside the per-dataset loop.
    """
    silent_first, silent_last = ago(29), ago(3)
    strap = readings(STRAP, between(ago(66), silent_first - timedelta(days=1)), 40.0, 6, "strap")
    strap += readings(STRAP, between(silent_last + timedelta(days=1), D), 40.0, 6, "back")
    snapshot = readings(SNAPSHOT, between(ago(66), D), 45.0, 7, "snap")

    bridged = hrv_trend.build_series(strap + snapshot, AUCKLAND, D)
    alone = hrv_trend.build_series(strap, AUCKLAND, D)
    slice_of(bridged, "bridged by the snapshot")
    slice_of(alone, "the strap alone")

    assert bridged.gap_reset_on is None
    assert {report(d) for d in bridged.datasets} == {(None, None)}
    assert dataset(bridged, STRAP).baseline_window == (ago(66), ago(7))
    assert dataset(bridged, STRAP).n == 37

    assert alone.gap_reset_on == silent_last + timedelta(days=1)
    assert report(dataset(alone, STRAP)) == (silent_last + timedelta(days=1), COVERAGE_GAP)
    # What a per-dataset gap would have answered on the bridged series.
    strap_only = hrv_trend._of_tier(bridged.readings, STRAP)
    assert hrv_trend.coverage_gap_reset(strap_only, ()) == silent_last + timedelta(days=1)
    assert hrv_trend.coverage_gap_reset(bridged.readings, ()) is None

    fn = _build_series_ast()
    calls = _calls_to(fn, "coverage_gap_reset")
    assert len(calls) == 1
    loop = next(
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "tier"
    )
    assert calls[0] not in list(ast.walk(loop)), "the gap is asked before the partition, not per dataset"
    assert calls[0].lineno < loop.lineno
    print(
        f"[slice compared] build_series: 1 coverage_gap_reset call at line {calls[0].lineno} < loop at {loop.lineno}"
    )


# ---------------------------------------------------------------------------
# deliverable 4: _era_boundary's three-term ordering key
# ---------------------------------------------------------------------------


def test_the_era_boundary_ordering_key_keeps_its_three_terms() -> None:
    """Two candidate boundaries within one ``(old, new)`` pair that tie on
    the first two terms -- both week-clear, one stray each -- are decided by
    the third: the later ``A_end`` wins, so the era begins ``07-07``. With
    the key cut to two terms ``max`` returns the first equal candidate and
    the era begins ``07-02`` (measured while this was written). The other
    two terms are pinned end to end by
    ``test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of``
    and ``stray_day_tie``'s pins in test_hrv_trend_reset.py; the source
    check below is what says the key was not rewritten when the pair began
    to be asked per dataset."""
    old = [parsed(date(2026, 7, 1), 7, SNAPSHOT), parsed(date(2026, 7, 6), 7, SNAPSHOT)]
    new = [parsed(date(2026, 7, 2), 6, STRAP)] + [
        parsed(day, 6, STRAP) for day in between(date(2026, 7, 7), ago(7))
    ]
    judged = hrv_trend.judged_window(D)

    boundary = hrv_trend._era_boundary(old, new, judged)
    print(
        f"[slice compared] candidates A_end=07-01 (1 stray: 07-06) and A_end=07-06 (1 stray: 07-02) -> {boundary}"
    )
    assert boundary == hrv_trend.EraBoundary(first_day=date(2026, 7, 7), reported=True)
    assert "key=lambda boundary: (boundary[0], -boundary[1], boundary[2])" in inspect.getsource(
        hrv_trend._era_boundary
    )


# ---------------------------------------------------------------------------
# the decision: the view presents the selected dataset's own report
# ---------------------------------------------------------------------------


def test_the_view_presents_the_selected_datasets_own_report_and_carries_the_others() -> None:
    """Three datasets, and the one with the reported reset is not the one
    selected. An overnight era sustains the previous window
    (``D-126..D-53``); a strap era ``D-52..D-36`` follows it cleanly and
    reports ``(D-52, tier_change)`` -- but it has no judged-week reading, so
    it is not judgeable; a daily snapshot ``D-66..D`` is, and is selected.
    The snapshot's own question is refused by clause (c) (its first
    fourteen mornings, ``D-66..D-53`` at 06:00, sit inside the overnight
    era, whose last capture is ``D-53`` at 07:00 -- 14 strays for every
    candidate; at 08:00 the ``D-53`` snapshot would be ``B_start`` and the
    strays 13, measured while this was written), so it reports
    ``(None, None)``, and that -- not the strap's reset -- is what
    ``selected_view`` presents beside the snapshot's band. The strap's
    report is carried in ``datasets`` (visible in the response with T159)
    and is never promoted to the view; the reported reset describes the
    band it clipped, and no other."""
    rows = readings(OVERNIGHT, between(ago(126), ago(53)), 50.0, 7, "night")
    rows += readings(STRAP, between(ago(52), ago(36)), 40.0, 5, "strap")
    rows += readings(SNAPSHOT, between(ago(66), D), 45.0, 6, "snap")

    series = hrv_trend.build_series(rows, AUCKLAND, D)
    view = hrv_trend.selected_view(series)
    slice_of(series, "three datasets")
    print(f"[slice compared] {view.selection.describe()} presented=({view.reset_on}, {view.reset_reason})")

    assert [d.tier for d in series.datasets] == [STRAP, SNAPSHOT, OVERNIGHT]
    assert report(dataset(series, STRAP)) == (ago(52), TIER_CHANGE)
    assert dataset(series, STRAP).established and not hrv_trend.is_judgeable(dataset(series, STRAP))
    assert report(dataset(series, SNAPSHOT)) == (None, None)
    assert report(dataset(series, OVERNIGHT)) == (None, None)

    assert view.tier == SNAPSHOT
    assert (view.reset_on, view.reset_reason) == (None, None)
    assert view.baseline_window == dataset(series, SNAPSHOT).baseline_window == (ago(66), ago(7))
    assert {report(d) for d in view.datasets} == {(ago(52), TIER_CHANGE), (None, None)}
