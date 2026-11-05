"""``runcoach state``: the critical speed block after the endurance exponent block.

Every test runs the real command against ``install_mock_client``'s fake API, with bodies shaped as
the API's ``CriticalSpeedDeterminant`` serves them (``as_of`` and start times in UTC with a ``Z``).
The threshold entry is the unavailable one and no ``riegel_exponent`` entry is served, so the first
two lines are the threshold line and the endurance exponent's older-API line, and the critical
speed block starts at line 2. The lines compared are F033 reference section 6's and F034 reference
section 4's, written from their text; the figures are F033 reference section 5's worked examples
(as of 2026-10-31, served by F033 and refused by F034's 4 s/km far-off cut, and 2025-01-31 with
its 720 s stretch dropped), the paces worked by hand from the distances there.
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

RIEGEL_OLDER_API_LINE = "Endurance exponent: not served by this API; update it and restart"
CS_OLDER_API_LINE = "Critical speed: not served by this API; update it and restart"
CS_LOW_LINE = (
    "No short race among the stretches kept: critical speed likely reads slow, as training stretches are "
    "rarely all-out; one short race (30 minutes or less, tagged Race, with its stretch kept) would raise "
    "the label."
)
# F034 reference section 4: printed after the CS line when ``label_held_at_medium`` is true.
CS_HELD_LINE = (
    "Two or more short races are kept, but none fills a 2-5 minute stretch: held at medium, as a training "
    "short end can read critical speed 1-2 s/km fast; a short race kept at a 2-5 minute stretch (a mile, "
    "1500 m or 3K race is the likeliest) would lift it."
)


def _slower_than_threshold(n: str) -> str:
    return f"Critical speed normally reads a little faster than threshold pace; here it reads {n} s/km slower."


def _threshold_at(value: float) -> dict[str, Any]:
    """A served threshold entry with an estimate of ``value`` s/km from one race."""
    return {
        **copy.deepcopy(THRESHOLD_UNAVAILABLE),
        "estimate": {
            "value": value,
            "unit": "s/km",
            "interval": {"low": value - 6.0, "high": value + 6.0},
            "confidence": "high",
            "provisional": False,
        },
        "unavailable": None,
        "error": 0.02,
        "efforts": [
            {
                "session_id": "s-race",
                "start_time": "2026-09-13T12:00:00Z",
                "kind": "race",
                "duration_s": 2500.0,
                "gap_pace_s_per_km": value,
                "gap_coverage": 1.0,
                "gps_degraded_fraction": 0.0,
                "gain": 1.0,
                "active_blocks": None,
                "admitted_by": None,
                "workout_name": None,
            }
        ],
    }

PARAMETERS = {
    "window_days": 90,
    "stretch_lengths_s": [120, 180, 300, 480, 720, 1200],
    "pause_break_s": 10,
    "population_b": 1.06,
    "far_off_s_per_km": 4,
    "min_stretches": 3,
    "max_shortest_s": 300,
    "min_longest_s": 720,
    "short_race_max_s": 1800,
    "coverage": 0.8,
}


def _stretch(
    length_s: int,
    distance_m: float | None,
    start: str,
    name: str | None,
    tag: str | None = None,
    drop_reason: str | None = None,
    beaten_by_s: int | None = None,
    duration_s: float | None = 3600.0,
) -> dict[str, Any]:
    """A stretch as ``CriticalSpeedStretch`` serves it; the pace from the distance."""
    pace = 1000 * length_s / distance_m if distance_m else None
    return {
        "length_s": length_s,
        "distance_m": distance_m,
        "pace_s_per_km": pace,
        "session_id": f"s-{length_s}",
        "start_time": start,
        "activity_tag": tag,
        "name": name,
        "duration_s": duration_s,
        "drop_reason": drop_reason,
        "beaten_by_s": beaten_by_s,
    }


def _excluded(start: str, reason: str, source: str | None = None, session_id: str = "x") -> dict[str, Any]:
    return {"session_id": session_id, "start_time": start, "reason": reason, "source": source}


# F033 reference section 5 as of 2026-10-31: six stretches, all kept, no short race.
TODAY = [
    _stretch(120, 523.5537014471311, "2026-10-07T12:00:00Z", None, "threshold_workout"),
    _stretch(180, 784.548392069948, "2026-10-16T12:00:00Z", "Speed Repeats", "threshold_workout"),
    _stretch(300, 1243.5530061433337, "2026-09-23T12:00:00Z", None),
    _stretch(480, 1973.681523449879, "2026-10-22T12:00:00Z", "Progression Run"),
    _stretch(720, 2905.6868789866103, "2026-08-20T12:00:00Z", "Run"),
    _stretch(1200, 4526.608531191341, "2026-09-30T12:00:00Z", None, "threshold_workout"),
]

# Section 5, 2025-01-31: the 720 s stretch (4:14/km) dropped, beaten by the 1200 s one.
JAN_2025 = [
    _stretch(120, 546.3600050967008, "2025-01-08T12:00:00Z", "Run"),
    _stretch(180, 814.32400631371, "2025-01-16T12:00:00Z", "Run"),
    _stretch(300, 1298.6926653138062, "2025-01-29T12:00:00Z", "Run"),
    _stretch(480, 2061.841202898636, "2024-12-03T12:00:00Z", "Run"),
    _stretch(720, 2831.3273836002936, "2024-11-03T12:00:00Z", "Run", drop_reason="beaten_by_longer", beaten_by_s=1200),
    _stretch(1200, 5002.6202330825545, "2024-11-09T12:00:00Z", "5K", "race", duration_s=1206.0),
]


def _cs(
    stretches: list[dict[str, Any]] | None = None,
    value: float = 268.95250569663716,
    low: float = 260.5193987146589,
    high: float = 277.94984243407475,
    confidence: str = "low",
    d_prime: tuple[float, float, float] = (133.87437866999107, 58.94298122682967, 208.8057761131525),
    excluded: list[dict[str, Any]] | None = None,
    held: bool = False,
) -> dict[str, Any]:
    """A served entry with an estimate; F033 section 5's 2026-10-31 figures unless given."""
    return {
        "name": "critical_speed",
        "estimate": {
            "value": value,
            "unit": "s/km",
            "interval": {"low": low, "high": high},
            "confidence": confidence,
            "provisional": False,
        },
        "unavailable": None,
        "trend": {"direction": "unknown", "rate": None},
        "d_prime": {"value_m": d_prime[0], "low_m": d_prime[1], "high_m": d_prime[2]},
        "d_prime_not_positive": d_prime[0] <= 0,
        "label_held_at_medium": held,
        "error_m_per_s": 0.07850030789562032,
        "t_multiplier": 1.533206274059,
        "far_off_excess_s_per_km": 10.03983535916143,
        "stretches": copy.deepcopy(TODAY if stretches is None else stretches),
        "excluded": excluded or [],
        "parameters": dict(PARAMETERS),
    }


