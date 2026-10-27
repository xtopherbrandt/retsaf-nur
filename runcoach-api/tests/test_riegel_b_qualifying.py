"""Which races qualify for the endurance exponent b, end to end through ``GET /state`` (F029 AC1, AC4).

Every row of the F029 reference section 2 table, every bound of section 1 and every recording-mode
population of section 7 is driven through the real path: the session is stored (``db.persist`` for
a synthetic session, ``POST /sessions`` for a real file), tagged (stored with ``activity_tag`` race,
or through F023's ``PUT /race-tags`` where the test is about the athlete's path), and read back from
``GET /state``. Each test asserts the served list (``efforts``, ``excluded`` with its reason, or
neither) for every candidate.

Synthetic races carry 1 Hz flat records, ``distance = t * 1000 / pace``, so F013's GAP pace is the
given pace and the GAP-equivalent distance is the distance run. F013 values the records cannot give
on demand (its ``grade_clamped`` and ``smart_recording`` flags on a race whose stored flags lack
them, a GAP pace of 0, a negative, NaN or an infinity, a computation that fails) are set on the
real computation's result by wrapping ``db._race_features`` for the named sessions only; every
other value is the one F013 computes. Every session's saved load is replaced through
``support/session_loads.py`` (and ``support/ctl_path.py`` where the fitness screen must fire), so
the load chart the fitness screen reads is fixed by the test. The clock is frozen through
``main._utcnow``; the autouse isolated store's ``athlete_timezone`` is UTC.

Real files, with their rows of the provenance table in ``fixtures/README.md``:

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| sample_run | real run | | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | yes |
| hilly_run_8k_fr945 | real run | was `Hilly_Smart_Recorded_run.fit`; Smart selected, watch wrote 1 Hz; positions stripped | 1 Hz, 82 s pause | FR945 LTE; HRM-Pro Plus | no | yes |

The smart-recording population of section 7 is pinned by the stored flag
(``test_stored_flags_exclude_without_a_record_read``): no run in the corpus shows it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
UTC_ZONE = ZoneInfo("UTC")

SHORT_S = 900.0  # a 15-minute race: outside the threshold band, so only the Riegel read reads it
SHORT_PACE = 300.0
LISTED = "Progression Run"
DROPOUTS_S = ((600, 611), (1200, 1230))  # [start, end) s after the first record: 11 s and 30 s removed

# F029 reference section 1, typed in from the reference.
WINDOW_DAYS = 1095
FADE_DAYS = 182


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
fit_patch = _load_support("fit_patch")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _records(duration_s: float, pace: float, *, altitude: float | None = 100.0) -> list[Record]:
    speed = 1000.0 / pace
    return [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=altitude, heart_rate=150.0)
        for t in range(int(duration_s) + 1)
    ]


def _named_plan(duration_s: float, name: str) -> tuple[list[SessionLap], list[SessionWorkoutStep]]:
    """One warm-up, one active and one cool-down lap on a four-step plan named ``name``."""
    laps = [
        SessionLap(
            lap_index=0, start_t=0.0, end_t=300.0, timer_s=300.0, intensity="warmup", wkt_step_index=0
        ),
        SessionLap(
            lap_index=1,
            start_t=300.0,
            end_t=duration_s - 300.0,
            timer_s=duration_s - 600.0,
            intensity="active",
            wkt_step_index=1,
        ),
        SessionLap(
            lap_index=2,
            start_t=duration_s - 300.0,
            end_t=duration_s,
            timer_s=300.0,
            intensity="cooldown",
            wkt_step_index=3,
        ),
    ]
    steps = [
        SessionWorkoutStep(step_index=0, workout_name=name, intensity="warmup", duration_type="time"),
        SessionWorkoutStep(
            step_index=1, workout_name=name, intensity="active", duration_type="time", target_type="speed"
        ),
        SessionWorkoutStep(step_index=2, workout_name=name, intensity="recovery", duration_type="time"),
        SessionWorkoutStep(step_index=3, workout_name=name, intensity="cooldown", duration_type="time"),
    ]
    return laps, steps


def _store(
    start: datetime,
    *,
    distance_m: float | None = None,
    pace: float = SHORT_PACE,
    duration_s: float | None = None,
    sport: str = "running",
    tag: str | None = "race",
    flags: list[str] | None = None,
    records: list[Record] | None = None,
    workout_name: str | None = None,
) -> str:
    """A stored session. ``distance_m`` at ``pace`` gives the duration (``distance * pace / 1000``),
    else ``duration_s`` (default 900 s) at ``pace`` gives the distance; the timer is the duration.
    ``workout_name`` stores a named four-step plan with its laps."""
    if distance_m is not None:
        duration_s = distance_m * pace / 1000.0
    elif duration_s is None:
        duration_s = SHORT_S
    records = _records(duration_s, pace) if records is None else records
    if distance_m is None:
        distance_m = records[-1].distance if records and records[-1].distance is not None else None
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
        timer_time_s=duration_s,
        quality_flags=list(flags or []),
        summary={"duration_s": duration_s, "distance_m": distance_m},
        context=Context(),
    )
    laps = _named_plan(duration_s, workout_name) if workout_name is not None else None
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {}, laps=laps)
    finally:
        conn.close()
    return session.session_id


def _sql(statement: str, *params) -> int:
    conn = db_module.get_connection()
    try:
        changed = conn.execute(statement, params).rowcount
        conn.commit()
    finally:
        conn.close()
    return changed


def _plant_loads(session_ids: list[str], value: float = 0.0) -> None:
    conn = db_module.get_connection()
    try:
        for session_id in session_ids:
            session_loads.plant_load(conn, session_id, value=value)
    finally:
        conn.close()


def _plant_every_load(value: float = 0.0) -> None:
    conn = db_module.get_connection()
    try:
        ids = [sid for (sid,) in conn.execute("SELECT session_id FROM sessions").fetchall()]
    finally:
        conn.close()
    _plant_loads(ids, value)


def _stored(session_id: str) -> sqlite3.Row:
    conn = db_module.get_connection()
    try:
        return conn.execute(
            "SELECT start_time, summary, timer_time_s FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()


def _put_tags(client: TestClient, session_ids: list[str], *, race: bool = True) -> None:
    """Connect's mark for each stored run: its start second in UTC, its distance 0.5 % long and its
    duration 0.5 % short (inside F023's 1 %)."""
    items = []
    for session_id in session_ids:
        row = _stored(session_id)
        summary = json.loads(row["summary"])
        start = datetime.fromisoformat(row["start_time"]).astimezone(UTC)
        items.append(
            {
                "connect_activity_id": 7000 + int(hashlib.sha256(session_id.encode()).hexdigest()[:6], 16),
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "distance_m": summary["distance_m"] * 1.005,
                "duration_s": summary["duration_s"] * 0.995,
                "race": race,
            }
        )
    response = client.put("/race-tags", json={"items": items})
    assert response.status_code == 200, response.text
    outcomes = [(i["outcome"], i["session_id"]) for i in response.json()["items"]]
    assert [s for _o, s in outcomes] == session_ids, outcomes
    assert all(o == ("tagged" if race else "cleared") for o, _s in outcomes), outcomes


def _riegel(client: TestClient, as_of: str | None = None) -> dict:
    response = client.get("/state", params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    determinants = response.json()["determinants"]
    assert [d["name"] for d in determinants] == ["functional_threshold_pace", "riegel_exponent"]
    return determinants[1]


def _used(riegel: dict) -> list[str]:
    return [e["session_id"] for e in riegel["efforts"]]


def _reasons(riegel: dict) -> dict[str, str]:
    return {e["session_id"]: e["reason"] for e in riegel["excluded"]}


def _effort(riegel: dict, session_id: str) -> dict:
    [effort] = [e for e in riegel["efforts"] if e["session_id"] == session_id]
    return effort


Patch = Callable[[dict], dict | None]


def _patch_features(monkeypatch: pytest.MonkeyPatch, patches: dict[str, Patch]) -> None:
    """``db._race_features`` computes F013's features as built; a session named in ``patches`` has
    its result passed through its patch (``None`` makes it a computation that failed)."""
    real = db_module._race_features

    def patched(conn, session_id):
        body = real(conn, session_id)
        if session_id in patches and body is not None:
            return patches[session_id](body)
        return body

    monkeypatch.setattr(db_module, "_race_features", patched)


def _f013_flags(*flags: str) -> Patch:
    def patch(body: dict) -> dict:
        body["flags"] = list(flags)
        return body

    return patch


def _gap_pace(value: float | None) -> Patch:
    def patch(body: dict) -> dict:
        body["features"]["gap_avg_pace_s_per_km"] = {
            "value": value,
            "unavailable": "no_distance" if value is None else None,
        }
        return body

    return patch


def _both(*patches: Patch) -> Patch:
    def patch(body: dict) -> dict:
        for p in patches:
            body = p(body)
        return body

    return patch


def _no_features(_body: dict) -> None:
    return None


def _counted_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    real = db_module.read_session_feature_inputs

    def counted(conn, session_id):
        reads.append(session_id)
        return real(conn, session_id)

    monkeypatch.setattr(db_module, "read_session_feature_inputs", counted)
    return reads


# ---------------------------------------------------------------------------
# The contract table: reference section 2, one row per condition, in order
# ---------------------------------------------------------------------------

# Written from the F029 reference section 2 (and its paragraph placing the unreadable reasons)
# before ``metrics/riegel_b.py`` was read, as contract-tables-need-an-independent-oracle asks.
# Each row: (name, when, what is stored or computed, where it lands, the reason). ``when`` is
# ``steady`` (June 2026, the chart's CTL rising), ``old`` (1100 days before the instant: outside
# the window) or ``drop`` (2026-09-15, after 45 days at no load: the day-before CTL is under 70 %
# of the day 29 days before). A row with two failing conditions names the first in the order:
# row 1 (sport, tag, name), 2 (window), 3 (summary_unreadable, then the duration), 4
# (quality_flags_unreadable, then the stored distance_regressed, then smart_recording), 5
# (fitness), 6 (features_unavailable, then F013's distance_regressed, smart_recording,
# grade_clamped), 7 (GAP pace).
NEITHER = "neither"
USED = "efforts"
OUT = "excluded"
CONTRACT_TABLE: list[tuple[str, str, dict, str, str | None]] = [
    ("clean race", "steady", {}, USED, None),
    ("row 1: sport other", "steady", {"sport": "other"}, NEITHER, None),
    ("row 1: untagged", "steady", {"tag": None}, NEITHER, None),
    ("row 1: tagged threshold_workout", "steady", {"tag": "threshold_workout"}, NEITHER, None),
    ("row 1: listed workout name", "steady", {"workout_name": LISTED}, OUT, "admitted_as_workout"),
    ("row 1 before row 2: listed name, old", "old", {"workout_name": LISTED}, OUT, "admitted_as_workout"),
    ("row 1: unlisted workout name", "steady", {"workout_name": "Easy Jog"}, USED, None),
    ("row 2: old", "old", {}, OUT, "outside_window"),
    ("row 2 before row 3: old, duration a string", "old", {"summary_sql": '{"duration_s": "900"}'}, OUT, "outside_window"),
    ("row 2 before row 3: old, summary unreadable", "old", {"summary_sql": "[900]"}, OUT, "outside_window"),
    ("row 2 before row 4: old, stored smart_recording", "old", {"flags": ["smart_recording"]}, OUT, "outside_window"),
    ("row 2 before row 4: old, flags unreadable", "old", {"flags_sql": '{"a": 1}'}, OUT, "outside_window"),
    ("row 2 before row 6: old, F013 grade_clamped", "old", {"f013": _f013_flags("grade_clamped")}, OUT, "outside_window"),
    ("row 3: duration a string", "steady", {"summary_sql": '{"duration_s": "900", "distance_m": 3000.0}'}, OUT, "duration_unavailable"),
    ("row 3: summary unreadable", "steady", {"summary_sql": "[900]"}, OUT, "summary_unreadable"),
    ("row 3 before row 4: summary and flags unreadable", "steady", {"summary_sql": "[900]", "flags_sql": '{"a": 1}'}, OUT, "summary_unreadable"),
    ("row 3 before row 4: duration zero, stored smart_recording", "steady", {"summary_sql": '{"duration_s": 0}', "flags": ["smart_recording"]}, OUT, "duration_unavailable"),
    ("row 4: flags unreadable", "steady", {"flags_sql": '{"a": 1}'}, OUT, "quality_flags_unreadable"),
    ("row 4 before row 6: flags unreadable, F013 grade_clamped", "steady", {"flags_sql": '"x"', "f013": _f013_flags("grade_clamped")}, OUT, "quality_flags_unreadable"),
    ("row 4: stored distance_regressed", "steady", {"flags": ["distance_regressed"]}, OUT, "distance_regressed"),
    ("row 4: stored smart_recording", "steady", {"flags": ["smart_recording"]}, OUT, "smart_recording"),
    ("row 4: both stored, smart first", "steady", {"flags": ["smart_recording", "distance_regressed"]}, OUT, "distance_regressed"),
    ("row 4 before row 5: dropped, stored smart_recording", "drop", {"flags": ["smart_recording"]}, OUT, "smart_recording"),
    ("row 4 before row 5: dropped, flags unreadable", "drop", {"flags_sql": '{"a": 1}'}, OUT, "quality_flags_unreadable"),
    ("row 5: dropped", "drop", {}, OUT, "fitness_dropped"),
    ("row 5 before row 6: dropped, F013 grade_clamped", "drop", {"f013": _f013_flags("grade_clamped")}, OUT, "fitness_dropped"),
    ("row 5 before row 6: dropped, features fail", "drop", {"f013": _no_features}, OUT, "fitness_dropped"),
    ("row 5 before row 7: dropped, GAP pace null", "drop", {"f013": _gap_pace(None)}, OUT, "fitness_dropped"),
    ("row 6: features fail", "steady", {"f013": _no_features}, OUT, "features_unavailable"),
    ("row 6: F013 distance_regressed", "steady", {"f013": _f013_flags("distance_regressed")}, OUT, "distance_regressed"),
    ("row 6: F013 smart_recording", "steady", {"f013": _f013_flags("smart_recording")}, OUT, "smart_recording"),
    ("row 6: F013 grade_clamped", "steady", {"f013": _f013_flags("grade_clamped")}, OUT, "grade_clamped"),
    ("row 6: all three, reversed", "steady", {"f013": _f013_flags("grade_clamped", "smart_recording", "distance_regressed")}, OUT, "distance_regressed"),
    ("row 6: grade_clamped and smart_recording", "steady", {"f013": _f013_flags("grade_clamped", "smart_recording")}, OUT, "smart_recording"),
    ("row 6 before row 7: grade_clamped, GAP pace null", "steady", {"f013": _both(_f013_flags("grade_clamped"), _gap_pace(None))}, OUT, "grade_clamped"),
    ("row 7: GAP pace null", "steady", {"f013": _gap_pace(None)}, OUT, "gap_pace_unavailable"),
    ("row 7: GAP pace 0", "steady", {"f013": _gap_pace(0.0)}, OUT, "gap_pace_unavailable"),
]  # fmt: skip

CHART_START = date(2026, 4, 1)
CHART_LOAD_UNTIL = date(2026, 7, 31)  # 50 a day from CHART_START, then 0 to the day before DROP_DAY
DROP_DAY = date(2026, 9, 15)


def _plant_drop_chart() -> None:
    """One untagged session a day from 2026-04-01 to the day before the drop day: a load of 50 to
    2026-07-31, then 0, so CTL rises through June and falls from August."""
    loads = {}
    day = CHART_START
    while day < DROP_DAY:
        loads[day] = 50.0 if day <= CHART_LOAD_UNTIL else 0.0
        day += timedelta(days=1)
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        ctl_path.plant(conn, loads, UTC_ZONE)
    finally:
        conn.close()


def _start_for(when: str, n: int) -> datetime:
    if when == "steady":
        return datetime(2026, 6, 7, 0, 0, tzinfo=UTC) + timedelta(hours=n)
    if when == "old":
        return NOW - timedelta(days=1100) + timedelta(hours=n)
    if when == "drop":
        return datetime(DROP_DAY.year, DROP_DAY.month, DROP_DAY.day, 6, 0, tzinfo=UTC) + timedelta(minutes=n)
    raise AssertionError(when)


def test_each_reference_row_lands_in_order(declared_config, monkeypatch: pytest.MonkeyPatch) -> None:
    """``CONTRACT_TABLE`` is written from the reference before the estimator was read (see the
    comment above it). One store holds every row; one ``GET /state`` serves them; each lands in
    the list the table names with the table's reason, and a candidate failing two rows reports the
    first. The fitness-dropped rows' served figures are the screen's: before under 70 % of earlier."""
    declared_config(threshold_workout_names=[LISTED])
    _plant_drop_chart()
    ids: list[str] = []
    patches: dict[str, Patch] = {}
    for n, (_name, when, spec, _where, _reason) in enumerate(CONTRACT_TABLE):
        kwargs = {k: spec[k] for k in ("sport", "tag", "flags", "workout_name") if k in spec}
        if "workout_name" in kwargs:
            kwargs["duration_s"] = 1800.0
        session_id = _store(_start_for(when, n), **kwargs)
        if "summary_sql" in spec:
            assert (
                _sql(
                    "UPDATE sessions SET timer_time_s = NULL, summary = ? WHERE session_id = ?",
                    spec["summary_sql"],
                    session_id,
                )
                == 1
            )
        if "flags_sql" in spec:
            assert (
                _sql(
                    "UPDATE sessions SET quality_flags = ? WHERE session_id = ?",
                    spec["flags_sql"],
                    session_id,
                )
                == 1
            )
        if "f013" in spec:
            patches[session_id] = spec["f013"]
        ids.append(session_id)
    _plant_loads(ids, 0.0)
    _patch_features(monkeypatch, patches)
    with TestClient(app) as client:
        riegel = _riegel(client)
    used, reasons = _used(riegel), _reasons(riegel)
    failures = []
    for session_id, (name, _when, _spec, where, reason) in zip(ids, CONTRACT_TABLE, strict=True):
        got = USED if session_id in used else OUT if session_id in reasons else NEITHER
        got_reason = reasons.get(session_id)
        print(f"  {name:<58} expected {where} {reason}; served {got} {got_reason}")
        if (got, got_reason) != (where, reason):
            failures.append((name, (where, reason), (got, got_reason)))
    assert failures == []
    assert len(used) == len(set(used)) and not set(used) & set(reasons)
    for entry in riegel["excluded"]:
        if entry["reason"] == "fitness_dropped":
            print(f"  dropped {entry}")
            assert entry["fitness_before"] < 0.7 * entry["fitness_earlier"], entry
    for session_id in used:
        assert (
            _effort(riegel, session_id)["fitness_drop"] is None
            or _effort(riegel, session_id)["fitness_drop"] <= 0.30
        )


# ---------------------------------------------------------------------------
# Section 1: the window
# ---------------------------------------------------------------------------


def test_window_bound_at_1095_days() -> None:
    """Ages are fractional days before the instant used. Without ``as_of`` the instant is the
    frozen clock; ``as_of`` 2026-09-15 is its last instant, 23:59:59.999999, so a race at noon on
    2023-09-16 is 1095.5 days old (out), though it would be 1094.5 days from the day's start."""
    end_of_day = datetime(2026, 9, 15, 23, 59, 59, 999999, tzinfo=UTC)
    in_now = _store(NOW - timedelta(days=1094.99))
    out_now = _store(NOW - timedelta(days=1095))
    in_date = _store(end_of_day - timedelta(days=1094.99))
    out_date = _store(end_of_day - timedelta(days=1095))
    noon = _store(datetime(2023, 9, 16, 12, 0, tzinfo=UTC))
    _plant_every_load(0.0)
    with TestClient(app) as client:
        now = _riegel(client)
        dated = _riegel(client, "2026-09-15")
    print(f"  now: used {[(e['session_id'], e['weight']) for e in now['efforts']]}; excluded {_reasons(now)}")
    print(
        f"  as_of: used {[(e['session_id'], e['weight']) for e in dated['efforts']]}; excluded {_reasons(dated)}"
    )
    assert _used(now) == [in_now]
    assert _reasons(now) == {s: "outside_window" for s in (out_now, in_date, out_date, noon)}
    assert _effort(now, in_now)["weight"] == pytest.approx(0.01 / FADE_DAYS, rel=1e-6)
    assert _effort(now, in_now)["weight"] > 0
    assert _used(dated) == [in_date, out_now, in_now]
    assert _reasons(dated) == {out_date: "outside_window", noon: "outside_window"}
    assert _effort(dated, in_date)["weight"] == pytest.approx(0.01 / FADE_DAYS, rel=1e-6)


# ---------------------------------------------------------------------------
# Section 2 row 3: the duration
# ---------------------------------------------------------------------------


def test_timer_falls_back_to_duration() -> None:
    """With ``timer_time_s`` null the summary's ``duration_s`` is ``T``; a bool, a string, zero, a
    negative or a non-finite value is ``duration_unavailable``, and nothing raises."""
    fallback = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    assert _sql("UPDATE sessions SET timer_time_s = NULL WHERE session_id = ?", fallback) == 1
    cases = ("true", "false", '"900"', "0", "0.0", "-900", "NaN", "Infinity", "-Infinity", "null")
    refused = []
    for n, raw in enumerate(cases):
        race = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC) + timedelta(hours=n))
        assert (
            _sql(
                "UPDATE sessions SET timer_time_s = NULL, summary = ? WHERE session_id = ?",
                f'{{"duration_s": {raw}, "distance_m": 3000.0}}',
                race,
            )
            == 1
        )
        refused.append(race)
    _plant_every_load(0.0)
    with TestClient(app) as client:
        riegel = _riegel(client)
    print(f"  used {riegel['efforts']}; excluded {_reasons(riegel)}")
    assert _used(riegel) == [fallback]
    effort = _effort(riegel, fallback)
    assert effort["duration_s"] == SHORT_S
    assert effort["gap_equivalent_m"] == pytest.approx(1000.0 * SHORT_S / SHORT_PACE, rel=1e-9)
    assert _reasons(riegel) == {race: "duration_unavailable" for race in refused}


