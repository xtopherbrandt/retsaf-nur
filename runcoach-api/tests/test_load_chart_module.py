"""``metrics/load_chart.py``: the daily fitness, fatigue and form series (F018, spec/03 section 3.5).

Pure unit tests over hand-built row dicts, a fixed ``today`` and a ``ZoneInfo``: the module
imports neither ``config`` nor ``db`` nor ``fastapi``, so no database is needed here.

**The oracle below is independent of the module.** ``oracle_series`` applies the reference's
formulas (F018 reference, "The curves" and "The start value") to a plain list of day loads and
nothing else: ``kC = 1 - e^(-1/42)``, ``kA = 1 - e^(-1/7)``, ``CTL_0 = ATL_0 = seed`` with the
seed the mean of the first 42 day loads (all of them when shorter), ``TSB_d`` from yesterday's
values. It does not import ``metrics.load_chart``; a defect in the module's recursion cannot be
mirrored into the thing checking it. The five-day table is also pinned as literal values from the
reference (to 0.01), so the oracle itself is checked against a number written by hand.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import load_chart

UTC = ZoneInfo("UTC")
DAY_ONE = date(2026, 1, 1)

ORACLE_K_CTL = 1 - math.exp(-1 / 42)
ORACLE_K_ATL = 1 - math.exp(-1 / 7)


def oracle_series(day_loads: list[float]) -> tuple[float, list[tuple[float, float, float]]]:
    """The reference's recursion over day loads: ``(seed, [(ctl, atl, tsb), ...])``."""
    window = day_loads[:42]
    seed = sum(window) / len(window)
    ctl = atl = seed
    out = []
    for load in day_loads:
        tsb = ctl - atl
        ctl = ctl + (load - ctl) * ORACLE_K_CTL
        atl = atl + (load - atl) * ORACLE_K_ATL
        out.append((ctl, atl, tsb))
    return seed, out


def day(n: int) -> date:
    """Day ``n`` of a history that starts on ``DAY_ONE`` (day 1)."""
    return DAY_ONE + timedelta(days=n - 1)


def at(n: int, hour: int = 8, minute: int = 0) -> str:
    """An aware UTC ``start_time`` on day ``n`` (UTC is the zone in every test but the local-day ones)."""
    return f"{day(n).isoformat()}T{hour:02d}:{minute:02d}:00+00:00"


def row(session_id: str, start_time: str, load=None, reason=None) -> dict:
    return {"session_id": session_id, "start_time": start_time, "load_value": load, "load_reason": reason}


def counted(n: int, load: float, session_id: str | None = None, hour: int = 8) -> dict:
    return row(session_id or f"run-{n:03d}", at(n, hour), load=load)


def uncounted(n: int, reason: str = "no_hr", session_id: str | None = None, hour: int = 8) -> dict:
    return row(session_id or f"unc-{n:03d}", at(n, hour), reason=reason)


def values(chart) -> list[tuple[float, float, float, float]]:
    return [(d.load, d.ctl, d.atl, d.tsb) for d in chart.days]


def assert_matches_oracle(chart, day_loads: list[float]) -> None:
    seed, expected = oracle_series(day_loads)
    assert chart.seed.value == pytest.approx(seed, abs=1e-9)
    assert len(chart.days) == len(day_loads)
    for served, load, (ctl, atl, tsb) in zip(chart.days, day_loads, expected, strict=True):
        assert served.load == pytest.approx(load, abs=1e-9)
        assert served.ctl == pytest.approx(ctl, abs=1e-9)
        assert served.atl == pytest.approx(atl, abs=1e-9)
        assert served.tsb == pytest.approx(tsb, abs=1e-9)


# --- AC2: the worked history ----------------------------------------------------------------

FIVE_DAY_ROWS = [counted(1, 60.0), counted(3, 45.0), uncounted(4, "wrist_hr_at_threshold"), counted(5, 50.0)]
FIVE_DAY_LOADS = [60.0, 0.0, 45.0, 0.0, 50.0]

