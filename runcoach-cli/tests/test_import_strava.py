"""``runcoach import`` on a Strava archive (F030 AC10), against a mocked API.

The API is an ``httpx.MockTransport`` serving ``POST /imports``, ``GET /imports/{id}`` with a
Strava ``Import`` body (``source`` ``strava``, ``counts.strava``, ``files[]`` rows carrying
``strava_activity_id``) and ``GET /strava-activities/{id}`` with each row's ``name``. The served
shapes are the API's ``Import`` and ``StravaActivity`` models; the run against the real app is
``runcoach-api/tests/test_import_strava_cli_against_app.py``.

The local zone is fixed through ``sessions.LOCAL_ZONE`` at America/Vancouver, so a start at 03:00
UTC prints the day before: the line's date is the athlete's, not the UTC one.

Degenerate inputs the report reads, each driven through the command:

| input | printed |
|---|---|
| ``counts.strava`` absent on a Strava body | every count 0 |
| a row's name 404 (no stored row) or null | ``-`` |
| ``time_created`` null | ``-`` for the date |
| a row that did not parse (``entry`` ``activities.csv line <n>``) | its entry and its ``detail`` cells; no name lookup |
| two attention rows with one Activity ID | one lookup |
| ``GET /strava-activities`` answering 500 | exit 1 naming the status |
| a Garmin body (``source`` ``garmin`` or absent) | the Garmin report, no Strava line, no lookup |

The load lines (F031 AC4) read ``counts.strava.excluded_from_load`` / ``restored_to_load`` and the
body's ``load_changes`` entries, each printed as served (no lookup, no re-zoning of ``local_date``):

| input | printed |
|---|---|
| both counts 0 and ``load_changes`` ``[]`` or absent | no load lines |
| both counts 0 but ``load_changes`` non-empty | the load lines (a change is never hidden) |
| an excluded entry's ``old_load`` null (a ``no_hr`` run) | ``no load`` |
| ``old_load`` 0.0 | ``0.0``, not ``no load`` |
| ``old_load`` 42.35 / 7 (an int) | ``42.4`` / ``7.0`` |
| a restored entry's ``new_load`` 0.5 / 33.01 / 7 / 0.0 | ``now 0.5`` / ``now 33.0`` / ``now 7.0`` / ``now 0.0`` |
| a restored entry's ``new_load`` null | ``now no load`` |
| a restored entry with no ``new_load`` key (an older API) | its old load as before: ``no load`` |
| an excluded entry's ``new_load`` | not printed; the line names the old load |
| ``strava_type`` null (the run is no longer linked) | ``-`` |
| ``load_changes`` not a list, an entry not an object, ``old_load`` or ``new_load`` a string or a bool, ``local_date`` null | exit 1, the body could not be understood |
| a Strava job's progress, its counts carrying the night keys | "activities", never "and nights" |
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from runcoach_cli.commands import imports, sessions
from runcoach_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")

IMPORT_ID = "imp-strava-1"
ZONE = ZoneInfo("America/Vancouver")


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "sleep", lambda _s: None)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)


STRAVA_KEYS = (
    "attached",
    "attached_by_file_start",
    "stored",
    "stored_retyped",
    "already_stored",
    "ambiguous",
    "multisport_not_stored",
    "not_fit",
    "no_file",
    "not_running",
    "failed",
    "unchanged",
    "updated",
    "deleted_in_strava",
    "distance_differs",
    "excluded_from_load",
    "restored_to_load",
)


def _strava_counts(**values: int) -> dict[str, int]:
    out = dict.fromkeys(STRAVA_KEYS, 0)
    assert set(values) <= set(out), values
    out.update(values)
    return out


def _counts(strava: dict[str, int] | None, **top: Any) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "stored": 0,
        "already_stored": 0,
        "failed": 0,
        "no_night": 0,
        "replaced": 0,
        "skipped_sport": {},
        "non_activity": {},
        "not_fit": {},
        "unreadable_archives": [],
    }
    if strava is not None:
        counts["strava"] = strava
    counts.update(top)
    return counts


def _row(seq: int, entry: str, outcome: str, time_created: str | None, aid: int | None, **extra: Any) -> dict:
    row: dict[str, Any] = {
        "seq": seq,
        "entry": entry,
        "time_created": time_created,
        "outcome": outcome,
        "session_id": None,
        "sport": "Run",
        "activity_tag": None,
        "quality_flags": None,
        "status": None,
        "detail": None,
        "strava_activity_id": aid,
    }
    row.update(extra)
    return row


def _body(counts: dict[str, Any], files: list[dict], source: str | None = "strava", **extra: Any) -> dict:
    body: dict[str, Any] = {
        "import_id": IMPORT_ID,
        "archive_name": "export_1347737.zip",
        "archive_bytes": 999,
        "status": "completed",
        "error": None,
        "created_at": "2026-11-01T08:00:00+00:00",
        "started_at": "2026-11-01T08:00:01+00:00",
        "finished_at": "2026-11-01T08:01:00+00:00",
        "progress": {
            "fit_files_scanned": 0,
            "activities_total": len(files),
            "activities_processed": len(files),
        },
        "counts": counts,
        "files": files,
        "load_changes": [],
    }
    if source is not None:
        body["source"] = source
    body.update(extra)
    return body


# The rows of one Strava import: each outcome once, and the four kinds the report lists.
UNPARSED_DETAIL = 'row has 3 cells, the header 103: Activity ID "77", Activity Date "Apr 1, 2022, 1:00:00 PM", Activity Name "Short"'
FILES = [
    _row(1, "activities/101.fit.gz", "attached", "2022-04-02T15:00:00+00:00", 101, session_id="s1"),
    _row(
        2,
        "activities/102.fit.gz",
        "failed",
        "2022-04-03T03:30:00+00:00",
        102,
        status=413,
        detail="file_over_cap",
    ),
    _row(
        3, "activities/103.fit.gz", "ambiguous", "2022-04-04T12:00:00+00:00", 103, detail="candidates: s7, s8"
    ),
    _row(4, "activities.csv#104", "not_running", "2022-04-05T12:00:00+00:00", 104, sport="Ride"),
    _row(5, "activities/105.fit.gz", "multisport_not_stored", "2023-05-06T14:00:00+00:00", 105, sport="Run"),
    _row(6, "activities/106.fit.gz", "unchanged", "2022-04-06T12:00:00+00:00", 106),
    _row(7, "activities.csv line 9", "failed", None, None, detail=UNPARSED_DETAIL),
    _row(8, "activities.csv#108", "deleted_in_strava", "2021-12-31T23:30:00+00:00", 108, sport="Walk"),
]
COUNTS = _counts(
    _strava_counts(
        attached=1,
        ambiguous=1,
        not_running=1,
        multisport_not_stored=1,
        unchanged=1,
        failed=2,
        deleted_in_strava=1,
        distance_differs=1,
    ),
    failed=2,
)
NAMES = {
    101: "Morning Run",
    102: "Big Long Run",
    103: "Twin Run",
    104: "Lunch Ride",
    105: "Ironman 70.3",
    106: "Easy",
    108: "Old Walk",
}


class _Api:
    """``POST /imports`` queues; each ``GET /imports/{id}`` serves ``final``; ``GET
    /strava-activities/{id}`` serves the row named in ``names`` (404 otherwise, or ``strava_status``)."""

    def __init__(self, final: dict, names: dict[int, str | None] | None = None, strava_status: int = 200):
        self.final = final
        self.names = NAMES if names is None else names
        self.strava_status = strava_status
        self.gets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/imports":
            queued = dict(self.final, status="queued", files=None)
            return httpx.Response(202, json=queued)
        if request.method == "GET":
            self.gets.append(path)
        if request.method == "GET" and path.startswith("/imports/"):
            return httpx.Response(200, json=self.final)
        if request.method == "GET" and path.startswith("/strava-activities/"):
            if self.strava_status != 200:
                return httpx.Response(self.strava_status, text="boom")
            aid = int(path.rsplit("/", 1)[1])
            if aid not in self.names:
                return httpx.Response(404, json={"detail": "no_strava_activity"})
            return httpx.Response(
                200, json={"activity_id": aid, "name": self.names[aid], "activity_type": "Run"}
            )
        return httpx.Response(599, text=f"unexpected {request.method} {path}")

    def strava_gets(self) -> list[str]:
        return [p for p in self.gets if p.startswith("/strava-activities/")]


def _archive(tmp_path: Path) -> Path:
    path = tmp_path / "export_1347737.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("activities.csv", "Activity ID\n")
    path.write_bytes(buffer.getvalue())
    return path


def _import(tmp_path: Path, install_mock_client, api: _Api):
    install_mock_client(api)
    return runner.invoke(app, ["import", str(_archive(tmp_path))])


def _report(output: str) -> list[str]:
    """The lines after the final status line."""
    lines = output.splitlines()
    return lines[lines.index("Status: completed") + 1 :]


def test_a_strava_import_prints_its_counts(tmp_path: Path, install_mock_client) -> None:
    result = _import(tmp_path, install_mock_client, _Api(_body(COUNTS, FILES)))

    assert result.exit_code == 0, result.output
    report = _report(result.output)
    assert report[0] == (
        "Strava: attached 1, attached_by_file_start 0, stored 0, stored_retyped 0, already_stored 0, "
        "ambiguous 1, multisport_not_stored 1, not_fit 0, no_file 0, not_running 1, failed 2, "
        "unchanged 1, updated 0, deleted_in_strava 1, distance_differs 1"
    ), result.output
    # The Garmin report's lines are not printed for a Strava import.
    assert not any(
        line.startswith(("Stored:", "Skipped by sport:", "Non-activity files:")) for line in report
    )


def test_a_strava_import_without_strava_counts_prints_zeros(tmp_path: Path, install_mock_client) -> None:
    result = _import(tmp_path, install_mock_client, _Api(_body(_counts(None), [])))

    assert result.exit_code == 0, result.output
    assert _report(result.output) == [
        (
            "Strava: attached 0, attached_by_file_start 0, stored 0, stored_retyped 0, already_stored 0, "
            "ambiguous 0, multisport_not_stored 0, not_fit 0, no_file 0, not_running 0, failed 0, "
            "unchanged 0, updated 0, deleted_in_strava 0, distance_differs 0"
        )
    ]


def test_one_line_per_failed_ambiguous_multisport_and_deleted_row(
    tmp_path: Path, install_mock_client
) -> None:
    api = _Api(_body(COUNTS, FILES))
    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    # In seq order; each date is the Vancouver date of time_created (03:30 UTC on Apr 3 is Apr 2
    # there; 23:30 UTC on Dec 31 is still Dec 31).
    assert _report(result.output)[1:] == [
        "  failed  2022-04-02  Big Long Run  413  file_over_cap",
        "  ambiguous  2022-04-04  Twin Run  candidates: s7, s8",
        "  multisport_not_stored  2023-05-06  Ironman 70.3",
        f"  failed  activities.csv line 9  {UNPARSED_DETAIL}",
        "  deleted_in_strava  2021-12-31  Old Walk",
    ], result.output
    # Only the listed rows' names are read; attached, not_running and unchanged rows are not.
    assert api.strava_gets() == [f"/strava-activities/{aid}" for aid in (102, 103, 105, 108)]


def test_a_row_that_did_not_parse_prints_its_raw_cells(tmp_path: Path, install_mock_client) -> None:
    detail = (
        'Activity Date does not parse: Activity ID "55", Activity Date "1 avr. 2022", Activity Name "Course"'
    )
    files = [_row(1, "activities.csv line 2", "failed", None, 55, sport="Course", detail=detail)]
    counts = _counts(_strava_counts(failed=1), failed=1)
    api = _Api(_body(counts, files), names={55: "Course"})

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    assert _report(result.output)[1:] == [f"  failed  activities.csv line 2  {detail}"]
    # The cells come from detail; nothing is looked up for the row.
    assert api.strava_gets() == []


def test_a_missing_name_or_date_prints_a_dash_and_one_id_is_read_once(
    tmp_path: Path, install_mock_client
) -> None:
    files = [
        _row(1, "activities/9.fit.gz", "failed", None, 9, detail="no_session_message"),
        _row(
            2, "activities/10.fit.gz", "failed", "2022-06-01T12:00:00+00:00", 10, detail="file_not_in_archive"
        ),
        _row(
            3,
            "activities/10.fit.gz",
            "failed",
            "2022-06-01T12:00:00+00:00",
            10,
            detail="duplicate_activity_id",
        ),
    ]
    api = _Api(_body(_counts(_strava_counts(failed=3), failed=3), files), names={10: None})

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    assert _report(result.output)[1:] == [
        "  failed  -  -  -  no_session_message",
        "  failed  2022-06-01  -  -  file_not_in_archive",
        "  failed  2022-06-01  -  -  duplicate_activity_id",
    ]
    assert api.strava_gets() == ["/strava-activities/9", "/strava-activities/10"]


def test_a_name_lookup_the_api_refuses_exits_1(tmp_path: Path, install_mock_client) -> None:
    result = _import(tmp_path, install_mock_client, _Api(_body(COUNTS, FILES), strava_status=500))

    assert result.exit_code == 1, result.output
    assert "500" in result.output


def test_a_strava_import_exits_0(tmp_path: Path, install_mock_client) -> None:
    api = _Api(_body(COUNTS, FILES))
    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    assert f"Import {IMPORT_ID} started (export_1347737.zip)." in result.output.splitlines()


_ABSENT = object()


def _change(
    sid: str, local_date: Any, strava_type: Any, old_load: Any, change: str, new_load: Any = _ABSENT
) -> dict:
    """A ``load_changes`` entry; without ``new_load`` it is an older API's entry, which has no key."""
    entry = {
        "session_id": sid,
        "local_date": local_date,
        "strava_type": strava_type,
        "old_load": old_load,
        "change": change,
    }
    if new_load is not _ABSENT:
        entry["new_load"] = new_load
    return entry


