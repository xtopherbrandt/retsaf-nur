"""``tests/support/lap_oracle.py``: laps and workout steps read with ``fitdecode`` alone.

The oracle is the independent reader F025's ingest tests compare stored rows with, so its
expected values here do not come from the oracle or from any ``runcoach_api`` code: the
counts, the ``sample_run`` step table and the two paused laps are typed in by hand from the
2026-10-16 analyst decode, recorded in the F025 reference's section 1.

All three files have run proof ``yes`` in ``tests/fixtures/README.md`` (``hilly_run_8k_fr945``
had Smart recording selected and wrote 1 Hz; positions stripped). The two paused laps are
``hilly_run_8k_fr945`` lap 5 (82 s pause) and ``strap_run_hrv`` lap 1 (229 s pause): their
``elapsed_s`` exceeds ``timer_s`` by the pause. No position value is read.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE_PATH = Path(__file__).parent / "support" / "lap_oracle.py"


def _oracle():
    spec = importlib.util.spec_from_file_location("lap_oracle", ORACLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(name: str):
    return _oracle().read_laps_and_steps((FIXTURES / name).read_bytes())


@pytest.mark.parametrize(
    ("name", "laps", "steps"),
    [
        ("sample_run.fit", 10, 4),
        ("hilly_run_8k_fr945.fit", 10, 4),
        ("strap_run_hrv.fit", 21, 4),
    ],
)
def test_oracle_counts_laps_and_steps_of_the_three_structured_fixtures(
    name: str, laps: int, steps: int
) -> None:
    read = _read(name)
    print(f"{name}: {len(read.laps)} laps, {len(read.steps)} steps, workout {read.workout_name!r}")
    assert len(read.laps) == laps
    assert len(read.steps) == steps
    assert read.laps[0].start_t == 0
    assert [s.step_index for s in read.steps] == [0, 1, 2, 3]
    assert [s.intensity for s in read.steps] == ["warmup", "active", "active", "cooldown"]


def test_oracle_reads_the_sample_run_step_table() -> None:
    """The F025 reference section 1 table for ``sample_run``, typed in by hand."""
    read = _read("sample_run.fit")
    table = [
        (
            s.step_index,
            s.intensity,
            s.duration_type,
            s.duration_value,
            s.target_type,
            s.target_low,
            s.target_high,
        )
        for s in read.steps
    ]
    for row in table:
        print(row)
    assert table == [
        (0, "warmup", "time", 300.0, "open", None, None),
        (1, "active", "time", 1200.0, "speed", 2.901, 3.253),
        (2, "active", "time", 900.0, "speed", 2.901, 3.253),
        (3, "cooldown", "time", 300.0, "open", None, None),
    ]
    # Laps per step 1, 4, 3, 2; the last lap has no step index and ends the session.
    assert [lap.wkt_step_index for lap in read.laps] == [0, 1, 1, 1, 1, 2, 2, 2, 3, None]
    assert read.laps[-1].lap_trigger == "session_end"
    # Lap end is the lap timestamp, whole seconds: lap 1 starts 300 and runs 377.535 s (to
    # 677.535), its timestamp is 678, and lap 2 starts 679 (F025 reference section 1).
    assert (read.laps[1].start_t, read.laps[1].end_t, read.laps[2].start_t) == (300, 678, 679)


def test_oracle_reads_the_two_real_paused_laps() -> None:
    hilly = _read("hilly_run_8k_fr945.fit").laps[5]
    strap = _read("strap_run_hrv.fit").laps[1]
    for label, lap in (("hilly lap 5", hilly), ("strap_run_hrv lap 1", strap)):
        print(
            f"{label}: timer {lap.timer_s}, elapsed {lap.elapsed_s}, start_t {lap.start_t}, end_t {lap.end_t}"
        )
    assert (hilly.timer_s, hilly.elapsed_s, hilly.start_t, hilly.end_t) == (355.299, 437.719, 1600, 2037)
    assert (strap.timer_s, strap.elapsed_s, strap.start_t, strap.end_t) == (317.823, 546.863, 300, 847)
    assert hilly.distance_m == strap.distance_m == 1000.0
    assert hilly.intensity == strap.intensity == "active"


def test_oracle_resolves_the_repeat_workout_step() -> None:
    spec = importlib.util.spec_from_file_location(
        "repeat_workout", Path(__file__).parent / "support" / "repeat_workout.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    read = _oracle().read_laps_and_steps(module.build_repeat_workout())
    repeat = read.steps[3]
    print(repeat)
    assert len(read.steps) == 5
    assert (repeat.duration_type, repeat.duration_name, repeat.duration_value) == (
        "repeat_until_steps_cmplt",
        "duration_step",
        1,
    )
    assert (repeat.target_name, repeat.target_value) == ("repeat_steps", 3)
    assert (read.steps[2].intensity, read.steps[2].duration_value, read.steps[2].target_type) == (
        "recovery",
        120.0,
        "open",
    )


def test_oracle_imports_nothing_from_runcoach_api() -> None:
    tree = ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    print(sorted(modules))
    assert not any(m.split(".")[0] == "runcoach_api" for m in modules)
    assert "fitdecode" in modules