# F018 reference, "Worked values (oracle)": written by hand, independent of oracle_series.
REFERENCE_TABLE = [
    (60.0, 31.68, 34.86, 0.00),
    (0.0, 30.94, 30.22, -3.18),
    (45.0, 31.27, 32.19, 0.72),
    (0.0, 30.53, 27.90, -0.92),
    (50.0, 30.99, 30.84, 2.63),
]


def test_the_five_day_history_matches_the_reference_table():
    chart = load_chart.build_chart(FIVE_DAY_ROWS, UTC, today=day(5))

    assert chart.first_day == day(1)
    assert chart.seed.value == pytest.approx(31.00, abs=0.005)
    assert [d.date for d in chart.days] == [day(n) for n in range(1, 6)]
    for served, (load, ctl, atl, tsb) in zip(chart.days, REFERENCE_TABLE, strict=True):
        assert served.load == pytest.approx(load, abs=0.005)
        assert served.ctl == pytest.approx(ctl, abs=0.005)
        assert served.atl == pytest.approx(atl, abs=0.005)
        assert served.tsb == pytest.approx(tsb, abs=0.005)
    assert chart.days[0].tsb == 0.0
    assert [d.marked for d in chart.days] == [False, False, False, True, False]
    assert_matches_oracle(chart, FIVE_DAY_LOADS)


def test_the_module_constants_are_the_fixed_42_and_7_day_rates():
    assert load_chart.K_CTL == pytest.approx(1 - math.exp(-1 / 42), abs=1e-12)
    assert load_chart.K_ATL == pytest.approx(1 - math.exp(-1 / 7), abs=1e-12)
    assert round(load_chart.K_CTL, 6) == 0.023528
    assert round(load_chart.K_ATL, 6) == 0.133122
    assert load_chart.SEED_WINDOW_DAYS == 42


def test_two_counted_runs_on_day_three_sum_to_the_same_table():
    rows = [counted(1, 60.0), counted(3, 20.0, "run-003a", hour=7), counted(3, 25.0, "run-003b", hour=17)]
    rows += [uncounted(4, "wrist_hr_at_threshold"), counted(5, 50.0)]
    chart = load_chart.build_chart(rows, UTC, today=day(5))
    reference = load_chart.build_chart(FIVE_DAY_ROWS, UTC, today=day(5))

    assert chart.days[2].load == 45.0
    assert [c.session_id for c in chart.days[2].counted] == ["run-003a", "run-003b"]
    assert [c.load for c in chart.days[2].counted] == [20.0, 25.0]
    assert values(chart) == values(reference)
    assert chart.seed == reference.seed
    assert_matches_oracle(chart, FIVE_DAY_LOADS)


# --- AC3: every session in exactly one group ------------------------------------------------


def test_every_session_on_a_day_lands_in_exactly_one_group():
    rows = [
        row("run", at(1, 6), load=30.0),
        row("wrist", at(1, 7), reason="wrist_hr_at_threshold"),
        row("nohr", at(1, 8), reason="no_hr"),
        row("bike", at(1, 9), reason="sport_not_running"),
        row("rest", at(1, 10), reason="declared_capture"),
    ]
    chart = load_chart.build_chart(rows, UTC, today=day(1))
    (d,) = chart.days

    assert d.load == 30.0
    assert [(c.session_id, c.load) for c in d.counted] == [("run", 30.0)]
    assert [(u.session_id, u.reason) for u in d.uncounted] == [
        ("wrist", "wrist_hr_at_threshold"),
        ("nohr", "no_hr"),
    ]
    assert [(e.session_id, e.reason) for e in d.excluded] == [
        ("bike", "sport_not_running"),
        ("rest", "declared_capture"),
    ]
    assert d.marked is True
    assert d.uncounted_in_window == 2
    listed = [c.session_id for c in d.counted] + [u.session_id for u in d.uncounted]
    listed += [e.session_id for e in d.excluded]
    assert sorted(listed) == sorted(r["session_id"] for r in rows)
    assert all(c.load != 0.0 for c in d.counted)


