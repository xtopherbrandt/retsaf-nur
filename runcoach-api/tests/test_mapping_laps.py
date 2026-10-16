"""F025 AC1, AC2 (the mapping seam) and AC6: a FIT file's laps and workout steps, mapped beside its records.

``mapping.to_laps(messages)`` maps every ``lap`` and ``workout_step`` message of a decoded file to
``SessionLap`` and ``SessionWorkoutStep`` rows (F025 reference sections 1, 2 and 6). Nothing is stored
here; this pins the mapping only.

**The oracle.** Expected values come from ``tests/support/lap_oracle.py``, which reads the bytes with
``fitdecode`` alone and imports nothing from ``runcoach_api``
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``). Its ``start_t`` and
``end_t`` are whole-second differences of the raw ``uint32`` timestamps, a different computation from
the mapping's datetime arithmetic. Section 1's ``sample_run.fit`` table is also asserted literally.

Fixtures cited as runs have run proof ``yes`` in ``tests/fixtures/README.md``: ``sample_run``,
``hilly_run_8k_fr945``, ``strap_run_hrv`` and ``dev_fields_run`` (the FR955 file).

**The seam.** Integer FIT fields cannot carry a negative or non-finite duration, a bool or a string,
so those forms are driven through ``to_laps`` with stand-in messages (``_Msg``) exposing the two
attributes the mapping reads, ``name`` and ``get_value``.

**Adversarial-probe table** (rule adversarial-input-probes-are-a-task-deliverable), driven by
``test_adversarial_probe_table``; each row names what it fed and what the mapping must return:

| input | where | result |
|---|---|---|
| lap ``start_time`` invalid (0xFFFFFFFF) | patched sample_run, one session | lap kept, ``start_t`` None |
| lap ``total_timer_time`` invalid | patched sample_run | lap kept, ``timer_s`` None |
| lap ``intensity`` 99 (unnamed enum) | patched sample_run | ``intensity`` None |
| step ``target_type`` 255 (invalid) | patched sample_run | ``target_type`` None, targets the raw numbers fitdecode returns |
| ``distance_m`` NaN, inf, -inf, -1 | seam | ``distance_m`` None |
| ``total_timer_time`` True, "300" | seam | ``timer_s`` None |
| no lap and no step messages | patched sample_run | ``([], [])`` |
| a workout message with no steps | patched sample_run | 10 laps, no steps |
| a lap ``wkt_step_index`` beyond the steps (50) | patched sample_run | stored as written, 50 |
"""

from __future__ import annotations

import importlib.util
import io
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitdecode
import pytest
from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.models import Record, Session, SessionLap, SessionWorkoutStep

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

SESSION, LAP, WORKOUT, WORKOUT_STEP = 18, 19, 26, 27
LAP_START_TIME, LAP_TIMER, LAP_INTENSITY, LAP_WKT_STEP_INDEX = 2, 8, 23, 71
STEP_TARGET_TYPE = 3
SESSION_START_TIME = 2
UINT32_INVALID = 0xFFFFFFFF


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
lap_oracle = _load_support("lap_oracle")
repeat_workout = _load_support("repeat_workout")


def _bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _map(data: bytes) -> tuple[list[SessionLap], list[SessionWorkoutStep]]:
    return mapping.to_laps(fit_parser.decode(data))


def _named(value):
    """The oracle's enum value as the mapping stores it: a name, or None for an int fitdecode cannot name."""
    return value if isinstance(value, str) else None


