"""``runcoach state``: the endurance exponent (Riegel b) block after the threshold lines.

Every test runs the real command against ``install_mock_client``'s fake API, with bodies shaped as
the API's ``RiegelDeterminant`` serves them (``as_of`` and start times in UTC with a ``Z``). The
threshold entry is the unavailable one, so its block is exactly one line and the Riegel block starts
at line 1. The lines compared are F029 reference section 6's, written from its text; the figures
are its section 4 worked examples (sets A-D, the A, B, C set with D excluded, the A, H, F set below
one) and the real-store interval of its section 4's last paragraph.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.main import app

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

THRESHOLD_UNAVAILABLE = {
    "name": "functional_threshold_pace",
    "estimate": None,
    "unavailable": "no_qualifying_effort",
    "trend": {"direction": "unknown", "rate": None},
    "error": None,
    "short_races_only": False,
    "efforts": [],
    "excluded": [],
    "parameters": {
        "typical_error": 0.025,
        "doubling_days": 42,
        "coverage": 0.8,
        "high_max_error": 0.025,
        "medium_max_error": 0.05,
        "short_race_s": 1800,
        "workout_min_active_s": 590,
        "workout_min_rep_s": 290,
        "workout_rep_band": 1.10,
    },
}

PARAMETERS = {
    "window_days": 1095,
    "fade_days": 182,
    "min_races": 3,
    "min_spread": 2.0,
    "fitness_drop": 0.30,
    "fitness_lookback_days": 28,
    "population_b": 1.06,
    "population_error": 0.05,
    "coverage": 0.8,
    "high_max_error": 0.02,
    "medium_max_error": 0.04,
}

OLDER_API_LINE = "Endurance exponent: not served by this API; update it and restart"
TAGGING_LINE = "Mark a race as Race in Garmin Connect and run runcoach sync tags --since <its date>"
POPULATION = "Endurance exponent b 1.06 (population default, 80 %: 0.996 to 1.124), low: "


def _race(
    session_id: str,
    start: str,
    distance_m: float | None,
    d_eq: float | None,
    duration_s: float,
    before: float | None,
    earlier: float | None,
    weight: float = 1.0,
    residual: float | None = None,
) -> dict[str, Any]:
    """A race used, as ``RiegelEffort`` serves it; ``fitness_drop`` from the two figures."""
    drop = None if before is None or earlier is None else 1 - before / earlier
    return {
        "session_id": session_id,
        "start_time": start,
        "duration_s": duration_s,
        "distance_m": distance_m,
        "gap_pace_s_per_km": 1000 * duration_s / d_eq if d_eq else 1e-320,
        "gap_equivalent_m": d_eq,
        "gap_coverage": 1.0,
        "gps_degraded_fraction": 0.0,
        "weight": weight,
        "fitness_before": before,
        "fitness_earlier": earlier,
        "fitness_drop": drop,
        "fitness_provisional": False,
        "fitness_after_gap": False,
        "residual": residual,
    }


def _excluded(session_id: str, start: str, reason: str, before=None, earlier=None) -> dict[str, Any]:
    """A race excluded, as ``RiegelExclusion`` serves it."""
    drop = None if before is None or earlier is None else 1 - before / earlier
    return {
        "session_id": session_id,
        "start_time": start,
        "reason": reason,
        "fitness_before": before,
        "fitness_earlier": earlier,
        "fitness_drop": drop,
    }


# Section 4's races A, B, C, D (no screen), D at weight 0.5, with the residuals of the first set.
A = _race("s-a", "2024-05-05T08:00:00Z", 5020.0, 5000.0, 1200.0, 36.0, 37.0, residual=0.00388)
B = _race("s-b", "2024-09-15T08:00:00Z", 10160.0, 10000.0, 2520.0, 40.4, 41.0, residual=-0.00169)
C = _race("s-c", "2025-03-30T08:00:00Z", 21097.5, 21100.0, 5600.0, 42.0, 40.0, residual=-0.00838)
D = _race("s-d", "2025-10-05T08:00:00Z", 42195.0, 42200.0, 12000.0, 29.0, None, weight=0.5, residual=0.00625)


def _riegel(
    efforts: list[dict[str, Any]],
    value: float = 1.078410,
    low: float = 1.068991,
    high: float = 1.087830,
    confidence: str = "high",
    excluded: list[dict[str, Any]] | None = None,
    population_default: bool = False,
    below_one: bool = False,
) -> dict[str, Any]:
    return {
        "name": "riegel_exponent",
        "estimate": {
            "value": value,
            "unit": "1",
            "interval": {"low": low, "high": high},
            "confidence": confidence,
            "provisional": population_default,
        },
        "unavailable": None,
        "trend": {"direction": "unknown", "rate": None},
        "error": 0.05 if population_default else 0.004995,
        "t_multiplier": 1.2815515655446004 if population_default else 1.885618,
        "population_default": population_default,
        "below_one": below_one,
        "efforts": efforts,
        "excluded": excluded or [],
        "parameters": dict(PARAMETERS),
    }


def _population(efforts: list[dict[str, Any]], excluded: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [{**e, "residual": None} for e in efforts]
    return _riegel(rows, 1.06, 0.995922, 1.124078, "low", excluded, population_default=True)


def _body(riegel: dict[str, Any] | None, timezone: str = "UTC", as_of: str = "2026-10-15T08:00:00Z") -> dict[str, Any]:
    determinants = [copy.deepcopy(THRESHOLD_UNAVAILABLE)]
    if riegel is not None:
        determinants.append(copy.deepcopy(riegel))
    return {
        "as_of": as_of,
        "timezone": timezone,
        "determinants": determinants,
        "readiness": None,
        "gap_to_goal": None,
    }


def _serve(install_mock_client, body: Any, seen: list[httpx.Request] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.url.path == "/state"
        return httpx.Response(200, json=body)

    install_mock_client(handler)


def _riegel_lines(install_mock_client, body: Any, *args: str) -> list[str]:
    """The lines after the one threshold line; the command must exit 0."""
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state", *args])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Threshold pace unavailable: "), result.stdout
    return lines[1:]


def _refused(install_mock_client, body: Any) -> None:
    """The command's error path: exit 1, nothing on stdout, no traceback."""
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "the API's response could not be understood" in result.stderr
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


