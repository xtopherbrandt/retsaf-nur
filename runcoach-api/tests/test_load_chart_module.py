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
from datetime import date, datetime, timedelta, timezone
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


VANCOUVER = ZoneInfo("America/Vancouver")
PST = timezone(timedelta(hours=-8))
PDT = timezone(timedelta(hours=-7))
# The 2026 spring-forward: 02:00 PST on 2026-03-08 is 10:00Z, and the clocks read 03:00 PDT.
VANCOUVER_SPRING_FORWARD_UTC = datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)


def vancouver_date_by_fixed_offset(start_time: str) -> date:
    """The local date from first principles: PST (UTC-8) before the change instant, PDT (UTC-7) from it."""
    instant = datetime.fromisoformat(start_time)
    offset = PDT if instant >= VANCOUVER_SPRING_FORWARD_UTC else PST
    return instant.astimezone(offset).date()


def test_vancouver_spring_forward_runs_land_on_their_local_days():
    # The athlete's zone. Row 1 is written with its own PST offset, 23:30 local on the 7th; row 2 is
    # 07:30Z on the 8th, which is 23:30 PST on the 7th; row 3 is 10:30Z on the 8th, half an hour
    # after the change, which is 03:30 PDT on the 8th. The UTC date would put rows 2 and 3 together.
    rows = [
        row("pst-local", "2026-03-07T23:30:00-08:00", load=30.0),
        row("pst-utc", "2026-03-08T07:30:00+00:00", load=20.0),
        row("pdt-utc", "2026-03-08T10:30:00+00:00", load=10.0),
    ]
    expected = {
        "pst-local": date(2026, 3, 7),
        "pst-utc": date(2026, 3, 7),
        "pdt-utc": date(2026, 3, 8),
    }
    for r in rows:
        assert vancouver_date_by_fixed_offset(r["start_time"]) == expected[r["session_id"]], r["session_id"]
    chart = load_chart.build_chart(rows, VANCOUVER, today=date(2026, 3, 8))

    served = {c.session_id: d.date for d in chart.days for c in d.counted}
    print(f"  served {served}")
    assert served == expected
    assert chart.first_day == date(2026, 3, 7)
    assert [(d.date, [c.session_id for c in d.counted]) for d in chart.days] == [
        (date(2026, 3, 7), ["pst-local", "pst-utc"]),
        (date(2026, 3, 8), ["pdt-utc"]),
    ]
    assert_matches_oracle(chart, [50.0, 10.0])


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


# --- A long gap in history (IDEA-143, ruled option B: carry on and mark) ----------------------
#
# The gap oracle below is written by hand from the ruling and never reads the module: an empty day
# has no counted and no uncounted run (an excluded ride or capture does not end a stretch, an
# uncounted-only day does); a stretch of MORE than 21 empty days that ends in a running day on or
# before today is a gap; the first running day after it and the 41 days after that are after_gap.
# The recursion is untouched, so oracle_series stays the oracle for every served value.

ORACLE_GAP_MIN_EMPTY_DAYS = 21
ORACLE_GAP_MARK_SPAN = 42
EXCLUDED_REASONS = {"sport_not_running", "declared_capture"}


def running_flags(rows: list[dict], n_days: int) -> list[bool]:
    """Day-by-day (day 1 .. n_days): does the day hold a counted or uncounted run? UTC rows only."""
    flags = [False] * n_days
    for r in rows:
        n = (datetime.fromisoformat(r["start_time"]).astimezone(timezone.utc).date() - DAY_ONE).days + 1
        is_running = r["load_value"] is not None or r["load_reason"] not in EXCLUDED_REASONS
        if is_running and 1 <= n <= n_days:
            flags[n - 1] = True
    return flags


def oracle_gaps(flags: list[bool]) -> tuple[list[tuple[int, int, int]], list[bool]]:
    """``([(first_empty_day_n, last_empty_day_n, empty_days), ...], [after_gap per day])``, day numbers 1-based."""
    gaps: list[tuple[int, int, int]] = []
    after = [False] * len(flags)
    empty = 0
    for i, is_running in enumerate(flags):
        if not is_running:
            empty += 1
            continue
        if empty > ORACLE_GAP_MIN_EMPTY_DAYS:
            gaps.append((i - empty + 1, i, empty))
            for j in range(i, min(i + ORACLE_GAP_MARK_SPAN, len(flags))):
                after[j] = True
        empty = 0
    return gaps, after