def test_a_day_holding_only_excluded_sessions_is_not_marked():
    rows = [counted(1, 30.0), row("bike", at(2, 9), reason="sport_not_running")]
    rows += [row("rest", at(2, 10), reason="declared_capture")]
    chart = load_chart.build_chart(rows, UTC, today=day(2))

    assert chart.days[1].load == 0.0
    assert chart.days[1].marked is False
    assert chart.days[1].counted == () and chart.days[1].uncounted == ()
    assert [e.session_id for e in chart.days[1].excluded] == ["bike", "rest"]
    assert chart.days[1].uncounted_in_window == 0


def test_a_run_without_a_load_and_without_a_reason_is_refused_not_served_as_zero():
    with pytest.raises(ValueError, match="orphan"):
        load_chart.build_chart([row("orphan", at(1))], UTC, today=day(1))


# --- first_day and the empty chart ----------------------------------------------------------


def test_excluded_sessions_before_the_first_run_do_not_move_first_day_or_the_seed():
    plain = [counted(1, 60.0), counted(3, 45.0)]
    older = [row("old-bike", at(-29, 9), reason="sport_not_running")]
    older += [row("old-rest", at(-29, 10), reason="declared_capture")]
    chart = load_chart.build_chart(older + plain, UTC, today=day(3))
    reference = load_chart.build_chart(plain, UTC, today=day(3))

    assert chart.first_day == day(1)
    assert chart.seed == reference.seed
    assert values(chart) == values(reference)
    assert [d.excluded for d in chart.days] == [(), (), ()]


def test_an_uncounted_first_run_starts_the_history():
    rows = [uncounted(1, "no_hr"), counted(4, 40.0)]
    chart = load_chart.build_chart(rows, UTC, today=day(4))

    assert chart.first_day == day(1)
    assert chart.days[0].marked is True
    assert_matches_oracle(chart, [0.0, 0.0, 0.0, 40.0])


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row("bike", at(1), reason="sport_not_running"), row("rest", at(2), reason="declared_capture")],
    ],
    ids=["no-session", "only-excluded"],
)
def test_no_running_session_gives_null_first_day_null_seed_and_no_days(rows):
    chart = load_chart.build_chart(rows, UTC, today=day(5))

    assert chart.first_day is None
    assert chart.seed is None
    assert chart.days == ()


def test_a_history_that_starts_after_today_has_no_days():
    chart = load_chart.build_chart([counted(3, 40.0)], UTC, today=day(2))

    assert chart.first_day == day(3)
    assert chart.seed is None
    assert chart.days == ()


# --- AC4: an uncounted-only day moves like a rest day ---------------------------------------

FIFTY_DAY_LOADS = [float((n * 37) % 70) if n % 3 else 0.0 for n in range(1, 51)]


def fifty_day_rows() -> list[dict]:
    return [counted(n, load) for n, load in enumerate(FIFTY_DAY_LOADS, start=1) if load]


def test_an_uncounted_run_on_day_four_changes_no_served_value():
    plain = load_chart.build_chart(fifty_day_rows(), UTC, today=day(50))
    with_gap = load_chart.build_chart(fifty_day_rows() + [uncounted(4, "no_hr")], UTC, today=day(50))

    assert len(plain.days) == 50
    assert values(with_gap) == values(plain)
    assert with_gap.seed == plain.seed
    assert_matches_oracle(with_gap, FIFTY_DAY_LOADS)
    for n, (a, b) in enumerate(zip(plain.days, with_gap.days, strict=True), start=1):
        assert a.date == b.date == day(n)
        assert a.counted == b.counted and a.excluded == b.excluded and a.provisional == b.provisional
        if n == 4:
            assert a.uncounted == () and a.marked is False
            assert [(u.session_id, u.reason) for u in b.uncounted] == [("unc-004", "no_hr")]
            assert b.marked is True
        else:
            assert a.uncounted == b.uncounted == ()
            assert a.marked is b.marked is False
    assert [d.uncounted_in_window for d in plain.days] == [0] * 50
    expected_window = [1 if 4 <= n <= 45 else 0 for n in range(1, 51)]
    assert [d.uncounted_in_window for d in with_gap.days] == expected_window


# --- AC5: the start value --------------------------------------------------------------------

