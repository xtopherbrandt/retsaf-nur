"""``db.read_state_inputs``: one snapshot of what ``GET /state`` reads (F029 reference section 2).

One deferred transaction reads the saved loads, hands them to the caller's factory for the
``needs_features`` predicate, reads the threshold rows exactly as ``read_threshold_effort_rows``
does, and reads every race-tagged running session for the Riegel fit. A race's F013 features are
computed at most once per request: the threshold read's results are kept by session id, and a
Riegel row whose predicate declines it is never read.

Feature computations are counted by wrapping ``db._race_features`` and recording session ids.

Adversarial rows (each is a test below):

| input | what happens |
|---|---|
| a race-tagged session with a null ``start_time`` | cannot be stored: the column is ``NOT NULL`` |
| an empty-string ``start_time`` | carried as stored on both the threshold and the Riegel row |
| a non-JSON summary with a null timer | ``summary_unreadable`` on the Riegel row and in ``threshold_unreadable`` (as today) |
| flags ``"null"`` | read as no flags: no unreadable reason, features computed when asked |
| flags not JSON on an out-of-band race | ``quality_flags_unreadable`` on the Riegel row only; threshold keeps it ``outside_threshold_band`` |
| a feature computation that raises | ``features_unavailable`` on the Riegel row; computed and logged once |
| a factory or a predicate that raises | rolled back, re-raised unchanged, the connection usable |
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from runcoach_api import db as db_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

LISTED = "Progression Run"
NAMES = [LISTED]
IN_BAND = 1800.0  # a 30-minute race: inside the threshold band
HALF = 5400.0  # a 90-minute race: outside the threshold band


def _records(duration_s: float, pace: float = 270.0) -> list[Record]:
    speed = 1000.0 / pace
    return [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
        for t in range(int(duration_s) + 1)
    ]


def _store(
    start: datetime,
    *,
    duration_s: float = IN_BAND,
    tag: str | None = "race",
    named: bool = False,
    timer_time_s: float | None | str = "same",
) -> str:
    """One running session of ``duration_s`` at 270 s/km; ``named`` stores one lap and a listed
    workout name on a four-step plan, so the threshold read admits it by name."""
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
        timer_time_s=duration_s if timer_time_s == "same" else timer_time_s,
        quality_flags=[],
        summary={"duration_s": duration_s},
        context=Context(),
    )
    laps = None
    if named:
        laps = (
            [
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
            ],
            [
                SessionWorkoutStep(
                    step_index=0, workout_name=LISTED, intensity="warmup", duration_type="time"
                ),
                SessionWorkoutStep(
                    step_index=1,
                    workout_name=LISTED,
                    intensity="active",
                    duration_type="time",
                    target_type="speed",
                ),
                SessionWorkoutStep(
                    step_index=2, workout_name=LISTED, intensity="recovery", duration_type="time"
                ),
                SessionWorkoutStep(
                    step_index=3, workout_name=LISTED, intensity="cooldown", duration_type="time"
                ),
            ],
        )
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, _records(duration_s), [], {}, laps=laps)
    finally:
        conn.close()
    return session.session_id


def _sql(statement: str, *params) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _accept_all(_load_rows):
    return lambda _row: True


def _decline_all(_load_rows):
    return lambda _row: False


def _read(make_needs_features=_accept_all, names=NAMES) -> db_module.StateInputs:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        return db_module.read_state_inputs(conn, names, make_needs_features)
    finally:
        conn.close()


def _threshold(names=NAMES) -> tuple[list[dict], list[dict]]:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        return db_module.read_threshold_effort_rows(conn, names)
    finally:
        conn.close()


@pytest.fixture
def computed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The session ids ``_race_features`` is called for, in call order."""
    calls: list[str] = []
    real = db_module._race_features

    def counting(conn, session_id):
        calls.append(session_id)
        return real(conn, session_id)

    monkeypatch.setattr(db_module, "_race_features", counting)
    return calls


def _riegel(inputs: db_module.StateInputs, session_id: str) -> dict:
    (row,) = [r for r in inputs.riegel_rows if r["session_id"] == session_id]
    return row


# ---------------------------------------------------------------------------
# The threshold half is unchanged
# ---------------------------------------------------------------------------