def _assert_matches_oracle(data: bytes) -> tuple[list[SessionLap], list[SessionWorkoutStep]]:
    laps, steps = _map(data)
    oracle = lap_oracle.read_laps_and_steps(data)
    assert len(laps) == len(oracle.laps)
    assert len(steps) == len(oracle.steps)
    for index, (lap, expected) in enumerate(zip(laps, oracle.laps, strict=True)):
        assert lap.lap_index == index
        assert lap.start_t == expected.start_t
        assert lap.end_t == expected.end_t
        assert lap.timer_s == expected.timer_s
        assert lap.elapsed_s == expected.elapsed_s
        assert lap.distance_m == expected.distance_m
        assert lap.intensity == _named(expected.intensity)
        assert lap.wkt_step_index == expected.wkt_step_index
        assert lap.lap_trigger == _named(expected.lap_trigger)
    for step, expected in zip(steps, oracle.steps, strict=True):
        assert step.step_index == expected.step_index
        assert step.workout_name == oracle.workout_name
        assert step.step_name == expected.step_name
        assert step.intensity == _named(expected.intensity)
        assert step.duration_type == _named(expected.duration_type)
        assert step.duration_value == expected.duration_value
        assert step.target_type == _named(expected.target_type)
        assert step.target_value == expected.target_value
        assert step.target_low == expected.target_low
        assert step.target_high == expected.target_high
    return laps, steps


# --- AC1 values ------------------------------------------------------------------------------


def test_sample_run_maps_ten_laps_and_four_steps_matching_the_oracle():
    laps, steps = _assert_matches_oracle(_bytes("sample_run"))

    # Reference section 1's table, literally.
    assert len(laps) == 10
    assert len(steps) == 4
    assert [s.step_index for s in steps] == [0, 1, 2, 3]
    assert [s.intensity for s in steps] == ["warmup", "active", "active", "cooldown"]
    assert [s.duration_type for s in steps] == ["time"] * 4
    assert [s.duration_value for s in steps] == [300.0, 1200.0, 900.0, 300.0]
    assert [s.target_type for s in steps] == ["open", "speed", "speed", "open"]
    assert [(s.target_low, s.target_high) for s in steps] == [
        (None, None),
        (2.901, 3.253),
        (2.901, 3.253),
        (None, None),
    ]
    assert {s.workout_name for s in steps} == {"Easy Run"}
    assert [lap.wkt_step_index for lap in laps] == [0, 1, 1, 1, 1, 2, 2, 2, 3, None]
    assert [lap.intensity for lap in laps] == ["warmup"] + ["active"] * 7 + ["cooldown"] * 2
    assert laps[-1].lap_trigger == "session_end"
    assert laps[0].start_t == 0
    # Lap 1 ends 677.535 by start + elapsed while lap 2 starts at 679 (section 1): end_t is the timestamp.
    assert laps[1].start_t + laps[1].elapsed_s == pytest.approx(677.535)
    assert laps[2].start_t == 679


@pytest.mark.parametrize("name", ["sample_run", "hilly_run_8k_fr945", "strap_run_hrv", "dev_fields_run"])
def test_structured_fixtures_match_the_oracle(name):
    laps, steps = _assert_matches_oracle(_bytes(name))
    assert laps, f"{name} carries lap messages"
    if name != "dev_fields_run":
        assert steps, f"{name} is a structured workout"


def test_end_t_is_the_lap_timestamp_minus_the_start():
    data = _bytes("sample_run")
    session, _ = mapping.to_canonical(fit_parser.decode(data))
    origin = datetime.fromisoformat(session.start_time)
    timestamps = []
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.global_mesg_num == LAP:
                timestamps.append(frame.get_value(253))
    laps, _ = _map(data)
    assert [lap.end_t for lap in laps] == [(ts - origin).total_seconds() for ts in timestamps]
    # Not start + elapsed: the two differ on every lap with a fractional elapsed time.
    assert laps[1].end_t == 678 != laps[1].start_t + laps[1].elapsed_s


def test_fr955_lap_ends_before_starts_are_kept_as_written():
    laps, _ = _assert_matches_oracle(_bytes("dev_fields_run"))
    assert len(laps) == 7
    backwards = [lap for lap in laps if lap.end_t < lap.start_t]
    assert len(backwards) == 6
    assert {lap.end_t for lap in laps} == {0}


