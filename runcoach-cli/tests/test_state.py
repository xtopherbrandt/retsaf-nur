"""``runcoach state``: threshold pace from ``GET /state``, the races and workouts used and excluded.

Every test runs the real command against ``install_mock_client``'s fake API, with bodies shaped as
the API's ``StateModel`` serves them (``as_of`` and start times in UTC with a ``Z``). The lines
compared are F024 reference section 6's, with F026 reference section 5's workouts, IDEA-167's
weights and F027 reference section 5's admission after the date (``(tag)``, ``(name Progression
Run)``, ``(tag, name Goal Pace Run)``) and its ``no_sustained_work`` reason in words.

The bodies here serve no ``riegel_exponent`` entry, so every output ends with ``OLDER_API_LINE``
(F029 reference section 6); the endurance exponent block is ``test_state_riegel.py``'s.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.main import app

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

OLDER_API_LINE = "Endurance exponent: not served by this API; update it and restart"

PARAMETERS = {
    "typical_error": 0.025,
    "doubling_days": 42,
    "coverage": 0.8,
    "high_max_error": 0.025,
    "medium_max_error": 0.05,
    "short_race_s": 1800,
    "workout_min_active_s": 590,
    "workout_min_rep_s": 290,
    "workout_rep_band": 1.10,
}


def _effort(session_id: str, start: str, duration_s: float, gap: float, gain: float, degraded: float | None = 0.0):
    return {
        "session_id": session_id,
        "start_time": start,
        "kind": "race",
        "duration_s": duration_s,
        "gap_pace_s_per_km": gap,
        "gap_coverage": 1.0,
        "gps_degraded_fraction": degraded,
        "gain": gain,
        "active_blocks": None,
        "admitted_by": None,
        "workout_name": None,
    }


def _workout(
    session_id: str,
    start: str,
    active_s: float,
    gap: float,
    gain: float,
    blocks: int,
    degraded=0.0,
    admitted_by: list[str] | None = None,
    workout_name: str | None = None,
):
    """A threshold workout as ``ThresholdEffort`` serves it: ``duration_s`` its counted time,
    ``admitted_by`` ``["tag"]`` unless given."""
    return {
        **_effort(session_id, start, active_s, gap, gain, degraded),
        "kind": "threshold_workout",
        "active_blocks": blocks,
        "admitted_by": ["tag"] if admitted_by is None else admitted_by,
        "workout_name": workout_name,
    }


def _excluded_workout(session_id: str, start: str, reason: str, admitted_by=("tag",), workout_name=None):
    """A workout as ``ThresholdExclusion`` serves it."""
    return {
        "session_id": session_id,
        "start_time": start,
        "kind": "threshold_workout",
        "reason": reason,
        "admitted_by": list(admitted_by),
        "workout_name": workout_name,
    }


ESTIMATE_BODY: dict[str, Any] = {
    "as_of": "2026-10-15T08:00:00Z",
    "timezone": "UTC",
    "determinants": [
        {
            "name": "functional_threshold_pace",
            "estimate": {
                "value": 261.97,
                "unit": "s/km",
                "interval": {"low": 254.57, "high": 269.59},
                "confidence": "high",
                "provisional": False,
            },
            "unavailable": None,
            "trend": {"direction": "unknown", "rate": None},
            "error": 0.02236,
            "short_races_only": False,
            "efforts": [
                _effort("s-a", "2026-08-09T07:00:00Z", 2500.0, 270.0, 1.0, 0.0),
                _effort("s-b", "2026-09-20T07:00:00Z", 2499.5, 260.0, 0.8, 0.0312),
            ],
            "excluded": [],
            "parameters": PARAMETERS,
        }
    ],
    "readiness": None,
    "gap_to_goal": None,
}


def _unavailable_body(reason: str = "no_qualifying_effort") -> dict[str, Any]:
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det.update(estimate=None, unavailable=reason, error=None, short_races_only=False, efforts=[])
    return body


def _serve(install_mock_client, body: Any, status: int = 200, seen: list[httpx.Request] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.url.path == "/state"
        return httpx.Response(status, json=body)

    install_mock_client(handler)


def _run(*args: str):
    return runner.invoke(app, ["state", *args])


def _lines(result) -> list[str]:
    return result.stdout.splitlines()


def test_estimate_line_prints_pace_interval_label_and_race_count(install_mock_client) -> None:
    _serve(install_mock_client, ESTIMATE_BODY)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[0] == "Threshold pace 4:22/km (80 %: 4:15-4:30), high, from 2 races, the last on 2026-09-20"


def test_estimate_line_says_one_race_in_the_singular(install_mock_client) -> None:
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = det["efforts"][:1]
    det["estimate"].update(value=270.0, interval={"low": 261.49, "high": 278.79}, confidence="medium")
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[0] == "Threshold pace 4:30/km (80 %: 4:21-4:39), medium, from 1 race, the last on 2026-08-09"


def test_pace_rounds_half_up(install_mock_client) -> None:
    """298.5 s/km is 4:59 and 299.5 is 5:00, as ``runcoach sessions`` prints them."""
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["estimate"].update(value=299.5, interval={"low": 298.5, "high": 359.5})
    _serve(install_mock_client, body)
    result = _run()
    assert _lines(result)[0].startswith("Threshold pace 5:00/km (80 %: 4:59-6:00), high,")


def test_each_race_used_prints_date_duration_gap_gain_and_gps(install_mock_client) -> None:
    _serve(install_mock_client, ESTIMATE_BODY)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[1:] == [
        "Race used 2026-08-09: 0:41:40, GAP 4:30/km, gain 1.00, degraded GPS 0.0 %, weight 20 %",
        "Race used 2026-09-20: 0:41:40, GAP 4:20/km, gain 0.80, degraded GPS 3.1 %, weight 80 %",
        OLDER_API_LINE,
    ], result.stdout


def test_a_race_with_no_degraded_gps_fraction_prints_a_dash(install_mock_client) -> None:
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["efforts"][1]["gps_degraded_fraction"] = None
    _serve(install_mock_client, body)
    result = _run()
    assert _lines(result)[2] == "Race used 2026-09-20: 0:41:40, GAP 4:20/km, gain 0.80, degraded GPS -, weight 80 %"


def _mixed_body(races: int, workouts: int) -> dict[str, Any]:
    """``races`` races then ``workouts`` workouts, a week apart from 2026-08-02, every later gain 0.5."""
    body = copy.deepcopy(ESTIMATE_BODY)
    efforts = []
    for i in range(races + workouts):
        start = f"2026-08-{2 + 7 * i:02d}T07:00:00Z"
        gain = 1.0 if i == 0 else 0.5
        if i < races:
            efforts.append(_effort(f"r-{i}", start, 2500.0, 265.0, gain))
        else:
            efforts.append(_workout(f"w-{i}", start, 1630.0, 266.0, gain, 1))
    body["determinants"][0]["efforts"] = efforts
    return body


@pytest.mark.parametrize(
    ("races", "workouts", "words", "last"),
    [
        (2, 1, "from 2 races and 1 workout", "2026-08-16"),
        (1, 2, "from 1 race and 2 workouts", "2026-08-16"),
        (0, 1, "from 1 workout", "2026-08-02"),
        (0, 3, "from 3 workouts", "2026-08-16"),
        (2, 0, "from 2 races", "2026-08-09"),
    ],
)
def test_heading_counts_races_and_workouts(install_mock_client, races, workouts, words, last) -> None:
    _serve(install_mock_client, _mixed_body(races, workouts))
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[0] == f"Threshold pace 4:22/km (80 %: 4:15-4:30), high, {words}, the last on {last}"


def test_a_workout_line(install_mock_client) -> None:
    """F026 reference section 5's example line: the active time, its blocks, the GAP pace over the
    active windows, the gain and the degraded-GPS share; then three blocks in the plural."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = [
        _effort("s-a", "2026-09-20T07:00:00Z", 2500.0, 270.0, 1.0),
        _workout("w-1", "2026-09-30T06:00:00Z", 1630.0, 266.0, 0.98, 1, 0.0),
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[2] == (
        "Workout used 2026-09-30 (tag): 0:27:10 active in 1 block, GAP 4:26/km, gain 0.98, degraded GPS 0.0 %, "
        "weight 98 %"
    )
    det["efforts"][1].update(active_blocks=3, gps_degraded_fraction=None, duration_s=1800.0)
    _serve(install_mock_client, body)
    assert _lines(_run())[2] == (
        "Workout used 2026-09-30 (tag): 0:30:00 active in 3 blocks, GAP 4:26/km, gain 0.98, degraded GPS -, "
        "weight 98 %"
    )
    for blocks in (None, "3", True, 1.5):
        det["efforts"][1]["active_blocks"] = blocks
        _serve(install_mock_client, body)
        broken = _run()
        assert broken.exit_code == 1, blocks
        assert "the API's response could not be understood" in broken.stderr


