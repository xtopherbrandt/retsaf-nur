"""T137 -- ``metrics/hrv_trend.py``/``schemas.py``: ``unavailable_reason``.

Closes F005's Negative Class row "the verdict still cannot say *why* it is
unavailable", open since review cycle 4 and re-confirmed at six causes by
T128 (``test_hrv_unavailable_causes.py``). A single enum on the verdict --
``no_tier_sustains_a_trend``, ``no_band``, ``week_too_thin``,
``week_not_representative``, ``baseline_unestablished``, ``day_not_happened``
-- names which of the six fired; null whenever ``verdict`` is not
``hrv_unavailable``.

**The defect this pins is two concrete days, not the mechanism in general**
(the task's own framing): T132's clean, gapless, permanent device-switch
geometry reads ``hrv_unavailable`` on 2026-09-03 and 09-04 while every field
the pre-T137 response exposed -- ``established``, ``baseline.n``,
``readings_in_window``, ``reset_reason`` -- said the week was judgeable. The
point of the new field is that it **distinguishes** those two opaque days
from 09-05, where ``readings_in_window`` already told the story; a test that
only asserted "non-null" could not tell an enum from a bare boolean.

``judge``'s guards fire in a fixed order (its own docstring), and this
module's ``_unavailable_reason`` reads exactly the values ``judge`` computed,
so T128's independent AST oracle is the authority on the six-cause set: if
this module and that oracle ever disagree about the count or the gating, the
oracle wins (module docstring, ``hrv_trend.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"
OVERNIGHT = "health_api_overnight"

R = hrv_trend
NO_TIER = R.REASON_NO_TIER
NO_BAND = R.REASON_NO_BAND
WEEK_TOO_THIN = R.REASON_WEEK_TOO_THIN
WEEK_NOT_REPRESENTATIVE = R.REASON_WEEK_NOT_REPRESENTATIVE
BASELINE_UNESTABLISHED = R.REASON_BASELINE_UNESTABLISHED
DAY_NOT_HAPPENED = R.REASON_DAY_NOT_HAPPENED

# The target date the T156 section judges at: baseline [2026-07-04, 2026-09-01],
# judged week [2026-09-02, 2026-09-08] (F005's canonical example).
D_156 = date(2026, 9, 8)


# ---------------------------------------------------------------------------
# row builders (the same shapes as test_hrv_trend_reset.py; duplicated rather
# than imported across test modules -- contract-tables-need-an-independent-oracle)
# ---------------------------------------------------------------------------


def local(day: date, hh: int = 6, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str | None, value: float | None, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def readings(tier: str, days: list[date], value: float = 40.0, prefix: str | None = None) -> list[dict]:
    return [row(local(day, 6), tier, value, f"{prefix}-{day}" if prefix else None) for day in days]


def span(first: date, last: date) -> list[date]:
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def build(rows: list[dict], target: date) -> hrv_trend.SingleDatasetView:
    # F006's selection (T155), on the F005 shape.
    return hrv_trend.selected_view(hrv_trend.build_series(rows, AUCKLAND, target))


# ---------------------------------------------------------------------------
# the two opaque days
# ---------------------------------------------------------------------------


def test_unavailable_reason_separates_the_opaque_withhold_days_from_the_thin_window_day() -> None:
    """The task's own geometry, verbatim (Pacific/Auckland): daily
    ``chest_strap_raw`` at 40.0 ms from 2026-01-01 to 2026-08-31, daily
    ``health_snapshot`` at 40.0 ms from 2026-09-01 on -- no gap, no
    suppression, identical values on both tiers. The athlete bought a new
    watch.

    At D = 2026-09-03 and 09-04 ``chest_strap_raw`` resolves (it still covers
    its judged week with >= ``MIN_WINDOW_READINGS`` days), the baseline is
    established at 60, and the verdict is ``hrv_unavailable`` solely because
    ``health_snapshot`` -- a tier never used in the baseline window -- holds a
    full judged week entirely after every one of ``chest_strap_raw``'s own
    (T125/T132's ``verdict_withheld``; at dataset scope since T158 -- the
    snapshot is not judgeable, the strap is selected, and the strap's
    ``withheld`` is what the view carries). Nothing else in the pre-T137 response
    said why. At 09-05 the resolved tier's own window has fallen to 2 days,
    below ``MIN_WINDOW_READINGS`` -- already explicable by
    ``readings_in_window`` alone, and the new field must report a
    **different** reason there, or it is not distinguishing anything.
    """
    strap_days = span(date(2026, 1, 1), date(2026, 8, 31))
    snapshot_days = span(date(2026, 9, 1), date(2026, 9, 10))
    rows = readings(STRAP, strap_days, 40.0, "strap") + readings(SNAPSHOT, snapshot_days, 40.0, "snap")

    d03 = hrv_trend.judge(build(rows, date(2026, 9, 3)))
    d04 = hrv_trend.judge(build(rows, date(2026, 9, 4)))
    d05 = hrv_trend.judge(build(rows, date(2026, 9, 5)))

    for label, verdict, expected_window in (("09-03", d03, 4), ("09-04", d04, 3)):
        assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, label
        assert verdict.established is True, label
        assert verdict.baseline_n == 60, label
        assert verdict.readings_in_window == expected_window, label
        assert verdict.readings_in_window >= hrv_trend.MIN_WINDOW_READINGS, label
        # The opacity itself: every other exposed field says the week is
        # judgeable, and only the new field explains the silence.
        assert verdict.unavailable_reason == WEEK_NOT_REPRESENTATIVE, (label, verdict.unavailable_reason)

    assert d05.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert d05.readings_in_window == 2
    # Distinguished, not merely non-null: 09-05's reason is the ordinary
    # thin-window one, not the opaque withheld one 09-03/09-04 report.
    assert d05.unavailable_reason == WEEK_TOO_THIN, d05.unavailable_reason
    assert d05.unavailable_reason != WEEK_NOT_REPRESENTATIVE


def test_unavailable_reason_reports_the_withheld_week_before_an_unestablished_baseline() -> None:
    """Precedence, pinned directly: when a week is **both** withheld
    (T125/T132) **and** built on an unestablished baseline, ``judge``'s fixed
    order reports the withheld reason -- the ``and not series.withheld``
    guard is evaluated before the ``established`` one (module docstring,
    ``judge``). Perturbation: swapping that order (reporting
    ``baseline_unestablished`` ahead of ``week_not_representative``) reds
    this test, which is the point -- the precedence was previously unpinned.

    ``chest_strap_raw`` reads only 5 distinct days in the baseline window
    (below ``MIN_BASELINE_READINGS``, so ``established`` is false) and still
    covers 3 of the judged week's earliest days; ``health_snapshot`` --
    never read in the baseline window -- covers the week's 3 latest days, all
    strictly after the strap's. ``verdict_withheld`` fires (a tier with zero
    baseline-window days and >= ``MIN_WINDOW_READINGS`` judged-week days, all
    later than the resolved tier's own) on a baseline that is *also* thin.

    Re-pointed at T158 (F006, 2026-09-19): nothing here is judgeable, so the
    strap is the presentation fallback (AC9), and ``verdict_withheld`` at
    dataset scope is asked of it as if selected -- the snapshot is not
    judgeable and its three days are all later, so the fallback's
    ``withheld`` is ``True`` and the precedence pinned below is unchanged.
    """
    D = date(2026, 9, 20)
    _, baseline_last = hrv_trend.baseline_window(D)
    # Contiguous with the judged week (ending on baseline_last == D-7, the
    # window's own last day), so no coverage-gap reset clips this thin block
    # before it can be counted: a silence of more than GAP_RESET_DAYS anywhere
    # in the read history re-establishes the baseline and would wipe these 5
    # readings before ``established`` was ever asked about them.
    strap_baseline_days = [baseline_last - timedelta(days=i) for i in range(4, -1, -1)]
    strap_week_days = [D - timedelta(days=6), D - timedelta(days=5), D - timedelta(days=4)]
    snapshot_week_days = [D - timedelta(days=2), D - timedelta(days=1), D]

    rows = (
        readings(STRAP, strap_baseline_days, 40.0, "strap-base")
        + readings(STRAP, strap_week_days, 40.0, "strap-week")
        + readings(SNAPSHOT, snapshot_week_days, 40.0, "snap-week")
    )

    series = build(rows, D)
    assert series.tier == STRAP, "the thin strap is still the densest/only baseline-window tier"
    verdict = hrv_trend.judge(series)

    assert verdict.established is False, "baseline_n should be 5, below MIN_BASELINE_READINGS"
    assert verdict.baseline_n == 5
    assert verdict.readings_in_window == 3
    assert series.withheld is True, "the never-used snapshot should trip verdict_withheld here"
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == WEEK_NOT_REPRESENTATIVE, verdict.unavailable_reason
    assert verdict.unavailable_reason != BASELINE_UNESTABLISHED


def test_unavailable_reason_is_null_whenever_the_verdict_is_asserted() -> None:
    """The invariant the field promises: null iff the verdict is not
    ``hrv_unavailable``. A settled athlete inside the band (``hrv_normal``)
    and one below it (``hrv_suppressed``) both carry ``None``."""
    baseline_days = span(date(2026, 1, 1), date(2026, 8, 27))
    D = date(2026, 9, 3)

    normal_rows = readings(STRAP, baseline_days, 40.0, "n-base") + readings(
        STRAP, span(D - timedelta(days=6), D), 40.0, "n-week"
    )
    normal = hrv_trend.judge(build(normal_rows, D))
    assert normal.verdict == hrv_trend.VERDICT_NORMAL
    assert normal.unavailable_reason is None

    suppressed_rows = readings(STRAP, baseline_days, 40.0, "s-base") + readings(
        STRAP, span(D - timedelta(days=6), D), 20.0, "s-week"
    )
    suppressed = hrv_trend.judge(build(suppressed_rows, D))
    assert suppressed.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert suppressed.unavailable_reason is None


def test_the_verdict_refuses_to_be_built_with_the_two_fields_disagreeing() -> None:
    """T144. The invariant above, made **structural** rather than merely
    upheld by ``judge``.

    ``judge`` is the only construction site and ``main._withhold_future`` the
    only ``replace``, and both pass a reason exactly when the verdict is
    ``hrv_unavailable`` -- so nothing is wrong today. What is missing is the
    *catch*: a future site writing
    ``HrvVerdict(verdict=VERDICT_UNAVAILABLE, ...)`` without the kwarg used to
    construct cleanly and emit ``verdict: "hrv_unavailable",
    unavailable_reason: null``, a response ``schemas.py`` and
    ``contracts/openapi.yaml`` both describe as impossible ("null whenever it
    is not") and neither can refuse, because ``None`` is a legal value of the
    field in every other row.

    Both directions are asserted, because ``replace`` makes the second as
    reachable as the first: a ``replace(v, verdict=VERDICT_NORMAL)`` that
    forgets to clear the reason carries a stale explanation on an asserted
    verdict, which a default -- of any value -- could not have caught.

    The legal constructions are re-asserted here too, so that a guard which
    rejects *everything* is not mistaken for one that bites correctly.
    """
    import pytest

    legal_fields: dict = {
        "ln_rmssd_7d_mean": None,
        "below_by": None,
        "band": None,
        "baseline_n": 0,
        "established": False,
        "readings_in_window": 0,
    }

    # unavailable without a reason: the T144 hole.
    with pytest.raises(ValueError, match="unavailable_reason"):
        hrv_trend.HrvVerdict(verdict=hrv_trend.VERDICT_UNAVAILABLE, **legal_fields)

    # an asserted verdict carrying a reason: the converse.
    for asserted in (hrv_trend.VERDICT_NORMAL, hrv_trend.VERDICT_SUPPRESSED):
        with pytest.raises(ValueError, match="unavailable_reason"):
            hrv_trend.HrvVerdict(verdict=asserted, unavailable_reason=WEEK_TOO_THIN, **legal_fields)

    # and the two legal shapes still build.
    withheld = hrv_trend.HrvVerdict(
        verdict=hrv_trend.VERDICT_UNAVAILABLE, unavailable_reason=WEEK_TOO_THIN, **legal_fields
    )
    assert withheld.unavailable_reason == WEEK_TOO_THIN
    asserted_verdict = hrv_trend.HrvVerdict(verdict=hrv_trend.VERDICT_NORMAL, **legal_fields)
    assert asserted_verdict.unavailable_reason is None

    # ``replace`` re-runs the guard, so the route's own override is covered by
    # it: flipping the verdict without the reason is refused there too.
    from dataclasses import replace

    with pytest.raises(ValueError, match="unavailable_reason"):
        replace(withheld, verdict=hrv_trend.VERDICT_NORMAL)


def test_unavailable_reason_no_tier_when_the_store_holds_no_reading_at_all() -> None:
    """The structural cause (T128's ``resolve_baseline_tier`` answering "no
    tier at all"): an empty series resolves ``tier`` to ``None``, so ``band``
    is trivially ``None`` too, and the reason is the structural one rather
    than the generic ``no_band``."""
    series = build([], date(2026, 9, 3))
    assert series.tier is None
    verdict = hrv_trend.judge(series)
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == NO_TIER


def test_unavailable_reason_no_band_when_a_resolved_tier_has_fewer_than_two_baseline_readings() -> None:
    """A resolved (non-null) tier with a single baseline-window reading:
    ``band`` is ``None`` (``build_band`` needs at least two), but the tier
    itself is real -- the generic ``no_band`` cause, not the structural one."""
    D = date(2026, 9, 3)
    baseline_first, _ = hrv_trend.baseline_window(D)
    rows = readings(STRAP, [baseline_first], 40.0, "one")
    series = build(rows, D)
    assert series.tier == STRAP
    verdict = hrv_trend.judge(series)
    assert verdict.band is None
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == NO_BAND
    assert verdict.unavailable_reason != NO_TIER


def test_unavailable_reason_baseline_unestablished_alone() -> None:
    """T116's population, in isolation: a computable but unestablished band
    (2 <= n < 14), a full judged week on the same tier, not withheld."""
    D = date(2026, 9, 3)
    _, baseline_last = hrv_trend.baseline_window(D)
    # Contiguous with the judged week, for the same reason as the precedence
    # test above: a thin, isolated baseline block far from the week would be
    # clipped away by the coverage-gap reset before ``established`` is asked.
    baseline_days_ = [baseline_last - timedelta(days=i) for i in range(4, -1, -1)]
    week_days = span(D - timedelta(days=6), D)
    rows = readings(STRAP, baseline_days_, 40.0, "base") + readings(STRAP, week_days, 40.0, "week")

    series = build(rows, D)
    verdict = hrv_trend.judge(series)
    assert verdict.band is not None
    assert verdict.established is False
    assert series.withheld is False
    assert verdict.readings_in_window >= hrv_trend.MIN_WINDOW_READINGS
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == BASELINE_UNESTABLISHED


# ---------------------------------------------------------------------------
# the invariant, swept -- and the enum's closure across the three declarations
# ---------------------------------------------------------------------------


def test_unavailable_reason_is_set_exactly_when_the_verdict_is_withheld_across_the_switch_walk() -> None:
    """The biconditional, swept rather than sampled:
    ``verdict == hrv_unavailable`` **iff** ``unavailable_reason is not None``,
    on every local day of the task's device-switch geometry -- the run that
    crosses from asserted verdicts (09-02 and earlier) into the two opaque
    withheld days and on into the thin-window ones.

    Both directions are asserted on every day, and every reported reason must
    be a member of ``UNAVAILABLE_REASONS``: an implementation that set the
    field on *every* day, or left it ``None`` on a withheld one, or invented a
    seventh name, reds here. ``judge`` is called and ``.verdict`` read inside
    this loop's own body (T127/T134's shape), so losing the verdict assertion
    is itself visible.
    """
    strap_days = span(date(2026, 1, 1), date(2026, 8, 31))
    snapshot_days = span(date(2026, 9, 1), date(2026, 9, 12))
    rows = readings(STRAP, strap_days, 40.0, "strap") + readings(SNAPSHOT, snapshot_days, 40.0, "snap")

    seen_withheld = 0
    seen_asserted = 0
    for day in span(date(2026, 8, 20), date(2026, 9, 12)):
        verdict = hrv_trend.judge(build(rows, day))
        withheld = verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
        assert (verdict.unavailable_reason is not None) is withheld, (
            day,
            verdict.verdict,
            verdict.unavailable_reason,
        )
        if withheld:
            seen_withheld += 1
            assert verdict.unavailable_reason in R.UNAVAILABLE_REASONS, (day, verdict.unavailable_reason)
        else:
            seen_asserted += 1

    # The walk must actually cross the boundary, or the sweep above proves
    # nothing about either direction (a -k that deselects everything exits 0).
    assert seen_withheld > 0 and seen_asserted > 0, (seen_withheld, seen_asserted)


def test_the_six_unavailable_reason_names_are_the_same_six_in_the_module_the_schema_and_the_contract() -> (
    None
):
    """One closed enum, declared three times, and the three must agree name
    for name -- the module's ``UNAVAILABLE_REASONS``, the ``Literal`` on
    ``HrvTrendResponse.unavailable_reason``, and ``HrvTrend``'s ``enum`` in
    ``contracts/openapi.yaml``. A field added to the code without moving the
    published contract is exactly the drift this task's probe exists to
    refuse; this pins the *values*, which a structural drift check does not
    read.
    """
    import typing
    from pathlib import Path

    import yaml
    from runcoach_api.schemas import HrvTrendResponse

    module_names = list(R.UNAVAILABLE_REASONS)
    assert len(module_names) == 6, module_names
    assert len(set(module_names)) == 6, "the six names must be distinct"

    annotation = HrvTrendResponse.model_fields["unavailable_reason"].annotation
    literals: list[str] = []
    widening: list[object] = []
    for member in typing.get_args(annotation):
        if typing.get_origin(member) is typing.Literal:
            literals.extend(typing.get_args(member))
        elif member is not type(None):
            widening.append(member)
    # T144: the assertion below used to be ``assert literals`` alone, whose
    # message claimed closure it did not check -- ``str | Literal[...] | None``
    # still yields six ``literals`` and stayed green under exactly that
    # mutation. The union's *other* members are what closure is about, so they
    # are named here rather than in the message only.
    assert literals, f"unavailable_reason names no Literal member at all: {annotation!r}"
    assert not widening, (
        f"unavailable_reason is not a closed Literal enum -- the union also admits {widening!r}: {annotation!r}"
    )
    assert set(literals) == set(module_names), (sorted(literals), sorted(module_names))
    assert type(None) in typing.get_args(annotation), "the field must admit null"

    contract_path = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
    with contract_path.open(encoding="utf-8") as fh:
        contract = yaml.safe_load(fh)
    published = contract["components"]["schemas"]["HrvTrend"]["properties"]["unavailable_reason"]["enum"]
    assert set(published) == set(module_names), (sorted(published), sorted(module_names))
    assert published == module_names, "and in the module's own precedence order"


def test_the_schema_refuses_an_unavailable_reason_outside_the_six() -> None:
    """Closed, not merely documented: a seventh value is a validation error,
    so a future cause cannot reach a client unnamed by the contract.

    The partial payload below is missing other required fields on purpose --
    so a bare ``pytest.raises(ValidationError)`` would be green whatever the
    field's type is, which is the T134 failure mode. The assertion is
    therefore on the error's **location**: ``unavailable_reason`` must be
    among the rejected fields with the seventh value, and must **not** be
    among them when the value is one of the six.
    """
    import pytest
    from pydantic import ValidationError
    from runcoach_api.schemas import HrvTrendResponse

    def locations(reason: str) -> set[tuple]:
        payload = {
            "date": "2026-09-03",
            "from": "2026-09-03",
            "timezone": "Pacific/Auckland",
            "points": [],
            "verdict": "hrv_unavailable",
            "unavailable_reason": reason,
        }
        with pytest.raises(ValidationError) as caught:
            HrvTrendResponse.model_validate(payload)
        return {tuple(error["loc"]) for error in caught.value.errors()}

    assert ("unavailable_reason",) in locations("the_week_looked_odd")
    for legal in R.UNAVAILABLE_REASONS:
        assert ("unavailable_reason",) not in locations(legal), legal


# ---------------------------------------------------------------------------
# T156: the precedence across datasets, and the presentation fallback (F006
# AC9; ``research/00`` section 5.4 (iii) as amended 2026-09-19)
#
# With N datasets, different datasets satisfy different causes at once, and
# ``judge``'s single-series guard order says nothing about which one the
# response names. The precedence is two-level and lives in ``selected_view``:
#
#   1. **which dataset speaks** -- the selected dataset; else the presentation
#      fallback (F005's rule 3 over datasets: the established dataset read last
#      in the baseline window, ties by n then fidelity; else the densest by n;
#      else the densest in the judged week, ties to fidelity); else no dataset;
#   2. **that dataset's own guard order** in ``judge`` (no band, thin week,
#      unrepresentative week, unestablished baseline).
#
# The reason is therefore always a true statement about the dataset whose
# ``baseline`` / ``band`` / ``established`` the response carries -- never a
# cause satisfied by a dataset the response does not present -- and the
# structural ``no_tier_sustains_a_trend`` fires only when the series holds no
# dataset at all. The fallback confers no verdict and names no dissenter
# (IDEA-082, settled here). Every pin prints the slice it compared: the
# selection line, each dataset's own reason, and what was presented and why.
# ---------------------------------------------------------------------------


def _series(rows: list[dict], target: date) -> hrv_trend.HrvSeries:
    return hrv_trend.build_series(rows, AUCKLAND, target)


def _slice(series: hrv_trend.HrvSeries) -> str:
    """The slice the cross-dataset precedence compared: the selection, every
    dataset's **own** reason (``judge`` on the dataset itself), and the
    presented dataset, the clause that presented it and the reason reported."""
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    own = " ".join(
        f"{d.tier}:n{d.n}/est{int(d.established)}/week{len({r.date for r in d.window})}"
        f"/withheld{int(d.withheld)}->{hrv_trend.judge(d).unavailable_reason}"
        for d in series.datasets
    )
    line = (
        f"{selection.describe()} | own=[{own}] | presented={view.tier} by={view.presented_by} "
        f"selected_reason={selection.selected_reason} -> {verdict.verdict} {verdict.unavailable_reason} "
        f"n={verdict.baseline_n} est={verdict.established} week={verdict.readings_in_window}"
    )
    print(line)
    return line


def _daily(tier: str, first_offset: int, last_offset: int, value: float = 40.0, target: date = D_156) -> list[dict]:
    """``tier`` on every local day ``target - first_offset .. target - last_offset``
    (offsets counted back from the target, so ``66, 7`` is the whole baseline
    window). Snapshot captures at 07:00, strap at 06:00, so a morning with
    both feeds both datasets (AC3)."""
    days = span(target - timedelta(days=first_offset), target - timedelta(days=last_offset))
    prefix = f"{tier}-{first_offset}-{last_offset}-{value}"
    hh = 7 if tier == SNAPSHOT else 8 if tier == OVERNIGHT else 6
    return [row(local(day, hh), tier, value, f"{prefix}-{day}") for day in days]


def test_the_illness_week_presents_the_dataset_used_last_and_says_week_too_thin() -> None:
    """The task's first failing test (F006 AC9). A strap the athlete has
    read daily through the whole baseline window (``D-66..D-7``, n 60) and
    a snapshot he stopped on ``D-11`` (n 40), then an illness week: nothing
    captured in ``[D-6, D]`` on either. No dataset is judgeable, so
    ``selected_dataset`` is null -- and ``baseline.n`` is **still 60**, from
    the strap, the established dataset read last (``D-7`` against
    ``D-11``): the presentation fallback, F005's rule 3 retained. The reason
    is ``week_too_thin``, which is true of the strap the response carries,
    and **not** ``no_tier_sustains_a_trend``, which is what an
    implementation that nulls the whole block on a null selection reports
    (``series.tier is None``). Red against any such implementation; red too
    if the fallback presents the snapshot (``n`` 40) or confers a verdict.
    """
    series = _series(_daily(STRAP, 66, 7) + _daily(SNAPSHOT, 50, 11), D_156)
    slice_ = _slice(series)
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)

    assert selection.judgeable == () and selection.selected is None, slice_
    assert selection.selected_reason is None, slice_
    assert view.tier == STRAP and view.presented_by == hrv_trend.FALLBACK_ESTABLISHED_READ_LAST, slice_
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_
    assert verdict.unavailable_reason == WEEK_TOO_THIN, slice_
    assert verdict.unavailable_reason != NO_TIER, slice_
    assert verdict.baseline_n == 60 and verdict.established is True and verdict.band is not None, slice_
    assert verdict.readings_in_window == 0 and verdict.ln_rmssd_7d_mean is None, slice_
    assert {d.tier for d in view.datasets} == {STRAP, SNAPSHOT}, slice_


def test_probe_two_datasets_satisfying_different_causes_at_once_report_the_presented_ones() -> None:
    """Adversarial (deliverable 5, row 1): two causes at opposite ends of
    ``judge``'s chain, satisfied at the same time by different datasets.
    The strap (n 60, established) has no judged-week reading --
    ``week_too_thin``; a snapshot the athlete bought on ``D-11`` (n 5,
    unestablished) covers all seven week days -- its own reason is
    ``baseline_unestablished``, the guard nearest a verdict. Neither is
    judgeable. The precedence presents the established dataset read last
    (the strap, clause 1) and reports **its** reason, ``week_too_thin``,
    with ``baseline.n`` 60. Degenerate because a "nearest a verdict"
    precedence across datasets -- report the cause furthest along the
    chain -- would name ``baseline_unestablished`` while the response
    carried ``established: true`` on 60 readings: a reason about a dataset
    the reader cannot see. The snapshot's own reason is asserted so the
    contrast is measured, not assumed.
    """
    series = _series(_daily(STRAP, 66, 7) + _daily(SNAPSHOT, 11, 7) + _daily(SNAPSHOT, 6, 0), D_156)
    slice_ = _slice(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    (snapshot,) = [d for d in series.datasets if d.tier == SNAPSHOT]

    assert hrv_trend.select_dataset(series).selected is None, slice_
    assert snapshot.n == 5 and not snapshot.established and len({r.date for r in snapshot.window}) == 7, slice_
    assert hrv_trend.judge(snapshot).unavailable_reason == BASELINE_UNESTABLISHED, slice_
    assert view.tier == STRAP and view.presented_by == hrv_trend.FALLBACK_ESTABLISHED_READ_LAST, slice_
    assert verdict.unavailable_reason == WEEK_TOO_THIN, slice_
    assert verdict.baseline_n == 60 and verdict.established is True, slice_


def test_probe_every_dataset_satisfying_the_same_cause_reports_it_whichever_is_presented() -> None:
    """Adversarial (row 2): all N (three) datasets satisfy one cause. Strap
    and snapshot read daily through the window (n 60 each), the overnight
    tier on 34 days; two, one and zero week days respectively -- every one
    ``week_too_thin``. Clause 1 ties on read-last (all ``D-7``), then on n
    (60, 60), and falls to fidelity: the strap. Degenerate because the
    reason must be invariant to which dataset the tie-break lands on when
    the causes agree -- so the strap is removed and the reason is asserted
    again on the snapshot, and the reported reason equals every dataset's
    own on both series.
    """
    rows = _daily(STRAP, 66, 7) + _daily(STRAP, 6, 5) + _daily(SNAPSHOT, 66, 7) + _daily(SNAPSHOT, 6, 6)
    rows += _daily(OVERNIGHT, 40, 7)
    series = _series(rows, D_156)
    slice_ = _slice(series)
    view = hrv_trend.selected_view(series)

    assert [d.tier for d in series.datasets] == [STRAP, SNAPSHOT, OVERNIGHT], slice_
    assert all(hrv_trend.judge(d).unavailable_reason == WEEK_TOO_THIN for d in series.datasets), slice_
    assert view.tier == STRAP and view.presented_by == hrv_trend.FALLBACK_ESTABLISHED_READ_LAST, slice_
    assert hrv_trend.judge(view).unavailable_reason == WEEK_TOO_THIN, slice_

    without_strap = _series([r for r in rows if r["hrv_source_tier"] != STRAP], D_156)
    slice_2 = _slice(without_strap)
    view_2 = hrv_trend.selected_view(without_strap)
    assert view_2.tier == SNAPSHOT and hrv_trend.judge(view_2).unavailable_reason == WEEK_TOO_THIN, slice_2
    assert hrv_trend.judge(view_2).baseline_n == 60, slice_2


def test_probe_a_dataset_with_a_band_but_no_judged_week_readings_is_presented_over_one_with_no_band() -> None:
    """Adversarial (row 3): a dataset with a band but no judged-week
    reading, beside one with a week but no band. The snapshot holds five
    baseline days (a band from five, unestablished) and nothing in the
    week; the strap holds one baseline day (no band) and three week days.
    Nothing is established, so clause 2 presents the densest by n -- the
    snapshot -- and its reason is ``week_too_thin``: the band guard passes
    and the week guard is the first to fire. Degenerate because the
    strap's ``no_band`` sits earlier in the chain than the reported cause,
    so a precedence that took the *earliest* cause any dataset satisfies
    would report ``no_band`` beside a non-null ``band``. The band is
    asserted present on the verdict.
    """
    series = _series(_daily(SNAPSHOT, 11, 7) + _daily(STRAP, 7, 7) + _daily(STRAP, 2, 0), D_156)
    slice_ = _slice(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    (strap,) = [d for d in series.datasets if d.tier == STRAP]

    assert strap.n == 1 and strap.band is None and hrv_trend.judge(strap).unavailable_reason == NO_BAND, slice_
    assert view.tier == SNAPSHOT and view.presented_by == hrv_trend.FALLBACK_DENSEST_BASELINE, slice_
    assert verdict.band is not None and verdict.baseline_n == 5 and verdict.established is False, slice_
    assert verdict.readings_in_window == 0, slice_
    assert verdict.unavailable_reason == WEEK_TOO_THIN, slice_


def test_the_fallback_confers_no_verdict_and_names_no_dissenter_even_when_its_own_week_reads_below() -> None:
    """Adversarial (row 4) and IDEA-082's second reading, pinned: the
    illness week where the fallback fires **and** the fallback dataset's
    own two week mornings read far below its band (15 ms against a 40 ms
    band), while a snapshot's one week morning reads within. Nothing is
    judgeable; the strap is presented (tie on ``D-7`` and n, then
    fidelity). The verdict is ``hrv_unavailable`` / ``week_too_thin`` with
    ``below_by`` None -- two mornings are not a trend, however bad -- and
    ``disagreed_with`` is **empty** although ``band_readings`` shows the
    strap below and the snapshot within: a disagreement is with a verdict
    (AC11, "with the selected one"), and the fallback confers none (AC9).
    Degenerate because this is the one geometry where the two readings of
    IDEA-082 (2) differ: treating the presented dataset as selected would
    name the snapshot here, and a consumer would infer a suppression the
    response refused to assert. Perturbation: pass the fallback dataset to
    ``disagreed_with`` as if selected -> ``('health_snapshot',)``, red.
    """
    rows = _daily(STRAP, 66, 7) + _daily(STRAP, 6, 5, 15.0) + _daily(SNAPSHOT, 66, 7) + _daily(SNAPSHOT, 6, 6)
    series = _series(rows, D_156)
    slice_ = _slice(series)
    selection = hrv_trend.select_dataset(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)
    sides = {r.tier: r.below for r in selection.band_readings}

    assert selection.selected is None and view.tier == STRAP, slice_
    assert sides == {STRAP: True, SNAPSHOT: False}, slice_
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE and verdict.unavailable_reason == WEEK_TOO_THIN, slice_
    assert verdict.below_by is None and verdict.ln_rmssd_7d_mean is not None, slice_
    assert selection.disagreed_with == (), slice_
    # The perturbation, measured in-process rather than described: the
    # fallback dataset handed to the predicate as if it were selected.
    assert hrv_trend.disagreed_with(view.selected, selection.band_readings) == (SNAPSHOT,), slice_


@pytest.mark.parametrize(
    ("rows", "expected_tier", "expected_by", "expected_reason"),
    [
        (
            _daily(STRAP, 20, 20) + _daily(SNAPSHOT, 2, 0),
            STRAP,
            "FALLBACK_DENSEST_BASELINE",
            NO_BAND,
        ),
        (
            _daily(SNAPSHOT, 2, 0),
            SNAPSHOT,
            "FALLBACK_DENSEST_WEEK",
            NO_BAND,
        ),
        ([], None, None, NO_TIER),
    ],
    ids=["one_baseline_reading_in_the_window", "no_baseline_reading_at_all", "no_dataset_at_all"],
)
def test_probe_the_fallback_dataset_itself_has_no_band(
    rows: list[dict], expected_tier: str | None, expected_by: str | None, expected_reason: str
) -> None:
    """Adversarial (row 5): the week where the fallback dataset itself has
    no band, and the boundary with the structural cause. One strap
    reading on ``D-20`` beside a three-morning snapshot week: nothing is
    established, the strap is the densest by n (1 against 0, clause 2),
    its band is ``None`` and the reason is ``no_band`` -- on a real tier.
    No baseline reading of any tier: clause 3 presents the densest in the
    week, the snapshot, ``no_band`` again. Only a series with **no
    dataset** reports ``no_tier_sustains_a_trend``. Degenerate because
    ``band is None`` is one guard in ``judge`` and two causes in the enum,
    told apart by ``tier is None`` alone: a fallback that gave up on an
    unestablished series would collapse the first two rows into the third
    and report "no reading of any tier" to an athlete who has some.
    """
    series = _series(rows, D_156)
    slice_ = _slice(series)
    view = hrv_trend.selected_view(series)
    verdict = hrv_trend.judge(view)

    assert hrv_trend.select_dataset(series).selected is None, slice_
    assert view.tier == expected_tier, slice_
    assert view.presented_by == (None if expected_by is None else getattr(hrv_trend, expected_by)), slice_
    assert verdict.band is None and verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, slice_
    assert verdict.unavailable_reason == expected_reason, slice_
    assert (view.tier is None) is (expected_reason == NO_TIER), slice_


def test_the_reported_reason_is_always_the_presented_datasets_own() -> None:
    """The invariant the precedence rests on, swept over every geometry
    above and the T158 shapes: ``judge`` of the view **is** ``judge`` of the
    dataset it presents -- the same ``HrvVerdict``, field for field -- so
    the reason can only ever be a statement about the dataset whose
    baseline the response carries; ``presented_by`` is ``selected`` exactly
    when the selection is non-null; and the structural cause fires exactly
    when the series holds no dataset. A precedence that consulted the
    other datasets' causes, or a view that nulled the block on a null
    selection, reds one of the three on some row.
    """
    geometries = {
        "illness week": _daily(STRAP, 66, 7) + _daily(SNAPSHOT, 50, 11),
        "different causes": _daily(STRAP, 66, 7) + _daily(SNAPSHOT, 11, 7) + _daily(SNAPSHOT, 6, 0),
        "band, no week": _daily(SNAPSHOT, 11, 7) + _daily(STRAP, 7, 7) + _daily(STRAP, 2, 0),
        "one baseline reading": _daily(STRAP, 20, 20) + _daily(SNAPSHOT, 2, 0),
        "no baseline reading": _daily(SNAPSHOT, 2, 0),
        "nothing": [],
        "selected, dissent": _daily(STRAP, 66, 0) + _daily(SNAPSHOT, 66, 7) + _daily(SNAPSHOT, 6, 0, 15.0),
        "selected, withheld": _daily(STRAP, 66, 3) + _daily(SNAPSHOT, 2, 0, 15.0),
        "selected after a skip": _daily(STRAP, 66, 36) + _daily(STRAP, 4, 4) + _daily(STRAP, 2, 2)
        + _daily(STRAP, 0, 0) + _daily(SNAPSHOT, 66, 0),
    }
    seen_selected = seen_fallback = 0
    for name, rows in geometries.items():
        series = _series(rows, D_156)
        slice_ = f"{name}: {_slice(series)}"
        selection = hrv_trend.select_dataset(series)
        view = hrv_trend.selected_view(series)
        verdict = hrv_trend.judge(view)
        assert (view.presented_by == hrv_trend.PRESENTED_SELECTED) is (selection.selected is not None), slice_
        assert (selection.selected_reason is not None) is (selection.selected is not None), slice_
        if view.selected is None:
            assert series.datasets == () and view.tier is None, slice_
            assert verdict.unavailable_reason == NO_TIER, slice_
            continue
        assert verdict == hrv_trend.judge(view.selected), slice_
        assert verdict.unavailable_reason != NO_TIER, slice_
        assert view.presented_by in hrv_trend.PRESENTATIONS, slice_
        seen_selected += selection.selected is not None
        seen_fallback += selection.selected is None
    assert seen_selected >= 3 and seen_fallback >= 5, (seen_selected, seen_fallback)


def test_the_promoted_verdict_is_the_selected_datasets_unchanged_whatever_the_others_read() -> None:
    """Deliverable 4 (AC11), with IDEA-082's first reading pinned. A strap
    read daily through the window and the week (selected: the highest
    fidelity judgeable dataset, nothing skipped) beside a snapshot read
    daily through the window whose week reads 15 ms. Strap within, snapshot
    below: ``hrv_normal``, reason null, the snapshot named. Strap below,
    snapshot within: ``hrv_suppressed``, the snapshot named -- the other
    direction (AC11). Both below: ``hrv_suppressed`` and **nobody named**
    -- the snapshot agrees, and a field called ``disagreed_with`` that
    listed it would be false (IDEA-082 (1): AC10's "reads below" is read
    as "reads the other side of its band from the selected dataset", the
    only reading under which AC11's "either direction" means anything).
    On every row the promoted verdict is ``judge`` of the strap's own
    dataset, field for field, so the others changed nothing about it.
    """
    for strap_week, snapshot_week, expected, named in (
        (40.0, 15.0, hrv_trend.VERDICT_NORMAL, (SNAPSHOT,)),
        (15.0, 40.0, hrv_trend.VERDICT_SUPPRESSED, (SNAPSHOT,)),
        (15.0, 15.0, hrv_trend.VERDICT_SUPPRESSED, ()),
    ):
        rows = _daily(STRAP, 66, 7) + _daily(STRAP, 6, 0, strap_week)
        rows += _daily(SNAPSHOT, 66, 7) + _daily(SNAPSHOT, 6, 0, snapshot_week)
        series = _series(rows, D_156)
        slice_ = _slice(series)
        selection = hrv_trend.select_dataset(series)
        view = hrv_trend.selected_view(series)
        verdict = hrv_trend.judge(view)

        assert selection.selected is not None and selection.selected.tier == STRAP, slice_
        assert selection.selected_reason == hrv_trend.SELECTED_HIGHEST_FIDELITY, slice_
        assert view.presented_by == hrv_trend.PRESENTED_SELECTED, slice_
        assert verdict.verdict == expected and verdict.unavailable_reason is None, slice_
        assert selection.disagreed_with == named, slice_
        assert verdict == hrv_trend.judge(selection.selected), slice_


def test_selected_reason_is_a_closed_enum_null_exactly_with_a_null_selection() -> None:
    """AC13, exposed for T159 on ``Selection`` (the contract renders it
    there). Two members: ``highest_fidelity_judgeable`` when no judgeable
    dataset outranks the selected one, and ``higher_fidelity_skipped_stale``
    when one did and the recency gate skipped it -- reference section 9's
    series: a strap established ``D-66..D-36``, back on ``D-4/D-2/D``,
    beside a daily snapshot; the strap is judgeable, 29 days behind, and
    skipped, so the snapshot is selected for the second reason. Null with
    null on the illness week. The tuple is closed at two.
    """
    plain = _series(_daily(STRAP, 66, 0) + _daily(SNAPSHOT, 66, 0), D_156)
    stale = _series(
        _daily(STRAP, 66, 36) + _daily(STRAP, 4, 4) + _daily(STRAP, 2, 2) + _daily(STRAP, 0, 0)
        + _daily(SNAPSHOT, 66, 0),
        D_156,
    )
    illness = _series(_daily(STRAP, 66, 7), D_156)
    slices = [_slice(s) for s in (plain, stale, illness)]

    assert hrv_trend.SELECTED_REASONS == (
        hrv_trend.SELECTED_HIGHEST_FIDELITY,
        hrv_trend.SELECTED_HIGHER_FIDELITY_STALE,
    ), slices
    plain_selection = hrv_trend.select_dataset(plain)
    assert plain_selection.selected is not None and plain_selection.selected.tier == STRAP, slices[0]
    assert plain_selection.selected_reason == "highest_fidelity_judgeable", slices[0]

    stale_selection = hrv_trend.select_dataset(stale)
    assert stale_selection.skipped == (STRAP,) and stale_selection.gap(STRAP) == 29, slices[1]
    assert stale_selection.selected is not None and stale_selection.selected.tier == SNAPSHOT, slices[1]
    assert stale_selection.selected_reason == "higher_fidelity_skipped_stale", slices[1]

    illness_selection = hrv_trend.select_dataset(illness)
    assert illness_selection.selected is None and illness_selection.selected_reason is None, slices[2]
    for selection in (plain_selection, stale_selection):
        assert selection.selected_reason in hrv_trend.SELECTED_REASONS