TEN_DAY_LOADS = [50.0, 0.0, 40.0, 0.0, 0.0, 60.0, 30.0, 0.0, 45.0, 0.0]


def test_a_ten_day_history_seeds_from_all_ten_days_and_is_provisional_throughout():
    rows = [counted(n, load) for n, load in enumerate(TEN_DAY_LOADS, start=1) if load]
    chart = load_chart.build_chart(rows, UTC, today=day(10))

    assert chart.seed.value == pytest.approx(sum(TEN_DAY_LOADS) / 10, abs=1e-9)
    assert chart.seed.window_days == 10
    assert chart.seed.provisional_until == day(42)
    assert [d.provisional for d in chart.days] == [True] * 10
    assert_matches_oracle(chart, TEN_DAY_LOADS)

    later = load_chart.build_chart(rows, UTC, today=day(11))
    assert later.seed.value == pytest.approx(sum(TEN_DAY_LOADS) / 11, abs=1e-9)
    assert later.seed.value < chart.seed.value
    assert later.seed.window_days == 11
    assert later.seed.provisional_until == day(42)
    assert_matches_oracle(later, TEN_DAY_LOADS + [0.0])


SIXTY_DAY_LOADS = [float((n * 23) % 80) if n % 4 else 0.0 for n in range(1, 61)]


def sixty_day_rows() -> list[dict]:
    return [counted(n, load) for n, load in enumerate(SIXTY_DAY_LOADS, start=1) if load]


def test_a_sixty_day_history_seeds_from_the_first_42_days_and_is_provisional_through_day_42():
    chart = load_chart.build_chart(sixty_day_rows(), UTC, today=day(60))

    assert chart.seed.value == pytest.approx(sum(SIXTY_DAY_LOADS[:42]) / 42, abs=1e-9)
    assert chart.seed.window_days == 42
    assert chart.seed.provisional_until == day(42)
    assert [d.provisional for d in chart.days] == [True] * 42 + [False] * 18
    assert_matches_oracle(chart, SIXTY_DAY_LOADS)


def test_the_seed_at_42_days_equals_the_seed_at_43_and_at_100():
    at_42 = load_chart.build_chart(sixty_day_rows(), UTC, today=day(42))
    at_43 = load_chart.build_chart(sixty_day_rows(), UTC, today=day(43))
    at_100 = load_chart.build_chart(sixty_day_rows(), UTC, today=day(100))

    assert at_42.seed == at_43.seed == at_100.seed
    assert at_42.seed.window_days == 42
    assert values(at_42) == values(at_43)[:42] == values(at_100)[:42]
    assert len(at_100.days) == 100
    assert [d.provisional for d in at_100.days] == [True] * 42 + [False] * 58
    assert_matches_oracle(at_100, SIXTY_DAY_LOADS + [0.0] * 40)


# --- AC6: local days -------------------------------------------------------------------------

LONDON = ZoneInfo("Europe/London")
AUCKLAND = ZoneInfo("Pacific/Auckland")


def test_runs_at_2330_and_0030_local_on_consecutive_dates_land_on_different_days():
    # 2026-07-10 23:30 BST is 22:30Z; 2026-07-11 00:30 BST is 23:30Z the same UTC date.
    rows = [row("late", "2026-07-10T22:30:00+00:00", load=30.0), row("early", "2026-07-10T23:30:00+00:00", load=20.0)]
    chart = load_chart.build_chart(rows, LONDON, today=date(2026, 7, 11))

    assert chart.first_day == date(2026, 7, 10)
    assert [(d.date, d.load) for d in chart.days] == [(date(2026, 7, 10), 30.0), (date(2026, 7, 11), 20.0)]


def test_a_run_at_0030_bst_on_the_october_change_date_lands_on_the_25th():
    # 2026-10-24T23:30Z is 00:30 BST on 2026-10-25 (the clocks go back at 02:00 BST that morning);
    # the UTC date and a fixed +00:00 offset would both put it on the 24th.
    rows = [row("change", "2026-10-24T23:30:00+00:00", load=40.0)]
    chart = load_chart.build_chart(rows, LONDON, today=date(2026, 10, 25))

    assert chart.first_day == date(2026, 10, 25)
    assert [(d.date, d.load) for d in chart.days] == [(date(2026, 10, 25), 40.0)]


