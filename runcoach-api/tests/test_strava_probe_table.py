"""F030 AC8 and AC5: the reference section 7 adversarial probe table, driven through ``POST /imports``.

One parametrised test, ``test_probe_table``, with one id per row. Each row builds its archive in
``tmp_path`` with ``tests/support/strava_archive.py``, posts it to the real app under ``with
TestClient(app)`` (the store is the autouse ``isolated_data_dir``), drains the worker with
``job.wait_idle`` and asserts what happened. The table of what was fed, what happened and why that
was intended is kept with the task's notes (its ``## Probe table`` section). Rows assert the counts
they care about key by key, never a whole ``counts`` dict, so a key added later leaves them green.

The rows were written alongside these tests, from the reference's sections 2, 3 and 7 and a review
list of degenerate inputs, by the same author: they are not an independent oracle. The import code
they drive was written earlier by other hands; where a row's behaviour is the code's reading of a
case the reference does not name (a media file in a Run row, a negative ``Distance (2)``), the
row's docstring says so.

``test_changing_strava_scores_leaves_the_load_body_and_every_metric_unchanged`` is AC5's
display-only check: Strava's own scores change between two imports, and the stored Strava-only
run's saved load body and every ``GET /metrics/load`` value stay byte-equal.

Every activity file is a copy of ``sample_run`` (README provenance: real run, run proof yes)
re-dated, re-measured or re-badged with ``fit_patch``; the caps are lowered with ``monkeypatch`` on
``archive.FIT_ENTRY_CAP`` (read at call time), so no row writes 50 MB.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sqlite3
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import archive, job, strava_csv
from runcoach_api.main import app


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "support" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")
fit_patch = SA.fit_patch

DRAIN_TIMEOUT_S = 120.0
CSV = "activities.csv"
SAMPLE = SA.fixture("sample_run")
BASE = SA.session_start(SAMPLE)  # 2026-09-01 12:34:21 UTC
DAY_S = SA.DAY_S
# A third device for three sessions on one second.
THIRD_PRODUCT = 3747


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _drain() -> None:
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"


# --- material ------------------------------------------------------------------------------------


@functools.cache
def _fit(seconds: int = 0, cm: int | None = None, product: int | None = None) -> bytes:
    """``sample_run`` moved by ``seconds``, with ``session.total_distance`` ``cm`` and
    ``file_id.product`` ``product`` when given (each patch repairs both CRCs)."""
    data = SA.shifted(SAMPLE, seconds) if seconds else SAMPLE
    if cm is not None:
        data = SA.with_distance(data, cm)
    if product is not None:
        data = fit_patch.set_field(data, SA.FILE_ID, SA.FILE_ID_PRODUCT, product)
    return data


def _day(days: int, cm: int | None = None, product: int | None = None) -> tuple[bytes, datetime]:
    """A ``sample_run`` copy ``days`` days off its own start, and that start."""
    return _fit(days * DAY_S, cm, product), BASE + timedelta(days=days)


def _row(aid: int | str, start: datetime | str, activity_type: str = "Run", **cells: str) -> dict[str, str]:
    date = start if isinstance(start, str) else SA.strava_date(start)
    cells.setdefault("Activity Name", f"row {aid}")
    return SA.row(**{"Activity ID": str(aid), "Activity Date": date, "Activity Type": activity_type, **cells})


def _raw_zip(path: Path, entries: Sequence[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def _zip_bytes(path: Path) -> bytes:
    return path.read_bytes()


# --- driving the app -----------------------------------------------------------------------------


def _import(client: TestClient, path: Path) -> dict:
    """Post an archive, drain the worker and return the finished import (any status)."""
    with path.open("rb") as fh:
        started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
    assert started.status_code == 202, started.text
    _drain()
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    report = done.json()
    print("status:", report["status"], "source:", report["source"], "error:", report["error"])
    print("counts:", report["counts"])
    for f in report["files"]:
        print("  file:", f["seq"], repr(f["entry"]), f["outcome"], f["status"], f["session_id"],
              f["strava_activity_id"], repr(f["detail"]))
    return report


def _completed(client: TestClient, path: Path) -> dict:
    report = _import(client, path)
    assert report["status"] == "completed", report["error"]
    return report


def _upload(client: TestClient, raw: bytes, name: str = "run.fit") -> str:
    response = client.post("/sessions", files={"file": (name, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _strava_rows(data_dir: Path) -> dict[int, dict]:
    conn = db.get_connection(data_dir)
    try:
        return {row["activity_id"]: row for row in db.read_strava_rows(conn)}
    finally:
        conn.close()


def _session_ids(data_dir: Path) -> list[str]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY upload_order")]
    finally:
        conn.close()


def _file(report: dict, aid: int) -> dict:
    rows = [f for f in report["files"] if f["strava_activity_id"] == aid]
    assert len(rows) == 1, (aid, rows)
    return rows[0]


def _outcome(report: dict, aid: int) -> str:
    return _file(report, aid)["outcome"]


def _strava_count(report: dict, key: str) -> int:
    return report["counts"]["strava"][key]


def _job_failed(client: TestClient, data_dir: Path, path: Path) -> str:
    """A job-level failure: ``failed`` with an ``error``, no report row, no ``strava_activities`` row."""
    report = _import(client, path)
    assert report["status"] == "failed"
    assert report["source"] == "strava"
    assert report["files"] == []
    assert _strava_rows(data_dir) == {}
    return report["error"]


# --- the rows: activities.csv as a whole ---------------------------------------------------------


def row_empty_activities_csv(client, data_dir, tmp_path, monkeypatch) -> None:
    """An empty ``activities.csv``: no header, so every required key is missing (section 2.2)."""
    error = _job_failed(client, data_dir, _raw_zip(tmp_path / "empty.zip", [(CSV, b"")]))
    assert error.startswith("StravaCsvError: activities.csv header lacks ")
    for key in strava_csv.REQUIRED_KEYS:
        assert key in error


def row_header_missing_distance_2(client, data_dir, tmp_path, monkeypatch) -> None:
    """A header with one ``Distance``: the job fails naming ``Distance (2)`` and only it (AC1)."""
    path = SA.strava_zip(tmp_path / "h.zip", [_row(1, BASE, "Ride")], header=SA.header_without("Distance (2)"))
    error = _job_failed(client, data_dir, path)
    assert error == "StravaCsvError: activities.csv header lacks Distance (2)"


def row_activities_csv_past_the_cap(client, data_dir, tmp_path, monkeypatch) -> None:
    """``activities.csv`` one byte past the cap fails the job naming the limit; at the cap it reads."""
    rows = [_row(i, BASE + timedelta(days=-i), "Ride") for i in range(1, 30)]
    data = SA.activities_csv(rows)
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(data) - 1)
    error = _job_failed(client, data_dir, _raw_zip(tmp_path / "big.zip", [(CSV, data)]))
    assert error == f"StravaCsvError: activities.csv exceeds the {len(data) - 1} byte limit"
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(data))
    report = _completed(client, _raw_zip(tmp_path / "at.zip", [(CSV, data)]))
    assert _strava_count(report, "not_running") == len(rows)


def row_non_utf8_csv(client, data_dir, tmp_path, monkeypatch) -> None:
    """A Latin-1 byte in a cell fails the job naming its byte offset, a BOM counted (section 2.2)."""
    for bom in (False, True):
        data = SA.activities_csv([_row(1, BASE, "Ride", **{"Activity Name": "Cafe_X"})], bom=bom)
        data = data.replace(b"Cafe_X", b"Caf\xe9 X")
        offset = data.index(b"\xe9")
        error = _job_failed(client, data_dir, _raw_zip(tmp_path / f"latin{bom}.zip", [(CSV, data)]))
        assert error == f"StravaCsvError: activities.csv is not UTF-8 at byte {offset}"


def row_two_activities_csv_entries(client, data_dir, tmp_path, monkeypatch) -> None:
    """Two root entries named ``activities.csv``: the first is read, the second ignored."""
    first = SA.activities_csv([_row(1, BASE, "Ride", **{"Activity Name": "first"})])
    second = SA.activities_csv([_row(2, BASE, "Ride", **{"Activity Name": "second"})])
    report = _completed(client, _raw_zip(tmp_path / "two.zip", [(CSV, first), (CSV, second)]))
    assert [f["strava_activity_id"] for f in report["files"]] == [1]
    assert {aid: r["name"] for aid, r in _strava_rows(data_dir).items()} == {1: "first"}


# --- the rows: one row's cells -------------------------------------------------------------------


def row_short_row(client, data_dir, tmp_path, monkeypatch) -> None:
    """A 12-cell row is ``failed`` at ``activities.csv line 2`` quoting its raw cells; its parsed
    ``Activity ID`` keeps a row (section 3: a failed row is kept when its ID parsed); the next row
    is processed."""
    short = list(_row(1, BASE, **{"Activity Name": "short"}).values())[:12]
    report = _completed(client, SA.strava_zip(tmp_path / "s.zip", [short, _row(2, BASE + timedelta(days=1), "Ride")]))
    failed = _file(report, 1)
    assert (failed["outcome"], failed["entry"]) == ("failed", f"{CSV} line 2")
    assert failed["detail"] == (
        f'row has 12 cells, the header 103: Activity ID "1", Activity Date "{SA.strava_date(BASE)}", '
        'Activity Name "short"'
    )
    assert _outcome(report, 2) == "not_running"
    rows = _strava_rows(data_dir)
    assert rows[1]["outcome"] == "failed" and len(rows[1]["columns"]) == 12


def row_blank_line(client, data_dir, tmp_path, monkeypatch) -> None:
    """A blank line between rows is not a row: skipped, neither failed nor counted; the line
    after it is still numbered physically (``line 4``)."""
    data = SA.activities_csv([_row(1, BASE, "Ride")])
    short = SA.activities_csv([list(_row(2, BASE).values())[:3]]).split(b"\n", 1)[1]
    data = data + b"\n" + short  # header line 1, the ride line 2, blank line 3, the short row line 4
    report = _completed(client, _raw_zip(tmp_path / "blank.zip", [(CSV, data)]))
    assert len(report["files"]) == 2
    assert _outcome(report, 1) == "not_running"
    failed = next(f for f in report["files"] if f["outcome"] == "failed")
    assert failed["entry"] == f"{CSV} line 4"
    assert report["progress"]["activities_total"] == 2


def _served_columns(client: TestClient, aid: int) -> dict[str, str]:
    response = client.get(f"/strava-activities/{aid}")
    assert response.status_code == 200, response.text
    return response.json()["columns"]


def row_newline_cell(client, data_dir, tmp_path, monkeypatch) -> None:
    """A description holding LF is served intact; the row after it starts on physical line 4."""
    text = "Bailed due to calf problems.\nWalked home."
    short = list(_row(2, BASE + timedelta(days=1)).values())[:5]
    path = SA.strava_zip(tmp_path / "lf.zip", [_row(1, BASE, "Ride", **{"Activity Description": text}), short])
    report = _completed(client, path)
    assert _outcome(report, 1) == "not_running"
    assert _served_columns(client, 1)["Activity Description"] == text
    assert _file(report, 2)["entry"] == f"{CSV} line 4"


def row_quote_cell(client, data_dir, tmp_path, monkeypatch) -> None:
    """A name holding a quote (written ``""`` inside a quoted cell) reads back as one quote."""
    name = 'The "easy" ride, mostly'
    report = _completed(client, SA.strava_zip(tmp_path / "q.zip", [_row(1, BASE, "Ride", **{"Activity Name": name})]))
    assert _outcome(report, 1) == "not_running"
    assert _served_columns(client, 1)["Activity Name"] == name
    assert _strava_rows(data_dir)[1]["name"] == name


def row_cr_inside_crlf_cell(client, data_dir, tmp_path, monkeypatch) -> None:
    """A CRLF-terminated CSV whose cells hold CRLF and a lone CR: both kept byte for byte."""
    note, description = "Foot pain\r\nblew up", "one\rtwo"
    rows = [_row(1, BASE, "Ride", **{"Activity Private Note": note, "Activity Description": description}),
            _row(2, BASE + timedelta(days=1), "Ride")]
    report = _completed(client, SA.strava_zip(tmp_path / "crlf.zip", rows, lineterminator="\r\n"))
    assert [_outcome(report, a) for a in (1, 2)] == ["not_running", "not_running"]
    served = _served_columns(client, 1)
    assert (served["Activity Private Note"], served["Activity Description"]) == (note, description)
    assert _strava_rows(data_dir)[2]["columns"]["Media"] == ""  # the record end is not part of a cell


def row_twelve_am_and_pm(client, data_dir, tmp_path, monkeypatch) -> None:
    """``12:34:21 AM`` is 00:34:21 UTC and ``12:34:21 PM`` 12:34:21 UTC: each links its own session."""
    am_fit, pm_fit = _fit(-10 * DAY_S - 12 * 3600), _fit(-10 * DAY_S)
    am_start, pm_start = BASE + timedelta(days=-10, hours=-12), BASE + timedelta(days=-10)
    am_id, pm_id = _upload(client, am_fit, "am.fit"), _upload(client, pm_fit, "pm.fit")
    rows = [_row(1, am_start), _row(2, pm_start)]
    assert rows[0]["Activity Date"].endswith(" 12:34:21 AM") and rows[1]["Activity Date"].endswith(" 12:34:21 PM")
    report = _completed(client, SA.strava_zip(tmp_path / "12.zip", rows))
    assert (_outcome(report, 1), _file(report, 1)["session_id"]) == ("attached", am_id)
    assert (_outcome(report, 2), _file(report, 2)["session_id"]) == ("attached", pm_id)
    assert _file(report, 1)["time_created"] == am_start.isoformat()


def row_dst_change_day(client, data_dir, tmp_path, monkeypatch) -> None:
    """Dates on the two 2026 North American DST changes, at local times that do not exist (02:30
    on 8 March) or occur twice (01:30 on 1 November): read as UTC, nothing moves."""
    targets = (datetime(2026, 3, 8, 2, 30, tzinfo=UTC), datetime(2026, 11, 1, 1, 30, tzinfo=UTC))
    rows, ids = [], []
    for aid, target in enumerate(targets, start=1):
        ids.append(_upload(client, _fit(int((target - BASE).total_seconds())), f"dst{aid}.fit"))
        rows.append(_row(aid, target))
    assert [r["Activity Date"] for r in rows] == ["Mar 8, 2026, 2:30:00 AM", "Nov 1, 2026, 1:30:00 AM"]
    report = _completed(client, SA.strava_zip(tmp_path / "dst.zip", rows))
    for aid, target, sid in zip((1, 2), targets, ids, strict=True):
        f = _file(report, aid)
        assert (f["outcome"], f["session_id"], f["time_created"]) == ("attached", sid, target.isoformat())


def row_activity_id_edges(client, data_dir, tmp_path, monkeypatch) -> None:
    """An ``Activity ID`` in Arabic-Indic digits and one of 2**63 on a row with a session at its
    second: both ``failed`` as rows that do not parse, no ``strava_activities`` row, the session
    not linked; the next row is processed."""
    fit, start = _day(-10)
    sid = _upload(client, fit)
    rows = [_row("\u0663\u0664\u0665", start - timedelta(days=1)), _row(2**63, start),
            _row(5, start + timedelta(days=1), "Ride")]
    report = _completed(client, SA.strava_zip(tmp_path / "ids.zip", rows))
    failed = [f for f in report["files"] if f["outcome"] == "failed"]
    assert len(failed) == 2 and all(f["strava_activity_id"] is None for f in failed)
    assert all(f["entry"].startswith(f"{CSV} line ") for f in failed)
    assert any("Activity ID does not parse" in f["detail"] for f in failed)
    assert any(str(2**63) in f["detail"] and "64-bit" in f["detail"] for f in failed)
    assert all(f["session_id"] is None for f in report["files"])
    assert _outcome(report, 5) == "not_running"
    assert list(_strava_rows(data_dir)) == [5]
    assert client.get(f"/sessions/{sid}").json()["strava"] is None


def row_duplicate_activity_id(client, data_dir, tmp_path, monkeypatch) -> None:
    """Two Run rows with one ``Activity ID``: the later by ``Activity Date`` is ``failed``
    (``duplicate_activity_id``) and writes nothing (section 3, planning 2026-10-31)."""
    older, newer = BASE + timedelta(days=-5), BASE + timedelta(days=-4)
    rows = [_row(77, newer, **{"Activity Name": "newer"}), _row(77, older, **{"Activity Name": "older"})]
    report = _completed(client, SA.strava_zip(tmp_path / "dup.zip", rows))
    first, second = report["files"]
    assert (first["outcome"], first["time_created"]) == ("no_file", older.isoformat())
    assert (second["outcome"], second["detail"], second["strava_activity_id"]) == ("failed", "duplicate_activity_id", 77)
    assert {a: r["name"] for a, r in _strava_rows(data_dir).items()} == {77: "older"}
    assert report["counts"]["failed"] == 1


# --- the rows: distance --------------------------------------------------------------------------


def row_zero_distance_run(client, data_dir, tmp_path, monkeypatch) -> None:
    """``Distance (2)`` ``0.0`` reads as no distance: attached with no warning on one candidate,
    and a Strava-only run stores with no warning."""
    fit, start = _day(-10)
    sid = _upload(client, fit)
    other, other_start = _day(-11)
    rows = [_row(1, start, **{"Distance (2)": "0.0"}),
            _row(2, other_start, **{"Distance (2)": "0.0", "Filename": "activities/2.fit.gz"})]
    report = _completed(client, SA.strava_zip(tmp_path / "z.zip", rows, [("activities/2.fit.gz", SA.gz(other))]))
    assert (_outcome(report, 1), _file(report, 1)["session_id"], _file(report, 1)["detail"]) == ("attached", sid, None)
    assert (_outcome(report, 2), _file(report, 2)["detail"]) == ("stored", None)
    assert _strava_count(report, "distance_differs") == 0


def row_distance_degenerate_forms(client, data_dir, tmp_path, monkeypatch) -> None:
    """Each row on its own one-candidate second. Row side, session 10,000 m: ``Distance (2)``
    empty, ``0.0``, ``nan``, ``inf``, ``-inf`` read as no distance (no warning); ``-10000.0``
    passes through as a distance, is never within 1 % and warns. Session side, row 10,000 m: no
    session distance (no warning), 0 m (present, warns), exactly 1.01 d and 0.99 d (no warning),
    10,100.01 m (warns). Every row stays linked."""
    cases = [  # (row distance cell, session centimetres or None for no distance, warns)
        ("", 1_000_000, False), ("0.0", 1_000_000, False), ("nan", 1_000_000, False),
        ("inf", 1_000_000, False), ("-inf", 1_000_000, False), ("-10000.0", 1_000_000, True),
        ("10000.0", SA.UINT32_INVALID, False), ("10000.0", 0, True), ("10000.0", 1_010_000, False),
        ("10000.0", 990_000, False), ("10000.0", 1_010_001, True),
    ]
    rows, sids = [], []
    for aid, (cell, cm, _warns) in enumerate(cases, start=1):
        fit, start = _day(-10 - aid, cm)
        sids.append(_upload(client, fit, f"d{aid}.fit"))
        rows.append(_row(aid, start, **{"Distance (2)": cell}))
    report = _completed(client, SA.strava_zip(tmp_path / "dist.zip", rows))
    stored = _strava_rows(data_dir)
    for aid, ((cell, cm, warns), sid) in enumerate(zip(cases, sids, strict=True), start=1):
        f = _file(report, aid)
        print(f"  d={cell!r} s_cm={cm} -> {f['outcome']} {f['detail']!r}")
        assert (f["outcome"], f["session_id"]) == ("attached", sid), (cell, cm)
        assert (f["detail"] == "warnings: distance_differs") is warns, (cell, cm)
        assert stored[aid]["warnings"] == (["distance_differs"] if warns else []), (cell, cm)
    assert _strava_count(report, "distance_differs") == sum(w for *_, w in cases)


# --- the rows: candidates on one second ----------------------------------------------------------


def row_candidates_on_one_second(client, data_dir, tmp_path, monkeypatch) -> None:
    """Two candidates uploaded in both orders and three candidates: the single one within 1 %
    links. A second row on a pair whose tiebreak winner is already linked is ``ambiguous``
    naming both and links nothing."""
    def pair(days: int, order: tuple[int, ...]) -> tuple[dict[int, str], datetime]:
        products = {0: None, 1: SA.OTHER_PRODUCT, 2: THIRD_PRODUCT}
        cms = {0: 1_000_000, 1: 309_000, 2: 500_000}
        ids = {}
        for k in order:
            fit, start = _day(days, cms[k], products[k])
            ids[k] = _upload(client, fit, f"c{days}_{k}.fit")
        return ids, start

    forward, f_start = pair(-10, (0, 1))
    reverse, r_start = pair(-11, (1, 0))
    three, t_start = pair(-12, (2, 1, 0))
    taken, k_start = pair(-13, (0, 1))
    d = {"Distance (2)": "10000.0"}
    rows = [_row(1, f_start, **d), _row(2, r_start, **d), _row(3, t_start, **d),
            _row(4, k_start, **d), _row(5, k_start, **d)]
    report = _completed(client, SA.strava_zip(tmp_path / "ties.zip", rows))
    for aid, ids in ((1, forward), (2, reverse), (3, three), (4, taken)):
        assert (_outcome(report, aid), _file(report, aid)["session_id"]) == ("attached", ids[0])
    loser = _file(report, 5)
    assert (loser["outcome"], loser["session_id"]) == ("ambiguous", None)
    assert loser["detail"] == f"candidates: {taken[0]}, {taken[1]}"
    assert _strava_rows(data_dir)[5]["linked_session_id"] is None


# --- the rows: activity files --------------------------------------------------------------------


def row_gz_past_the_cap(client, data_dir, tmp_path, monkeypatch) -> None:
    """A ``.gz`` inflating past the cap: ``failed`` with status 413 naming the limit; the row is
    kept; the next row is processed."""
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", SA.GZ_BOMB_CAP)
    rows = [_row(1, BASE + timedelta(days=-10), Filename="activities/1.fit.gz"),
            _row(2, BASE + timedelta(days=-9), "Ride")]
    report = _completed(client, SA.strava_zip(tmp_path / "bomb.zip", rows, [("activities/1.fit.gz", SA.gz_bomb())]))
    bomb = _file(report, 1)
    assert (bomb["outcome"], bomb["status"]) == ("failed", 413)
    assert bomb["detail"] == f"file_too_large: upload exceeds {SA.GZ_BOMB_CAP} byte limit"
    assert _outcome(report, 2) == "not_running"
    assert _strava_rows(data_dir)[1]["outcome"] == "failed"


def row_corrupt_gz(client, data_dir, tmp_path, monkeypatch) -> None:
    """A ``.gz`` that does not inflate: ``failed`` naming the error (a ``FileError`` with detail)."""
    rows = [_row(1, BASE + timedelta(days=-10), Filename="activities/1.fit.gz")]
    report = _completed(client, SA.strava_zip(tmp_path / "c.zip", rows, [("activities/1.fit.gz", SA.CORRUPT_GZ)]))
    f = _file(report, 1)
    assert (f["outcome"], f["status"]) == ("failed", None)
    assert f["detail"].startswith("file_not_read: ") and len(f["detail"]) > len("file_not_read: ")


def row_sessions_in_the_file(client, data_dir, tmp_path, monkeypatch) -> None:
    """Run rows with no session at their CSV start whose FIT holds zero, one, two or five
    ``session`` messages, and one whose single session's start cannot be read: ``failed``
    (``no_session_message``), ``stored``, ``multisport_not_stored`` twice, ``stored`` (no file
    start, so no candidates: step 3). A missing file is a ``FileError`` without detail."""
    def multi(fit: bytes, n: int) -> bytes:
        for _ in range(n - 1):
            fit = SA.two_session_copy(fit)
        return fit

    files = {
        1: SA.no_session_copy(_day(-10)[0]),
        2: _day(-11)[0],
        3: multi(_day(-12)[0], 2),
        4: multi(_day(-13)[0], 5),
        5: SA.unstarted_copy(_day(-14)[0]),
    }
    rows = [_row(aid, BASE + timedelta(days=-9 - aid), Filename=f"activities/{aid}.fit.gz") for aid in files]
    rows.append(_row(6, BASE + timedelta(days=-20), Filename="activities/missing.fit.gz"))
    entries = [(f"activities/{aid}.fit.gz", SA.gz(data)) for aid, data in files.items()]
    report = _completed(client, SA.strava_zip(tmp_path / "sessions.zip", rows, entries))
    assert (_outcome(report, 1), _file(report, 1)["detail"]) == ("failed", "no_session_message")
    assert _outcome(report, 2) == "stored"
    assert _outcome(report, 3) == "multisport_not_stored" and _outcome(report, 4) == "multisport_not_stored"
    assert _outcome(report, 5) == "stored"
    assert (_outcome(report, 6), _file(report, 6)["detail"]) == ("failed", "file_not_in_archive")
    assert len(_session_ids(data_dir)) == 2
    assert _strava_count(report, "multisport_not_stored") == 2


def row_fit_with_no_session_message(client, data_dir, tmp_path, monkeypatch) -> None:
    """A FIT with no ``session`` message in a Run row: ``failed`` (``no_session_message``), nothing stored."""
    fit, start = _day(-10)
    rows = [_row(1, start + timedelta(minutes=5), Filename="activities/1.fit.gz")]
    report = _completed(client, SA.strava_zip(tmp_path / "ns.zip", rows, [("activities/1.fit.gz", SA.gz(SA.no_session_copy(fit)))]))
    assert (_outcome(report, 1), _file(report, 1)["detail"]) == ("failed", "no_session_message")
    assert _session_ids(data_dir) == []


def _landing(tmp_path: Path, data_dir: Path) -> set[Path]:
    cwd = Path.cwd()
    return {tmp_path, tmp_path.parent, data_dir, data_dir.parent, cwd, cwd.parent, Path("C:/"), Path("C:/activities")}


def row_filename_with_dot_dot(client, data_dir, tmp_path, monkeypatch) -> None:
    """A ``Filename`` is a label: ``activities/../activities/a.fit.gz`` names no entry
    (``file_not_in_archive``, never normalised), and ``../escape.fit.gz``, an entry of exactly
    that name, is read in memory and stored. Nothing is written outside the store."""
    a, a_start = _day(-10)
    b, b_start = _day(-11)
    rows = [_row(1, a_start + timedelta(minutes=5), Filename="activities/../activities/a.fit.gz"),
            _row(2, b_start + timedelta(minutes=5), Filename="../escape_q9.fit.gz")]
    path = _raw_zip(tmp_path / "dots.zip", [
        (CSV, SA.activities_csv(rows)), ("activities/a.fit.gz", SA.gz(a)), ("../escape_q9.fit.gz", SA.gz(b))])
    with zipfile.ZipFile(path) as zf:
        assert "../escape_q9.fit.gz" in zf.namelist()
    report = _completed(client, path)
    assert (_outcome(report, 1), _file(report, 1)["detail"]) == ("failed", "file_not_in_archive")
    assert _outcome(report, 2) == "stored"
    hits = [d / n for d in _landing(tmp_path, data_dir) for n in ("escape_q9.fit.gz", "escape_q9.fit")
            if (d / n).exists()]
    assert hits == []


def row_filename_with_drive_letter(client, data_dir, tmp_path, monkeypatch) -> None:
    """``C:/activities/d.fit.gz`` and ``C:\\activities\\d.fit.gz`` name no entry (the entry is
    ``activities/d.fit.gz``): ``file_not_in_archive``; nothing is opened on disk."""
    fit, start = _day(-10)
    rows = [_row(1, start + timedelta(minutes=5), Filename="C:/activities/d.fit.gz"),
            _row(2, start + timedelta(minutes=6), Filename="C:\\activities\\d.fit.gz")]
    report = _completed(client, SA.strava_zip(tmp_path / "drive.zip", rows, [("activities/d.fit.gz", SA.gz(fit))]))
    for aid in (1, 2):
        assert (_outcome(report, aid), _file(report, aid)["detail"]) == ("failed", "file_not_in_archive")
    assert _session_ids(data_dir) == []
    assert not Path("C:/activities/d.fit.gz").exists()


def row_filename_pointing_at_media(client, data_dir, tmp_path, monkeypatch) -> None:
    """Run rows whose ``Filename`` is a ``.jpg`` and an ``.mp4`` present in the archive: neither
    FIT, GPX nor TCX, so not decoded: ``not_fit``, counted by extension. The reference's step 4
    names GPX and TCX; a media file is the code's reading of "not a FIT file"."""
    rows = [_row(1, BASE + timedelta(days=-10), Filename="media/a.jpg"),
            _row(2, BASE + timedelta(days=-11), Filename="media/b.mp4")]
    files = [("media/a.jpg", b"\xff\xd8\xff\xe0 not decoded"), ("media/b.mp4", b"\x00\x00\x00\x18ftypmp42")]
    report = _completed(client, SA.strava_zip(tmp_path / "media.zip", rows, files))
    assert [_outcome(report, a) for a in (1, 2)] == ["not_fit", "not_fit"]
    assert report["counts"]["not_fit"].get("jpg") == 1 and report["counts"]["not_fit"].get("mp4") == 1
    assert _session_ids(data_dir) == []


