"""A Strava row's outcome follows the matching steps 1-4, the 1 % tiebreak, one row per session and
the distance warning (F030 reference section 3; AC2, AC3, AC6 and the decision half of AC4).

``strava_match.decide`` is pure: every test hands it the candidates at the CSV start, a file
reader and the candidates at a file start as plain values or callables, so each rule is driven
alone. Step 0 (rows already stored) needs the store and is the import job's, not tested here. One
test reads candidates through ``db.sessions_at_second`` on a ``tmp_path`` store, to pin the order an
ambiguous row's ``candidates:`` detail names them in.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from runcoach_api import db
from runcoach_api.imports import strava_match
from runcoach_api.imports.strava_types import (
    AMBIGUOUS,
    ATTACHED,
    ATTACHED_BY_FILE_START,
    DISTANCE_DIFFERS,
    FAILED,
    MATCH_CSV_START,
    MATCH_FILE_START,
    MATCH_STORED,
    MULTISPORT_NOT_STORED,
    NO_FILE,
    NOT_FIT,
    NOT_RUNNING,
    STORED,
    CsvRow,
    Decision,
    FileError,
)

CSV_START = "2024-07-05T10:00:00+00:00"
FILE_START = "2024-07-05T10:50:00+00:00"


def _row(activity_type: str = "Run", distance_m: float | None = 10000.0,
         filename: str = "activities/1.fit.gz") -> CsvRow:
    return CsvRow(line=2, activity_id=1, start_time=CSV_START, activity_type=activity_type, name="row",
                  filename=filename, distance_m=distance_m, columns={})


def _never_read() -> Sequence[str | None] | FileError:
    raise AssertionError("the file was read")


def _never_at_file_start(second: str) -> list[tuple[str, float | None]]:
    raise AssertionError(f"looked up sessions at the file start {second}")


def _file(*starts: str | None) -> Callable[[], Sequence[str | None] | FileError]:
    calls: list[int] = []

    def read() -> Sequence[str | None] | FileError:
        calls.append(1)
        assert len(calls) == 1, "the file was read twice"
        return list(starts)

    return read


def _at(second: str, candidates: list[tuple[str, float | None]]) -> Callable[[str], list[tuple[str, float | None]]]:
    def at_file_start(asked: str) -> list[tuple[str, float | None]]:
        return candidates if asked == second else []

    return at_file_start


def _decide(row: CsvRow, at_csv_start: list[tuple[str, float | None]],
            read_file: Callable[[], Sequence[str | None] | FileError] = _never_read,
            at_file_start: Callable[[str], list[tuple[str, float | None]]] = _never_at_file_start,
            linked: frozenset[str] = frozenset()) -> Decision:
    got = strava_match.decide(row, at_csv_start, read_file, at_file_start, linked)
    print(f"  {row.activity_type} d={row.distance_m} file={row.filename!r} csv={at_csv_start} -> {got}")
    return got


# --- step 1: the CSV start ---------------------------------------------------------------------


def test_one_session_at_the_second_attaches() -> None:
    got = _decide(_row(), [("s1", 10000.0)])
    assert got == Decision(outcome=ATTACHED, session_id="s1", match=MATCH_CSV_START)
    # Any distance, absent or far off, still attaches a single candidate (with a warning if off).
    assert _decide(_row(distance_m=None), [("s1", 3090.0)]).session_id == "s1"
    assert _decide(_row(), [("s1", None)]).session_id == "s1"
    # A non-running row attaches too: a stored session of any sport is a candidate.
    ride = _decide(_row("Ride"), [("s1", 10000.0)])
    assert (ride.outcome, ride.session_id, ride.match) == (ATTACHED, "s1", MATCH_CSV_START)


def test_two_candidates_one_within_one_percent_links_that_one() -> None:
    # The 2024-07-05 shape: FR945 13,712 m and device 3476 3,090 m, Strava 13,711.6 m.
    for order in ([("fr945", 13712.0), ("d3476", 3090.0)], [("d3476", 3090.0), ("fr945", 13712.0)]):
        got = _decide(_row(distance_m=13711.6), order)
        assert got == Decision(outcome=ATTACHED, session_id="fr945", match=MATCH_CSV_START)
    # Three candidates, exactly one within 1 %.
    got = _decide(_row(), [("a", 3090.0), ("b", 10050.0), ("c", None)])
    assert (got.outcome, got.session_id) == (ATTACHED, "b")


def test_neither_or_both_within_one_percent_is_ambiguous_naming_candidates() -> None:
    neither = _decide(_row(), [("fr945", 12000.0), ("d3476", 3090.0)])
    assert neither == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")
    both = _decide(_row(), [("fr945", 10000.0), ("d3476", 10050.0)])
    assert both == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")
    # A candidate with no distance is never within 1 %.
    absent = _decide(_row(), [("x", None), ("y", None)])
    assert absent == Decision(outcome=AMBIGUOUS, detail="candidates: x, y")


def test_zero_distance_with_two_candidates_is_ambiguous() -> None:
    # CsvRow.distance_m is None for a Distance (2) of 0.0 or empty; 0.0 itself is guarded too.
    for d in (None, 0.0):
        got = _decide(_row(distance_m=d), [("fr945", 10000.0), ("d3476", 3090.0)])
        assert got == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")
    # A zero-distance session never wins the tiebreak either.
    got = _decide(_row(distance_m=0.0), [("a", 0.0), ("b", 0.0)])
    assert got.outcome == AMBIGUOUS


def test_a_linked_session_makes_the_second_row_ambiguous() -> None:
    got = _decide(_row(), [("s1", 10000.0)], linked=frozenset({"s1"}))
    assert got == Decision(outcome=AMBIGUOUS, detail="candidates: s1")
    # The tiebreak's winner already linked: ambiguous, nothing linked, the other is not taken.
    got = _decide(_row(distance_m=13711.6), [("fr945", 13712.0), ("d3476", 3090.0)],
                  linked=frozenset({"fr945"}))
    assert got == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")
    # A link to another session does not get in the way.
    assert _decide(_row(), [("s1", 10000.0)], linked=frozenset({"s2"})).session_id == "s1"
    # At the file start too.
    got = _decide(_row(), [], _file(FILE_START), _at(FILE_START, [("s9", 10000.0)]), frozenset({"s9"}))
    assert got == Decision(outcome=AMBIGUOUS, detail="candidates: s9")


# --- step 2: the file's own start ----------------------------------------------------------------


def test_file_start_links_a_running_row_only() -> None:
    for kind in ("Run", "Trail Run", "Virtual Run"):
        got = _decide(_row(kind), [], _file(FILE_START), _at(FILE_START, [("s9", 10000.0)]))
        assert got == Decision(outcome=ATTACHED_BY_FILE_START, session_id="s9", match=MATCH_FILE_START)
    # A non-running row is never decoded, so never linked by the file's start.
    for kind in ("Ride", "Walk", "Workout", "run", "Hike"):
        got = _decide(_row(kind), [], _never_read, _never_at_file_start)
        assert got == Decision(outcome=NOT_RUNNING)
    # A candidate at the CSV start wins; the file is not read.
    got = _decide(_row(), [("s1", 10000.0)], _never_read, _never_at_file_start)
    assert got.match == MATCH_CSV_START


def test_file_start_uses_the_same_tiebreak() -> None:
    pair = [("fr945", 13712.0), ("d3476", 3090.0)]
    got = _decide(_row(distance_m=13711.6), [], _file(FILE_START), _at(FILE_START, pair))
    assert got == Decision(outcome=ATTACHED_BY_FILE_START, session_id="fr945", match=MATCH_FILE_START)
    got = _decide(_row(), [], _file(FILE_START), _at(FILE_START, pair))
    assert got == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")
    got = _decide(_row(distance_m=None), [], _file(FILE_START), _at(FILE_START, pair))
    assert got == Decision(outcome=AMBIGUOUS, detail="candidates: fr945, d3476")


def test_two_sessions_in_the_file_is_multisport_not_stored() -> None:
    got = _decide(_row(), [], _file(FILE_START, "2024-07-05T11:30:00+00:00"), _never_at_file_start)
    assert got == Decision(outcome=MULTISPORT_NOT_STORED)
    assert not got.store
    # Five sessions (the 70.3 shape), even with a stored session at the first one's start.
    got = _decide(_row(), [], _file(*[FILE_START] * 5), _at(FILE_START, [("s9", 10000.0)]))
    assert got == Decision(outcome=MULTISPORT_NOT_STORED)


def test_no_session_message_is_failed() -> None:
    # The reader may answer an empty list of sessions or name the cause itself.
    got = _decide(_row(), [], _file(), _never_at_file_start)
    assert got == Decision(outcome=FAILED, detail="no_session_message")
    got = _decide(_row(), [], lambda: FileError("no_session_message"), _never_at_file_start)
    assert got == Decision(outcome=FAILED, detail="no_session_message")


def test_a_file_error_is_failed_with_its_reason() -> None:
    got = _decide(_row(), [], lambda: FileError("corrupt_gz"), _never_at_file_start)
    assert got == Decision(outcome=FAILED, detail="corrupt_gz")
    got = _decide(_row(), [], lambda: FileError("over_cap", 413, "past 52428800 bytes"), _never_at_file_start)
    assert got == Decision(outcome=FAILED, detail="over_cap: past 52428800 bytes")
    assert not got.store


# --- step 3: store ------------------------------------------------------------------------------


def test_a_running_fit_with_no_session_anywhere_is_stored() -> None:
    for name in ("activities/1.fit.gz", "activities/1.fit", "activities/1.FIT.GZ"):
        got = _decide(_row(filename=name), [], _file(FILE_START), _at(FILE_START, []))
        assert got == Decision(outcome=STORED, match=MATCH_STORED, store=True)
    # Stored whatever its distance: the warning is read against a linked session only.
    got = _decide(_row(distance_m=None), [], _file(FILE_START), _at(FILE_START, []))
    assert (got.store, got.warnings) == (True, ())


def test_a_file_session_with_an_unreadable_start_is_stored() -> None:
    # A single session whose start_time the reader could not read has no session at its start:
    # step 3 stores it, and the store's (source_device, start_time) key turns a copy of a stored
    # file into already_stored.
    got = _decide(_row(), [], _file(None), _never_at_file_start)
    assert got == Decision(outcome=STORED, match=MATCH_STORED, store=True)


# --- step 4: nothing to store -------------------------------------------------------------------


def test_gpx_run_is_not_fit_and_no_file_is_no_file() -> None:
    for name in ("activities/1.gpx.gz", "activities/1.gpx", "activities/1.tcx.gz", "activities/1.TCX"):
        assert _decide(_row(filename=name), [], _never_read, _never_at_file_start) == Decision(outcome=NOT_FIT)
    assert _decide(_row(filename=""), [], _never_read, _never_at_file_start) == Decision(outcome=NO_FILE)
    # A candidate at the CSV start still attaches a GPX run and a run with no file.
    assert _decide(_row(filename="activities/1.gpx.gz"), [("s1", 10000.0)]).outcome == ATTACHED
    assert _decide(_row(filename=""), [("s1", 10000.0)]).outcome == ATTACHED


def test_a_non_running_row_is_never_decoded() -> None:
    # AC3: a Ride's file is never read, so bytes that do not decode leave it not_running.
    for name in ("activities/1.fit.gz", "activities/1.gpx.gz", ""):
        got = _decide(_row("Ride", filename=name), [], _never_read, _never_at_file_start)
        assert got == Decision(outcome=NOT_RUNNING)


# --- the warning --------------------------------------------------------------------------------


def test_warning_just_over_one_percent_not_at_exactly() -> None:
    warned = Decision(outcome=ATTACHED, session_id="s1", match=MATCH_CSV_START,
                      warnings=(DISTANCE_DIFFERS,), detail="warnings: distance_differs")
    assert _decide(_row(), [("s1", 10100.01)]) == warned
    assert _decide(_row(), [("s1", 9899.99)]) == warned
    assert _decide(_row(), [("s1", 3090.0)]) == warned
    # s = 1.01 d and 0.99 d exactly carry none (the test material's 10,100 m against 10,000.0).
    for s in (10100.0, 9900.0, 10000.0):
        assert _decide(_row(), [("s1", s)]) == Decision(outcome=ATTACHED, session_id="s1", match=MATCH_CSV_START)
    # The warning on a file-start link.
    got = _decide(_row(), [], _file(FILE_START), _at(FILE_START, [("s9", 12000.0)]))
    assert (got.outcome, got.warnings) == (ATTACHED_BY_FILE_START, (DISTANCE_DIFFERS,))


def test_zero_distance_carries_no_warning() -> None:
    for d, s in ((None, 10000.0), (0.0, 10000.0), (10000.0, None), (None, None)):
        got = _decide(_row(distance_m=d), [("s1", s)])
        assert got == Decision(outcome=ATTACHED, session_id="s1", match=MATCH_CSV_START)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_distance_is_absent(bad: float) -> None:
    # Never within 1 % and never warned on, on either side.
    assert _decide(_row(distance_m=bad), [("s1", 10000.0)]).warnings == ()
    assert _decide(_row(), [("s1", bad)]).warnings == ()
    got = _decide(_row(), [("a", bad), ("b", 3090.0)])
    assert got.outcome == AMBIGUOUS


def test_within_one_percent_is_false_for_absent_or_zero() -> None:
    within = strava_match.within_one_percent
    assert within(10100.0, 10000.0) and within(9900.0, 10000.0)
    assert not within(10100.01, 10000.0)
    assert not within(None, 10000.0) and not within(10000.0, None)
    assert not within(0.0, 0.0) and not within(0.0, None)


# --- candidate order ----------------------------------------------------------------------------


def test_candidates_are_named_in_upload_order(tmp_path: Path) -> None:
    """``db.sessions_at_second`` returns upload order, and the ``candidates:`` detail keeps it: the
    later-uploaded session sorts first by id, so ordering by id instead of upload order reds this."""
    conn = sqlite3.connect(tmp_path / "store.db")
    conn.row_factory = sqlite3.Row
    try:
        db.init_schema(conn)
        with conn:
            for upload_order, session_id, distance in ((1, "zz-first", 12000.0), (2, "aa-second", 3090.0)):
                conn.execute(
                    "INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device,"
                    " summary, upload_order) VALUES (?, ?, 'running', 'garmin', ?, ?, ?)",
                    (session_id, CSV_START, session_id, json.dumps({"distance_m": distance}), upload_order),
                )
        found = db.sessions_at_second(conn, CSV_START)
        print("  sessions_at_second:", found)
        assert [s[0] for s in found] == ["zz-first", "aa-second"]
        got = _decide(_row(), [(sid, dist) for sid, _sport, dist in found])
        assert got == Decision(outcome=AMBIGUOUS, detail="candidates: zz-first, aa-second")
    finally:
        conn.close()
