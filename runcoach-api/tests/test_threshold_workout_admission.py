"""Which running sessions ``GET /state`` reads as threshold workouts: the ``threshold_workout`` tag
or a listed workout name (F027 AC1, AC4's route half and AC6's API half; reference section 2).

Every row is driven through the real app: a session is stored with ``db.persist`` under the tag
given, its laps and steps through ``persist``'s ``laps`` argument (which sets ``laps_read_at``),
the config's ``threshold_workout_names`` is declared with ``declared_config`` (a real
``AppConfig``; the cache the route reads is cleared), and the determinant is read back from
``GET /state``. No response body is written by hand. The clock is frozen through
``main._utcnow``; every request runs under the autouse isolated store.

The name is compared as Python ``str.strip().casefold()`` on both sides, so a stored
``" progression run "`` matches a listed ``"Progression Run"``; ``workout_name`` in the response
is the stored value as written on the first step row (by ``seq``) that has one, which need not be
the row that matched. A listed name beats a ``race`` tag; a race whose laps are unread or whose
step read raises cannot be name-checked and stays a race; any other tag is never admitted by name.

The bounds of reference section 4 are read through the route on name-admitted workouts, so the
name path is shown to go through orders 1 to 11 as the tag path does. Records are 1 Hz flat, each
second at its lap's pace, so a rep's GAP pace is its lap's pace to rounding; the band's exact
bound (275 beside 250) is pinned on ``threshold_pace.active_block`` with float-exact streams in
``test_threshold_workout_active_block.py``, and the route reads a rep at 274 s/km counted and one
at 276 s/km dropped beside a fastest of 250.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
START = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)
LATEST = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
WORKOUT = "threshold_workout"
RACE = "race"
LISTED = "Progression Run"
STORED = " progression run "
NAMES = [LISTED]
WARMUP_PACE = 360.0
ACTIVE_PACE = 255.0
RECOVERY_PACE = 390.0
COOLDOWN_PACE = 390.0
PACE_BY_INTENSITY = {"warmup": WARMUP_PACE, "recovery": RECOVERY_PACE, "cooldown": COOLDOWN_PACE}


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _steps(*names: str | None) -> list[dict]:
    """The four-step plan (warm-up, work step 1 with a speed target, recovery, cool-down), each
    step's ``workout_name`` taken from ``names`` in step order (unnamed past the end)."""
    base = (
        {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
        {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
        {"step_index": 2, "intensity": "recovery", "duration_type": "time"},
        {"step_index": 3, "intensity": "cooldown", "duration_type": "time"},
    )
    padded = (*names, None, None, None, None)[: len(base)]
    return [{**step, "workout_name": name} for step, name in zip(base, padded, strict=True)]


def _named(name: str | None = STORED) -> list[dict]:
    """Every step row carrying ``name``, as a FIT file's single ``workout`` message writes it."""
    return _steps(name, name, name, name)


def _plan(active_s: float = 1200.0) -> list[tuple[str, float, float, int | None]]:
    """(intensity, seconds, pace s/km, step index) per lap: warm-up, one work lap, cool-down."""
    return [("warmup", 300.0, WARMUP_PACE, 0), ("active", active_s, ACTIVE_PACE, 1), ("cooldown", 300.0, COOLDOWN_PACE, 3)]


def _reps(*reps: tuple[float, float]) -> list[tuple[str, float, float, int | None]]:
    """A warm-up, the given (seconds, pace) work reps on step 1 with 120 s recoveries between, a
    cool-down."""
    plan: list[tuple[str, float, float, int | None]] = [("warmup", 300.0, WARMUP_PACE, 0)]
    for index, (seconds, pace) in enumerate(reps):
        if index:
            plan.append(("recovery", 120.0, RECOVERY_PACE, 2))
        plan.append(("active", seconds, pace, 1))
    plan.append(("cooldown", 300.0, COOLDOWN_PACE, 3))
    return plan


def _records_for(plan) -> list[Record]:
    """1 Hz flat records; the second [t, t+1) runs at the pace of the lap it lies in."""
    speeds: list[float] = []
    for _intensity, seconds, pace, _step in plan:
        speeds.extend([1000.0 / pace] * int(seconds))
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
    tag: str | None,
    steps: list[dict] | None = None,
    plan=None,
    laps: bool = True,
    flags: list[str] | None = None,
) -> str:
    """Store one running session under ``tag`` with 1 Hz records of ``plan``; ``laps=False``
    stores no laps or steps (``laps_read_at`` null)."""
    plan = _plan() if plan is None else plan
    steps = _steps() if steps is None else steps
    total = sum(seconds for _i, seconds, _p, _s in plan)
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
        timer_time_s=total,
        quality_flags=list(flags or []),
        summary={"duration_s": total},
        context=Context(),
    )
    stored_laps = None
    if laps:
        stored_laps = ([SessionLap(**lap) for lap in _laps_for(plan)], [SessionWorkoutStep(**step) for step in steps])
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, _records_for(plan), [], {}, laps=stored_laps)
    finally:
        conn.close()
    return session.session_id


