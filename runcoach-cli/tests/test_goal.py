"""``runcoach goal set``, ``runcoach goal`` and ``runcoach goal preview`` (F032 AC8, reference
section 7).

Every test runs the real command against ``install_mock_client``'s fake API, with bodies shaped as
the API's ``GoalContract`` and ``PaceTarget`` serve them. The fitted-line body is reference section
8's E2 half with a goal of 5500 s (its goal-time gap example); the closest-race body is section 8's
fallback, one 21,580 m race alone. The lines compared are section 7's, written from its text. The
distance and time parsers each get an adversarial table: zero, negative, a bare number, ``nan``,
``inf``, a number too large for a float, an empty string and a minute of 60, each refused with
exit 2 before any request is made.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import httpx
import pytest
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

HALF_M = 21097.5
NO_GOAL_LINE = "No goal set. Set one with runcoach goal set."

ASSUMPTIONS = [
    "flat_course",
    "still_air",
    "cool_conditions",
    "no_economy_adjustment",
    "no_durability_adjustment",
    "projected_for_today_not_race_day",
    "watch_timer_time",
]
ASSUMES_LINE = (
    "Assumes: a flat course, still air, cool conditions, no economy adjustment, no durability adjustment, "
    "projected for today, not race day, watch timer time (an auto-pause shortens it; chip time may differ)"
)

# The values the API serves (``metrics.pace_target.PARAMETERS``, the F032 reference section 1).
PARAMETERS = {
    "high_max_uncertainty": 0.02,
    "medium_max_uncertainty": 0.05,
    "fitness_gap": 0.30,
    "max_extrapolation": 1.5,
    "calibrated_min_m": 5000,
    "calibrated_max_m": 42195,
    "population_b": 1.06,
    "tie_slack": 1e-9,
}


def _value(value: float, unit: str, low: float | None, high: float | None, label: str, provisional: bool) -> dict:
    return {
        "value": value,
        "unit": unit,
        "interval": None if low is None else {"low": low, "high": high},
        "confidence": label,
        "provisional": provisional,
    }


def _race_used(session_id: str, name: str | None, start: str, d_eq: float, t: float, residual: float | None, before: float | None) -> dict:
    return {
        "session_id": session_id,
        "name": name,
        "start_time": start,
        "gap_equivalent_m": d_eq,
        "duration_s": t,
        "weight": 1.0,
        "residual": residual,
        "fitness_before": before,
    }


# Section 8's E2 (four tight races) projected over the half, goal 5500 s: the gap example.
E2_RACES = [
    _race_used("s-5k", "Run", "2026-09-06T07:00:00Z", 5000.0, 1200.0, 0.0011, 40.0),
    _race_used("s-10k", "Run", "2026-09-13T07:00:00Z", 10000.0, 2502.0, -0.0008, 44.0),
    _race_used("s-half", "Run", "2026-09-20T07:00:00Z", HALF_M, 5530.0, -0.0023, 46.0),
    _race_used("s-mar", "Run", "2026-09-27T07:00:00Z", 42195.0, 11610.0, 0.0019, 48.0),
]
E2_LEAVE_ONE_OUT = [
    {"session_id": "s-5k", "route": "fitted_line", "projected_time_s": 5549.5, "shift": 0.001208, "sets_the_line": False},
    {"session_id": "s-10k", "route": "fitted_line", "projected_time_s": 5540.1, "shift": -0.000488, "sets_the_line": False},
    {"session_id": "s-half", "route": "fitted_line", "projected_time_s": 5557.2, "shift": 0.002597, "sets_the_line": False},
    {"session_id": "s-mar", "route": "fitted_line", "projected_time_s": 5528.6, "shift": -0.002563, "sets_the_line": False},
]

FITTED: dict[str, Any] = {
    "as_of": "2026-10-14T12:00:00Z",
    "timezone": "UTC",
    "race": {"date": "2027-04-11", "distance_m": HALF_M, "name": "Vancouver Half"},
    "target_time_s": 5500.0,
    "unavailable": None,
    "route": "fitted_line",
    "projected_time_s": _value(5542.806803, "s", 5512.797559, 5572.979404, "high", False),
    "avg_pace_s_per_km": _value(262.723394, "s/km", 261.301044, 264.153528, "high", False),
    "halfwidth": 0.005444,
    "confidence": {"label": "high", "basis": "range", "uncertainty": 0.005444, "reasons": []},
    "goal_time_gap": {"gap_s": -42.806803, "gap_fraction": -0.007723, "within_range": False},
    "provenance": {
        "b": 1.063891,
        "error": 0.001578,
        "label": "high",
        "population_default": False,
        "n_eff": 4.0,
        "races": E2_RACES,
        "newest_race": {"session_id": "s-mar", "name": "Run", "start_time": "2026-09-27T07:00:00Z"},
        "fitness_at_races": 44.5,
        "fitness_now": 42.3,
    },
    "leave_one_out": {"races": E2_LEAVE_ONE_OUT, "largest": E2_LEAVE_ONE_OUT[2]},
    "effort_note": None,
    "assumptions": list(ASSUMPTIONS),
    "parameters": dict(PARAMETERS),
    "gap_to_goal": None,
}

FITTED_LINES = [
    "Projected 1:32:23 (4:23/km) for today, not race day, 80 %: 1:31:53 to 1:32:53 (4:21 to 4:24/km), high",
    "Confidence high: from the range, 0.5 %",
    "Goal time 1:31:40 is 0:00:43 faster than the projection (-0.8 %), outside the 80 % range",
    "Route: the fitted line through the races used",
    "b 1.064 (error 0.002), high",
    "Newest race 2026-09-27 Run",
    "Fitness now 42, at the races 45",
    "Race 2026-09-06 Run: 5.0 km in 0:20:00, weight 1.00, +0.1 % against the line, without it 1:32:30 (+0.1 %)",
    "Race 2026-09-13 Run: 10.0 km in 0:41:42, weight 1.00, -0.1 % against the line, without it 1:32:20 (0.0 %)",
    "Race 2026-09-20 Run: 21.1 km in 1:32:10, weight 1.00, -0.2 % against the line, without it 1:32:37 (+0.3 %)",
    "Race 2026-09-27 Run: 42.2 km in 3:13:30, weight 1.00, +0.2 % against the line, without it 1:32:09 (-0.3 %)",
    "Largest shift the label reads: 2026-09-20 Run, without it 1:32:37 (+0.3 %)",
    ASSUMES_LINE,
]

# Section 8's fallback: one race of 21,580 m in 5,928 s alone, the half projected by the closest race.
ONE_RACE = [_race_used("s-one", "Run", "2026-09-20T07:00:00Z", 21580.0, 5928.0, None, 46.0)]
# Three races at full weight whose adjusted distances span under twice (10.0 to 10.6 km): the count
# clears F029's bar and the spread fails it, so the closest race is used all the same.
THREE_CLOSE_RACES = [
    _race_used("s-a", "Run", "2026-05-10T07:00:00Z", 10050.0, 2460.0, None, 44.0),
    _race_used("s-b", "Run", "2026-07-12T07:00:00Z", 10210.0, 2490.0, None, 45.0),
    _race_used("s-c", "Run", "2026-09-20T07:00:00Z", 10600.0, 2600.0, None, 46.0),
]

CLOSEST: dict[str, Any] = {
    "as_of": "2026-10-14T12:00:00Z",
    "timezone": "UTC",
    "race": {"date": "2027-04-11", "distance_m": HALF_M, "name": None},
    "target_time_s": 5500.0,
    "unavailable": None,
    "route": "closest_race",
    "projected_time_s": _value(5787.600207, "s", None, None, "low", True),
    "avg_pace_s_per_km": _value(274.326784, "s/km", None, None, "low", True),
    "halfwidth": None,
    "confidence": {"label": "low", "basis": None, "uncertainty": None, "reasons": ["population_default"]},
    "goal_time_gap": {"gap_s": -287.600207, "gap_fraction": -0.049693, "within_range": None},
    "provenance": {
        "b": 1.06,
        "error": 0.05,
        "label": "low",
        "population_default": True,
        "n_eff": 1.0,
        "races": ONE_RACE,
        "newest_race": {"session_id": "s-one", "name": "Run", "start_time": "2026-09-20T07:00:00Z"},
        "fitness_at_races": 46.0,
        "fitness_now": 42.3,
    },
    "leave_one_out": None,
    "effort_note": None,
    "assumptions": list(ASSUMPTIONS),
    "parameters": dict(PARAMETERS),
    "gap_to_goal": None,
}

NO_RANGE_LINE = "Projected 1:36:28 (4:34/km) for today, not race day, no range: the races do not clear the fit's bar (3 races, the longest at least twice the shortest), low, provisional"
BELOW_BAR_LINE = "Confidence low: below the fit's bar, so the population b 1.06 scales the closest race"

CLOSEST_LINES = [
    NO_RANGE_LINE,
    BELOW_BAR_LINE,
    "Goal time 1:31:40 is 0:04:48 faster than the projection (-5.0 %)",
    "Route: the closest race, scaled by b",
    "b 1.06 (population default), low",
    "Newest race 2026-09-20 Run",
    "Fitness now 42, at the races 46",
    "Race 2026-09-20 Run: 21.6 km in 1:38:48, weight 1.00",
    ASSUMES_LINE,
]


def _contract(target: dict[str, Any], status: str = "active") -> dict[str, Any]:
    return {
        "id": "1",
        "race": copy.deepcopy(target["race"]),
        "target_time_s": target["target_time_s"],
        "status": status,
        "created_at": "2026-10-14T12:00:00Z",
        "target": copy.deepcopy(target),
    }


class Api:
    """A fake API: records every request and answers ``responses``, keyed ``METHOD_path_segments``
    (``PUT_goal`` answers ``PUT /goal``, ``POST_goal_preview`` answers ``POST /goal/preview``)."""

    def __init__(self, **responses: httpx.Response | Exception) -> None:
        self.responses = {}
        for key, value in responses.items():
            method, _, path = key.partition("_")
            self.responses[f"{method} /{path.replace('_', '/')}"] = value
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses.get(f"{request.method} {request.url.path}")
        if answer is None:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def calls(self) -> list[str]:
        return [f"{request.method} {request.url.path}" for request in self.requests]


def _install(install_mock_client, **responses: httpx.Response | Exception) -> Api:
    api = Api(**responses)
    install_mock_client(api)
    return api


def _sent(api: Api) -> dict:
    [request] = api.requests
    return json.loads(request.read())


@pytest.mark.parametrize(
    ("given", "metres"),
    [
        ("5k", 5000.0),
        ("5K", 5000.0),
        ("10k", 10000.0),
        ("half", HALF_M),
        ("HALF", HALF_M),
        ("marathon", 42195.0),
        ("Marathon", 42195.0),
        ("13.1mi", 13.1 * 1609.344),
        ("5 km", 5000.0),
        ("21097.5m", HALF_M),
        ("8km", 8000.0),
        ("0.5 mi", 804.672),
    ],
)
def test_distance_parses(install_mock_client, given: str, metres: float) -> None:
    """The table's words and a positive number with km, m or mi (a space allowed inside one quoted
    argument) parse to metres, case-insensitively, and go to the API as ``race.distance_m``."""
    api = _install(install_mock_client, PUT_goal=httpx.Response(200, json=_contract(FITTED)))
    result = runner.invoke(app, ["goal", "set", given, "--date", "2027-04-11"])
    assert result.exit_code == 0, result.output
    assert api.calls == ["PUT /goal"]
    assert _sent(api)["race"]["distance_m"] == pytest.approx(metres, abs=1e-9)


@pytest.mark.parametrize(
    "given",
    ["0km", "-5k", "5x", "5", "nan km", "infkm", "1e400m", "", " ", "km", "0.0 mi", "-0m", "5 k m", "half marathon", "5kms", "0x10m"],
)
def test_bad_distance_exits_2_before_any_request(install_mock_client, given: str) -> None:
    """Zero, negative, a bare number, a non-finite or overflowing number and an unknown word each
    exit 2 with the parameter named, and the API sees no request; ``preview`` refuses the same."""
    api = _install(install_mock_client)
    for args in (["goal", "set", given, "--date", "2027-04-11"], ["goal", "preview", given]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, result.output
        assert "distance" in result.output.lower(), result.output
        assert "Traceback" not in result.output
    assert api.calls == []


@pytest.mark.parametrize(
    ("given", "seconds"),
    [
        ("1:30:00", 5400.0),
        ("90:00", 5400.0),
        ("0:19:59", 1199.0),
        ("19:59", 1199.0),
        ("2:05:07", 7507.0),
        ("0:01", 1.0),
        ("125:00", 7500.0),
    ],
)
def test_time_parses(install_mock_client, given: str, seconds: float) -> None:
    """``H:MM:SS`` and ``MM:SS`` go to the API as ``target_time_s`` in seconds; a bad time exits 2
    before any request."""
    api = _install(install_mock_client, PUT_goal=httpx.Response(200, json=_contract(FITTED)))
    result = runner.invoke(app, ["goal", "set", "half", "--date", "2027-04-11", "--time", given])
    assert result.exit_code == 0, result.output
    assert api.calls == ["PUT /goal"]
    assert _sent(api)["target_time_s"] == seconds

    for bad in ("1:60:00", "1:30:60", "0:00", "0:00:00", "90", "1h30", "", " ", "1:30:00:00", "-1:30:00", "nan", "inf", "1e400:00", "1:3", "01:30:00.5", "1:-5:00"):
        api = _install(install_mock_client)
        result = runner.invoke(app, ["goal", "set", "half", "--date", "2027-04-11", "--time", bad])
        assert result.exit_code == 2, (bad, result.output)
        assert "time" in result.output.lower(), result.output
        assert "Traceback" not in result.output
        assert api.calls == [], bad


def test_set_prints_every_line(install_mock_client) -> None:
    """``set`` sends the race and goal time as ``PUT /goal`` takes them (no time: no
    ``target_time_s``; no name: ``name`` null) and prints section 7's lines from the returned
    contract, on a fitted-line and a closest-race response."""
    api = _install(install_mock_client, PUT_goal=httpx.Response(200, json=_contract(FITTED)))
    result = runner.invoke(
        app, ["goal", "set", "half", "--date", "2027-04-11", "--time", "1:31:40", "--name", "Vancouver Half"]
    )
    assert result.exit_code == 0, result.output
    assert _sent(api) == {
        "race": {"date": "2027-04-11", "distance_m": HALF_M, "name": "Vancouver Half"},
        "target_time_s": 5500.0,
    }
    assert result.stdout.splitlines() == ["Goal 2027-04-11 Vancouver Half: 21.1 km, goal time 1:31:40", *FITTED_LINES]

    api = _install(install_mock_client, PUT_goal=httpx.Response(200, json=_contract(CLOSEST)))
    result = runner.invoke(app, ["goal", "set", "21097.5m", "--date", "2027-04-11", "--time", "91:40"])
    assert result.exit_code == 0, result.output
    assert _sent(api) == {"race": {"date": "2027-04-11", "distance_m": HALF_M, "name": None}, "target_time_s": 5500.0}
    assert result.stdout.splitlines() == ["Goal 2027-04-11: 21.1 km, goal time 1:31:40", *CLOSEST_LINES]

    # No goal time: none is sent, and the goal-time line says so.
    no_time = copy.deepcopy(FITTED)
    no_time["target_time_s"] = None
    no_time["goal_time_gap"] = None
    api = _install(install_mock_client, PUT_goal=httpx.Response(200, json=_contract(no_time)))
    result = runner.invoke(app, ["goal", "set", "half", "--date", "2027-04-11"])
    assert result.exit_code == 0, result.output
    assert _sent(api) == {"race": {"date": "2027-04-11", "distance_m": HALF_M, "name": None}}
    lines = result.stdout.splitlines()
    assert lines[0] == "Goal 2027-04-11 Vancouver Half: 21.1 km, no goal time"
    assert lines[3] == "Goal time: none set"
    assert lines[1:3] + lines[4:] == FITTED_LINES[:2] + FITTED_LINES[3:]


def test_show_prints_every_line(install_mock_client) -> None:
    """The bare command reads ``GET /goal`` and prints the same lines; a completed goal says so;
    ``--as-of`` is sent as given; and every served figure is read from the body, not recomputed:
    an unavailable projection prints its reason and still lists b, the races and the assumptions."""
    api = _install(install_mock_client, GET_goal=httpx.Response(200, json=_contract(FITTED)))
    result = runner.invoke(app, ["goal"])
    assert result.exit_code == 0, result.output
    assert api.calls == ["GET /goal"]
    assert api.requests[0].url.params.get("as_of") is None
    assert result.stdout.splitlines() == ["Goal 2027-04-11 Vancouver Half: 21.1 km, goal time 1:31:40", *FITTED_LINES]

    api = _install(install_mock_client, GET_goal=httpx.Response(200, json=_contract(CLOSEST, "completed")))
    result = runner.invoke(app, ["goal", "--as-of", "2027-05-01"])
    assert result.exit_code == 0, result.output
    assert api.requests[0].url.params.get("as_of") == "2027-05-01"
    assert result.stdout.splitlines() == ["Goal 2027-04-11: 21.1 km, goal time 1:31:40 (completed)", *CLOSEST_LINES]

    unavailable = copy.deepcopy(CLOSEST)
    unavailable.update(
        unavailable="no_races",
        route=None,
        projected_time_s=None,
        avg_pace_s_per_km=None,
        halfwidth=None,
        confidence=None,
        goal_time_gap=None,
    )
    unavailable["provenance"].update(races=[], newest_race=None, fitness_at_races=None, n_eff=None)
    _install(install_mock_client, GET_goal=httpx.Response(200, json=_contract(unavailable)))
    result = runner.invoke(app, ["goal"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Goal 2027-04-11: 21.1 km, goal time 1:31:40",
        "Projection unavailable: no race used; mark a race as Race in Garmin Connect and run runcoach sync tags --since <its date>",
        "b 1.06 (population default), low",
        "Newest race: none",
        "Fitness now 42, at the races -",
        ASSUMES_LINE,
    ]


def test_riegel_unavailable_prints_its_reason_and_the_rest(install_mock_client) -> None:
    """``riegel_unavailable`` as the API serves it: F029's estimate is not finite, so ``b``, its
    error and its label are null and ``population_default`` false, every figure is null, and the
    races, the newest race and the fitness are still served. The CLI prints the reason, ``b
    unavailable``, the newest race, the fitness, each race and the assumptions, and exits 0."""
    body = copy.deepcopy(CLOSEST)
    body.update(
        unavailable="riegel_unavailable",
        route=None,
        projected_time_s=None,
        avg_pace_s_per_km=None,
        halfwidth=None,
        confidence=None,
        goal_time_gap=None,
    )
    body["provenance"].update(b=None, error=None, label=None, population_default=False)
    _install(install_mock_client, GET_goal=httpx.Response(200, json=_contract(body)))
    result = runner.invoke(app, ["goal"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Goal 2027-04-11: 21.1 km, goal time 1:31:40",
        "Projection unavailable: the endurance exponent could not be computed; check the races in runcoach state",
        "b unavailable",
        "Newest race 2026-09-20 Run",
        "Fitness now 42, at the races 46",
        "Race 2026-09-20 Run: 21.6 km in 1:38:48, weight 1.00",
        ASSUMES_LINE,
    ]


def test_race_dates_print_in_the_response_timezone(install_mock_client) -> None:
    """The marathon started at 18:30 on 2026-09-27 in Vancouver, stored in UTC as 01:30 on
    2026-09-28. With ``timezone`` America/Vancouver every line prints it as the athlete's
    2026-09-27, the same lines as the UTC body whose start is 07:00 that day: the race line, the
    newest race and nothing dated the UTC day."""
    body = copy.deepcopy(FITTED)
    body["timezone"] = "America/Vancouver"
    evening = "2026-09-28T01:30:00Z"
    body["provenance"]["races"][3]["start_time"] = evening
    body["provenance"]["newest_race"]["start_time"] = evening
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["Preview 2027-04-11 Vancouver Half: 21.1 km, goal time 1:31:40", *FITTED_LINES]
    assert "2026-09-28" not in result.stdout


def test_preview_prints_every_line(install_mock_client) -> None:
    """``preview`` posts the distance alone (no date, no time, no name) to ``POST /goal/preview``,
    sends ``--as-of`` as given, and prints section 7's lines headed ``Preview``, on a fitted-line
    and a closest-race response."""
    api = _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=FITTED))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    assert api.calls == ["POST /goal/preview"]
    assert api.requests[0].url.params.get("as_of") is None
    assert _sent(api) == {"race": {"distance_m": HALF_M}}
    assert result.stdout.splitlines() == ["Preview 2027-04-11 Vancouver Half: 21.1 km, goal time 1:31:40", *FITTED_LINES]

    api = _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=CLOSEST))
    result = runner.invoke(app, ["goal", "preview", "13.1 mi", "--as-of", "2026-10-01"])
    assert result.exit_code == 0, result.output
    assert api.requests[0].url.params.get("as_of") == "2026-10-01"
    assert _sent(api) == {"race": {"distance_m": pytest.approx(13.1 * 1609.344)}}
    assert result.stdout.splitlines() == ["Preview 2027-04-11: 21.1 km, goal time 1:31:40", *CLOSEST_LINES]


@pytest.mark.parametrize(
    "args",
    [
        ["goal", "--as-of", "2026-09-01", "preview", "half"],
        ["goal", "--as-of", "2026-09-01", "set", "half", "--date", "2026-11-29"],
    ],
    ids=["preview", "set"],
)
def test_as_of_before_a_subcommand_exits_2(install_mock_client, args: list[str]) -> None:
    """``--as-of`` belongs to the bare command and to ``preview``'s own option; given before a
    subcommand it would be dropped and a projection for now printed as if it were the backtest.
    It exits 2 naming ``--as-of`` before any request, and points ``preview`` at its own option."""
    api = _install(install_mock_client)
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "--as-of" in result.output
    unboxed = " ".join("".join(" " if "─" <= c <= "╿" else c for c in result.output).split())
    assert "runcoach goal preview <distance> --as-of DATE" in unboxed, unboxed
    assert "Traceback" not in result.output
    assert api.calls == []


def test_no_goal_set_exits_0(install_mock_client) -> None:
    _install(install_mock_client, GET_goal=httpx.Response(404, json={"detail": "no_goal"}))
    result = runner.invoke(app, ["goal"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [NO_GOAL_LINE]


def test_api_without_goal_exits_1(install_mock_client) -> None:
    """An API from before the route answers 404 ``Not Found``: an error, not "no goal"; a 422 and a
    500 are reported the same way, the body cut to 200 characters and made printable."""
    _install(install_mock_client, GET_goal=httpx.Response(404, json={"detail": "Not Found"}))
    result = runner.invoke(app, ["goal"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "API returned 404" in result.stderr and "Not Found" in result.stderr
    assert NO_GOAL_LINE not in result.output
    assert "Traceback" not in result.output

    long_body = "x" * 300 + "\x1b[2J"
    _install(install_mock_client, PUT_goal=httpx.Response(422, text=long_body))
    result = runner.invoke(app, ["goal", "set", "half", "--date", "2020-01-01"])
    assert result.exit_code == 1, result.output
    assert "API returned 422: " + "x" * 200 in result.stderr
    assert "\x1b" not in result.stderr and "x" * 201 not in result.stderr

    _install(install_mock_client, POST_goal_preview=httpx.Response(500, text="boom"))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 1, result.output
    assert "API returned 500: boom" in result.stderr


def test_unreachable_api_exits_1(install_mock_client) -> None:
    for args in (["goal"], ["goal", "set", "half", "--date", "2027-04-11"], ["goal", "preview", "half"]):
        api = _install(
            install_mock_client,
            GET_goal=httpx.ConnectError("connection refused"),
            PUT_goal=httpx.ConnectError("connection refused"),
            POST_goal_preview=httpx.ConnectError("connection refused"),
        )
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert result.stdout == ""
        assert "could not reach API at http://example.test" in result.stderr
        assert "Traceback" not in result.output
        assert len(api.calls) == 1


def test_only_a_transport_error_is_unreachable(install_mock_client) -> None:
    """``get_goal``, ``put_goal`` and ``post_goal_preview`` translate ``httpx.TransportError`` alone
    into ``ApiUnreachableError``: a non-transport error raised under the request (a ``RuntimeError``
    here) propagates as itself from the client function, and the command does not report it as an
    unreachable API."""
    from runcoach_cli import api_client

    calls = (
        (lambda: api_client.get_goal("http://example.test", None), ["goal"], "GET_goal"),
        (lambda: api_client.put_goal("http://example.test", {}), ["goal", "set", "half", "--date", "2027-04-11"], "PUT_goal"),
        (lambda: api_client.post_goal_preview("http://example.test", {}, None), ["goal", "preview", "half"], "POST_goal_preview"),
    )
    for call, args, key in calls:
        _install(install_mock_client, **{key: RuntimeError("not a transport error")})
        with pytest.raises(RuntimeError, match="not a transport error"):
            call()
        _install(install_mock_client, **{key: RuntimeError("not a transport error")})
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert isinstance(result.exception, RuntimeError), repr(result.exception)
        assert "could not reach API" not in result.output, result.output


def test_closest_race_names_the_bar_not_a_count(install_mock_client) -> None:
    """F029's bar fails on the count (fewer than 3 effective races) and on the spread (the longest
    adjusted distance under twice the shortest), so the closest-race route is taken for one race
    and for three races too close in distance alike. The no-range and reason sentences name the
    bar, never a race count, on both bodies; every other closest-race line stays as it is."""
    for races in (ONE_RACE, THREE_CLOSE_RACES):
        body = copy.deepcopy(CLOSEST)
        body["provenance"]["races"] = races
        body["provenance"]["n_eff"] = float(len(races))
        _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
        result = runner.invoke(app, ["goal", "preview", "half"])
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert lines[1] == NO_RANGE_LINE, lines[1]
        assert lines[2] == BELOW_BAR_LINE, lines[2]
        assert "fewer than three" not in result.stdout
        assert "three races" not in result.stdout
        assert lines[3:8] == CLOSEST_LINES[2:7]
        assert lines[8 : 8 + len(races)] == [
            f"Race {r['start_time'][:10]} Run: {r['gap_equivalent_m'] / 1000:.1f} km in "
            f"{int(r['duration_s']) // 3600}:{int(r['duration_s']) % 3600 // 60:02d}:{int(r['duration_s']) % 60:02d}, weight 1.00"
            for r in races
        ]
        assert lines[-1] == ASSUMES_LINE


def test_label_basis_and_reasons_in_words(install_mock_client) -> None:
    """Basis ``one_race`` names the leave-one-out shift; each reason code prints in words after it,
    in the served order; an unknown code prints as served through ``_printable``."""
    body = copy.deepcopy(FITTED)
    body["confidence"] = {
        "label": "low",
        "basis": "one_race",
        "uncertainty": 0.058655,
        "reasons": ["fitness_below_races", "below_one", "outside_calibrated_range", "beyond_longest_race", "new\x1bcode"],
    }
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "marathon"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[2] == (
        "Confidence low: from one race's leave-one-out shift, 5.9 %; fitness now is below 70 % of fitness at the "
        "races; b reads below 1; the goal distance is outside the calibrated range; the goal distance is beyond "
        "the longest race used; new?code"
    )


@pytest.mark.parametrize(
    ("label", "uncertainty", "printed"),
    [
        ("high", 0.02, "2.0 %"),
        ("medium", 0.020049, "2.01 %"),
        ("medium", 0.0200000001, "2.01 %"),
        ("medium", math.nextafter(0.02, 1.0), "2.01 %"),
        ("medium", 0.0204, "2.04 %"),
        ("medium", 0.0205, "2.1 %"),
        ("medium", 0.05, "5.0 %"),
        ("low", 0.050049, "5.01 %"),
        ("low", math.nextafter(0.05, 1.0), "5.01 %"),
        ("low", 0.0549, "5.5 %"),
        ("high", 0.019951, "2.0 %"),
    ],
)
def test_the_printed_uncertainty_never_contradicts_the_label(
    install_mock_client, label: str, uncertainty: float, printed: str
) -> None:
    """The label compares the unrounded ``u`` with the served edges (2 % and 5 %, at most); a ``u``
    just above an edge rounds onto it at one decimal, so it prints to two decimals rounded up,
    which shows it above the edge as the label says. Rounded up from the figure the fraction
    stands for, not from its binary product (0.0204 times 10000 is 204.00000000000003, which a
    bare ceiling prints as 2.05 %), and never below the edge plus 0.01 (the float just above 2 %
    prints 2.01 %). At or below an edge, and clear of one, the one-decimal half-up form stands."""
    body = copy.deepcopy(FITTED)
    body["confidence"] = {"label": label, "basis": "range", "uncertainty": uncertainty, "reasons": []}
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[2] == f"Confidence {label}: from the range, {printed}"


def test_goal_time_gap_in_words(install_mock_client) -> None:
    """A slower goal reads ``slower``, one inside the range ``inside``, and a null ``largest`` on the
    fitted line says no single race decides it."""
    body = copy.deepcopy(FITTED)
    body["goal_time_gap"] = {"gap_s": 30.0, "gap_fraction": 0.005413, "within_range": True}
    body["leave_one_out"]["largest"] = None
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[3] == "Goal time 1:31:40 is 0:00:30 slower than the projection (+0.5 %), inside the 80 % range"
    assert lines[-2] == "Largest shift the label reads: none, no leave-one-out keeps the fitted line"


def test_the_largest_line_names_the_shift_the_label_reads_not_the_largest_overall(install_mock_client) -> None:
    """A race without which the rest fall below the bar prints its own ``without it`` time, and its
    shift can be the largest on the page (the athlete's Chicago marathon, +4.0 % beside a 5K at
    +0.1 %); the label does not read it. The closing line names the shift the label does read, and
    says so, so it never contradicts the race lines above it."""
    body = copy.deepcopy(FITTED)
    rows = body["leave_one_out"]["races"]
    rows[3].update(route="closest_race", projected_time_s=5764.5, shift=0.040049)
    rows[0].update(shift=0.001408)
    body["leave_one_out"]["largest"] = rows[0]
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[-3].endswith("without it 1:36:05 (+4.0 %)"), lines[-3]
    assert lines[-2] == "Largest shift the label reads: 2026-09-06 Run, without it 1:32:30 (+0.1 %)"


# The athlete's six once the trail halves are untagged (five 5Ks and Chicago), the half as the demo's
# figures give it: 1:30:01, range 2.1 %, Chicago setting the line.
SIX_RACES = [
    _race_used("s-chi", "Run", "2024-10-13T12:30:00Z", 42515.76, 11138.164, 0.0001, 47.1),
    _race_used("s-a", "Run", "2024-10-26T15:00:00Z", 4974.80, 1232.761, 0.0103, 41.5),
    _race_used("s-b", "Run", "2024-11-09T15:00:00Z", 5019.45, 1205.641, -0.0127, 36.4),
    _race_used("s-c", "Run", "2025-03-08T15:00:00Z", 4950.81, 1206.331, 0.0008, 30.8),
    _race_used("s-d", "Run", "2025-03-15T15:00:00Z", 5047.45, 1233.371, 0.0050, 30.4),
    _race_used("s-e", "Run", "2025-05-24T15:00:00Z", 4978.70, 1218.939, -0.0035, 31.5),
]
SIX_LEAVE_ONE_OUT = [
    {"session_id": "s-chi", "route": "closest_race", "projected_time_s": 5617.22, "shift": 0.040049, "sets_the_line": True},
    {"session_id": "s-a", "route": "fitted_line", "projected_time_s": 5394.44, "shift": -0.001201, "sets_the_line": False},
    {"session_id": "s-b", "route": "fitted_line", "projected_time_s": 5408.39, "shift": 0.001382, "sets_the_line": False},
    {"session_id": "s-c", "route": "fitted_line", "projected_time_s": 5401.67, "shift": 0.000138, "sets_the_line": False},
    {"session_id": "s-d", "route": "fitted_line", "projected_time_s": 5400.72, "shift": -0.000038, "sets_the_line": False},
    {"session_id": "s-e", "route": "fitted_line", "projected_time_s": 5399.68, "shift": -0.00023, "sets_the_line": False},
]


def _one_race_sets_the_line() -> dict[str, Any]:
    body = copy.deepcopy(FITTED)
    body["projected_time_s"] = _value(5400.925134, "s", 5288.81, 5515.41, "low", False)
    body["avg_pace_s_per_km"] = _value(255.997, "s/km", 250.68, 261.43, "low", False)
    body["halfwidth"] = 0.021198
    body["confidence"] = {"label": "low", "basis": "range", "uncertainty": 0.021198, "reasons": ["one_race_sets_the_line"]}
    body["target_time_s"] = 5580.0
    body["goal_time_gap"] = {"gap_s": 179.07, "gap_fraction": 0.033156, "within_range": False}
    body["provenance"]["races"] = copy.deepcopy(SIX_RACES)
    body["provenance"]["newest_race"] = {"session_id": "s-e", "name": "Run", "start_time": "2025-05-24T15:00:00Z"}
    rows = copy.deepcopy(SIX_LEAVE_ONE_OUT)
    body["leave_one_out"] = {"races": rows, "largest": copy.deepcopy(rows[2])}
    return body


def test_one_race_sets_the_line_is_named_in_words(install_mock_client) -> None:
    """Reason ``one_race_sets_the_line`` prints in words naming the race by date and name, from the
    leave-one-out row marked ``sets_the_line``; that race's own line says it sets the line in place
    of a bare residual, and keeps its ``without it`` time; the other race lines are unchanged."""
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=_one_race_sets_the_line()))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[1] == (
        "Projected 1:30:01 (4:16/km) for today, not race day, 80 %: 1:28:09 to 1:31:55 (4:11 to 4:21/km), low"
    )
    assert lines[2] == (
        "Confidence low: from the range, 2.1 %; one race sets the line: 2024-10-13 Run (the other races span "
        "less than twice the distance without it)"
    )
    assert lines[8] == (
        "Race 2024-10-13 Run: 42.5 km in 3:05:38, weight 1.00, sets the line (its 0.0 % against the line is no "
        "check), without it 1:33:37 (+4.0 %)"
    )
    assert lines[9] == (
        "Race 2024-10-26 Run: 5.0 km in 0:20:33, weight 1.00, +1.0 % against the line, without it 1:29:54 (-0.1 %)"
    )
    assert lines[-2] == "Largest shift the label reads: 2024-11-09 Run, without it 1:30:08 (+0.1 %)"


def test_two_races_that_each_set_the_line_are_both_named(install_mock_client) -> None:
    """When two rows are marked (each end of a narrow middle), the reason names both, in race order."""
    body = _one_race_sets_the_line()
    body["leave_one_out"]["races"][2]["sets_the_line"] = True
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[2] == (
        "Confidence low: from the range, 2.1 %; one race sets the line: 2024-10-13 Run, 2024-11-09 Run (the "
        "other races span less than twice the distance without it)"
    )
    assert lines[10].startswith("Race 2024-11-09 Run: 5.0 km in 0:20:06, weight 1.00, sets the line (")


@pytest.mark.parametrize(
    "breaks",
    [
        pytest.param(lambda b: b["leave_one_out"]["races"][0].pop("sets_the_line"), id="mark_missing"),
        pytest.param(lambda b: b["leave_one_out"]["races"][0].update(sets_the_line="yes"), id="mark_not_a_bool"),
        pytest.param(
            lambda b: b["leave_one_out"]["races"][0].update(sets_the_line=False), id="reason_without_a_marked_row"
        ),
    ],
)
def test_a_malformed_mark_takes_the_error_path(install_mock_client, breaks) -> None:
    body = _one_race_sets_the_line()
    breaks(body)
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "the API's response could not be understood" in result.stderr


@pytest.mark.parametrize(
    "breaks",
    [
        pytest.param(lambda b: b.pop("provenance"), id="provenance_missing"),
        pytest.param(lambda b: b["projected_time_s"].update(value="5542"), id="time_a_string"),
        pytest.param(lambda b: b.update(route="guess"), id="route_unknown"),
        pytest.param(lambda b: b.update(route=None), id="route_null_while_served"),
        pytest.param(lambda b: b["confidence"].update(label="certain"), id="label_unknown"),
        pytest.param(lambda b: b.update(assumptions="flat"), id="assumptions_not_a_list"),
        pytest.param(lambda b: b["provenance"]["races"][0].update(start_time="yesterday"), id="start_unreadable"),
        pytest.param(lambda b: b["provenance"].update(races={}), id="races_not_a_list"),
        pytest.param(lambda b: b.update(projected_time_s=None, unavailable=None), id="no_time_and_no_reason"),
    ],
)
def test_a_malformed_body_takes_the_error_path(install_mock_client, breaks) -> None:
    body = copy.deepcopy(FITTED)
    breaks(body)
    _install(install_mock_client, POST_goal_preview=httpx.Response(200, json=body))
    result = runner.invoke(app, ["goal", "preview", "half"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "the API's response could not be understood" in result.stderr
    assert "Traceback" not in result.output
