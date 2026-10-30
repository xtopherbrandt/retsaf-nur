"""``main._riegel_at`` builds b and the load chart for one request, and ``GET /state`` reads it.

The helper takes an open connection, the instant, the athlete's zone and the local today, reads the
loads, the threshold rows and the Riegel rows as the one ``db.read_state_inputs`` snapshot, builds
the chart from the loads (a chart that cannot be built is the route's 500, with the chart's own
message) and runs ``riegel_b.estimate`` over the Riegel rows against that chart's fitness lookup. It
returns values, not a one-element carrier: the estimate, the chart, the threshold rows and
unreadable list the threshold estimate still needs, and the Riegel rows.

The store is seeded as ``test_riegel_b_route.py`` seeds it: ``db.persist`` with synthetic 1 Hz flat
records, tagged through ``PUT /race-tags``, every saved load replaced through
``support/session_loads.py``, the clock frozen through ``main._utcnow``, under the autouse isolated
store (``athlete_timezone`` UTC).

The last test is a pin on ``_riegel_determinant`` alone, which no path through ``riegel_b.estimate``
reaches: the estimator withholds every figure when any is not finite, so the determinant's own
``_finite_or_none`` nulling of ``error`` and ``t_multiplier``, and its refusal of an infinite b,
are only visible on a hand-built ``RiegelEstimate``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import load_chart, riegel_b
from runcoach_api.models import Context, Record, Session

SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ZONE = ZoneInfo("UTC")

FIVE_K = (datetime(2026, 9, 6, 7, 0, tzinfo=UTC), 5000.0, 252.0)  # in the threshold band
TEN_K = (datetime(2026, 9, 13, 7, 0, tzinfo=UTC), 10000.0, 262.0)
HALF = (datetime(2026, 9, 20, 7, 0, tzinfo=UTC), 21000.0, 268.0)


def _load_support(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


session_loads = _load_support("session_loads")
ctl_path = _load_support("ctl_path")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Seeding, as test_riegel_b_route.py seeds
# ---------------------------------------------------------------------------


def _store(start: datetime, distance_m: float, pace: float) -> str:
    """An untagged running session of ``distance_m`` at ``pace`` s/km with 1 Hz flat records."""
    duration_s = round(distance_m * pace / 1000.0)
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        timer_time_s=float(duration_s),
        quality_flags=[],
        summary={"duration_s": float(duration_s), "distance_m": distance_m},
        context=Context(),
    )
    speed = 1000.0 / pace
    records = [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
        for t in range(duration_s + 1)
    ]
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {})
    finally:
        conn.close()
    return session.session_id


def _stored(session_id: str) -> sqlite3.Row:
    conn = db_module.get_connection()
    try:
        return conn.execute("SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()


def _put_tags(client: TestClient, session_ids: list[str]) -> None:
    """Connect's mark for each stored run: its start second in UTC, its distance 0.5 % long and its
    duration 0.5 % short (inside F023's 1 %)."""
    items = []
    for n, session_id in enumerate(session_ids):
        row = _stored(session_id)
        summary = json.loads(row["summary"])
        start = datetime.fromisoformat(row["start_time"]).astimezone(UTC)
        items.append(
            {
                "connect_activity_id": 5000 + int(hashlib.sha256(session_id.encode()).hexdigest()[:6], 16) + n,
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "distance_m": summary["distance_m"] * 1.005,
                "duration_s": summary["duration_s"] * 0.995,
                "race": True,
            }
        )
    response = client.put("/race-tags", json={"items": items})
    assert response.status_code == 200, response.text
    outcomes = [(i["outcome"], i["session_id"]) for i in response.json()["items"]]
    print(f"  race-tags {outcomes}")
    assert [s for _o, s in outcomes] == session_ids, outcomes


def _plant_every_load(value: float = 50.0) -> None:
    conn = db_module.get_connection()
    try:
        for (session_id,) in conn.execute("SELECT session_id FROM sessions").fetchall():
            session_loads.plant_load(conn, session_id, value=value)
    finally:
        conn.close()


def _races(client: TestClient, *races: tuple[datetime, float, float]) -> list[str]:
    ids = [_store(*race) for race in races]
    _put_tags(client, ids)
    _plant_every_load()
    return ids


def _riegel_at() -> main_module.RiegelAt:
    conn = db_module.get_connection()
    try:
        return main_module._riegel_at(conn, NOW, ZONE, NOW.date())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_riegel_at_returns_the_estimate_and_the_chart() -> None:
    """Three tagged races: the helper's estimate is the route's ``riegel_exponent`` entry (b, the
    error, the efforts in order), its chart ends on today, and it carries the three tagged races as
    the threshold rows the threshold estimate reads and as the Riegel rows."""
    with TestClient(app) as client:
        ids = _races(client, FIVE_K, TEN_K, HALF)
        response = client.get("/state")
        assert response.status_code == 200, response.text
        at = _riegel_at()
    served = response.json()["determinants"][1]
    assert served["name"] == "riegel_exponent"
    print(f"  helper b {at.estimate.value!r} error {at.estimate.error!r}; served {served['estimate']}")
    assert isinstance(at, main_module.RiegelAt)
    assert at.estimate.value == served["estimate"]["value"]
    assert at.estimate.error == served["error"]
    assert at.estimate.population_default is False and at.estimate.unavailable is None
    assert [e.session_id for e in at.estimate.efforts] == [e["session_id"] for e in served["efforts"]] == ids
    assert isinstance(at.chart, load_chart.LoadChart)
    assert at.chart.days[-1].date == NOW.date()
    assert sorted(row["session_id"] for row in at.threshold_rows) == sorted(ids)
    assert at.threshold_unreadable == []
    assert sorted(row["session_id"] for row in at.riegel_rows) == sorted(ids)


def test_riegel_at_builds_the_chart_to_the_local_today() -> None:
    """The chart runs to ``today``, the athlete's local date, not to the instant's UTC date: in
    Pacific/Auckland (UTC+13 in October) the frozen 2026-10-14 12:00 UTC is the local 10-15, so with
    a load planted on every local day to 10-15 the chart's last day is 10-15, and the fitness lookup
    for that day reads the local 10-14's CTL (a chart built to the UTC date would end on 10-14)."""
    zone = ZoneInfo("Pacific/Auckland")
    local_today = NOW.astimezone(zone).date()
    assert local_today == date(2026, 10, 15) and NOW.date() == date(2026, 10, 14)
    start = date(2026, 6, 1)
    loads = {start + timedelta(days=n): 30.0 + 4.0 * (n % 9) for n in range((local_today - start).days + 1)}
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        ctl_path.plant(conn, loads, zone)
        at = main_module._riegel_at(conn, NOW, zone, local_today)
    finally:
        conn.close()
    days = {day.date: day.ctl for day in at.chart.days}
    print(f"  chart last day {at.chart.days[-1].date}; local today {local_today}; UTC date {NOW.date()}")
    assert at.chart.days[-1].date == local_today, "the chart ends on the UTC date, not the athlete's local today"
    assert at.chart.days[-1].date != NOW.date()
    print(f"  CTL 10-13 {days[date(2026, 10, 13)]} 10-14 {days[date(2026, 10, 14)]} 10-15 {days[date(2026, 10, 15)]}")
    assert len({days[date(2026, 10, 13)], days[date(2026, 10, 14)], days[date(2026, 10, 15)]}) == 3
    before, _earlier, _provisional, _after_gap = main_module._riegel_fitness(at.chart)(local_today)
    assert before == days[date(2026, 10, 14)]


def test_riegel_at_reads_one_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The load rows, the threshold rows and the Riegel rows come from one ``db.read_state_inputs``
    call: the helper calls it once, hands back the very lists that call returned, and its chart is
    the one built from that call's load rows."""
    calls: list[db_module.StateInputs] = []
    real = db_module.read_state_inputs

    def counted(conn, workout_names, make_needs_features):
        inputs = real(conn, workout_names, make_needs_features)
        calls.append(inputs)
        return inputs

    monkeypatch.setattr(db_module, "read_state_inputs", counted)
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K, HALF)
        at = _riegel_at()
    print(f"  read_state_inputs calls {len(calls)}")
    assert len(calls) == 1
    (inputs,) = calls
    assert at.threshold_rows is inputs.threshold_rows
    assert at.threshold_unreadable is inputs.threshold_unreadable
    assert at.riegel_rows is inputs.riegel_rows
    assert at.chart == load_chart.build_chart(inputs.load_rows, ZONE, NOW.date())
    assert len(at.chart.days) > 0