# ---------------------------------------------------------------------------
# Section 2 row 4: the stored flags, before any record read
# ---------------------------------------------------------------------------


def test_stored_flags_exclude_without_a_record_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each stored flag alone and both (in either stored order, ``distance_regressed`` first): the
    races are 3700 s long, out of the threshold band, so no path reads their records, and none is
    read. A clean out-of-band race is read once (the counter counts), and an in-band race with the
    stored ``smart_recording`` flag is still read, by the threshold path (reference section 7)."""
    start = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
    cases = (
        (["distance_regressed"], "distance_regressed"),
        (["smart_recording"], "smart_recording"),
        (["distance_regressed", "smart_recording"], "distance_regressed"),
        (["smart_recording", "distance_regressed"], "distance_regressed"),
    )
    flagged = {
        _store(start + timedelta(days=n), duration_s=3700.0, flags=flags): reason
        for n, (flags, reason) in enumerate(cases)
    }
    clean = _store(start + timedelta(days=10), duration_s=3700.0)
    in_band = _store(start + timedelta(days=11), duration_s=1800.0, flags=["smart_recording"])
    _plant_every_load(0.0)
    reads = _counted_reads(monkeypatch)
    with TestClient(app) as client:
        riegel = _riegel(client)
    print(f"  reads {reads}; excluded {_reasons(riegel)}")
    assert sorted(reads) == sorted([clean, in_band])
    assert _used(riegel) == [clean]
    assert _reasons(riegel) == {**flagged, in_band: "smart_recording"}


# ---------------------------------------------------------------------------
# Section 2 rows 6 and 7: F013's flags and GAP pace
# ---------------------------------------------------------------------------


def test_f013_flags_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each of F013's three screened flags alone and all three (given in reverse), with the stored
    flags empty; and a race whose records step backwards, so F013 computes ``distance_regressed``
    from them, with no patch."""
    start = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
    cases = (
        (("distance_regressed",), "distance_regressed"),
        (("smart_recording",), "smart_recording"),
        (("grade_clamped",), "grade_clamped"),
        (("grade_clamped", "smart_recording", "distance_regressed"), "distance_regressed"),
    )
    patches: dict[str, Patch] = {}
    expected: dict[str, str] = {}
    for n, (flags, reason) in enumerate(cases):
        race = _store(start + timedelta(days=n))
        patches[race] = _f013_flags(*flags)
        expected[race] = reason
    regressing = _store(start + timedelta(days=9))
    assert (
        _sql("UPDATE records SET distance = distance - 10.0 WHERE session_id = ? AND t = 450.0", regressing)
        == 1
    )
    expected[regressing] = "distance_regressed"
    clean = _store(start + timedelta(days=10))
    _plant_every_load(0.0)
    _patch_features(monkeypatch, patches)
    with TestClient(app) as client:
        features = client.get(f"/sessions/{regressing}/features").json()
        riegel = _riegel(client)
    print(f"  regressing flags {features['flags']}; excluded {_reasons(riegel)}")
    assert "distance_regressed" in features["flags"]
    assert _used(riegel) == [clean]
    assert _reasons(riegel) == expected