def _unavailable(reason: str, stretches: list[dict[str, Any]], excess: float | None = None, **kw: Any) -> dict[str, Any]:
    entry = _cs(stretches, **kw)
    entry.update(
        estimate=None,
        unavailable=reason,
        d_prime=None,
        d_prime_not_positive=None,
        label_held_at_medium=None,
        error_m_per_s=None,
        t_multiplier=None,
        far_off_excess_s_per_km=excess,
    )
    return entry


def _body(cs: dict[str, Any] | None, timezone: str = "UTC", as_of: str = "2026-10-31T08:00:00Z") -> dict[str, Any]:
    determinants: list[Any] = [copy.deepcopy(THRESHOLD_UNAVAILABLE)]
    if cs is not None:
        determinants.append(copy.deepcopy(cs))
    return {"as_of": as_of, "timezone": timezone, "determinants": determinants, "readiness": None, "gap_to_goal": None}


def _serve(install_mock_client, body: Any, seen: list[httpx.Request] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.url.path == "/state"
        return httpx.Response(200, json=body)

    install_mock_client(handler)


def _cs_lines(install_mock_client, body: Any, *args: str) -> list[str]:
    """The lines after the threshold line and the endurance exponent's older-API line; exit 0."""
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state", *args])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Threshold pace unavailable: "), result.stdout
    assert lines[1] == RIEGEL_OLDER_API_LINE, result.stdout
    return lines[2:]


def _refused(install_mock_client, body: Any) -> None:
    """The command's error path: exit 1, nothing on stdout, no traceback."""
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "the API's response could not be understood" in result.stderr
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


