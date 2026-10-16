"""``GET /state`` (F024 AC2 through the route, AC4, AC5 and AC6 but the contract; reference section 5).

Races are stored through ``db.persist`` as ``Session(activity_tag="race", ...)`` with synthetic
1 Hz flat records, ``distance = t * 1000 / pace``, so F013's GAP pace is the given pace exactly
(flat altitude: every segment graded at g = 1). Every request runs under the autouse isolated
store, with the clock frozen through ``main._utcnow``.

The worked example (reference section 4) is checked against the table's own printed values and
against ``_oracle``, a closed-form recomputation in this file that imports nothing from
``metrics.threshold_pace`` (the AC2 oracle; the module's own suite carries the other).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
A_START = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)
B_START = A_START + timedelta(days=42)

PARAMETERS = {
    "typical_error": 0.025,
    "doubling_days": 42,
    "coverage": 0.80,
    "high_max_error": 0.025,
    "medium_max_error": 0.05,
    "short_race_s": 1800,
    "workout_min_active_s": 600,
}


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


@pytest.fixture
def zone_config(isolated_data_dir, monkeypatch: pytest.MonkeyPatch):
    """``zone_config("Pacific/Auckland")``: the isolated store with another ``athlete_timezone``."""

    def _set(name: str) -> None:
        config = AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=isolated_data_dir,
            resting_hrv_profile_names=[],
            athlete_timezone=name,
        )
        monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
        db_module._load_config_cached.cache_clear()

    return _set


def _records(duration_s: float, pace_s_per_km: float) -> list[Record]:
    speed = 1000.0 / pace_s_per_km
    return [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
        for t in range(int(duration_s) + 1)
    ]


def _store(
    start: datetime,
    *,
    pace: float = 270.0,
    duration_s: float = 1800.0,
    tag: str | None = "race",
    sport: str = "running",
    device: str = "fr945",
    flags: list[str] | None = None,
    timer_time_s: float | None | str = "same",
    summary: dict | None | str = "same",
    with_records: bool = True,
) -> str:
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id(device, start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device=device,
        activity_tag=tag,
        timer_time_s=duration_s if timer_time_s == "same" else timer_time_s,
        quality_flags=list(flags or []),
        summary={"duration_s": duration_s} if summary == "same" else summary,
        context=Context(),
    )
    records = _records(duration_s, pace) if with_records else []
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {})
    finally:
        conn.close()
    return session.session_id


def _state(client: TestClient, as_of: str | None = None):
    return client.get("/state", params={} if as_of is None else {"as_of": as_of})


def _ftp(body: dict) -> dict:
    assert [d["name"] for d in body["determinants"]] == ["functional_threshold_pace"]
    return body["determinants"][0]


def _ok(client: TestClient, as_of: str | None = None) -> dict:
    response = _state(client, as_of)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_as_of_422(response) -> None:
    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [["query", "as_of"]], response.text


def _assert_no_qualifying_effort(determinant: dict) -> None:
    assert determinant["estimate"] is None
    assert determinant["unavailable"] == "no_qualifying_effort"
    assert determinant["error"] is None
    assert determinant["efforts"] == []
    assert determinant["short_races_only"] is False


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------------------
# The independent oracle (reference sections 1 and 3, closed form)
# ---------------------------------------------------------------------------


def _oracle(races: list[tuple[float, float]], at_day: float) -> tuple[float, float, float, float, str]:
    """``(pace, error %, low, high, label)`` at ``at_day`` for ``(day, pace)`` races, recomputed here."""
    r = 0.025**2
    q = 3 * r / 42
    x = p = None
    last = None
    for day, pace in sorted(races):
        y = math.log(1000 / pace)
        if x is None:
            x, p = y, r
        else:
            p += q * (day - last)
            k = p / (p + r)
            x += k * (y - x)
            p *= 1 - k
        last = day
    p += q * (at_day - last)
    z = 1.2815515655446004
    label = "high" if p <= r else "medium" if p <= 4 * r else "low"
    return (
        1000 / math.exp(x),
        100 * math.sqrt(p),
        1000 / math.exp(x + z * math.sqrt(p)),
        1000 / math.exp(x - z * math.sqrt(p)),
        label,
    )


def _assert_row(determinant: dict, row: tuple[float, float, float, float, str], races, at_day: float) -> None:
    estimate = determinant["estimate"]
    assert estimate is not None, determinant
    oracle = _oracle(races, at_day)
    got = (estimate["value"], 100 * determinant["error"], estimate["interval"]["low"], estimate["interval"]["high"])
    for name, value, table, recomputed, tol in zip(
        ("pace", "error %", "low", "high"), got, row[:4], oracle[:4], (0.01, 0.001, 0.01, 0.01)
    ):
        assert abs(value - table) <= tol, f"{name}: served {value}, table {table}"
        assert abs(value - recomputed) <= 1e-9, f"{name}: served {value}, oracle {recomputed}"
    assert estimate["confidence"] == row[4] == oracle[4]


# ---------------------------------------------------------------------------
# AC4: unavailable
# ---------------------------------------------------------------------------


def test_empty_store_is_unavailable_no_qualifying_effort() -> None:
    with TestClient(app) as client:
        body = _ok(client)
    determinant = _ftp(body)
    _assert_no_qualifying_effort(determinant)
    assert determinant["excluded"] == []
    assert _instant(body["as_of"]) == NOW


def test_only_a_marathon_tagged_is_unavailable_and_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 4, 26, 8, 0, tzinfo=UTC)
    marathon = _store(start, duration_s=11400.0, with_records=False)
    reads: list[str] = []
    real = db_module.read_session_feature_inputs
    monkeypatch.setattr(
        db_module, "read_session_feature_inputs", lambda conn, sid: reads.append(sid) or real(conn, sid)
    )
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert determinant["excluded"] == [
        {
            "session_id": marathon,
            "start_time": determinant["excluded"][0]["start_time"],
            "kind": "race",
            "reason": "outside_threshold_band",
        }
    ]
    assert _instant(determinant["excluded"][0]["start_time"]) == start
    assert reads == [], "a race outside the band must cost no records read (reference section 2 rows 1-2 first)"


def test_untagged_10k_runs_are_unavailable() -> None:
    for day in (1, 8, 15):
        _store(datetime(2026, 9, day, 7, 0, tzinfo=UTC), duration_s=2400.0, tag=None)
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert determinant["excluded"] == []


def test_only_a_flagged_race_is_unavailable() -> None:
    smart = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), flags=["smart_recording"])
    both = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), flags=["smart_recording", "distance_regressed"])
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [
        (smart, "smart_recording"),
        (both, "distance_regressed"),
    ]


def _store_regressing(start: datetime, *, flags: list[str]) -> str:
    """An in-band race whose records hold one 1 s segment where distance goes down.

    F013's ``build_time_base`` marks a sampled segment (0 < dt <= 5 s) with a negative distance
    step as regressed, so ``compute_session_features`` reports ``distance_regressed`` from these
    records whatever the stored ``quality_flags`` say.
    """
    race = _store(start, flags=flags)
    conn = db_module.get_connection()
    try:
        moved = conn.execute(
            "UPDATE records SET distance = distance - 10.0 WHERE session_id = ? AND t = 900.0", (race,)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    assert moved == 1, "the regressing record must exist"
    return race


def test_a_smart_recording_race_whose_records_regress_reads_distance_regressed() -> None:
    """The stored ``smart_recording`` is never a short-cut: ``distance_regressed`` comes from the
    records and is first in the reason order (reference section 2 row 3)."""
    race = _store_regressing(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), flags=["smart_recording"])
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [(race, "distance_regressed")]


def test_an_unflagged_race_whose_records_regress_reads_distance_regressed() -> None:
    """No stored flag at all: the exclusion can only come from F013's flags computed from records."""
    race = _store_regressing(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), flags=[])
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [(race, "distance_regressed")]