def test_estimate_line(install_mock_client) -> None:
    """The real-store set of section 4: eight races, b 1.059757, 80 % 1.035116 to 1.084398, three
    decimals; the distances are the GAP-equivalent ones, shortest to longest, not the recorded
    ones (each 2 % longer here, so 5.1 to 43.4 km would be the wrong range)."""
    d_eqs = [5000.0, 5010.0, 10000.0, 10050.0, 21100.0, 21150.0, 42200.0, 42500.0]
    efforts = [
        _race(f"s-{i}", f"2024-0{i + 1}-01T08:00:00Z", d * 1.02, d, d * 0.25, 40.0, 41.0, residual=0.001)
        for i, d in enumerate(d_eqs)
    ]
    lines = _riegel_lines(install_mock_client, _body(_riegel(efforts, 1.059757, 1.035116, 1.084398)))
    assert lines[0] == (
        "Endurance exponent b 1.060 (80 %: 1.035 to 1.084), high, from 8 races in the last 1095 days, 5.0 to 42.5 km"
    ), "\n".join(lines)
    assert len(lines) == 9, "\n".join(lines)
    assert TAGGING_LINE not in lines


def test_population_too_few_races_line(install_mock_client) -> None:
    lines = _riegel_lines(install_mock_client, _body(_population([A, B])))
    assert lines[:2] == [POPULATION + "2 races in the last 1095 days; 3 are needed", TAGGING_LINE], "\n".join(lines)

    none = _riegel_lines(install_mock_client, _body(_population([])))
    assert none == [POPULATION + "0 races in the last 1095 days; 3 are needed", TAGGING_LINE], "\n".join(none)

    one = _riegel_lines(install_mock_client, _body(_population([A])))
    assert one[0] == POPULATION + "1 race in the last 1095 days; 3 are needed", "\n".join(one)


def test_population_one_distance_line(install_mock_client) -> None:
    """Four 5Ks clear the race count, not the spread."""
    fives = [_race(f"s-{i}", f"2025-0{i + 1}-01T08:00:00Z", 5000.0, 5000.0 + 100 * i, 1200.0, 40.0, 41.0) for i in range(4)]
    lines = _riegel_lines(install_mock_client, _body(_population(fives)))
    assert lines[:2] == [
        POPULATION + "4 races at one distance; the longest must be at least twice the shortest",
        TAGGING_LINE,
    ], "\n".join(lines)