def test_cs_line_prints_pace_interval_and_label(install_mock_client) -> None:
    """F033 section 5's 2026-10-31 fit: CS 268.95 s/km, 80 % 260.52 to 277.95 s/km, each rounded half up to m:ss
    (4:29, 4:20.52 reads 4:21, 4:37.95 reads 4:38), the fast end first, then the label. 2025-01-31's
    242.85 s/km, 241.25 to 244.46, is ``medium``."""
    lines = _cs_lines(install_mock_client, _body(_cs()))
    assert lines[0] == "Critical speed 4:29/km (80 %: 4:21 to 4:38), low", "\n".join(lines)
    # The CS line, the low fit's direction line, D' and six stretches.
    assert len(lines) == 3 + 6, "\n".join(lines)

    jan = _cs(JAN_2025, 242.8470975270634, 241.25141196259918, 244.46403200937243, "medium")
    assert _cs_lines(install_mock_client, _body(jan))[0] == "Critical speed 4:03/km (80 %: 4:01 to 4:04), medium"

    # A pace exactly on a half second rounds up, as every pace here does.
    high = _cs(value=242.5, low=241.5, high=243.49, confidence="high")
    assert _cs_lines(install_mock_client, _body(high))[0] == "Critical speed 4:03/km (80 %: 4:02 to 4:03), high"


def test_d_prime_prints_with_its_interval(install_mock_client) -> None:
    """F033 section 5's 2026-10-31 fit: D' 133.87 m, 80 % 58.94 to 208.81 m, to whole metres. D' at or below 0 is
    served as computed with ``d_prime_not_positive``, and the line says so; a signed figure rounds
    half away from zero (-12.5 reads -13, 15.5 reads 16) and one that rounds to zero reads 0, never
    -0. These fits are ``low``, so D' follows the CS line and its direction line."""
    lines = _cs_lines(install_mock_client, _body(_cs()))
    assert lines[2] == "D' 134 m (80 %: 59 to 209 m)", "\n".join(lines)

    negative = _cs(d_prime=(-12.5, -40.5, 15.5))
    assert _cs_lines(install_mock_client, _body(negative))[2] == (
        "D' -13 m (80 %: -41 to 16 m), at or below 0: every kept stretch ran at the same average speed to rounding"
    )

    zero = _cs(d_prime=(-0.4, -0.5, 0.5))
    assert _cs_lines(install_mock_client, _body(zero))[2] == (
        "D' 0 m (80 %: -1 to 1 m), at or below 0: every kept stretch ran at the same average speed to rounding"
    )

    # The note follows the served flag, not the sign: a positive D' served without it prints none.
    positive = _cs_lines(install_mock_client, _body(_cs(d_prime=(0.4, -1.0, 2.0))))[2]
    assert positive.startswith("D' 0 m") and "at or below 0" not in positive


def test_one_line_per_stretch_kept_or_dropped_in_words(install_mock_client) -> None:
    """One line per stretch served, shortest first: length and pace as m:ss, the run's local date
    and name (``unnamed`` when it has none), and ``kept`` or why it was dropped. 2025-01-31's 720 s
    stretch, 2831.33 m (254.29 s/km, 4:14), was beaten by the 1200 s one."""
    lines = _cs_lines(install_mock_client, _body(_cs()))
    assert lines[3:] == [
        "Stretch 2:00 at 3:49/km, 2026-10-07 unnamed: kept",
        "Stretch 3:00 at 3:49/km, 2026-10-16 Speed Repeats: kept",
        "Stretch 5:00 at 4:01/km, 2026-09-23 unnamed: kept",
        "Stretch 8:00 at 4:03/km, 2026-10-22 Progression Run: kept",
        "Stretch 12:00 at 4:08/km, 2026-08-20 Run: kept",
        "Stretch 20:00 at 4:25/km, 2026-09-30 unnamed: kept",
    ], "\n".join(lines)

    jan = _cs(JAN_2025, 242.8470975270634, 241.25141196259918, 244.46403200937243, "medium")
    lines = _cs_lines(install_mock_client, _body(jan))
    assert lines[2:] == [
        "Stretch 2:00 at 3:40/km, 2025-01-08 Run: kept",
        "Stretch 3:00 at 3:41/km, 2025-01-16 Run: kept",
        "Stretch 5:00 at 3:51/km, 2025-01-29 Run: kept",
        "Stretch 8:00 at 3:53/km, 2024-12-03 Run: kept",
        "Stretch 12:00 at 4:14/km, 2024-11-03 Run: dropped, a longer stretch was faster (20:00)",
        "Stretch 20:00 at 4:00/km, 2024-11-09 5K: kept",
    ], "\n".join(lines)

    # A null pace prints -, an empty name reads unnamed, a served name or a drop reason the CLI does
    # not know passes through _printable, so a control character cannot start a line.
    odd = [
        _stretch(120, None, "2026-10-07T12:00:00Z", ""),
        _stretch(180, 784.548392069948, "2026-10-16T12:00:00Z", "Run\nCritical speed 1:00/km", drop_reason="new\x1breason"),
    ]
    lines = _cs_lines(install_mock_client, _body(_unavailable("too_few_stretches", odd)))
    assert lines[1:] == [
        "Stretch 2:00 at -, 2026-10-07 unnamed: kept",
        "Stretch 3:00 at 3:49/km, 2026-10-16 Run?Critical speed 1:00/km: dropped, new?reason",
    ], "\n".join(lines)