# ---------------------------------------------------------------------------
# AC2: the worked example through the route
# ---------------------------------------------------------------------------

ROW_AFTER_A = (270.00, 2.500, 261.49, 278.79, "high")
ROW_BEFORE_B = (270.00, 5.000, 253.24, 287.87, "medium")
ROW_AFTER_B = (261.97, 2.236, 254.57, 269.59, "high")
ROW_B_PLUS_30 = (261.97, 4.289, 247.96, 276.77, "medium")
ROW_B_PLUS_90 = (261.97, 6.722, 240.35, 285.54, "low")
ROW_SAME_INSTANT = (264.95, 1.768, 259.02, 271.02, "high")


def test_worked_example_through_the_route() -> None:
    a = _store(A_START, pace=270.0)
    with TestClient(app) as client:
        # The pre-B row is asked with A alone tagged (reference section 4).
        _assert_row(_ftp(_ok(client, A_START.isoformat())), ROW_AFTER_A, [(0, 270.0)], 0)
        _assert_row(_ftp(_ok(client, B_START.isoformat())), ROW_BEFORE_B, [(0, 270.0)], 42)
        b = _store(B_START, pace=260.0)
        races = [(0, 270.0), (42, 260.0)]
        after_b = _ftp(_ok(client, B_START.isoformat()))
        _assert_row(after_b, ROW_AFTER_B, races, 42)
        assert [(e["session_id"], e["gain"]) for e in after_b["efforts"]] == [
            (a, 1.0),
            (b, pytest.approx(0.8, abs=1e-12)),
        ]
        _assert_row(_ftp(_ok(client, (B_START + timedelta(days=30)).isoformat())), ROW_B_PLUS_30, races, 72)
        _assert_row(_ftp(_ok(client, (B_START + timedelta(days=90)).isoformat())), ROW_B_PLUS_90, races, 132)