ATTENTION_LINES = [
    "  failed  2022-04-02  Big Long Run  413  file_over_cap",
    "  ambiguous  2022-04-04  Twin Run  candidates: s7, s8",
    "  multisport_not_stored  2023-05-06  Ironman 70.3",
    f"  failed  activities.csv line 9  {UNPARSED_DETAIL}",
    "  deleted_in_strava  2021-12-31  Old Walk",
]


def _load_counts(excluded: int, restored: int) -> dict[str, Any]:
    strava = dict(COUNTS["strava"], excluded_from_load=excluded, restored_to_load=restored)
    return dict(COUNTS, strava=strava)


def test_one_line_per_run_excluded_or_restored(tmp_path: Path, install_mock_client) -> None:
    changes = [
        _change("s1", "2022-03-01", "Alpine Ski", 61.25, "excluded"),
        _change("s2", "2022-03-05", "Hike", None, "excluded"),
        _change("s3", "2022-02-27", "Run", None, "restored"),
    ]
    api = _Api(_body(_load_counts(2, 1), FILES, load_changes=changes))

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    # After the Strava block: the two counts, then one line per entry in the served order.
    assert _report(result.output)[1:] == ATTENTION_LINES + [
        "Load: excluded_from_load 2, restored_to_load 1",
        "  excluded  2022-03-01  Alpine Ski  61.2",
        "  excluded  2022-03-05  Hike  no load",
        "  restored  2022-02-27  Run  no load",
    ], result.output
    # The load lines look nothing up: the names read are the attention rows' only.
    assert api.strava_gets() == [f"/strava-activities/{aid}" for aid in (102, 103, 105, 108)]


