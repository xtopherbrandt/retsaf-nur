"""``runcoach goal`` -- the goal race and its projected finish (F032 reference section 7).

``runcoach goal set <distance> --date YYYY-MM-DD [--time TIME] [--name TEXT]`` stores the goal
through ``PUT /goal``; ``runcoach goal [--as-of DATE]`` reads it through ``GET /goal``;
``runcoach goal preview <distance> [--as-of DATE]`` posts the distance alone to
``POST /goal/preview`` (no race date, so the API dates it on the day used; nothing is stored).
``--as-of`` given before a subcommand (``runcoach goal --as-of DATE preview half``) would not be
read, so it exits 2 before any request.

``<distance>`` is case-insensitive: ``5k``, ``10k``, ``half``, ``marathon`` (5000, 10000, 21097.5,
42195 m) or a positive decimal number with ``km``, ``m`` or ``mi``, a space allowed between them
inside one quoted argument (``"5 km"``). Zero, a negative, a bare number, ``nan``, ``inf``, a number
too large for a float and anything else exit 2 before any request is made. ``TIME`` is ``H:MM:SS``
or ``MM:SS`` (``90:00`` is ninety minutes); a minute or second of 60 or more in a field that has a
field above it, or a zero time, exits 2. The date is sent as given; the API refuses a malformed or
past one with a 422, reported like any other non-2xx.

Printed, one line each: the goal (date, name, distance, goal time, ``(completed)`` once the race day
has passed); the projected time and the pace per km (athlete, 2026-10-29) with the 80 % range, or
``no range: the races do not clear the fit's bar (3 races, the longest at least twice the shortest)``
on the closest-race route (F029's bar fails on the count or on the spread, and the served body says
only that it failed), and the label; the label with its
basis and each reason in words; the goal time's gap and whether it sits inside the range; the route
in words; b with its label (``b unavailable`` when the API serves it null); the newest race;
fitness now and at the races; one line per race used (date, name, adjusted distance, time,
weight, residual, leave-one-out time and shift); ``Largest shift the label reads: <date> <name>,
without it <time> (<shift>)``, the largest shift among the races whose removal leaves the rest
above the bar (a race the fit cannot lose may move the time further on its own line and is not
read); the assumptions in words. Every race line leads with
its date, since every session on the athlete's store is named ``Run``. When the API serves
``unavailable`` the reason prints in its place and b, the races and the assumptions still follow.

No active goal (404 ``no_goal``) prints ``No goal set. Set one with runcoach goal set.`` and exits
0. A 404 with any other ``detail`` (an API from before the route answers ``Not Found``), any other
non-200 status (the body cut to 200 characters and made printable), an unreachable API and a body
that cannot be read each print an error and exit 1, as ``runcoach state`` does.

Dates print in the response's ``timezone``. Paces and durations are rounded half up, as
``runcoach sessions`` rounds them; percentages to one decimal, half up, as ``runcoach state`` prints
a residual. Served text printed as served (a race name, an unknown reason, an unknown assumption)
passes through ``sessions._printable``.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, get_goal, post_goal_preview, put_goal
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import (
    _half_up_seconds,
    _printable,
    _write_utf8,
    format_duration,
    format_pace,
)

goal_app = typer.Typer(
    invoke_without_command=True,
    help="Show, set or preview the goal race and its projected finish.",
)

# A non-200 body is printed cut to this many characters, as ``runcoach state`` cuts one.
ERROR_TEXT_CHARS = 200

NO_GOAL_DETAIL = "no_goal"
NO_GOAL_LINE = "No goal set. Set one with runcoach goal set."

METRES_PER_MILE = 1609.344
DISTANCE_WORDS: dict[str, float] = {"5k": 5000.0, "10k": 10000.0, "half": 21097.5, "marathon": 42195.0}
DISTANCE_UNITS: dict[str, float] = {"km": 1000.0, "m": 1.0, "mi": METRES_PER_MILE}
# A plain decimal number and a unit: digits only, so ``nan``, ``inf``, a sign and an exponent never
# reach ``float``.
_DISTANCE = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)\s*(km|m|mi)$")
_TIME = re.compile(r"^(?:(\d{1,3}):)?(\d{1,3}):(\d{2})$")

DISTANCE_HELP = (
    "5k, 10k, half, marathon, or a positive number with km, m or mi (13.1mi; a space is allowed inside one "
    'quoted argument, "5 km").'
)

FITTED_LINE = "fitted_line"
CLOSEST_RACE = "closest_race"
ROUTE_WORDS = {
    FITTED_LINE: "the fitted line through the races used",
    CLOSEST_RACE: "the closest race, scaled by b",
}
# F029's bar fails on the count (3 effective races) or on the spread (the longest adjusted distance
# at least twice the shortest); the served body says only that it failed, so the sentences name the
# bar, never a race count.
NO_RANGE = "no range: the races do not clear the fit's bar (3 races, the longest at least twice the shortest)"
LABELS = ("low", "medium", "high")

BASIS_WORDS = {"range": "from the range", "one_race": "from one race's leave-one-out shift"}
REASON_WORDS = {
    "fitness_below_races": "fitness now is below 70 % of fitness at the races",
    "population_default": "below the fit's bar, so the population b 1.06 scales the closest race",
    "below_one": "b reads below 1",
    "outside_calibrated_range": "the goal distance is outside the calibrated range",
    "beyond_longest_race": "the goal distance is beyond the longest race used",
}
UNAVAILABLE_WORDS = {
    "no_races": (
        "no race used; mark a race as Race in Garmin Connect and run runcoach sync tags --since <its date>"
    ),
    "riegel_unavailable": "the endurance exponent could not be computed; check the races in runcoach state",
    "projection_not_finite": "a served figure is out of range; check the races listed",
}
ASSUMPTION_WORDS = {
    "flat_course": "a flat course",
    "still_air": "still air",
    "cool_conditions": "cool conditions",
    "no_economy_adjustment": "no economy adjustment",
    "no_durability_adjustment": "no durability adjustment",
    "projected_for_today_not_race_day": "projected for today, not race day",
    "watch_timer_time": "watch timer time (an auto-pause shortens it; chip time may differ)",
}
# The label reads only the shifts whose remaining races still clear the bar, so the line says it
# names the largest of those, not the largest shift on the page (a race the fit cannot lose can
# move the time further and still not count).
LARGEST_WORDS = "Largest shift the label reads"
NO_LARGEST_LINE = f"{LARGEST_WORDS}: none, no leave-one-out keeps the fitted line"


# ---------------------------------------------------------------------------
# The two parsers: each refuses before any request is made.
# ---------------------------------------------------------------------------


def parse_distance(text: str) -> float:
    """``text`` in metres, or raise ``typer.BadParameter`` (exit 2)."""
    given = text.strip().lower()
    if given in DISTANCE_WORDS:
        return DISTANCE_WORDS[given]
    match = _DISTANCE.match(given)
    if match is None:
        raise typer.BadParameter(f"{text!r} is not a distance: {DISTANCE_HELP}", param_hint="DISTANCE")
    metres = float(match.group(1)) * DISTANCE_UNITS[match.group(2)]
    if not math.isfinite(metres) or metres <= 0:
        raise typer.BadParameter(f"{text!r} is not a positive distance", param_hint="DISTANCE")
    return metres


def parse_time(text: str) -> float:
    """``text`` (``H:MM:SS`` or ``MM:SS``) in seconds, or raise ``typer.BadParameter`` (exit 2)."""
    match = _TIME.match(text.strip())
    if match is None:
        raise typer.BadParameter(f"{text!r} is not a time: give H:MM:SS or MM:SS", param_hint="--time")
    hours, minutes, seconds = (int(part) if part is not None else None for part in match.groups())
    if seconds >= 60 or (hours is not None and minutes >= 60):
        raise typer.BadParameter(f"{text!r} is not a time: a minute or second of 60 or more", param_hint="--time")
    total = float((hours or 0) * 3600 + minutes * 60 + seconds)
    if total <= 0:
        raise typer.BadParameter(f"{text!r} is not a time above zero", param_hint="--time")
    return total


# ---------------------------------------------------------------------------
# Rendering a PaceTarget body.
# ---------------------------------------------------------------------------


def _number(value: Any, what: str) -> float:
    """``value`` as a finite float; a bool, a string, a null or a non-finite number is malformed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} is not a number")
    return float(value)