def test_worked_example_same_instant_row() -> None:
    """The table's last row: A and B on one instant instead (two devices, one start second)."""
    _store(B_START, pace=270.0, device="fr945-a")
    _store(B_START, pace=260.0, device="fr945-b")
    with TestClient(app) as client:
        _assert_row(_ftp(_ok(client, B_START.isoformat())), ROW_SAME_INSTANT, [(42, 270.0), (42, 260.0)], 42)


def test_worked_example_stored_in_reverse_order_is_the_same() -> None:
    _store(B_START, pace=260.0)
    _store(A_START, pace=270.0)
    races = [(0, 270.0), (42, 260.0)]
    with TestClient(app) as client:
        _assert_row(_ftp(_ok(client, B_START.isoformat())), ROW_AFTER_B, races, 42)
        _assert_row(_ftp(_ok(client, (B_START + timedelta(days=30)).isoformat())), ROW_B_PLUS_30, races, 72)


def test_a_third_race_at_bs_instant_moves_pace_less_than_b_did() -> None:
    _store(A_START, pace=270.0, device="pair-a")
    _store(B_START, pace=260.0, device="pair-b")
    with TestClient(app) as client:
        before_b = _ftp(_ok(client, (B_START - timedelta(seconds=1)).isoformat()))
        after_b = _ftp(_ok(client, B_START.isoformat()))
        third = _store(B_START, pace=260.0, device="pair-c")
        after_c = _ftp(_ok(client, B_START.isoformat()))
    assert [e["gain"] for e in after_c["efforts"] if e["session_id"] == third] == [pytest.approx(0.444, abs=0.0005)]
    b_move = before_b["estimate"]["value"] - after_b["estimate"]["value"]
    third_move = after_b["estimate"]["value"] - after_c["estimate"]["value"]
    assert 0 < third_move < b_move


# ---------------------------------------------------------------------------
# AC6: the response
# ---------------------------------------------------------------------------


