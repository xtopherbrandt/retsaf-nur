"""``tests/support/ctl_path.py``: choose daily loads so the F018 chart's CTL passes through chosen
values on chosen days, and plant them in the store as one running session per day.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are.

``loads_for`` is pure. The chart's CTL is causal, ``CTL_d = CTL_(d-1) + (load_d - CTL_(d-1)) *
K_CTL``, seeded with the mean daily load of the first ``SEED_WINDOW_DAYS`` days of history; both
constants are read from ``metrics/load_chart.py``. The loads run at the first target's value from
``start`` to the first target (at least ``SEED_WINDOW_DAYS`` days, so the seed equals the first
target and CTL holds there), then make CTL follow a geometric path between consecutive targets,
then hold the last target until ``end``. A step whose CTL falls faster than the decay alone
(``1 - K_CTL``, about 2.35 % a day) would need a negative load and is refused.

``plant`` stores one minimal running session per day at a chosen local hour (noon by default)
with ``db.persist`` and no records, then replaces its saved load with the chosen value through
``session_loads.plant_load``. A day in ``gap_days`` gets no session, so the chart sees an empty
day there. The result maps each planted day to its session id and load, so a test can exclude
the planted sessions from a race read or plant a race beside them.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from runcoach_api import db
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.metrics.load_chart import K_CTL, SEED_WINDOW_DAYS
from runcoach_api.models import Context, Session

DEVICE = "ctl_path"


def _session_loads():
    name = "session_loads"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@dataclass(frozen=True)
class Planted:
    session_id: str
    load: float


def loads_for(targets: Mapping[date, float], start: date, end: date) -> dict[date, float]:
    """One daily load per day from ``start`` to ``end`` inclusive whose chart CTL passes through
    every ``targets`` value on its day.

    Raises ``ValueError`` when the lead-in before the first target is shorter than the seed
    window, when a target is outside ``start``..``end`` or not positive, or when a step needs a
    negative load (a fall steeper than the decay allows)."""
    if not targets:
        raise ValueError("loads_for needs at least one CTL target")
    days = sorted(targets)
    if days[0] < start or days[-1] > end:
        raise ValueError(f"targets run {days[0]} to {days[-1]}, outside {start} to {end}")
    if (days[0] - start).days < SEED_WINDOW_DAYS:
        raise ValueError(
            f"the first target {days[0]} is {(days[0] - start).days} days after {start}; the seed "
            f"needs {SEED_WINDOW_DAYS} days of constant load before it"
        )
    if any(targets[d] <= 0 for d in days):
        raise ValueError("a geometric CTL path needs positive targets")

    path: dict[date, float] = {}
    first = targets[days[0]]
    for n in range((days[0] - start).days + 1):
        path[start + timedelta(days=n)] = first
    for a, b in zip(days, days[1:], strict=False):
        span = (b - a).days
        ratio = targets[b] / targets[a]
        for n in range(1, span + 1):
            path[a + timedelta(days=n)] = targets[b] if n == span else targets[a] * ratio ** (n / span)
    last = targets[days[-1]]
    for n in range(1, (end - days[-1]).days + 1):
        path[days[-1] + timedelta(days=n)] = last

    loads: dict[date, float] = {}
    previous = first
    for day in sorted(path):
        ctl = path[day]
        load = previous + (ctl - previous) / K_CTL
        if load < 0:
            raise ValueError(
                f"CTL {previous:.6g} to {ctl:.6g} on {day} needs a negative load ({load:.6g}); "
                f"the decay alone falls by {K_CTL:.4%} a day"
            )
        loads[day] = load
        previous = ctl
    return loads


def plant(
    conn: sqlite3.Connection,
    loads: Mapping[date, float],
    zone: ZoneInfo,
    gap_days: Iterable[date] = (),
    *,
    hour: int = 12,
) -> dict[date, Planted]:
    """One running session per day of ``loads`` at ``hour`` local time with that saved load,
    except on ``gap_days``; returns each planted day's session id and load."""
    skip = set(gap_days)
    planted: dict[date, Planted] = {}
    plant_load = _session_loads().plant_load
    for day in sorted(loads):
        if day in skip:
            continue
        start_time = datetime(day.year, day.month, day.day, hour, tzinfo=zone).isoformat()
        session = Session(
            session_id=derive_session_id(DEVICE, start_time),
            sport="running",
            source_vendor="garmin",
            start_time=start_time,
            source_device=DEVICE,
            timer_time_s=1800.0,
            quality_flags=[],
            summary={"duration_s": 1800.0},
            context=Context(),
        )
        db.persist(conn, session, [], [], {})
        plant_load(conn, session.session_id, value=float(loads[day]))
        planted[day] = Planted(session.session_id, float(loads[day]))
    return planted
