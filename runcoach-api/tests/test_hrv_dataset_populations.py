"""T150 -- shipped F005's verdict on the two populations no suite pinned (F006 AC20).

[[T130]] measured nine return predicates over 12,300 rows and closed with two populations
that *only its throwaway harness* could see: ``switch_away_rows`` (the athlete who **left** a
tier) and ``inter_rows`` (a device return and an abandoned trial at *matched* era lengths).
Both were "green" on every candidate only in the sense that no test contained them. This
module ports the two generators out of ``spec/references/T130-overlap-sweep-harness.py``
(``inter_rows`` at its line 775, ``switch_away_rows`` at 884) into
``runcoach-api/tests/support/hrv_populations.py``, parameters preserved, and pins what the
**shipped** F005 rule at ``42f7705`` says on them today.

**These are pins of the current fact, not of the desired one.** F006 AC21 compares its
selection form against shipped F005 on exactly these populations; a baseline that is not
recorded before the candidate exists is not a baseline. Where shipped is measurably wrong --
one carrier morning disarms the return withhold (``T130``: 54/72/90 forbidden flips at
``c`` = 1/2/3+), era 10 never becomes a candidate -- the pin says so in its name and records
the wrong verdict as the thing F006 must improve on, not silently.

**Why ``3/2/1`` is here explicitly.** The suite's one switch-away fixture,
``test_hrv_trend_reset.trial_then_switch``, places its stray old-tier captures at
``base-4/-3/-2`` -- the single placement where the judged-week medians tie. T130's own
written deliverable (era >= 15 conjoined with the median order) scored 0 red of 408 *because*
of that placement and false-withholds two days later at ``3/2/1``. Shipped F005 does not:
its order clause asks whether the struck tier's earliest week day is later than the resolved
tier's latest, and a daily strap through ``base`` makes that false at every placement. That
is the fact the first test pins.

**Axes.** Every test below states the axes it holds constant, per
``a-sweep-must-name-the-axes-it-holds-constant``. Common to all of them, inherited from the
harness and stated once: **capture density is daily on every era** (the old tier's era, the
carrier, the new tier), the judged target is a single fixed day (``SWITCH_BASE`` =
2026-09-07 for the switch-away rows, ``D`` = 2026-09-12 for the matched pairs), the zone is
``Pacific/Auckland``, and the value pattern is the harness's (alternating 38/44 ms on the
carrier and the old snapshot era; 79 ms on the strap era and 25 ms on its week for
``inter_rows``; 40 ms then 33 ms on the strap for ``switch_away_rows``). Sub-daily density
on either dataset is *not* varied here -- [[T161]] owns that cross-product.

**N = 2, stated for the DB-backed test.** ``hrv_classification.py`` writes only
``chest_strap_raw`` and ``health_snapshot``; ``health_api_overnight`` is reachable only by
hand-built row dicts, so the one test that persists through the real classifier and reads
back through ``GET /metrics/hrv`` is, and can only be, a two-tier test.

Kept out of the five HRV suites (test_hrv_trend_band.py/_endpoint.py/_points.py/_reset.py/
_series.py) so ``SCOPED_SUITE_COLLECTED`` does not move: ``SCOPED_HRV_SUITES`` is an explicit
path list, so a new file is free where a new test inside one of the five is not.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.config import AppConfig
from runcoach_api.main import app
from runcoach_api.metrics import hrv_trend
from runcoach_api.models import RRInterval

_REPO_ROOT = Path(__file__).resolve().parents[2]
_POPULATIONS_PATH = _REPO_ROOT / "runcoach-api" / "tests" / "support" / "hrv_populations.py"

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"
PROFILE = "HRV Snapshot"
SNAPSHOT_SPORT = 60


def _load_populations() -> ModuleType:
    """Loads the ported generators by file path, not by adding ``tests/support`` to
    ``sys.path`` (the ``test_walk_verdict_checker`` pattern): the workspace runs pytest
    with ``--import-mode=importlib``, under which nothing in ``tests/`` is importable by
    name, and a path load leaves no import-path side effect for later files."""
    spec = importlib.util.spec_from_file_location("hrv_populations", _POPULATIONS_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_POPULATIONS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pop = _load_populations()

# T130's own name for the suite's fixture placement, and the placement two days later that
# its written deliverable false-withheld on. Both are spelled here, not read from the
# support module, so the pin cannot drift with the table it pins.
SUITE_STRAYS = (4, 3, 2)
SHIFTED_STRAYS = (3, 2, 1)
TRIAL_THEN_SWITCH_ERA = 87  # ``trial_then_switch``'s snapshot era: base-126 .. base-40


def _slice(series: hrv_trend.SingleDatasetView, verdict: hrv_trend.HrvVerdict) -> str:
    """The rows a verdict was judged on, spelled out -- a witness prints the slice it
    compared (``a-witness-must-print-the-slice-it-compared``)."""
    week = ", ".join(f"{r.date}:{r.tier[:5]}:{r.rmssd_ms:g}" for r in series.window)
    return (
        f"tier={series.tier} withheld={series.withheld} verdict={verdict.verdict} "
        f"reason={verdict.unavailable_reason} baseline_n={verdict.baseline_n} "
        f"window={series.baseline_window[0]}..{series.baseline_window[1]} "
        f"reset={series.reset_reason}@{series.reset_on} week=[{week}]"
    )


def _judged(rows: list[dict], target: date) -> tuple[hrv_trend.SingleDatasetView, hrv_trend.HrvVerdict]:
    # F006's selection (T155), on the F005 shape; the pins record shipped F005's verdicts.
    series = hrv_trend.selected_view(hrv_trend.build_series(rows, pop.AUCK, target))
    return series, hrv_trend.judge(series)


# ---------------------------------------------------------------------------
# switch_away_rows -- the athlete who left the tier
# ---------------------------------------------------------------------------


def test_the_switch_away_row_at_3_2_1_is_not_withheld_on_shipped_f005() -> None:
    """The first failing test (T150): ``switch_away_rows`` with the strays moved two days
    later than the suite's fixture, at ``trial_then_switch``'s own era length.

    Shipped F005's verdict, pinned as the current fact: **not withheld**, the strap owns
    the week, and the suppressed week reads ``hrv_suppressed`` -- every judged-week reading
    is one of the strap's 33 ms mornings, and none of the three stray snapshot captures
    reaches the mean. Where a candidate form withholds here it is a false withhold on a
    week the athlete's current daily device covers outright (T130's median conjunction
    does, and its suite-greenness was a property of the ``4/3/2`` placement alone).

    Held constant: era length 87 (``trial_then_switch``'s), ``new_from`` 39, the three
    strays' count (3) and their hour (08:00), daily density on both tiers, target
    ``SWITCH_BASE``. Varied against the suite: only the strays' offsets, ``4/3/2`` ->
    ``3/2/1``."""
    rows = pop.switch_away_rows(TRIAL_THEN_SWITCH_ERA, SHIFTED_STRAYS)
    series, verdict = _judged(rows, pop.SWITCH_BASE)
    slice_ = _slice(series, verdict)
    print(f"switch_away era={TRIAL_THEN_SWITCH_ERA} strays={SHIFTED_STRAYS}: {slice_}")

    assert series.withheld is False, slice_
    assert series.tier == STRAP, slice_
    assert verdict.verdict == hrv_trend.VERDICT_SUPPRESSED, slice_
    assert verdict.unavailable_reason is None, slice_
    # The rows judged: the strap's seven suppressed mornings, and no stray.
    assert [r.tier for r in series.window] == [STRAP] * 7, slice_
    assert {r.rmssd_ms for r in series.window} == {33.0}, slice_
    stray_ids = {f"S-{pop.SWITCH_BASE - timedelta(days=n)}" for n in SHIFTED_STRAYS}
    assert stray_ids.isdisjoint({r.session_id for r in series.window}), slice_


@pytest.mark.parametrize("era_len", pop.SWITCH_LENS)
@pytest.mark.parametrize("strays", pop.SWITCH_STRAYS, ids=lambda s: "/".join(map(str, s)))
def test_shipped_f005_withholds_on_no_switch_away_row(era_len: int, strays: tuple[int, ...]) -> None:
    """T130's full switch-away table on shipped F005: 8 era lengths x 7 stray placements,
    the 56 rows the measurement reported as ``.`` on every one. Each is an athlete who
    left ``health_snapshot`` for a daily strap; a withhold anywhere is a false withhold.

    Pinned per row: not withheld, the strap resolves the tier, and the verdict is the
    strap's own -- ``hrv_suppressed``, since its week is 33 ms against a 40 ms era.

    Held constant per row: ``new_from`` 39, daily density on both tiers, the strays'
    hour, target ``SWITCH_BASE``. Varied: the old era's length (14..87) and where in the
    judged week its strays fall, including the ``2/1/0`` placement whose last capture is
    on the target day itself and the four-stray ``2/1/0/3`` placement."""
    rows = pop.switch_away_rows(era_len, strays)
    series, verdict = _judged(rows, pop.SWITCH_BASE)
    slice_ = _slice(series, verdict)
    print(f"switch_away era={era_len} strays={strays}: {slice_}")

    assert series.withheld is False, slice_
    assert series.tier == STRAP, slice_
    assert verdict.verdict == hrv_trend.VERDICT_SUPPRESSED, slice_
    assert all(r.tier == STRAP for r in series.window), slice_


def test_the_stray_placement_does_not_move_the_switch_away_verdict_on_shipped_f005() -> None:
    """The pin T130 said was missing, stated as a relation: the suite's ``4/3/2`` row and
    the ``3/2/1`` row two days later read identically on shipped F005 -- same tier, same
    ``withheld``, same verdict, same band, same baseline window -- at every era length.
    A form whose answer depends on where three off-tier captures sit inside a week the
    current device covers daily is asking the week's shape a question it cannot answer
    (T130's structural finding), and this is the test that goes red when one does.

    Held constant: everything but the strays' offsets. Varied: era length over
    ``SWITCH_LENS``; the two placements compared."""
    for era_len in pop.SWITCH_LENS:
        suite = _judged(pop.switch_away_rows(era_len, SUITE_STRAYS), pop.SWITCH_BASE)
        shifted = _judged(pop.switch_away_rows(era_len, SHIFTED_STRAYS), pop.SWITCH_BASE)
        a, b = _slice(*suite), _slice(*shifted)
        print(f"era={era_len} 4/3/2 -> {a}\n         3/2/1 -> {b}")
        assert (suite[0].tier, suite[0].withheld, suite[1].verdict) == (
            shifted[0].tier,
            shifted[0].withheld,
            shifted[1].verdict,
        ), f"era {era_len}: {a} != {b}"
        assert suite[0].baseline_window == shifted[0].baseline_window, f"era {era_len}: {a} != {b}"
        assert suite[1].band == shifted[1].band, f"era {era_len}: {a} != {b}"


# ---------------------------------------------------------------------------
# inter_rows -- a return and an abandoned trial at matched era lengths
# ---------------------------------------------------------------------------


def _confusion(c: int) -> tuple[dict[str, int], list[str]]:
    """T130's ``matched`` table for one overlap ``c`` on shipped F005: ``TP`` resumed and
    withheld, ``FN`` resumed and not, ``FP`` abandoned and withheld, ``TN`` abandoned and
    not -- over the 17 era lengths of ``INTER_LENS``."""
    cells = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
    detail = []
    for era_len in pop.INTER_LENS:
        resumed, _ = _judged(pop.inter_rows(era_len, "resumed", c), pop.D)
        abandoned, _ = _judged(pop.inter_rows(era_len, "abandoned", c), pop.D)
        cells["TP" if resumed.withheld else "FN"] += 1
        cells["FP" if abandoned.withheld else "TN"] += 1
        detail.append(f"{era_len}:{'W' if resumed.withheld else '.'}{'W' if abandoned.withheld else '.'}")
    return cells, detail


@pytest.mark.parametrize("c", [0, 1, 2, 3, 4, 5])
def test_shipped_f005s_confusion_table_over_matched_era_lengths(c: int) -> None:
    """T130's matched-pair confusion table, as a pin. At ``c`` = 0 shipped F005 withheld
    on 16 of 17 returns and on 0 of 17 abandoned trials; the one miss was era 10, which
    ``MIN_BASELINE_READINGS`` (14) never made a candidate, so the order clause was never
    asked about it -- the same defect class T132 closed for a *zero*-day tier, left open
    for a 10-day one. At every ``c`` >= 1 the order clause is disarmed outright: **0 of
    17** returns withheld, because one carrier morning inside the return makes "every
    return day later than every carrier day" false. ``FP`` is 0 at every ``c``: shipped
    never false-withholds on the abandoned trial.

    **Re-pointed at T158 (F006 AC24, 2026-09-19): the era-10 miss closes.** Shipped
    F005 recorded ``TP 16 FN 1`` at ``c`` = 0 with ``detail[0] == "10:.."``. The withhold
    at dataset scope asks the order clause of every dataset that is **not judgeable**
    (or skipped), not only of a struck candidate or a zero-day tier, and a 10-day strap
    era is unestablished and so not judgeable -- the athlete's three later strap
    mornings withhold the carrier's stale week exactly as they do at era 15..90. That
    is ``hrv_normal -> hrv_unavailable`` on one row, the direction §1.7 tolerates
    freely; ``TP 17 FN 0``, ``detail[0] == "10:W."``. Nothing else in the table moves:
    ``c`` >= 1 stays ``0 of 17`` (the T130 disarm, pinned below and in
    ``test_hrv_trend_series.py``), and ``FP`` stays 0 at every ``c``.

    Held constant: the era's last day (``D-43``), the strap's week-day count (3), the
    carrier's density (daily), the strap's values (79 ms era, 25 ms week), target ``D``.
    Varied: era length over ``INTER_LENS`` (10..90 by 5) and the mode; ``c`` is the
    parametrised axis, and it is **carrier days elapsed inside the return, at daily
    density**, so days and captures coincide here."""
    cells, detail = _confusion(c)
    line = f"c={c}  TP {cells['TP']} FN {cells['FN']} | FP {cells['FP']} TN {cells['TN']}   {' '.join(detail)}"
    print(line)

    # Shipped F005 at c == 0: {"TP": 16, "FN": 1, "FP": 0, "TN": 17}, detail[0] == "10:..".
    expected = {"TP": 17, "FN": 0, "FP": 0, "TN": 17} if c == 0 else {"TP": 0, "FN": 17, "FP": 0, "TN": 17}
    assert cells == expected, line
    if c == 0:
        assert detail[0] == "10:W.", line  # shipped's single FN, era 10, withholds at dataset scope


def test_one_carrier_morning_inside_the_return_disarms_the_withhold_on_shipped_f005() -> None:
    """The residual T130 priced and F005 ships open, at one era length, both sides shown.
    ``c`` = 0: the carrier stops on ``D-3``, the strap holds ``D-2, D-1, D``, every strap
    day later than every carrier day -- withheld, ``hrv_unavailable``,
    ``week_not_representative``. ``c`` = 1: the carrier also captures ``D-2`` -- the order
    clause is false, nothing is withheld, and the week is judged on the carrier's own
    38/44 ms mornings against the carrier's own band: ``hrv_normal`` on a week whose
    strap mornings read 25 ms. That is the forbidden flip (``research/00`` §1.7), and it
    is the current fact F006 AC21 measures against.

    Held constant: era length 30, everything ``inter_rows`` fixes. Varied: ``c``, 0 -> 1."""
    stopped, stopped_v = _judged(pop.inter_rows(30, "resumed", 0), pop.D)
    overlap, overlap_v = _judged(pop.inter_rows(30, "resumed", 1), pop.D)
    a, b = _slice(stopped, stopped_v), _slice(overlap, overlap_v)
    print(f"c=0 -> {a}\nc=1 -> {b}")

    assert stopped.withheld is True, a
    assert stopped_v.verdict == hrv_trend.VERDICT_UNAVAILABLE, a
    assert stopped_v.unavailable_reason == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, a
    assert stopped.tier == SNAPSHOT, a

    assert overlap.withheld is False, b
    assert overlap_v.verdict == hrv_trend.VERDICT_NORMAL, b
    assert overlap.tier == SNAPSHOT, b
    # The week the verdict was computed from: the carrier's, not the athlete's.
    assert all(r.tier == SNAPSHOT for r in overlap.window), b
    assert pop.D - timedelta(days=2) in {r.date for r in overlap.window}, b
    assert stopped.baseline_window == overlap.baseline_window, f"{a} vs {b}"


def test_the_abandoned_trial_is_judged_on_the_carrier_and_never_withheld_on_shipped_f005() -> None:
    """The ``FP`` = 0 column at one era length, with its rows: the abandoned-trial mode
    keeps the carrier daily through ``D`` and puts the strap on ``D-6, D-4, D-2`` (the
    July trial's shape). Shipped judges the carrier's week -- ``hrv_normal`` -- and the
    three 25 ms strap days are the strap's own dataset, in no excluded list (shipped
    F005 excluded them ``off_baseline_tier``; T152), at ``c`` = 0 and at ``c`` = 5 alike (``c`` does not reach the abandoned mode's carrier, which already runs
    to ``D``).

    Held constant: era length 30, mode ``abandoned``. Varied: ``c`` at its two ends."""
    for c in (0, 5):
        series, verdict = _judged(pop.inter_rows(30, "abandoned", c), pop.D)
        slice_ = _slice(series, verdict)
        print(f"abandoned c={c} -> {slice_}")
        assert series.withheld is False, slice_
        assert series.tier == SNAPSHOT, slice_
        assert verdict.verdict == hrv_trend.VERDICT_NORMAL, slice_
        assert all(r.tier == SNAPSHOT for r in series.window), slice_
        assert not any(e.session_id.startswith("W-") for e in series.excluded), slice_
        (strap,) = [d for d in series.datasets if d.tier == STRAP]
        strap_week = {r.session_id for r in strap.window if r.session_id.startswith("W-")}
        assert len(strap_week) == 3, slice_


# ---------------------------------------------------------------------------
# the same switch-away row, through the real classifier and the route (N = 2)
# ---------------------------------------------------------------------------


def _beats(rmssd_ms: float) -> list[RRInterval]:
    """An alternating chest-strap beat series whose rMSSD is exactly ``rmssd_ms``:
    every successive difference is ``rmssd_ms`` in magnitude, so the root mean square
    of them is ``rmssd_ms`` (the ``_seed_beats`` construction in ``conftest.py``)."""
    values = (900.0, 900.0 + rmssd_ms)
    return [
        RRInterval(seq=i, rr_ms=values[i % 2], rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(160)
    ]


def _persist_rows(rows: Iterable[dict], synthetic, classified, persist_sessions) -> list[str]:
    """Persist harness row dicts through ``mapping.to_canonical`` ->
    ``hrv_classification.classify`` -> ``db.persist`` -- never raw SQL -- and return the
    session ids the store now holds. A snapshot row becomes a Health Snapshot capture
    carrying its ``rmssd_hrv``; a strap row becomes a declared capture whose reading is
    computed from beats. Only these two tiers exist on this path: N = 2."""
    sessions = []
    beats_by_id = {}
    for r in rows:
        when = datetime.fromisoformat(r["start_time"])
        value = r["resting_rmssd_ms"]
        if r["hrv_source_tier"] == SNAPSHOT:
            session = classified(synthetic(SNAPSHOT_SPORT, rmssd_hrv=value, start_time=when))
            assert session.hrv_source_tier == SNAPSHOT and session.resting_rmssd_ms == value
        else:
            assert r["hrv_source_tier"] == STRAP, r
            messages = synthetic(
                total_timer_time=150.0, avg_heart_rate=60, sport_profile_name=PROFILE, start_time=when
            )
            session = classified(messages, _beats(value), 1.0, [PROFILE])
            assert session.hrv_source_tier == STRAP, r
            assert session.resting_rmssd_ms == pytest.approx(value), (session.resting_rmssd_ms, value)
            beats_by_id[session.session_id] = _beats(value)
        sessions.append(session)
    persist_sessions(sessions, beats_by_id)
    return [s.session_id for s in sessions]


def test_the_3_2_1_switch_away_row_reads_the_same_through_the_endpoint(
    isolated_data_dir, monkeypatch, synthetic, classified, persist_sessions
) -> None:
    """The first test's row, persisted through the real classifier and read back through
    ``GET /metrics/hrv`` in the athlete's zone: the route's verdict is the pure module's,
    the strap is the baseline tier, and the seven included rows are the strap's -- none of
    the three strays. This is the shape AC21's comparison runs against, so it is pinned at
    the seam a consumer reads, not only at ``build_series``.

    Two-tier by construction (N = 2): the classifier can write ``chest_strap_raw`` and
    ``health_snapshot`` and nothing else. Held constant: everything the first test holds,
    plus the route's 126-day read window, which the 87-day era exactly fits
    (``base-126`` is its first day)."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[PROFILE],
        athlete_timezone="Pacific/Auckland",
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    assert Path(db_module._load_config_cached().data_dir) == Path(isolated_data_dir)

    rows = pop.switch_away_rows(TRIAL_THEN_SWITCH_ERA, SHIFTED_STRAYS)
    persisted = _persist_rows(rows, synthetic, classified, persist_sessions)
    assert len(persisted) == len(rows) == len(set(persisted))

    pure_series, pure_verdict = _judged(rows, pop.SWITCH_BASE)
    with TestClient(app) as client:
        response = client.get("/metrics/hrv", params={"to": pop.SWITCH_BASE.isoformat()})
    assert response.status_code == 200, response.text
    body = response.json()
    included = [(e["date"], e["tier"], e["rmssd_ms"]) for e in body["included"]]
    print(f"endpoint verdict={body['verdict']} tier={body['baseline']['tier']} included={included}")
    print(f"pure     {_slice(pure_series, pure_verdict)}")

    assert body["verdict"] == pure_verdict.verdict == hrv_trend.VERDICT_SUPPRESSED, body
    assert body["unavailable_reason"] is None, body
    assert body["baseline"]["tier"] == STRAP, body
    assert body["baseline"]["n"] == pure_verdict.baseline_n, body
    assert body["readings_in_window"] == 7, body
    assert [tier for _, tier, _ in included] == [STRAP] * 7, included
    assert all(value == pytest.approx(33.0) for _, _, value in included), included


# ---------------------------------------------------------------------------
# T167 (B-CR-002) -- the third verdict-free state, through the route
# ---------------------------------------------------------------------------


def _hrv_body(client: TestClient, target: date) -> dict:
    response = client.get("/metrics/hrv", params={"to": target.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def _row_keys(rows: Iterable[dict]) -> list[tuple]:
    return sorted((r["start_time"], r["hrv_source_tier"], r["resting_rmssd_ms"]) for r in rows)


def test_a_withheld_verdict_names_no_dissenter_and_a_conferred_one_still_does(
    isolated_data_dir, monkeypatch, synthetic, classified, persist_sessions
) -> None:
    """T167, fixing ``B-CR-002``: ``disagreed_with`` is empty in **every** state in
    which no verdict is conferred, not only the two the code happened to guard.

    ``research/00`` §5.4 (iii) as amended 2026-09-21 (T167): "whenever the served
    verdict is ``hrv_unavailable``, for any cause whatever, nothing is named as
    disagreeing ... because a disagreement is a claim *about* a verdict and a
    withheld verdict makes no claim to contradict". Before this task the endpoint
    guarded a null selection (in ``hrv_trend.disagreed_with``) and
    ``day_not_happened`` (in ``main._disagreed_with``) and left the third state --
    a **selected** dataset whose verdict is withheld under (v),
    ``week_not_representative`` -- naming a dissenter beside
    ``verdict: hrv_unavailable``. That state is the returning athlete, T125's
    population and the reason F006 exists, so it is the one this pins.

    **The fixture is the matched pair, and ``c`` is the whole of what differs.**
    ``inter_rows(30, "resumed", c)`` is the harness's device return: a snapshot
    carrier at 38/44 ms, a chest-strap era at 79 ms ending ``D-43``, and the strap
    back on the judged week's last three mornings at 25 ms.

    * ``c`` = 0 -- the carrier stops on ``D-3``, so every strap week day is later
      than every carrier week day, the withhold of (v) fires, and the served
      verdict is ``hrv_unavailable`` / ``week_not_representative``.
    * ``c`` = 1 -- **the control.** The carrier also captures ``D-2``, the order
      clause is false, nothing is withheld, and the served verdict is
      ``hrv_normal``.

    In *both* rows the strap holds a computable band and a judged week that reads
    the other side of it from the selected snapshot, so ``hrv_trend.disagreed_with``
    names it in both (asserted below on ``datasets[]``, which reports each
    dataset's own ``below`` in both rows alike). The control is therefore not a
    different fixture that happens to dissent: it is the *same* rows one carrier
    morning apart, and it shows the empty list at ``c`` = 0 is the rule rather than
    a week on which nobody disagreed. Without it this assertion could pass on a
    fixture that never exercised the axis -- the shape that shipped ``B-CR-002``
    itself, whose killing mutation tested the branch beside the bug.

    Read at the seam a consumer reads: persisted through ``mapping.to_canonical``
    -> ``hrv_classification.classify`` -> ``db.persist`` and served by
    ``GET /metrics/hrv`` (N = 2; the classifier writes these two tiers only).

    **Axes held constant:** era length 30, mode ``resumed``, target ``D``, zone
    ``Pacific/Auckland``, daily density on both datasets, the harness's values.
    **Varied:** ``c``, 0 -> 1, and nothing else."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[PROFILE],
        athlete_timezone="Pacific/Auckland",
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    assert Path(db_module._load_config_cached().data_dir) == Path(isolated_data_dir)

    withheld_rows = pop.inter_rows(30, "resumed", 0)
    conferred_rows = pop.inter_rows(30, "resumed", 1)
    # The two row sets differ by exactly one carrier morning -- stated, not assumed,
    # so "the control is the same fixture" is a fact of the rows and not of the prose.
    withheld_keys, conferred_keys = _row_keys(withheld_rows), _row_keys(conferred_rows)
    extra = [k for k in conferred_keys if k not in withheld_keys]
    print("the one row that differs:", extra)
    assert len(extra) == 1, extra
    assert withheld_keys == [k for k in conferred_keys if k not in extra]

    persisted = _persist_rows(withheld_rows, synthetic, classified, persist_sessions)
    assert len(persisted) == len(withheld_rows) == len(set(persisted))
    with TestClient(app) as client:
        withheld = _hrv_body(client, pop.D)

    # The slice compared, printed before it is asserted
    # (``a-witness-must-print-the-slice-it-compared``).
    print(
        "withheld  verdict=", withheld["verdict"], "reason=", withheld["unavailable_reason"],
        "selected=", withheld["selected_dataset"], "/", withheld["selected_reason"],
        "disagreed_with=", withheld["disagreed_with"],
        "datasets=", [(d["tier"], d["week_days"], d["below"]) for d in withheld["datasets"]],
    )

    assert withheld["verdict"] == hrv_trend.VERDICT_UNAVAILABLE, withheld["verdict"]
    assert withheld["unavailable_reason"] == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, withheld
    # A dataset **is** selected here -- this is not the AC9 fallback, and that is
    # what made the state reachable past both existing guards.
    assert withheld["selected_dataset"] == SNAPSHOT, withheld["selected_dataset"]
    assert withheld["selected_reason"] is not None, withheld["selected_reason"]
    # The claim about a verdict: withheld with it.
    assert withheld["disagreed_with"] == [], withheld["disagreed_with"]
    # And the state that makes the empty list a decision rather than an absence:
    # the strap still reads the other side of its own band, and the block says so.
    withheld_by_tier = {d["tier"]: d for d in withheld["datasets"]}
    assert withheld_by_tier[STRAP]["below"] is True, withheld_by_tier[STRAP]
    assert withheld_by_tier[SNAPSHOT]["below"] is False, withheld_by_tier[SNAPSHOT]

    # -- the control: one carrier morning later, a verdict IS conferred --
    for path in sorted(Path(isolated_data_dir).rglob("*")):
        if path.is_file():
            path.unlink()
    db_module._load_config_cached.cache_clear()
    _persist_rows(conferred_rows, synthetic, classified, persist_sessions)
    with TestClient(app) as client:
        conferred = _hrv_body(client, pop.D)

    print(
        "conferred verdict=", conferred["verdict"], "reason=", conferred["unavailable_reason"],
        "selected=", conferred["selected_dataset"], "/", conferred["selected_reason"],
        "disagreed_with=", conferred["disagreed_with"],
        "datasets=", [(d["tier"], d["week_days"], d["below"]) for d in conferred["datasets"]],
    )

    assert conferred["verdict"] == hrv_trend.VERDICT_NORMAL, conferred["verdict"]
    assert conferred["unavailable_reason"] is None, conferred["unavailable_reason"]
    assert conferred["selected_dataset"] == SNAPSHOT, conferred["selected_dataset"]
    assert [d["dataset"] for d in conferred["disagreed_with"]] == [STRAP], conferred["disagreed_with"]
    conferred_by_tier = {d["tier"]: d for d in conferred["datasets"]}
    assert conferred_by_tier[STRAP]["below"] is True, conferred_by_tier[STRAP]
    assert conferred_by_tier[SNAPSHOT]["below"] is False, conferred_by_tier[SNAPSHOT]
    # The dissent the withheld row does not report is the same dissent the control
    # does: same dataset, same judged-week count, same side of its own band.
    assert conferred_by_tier[STRAP]["week_days"] == withheld_by_tier[STRAP]["week_days"]
    assert conferred["disagreed_with"][0]["week_days"] == withheld_by_tier[STRAP]["week_days"]


# ---------------------------------------------------------------------------
# T168 -- week_not_representative does not imply a selected dataset
# ---------------------------------------------------------------------------


def _fallback_withheld_rows(strap_week: int) -> list[dict]:
    """The sprint-006 cycle-2 critic's shape, at ``pop.D``: a ``health_snapshot`` of ten
    baseline days (``D-20..D-11``, alternating 38/44 ms) and three judged-week mornings
    ``D-6..D-4`` at 25 ms, far below its band; a ``chest_strap_raw`` of two baseline days
    (``D-15``, ``D-14``, 38/44 ms -- a band, but no establishment) and ``strap_week``
    judged-week mornings ending on ``D`` at 40 ms, inside its band. Neither dataset is
    established, so neither is judgeable and nothing is selected; the fallback presents the
    snapshot, densest by ``n`` (clause 2)."""
    snap_base = pop.span(pop.D - timedelta(days=20), pop.D - timedelta(days=11))
    strap_base = [pop.D - timedelta(days=15), pop.D - timedelta(days=14)]
    snap_week = pop.span(pop.D - timedelta(days=6), pop.D - timedelta(days=4))
    strap_days = pop.span(pop.D - timedelta(days=strap_week - 1), pop.D)
    rows = []
    for day, value in zip(snap_base, pop._alt(snap_base), strict=True):
        rows.append(pop._row(f"S-{day}", day, 6, value, SNAPSHOT))
    for day in snap_week:
        rows.append(pop._row(f"S-{day}", day, 6, 25.0, SNAPSHOT))
    for day, value in zip(strap_base, pop._alt(strap_base), strict=True):
        rows.append(pop._row(f"C-{day}", day, 7, value, STRAP))
    for day in strap_days:
        rows.append(pop._row(f"C-{day}", day, 7, 40.0, STRAP))
    return rows


def test_week_not_representative_is_served_with_nothing_selected_and_names_no_dissenter(
    isolated_data_dir, monkeypatch, synthetic, classified, persist_sessions
) -> None:
    """T168, section 4: ``week_not_representative`` is **not** a sign that a dataset is
    selected. The contract used to pair that reason with "a dataset that IS selected";
    it is also served when **nothing** is selected and the dataset the AC9 fallback
    presents is itself withheld under ``research/00`` §5.4 (v) -- ``unavailable_reason``
    is the *presented* dataset's first-firing guard (T156), and ``verdict_withheld`` is
    asked of every dataset, selected or not.

    Nothing pinned the pairing before this: the T167 pin above serves
    ``week_not_representative`` on a **selected** snapshot, and the fallback's own pins
    serve ``week_too_thin`` / ``baseline_unestablished``. So a consumer inferring
    selection from the reason, or a renderer computing ``disagreed_with`` against the
    presented dataset as though it had been selected, was unseen by every gate.

    **Why the empty list is a decision, not an absence.** The two datasets read opposite
    sides of their own bands (snapshot below, strap within), asserted on ``datasets[]``,
    so a dissent computed against the presented snapshot *would* name the strap.

    **The control** is the same rows with the strap's judged week cut from four mornings
    to two: fewer than ``MIN_WINDOW_READINGS``, the withhold cannot fire, and the reason
    moves to ``baseline_unestablished`` while ``selected_dataset`` stays null and
    ``disagreed_with`` stays empty -- the reason varies, the selection does not.

    **Axes held constant:** target ``pop.D``, zone ``Pacific/Auckland``, daily density,
    the values above, N = 2 (the classifier writes these two tiers only). **Varied:** the
    strap's judged-week length, 4 -> 2, and nothing else."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[PROFILE],
        athlete_timezone="Pacific/Auckland",
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    assert Path(db_module._load_config_cached().data_dir) == Path(isolated_data_dir)

    def served(rows: list[dict]) -> tuple[dict, hrv_trend.SingleDatasetView]:
        for path in sorted(Path(isolated_data_dir).rglob("*")):
            if path.is_file():
                path.unlink()
        db_module._load_config_cached.cache_clear()
        persisted = _persist_rows(rows, synthetic, classified, persist_sessions)
        assert len(persisted) == len(rows) == len(set(persisted))
        with TestClient(app) as client:
            body = _hrv_body(client, pop.D)
        view, _ = _judged(rows, pop.D)
        print(
            f"strap_week={len([r for r in rows if r['hrv_source_tier'] == STRAP]) - 2}",
            "verdict=", body["verdict"], "reason=", body["unavailable_reason"],
            "selected=", body["selected_dataset"], "/", body["selected_reason"],
            "presented=", body["baseline"]["tier"], view.presented_by,
            "disagreed_with=", body["disagreed_with"],
            "datasets=", [(d["tier"], d["n"], d["week_days"], d["below"]) for d in body["datasets"]],
        )
        return body, view

    withheld, withheld_view = served(_fallback_withheld_rows(strap_week=4))

    assert withheld["verdict"] == hrv_trend.VERDICT_UNAVAILABLE, withheld["verdict"]
    assert withheld["unavailable_reason"] == hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE, withheld
    # Nothing is selected: the reason came from the fallback's presented dataset.
    assert withheld["selected_dataset"] is None, withheld["selected_dataset"]
    assert withheld["selected_reason"] is None, withheld["selected_reason"]
    assert withheld["baseline"]["tier"] == SNAPSHOT, withheld["baseline"]
    assert withheld_view.presented_by == hrv_trend.FALLBACK_DENSEST_BASELINE, withheld_view.presented_by
    assert withheld_view.withheld is True
    assert withheld["disagreed_with"] == [], withheld["disagreed_with"]
    by_tier = {d["tier"]: d for d in withheld["datasets"]}
    assert by_tier[SNAPSHOT]["below"] is True, by_tier[SNAPSHOT]
    assert by_tier[STRAP]["below"] is False, by_tier[STRAP]
    assert by_tier[STRAP]["week_days"] == 4, by_tier[STRAP]

    control, control_view = served(_fallback_withheld_rows(strap_week=2))

    assert control["verdict"] == hrv_trend.VERDICT_UNAVAILABLE, control["verdict"]
    assert control["unavailable_reason"] == hrv_trend.REASON_BASELINE_UNESTABLISHED, control
    assert control["selected_dataset"] is None, control["selected_dataset"]
    assert control_view.presented_by == hrv_trend.FALLBACK_DENSEST_BASELINE, control_view.presented_by
    assert control_view.withheld is False
    assert control["disagreed_with"] == [], control["disagreed_with"]
    control_by_tier = {d["tier"]: d for d in control["datasets"]}
    assert control_by_tier[SNAPSHOT]["below"] is True, control_by_tier[SNAPSHOT]
    assert control_by_tier[STRAP]["below"] is False, control_by_tier[STRAP]
