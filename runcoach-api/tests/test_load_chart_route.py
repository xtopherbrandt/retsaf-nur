"""``GET /metrics/load`` and its contract entry (F018 AC1, AC3, AC6 and AC7 at the API; AC8's contract bullet).

The route resolves the zone and today once per request, reads every session's saved load in one
statement (``db.read_session_load_rows``), hands the rows to the pure ``metrics.load_chart`` and
slices ``from``..``to`` off the result. Everything here is driven through ``TestClient`` against
the autouse isolated store with ``main._utcnow`` frozen and the zone set through ``configure``.

**The oracle is independent of the application.** ``oracle_chart`` buckets each stored
``start_time`` with ``zoneinfo`` itself, sums the saved loads per local date, seeds from the first
42 day loads and runs the reference's recursion (F018 reference, "The curves" and "The start
value"); it imports nothing from ``runcoach_api`` and reads the saved rows with its own SQL. The
real-fixture row (AC1) uploads every fixture with run proof ``yes`` plus the cool-down walk, which
shares a local date with ``strap_run_hrv`` so same-day summing is exercised on real data.

Day loads are set through ``tests/support/session_loads.py``'s ``plant_load`` where a row needs a
chosen counted value or uncounted reason; a cycling session and a declared capture get their
exclusion reasons from ``db.persist`` itself.

**The contract walk** is ``tests/support/contract_walk.py``'s: every component reachable from
``getLoadChart`` compared property by property, recursing into array ``items``, and every F018
component asserted visited.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Session

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
CHART_PATH = "/metrics/load"
PROFILE = "HRV Snapshot"
TOLERANCE = 1e-9

#: Every fixture with run proof ``yes`` in ``fixtures/README.md``, plus the cool-down walk
#: (``walk only``; a Run-profile walk on the same local date as ``strap_run_hrv``).
RUN_FIXTURES = (
    "sample_run.fit",
    "dev_fields_run.fit",
    "wrist_ppg_run.fit",
    "strap_run_hrv.fit",
    "hilly_run_8k_fr945.fit",
    "hilly_long_run_17k_fr945.fit",
)
WALK_FIXTURE = "strap_cool_down_walk.fit"
EXCLUSION_REASONS = {"sport_not_running", "declared_capture"}
#: F017's closed reason enum minus the two exclusion reasons: what an uncounted run may carry.
CHART_REASONS = (
    "no_hr",
    "missing_anchor",
    "order_conflict",
    "avg_hr_below_resting",
    "avg_hr_above_max",
    "wrist_hr_threshold_unknown",
    "wrist_hr_at_threshold",
    "not_representable",
    "no_threshold_hr",
    "threshold_order_conflict",
)
DAY_KEYS = {
    "date",
    "load",
    "ctl",
    "atl",
    "tsb",
    "provisional",
    "marked",
    "uncounted_in_window",
    "counted",
    "uncounted",
    "excluded",
}
NEW_COMPONENTS = {
    "LoadChart",
    "LoadChartSeed",
    "LoadChartDay",
    "LoadChartCounted",
    "LoadChartUncounted",
    "LoadChartExcluded",
    "LoadChartReason",
}

ORACLE_K_CTL = 1 - math.exp(-1 / 42)
ORACLE_K_ATL = 1 - math.exp(-1 / 7)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


session_loads = _load_support("session_loads")


# ---------------------------------------------------------------------------
# configuration, clock and store helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def configure(isolated_data_dir, monkeypatch):
    """``configure(zone)``: re-install the isolated ``AppConfig`` with a different
    ``athlete_timezone`` and clear ``db._load_config_cached`` (``test_hrv_trend_endpoint.py``'s
    fixture). Without the clear the route keeps reading the cached config and the zone is a
    silent no-op."""

    def _configure(zone: str) -> AppConfig:
        config = AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=isolated_data_dir,
            resting_hrv_profile_names=[PROFILE],
            athlete_timezone=zone,
        )
        monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
        db_module._load_config_cached.cache_clear()
        return config

    return _configure


@pytest.fixture
def freeze(monkeypatch):
    """``freeze(instant)``: pin ``main._utcnow`` to an aware UTC instant, the route's one clock read."""

    def _freeze(instant: datetime) -> None:
        assert instant.tzinfo is not None
        monkeypatch.setattr(main_module, "_utcnow", lambda: instant)

    return _freeze


def utc(day: date, hh: int = 8, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC)


def at(day: date, hh: int = 8, mm: int = 0, zone: str = "UTC") -> str:
    """The stored ``start_time`` (aware UTC, ISO) of local wall time ``hh:mm`` on ``day`` in ``zone``."""
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(zone))
    return local.astimezone(UTC).isoformat()