def row_activity_types_and_extensions(client, data_dir, tmp_path, monkeypatch) -> None:
    """``Ride``, ``Walk``, ``Workout``, ``Hike`` and lower-case ``run`` rows whose files are not
    FIT: ``not_running``, never read (a read would fail them). A ``Trail Run`` and an upper-case
    ``.FIT.GZ`` Run are decoded and stored; an upper-case ``.TCX`` Run is ``not_fit``."""
    junk = b"these bytes are not a FIT file"
    types = ("Ride", "Walk", "Workout", "Hike", "run")
    rows = [_row(i, BASE + timedelta(days=-10 - i), t, Filename=f"activities/{i}.fit.gz")
            for i, t in enumerate(types, start=1)]
    files = [(f"activities/{i}.fit.gz", junk) for i in range(1, len(types) + 1)]
    trail, trail_start = _day(-20)
    upper, upper_start = _day(-21)
    rows += [_row(10, trail_start + timedelta(minutes=5), "Trail Run", Filename="activities/10.fit.gz"),
             _row(11, upper_start + timedelta(minutes=5), Filename="activities/11.FIT.GZ"),
             _row(12, BASE + timedelta(days=-22), Filename="activities/12.TCX")]
    files += [("activities/10.fit.gz", SA.gz(trail)), ("activities/11.FIT.GZ", SA.gz(upper)),
              ("activities/12.TCX", b'<?xml version="1.0"?><TrainingCenterDatabase/>')]
    report = _completed(client, SA.strava_zip(tmp_path / "types.zip", rows, files))
    assert [_outcome(report, i) for i in range(1, len(types) + 1)] == ["not_running"] * len(types)
    assert [_outcome(report, i) for i in (10, 11, 12)] == ["stored", "stored", "not_fit"]
    assert report["counts"]["not_fit"].get("tcx") == 1
    assert report["counts"]["failed"] == 0


