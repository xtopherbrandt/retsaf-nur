"""Which Training-tagged workouts qualify for threshold pace, end to end through ``GET /state``
(F026 AC2; F027 AC3 and AC4, reference section 4).

Authored from the F026 reference section 3 before the reader and the estimator's workout branch
were written (rule contract-tables-need-an-independent-oracle), then re-pointed row by row to the
F027 reference section 4 order (each old and new outcome listed in the commit that moved it).
Every row is driven through the real app: a session is stored with ``db.persist`` tagged
``threshold_workout`` and its laps and steps through ``persist``'s ``laps`` argument (which sets
``laps_read_at``), then read back from ``GET /state``. Each excluded row asserts where the workout
lands (``excluded`` with its reason and ``kind``) and that it **changes nothing**: the determinant
served beside a control race is the one served with the control alone. The clock is frozen through
``main._utcnow``; every request runs under the autouse isolated store.

| order | stored workout | lands |
|---|---|---|
| 1 | no laps read (``laps_read_at`` null) | ``laps_not_read`` |
| 2 | the lap reader raises | ``laps_unreadable`` |
| 3 | a work lap (intensity ``active``, step index naming a work step) with a null ``start_t``, ``end_t`` or ``timer_s``, or ``end_t <= start_t`` with a timer of 2 s or more (the FR955: every lap end the session start) | ``lap_times_unreadable`` |
| 4 | laps read and none work: a free run of ``interval`` auto-laps with no step index, no laps at all, an ``active`` lap with a null step index, an ``active`` lap naming a recovery step, an ``active`` lap naming a repeat step that carries ``active`` and a target | ``no_workout_steps`` |
| 5 | no work rep of 290 s or more | ``no_sustained_work`` |
| 6 | quality flags not a JSON list; context not JSON | ``quality_flags_unreadable``; ``features_unavailable`` |
| 7 | ``smart_recording``; records that regress (F013's ``distance_regressed``); both | that flag, ``distance_regressed`` first |
| 8 | no distance in the work laps | ``gap_pace_unavailable`` (0 and non-finite paces through a patched ``active_block``) |
| 10 | counted time 589 s; finite lap times whose sum overflows to inf | ``active_time_below_minimum`` |
| 10 | counted time 599 s, 600 s and 7200 s (no maximum) | used, ``duration_s`` the counted time |
| - | tagged ``threshold_workout`` but not running | in neither list |

Orders 1 to 5 decide before any record is read (``read_session_feature_inputs`` is counted); the
record read follows for every workout past them, short ones included, so a short workout with
unreadable flags reads ``quality_flags_unreadable`` where F026 read ``active_time_below_minimum``
before any record read.

Synthetic workouts carry 1 Hz flat records, each second at the pace of the lap it lies in, so the
GAP pace over the counted windows is the work laps' pace exactly while the session's whole GAP
pace is slower. Real files, with their rows of the provenance table in ``fixtures/README.md``:

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| sample_run | real run | | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | yes |

``sample_run.fit`` is a structured workout: laps 1-7 name work steps 1 and 2 (reps of 1200 s and
900 s whose GAP paces sit at a 1.0992 ratio, inside the 1.10 band by under a percent, so both
count: 2100 s, 1 block; never a pin of the band), lap 9 is the step-less ``session_end`` lap.
``repeat_workout.build_repeat_workout()`` patches its step table to five steps (step 2 recovery,
step 3 a repeat step keeping the cool-down's intensity).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion import fit_parser, mapping, quality_gates
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import session_features as SF
from runcoach_api.metrics import threshold_pace as TP
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CONTROL_START = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
WORKOUT_START = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
CONTROL_PACE = 270.0
WARMUP_PACE = 360.0
ACTIVE_PACE = 255.0
RECOVERY_PACE = 390.0
COOLDOWN_PACE = 390.0
WORKOUT = "threshold_workout"

STEPS = (
    {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
    {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
    {"step_index": 2, "intensity": "recovery", "duration_type": "time"},
    {"step_index": 3, "intensity": "cooldown", "duration_type": "time"},
)


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(active_s: float = 1200.0) -> list[tuple[str, float, float, int | None]]:
    """(intensity, seconds, pace s/km, step index) per lap: warm-up, one active lap, cool-down."""
    return [
        ("warmup", 300.0, WARMUP_PACE, 0),
        ("active", active_s, ACTIVE_PACE, 1),
        ("cooldown", 300.0, COOLDOWN_PACE, 3),
    ]


def _records_for(plan, *, active_speed: float | None = None) -> list[Record]:
    """1 Hz flat records; the second [t, t+1) runs at the pace of the lap it lies in."""
    speeds: list[float] = []
    for intensity, seconds, pace, _step in plan:
        speed = 1000.0 / pace
        if intensity == "active" and active_speed is not None:
            speed = active_speed
        speeds.extend([speed] * int(seconds))
    records = [Record(t=0.0, distance=0.0, speed=speeds[0], altitude=100.0, heart_rate=150.0)]
    distance = 0.0
    for t, speed in enumerate(speeds, start=1):
        distance += speed
        records.append(Record(t=float(t), distance=distance, speed=speed, altitude=100.0, heart_rate=150.0))
    return records


def _laps_for(plan) -> list[dict]:
    laps, t = [], 0.0
    for index, (intensity, seconds, _pace, step) in enumerate(plan):
        laps.append(
            {
                "lap_index": index,
                "start_t": t,
                "end_t": t + seconds,
                "timer_s": seconds,
                "elapsed_s": seconds,
                "distance_m": None,
                "intensity": intensity,
                "wkt_step_index": step,
                "lap_trigger": "time",
            }
        )
        t += seconds
    return laps


def _store(
    start: datetime,
    *,
    tag: str | None = WORKOUT,
    sport: str = "running",
    plan=None,
    laps: list[dict] | None | str = "plan",
    steps=STEPS,
    flags: list[str] | None = None,
    records: list[Record] | None = None,
    duration_s: float | None = None,
) -> str:
    """Store one session; a workout by default. ``laps=None`` stores no laps (``laps_read_at``
    null); a list stores those laps (dicts, in the order given) with ``steps``."""
    plan = _plan() if plan is None else plan
    records = _records_for(plan) if records is None else records
    total = sum(seconds for _i, seconds, _p, _s in plan) if duration_s is None else duration_s
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
        timer_time_s=total,
        quality_flags=list(flags or []),
        summary={"duration_s": total},
        context=Context(),
    )
    lap_rows = _laps_for(plan) if laps == "plan" else laps
    stored_laps = None
    if lap_rows is not None:
        stored_laps = ([SessionLap(**lap) for lap in lap_rows], [SessionWorkoutStep(**step) for step in steps])
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {}, laps=stored_laps)
    finally:
        conn.close()
    return session.session_id


def _control() -> str:
    plan = [("race", 1800.0, CONTROL_PACE, None)]
    return _store(CONTROL_START, tag="race", plan=plan, laps=None)


def _sql(statement: str, *params) -> int:
    conn = db_module.get_connection()
    try:
        changed = conn.execute(statement, params).rowcount
        conn.commit()
    finally:
        conn.close()
    return changed


def _ftp(client: TestClient) -> dict:
    response = client.get("/state")
    assert response.status_code == 200, response.text
    determinants = response.json()["determinants"]
    assert [d["name"] for d in determinants] == ["functional_threshold_pace"]
    return determinants[0]


def _efforts(determinant: dict) -> list[tuple[str, str]]:
    return [(e["session_id"], e["kind"]) for e in determinant["efforts"]]


def _excluded(determinant: dict) -> list[tuple[str, str, str]]:
    return [(e["session_id"], e["reason"], e["kind"]) for e in determinant["excluded"]]


def _comparable(determinant: dict) -> dict:
    """Everything an excluded workout must leave as it was."""
    keys = ("estimate", "unavailable", "error", "short_races_only", "efforts", "parameters")
    return {key: determinant[key] for key in keys}


def _assert_excluded_changes_nothing(store_workout, reason: str) -> str:
    """Store the control race, read ``GET /state``, store the workout under test, read again: the
    workout lands in ``excluded`` with ``reason`` and kind ``threshold_workout``, and every other
    part of the determinant is unchanged."""
    control = _control()
    with TestClient(app) as client:
        before = _ftp(client)
        assert _efforts(before) == [(control, "race")] and before["excluded"] == []
        workout = store_workout()
        after = _ftp(client)
    print(f"  excluded {workout}: {_excluded(after)}; estimate {after['estimate']}")
    assert _excluded(after) == [(workout, reason, WORKOUT)]
    assert _comparable(after) == _comparable(before)
    return workout


def _assert_qualifies(determinant: dict, workout: str, *, active_s: float, pace: float, blocks: int = 1) -> dict:
    print(f"  efforts {determinant['efforts']}; excluded {determinant['excluded']}")
    assert determinant["excluded"] == []
    assert _efforts(determinant) == [(workout, WORKOUT)]
    effort = determinant["efforts"][0]
    assert effort["duration_s"] == active_s
    assert effort["active_blocks"] == blocks
    assert effort["gap_pace_s_per_km"] == pytest.approx(pace, rel=1e-9)
    assert effort["gain"] == 1
    assert determinant["estimate"]["value"] == pytest.approx(pace, rel=1e-9)
    return effort


def _with(plan_laps: list[dict], index: int, **changes) -> list[dict]:
    laps = [dict(lap) for lap in plan_laps]
    laps[index].update(changes)
    return laps


# ---------------------------------------------------------------------------
# Row 1: laps_read_at
# ---------------------------------------------------------------------------


def test_laps_not_read() -> None:
    """A Training-tagged run whose laps were never read: ``laps_not_read``, whatever its records."""
    _assert_excluded_changes_nothing(lambda: _store(WORKOUT_START, laps=None), "laps_not_read")


# ---------------------------------------------------------------------------
# Order 3: lap_times_unreadable
# ---------------------------------------------------------------------------


_BASE_LAPS = _laps_for(_plan())


@pytest.mark.parametrize(
    "laps",
    [
        pytest.param(_with(_BASE_LAPS, 1, start_t=None), id="null-start"),
        pytest.param(_with(_BASE_LAPS, 1, end_t=None), id="null-end"),
        pytest.param(_with(_BASE_LAPS, 1, timer_s=None), id="null-timer"),
        pytest.param(_with(_BASE_LAPS, 1, end_t=300.0), id="end-equals-start"),
        pytest.param(_with(_BASE_LAPS, 1, end_t=200.0), id="end-before-start"),
        # dev_fields_run.fit (an FR955 recording): every lap end written as the session start.
        pytest.param([{**lap, "end_t": 0.0} for lap in _BASE_LAPS], id="fr955-every-end-at-session-start"),
        # One readable active lap beside an unreadable one: excluded, never a smaller block.
        pytest.param(
            [
                *_with(_BASE_LAPS, 1, timer_s=None)[:2],
                {**_BASE_LAPS[1], "lap_index": 2, "start_t": 1500.0, "end_t": 1800.0, "timer_s": 300.0},
                {**_BASE_LAPS[2], "lap_index": 3, "start_t": 1800.0, "end_t": 2100.0},
            ],
            id="one-of-two-active-laps",
        ),
    ],
)
def test_lap_times_unreadable(laps: list[dict]) -> None:
    _assert_excluded_changes_nothing(lambda: _store(WORKOUT_START, laps=laps), "lap_times_unreadable")


def test_a_null_time_on_a_lap_that_is_not_active_reads_nothing() -> None:
    """Order 3 reads work laps only: a warm-up lap with null times does not exclude."""
    laps = _with(_BASE_LAPS, 0, start_t=None, end_t=None, timer_s=None)
    with TestClient(app) as client:
        workout = _store(WORKOUT_START, laps=laps)
        _assert_qualifies(_ftp(client), workout, active_s=1200.0, pace=ACTIVE_PACE)


# ---------------------------------------------------------------------------
# Order 4: no_workout_steps
# ---------------------------------------------------------------------------


_REPEAT_STEPS = (
    *STEPS[:3],
    {"step_index": 3, "intensity": "active", "duration_type": "repeat_until_steps_cmplt", "target_type": "speed"},
)


@pytest.mark.parametrize(
    ("laps", "steps"),
    [
        # A free run: auto-laps carry interval, and no step index.
        pytest.param(
            [{**lap, "intensity": "interval", "wkt_step_index": None} for lap in _BASE_LAPS], (), id="free-run"
        ),
        # Laps read, the file had none: two empty lists still set laps_read_at.
        pytest.param([], (), id="no-laps-at-all"),
        # An active lap with a null step index (the lap after a workout ends) is never active.
        pytest.param(_with(_BASE_LAPS, 1, wkt_step_index=None), STEPS, id="active-lap-null-step"),
        # An active lap naming a non-active step.
        pytest.param(_with(_BASE_LAPS, 1, wkt_step_index=2), STEPS, id="active-lap-names-recovery"),
        # A repeat step's intensity means nothing, even active.
        pytest.param(_with(_BASE_LAPS, 1, wkt_step_index=3), _REPEAT_STEPS, id="active-lap-names-a-repeat-step"),
        # A workout with no active step at all.
        pytest.param(_BASE_LAPS, (STEPS[0], STEPS[2], STEPS[3]), id="no-active-step"),
    ],
)
def test_no_workout_steps(laps: list[dict], steps) -> None:
    _assert_excluded_changes_nothing(lambda: _store(WORKOUT_START, laps=laps, steps=steps), "no_workout_steps")


def test_the_repeat_workout_counts_only_its_active_step() -> None:
    """The repeat workout file: step 2 is recovery, so only laps 1-4 (step 1) are active, 1200 s in one
    block; its repeat step keeps the cool-down's intensity, and the reading is the same when the
    store says that step and the lap naming it are ``active``: a repeat step is never active."""
    repeat_workout = _load_support("repeat_workout")
    with TestClient(app) as client:
        response = client.post("/sessions", files={"file": ("repeat.fit", repeat_workout.build_repeat_workout())})
        assert response.status_code == 201, response.text
        workout = response.json()["session_id"]
        _sql("UPDATE sessions SET activity_tag = ? WHERE session_id = ?", WORKOUT, workout)
        first = _ftp(client)
        [effort] = first["efforts"]
        print(f"  repeat workout: {effort}")
        assert (effort["session_id"], effort["kind"], effort["active_blocks"]) == (workout, WORKOUT, 1)
        assert effort["duration_s"] == pytest.approx(377.535 + 356.45 + 300.614 + 165.401, abs=1e-9)
        assert _sql(
            "UPDATE workout_steps SET intensity = 'active' WHERE session_id = ? AND step_index = 3", workout
        ) == 1
        assert _sql("UPDATE laps SET intensity = 'active' WHERE session_id = ? AND wkt_step_index = 3", workout) == 1
        second = _ftp(client)
    assert second["efforts"] == first["efforts"]


# ---------------------------------------------------------------------------
# Order 10: the counted time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("active_s", [599.0, 600.0, 7200.0])
def test_active_time_599_600_and_7200(active_s: float) -> None:
    """599 s, 600 s and 7200 s all count (no maximum, user ruling), with ``duration_s`` the counted
    time, not the session's. Before the sustained-rep rule 599 s was excluded as
    ``active_time_below_minimum`` at a 600 s bound; the bound is 590 s now, pinned on the pure
    function by ``test_threshold_workout_active_block.test_counted_reps_at_589_99_and_590``, and
    589 s is the excluded case of ``test_an_excluded_workout_changes_nothing`` below."""
    workout = _store(WORKOUT_START, plan=_plan(active_s))
    with TestClient(app) as client:
        effort = _assert_qualifies(_ftp(client), workout, active_s=active_s, pace=ACTIVE_PACE)
    assert effort["gap_coverage"] == 1.0
    assert effort["gps_degraded_fraction"] == 0.0


def test_an_active_time_that_overflows_to_inf_is_refused() -> None:
    """Every lap time finite, their sum inf: the two adjacent laps are one rep of inf length (so
    sustained), and order 10 asks for ``A`` finite, so it is refused and never used (an inf
    duration would serve a number JSON cannot carry)."""
    laps = [
        {**_BASE_LAPS[0]},
        {**_BASE_LAPS[1], "start_t": 300.0, "end_t": 900.0, "timer_s": 1e308},
        {**_BASE_LAPS[1], "lap_index": 2, "start_t": 900.0, "end_t": 1500.0, "timer_s": 1e308},
        {**_BASE_LAPS[2], "lap_index": 3, "start_t": 1500.0, "end_t": 1800.0},
    ]
    decided = TP.active_laps(laps, STEPS)
    assert decided.unavailable is None
    assert [rep.length_s for rep in decided.reps] == [math.inf]
    _assert_excluded_changes_nothing(lambda: _store(WORKOUT_START, laps=laps), "active_time_below_minimum")


# ---------------------------------------------------------------------------
# Order 7: the flags, in F024's fixed order
# ---------------------------------------------------------------------------


def _store_regressing(*, flags: list[str]) -> str:
    """A workout whose records hold one step where distance goes down, inside the active lap."""
    workout = _store(WORKOUT_START, flags=flags)
    assert _sql("UPDATE records SET distance = distance - 10.0 WHERE session_id = ? AND t = 900.0", workout) == 1
    return workout


@pytest.mark.parametrize(
    ("store", "reason"),
    [
        pytest.param(lambda: _store(WORKOUT_START, flags=["smart_recording"]), "smart_recording", id="smart"),
        pytest.param(lambda: _store_regressing(flags=[]), "distance_regressed", id="regressed"),
        pytest.param(lambda: _store_regressing(flags=["smart_recording"]), "distance_regressed", id="both"),
    ],
)
def test_workout_flags_exclude_in_fixed_order(store, reason: str) -> None:
    _assert_excluded_changes_nothing(store, reason)


# ---------------------------------------------------------------------------
# Orders 8 and 11: the GAP pace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pace", [None, 0.0, -0.0, -255.0, math.nan, math.inf, -math.inf, 5e-324])
def test_workout_gap_null_zero_and_non_finite(pace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Null end to end (no distance in the work laps); 0, negative, non-finite and a subnormal
    whose speed overflows through a patched ``active_block``, which no counted segment can
    produce, on the real reader's path."""
    if pace is None:
        store = lambda: _store(WORKOUT_START, records=_records_for(_plan(), active_speed=0.0))  # noqa: E731
    else:
        real = TP.active_block

        def patched(laps, steps, segment_rows):
            return dataclasses.replace(real(laps, steps, segment_rows), gap_pace_s_per_km=pace)

        monkeypatch.setattr(TP, "active_block", patched)
        store = lambda: _store(WORKOUT_START)  # noqa: E731
    _assert_excluded_changes_nothing(store, "gap_pace_unavailable")


