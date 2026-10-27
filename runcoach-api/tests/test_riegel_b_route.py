"""``GET /state`` serves the endurance exponent b beside threshold pace (F029 AC5, AC6, the route's 500).

Races are stored as ``test_threshold_pace_qualifying.py`` stores them: ``db.persist`` with
synthetic 1 Hz flat records (``distance = t * 1000 / pace``, so F013's GAP pace is the given pace
and the GAP-equivalent distance is the distance run), untagged, then tagged through F023's ``PUT
/race-tags``. Every session's saved load is replaced with a chosen value through
``support/session_loads.py`` (or ``support/ctl_path.py`` where the chart's CTL must vary day by
day), so the load chart the fitness screen reads is fixed by the test. The clock is frozen through
``main._utcnow`` and every request runs under the autouse isolated store, whose
``athlete_timezone`` is UTC.

The expected fits come from ``support/riegel_oracle.py``, which imports nothing from
``runcoach_api``; the reference section 1 constants and t table are typed in here from the
reference.
"""

from __future__ import annotations

import copy
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
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import load_chart, riegel_b
from runcoach_api.models import Context, Record, Session

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

FIVE_K = (datetime(2026, 9, 6, 7, 0, tzinfo=UTC), 5000.0, 252.0)  # 1260 s: in the threshold band
TEN_K = (datetime(2026, 9, 13, 7, 0, tzinfo=UTC), 10000.0, 262.0)  # 2620 s: in the band
HALF = (datetime(2026, 9, 20, 7, 0, tzinfo=UTC), 21000.0, 268.0)  # 5628 s: outside the band
MARATHON = (datetime(2026, 9, 27, 7, 0, tzinfo=UTC), 42000.0, 285.0)  # 11970 s: outside the band

# F029 reference section 1, typed in from the reference (not read from the estimator).
REFERENCE_PARAMETERS = {
    "window_days": 1095,
    "fade_days": 182,
    "min_races": 3,
    "min_spread": 2.0,
    "fitness_drop": 0.30,
    "fitness_lookback_days": 28,
    "population_b": 1.06,
    "population_error": 0.05,
    "coverage": 0.80,
    "high_max_error": 0.02,
    "medium_max_error": 0.04,
}
# Student's t at 0.90 for 1 to 30 degrees of freedom, to the reference's 6 decimals.
REFERENCE_T = {
    1: 3.077684, 2: 1.885618, 3: 1.637744, 4: 1.533206, 5: 1.475884, 6: 1.439756, 7: 1.414924,
    8: 1.396815, 9: 1.383029, 10: 1.372184, 11: 1.363430, 12: 1.356217, 13: 1.350171, 14: 1.345030,
    15: 1.340606, 16: 1.336757, 17: 1.333379, 18: 1.330391, 19: 1.327728, 20: 1.325341, 21: 1.323188,
    22: 1.321237, 23: 1.319460, 24: 1.317836, 25: 1.316345, 26: 1.314972, 27: 1.313703, 28: 1.312527,
    29: 1.311434, 30: 1.310415,
}  # fmt: skip
REFERENCE_Z = 1.2815515655446004
THRESHOLD_UNCHANGED = ("estimate", "error", "short_races_only", "efforts", "parameters")


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
contract_validate = _load_support("contract_validate")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _store(start: datetime, distance_m: float, pace: float, *, flags: list[str] | None = None) -> str:
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
        quality_flags=list(flags or []),
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


def _plant_every_load(value: float = 50.0) -> None:
    """Every stored session's saved load replaced with ``value``, so the chart is the test's."""
    conn = db_module.get_connection()
    try:
        for (session_id,) in conn.execute("SELECT session_id FROM sessions").fetchall():
            session_loads.plant_load(conn, session_id, value=value)
    finally:
        conn.close()


def _stored(session_id: str) -> sqlite3.Row:
    conn = db_module.get_connection()
    try:
        return conn.execute("SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()


def _put_tags(client: TestClient, session_ids: list[str], *, race: bool = True) -> None:
    """Connect's mark for each stored run, as ``test_threshold_pace_qualifying.py`` sends it: its start
    second in UTC, its distance 0.5 % long and its duration 0.5 % short (inside F023's 1 %)."""
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
                "race": race,
            }
        )
    response = client.put("/race-tags", json={"items": items})
    assert response.status_code == 200, response.text
    outcomes = [(i["outcome"], i["session_id"]) for i in response.json()["items"]]
    print(f"  race-tags {outcomes}")
    assert [s for _o, s in outcomes] == session_ids, outcomes


