"""``PUT /goal`` stores the goal race and ``GET /goal`` serves it with its projection (F032 AC1,
AC2's PUT half, reference sections 2 and 6).

The seeding helpers and the refused-input table are ``test_goal_preview_route.py``'s, loaded by
path (the suite runs under ``--import-mode=importlib``, so a sibling test module is not importable
by name): races are stored untagged as flat constant-speed runs, tagged through ``PUT /race-tags``
and given a saved load each. The clock is frozen through ``main._utcnow``; every request runs under
the autouse isolated store, whose ``athlete_timezone`` is UTC unless a test installs its own
``AppConfig``. The race date is judged in the athlete's zone, so this file is named to the release
gate's Linux run.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.main import app

HERE = Path(__file__).parent


def _load_by_path(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


preview = _load_by_path("goal_preview_route_helpers", HERE / "test_goal_preview_route.py")

NOW: datetime = preview.NOW
TODAY: str = preview.TODAY
HALF_M: float = preview.HALF_M
E2 = preview.E2
BAD_INPUTS = preview.BAD_INPUTS
_races = preview._races
_store = preview._store
_goal = preview._goal
_snapshot = preview._snapshot


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


def _goal_rows() -> list[dict]:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM goals ORDER BY id").fetchall()]
    finally:
        conn.close()


def _put(client: TestClient, body: dict) -> dict:
    response = client.put("/goal", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _get(client: TestClient, as_of: str | None = None) -> dict:
    response = client.get("/goal", params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    return response.json()


def _names_field(errors: list[dict], field: str) -> list[dict]:
    return [e for e in errors if field in [str(part) for part in e["loc"]]]


def _auckland(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install an ``AppConfig`` whose ``athlete_timezone`` is Pacific/Auckland (UTC+13 in October,
    so the frozen 2026-10-14 12:00 UTC is the local 2026-10-15 01:00) and return the zone name."""
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
    return name


# ---------------------------------------------------------------------------
# AC1: set, read, replace
# ---------------------------------------------------------------------------


def test_no_goal_is_a_404() -> None:
    """A store with no goal: ``GET /goal`` is 404 with ``{"detail": "no_goal"}``, the detail the
    CLI tells apart from an API without the route (``Not Found``)."""
    with TestClient(app) as client:
        response = client.get("/goal")
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 404
    assert response.json() == {"detail": "no_goal"}


def test_put_serves_the_active_goal() -> None:
    """A half dated today with a goal time of 5400 s: 200 with the goal ``active``, its ``id`` the
    row id as a string, ``created_at`` the clock's instant, and ``target`` the same projection the
    preview serves for the same body; ``GET /goal`` then serves the same goal."""
    with TestClient(app) as client:
        _races(client, *E2)
        body = _goal(HALF_M, race_date=TODAY, target_time_s=5400.0)
        previewed = preview._preview(client, body)
        goal = _put(client, body)
        read = _get(client)
    rows = _goal_rows()
    print(f"  put {goal['id']} {goal['status']} {goal['created_at']} route {goal['target']['route']}")
    print(f"  rows {rows}")
    assert goal["status"] == "active"
    assert goal["race"] == {"date": TODAY, "distance_m": HALF_M, "name": None}
    assert goal["target_time_s"] == 5400.0
    assert len(rows) == 1 and goal["id"] == str(rows[0]["id"]) and isinstance(goal["id"], str)
    assert rows[0]["superseded_at"] is None and rows[0]["created_at"] == NOW.isoformat()
    assert datetime.fromisoformat(goal["created_at"]) == NOW
    assert goal["target"]["route"] == "fitted_line"
    assert goal["target"]["projected_time_s"]["value"] == pytest.approx(5542.806803, abs=1e-3)
    assert goal["target"]["goal_time_gap"]["gap_s"] == pytest.approx(5400.0 - 5542.806803, abs=1e-3)
    assert goal["target"] == previewed
    assert read == goal