# ---------------------------------------------------------------------------
# Orders 2 and 6: what does not read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["not json", '"smart_recording"', '{"a": 1}', "7"])
def test_quality_flags_unreadable_for_a_workout(value: str) -> None:
    def store() -> str:
        workout = _store(WORKOUT_START)
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", value, workout)
        return workout

    _assert_excluded_changes_nothing(store, "quality_flags_unreadable")


def test_orders_1_to_5_decide_before_the_flags_are_read() -> None:
    """Order: a workout failing order 1 or order 5 with unreadable flags reads its lap reason; a
    short one (589 s, past order 5) reads its records and so ``quality_flags_unreadable``, where
    F026 read ``active_time_below_minimum`` before any record read."""
    no_laps = _store(WORKOUT_START, laps=None)
    short = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), plan=_plan(589.0))
    no_rep = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), plan=_plan(289.0))
    for workout in (no_laps, short, no_rep):
        _sql("UPDATE sessions SET quality_flags = 'not json' WHERE session_id = ?", workout)
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _excluded(determinant) == [
        (no_laps, "laps_not_read", WORKOUT),
        (short, "quality_flags_unreadable", WORKOUT),
        (no_rep, "no_sustained_work", WORKOUT),
    ]


def test_features_unavailable_for_a_workout(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def store() -> str:
        workout = _store(WORKOUT_START)
        _sql("UPDATE sessions SET context = 'not json' WHERE session_id = ?", workout)
        return workout

    workout = _assert_excluded_changes_nothing(store, "features_unavailable")

    # A raising segment computation is caught and logged with the session id, as F013's is.
    _sql("UPDATE sessions SET context = NULL WHERE session_id = ?", workout)
    with TestClient(app) as client:
        assert (workout, WORKOUT) in _efforts(_ftp(client))

    def raising(session, rows):
        raise RuntimeError("a defect in the segment stream")

    monkeypatch.setattr(db_module.session_features, "segment_rows", raising)
    caplog.clear()
    with TestClient(app) as client, caplog.at_level("ERROR", logger="runcoach_api.db"):
        determinant = _ftp(client)
    assert (workout, "features_unavailable", WORKOUT) in _excluded(determinant)
    logged = [r for r in caplog.records if r.name == "runcoach_api.db" and workout in r.getMessage()]
    assert len(logged) == 1 and isinstance(logged[0].exc_info[1], RuntimeError), caplog.records


def test_laps_unreadable(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A lap or step read that raises: ``laps_unreadable``, caught and logged with the session id."""

    def raising(conn, session_id):
        raise RuntimeError("a lap row that does not read")

    def store() -> str:
        workout = _store(WORKOUT_START)
        monkeypatch.setattr(db_module, "read_workout_laps", raising)
        return workout

    with caplog.at_level("ERROR", logger="runcoach_api.db"):
        workout = _assert_excluded_changes_nothing(store, "laps_unreadable")
    logged = [r for r in caplog.records if r.name == "runcoach_api.db" and workout in r.getMessage()]
    assert len(logged) == 1 and isinstance(logged[0].exc_info[1], RuntimeError), caplog.records


# ---------------------------------------------------------------------------
# What the reader reads, and in what order
# ---------------------------------------------------------------------------


def test_an_excluded_workout_changes_nothing() -> None:
    """Every excluded kind at once beside the control: the determinant is the control's alone."""
    control = _control()
    with TestClient(app) as client:
        before = _ftp(client)
        _store(datetime(2026, 9, 1, 7, 0, tzinfo=UTC), laps=None)
        _store(datetime(2026, 9, 2, 7, 0, tzinfo=UTC), laps=_with(_BASE_LAPS, 1, end_t=None))
        _store(datetime(2026, 9, 3, 7, 0, tzinfo=UTC), laps=_with(_BASE_LAPS, 1, wkt_step_index=None))
        _store(datetime(2026, 9, 4, 7, 0, tzinfo=UTC), plan=_plan(589.0))
        _store(datetime(2026, 9, 5, 7, 0, tzinfo=UTC), flags=["smart_recording"])
        _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), records=_records_for(_plan(), active_speed=0.0))
        _store(datetime(2026, 9, 7, 7, 0, tzinfo=UTC), plan=_plan(289.0))
        after = _ftp(client)
    print(f"  excluded {_excluded(after)}")
    assert [reason for _s, reason, _k in _excluded(after)] == [
        "laps_not_read",
        "lap_times_unreadable",
        "no_workout_steps",
        "active_time_below_minimum",
        "smart_recording",
        "gap_pace_unavailable",
        "no_sustained_work",
    ]
    assert _efforts(after) == [(control, "race")]
    assert _comparable(after) == _comparable(before)