# --- the rows: archive layouts -------------------------------------------------------------------


def _one_attached_one_ride(client: TestClient) -> tuple[list[dict], list[tuple[str, bytes]], str]:
    fit, start = _day(-10)
    sid = _upload(client, fit)
    rows = [_row(1, start, Filename="activities/1.fit.gz"), _row(2, start + timedelta(days=1), "Ride")]
    return rows, [("activities/1.fit.gz", SA.gz(fit))], sid


def _strava_outcomes(report: dict, sid: str) -> None:
    assert report["source"] == "strava"
    assert (_outcome(report, 1), _file(report, 1)["session_id"]) == ("attached", sid)
    assert _outcome(report, 2) == "not_running"


def row_rezipped_folder(client, data_dir, tmp_path, monkeypatch) -> None:
    """The export re-zipped under one folder: Strava rules, the folder prefix ignored for every
    ``Filename`` (section 2.1)."""
    rows, files, sid = _one_attached_one_ride(client)
    _strava_outcomes(_completed(client, SA.strava_zip(tmp_path / "f.zip", rows, files, layout="folder")), sid)


def row_macosx_rezip(client, data_dir, tmp_path, monkeypatch) -> None:
    """A macOS re-zip: the folder and a ``__MACOSX/`` tree beside it (AppleDouble
    ``._activities.csv``): still one folder, Strava rules."""
    rows, files, sid = _one_attached_one_ride(client)
    _strava_outcomes(_completed(client, SA.strava_zip(tmp_path / "m.zip", rows, files, layout="macosx")), sid)