def _session(start_time: str, device: str = "fr945", sport: str = "running", activity_tag: str | None = None) -> Session:
    return Session(
        session_id=derive_session_id(device, start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device=device,
        activity_tag=activity_tag,
    )


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """A connection to the isolated store, opened outside the app and closed on exit."""
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _plant(session_id: str, value: float | None = None, reason: str | None = None) -> None:
    with _connection() as conn:
        session_loads.plant_load(conn, session_id, value=value, reason=reason)


def _saved_rows() -> list[dict]:
    """Every stored session with its saved load, read with this file's own SQL."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT s.session_id, s.start_time, l.load_value, l.load_reason FROM sessions s "
            "LEFT JOIN session_loads l USING (session_id) ORDER BY s.start_time, s.session_id"
        ).fetchall()
    return [dict(row) for row in rows]


def _schema_and_rows() -> tuple[list[tuple], dict[str, list[tuple]]]:
    """``sqlite_master`` and every table's rows, read outside the app."""
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}
    return schema, rows


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def get(client: TestClient, **params):
    return client.get(CHART_PATH, params={k: v for k, v in params.items() if v is not None})


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# the oracle: the reference's formulas over day loads, bucketed with zoneinfo
# ---------------------------------------------------------------------------


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


def local_date(start_time: str, zone: str) -> date:
    return datetime.fromisoformat(start_time).astimezone(ZoneInfo(zone)).date()


def oracle_chart(rows: list[dict], zone: str, today: date) -> dict:
    """``first_day``, ``seed`` and ``{date: (load, ctl, atl, tsb)}`` from the saved rows alone."""
    running = [r for r in rows if r["load_value"] is not None or r["load_reason"] not in EXCLUSION_REASONS]
    if not running:
        return {"first_day": None, "seed": None, "days": {}}
    first_day = min(local_date(r["start_time"], zone) for r in running)
    if first_day > today:
        return {"first_day": first_day, "seed": None, "days": {}}
    loads_by_date: dict[date, float] = defaultdict(float)
    for r in rows:
        if r["load_value"] is not None:
            loads_by_date[local_date(r["start_time"], zone)] += r["load_value"]
    dates = [first_day + timedelta(days=n) for n in range((today - first_day).days + 1)]
    day_loads = [loads_by_date.get(d, 0.0) for d in dates]
    seed, series = oracle_series(day_loads)
    days = {d: (load, *values) for d, load, values in zip(dates, day_loads, series, strict=True)}
    return {"first_day": first_day, "seed": seed, "days": days}


def assert_matches_oracle(body: dict, rows: list[dict], zone: str, today: date, show: set[date] = frozenset()) -> None:
    """Every served day against the oracle to 1e-9; the ``show`` days printed side by side."""
    want = oracle_chart(rows, zone, today)
    assert body["first_day"] == (want["first_day"].isoformat() if want["first_day"] else None)
    if want["seed"] is None:
        assert body["seed"] is None
    else:
        assert body["seed"]["value"] == pytest.approx(want["seed"], abs=TOLERANCE)
    assert [d["date"] for d in body["days"]] == [d.isoformat() for d in want["days"]]
    for served in body["days"]:
        d = date.fromisoformat(served["date"])
        load, ctl, atl, tsb = want["days"][d]
        if d in show:
            print(
                f"  {d}  load {served['load']:9.4f} | {load:9.4f}   ctl {served['ctl']:8.4f} | {ctl:8.4f}"
                f"   atl {served['atl']:8.4f} | {atl:8.4f}   tsb {served['tsb']:8.4f} | {tsb:8.4f}"
            )
        assert served["load"] == pytest.approx(load, abs=TOLERANCE), d
        assert served["ctl"] == pytest.approx(ctl, abs=TOLERANCE), d
        assert served["atl"] == pytest.approx(atl, abs=TOLERANCE), d
        assert served["tsb"] == pytest.approx(tsb, abs=TOLERANCE), d


def _listed(body: dict) -> dict[str, list[str]]:
    """``session_id -> [the lists it appears in]`` over every served day."""
    seen: dict[str, list[str]] = defaultdict(list)
    for served in body["days"]:
        for group in ("counted", "uncounted", "excluded"):
            for entry in served[group]:
                seen[entry["session_id"]].append(f"{served['date']}/{group}")
    return seen


# ---------------------------------------------------------------------------
# AC1: the chart from real runs
# ---------------------------------------------------------------------------


