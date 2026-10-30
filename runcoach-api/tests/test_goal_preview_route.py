"""``POST /goal/preview`` serves the goal-race projection for a goal without storing it (F032 AC2 to
AC7 at the route, reference sections 2, 5 and 6).

Races are stored as ``test_riegel_b_route.py`` stores them: ``db.persist`` with synthetic 1 Hz flat
records at a constant speed (a final fractional record where the time is not whole), so F013's GAP
pace is the flat pace and the GAP-equivalent distance the fit reads is the distance run, untagged,
then tagged through F023's ``PUT /race-tags``. Every saved load is replaced through
``support/session_loads.py`` (or ``support/ctl_path.py`` where the chart's CTL must vary day by
day). The clock is frozen through ``main._utcnow``; every request runs under the autouse isolated
store, whose ``athlete_timezone`` is UTC unless a test installs its own ``AppConfig``.

The expected projections come from ``support/pace_target_oracle.py``, which imports nothing from
``runcoach_api`` (``test_pace_target_oracle.py`` pins that); the worked figures typed in here are the
F032 reference section 8's. Dates and zones are read through the athlete's zone, so this file is
named to the release gate's Linux run.
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
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session

SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
TODAY = NOW.date().isoformat()

HALF_M = 21097.5
MARATHON_M = 42195.0
DISTANCES = (5000.0, HALF_M, MARATHON_M, 60000.0)

# The reference section 8 sets as (start, D m, T s): E1 exactly three, E2 four tight, E3 is E2 with
# the marathon at weight 0.5, placed 1004 days before the frozen clock so F029's fade weighs it so.
_SEPT = [datetime(2026, 9, d, 7, 0, tzinfo=UTC) for d in (6, 13, 20, 27)]
E1 = ((_SEPT[0], 5000.0, 1200.0), (_SEPT[1], 10000.0, 2526.937004), (_SEPT[2], HALF_M, 5465.036808))
E2 = (
    (_SEPT[0], 5000.0, 1200.0),
    (_SEPT[1], 10000.0, 2502.0),
    (_SEPT[2], HALF_M, 5530.0),
    (_SEPT[3], MARATHON_M, 11610.0),
)
E3 = (E2[0], E2[1], E2[2], (NOW - timedelta(days=1004), MARATHON_M, 11610.0))
ONE_RACE = ((_SEPT[2], 21580.0, 5928.0),)
WORKED_SETS = {"E1": E1, "E2": E2, "E3": E3}


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
oracle = _load_support("pace_target_oracle")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _store(start: datetime, distance_m: float, time_s: float, *, name: str | None = None) -> str:
    """An untagged running session of ``distance_m`` in ``time_s`` with flat records at a constant
    speed: one per second and, when the time is not whole, a last one at ``time_s`` itself."""
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        timer_time_s=float(time_s),
        quality_flags=[],
        summary={"duration_s": float(time_s), "distance_m": distance_m},
        context=Context(),
        name=name,
    )
    speed = distance_m / time_s
    ticks = [float(t) for t in range(math.floor(time_s) + 1)]
    if ticks[-1] != time_s:
        ticks.append(float(time_s))
    records = [Record(t=t, distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0) for t in ticks]
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
        return conn.execute(
            "SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
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
                "connect_activity_id": 5000
                + int(hashlib.sha256(session_id.encode()).hexdigest()[:6], 16)
                + n,
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


def _races(client: TestClient, *races: tuple[datetime, float, float], loads: bool = True) -> list[str]:
    ids = [_store(*race) for race in races]
    _put_tags(client, ids)
    if loads:
        _plant_every_load()
    return ids


def _plant_chart(
    zone: ZoneInfo, end: date, *, start: date = date(2026, 6, 1), flat: float | None = None
) -> None:
    """A load chart from ``start`` to ``end`` in ``zone``: a CTL that varies day by day, or every day
    at ``flat`` so no race's fitness falls (E3's marathon is 1004 days old, and with no load between
    it and the 2026 races the chart would read the 2026 races after a long gap)."""
    loads = {
        start + timedelta(days=n): (30.0 + 4.0 * (n % 9) if flat is None else flat)
        for n in range((end - start).days + 1)
    }
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        ctl_path.plant(conn, loads, zone)
    finally:
        conn.close()


def _chart_days(client: TestClient) -> dict[date, dict]:
    response = client.get("/metrics/load")
    assert response.status_code == 200, response.text
    return {date.fromisoformat(d["date"]): d for d in response.json()["days"]}


def _snapshot() -> tuple[dict, str]:
    """Row counts of every table and the sha256 of every store file, the WAL included."""
    data_dir = db_module._load_config_cached().data_dir
    conn = sqlite3.connect(data_dir / "runcoach.db")
    try:
        tables = [
            n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        ]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()
    digest = hashlib.sha256()
    for path in sorted(data_dir.glob("runcoach.db*")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return counts, digest.hexdigest()


def _goal(distance_m: float, *, race_date: str | None = None, target_time_s: float | None = None) -> dict:
    race: dict = {"distance_m": distance_m}
    if race_date is not None:
        race["date"] = race_date
    body: dict = {"race": race}
    if target_time_s is not None:
        body["target_time_s"] = target_time_s
    return body


def _preview(client: TestClient, body: dict, as_of: str | None = None) -> dict:
    response = client.post("/goal/preview", json=body, params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    return response.json()


def _served_races(target: dict) -> list[tuple[float, float, float, str]]:
    return [
        (r["gap_equivalent_m"], r["duration_s"], r["weight"], r["start_time"])
        for r in target["provenance"]["races"]
    ]


# ---------------------------------------------------------------------------
# The fitted line (AC2 preview half, AC3)
# ---------------------------------------------------------------------------


def test_preview_serves_the_fitted_line() -> None:
    """E2 as tagged races, a half previewed: the reference section 8's 5542.806803 s to 1e-3, route
    ``fitted_line``, label ``high`` on both the time and the pace, and the response's shape."""
    with TestClient(app) as client:
        ids = _races(client, *E2)
        target = _preview(client, _goal(HALF_M))
    print(f"  served time {target['projected_time_s']}\n  confidence {target['confidence']}")
    assert target["unavailable"] is None
    assert target["route"] == "fitted_line"
    assert target["projected_time_s"]["value"] == pytest.approx(5542.806803, abs=1e-3)
    assert target["projected_time_s"]["interval"]["low"] == pytest.approx(5512.797559, abs=1e-3)
    assert target["projected_time_s"]["interval"]["high"] == pytest.approx(5572.979404, abs=1e-3)
    assert target["halfwidth"] == pytest.approx(0.005444, abs=1e-6)
    assert target["avg_pace_s_per_km"]["value"] == pytest.approx(262.723394, abs=1e-3)
    assert target["confidence"] == {
        "label": "high",
        "basis": "range",
        "uncertainty": target["halfwidth"],
        "reasons": [],
    }
    assert target["projected_time_s"]["confidence"] == target["avg_pace_s_per_km"]["confidence"] == "high"
    assert target["projected_time_s"]["provisional"] is False
    assert target["race"] == {"date": TODAY, "distance_m": HALF_M, "name": None}
    assert target["target_time_s"] is None and target["goal_time_gap"] is None
    assert [r["session_id"] for r in target["provenance"]["races"]] == ids
    assert target["provenance"]["b"] == pytest.approx(1.063891, abs=1e-6)
    assert target["provenance"]["n_eff"] == pytest.approx(4.0, abs=1e-9)
    assert target["leave_one_out"]["largest"]["session_id"] in ids
    assert target["effort_note"] is None and target["gap_to_goal"] is None
    assert target["assumptions"] == [
        "flat_course",
        "still_air",
        "cool_conditions",
        "no_economy_adjustment",
        "no_durability_adjustment",
        "projected_for_today_not_race_day",
        "watch_timer_time",
    ]
    assert target["parameters"]["population_b"] == 1.06 and target["parameters"]["tie_slack"] == 1e-9