def _race(start: datetime, *, laps: bool = False, steps: list[dict] | None = None) -> str:
    """A ``race``-tagged 1800 s run at 270 s/km, in the threshold band."""
    return _store(start, tag=RACE, plan=[("race", 1800.0, 270.0, None)], laps=laps, steps=steps)


def _sql(statement: str, *params) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _ftp(client: TestClient) -> dict:
    response = client.get("/state")
    assert response.status_code == 200, response.text
    determinants = response.json()["determinants"]
    assert [d["name"] for d in determinants] == ["functional_threshold_pace", "riegel_exponent"]
    [determinant] = [d for d in determinants if d["name"] == "functional_threshold_pace"]
    print(f"  efforts {determinant['efforts']}\n  excluded {determinant['excluded']}")
    return determinant


def _rows(determinant: dict) -> list[dict]:
    """Every effort and exclusion, in start-time order, with ``reason`` None on an effort."""
    rows = [{**e, "reason": None} for e in determinant["efforts"]] + list(determinant["excluded"])
    return sorted(rows, key=lambda row: (row["start_time"], row["session_id"]))


def _admission(row: dict) -> tuple[str, str | None, list[str] | None, str | None]:
    return (row["kind"], row["reason"], row["admitted_by"], row["workout_name"])


def _only(determinant: dict, session_id: str) -> dict:
    rows = [row for row in _rows(determinant) if row["session_id"] == session_id]
    assert len(rows) == 1, rows
    return rows[0]


# ---------------------------------------------------------------------------
# AC1: admission
# ---------------------------------------------------------------------------


def test_an_untagged_session_with_a_listed_name_is_admitted_by_name(declared_config) -> None:
    """A session with no tag whose steps are named ``" progression run "`` is a workout when the
    config lists ``"Progression Run"``: ``admitted_by`` ``["name"]``, ``workout_name`` the stored
    value as written, the effort read over its work rep as a tagged one is."""
    declared_config(threshold_workout_names=NAMES)
    workout = _store(START, tag=None, steps=_named())
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert determinant["excluded"] == []
    [effort] = determinant["efforts"]
    assert _admission({**effort, "reason": None}) == (WORKOUT, None, ["name"], STORED)
    assert effort["session_id"] == workout
    assert effort["duration_s"] == 1200.0 and effort["active_blocks"] == 1
    assert effort["gap_pace_s_per_km"] == pytest.approx(ACTIVE_PACE, rel=1e-9)
    assert determinant["estimate"]["value"] == pytest.approx(ACTIVE_PACE, rel=1e-9)


def test_a_tagged_session_is_admitted_by_tag(declared_config) -> None:
    """A ``threshold_workout``-tagged session with unnamed steps reads ``["tag"]`` and a null
    ``workout_name``; a tagged one whose laps are unread is excluded ``laps_not_read`` with
    ``["tag"]`` and a null name too, whatever its file would have named it."""
    declared_config(threshold_workout_names=NAMES)
    used = _store(START, tag=WORKOUT)
    unread = _store(LATER, tag=WORKOUT, laps=False)
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, used)) == (WORKOUT, None, ["tag"], None)
    assert _admission(_only(determinant, unread)) == (WORKOUT, "laps_not_read", ["tag"], None)


def test_a_tagged_and_named_session_reads_tag_and_name(declared_config) -> None:
    declared_config(threshold_workout_names=NAMES)
    workout = _store(START, tag=WORKOUT, steps=_named(LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, workout)) == (WORKOUT, None, ["tag", "name"], LISTED)