def _races(client: TestClient, *races: tuple[datetime, float, float]) -> list[str]:
    ids = [_store(*race) for race in races]
    _put_tags(client, ids)
    _plant_every_load()
    return ids


def _body(client: TestClient, as_of: str | None = None) -> dict:
    response = client.get("/state", params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    return response.json()


def _entries(body: dict) -> tuple[dict, dict]:
    threshold, riegel = body["determinants"]
    assert (threshold["name"], riegel["name"]) == ("functional_threshold_pace", "riegel_exponent")
    return threshold, riegel


def _riegel(body: dict) -> dict:
    return _entries(body)[1]


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


def test_state_serves_two_determinants_in_order() -> None:
    """Threshold pace first, b second; b is the fit of the three races, its interval around it, its
    trend unknown, each race at weight 1 with its GAP-equivalent distance the distance run."""
    with TestClient(app) as client:
        ids = _races(client, FIVE_K, TEN_K, HALF)
        body = _body(client)
    names = [d["name"] for d in body["determinants"]]
    print(f"  names {names}")
    assert names == ["functional_threshold_pace", "riegel_exponent"]
    riegel = body["determinants"][1]
    estimate = riegel["estimate"]
    print(f"  riegel estimate {estimate}; error {riegel['error']}; t {riegel['t_multiplier']}")
    print(f"  efforts {[(e['session_id'], e['duration_s'], e['gap_equivalent_m']) for e in riegel['efforts']]}")
    assert riegel["unavailable"] is None
    assert estimate["interval"]["low"] <= estimate["value"] <= estimate["interval"]["high"]
    assert riegel["trend"] == {"direction": "unknown", "rate": None}
    assert (estimate["unit"], estimate["provisional"]) == ("1", False)
    assert riegel["population_default"] is False
    assert [e["session_id"] for e in riegel["efforts"]] == ids
    assert riegel["excluded"] == []
    for effort, (_start, distance, pace) in zip(riegel["efforts"], (FIVE_K, TEN_K, HALF), strict=True):
        assert effort["weight"] == 1.0
        assert effort["distance_m"] == distance
        assert effort["gap_pace_s_per_km"] == pytest.approx(pace, rel=1e-9)
        assert effort["gap_equivalent_m"] == pytest.approx(distance, rel=1e-6)
    expected = riegel_oracle.fit([(e["gap_equivalent_m"], e["duration_s"], e["weight"]) for e in riegel["efforts"]])
    print(f"  oracle b {expected.b} error {expected.error} t {expected.t}")
    assert estimate["value"] == pytest.approx(expected.b, abs=1e-9)
    assert riegel["error"] == pytest.approx(expected.error, abs=1e-9)
    assert riegel["t_multiplier"] == pytest.approx(expected.t, abs=1e-6)
    assert (estimate["interval"]["low"], estimate["interval"]["high"]) == (
        pytest.approx(expected.low, abs=1e-6),
        pytest.approx(expected.high, abs=1e-6),
    )
    assert [e["residual"] for e in riegel["efforts"]] == pytest.approx(list(expected.residuals), abs=1e-9)


def test_parameters_equal_the_reference() -> None:
    """``parameters`` is the reference section 1 keys and values; the t table rows the multiplier
    comes from match the reference's, and a population and a fitted entry serve z and t(1)."""
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K)
        population = _riegel(_body(client))
        _races(client, HALF)
        fitted = _riegel(_body(client))
    print(f"  parameters {population['parameters']}")
    for entry in (population, fitted):
        assert entry["parameters"] == REFERENCE_PARAMETERS
        assert [type(entry["parameters"][k]) for k in REFERENCE_PARAMETERS] == [
            type(v) for v in REFERENCE_PARAMETERS.values()
        ]
    print(f"  t table {dict(riegel_b.T_TABLE)}")
    assert sorted(riegel_b.T_TABLE) == sorted(REFERENCE_T)
    for df, t in REFERENCE_T.items():
        assert round(riegel_b.T_TABLE[df], 6) == t, df
    assert population["population_default"] is True
    assert population["t_multiplier"] == REFERENCE_Z
    assert population["error"] == 0.05 and population["estimate"]["value"] == 1.06
    assert fitted["population_default"] is False
    assert round(fitted["t_multiplier"], 6) == REFERENCE_T[1]