@pytest.mark.parametrize("name", sorted(WORKED_SETS))
def test_preview_matches_the_worked_sets_at_four_distances(name: str) -> None:
    """AC3 at the route: the set's races are seeded as tagged races and served back as the oracle's
    triples (``D_eq`` the distance, ``T`` the time, E3's marathon at weight 0.5), and the previews
    at 5K, half, marathon and 60 km match ``support/pace_target_oracle.py`` (which imports nothing
    from the estimator) to 1e-3 s on the time, both interval ends and the pace, 1e-6 on the
    halfwidth, route ``fitted_line``."""
    races = WORKED_SETS[name]
    _plant_chart(ZoneInfo("UTC"), NOW.date(), start=date(2023, 12, 1), flat=50.0)
    with TestClient(app) as client:
        _races(client, *races)
        served = {d: _preview(client, _goal(d)) for d in DISTANCES}
    triples = _served_races(served[HALF_M])
    print(f"  {name} served races {triples}")
    by_start = sorted(races)  # F029 serves the races used in start-time order
    for (_start, d, t), (d_eq, time_s, weight, _s) in zip(by_start, triples, strict=True):
        assert d_eq == pytest.approx(d, abs=1e-6) and time_s == pytest.approx(t, abs=1e-6)
    assert [w for _d, _t, w, _s in triples] == ([0.5, 1.0, 1.0, 1.0] if name == "E3" else [1.0] * len(races))
    for distance_m, target in served.items():
        expected = oracle.projection(triples, distance_m)
        time = target["projected_time_s"]
        print(
            f"  {name} {distance_m:g} m: served {time['value']:.6f} [{time['interval']['low']:.6f}, "
            f"{time['interval']['high']:.6f}] hw {target['halfwidth']:.6f}; oracle {expected.time_s:.6f} "
            f"{expected.interval} hw {expected.halfwidth:.6f}"
        )
        assert expected.route == target["route"] == "fitted_line"
        assert time["value"] == pytest.approx(expected.time_s, abs=1e-3)
        assert time["interval"]["low"] == pytest.approx(expected.interval[0], abs=1e-3)
        assert time["interval"]["high"] == pytest.approx(expected.interval[1], abs=1e-3)
        assert target["avg_pace_s_per_km"]["value"] == pytest.approx(expected.pace_s_per_km, abs=1e-3)
        assert target["halfwidth"] == pytest.approx(expected.halfwidth, abs=1e-6)
    # The served n_eff is the races' own, not their count: E3's faded marathon gives 3.769231.
    n_eff = {"E1": 3.0, "E2": 4.0, "E3": 3.769231}[name]
    assert served[HALF_M]["provenance"]["n_eff"] == pytest.approx(n_eff, abs=1e-6)
    # The reference section 4 table's rows for these sets, read at the route, so the races and
    # F029's flags reach the label as the route passes them.
    for distance_m, (label, reasons) in SERVED_LABELS.get(name, {}).items():
        confidence = served[distance_m]["confidence"]
        print(f"  {name} {distance_m:g} m: served {confidence}")
        assert (confidence["label"], confidence["reasons"]) == (label, reasons), distance_m


