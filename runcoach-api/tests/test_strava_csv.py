"""``imports/strava_csv.py``: recognising a Strava archive and reading its ``activities.csv``
(F030 AC1, AC5, AC8; reference section 2, items 1-4 and 6).

Every archive is built at run time by ``tests/support/strava_archive.py`` into ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import io
import locale
import zipfile
from pathlib import Path

import pytest
from runcoach_api.imports import archive, strava_csv
from runcoach_api.imports.strava_types import CsvRow

_SPEC = importlib.util.spec_from_file_location(
    "strava_archive", Path(__file__).parent / "support" / "strava_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
SA = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SA)


def _zip_bytes(entries: list[tuple[str, bytes]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def _csv_zip(csv_bytes: bytes, prefix: str = "") -> zipfile.ZipFile:
    return _zip_bytes([(f"{prefix}activities.csv", csv_bytes)])


def _read(rows, **kw) -> strava_csv.CsvResult:
    return strava_csv.read_activities(_csv_zip(SA.activities_csv(rows, **kw)), "")


def _run(activity_id: str, date: str = SA.DEFAULT_DATE, **cells: str) -> dict[str, str]:
    base = {
        "Activity Name": f"Run {activity_id}",
        "Activity Type": "Run",
        "Filename": f"activities/{activity_id}.fit.gz",
        "Distance": "5.01",
        "Distance (2)": "5012.3",
    }
    base.update(cells)
    return SA.row(**{"Activity ID": activity_id, "Activity Date": date}, **base)


# ---------------------------------------------------------------------------
# recognition
# ---------------------------------------------------------------------------


def test_recognised_at_the_root_and_under_one_folder(tmp_path: Path) -> None:
    rows = [_run("101")]
    with zipfile.ZipFile(SA.strava_zip(tmp_path / "root.zip", rows, layout="root")) as zf:
        assert strava_csv.recognise(zf) == ""
        assert [r.activity_id for r in strava_csv.read_activities(zf, "").rows] == [101]
    with zipfile.ZipFile(SA.strava_zip(tmp_path / "folder.zip", rows, layout="folder")) as zf:
        # The folder's own directory entry ("export_1347737/") counts as that folder.
        assert any(i.filename == f"{SA.FOLDER}/" for i in zf.infolist())
        prefix = strava_csv.recognise(zf)
        assert prefix == f"{SA.FOLDER}/"
        assert [r.activity_id for r in strava_csv.read_activities(zf, prefix).rows] == [101]
    # Without explicit directory entries the one folder is still recognised.
    assert strava_csv.recognise(_csv_zip(SA.activities_csv(rows), "my export/")) == "my export/"


def test_macosx_entry_beside_the_folder_is_still_one_folder(tmp_path: Path) -> None:
    with zipfile.ZipFile(SA.strava_zip(tmp_path / "mac.zip", [_run("7")], layout="macosx")) as zf:
        assert any(i.filename.startswith("__MACOSX/") for i in zf.infolist())
        assert strava_csv.recognise(zf) == f"{SA.FOLDER}/"
    csv_bytes = SA.activities_csv([_run("7")])
    # A second folder, or a file beside the folder, is not "one folder".
    assert strava_csv.recognise(_zip_bytes([("a/activities.csv", csv_bytes), ("b/x.txt", b"x")])) is None
    assert strava_csv.recognise(_zip_bytes([("a/activities.csv", csv_bytes), ("readme.txt", b"x")])) is None
    # __MACOSX alone beside a root-less folder is ignored, but a __MACOSX copy of the CSV is not the CSV.
    assert strava_csv.recognise(_zip_bytes([("__MACOSX/activities.csv", csv_bytes)])) is None


def test_a_garmin_archive_is_not_recognised() -> None:
    csv_bytes = SA.activities_csv([_run("1")])
    garmin = _zip_bytes(
        [
            ("DI_CONNECT/DI-Connect-Fitness/summary.json", b"{}"),
            ("DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip", b"PK"),
        ]
    )
    assert strava_csv.recognise(garmin) is None
    # Two folders deep is not "one folder holding activities.csv".
    assert strava_csv.recognise(_zip_bytes([("a/b/activities.csv", csv_bytes)])) is None
    # The name is matched exactly.
    assert strava_csv.recognise(_zip_bytes([("Activities.csv", csv_bytes)])) is None
    assert strava_csv.recognise(_zip_bytes([("activities.csv.gz", csv_bytes)])) is None
    assert strava_csv.recognise(_zip_bytes([("x.fit", b"\x0e")])) is None
    assert strava_csv.recognise(_zip_bytes([])) is None


# ---------------------------------------------------------------------------
# cells and keys
# ---------------------------------------------------------------------------


def test_repeated_distance_header_is_keyed_distance_and_distance_2() -> None:
    result = _read(
        [
            _run(
                "42",
                **{
                    "Distance": "5.01",
                    "Distance (2)": "5012.3",
                    "Elapsed Time": "1500",
                    "Elapsed Time (2)": "1501",
                },
            )
        ]
    )
    assert result.failed == ()
    (got,) = result.rows
    assert isinstance(got, CsvRow)
    assert list(got.columns) == list(SA.KEYS)
    assert len(got.columns) == len(SA.FULL_HEADER) == 103
    assert got.columns["Distance"] == "5.01"
    assert got.columns["Distance (2)"] == "5012.3"
    assert got.columns["Elapsed Time"] == "1500"
    assert got.columns["Elapsed Time (2)"] == "1501"
    assert got.distance_m == 5012.3
    assert got.activity_id == 42
    assert got.activity_type == "Run"
    assert got.name == "Run 42"
    assert got.filename == "activities/42.fit.gz"
    assert got.line == 2
    assert strava_csv.keys_of(["A", "B", "A", "A"]) == ["A", "B", "A (2)", "A (3)"]


def test_activity_date_reads_12_am_as_hour_0_in_utc() -> None:
    cases = {
        "Jan 2, 2023, 12:05:09 AM": "2023-01-02T00:05:09+00:00",
        "Jan 2, 2023, 12:05:09 PM": "2023-01-02T12:05:09+00:00",
        "Jan 2, 2023, 1:00:00 AM": "2023-01-02T01:00:00+00:00",
        "Jan 2, 2023, 11:59:59 PM": "2023-01-02T23:59:59+00:00",
        # A DST change day in Europe and the US: UTC throughout, no arithmetic.
        "Mar 26, 2023, 2:30:00 AM": "2023-03-26T02:30:00+00:00",
        "Apr 18, 2022, 2:26:14 PM": "2022-04-18T14:26:14+00:00",
    }
    result = _read([_run(str(i + 1), date) for i, date in enumerate(cases)])
    assert result.failed == ()
    by_id = {r.activity_id: r.start_time for r in result.rows}
    for i, expected in enumerate(cases.values()):
        assert by_id[i + 1] == expected
        assert len(expected) == 25


def test_month_names_do_not_depend_on_the_locale() -> None:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    rows = [_run(str(m + 1), f"{name} 3, 2021, 4:05:06 PM") for m, name in enumerate(months)]
    saved = locale.setlocale(locale.LC_TIME)
    switched = None
    for candidate in (
        "de_DE.UTF-8",
        "de_DE.utf8",
        "fr_FR.UTF-8",
        "German_Germany.1252",
        "French_France.1252",
    ):
        try:
            switched = locale.setlocale(locale.LC_TIME, candidate)
            break
        except locale.Error:
            continue
    try:
        result = _read(rows)
    finally:
        locale.setlocale(locale.LC_TIME, saved)
    print(f"LC_TIME during the read: {switched or saved}")
    assert result.failed == ()
    assert [r.start_time for r in result.rows] == [f"2021-{m:02d}-03T16:05:06+00:00" for m in range(1, 13)]
    # A localised month name is not English: the row fails on its date.
    failed = _read([_run("99", "Mai 3, 2021, 4:05:06 PM")]).failed
    assert [f.entry for f in failed] == ["activities.csv line 2"]


@pytest.mark.parametrize("lineterminator", ["\n", "\r\n"])
def test_a_cell_holding_crlf_keeps_its_cr(lineterminator: str) -> None:
    texts = {
        "1": "first line\r\nsecond line",
        "2": "a\nb",
        "3": 'he said "go"',
    }
    result = _read(
        [_run(k, **{"Activity Description": v}) for k, v in texts.items()], lineterminator=lineterminator
    )
    assert result.failed == ()
    got = {str(r.activity_id): r.columns["Activity Description"] for r in result.rows}
    assert got == texts
    assert {str(r.activity_id): r.description for r in result.rows} == texts
    # A lone CR inside a quoted cell is kept too (csv.writer leaves a lone CR unquoted under an LF
    # terminator, so this CSV is written by hand).
    header = ",".join(f'"{name}"' for name in SA.FULL_HEADER)
    cells = ["9", f'"{SA.DEFAULT_DATE}"', "Run 9", "Run", '"lone\rcarriage"'] + [""] * (
        len(SA.FULL_HEADER) - 5
    )
    csv_bytes = f"{header}{lineterminator}{','.join(cells)}{lineterminator}".encode()
    (lone,) = strava_csv.read_activities(_csv_zip(csv_bytes), "").rows
    assert lone.columns["Activity Description"] == "lone\rcarriage"


def test_an_empty_cell_reads_as_blank() -> None:
    result = _read(
        [
            _run("1", **{"Distance (2)": ""}),
            _run("2", **{"Distance (2)": "0.0"}),
            _run("3", **{"Distance (2)": "0.5"}),
        ]
    )
    assert result.failed == ()
    rows = {r.activity_id: r for r in result.rows}
    assert rows[1].distance_m is None
    assert rows[2].distance_m is None
    assert rows[3].distance_m == 0.5
    assert rows[1].columns["Distance (2)"] == ""
    assert rows[2].columns["Distance (2)"] == "0.0"
    assert rows[1].columns["Activity Description"] == ""
    assert rows[1].description == ""
    assert rows[1].private_note == ""
    assert all(isinstance(v, str) for v in rows[1].columns.values())


# ---------------------------------------------------------------------------
# job-level failures
# ---------------------------------------------------------------------------


def test_csv_past_the_cap_fails_naming_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    csv_bytes = SA.activities_csv([_run("1"), _run("2")])
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(csv_bytes))
    assert len(strava_csv.read_activities(_csv_zip(csv_bytes), "").rows) == 2
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(csv_bytes) - 1)
    with pytest.raises(strava_csv.StravaCsvError) as err:
        strava_csv.read_activities(_csv_zip(csv_bytes), "")
    print(err.value)
    assert f"{len(csv_bytes) - 1} byte limit" in str(err.value)
    assert "activities.csv" in str(err.value)


@pytest.mark.parametrize("bom", [False, True])
def test_a_non_utf8_csv_fails_naming_the_byte_offset(bom: bool) -> None:
    good = SA.activities_csv([_run("1")], bom=bom)
    offset = good.index(b"Run 1")
    bad = good[:offset] + b"\xff" + good[offset + 1 :]
    with pytest.raises(strava_csv.StravaCsvError) as err:
        strava_csv.read_activities(_csv_zip(bad), "")
    print(err.value)
    assert f"byte {offset}" in str(err.value)
    assert "UTF-8" in str(err.value)


def test_a_bom_is_accepted() -> None:
    result = _read([_run("5")], bom=True)
    assert result.failed == ()
    (got,) = result.rows
    assert got.activity_id == 5
    assert next(iter(got.columns)) == "Activity ID"


def test_a_missing_required_header_fails_naming_it() -> None:
    required = [
        "Activity ID",
        "Activity Date",
        "Activity Name",
        "Activity Type",
        "Filename",
        "Distance",
        "Distance (2)",
    ]
    assert list(strava_csv.REQUIRED_KEYS) == required
    for key in required:
        header = SA.header_without(key)
        with pytest.raises(strava_csv.StravaCsvError) as err:
            _read([[""] * len(header)], header=header)
        print(err.value)
        assert key in str(err.value), key
        assert "header" in str(err.value)
    # Both Distance columns gone: each is named.
    header = SA.header_without("Distance", SA.header_without("Distance (2)"))
    with pytest.raises(strava_csv.StravaCsvError) as err:
        _read([], header=header)
    assert "Distance, Distance (2)" in str(err.value)
    # An empty file has no header at all.
    with pytest.raises(strava_csv.StravaCsvError) as err:
        strava_csv.read_activities(_csv_zip(b""), "")
    assert "Activity ID" in str(err.value)


# ---------------------------------------------------------------------------
# failed rows, line numbers, order
# ---------------------------------------------------------------------------


def test_a_short_row_is_failed_with_its_raw_cells() -> None:
    long_row = list(_run("6").values()) + ["extra"]
    rows = [
        _run("1"),
        ["2", "Apr 18, 2022, 2:26:14 PM", "Short Run"],
        _run("3", "18/04/2022 14:26"),
        _run("4x"),
        long_row,
        _run("7"),
    ]
    result = _read(rows)
    assert [r.activity_id for r in result.rows] == [1, 7]
    assert [f.entry for f in result.failed] == [f"activities.csv line {n}" for n in (3, 4, 5, 6)]
    short, bad_date, bad_id, too_long = result.failed
    for failed in result.failed:
        print(failed.entry, "|", failed.detail)
    assert (
        '"2"' in short.detail
        and '"Apr 18, 2022, 2:26:14 PM"' in short.detail
        and '"Short Run"' in short.detail
    )
    assert "3 cells" in short.detail and "103" in short.detail
    assert short.activity_id == 2
    assert short.cells == ("2", "Apr 18, 2022, 2:26:14 PM", "Short Run")
    assert '"18/04/2022 14:26"' in bad_date.detail and "Activity Date" in bad_date.detail
    assert bad_date.activity_id == 3
    assert '"4x"' in bad_id.detail and "Activity ID" in bad_id.detail
    assert bad_id.activity_id is None
    assert "104 cells" in too_long.detail and '"Run 6"' in too_long.detail
    assert too_long.line == 6
    # A short row too short to hold the name quotes it as empty.
    (tiny,) = _read([["8"]]).failed
    assert '"8"' in tiny.detail and 'Activity Name ""' in tiny.detail


def test_line_n_counts_the_lines_of_a_multiline_cell() -> None:
    rows = [
        _run("1", **{"Activity Description": "a\nb\r\nc"}),  # lines 2-4
        _run("2", **{"Activity Private Note": "x\ny"}),  # lines 5-6
        ["3", "nope", "Bad Date Run"],  # line 7
    ]
    result = _read(rows)
    assert [(r.activity_id, r.line) for r in result.rows] == [(1, 2), (2, 5)]
    assert [f.entry for f in result.failed] == ["activities.csv line 7"]
    assert result.failed[0].line == 7


def test_rows_are_ordered_by_date_then_id() -> None:
    rows = [
        _run("30", "Jan 2, 2023, 8:00:00 AM"),
        _run("10", "Jan 2, 2023, 9:00:00 AM"),
        _run("20", "Jan 2, 2023, 8:00:00 AM"),
        _run("5", "Dec 31, 2022, 11:00:00 PM"),
        _run("100", "Jan 2, 2023, 8:00:00 AM"),
    ]
    result = _read(rows)
    assert [r.activity_id for r in result.rows] == [5, 20, 30, 100, 10]
