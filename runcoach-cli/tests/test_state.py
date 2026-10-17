"""``runcoach state``: threshold pace from ``GET /state``, the races used and the races excluded.

Every test runs the real command against ``install_mock_client``'s fake API, with bodies shaped as
the API's ``StateModel`` serves them (``as_of`` and start times in UTC with a ``Z``). The lines
compared are F024 reference section 6's, with F026 reference section 5's workouts and IDEA-167's
weights.
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

PARAMETERS = {
    "typical_error": 0.025,
    "doubling_days": 42,
    "coverage": 0.8,
    "high_max_error": 0.025,
    "medium_max_error": 0.05,
    "short_race_s": 1800,
    "workout_min_active_s": 600,
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
    }


def _workout(session_id: str, start: str, active_s: float, gap: float, gain: float, blocks: int, degraded=0.0):
    """A threshold workout as ``ThresholdEffort`` serves it: ``duration_s`` its active time."""
    return {
        **_effort(session_id, start, active_s, gap, gain, degraded),
        "kind": "threshold_workout",
        "active_blocks": blocks,
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
    ]


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
        "Workout used 2026-09-30: 0:27:10 active in 1 block, GAP 4:26/km, gain 0.98, degraded GPS 0.0 %, weight 98 %"
    )
    det["efforts"][1].update(active_blocks=3, gps_degraded_fraction=None, duration_s=1800.0)
    _serve(install_mock_client, body)
    assert _lines(_run())[2] == (
        "Workout used 2026-09-30: 0:30:00 active in 3 blocks, GAP 4:26/km, gain 0.98, degraded GPS -, weight 98 %"
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
    "no_workout_steps": "no lap of an active workout step",
    "active_time_below_minimum": "under 10 minutes of active laps",
    "laps_unreadable": "its stored laps could not be read",
    "smart_recording": "recorded in smart or irregular recording mode",
    "gap_pace_unavailable": "no grade-adjusted pace",
}


def test_workout_exclusion_reasons_in_words(install_mock_client) -> None:
    """Each workout reason in words, every one on 2026-07-01 so the sync laps hint names that day; a
    race excluded beside them keeps its ``Race excluded`` prefix."""
    body = copy.deepcopy(ESTIMATE_BODY)
    excluded = [
        {"session_id": f"w-{i}", "start_time": "2026-07-01T07:00:00Z", "kind": "threshold_workout", "reason": reason}
        for i, reason in enumerate(WORKOUT_REASON_WORDS)
    ]
    excluded.append(
        {"session_id": "r", "start_time": "2026-07-02T07:00:00Z", "kind": "race", "reason": "outside_threshold_band"}
    )
    body["determinants"][0]["excluded"] = excluded
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result)[3:] == [
        f"Workout excluded 2026-07-01: {words}" for words in WORKOUT_REASON_WORDS.values()
    ] + ["Race excluded 2026-07-02: not 20 to 60 minutes long"]
    assert not any(reason in result.stdout for reason in WORKOUT_REASON_WORDS)

    # The hint's date is the workout's own, in the response's timezone.
    body["timezone"] = "Pacific/Auckland"
    body["determinants"][0]["excluded"] = [
        {"session_id": "w", "start_time": "2026-07-01T23:30:00Z", "kind": "threshold_workout", "reason": "laps_not_read"}
    ]
    _serve(install_mock_client, body)
    assert _lines(_run())[-1] == (
        "Workout excluded 2026-07-02: its laps have not been read: run runcoach sync laps --since 2026-07-02"
    )

    # The minimum is the served parameter's.
    body["determinants"][0]["parameters"] = {**PARAMETERS, "workout_min_active_s": 900}
    body["determinants"][0]["excluded"][0]["reason"] = "active_time_below_minimum"
    _serve(install_mock_client, body)
    assert _lines(_run())[-1] == "Workout excluded 2026-07-02: under 15 minutes of active laps"


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
    assert [line.rsplit(", weight ", 1)[1] for line in _lines(_run())[1:]] == ["25 %", "25 %", "50 %"]


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
    ]
    assert "recorded in smart recording mode" not in "\n".join(lines)


def test_an_older_api_body_without_kind_still_renders(install_mock_client) -> None:
    """A body from an API before F026 has no ``kind``, ``active_blocks`` or ``workout_min_active_s``:
    every effort and exclusion reads as a race."""
    body = copy.deepcopy(ESTIMATE_BODY)
    det = body["determinants"][0]
    for effort in det["efforts"]:
        del effort["kind"], effort["active_blocks"]
    det["parameters"] = {k: v for k, v in PARAMETERS.items() if k != "workout_min_active_s"}
    det["excluded"] = [{"session_id": "x", "start_time": "2026-07-01T07:00:00Z", "reason": "outside_threshold_band"}]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [
        "Threshold pace 4:22/km (80 %: 4:15-4:30), high, from 2 races, the last on 2026-09-20",
        "Race used 2026-08-09: 0:41:40, GAP 4:30/km, gain 1.00, degraded GPS 0.0 %, weight 20 %",
        "Race used 2026-09-20: 0:41:40, GAP 4:20/km, gain 0.80, degraded GPS 3.1 %, weight 80 %",
        "Race excluded 2026-07-01: not 20 to 60 minutes long",
    ]

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
        f"Race excluded 2026-07-{i + 1:02d}: {REASON_WORDS[reason]}" for i, reason in enumerate(reasons)
    ]
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
    assert _lines(result)[-1] == "Race excluded 2026-07-01: some_new_reason"
    # The words fill in a date and a minimum; a served reason is never read as a template.
    body["determinants"][0]["excluded"][0]["reason"] = "new_{date}_{x}"
    _serve(install_mock_client, body)
    assert _lines(_run())[-1] == "Race excluded 2026-07-01: new_{date}_{x}"


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
    "Threshold pace unavailable: no tagged race of 20 to 60 minutes and no threshold workout with 10 minutes "
    "of active laps. Mark a race or time trial as Race in Garmin Connect, or a threshold workout as Training, "
    "then run runcoach sync tags --since <its date> (and, for a workout, runcoach sync laps --since <its date>)."
)


def test_unavailable_message_names_training_workouts(install_mock_client) -> None:
    """The unavailable message names workouts tagged Training in Connect beside races, the sync laps
    step a workout needs, and the served minimum active time; a workout excluded follows it."""
    body = _unavailable_body()
    body["determinants"][0]["excluded"] = [
        {"session_id": "w", "start_time": "2026-09-30T06:00:00Z", "kind": "threshold_workout",
         "reason": "no_workout_steps"}
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [UNAVAILABLE_LINE, "Workout excluded 2026-09-30: no lap of an active workout step"]
    assert "Training" in UNAVAILABLE_LINE and "workout" in UNAVAILABLE_LINE

    body["determinants"][0]["parameters"] = {**PARAMETERS, "workout_min_active_s": 900}
    _serve(install_mock_client, body)
    assert _lines(_run())[0] == UNAVAILABLE_LINE.replace("with 10 minutes", "with 15 minutes")


def test_unavailable_message_exits_0(install_mock_client) -> None:
    body = _unavailable_body()
    body["determinants"][0]["excluded"] = [
        {"session_id": "m", "start_time": "2026-04-26T08:00:00Z", "reason": "outside_threshold_band"}
    ]
    _serve(install_mock_client, body)
    result = _run()
    assert result.exit_code == 0, result.output
    assert _lines(result) == [UNAVAILABLE_LINE, "Race excluded 2026-04-26: not 20 to 60 minutes long"]


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
    ],
    ids=["not-object", "no-determinant", "bad-zone", "effort-missing-fields", "bad-estimate"],
)
def test_an_unparseable_body_exits_1(install_mock_client, mangle) -> None:
    _serve(install_mock_client, mangle(copy.deepcopy(ESTIMATE_BODY)))
    result = _run()
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


def test_the_request_goes_to_get_state(install_mock_client) -> None:
    seen: list[httpx.Request] = []
    _serve(install_mock_client, ESTIMATE_BODY, seen=seen)
    _run()
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/state")]