@pytest.mark.parametrize(
    ("entry", "line"),
    [
        (
            _change("s1", "2022-03-01", "Alpine Ski", 42.35, "excluded"),
            "  excluded  2022-03-01  Alpine Ski  42.4",
        ),
        (_change("s1", "2022-03-01", "Alpine Ski", 7, "excluded"), "  excluded  2022-03-01  Alpine Ski  7.0"),
        (_change("s1", "2022-03-01", "Walk", 0.0, "excluded"), "  excluded  2022-03-01  Walk  0.0"),
        (_change("s1", "2022-03-01", "Hike", None, "excluded"), "  excluded  2022-03-01  Hike  no load"),
        (_change("s1", "2021-12-31", None, None, "restored"), "  restored  2021-12-31  -  no load"),
    ],
)
def test_the_line_names_local_date_strava_type_and_old_load(
    tmp_path: Path, install_mock_client, entry: dict, line: str
) -> None:
    excluded, restored = (1, 0) if entry["change"] == "excluded" else (0, 1)
    files = [_row(1, "activities/101.fit.gz", "attached", "2022-04-02T15:00:00+00:00", 101, session_id="s1")]
    counts = _counts(_strava_counts(attached=1, excluded_from_load=excluded, restored_to_load=restored))
    api = _Api(_body(counts, files, load_changes=[entry]))

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    # local_date is the served athlete-zone date, printed as is (the CLI's zone is Vancouver here,
    # and the date is not converted again).
    assert _report(result.output)[1:] == [
        f"Load: excluded_from_load {excluded}, restored_to_load {restored}",
        line,
    ], result.output