def test_population_line_names_the_screened_count(install_mock_client) -> None:
    """Only ``fitness_dropped`` counts toward the screened number; other reasons do not."""
    one = [
        _excluded("x-1", "2025-11-02T08:00:00Z", "fitness_dropped", 29.0, 48.0),
        _excluded("x-2", "2025-12-02T08:00:00Z", "smart_recording"),
    ]
    lines = _riegel_lines(install_mock_client, _body(_population([A, B], one)))
    assert lines[0] == POPULATION + "2 races in the last 1095 days; 3 are needed, 1 excluded by the fitness screen"

    two = [*one, _excluded("x-3", "2026-01-02T08:00:00Z", "fitness_dropped", 20.0, 40.0)]
    fives = [_race(f"s-{i}", f"2025-0{i + 1}-01T08:00:00Z", 5000.0, 5000.0, 1200.0, 40.0, 41.0) for i in range(3)]
    lines = _riegel_lines(install_mock_client, _body(_population(fives, two)))
    assert lines[0] == (
        POPULATION + "3 races at one distance; the longest must be at least twice the shortest, "
        "2 excluded by the fitness screen"
    ), "\n".join(lines)

    none = _riegel_lines(install_mock_client, _body(_population([A, B], one[1:])))
    assert none[0] == POPULATION + "2 races in the last 1095 days; 3 are needed"


def test_race_used_lines(install_mock_client) -> None:
    """Distance, GAP-equivalent distance, time, fitness before (whole) and its change in words (whole
    percent, ``fell`` or ``rose``), weight to two decimals and the residual to one decimal with its sign; below
    the bar no residual clause; a null figure prints ``-``."""
    lines = _riegel_lines(install_mock_client, _body(_riegel([A, B, C, D])))
    assert lines == [
        "Endurance exponent b 1.078 (80 %: 1.069 to 1.088), high, from 4 races in the last 1095 days, 5.0 to 42.2 km",
        "Race in b 2024-05-05: 5.0 km (5.0 km adjusted), 0:20:00, fitness 36, fell 3 % over 4 weeks, weight 1.00, "
        "+0.4 % against the line",
        "Race in b 2024-09-15: 10.2 km (10.0 km adjusted), 0:42:00, fitness 40, fell 1 % over 4 weeks, weight 1.00, "
        "-0.2 % against the line",
        "Race in b 2025-03-30: 21.1 km (21.1 km adjusted), 1:33:20, fitness 42, rose 5 % over 4 weeks, weight 1.00, "
        "-0.8 % against the line",
        "Race in b 2025-10-05: 42.2 km (42.2 km adjusted), 3:20:00, fitness 29 (-), weight 0.50, "
        "+0.6 % against the line",
    ]

    no_fitness = _race("s-e", "2024-01-07T08:00:00Z", None, 5000.0, 1200.0, None, None)
    lines = _riegel_lines(install_mock_client, _body(_population([no_fitness, B])))
    assert lines[2:] == [
        "Race in b 2024-01-07: - (5.0 km adjusted), 0:20:00, fitness -, weight 1.00",
        "Race in b 2024-09-15: 10.2 km (10.0 km adjusted), 0:42:00, fitness 40, fell 1 % over 4 weeks, weight 1.00",
    ], "\n".join(lines)

    # A residual that rounds to zero prints without a sign rather than as -0.0.
    tiny = _riegel([{**A, "residual": -0.00012}, {**B, "residual": 0.00024}, {**C, "residual": -0.00011}])
    lines = _riegel_lines(install_mock_client, _body(tiny))
    assert [line.rsplit(", ", 1)[1] for line in lines[1:]] == [
        "0.0 % against the line",
        "0.0 % against the line",
        "0.0 % against the line",
    ], "\n".join(lines)