def test_records_are_read_only_for_workouts_past_orders_1_to_5(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orders 1 to 5 decide from the session row, laps and steps: such workouts cost no record
    read; a workout past them, the short one included (its counted time is order 10, after the
    record read), reads its records once, for both the flags and the segment stream."""
    calls: list[str] = []
    lap_reads: list[str] = []
    real_inputs = db_module.read_session_feature_inputs
    real_laps = db_module.read_workout_laps

    def counted_inputs(conn, session_id):
        calls.append(session_id)
        return real_inputs(conn, session_id)

    def counted_laps(conn, session_id):
        lap_reads.append(session_id)
        return real_laps(conn, session_id)

    no_laps = _store(datetime(2026, 9, 1, 7, 0, tzinfo=UTC), laps=None)
    _store(datetime(2026, 9, 2, 7, 0, tzinfo=UTC), laps=_with(_BASE_LAPS, 1, end_t=None))
    _store(datetime(2026, 9, 3, 7, 0, tzinfo=UTC), laps=_with(_BASE_LAPS, 1, wkt_step_index=2))
    short = _store(datetime(2026, 9, 4, 7, 0, tzinfo=UTC), plan=_plan(589.0))
    used = _store(datetime(2026, 9, 5, 7, 0, tzinfo=UTC))
    monkeypatch.setattr(db_module, "read_session_feature_inputs", counted_inputs)
    monkeypatch.setattr(db_module, "read_workout_laps", counted_laps)
    with TestClient(app) as client:
        determinant = _ftp(client)
    print(f"  feature reads {calls}; lap reads {lap_reads}")
    assert _efforts(determinant) == [(used, WORKOUT)]
    assert (short, "active_time_below_minimum", WORKOUT) in _excluded(determinant)
    assert calls == [short, used]
    assert no_laps not in lap_reads and len(lap_reads) == 4


def test_laps_are_read_in_lap_index_order() -> None:
    """Blocks count runs of consecutive counted laps in ``lap_index`` order: laps inserted out of
    order (rowid order active, recovery, active, warm-up, active, cool-down) still read 2 blocks."""
    plan = [
        ("warmup", 300.0, WARMUP_PACE, 0),
        ("active", 400.0, ACTIVE_PACE, 1),
        ("active", 400.0, ACTIVE_PACE, 1),
        ("recovery", 120.0, RECOVERY_PACE, 2),
        ("active", 400.0, ACTIVE_PACE, 1),
        ("cooldown", 300.0, COOLDOWN_PACE, 3),
    ]
    laps = _laps_for(plan)
    shuffled = [laps[i] for i in (1, 3, 2, 0, 4, 5)]
    workout = _store(WORKOUT_START, plan=plan, laps=shuffled)
    conn = db_module.get_connection()
    try:
        rowid_order = [r[0] for r in conn.execute("SELECT lap_index FROM laps ORDER BY rowid")]
    finally:
        conn.close()
    assert rowid_order == [1, 3, 2, 0, 4, 5]
    with TestClient(app) as client:
        effort = _assert_qualifies(_ftp(client), workout, active_s=1200.0, pace=ACTIVE_PACE, blocks=2)
    assert effort["duration_s"] == 1200.0


def test_a_training_tagged_ride_is_in_neither_list() -> None:
    _store(WORKOUT_START, sport="cycling")
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert determinant["efforts"] == [] and determinant["excluded"] == []
    assert determinant["unavailable"] == "no_qualifying_effort"


def test_efforts_and_exclusions_carry_kind() -> None:
    race = _control()
    long_race = _store(
        datetime(2026, 8, 9, 7, 0, tzinfo=UTC), tag="race", plan=[("race", 3601.0, 300.0, None)], laps=None
    )
    unreadable_race = _store(datetime(2026, 8, 16, 7, 0, tzinfo=UTC), tag="race", plan=[("race", 1800.0, 260.0, None)])
    workout = _store(WORKOUT_START)
    not_read = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), laps=None)
    unreadable_workout = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    for session_id in (unreadable_race, unreadable_workout):
        _sql("UPDATE sessions SET quality_flags = 'not json' WHERE session_id = ?", session_id)
    with TestClient(app) as client:
        determinant = _ftp(client)
    print(f"  efforts {determinant['efforts']}\n  excluded {determinant['excluded']}")
    assert _efforts(determinant) == [(race, "race"), (workout, WORKOUT)]
    race_effort, workout_effort = determinant["efforts"]
    assert race_effort["active_blocks"] is None
    assert workout_effort["active_blocks"] == 1
    assert _excluded(determinant) == [
        (long_race, "outside_threshold_band", "race"),
        (unreadable_race, "quality_flags_unreadable", "race"),
        (not_read, "laps_not_read", WORKOUT),
        (unreadable_workout, "quality_flags_unreadable", WORKOUT),
    ]


# ---------------------------------------------------------------------------
# AC5's API half: a served workout from a real file
# ---------------------------------------------------------------------------


def _stored_summary(session_id: str) -> tuple[str, dict]:
    conn = db_module.get_connection()
    try:
        row = conn.execute("SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return row["start_time"], json.loads(row["summary"])


def _file_block():
    """``threshold_pace.active_block`` on ``sample_run.fit`` itself, through the ingest's mapping."""
    messages = fit_parser.decode((FIXTURES / "sample_run.fit").read_bytes())
    laps, steps = mapping.to_laps(messages)
    session, records = mapping.to_canonical(messages)
    quality_gates.apply(session, records)
    stream = SF.segment_rows(
        {"session_id": session.session_id, "sport": "running", "quality_flags": [], "context": {}},
        [dataclasses.asdict(record) for record in records],
    )
    return TP.active_block(
        [dataclasses.asdict(lap) for lap in laps], [dataclasses.asdict(step) for step in steps], stream
    )


def test_a_served_workout_carries_active_duration_blocks_and_window_fractions() -> None:
    """``sample_run.fit`` uploaded, tagged ``threshold_workout`` through ``PUT /race-tags``: its effort's
    ``duration_s`` is the work laps 1-7's ``timer_s`` summed (written by hand), one block, the
    pace and the window fractions ``active_block`` gives on the same file, not the session's.
    Fragile: the two reps' GAP paces sit at a 1.0992 ratio, inside the 1.10 band by under a
    percent; this file is never used to pin the band."""
    with TestClient(app) as client:
        response = client.post("/sessions", files={"file": ("sample_run.fit", (FIXTURES / "sample_run.fit").read_bytes())})
        assert response.status_code == 201, response.text
        workout = response.json()["session_id"]
        start_time, summary = _stored_summary(workout)
        item = {
            "connect_activity_id": 24557261242,
            "start_time": datetime.fromisoformat(start_time).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance_m": summary["distance_m"],
            "duration_s": summary["duration_s"],
            "tag": WORKOUT,
        }
        tagged = client.put("/race-tags", json={"items": [item]})
        assert tagged.status_code == 200, tagged.text
        assert [(i["outcome"], i["session_id"]) for i in tagged.json()["items"]] == [("tagged", workout)]
        determinant = _ftp(client)
        features = client.get(f"/sessions/{workout}/features").json()
    block = _file_block()
    print(f"  served {determinant['efforts']}\n  file block {block}")
    print(f"  session gap_coverage {features['gap_coverage']} gps_degraded_fraction {features['gps_degraded_fraction']}")
    [effort] = determinant["efforts"]
    assert (effort["session_id"], effort["kind"]) == (workout, WORKOUT)
    assert effort["duration_s"] == pytest.approx(
        377.535 + 356.45 + 300.614 + 165.401 + 322.321 + 310.872 + 266.807, abs=1e-9
    )
    assert effort["duration_s"] == pytest.approx(2100.0, abs=1e-9)
    assert effort["active_blocks"] == 1
    assert effort["gap_pace_s_per_km"] == pytest.approx(block.gap_pace_s_per_km, rel=1e-12)
    assert effort["gap_pace_s_per_km"] != pytest.approx(features["features"]["gap_avg_pace_s_per_km"]["value"], rel=1e-6)
    assert effort["gap_coverage"] == pytest.approx(block.gap_coverage, rel=1e-12)
    assert effort["gps_degraded_fraction"] == pytest.approx(block.gps_degraded_fraction, rel=1e-12, abs=0.0)
    assert determinant["excluded"] == []
    assert determinant["estimate"]["value"] == pytest.approx(block.gap_pace_s_per_km, rel=1e-12)

    # On the file both kinds of fraction read 1.0 and 0.0, so outside the windows the store is
    # changed: the warm-up loses its altitude (ungraded) and the cool-down is tagged degraded GPS.
    # The session's fractions move; the workout's, over its counted reps' windows, do not.
    assert _sql("UPDATE records SET altitude = NULL WHERE session_id = ? AND t < 300", workout) > 0
    assert _sql(
        "UPDATE records SET sample_quality = '[\"gps_degraded\"]' WHERE session_id = ? AND t >= 2400", workout
    ) > 0
    with TestClient(app) as client:
        changed = _ftp(client)
        session = client.get(f"/sessions/{workout}/features").json()
    print(f"  changed store: effort {changed['efforts']}; session {session['gap_coverage']} {session['gps_degraded_fraction']}")
    assert session["gap_coverage"] < 1.0 and session["gps_degraded_fraction"] > 0.0
    [moved] = changed["efforts"]
    assert moved["gap_coverage"] == 1.0
    assert moved["gps_degraded_fraction"] == 0.0