def test_gap_pace_rejects_non_positive_and_non_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    """F013's GAP pace null, 0, negative, NaN, +inf and -inf: each ``gap_pace_unavailable``, and
    the response is still valid JSON (nothing non-finite is served)."""
    start = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
    values = (None, 0.0, -0.0, -300.0, math.nan, math.inf, -math.inf)
    patches: dict[str, Patch] = {}
    for n, value in enumerate(values):
        patches[_store(start + timedelta(days=n))] = _gap_pace(value)
    clean = _store(start + timedelta(days=10))
    _plant_every_load(0.0)
    _patch_features(monkeypatch, patches)
    with TestClient(app) as client:
        response = client.get("/state")
    assert response.status_code == 200, response.text
    riegel = json.loads(response.text, parse_constant=lambda c: pytest.fail(f"non-finite {c} served"))[
        "determinants"
    ][1]
    print(f"  excluded {_reasons(riegel)}")
    assert _used(riegel) == [clean]
    assert _reasons(riegel) == {race: "gap_pace_unavailable" for race in patches}


# ---------------------------------------------------------------------------
# What does not exclude: no altitude, degraded GPS
# ---------------------------------------------------------------------------


def test_a_race_with_no_altitude_qualifies_flat() -> None:
    """A track race with no altitude: GAP coverage 0, counted at its flat pace."""
    race = _store(
        datetime(2026, 8, 2, 7, 0, tzinfo=UTC), records=_records(SHORT_S, SHORT_PACE, altitude=None)
    )
    _plant_every_load(0.0)
    with TestClient(app) as client:
        features = client.get(f"/sessions/{race}/features").json()
        riegel = _riegel(client)
    print(f"  efforts {riegel['efforts']}")
    assert riegel["excluded"] == []
    effort = _effort(riegel, race)
    assert effort["gap_coverage"] == 0
    assert effort["gap_pace_s_per_km"] == features["features"]["avg_pace_s_per_km"]["value"]
    assert effort["gap_pace_s_per_km"] == pytest.approx(SHORT_PACE, rel=1e-9)
    assert effort["gap_equivalent_m"] == pytest.approx(3000.0, rel=1e-9)