def test_a_missing_load_row_is_still_a_500() -> None:
    """A stored session with no saved load row: the chart cannot be built, so the helper raises the
    route's ``HTTPException`` 500 with the chart's own message, and ``GET /state`` answers it."""
    session_id = _store(*FIVE_K)
    with TestClient(app) as client:
        conn = db_module.get_connection()
        try:
            conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
            conn.commit()
            rows = db_module.read_session_load_rows(conn)
        finally:
            conn.close()
        with pytest.raises(ValueError) as chart_error:
            load_chart.build_chart(rows, ZONE, NOW.date())
        with pytest.raises(HTTPException) as raised:
            _riegel_at()
        response = client.get("/state")
    print(f"  helper {raised.value.status_code} {raised.value.detail!r}; route {response.status_code}")
    assert raised.value.status_code == 500
    assert raised.value.detail == str(chart_error.value)
    assert session_id in raised.value.detail
    assert response.status_code == 500
    assert response.json() == {"detail": str(chart_error.value)}


# ---------------------------------------------------------------------------
# The determinant's own nulling, on a hand-built estimate
# ---------------------------------------------------------------------------


def _hand_built(**over: object) -> riegel_b.RiegelEstimate:
    """A fitted-looking estimate with no efforts and no exclusions, overridden by ``over``."""
    fields = {
        "as_of": NOW,
        "efforts": (),
        "excluded": (),
        "value": 1.06,
        "interval_low": 1.04,
        "interval_high": 1.08,
        "error": 0.01,
        "t_multiplier": 1.885618,
        "confidence": "high",
        "provisional": False,
        "population_default": False,
        "below_one": False,
        "unavailable": None,
    }
    fields.update(over)
    return riegel_b.RiegelEstimate(**fields)