def test_threshold_rows_are_unchanged_by_the_new_reader() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))  # in band
    _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)  # out of band
    _store(datetime(2026, 8, 16, 7, 0, tzinfo=UTC), named=True)  # race-tagged, admitted by name
    _store(datetime(2026, 8, 23, 7, 0, tzinfo=UTC), tag="threshold_workout", named=True)
    bad = _store(datetime(2026, 8, 30, 7, 0, tzinfo=UTC))
    _sql("UPDATE sessions SET quality_flags = 'not json' WHERE session_id = ?", bad)
    rows, unreadable = _threshold()
    for factory in (_accept_all, _decline_all):
        inputs = _read(factory)
        assert json.dumps(inputs.threshold_rows, sort_keys=True) == json.dumps(rows, sort_keys=True)
        assert json.dumps(inputs.threshold_unreadable, sort_keys=True) == json.dumps(
            unreadable, sort_keys=True
        )
    assert len(rows) == 4 and [u["session_id"] for u in unreadable] == [bad]


# ---------------------------------------------------------------------------
# Features are computed at most once, and only when asked
# ---------------------------------------------------------------------------


def test_features_are_computed_at_most_once_per_session(computed: list[str]) -> None:
    five_k = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    half = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    inputs = _read(_accept_all)
    assert computed.count(five_k) == 1  # the threshold read's result is reused for Riegel
    assert computed.count(half) == 1  # out of the threshold band: computed once, for Riegel
    for session_id in (five_k, half):
        features = _riegel(inputs, session_id)["features"]
        assert set(features) == {"flags", "gap_avg_pace_s_per_km", "gap_coverage", "gps_degraded_fraction"}
        assert features["gap_avg_pace_s_per_km"] == pytest.approx(270.0, rel=1e-3)
        assert _riegel(inputs, session_id)["unreadable"] is None
    (threshold_row,) = [r for r in inputs.threshold_rows if r["session_id"] == five_k]
    assert (
        _riegel(inputs, five_k)["features"]["gap_avg_pace_s_per_km"] == threshold_row["gap_avg_pace_s_per_km"]
    )


def test_a_failed_computation_is_not_retried_for_riegel(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bad = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    calls: list[str] = []

    def compute(session, rows):
        calls.append(session["session_id"])
        raise RuntimeError("a defect in the feature computation")

    monkeypatch.setattr(db_module.session_features, "compute_session_features", compute)
    with caplog.at_level(logging.ERROR, logger="runcoach_api.db"):
        inputs = _read(_accept_all)
    assert calls == [bad]
    assert len([r for r in caplog.records if bad in r.getMessage()]) == 1
    assert [(u["session_id"], u["reason"]) for u in inputs.threshold_unreadable] == [
        (bad, "features_unavailable")
    ]
    row = _riegel(inputs, bad)
    assert row["unreadable"] == "features_unavailable" and row["features"] is None


def test_a_race_the_predicate_declines_is_never_read(computed: list[str]) -> None:
    half = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    inputs = _read(_decline_all)
    assert computed.count(half) == 0
    row = _riegel(inputs, half)
    assert row["features"] is None and row["unreadable"] is None


def test_the_predicate_sees_every_riegel_row_before_its_features() -> None:
    first = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    second = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    seen: list[tuple[str, bool]] = []

    def factory(_load_rows):
        def needs(row):
            seen.append((row["session_id"], row["features"] is not None))
            return row["session_id"] == second

        return needs

    inputs = _read(factory)
    assert seen == [(first, False), (second, False)]
    assert _riegel(inputs, first)["features"] is None
    assert _riegel(inputs, second)["features"] is not None


# ---------------------------------------------------------------------------
# The Riegel rows
# ---------------------------------------------------------------------------


def test_named_race_tagged_sessions_are_returned_for_riegel() -> None:
    race = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    named = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), named=True)
    _store(datetime(2026, 8, 16, 7, 0, tzinfo=UTC), tag="threshold_workout", named=True)  # not a race
    _store(datetime(2026, 8, 23, 7, 0, tzinfo=UTC), tag=None)  # untagged: not a race
    inputs = _read(_accept_all)
    assert [r["session_id"] for r in inputs.riegel_rows] == [race, named]
    assert _riegel(inputs, race)["admitted_as_workout"] is False
    assert _riegel(inputs, named)["admitted_as_workout"] is True
    assert set(_riegel(inputs, race)) == {
        "session_id",
        "start_time",
        "sport",
        "activity_tag",
        "timer_time_s",
        "duration_s",
        "summary",
        "quality_flags",
        "admitted_as_workout",
        "unreadable",
        "features",
    }
    row = _riegel(inputs, race)
    assert row["timer_time_s"] == IN_BAND and row["duration_s"] == IN_BAND
    assert json.loads(row["summary"]) == {"duration_s": IN_BAND}  # the stored text, as written
    assert row["quality_flags"] == "[]"
    assert [r["session_id"] for r in inputs.threshold_rows if r["kind"] == "race"] == [race]