# The reference section 4 table's served labels for the worked sets (a flat chart, so no fitness gap).
SERVED_LABELS = {
    "E1": {HALF_M: ("low", []), MARATHON_M: ("low", ["beyond_longest_race"])},
    "E2": {HALF_M: ("high", []), 60000.0: ("low", ["outside_calibrated_range"])},
}


def test_a_b_below_one_reads_low_at_the_route() -> None:
    """Four races whose times grow slower than their distances fit a b under 1, which F029 flags
    ``below_one``; the route passes the flag to the label, so the half reads ``low`` with reason
    ``below_one`` on the fitted line, whatever its range."""
    races = (
        (_SEPT[0], 5000.0, 1200.0),
        (_SEPT[1], 10000.0, 2350.0),
        (_SEPT[2], HALF_M, 4900.0),
        (_SEPT[3], MARATHON_M, 9700.0),
    )
    _plant_chart(ZoneInfo("UTC"), NOW.date(), flat=50.0)
    with TestClient(app) as client:
        _races(client, *races)
        target = _preview(client, _goal(HALF_M))
    print(f"  b {target['provenance']['b']} route {target['route']} confidence {target['confidence']}")
    assert target["provenance"]["b"] < 1
    assert target["route"] == "fitted_line"
    assert target["confidence"]["label"] == "low" and target["confidence"]["reasons"] == ["below_one"]
    assert target["confidence"]["uncertainty"] is not None and target["confidence"]["uncertainty"] <= 0.05


# ---------------------------------------------------------------------------
# Refusals and reference dates (AC2)
# ---------------------------------------------------------------------------

_LONG_NAME = "x" * 201
_NOT_MODELLED = "course profile and conditions are not modelled yet"
BAD_INPUTS = [
    ("distance_zero", '{"race": {"distance_m": 0}}', "distance_m", None),
    ("distance_negative", '{"race": {"distance_m": -21097.5}}', "distance_m", None),
    ("distance_nan", '{"race": {"distance_m": NaN}}', "distance_m", None),
    ("distance_inf", '{"race": {"distance_m": Infinity}}', "distance_m", None),
    ("distance_bool", '{"race": {"distance_m": true}}', "distance_m", None),
    ("distance_string", '{"race": {"distance_m": "21097.5"}}', "distance_m", None),
    ("distance_400_digits", '{"race": {"distance_m": ' + "9" * 400 + "}}", "distance_m", None),
    ("goal_time_zero", '{"race": {"distance_m": 21097.5}, "target_time_s": 0}', "target_time_s", None),
    ("goal_time_negative", '{"race": {"distance_m": 21097.5}, "target_time_s": -5400}', "target_time_s", None),
    ("goal_time_nan", '{"race": {"distance_m": 21097.5}, "target_time_s": NaN}', "target_time_s", None),
    ("goal_time_inf", '{"race": {"distance_m": 21097.5}, "target_time_s": -Infinity}', "target_time_s", None),
    ("date_timestamp", '{"race": {"distance_m": 21097.5, "date": 1792454400}}', "date", None),
    ("date_datetime", '{"race": {"distance_m": 21097.5, "date": "2026-11-02T00:00:00Z"}}', "date", None),
    ("date_not_a_day", '{"race": {"distance_m": 21097.5, "date": "2026-13-01"}}', "date", None),
    ("course_elevation", '{"race": {"distance_m": 21097.5, "course_elevation": {"points": []}}}', "course_elevation", _NOT_MODELLED),
    ("environment", '{"race": {"distance_m": 21097.5, "environment": {"temperature_c": 20}}}', "environment", _NOT_MODELLED),
    ("unknown_key", '{"race": {"distance_m": 21097.5}, "pace_s_per_km": 255}', "pace_s_per_km", None),
    ("unknown_race_key", '{"race": {"distance_m": 21097.5, "surface": "trail"}}', "surface", None),
    ("name_201", '{"race": {"distance_m": 21097.5, "name": "' + _LONG_NAME + '"}}', "name", None),
    ("name_lone_surrogate", '{"race": {"distance_m": 21097.5, "name": "\\ud800"}}', "name", None),
    ("name_reversed_surrogates", '{"race": {"distance_m": 21097.5, "name": "\\udfff\\ud800"}}', "name", None),
]  # fmt: skip


