"""Laps and workout steps read from FIT bytes with ``fitdecode`` alone: the independent oracle.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. It imports nothing from ``runcoach_api`` (``test_lap_oracle.py``
pins that by parsing this file's imports), so values it reads cannot share code with the
ingest mapping they are compared with
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``).

``read_laps_and_steps(data)`` decodes every frame with both CRCs checked and returns:

- ``laps``, one ``Lap`` per ``lap`` message in file order: ``start_t`` and ``end_t`` are the
  lap ``start_time`` (field 2) and ``timestamp`` (field 253) minus the first ``session``
  message's ``start_time``, in whole seconds from the raw ``uint32`` values (``None`` when the
  field or the session start is absent); ``timer_s`` (``total_timer_time``, 8), ``elapsed_s``
  (``total_elapsed_time``, 7) and ``distance_m`` (``total_distance``, 9) are fitdecode's scaled
  values; ``intensity`` (23), ``lap_trigger`` (24) and ``wkt_step_index`` (71) are its resolved
  values (an enum name, or an int it cannot name).
- ``steps``, one ``Step`` per ``workout_step`` message in file order, with fitdecode's resolved
  values: subfields resolved by ``duration_type`` and ``target_type`` and scaled (a time step's
  ``duration_value`` in seconds, a speed target in m/s; a repeat step's ``duration_value`` is
  ``duration_step``, the step it repeats from, and its ``target_value`` is ``repeat_steps``).
  ``duration_name`` and ``target_name`` give the name fitdecode resolved fields 2 and 4 to.
- ``workout_name``, the first ``workout`` message's ``wkt_name``.

Field numbers are the FIT profile's; fields are found by number, not by name, so a subfield
rename cannot hide one. No position field is read.
"""

from __future__ import annotations

import io
from typing import NamedTuple

import fitdecode

SESSION, LAP, WORKOUT, WORKOUT_STEP = 18, 19, 26, 27
TIMESTAMP = 253
START_TIME = 2


class Lap(NamedTuple):
    start_t: int | None
    end_t: int | None
    timer_s: float | None
    elapsed_s: float | None
    distance_m: float | None
    intensity: object
    wkt_step_index: int | None
    lap_trigger: object


class Step(NamedTuple):
    step_index: int | None
    step_name: str | None
    intensity: object
    duration_type: object
    duration_name: str | None
    duration_value: object
    target_type: object
    target_name: str | None
    target_value: object
    target_low: object
    target_high: object


class Read(NamedTuple):
    laps: list[Lap]
    steps: list[Step]
    workout_name: str | None


def _field(message: fitdecode.FitDataMessage, num: int):
    """The first field numbered ``num`` in ``message``, or ``None``."""
    for field in message.fields:
        if field.def_num == num and getattr(field.field_def, "dev_data_index", None) is None:
            return field
    return None


def _value(message: fitdecode.FitDataMessage, num: int):
    field = _field(message, num)
    return None if field is None else field.value


def _raw(message: fitdecode.FitDataMessage, num: int):
    field = _field(message, num)
    return None if field is None else field.raw_value


def _name(message: fitdecode.FitDataMessage, num: int) -> str | None:
    field = _field(message, num)
    return None if field is None else field.name


def _minus(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a - b


def read_laps_and_steps(data: bytes) -> Read:
    """Laps, steps and the workout name of ``data``, read with ``fitdecode`` only."""
    messages = []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                messages.append(frame)
    session = next((m for m in messages if m.global_mesg_num == SESSION), None)
    origin = None if session is None else _raw(session, START_TIME)
    laps = [
        Lap(
            start_t=_minus(_raw(m, START_TIME), origin),
            end_t=_minus(_raw(m, TIMESTAMP), origin),
            timer_s=_value(m, 8),
            elapsed_s=_value(m, 7),
            distance_m=_value(m, 9),
            intensity=_value(m, 23),
            wkt_step_index=_value(m, 71),
            lap_trigger=_value(m, 24),
        )
        for m in messages
        if m.global_mesg_num == LAP
    ]
    steps = [
        Step(
            step_index=_value(m, 254),
            step_name=_value(m, 0),
            intensity=_value(m, 7),
            duration_type=_value(m, 1),
            duration_name=_name(m, 2),
            duration_value=_value(m, 2),
            target_type=_value(m, 3),
            target_name=_name(m, 4),
            target_value=_value(m, 4),
            target_low=_value(m, 5),
            target_high=_value(m, 6),
        )
        for m in messages
        if m.global_mesg_num == WORKOUT_STEP
    ]
    workout = next((m for m in messages if m.global_mesg_num == WORKOUT), None)
    return Read(laps, steps, None if workout is None else _value(workout, 8))