@pytest.mark.parametrize(
    ("entry", "line"),
    [
        (
            _change("s1", "2021-02-07", "Run", None, "restored", 0.5),
            "  restored  2021-02-07  Run  now 0.5",
        ),
        (
            _change("s1", "2022-08-27", None, None, "restored", 33.01),
            "  restored  2022-08-27  -  now 33.0",
        ),
        (_change("s1", "2022-08-27", "Run", None, "restored", 7), "  restored  2022-08-27  Run  now 7.0"),
        (_change("s1", "2022-08-27", "Run", None, "restored", 0.0), "  restored  2022-08-27  Run  now 0.0"),
        (
            _change("s1", "2022-08-27", "Run", None, "restored", None),
            "  restored  2022-08-27  Run  now no load",
        ),
        # An excluded line is unchanged: it names the old load, whatever new_load says.
        (
            _change("s1", "2018-12-30", "Alpine Ski", 21.64, "excluded", None),
            "  excluded  2018-12-30  Alpine Ski  21.6",
        ),
        # An older API serves no new_load key: the restored line keeps its earlier wording.
        (_change("s1", "2021-02-07", "Run", None, "restored"), "  restored  2021-02-07  Run  no load"),
    ],
)
def test_a_restored_line_prints_the_load_it_counts_again(
    tmp_path: Path, install_mock_client, entry: dict, line: str
) -> None:
    """Athlete ruling 2026-11-05: a restored line shows the load the run counts again,
    ``now <new_load>`` to 1 decimal (``now no load`` when null), not the old load it carried while
    excluded."""
    excluded, restored = (1, 0) if entry["change"] == "excluded" else (0, 1)
    files = [_row(1, "activities/101.fit.gz", "attached", "2022-04-02T15:00:00+00:00", 101, session_id="s1")]
    counts = _counts(_strava_counts(attached=1, excluded_from_load=excluded, restored_to_load=restored))
    api = _Api(_body(counts, files, load_changes=[entry]))

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    print("[slice compared] load lines:", _report(result.output)[1:])
    assert _report(result.output)[1:] == [
        f"Load: excluded_from_load {excluded}, restored_to_load {restored}",
        line,
    ], result.output