@pytest.mark.parametrize(
    ("body", "field", "message"), [case[1:] for case in BAD_INPUTS], ids=[c[0] for c in BAD_INPUTS]
)
def test_preview_refuses_each_bad_input(body: str, field: str, message: str | None) -> None:
    """Each AC2 input, sent as a raw body so the ``NaN`` and ``Infinity`` tokens and the 400-digit
    integer reach the parser as written, is a 422 whose error names the field; a course profile or
    conditions is refused with the reference's sentence; nothing is stored."""
    with TestClient(app) as client:
        _races(client, *E2)
        before = _snapshot()
        response = client.post("/goal/preview", content=body, headers={"content-type": "application/json"})
        after = _snapshot()
    print(f"  {response.status_code} {response.text[:300]}")
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    named = [e for e in errors if field in [str(part) for part in e["loc"]]]
    assert named, errors
    if message is not None:
        assert any(message in e["msg"] for e in named), named
    assert after[0] == before[0] and after[0]["goals"] == 0


def test_preview_as_of_backtests() -> None:
    """Preview's reference date is the local date of the instant used: with ``as_of`` 2025-10-01 a
    2025-11-02 race is accepted and a 2025-09-30 one is a 422 on ``race.date``; with no date the race
    is dated that day, and with no ``as_of`` the frozen clock's day. A race dated yesterday by the
    clock is refused, today accepted."""
    with TestClient(app) as client:
        _races(client, *E2)
        past = _preview(client, _goal(HALF_M, race_date="2025-11-02"), as_of="2025-10-01")
        refused = client.post(
            "/goal/preview", json=_goal(HALF_M, race_date="2025-09-30"), params={"as_of": "2025-10-01"}
        )
        undated = _preview(client, _goal(HALF_M), as_of="2025-10-01")
        today = _preview(client, _goal(HALF_M))
        yesterday = client.post("/goal/preview", json=_goal(HALF_M, race_date="2026-10-13"))
        dated_today = _preview(client, _goal(HALF_M, race_date=TODAY))
    print(f"  as_of 2025-10-01: {past['as_of']} race {past['race']} unavailable {past['unavailable']}")
    print(f"  refused {refused.status_code} {refused.text[:200]}; yesterday {yesterday.status_code}")
    assert past["race"]["date"] == "2025-11-02" and past["as_of"].startswith("2025-10-01")
    assert past["unavailable"] == "no_races"  # every E2 race is after that instant
    assert refused.status_code == 422
    assert any("date" in [str(p) for p in e["loc"]] for e in refused.json()["detail"]), refused.text
    assert undated["race"]["date"] == "2025-10-01"
    assert today["race"]["date"] == TODAY and today["route"] == "fitted_line"
    assert yesterday.status_code == 422
    assert dated_today["race"]["date"] == TODAY


def test_preview_refuses_a_malformed_as_of() -> None:
    """``as_of=not-a-date`` is a 422 (not a 500) whose detail names ``("query", "as_of")`` and
    carries the value sent; a basic-form date is refused the same way."""
    with TestClient(app) as client:
        _races(client, *E2)
        malformed = client.post("/goal/preview", json=_goal(HALF_M), params={"as_of": "not-a-date"})
        basic = client.post("/goal/preview", json=_goal(HALF_M), params={"as_of": "20261001"})
    print(f"  not-a-date {malformed.status_code} {malformed.text[:300]}\n  20261001 {basic.status_code}")
    for response, sent in ((malformed, "not-a-date"), (basic, "20261001")):
        assert response.status_code == 422, response.text
        [error] = response.json()["detail"]
        assert error["loc"] == ["query", "as_of"]
        assert error["input"] == sent
        assert error["type"] == "value_error"