def test_each_unavailable_reason_prints_in_words(install_mock_client) -> None:
    """Each of the five reasons of section 6 in words, the figures from the served parameters; the
    stretches still listed and no D' line. The far-off excess rounds half away from zero to one
    decimal (20.25 reads 20.3); one over the served cut of 4 that rounds to 4.0 reads ``just over 4``,
    never 4.0, and F033 reference section 5's 2026-10-31 excess, +10.04, reads 10.0."""
    two = TODAY[:2]
    cases = [
        (_unavailable("no_candidate_runs", []), "no run in the 90 days to 2026-10-31 could be read"),
        (_unavailable("too_few_stretches", two), "2 stretches kept; 3 are needed"),
        (_unavailable("too_few_stretches", two[:1]), "1 stretch kept; 3 are needed"),
        (
            _unavailable("spread_too_narrow", TODAY[2:5]),
            "the stretches kept do not span far enough: the shortest must be 5:00 or less and the longest "
            "12:00 or more",
        ),
        (
            _unavailable("longest_far_off", TODAY, 20.25),
            "the longest stretch kept is 20.3 s/km slower than the next-longest predicts, more than 4; the "
            "longest run below its best, or the next-longest read fast (for example a GPS over-read or an "
            "unmarked descent), can cause it",
        ),
        (
            _unavailable("longest_far_off", TODAY, 10.03983535916143),
            "the longest stretch kept is 10.0 s/km slower than the next-longest predicts, more than 4; the "
            "longest run below its best, or the next-longest read fast (for example a GPS over-read or an "
            "unmarked descent), can cause it",
        ),
        (
            _unavailable("longest_far_off", TODAY, 4.04),
            "the longest stretch kept is just over 4 s/km slower than the next-longest predicts, more than 4; "
            "the longest run below its best, or the next-longest read fast (for example a GPS over-read or an "
            "unmarked descent), can cause it",
        ),
        (
            _unavailable("estimate_not_finite", TODAY),
            "the fit's figures are out of range; check the stretches listed",
        ),
    ]
    for entry, words in cases:
        lines = _cs_lines(install_mock_client, _body(entry))
        assert lines[0] == f"Critical speed unavailable: {words}", "\n".join(lines)
        assert len(lines) == 1 + len(entry["stretches"]), "\n".join(lines)
        assert not any(line.startswith("D' ") for line in lines), "\n".join(lines)

    # A dropped stretch is not counted as kept.
    dropped = [*two, {**TODAY[2], "drop_reason": "beaten_by_longer", "beaten_by_s": 480}]
    assert _cs_lines(install_mock_client, _body(_unavailable("too_few_stretches", dropped)))[0] == (
        "Critical speed unavailable: 2 stretches kept; 3 are needed"
    )

    # An unavailable value the CLI does not know is printed as served, through ``_printable``.
    lines = _cs_lines(install_mock_client, _body(_unavailable("new\x1breason", TODAY)))
    assert lines[0] == "Critical speed unavailable: new?reason."


