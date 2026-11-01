"""``tests/support/goal_seed.py``: the goal tests' seeding helpers, race sets and refused-input
table (F032 reference section 8's worked sets and the athlete's races).

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are (the suite runs under ``--import-mode=importlib``, so a sibling test module is not
importable by name).

Races are stored as ``test_riegel_b_route.py`` stores them: ``db.persist`` with synthetic 1 Hz flat
records at a constant speed (a final fractional record where the time is not whole), so F013's GAP
pace is the flat pace and the GAP-equivalent distance the fit reads is the distance run, untagged,
then tagged through F023's ``PUT /race-tags``. Every saved load is replaced through
``support/session_loads.py`` (or ``support/ctl_path.py`` where the chart's CTL must vary day by
day). ``NOW`` is the instant the goal tests freeze ``main._utcnow`` at; each test file installs its
own frozen-clock fixture.
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

from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.models import Context, Record, Session

SUPPORT = Path(__file__).parent
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
TODAY = NOW.date().isoformat()

HALF_M = 21097.5
MARATHON_M = 42195.0

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

# The athlete's races as (D m, T s), Chicago first: their six once the trail halves are untagged
# (Chicago at its GPS distance and five 5Ks; reference section 12).
_CHICAGO = (42515.75986034907, 11138.164)
_FIVE_5KS = (
    (4974.797208376185, 1232.761),
    (5019.445737328818, 1205.641),
    (4950.807486237603, 1206.331),
    (5047.4541635015075, 1233.371),
    (4978.700365474373, 1218.939),
)
ATHLETE_SIX = tuple(
    (datetime(2026, 8, 1 + 3 * n, 7, 0, tzinfo=UTC), *race) for n, race in enumerate((_CHICAGO, *_FIVE_5KS))
)


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


def _install_zone(isolated_data_dir: Path, monkeypatch, name: str) -> None:
    """Serve the isolated store under ``athlete_timezone`` ``name`` (``monkeypatch`` is pytest's)."""
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


# ---------------------------------------------------------------------------
# Refused goal bodies (AC2), as (id, raw body, the field the 422 names, the message or None)
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
