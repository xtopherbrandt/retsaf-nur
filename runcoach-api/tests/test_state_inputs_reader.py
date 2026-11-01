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
| a factory or a predicate that writes a row through the connection, then raises | the row is gone, on that connection and a fresh one |
"""

from __future__ import annotations

import json
import logging
import sqlite3
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
    sport: str = "running",
    name: str | None = None,
) -> str:
    """One session of ``duration_s`` at 270 s/km (``sport`` running unless given); ``named`` stores
    one lap and a listed workout name on a four-step plan, so the threshold read admits it by name."""
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        name=name,
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


WRITTEN_IN_SNAPSHOT = "written-inside-the-snapshot"


def _write_a_session_row(conn) -> None:
    """Copy the one stored session under a new id and start time, through ``conn``, inside its
    open transaction (``source_device`` and ``start_time`` are unique together)."""
    keys = ("session_id", "start_time")
    columns = [r[1] for r in conn.execute("PRAGMA table_info(sessions)") if r[1] not in keys]
    listed = ", ".join(columns)
    conn.execute(
        f"INSERT INTO sessions (session_id, start_time, {listed}) SELECT ?, ?, {listed} FROM sessions LIMIT 1",
        (WRITTEN_IN_SNAPSHOT, "2026-08-03T07:00:00+00:00"),
    )
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2  # the write is visible inside


def _assert_a_write_inside_the_snapshot_is_rolled_back(make_factory, error: Exception) -> None:
    """``make_factory(conn)`` returns a factory that writes a row through ``conn`` and raises."""
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        with pytest.raises(type(error)) as raised:
            db_module.read_state_inputs(conn, NAMES, make_factory(conn))
        assert raised.value is error
        assert not conn.in_transaction
        written = "SELECT count(*) FROM sessions WHERE session_id = ?"
        assert conn.execute(written, (WRITTEN_IN_SNAPSHOT,)).fetchone()[0] == 0
    finally:
        conn.close()
    other = db_module.get_connection()
    try:
        assert other.execute(written, (WRITTEN_IN_SNAPSHOT,)).fetchone()[0] == 0
        assert other.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    finally:
        other.close()


def test_a_row_the_factory_writes_before_raising_is_rolled_back() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    error = ValueError("the factory wrote and then failed")

    def make_factory(conn):
        def factory(_load_rows):
            _write_a_session_row(conn)
            raise error

        return factory

    _assert_a_write_inside_the_snapshot_is_rolled_back(make_factory, error)


def test_a_row_the_predicate_writes_before_raising_is_rolled_back() -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    error = RuntimeError("the predicate wrote and then failed")

    def make_factory(conn):
        def factory(_load_rows):
            def needs(_row):
                _write_a_session_row(conn)
                raise error

            return needs

        return factory

    _assert_a_write_inside_the_snapshot_is_rolled_back(make_factory, error)


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


# ---------------------------------------------------------------------------
# F033: the critical-speed rows, read only when asked
#
# Adversarial rows (each is asserted below):
#
# | input | what happens |
# |---|---|
# | no ``cs_needs_records`` (every goal route) | ``cs_rows == []``; ``_read_cs_rows`` never called |
# | a cycling session, a declared capture | listed with stored values; the predicate decides |
# | flags not a JSON list | ``quality_flags_unreadable``; never read, even when accepted |
# | a corrupt summary with a null timer | not a CS reason: ``unreadable`` None, ``duration_s`` None |
# | the predicate declines | no record read; ``flags`` and ``segments`` stay None |
# | a record read or computation that raises | ``features_unavailable``, logged with the session id |
# | a session whose threshold computation failed | ``features_unavailable``, not retried |
# ---------------------------------------------------------------------------

CS_KEYS = {
    "session_id",
    "start_time",
    "sport",
    "activity_tag",
    "name",
    "timer_time_s",
    "duration_s",
    "summary",
    "quality_flags",
    "admitted_as_workout",
    "unreadable",
    "flags",
    "segments",
}


def _read_cs(cs_needs_records, make_needs_features=_accept_all, names=NAMES) -> db_module.StateInputs:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        return db_module.read_state_inputs(conn, names, make_needs_features, cs_needs_records=cs_needs_records)
    finally:
        conn.close()


def _cs(inputs: db_module.StateInputs, session_id: str) -> dict:
    (row,) = [r for r in inputs.cs_rows if r["session_id"] == session_id]
    return row


def _recorded_inputs(session_id: str) -> tuple[dict, list[dict]]:
    conn = db_module.get_connection()
    try:
        return db_module.read_session_feature_inputs(conn, session_id)  # type: ignore[return-value]
    finally:
        conn.close()


@pytest.fixture
def record_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """``(what, session_id)`` in call order for ``read_session_feature_inputs`` (``read``),
    ``compute_session_features`` (``features``) and ``segment_rows`` (``segments``), with
    ``("cs", "")`` when ``_read_cs_rows`` starts."""
    calls: list[tuple[str, str]] = []
    read = db_module.read_session_feature_inputs
    features = db_module.session_features.compute_session_features
    segments = db_module.session_features.segment_rows
    cs = db_module._read_cs_rows

    def counted_read(conn, session_id):
        calls.append(("read", session_id))
        return read(conn, session_id)

    def counted_features(session, rows):
        calls.append(("features", session["session_id"]))
        return features(session, rows)

    def counted_segments(session, rows):
        calls.append(("segments", session["session_id"]))
        return segments(session, rows)

    def counted_cs(*args, **kwargs):
        calls.append(("cs", ""))
        return cs(*args, **kwargs)

    monkeypatch.setattr(db_module, "read_session_feature_inputs", counted_read)
    monkeypatch.setattr(db_module.session_features, "compute_session_features", counted_features)
    monkeypatch.setattr(db_module.session_features, "segment_rows", counted_segments)
    monkeypatch.setattr(db_module, "_read_cs_rows", counted_cs)
    return calls


def test_cs_rows_are_not_read_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None)
    calls: list[tuple] = []
    real = db_module._read_cs_rows

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(db_module, "_read_cs_rows", spy)
    inputs = _read(_accept_all)  # the positional three-argument call
    assert inputs.cs_rows == [] and calls == []
    asked = _read_cs(lambda _row: False)
    assert len(calls) == 1 and len(asked.cs_rows) == 1


def test_cs_rows_list_every_session_with_its_stored_values() -> None:
    ride = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None, sport="cycling", name="Ride")
    capture = _store(datetime(2026, 8, 3, 7, 0, tzinfo=UTC), tag="resting_hrv_check", duration_s=300.0)
    easy = _store(datetime(2026, 8, 4, 7, 0, tzinfo=UTC), tag=None, name="Easy Run")
    named = _store(datetime(2026, 8, 5, 7, 0, tzinfo=UTC), tag=None, named=True)
    seen: list[str] = []

    def needs(row):
        seen.append(row["session_id"])
        return False

    inputs = _read_cs(needs)
    assert [r["session_id"] for r in inputs.cs_rows] == [ride, capture, easy, named] == seen
    for row in inputs.cs_rows:
        assert set(row) == CS_KEYS
        assert row["unreadable"] is None and row["flags"] is None and row["segments"] is None
        assert row["quality_flags"] == "[]"
    assert [r["sport"] for r in inputs.cs_rows] == ["cycling", "running", "running", "running"]
    assert [r["activity_tag"] for r in inputs.cs_rows] == [None, "resting_hrv_check", None, None]
    assert _cs(inputs, ride)["name"] == "Ride" and _cs(inputs, easy)["name"] == "Easy Run"
    assert [r["admitted_as_workout"] for r in inputs.cs_rows] == [False, False, False, True]
    row = _cs(inputs, easy)
    assert row["timer_time_s"] == IN_BAND and row["duration_s"] == IN_BAND
    assert json.loads(row["summary"]) == {"duration_s": IN_BAND}  # the stored text, as written
    assert _cs(inputs, capture)["duration_s"] == 300.0

    # Stored flags that are not a JSON list are the one stored-value reason; a corrupt summary is not.
    _sql("UPDATE sessions SET quality_flags = '{\"a\": 1}' WHERE session_id = ?", easy)
    _sql("UPDATE sessions SET summary = 'not json', timer_time_s = NULL WHERE session_id = ?", ride)
    inputs = _read_cs(lambda _row: True)
    assert _cs(inputs, easy)["unreadable"] == "quality_flags_unreadable"
    assert _cs(inputs, easy)["flags"] is None and _cs(inputs, easy)["segments"] is None  # never read
    ride_row = _cs(inputs, ride)
    assert ride_row["unreadable"] is None and ride_row["duration_s"] is None and ride_row["timer_time_s"] is None
    assert ride_row["summary"] == "not json"


def test_a_cs_row_the_predicate_declines_reads_no_records(record_calls: list[tuple[str, str]]) -> None:
    declined = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None)
    accepted = _store(datetime(2026, 8, 4, 7, 0, tzinfo=UTC), tag=None)
    record_calls.clear()  # the upload's own load computation read the records too
    inputs = _read_cs(lambda row: row["session_id"] == accepted)
    reads = [sid for what, sid in record_calls if what == "read"]
    assert reads == [accepted]
    assert _cs(inputs, declined)["flags"] is None and _cs(inputs, declined)["segments"] is None
    assert _cs(inputs, declined)["unreadable"] is None
    assert _cs(inputs, accepted)["segments"]


def test_a_cs_row_carries_flags_and_segments_from_one_record_read(record_calls: list[tuple[str, str]]) -> None:
    first = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None)
    second = _store(datetime(2026, 8, 4, 7, 0, tzinfo=UTC), tag=None, duration_s=HALF)
    record_calls.clear()  # the upload's own load computation read the records too
    inputs = _read_cs(lambda _row: True)
    for session_id in (first, second):
        assert record_calls.count(("read", session_id)) == 1
        assert record_calls.count(("features", session_id)) == 1
        assert record_calls.count(("segments", session_id)) == 1
    for session_id in (first, second):
        session, records = _recorded_inputs(session_id)
        row = _cs(inputs, session_id)
        assert row["unreadable"] is None
        assert row["segments"] == db_module.session_features.segment_rows(session, records)
        assert row["flags"] == list(db_module.session_features.compute_session_features(session, records)["flags"])
        assert len(row["segments"]) > 0


@pytest.mark.parametrize(
    "shape",
    ["threshold_race", "riegel_only_race", "named_workout"],
)
def test_cs_reuses_features_computed_earlier_in_the_snapshot(
    shape: str, record_calls: list[tuple[str, str]]
) -> None:
    if shape == "threshold_race":
        session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    elif shape == "riegel_only_race":
        session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), duration_s=HALF)
    else:
        session_id = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None, named=True)
    record_calls.clear()  # the upload's own load computation read the records too
    inputs = _read_cs(lambda _row: True, _accept_all)
    for what in ("read", "features", "segments"):
        assert record_calls.count((what, session_id)) == 1, what
    # The one read happened before the CS read started: CS reused it rather than making it.
    assert record_calls.index(("read", session_id)) < record_calls.index(("cs", ""))
    row = _cs(inputs, session_id)
    assert row["unreadable"] is None and row["segments"]
    session, records = _recorded_inputs(session_id)
    assert row["segments"] == db_module.session_features.segment_rows(session, records)
    assert row["flags"] == list(db_module.session_features.compute_session_features(session, records)["flags"])
    if shape == "named_workout":
        assert row["admitted_as_workout"] is True
        (workout,) = inputs.threshold_rows
        assert workout["active_time_s"] is not None
    else:
        assert _riegel(inputs, session_id)["features"] is not None


def test_threshold_paths_are_unchanged_without_cs(
    monkeypatch: pytest.MonkeyPatch, record_calls: list[tuple[str, str]]
) -> None:
    five_k = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC))
    half = _store(datetime(2026, 8, 9, 7, 0, tzinfo=UTC), duration_s=HALF)
    _store(datetime(2026, 8, 16, 7, 0, tzinfo=UTC), tag=None, named=True)
    _store(datetime(2026, 8, 23, 7, 0, tzinfo=UTC), tag=None)
    race_args: list[tuple] = []
    real_race = db_module._race_features

    def spy_race(*args, **kwargs):
        race_args.append(args[2:] + tuple(kwargs.values()))
        return real_race(*args, **kwargs)

    monkeypatch.setattr(db_module, "_race_features", spy_race)
    record_calls.clear()  # the upload's own load computation read the records too
    without = _read(_accept_all)
    assert without.cs_rows == []
    assert ("cs", "") not in record_calls
    for race in (five_k, half):
        assert ("segments", race) not in record_calls  # no stream computed for a race
    assert race_args and all(extra in ((), (None,)) for extra in race_args)  # no blocks built
    rows, unreadable = _threshold()
    with_cs = _read_cs(lambda _row: True)
    for inputs in (without, with_cs):
        assert json.dumps(inputs.threshold_rows, sort_keys=True) == json.dumps(rows, sort_keys=True)
        assert json.dumps(inputs.threshold_unreadable, sort_keys=True) == json.dumps(unreadable, sort_keys=True)
    assert json.dumps(without.riegel_rows, sort_keys=True) == json.dumps(with_cs.riegel_rows, sort_keys=True)


def test_a_cs_row_whose_records_fail_is_features_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    unread = _store(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), tag=None)
    no_stream = _store(datetime(2026, 8, 4, 7, 0, tzinfo=UTC), tag=None)
    failed_race = _store(datetime(2026, 8, 6, 7, 0, tzinfo=UTC))  # in band: the threshold read computes it
    fine = _store(datetime(2026, 8, 8, 7, 0, tzinfo=UTC), tag=None)
    real_read = db_module.read_session_feature_inputs
    real_features = db_module.session_features.compute_session_features
    real_segments = db_module.session_features.segment_rows
    computed: list[str] = []

    def read(conn, session_id):
        if session_id == unread:
            raise sqlite3.DatabaseError("a record row does not read")
        return real_read(conn, session_id)

    def features(session, rows):
        computed.append(session["session_id"])
        if session["session_id"] == failed_race:
            raise RuntimeError("a defect in the feature computation")
        return real_features(session, rows)

    def segments(session, rows):
        if session["session_id"] == no_stream:
            raise RuntimeError("a defect in the segment stream")
        return real_segments(session, rows)

    monkeypatch.setattr(db_module, "read_session_feature_inputs", read)
    monkeypatch.setattr(db_module.session_features, "compute_session_features", features)
    monkeypatch.setattr(db_module.session_features, "segment_rows", segments)
    with caplog.at_level(logging.ERROR, logger="runcoach_api.db"):
        inputs = _read_cs(lambda _row: True)
    for session_id in (unread, no_stream, failed_race):
        row = _cs(inputs, session_id)
        assert row["unreadable"] == "features_unavailable", session_id
        assert row["flags"] is None and row["segments"] is None
        assert [r for r in caplog.records if session_id in r.getMessage()], session_id
    assert computed.count(failed_race) == 1  # the threshold read's failure is not retried
    assert [(u["session_id"], u["reason"]) for u in inputs.threshold_unreadable] == [
        (failed_race, "features_unavailable")
    ]
    assert _cs(inputs, fine)["unreadable"] is None and _cs(inputs, fine)["segments"]