def test_race_excluded_lines_in_words(install_mock_client) -> None:
    """Every reason of section 2 in words; ``fitness_dropped`` with its figures, ``outside_window``
    naming the window and the as-of date; a reason the CLI does not know is printed as served
    through ``_printable``."""
    reasons = [
        ("admitted_as_workout", "its workout name is listed in threshold_workout_names, so it is read as a workout"),
        ("outside_window", "more than 1095 days before 2026-10-15"),
        ("summary_unreadable", "its stored summary could not be read"),
        ("duration_unavailable", "no timer or summary duration"),
        ("quality_flags_unreadable", "its stored quality flags could not be read"),
        ("distance_regressed", "its distance went backwards in the recording"),
        ("smart_recording", "recorded in smart or irregular recording mode"),
        ("fitness_dropped", "fitness fell 40 % over 4 weeks (48 to 29)"),
        ("features_unavailable", "its pace features could not be computed"),
        ("grade_clamped", "a grade was outside the adjustment's range"),
        ("gap_pace_unavailable", "no grade-adjusted pace"),
        ("some_new_reason\x1b]0;x\x07", "some_new_reason?]0;x?"),
    ]
    excluded = [
        _excluded(f"x-{i}", f"2025-0{i // 10 + 1}-{i % 10 + 10}T08:00:00Z", reason, *((29.0, 48.0) if reason == "fitness_dropped" else ()))
        for i, (reason, _words) in enumerate(reasons)
    ]
    lines = _riegel_lines(install_mock_client, _body(_population([], excluded)))
    assert lines[2:] == [
        f"Race left out of b 2025-0{i // 10 + 1}-{i % 10 + 10}: {words}" for i, (_reason, words) in enumerate(reasons)
    ], "\n".join(lines)


def test_b_block_race_lines_name_b(install_mock_client) -> None:
    """A body with a race used and a race excluded in each block: the threshold block prints
    ``Race used`` / ``Race excluded`` and the b block ``Race in b`` / ``Race left out of b``, so a
    5K feeding both blocks never prints twice under one label with different figures."""
    body = _body(_population([A], [_excluded("x-1", "2025-11-02T08:00:00Z", "smart_recording")]))
    body["determinants"][0].update(
        estimate={"value": 264.0, "unit": "s/km", "interval": {"low": 258.0, "high": 270.0}, "confidence": "low"},
        unavailable=None,
        efforts=[
            {
                "kind": "race",
                "session_id": "s-a",
                "start_time": "2024-05-05T08:00:00Z",
                "duration_s": 1200.0,
                "gap_pace_s_per_km": 240.0,
                "gain": 1.0,
                "gps_degraded_fraction": 0.0,
            }
        ],
        excluded=[{"kind": "race", "session_id": "x-1", "start_time": "2025-11-02T08:00:00Z", "reason": "smart_recording"}],
    )
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    b_start = next(i for i, line in enumerate(lines) if line.startswith("Endurance exponent"))
    threshold, b_block = lines[:b_start], lines[b_start:]
    assert threshold[1:] == [
        "Race used 2024-05-05: 0:20:00, GAP 4:00/km, gain 1.00, degraded GPS 0.0 %, weight 100 %",
        "Race excluded 2025-11-02: recorded in smart or irregular recording mode",
    ], result.stdout
    assert b_block[2:] == [
        "Race in b 2024-05-05: 5.0 km (5.0 km adjusted), 0:20:00, fitness 36, fell 3 % over 4 weeks, weight 1.00",
        "Race left out of b 2025-11-02: recorded in smart or irregular recording mode",
    ], result.stdout
    assert not any(line.startswith(("Race in b", "Race left out of b")) for line in threshold), result.stdout
    assert not any(line.startswith(("Race used", "Race excluded")) for line in b_block), result.stdout