def test_threshold_entry_is_byte_identical_with_and_without_a_marathon() -> None:
    """A tagged marathon moves b and nothing of threshold pace but one ``outside_threshold_band``
    row in its ``excluded``."""
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K)
        without, riegel_without = _entries(_body(client))
        [marathon] = _races(client, MARATHON)
        with_, riegel_with = _entries(_body(client))
    for field in THRESHOLD_UNCHANGED:
        a, b = json.dumps(without[field], sort_keys=True), json.dumps(with_[field], sort_keys=True)
        print(f"  {field}: {hashlib.sha256(a.encode()).hexdigest()[:12]} / {hashlib.sha256(b.encode()).hexdigest()[:12]}")
        assert a == b, field
    print(f"  excluded without {without['excluded']}; with {with_['excluded']}")
    added = [e for e in with_["excluded"] if e not in without["excluded"]]
    assert [e for e in without["excluded"] if e not in with_["excluded"]] == []
    assert [(e["session_id"], e["reason"]) for e in added] == [(marathon, "outside_threshold_band")]
    assert len(with_["excluded"]) == len(without["excluded"]) + 1
    assert riegel_without["population_default"] is True
    assert riegel_with["population_default"] is False
    assert marathon in [e["session_id"] for e in riegel_with["efforts"]]


# ---------------------------------------------------------------------------
# Recompute on read
# ---------------------------------------------------------------------------


