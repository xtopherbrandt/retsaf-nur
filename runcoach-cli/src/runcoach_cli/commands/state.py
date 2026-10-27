"""``runcoach state`` -- threshold pace and the endurance exponent from ``GET /state``, with the races
and workouts used and excluded.

The first line is the estimate: ``Threshold pace 4:22/km (80 %: 4:15-4:30), high, from 2 races and 1
workout, the last on 2026-09-30``, the interval fast end first and a zero count left out. Then one
line per race used (date, duration, GAP pace, the gain it entered with, its degraded-GPS percentage
and its weight) and one per threshold workout used (the same, with what admitted it after the date
and its counted time and blocks in place of the duration), then one per race or workout excluded
(date, a workout's admission, and the reason in words). When every race used lasted under 30
minutes the API sets ``short_races_only`` and a warning line follows the estimate.

A workout's admission is the served ``admitted_by`` in words: ``(tag)`` for one tagged Training in
Connect, ``(name Progression Run)`` for one whose workout name is listed in the API's
``threshold_workout_names``, ``(tag, name Goal Pace Run)`` for both, the name printed only beside
the ``name`` admitter. The workout minimums print as ``m:ss`` from the served parameters: a work rep
counts from ``workout_min_rep_s`` (4:50) and the counted reps must sum to ``workout_min_active_s``
(9:50).

An effort's weight is its share of today's estimate, computed here from the served gains: the
estimate is ``sum_i w_i * x_i`` with ``w_i = K_i * prod_{j>i}(1 - K_j)``, which sums to 1 because
the first gain is 1. Each effort's ``kind`` is read as ``race`` when absent, so an API from before
threshold workouts still renders; an absent, null or empty ``admitted_by`` prints no parenthesis,
so an API from before the admission fields renders too.

With no qualifying race or workout the first line says how to tag one; ``<its date>`` in it is
printed as is, for the athlete to fill in. When the API serves ``estimate_not_finite`` (an effort's
GAP pace so far out of range that the estimate overflows), the first line says so and the efforts
used are still listed, so the athlete can find the one. Both exit 0.

After the threshold lines comes the endurance exponent block, from the ``riegel_exponent`` entry
(F029 reference section 6). A fitted b prints to three decimals with its 80 % interval, its label,
the race count, the window and the shortest and longest GAP-equivalent distance used: ``Endurance
exponent b 1.060 (80 %: 1.035 to 1.084), high, from 8 races in the last 1095 days, 5.0 to 42.5 km``.
Below the bar the population value prints with why (too few races, or every race at one distance),
the number the fitness screen excluded when any were, and a line on how to tag a race; a fitted b
under 1 adds a warning line. Then one ``Race in b`` line per race used (distance, GAP-equivalent
distance, time, the day-before fitness and its change over the four weeks in words, ``fell 3 %``,
``rose 5 %`` or ``no change``, the weight, and the residual against the line when one is served)
and one ``Race left out of b`` line per race excluded with its reason in words. The labels differ
from the threshold block's ``Race used`` and ``Race excluded``, so a race in both blocks is not
printed twice under one label with different figures.
An entry with ``estimate_not_finite`` says so and still lists the races. A body with no
``riegel_exponent`` entry (an API from before it) ends with a line saying so, and exits 0.

Dates print in the response's ``timezone`` (the API's ``athlete_timezone``), not the computer's.
Paces and durations are rounded half up, as ``runcoach sessions`` rounds them. ``--as-of`` is sent
as given; the API reads and refuses it. An unreachable API, a status other than 200 and a body that
cannot be read each print an error and exit 1, as ``runcoach sessions`` does.

Served text printed as served (a workout name, an unknown reason, an unknown unavailable value, a
non-200 body cut to 200 characters, and the text of a body that cannot be read) passes through
``sessions._printable``, so a control character prints as ``?`` and cannot start a second line or
reach the terminal as an escape sequence.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, get_state
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _half_up_seconds, _printable, _write_utf8, format_duration, format_pace

DETERMINANT = "functional_threshold_pace"

# A non-200 body is printed cut to this many characters, as ``runcoach sync`` cuts one.
ERROR_TEXT_CHARS = 200

RACE = "race"
WORKOUT = "threshold_workout"
KIND_WORDS = {RACE: "Race", WORKOUT: "Workout"}

# A threshold workout's least counted time and least work-rep length when the body's ``parameters``
# carry no ``workout_min_active_s`` or ``workout_min_rep_s``. An API from before the rep rule
# served no rep minimum and summed every active lap against a 600 s minimum; one from before the
# parameters block held that same minimum. Each is the figure such an API applied, so the words
# describe the rule that excluded the workout, not today's.
DEFAULT_WORKOUT_MIN_ACTIVE_S = 600
DEFAULT_WORKOUT_MIN_REP_S = 290

# What admitted a workout, as ``GET /state`` serves ``admitted_by``, in the words printed after
# the date. ``name`` carries the served ``workout_name`` after it.
ADMITTED_BY_TAG = "tag"
ADMITTED_BY_NAME = "name"
ADMITTERS = (ADMITTED_BY_TAG, ADMITTED_BY_NAME)

# Each exclusion reason the API serves, in words. A reason not listed prints as served. ``{date}``
# is the excluded effort's date, ``{minimum}`` the served least counted time of a workout and
# ``{rep}`` the served least work-rep length, both as ``m:ss``.
#
# ``smart_recording`` names both modes: the ingest sets that one flag for a ``smart`` and an
# ``irregular`` recording-interval verdict alike (every verdict but ``1hz``), and ``GET /state``
# serves the flag, not the verdict, so nothing here can tell the two apart.
REASON_WORDS = {
    "outside_threshold_band": "not 20 to 60 minutes long",
    "distance_regressed": "its distance went backwards in the recording",
    "smart_recording": "recorded in smart or irregular recording mode",
    "gap_pace_unavailable": "no grade-adjusted pace",
    "summary_unreadable": "its stored summary could not be read",
    "quality_flags_unreadable": "its stored quality flags could not be read",
    "features_unavailable": "its pace features could not be computed",
    "laps_not_read": "its laps have not been read: run runcoach sync laps --since {date}",
    "lap_times_unreadable": "its stored lap times could not be read",
    "no_workout_steps": "no lap of a work step",
    "no_sustained_work": "no work rep of {rep} or longer",
    "active_time_below_minimum": "under {minimum} of counted work reps",
    "laps_unreadable": "its stored laps could not be read",
}

UNAVAILABLE_LINES = {
    "no_qualifying_effort": (
        "Threshold pace unavailable: no tagged race of 20 to 60 minutes and no threshold workout with "
        "{minimum} of counted work reps. Mark a race or time trial as Race in Garmin Connect, or a threshold "
        "workout as Training or list its workout name in the API's threshold_workout_names and restart the "
        "API, then run runcoach sync tags --since <its date> (and, for a workout, runcoach sync laps --since "
        "<its date>)."
    ),
    "estimate_not_finite": (
        "Threshold pace unavailable: a race's or workout's grade-adjusted pace is out of range, so the "
        "estimate could not be computed. Check the races and workouts used below."
    ),
}

SHORT_RACES_LINE = (
    "All races used are under 30 minutes: threshold likely reads fast; a 10K race or time trial would correct it."
)

RIEGEL_DETERMINANT = "riegel_exponent"

# Each reason a race is left out of b, in words (F029 reference section 2). ``fitness_dropped``
# and ``outside_window`` carry figures and are worded where the line is built; a reason not listed
# prints as served.
RIEGEL_REASON_WORDS = {
    "admitted_as_workout": "its workout name is listed in threshold_workout_names, so it is read as a workout",
    "summary_unreadable": REASON_WORDS["summary_unreadable"],
    "duration_unavailable": "no timer or summary duration",
    "quality_flags_unreadable": REASON_WORDS["quality_flags_unreadable"],
    "distance_regressed": REASON_WORDS["distance_regressed"],
    "smart_recording": REASON_WORDS["smart_recording"],
    "features_unavailable": REASON_WORDS["features_unavailable"],
    "grade_clamped": "a grade was outside the adjustment's range",
    "gap_pace_unavailable": REASON_WORDS["gap_pace_unavailable"],
}

RIEGEL_TAGGING_LINE = "Mark a race as Race in Garmin Connect and run runcoach sync tags --since <its date>"
RIEGEL_BELOW_ONE_LINE = (
    "b reads below 1: a race used was probably not all-out, or a trail race; check the races listed"
)
RIEGEL_NOT_FINITE_LINE = "Endurance exponent unavailable: a race's figures are out of range; check the races listed"
RIEGEL_NOT_SERVED_LINE = "Endurance exponent: not served by this API; update it and restart"


def _number(value: Any, what: str) -> float:
    """``value`` as a finite float; a bool, a string, a null or a non-finite number is malformed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} is not a number")
    return float(value)