def test_the_factory_receives_the_load_rows() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), tag=None)
    received: list = []

    def factory(load_rows):
        received.append(load_rows)
        return lambda _row: False

    inputs = _read(factory)
    conn = db_module.get_connection()
    try:
        expected = [dict(r) for r in db_module.read_session_load_rows(conn)]
    finally:
        conn.close()
    assert len(received) == 1 and received[0] is inputs.load_rows
    assert [dict(r) for r in inputs.load_rows] == expected and len(expected) == 2


# ---------------------------------------------------------------------------
# Errors and unreadable rows
# ---------------------------------------------------------------------------


def _assert_rolls_back_and_re_raises(factory, error: Exception) -> None:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        with pytest.raises(type(error)) as raised:
            db_module.read_state_inputs(conn, NAMES, factory)
        assert raised.value is error
        assert not conn.in_transaction
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        again = db_module.read_state_inputs(conn, NAMES, _decline_all)  # a second BEGIN does not raise
        assert len(again.riegel_rows) == 1
    finally:
        conn.close()


def test_a_factory_error_rolls_back_and_re_raises() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    error = ValueError("session x has no saved load row")

    def factory(_load_rows):
        raise error

    _assert_rolls_back_and_re_raises(factory, error)


def test_a_predicate_error_rolls_back_and_re_raises() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    error = RuntimeError("the predicate failed")

    def factory(_load_rows):
        def needs(_row):
            raise error

        return needs

    _assert_rolls_back_and_re_raises(factory, error)


def test_riegel_unreadable_rows_never_reach_threshold(computed: list[str]) -> None:
    half = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    _sql("UPDATE sessions SET quality_flags = 'not json' WHERE session_id = ?", half)
    inputs = _read(_accept_all)
    row = _riegel(inputs, half)
    assert row["unreadable"] == "quality_flags_unreadable" and row["features"] is None
    assert computed == []
    assert inputs.threshold_unreadable == []
    (threshold_row,) = inputs.threshold_rows
    assert threshold_row["session_id"] == half and "quality_flags" not in threshold_row  # outside the band
    assert "unreadable" not in threshold_row


def test_a_riegel_only_feature_failure_never_reaches_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    half = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    monkeypatch.setattr(db_module, "_race_features", lambda _conn, _sid: None)
    inputs = _read(_accept_all)
    assert _riegel(inputs, half)["unreadable"] == "features_unavailable"
    assert inputs.threshold_unreadable == [] and [r["session_id"] for r in inputs.threshold_rows] == [half]


# ---------------------------------------------------------------------------
# Adversarial rows
# ---------------------------------------------------------------------------


def test_a_null_start_time_cannot_be_stored() -> None:
    session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    conn = db_module.get_connection()
    try:
        with pytest.raises(Exception, match="NOT NULL"):
            conn.execute("UPDATE sessions SET start_time = NULL WHERE session_id = ?", (session_id,))
    finally:
        conn.close()


def test_an_empty_start_time_is_carried_as_stored() -> None:
    session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), duration_s=HALF)
    _sql("UPDATE sessions SET start_time = '' WHERE session_id = ?", session_id)
    inputs = _read(_decline_all)
    assert _riegel(inputs, session_id)["start_time"] == ""
    assert [r["start_time"] for r in inputs.threshold_rows] == [""]


def test_a_non_json_summary_with_a_null_timer_is_summary_unreadable(computed: list[str]) -> None:
    session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), timer_time_s=None)
    _sql("UPDATE sessions SET summary = 'not json' WHERE session_id = ?", session_id)
    inputs = _read(_accept_all)
    row = _riegel(inputs, session_id)
    assert row["unreadable"] == "summary_unreadable" and row["duration_s"] is None and row["features"] is None
    assert row["summary"] == "not json"
    assert [(u["session_id"], u["reason"]) for u in inputs.threshold_unreadable] == [
        (session_id, "summary_unreadable")
    ]
    assert computed == []


def test_flags_null_read_as_no_flags(computed: list[str]) -> None:
    session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), duration_s=HALF)
    _sql("UPDATE sessions SET quality_flags = 'null' WHERE session_id = ?", session_id)
    inputs = _read(_accept_all)
    row = _riegel(inputs, session_id)
    assert row["unreadable"] is None and row["quality_flags"] == "null" and row["features"] is not None
    assert computed == [session_id]