@pytest.mark.parametrize(
    ("drop", "words"),
    [
        (0.42, "fell 42 % over 4 weeks"),
        (-0.05, "rose 5 % over 4 weeks"),
        (0.0, "no change over 4 weeks"),
        # Half up, whole numbers, as before: 2.5 rounds to 3 and -2.5 to -2.
        (0.025, "fell 3 % over 4 weeks"),
        (-0.025, "rose 2 % over 4 weeks"),
        # A figure that rounds to 0 is no change, never ``rose 0 %`` or ``fell -0 %``.
        (0.004, "no change over 4 weeks"),
        (-0.004, "no change over 4 weeks"),
        (-0.005, "no change over 4 weeks"),
        (-0.0, "no change over 4 weeks"),
    ],
)
def test_a_fitness_change_reads_rose_or_fell(install_mock_client, drop: float, words: str) -> None:
    """The served ``fitness_drop`` (a fall positive) prints in words on a race in b and on a
    ``fitness_dropped`` line; a missing drop keeps ``(-)``."""
    used = {**A, "fitness_before": 42.0, "fitness_drop": drop, "residual": None}
    dropped = {**_excluded("x-1", "2025-11-02T08:00:00Z", "fitness_dropped", 29.0, 50.0), "fitness_drop": drop}
    lines = _riegel_lines(install_mock_client, _body(_population([used], [dropped])))
    assert lines[2:] == [
        f"Race in b 2024-05-05: 5.0 km (5.0 km adjusted), 0:20:00, fitness 42, {words}, weight 1.00",
        f"Race left out of b 2025-11-02: fitness {words} (50 to 29)",
    ], "\n".join(lines)
    assert not any(" rose 0 %" in line or "fell -" in line or "rose -" in line for line in lines), "\n".join(lines)

    no_drop = _riegel_lines(install_mock_client, _body(_population([{**used, "fitness_drop": None}])))
    assert no_drop[2] == "Race in b 2024-05-05: 5.0 km (5.0 km adjusted), 0:20:00, fitness 42 (-), weight 1.00"


def test_below_one_line(install_mock_client) -> None:
    """Section 4's A, H, F set: b 0.947937, 80 % 0.911284 to 0.984589, high; the warning follows the
    estimate line, before the races."""
    h = _race("s-h", "2025-01-12T08:00:00Z", 10000.0, 10000.0, 2350.0, 40.0, 41.0, residual=0.00995)
    f = _race("s-f", "2025-04-13T08:00:00Z", 21100.0, 21100.0, 4700.0, 42.0, 43.0, residual=-0.00475)
    a = {**A, "residual": -0.00512}
    lines = _riegel_lines(install_mock_client, _body(_riegel([a, h, f], 0.947937, 0.911284, 0.984589, below_one=True)))
    assert lines[:2] == [
        "Endurance exponent b 0.948 (80 %: 0.911 to 0.985), high, from 3 races in the last 1095 days, 5.0 to 21.1 km",
        "b reads below 1: a race used was probably not all-out, or a trail race; check the races listed",
    ], "\n".join(lines)
    assert lines[2].startswith("Race in b 2024-05-05: ")
    assert len(lines) == 5

    above = _riegel_lines(install_mock_client, _body(_riegel([A, B, C, D])))
    assert not any(line.startswith("b reads below 1") for line in above)


def test_not_finite_line(install_mock_client) -> None:
    """``estimate``, ``error`` and ``t_multiplier`` null; the races still listed, the one whose
    GAP-equivalent distance is not finite showing ``-``."""
    riegel = _riegel([A, _race("s-z", "2025-02-02T08:00:00Z", 10000.0, None, 2520.0, 40.0, 41.0), C])
    riegel.update(estimate=None, unavailable="estimate_not_finite", error=None, t_multiplier=None)
    for effort in riegel["efforts"]:
        effort["residual"] = None
    lines = _riegel_lines(install_mock_client, _body(riegel))
    assert lines == [
        "Endurance exponent unavailable: a race's figures are out of range; check the races listed",
        "Race in b 2024-05-05: 5.0 km (5.0 km adjusted), 0:20:00, fitness 36, fell 3 % over 4 weeks, weight 1.00",
        "Race in b 2025-02-02: 10.0 km (- adjusted), 0:42:00, fitness 40, fell 2 % over 4 weeks, weight 1.00",
        "Race in b 2025-03-30: 21.1 km (21.1 km adjusted), 1:33:20, fitness 42, rose 5 % over 4 weeks, weight 1.00",
    ], "\n".join(lines)

    # An unavailable value the CLI does not know is printed as served, through ``_printable``.
    riegel["unavailable"] = "new\x1breason"
    lines = _riegel_lines(install_mock_client, _body(riegel))
    assert lines[0] == "Endurance exponent unavailable: new?reason."


def test_older_api_line_exits_zero(install_mock_client) -> None:
    lines = _riegel_lines(install_mock_client, _body(None))
    assert lines == [OLDER_API_LINE]

    # An entry that is not an object is not a riegel_exponent entry.
    body = _body(None)
    body["determinants"].append("riegel_exponent")
    assert _riegel_lines(install_mock_client, body) == [OLDER_API_LINE]


