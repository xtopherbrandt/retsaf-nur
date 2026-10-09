"""The fitness, fatigue and form series (F018, spec/03 section 3.5): daily load by local date,
CTL and ATL at the fixed 42- and 7-day rates, TSB from yesterday's values, and the start value.

**Pure, by design**, on the pattern of ``metrics/hrv_trend.py``: ``build_chart`` takes plain rows,
a ``ZoneInfo`` and the athlete's local ``today`` and returns a dataclass; it imports neither
``config`` nor ``db`` nor ``fastapi``. The route resolves the zone and today once per request and
reads the saved loads; the suites drive the computation with hand-built dicts and no database.
Rows are read **by key** (``session_id``, ``start_time``, ``load_value``, ``load_reason``), so a
``sqlite3.Row`` and a plain dict are interchangeable.

**Every session is in exactly one group** (F018 reference, "What it reads"):

- *counted*: the saved load is a number; it adds to the day's load.
- *excluded*: the saved load is unavailable ``sport_not_running`` or ``declared_capture``; it
  adds nothing and does not mark the day (not running load, by design).
- *uncounted*: the saved load is unavailable for any other reason (a running session the load
  computation could not price); it adds nothing and the day is **marked**. A run without a load
  is never served as 0 (user ruling C4): the chart's recursion cannot drop a day, so an
  uncounted-only day moves like a rest day, and ``marked`` with ``uncounted_in_window`` say so.

A row with neither a load nor a reason is a defect of the writer, not an input to guess at, and
is refused naming the row (as ``hrv_trend.local_day`` refuses a naive ``start_time``).

**Days are the athlete's local days.** Each row is bucketed with ``hrv_trend.local_day``, per
row, so a DST change between two rows is honoured; a run crossing midnight counts on its start
date. History runs from the first local date holding a counted or uncounted session to ``today``,
every date in between included; excluded sessions dated earlier do not move it and are not
returned. With no counted or uncounted session, ``first_day`` and ``seed`` are ``None`` and
``days`` is empty. A history whose first day is after ``today`` (a watch clock ahead) has no days
and no seed, since the seed is a mean over days that do not exist yet.

**The curves** (spec/03 section 3.5.2; research/00 REG-01, REG-20, IND-06): with ``load_d`` the
sum of the day's counted loads,

    CTL_d = CTL_(d-1) + (load_d - CTL_(d-1)) * K_CTL
    ATL_d = ATL_(d-1) + (load_d - ATL_(d-1)) * K_ATL
    TSB_d = CTL_(d-1) - ATL_(d-1)

The 42/7 constants are fixed, not configurable (IND-06, REG-20; user ruling 2026-10-08).

**The start value** (user ruling C5; spec/03 section 3.5.3 reworded to the average *daily* load):
``seed`` is the mean of ``load_d`` over the first ``SEED_WINDOW_DAYS`` days of history, or over
all of it when shorter, rest and uncounted days counting 0; ``CTL_0 = ATL_0 = seed``, so TSB on
the first day is 0. One rule for every history length, so the chart never jumps when history
passes 42 days. Every day inside the first 42 days of history is ``provisional``: while history is
shorter than that the seed moves each day, and every value after it moves with it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from runcoach_api.metrics.hrv_trend import local_day

CTL_DAYS = 42
ATL_DAYS = 7
K_CTL = 1 - math.exp(-1 / CTL_DAYS)
K_ATL = 1 - math.exp(-1 / ATL_DAYS)

SEED_WINDOW_DAYS = CTL_DAYS
"""The seed averages this many days of history; ``uncounted_in_window`` counts over the same span."""

EXCLUSION_REASONS = frozenset({"sport_not_running", "declared_capture"})
"""The two unavailable-load reasons that exclude a session without marking its day."""


@dataclass(frozen=True)
class CountedSession:
    session_id: str
    load: float


@dataclass(frozen=True)
class ReasonedSession:
    """An uncounted or excluded session with the reason its saved load is unavailable."""

    session_id: str
    reason: str


@dataclass(frozen=True)
class Seed:
    value: float
    window_days: int
    provisional_until: date


@dataclass(frozen=True)
class Day:
    date: date
    load: float
    ctl: float
    atl: float
    tsb: float
    provisional: bool
    marked: bool
    uncounted_in_window: int
    counted: tuple[CountedSession, ...]
    uncounted: tuple[ReasonedSession, ...]
    excluded: tuple[ReasonedSession, ...]


@dataclass(frozen=True)
class LoadChart:
    first_day: date | None
    seed: Seed | None
    days: tuple[Day, ...]


@dataclass(frozen=True)
class _Placed:
    """One row bucketed into its local day, keyed for the within-day order."""

    instant: datetime
    session_id: str
    load: float | None
    reason: str | None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _place(rows: Iterable[Mapping[str, Any]], zone: ZoneInfo) -> dict[date, list[_Placed]]:
    by_day: dict[date, list[_Placed]] = defaultdict(list)
    for row in rows:
        session_id = row["session_id"]
        load = row["load_value"]
        reason = row["load_reason"]
        if not _is_number(load) and reason is None:
            raise ValueError(
                f"session {session_id!r} has neither a saved load nor a reason it is unavailable; "
                "a run without a load is never served as 0, and this row cannot be grouped"
            )
        day, instant = local_day(session_id, row["start_time"], zone)
        by_day[day].append(_Placed(instant, session_id, float(load) if _is_number(load) else None, reason))
    for placed in by_day.values():
        placed.sort(key=lambda p: (p.instant, p.session_id))
    return by_day


def _is_running(placed: _Placed) -> bool:
    """Counted or uncounted: the sessions that start history."""
    return placed.load is not None or placed.reason not in EXCLUSION_REASONS


def build_chart(rows: Iterable[Mapping[str, Any]], zone: ZoneInfo, today: date) -> LoadChart:
    """The daily series from ``first_day`` to ``today`` inclusive, computed from every row."""
    by_day = _place(rows, zone)
    running_days = [day for day, placed in by_day.items() if any(_is_running(p) for p in placed)]
    if not running_days:
        return LoadChart(first_day=None, seed=None, days=())
    first_day = min(running_days)
    if first_day > today:
        return LoadChart(first_day=first_day, seed=None, days=())

    dates = [first_day + timedelta(days=n) for n in range((today - first_day).days + 1)]
    loads = [sum(p.load for p in by_day.get(d, ()) if p.load is not None) for d in dates]
    uncounted_counts = [sum(1 for p in by_day.get(d, ()) if p.load is None and _is_running(p)) for d in dates]

    window = loads[:SEED_WINDOW_DAYS]
    seed = Seed(
        value=sum(window) / len(window),
        window_days=len(window),
        provisional_until=first_day + timedelta(days=SEED_WINDOW_DAYS - 1),
    )

    days: list[Day] = []
    ctl = atl = seed.value
    for index, (d, load) in enumerate(zip(dates, loads, strict=True)):
        tsb = ctl - atl
        ctl = ctl + (load - ctl) * K_CTL
        atl = atl + (load - atl) * K_ATL
        placed = by_day.get(d, ())
        uncounted = tuple(ReasonedSession(p.session_id, p.reason) for p in placed if p.load is None and _is_running(p))
        days.append(
            Day(
                date=d,
                load=load,
                ctl=ctl,
                atl=atl,
                tsb=tsb,
                provisional=index < SEED_WINDOW_DAYS,
                marked=bool(uncounted),
                uncounted_in_window=sum(uncounted_counts[max(0, index - SEED_WINDOW_DAYS + 1) : index + 1]),
                counted=tuple(CountedSession(p.session_id, p.load) for p in placed if p.load is not None),
                uncounted=uncounted,
                excluded=tuple(ReasonedSession(p.session_id, p.reason) for p in placed if not _is_running(p)),
            )
        )
    return LoadChart(first_day=first_day, seed=seed, days=tuple(days))