def test_the_fixture_chart_matches_the_oracle(configure, freeze) -> None:
    """Every run-proof fixture plus the cool-down walk uploaded; each day's load is the sum of
    the saved loads on that Vancouver date and the curves equal the oracle to 1e-9. The history
    starts at ``dev_fields_run`` (2024-07-09), so the full series is asserted but only the days
    holding a session and the last day are printed."""
    zone = "America/Vancouver"
    configure(zone)
    today = date(2026, 9, 30)
    freeze(utc(today, 20))
    with TestClient(app) as client:
        uploaded = {name: _upload(client, name) for name in (*RUN_FIXTURES, WALK_FIXTURE)}
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = _saved_rows()
    assert {r["session_id"] for r in rows} == set(uploaded.values())
    session_days = {local_date(r["start_time"], zone) for r in rows}
    counted_values = {r["session_id"]: r["load_value"] for r in rows if r["load_value"] is not None}
    print(f"\n  {len(rows)} sessions, {len(counted_values)} counted; served {len(body['days'])} days in {zone}")
    print("  date        load served | oracle       ctl served | oracle     atl served | oracle     tsb served | oracle")
    assert_matches_oracle(body, rows, zone, today, show=session_days | {today})

    assert body["timezone"] == zone and body["today"] == today.isoformat()
    assert body["first_day"] == "2024-07-09"
    assert len(body["days"]) == (today - date(2024, 7, 9)).days + 1 > 800
    # Same-day summing on real data: the walk and strap_run_hrv share 2026-09-06.
    walk_day = local_date(next(r["start_time"] for r in rows if r["session_id"] == uploaded[WALK_FIXTURE]), zone)
    run_day = local_date(next(r["start_time"] for r in rows if r["session_id"] == uploaded["strap_run_hrv.fit"]), zone)
    assert walk_day == run_day == date(2026, 9, 6)
    served_day = next(d for d in body["days"] if d["date"] == "2026-09-06")
    assert {c["session_id"] for c in served_day["counted"]} >= {uploaded[WALK_FIXTURE], uploaded["strap_run_hrv.fit"]}
    assert served_day["load"] == pytest.approx(sum(c["load"] for c in served_day["counted"]), abs=TOLERANCE)
    assert len(counted_values) >= 2 and served_day["load"] > 0
    # Every uploaded session is listed exactly once, and a counted entry carries its saved load.
    listed = _listed(body)
    assert set(listed) == set(uploaded.values())
    assert all(len(places) == 1 for places in listed.values()), listed
    for served in body["days"]:
        for entry in served["counted"]:
            assert entry["load"] == pytest.approx(counted_values[entry["session_id"]], abs=TOLERANCE)


# ---------------------------------------------------------------------------
# AC3: every session in exactly one group
# ---------------------------------------------------------------------------


def test_every_session_on_a_day_lands_in_exactly_one_group(configure, freeze, persist_sessions) -> None:
    """Day 1: a counted run, two uncounted runs (wrist_hr_at_threshold, no_hr), a cycling session
    and a declared capture. Day 2: the cycling session and the capture only. Day 1's load is the
    counted run's alone and it is marked; day 2 is not marked."""
    configure("UTC")
    day1, day2 = date(2026, 3, 1), date(2026, 3, 2)
    freeze(utc(day2, 20))
    counted = _session(at(day1, 7))
    at_threshold = _session(at(day1, 8))
    no_hr = _session(at(day1, 9))
    ride1 = _session(at(day1, 10), sport="cycling")
    capture1 = _session(at(day1, 11), activity_tag="resting_hrv_check")
    ride2 = _session(at(day2, 10), sport="cycling")
    capture2 = _session(at(day2, 11), activity_tag="resting_hrv_check")
    persist_sessions([counted, at_threshold, no_hr, ride1, capture1, ride2, capture2])
    _plant(counted.session_id, value=50.0)
    _plant(at_threshold.session_id, reason="wrist_hr_at_threshold")
    _plant(no_hr.session_id, reason="no_hr")
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_day"] == day1.isoformat()
    first, second = body["days"]
    assert set(first) == DAY_KEYS
    assert first["load"] == pytest.approx(50.0) and first["marked"] is True
    assert first["counted"] == [{"session_id": counted.session_id, "load": 50.0}]
    assert first["uncounted"] == [
        {"session_id": at_threshold.session_id, "reason": "wrist_hr_at_threshold"},
        {"session_id": no_hr.session_id, "reason": "no_hr"},
    ]
    assert first["excluded"] == [
        {"session_id": ride1.session_id, "reason": "sport_not_running"},
        {"session_id": capture1.session_id, "reason": "declared_capture"},
    ]
    assert first["uncounted_in_window"] == 2
    assert second["load"] == 0 and second["marked"] is False and second["uncounted"] == [] and second["counted"] == []
    assert second["excluded"] == [
        {"session_id": ride2.session_id, "reason": "sport_not_running"},
        {"session_id": capture2.session_id, "reason": "declared_capture"},
    ]
    assert second["uncounted_in_window"] == 2
    # No uncounted run is in counted or served with a load of 0; every stored session is in one list.
    listed = _listed(body)
    assert set(listed) == {r["session_id"] for r in _saved_rows()}
    assert all(len(places) == 1 for places in listed.values()), listed
    for served in body["days"]:
        assert all(c["load"] != 0 for c in served["counted"])
    assert_matches_oracle(body, _saved_rows(), "UTC", day2)