def test_a_listed_name_on_a_later_step_row_admits(declared_config) -> None:
    """The first step rows carry no name and the cool-down step carries the listed one: admitted,
    and ``workout_name`` is that later row's value, the first row that has one."""
    declared_config(threshold_workout_names=NAMES)
    workout = _store(START, tag=None, steps=_steps(None, None, None, LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, workout)) == (WORKOUT, None, ["name"], LISTED)


def test_workout_name_is_the_first_step_row_that_has_one(declared_config) -> None:
    """Step 1 is named ``"Tempo "`` (not listed) and step 2 ``"Progression Run"`` (listed): the
    session is admitted by name, and ``workout_name`` reports ``"Tempo "``, the first row that has
    a name, as written, not the row that matched."""
    declared_config(threshold_workout_names=NAMES)
    workout = _store(START, tag=None, steps=_steps(None, "Tempo ", LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, workout)) == (WORKOUT, None, ["name"], "Tempo ")


def test_a_race_tagged_listed_name_appears_once_as_a_workout(declared_config) -> None:
    """A ``race``-tagged session named ``Progression Run`` with laps read is a workout once
    (``["name"]``) and is in no race row; the untouched race beside it stays a race."""
    declared_config(threshold_workout_names=NAMES)
    race = _race(START)
    named = _race(LATER, laps=True, steps=_named(LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    rows = _rows(determinant)
    assert [row["session_id"] for row in rows] == [race, named]
    assert _admission(rows[0]) == (RACE, None, None, None)
    assert _admission(rows[1]) == (WORKOUT, "no_workout_steps", ["name"], LISTED)
    assert [e["kind"] for e in determinant["efforts"]] == [RACE]


@pytest.mark.parametrize("tag", ["health_snapshot", "resting_hrv_check", "mystery_tag"])
def test_other_tags_are_never_admitted_by_name(declared_config, tag: str) -> None:
    declared_config(threshold_workout_names=NAMES)
    _store(START, tag=tag, steps=_named(LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert determinant["efforts"] == [] and determinant["excluded"] == []
    assert determinant["unavailable"] == "no_qualifying_effort"


def test_an_unlisted_name_without_a_tag_is_neither(declared_config) -> None:
    declared_config(threshold_workout_names=NAMES)
    _store(START, tag=None, steps=_named("Fartlek Run"))
    _store(LATER, tag=None, steps=_named("Progression"))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert determinant["efforts"] == [] and determinant["excluded"] == []


def test_an_empty_name_list_admits_by_tag_only(declared_config) -> None:
    """``threshold_workout_names = []`` (what ``runcoach-api init`` writes): the tag admits as
    before this rule, and a named untagged session is in neither list."""
    declared_config()
    tagged = _store(START, tag=WORKOUT, steps=_named(LISTED))
    _store(LATER, tag=None, steps=_named(LISTED))
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert [row["session_id"] for row in _rows(determinant)] == [tagged]
    assert _admission(_only(determinant, tagged)) == (WORKOUT, None, ["tag"], LISTED)


def test_a_race_with_unread_laps_stays_a_race(declared_config) -> None:
    declared_config(threshold_workout_names=NAMES)
    race = _race(START)
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, race)) == (RACE, None, None, None)
    assert determinant["estimate"]["value"] == pytest.approx(270.0, rel=1e-9)


def test_a_race_whose_step_read_raises_stays_a_race(
    declared_config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A ``race``-tagged session with laps read whose lap or step read raises cannot be
    name-checked: it is served as a race (used, here), never ``laps_unreadable``."""
    declared_config(threshold_workout_names=NAMES)
    race = _race(START, laps=True, steps=_named(LISTED))

    def raising(conn, session_id):
        raise RuntimeError("a step row that does not read")

    monkeypatch.setattr(db_module, "read_workout_laps", raising)
    with TestClient(app) as client, caplog.at_level("ERROR", logger="runcoach_api.db"):
        determinant = _ftp(client)
    assert _admission(_only(determinant, race)) == (RACE, None, None, None)
    assert determinant["excluded"] == []
    assert determinant["estimate"]["value"] == pytest.approx(270.0, rel=1e-9)


def test_races_carry_admitted_by_null(declared_config) -> None:
    """A used race and an excluded one (a marathon, ``outside_threshold_band``) carry
    ``admitted_by`` null and ``workout_name`` null."""
    declared_config(threshold_workout_names=NAMES)
    used = _race(START)
    long = _store(LATER, tag=RACE, plan=[("race", 3601.0, 300.0, None)], laps=False)
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert _admission(_only(determinant, used)) == (RACE, None, None, None)
    assert _admission(_only(determinant, long)) == (RACE, "outside_threshold_band", None, None)


# ---------------------------------------------------------------------------
# AC6's API half: the reader's unreadable entries carry the admission too
# ---------------------------------------------------------------------------


def test_unreadable_entries_carry_admitted_by_and_workout_name(
    declared_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``laps_unreadable`` on a tagged and named workout whose step read raises reads ``["tag"]``
    with a null name (the name is unseen); ``quality_flags_unreadable`` on a tagged and named one
    reads ``["tag", "name"]`` with the name; ``features_unavailable`` on a name-admitted one
    reads ``["name"]`` with the name."""
    declared_config(threshold_workout_names=NAMES)
    unreadable_laps = _store(START, tag=WORKOUT, steps=_named(LISTED))
    bad_flags = _store(LATER, tag=WORKOUT, steps=_named(LISTED))
    bad_context = _store(LATEST, tag=None, steps=_named(STORED))
    _sql("UPDATE sessions SET quality_flags = 'not json' WHERE session_id = ?", bad_flags)
    _sql("UPDATE sessions SET context = 'not json' WHERE session_id = ?", bad_context)
    real = db_module.read_workout_laps

    def reading(conn, session_id):
        if session_id == unreadable_laps:
            raise RuntimeError("a lap row that does not read")
        return real(conn, session_id)

    monkeypatch.setattr(db_module, "read_workout_laps", reading)
    with TestClient(app) as client:
        determinant = _ftp(client)
    assert determinant["efforts"] == []
    assert [_admission(row) for row in _rows(determinant)] == [
        (WORKOUT, "laps_unreadable", ["tag"], None),
        (WORKOUT, "quality_flags_unreadable", ["tag", "name"], LISTED),
        (WORKOUT, "features_unavailable", ["name"], STORED),
    ]


# ---------------------------------------------------------------------------
# AC4's route half: the bounds, each one name-admitted workout
# ---------------------------------------------------------------------------


def _counted_pace(*reps: tuple[float, float]) -> float:
    """The GAP pace over flat counted reps: their seconds over their metres."""
    return 1000.0 * sum(s for s, _p in reps) / sum(s * 1000.0 / p for s, p in reps)


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        pytest.param(_reps((400.0, 250.0), (290.0, 250.0)), ("used", 690.0, 2, 250.0), id="rep-290-counts"),
        pytest.param(_reps((400.0, 250.0), (289.99, 250.0)), ("active_time_below_minimum",), id="rep-289.99-dropped"),
        pytest.param(_reps((289.99, 250.0)), ("no_sustained_work",), id="lone-289.99-rep"),
        pytest.param(
            _reps((1100.0, 274.0), (1000.0, 250.0)),
            ("used", 2100.0, 2, _counted_pace((1100.0, 274.0), (1000.0, 250.0))),
            id="band-274-counts",
        ),
        pytest.param(_reps((1100.0, 276.0), (1000.0, 250.0)), ("used", 1000.0, 1, 250.0), id="band-276-dropped"),
        pytest.param(_reps((300.0, 250.0), (290.0, 250.0)), ("used", 590.0, 2, 250.0), id="counted-590-counts"),
        pytest.param(_reps((299.99, 250.0), (290.0, 250.0)), ("active_time_below_minimum",), id="counted-589.99"),
    ],
)
def test_each_bound_reads_through_the_route(declared_config, plan, expected) -> None:
    """One name-admitted workout per bound of reference section 4, read through ``GET /state``:
    the rep minimum (order 5 and the sum of order 10), the band (order 9) and the counted minimum
    (order 10) decide the served ``duration_s``, ``active_blocks`` and pace, or the reason."""
    declared_config(threshold_workout_names=NAMES)
    workout = _store(START, tag=None, steps=_named(), plan=plan)
    with TestClient(app) as client:
        determinant = _ftp(client)
    row = _only(determinant, workout)
    assert (row["kind"], row["admitted_by"], row["workout_name"]) == (WORKOUT, ["name"], STORED)
    if expected[0] == "used":
        _used, active_s, blocks, pace = expected
        assert row["reason"] is None
        assert (row["duration_s"], row["active_blocks"]) == (active_s, blocks)
        assert row["gap_pace_s_per_km"] == pytest.approx(pace, rel=1e-9)
    else:
        assert row["reason"] == expected[0]
        assert determinant["efforts"] == []