def test_a_strava_import_progress_names_no_nights(tmp_path: Path, install_mock_client) -> None:
    """A Strava archive holds no nights, so a Strava job's progress lines say "activities", though
    its counts carry the night keys an API that stores nights always serves."""

    def running(processed: int) -> dict:
        body = _body(COUNTS, FILES, status="running")
        body["counts"] = dict(COUNTS, replaced=0, no_night=0)
        body["progress"] = {"fit_files_scanned": 0, "activities_total": 250, "activities_processed": processed}
        return body

    final = _body(dict(COUNTS, replaced=0, no_night=0), FILES)
    bodies = [running(0), running(120), final]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/imports":
            return httpx.Response(202, json=dict(running(0), status="queued", files=None))
        if request.method == "GET" and path.startswith("/imports/"):
            return httpx.Response(200, json=bodies.pop(0) if len(bodies) > 1 else bodies[0])
        return _Api(final)(request)

    install_mock_client(handler)
    result = runner.invoke(app, ["import", str(_archive(tmp_path))])

    assert result.exit_code == 0, result.output
    progress = [line for line in result.output.splitlines() if line.startswith(("Status: running", "Processed"))]
    print("[slice compared] progress lines:", progress)
    assert progress == ["Status: running (250 activities)", "Processed 120 of 250 activities"]
    assert "nights" not in result.output
    # A Garmin job from an API that stores nights still names them.
    garmin = dict(running(5), source="garmin")
    assert imports._status_line(garmin) == "Status: running (250 activities and nights)"
    assert imports._processed_line(garmin, 5, 250) == "Processed 5 of 250 activities and nights"