def test_parameters_trend_readiness_gap_to_goal() -> None:
    race = _store(datetime(2026, 9, 20, 8, 0, tzinfo=UTC), pace=262.0, duration_s=2400.0)
    with TestClient(app) as client:
        body = _ok(client)
    assert body["readiness"] is None
    assert body["gap_to_goal"] is None
    determinant = _ftp(body)
    assert determinant["parameters"] == PARAMETERS
    assert determinant["trend"] == {"direction": "unknown", "rate": None}
    assert determinant["unavailable"] is None
    estimate = determinant["estimate"]
    assert estimate["unit"] == "s/km"
    assert estimate["provisional"] is False
    assert estimate["interval"]["low"] < estimate["value"] < estimate["interval"]["high"]
    assert determinant["short_races_only"] is False
    [effort] = determinant["efforts"]
    assert effort["session_id"] == race
    assert effort["duration_s"] == 2400.0
    assert effort["gap_pace_s_per_km"] == pytest.approx(262.0, abs=1e-9)
    assert effort["gap_coverage"] == pytest.approx(1.0)
    assert effort["gps_degraded_fraction"] == 0.0
    assert effort["gain"] == 1.0
    assert (effort["kind"], effort["active_blocks"]) == ("race", None)


def test_timezone_is_served(zone_config) -> None:
    zone_config("Pacific/Auckland")
    with TestClient(app) as client:
        assert _ok(client)["timezone"] == "Pacific/Auckland"


def test_rtss_still_reads_no_threshold_pace() -> None:
    race = _store(datetime(2026, 9, 20, 8, 0, tzinfo=UTC), pace=262.0, duration_s=2400.0)
    with TestClient(app) as client:
        assert _ftp(_ok(client))["estimate"] is not None
        load = client.get(f"/sessions/{race}/load")
    assert load.status_code == 200, load.text
    assert load.json()["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}


# ---------------------------------------------------------------------------
# AC5: as_of
# ---------------------------------------------------------------------------


def test_efforts_after_as_of_are_ignored() -> None:
    first = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0)
    _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC), pace=250.0)
    _store(datetime(2026, 9, 21, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    with TestClient(app) as client:
        determinant = _ftp(_ok(client, "2026-09-10T00:00:00+00:00"))
        everything = _ftp(_ok(client))
    assert [e["session_id"] for e in determinant["efforts"]] == [first]
    assert determinant["excluded"] == []
    assert determinant["estimate"]["value"] == pytest.approx(270.0, abs=1e-9)
    assert len(everything["efforts"]) == 2 and len(everything["excluded"]) == 1


def test_date_means_end_of_local_day(zone_config) -> None:
    zone_config("Pacific/Auckland")
    zone = ZoneInfo("Pacific/Auckland")
    last_second = datetime(2026, 10, 10, 23, 59, 59, tzinfo=zone)
    midnight = datetime(2026, 10, 11, 0, 0, tzinfo=zone)
    inside = _store(last_second, device="inside")
    _store(midnight, device="outside")
    with TestClient(app) as client:
        body = _ok(client, "2026-10-10")
    determinant = _ftp(body)
    assert [e["session_id"] for e in determinant["efforts"]] == [inside]
    assert determinant["excluded"] == []
    as_of = _instant(body["as_of"])
    assert as_of < midnight
    assert midnight - as_of <= timedelta(milliseconds=1)


def test_today_is_accepted_and_clamped_to_now(zone_config) -> None:
    with TestClient(app) as client:
        assert _instant(_ok(client, NOW.date().isoformat())["as_of"]) == NOW
    # At 12:00Z on 14 October it is already 15 October in Auckland: that date is today there.
    zone_config("Pacific/Auckland")
    with TestClient(app) as client:
        assert _instant(_ok(client, "2026-10-15")["as_of"]) == NOW
        _assert_as_of_422(_state(client, "2026-10-16"))


def test_tomorrow_and_9999_12_31_are_422() -> None:
    with TestClient(app) as client:
        _assert_as_of_422(_state(client, (NOW.date() + timedelta(days=1)).isoformat()))
        _assert_as_of_422(_state(client, "9999-12-31"))


def test_naive_future_basic_and_week_dates_are_422() -> None:
    with TestClient(app) as client:
        for value in (
            "2026-10-01T12:00:00",
            (NOW + timedelta(seconds=1)).isoformat(),
            "2026-10-15T00:00:00Z",
            "20261014",
            "2026-W42-3",
            "20261014T120000+00:00",
        ):
            _assert_as_of_422(_state(client, value))


def test_unparseable_as_of_is_422() -> None:
    with TestClient(app) as client:
        for value in ("yesterday", "", "2026-13-01", "2026-02-30", "2026-10-01T25:00:00+00:00", "0"):
            _assert_as_of_422(_state(client, value))


def test_a_past_date_time_is_the_instant_used() -> None:
    with TestClient(app) as client:
        assert _instant(_ok(client, "2026-09-01T06:30:00+02:00")["as_of"]) == datetime(
            2026, 9, 1, 4, 30, tzinfo=UTC
        )


# ---------------------------------------------------------------------------
# Writes nothing
# ---------------------------------------------------------------------------


def _snapshot() -> tuple[list, dict]:
    conn = sqlite3.connect(db_module._load_config_cached().data_dir / "runcoach.db")
    try:
        schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        counts = {
            name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for (kind, name, _sql) in schema
            if kind == "table"
        }
        tags = conn.execute("SELECT session_id, activity_tag, quality_flags FROM sessions ORDER BY session_id").fetchall()
    finally:
        conn.close()
    return schema, {**counts, "_tags": tags}


def test_get_state_writes_nothing() -> None:
    _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0)
    _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC), flags=["smart_recording"])
    with TestClient(app) as client:
        before = _snapshot()
        assert _ftp(_ok(client))["estimate"] is not None
        _ok(client, "2026-09-10")
        after = _snapshot()
    assert before[1]["sessions"] == 3
    assert after == before