def test_an_older_api_prints_the_not_served_line(install_mock_client) -> None:
    """A body with no ``critical_speed`` entry (an API from before it) ends with the not-served line
    and exits 0; an entry that is not an object is not a ``critical_speed`` entry."""
    assert _cs_lines(install_mock_client, _body(None)) == [CS_OLDER_API_LINE]

    body = _body(None)
    body["determinants"].append("critical_speed")
    assert _cs_lines(install_mock_client, body) == [CS_OLDER_API_LINE]


def test_stretch_dates_are_in_the_response_timezone(install_mock_client) -> None:
    """22:30 UTC on 20 September is 21 September in Auckland and still 20 September in New York; an
    excluded session's date and the as-of date of ``no_candidate_runs`` move the same way."""
    stretch = _stretch(120, 523.5537014471311, "2026-09-20T22:30:00Z", "Run")
    excluded = [_excluded("2026-08-31T23:30:00Z", "smart_recording", "stored")]
    body = _body(_unavailable("too_few_stretches", [stretch]), "Pacific/Auckland", as_of="2026-10-31T20:00:00Z")
    body["determinants"][1]["excluded"] = excluded
    assert _cs_lines(install_mock_client, body)[1:] == [
        "Stretch 2:00 at 3:49/km, 2026-09-21 Run: kept",
        "Left out of critical speed 2026-09-01: recorded in smart or irregular recording mode, by its stored quality flags",
    ]

    body["timezone"] = "America/New_York"
    assert _cs_lines(install_mock_client, body)[1:] == [
        "Stretch 2:00 at 3:49/km, 2026-09-20 Run: kept",
        "Left out of critical speed 2026-08-31: recorded in smart or irregular recording mode, by its stored quality flags",
    ]

    none = _body(_unavailable("no_candidate_runs", []), "Pacific/Auckland", as_of="2026-10-31T20:00:00Z")
    assert _cs_lines(install_mock_client, none) == [
        "Critical speed unavailable: no run in the 90 days to 2026-11-01 could be read"
    ]


def test_as_of_passes_through_for_cs(install_mock_client) -> None:
    seen: list[httpx.Request] = []
    _serve(install_mock_client, _body(_cs()), seen=seen)
    result = runner.invoke(app, ["state", "--as-of", "2026-10-31"])
    assert result.exit_code == 0, result.output
    assert "Critical speed 4:29/km (80 %: 4:21 to 4:38), low" in result.stdout.splitlines()
    assert [r.url.params.get("as_of") for r in seen] == ["2026-10-31"]


def test_not_running_sessions_print_as_one_count_line(install_mock_client) -> None:
    """Each exclusion but ``not_running`` prints its own line with its reason in words, a flag
    reason saying whether the stored quality flags or the pace features raised it; the sessions that
    are not runs are counted on one line after them."""
    excluded = [
        _excluded("2026-08-03T12:00:00Z", "not_running"),
        _excluded("2026-08-04T12:00:00Z", "declared_capture"),
        _excluded("2026-08-05T12:00:00Z", "quality_flags_unreadable"),
        _excluded("2026-08-06T12:00:00Z", "distance_regressed", "stored"),
        _excluded("2026-08-07T12:00:00Z", "smart_recording", "stored"),
        _excluded("2026-08-08T12:00:00Z", "not_running"),
        _excluded("2026-08-09T12:00:00Z", "features_unavailable"),
        _excluded("2026-08-10T12:00:00Z", "distance_regressed", "f013"),
        _excluded("2026-08-11T12:00:00Z", "smart_recording", "f013"),
        _excluded("2026-08-12T12:00:00Z", "grade_clamped", "f013"),
        _excluded("2026-08-13T12:00:00Z", "new\x1breason"),
        _excluded("2026-08-14T12:00:00Z", "not_running"),
    ]
    lines = _cs_lines(install_mock_client, _body(_cs(excluded=excluded)))
    assert lines[9:] == [
        "Left out of critical speed 2026-08-04: a declared resting capture, not a run",
        "Left out of critical speed 2026-08-05: its stored quality flags could not be read",
        "Left out of critical speed 2026-08-06: its distance went backwards in the recording, by its stored quality flags",
        "Left out of critical speed 2026-08-07: recorded in smart or irregular recording mode, by its stored quality flags",
        "Left out of critical speed 2026-08-09: its pace features could not be computed",
        "Left out of critical speed 2026-08-10: its distance went backwards in the recording, by its pace features",
        "Left out of critical speed 2026-08-11: recorded in smart or irregular recording mode, by its pace features",
        "Left out of critical speed 2026-08-12: a grade was outside the adjustment's range, by its pace features",
        "Left out of critical speed 2026-08-13: new?reason",
        "3 other sessions in the window are not runs",
    ], "\n".join(lines)

    one = _cs_lines(install_mock_client, _body(_cs(excluded=excluded[:1])))
    assert one[9:] == ["1 other session in the window is not a run"]

    assert len(_cs_lines(install_mock_client, _body(_cs(excluded=excluded[1:2])))) == 10