def _snapshot() -> tuple[dict, str]:
    """Row counts of every table and the sha256 of every store file, the WAL included."""
    data_dir = db_module._load_config_cached().data_dir
    conn = sqlite3.connect(data_dir / "runcoach.db")
    try:
        tables = [n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()
    digest = hashlib.sha256()
    for path in sorted(data_dir.glob("runcoach.db*")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return counts, digest.hexdigest()


def test_state_writes_nothing() -> None:
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K, HALF)
        conn = db_module.get_connection()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        before = _snapshot()
        assert _riegel(_body(client))["estimate"] is not None
        _body(client, "2026-09-15")
        after = _snapshot()
    print(f"  before {before}\n  after  {after}")
    assert before[0]["sessions"] == 3
    assert after == before


def test_untagging_a_race_removes_it() -> None:
    with TestClient(app) as client:
        five, ten, half = _races(client, FIVE_K, TEN_K, HALF)
        tagged = _riegel(_body(client))
        _put_tags(client, [half], race=False)
        untagged = _riegel(_body(client))
    print(f"  tagged {[e['session_id'] for e in tagged['efforts']]}; untagged {[e['session_id'] for e in untagged['efforts']]}")
    assert [e["session_id"] for e in tagged["efforts"]] == [five, ten, half]
    assert [e["session_id"] for e in untagged["efforts"]] == [five, ten]
    assert half not in [e["session_id"] for e in untagged["excluded"]]
    assert (tagged["population_default"], untagged["population_default"]) == (False, True)


def test_races_after_as_of_are_ignored() -> None:
    """A race after ``as_of`` is in neither list; the weights are measured from the instant used."""
    with TestClient(app) as client:
        five, ten, half = _races(client, FIVE_K, TEN_K, HALF)
        body = _body(client, "2026-09-15")
        now = _riegel(_body(client))
    riegel = _riegel(body)
    print(f"  as_of {body['as_of']}; efforts {riegel['efforts']}; excluded {riegel['excluded']}")
    assert datetime.fromisoformat(body["as_of"]) == datetime(2026, 9, 15, 23, 59, 59, 999999, tzinfo=UTC)
    assert [e["session_id"] for e in riegel["efforts"]] == [five, ten]
    assert riegel["excluded"] == []
    assert riegel["population_default"] is True
    assert [e["session_id"] for e in now["efforts"]] == [five, ten, half]


def test_a_race_outside_the_window_is_listed_and_a_fading_race_is_weighted() -> None:
    """Ages are measured from ``as_of``: at 1100 days a race is ``outside_window``; at 1004 days its
    weight is (1095 - 1004) / 182 = 0.5."""
    old = (NOW - timedelta(days=1100), 5000.0, 252.0)
    fading = (NOW - timedelta(days=1004), 10000.0, 262.0)
    with TestClient(app) as client:
        old_id, fading_id = _races(client, old, fading)
        _plant_every_load(0.0)  # a flat chart at 0: no fitness figure to screen on
        riegel = _riegel(_body(client))
    print(f"  efforts {[(e['session_id'], e['weight']) for e in riegel['efforts']]}; excluded {riegel['excluded']}")
    assert [(e["session_id"], e["reason"]) for e in riegel["excluded"]] == [(old_id, "outside_window")]
    [effort] = riegel["efforts"]
    assert effort["session_id"] == fading_id
    assert effort["weight"] == pytest.approx(0.5, abs=1e-12)


# ---------------------------------------------------------------------------
# Fitness from the load chart
# ---------------------------------------------------------------------------


def _chart_days(client: TestClient) -> dict[date, dict]:
    response = client.get("/metrics/load")
    assert response.status_code == 200, response.text
    return {date.fromisoformat(d["date"]): d for d in response.json()["days"]}


def test_fitness_is_the_chart_on_the_day_before_and_29_days_before() -> None:
    """The chart's CTL varies every day, so the served figures name their days: ``fitness_before``
    is the local day before the race (not the race day), ``fitness_earlier`` the day 29 days before
    the race (not 28, not 30), with the day-before day's two flags. A race in the chart's first days
    has its earlier figure null and the day-before day's ``provisional`` carried."""
    zone = ZoneInfo("UTC")
    start, end = date(2026, 6, 1), NOW.date()
    loads = {start + timedelta(days=n): 30.0 + 4.0 * (n % 9) for n in range((end - start).days + 1)}
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        ctl_path.plant(conn, loads, zone)
    finally:
        conn.close()
    early_race = (datetime(2026, 6, 11, 7, 0, tzinfo=UTC), 5000.0, 252.0)
    race = (datetime(2026, 9, 20, 7, 0, tzinfo=UTC), 10000.0, 262.0)
    with TestClient(app) as client:
        early_id = _store(*early_race)
        race_id = _store(*race)
        _put_tags(client, [early_id, race_id])
        conn = db_module.get_connection()
        try:
            session_loads.plant_load(conn, early_id, value=60.0)
            session_loads.plant_load(conn, race_id, value=90.0)
        finally:
            conn.close()
        days = _chart_days(client)
        riegel = _riegel(_body(client))
    early, effort = riegel["efforts"]
    assert (early["session_id"], effort["session_id"]) == (early_id, race_id)
    race_day = race[0].date()
    ctl = {offset: days[race_day - timedelta(days=offset)]["ctl"] for offset in (0, 1, 28, 29, 30)}
    print(f"  chart CTL by days before the race {ctl}")
    print(
        f"  served before {effort['fitness_before']} earlier {effort['fitness_earlier']} drop {effort['fitness_drop']}"
        f" provisional {effort['fitness_provisional']} after_gap {effort['fitness_after_gap']}"
    )
    assert len(set(ctl.values())) == len(ctl), ctl  # every candidate day reads a different CTL
    assert effort["fitness_before"] == ctl[1]
    assert effort["fitness_earlier"] == ctl[29]
    assert effort["fitness_drop"] == pytest.approx(1 - ctl[1] / ctl[29], abs=1e-12)
    before_day = days[race_day - timedelta(days=1)]
    assert (effort["fitness_provisional"], effort["fitness_after_gap"]) == (
        before_day["provisional"],
        before_day["after_gap"],
    )
    early_before = days[early_race[0].date() - timedelta(days=1)]
    print(f"  early race {early}; its day-before chart day {early_before}")
    assert early["fitness_earlier"] is None and early["fitness_drop"] is None
    assert early["fitness_before"] == early_before["ctl"]
    assert early["fitness_provisional"] is True and early_before["provisional"] is True


def test_a_missing_load_row_is_a_500_without_a_tagged_race() -> None:
    """No race tagged; one session's saved load row deleted: the chart cannot be built, and the
    route answers 500 with the chart's own message, withholding threshold pace too."""
    session_id = _store(*FIVE_K)
    with TestClient(app) as client:
        conn = db_module.get_connection()
        try:
            conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
            conn.commit()
            rows = db_module.read_session_load_rows(conn)
        finally:
            conn.close()
        with pytest.raises(ValueError) as raised:
            load_chart.build_chart(rows, ZoneInfo("UTC"), NOW.date())
        response = client.get("/state")
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 500
    assert response.json() == {"detail": str(raised.value)}
    assert session_id in str(raised.value)


def test_an_empty_stored_start_time_is_a_500_with_the_chart_message() -> None:
    """A tagged race whose stored ``start_time`` is ``''`` (only a write from outside the API leaves
    one): the chart reads every session's start time first and cannot place it, so the route
    answers the chart's 500, as for a naive one, before any Riegel row is screened."""
    with TestClient(app) as client:
        five, _ten = _races(client, FIVE_K, TEN_K)
        conn = db_module.get_connection()
        try:
            conn.execute("UPDATE sessions SET start_time = '' WHERE session_id = ?", (five,))
            conn.commit()
            rows = db_module.read_session_load_rows(conn)
        finally:
            conn.close()
        with pytest.raises(ValueError) as raised:
            load_chart.build_chart(rows, ZoneInfo("UTC"), NOW.date())
        response = client.get("/state")
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 500
    assert response.json() == {"detail": str(raised.value)}


# ---------------------------------------------------------------------------
# One record read per race
# ---------------------------------------------------------------------------


def test_features_are_computed_once_per_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-band 5K (read by the threshold path, its features reused) and an out-of-band marathon
    are each read once; an out-of-band race carrying the stored ``smart_recording`` flag is never
    read."""
    with TestClient(app) as client:
        five, marathon = _races(client, FIVE_K, MARATHON)
        smart = _store(HALF[0], HALF[1], HALF[2], flags=["smart_recording"])
        _put_tags(client, [smart])
        _plant_every_load()
        reads: list[str] = []
        real = db_module.read_session_feature_inputs

        def counted(conn, session_id):
            reads.append(session_id)
            return real(conn, session_id)

        monkeypatch.setattr(db_module, "read_session_feature_inputs", counted)
        threshold, riegel = _entries(_body(client))
    print(f"  reads {reads}")
    assert sorted(reads) == sorted([five, marathon])
    assert [e["session_id"] for e in riegel["efforts"]] == [five, marathon]
    assert [(e["session_id"], e["reason"]) for e in riegel["excluded"]] == [(smart, "smart_recording")]
    assert [e["session_id"] for e in threshold["efforts"]] == [five]


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def _validate(contract: dict, body: dict) -> None:
    determinant = contract["components"]["schemas"]["Determinant"]
    for index, entry in enumerate(body["determinants"]):
        errors = contract_validate.validate(contract, determinant, entry, f"$.determinants[{index}]")
        print(f"  {entry['name']}: {errors or 'valid'}")
        assert errors == [], errors


def test_a_two_determinant_response_validates(contract: dict) -> None:
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K, HALF)
        body = _body(client)
    _validate(contract, body)
    riegel = copy.deepcopy(_riegel(body))
    riegel["efforts"][0]["weight"] = "1"
    errors = contract_validate.validate(contract, contract["components"]["schemas"]["Determinant"], riegel)
    print(f"  mutated weight: {errors}")
    assert any("$.efforts[0].weight" in e for e in errors), errors


def test_a_population_default_response_validates(contract: dict) -> None:
    with TestClient(app) as client:
        _races(client, FIVE_K, TEN_K)
        body = _body(client)
    _validate(contract, body)
    riegel = _riegel(body)
    assert riegel["population_default"] is True and riegel["estimate"]["provisional"] is True
    assert riegel["estimate"]["confidence"] == "low"
    assert [e["residual"] for e in riegel["efforts"]] == [None, None]


def test_a_not_finite_response_validates(contract: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A race whose GAP pace is so small that its GAP-equivalent distance is infinite: b is
    unavailable, ``estimate``, ``error`` and ``t_multiplier`` null, the race listed with a null
    GAP-equivalent distance, the parameters served. The race is outside the threshold band, so
    threshold pace never reads it."""
    real = db_module._race_features
    with TestClient(app) as client:
        five, ten, half = _races(client, FIVE_K, TEN_K, HALF)

        def absurd(conn, session_id):
            body = real(conn, session_id)
            if session_id == half:
                body["features"]["gap_avg_pace_s_per_km"] = {"value": 1e-320, "unavailable": None}
            return body

        monkeypatch.setattr(db_module, "_race_features", absurd)
        body = _body(client)
    _validate(contract, body)
    threshold, riegel = _entries(body)
    print(f"  riegel {riegel}")
    assert riegel["unavailable"] == "estimate_not_finite"
    assert riegel["estimate"] is None and riegel["error"] is None and riegel["t_multiplier"] is None
    assert [e["session_id"] for e in riegel["efforts"]] == [five, ten, half]
    assert riegel["efforts"][2]["gap_equivalent_m"] is None
    assert riegel["efforts"][2]["gap_pace_s_per_km"] == 1e-320
    assert math.isfinite(riegel["efforts"][0]["gap_equivalent_m"])
    assert riegel["parameters"] == REFERENCE_PARAMETERS
    assert threshold["unavailable"] is None
