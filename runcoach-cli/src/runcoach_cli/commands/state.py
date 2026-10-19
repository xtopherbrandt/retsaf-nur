"""``runcoach state`` -- threshold pace from ``GET /state``, with the races and workouts used and excluded.

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

Dates print in the response's ``timezone`` (the API's ``athlete_timezone``), not the computer's.
Paces and durations are rounded half up, as ``runcoach sessions`` rounds them. ``--as-of`` is sent
as given; the API reads and refuses it. An unreachable API, a status other than 200 and a body that
cannot be read each print an error and exit 1, as ``runcoach sessions`` does.
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
from runcoach_cli.commands.sessions import _half_up_seconds, _write_utf8, format_duration, format_pace

DETERMINANT = "functional_threshold_pace"

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
    "active_time_below_minimum": "under {minimum} of work reps",
    "laps_unreadable": "its stored laps could not be read",
}

UNAVAILABLE_LINES = {
    "no_qualifying_effort": (
        "Threshold pace unavailable: no tagged race of 20 to 60 minutes and no threshold workout with "
        "{minimum} of work reps. Mark a race or time trial as Race in Garmin Connect, or a threshold workout "
        "as Training or list its workout name in the API's threshold_workout_names, then run "
        "runcoach sync tags --since <its date> (and, for a workout, runcoach sync laps --since <its date>)."
    ),
    "estimate_not_finite": (
        "Threshold pace unavailable: a race's or workout's grade-adjusted pace is out of range, so the "
        "estimate could not be computed. Check the races and workouts used below."
    ),
}

SHORT_RACES_LINE = (
    "All races used are under 30 minutes: threshold likely reads fast; a 10K race or time trial would correct it."
)


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
        f"{ADMITTED_BY_NAME} {name}" if a == ADMITTED_BY_NAME and isinstance(name, str) and name else a
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
    words = REASON_WORDS[reason].format(date=day, **minimums) if reason in REASON_WORDS else reason
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


def render(body: Any) -> list[str]:
    """The lines for a ``GET /state`` body, or raise ``KeyError``/``TypeError``/``ValueError`` (or
    ``ZoneInfoNotFoundError``/``OverflowError``/``OSError``) on a body that cannot be read."""
    if not isinstance(body, dict):
        raise ValueError("the body is not an object")
    zone = ZoneInfo(body["timezone"])
    found = [d for d in body["determinants"] if isinstance(d, dict) and d.get("name") == DETERMINANT]
    if not found:
        raise ValueError(f"no {DETERMINANT} determinant")
    det = found[0]
    efforts = det["efforts"]
    excluded = det["excluded"]
    if not isinstance(efforts, list) or not isinstance(excluded, list):
        raise ValueError("efforts or excluded is not a list")

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
            lines = [f"Threshold pace unavailable: {reason}."]
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
    workout's fastest such rep; they must sum to at least 9:50. Warm-ups, recoveries, cool-downs
    and shorter reps never enter the estimate.

    Each one used shows its weight: its share of today's estimate. The 80 % interval covers
    race-to-race scatter, not a biased input: a race or workout read wrongly moves the estimate and
    the interval together.

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
        raise _fail(f"API returned {resp.status_code}: {resp.text}")
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
        raise _fail(f"the API's response could not be understood: {exc}") from None
    for line in lines:
        _write_utf8(line)