def test_the_determinant_nulls_a_non_finite_error_and_t_itself() -> None:
    """An estimate whose ``error`` and ``t_multiplier`` are infinite (the estimator never builds one:
    it withholds them with ``estimate_not_finite``): the entry's ``error`` and ``t_multiplier`` are
    null by the determinant's own ``_finite_or_none``, not by the JSON serialiser."""
    for error, t in ((math.inf, math.inf), (-math.inf, math.nan), (math.nan, -math.inf)):
        entry = main_module._riegel_determinant(
            _hand_built(error=error, t_multiplier=t, unavailable="estimate_not_finite")
        )
        print(f"  error {error!r} t {t!r}: served error {entry.error!r} t {entry.t_multiplier!r}")
        assert entry.error is None and entry.t_multiplier is None
        assert entry.estimate is None and entry.unavailable == "estimate_not_finite"
    finite = main_module._riegel_determinant(_hand_built())
    assert (finite.error, finite.t_multiplier) == (0.01, 1.885618)


def test_the_determinant_refuses_an_infinite_b_with_no_reason() -> None:
    """An estimate carrying an infinite b (or interval end) with ``unavailable`` unset, which the
    estimator never builds: the contract's ``RiegelExponentValue.value`` is a non-null float, so the
    determinant nulls the figure and the entry is refused, rather than serving a value JSON cannot
    carry."""
    for over in ({"value": math.inf}, {"value": math.nan}, {"interval_low": -math.inf}, {"interval_high": math.inf}):
        with pytest.raises(ValidationError) as raised:
            main_module._riegel_determinant(_hand_built(**over))
        print(f"  {over}: {raised.value.error_count()} error(s), {raised.value.errors()[0]['loc']}")
        assert raised.value.errors()[0]["loc"][0] == "estimate"