# --- AC2: the seam ---------------------------------------------------------------------------


class _Msg:
    """A stand-in data message: the ``name`` and ``get_value(name, fallback=...)`` the mapping reads."""

    def __init__(self, name: str, **values):
        self.name = name
        self._values = values

    def get_value(self, key, fallback=None):
        return self._values.get(key, fallback)


START = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seam_lap(**overrides) -> SessionLap:
    values = {
        "start_time": START,
        "timestamp": START + timedelta(seconds=300),
        "total_timer_time": 300.0,
        "total_elapsed_time": 300.0,
        "total_distance": 1000.0,
        "intensity": "active",
        "wkt_step_index": 1,
        "lap_trigger": "distance",
    }
    values.update(overrides)
    laps, steps = mapping.to_laps([_Msg("session", start_time=START), _Msg("lap", **values)])
    assert steps == []
    assert len(laps) == 1
    return laps[0]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1, -0.001])
@pytest.mark.parametrize(
    ("field", "attr"),
    [("total_timer_time", "timer_s"), ("total_elapsed_time", "elapsed_s"), ("total_distance", "distance_m")],
)
def test_seam_nulls_negative_and_non_finite_durations_and_distance(field, attr, bad):
    lap = _seam_lap(**{field: bad})
    assert getattr(lap, attr) is None
    # The other two measures are untouched.
    others = {"timer_s", "elapsed_s", "distance_m"} - {attr}
    assert all(getattr(lap, other) is not None for other in others)
    # Zero is a measure, not a missing one.
    assert getattr(_seam_lap(**{field: 0}), attr) == 0


@pytest.mark.parametrize("bad", [True, False, "300", b"300", (300, 1)])
@pytest.mark.parametrize(
    ("field", "attr"),
    [
        ("total_timer_time", "timer_s"),
        ("total_elapsed_time", "elapsed_s"),
        ("total_distance", "distance_m"),
        ("wkt_step_index", "wkt_step_index"),
    ],
)
def test_seam_nulls_a_bool_or_a_string_in_a_numeric_field(field, attr, bad):
    assert getattr(_seam_lap(**{field: bad}), attr) is None


@pytest.mark.parametrize("bad", [True, "1", math.nan, math.inf])
@pytest.mark.parametrize(
    "field", ["duration_value", "target_value", "custom_target_value_low", "custom_target_value_high"]
)
def test_seam_nulls_a_non_number_in_a_step_value(field, bad):
    message = _Msg("workout_step", message_index=0, duration_type="time", target_type="speed", **{field: bad})
    _, steps = mapping.to_laps([message])
    attr = {"custom_target_value_low": "target_low", "custom_target_value_high": "target_high"}.get(field, field)
    assert getattr(steps[0], attr) is None


@pytest.mark.parametrize("bad", [True, "0", 1.5, -1])
def test_seam_nulls_a_step_index_that_is_not_a_message_index(bad):
    _, steps = mapping.to_laps([_Msg("workout_step", message_index=bad)])
    assert steps[0].step_index is None


def test_seam_keeps_a_negative_start_t():
    lap = _seam_lap(start_time=START - timedelta(seconds=7), timestamp=START - timedelta(seconds=2))
    assert lap.start_t == -7
    assert lap.end_t == -2
    # A lap time that is not a datetime is no time.
    assert _seam_lap(start_time=12345).start_t is None
    assert _seam_lap(timestamp="12:00").end_t is None