def _optional(value: Any, what: str) -> float | None:
    return None if value is None else _number(value, what)


def _label(value: Any) -> str:
    if value not in LABELS:
        raise ValueError("confidence is not low, medium or high")
    return value


def _string(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} is not a string")
    return _printable(value)


def _mss(seconds: float) -> str:
    minutes, secs = divmod(_half_up_seconds(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _percent(fraction: Any, what: str, signed: bool) -> str:
    """A fraction as a percentage to one decimal, rounded half up; signed when asked, and one that
    rounds to zero prints ``0.0 %``, never ``-0.0 %``."""
    tenths = math.floor(_number(fraction, what) * 1000 + 0.5)
    if tenths == 0:
        return "0.0 %"
    return f"{tenths / 10:+.1f} %" if signed else f"{tenths / 10:.1f} %"


def _whole(value: Any, what: str) -> str:
    """A figure rounded half up to a whole number; ``-`` for a null."""
    return "-" if value is None else str(math.floor(_number(value, what) + 0.5))


def _km(value: Any, what: str) -> str:
    return f"{_number(value, what) / 1000:.1f} km"


def _date(start_time: Any, zone: ZoneInfo) -> str:
    when = datetime.fromisoformat(_string(start_time, "start_time"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(zone).strftime("%Y-%m-%d")


def _dated_name(start_time: Any, name: Any, zone: ZoneInfo) -> str:
    """``<date> <name>``, the date alone when the name is null."""
    day = _date(start_time, zone)
    return day if name is None else f"{day} {_string(name, 'name')}"


def _head(word: str, body: dict, status: Any) -> str:
    """``Goal 2027-04-11 Vancouver Half: 21.1 km, goal time 1:31:40``; ``(completed)`` when the served
    status says so."""
    race = body["race"]
    if not isinstance(race, dict):
        raise ValueError("race is not an object")
    when = _string(race["date"], "race.date")
    name = race["name"]
    who = when if name is None else f"{when} {_string(name, 'race.name')}"
    target = _optional(body["target_time_s"], "target_time_s")
    time = "no goal time" if target is None else f"goal time {format_duration(target)}"
    line = f"{word} {who}: {_km(race['distance_m'], 'race.distance_m')}, {time}"
    if status == "completed":
        line += " (completed)"
    return line


def _projected_line(body: dict, route: str) -> str:
    time = body["projected_time_s"]
    pace = body["avg_pace_s_per_km"]
    if not isinstance(time, dict) or not isinstance(pace, dict):
        raise ValueError("projected_time_s or avg_pace_s_per_km is not an object")
    label = _label(time["confidence"])
    line = f"Projected {format_duration(_number(time['value'], 'projected_time_s'))} ({format_pace(_number(pace['value'], 'avg_pace_s_per_km'))})"
    if route == FITTED_LINE:
        interval, pace_interval = time["interval"], pace["interval"]
        if not isinstance(interval, dict) or not isinstance(pace_interval, dict):
            raise ValueError("a fitted line without an interval")
        low, high = (_number(interval[k], f"interval.{k}") for k in ("low", "high"))
        pace_low, pace_high = (_number(pace_interval[k], f"pace interval.{k}") for k in ("low", "high"))
        line += f", 80 %: {format_duration(low)} to {format_duration(high)} ({_mss(pace_low)} to {_mss(pace_high)}/km), {label}"
    else:
        line += f", {NO_RANGE}, {label}"
    if time["provisional"] is True:
        line += ", provisional"
    return line


def _confidence_line(confidence: Any) -> str:
    if not isinstance(confidence, dict):
        raise ValueError("confidence is not an object")
    parts: list[str] = []
    basis = confidence["basis"]
    if basis is not None:
        if basis not in BASIS_WORDS:
            raise ValueError("basis is not range or one_race")
        parts.append(f"{BASIS_WORDS[basis]}, {_percent(confidence['uncertainty'], 'uncertainty', signed=False)}")
    reasons = confidence["reasons"]
    if not isinstance(reasons, list):
        raise ValueError("reasons is not a list")
    parts += [REASON_WORDS[r] if r in REASON_WORDS else _string(r, "reason") for r in reasons]
    return f"Confidence {_label(confidence['label'])}: {'; '.join(parts)}"


def _gap_line(body: dict, route: str) -> str:
    target = _optional(body["target_time_s"], "target_time_s")
    if target is None:
        return "Goal time: none set"
    gap = body["goal_time_gap"]
    if not isinstance(gap, dict):
        raise ValueError("a goal time without its gap")
    gap_s = _number(gap["gap_s"], "gap_s")
    fraction = _percent(gap["gap_fraction"], "gap_fraction", signed=True)
    if _half_up_seconds(abs(gap_s)) == 0:
        words = f"matches the projection ({fraction})"
    else:
        side = "faster" if gap_s < 0 else "slower"
        words = f"is {format_duration(abs(gap_s))} {side} than the projection ({fraction})"
    line = f"Goal time {format_duration(target)} {words}"
    within = gap["within_range"]
    if route == FITTED_LINE:
        if not isinstance(within, bool):
            raise ValueError("within_range is not true or false")
        line += ", inside the 80 % range" if within else ", outside the 80 % range"
    return line


def _b_line(provenance: dict) -> str:
    """b with its error and label; ``b unavailable`` when the API serves b and its label null (the
    exponent's estimate is not finite)."""
    if provenance["b"] is None and provenance["label"] is None:
        return "b unavailable"
    label = _label(provenance["label"])
    if provenance["population_default"] is True:
        return f"b {_number(provenance['b'], 'b'):.2f} (population default), {label}"
    return f"b {_number(provenance['b'], 'b'):.3f} (error {_number(provenance['error'], 'error'):.3f}), {label}"


def _race_line(race: dict, without: dict | None, zone: ZoneInfo) -> str:
    line = (
        f"Race {_dated_name(race['start_time'], race['name'], zone)}: "
        f"{_km(race['gap_equivalent_m'], 'gap_equivalent_m')} in "
        f"{format_duration(_number(race['duration_s'], 'duration_s'))}, weight {_number(race['weight'], 'weight'):.2f}"
    )
    if race["residual"] is not None:
        line += f", {_percent(race['residual'], 'residual', signed=True)} against the line"
    if without is not None:
        line += f", without it {_without(without)}"
    return line


def _without(row: dict) -> str:
    return (
        f"{format_duration(_number(row['projected_time_s'], 'leave-one-out projected_time_s'))} "
        f"({_percent(row['shift'], 'shift', signed=True)})"
    )


def _unavailable_line(reason: Any) -> str:
    words = UNAVAILABLE_WORDS[reason] if reason in UNAVAILABLE_WORDS else _string(reason, "unavailable")
    return f"Projection unavailable: {words}"


def render(word: str, body: Any, status: Any = None) -> list[str]:
    """The lines for a ``PaceTarget`` body headed ``word`` (``Goal`` or ``Preview``), or raise
    ``KeyError``/``TypeError``/``ValueError`` (or ``ZoneInfoNotFoundError``/``OverflowError``/``OSError``)
    on a body that cannot be read."""
    if not isinstance(body, dict):
        raise ValueError("the body is not an object")
    zone = ZoneInfo(_string(body["timezone"], "timezone"))
    provenance = body["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError("provenance is not an object")
    races = provenance["races"]
    if not isinstance(races, list) or not all(isinstance(r, dict) for r in races):
        raise ValueError("races is not a list of objects")
    assumptions = body["assumptions"]
    if not isinstance(assumptions, list):
        raise ValueError("assumptions is not a list")

    lines = [_head(word, body, status)]
    unavailable = body["unavailable"]
    if unavailable is None:
        route = body["route"]
        if route not in ROUTE_WORDS:
            raise ValueError("route is not fitted_line or closest_race")
        lines.append(_projected_line(body, route))
        lines.append(_confidence_line(body["confidence"]))
        lines.append(_gap_line(body, route))
        lines.append(f"Route: {ROUTE_WORDS[route]}")
    else:
        route = None
        lines.append(_unavailable_line(unavailable))
    lines.append(_b_line(provenance))

    newest = provenance["newest_race"]
    if newest is None:
        lines.append("Newest race: none")
    else:
        if not isinstance(newest, dict):
            raise ValueError("newest_race is not an object")
        lines.append(f"Newest race {_dated_name(newest['start_time'], newest['name'], zone)}")
    lines.append(
        f"Fitness now {_whole(provenance['fitness_now'], 'fitness_now')}, "
        f"at the races {_whole(provenance['fitness_at_races'], 'fitness_at_races')}"
    )

    without_by_id: dict[Any, dict] = {}
    leave_one_out = body["leave_one_out"]
    if route == FITTED_LINE:
        if not isinstance(leave_one_out, dict) or not isinstance(leave_one_out["races"], list):
            raise ValueError("a fitted line without its leave-one-out")
        for row in leave_one_out["races"]:
            if not isinstance(row, dict):
                raise ValueError("a leave-one-out row is not an object")
            without_by_id[row["session_id"]] = row
    for race in races:
        lines.append(_race_line(race, without_by_id.get(race["session_id"]), zone))
    if route == FITTED_LINE:
        largest = leave_one_out["largest"]
        if largest is None:
            lines.append(NO_LARGEST_LINE)
        else:
            if not isinstance(largest, dict):
                raise ValueError("largest is not an object")
            [race] = [r for r in races if r["session_id"] == largest["session_id"]] or [None]
            if race is None:
                raise ValueError("largest names a race not used")
            lines.append(
                f"{LARGEST_WORDS}: {_dated_name(race['start_time'], race['name'], zone)}, without it {_without(largest)}"
            )

    words = [ASSUMPTION_WORDS[a] if a in ASSUMPTION_WORDS else _string(a, "assumption") for a in assumptions]
    lines.append(f"Assumes: {', '.join(words)}")
    return lines


# ---------------------------------------------------------------------------
# The commands.
# ---------------------------------------------------------------------------


def _request(cfg: config.Config, request) -> httpx.Response:
    try:
        return request()
    except ApiUnreachableError:
        raise _fail(f"could not reach API at {cfg.api_url}. Is it running? Start it with `runcoach-api`.") from None


def _print(resp: httpx.Response, word: str, contract: bool) -> None:
    """Print the lines of a 200 body: a ``GoalContract`` (its ``target`` and ``status``) when
    ``contract``, else a ``PaceTarget``; any other status is an error, exit 1."""
    if resp.status_code != 200:
        raise _fail(f"API returned {resp.status_code}: {_printable(resp.text[:ERROR_TEXT_CHARS])}")
    try:
        body = resp.json()
        if contract:
            if not isinstance(body, dict):
                raise ValueError("the body is not an object")
            lines = render(word, body["target"], body["status"])
        else:
            lines = render(word, body)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ZoneInfoNotFoundError, OverflowError, OSError) as exc:
        raise _fail(f"the API's response could not be understood: {_printable(str(exc))}") from None
    for line in lines:
        _write_utf8(line)


def _no_goal(resp: httpx.Response) -> bool:
    """A 404 whose ``detail`` is ``no_goal``: no active goal, not a missing route."""
    if resp.status_code != 404:
        return False
    try:
        body = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(body, dict) and body.get("detail") == NO_GOAL_DETAIL


@goal_app.callback()
def goal(
    ctx: typer.Context,
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="The date (YYYY-MM-DD, in the athlete's timezone) or date-time with zone to project at; default now.",
    ),
) -> None:
    """Show the goal race and its projected finish from your tagged races.

    Without a subcommand, prints the active goal (GET /goal): the projected time and pace per km
    with the 80 % range and its label, the goal time's gap, the route, b, the newest race, fitness
    now and at the races, each race used with what the projection reads without it, and the
    assumptions. With no goal set, says so and exits 0.
    """
    if ctx.invoked_subcommand is not None:
        if as_of is not None:
            # A subcommand never reads this option: dropped, a projection for now would print as
            # if it were the backtest asked for.
            raise typer.BadParameter(
                f"--as-of before {ctx.invoked_subcommand} is not read; for a backtest give it after the "
                "distance: runcoach goal preview <distance> --as-of DATE (set always uses today)",
                param_hint="--as-of",
            )
        return
    cfg = config.load_config()
    resp = _request(cfg, lambda: get_goal(cfg.api_url, as_of))
    if _no_goal(resp):
        _write_utf8(NO_GOAL_LINE)
        return
    _print(resp, "Goal", contract=True)


@goal_app.command(name="set")
def set_goal(
    distance: str = typer.Argument(..., help=DISTANCE_HELP),
    date: str = typer.Option(..., "--date", help="The race day, YYYY-MM-DD, today or later."),
    time: str | None = typer.Option(None, "--time", help="The goal finish time, H:MM:SS or MM:SS."),
    name: str | None = typer.Option(None, "--name", help="The race's name."),
) -> None:
    """Set the goal race (PUT /goal) and print it with its projected finish."""
    metres = parse_distance(distance)
    seconds = None if time is None else parse_time(time)
    body: dict[str, Any] = {"race": {"date": date, "distance_m": metres, "name": name}}
    if seconds is not None:
        body["target_time_s"] = seconds
    cfg = config.load_config()
    _print(_request(cfg, lambda: put_goal(cfg.api_url, body)), "Goal", contract=True)


@goal_app.command(name="preview")
def preview(
    distance: str = typer.Argument(..., help=DISTANCE_HELP),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="The date (YYYY-MM-DD, in the athlete's timezone) or date-time with zone to project at; default now.",
    ),
) -> None:
    """Project a finish over a distance without storing a goal (POST /goal/preview)."""
    metres = parse_distance(distance)
    body = {"race": {"distance_m": metres}}
    cfg = config.load_config()
    _print(_request(cfg, lambda: post_goal_preview(cfg.api_url, body, as_of)), "Preview", contract=False)