def row_folder_beside_other_top_level_entries(client, data_dir, tmp_path, monkeypatch) -> None:
    """A folder holding ``activities.csv`` beside a root file, or beside a second folder: not a
    Strava archive, so Garmin rules: no FIT entry, the CSV and the ``.gz`` counted under
    ``not_fit``, no ``strava_activities`` row."""
    fit, start = _day(-10)
    csv_bytes = SA.activities_csv([_row(1, start, Filename="activities/1.fit.gz")])
    inside = [(f"{SA.FOLDER}/{CSV}", csv_bytes), (f"{SA.FOLDER}/activities/1.fit.gz", SA.gz(fit))]
    for label, extra in (("file", ("notes.txt", b"x")), ("folder", ("other/readme.txt", b"x"))):
        report = _completed(client, _raw_zip(tmp_path / f"beside_{label}.zip", [*inside, extra]))
        assert report["source"] == "garmin" and report["files"] == []
        assert report["counts"]["not_fit"].get("csv") == 1 and report["counts"]["not_fit"].get("gz") == 1
        assert report["counts"]["not_fit"].get("txt") == 1
    assert _strava_rows(data_dir) == {} and _session_ids(data_dir) == []


def row_strava_archive_inside_a_garmin_archive(client, data_dir, tmp_path, monkeypatch) -> None:
    """A Strava export as an inner zip of a Garmin export: the outer archive is Garmin's (no
    ``activities.csv`` at its root or in its one folder holding it); the inner zip is visited by
    Garmin's rules, its CSV and ``.gz`` files counted under ``not_fit``; its loose FIT stored."""
    fit, start = _day(-10)
    inner = SA.strava_zip(tmp_path / "inner.zip", [_row(1, start + timedelta(minutes=5), Filename="activities/1.fit.gz")],
                          [("activities/1.fit.gz", SA.gz(fit))])
    path = _raw_zip(tmp_path / "garmin.zip", [
        ("DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip", _zip_bytes(inner)),
        ("DI_CONNECT/DI-Connect-Uploaded-Files/run.fit", _day(-11)[0])])
    report = _completed(client, path)
    assert report["source"] == "garmin"
    assert [(f["entry"], f["outcome"]) for f in report["files"]] == [("DI_CONNECT/DI-Connect-Uploaded-Files/run.fit", "stored")]
    assert report["counts"]["not_fit"].get("csv") == 1 and report["counts"]["not_fit"].get("gz") == 1
    assert _strava_rows(data_dir) == {}