# ---------------------------------------------------------------------------
# Data the route must never 500 on (the adversarial rows of the reader)
# ---------------------------------------------------------------------------


def _corrupt(session_id: str, column: str, value) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(f"UPDATE sessions SET {column} = ? WHERE session_id = ?", (value, session_id))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("quality_flags", "not json", "quality_flags_unreadable"),
        ("quality_flags", '"smart_recording"', "quality_flags_unreadable"),
        ("quality_flags", '{"a": 1}', "quality_flags_unreadable"),
        ("quality_flags", "7", "quality_flags_unreadable"),
        ("context", "not json", "features_unavailable"),
    ],
)
def test_an_undecodable_in_band_race_is_excluded_with_its_reason(column: str, value: str, reason: str) -> None:
    good = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0)
    bad = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), pace=260.0)
    with TestClient(app) as client:
        _corrupt(bad, column, value)
        determinant = _ftp(_ok(client))
    assert [e["session_id"] for e in determinant["efforts"]] == [good]
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [(bad, reason)]


def test_a_raising_feature_computation_is_excluded_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The reader's broad except keeps the route answering, and logs what it swallowed with the
    session id, so a defect in F013's computation is not silent."""
    good = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0)
    bad = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), pace=260.0)
    real = db_module.session_features.compute_session_features

    def compute(session, rows):
        if session["session_id"] == bad:
            raise RuntimeError("a defect in the feature computation")
        return real(session, rows)

    monkeypatch.setattr(db_module.session_features, "compute_session_features", compute)
    with TestClient(app) as client, caplog.at_level("ERROR", logger="runcoach_api.db"):
        determinant = _ftp(_ok(client))
    assert [e["session_id"] for e in determinant["efforts"]] == [good]
    excluded = [(e["session_id"], e["reason"]) for e in determinant["excluded"]]
    assert excluded == [(bad, "features_unavailable")]
    logged = [r for r in caplog.records if r.name == "runcoach_api.db" and bad in r.getMessage()]
    assert len(logged) == 1, caplog.records
    assert logged[0].exc_info is not None and isinstance(logged[0].exc_info[1], RuntimeError)