WORKOUT_REASON_WORDS = {
    "laps_not_read": "its laps have not been read: run runcoach sync laps --since 2026-07-01",
    "lap_times_unreadable": "its stored lap times could not be read",
    "no_workout_steps": "no lap of a work step",
    "no_sustained_work": "no work rep of 4:50 or longer",
    "active_time_below_minimum": "under 9:50 of counted work reps",
    "laps_unreadable": "its stored laps could not be read",
    "smart_recording": "recorded in smart or irregular recording mode",
    "gap_pace_unavailable": "no grade-adjusted pace",
}


def test_workout_exclusion_reasons_in_words(install_mock_client) -> None:
    """Each workout reason in words, every one on 2026-07-01 so the sync laps hint names that day; a
    race excluded beside them keeps its ``Race excluded`` prefix."""
    body = copy.deepcopy(ESTIMATE_BODY)
    excluded = [
        _excluded_workout(f"w-{i}", "2026-07-01T07:00:00Z", reason) for i, reason in enumerate(WORKOUT_REASON_WORDS)
    ]
    excluded.append(
        {"session_id": "r", "start_time": "2026-07-02T07:00:00Z", "kind": "race", "reason": "outside_threshold_band"}
    )
    body["determinants"][0]["excluded"] = excluded
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[3:] == [
        f"Workout excluded 2026-07-01 (tag): {words}" for words in WORKOUT_REASON_WORDS.values()
    ] + ["Race excluded 2026-07-02: not 20 to 60 minutes long", OLDER_API_LINE], result.stdout
    assert not any(reason in result.stdout for reason in WORKOUT_REASON_WORDS)

    # The hint's date is the workout's own, in the response's timezone.
    body["timezone"] = "Pacific/Auckland"
    body["determinants"][0]["excluded"] = [_excluded_workout("w", "2026-07-01T23:30:00Z", "laps_not_read")]
    _serve(install_mock_client, body)
    assert _lines(_run())[-2] == (
        "Workout excluded 2026-07-02 (tag): its laps have not been read: run runcoach sync laps --since 2026-07-02"
    )

    # The minimum is the served parameter's.
    body["determinants"][0]["parameters"] = {**PARAMETERS, "workout_min_active_s": 900}
    body["determinants"][0]["excluded"][0]["reason"] = "active_time_below_minimum"
    _serve(install_mock_client, body)
    assert _lines(_run())[-2] == "Workout excluded 2026-07-02 (tag): under 15:00 of counted work reps"