def test_an_unnamed_enum_maps_to_null():
    lap = _seam_lap(intensity=99, lap_trigger=200)
    assert lap.intensity is None
    assert lap.lap_trigger is None
    _, steps = mapping.to_laps(
        [_Msg("workout_step", message_index=0, intensity=9, duration_type=77, target_type=55, duration_value=12)]
    )
    assert (steps[0].intensity, steps[0].duration_type, steps[0].target_type) == (None, None, None)
    # An unresolved duration_value is stored as the number fitdecode returns.
    assert steps[0].duration_value == 12

    # Through the real decode: lap 3's intensity patched to 99, which fitdecode cannot name.
    data = fit_patch.set_field_at(_bytes("sample_run"), LAP, 3, LAP_INTENSITY, 99)
    assert lap_oracle.read_laps_and_steps(data).laps[3].intensity == 99
    laps, _ = _assert_matches_oracle(data)
    assert laps[3].intensity is None
    assert laps[2].intensity == "active"


def test_repeat_step_is_stored_as_written():
    data = repeat_workout.build_repeat_workout()
    _, steps = _assert_matches_oracle(data)
    assert [s.step_index for s in steps] == [0, 1, 2, 3, 4]
    assert [s.intensity for s in steps[:3]] == ["warmup", "active", "recovery"]
    assert [s.duration_value for s in steps[:3]] == [300.0, 600.0, 120.0]
    repeat = steps[3]
    assert repeat.duration_type == "repeat_until_steps_cmplt"
    assert repeat.duration_value == 1  # duration_step: repeat from step 1
    assert repeat.target_value == 3  # repeat_steps: three times
    assert steps[2].target_type == "open"


# --- the multi-session window ----------------------------------------------------------------


def _raw_session_start_and_elapsed(data: bytes) -> tuple[int, float]:
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.global_mesg_num == SESSION:
                return frame.get_value(SESSION_START_TIME, raw_value=True), frame.get_value("total_elapsed_time")
    raise AssertionError("no session message")


def test_multi_session_window_applies_only_with_two_sessions():
    base = _bytes("sample_run")
    start_raw, elapsed = _raw_session_start_and_elapsed(base)
    after_end = int(start_raw + elapsed) + 100
    # Lap 3 loses its start, lap 9 starts after the first session ends.
    patched = fit_patch.set_field_at(base, LAP, 3, LAP_START_TIME, UINT32_INVALID)
    patched = fit_patch.set_field_at(patched, LAP, 9, LAP_START_TIME, after_end)

    # One session: every lap is mapped, the outside one and the start-less one included.
    one, _ = _assert_matches_oracle(patched)
    assert len(one) == 10
    assert one[3].start_t is None
    assert one[9].start_t == after_end - start_raw

    # Two sessions: the second is a copy of the first starting where lap 9 does.
    two_sessions = fit_patch.duplicate_message(patched, SESSION, 0)
    two_sessions = fit_patch.set_field_at(two_sessions, SESSION, 1, SESSION_START_TIME, after_end)
    two, steps = _map(two_sessions)
    assert [lap.lap_index for lap in two] == list(range(8))
    kept = [lap for i, lap in enumerate(one) if i not in (3, 9)]
    assert [(lap.start_t, lap.end_t, lap.timer_s) for lap in two] == [
        (lap.start_t, lap.end_t, lap.timer_s) for lap in kept
    ]
    assert len(steps) == 4


@pytest.mark.parametrize(
    ("offset_s", "kept"),
    [(-1, False), (0, True), (500, True), (1000, True), (1001, False), (None, False)],
)
def test_multi_session_window_bounds_are_inclusive(offset_s, kept):
    lap_start = None if offset_s is None else START + timedelta(seconds=offset_s)
    second = START + timedelta(seconds=2000)
    messages = [
        _Msg("session", start_time=START, total_elapsed_time=1000.0),
        _Msg("session", start_time=second, total_elapsed_time=500.0),
        _Msg("lap", start_time=lap_start, timestamp=lap_start),
    ]
    laps, _ = mapping.to_laps(messages)
    assert len(laps) == (1 if kept else 0)