def day_loads(rows: list[dict], n_days: int) -> list[float]:
    loads = [0.0] * n_days
    for r in rows:
        n = (datetime.fromisoformat(r["start_time"]).astimezone(timezone.utc).date() - DAY_ONE).days + 1
        if r["load_value"] is not None and 1 <= n <= n_days:
            loads[n - 1] += r["load_value"]
    return loads


def assert_matches_gap_oracle(chart, rows: list[dict], n_days: int) -> None:
    gaps, after = oracle_gaps(running_flags(rows, n_days))
    assert [g.empty_days for g in chart.gaps] == [g[2] for g in gaps]
    assert [(g.first_empty_day, g.last_empty_day) for g in chart.gaps] == [(day(a), day(b)) for a, b, _ in gaps]
    assert [d.after_gap for d in chart.days] == after
    assert_matches_oracle(chart, day_loads(rows, n_days))


def runs_from(first: int, last: int, step: int = 3, load: float = 40.0) -> list[dict]:
    return [counted(n, load) for n in range(first, last + 1, step)]


def test_the_42_days_after_a_22_day_gap_are_marked_and_values_are_unchanged():
    # Day 1 runs, days 2..23 are empty (22 days), the athlete returns on day 24 and runs every third day.
    rows = [counted(1, 60.0)] + runs_from(24, 70)
    chart = load_chart.build_chart(rows, UTC, today=day(70))
    plain = load_chart.build_chart(rows, UTC, today=day(70))

    assert [d.after_gap for d in chart.days] == [False] * 23 + [True] * 42 + [False] * 5
    assert chart.days[23].date == day(24) and chart.days[23].after_gap is True
    assert chart.days[64].date == day(65) and chart.days[64].after_gap is True
    assert chart.days[65].date == day(66) and chart.days[65].after_gap is False
    (gap,) = chart.gaps
    assert gap == load_chart.Gap(first_empty_day=day(2), last_empty_day=day(23), empty_days=22)
    assert (gap.last_empty_day - gap.first_empty_day).days + 1 == gap.empty_days
    assert_matches_gap_oracle(chart, rows, 70)
    # Carry on, do not re-seed: seed, provisional and every served value are those of the plain rule.
    assert chart.seed == plain.seed
    assert values(chart) == values(plain)
    assert [d.provisional for d in chart.days] == [True] * 42 + [False] * 28
    assert [d.marked for d in chart.days] == [False] * 70


def test_the_gap_constants_are_21_strict_and_the_42_day_ctl_span():
    assert load_chart.GAP_MARK_DAYS == 21
    assert load_chart.GAP_MARK_SPAN_DAYS == load_chart.CTL_DAYS == 42


def test_a_21_day_gap_is_not_marked():
    # Days 2..22 empty (21 days) -> the N-1 / N pin: nothing marked, no gap listed.
    rows = [counted(1, 60.0)] + runs_from(23, 70)
    chart = load_chart.build_chart(rows, UTC, today=day(70))

    assert chart.gaps == ()
    assert [d.after_gap for d in chart.days] == [False] * 70
    assert_matches_gap_oracle(chart, rows, 70)
    # One more empty day and the same history is a gap.
    with_one_more = load_chart.build_chart([counted(1, 60.0)] + runs_from(24, 70), UTC, today=day(70))
    assert [g.empty_days for g in with_one_more.gaps] == [22]


def test_an_uncounted_only_day_inside_a_stretch_ends_it():
    # Days 2..19 empty (18), an uncounted run on day 20, days 21..39 empty (19), a run on day 40.
    rows = [counted(1, 60.0), uncounted(20, "no_hr")] + runs_from(40, 70)
    chart = load_chart.build_chart(rows, UTC, today=day(70))

    assert chart.gaps == ()
    assert [d.after_gap for d in chart.days] == [False] * 70
    assert chart.days[19].marked is True
    assert_matches_gap_oracle(chart, rows, 70)