def test_a_used_workout_shows_its_admission_after_the_date(install_mock_client) -> None:
    """F027 reference section 5's line: a name-admitted workout reads ``(name Progression Run)``
    after the date, the name as the API serves it; a tag-admitted one reads ``(tag)``."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = [
        _workout("w-tag", "2026-09-20T07:00:00Z", 1500.0, 270.0, 1.0, 1),
        _workout("w-name", "2026-10-17T06:00:00Z", 900.0, 266.0, 0.98, 1, 0.0, ["name"], "Progression Run"),
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[1:] == [
        (
            "Workout used 2026-09-20 (tag): 0:25:00 active in 1 block, GAP 4:30/km, gain 1.00, degraded GPS 0.0 %, "
            "weight 2 %"
        ),
        (
            "Workout used 2026-10-17 (name Progression Run): 0:15:00 active in 1 block, GAP 4:26/km, gain 0.98, "
            "degraded GPS 0.0 %, weight 98 %"
        ),
        OLDER_API_LINE,
    ], result.stdout

    # A tag-admitted workout that carries a name prints the tag alone: the name did not admit it.
    det["efforts"][0]["workout_name"] = "Tempo Run"
    _serve(install_mock_client, body)
    assert _lines(_run())[1].startswith("Workout used 2026-09-20 (tag): ")


def test_an_excluded_workout_shows_its_admission_after_the_date(install_mock_client) -> None:
    """The admission is shown on an excluded workout too (the athlete's ruling): the Speed Repeats
    admitted by name and refused for want of a sustained rep. A race excluded beside it carries
    ``admitted_by`` null and prints no parenthesis."""
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["excluded"] = [
        _excluded_workout("w", "2026-10-07T06:00:00Z", "no_sustained_work", ["name"], "Speed Repeats"),
        {
            "session_id": "r",
            "start_time": "2026-10-08T06:00:00Z",
            "kind": "race",
            "reason": "outside_threshold_band",
            "admitted_by": None,
            "workout_name": None,
        },
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[3:] == [
        "Workout excluded 2026-10-07 (name Speed Repeats): no work rep of 4:50 or longer",
        "Race excluded 2026-10-08: not 20 to 60 minutes long",
        OLDER_API_LINE,
    ], result.stdout


def test_tag_and_name_admission_reads_tag_comma_name(install_mock_client) -> None:
    """``["tag", "name"]`` reads ``(tag, name Goal Pace Run)``, in the served order; on an excluded
    workout too. A tag-admitted workout whose stored name is the empty string reads ``(tag)``: the
    name is printed only beside the ``name`` admitter. (``["name"]`` with an empty name cannot be
    served: the API refuses an empty entry in ``threshold_workout_names`` and compares names
    stripped, so an empty stored name admits nothing.) An admitter the CLI does not know is refused
    rather than printed."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = [
        _effort("s-a", "2026-09-20T07:00:00Z", 2500.0, 270.0, 1.0),
        _workout("w-1", "2026-09-30T06:00:00Z", 1630.0, 266.0, 0.98, 1, 0.0, ["tag", "name"], "Goal Pace Run"),
    ]
    det["excluded"] = [
        _excluded_workout("w-2", "2026-10-01T06:00:00Z", "active_time_below_minimum", ["tag", "name"], "Goal Pace Run"),
        _excluded_workout("w-3", "2026-10-02T06:00:00Z", "no_workout_steps", ["tag"], ""),
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[2:] == [
        (
            "Workout used 2026-09-30 (tag, name Goal Pace Run): 0:27:10 active in 1 block, GAP 4:26/km, gain 0.98, "
            "degraded GPS 0.0 %, weight 98 %"
        ),
        "Workout excluded 2026-10-01 (tag, name Goal Pace Run): under 9:50 of counted work reps",
        "Workout excluded 2026-10-02 (tag): no lap of a work step",
        OLDER_API_LINE,
    ], result.stdout

    for admitted_by in (["coach"], "tag", [1], [None]):
        det["efforts"][1]["admitted_by"] = admitted_by
        _serve(install_mock_client, body)
        broken = _run()
        assert broken.exit_code == 1, admitted_by
        assert "the API's response could not be understood" in broken.stderr


def test_no_sustained_work_reads_in_words(install_mock_client) -> None:
    """``no_sustained_work`` names the served rep minimum as m:ss: 290 s reads 4:50 and 300 s reads
    5:00; an older API without ``workout_min_rep_s`` reads 4:50 too."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["excluded"] = [_excluded_workout("w", "2026-10-07T06:00:00Z", "no_sustained_work")]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[-2] == "Workout excluded 2026-10-07 (tag): no work rep of 4:50 or longer"
    assert "no_sustained_work" not in result.stdout

    det["parameters"] = {**PARAMETERS, "workout_min_rep_s": 300}
    _serve(install_mock_client, body)
    assert _lines(_run())[-2] == "Workout excluded 2026-10-07 (tag): no work rep of 5:00 or longer"

    det["parameters"] = {k: v for k, v in PARAMETERS.items() if k != "workout_min_rep_s"}
    _serve(install_mock_client, body)
    assert _lines(_run())[-2] == "Workout excluded 2026-10-07 (tag): no work rep of 4:50 or longer"


def test_the_workout_minimum_reads_9_50(install_mock_client) -> None:
    """The served ``workout_min_active_s`` 590 reads ``9:50`` in the exclusion and in the unavailable
    line, never a decimal number of minutes; 600 reads ``10:00``, 605 reads ``10:05``."""
    body = _unavailable_body()
    det = body["determinants"][0]
    det["excluded"] = [_excluded_workout("w", "2026-10-07T06:00:00Z", "active_time_below_minimum")]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert "no threshold workout with 9:50 of counted work reps" in lines[0]
    assert lines[1] == "Workout excluded 2026-10-07 (tag): under 9:50 of counted work reps"
    assert "9.8" not in result.stdout and "minutes of" not in result.stdout

    for seconds, clock in ((600, "10:00"), (605, "10:05")):
        det["parameters"] = {**PARAMETERS, "workout_min_active_s": seconds}
        _serve(install_mock_client, body)
        lines = _lines(_run())
        assert f"no threshold workout with {clock} of counted work reps" in lines[0], seconds
        assert lines[1] == f"Workout excluded 2026-10-07 (tag): under {clock} of counted work reps"


def test_an_older_api_without_admitted_by_prints_no_parenthesis(install_mock_client) -> None:
    """A workout from an API before the admission fields (no ``admitted_by`` or ``workout_name``
    key) prints the date with no parenthesis, as an absent ``kind`` reads as a race; so does a
    served ``admitted_by`` null or ``[]``, which the API never writes on a workout."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = [
        _effort("s-a", "2026-09-20T07:00:00Z", 2500.0, 270.0, 1.0),
        _workout("w-1", "2026-09-30T06:00:00Z", 1630.0, 266.0, 0.98, 1),
    ]
    det["excluded"] = [_excluded_workout("w-2", "2026-10-01T06:00:00Z", "no_workout_steps")]
    for row in (det["efforts"][1], det["excluded"][0]):
        del row["admitted_by"], row["workout_name"]
    det["parameters"] = {k: v for k, v in PARAMETERS.items() if k not in ("workout_min_rep_s", "workout_rep_band")}
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[2:] == [
        "Workout used 2026-09-30: 0:27:10 active in 1 block, GAP 4:26/km, gain 0.98, degraded GPS 0.0 %, weight 98 %",
        "Workout excluded 2026-10-01: no lap of a work step",
        OLDER_API_LINE,
    ], result.stdout

    for admitted_by in (None, []):
        det["efforts"][1]["admitted_by"] = admitted_by
        det["excluded"][0]["admitted_by"] = admitted_by
        _serve(install_mock_client, body)
        lines = _lines(_run())
        assert lines[2].startswith("Workout used 2026-09-30: "), admitted_by
        assert lines[3] == "Workout excluded 2026-10-01: no lap of a work step", admitted_by