LOCAL_DAY_ROWS = [
    row("noon-utc", "2026-10-24T12:00:00+00:00", load=10.0),
    row("late-bst", "2026-10-24T22:30:00+00:00", load=20.0),
    row("change", "2026-10-24T23:30:00+00:00", load=30.0),
]


def test_the_same_rows_land_on_their_london_dates_and_on_their_auckland_dates():
    london = load_chart.build_chart(LOCAL_DAY_ROWS, LONDON, today=date(2026, 10, 26))
    auckland = load_chart.build_chart(LOCAL_DAY_ROWS, AUCKLAND, today=date(2026, 10, 26))

    # London (BST, +01:00 until 02:00 on the 25th): 13:00 on the 24th, 23:30 on the 24th, 00:30 on the 25th.
    assert [(d.date, [c.session_id for c in d.counted]) for d in london.days] == [
        (date(2026, 10, 24), ["noon-utc", "late-bst"]),
        (date(2026, 10, 25), ["change"]),
        (date(2026, 10, 26), []),
    ]
    # Auckland (NZDT, +13:00): 01:00 on the 25th, 11:30 on the 25th, 12:30 on the 25th.
    assert [(d.date, [c.session_id for c in d.counted]) for d in auckland.days] == [
        (date(2026, 10, 25), ["noon-utc", "late-bst", "change"]),
        (date(2026, 10, 26), []),
    ]
    assert london.first_day == date(2026, 10, 24)
    assert auckland.first_day == date(2026, 10, 25)
    assert_matches_oracle(london, [30.0, 30.0, 0.0])
    assert_matches_oracle(auckland, [60.0, 0.0])


def test_a_naive_start_time_is_refused_naming_the_row():
    with pytest.raises(ValueError, match="naive-row"):
        load_chart.build_chart([row("naive-row", "2026-01-01T08:00:00", load=10.0)], UTC, today=day(1))


# --- Ordering within a day -------------------------------------------------------------------


def test_each_list_is_ordered_by_start_time_then_session_id():
    rows = [
        row("z-late", at(1, 18), load=5.0),
        row("b-same", at(1, 9), load=6.0),
        row("a-same", at(1, 9), load=7.0),
        row("m-early", at(1, 6), load=8.0),
        row("u-late", at(1, 15), reason="no_hr"),
        row("u-early", at(1, 7), reason="wrist_hr_at_threshold"),
        row("x-bike-late", at(1, 20), reason="sport_not_running"),
        row("x-rest-early", at(1, 5), reason="declared_capture"),
    ]
    chart = load_chart.build_chart(list(reversed(rows)), UTC, today=day(1))
    (d,) = chart.days

    assert [c.session_id for c in d.counted] == ["m-early", "a-same", "b-same", "z-late"]
    assert [u.session_id for u in d.uncounted] == ["u-early", "u-late"]
    assert [e.session_id for e in d.excluded] == ["x-rest-early", "x-bike-late"]
    assert d.load == 26.0


def test_start_time_order_is_by_instant_not_by_string():
    # The same instant written with two offsets, and an earlier instant whose string sorts later.
    rows = [
        row("b-plus-one", "2026-01-01T10:00:00+01:00", load=1.0),  # 09:00Z
        row("a-zulu", "2026-01-01T09:00:00+00:00", load=1.0),  # 09:00Z
        row("c-earlier", "2026-01-01T08:30:00+00:00", load=1.0),  # 08:30Z
    ]
    chart = load_chart.build_chart(rows, UTC, today=day(1))

    assert [c.session_id for c in chart.days[0].counted] == ["c-earlier", "a-zulu", "b-plus-one"]


def test_the_module_is_pure():
    source = Path(load_chart.__file__).read_text(encoding="utf-8")
    for forbidden in ("config", "db", "fastapi", "sqlite3"):
        assert f"import {forbidden}" not in source and f"from runcoach_api import {forbidden}" not in source
    assert "hrv_trend" in source and "def local_day" not in source