def row_garmin_archive_inside_a_strava_archive(client, data_dir, tmp_path, monkeypatch) -> None:
    """The reverse: a Strava archive holding a Garmin export zip: Strava rules; only the CSV's rows
    are processed, the inner zip is never opened (its FIT is not stored)."""
    garmin = _raw_zip(tmp_path / "g.zip", [("DI_CONNECT/run.fit", _day(-11)[0])])
    path = SA.strava_zip(tmp_path / "s.zip", [_row(1, BASE + timedelta(days=-10), "Ride")],
                         extra=[("garmin_export.zip", _zip_bytes(garmin))])
    report = _completed(client, path)
    assert report["source"] == "strava"
    assert [(f["strava_activity_id"], f["outcome"]) for f in report["files"]] == [(1, "not_running")]
    assert _session_ids(data_dir) == []


def row_garmin_archive_with_activities_csv_below_the_root(client, data_dir, tmp_path, monkeypatch) -> None:
    """A Garmin export whose one folder holds an ``activities.csv`` two levels down: not recognised
    (it must sit at the root or directly in the one folder); Garmin rules store its FIT."""
    csv_bytes = SA.activities_csv([_row(1, BASE + timedelta(days=-10), "Ride")])
    path = _raw_zip(tmp_path / "below.zip", [
        ("DI_CONNECT/DI-Connect-Fitness/activities.csv", csv_bytes),
        ("DI_CONNECT/DI-Connect-Uploaded-Files/run.fit", _day(-11)[0])])
    report = _completed(client, path)
    assert report["source"] == "garmin"
    assert [f["outcome"] for f in report["files"]] == ["stored"]
    assert report["counts"]["not_fit"].get("csv") == 1
    assert _strava_rows(data_dir) == {}


