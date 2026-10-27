"""The fitness screen through ``GET /state`` (F029 AC2, reference sections 1, 2 and 4).

The reference section 4 races A to D are stored with 1 Hz flat records at constant speed (so
F013's GAP pace is ``duration / distance`` and the GAP-equivalent distance is the distance run)
and tagged through F023's ``PUT /race-tags``. Untagged filler sessions planted by
``support/ctl_path.py`` carry the daily loads that put the F018 chart's CTL on the section 4
figures on each race's day before, 29 days before and race day; each race's own saved load is
replaced through ``support/session_loads.py`` with a share of its day's path load, the fillers
carrying the rest. Every test reads those figures back from ``load_chart.build_chart`` and
prints them before asserting on the served entry.

The athlete's zone is ``America/Vancouver`` and the clock is frozen through ``main._utcnow``.
The expected fits come from the reference's printed figures and from ``support/riegel_oracle.py``,
which imports nothing from ``runcoach_api``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
from runcoach_api.metrics import load_chart
from runcoach_api.models import Context, Record, Session

SUPPORT = Path(__file__).parent / "support"
ZONE_NAME = "America/Vancouver"
ZONE = ZoneInfo(ZONE_NAME)
NOW = datetime(2026, 10, 14, 19, 0, tzinfo=UTC)  # 12:00 in Vancouver
TODAY = date(2026, 10, 14)
CHART_START = date(2026, 3, 1)
RACE_HOUR = 8  # local; the fillers sit at noon
RACE_SHARE = 0.5  # the race's share of its day's path load

# Reference section 4: GAP-equivalent distance, time, and CTL on the day before / 29 days before /
# race day. Race dates are the test's, 40 days apart, in the athlete's zone.
SECTION_4 = {
    "A": (date(2026, 6, 6), 5000.0, 1200, (36.0, 37.0, 37.0)),
    "B": (date(2026, 7, 16), 10000.0, 2520, (40.0, 41.0, 42.0)),
    "C": (date(2026, 8, 25), 21100.0, 5600, (42.0, 40.0, 44.0)),
    "D": (date(2026, 10, 4), 42200.0, 12000, (29.0, 50.0, 36.0)),
}
# Reference section 4, the screened set A, B, C.
SCREENED_B, SCREENED_ERROR, SCREENED_T = 1.069870, 0.000285, 3.077684
SCREENED_INTERVAL = (1.068992, 1.070748)
SCREENED_RESIDUALS_PCT = (-0.012, 0.024, -0.011)


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
riegel_oracle = _load_support("riegel_oracle")


@pytest.fixture(autouse=True)
def vancouver(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The isolated store with ``athlete_timezone`` America/Vancouver and the clock frozen."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=ZONE_NAME,
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _store_race(day: date, distance_m: float, duration_s: int) -> str:
    """An untagged running session at ``RACE_HOUR`` local on ``day``, ``distance_m`` in
    ``duration_s`` at constant speed on flat ground, 1 Hz records."""
    # Stored in UTC, as ingestion stores it and as F023 matches it.
    start_time = datetime(day.year, day.month, day.day, RACE_HOUR, tzinfo=ZONE).astimezone(UTC).isoformat()
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
    speed = distance_m / duration_s
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


def _put_tags(client: TestClient, session_ids: list[str]) -> None:
    """Connect's race mark for each stored run: its start second in UTC, distance 0.5 % long and
    duration 0.5 % short (inside F023's 1 %)."""
    items = []
    conn = db_module.get_connection()
    try:
        for n, session_id in enumerate(session_ids):
            row = conn.execute("SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            summary = json.loads(row["summary"])
            start = datetime.fromisoformat(row["start_time"]).astimezone(UTC)
            items.append(
                {
                    "connect_activity_id": 7000 + int(hashlib.sha256(session_id.encode()).hexdigest()[:6], 16) + n,
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "distance_m": summary["distance_m"] * 1.005,
                    "duration_s": summary["duration_s"] * 0.995,
                    "race": True,
                }
            )
    finally:
        conn.close()
    response = client.put("/race-tags", json={"items": items})
    assert response.status_code == 200, response.text
    outcomes = [(i["outcome"], i["session_id"]) for i in response.json()["items"]]
    print(f"  race-tags {outcomes}")
    assert [s for _o, s in outcomes] == session_ids, outcomes


def _seed(
    client: TestClient,
    races: dict[str, tuple[date, float, int]],
    loads: dict[date, float],
    gap_days: tuple[date, ...] = (),
) -> dict[str, str]:
    """Fillers carry ``loads`` (less each race's share on its day); each race is stored, tagged and
    given its share of its day's load. Returns each race's session id by name."""
    path = dict(loads)
    shares = {}
    for name, (day, _distance, _duration) in races.items():
        shares[name] = loads[day] * RACE_SHARE
        path[day] = loads[day] - shares[name]
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        fillers = ctl_path.plant(conn, path, ZONE, gap_days=gap_days)
    finally:
        conn.close()
    ids = {name: _store_race(day, distance, duration) for name, (day, distance, duration) in races.items()}
    assert not set(ids.values()) & {p.session_id for p in fillers.values()}
    _put_tags(client, list(ids.values()))
    conn = db_module.get_connection()
    try:
        for name, session_id in ids.items():
            session_loads.plant_load(conn, session_id, value=shares[name])
    finally:
        conn.close()
    return ids


def _section_4_loads() -> dict[date, float]:
    targets = {}
    for day, _distance, _duration, (before, earlier, on_day) in SECTION_4.values():
        targets[day - timedelta(days=29)] = earlier
        targets[day - timedelta(days=1)] = before
        targets[day] = on_day
    return ctl_path.loads_for(targets, CHART_START, TODAY)


def _seed_section_4(client: TestClient) -> dict[str, str]:
    return _seed(client, {k: v[:3] for k, v in SECTION_4.items()}, _section_4_loads())


def _chart() -> load_chart.LoadChart:
    conn = db_module.get_connection()
    try:
        return load_chart.build_chart(db_module.read_session_load_rows(conn), ZONE, TODAY)
    finally:
        conn.close()


def _read_back_section_4() -> dict[str, tuple[load_chart.Day, load_chart.Day, load_chart.Day]]:
    """The chart's day before, 29 days before and race day for each section 4 race, printed and
    checked against the section 4 figures."""
    days = {d.date: d for d in _chart().days}
    out = {}
    for name, (day, _distance, _duration, figures) in SECTION_4.items():
        trio = (days[day - timedelta(days=1)], days[day - timedelta(days=29)], days[day])
        print(f"  chart {name} {day}: before {trio[0].ctl!r} earlier {trio[1].ctl!r} race day {trio[2].ctl!r}")
        assert [d.ctl for d in trio] == pytest.approx(list(figures), abs=1e-9), name
        out[name] = trio
    return out


def _riegel(client: TestClient) -> dict:
    response = client.get("/state")
    assert response.status_code == 200, response.text
    threshold, riegel = response.json()["determinants"]
    assert (threshold["name"], riegel["name"]) == ("functional_threshold_pace", "riegel_exponent")
    print(f"  efforts {[(e['session_id'], e['fitness_before'], e['fitness_earlier']) for e in riegel['efforts']]}")
    print(f"  excluded {riegel['excluded']}")
    return riegel


# ---------------------------------------------------------------------------
# Section 4
# ---------------------------------------------------------------------------


def test_race_d_is_excluded_on_the_day_before() -> None:
    """D's CTL fell from 50 to 29 over the four weeks before it: 29 < 0.7 x 50 = 35, so it is
    ``fitness_dropped`` with 29, 50 and the drop served. Its race day reads 36, which would pass:
    a lookup that read the race day would keep D."""
    with TestClient(app) as client:
        ids = _seed_section_4(client)
        before, earlier, race_day = _read_back_section_4()["D"]
        riegel = _riegel(client)
    assert race_day.ctl >= 0.7 * earlier.ctl  # the race-day reading would pass
    assert before.ctl < 0.7 * earlier.ctl
    assert ids["D"] not in [e["session_id"] for e in riegel["efforts"]]
    [dropped] = [e for e in riegel["excluded"] if e["session_id"] == ids["D"]]
    assert dropped["reason"] == "fitness_dropped"
    assert dropped["fitness_before"] == before.ctl
    assert dropped["fitness_earlier"] == earlier.ctl
    assert dropped["fitness_before"] == pytest.approx(29.0, abs=1e-9)
    assert dropped["fitness_earlier"] == pytest.approx(50.0, abs=1e-9)
    assert dropped["fitness_drop"] == pytest.approx(1 - before.ctl / earlier.ctl, abs=1e-12)
    assert dropped["fitness_drop"] == pytest.approx(0.42, abs=1e-9)
    assert [e["session_id"] for e in riegel["excluded"]] == [ids["D"]]


def test_races_a_b_c_are_used_with_their_figures() -> None:
    with TestClient(app) as client:
        ids = _seed_section_4(client)
        chart = _read_back_section_4()
        riegel = _riegel(client)
    assert [e["session_id"] for e in riegel["efforts"]] == [ids["A"], ids["B"], ids["C"]]
    for effort, name in zip(riegel["efforts"], "ABC", strict=True):
        before, earlier, _race_day = chart[name]
        figures = SECTION_4[name][3]
        assert effort["fitness_before"] == before.ctl, name
        assert effort["fitness_earlier"] == earlier.ctl, name
        assert (effort["fitness_before"], effort["fitness_earlier"]) == (
            pytest.approx(figures[0], abs=1e-9),
            pytest.approx(figures[1], abs=1e-9),
        ), name
        assert effort["fitness_drop"] == pytest.approx(1 - before.ctl / earlier.ctl, abs=1e-12), name
        assert (effort["fitness_provisional"], effort["fitness_after_gap"]) == (False, False), name
        assert effort["weight"] == 1.0
    # C's fitness rose: a negative drop is served as computed.
    assert riegel["efforts"][2]["fitness_drop"] == pytest.approx(-0.05, abs=1e-9)


def test_the_fit_after_the_screen_matches_the_reference() -> None:
    """A, B and C after the screen: b 1.069870, error 0.000285, t 3.077684 (reference section 4),
    and the oracle on the served GAP-equivalent distances agrees."""
    with TestClient(app) as client:
        _seed_section_4(client)
        _read_back_section_4()
        riegel = _riegel(client)
    efforts = riegel["efforts"]
    print(f"  D_eq {[e['gap_equivalent_m'] for e in efforts]}; T {[e['duration_s'] for e in efforts]}")
    for effort, name in zip(efforts, "ABC", strict=True):
        assert effort["gap_equivalent_m"] == pytest.approx(SECTION_4[name][1], rel=1e-9), name
        assert effort["duration_s"] == SECTION_4[name][2]
    estimate = riegel["estimate"]
    print(f"  b {estimate['value']} error {riegel['error']} t {riegel['t_multiplier']} interval {estimate['interval']}")
    assert riegel["population_default"] is False
    assert estimate["value"] == pytest.approx(SCREENED_B, abs=1e-6)
    assert riegel["error"] == pytest.approx(SCREENED_ERROR, abs=1e-6)
    assert riegel["t_multiplier"] == pytest.approx(SCREENED_T, abs=1e-6)
    assert (estimate["interval"]["low"], estimate["interval"]["high"]) == (
        pytest.approx(SCREENED_INTERVAL[0], abs=1e-6),
        pytest.approx(SCREENED_INTERVAL[1], abs=1e-6),
    )
    assert estimate["confidence"] == "high"
    assert [100 * e["residual"] for e in efforts] == pytest.approx(list(SCREENED_RESIDUALS_PCT), abs=5e-4)
    expected = riegel_oracle.fit([(e["gap_equivalent_m"], e["duration_s"], e["weight"]) for e in efforts])
    print(f"  oracle b {expected.b} error {expected.error} t {expected.t}")
    assert estimate["value"] == pytest.approx(expected.b, abs=1e-9)
    assert riegel["error"] == pytest.approx(expected.error, abs=1e-9)


# ---------------------------------------------------------------------------
# Missing, provisional and after-gap days
# ---------------------------------------------------------------------------


def test_an_earlier_day_before_the_chart_is_null_and_passes() -> None:
    """The chart starts 20 days before the race, so its 29-days-before day precedes ``first_day``:
    the race is used, ``fitness_earlier`` and ``fitness_drop`` null, ``fitness_before`` the chart's."""
    race_day = date(2026, 9, 20)
    first = race_day - timedelta(days=20)
    loads = {first + timedelta(days=n): 40.0 for n in range((TODAY - first).days + 1)}
    with TestClient(app) as client:
        ids = _seed(client, {"race": (race_day, 10000.0, 2520)}, loads)
        chart = _chart()
        days = {d.date: d for d in chart.days}
        before = days[race_day - timedelta(days=1)]
        print(f"  chart first_day {chart.first_day}; day before {before}")
        assert chart.first_day == first
        assert race_day - timedelta(days=29) not in days
        riegel = _riegel(client)
    assert riegel["excluded"] == []
    [effort] = riegel["efforts"]
    assert effort["session_id"] == ids["race"]
    assert effort["fitness_earlier"] is None and effort["fitness_drop"] is None
    assert effort["fitness_before"] == before.ctl


def test_provisional_and_after_gap_are_carried() -> None:
    """One race 35 days into the chart (its day before inside the 42 provisional days, its earlier
    day inside the chart) and one whose day before ends a gap of ``GAP_MARK_DAYS + 1`` empty days
    (a heavy filler that day keeps its CTL above the screen): both used, the flag true."""
    first = date(2026, 5, 1)
    early_day = first + timedelta(days=35)
    late_day = date(2026, 9, 20)
    gap = tuple(late_day - timedelta(days=2 + n) for n in range(load_chart.GAP_MARK_DAYS + 1))
    loads = {first + timedelta(days=n): 40.0 for n in range((TODAY - first).days + 1)}
    loads[late_day - timedelta(days=1)] = 600.0
    with TestClient(app) as client:
        ids = _seed(client, {"early": (early_day, 10000.0, 2520), "late": (late_day, 21100.0, 5600)}, loads, gap)
        days = {d.date: d for d in _chart().days}
        early_before, early_earlier = days[early_day - timedelta(days=1)], days[early_day - timedelta(days=29)]
        late_before, late_earlier = days[late_day - timedelta(days=1)], days[late_day - timedelta(days=29)]
        print(f"  early: before {early_before}; earlier ctl {early_earlier.ctl}")
        print(f"  late: before {late_before}; earlier ctl {late_earlier.ctl}")
        assert (early_before.provisional, early_before.after_gap) == (True, False)
        assert (late_before.provisional, late_before.after_gap) == (False, True)
        assert late_before.ctl >= 0.7 * late_earlier.ctl
        riegel = _riegel(client)
    assert riegel["excluded"] == []
    early, late = riegel["efforts"]
    assert (early["session_id"], late["session_id"]) == (ids["early"], ids["late"])
    assert (early["fitness_provisional"], early["fitness_after_gap"]) == (True, False)
    assert (late["fitness_provisional"], late["fitness_after_gap"]) == (False, True)
    assert (early["fitness_before"], early["fitness_earlier"]) == (early_before.ctl, early_earlier.ctl)
    assert (late["fitness_before"], late["fitness_earlier"]) == (late_before.ctl, late_earlier.ctl)


# ---------------------------------------------------------------------------
# A chart that cannot be built
# ---------------------------------------------------------------------------


def test_a_chart_that_cannot_be_built_is_a_500() -> None:
    """The section 4 store with one filler running session's load row deleted: the chart raises,
    and ``GET /state`` answers 500 with the chart's own message, serving neither determinant."""
    with TestClient(app) as client:
        _seed_section_4(client)
        conn = db_module.get_connection()
        try:
            (victim,) = conn.execute(
                "SELECT session_id FROM sessions WHERE source_device = ? ORDER BY start_time LIMIT 1",
                (ctl_path.DEVICE,),
            ).fetchone()
            conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (victim,))
            conn.commit()
            rows = db_module.read_session_load_rows(conn)
        finally:
            conn.close()
        with pytest.raises(ValueError) as raised:
            load_chart.build_chart(rows, ZONE, TODAY)
        print(f"  chart message {raised.value}")
        response = client.get("/state")
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 500
    assert response.json() == {"detail": str(raised.value)}
    assert victim in str(raised.value)