@pytest.mark.parametrize("load_changes", [[], None, "absent"])
def test_no_load_lines_when_nothing_changed(tmp_path: Path, install_mock_client, load_changes: Any) -> None:
    body = _body(COUNTS, FILES, load_changes=load_changes)
    if load_changes == "absent":
        del body["load_changes"]

    result = _import(tmp_path, install_mock_client, _Api(body))

    assert result.exit_code == 0, result.output
    assert _report(result.output)[1:] == ATTENTION_LINES, result.output
    assert not any(line.startswith("Load:") for line in result.output.splitlines())


def test_load_changes_with_zero_counts_are_still_printed(tmp_path: Path, install_mock_client) -> None:
    changes = [_change("s1", "2022-03-01", "Alpine Ski", 3.0, "excluded")]
    result = _import(tmp_path, install_mock_client, _Api(_body(COUNTS, FILES, load_changes=changes)))

    assert result.exit_code == 0, result.output
    assert _report(result.output)[len(ATTENTION_LINES) + 1 :] == [
        "Load: excluded_from_load 0, restored_to_load 0",
        "  excluded  2022-03-01  Alpine Ski  3.0",
    ], result.output


@pytest.mark.parametrize(
    "load_changes",
    [
        {"s1": "excluded"},
        ["excluded"],
        [_change("s1", "2022-03-01", "Hike", "12.5", "excluded")],
        [_change("s1", "2022-03-01", "Hike", True, "excluded")],
        [_change("s1", None, "Hike", 1.0, "excluded")],
        [_change("s1", "2022-03-01", "Run", None, "restored", "0.5")],
        [_change("s1", "2022-03-01", "Run", None, "restored", True)],
    ],
)
def test_malformed_load_changes_exit_1(tmp_path: Path, install_mock_client, load_changes: Any) -> None:
    api = _Api(_body(_load_counts(1, 0), FILES, load_changes=load_changes))

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 1, result.output
    assert "load_changes" in result.output, result.output


GARMIN_FILES = [
    _row(1, "a/run1.fit", "stored", "2026-01-01T00:00:00+00:00", None, session_id="s1", sport="running"),
    _row(2, "a/bad.fit", "failed", "2026-01-01T00:00:00+00:00", None, status=400, detail="not a FIT file"),
]
GARMIN_REPORT = [
    "Stored: 1 runs, 0 resting captures",
    "Already stored: 0",
    "Skipped by sport: 0",
    "Failed: 1",
    "  a/bad.fit  400  not a FIT file",
    "Non-activity files: 0",
    "Not FIT: 1 (tcx 1)",
    "Unreadable archives: 0",
]


@pytest.mark.parametrize("source", ["garmin", None])
def test_a_garmin_import_prints_as_before(tmp_path: Path, install_mock_client, source: str | None) -> None:
    counts = _counts(_strava_counts() if source else None, stored=1, failed=1, not_fit={"tcx": 1})
    api = _Api(_body(counts, GARMIN_FILES, source=source))

    result = _import(tmp_path, install_mock_client, api)

    assert result.exit_code == 0, result.output
    assert _report(result.output) == GARMIN_REPORT, result.output
    assert api.strava_gets() == []