def test_degraded_gps_qualifies_with_its_fraction() -> None:
    """The first quarter of the records ``gps_degraded``: the race qualifies and serves F013's
    fraction, about 0.25."""
    records = _records(SHORT_S, SHORT_PACE)
    for record in records[: len(records) // 4]:
        record.gps_degraded = True
        record.sample_quality = ["gps_degraded"]
    race = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), records=records)
    _plant_every_load(0.0)
    with TestClient(app) as client:
        served = client.get(f"/sessions/{race}/features").json()["gps_degraded_fraction"]
        riegel = _riegel(client)
    print(f"  efforts {riegel['efforts']}; F013 fraction {served}")
    assert riegel["excluded"] == []
    effort = _effort(riegel, race)
    assert effort["gps_degraded_fraction"] == served
    assert effort["gps_degraded_fraction"] == pytest.approx(0.25, abs=0.002)


# ---------------------------------------------------------------------------
# Section 2 row 1: workouts by name, other sports
# ---------------------------------------------------------------------------


def test_a_named_workout_is_admitted_as_workout(declared_config) -> None:
    """A race-tagged session whose steps carry a listed workout name is ``admitted_as_workout``;
    the same plan under a name not listed is a race and is used."""
    declared_config(threshold_workout_names=[LISTED])
    named = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), duration_s=1800.0, workout_name=LISTED)
    unlisted = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=1800.0, workout_name="Easy Jog")
    _plant_every_load(0.0)
    with TestClient(app) as client:
        riegel = _riegel(client)
    print(f"  efforts {_used(riegel)}; excluded {riegel['excluded']}")
    assert _reasons(riegel) == {named: "admitted_as_workout"}
    assert _used(riegel) == [unlisted]