# --- the rows: running it again ------------------------------------------------------------------


def row_reimport(client, data_dir, tmp_path, monkeypatch) -> None:
    """The same archive twice: every row ``unchanged``. A newer archive: a linked row with a new
    description ``updated`` (link kept); an unlinked row with a new description matched again
    (``not_running``); a missing row ``deleted_in_strava`` and unlinked (reported once); a linked
    row whose ``Activity Date`` no longer parses ``failed`` keeping its live link; an unlinked one
    ``failed`` with no link. That newer archive again: every row ``unchanged`` but the two failed
    ones, processed again and ``failed`` again (athlete ruling 2026-11-06), the deleted row not
    reported."""
    sids = {}
    rows1 = []
    for aid, days in ((1, -10), (3, -12), (4, -13)):
        fit, start = _day(days)
        sids[aid] = _upload(client, fit, f"r{aid}.fit")
        rows1.append(_row(aid, start))
    for aid, days in ((2, -11), (5, -14), (6, -15)):
        rows1.append(_row(aid, BASE + timedelta(days=days), "Ride"))
    first = _completed(client, SA.strava_zip(tmp_path / "one.zip", rows1))
    again = _completed(client, SA.strava_zip(tmp_path / "one_again.zip", rows1))
    assert [_outcome(first, a) for a in range(1, 7)] == ["attached", "not_running", "attached", "attached",
                                                         "not_running", "not_running"]
    assert {f["outcome"] for f in again["files"]} == {"unchanged"}
    assert _file(again, 1)["session_id"] == sids[1]

    by_id = {int(r["Activity ID"]): dict(r) for r in rows1}
    by_id[3]["Activity Description"] = "new words"
    by_id[2]["Activity Description"] = "new words"
    by_id[4]["Activity Date"] = "18 avr. 2022 14:26:14"
    by_id[6]["Activity Date"] = "18 avr. 2022 14:26:14"
    rows2 = [by_id[a] for a in (1, 2, 3, 4, 6)]  # 5 is absent
    second = _completed(client, SA.strava_zip(tmp_path / "two.zip", rows2))
    assert _outcome(second, 1) == "unchanged"
    assert (_outcome(second, 3), _file(second, 3)["session_id"]) == ("updated", sids[3])
    assert client.get(f"/sessions/{sids[3]}").json()["strava"]["description"] == "new words"
    assert _outcome(second, 2) == "not_running"
    deleted = _file(second, 5)
    assert (deleted["outcome"], deleted["entry"]) == ("deleted_in_strava", f"{CSV}#5")
    assert (_outcome(second, 4), _file(second, 4)["session_id"]) == ("failed", sids[4])
    assert (_outcome(second, 6), _file(second, 6)["session_id"]) == ("failed", None)
    stored = _strava_rows(data_dir)
    assert stored[5]["deleted_in_strava"] is True and stored[5]["linked_session_id"] is None
    assert stored[4]["linked_session_id"] == sids[4] and stored[4]["outcome"] == "failed"
    assert _strava_count(second, "deleted_in_strava") == 1

    third = _completed(client, SA.strava_zip(tmp_path / "two_again.zip", rows2))
    assert sorted(f["strava_activity_id"] for f in third["files"]) == [1, 2, 3, 4, 6]
    # The two failed rows are processed again and fail again (athlete ruling 2026-11-06).
    assert {a: _outcome(third, a) for a in (1, 2, 3, 4, 6)} == {
        1: "unchanged", 2: "unchanged", 3: "unchanged", 4: "failed", 6: "failed"}
    assert _file(third, 4)["session_id"] == sids[4]
    assert _file(third, 6)["session_id"] is None
    assert _strava_count(third, "deleted_in_strava") == 0