def _mss(seconds: float) -> str:
    """``m:ss`` from seconds rounded half up, without a unit: a pace in seconds per km or a length
    of time."""
    minutes, secs = divmod(_half_up_seconds(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _date(start_time: Any, zone: ZoneInfo) -> str:
    """The ``YYYY-MM-DD`` of a served instant in ``zone``."""
    when = datetime.fromisoformat(start_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone).strftime("%Y-%m-%d")


def _gain(value: Any) -> str:
    return f"{_number(value, 'gain'):.2f}"


def _degraded(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_number(value, 'gps_degraded_fraction') * 100:.1f} %"


def _gap_pace(value: Any) -> str:
    """A race's GAP pace; one too large to print as minutes is printed in seconds per km."""
    pace = _number(value, "gap_pace_s_per_km")
    if pace >= 1e9:
        return f"{pace:g} s/km"
    return format_pace(pace)


def _kind(row: dict) -> str:
    """A served effort's or exclusion's ``kind``; ``race`` when absent (an API from before workouts)."""
    kind = row.get("kind", RACE)
    if kind not in KIND_WORDS:
        raise ValueError(f"kind {kind!r} is not race or threshold_workout")
    return kind


def _admission(row: dict) -> str:
    """A workout's admission after the date, ``" (tag, name Goal Pace Run)"`` from the served
    ``admitted_by`` and ``workout_name``; empty when ``admitted_by`` is absent, null or empty (an
    API from before the admission fields, or a race). The name is printed beside the ``name``
    admitter only, and only when it is a non-empty string."""
    admitted_by = row.get("admitted_by")
    if admitted_by is None or admitted_by == []:
        return ""
    if not isinstance(admitted_by, list) or any(a not in ADMITTERS for a in admitted_by):
        raise ValueError(f"admitted_by {admitted_by!r} is not a list of tag and name")
    name = row.get("workout_name")
    words = [
        f"{ADMITTED_BY_NAME} {_printable(name)}" if a == ADMITTED_BY_NAME and isinstance(name, str) and name else a
        for a in admitted_by
    ]
    return f" ({', '.join(words)})"


def weights(gains: list[float]) -> list[float]:
    """Each effort's share of the estimate, from the gains in start-time order:
    ``w_i = K_i * prod_{j>i}(1 - K_j)``. They sum to 1 when the first gain is 1."""
    out: list[float] = []
    later = 1.0
    for gain in reversed(gains):
        out.append(gain * later)
        later *= 1.0 - gain
    return out[::-1]


def _percent(weight: float) -> str:
    return f"{_half_up_seconds(weight * 100)} %"


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _estimate_line(estimate: dict, efforts: list[dict], zone: ZoneInfo) -> str:
    value = _number(estimate["value"], "value")
    low = _number(estimate["interval"]["low"], "interval.low")
    high = _number(estimate["interval"]["high"], "interval.high")
    label = estimate["confidence"]
    if label not in ("low", "medium", "high"):
        raise ValueError("confidence is not low, medium or high")
    if not efforts:
        raise ValueError("an estimate with no races or workouts used")
    kinds = [_kind(effort) for effort in efforts]
    parts = [
        _count(n, word)
        for n, word in ((kinds.count(RACE), "race"), (kinds.count(WORKOUT), "workout"))
        if n
    ]
    last = max(efforts, key=lambda e: datetime.fromisoformat(e["start_time"]))
    return (
        f"Threshold pace {format_pace(value)} (80 %: {_mss(low)}-{_mss(high)}), {label}, "
        f"from {' and '.join(parts)}, the last on {_date(last['start_time'], zone)}"
    )


def _effort_line(effort: dict, weight: float, zone: ZoneInfo) -> str:
    duration = _number(effort["duration_s"], "duration_s")
    tail = (
        f"GAP {_gap_pace(effort['gap_pace_s_per_km'])}, gain {_gain(effort['gain'])}, "
        f"degraded GPS {_degraded(effort['gps_degraded_fraction'])}, weight {_percent(weight)}"
    )
    day = _date(effort["start_time"], zone)
    if _kind(effort) == RACE:
        return f"Race used {day}: {format_duration(duration)}, {tail}"
    blocks = effort["active_blocks"]
    if isinstance(blocks, bool) or not isinstance(blocks, int):
        raise ValueError("active_blocks is not a whole number")
    return (
        f"Workout used {day}{_admission(effort)}: {format_duration(duration)} active in "
        f"{_count(blocks, 'block')}, {tail}"
    )


def _excluded_line(row: dict, minimums: dict[str, str], zone: ZoneInfo) -> str:
    reason = row["reason"]
    if not isinstance(reason, str):
        raise ValueError("reason is not a string")
    day = _date(row["start_time"], zone)
    kind = _kind(row)
    words = REASON_WORDS[reason].format(date=day, **minimums) if reason in REASON_WORDS else _printable(reason)
    admission = _admission(row) if kind == WORKOUT else ""
    return f"{KIND_WORDS[kind]} excluded {day}{admission}: {words}"


def _workout_minimums(parameters: Any) -> dict[str, str]:
    """The served ``workout_min_active_s`` (``minimum``) and ``workout_min_rep_s`` (``rep``) as
    ``m:ss``, each defaulted when an older API does not serve it."""
    served = parameters if isinstance(parameters, dict) else {}
    out = {}
    for word, key, default in (
        ("minimum", "workout_min_active_s", DEFAULT_WORKOUT_MIN_ACTIVE_S),
        ("rep", "workout_min_rep_s", DEFAULT_WORKOUT_MIN_REP_S),
    ):
        seconds = _number(served[key], key) if key in served else default
        out[word] = _mss(seconds)
    return out


def _flag(det: dict, key: str) -> bool:
    value = det[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} is not true or false")
    return value


def _b(value: Any, what: str, places: int = 3) -> str:
    return f"{_number(value, what):.{places}f}"


def _km(value: Any, what: str) -> str:
    """Metres as ``N.N km``; ``-`` for a null."""
    if value is None:
        return "-"
    return f"{_number(value, what) / 1000:.1f} km"


def _whole(value: Any, what: str) -> str:
    """A figure rounded half up to a whole number, a negative one included."""
    return str(math.floor(_number(value, what) + 0.5))


def _fitness_change(drop: Any) -> str:
    """A served ``fitness_drop`` (a fall positive) in words: ``fell 42 % over 4 weeks``, ``rose 5 %
    over 4 weeks``, or ``no change over 4 weeks`` when the percentage rounds to 0. The signed
    percentage is rounded half up to a whole number before its sign picks the word."""
    percent = math.floor(_number(drop, "fitness_drop") * 100 + 0.5)
    if percent == 0:
        return "no change over 4 weeks"
    return f"{'fell' if percent > 0 else 'rose'} {abs(percent)} % over 4 weeks"


def _fitness(effort: dict) -> str:
    """``fitness 36, fell 3 % over 4 weeks``: the day-before CTL and its change over the four weeks
    in words; ``(-)`` without the change, ``fitness -`` without the day-before figure."""
    if effort["fitness_before"] is None:
        return "fitness -"
    before = _whole(effort["fitness_before"], "fitness_before")
    drop = effort["fitness_drop"]
    if drop is None:
        return f"fitness {before} (-)"
    return f"fitness {before}, {_fitness_change(drop)}"


def _residual(value: Any) -> str:
    """A residual as a signed percentage to one decimal, rounded half up; one that rounds to zero
    prints ``0.0 %``, never ``-0.0 %``."""
    tenths = math.floor(_number(value, "residual") * 1000 + 0.5)
    return "0.0 %" if tenths == 0 else f"{tenths / 10:+.1f} %"


def _riegel_effort_line(effort: dict, zone: ZoneInfo) -> str:
    weight = _number(effort["weight"], "weight")
    line = (
        f"Race in b {_date(effort['start_time'], zone)}: {_km(effort['distance_m'], 'distance_m')} "
        f"({_km(effort['gap_equivalent_m'], 'gap_equivalent_m')} adjusted), "
        f"{format_duration(_number(effort['duration_s'], 'duration_s'))}, {_fitness(effort)}, weight {weight:.2f}"
    )
    if effort["residual"] is not None:
        line += f", {_residual(effort['residual'])} against the line"
    return line


def _riegel_excluded_line(row: dict, window: str, as_of: str, zone: ZoneInfo) -> str:
    reason = row["reason"]
    if not isinstance(reason, str):
        raise ValueError("reason is not a string")
    day = _date(row["start_time"], zone)
    if reason == "fitness_dropped":
        earlier = _whole(row["fitness_earlier"], "fitness_earlier")
        before = _whole(row["fitness_before"], "fitness_before")
        words = f"fitness {_fitness_change(row['fitness_drop'])} ({earlier} to {before})"
    elif reason == "outside_window":
        words = f"more than {window} days before {as_of}"
    elif reason in RIEGEL_REASON_WORDS:
        words = RIEGEL_REASON_WORDS[reason]
    else:
        words = _printable(reason)
    return f"Race left out of b {day}: {words}"


def _riegel_lines(det: dict, as_of: Any, zone: ZoneInfo) -> list[str]:
    """The endurance exponent block for a ``riegel_exponent`` entry (F029 reference section 6)."""
    efforts = det["efforts"]
    excluded = det["excluded"]
    if not isinstance(efforts, list) or not isinstance(excluded, list):
        raise ValueError("efforts or excluded is not a list")
    if not all(isinstance(row, dict) for row in [*efforts, *excluded]):
        raise ValueError("an entry of efforts or excluded is not an object")
    parameters = det["parameters"]
    if not isinstance(parameters, dict):
        raise ValueError("parameters is not an object")
    window = f"{_number(parameters['window_days'], 'window_days'):g}"
    population_default = _flag(det, "population_default")
    below_one = _flag(det, "below_one")

    estimate = det["estimate"]
    if estimate is not None:
        label = estimate["confidence"]
        if label not in ("low", "medium", "high"):
            raise ValueError("confidence is not low, medium or high")
        low = _b(estimate["interval"]["low"], "interval.low")
        high = _b(estimate["interval"]["high"], "interval.high")
        races = _count(len(efforts), "race")
        if population_default:
            min_races = _number(parameters["min_races"], "min_races")
            if len(efforts) < min_races:
                why = f"{races} in the last {window} days; {min_races:g} are needed"
            else:
                why = f"{races} at one distance; the longest must be at least twice the shortest"
            screened = sum(1 for row in excluded if row["reason"] == "fitness_dropped")
            if screened:
                why += f", {screened} excluded by the fitness screen"
            lines = [
                f"Endurance exponent b {_b(estimate['value'], 'value', 2)} (population default, 80 %: {low} to "
                f"{high}), {label}: {why}",
                RIEGEL_TAGGING_LINE,
            ]
        else:
            if not efforts:
                raise ValueError("an estimate of b with no races used")
            distances = [_number(e["gap_equivalent_m"], "gap_equivalent_m") / 1000 for e in efforts]
            lines = [
                f"Endurance exponent b {_b(estimate['value'], 'value')} (80 %: {low} to {high}), {label}, from "
                f"{races} in the last {window} days, {min(distances):.1f} to {max(distances):.1f} km"
            ]
        if below_one:
            lines.append(RIEGEL_BELOW_ONE_LINE)
    else:
        reason = det["unavailable"]
        if not isinstance(reason, str):
            raise ValueError("no estimate of b and no unavailable reason")
        if reason == "estimate_not_finite":
            lines = [RIEGEL_NOT_FINITE_LINE]
        else:
            lines = [f"Endurance exponent unavailable: {_printable(reason)}."]
    lines += [_riegel_effort_line(effort, zone) for effort in efforts]
    if excluded:
        as_of_day = _date(as_of, zone)
        lines += [_riegel_excluded_line(row, window, as_of_day, zone) for row in excluded]
    return lines


def render(body: Any) -> list[str]:
    """The lines for a ``GET /state`` body, or raise ``KeyError``/``TypeError``/``ValueError`` (or
    ``ZoneInfoNotFoundError``/``OverflowError``/``OSError``) on a body that cannot be read."""
    if not isinstance(body, dict):
        raise ValueError("the body is not an object")
    zone = ZoneInfo(body["timezone"])
    lines = _threshold_lines(body, zone)
    riegel = [d for d in body["determinants"] if isinstance(d, dict) and d.get("name") == RIEGEL_DETERMINANT]
    if riegel:
        lines += _riegel_lines(riegel[0], body["as_of"], zone)
    else:
        lines.append(RIEGEL_NOT_SERVED_LINE)
    return lines


def _threshold_lines(body: dict, zone: ZoneInfo) -> list[str]:
    """The threshold pace lines of a ``GET /state`` body."""
    found = [d for d in body["determinants"] if isinstance(d, dict) and d.get("name") == DETERMINANT]
    if not found:
        raise ValueError(f"no {DETERMINANT} determinant")
    det = found[0]
    efforts = det["efforts"]
    excluded = det["excluded"]
    if not isinstance(efforts, list) or not isinstance(excluded, list):
        raise ValueError("efforts or excluded is not a list")
    if not all(isinstance(row, dict) for row in [*efforts, *excluded]):
        raise ValueError("an entry of efforts or excluded is not an object")

    minimums = _workout_minimums(det.get("parameters"))
    estimate = det["estimate"]
    if estimate is not None:
        lines = [_estimate_line(estimate, efforts, zone)]
        if det["short_races_only"] is True:
            lines.append(SHORT_RACES_LINE)
    else:
        reason = det["unavailable"]
        if not isinstance(reason, str):
            raise ValueError("no estimate and no unavailable reason")
        if reason in UNAVAILABLE_LINES:
            lines = [UNAVAILABLE_LINES[reason].format(**minimums)]
        else:
            lines = [f"Threshold pace unavailable: {_printable(reason)}."]
    shares = weights([_number(effort["gain"], "gain") for effort in efforts])
    lines += [_effort_line(effort, share, zone) for effort, share in zip(efforts, shares, strict=True)]
    lines += [_excluded_line(row, minimums, zone) for row in excluded]
    return lines


def state(
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="The date (YYYY-MM-DD, in the athlete's timezone) or date-time with zone to estimate at; "
        "default now.",
    ),
) -> None:
    """Show threshold pace estimated from your tagged races and threshold workouts, with those used and
    excluded.

    A run is a threshold workout when it is tagged Training in Garmin Connect or its workout name is
    listed in the API's threshold_workout_names; each workout line says which after its date. Only
    its work reps count: a work step with a target, held for at least 4:50, within 10 % of the
    workout's fastest such rep; they must sum to at least 9:50. Warm-ups, recovery steps,
    cool-downs and shorter reps never enter the estimate.

    Each one used shows its weight: its share of today's estimate. The 80 % interval covers
    race-to-race scatter, not a biased input: a race or workout read wrongly moves the estimate and
    the interval together.

    Then the endurance exponent b, the slope of race time on distance, fitted to your tagged races of
    the last 1095 days (the population 1.06 until three races span a twofold distance), with each
    race used and excluded. A race run while your fitness had fallen more than 30 % in the four
    weeks before it is left out.

    This is the training state; for whether the API is running, see runcoach status.
    """
    cfg = config.load_config()
    try:
        resp: httpx.Response = get_state(cfg.api_url, as_of)
    except ApiUnreachableError:
        raise _fail(
            f"could not reach API at {cfg.api_url}. Is it running? Start it with `runcoach-api`."
        ) from None
    if resp.status_code != 200:
        raise _fail(f"API returned {resp.status_code}: {_printable(resp.text[:ERROR_TEXT_CHARS])}")
    try:
        lines = render(resp.json())
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ZoneInfoNotFoundError,
        OverflowError,
        OSError,
    ) as exc:
        raise _fail(f"the API's response could not be understood: {_printable(str(exc))}") from None
    for line in lines:
        _write_utf8(line)
