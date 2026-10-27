"""``tests/support/ctl_path.py``: planted daily loads put the chart's CTL on chosen values.

Every value is read back through ``db.read_session_load_rows`` and ``load_chart.build_chart``,
never through the helper's own arithmetic: the helper chooses loads, the store and the chart say
what CTL they give. The zone is pinned to ``America/Vancouver`` and ``today`` is fixed.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api import db as db_module
from runcoach_api.metrics import load_chart

SUPPORT = Path(__file__).parent / "support"
ZONE = ZoneInfo("America/Vancouver")
TODAY = date(2026, 10, 14)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


ctl_path = _load_support("ctl_path")


@pytest.fixture
def conn():
    connection = db_module.get_connection()
    db_module.init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _chart(conn) -> dict[date, load_chart.Day]:
    chart = load_chart.build_chart(db_module.read_session_load_rows(conn), ZONE, TODAY)
    return {day.date: day for day in chart.days}


def test_planted_loads_read_back_the_target_ctl(conn) -> None:
    start = date(2026, 4, 1)
    targets = {date(2026, 6, 1): 40.0, date(2026, 7, 15): 55.0, date(2026, 9, 1): 47.5}
    planted = ctl_path.plant(conn, ctl_path.loads_for(targets, start, TODAY), ZONE)
    assert len(planted) == (TODAY - start).days + 1
    days = _chart(conn)
    assert min(days) == start
    for day, target in targets.items():
        assert days[day].ctl == pytest.approx(target, abs=1e-9), day
    stored = {row["session_id"] for row in db_module.read_session_load_rows(conn)}
    assert {p.session_id for p in planted.values()} == stored


def test_section_4_race_d_reads_29_50_36(conn) -> None:
    """Race D of the F029 reference, section 4: CTL 50 29 days before the race, 29 the day
    before it and 36 on the race day, the race's own load included."""
    race = date(2026, 9, 20)
    targets = {race - timedelta(days=29): 50.0, race - timedelta(days=1): 29.0, race: 36.0}
    loads = ctl_path.loads_for(targets, date(2026, 5, 1), TODAY)
    race_load = 120.0
    path_loads = dict(loads)
    path_loads[race] = loads[race] - race_load
    planted = ctl_path.plant(conn, path_loads, ZONE)
    race_id = ctl_path.plant(conn, {race: race_load}, ZONE, hour=9)[race].session_id
    assert race_id not in {p.session_id for p in planted.values()}
    days = _chart(conn)
    assert days[race - timedelta(days=29)].ctl == pytest.approx(50.0, abs=1e-9)
    assert days[race - timedelta(days=1)].ctl == pytest.approx(29.0, abs=1e-9)
    assert days[race].ctl == pytest.approx(36.0, abs=1e-9)
    assert {s.session_id for s in days[race].counted} == {race_id, planted[race].session_id}
    assert days[race].load == pytest.approx(loads[race], abs=1e-9)


def test_a_drop_steeper_than_the_decay_is_refused() -> None:
    start = date(2026, 4, 1)
    first = date(2026, 6, 1)
    # Zero load for ten days gives the steepest fall the decay allows; one more percent is refused.
    steepest = 50.0 * (1 - load_chart.K_CTL) ** 10
    ctl_path.loads_for({first: 50.0, first + timedelta(days=10): steepest * 1.0001}, start, TODAY)
    with pytest.raises(ValueError, match="negative load"):
        ctl_path.loads_for({first: 50.0, first + timedelta(days=10): steepest * 0.99}, start, TODAY)


def test_gap_days_leave_a_gap_the_chart_marks(conn) -> None:
    start = date(2026, 4, 1)
    loads = ctl_path.loads_for({date(2026, 6, 1): 40.0}, start, TODAY)
    gap = tuple(date(2026, 7, 1) + timedelta(days=n) for n in range(load_chart.GAP_MARK_DAYS + 1))
    planted = ctl_path.plant(conn, loads, ZONE, gap_days=gap)
    assert not set(gap) & set(planted)
    days = _chart(conn)
    back = gap[-1] + timedelta(days=1)
    assert all(days[d].load == 0 and not days[d].counted for d in gap)
    assert days[back].after_gap is True
    assert days[gap[0] - timedelta(days=1)].after_gap is False


def test_a_gap_one_day_short_is_not_marked(conn) -> None:
    start = date(2026, 4, 1)
    loads = ctl_path.loads_for({date(2026, 6, 1): 40.0}, start, TODAY)
    gap = tuple(date(2026, 7, 1) + timedelta(days=n) for n in range(load_chart.GAP_MARK_DAYS))
    ctl_path.plant(conn, loads, ZONE, gap_days=gap)
    assert _chart(conn)[gap[-1] + timedelta(days=1)].after_gap is False


def test_a_lead_in_shorter_than_the_seed_window_is_refused() -> None:
    first = date(2026, 6, 1)
    with pytest.raises(ValueError, match="seed"):
        ctl_path.loads_for({first: 40.0}, first - timedelta(days=load_chart.SEED_WINDOW_DAYS - 1), TODAY)