# ---------------------------------------------------------------------------
# AC6: local days, through the route
# ---------------------------------------------------------------------------


def test_sessions_land_on_their_local_days_in_london_then_auckland_and_nothing_is_stored(
    configure, freeze, persist_sessions
) -> None:
    """Runs at 23:30 and 00:30 London on consecutive dates land on different days, and a run at
    00:30 BST on 2026-10-25 (23:30 UTC the day before) lands on the 25th. The same store read
    under Pacific/Auckland puts each session on its Auckland date, and every table's rows are
    the same before and after the read."""
    configure("Europe/London")
    freeze(datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
    late = _session(at(date(2026, 10, 20), 23, 30, zone="Europe/London"))
    early = _session(at(date(2026, 10, 21), 0, 30, zone="Europe/London"))
    change = _session(at(date(2026, 10, 25), 0, 30, zone="Europe/London"))
    assert late.start_time == "2026-10-20T22:30:00+00:00"
    assert early.start_time == "2026-10-20T23:30:00+00:00"
    assert change.start_time == "2026-10-24T23:30:00+00:00"
    persist_sessions([late, early, change])
    _plant(late.session_id, value=10.0)
    _plant(early.session_id, value=20.0)
    _plant(change.session_id, value=30.0)

    with TestClient(app) as client:
        london = get(client)
    assert london.status_code == 200, london.text
    body = london.json()
    assert body["timezone"] == "Europe/London" and body["today"] == "2026-10-27"
    by_date = {d["date"]: d for d in body["days"]}
    assert body["first_day"] == "2026-10-20"
    assert [c["session_id"] for c in by_date["2026-10-20"]["counted"]] == [late.session_id]
    assert [c["session_id"] for c in by_date["2026-10-21"]["counted"]] == [early.session_id]
    assert by_date["2026-10-24"]["counted"] == [] and by_date["2026-10-24"]["load"] == 0
    assert [c["session_id"] for c in by_date["2026-10-25"]["counted"]] == [change.session_id]
    assert by_date["2026-10-25"]["load"] == pytest.approx(30.0)
    assert_matches_oracle(body, _saved_rows(), "Europe/London", date(2026, 10, 27))

    configure("Pacific/Auckland")
    before = _schema_and_rows()
    with TestClient(app) as client:
        auckland = get(client)
    after = _schema_and_rows()
    assert auckland.status_code == 200, auckland.text
    body = auckland.json()
    # NZDT (UTC+13): 22:30Z and 23:30Z on the 20th are 11:30 and 12:30 on the 21st; 23:30Z on the
    # 24th is 12:30 on the 25th.
    assert body["timezone"] == "Pacific/Auckland" and body["today"] == "2026-10-28"
    assert body["first_day"] == "2026-10-21"
    by_date = {d["date"]: d for d in body["days"]}
    assert [c["session_id"] for c in by_date["2026-10-21"]["counted"]] == [late.session_id, early.session_id]
    assert by_date["2026-10-21"]["load"] == pytest.approx(30.0)
    assert [c["session_id"] for c in by_date["2026-10-25"]["counted"]] == [change.session_id]
    assert "2026-10-20" not in by_date
    assert_matches_oracle(body, _saved_rows(), "Pacific/Auckland", date(2026, 10, 28))
    assert after == before, "a read under a second zone stored something"


# ---------------------------------------------------------------------------
# AC7: range, errors and stability
# ---------------------------------------------------------------------------


def test_excluded_sessions_thirty_days_before_the_first_run_leave_first_day_and_the_seed_unchanged(
    configure, freeze, persist_sessions
) -> None:
    configure("UTC")
    first_run_day = date(2026, 3, 10)
    freeze(utc(date(2026, 3, 15), 20))
    run_a = _session(at(first_run_day))
    run_b = _session(at(date(2026, 3, 12)))
    persist_sessions([run_a, run_b])
    _plant(run_a.session_id, value=40.0)
    _plant(run_b.session_id, value=20.0)
    with TestClient(app) as client:
        before = get(client).json()
        older = first_run_day - timedelta(days=30)
        ride = _session(at(older, 9), sport="cycling")
        capture = _session(at(older, 10), activity_tag="resting_hrv_check")
        persist_sessions([ride, capture])
        after = get(client).json()
    rows = {r["session_id"]: r["load_reason"] for r in _saved_rows()}
    assert rows[ride.session_id] == "sport_not_running" and rows[capture.session_id] == "declared_capture"
    assert before["first_day"] == first_run_day.isoformat()
    assert after == before
    assert ride.session_id not in _listed(after) and capture.session_id not in _listed(after)


def test_to_defaults_to_the_local_today_and_from_to_first_day(configure, freeze, persist_sessions) -> None:
    configure("Pacific/Auckland")
    # 20:00Z on the 14th is already the 15th in Auckland: today is the configured zone's.
    freeze(utc(date(2026, 3, 14), 20))
    run = _session(at(date(2026, 3, 10)))
    persist_sessions([run])
    _plant(run.session_id, value=30.0)
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["today"] == "2026-03-15"
    assert body["first_day"] == "2026-03-10"
    assert [d["date"] for d in body["days"]] == [(date(2026, 3, 10) + timedelta(days=n)).isoformat() for n in range(6)]
    assert body["seed"] == {"value": 5.0, "window_days": 6, "provisional_until": "2026-04-20"}
    assert all(d["provisional"] for d in body["days"])
    assert body["days"][0]["tsb"] == 0.0


def test_from_after_to_is_a_422_naming_both(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    run = _session(at(date(2026, 3, 10)))
    persist_sessions([run])
    _plant(run.session_id, value=30.0)
    with TestClient(app) as client:
        response = client.get(CHART_PATH, params={"from": "2026-03-12", "to": "2026-03-11"})
    assert response.status_code == 422, response.text
    assert "2026-03-12" in response.text and "2026-03-11" in response.text


def test_to_after_the_local_today_is_a_422(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    run = _session(at(date(2026, 3, 10)))
    persist_sessions([run])
    _plant(run.session_id, value=30.0)
    with TestClient(app) as client:
        tomorrow = client.get(CHART_PATH, params={"to": "2026-03-16"})
        today = client.get(CHART_PATH, params={"to": "2026-03-15"})
    assert tomorrow.status_code == 422, tomorrow.text
    assert "2026-03-16" in tomorrow.text and "2026-03-15" in tomorrow.text
    assert today.status_code == 200, today.text


@pytest.mark.parametrize(
    "value",
    ["", "yesterday", "2026-13-01", "2026-09-0", "2026/09/08", "2026-02-29", "20260908", "2026-09-08T06:00"],
)
@pytest.mark.parametrize("name", ["from", "to"])
def test_a_malformed_date_is_pydantics_422_not_a_hand_parse(configure, freeze, name: str, value: str) -> None:
    """``test_hrv_trend_endpoint.py``'s malformed-date table, on both parameters."""
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    with TestClient(app) as client:
        response = client.get(CHART_PATH, params={name: value})
    assert response.status_code == 422, response.text
    assert any(err["loc"] == ["query", name] for err in response.json()["detail"])


def test_an_empty_store_answers_200_with_nulls_and_no_days(configure, freeze) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "timezone": "UTC",
        "today": "2026-03-15",
        "first_day": None,
        "seed": None,
        "days": [],
    }


def test_a_store_holding_only_non_running_sessions_answers_the_same_nulls(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    persist_sessions(
        [
            _session(at(date(2026, 3, 1)), sport="cycling"),
            _session(at(date(2026, 3, 2)), activity_tag="resting_hrv_check"),
            _session(at(date(2026, 3, 3)), activity_tag="health_snapshot"),
        ]
    )
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_day"] is None and body["seed"] is None and body["days"] == []


def test_every_session_after_today_answers_200_with_no_days(configure, freeze, persist_sessions) -> None:
    """A watch clock ahead: ``from`` defaults to the earlier of ``first_day`` and ``to``, so this
    is the documented empty answer and not a from-after-to 422."""
    configure("UTC")
    freeze(utc(date(2026, 3, 15)))
    run = _session(at(date(2026, 3, 16)))
    persist_sessions([run])
    _plant(run.session_id, value=30.0)
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_day"] == "2026-03-16" and body["seed"] is None and body["days"] == []


def _five_day_history(persist_sessions) -> tuple[date, list[Session]]:
    """Loads 60, rest, 45, an uncounted run, 50 on 2026-03-01..05 (the reference's history)."""
    day1 = date(2026, 3, 1)
    runs = [
        _session(at(day1)),
        _session(at(day1 + timedelta(days=2))),
        _session(at(day1 + timedelta(days=3))),
        _session(at(day1 + timedelta(days=4))),
    ]
    persist_sessions(runs)
    _plant(runs[0].session_id, value=60.0)
    _plant(runs[1].session_id, value=45.0)
    _plant(runs[2].session_id, reason="no_hr")
    _plant(runs[3].session_id, value=50.0)
    return day1, runs


def test_from_before_first_day_returns_days_from_first_day(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 5), 20))
    day1, _runs = _five_day_history(persist_sessions)
    with TestClient(app) as client:
        response = client.get(CHART_PATH, params={"from": "2026-01-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_day"] == day1.isoformat()
    assert [d["date"] for d in body["days"]] == [(day1 + timedelta(days=n)).isoformat() for n in range(5)]
    assert body["seed"]["value"] == pytest.approx(31.0)


def test_to_before_first_day_returns_no_days(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    freeze(utc(date(2026, 3, 5), 20))
    day1, _runs = _five_day_history(persist_sessions)
    with TestClient(app) as client:
        response = client.get(CHART_PATH, params={"to": "2026-02-27"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == []
    assert body["first_day"] == day1.isoformat()
    assert body["seed"]["value"] == pytest.approx(31.0)


def test_a_days_values_are_the_same_under_two_froms(configure, freeze, persist_sessions) -> None:
    """The curves are computed from the first day of history whatever ``from`` is."""
    configure("UTC")
    freeze(utc(date(2026, 3, 5), 20))
    day1, _runs = _five_day_history(persist_sessions)
    with TestClient(app) as client:
        whole = client.get(CHART_PATH, params={"from": day1.isoformat()}).json()
        sliced = client.get(CHART_PATH, params={"from": "2026-03-04", "to": "2026-03-04"}).json()
    assert [d["date"] for d in sliced["days"]] == ["2026-03-04"]
    assert sliced["days"][0] == whole["days"][3]
    assert sliced["first_day"] == whole["first_day"] and sliced["seed"] == whole["seed"]
    assert whole["days"][3]["marked"] is True
    assert whole["days"][3]["ctl"] == pytest.approx(30.53, abs=0.01)
    assert whole["days"][3]["atl"] == pytest.approx(27.90, abs=0.01)
    assert whole["days"][3]["tsb"] == pytest.approx(-0.92, abs=0.01)


def test_each_list_is_ordered_by_start_time_then_session_id(configure, freeze, persist_sessions) -> None:
    """Inserted out of order, with a pair sharing a start_time inserted larger id first."""
    configure("UTC")
    day = date(2026, 3, 1)
    freeze(utc(day, 20))
    late_counted = _session(at(day, 9))
    tied_a = _session(at(day, 7), device="fr945")
    tied_b = _session(at(day, 7), device="fr955")
    lo, hi = sorted([tied_a, tied_b], key=lambda s: s.session_id)
    late_uncounted = _session(at(day, 10), device="fr255")
    early_uncounted = _session(at(day, 8), device="fr255")
    late_ride = _session(at(day, 11), sport="cycling")
    early_ride = _session(at(day, 6), sport="cycling")
    inserted = [late_counted, hi, lo, late_uncounted, early_uncounted, late_ride, early_ride]
    persist_sessions(inserted)
    for run in (late_counted, hi, lo):
        _plant(run.session_id, value=10.0)
    _plant(late_uncounted.session_id, reason="no_hr")
    _plant(early_uncounted.session_id, reason="missing_anchor")
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    (served,) = response.json()["days"]
    assert [c["session_id"] for c in served["counted"]] == [lo.session_id, hi.session_id, late_counted.session_id]
    assert [u["session_id"] for u in served["uncounted"]] == [early_uncounted.session_id, late_uncounted.session_id]
    assert [e["session_id"] for e in served["excluded"]] == [early_ride.session_id, late_ride.session_id]
    assert [c["session_id"] for c in served["counted"]] != [hi.session_id, lo.session_id, late_counted.session_id]


def test_a_deleted_run_leaves_its_day_and_every_value_follows_the_oracle(configure, freeze, persist_sessions) -> None:
    configure("UTC")
    today = date(2026, 3, 6)
    freeze(utc(today, 20))
    day1, runs = _five_day_history(persist_sessions)
    with TestClient(app) as client:
        before = get(client).json()
        assert client.delete(f"/sessions/{runs[1].session_id}").status_code == 204
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = _saved_rows()
    assert runs[1].session_id not in {r["session_id"] for r in rows}
    assert runs[1].session_id not in _listed(body)
    assert body["first_day"] == day1.isoformat()
    day3 = next(d for d in body["days"] if d["date"] == "2026-03-03")
    assert day3["counted"] == [] and day3["load"] == 0
    assert body["seed"]["value"] == pytest.approx((60 + 0 + 0 + 0 + 50 + 0) / 6, abs=TOLERANCE)
    assert body != before
    assert_matches_oracle(body, rows, "UTC", today, show={date(2026, 3, 3)})


def test_deleting_the_only_session_on_first_day_moves_first_day_and_recomputes_the_seed(
    configure, freeze, persist_sessions
) -> None:
    configure("UTC")
    today = date(2026, 3, 6)
    freeze(utc(today, 20))
    _, runs = _five_day_history(persist_sessions)
    with TestClient(app) as client:
        assert client.delete(f"/sessions/{runs[0].session_id}").status_code == 204
        response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_day"] == "2026-03-03"
    assert [d["date"] for d in body["days"]] == ["2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]
    assert body["seed"]["value"] == pytest.approx((45 + 0 + 50 + 0) / 4, abs=TOLERANCE)
    assert body["seed"]["window_days"] == 4 and body["seed"]["provisional_until"] == "2026-04-13"
    assert_matches_oracle(body, _saved_rows(), "UTC", today)


def test_a_session_without_a_saved_load_is_a_named_500(configure, freeze, persist_sessions) -> None:
    """A session whose ``session_loads`` row is gone (both load columns null in the LEFT JOIN) is a
    500 naming the session, not a chart that silently omits a run. The row is removed after the
    client has started, because startup fills a load for every session still missing one."""
    configure("UTC")
    freeze(utc(date(2026, 3, 5)))
    kept = _session(at(date(2026, 3, 1)))
    stripped = _session(at(date(2026, 3, 2)))
    persist_sessions([kept, stripped])
    _plant(kept.session_id, value=30.0)
    with TestClient(app) as client:
        with _connection() as conn, conn:
            conn.execute(
                f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (stripped.session_id,)
            )
        response = get(client)
    assert response.status_code == 500, response.text
    assert stripped.session_id in response.json()["detail"]


def test_a_rest_days_load_is_served_as_a_float(configure, freeze, persist_sessions) -> None:
    """The contract types ``load`` as a number; a rest day serves ``0.0``, not the integer ``0``."""
    configure("UTC")
    freeze(utc(date(2026, 3, 3)))
    run = _session(at(date(2026, 3, 1)))
    persist_sessions([run])
    _plant(run.session_id, value=30.0)
    with TestClient(app) as client:
        response = get(client)
    assert response.status_code == 200, response.text
    rest = response.json()["days"][1]
    assert rest["counted"] == []
    assert isinstance(rest["load"], float) and rest["load"] == 0.0
    assert '"load":0.0' in response.text.replace(" ", "")


# ---------------------------------------------------------------------------
# AC8: the contract moves with the route
# ---------------------------------------------------------------------------


def test_the_contract_operation_matches_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"][CHART_PATH]["get"]
    built_op = built["paths"][CHART_PATH]["get"]
    assert op["operationId"] == built_op["operationId"] == "getLoadChart"
    assert op["tags"] == built_op["tags"] == ["State & Metrics"]
    assert op["x-readiness"] == "implemented"
    assert "security" not in op, "getLoadChart inherits the global bearerAuth, as getHrvTrend does"
    params = {p["name"]: p for p in op["parameters"]}
    built_params = {p["name"]: p for p in built_op["parameters"]}
    assert set(params) == set(built_params) == {"from", "to"}
    for name in ("from", "to"):
        assert params[name]["in"] == built_params[name]["in"] == "query"
        assert not params[name].get("required", False) and not built_params[name].get("required", False)
        assert params[name]["schema"] == {"type": "string", "format": "date"}
        built_schema = built_params[name]["schema"]
        assert {"type": "string", "format": "date"} in built_schema.get("anyOf", [built_schema]), built_schema
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LoadChart"
    }
    assert built_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LoadChart"
    }
    assert set(op["responses"]) == {"200", "422"}
    # The path sits inside paths:, right after /metrics/hrv.
    paths = list(contract["paths"])
    assert paths.index(CHART_PATH) == paths.index("/metrics/hrv") + 1
    tag = contract["tags"][[t["name"] for t in contract["tags"]].index("State & Metrics")]["description"]
    assert "getLoadChart" in tag and "implemented" in tag, tag


def test_get_load_series_stays_planned() -> None:
    op = _contract()["paths"]["/plan/load"]["get"]
    assert op["operationId"] == "getLoadSeries" and op["x-readiness"] == "planned"
    assert "/plan/load" not in app.openapi()["paths"]


def test_the_chart_blocks_of_the_contract_are_ascii_only() -> None:
    """``check_drift.py`` opens the contract without an encoding, so a Windows console reads it as
    cp1252; the blocks this feature wrote carry no non-ASCII text."""
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index(f"  {CHART_PATH}:")
    end = text.index("  # ---", start)
    assert text[start:end].isascii(), "the chart operation carries non-ASCII text"
    components = text.index("    LoadChartReason:")
    assert text[components:].isascii(), "the chart components carry non-ASCII text"
    tag = next(t for t in _contract()["tags"] if t["name"] == "State & Metrics")
    assert tag["description"].isascii()


def test_the_reason_enum_is_f017s_minus_the_two_exclusions() -> None:
    served = app.openapi()["components"]["schemas"]
    contract = _contract()["components"]["schemas"]
    f017 = set(contract["SessionLoadReason"]["enum"])
    assert set(served["LoadChartReason"]["enum"]) == set(contract["LoadChartReason"]["enum"]) == set(CHART_REASONS)
    assert set(CHART_REASONS) == f017 - EXCLUSION_REASONS
    assert len(served["LoadChartReason"]["enum"]) == len(CHART_REASONS)


contract_walk = _load_support("contract_walk")
REF_PREFIX = contract_walk.REF_PREFIX
_ref_name = contract_walk.ref_name
_normalised = contract_walk.normalised


def _chart_root(doc: dict) -> str:
    schema = doc["paths"][CHART_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return _ref_name(schema["$ref"])


def test_every_chart_component_matches_the_served_model_property_by_property() -> None:
    """Walk every component reachable from ``getLoadChart``, in the contract and in ``app.openapi()``
    alike, and compare each property's type, nullability, enum, format and ``$ref`` target, recursing
    into array ``items`` so the components behind ``days[]``, ``counted[]``, ``uncounted[]`` and
    ``excluded[]`` are compared rather than skipped. The enum component (``LoadChartReason``) has no
    properties and is compared as one node. Each object component's ``required`` list is compared
    too, and every F018 component must have been visited."""
    contract = _contract()
    built = app.openapi()
    assert _chart_root(contract) == _chart_root(built) == "LoadChart"
    seen, compared = contract_walk.walk(contract, built, ["LoadChart"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == NEW_COMPONENTS, seen
    # The array items were opened: the walk reached the per-session components through the lists.
    days = _normalised(contract["components"]["schemas"]["LoadChart"]["properties"]["days"])
    assert days["items"]["ref"] == "LoadChartDay", days
    excluded = _normalised(contract["components"]["schemas"]["LoadChartDay"]["properties"]["excluded"])
    assert excluded["items"]["ref"] == "LoadChartExcluded", excluded
    reason = _normalised(contract["components"]["schemas"]["LoadChartExcluded"]["properties"]["reason"])
    assert reason["enum"] == ["declared_capture", "sport_not_running"], reason


def test_the_walker_opens_array_items() -> None:
    """The extension itself, perturbed: two array nodes that agree on everything but their items'
    ``$ref`` are unequal here."""
    one = {"type": "array", "items": {"$ref": f"{REF_PREFIX}LoadChartCounted"}}
    other = {"type": "array", "items": {"$ref": f"{REF_PREFIX}LoadChartUncounted"}}
    assert _normalised(one) != _normalised(other)
    assert _normalised(one) == _normalised({"items": {"$ref": f"{REF_PREFIX}LoadChartCounted"}, "title": "Counted", "type": "array"})


def test_the_served_shape_is_the_reference_shape(configure, freeze, persist_sessions) -> None:
    """The response's keys, the day's keys and the per-session keys, read from a served body, and
    the JSON types the contract declares for them."""
    configure("UTC")
    freeze(utc(date(2026, 3, 2)))
    run = _session(at(date(2026, 3, 1)))
    unc = _session(at(date(2026, 3, 1), 9))
    ride = _session(at(date(2026, 3, 1), 10), sport="cycling")
    persist_sessions([run, unc, ride])
    _plant(run.session_id, value=30.0)
    _plant(unc.session_id, reason="no_hr")
    with TestClient(app) as client:
        body = get(client).json()
    assert set(body) == {"timezone", "today", "first_day", "seed", "days"}
    assert set(body["seed"]) == {"value", "window_days", "provisional_until"}
    served = body["days"][0]
    assert set(served) == DAY_KEYS
    assert set(served["counted"][0]) == {"session_id", "load"}
    assert set(served["uncounted"][0]) == {"session_id", "reason"}
    assert set(served["excluded"][0]) == {"session_id", "reason"}
    assert isinstance(served["uncounted_in_window"], int) and isinstance(body["seed"]["window_days"], int)
    assert json.dumps(body)  # serialisable as served