def _from_cs(install_mock_client, body: Any) -> list[str]:
    """The lines from the critical speed line on, whatever the threshold block printed; exit 0."""
    _serve(install_mock_client, body)
    result = runner.invoke(app, ["state"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("Critical speed"))
    return lines[first:]


def test_a_low_fit_says_it_likely_reads_slow(install_mock_client) -> None:
    """A ``low`` fit (no short race among the stretches kept) prints one direction line after the
    CS line, as threshold pace's all-short-races line does; ``medium`` and ``high`` print none, and
    neither does an unavailable entry."""
    lines = _cs_lines(install_mock_client, _body(_cs()))
    assert lines[:3] == [
        "Critical speed 4:29/km (80 %: 4:21 to 4:38), low",
        CS_LOW_LINE,
        "D' 134 m (80 %: 59 to 209 m)",
    ], "\n".join(lines)

    for label in ("medium", "high"):
        lines = _cs_lines(install_mock_client, _body(_cs(confidence=label)))
        assert lines[0] == f"Critical speed 4:29/km (80 %: 4:21 to 4:38), {label}", "\n".join(lines)
        assert lines[1] == "D' 134 m (80 %: 59 to 209 m)", "\n".join(lines)
        assert CS_LOW_LINE not in lines

    assert CS_LOW_LINE not in _cs_lines(install_mock_client, _body(_unavailable("too_few_stretches", TODAY[:2])))


def test_cs_slower_than_threshold_pace_prints_a_note(install_mock_client) -> None:
    """With both served and CS pace slower than threshold pace, a note after the CS line (and after
    the low line, when there is one) says by how much, in whole s/km rounded half away from zero.
    F033's 2026-10-31 demo copy: CS 268.95 s/km against threshold 260.56 s/km reads 8 s/km slower,
    beside the low line. No note when CS is faster (5 s/km, and 0.5 s/km, which rounds to 1 faster), when the
    difference rounds to 0 (2025-01-31's 242.85 against 242.70), or when threshold pace is
    unavailable."""
    cs_today, threshold_today = 268.95250569663716, 260.56157884265895
    body = _body(_cs(value=cs_today))
    body["determinants"][0] = _threshold_at(threshold_today)
    assert _from_cs(install_mock_client, body)[:4] == [
        "Critical speed 4:29/km (80 %: 4:21 to 4:38), low",
        CS_LOW_LINE,
        _slower_than_threshold("8"),
        "D' 134 m (80 %: 59 to 209 m)",
    ]

    # Both absent: a high fit faster than threshold pace (F033's 2024-11-30 fit, 242.56 against
    # 242.70, served high before F034 held it at medium).
    body = _body(_cs(value=242.56172071408272, low=241.6051418736522, high=243.52590436267354, confidence="high"))
    body["determinants"][0] = _threshold_at(242.70097421862755)
    assert _from_cs(install_mock_client, body)[:2] == [
        "Critical speed 4:03/km (80 %: 4:02 to 4:04), high",
        "D' 134 m (80 %: 59 to 209 m)",
    ]

    # A medium fit 0.15 s/km slower rounds to 0: no note.
    body = _body(_cs(JAN_2025, 242.8470975270634, 241.25141196259918, 244.46403200937243, "medium"))
    body["determinants"][0] = _threshold_at(242.70097421862755)
    assert _from_cs(install_mock_client, body)[1].startswith("D' ")

    # Half a second rounds away from zero: 1.5 reads 2, 0.5 reads 1; a medium fit has no low line.
    for slower, words in ((1.5, "2"), (0.5, "1")):
        body = _body(_cs(value=260.0 + slower, confidence="medium"))
        body["determinants"][0] = _threshold_at(260.0)
        assert _from_cs(install_mock_client, body)[1] == _slower_than_threshold(words)

    # CS faster than threshold pace by 1 s/km or more after rounding: no note, whatever the size.
    for faster in (5.0, 0.5):
        cs = 260.0 - faster
        body = _body(_cs(value=cs, low=cs - 8.0, high=cs + 8.0, confidence="medium"))
        body["determinants"][0] = _threshold_at(260.0)
        lines = _from_cs(install_mock_client, body)
        assert lines[1] == "D' 134 m (80 %: 59 to 209 m)", "\n".join(lines)
        assert not any("threshold pace" in line for line in lines), "\n".join(lines)

    # Threshold pace unavailable: the low line only.
    lines = _cs_lines(install_mock_client, _body(_cs(value=cs_today)))
    assert lines[1:3] == [CS_LOW_LINE, "D' 134 m (80 %: 59 to 209 m)"]
    assert not any("threshold pace" in line for line in lines)


def test_help_names_the_two_direction_lines() -> None:
    """``runcoach state --help`` says a low fit likely reads slow and what would raise its label, and
    that a note prints when critical speed reads slower than threshold pace. Fragments are pinned."""
    result = runner.invoke(app, ["state", "--help"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.stdout.split())
    assert "likely reads slow" in text, text
    assert "one short race (30 minutes or less, tagged Race, with its stretch kept) would raise the label" in text, text
    assert "correct it" not in text.split("Last, critical speed")[1], text
    assert "slower than threshold pace" in text, text
    # F034: the label rule keeps "two or more high" and names the hold at medium.
    assert "two or more high." not in text, text
    assert "two or more high, unless none of them fills a stretch of 5 minutes or less: then medium" in text, text


def _held_body(confidence: str = "medium", held: bool | None = True, threshold: float | None = 242.70097421862755) -> dict[str, Any]:
    """F033's 2024-11-30 fit (CS 242.56 s/km, 80 % 241.61 to 243.53), served ``medium`` with
    ``label_held_at_medium`` as given (F034 reference section 5), with a threshold pace served at
    ``threshold`` s/km."""
    cs = _cs(value=242.56172071408272, low=241.6051418736522, high=243.52590436267354, confidence=confidence, held=bool(held))
    cs["label_held_at_medium"] = held
    body = _body(cs)
    if threshold is not None:
        body["determinants"][0] = _threshold_at(threshold)
    return body


def test_a_held_fit_prints_its_line_after_the_cs_line(install_mock_client) -> None:
    """A held fit prints F034 reference section 4's line right after the CS line, before the
    slower-than-threshold note and before the D' line. The 2024-11-30 fit served against a threshold
    3 s/km faster (239.56) prints all three in that order; against 2024-11-30's own threshold
    (242.70) CS is faster and no note prints."""
    lines = _from_cs(install_mock_client, _held_body(threshold=239.56))
    assert lines[:4] == [
        "Critical speed 4:03/km (80 %: 4:02 to 4:04), medium",
        CS_HELD_LINE,
        _slower_than_threshold("3"),
        "D' 134 m (80 %: 59 to 209 m)",
    ], "\n".join(lines)
    assert CS_LOW_LINE not in lines

    lines = _from_cs(install_mock_client, _held_body())
    assert lines[:3] == [
        "Critical speed 4:03/km (80 %: 4:02 to 4:04), medium",
        CS_HELD_LINE,
        "D' 134 m (80 %: 59 to 209 m)",
    ], "\n".join(lines)
    assert lines.count(CS_HELD_LINE) == 1


def test_an_unheld_fit_prints_no_held_line(install_mock_client) -> None:
    """``label_held_at_medium`` false: a ``medium`` or ``high`` fit prints the D' line straight after
    the CS line, and a ``low`` fit keeps F033's likely-reads-slow line. An unavailable entry (the key
    served null) prints none either."""
    for label in ("medium", "high"):
        lines = _from_cs(install_mock_client, _held_body(label, held=False))
        assert lines[:2] == [
            f"Critical speed 4:03/km (80 %: 4:02 to 4:04), {label}",
            "D' 134 m (80 %: 59 to 209 m)",
        ], "\n".join(lines)
        assert CS_HELD_LINE not in lines

    lines = _cs_lines(install_mock_client, _body(_cs()))
    assert lines[:3] == ["Critical speed 4:29/km (80 %: 4:21 to 4:38), low", CS_LOW_LINE, "D' 134 m (80 %: 59 to 209 m)"]
    assert CS_HELD_LINE not in lines

    lines = _cs_lines(install_mock_client, _body(_unavailable("longest_far_off", TODAY, 10.03983535916143)))
    assert CS_HELD_LINE not in lines and lines[0].startswith("Critical speed unavailable: ")


def test_an_older_api_prints_no_held_line(install_mock_client) -> None:
    """An API from before F034 serves no ``label_held_at_medium`` key: no held line, and every line
    is the one the same entry with the key false prints, for each label and for an unavailable
    entry."""
    entries = [
        _cs(value=242.56172071408272, low=241.6051418736522, high=243.52590436267354, confidence=label)
        for label in ("low", "medium", "high")
    ]
    entries.append(_unavailable("longest_far_off", TODAY, 10.03983535916143))
    for entry in entries:
        older = copy.deepcopy(entry)
        del older["label_held_at_medium"]
        printed = _cs_lines(install_mock_client, _body(older))
        assert printed == _cs_lines(install_mock_client, _body(entry)), "\n".join(printed)
        assert CS_HELD_LINE not in printed


@pytest.mark.parametrize(
    "breaks",
    [
        pytest.param(lambda c: c.update(stretches={"s": 1}), id="stretches_not_a_list"),
        pytest.param(lambda c: c.update(excluded="none"), id="excluded_not_a_list"),
        pytest.param(lambda c: c["stretches"].append("s-x"), id="stretch_not_an_object"),
        pytest.param(lambda c: c["stretches"][0].pop("drop_reason"), id="stretch_missing_drop_reason"),
        pytest.param(lambda c: c["stretches"][0].update(length_s="120"), id="length_a_string"),
        pytest.param(lambda c: c["stretches"][0].update(pace_s_per_km="3:49"), id="pace_a_string"),
        pytest.param(lambda c: c["stretches"][0].update(start_time="yesterday"), id="start_time_unreadable"),
        pytest.param(
            lambda c: c["stretches"][0].update(drop_reason="beaten_by_longer", beaten_by_s=None),
            id="beaten_without_its_length",
        ),
        pytest.param(lambda c: c["stretches"][0].update(drop_reason=7), id="drop_reason_not_a_string"),
        pytest.param(lambda c: c.update(estimate=None, unavailable=None), id="estimate_and_unavailable_null"),
        pytest.param(lambda c: c["estimate"].update(confidence="certain"), id="confidence_unknown"),
        pytest.param(lambda c: c["estimate"]["interval"].update(low="4:21"), id="interval_a_string"),
        pytest.param(lambda c: c.update(d_prime=None), id="estimate_without_d_prime"),
        pytest.param(lambda c: c["d_prime"].update(value_m=None), id="d_prime_value_null"),
        pytest.param(lambda c: c.update(d_prime_not_positive=None), id="d_prime_not_positive_not_a_bool"),
        pytest.param(
            lambda c: c.update(label_held_at_medium="yes", estimate={**c["estimate"], "confidence": "medium"}),
            id="held_not_a_bool",
        ),
        pytest.param(lambda c: c.update(label_held_at_medium=None), id="held_null_on_fitted"),
        pytest.param(
            lambda c: c.update(label_held_at_medium=True, estimate={**c["estimate"], "confidence": "high"}),
            id="held_true_on_high",
        ),
        pytest.param(lambda c: c.update(label_held_at_medium=True), id="held_true_on_low"),
        pytest.param(lambda c: c.pop("parameters"), id="parameters_missing"),
        pytest.param(
            lambda c: c.update(estimate=None, unavailable="longest_far_off", far_off_excess_s_per_km=None),
            id="far_off_without_its_excess",
        ),
        pytest.param(
            lambda c: c.update(estimate=None, unavailable="too_few_stretches", parameters={**PARAMETERS, "min_stretches": None}),
            id="min_stretches_null",
        ),
        pytest.param(lambda c: c["excluded"].append({**_excluded("2026-08-01T12:00:00Z", "x"), "reason": 7}), id="reason_not_a_string"),
    ],
)
def test_a_malformed_cs_entry_takes_the_error_path(install_mock_client, breaks) -> None:
    cs = _cs()
    breaks(cs)
    _refused(install_mock_client, _body(cs))