def test_a_non_running_session_is_in_neither_list() -> None:
    """A race-tagged session of sport ``other`` is in neither list and changes nothing."""
    control = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    _plant_every_load(0.0)
    with TestClient(app) as client:
        before = _riegel(client)
        other = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), sport="other")
        _plant_every_load(0.0)
        after = _riegel(client)
    print(f"  other {other}: efforts {_used(after)}, excluded {after['excluded']}")
    assert _used(after) == [control]
    assert after["excluded"] == []
    assert after == before


# ---------------------------------------------------------------------------
# Section 7: the real recording-mode populations
# ---------------------------------------------------------------------------


def _upload(client: TestClient, raw: bytes) -> str:
    response = client.post("/sessions", files={"file": ("race.fit", raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _real_race(client: TestClient, monkeypatch: pytest.MonkeyPatch, raw: bytes) -> tuple[str, dict, dict]:
    """Upload, tag through ``PUT /race-tags``, freeze the clock 30 days after the file's own start
    (so the race is inside the window), read ``GET /state`` and the race's F013 features."""
    race = _upload(client, raw)
    _put_tags(client, [race])
    start = datetime.fromisoformat(_stored(race)["start_time"])
    monkeypatch.setattr(main_module, "_utcnow", lambda: start.astimezone(UTC) + timedelta(days=30))
    features = client.get(f"/sessions/{race}/features").json()
    return race, _riegel(client), features


def _assert_real_race_used(race: str, riegel: dict, features: dict) -> dict:
    print(f"  efforts {riegel['efforts']}; excluded {riegel['excluded']}; flags {features['flags']}")
    assert riegel["excluded"] == []
    assert _used(riegel) == [race]
    effort = _effort(riegel, race)
    stored = _stored(race)
    assert effort["duration_s"] == stored["timer_time_s"]
    assert effort["distance_m"] == json.loads(stored["summary"])["distance_m"]
    assert effort["gap_pace_s_per_km"] == features["features"]["gap_avg_pace_s_per_km"]["value"]
    assert effort["gap_coverage"] == features["gap_coverage"]
    assert effort["gps_degraded_fraction"] == features["gps_degraded_fraction"]
    assert effort["weight"] == 1.0
    expected = 1000.0 * effort["duration_s"] / effort["gap_pace_s_per_km"]
    assert effort["gap_equivalent_m"] == pytest.approx(expected, rel=1e-12)
    return effort


def test_the_paused_real_run_prints_d_eq_beside_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    """``hilly_run_8k_fr945`` (kind ``real run``, run proof ``yes``; 1 Hz with an 82 s pause, timer
    2,872.1 s) tagged a race: used with ``T`` the stored timer and ``D_eq = 1000 T / GAP pace``.
    F013's GAP is over counted segments and the terrain is hilly, so ``D_eq`` differs from the
    distance run; the difference is printed and stated."""
    with TestClient(app) as client:
        race, riegel, features = _real_race(
            client, monkeypatch, (FIXTURES / "hilly_run_8k_fr945.fit").read_bytes()
        )
    effort = _assert_real_race_used(race, riegel, features)
    d_eq, distance = effort["gap_equivalent_m"], effort["distance_m"]
    difference = d_eq - distance
    print(
        f"  D_eq {d_eq:.1f} m beside distance_m {distance:.1f} m (difference {difference:+.1f} m, {difference / distance:+.2%})"
    )
    assert effort["duration_s"] == pytest.approx(2872.078, abs=0.001)
    assert math.isfinite(d_eq) and d_eq > 0, (
        f"D_eq {d_eq} m against distance_m {distance} m: difference {difference:+.1f} m"
    )
    assert abs(difference) / distance < 0.10, (
        f"D_eq {d_eq:.1f} m is {difference:+.1f} m from distance_m {distance:.1f} m, over 10 %"
    )


def test_the_dropout_real_run_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The moving-dropout population: ``sample_run`` (kind ``real run``, run proof ``yes``; 1 Hz)
    with an 11 s and a 30 s run of records removed from its FIT bytes (``fit_patch.drop_records``,
    as ``test_threshold_pace_qualifying.py`` does). It stays predominantly 1 Hz, so no
    ``smart_recording``; F013 flags ``moving_gap``, which is not screened; the race is used."""
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    for start_s, end_s in DROPOUTS_S:
        raw = fit_patch.drop_records(raw, start_s, end_s)
    with TestClient(app) as client:
        race, riegel, features = _real_race(client, monkeypatch, raw)
    effort = _assert_real_race_used(race, riegel, features)
    print(f"  D_eq {effort['gap_equivalent_m']:.1f} m beside distance_m {effort['distance_m']:.1f} m")
    assert "smart_recording" not in features["flags"]
    assert "moving_gap" in features["flags"]


# ---------------------------------------------------------------------------
# AC4: the bar and the fade, through PUT /race-tags
# ---------------------------------------------------------------------------


def _deqs(riegel: dict) -> list[float]:
    return [e["gap_equivalent_m"] for e in riegel["efforts"]]


def test_the_bar_and_fade_through_the_route() -> None:
    """Flat races, tagged through ``PUT /race-tags``: 5 km at 240 s/km and 10 km at 252 s/km give
    served ``D_eq`` 5000.0 and 10000.0, a ratio of exactly 2.0, and with a 7 km race between them
    the individual b; 9,995 m in place of 10 km (1.999) the population; the 7 km race excluded by
    a screen (stored ``smart_recording``) the population. Races at 913 and 1004 days are used at
    weight 1 and 0.5; a third race at exactly 1095 days (weight 0) is not counted by the bar, one
    at 1094.99 days (weight above 0) is."""
    five = (datetime(2026, 8, 2, 7, 0, tzinfo=UTC), 5000.0, 240.0)
    seven = (datetime(2026, 8, 16, 7, 0, tzinfo=UTC), 7000.0, 246.0)
    ten = (datetime(2026, 8, 30, 7, 0, tzinfo=UTC), 10000.0, 252.0)
    short_ten = (datetime(2026, 9, 6, 7, 0, tzinfo=UTC), 9995.0, 252.0)

    def stored(start: datetime, distance: float, pace: float, **kwargs) -> str:
        session_id = _store(start, distance_m=distance, pace=pace, tag=None, **kwargs)
        _plant_every_load(0.0)
        return session_id

    with TestClient(app) as client:
        a, m, b = stored(*five), stored(*seven), stored(*ten)
        _put_tags(client, [a, m, b])
        fitted = _riegel(client)
        deqs = _deqs(fitted)
        print(
            f"  ratio 2.0: served D_eq {deqs}; b {fitted['estimate']}; population {fitted['population_default']}"
        )
        assert max(deqs) / min(deqs) == 2.0
        assert fitted["population_default"] is False

        _put_tags(client, [b], race=False)
        b2 = stored(*short_ten)
        _put_tags(client, [b2])
        narrow = _riegel(client)
        deqs = _deqs(narrow)
        print(
            f"  ratio {max(deqs) / min(deqs):.6f}: served D_eq {deqs}; population {narrow['population_default']}"
        )
        assert max(deqs) / min(deqs) == pytest.approx(1.999, abs=1e-9)
        assert narrow["population_default"] is True
        assert narrow["estimate"]["value"] == 1.06

        _put_tags(client, [b2, m], race=False)
        m2 = stored(seven[0] + timedelta(days=1), 7000.0, 246.0, flags=["smart_recording"])
        _put_tags(client, [b, m2])
        screened = _riegel(client)
        print(
            f"  screened third: used {_used(screened)}; excluded {_reasons(screened)}; D_eq {_deqs(screened)}"
        )
        assert _used(screened) == [a, b]
        assert _reasons(screened) == {m2: "smart_recording"}
        assert screened["population_default"] is True

        old = stored(NOW - timedelta(days=913), 7000.0, 246.0)
        older = stored(NOW - timedelta(days=1004), 7000.0, 246.0)
        _put_tags(client, [old, older])
        faded = _riegel(client)
        weights = {e["session_id"]: e["weight"] for e in faded["efforts"]}
        print(f"  fade: weights {weights}; D_eq {_deqs(faded)}")
        assert weights[old] == 1.0
        assert weights[older] == pytest.approx(0.5, abs=1e-12)
        assert faded["population_default"] is False
        _put_tags(client, [old, older], race=False)

        edge = stored(NOW - timedelta(days=WINDOW_DAYS), 7000.0, 246.0)
        _put_tags(client, [edge])
        at_edge = _riegel(client)
        print(f"  at 1095 days: used {_used(at_edge)}; excluded {_reasons(at_edge)}")
        assert _used(at_edge) == [a, b]
        assert _reasons(at_edge)[edge] == "outside_window"
        assert at_edge["population_default"] is True
        _put_tags(client, [edge], race=False)

        inside = stored(NOW - timedelta(days=1094.99), 7000.0, 246.0)
        _put_tags(client, [inside])
        inside_edge = _riegel(client)
        print(
            f"  at 1094.99 days: weights {[(e['session_id'], e['weight']) for e in inside_edge['efforts']]}"
        )
        assert _used(inside_edge) == [inside, a, b]
        assert 0 < _effort(inside_edge, inside)["weight"] < 1e-4
        assert inside_edge["population_default"] is False
    for riegel in (fitted, narrow, screened, faded, at_edge, inside_edge):
        for e in riegel["efforts"]:
            print(
                f"  served D_eq {e['gap_equivalent_m']} m for {e['session_id'][:12]} at weight {e['weight']}"
            )


# ---------------------------------------------------------------------------
# An excluded race changes nothing
# ---------------------------------------------------------------------------


def _without_excluded(riegel: dict) -> dict:
    return {k: v for k, v in riegel.items() if k != "excluded"}


def test_an_excluded_race_changes_nothing(declared_config, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three races fit b; each excluded candidate is added in turn and the entry, ``excluded``
    aside, is identical to the three races' alone: b, error, interval, t and every effort. Every
    load is 0 and an untagged session 1300 days back fixes the chart's first day, so no candidate
    moves a fitness figure. The fitness screen's own candidates are in the contract table."""
    declared_config(threshold_workout_names=[LISTED])
    _store(NOW - timedelta(days=1300), tag=None)
    base = [
        _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), distance_m=5000.0, pace=240.0),
        _store(datetime(2026, 8, 16, 7, 0, tzinfo=UTC), distance_m=7000.0, pace=246.0),
        _store(datetime(2026, 8, 30, 7, 0, tzinfo=UTC), distance_m=10000.0, pace=252.0),
    ]
    _plant_every_load(0.0)
    patches: dict[str, Patch] = {}
    _patch_features(monkeypatch, patches)
    start = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)

    def flags_sql(sid: str) -> None:
        _sql("UPDATE sessions SET quality_flags = '{}' WHERE session_id = ?", sid)

    def summary_sql(raw: str) -> Callable[[str], None]:
        return lambda sid: _sql(
            "UPDATE sessions SET timer_time_s = NULL, summary = ? WHERE session_id = ?", raw, sid
        )

    candidates: list[tuple[str, Callable[[int], str], Callable[[str], None] | None, Patch | None]] = [
        ("outside_window", lambda n: _store(NOW - timedelta(days=1100)), None, None),
        (
            "duration_unavailable",
            lambda n: _store(start + timedelta(days=n)),
            summary_sql('{"duration_s": "x"}'),
            None,
        ),
        ("summary_unreadable", lambda n: _store(start + timedelta(days=n)), summary_sql("[1]"), None),
        ("quality_flags_unreadable", lambda n: _store(start + timedelta(days=n)), flags_sql, None),
        (
            "smart_recording",
            lambda n: _store(start + timedelta(days=n), flags=["smart_recording"]),
            None,
            None,
        ),
        ("grade_clamped", lambda n: _store(start + timedelta(days=n)), None, _f013_flags("grade_clamped")),
        ("features_unavailable", lambda n: _store(start + timedelta(days=n)), None, _no_features),
        ("gap_pace_unavailable", lambda n: _store(start + timedelta(days=n)), None, _gap_pace(math.inf)),
        (
            "admitted_as_workout",
            lambda n: _store(start + timedelta(days=n), duration_s=1800.0, workout_name=LISTED),
            None,
            None,
        ),
        (NEITHER, lambda n: _store(start + timedelta(days=n), sport="other"), None, None),
    ]
    with TestClient(app) as client:
        alone = _riegel(client)
        assert _used(alone) == base and alone["population_default"] is False
        expected: dict[str, str] = {}
        for n, (reason, make, mutate, patch) in enumerate(candidates):
            candidate = make(n)
            if mutate is not None:
                mutate(candidate)
            if patch is not None:
                patches[candidate] = patch
            _plant_every_load(0.0)
            if reason != NEITHER:
                expected[candidate] = reason
            response = client.get("/state")
            assert response.status_code == 200, (reason, response.text)
            riegel = response.json()["determinants"][1]
            print(f"  + {reason}: b {riegel['estimate']['value']}; excluded {len(riegel['excluded'])}")
            assert _reasons(riegel) == expected, reason
            assert _without_excluded(riegel) == _without_excluded(alone), reason