def test_dates_print_in_the_response_timezone(install_mock_client) -> None:
    """22:30 UTC on 20 September is 21 September in Auckland and still 20 September in New York; the
    as-of date ``outside_window`` names moves the same way."""
    race = _race("s-t", "2025-09-20T22:30:00Z", 5000.0, 5000.0, 1200.0, 36.0, 37.0)
    excluded = [
        _excluded("x-1", "2025-07-31T23:30:00Z", "smart_recording"),
        _excluded("x-2", "2022-07-31T23:30:00Z", "outside_window"),
    ]
    body = _body(_population([race], excluded), "Pacific/Auckland", as_of="2026-10-15T20:00:00Z")
    lines = _riegel_lines(install_mock_client, body)
    assert lines[2:] == [
        "Race in b 2025-09-21: 5.0 km (5.0 km adjusted), 0:20:00, fitness 36, fell 3 % over 4 weeks, weight 1.00",
        "Race left out of b 2025-08-01: recorded in smart or irregular recording mode",
        "Race left out of b 2022-08-01: more than 1095 days before 2026-10-16",
    ], "\n".join(lines)

    body["timezone"] = "America/New_York"
    lines = _riegel_lines(install_mock_client, body)
    assert lines[2:] == [
        "Race in b 2025-09-20: 5.0 km (5.0 km adjusted), 0:20:00, fitness 36, fell 3 % over 4 weeks, weight 1.00",
        "Race left out of b 2025-07-31: recorded in smart or irregular recording mode",
        "Race left out of b 2022-07-31: more than 1095 days before 2026-10-15",
    ], "\n".join(lines)


def test_as_of_passes_through_with_the_riegel_block(install_mock_client) -> None:
    seen: list[httpx.Request] = []
    _serve(install_mock_client, _body(_population([A, B])), seen=seen)
    assert runner.invoke(app, ["state", "--as-of", "2026-09-30"]).exit_code == 0
    assert [r.url.params.get("as_of") for r in seen] == ["2026-09-30"]


@pytest.mark.parametrize(
    "breaks",
    [
        pytest.param(lambda r: r.update(efforts={"s-a": A}), id="efforts_not_a_list"),
        pytest.param(lambda r: r.update(excluded="none"), id="excluded_not_a_list"),
        pytest.param(lambda r: r["efforts"][0].pop("weight"), id="effort_missing_weight"),
        pytest.param(lambda r: r["efforts"][0].update(weight="1"), id="weight_a_string"),
        pytest.param(lambda r: r["efforts"][0].update(duration_s=None), id="duration_null"),
        pytest.param(lambda r: r["efforts"].append("s-x"), id="effort_not_an_object"),
        pytest.param(lambda r: r.update(estimate=None, unavailable=None), id="estimate_and_unavailable_null"),
        pytest.param(lambda r: r.update(population_default="yes"), id="population_default_not_a_bool"),
        pytest.param(lambda r: r.update(below_one=None), id="below_one_not_a_bool"),
        pytest.param(lambda r: r["estimate"].update(confidence="certain"), id="confidence_unknown"),
        pytest.param(lambda r: r["estimate"]["interval"].update(low="1.069"), id="interval_a_string"),
        pytest.param(lambda r: r.pop("parameters"), id="parameters_missing"),
        pytest.param(lambda r: r["parameters"].update(window_days=None), id="window_days_null"),
        pytest.param(lambda r: r["efforts"][0].update(gap_equivalent_m=None), id="fitted_estimate_d_eq_null"),
        pytest.param(lambda r: r.update(efforts=[]), id="fitted_estimate_no_races"),
        pytest.param(lambda r: r["efforts"][0].update(start_time="yesterday"), id="start_time_unreadable"),
        pytest.param(
            lambda r: r["excluded"].append(_excluded("x", "2025-01-01T08:00:00Z", "fitness_dropped")),
            id="fitness_dropped_without_figures",
        ),
        pytest.param(
            lambda r: r["excluded"].append({**_excluded("x", "2025-01-01T08:00:00Z", "smart_recording"), "reason": 7}),
            id="reason_not_a_string",
        ),
    ],
)
def test_a_malformed_riegel_entry_takes_the_error_path(install_mock_client, breaks) -> None:
    riegel = copy.deepcopy(_riegel([A, B, C, D]))
    breaks(riegel)
    _refused(install_mock_client, _body(riegel))
