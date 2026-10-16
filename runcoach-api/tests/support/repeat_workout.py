"""A synthetic repeat workout: ``sample_run.fit``'s four workout steps patched into five.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. No repo fixture carries a ``recovery`` step or a repeat
step, so this builds one in memory with ``fit_patch``; nothing is written to
``tests/fixtures/``.

``build_repeat_workout()`` returns the bytes. Steps are 0-based ``message_index``
values; the raw values are the FIT profile's (``duration_value`` of a time step in ms):

| step | from | intensity | duration | target |
|---|---|---|---|---|
| 0 | step 0, as written | warmup (2) | time 300 s | open |
| 1 | step 1, ``duration_value`` 600000 | active (0) | time 600 s | speed, as written |
| 2 | step 2, ``intensity`` 4, ``duration_value`` 120000, ``target_type`` 2 | recovery (4) | time 120 s | open |
| 3 | step 3 (the cool-down), ``duration_type`` 6, ``duration_value`` 1, ``target_value`` 3 | cooldown (3), as written | repeat from step 1 | 3 times |
| 4 | a byte copy of step 3, ``message_index`` 4 | cooldown (3) | time 300 s | open |

``workout.num_valid_steps`` (field 6) is 5. The laps, the records and the session stay
as ``sample_run.fit`` wrote them: this is a step-table fixture, and its laps still carry
the original ``wkt_step_index`` values 0 to 3.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("fit_patch", Path(__file__).resolve().parent / "fit_patch.py")
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

SOURCE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run.fit"

WORKOUT = 26
WORKOUT_STEP = 27

# workout_step fields
DURATION_TYPE = 1
DURATION_VALUE = 2
TARGET_TYPE = 3
TARGET_VALUE = 4
INTENSITY = 7
MESSAGE_INDEX = 254
# workout fields
NUM_VALID_STEPS = 6

INTENSITY_RECOVERY = 4
TARGET_TYPE_OPEN = 2
DURATION_TYPE_REPEAT_UNTIL_STEPS_CMPLT = 6


def build_repeat_workout(source: bytes | None = None) -> bytes:
    """The five-step repeat workout, from ``source`` (default ``sample_run.fit``'s bytes)."""
    data = SOURCE.read_bytes() if source is None else source
    data = fit_patch.duplicate_message(data, WORKOUT_STEP, 3)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 4, MESSAGE_INDEX, 4)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 1, DURATION_VALUE, 600_000)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 2, INTENSITY, INTENSITY_RECOVERY)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 2, DURATION_VALUE, 120_000)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 2, TARGET_TYPE, TARGET_TYPE_OPEN)
    data = fit_patch.set_field_at(
        data, WORKOUT_STEP, 3, DURATION_TYPE, DURATION_TYPE_REPEAT_UNTIL_STEPS_CMPLT
    )
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 3, DURATION_VALUE, 1)
    data = fit_patch.set_field_at(data, WORKOUT_STEP, 3, TARGET_VALUE, 3)
    return fit_patch.set_field(data, WORKOUT, NUM_VALID_STEPS, 5)