ROWS: dict[str, Callable] = {
    "empty_activities_csv": row_empty_activities_csv,
    "header_missing_distance_2": row_header_missing_distance_2,
    "activities_csv_past_the_cap": row_activities_csv_past_the_cap,
    "non_utf8_csv": row_non_utf8_csv,
    "two_activities_csv_entries": row_two_activities_csv_entries,
    "short_row": row_short_row,
    "blank_line": row_blank_line,
    "newline_cell": row_newline_cell,
    "quote_cell": row_quote_cell,
    "cr_inside_crlf_cell": row_cr_inside_crlf_cell,
    "twelve_am_and_pm": row_twelve_am_and_pm,
    "dst_change_day": row_dst_change_day,
    "activity_id_edges": row_activity_id_edges,
    "duplicate_activity_id": row_duplicate_activity_id,
    "zero_distance_run": row_zero_distance_run,
    "distance_degenerate_forms": row_distance_degenerate_forms,
    "candidates_on_one_second": row_candidates_on_one_second,
    "gz_past_the_cap": row_gz_past_the_cap,
    "corrupt_gz": row_corrupt_gz,
    "fit_with_no_session_message": row_fit_with_no_session_message,
    "sessions_in_the_file": row_sessions_in_the_file,
    "filename_with_dot_dot": row_filename_with_dot_dot,
    "filename_with_drive_letter": row_filename_with_drive_letter,
    "filename_pointing_at_media": row_filename_pointing_at_media,
    "activity_types_and_extensions": row_activity_types_and_extensions,
    "rezipped_folder": row_rezipped_folder,
    "macosx_rezip": row_macosx_rezip,
    "folder_beside_other_top_level_entries": row_folder_beside_other_top_level_entries,
    "strava_archive_inside_a_garmin_archive": row_strava_archive_inside_a_garmin_archive,
    "garmin_archive_inside_a_strava_archive": row_garmin_archive_inside_a_strava_archive,
    "garmin_archive_with_activities_csv_below_the_root": row_garmin_archive_with_activities_csv_below_the_root,
    "reimport": row_reimport,
}


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("row", list(ROWS), ids=list(ROWS))
def test_probe_table(row: str, isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        ROWS[row](client, isolated_data_dir, tmp_path, monkeypatch)
    _drain()


# --- AC5: Strava's scores are display-only -------------------------------------------------------

SCORE_KEYS = ("Relative Effort", "Relative Effort (2)", "Training Load")


def _saved_load(data_dir: Path, session_id: str) -> str:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        row = conn.execute(
            f"SELECT body FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no saved load for {session_id}"
    return row[0]


def test_changing_strava_scores_leaves_the_load_body_and_every_metric_unchanged(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """Import a Strava-only run (stored), change ``Relative Effort``, ``Relative Effort (2)`` and
    ``Training Load`` on its row, import again: the row is ``updated`` and serves the new scores,
    and its saved load body and the whole ``GET /metrics/load`` body are byte-equal to before."""
    fit, start = _day(-10)
    scores1 = {"Relative Effort": "25", "Relative Effort (2)": "25.0", "Training Load": "80"}
    scores2 = {"Relative Effort": "250", "Relative Effort (2)": "250.0", "Training Load": "800"}
    files = [("activities/1.fit.gz", SA.gz(fit))]
    with TestClient(app) as client:
        first = _completed(client, SA.strava_zip(
            tmp_path / "one.zip", [_row(1, start + timedelta(minutes=5), **{"Filename": "activities/1.fit.gz", **scores1})], files))
        sid = _file(first, 1)["session_id"]
        assert _outcome(first, 1) == "stored" and sid is not None
        body_before = _saved_load(isolated_data_dir, sid)
        chart_before = client.get("/metrics/load")
        assert chart_before.status_code == 200, chart_before.text
        print("saved load before:", body_before)
        assert json.loads(body_before)["session_load"]["value"] is not None

        second = _completed(client, SA.strava_zip(
            tmp_path / "two.zip", [_row(1, start + timedelta(minutes=5), **{"Filename": "activities/1.fit.gz", **scores2})], files))
        assert (_outcome(second, 1), _file(second, 1)["session_id"]) == ("updated", sid)
        served = client.get("/strava-activities/1").json()["columns"]
        assert {k: served[k] for k in SCORE_KEYS} == scores2
        chart_after = client.get("/metrics/load")
    body_after = _saved_load(isolated_data_dir, sid)
    print("saved load after: ", body_after)
    assert body_after == body_before
    assert chart_after.content == chart_before.content
    assert json.loads(chart_after.content)["days"], "the chart holds no day: the comparison would be vacuous"