def test_a_second_put_supersedes_the_first() -> None:
    """Two PUTs: the second goal is served; the first row keeps ``superseded_at`` set to the clock's
    instant and is not served; nothing is deleted."""
    with TestClient(app) as client:
        _races(client, *E2)
        first = _put(client, _goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        second = _put(client, _goal(42195.0, race_date="2027-04-18", target_time_s=12600.0))
        read = _get(client)
    rows = _goal_rows()
    print(f"  first {first['id']} second {second['id']} served {read['id']}; rows {rows}")
    assert len(rows) == 2
    assert rows[0]["id"] == int(first["id"]) and rows[0]["superseded_at"] == NOW.isoformat()
    assert rows[1]["id"] == int(second["id"]) and rows[1]["superseded_at"] is None
    assert read["id"] == second["id"] and read["race"]["distance_m"] == 42195.0
    assert read["race"]["date"] == "2027-04-18" and read["status"] == "active"


def test_past_race_date_reads_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the clock moved two days past the race date, ``GET /goal`` serves the same row as
    ``completed`` (derived on read: the stored row is unchanged) with a projection for that
    instant; a goal whose race is still ahead stays ``active``."""
    later = NOW + timedelta(days=2)
    with TestClient(app) as client:
        _races(client, *E2)
        goal = _put(client, _goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        monkeypatch.setattr(main_module, "_utcnow", lambda: later)
        read = _get(client)
        _put(client, _goal(HALF_M, race_date=(later + timedelta(days=7)).date().isoformat()))
        ahead = _get(client)
    rows = _goal_rows()
    print(f"  {read['status']} as_of {read['target']['as_of']} route {read['target']['route']}; rows {rows}")
    assert read["id"] == goal["id"] and read["status"] == "completed"
    assert datetime.fromisoformat(read["target"]["as_of"]) == later
    assert read["target"]["route"] == "fitted_line" and read["target"]["projected_time_s"] is not None
    assert rows[0]["race_date"] == TODAY and rows[0]["created_at"] == NOW.isoformat()
    assert ahead["status"] == "active"


def test_get_writes_nothing() -> None:
    """Row counts of every table and the hash of every store file are unchanged by ``GET /goal``
    with and without ``as_of``."""
    with TestClient(app) as client:
        _races(client, *E2)
        _put(client, _goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        conn = db_module.get_connection()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        before = _snapshot()
        assert _get(client)["status"] == "active"
        _get(client, as_of="2026-09-15")
        after = _snapshot()
    print(f"  before {before}\n  after  {after}")
    assert before[0]["goals"] == 1 and before[0]["sessions"] == 4
    assert after == before


def test_get_as_of_moves_the_projection_only() -> None:
    """``GET /goal?as_of=2026-09-15`` serves the same goal row (id, race, goal time, created_at)
    with a projection at that instant (two of E2's four races had been run), ``active`` since the
    race is still ahead of that date; a future ``as_of`` is a 422 on the query parameter."""
    with TestClient(app) as client:
        _races(client, *E2)
        goal = _put(client, _goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        past = _get(client, as_of="2026-09-15")
        future = client.get("/goal", params={"as_of": "2026-10-15"})
    print(
        f"  as_of {past['target']['as_of']} races {len(past['target']['provenance']['races'])} route {past['target']['route']}"
    )
    print(f"  future {future.status_code} {future.text[:200]}")
    for key in ("id", "race", "target_time_s", "created_at"):
        assert past[key] == goal[key], key
    assert past["status"] == "active"
    assert past["target"]["as_of"].startswith("2026-09-15")
    assert len(past["target"]["provenance"]["races"]) == 2 < len(goal["target"]["provenance"]["races"]) == 4
    assert past["target"]["projected_time_s"] != goal["target"]["projected_time_s"]
    assert future.status_code == 422
    assert _names_field(future.json()["detail"], "as_of"), future.text


def test_get_status_is_derived_from_the_as_of_instant(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``status`` is read against the local date of the instant used, not the clock's. In
    Pacific/Auckland a goal dated the local 2026-10-15 is stored; the clock then moves three days
    on, so the same row is ``completed`` with no ``as_of`` but ``active`` for every ``as_of``
    whose local date is on or before the race date: the day before, the race date itself (its last
    local instant), and an instant whose UTC date is 2026-10-14 but whose Auckland date is the race
    date. The day after, and an instant whose UTC date is the day after but whose Auckland date is
    two days after, read ``completed``. The projection is at the instant used in every case."""
    name = _auckland(isolated_data_dir, monkeypatch)
    zone = ZoneInfo(name)
    race_day = date(2026, 10, 15)
    later = NOW + timedelta(days=3)
    assert later.astimezone(zone).date() == date(2026, 10, 18)
    with TestClient(app) as client:
        _races(client, *E2)
        goal = _put(client, _goal(HALF_M, race_date=race_day.isoformat(), target_time_s=5400.0))
        monkeypatch.setattr(main_module, "_utcnow", lambda: later)
        at_now = _get(client)
        reads = {
            as_of: _get(client, as_of=as_of)
            for as_of in (
                "2026-10-14",
                "2026-10-15",
                "2026-10-15T00:30:00+13:00",
                "2026-10-16",
                "2026-10-16T13:00:00Z",
            )
        }
    rows = _goal_rows()
    print(f"  now {at_now['target']['as_of']} -> {at_now['status']}")
    for as_of, read in reads.items():
        print(f"  as_of {as_of} -> {read['target']['as_of']} {read['status']}")
    assert goal["status"] == "active" and len(rows) == 1 and rows[0]["race_date"] == race_day.isoformat()
    assert at_now["id"] == goal["id"] and at_now["status"] == "completed"
    assert datetime.fromisoformat(at_now["target"]["as_of"]) == later
    for as_of, read in reads.items():
        assert read["id"] == goal["id"], as_of
        instant = datetime.fromisoformat(read["target"]["as_of"])
        if as_of == "2026-10-15T00:30:00+13:00":
            assert instant == datetime(2026, 10, 14, 11, 30, tzinfo=UTC), as_of
        elif as_of == "2026-10-16T13:00:00Z":
            assert instant == datetime(2026, 10, 16, 13, 0, tzinfo=UTC), as_of
        else:
            assert instant.astimezone(zone).date() == date.fromisoformat(as_of), as_of
        expected = "completed" if instant.astimezone(zone).date() > race_day else "active"
        assert read["status"] == expected, f"{as_of}: {read['status']} (local {instant.astimezone(zone)})"
    assert [reads[k]["status"] for k in reads] == ["active", "active", "active", "completed", "completed"]
    assert reads["2026-10-15"]["target"]["as_of"] != at_now["target"]["as_of"]


def test_goal_without_races_is_served_unavailable() -> None:
    """No race tagged: the goal is stored and served with ``unavailable`` ``no_races``, every
    figure null and the assumptions listed, on PUT and on GET alike."""
    with TestClient(app) as client:
        goal = _put(client, _goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        read = _get(client)
    target = goal["target"]
    print(f"  {goal['status']} unavailable {target['unavailable']} time {target['projected_time_s']}")
    assert goal["status"] == "active" and len(_goal_rows()) == 1
    assert target["unavailable"] == "no_races"
    assert (
        target["route"] is None and target["projected_time_s"] is None and target["avg_pace_s_per_km"] is None
    )
    assert target["confidence"] is None and target["goal_time_gap"] is None and target["halfwidth"] is None
    assert target["provenance"]["races"] == [] and len(target["assumptions"]) == 7
    assert read["target"]["unavailable"] == "no_races" and read["id"] == goal["id"]


# ---------------------------------------------------------------------------
# AC2's PUT half: refusals and the reference date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field", "message"), [case[1:] for case in BAD_INPUTS], ids=[c[0] for c in BAD_INPUTS]
)
def test_put_refuses_each_bad_input(body: str, field: str, message: str | None) -> None:
    """Each AC2 input through ``PUT /goal``, sent as a raw body so the ``NaN`` and ``Infinity``
    tokens and the 400-digit integer reach the parser as written: a 422 naming the field, the
    reference's sentence for a course profile or conditions, and zero goal rows afterwards."""
    with TestClient(app) as client:
        _races(client, *E2)
        before = _snapshot()
        response = client.put("/goal", content=body, headers={"content-type": "application/json"})
        after = _snapshot()
    print(f"  {response.status_code} {response.text[:300]}")
    assert response.status_code == 422, response.text
    named = _names_field(response.json()["detail"], field)
    assert named, response.json()["detail"]
    if message is not None:
        assert any(message in e["msg"] for e in named), named
    assert after[0] == before[0] and after[0]["goals"] == 0 and _goal_rows() == []


def test_put_refuses_yesterday_and_accepts_today(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In Pacific/Auckland (UTC+13 in October) the frozen 2026-10-14 12:00 UTC is the local
    2026-10-15: a race dated 2026-10-14 (today in UTC, yesterday for the athlete) is a 422 on
    ``race.date`` and stores nothing; 2026-10-15 is accepted."""
    name = _auckland(isolated_data_dir, monkeypatch)
    assert NOW.astimezone(ZoneInfo(name)).date() == date(2026, 10, 15) and NOW.date() == date(2026, 10, 14)
    with TestClient(app) as client:
        _races(client, *E2)
        yesterday = client.put("/goal", json=_goal(HALF_M, race_date="2026-10-14"))
        rows_after_refusal = _goal_rows()
        today = _put(client, _goal(HALF_M, race_date="2026-10-15"))
    print(
        f"  yesterday {yesterday.status_code} {yesterday.text[:200]}; today {today['status']} {today['race']}"
    )
    assert yesterday.status_code == 422
    assert _names_field(yesterday.json()["detail"], "date"), yesterday.text
    assert rows_after_refusal == []
    assert today["race"]["date"] == "2026-10-15" and today["target"]["timezone"] == name
    assert len(_goal_rows()) == 1


def test_a_raising_projection_stores_nothing() -> None:
    """A running session whose saved load row is deleted: the chart cannot be built, the PUT is the
    chart's 500, and no goal row is written (the projection is computed before the write)."""
    session_id = _store(*E2[0])
    with TestClient(app) as client:
        conn = db_module.get_connection()
        try:
            conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
            conn.commit()
        finally:
            conn.close()
        response = client.put("/goal", json=_goal(HALF_M, race_date=TODAY, target_time_s=5400.0))
        read = client.get("/goal")
    print(f"  {response.status_code} {response.text[:200]}; get {read.status_code}")
    assert response.status_code == 500
    assert session_id in response.json()["detail"]
    assert _goal_rows() == []
    assert read.status_code == 404 and read.json() == {"detail": "no_goal"}