def test_each_effort_line_shows_its_weight(install_mock_client) -> None:
    """Gains 1.0 then 0.98, by hand: the first enters at 1 and keeps 1 - 0.98 = 2 %; the second 98 %."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"][1]["gain"] = 0.98
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert lines[1].endswith(", gain 1.00, degraded GPS 0.0 %, weight 2 %")
    assert lines[2].endswith(", gain 0.98, degraded GPS 3.1 %, weight 98 %")

    # Three, by hand: gains 1, 0.5, 0.5 give 0.25, 0.25, 0.5.
    _serve(install_mock_client, _mixed_body(1, 2))
    assert [line.rsplit(", weight ", 1)[1] for line in _lines(_run())[1:-1]] == ["25 %", "25 %", "50 %"]


def test_weights_sum_to_one() -> None:
    """``w_i = K_i * prod_{j>i}(1 - K_j)`` sums to 1 whenever the first gain is 1, for any later gains."""
    import random

    from runcoach_cli.commands import state as state_command

    rng = random.Random(20261016)
    for _ in range(200):
        gains = [1.0] + [rng.random() for _ in range(rng.randrange(0, 12))]
        weights = state_command.weights(gains)
        assert len(weights) == len(gains)
        assert all(0.0 <= w <= 1.0 for w in weights)
        assert math.fsum(weights) == pytest.approx(1.0, abs=1e-12), gains
    assert state_command.weights([1.0, 0.98]) == pytest.approx([0.02, 0.98], abs=1e-15)
    assert state_command.weights([1.0]) == [1.0]
    assert state_command.weights([]) == []


def test_smart_recording_is_worded_the_same_for_races_and_workouts(install_mock_client) -> None:
    """A race and a workout excluded as ``smart_recording`` print the same words, which name both
    recording modes the one flag covers."""
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["excluded"] = [
        {"session_id": "r", "start_time": "2026-07-01T07:00:00Z", "kind": "race", "reason": "smart_recording"},
        {"session_id": "w", "start_time": "2026-07-02T07:00:00Z", "kind": "threshold_workout", "reason": "smart_recording"},
    ]
    _serve(install_mock_client, body)
    lines = _lines(_run())
    assert lines[3:] == [
        "Race excluded 2026-07-01: recorded in smart or irregular recording mode",
        "Workout excluded 2026-07-02: recorded in smart or irregular recording mode",
        OLDER_API_LINE,
    ]
    assert "recorded in smart recording mode" not in "\n".join(lines)


def test_an_older_api_body_without_kind_still_renders(install_mock_client) -> None:
    """A body from an API before F026 has no ``kind``, ``active_blocks`` or ``workout_min_active_s``:
    every effort and exclusion reads as a race."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    for effort in det["efforts"]:
        del effort["kind"], effort["active_blocks"], effort["admitted_by"], effort["workout_name"]
    det["parameters"] = {
        k: v for k, v in PARAMETERS.items() if k not in ("workout_min_active_s", "workout_min_rep_s", "workout_rep_band")
    }
    det["excluded"] = [{"session_id": "x", "start_time": "2026-07-01T07:00:00Z", "reason": "outside_threshold_band"}]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [
        "Threshold pace 4:22/km (80 %: 4:15-4:30), high, from 2 races, the last on 2026-09-20",
        "Race used 2026-08-09: 0:41:40, GAP 4:30/km, gain 1.00, degraded GPS 0.0 %, weight 20 %",
        "Race used 2026-09-20: 0:41:40, GAP 4:20/km, gain 0.80, degraded GPS 3.1 %, weight 80 %",
        "Race excluded 2026-07-01: not 20 to 60 minutes long",
        OLDER_API_LINE,
    ], result.stdout

    # A kind the CLI does not know is refused rather than printed as a race.
    det["efforts"][0]["kind"] = "tempo_run"
    _serve(install_mock_client, body)
    unknown = _run()
    assert unknown.exit_code == 1
    assert "the API's response could not be understood" in unknown.stderr