# --- AC6: recording modes --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "index", "timer_s", "elapsed_s"),
    [("hilly_run_8k_fr945", 5, 355.299, 437.719), ("strap_run_hrv", 1, 317.823, 546.863)],
)
def test_paused_laps_match_the_oracle(name, index, timer_s, elapsed_s):
    laps, _ = _assert_matches_oracle(_bytes(name))
    lap = laps[index]
    assert (lap.timer_s, lap.elapsed_s) == (timer_s, elapsed_s)
    # The timer excludes the pause; the elapsed time and the lap's span include it.
    assert lap.timer_s < lap.elapsed_s
    assert lap.end_t - lap.start_t == pytest.approx(lap.elapsed_s, abs=1)


# --- to_canonical is unchanged ---------------------------------------------------------------


def test_to_canonical_still_returns_session_and_records():
    data = _bytes("sample_run")
    result = mapping.to_canonical(fit_parser.decode(data))
    assert isinstance(result, tuple) and len(result) == 2
    session, records = result
    assert isinstance(session, Session)
    assert all(isinstance(r, Record) for r in records)
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        record_count = sum(
            1 for f in reader if isinstance(f, fitdecode.FitDataMessage) and f.name == "record"
        )
    assert len(records) == record_count
    # Records and laps share one origin: lap 0 starts at the session start.
    laps, _ = _map(data)
    assert laps[0].start_t == 0
    assert records[0].t >= 0
    assert mapping.to_laps([]) == ([], [])


# --- the adversarial-probe table -------------------------------------------------------------


def _sample_without(*global_nums: int) -> bytes:
    data = _bytes("sample_run")
    for num in global_nums:
        data = fit_patch.remove_messages(data, num)
    return data


_PROBES = {
    "lap start invalid": (
        lambda: fit_patch.set_field_at(_bytes("sample_run"), LAP, 4, LAP_START_TIME, UINT32_INVALID),
        lambda laps, steps: len(laps) == 10 and laps[4].start_t is None and laps[4].end_t is not None,
    ),
    "timer invalid": (
        lambda: fit_patch.set_field_at(_bytes("sample_run"), LAP, 4, LAP_TIMER, UINT32_INVALID),
        lambda laps, steps: laps[4].timer_s is None and laps[4].elapsed_s is not None,
    ),
    "intensity 99": (
        lambda: fit_patch.set_field_at(_bytes("sample_run"), LAP, 0, LAP_INTENSITY, 99),
        lambda laps, steps: laps[0].intensity is None and laps[0].wkt_step_index == 0,
    ),
    "target_type 255": (
        lambda: fit_patch.set_field_at(_bytes("sample_run"), WORKOUT_STEP, 1, STEP_TARGET_TYPE, 255),
        # With no target type fitdecode cannot resolve the custom target subfield, so it returns
        # the unscaled raw number (2901, not 2.901 m/s); that is stored as returned.
        lambda laps, steps: steps[1].target_type is None and steps[1].target_low == 2901,
    ),
    "no lap and no step messages": (
        lambda: _sample_without(LAP, WORKOUT_STEP),
        lambda laps, steps: laps == [] and steps == [],
    ),
    "a workout message with no steps": (
        lambda: _sample_without(WORKOUT_STEP),
        lambda laps, steps: len(laps) == 10 and steps == [],
    ),
    "a step index beyond the steps": (
        lambda: fit_patch.set_field_at(_bytes("sample_run"), LAP, 0, LAP_WKT_STEP_INDEX, 50),
        lambda laps, steps: laps[0].wkt_step_index == 50 and len(steps) == 4,
    ),
}


@pytest.mark.parametrize("probe", list(_PROBES))
def test_adversarial_probe_table(probe):
    build, check = _PROBES[probe]
    data = build()
    laps, steps = _assert_matches_oracle(data)
    assert check(laps, steps), probe
    # The session and its records map as before.
    session, records = mapping.to_canonical(fit_parser.decode(data))
    base_session, base_records = mapping.to_canonical(fit_parser.decode(_bytes("sample_run")))
    assert session.session_id == base_session.session_id
    assert len(records) == len(base_records)