@pytest.mark.parametrize(
    ("summary", "reason"),
    [
        ("not json", "summary_unreadable"),
        ('"1800"', "summary_unreadable"),
        ('{"duration_s": "1800"}', "outside_threshold_band"),
        ('{"duration_s": true}', "outside_threshold_band"),
        ('{"duration_s": NaN}', "outside_threshold_band"),
        ("{}", "outside_threshold_band"),
        (None, "outside_threshold_band"),
    ],
)
def test_the_duration_fallback_refuses_what_it_cannot_read(summary, reason: str) -> None:
    race = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), timer_time_s=None)
    with TestClient(app) as client:
        _corrupt(race, "timer_time_s", None)
        _corrupt(race, "summary", summary)
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [(race, reason)]


def test_the_summary_duration_counts_when_the_timer_is_null() -> None:
    race = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), timer_time_s=None, duration_s=2000.0)
    with TestClient(app) as client:
        _corrupt(race, "timer_time_s", None)
        determinant = _ftp(_ok(client))
    assert [(e["session_id"], e["duration_s"]) for e in determinant["efforts"]] == [(race, 2000.0)]


def test_races_of_exactly_1200_s_and_3600_s_are_read_and_used() -> None:
    """The band is closed at both ends in the reader's pre-filter, as in the estimator."""
    short = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), duration_s=1200.0)
    long = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), duration_s=3600.0)
    with TestClient(app) as client:
        determinant = _ftp(_ok(client))
    assert [(e["session_id"], e["duration_s"]) for e in determinant["efforts"]] == [(short, 1200.0), (long, 3600.0)]
    assert determinant["excluded"] == []


def test_untagged_and_non_running_rows_are_never_read() -> None:
    """Rows 1 of reference section 2 are SQL filters: an untagged running race and a tagged ride,
    both in the band and both unreadable, appear nowhere, so neither was read."""
    untagged = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), tag=None)
    ride = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), sport="cycling")
    with TestClient(app) as client:
        _corrupt(untagged, "quality_flags", "not json")
        _corrupt(ride, "quality_flags", "not json")
        determinant = _ftp(_ok(client))
    _assert_no_qualifying_effort(determinant)
    assert determinant["excluded"] == []


def test_a_date_time_with_an_offset_is_served_in_utc() -> None:
    with TestClient(app) as client:
        body = _ok(client, "2026-10-10T12:00:00+02:00")
    assert body["as_of"] == "2026-10-10T10:00:00Z"


def test_an_unreadable_race_after_as_of_is_not_excluded() -> None:
    late = _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC))
    with TestClient(app) as client:
        _corrupt(late, "quality_flags", "not json")
        determinant = _ftp(_ok(client, "2026-09-10T00:00:00+00:00"))
        everything = _ftp(_ok(client))
    assert determinant["excluded"] == []
    assert [(e["session_id"], e["reason"]) for e in everything["excluded"]] == [(late, "quality_flags_unreadable")]


def test_an_unreadable_race_is_merged_into_excluded_in_start_order() -> None:
    first = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    middle = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    last = _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    with TestClient(app) as client:
        _corrupt(middle, "quality_flags", "not json")
        determinant = _ftp(_ok(client))
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [
        (first, "outside_threshold_band"),
        (middle, "quality_flags_unreadable"),
        (last, "outside_threshold_band"),
    ]


def test_a_finite_but_absurd_gap_pace_never_serves_a_non_finite_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GAP pace of 1.7e308 s/km is finite and > 0, so it qualifies, and its interval's slow end
    overflows to infinity, which JSON cannot carry: the route serves no estimate instead."""
    race = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    real = db_module.session_features.compute_session_features

    def absurd(session, rows):
        body = real(session, rows)
        body["features"]["gap_avg_pace_s_per_km"] = {"value": 1.7e308, "unavailable": None}
        return body

    monkeypatch.setattr(db_module.session_features, "compute_session_features", absurd)
    with TestClient(app) as client:
        response = _state(client)
    assert response.status_code == 200, response.text
    assert "Infinity" not in response.text and "NaN" not in response.text
    determinant = _ftp(response.json())
    assert determinant["estimate"] is None
    assert determinant["error"] is None
    assert determinant["unavailable"] == "estimate_not_finite"
    assert [e["session_id"] for e in determinant["efforts"]] == [race]
    assert determinant["short_races_only"] is False