def _install_zone(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=name,
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()


# (zone, as_of, expected): 200 with fitness_now null when the local date is 0001-01-01, whose day
# before cannot exist; 422 on as_of when the instant's local date falls before the calendar.
_FIRST_DAY_AS_OF = [
    ("UTC", "0001-01-01", 200),
    ("UTC", "0001-01-01T00:00:00+00:00", 200),
    ("UTC", "0001-01-02", 200),
    ("America/Los_Angeles", "0001-01-01", 200),
    ("America/Los_Angeles", "0001-01-01T12:00:00Z", 200),
    ("America/Los_Angeles", "0001-01-01T00:00:00+00:00", 422),
    ("Pacific/Auckland", "0001-01-01", 200),
    ("Pacific/Auckland", "0001-01-01T12:00:00Z", 200),
]


@pytest.mark.parametrize(("zone", "as_of", "expected"), _FIRST_DAY_AS_OF)
def test_an_as_of_on_the_calendar_s_first_day_is_never_a_500(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, zone: str, as_of: str, expected: int
) -> None:
    """``GET /state`` accepts every ``as_of`` here, so the goal routes never answer a 500 for one.
    When the instant's local date is 0001-01-01 the chart has no day before it, so ``fitness_now``
    is null and the projection is served; when the instant's local date would fall before
    0001-01-01 (a UTC instant on the first day, read west of UTC) the date cannot be formed and the
    request is a 422 on ``("query", "as_of")``, on preview and ``GET /goal`` alike."""
    _install_zone(isolated_data_dir, monkeypatch, zone)
    with TestClient(app) as client:
        _races(client, *E2)
        assert client.put("/goal", json=_goal(HALF_M, race_date="2027-01-01")).status_code == 200
        state = client.get("/state", params={"as_of": as_of})
        previewed = client.post("/goal/preview", json=_goal(HALF_M), params={"as_of": as_of})
        read = client.get("/goal", params={"as_of": as_of})
    print(f"  {zone} {as_of}: state {state.status_code}, preview {previewed.status_code} {previewed.text[:160]}")
    print(f"  get {read.status_code} {read.text[:160]}")
    assert state.status_code == 200
    for response in (previewed, read):
        assert response.status_code == expected, response.text
        if expected == 422:
            [error] = response.json()["detail"]
            assert error["loc"] == ["query", "as_of"] and error["input"] == as_of
    if expected == 200:
        target = previewed.json()
        assert target["unavailable"] == "no_races"
        assert read.json()["target"]["unavailable"] == "no_races" and read.json()["status"] == "active"
        local_day = datetime.fromisoformat(target["as_of"]).astimezone(ZoneInfo(zone)).date()
        if local_day == date(1, 1, 1):
            assert target["provenance"]["fitness_now"] is None
            assert read.json()["target"]["provenance"]["fitness_now"] is None


def test_preview_goal_time_whose_gap_overflows_is_projection_not_finite() -> None:
    """A goal time of 1e308 s is a finite float the schema accepts; over a 1 mm goal the projected
    time is a fraction of a second, so ``gap_fraction`` overflows to infinity. The goal time goes
    into the projection, so the preview is ``unavailable`` ``projection_not_finite`` with every
    figure null (reference section 3.3), not a served number beside an infinite gap. The same
    goal without the time serves a finite time, so the overflow is the gap's alone."""
    with TestClient(app) as client:
        _races(client, *E2)
        plain = _preview(client, _goal(0.001))
        overflow = _preview(client, _goal(0.001, target_time_s=1e308))
    print(f"  without a goal time {plain['projected_time_s']}\n  with 1e308 s: {overflow['unavailable']}")
    assert plain["unavailable"] is None and math.isfinite(plain["projected_time_s"]["value"])
    assert 1e308 / plain["projected_time_s"]["value"] == math.inf  # the gap this goal time makes
    assert overflow["unavailable"] == "projection_not_finite"
    assert overflow["target_time_s"] == 1e308
    assert overflow["route"] is None and overflow["projected_time_s"] is None
    assert overflow["avg_pace_s_per_km"] is None and overflow["halfwidth"] is None
    assert overflow["confidence"] is None and overflow["goal_time_gap"] is None
    assert overflow["leave_one_out"] is None
    assert [r["session_id"] for r in overflow["provenance"]["races"]] == [
        r["session_id"] for r in plain["provenance"]["races"]
    ]


@pytest.mark.parametrize(("name", "races"), [("one race", ONE_RACE), ("E2", E2)])
def test_preview_a_time_that_underflows_is_projection_not_finite(name: str, races: tuple) -> None:
    """A 1e-310 m goal is a positive finite number the schema accepts; its projected time underflows
    to 0.0 s on the closest-race route (one race) and the fitted line (E2) alike. With a goal time
    and without one, the preview is a 200 ``projection_not_finite`` with every figure null: not a
    500 from the gap or the leave-one-out shift dividing by 0 s, and not a served 0 s finish."""
    with TestClient(app) as client:
        _races(client, *races)
        responses = [
            client.post("/goal/preview", json=_goal(1e-310, target_time_s=goal_time)) for goal_time in (None, 5400.0)
        ]
    for response in responses:
        print(f"  {name}: {response.status_code} {response.text[:200]}")
        assert response.status_code == 200, response.text
        target = response.json()
        assert target["unavailable"] == "projection_not_finite"
        assert target["route"] is None and target["projected_time_s"] is None
        assert target["avg_pace_s_per_km"] is None and target["goal_time_gap"] is None
        assert target["confidence"] is None and target["leave_one_out"] is None


@pytest.mark.parametrize(
    ("variant", "timer_s", "gap_pace"),
    [("zero", 5e-324, 1e10), ("inf", None, 5e-324)],
    ids=["d-eq-zero", "d-eq-inf"],
)
def test_riegel_unavailable_is_served_with_the_goal(
    monkeypatch: pytest.MonkeyPatch, variant: str, timer_s: float | None, gap_pace: float
) -> None:
    """F029's ``estimate_not_finite``, reached as its own pinned sets reach it: the newest race's
    GAP-equivalent distance ``1000 * T / pace`` underflows to 0 (a 5e-324 s timer at 1e10 s/km) or
    overflows to infinity (a 5e-324 s/km pace). Neither is reachable from a FIT file, so the race's
    stored row is altered on its way into ``riegel_b.estimate``, which then decides on it as it
    would on a stored row. Preview and ``GET /goal`` are a 200 ``riegel_unavailable`` with every
    figure null, b, its error and its label null, the four races served and ``n_eff`` 4: never a
    500 from re-fitting races F029 has already refused."""
    real = main_module.riegel_b.estimate

    def altered(rows, *args, **kwargs):
        rows = [dict(row) for row in rows]
        newest = rows[-1]
        if timer_s is not None:
            newest["timer_time_s"] = timer_s
        newest["features"] = {**newest["features"], "gap_avg_pace_s_per_km": gap_pace}
        return real(rows, *args, **kwargs)

    with TestClient(app) as client:
        _races(client, *E2)
        assert client.put("/goal", json=_goal(HALF_M, race_date=TODAY, target_time_s=5400.0)).status_code == 200
        monkeypatch.setattr(main_module.riegel_b, "estimate", altered)
        previewed = client.post("/goal/preview", json=_goal(HALF_M, target_time_s=5400.0))
        read = client.get("/goal")
    print(f"  {variant}: preview {previewed.status_code} {previewed.text[:200]}\n  get {read.status_code}")
    assert previewed.status_code == 200, previewed.text
    assert read.status_code == 200, read.text
    for target in (previewed.json(), read.json()["target"]):
        assert target["unavailable"] == "riegel_unavailable"
        assert target["route"] is None and target["projected_time_s"] is None
        assert target["confidence"] is None and target["goal_time_gap"] is None
        provenance = target["provenance"]
        assert provenance["b"] is None and provenance["error"] is None and provenance["label"] is None
        assert provenance["population_default"] is False
        assert len(provenance["races"]) == 4 and provenance["n_eff"] == pytest.approx(4.0, abs=1e-9)


def test_preview_writes_nothing() -> None:
    """Row counts of every table and the hash of every store file are unchanged by a preview with
    and without ``as_of`` and a goal time."""
    with TestClient(app) as client:
        _races(client, *E2)
        conn = db_module.get_connection()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        before = _snapshot()
        assert _preview(client, _goal(HALF_M, target_time_s=5500.0))["route"] == "fitted_line"
        _preview(client, _goal(HALF_M), as_of="2026-09-15")
        after = _snapshot()
    print(f"  before {before}\n  after  {after}")
    assert before[0]["sessions"] == 4 and before[0]["goals"] == 0
    assert after == before


# ---------------------------------------------------------------------------
# Fitness and the newest race (AC6)
# ---------------------------------------------------------------------------


def test_preview_fitness_now_is_the_day_before() -> None:
    """With a chart whose CTL differs every day, ``fitness_now`` is the chart's CTL on the local day
    before the date used (not the date itself), ``fitness_at_races`` the weight-mean of the races'
    ``fitness_before`` (each the CTL on the day before its race), and the time is the same with
    and without a goal time."""
    _plant_chart(ZoneInfo("UTC"), NOW.date())
    with TestClient(app) as client:
        ids = _races(client, *E2, loads=False)
        conn = db_module.get_connection()
        try:
            for session_id in ids:
                session_loads.plant_load(conn, session_id, value=50.0)
        finally:
            conn.close()
        days = _chart_days(client)
        target = _preview(client, _goal(HALF_M))
        with_goal = _preview(client, _goal(HALF_M, target_time_s=5500.0))
    provenance = target["provenance"]
    before, on = days[NOW.date() - timedelta(days=1)]["ctl"], days[NOW.date()]["ctl"]
    print(f"  chart CTL day before {before}, on the day {on}; served fitness_now {provenance['fitness_now']}")
    assert before != on
    assert provenance["fitness_now"] == before
    races = provenance["races"]
    for race in races:
        race_day = datetime.fromisoformat(race["start_time"]).astimezone(UTC).date()
        assert race["fitness_before"] == days[race_day - timedelta(days=1)]["ctl"]
    expected_at_races = sum(r["weight"] * r["fitness_before"] for r in races) / sum(
        r["weight"] for r in races
    )
    print(f"  fitness_at_races served {provenance['fitness_at_races']} expected {expected_at_races}")
    assert provenance["fitness_at_races"] == pytest.approx(expected_at_races, abs=1e-9)
    assert target["confidence"]["reasons"] == []
    assert with_goal["projected_time_s"] == target["projected_time_s"]
    assert with_goal["goal_time_gap"]["gap_s"] == pytest.approx(-42.806803, abs=1e-3)
    assert with_goal["goal_time_gap"]["gap_fraction"] == pytest.approx(-0.007723, abs=1e-6)
    assert with_goal["goal_time_gap"]["within_range"] is False


def test_preview_fitness_now_reads_the_local_day_in_the_athlete_timezone(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In Pacific/Auckland (UTC+13 in October) the frozen 2026-10-14 12:00 UTC is the local 10-15,
    so the date used is 10-15 and ``fitness_now`` is the chart's CTL on the local 10-14, not on the
    UTC day before (10-13); the race is dated the local 10-15 and a 10-14 race is yesterday."""
    name = "Pacific/Auckland"
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=name,
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    zone = ZoneInfo(name)
    local_today = NOW.astimezone(zone).date()
    assert local_today == date(2026, 10, 15) and NOW.date() == date(2026, 10, 14)
    _plant_chart(zone, local_today)
    with TestClient(app) as client:
        ids = _races(client, *E2, loads=False)
        conn = db_module.get_connection()
        try:
            for session_id in ids:
                session_loads.plant_load(conn, session_id, value=50.0)
        finally:
            conn.close()
        days = _chart_days(client)
        target = _preview(client, _goal(HALF_M))
        yesterday = client.post("/goal/preview", json=_goal(HALF_M, race_date="2026-10-14"))
    ctl = {d: days[d]["ctl"] for d in (date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15))}
    print(f"  chart CTL by local day {ctl}; served fitness_now {target['provenance']['fitness_now']}")
    assert len(set(ctl.values())) == 3
    assert max(days) == local_today
    assert target["provenance"]["fitness_now"] == ctl[date(2026, 10, 14)]
    assert target["race"]["date"] == "2026-10-15" and target["timezone"] == name
    assert yesterday.status_code == 422


_LOWERED = {"high": "medium", "medium": "low", "low": "low"}


def test_preview_fitness_below_races_lowers_the_label_and_not_the_time() -> None:
    """A chart at CTL 50 through the 2026-09-20 half and empty afterwards: by the day before the
    reference date the CTL has decayed below 70 % of the races' weighted ``fitness_before``, so the
    gap holds, ``confidence.reasons`` is ``["fitness_below_races"]`` and the label is one step below
    the pure label. The served time is identical to 1e-9 with the gap and without it (the same
    store after the autumn days' loads are raised to 50) and matches the oracle: the gap lowers the
    label and never the time (AC6, reference section 5)."""
    zone = ZoneInfo("UTC")
    start, cut, end = date(2026, 6, 1), date(2026, 9, 20), NOW.date() - timedelta(days=1)
    loads = {start + timedelta(days=n): 50.0 for n in range((cut - start).days + 1)}
    loads.update({cut + timedelta(days=n): 0.0 for n in range(1, (end - cut).days + 1)})
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        planted = ctl_path.plant(conn, loads, zone)
    finally:
        conn.close()
    with TestClient(app) as client:
        ids = _races(client, *E2, loads=False)
        conn = db_module.get_connection()
        try:
            for session_id in ids:
                session_loads.plant_load(conn, session_id, value=50.0)
        finally:
            conn.close()
        days = _chart_days(client)
        lowered = _preview(client, _goal(HALF_M))
        conn = db_module.get_connection()
        try:
            for day, row in planted.items():
                if day > cut:
                    session_loads.plant_load(conn, row.session_id, value=50.0)
        finally:
            conn.close()
        raised_days = _chart_days(client)
        pure = _preview(client, _goal(HALF_M))
    provenance = lowered["provenance"]
    at_races = sum(r["weight"] * r["fitness_before"] for r in provenance["races"]) / sum(
        r["weight"] for r in provenance["races"]
    )
    print(
        f"  low chart: CTL on {end} {days[end]['ctl']:.3f}, fitness_now {provenance['fitness_now']:.3f}, "
        f"fitness_at_races {provenance['fitness_at_races']:.3f}, 70 % of it {0.7 * at_races:.3f}; "
        f"raised chart CTL on {end} {raised_days[end]['ctl']:.3f}, fitness_now {pure['provenance']['fitness_now']:.3f}"
    )
    print(f"  lowered {lowered['confidence']} time {lowered['projected_time_s']}")
    print(f"  pure    {pure['confidence']} time {pure['projected_time_s']}")
    assert provenance["fitness_now"] == days[end]["ctl"]
    assert provenance["fitness_at_races"] == pytest.approx(at_races, abs=1e-9)
    assert provenance["fitness_now"] < 0.7 * at_races
    assert pure["provenance"]["fitness_now"] >= 0.7 * at_races
    assert [r["session_id"] for r in pure["provenance"]["races"]] == ids
    assert [r["session_id"] for r in provenance["races"]] == ids
    assert lowered["route"] == pure["route"] == "fitted_line"
    assert pure["confidence"]["reasons"] == [] and pure["confidence"]["label"] != "low"
    assert lowered["confidence"]["reasons"] == ["fitness_below_races"]
    assert lowered["confidence"]["label"] == _LOWERED[pure["confidence"]["label"]]
    assert lowered["confidence"]["basis"] == pure["confidence"]["basis"]
    assert lowered["confidence"]["uncertainty"] == pure["confidence"]["uncertainty"]
    assert lowered["projected_time_s"]["confidence"] == lowered["confidence"]["label"]
    assert lowered["avg_pace_s_per_km"]["confidence"] == lowered["confidence"]["label"]
    for key in ("value", "interval"):
        assert lowered["projected_time_s"][key] == pytest.approx(pure["projected_time_s"][key], abs=1e-9)
        assert lowered["avg_pace_s_per_km"][key] == pytest.approx(pure["avg_pace_s_per_km"][key], abs=1e-9)
    assert lowered["halfwidth"] == pytest.approx(pure["halfwidth"], abs=1e-12)
    expected = oracle.projection(_served_races(lowered), HALF_M)
    assert lowered["projected_time_s"]["value"] == pytest.approx(expected.time_s, abs=1e-3)
    assert lowered["projected_time_s"]["value"] == pytest.approx(5542.806803, abs=1e-3)


def test_preview_newest_race_with_no_stored_name_serves_null() -> None:
    """When the newest race's session row has no name, ``newest_race.name`` is null, not the
    session id or any other stand-in, while the older named race keeps its name in ``races``."""
    with TestClient(app) as client:
        ids = [_store(*race, name=name) for race, name in zip(E2[:2], ("Spring 5K", None), strict=True)]
        _put_tags(client, ids)
        _plant_every_load()
        target = _preview(client, _goal(HALF_M))
    newest = target["provenance"]["newest_race"]
    print(f"  newest {newest}; races {[(r['session_id'], r['name']) for r in target['provenance']['races']]}")
    assert newest["session_id"] == ids[1]
    assert newest["name"] is None
    assert newest["start_time"].startswith("2026-09-13T07:00:00")
    assert [r["name"] for r in target["provenance"]["races"]] == ["Spring 5K", None]


def test_preview_names_the_newest_race() -> None:
    """``newest_race`` is the latest race used, with the session's stored name joined by id; a race
    whose file carried no name serves null, and each served race carries its name the same way."""
    with TestClient(app) as client:
        named = [
            _store(*race, name=name)
            for race, name in zip(E2[:3], ("Spring 5K", None, "Summer Half"), strict=True)
        ]
        _put_tags(client, named)
        _plant_every_load()
        target = _preview(client, _goal(HALF_M))
        marathon = _store(*E2[3], name="Autumn Marathon")
        _put_tags(client, [marathon])
        _plant_every_load()
        with_marathon = _preview(client, _goal(HALF_M))
    print(f"  newest {target['provenance']['newest_race']} then {with_marathon['provenance']['newest_race']}")
    newest = target["provenance"]["newest_race"]
    assert newest["session_id"] == named[2] and newest["name"] == "Summer Half"
    assert newest["start_time"].startswith("2026-09-20T07:00:00")
    assert [r["name"] for r in target["provenance"]["races"]] == ["Spring 5K", None, "Summer Half"]
    assert with_marathon["provenance"]["newest_race"] == {
        "session_id": marathon,
        "name": "Autumn Marathon",
        "start_time": with_marathon["provenance"]["races"][3]["start_time"],
    }


# ---------------------------------------------------------------------------
# The fallback and unavailable (AC4, AC7)
# ---------------------------------------------------------------------------


def test_preview_closest_race_and_unavailable() -> None:
    """One race (21580 m, 5928 s): a half is 5787.600 s to 1e-3, route ``closest_race``, interval
    null, ``provisional`` true, label ``low`` with reason ``population_default``, ``basis`` and
    ``uncertainty`` null, ``leave_one_out`` null, and a goal time's ``within_range`` null. No race:
    ``unavailable`` ``no_races`` with every figure null, the assumptions still served."""
    with TestClient(app) as client:
        none = _preview(client, _goal(HALF_M, target_time_s=5400.0))
        _races(client, *ONE_RACE)
        one = _preview(client, _goal(HALF_M))
        with_goal = _preview(client, _goal(HALF_M, target_time_s=5400.0))
    print(
        f"  one race {one['projected_time_s']} {one['confidence']}\n  none {none['unavailable']} {none['projected_time_s']}"
    )
    assert one["route"] == "closest_race" and one["unavailable"] is None
    assert one["projected_time_s"]["value"] == pytest.approx(5787.600, abs=1e-3)
    assert one["projected_time_s"]["interval"] is None and one["avg_pace_s_per_km"]["interval"] is None
    assert one["projected_time_s"]["provisional"] is True and one["avg_pace_s_per_km"]["provisional"] is True
    assert one["projected_time_s"]["confidence"] == one["avg_pace_s_per_km"]["confidence"] == "low"
    assert one["halfwidth"] is None
    assert one["confidence"] == {
        "label": "low",
        "basis": None,
        "uncertainty": None,
        "reasons": ["population_default"],
    }
    assert one["leave_one_out"] is None
    assert one["provenance"]["population_default"] is True and one["provenance"]["b"] == 1.06
    assert one["provenance"]["newest_race"]["session_id"] == one["provenance"]["races"][0]["session_id"]
    assert with_goal["goal_time_gap"]["gap_s"] == pytest.approx(5400.0 - 5787.600207, abs=1e-3)
    assert with_goal["goal_time_gap"]["within_range"] is None
    assert none["unavailable"] == "no_races"
    assert none["route"] is None and none["projected_time_s"] is None and none["avg_pace_s_per_km"] is None
    assert none["halfwidth"] is None and none["confidence"] is None and none["goal_time_gap"] is None
    assert none["leave_one_out"] is None
    assert none["provenance"]["races"] == [] and none["provenance"]["newest_race"] is None
    assert none["provenance"]["fitness_at_races"] is None
    assert none["target_time_s"] == 5400.0
    assert none["assumptions"] == one["assumptions"] and len(none["assumptions"]) == 7