def test_an_excluded_ride_or_capture_inside_a_stretch_does_not_end_it():
    rows = [counted(1, 60.0)]
    rows += [row("bike", at(10, 9), reason="sport_not_running"), row("rest", at(15, 7), reason="declared_capture")]
    rows += runs_from(24, 70)
    chart = load_chart.build_chart(rows, UTC, today=day(70))

    assert [(g.first_empty_day, g.last_empty_day, g.empty_days) for g in chart.gaps] == [(day(2), day(23), 22)]
    assert [d.after_gap for d in chart.days] == [False] * 23 + [True] * 42 + [False] * 5
    assert [e.session_id for e in chart.days[9].excluded] == ["bike"]
    assert [e.session_id for e in chart.days[14].excluded] == ["rest"]
    assert_matches_gap_oracle(chart, rows, 70)


def test_a_trailing_stretch_with_no_return_is_not_listed_and_marks_nothing():
    rows = [counted(1, 60.0)]
    chart = load_chart.build_chart(rows, UTC, today=day(31))

    assert len(chart.days) == 31
    assert chart.gaps == ()
    assert [d.after_gap for d in chart.days] == [False] * 31
    assert_matches_gap_oracle(chart, rows, 31)


def test_a_second_gap_inside_the_first_window_restarts_the_42_days():
    # Days 2..23 empty (22), a run on day 24, days 25..47 empty (23), runs from day 48.
    rows = [counted(1, 60.0), counted(24, 30.0)] + runs_from(48, 100)
    chart = load_chart.build_chart(rows, UTC, today=day(100))

    assert [(g.first_empty_day, g.last_empty_day, g.empty_days) for g in chart.gaps] == [
        (day(2), day(23), 22),
        (day(25), day(47), 23),
    ]
    # 24..65 from the first return, 48..89 from the second: the union runs 24..89, day 90 is not marked.
    assert [d.after_gap for d in chart.days] == [False] * 23 + [True] * 66 + [False] * 11
    assert chart.days[88].date == day(89) and chart.days[88].after_gap is True
    assert chart.days[89].date == day(90) and chart.days[89].after_gap is False
    assert_matches_gap_oracle(chart, rows, 100)


def test_a_gap_starting_on_the_day_after_an_uncounted_first_day():
    rows = [uncounted(1, "no_hr")] + runs_from(24, 50)
    chart = load_chart.build_chart(rows, UTC, today=day(50))

    assert chart.first_day == day(1)
    assert [(g.first_empty_day, g.last_empty_day, g.empty_days) for g in chart.gaps] == [(day(2), day(23), 22)]
    assert chart.gaps[0].first_empty_day == chart.first_day + timedelta(days=1)
    assert [d.after_gap for d in chart.days] == [False] * 23 + [True] * 27
    assert_matches_gap_oracle(chart, rows, 50)


def test_a_run_dated_after_today_does_not_close_a_gap():
    rows = [counted(1, 60.0), counted(40, 50.0)]
    chart = load_chart.build_chart(rows, UTC, today=day(31))

    assert len(chart.days) == 31
    assert chart.gaps == ()
    assert [d.after_gap for d in chart.days] == [False] * 31
    assert_matches_gap_oracle(chart, rows, 31)
    # Once that date arrives the same rows list the gap and mark the return.
    later = load_chart.build_chart(rows, UTC, today=day(40))
    assert [(g.first_empty_day, g.last_empty_day, g.empty_days) for g in later.gaps] == [(day(2), day(39), 38)]
    assert [d.after_gap for d in later.days] == [False] * 39 + [True]


def test_a_gap_closed_by_a_run_on_today_is_listed_and_today_is_after_gap():
    rows = [counted(1, 60.0), counted(24, 50.0)]
    chart = load_chart.build_chart(rows, UTC, today=day(24))

    assert chart.days[-1].date == day(24)
    assert [(g.first_empty_day, g.last_empty_day, g.empty_days) for g in chart.gaps] == [(day(2), day(23), 22)]
    assert chart.days[-1].after_gap is True
    assert [d.after_gap for d in chart.days] == [False] * 23 + [True]
    assert_matches_gap_oracle(chart, rows, 24)


def test_the_gap_is_a_frozen_dataclass_with_named_fields():
    gap = load_chart.Gap(first_empty_day=day(2), last_empty_day=day(23), empty_days=22)

    assert (gap.first_empty_day, gap.last_empty_day, gap.empty_days) == (day(2), day(23), 22)
    with pytest.raises(Exception):
        gap.empty_days = 1  # type: ignore[misc]