REASON_WORDS = {
    "outside_threshold_band": "not 20 to 60 minutes long",
    "distance_regressed": "its distance went backwards in the recording",
    "smart_recording": "recorded in smart or irregular recording mode",
    "gap_pace_unavailable": "no grade-adjusted pace",
    "summary_unreadable": "its stored summary could not be read",
    "quality_flags_unreadable": "its stored quality flags could not be read",
    "features_unavailable": "its pace features could not be computed",
}


def test_each_excluded_race_prints_its_reason_in_words(install_mock_client) -> None:
    body = copy.deepcopy(ESTIMATE_BODY)
    reasons = list(REASON_WORDS)
    body["determinants"][0]["excluded"] = [
        {"session_id": f"x-{i}", "start_time": f"2026-07-{i + 1:02d}T07:00:00Z", "reason": reason}
        for i, reason in enumerate(reasons)
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[3:] == [
        *(f"Race excluded 2026-07-{i + 1:02d}: {REASON_WORDS[reason]}" for i, reason in enumerate(reasons)),
        OLDER_API_LINE,
    ], result.stdout
    # No snake_case reason leaks into the words.
    assert not any(reason in result.stdout for reason in reasons)


def test_an_unknown_reason_prints_as_served(install_mock_client) -> None:
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["excluded"] = [
        {"session_id": "x", "start_time": "2026-07-01T07:00:00Z", "reason": "some_new_reason"}
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[-2] == "Race excluded 2026-07-01: some_new_reason"
    # The words fill in a date and a minimum; a served reason is never read as a template.
    body["determinants"][0]["excluded"][0]["reason"] = "new_{date}_{x}"
    _serve(install_mock_client, body)
    assert _lines(_run())[-2] == "Race excluded 2026-07-01: new_{date}_{x}"


FORGED = "\x1b]0;x\x07\nThreshold pace 3:00/km"


def _no_forgery(text: str) -> None:
    """No escape, bell or carriage return reaches the terminal, and the forged text starts no line."""
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert not any(line.startswith("Threshold pace 3:00/km") for line in text.splitlines())


def test_a_served_workout_name_cannot_forge_a_line_or_reach_the_terminal(install_mock_client) -> None:
    """The workout name is the athlete's Garmin text: a name holding an OSC title sequence and a
    line break prints each control character as ``?``, on the workout's own line, as every other
    command prints Garmin text."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"] = [_workout("w", "2026-09-30T06:00:00Z", 1630.0, 266.0, 1.0, 1, admitted_by=["name"], workout_name=FORGED)]
    det["excluded"] = [_excluded_workout("x", "2026-10-01T06:00:00Z", "no_workout_steps", ["name"], FORGED)]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    _no_forgery(result.stdout)
    assert _lines(result)[1:] == [
        "Workout used 2026-09-30 (name ?]0;x??Threshold pace 3:00/km): 0:27:10 active in 1 block, GAP 4:26/km, "
        "gain 1.00, degraded GPS 0.0 %, weight 100 %",
        "Workout excluded 2026-10-01 (name ?]0;x??Threshold pace 3:00/km): no lap of a work step",
        OLDER_API_LINE,
    ], result.stdout


def test_served_text_printed_as_is_cannot_forge_a_line(install_mock_client) -> None:
    """Each served string printed as served passes through the same filter: an unknown exclusion
    reason, an unknown unavailable reason and a non-200 body (cut to 200 characters, as sync cuts
    it). The read-error text is filtered too; today every served value reaches it through a repr
    (a ``KeyError``'s text, ``{kind!r}``), so the forged time zone below reads safely with or
    without that filter and the clause pins the outcome, not the filter."""
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["excluded"] = [{"session_id": "x", "start_time": "2026-07-01T07:00:00Z", "reason": FORGED}]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    _no_forgery(result.stdout)
    assert _lines(result)[-2] == "Race excluded 2026-07-01: ?]0;x??Threshold pace 3:00/km"

    unavailable = _unavailable_body(FORGED)
    _serve(install_mock_client, unavailable)
    result = _run()
    assert result.exit_code == 0, result.output
    _no_forgery(result.stdout)
    assert _lines(result) == ["Threshold pace unavailable: ?]0;x??Threshold pace 3:00/km.", OLDER_API_LINE], result.stdout

    install_mock_client(lambda request: httpx.Response(500, text=FORGED + "y" * 300))
    result = _run()
    assert result.exit_code == 1
    _no_forgery(result.stderr)
    assert "API returned 500: ?]0;x??Threshold pace 3:00/km" in result.stderr
    assert "y" * 171 in result.stderr and "y" * 172 not in result.stderr  # 29 + 171 = 200

    _serve(install_mock_client, {**copy.deepcopy(ESTIMATE_BODY), "timezone": FORGED})
    result = _run()
    assert result.exit_code == 1
    _no_forgery(result.stderr)
    assert "the API's response could not be understood" in result.stderr


SHORT_LINE = (
    "All races used are under 30 minutes: threshold likely reads fast; a 10K race or time trial would correct it."
)


def test_short_races_warning_line(install_mock_client) -> None:
    body = copy.deepcopy(ESTIMATE_BODY)
    body["determinants"][0]["short_races_only"] = True
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert SHORT_LINE in _lines(result)
    _serve(install_mock_client, ESTIMATE_BODY)
    assert SHORT_LINE not in _lines(_run())


UNAVAILABLE_LINE = (
    "Threshold pace unavailable: no tagged race of 20 to 60 minutes and no threshold workout with 9:50 of counted "
    "work reps. Mark a race or time trial as Race in Garmin Connect, or a threshold workout as Training or list "
    "its workout name in the API's threshold_workout_names and restart the API, then run runcoach sync tags "
    "--since <its date> (and, for a workout, runcoach sync laps --since <its date>)."
)


def test_unavailable_message_names_training_workouts(install_mock_client) -> None:
    """The unavailable message names workouts tagged Training in Connect and the workout name list
    beside races, the API restart a changed list needs (the API reads its config once, at
    startup), the sync laps step a workout needs, and the served minimum counted time; a workout
    excluded follows it."""
    body = _unavailable_body()
    body["determinants"][0]["excluded"] = [_excluded_workout("w", "2026-09-30T06:00:00Z", "no_workout_steps")]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [UNAVAILABLE_LINE, "Workout excluded 2026-09-30 (tag): no lap of a work step", OLDER_API_LINE], result.stdout
    assert "Training" in UNAVAILABLE_LINE and "threshold_workout_names" in UNAVAILABLE_LINE
    assert "restart the API" in _lines(result)[0]

    body["determinants"][0]["parameters"] = {**PARAMETERS, "workout_min_active_s": 900}
    _serve(install_mock_client, body)
    assert _lines(_run())[0] == UNAVAILABLE_LINE.replace("with 9:50 of", "with 15:00 of")


def test_unavailable_message_exits_0(install_mock_client) -> None:
    body = _unavailable_body()
    body["determinants"][0]["excluded"] = [
        {"session_id": "m", "start_time": "2026-04-26T08:00:00Z", "reason": "outside_threshold_band"}
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [UNAVAILABLE_LINE, "Race excluded 2026-04-26: not 20 to 60 minutes long", OLDER_API_LINE], result.stdout


def test_estimate_not_finite_prints_words_and_the_races_used(install_mock_client) -> None:
    """The API serves ``estimate_not_finite`` with the estimate null but the races still listed, so the
    athlete can find the race whose GAP pace is out of range."""
    body = _unavailable_body("estimate_not_finite")
    body["determinants"][0]["efforts"] = [_effort("s-a", "2026-08-09T07:00:00Z", 2500.0, 1e308, 1.0)]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert lines[0] == (
        "Threshold pace unavailable: a race's or workout's grade-adjusted pace is out of range, so the estimate "
        "could not be computed. Check the races and workouts used below."
    )
    assert lines[1].startswith("Race used 2026-08-09: 0:41:40, GAP ")
    assert "estimate_not_finite" not in result.stdout


def test_dates_print_in_the_response_timezone(install_mock_client) -> None:
    """22:30 UTC on 20 September is 21 September in Auckland and still 20 September in New York."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    det["efforts"][1]["start_time"] = "2026-09-20T22:30:00Z"
    det["excluded"] = [{"session_id": "x", "start_time": "2026-07-31T23:30:00Z", "reason": "smart_recording"}]
    body["timezone"] = "Pacific/Auckland"
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert lines[0].endswith("the last on 2026-09-21")
    assert lines[2].startswith("Race used 2026-09-21:")
    assert lines[3] == "Race excluded 2026-08-01: recorded in smart or irregular recording mode"

    body["timezone"] = "America/New_York"
    _serve(install_mock_client, body)
    lines = _lines(_run())
    assert lines[0].endswith("the last on 2026-09-20")
    assert lines[3] == "Race excluded 2026-07-31: recorded in smart or irregular recording mode"


def test_as_of_passes_through(install_mock_client) -> None:
    seen: list[httpx.Request] = []
    _serve(install_mock_client, ESTIMATE_BODY, seen=seen)
    assert _run("--as-of", "2026-09-30").exit_code == 0
    assert _run("--as-of", "2026-09-30T12:00:00+02:00").exit_code == 0
    assert _run().exit_code == 0
    assert [r.url.params.get("as_of") for r in seen] == ["2026-09-30", "2026-09-30T12:00:00+02:00", None]
    assert "as_of" not in seen[2].url.params


def test_a_refused_as_of_exits_1_with_the_api_text(install_mock_client) -> None:
    detail = {"detail": [{"loc": ["query", "as_of"], "msg": "Value error, after now"}]}
    _serve(install_mock_client, detail, status=422)
    result = _run("--as-of", "9999-12-31")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "API returned 422" in result.stderr
    assert "after now" in result.stderr


def test_unreachable_api_exits_1(install_mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    install_mock_client(handler)
    result = _run()
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "could not reach API at http://example.test" in result.stderr

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    install_mock_client(slow)
    assert _run().exit_code == 1


@pytest.mark.parametrize(
    "mangle",
    [
        lambda b: "not an object",
        lambda b: {**b, "determinants": []},
        lambda b: {**b, "timezone": "Not/AZone"},
        lambda b: {**b, "determinants": [{**b["determinants"][0], "efforts": [{"session_id": "s"}]}]},
        lambda b: {**b, "determinants": [{**b["determinants"][0], "estimate": {"value": "fast"}}]},
        lambda b: {**b, "determinants": [{**b["determinants"][0], "efforts": ["x"]}]},
        lambda b: {**b, "determinants": [{**b["determinants"][0], "efforts": [None]}]},
        lambda b: {**b, "determinants": [{**b["determinants"][0], "excluded": ["x"]}]},
    ],
    ids=[
        "not-object",
        "no-determinant",
        "bad-zone",
        "effort-missing-fields",
        "bad-estimate",
        "effort-a-string",
        "effort-null",
        "exclusion-a-string",
    ],
)
def test_an_unparseable_body_exits_1(install_mock_client, mangle) -> None:
    """Each malformed body exits 1 with the read error and no traceback: an entry of ``efforts`` or
    ``excluded`` that is not an object is malformed, not an ``AttributeError``."""
    _serve(install_mock_client, mangle(copy.deepcopy(ESTIMATE_BODY)))
    result = _run()
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 1
    assert "the API's response could not be understood" in result.stderr


def test_a_non_json_body_exits_1(install_mock_client) -> None:
    install_mock_client(lambda request: httpx.Response(200, content=b"<html>"))
    result = _run()
    assert result.exit_code == 1
    assert "the API's response could not be understood" in result.stderr


def test_help_texts_tell_state_from_status() -> None:
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0
    state_help = runner.invoke(app, ["state", "--help"]).stdout
    status_help = runner.invoke(app, ["status", "--help"]).stdout
    flat_state = " ".join(state_help.split())
    flat_status = " ".join(status_help.split())
    assert "threshold pace" in flat_state
    assert "runcoach status" in flat_state
    assert "--as-of" in flat_state
    assert "workouts" in flat_state
    assert "weight" in flat_state
    assert "race-to-race scatter, not a biased input" in flat_state
    assert "running and its version" in flat_status
    assert "runcoach state" in flat_status
    assert "threshold" not in flat_status
    flat_top = " ".join(top.stdout.split())
    assert "state" in flat_top and "status" in flat_top


def test_state_help_states_the_admission_and_rep_rule() -> None:
    """The help states the rule in words: the Training tag or a listed workout name admits a run;
    its work reps of at least 4:50, within 10 % of the fastest, count, and they must sum to 9:50."""
    flat = " ".join(runner.invoke(app, ["state", "--help"]).stdout.split())
    assert "Training" in flat
    assert "threshold_workout_names" in flat
    assert "4:50" in flat
    assert "10 %" in flat
    assert "9:50" in flat
    assert "active laps" not in flat and "active steps" not in flat
    # A recovery written as an active step with a target is a work step and can count: the help
    # names recovery steps, never "recoveries".
    assert "recovery steps" in flat and "recoveries" not in flat


def test_the_request_goes_to_get_state(install_mock_client) -> None:
    seen: list[httpx.Request] = []
    _serve(install_mock_client, ESTIMATE_BODY, seen=seen)
    _run()
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/state")]
