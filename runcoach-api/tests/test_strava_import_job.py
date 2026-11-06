"""F030 end to end: a first Strava import through ``POST /imports`` (AC1-AC4, AC6, AC8).

Every test posts a Strava archive built in ``tmp_path`` by ``tests/support/strava_archive.py`` to
the real app under ``with TestClient(app)`` (the store is the autouse ``isolated_data_dir``, or a
second store under ``tmp_path`` pointed at with ``_use_store``), drains the worker with
``job.wait_idle`` and reads the report, the ``strava_activities`` rows and the sessions.

The expectations come from the catalogue's ``Case`` fields (written with the test material, from
the reference) and from the reference's sections 2-3 directly; the job was written after them by
the same author, so they are not an independent oracle. One catalogue label is not used as
written: the two rows sharing one ``Activity ID`` are ordered by ``Activity Date`` (reference
section 2.6, and section 3's "the later row by section 2.6's order is failed"), not by their CSV
order, so the full-catalogue test derives which of the two fails from their dates.

Sessions a test stores first are uploaded through ``POST /sessions`` with the copies the
catalogue builds from ``sample_run`` (README provenance: real run, run proof yes).
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import shutil
import sqlite3
import zipfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.config import AppConfig
from runcoach_api.imports import archive, job, strava, strava_files
from runcoach_api.main import app


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "support" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")
IA = _load("import_archive")

DRAIN_TIMEOUT_S = 120.0
CSV = "activities.csv"
# Columns that differ between two stores by construction (F020 AC1's exclusions).
EXCLUDED_COLUMNS = {"session_id", "upload_order", "import_id", "laps_read_at"}
EXCLUDED_CONTEXT_KEYS = {"ingested_at"}


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _drain() -> None:
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"


def _use_store(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    """Point the API's config at ``data_dir``."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone="UTC",
    )
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: config)
    db._load_config_cached.cache_clear()
    assert db._load_config_cached().data_dir == data_dir


def _post(client: TestClient, path: Path):
    with path.open("rb") as fh:
        return client.post("/imports", files={"file": (path.name, fh, "application/zip")})


def _import(client: TestClient, path: Path) -> dict:
    started = _post(client, path)
    assert started.status_code == 202, started.text
    _drain()
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    return done.json()


def _upload(client: TestClient, post_fit_bytes, files) -> dict[str, str]:
    """Upload each ``(name, FIT bytes)`` through ``POST /sessions``; their session ids by name."""
    ids = {}
    for name, raw in files:
        response = post_fit_bytes(client, name, raw)
        assert response.status_code == 201, (name, response.text)
        ids[name] = response.json()["session_id"]
    return ids


def _strava_rows(data_dir: Path) -> dict[int, dict]:
    conn = db.get_connection(data_dir)
    try:
        return {row["activity_id"]: row for row in db.read_strava_rows(conn)}
    finally:
        conn.close()


def _sessions(data_dir: Path) -> list[dict]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY upload_order")]
    finally:
        conn.close()


def _comparable(row: dict, also: frozenset[str] = frozenset()) -> dict:
    out = {k: v for k, v in row.items() if k not in EXCLUDED_COLUMNS | also}
    context = json.loads(out["context"]) if out["context"] is not None else None
    if isinstance(context, dict):
        for key in EXCLUDED_CONTEXT_KEYS:
            assert key in context, f"context has no {key}: the exclusion would be vacuous"
            context.pop(key)
    out["context"] = context
    return out


def _case_id(case) -> int:
    return int(case.cells["Activity ID"] if isinstance(case.cells, dict) else case.cells[0])


def _by_id(report: dict) -> dict[int | None, list[dict]]:
    out: dict[int | None, list[dict]] = {}
    for row in report["files"]:
        out.setdefault(row["strava_activity_id"], []).append(row)
    return out


def _one(report: dict, activity_id: int) -> dict:
    rows = _by_id(report).get(activity_id, [])
    assert len(rows) == 1, (activity_id, rows)
    return rows[0]


def _cases(*keys: str):
    return [SA.by_key(k) for k in keys]


def _import_cases(client, post_fit_bytes, tmp_path: Path, keys, **zip_kw) -> tuple[dict, dict[str, str]]:
    cases = _cases(*keys)
    ids = _upload(client, post_fit_bytes, SA.stored_files(cases))
    path = SA.case_zip(tmp_path / f"strava_{zip_kw.get('layout', 'root')}.zip", cases, **zip_kw)
    return _import(client, path), ids


def _run_row(start, name: str = "run", **cells: str) -> dict[str, str]:
    """A Run row whose file ``activities/<id>.fit.gz`` the caller places."""
    return SA.row(**{"Activity Date": SA.strava_date(start), "Activity Type": "Run", "Activity Name": name, **cells})


# --- AC1: recognition and the report's source ------------------------------------------------------


def test_a_strava_archive_reports_every_outcome_with_source_strava(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """The whole catalogue in one archive: every row's outcome, link, match, warning and failure
    as its case says, the counts tallying them, and ``source`` ``strava``."""
    cases = SA.outcome_rows()
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files())
        monkeypatch.setattr(archive, "FIT_ENTRY_CAP", SA.GZ_BOMB_CAP)
        report = _import(client, SA.case_zip(tmp_path / "export.zip"))
    assert report["status"] == "completed", report["error"]
    assert report["source"] == "strava"
    assert report["progress"]["activities_total"] == len(cases) == len(report["files"])
    assert [r["seq"] for r in report["files"]] == list(range(1, len(cases) + 1))

    # The two rows sharing one Activity ID: the later by Activity Date is failed.
    dup_id = _case_id(SA.by_key("failed_duplicate_activity_id"))
    pair = [c for c in cases if isinstance(c.cells, dict) and c.cells["Activity ID"] == str(dup_id)]
    assert len(pair) == 2
    later = max(pair, key=lambda c: strava.strava_csv.parse_activity_date(c.cells["Activity Date"]))
    print("  duplicate pair (key, date):", [(c.key, c.cells["Activity Date"]) for c in pair], "later:", later.key)
    assert later.key == "failed_duplicate_activity_id"

    by_id = _by_id(report)
    expected_counts: Counter[str] = Counter()
    for case in cases:
        outcome = case.outcome
        expected_counts[outcome] += 1
        if case.key == "failed_bad_activity_id":
            row = next(r for r in by_id[None] if r["outcome"] == "failed")
        elif case in pair:
            row = next(r for r in by_id[dup_id] if r["outcome"] == outcome)
        else:
            row = _one(report, _case_id(case))
        print(f"  {case.key}: {row['outcome']} {row['entry']} {row['status']} {row['detail']}")
        assert row["outcome"] == outcome, case.key
        if case.link:
            assert row["session_id"] == ids[case.link], case.key
        elif outcome in ("stored", "stored_retyped"):
            assert row["session_id"] is not None and row["session_id"] not in ids.values(), case.key
        else:
            assert row["session_id"] is None, case.key
        if case.warnings:
            assert row["detail"] == "warnings: distance_differs", case.key
        if case.failure in ("short_row", "bad_activity_date", "bad_activity_id"):
            assert row["entry"].startswith(f"{CSV} line "), case.key
        elif isinstance(case.cells, dict) and case.failure is None:
            assert row["entry"] == (case.cells["Filename"] or f"{CSV}#{_case_id(case)}"), case.key
            assert row["time_created"] == strava.strava_csv.parse_activity_date(case.cells["Activity Date"])
            assert row["sport"] == case.cells["Activity Type"], case.key
        failure_text = {
            "file_not_in_archive": "file_not_in_archive",
            "no_session_message": "no_session_message",
            "corrupt_gz": "file_not_read",
            "over_cap": "file_too_large",
            "fit_does_not_decode": "fit_not_decoded",
            "duplicate_activity_id": "duplicate_activity_id",
            "short_row": "cells",
            "bad_activity_date": "Activity Date does not parse",
            "bad_activity_id": "Activity ID does not parse",
        }
        failure = "duplicate_activity_id" if case is later else None if case in pair else case.failure
        if failure is not None:
            assert failure_text[failure] in row["detail"], (case.key, row["detail"])
        assert row["status"] == (413 if case.failure == "over_cap" else None), case.key

    stored_rows = _strava_rows(isolated_data_dir)
    for case in cases:
        if case.match is None or case in pair:
            continue
        stored = stored_rows[_case_id(case)]
        assert stored["match"] == case.match, case.key
        assert stored["linked_session_id"] == _one(report, _case_id(case))["session_id"], case.key
        assert stored["warnings"] == list(case.warnings), case.key

    strava_counts = report["counts"]["strava"]
    for outcome, n in expected_counts.items():
        assert strava_counts[outcome] == n, outcome
    assert sum(strava_counts[o] for o in SA.OUTCOMES) == len(cases)
    assert strava_counts["distance_differs"] == sum(1 for c in cases if c.warnings)
    assert report["counts"]["stored"] == expected_counts["stored"]
    assert report["counts"]["already_stored"] == expected_counts["already_stored"]
    assert report["counts"]["failed"] == expected_counts["failed"]
    assert report["counts"]["not_fit"] == {"gpx": 1, "tcx": 1}
    assert report["counts"]["skipped_sport"] == {} and report["counts"]["non_activity"] == {}


def test_the_202_body_already_names_source_strava(isolated_data_dir: Path, tmp_path: Path) -> None:
    """``POST /imports`` recognises the archive before queuing: the 202 body and ``imports.source``
    say ``strava`` (root and one-folder layouts) or ``garmin``, before the worker has run."""
    start = SA.session_start(SA.fixture("sample_run"))
    rows = [SA.row(**{"Activity Date": SA.strava_date(start), "Activity Type": "Ride"})]
    root = SA.strava_zip(tmp_path / "root.zip", rows)
    folder = SA.strava_zip(tmp_path / "folder.zip", rows, layout="folder")
    garmin = IA.build_archive(tmp_path)
    with TestClient(app) as client:
        for path, source in ((root, "strava"), (folder, "strava"), (garmin, "garmin")):
            started = _post(client, path)
            assert started.status_code == 202, started.text
            body = started.json()
            assert (body["status"], body["source"]) == ("queued", source), path.name
            _drain()
            conn = sqlite3.connect(isolated_data_dir / db.DB_FILENAME)
            try:
                stored = conn.execute(
                    "SELECT source FROM imports WHERE import_id = ?", (body["import_id"],)
                ).fetchone()[0]
            finally:
                conn.close()
            assert stored == source
            assert client.get(f"/imports/{body['import_id']}").json()["source"] == source


ROOT_FOLDER_KEYS = (
    "attached",
    "attached_tiebreak",
    "attached_by_file_start",
    "stored",
    "not_fit_gpx",
    "no_file",
    "not_running",
    "failed_file_not_in_archive",
)


def test_root_and_one_folder_archives_give_the_same_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """The same rows and files at the root, under one folder, and under one folder beside a
    ``__MACOSX/`` tree: three stores, the same outcomes, links and counts."""
    seen = {}
    for layout in SA.LAYOUTS:
        store = tmp_path / f"store_{layout}"
        _use_store(monkeypatch, store)
        with TestClient(app) as client:
            report, ids = _import_cases(client, post_fit_bytes, tmp_path, ROOT_FOLDER_KEYS, layout=layout)
        assert report["status"] == "completed", report["error"]
        assert report["source"] == "strava"
        by_name = {v: k for k, v in ids.items()}
        seen[layout] = (
            [
                (r["strava_activity_id"], r["entry"], r["outcome"], by_name.get(r["session_id"], r["session_id"]
                 if r["outcome"] != "stored" else "new"))
                for r in report["files"]
            ],
            report["counts"],
        )
        print(f"  {layout}:", [(r[2]) for r in seen[layout][0]])
    assert {o for _, _, o, _ in seen["root"][0]} >= {"attached", "attached_by_file_start", "stored", "not_fit",
                                                       "no_file", "not_running", "failed"}
    assert seen["folder"] == seen["root"]
    assert seen["macosx"] == seen["root"]


def test_a_header_missing_distance_2_fails_the_job_and_writes_no_row(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    case = SA.by_key("stored")
    path = SA.strava_zip(
        tmp_path / "no_distance_2.zip", [case.cells], [case.file], header=SA.header_without("Distance (2)")
    )
    with TestClient(app) as client:
        report = _import(client, path)
    assert report["status"] == "failed"
    assert report["source"] == "strava"
    assert "Distance (2)" in report["error"], report["error"]
    assert report["files"] == []
    assert _strava_rows(isolated_data_dir) == {}
    assert _sessions(isolated_data_dir) == []


def test_an_unreadable_activities_csv_entry_fails_the_job_naming_it(isolated_data_dir: Path, tmp_path: Path) -> None:
    """A bad CRC on the ``activities.csv`` entry itself is the job's failure, naming the entry and
    the error, with no row written; the zip's other entries are fine."""
    marker = "CRC-MARKER-ROW"
    rows = [SA.row(**{"Activity ID": "123", "Activity Type": "Ride", "Activity Name": marker})]
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("activities/", b"")
        zf.writestr(CSV, SA.activities_csv(rows))
    raw = bytearray(good.read_bytes())
    at = raw.find(marker.encode())
    assert at > 0
    raw[at] ^= 0x01  # the stored bytes no longer match the entry's CRC-32
    bad = tmp_path / "bad_crc.zip"
    bad.write_bytes(bytes(raw))
    with TestClient(app) as client:
        report = _import(client, bad)
    print("  error:", report["error"])
    assert report["status"] == "failed"
    assert report["error"].startswith("StravaCsvError: activities.csv could not be read: "), report["error"]
    assert "CRC" in report["error"]
    assert report["files"] == []
    assert _strava_rows(isolated_data_dir) == {}


# --- AC2/AC3 seams: start times and candidates as the store spells them -----------------------------


def test_a_file_start_is_matched_as_the_store_spells_it(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """The file's own start (a datetime from the reader) meets the store's ``+00:00`` text: the
    row 50 minutes off its file links by the file's start, and no session is added."""
    with TestClient(app) as client:
        report, ids = _import_cases(client, post_fit_bytes, tmp_path, ("attached_by_file_start",))
    row = report["files"][0]
    assert (row["outcome"], row["session_id"]) == ("attached_by_file_start", ids["by_file_start.fit"])
    assert len(_sessions(isolated_data_dir)) == 1
    stored = _strava_rows(isolated_data_dir)[row["strava_activity_id"]]
    assert (stored["match"], stored["linked_session_id"]) == ("file_start", ids["by_file_start.fit"])


def test_two_sessions_on_one_second_link_the_one_within_one_percent(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """The 2024-07-05 shape: the store lists ``(session_id, sport, distance_m)``; the job hands
    ``decide`` the distance, so the FR945 run within 1 % takes the link."""
    with TestClient(app) as client:
        report, ids = _import_cases(
            client, post_fit_bytes, tmp_path, ("attached_tiebreak", "ambiguous_neither_within")
        )
    rows = {r["strava_activity_id"]: r for r in report["files"]}
    tiebreak, neither = (rows[_case_id(SA.by_key(k))] for k in ("attached_tiebreak", "ambiguous_neither_within"))
    assert (tiebreak["outcome"], tiebreak["session_id"]) == ("attached", ids["attached_tiebreak_fr945.fit"])
    assert neither["outcome"] == "ambiguous"
    assert neither["detail"] == (
        f"candidates: {ids['ambiguous_neither_within_fr945.fit']}, {ids['ambiguous_neither_within_3476.fit']}"
    )


def test_a_copy_with_an_unreadable_start_is_already_stored(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """The catalogue's ``already_stored``: the file's session start is invalid and its CSV start 50
    minutes off, so no step links it; the store is attempted and the ``(source_device,
    start_time)`` key finds the uploaded session, which the row attaches to with ``match``
    ``stored``. No session is added."""
    with TestClient(app) as client:
        report, ids = _import_cases(client, post_fit_bytes, tmp_path, ("already_stored",))
    assert report["status"] == "completed", report["error"]
    row = report["files"][0]
    assert (row["outcome"], row["session_id"]) == ("already_stored", ids["already_stored.fit"])
    assert report["counts"]["already_stored"] == 1 and report["counts"]["strava"]["already_stored"] == 1
    assert len(_sessions(isolated_data_dir)) == 1
    stored = _strava_rows(isolated_data_dir)[row["strava_activity_id"]]
    assert (stored["outcome"], stored["match"], stored["linked_session_id"]) == (
        "already_stored",
        "stored",
        ids["already_stored.fit"],
    )


def test_an_already_stored_session_another_row_links_is_ambiguous(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """One row per session on the store's duplicate key: a Run row on the session's own second
    (no file) attaches first; the catalogue's ``already_stored`` row, 50 minutes later with an
    unreadable file start, is refused by the ``(source_device, start_time)`` key on that same
    session, which the first row holds, so it is ``ambiguous`` and links nothing."""
    case = SA.by_key("already_stored")
    session_start = SA.session_start(case.stored[0][1])
    holder = _run_row(session_start, "holder", **{"Activity ID": "41"})
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([case]))
        report = _import(client, SA.strava_zip(tmp_path / "held.zip", [case.cells, holder], [case.file]))
    assert report["status"] == "completed", report["error"]
    session = ids["already_stored.fit"]
    print("  rows:", [(r["strava_activity_id"], r["outcome"], r["session_id"], r["detail"]) for r in report["files"]])
    first, second = report["files"]
    assert (first["strava_activity_id"], first["outcome"], first["session_id"]) == (41, "attached", session)
    assert (second["strava_activity_id"], second["outcome"], second["session_id"]) == (
        _case_id(case), "ambiguous", None)
    assert second["detail"] == f"candidates: {session}"
    rows = _strava_rows(isolated_data_dir)
    assert (rows[41]["linked_session_id"], rows[41]["match"], rows[41]["outcome"]) == (session, "csv_start", "attached")
    late = rows[_case_id(case)]
    assert (late["outcome"], late["linked_session_id"], late["match"]) == ("ambiguous", None, None)
    assert len(_sessions(isolated_data_dir)) == 1
    assert report["counts"]["already_stored"] == 0 and report["counts"]["strava"]["ambiguous"] == 1


# --- AC4: a Strava-only run is stored, oldest first; retyped --------------------------------------


def test_a_strava_only_run_equals_an_upload(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    case = SA.by_key("stored")
    with TestClient(app) as client:
        report, _ = _import_cases(client, post_fit_bytes, tmp_path, ("stored",))
    row = report["files"][0]
    assert row["outcome"] == "stored" and row["session_id"] is not None
    assert report["counts"]["stored"] == 1 and report["counts"]["strava"]["stored"] == 1
    imported = _sessions(isolated_data_dir)
    assert len(imported) == 1 and imported[0]["session_id"] == row["session_id"]
    assert imported[0]["import_id"] == report["import_id"]
    stored = _strava_rows(isolated_data_dir)[row["strava_activity_id"]]
    assert (stored["match"], stored["linked_session_id"]) == ("stored", row["session_id"])

    store_b = tmp_path / "store_b"
    _use_store(monkeypatch, store_b)
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, [("stored.fit", gzip.decompress(case.file[1]))])
    uploaded = _sessions(store_b)
    assert len(uploaded) == 1
    assert _comparable(imported[0]) == _comparable(uploaded[0])


def test_runs_are_stored_oldest_first_whatever_the_csv_order(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Three Strava-only runs listed newest first in the CSV are stored oldest first:
    ``upload_order`` follows their start times."""
    sample = SA.fixture("sample_run")
    base = SA.session_start(sample)
    rows, files = [], []
    for days in (-40, -50, -60):  # newest first
        fit = SA.shifted(sample, days * SA.DAY_S)
        aid = str(8_000_000_000 - days)
        rows.append(_run_row(base + timedelta(days=days), **{"Activity ID": aid}))
        files.append((f"activities/{aid}.fit.gz", SA.gz(fit)))
        rows[-1]["Filename"] = files[-1][0]
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "order.zip", rows, files))
    assert [r["outcome"] for r in report["files"]] == ["stored"] * 3
    sessions = _sessions(isolated_data_dir)
    starts = [s["start_time"] for s in sessions]
    print("  start_time by upload_order:", starts)
    assert len(starts) == 3
    assert starts == sorted(starts)
    assert [r["time_created"] for r in report["files"]] == sorted(r["time_created"] for r in report["files"])


def test_a_cycling_file_in_a_run_row_is_stored_retyped(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Sport ``running``, the flag, ``name`` the row's ``Activity Name``, the watch's sport and
    profile name in provenance; every other column equals a ``POST /sessions`` upload of it."""
    case = SA.by_key("stored_retyped")
    with TestClient(app) as client:
        report, _ = _import_cases(client, post_fit_bytes, tmp_path, ("stored_retyped",))
    row = report["files"][0]
    assert row["outcome"] == "stored_retyped"
    assert "sport_retyped_by_strava" in row["quality_flags"]
    assert report["counts"]["strava"]["stored_retyped"] == 1
    (imported,) = _sessions(isolated_data_dir)
    assert imported["session_id"] == row["session_id"]
    assert imported["sport"] == "running"
    assert imported["name"] == case.cells["Activity Name"]
    stored = _strava_rows(isolated_data_dir)[row["strava_activity_id"]]
    assert (stored["outcome"], stored["match"]) == ("stored_retyped", "stored")

    store_b = tmp_path / "store_b"
    _use_store(monkeypatch, store_b)
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, [("cycling.fit", gzip.decompress(case.file[1]))])
    (uploaded,) = _sessions(store_b)
    print("  upload sport/name/flags:", uploaded["sport"], uploaded["name"], uploaded["quality_flags"])
    assert uploaded["sport"] != "running"
    assert json.loads(imported["quality_flags"]) == [*json.loads(uploaded["quality_flags"]), "sport_retyped_by_strava"]
    provenance = json.loads(imported["context"])["provenance"]
    assert provenance == json.loads(uploaded["context"])["provenance"]
    assert provenance["raw_sport_value"] == "cycling"
    also = frozenset({"sport", "name", "quality_flags"})
    assert _comparable(imported, also) == _comparable(uploaded, also)


def test_the_same_file_in_a_ride_row_stores_nothing(isolated_data_dir: Path, tmp_path: Path, post_fit_bytes) -> None:
    with TestClient(app) as client:
        report, _ = _import_cases(client, post_fit_bytes, tmp_path, ("not_running_cycling_file",))
    row = report["files"][0]
    assert (row["outcome"], row["session_id"]) == ("not_running", None)
    assert _sessions(isolated_data_dir) == []
    stored = _strava_rows(isolated_data_dir)[row["strava_activity_id"]]
    assert (stored["outcome"], stored["linked_session_id"], stored["match"]) == ("not_running", None, None)


def test_a_bad_file_in_a_ride_row_is_never_read(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    reads: list[str] = []
    real = strava_files.read_activity_file

    def counting(zf, prefix, filename):
        reads.append(filename)
        return real(zf, prefix, filename)

    monkeypatch.setattr(strava_files, "read_activity_file", counting)
    with TestClient(app) as client:
        report, _ = _import_cases(client, post_fit_bytes, tmp_path, ("not_running_undecodable_file", "stored"))
    outcomes = {r["strava_activity_id"]: r["outcome"] for r in report["files"]}
    assert outcomes[_case_id(SA.by_key("not_running_undecodable_file"))] == "not_running"
    assert outcomes[_case_id(SA.by_key("stored"))] == "stored"
    # The reader is live (the Run row's file was read) and never saw the Ride row's file.
    assert reads == [SA.by_key("stored").file[0]]


# --- AC5 (first import): every row kept, every column --------------------------------------------


def test_every_row_is_kept_with_every_column(isolated_data_dir: Path, tmp_path: Path, post_fit_bytes) -> None:
    keys = ("attached", "not_running", "not_fit_gpx", "no_file", "lf_cell", "crlf_cell", "quote_cell",
            "failed_bad_activity_date")
    cases = _cases(*keys)
    attached = dict(cases[0].cells)
    attached.update({"Distance": "6.21", "Activity Description": "", "Relative Effort": "57"})
    cases[0] = cases[0]._replace(cells=attached)
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files(cases))
        path = SA.strava_zip(tmp_path / "kept.zip", [c.cells for c in cases], [c.file for c in cases if c.file])
        report = _import(client, path)
    assert report["status"] == "completed", report["error"]
    rows = _strava_rows(isolated_data_dir)
    assert set(rows) == {_case_id(c) for c in cases}
    for case in cases:
        stored = rows[_case_id(case)]
        assert list(stored["columns"]) == list(SA.KEYS), case.key
        assert stored["columns"] == dict(case.cells), case.key
    first = rows[_case_id(cases[0])]
    assert (first["columns"]["Distance"], first["columns"]["Distance (2)"]) == ("6.21", "10000.0")
    assert first["columns"]["Activity Description"] == ""
    assert first["linked_session_id"] == ids["attached.fit"]
    lf = rows[_case_id(SA.by_key("lf_cell"))]
    assert lf["description"] == "Bailed due to calf problems.\nWalked home."
    crlf = rows[_case_id(SA.by_key("crlf_cell"))]
    assert crlf["private_note"] == "Foot pain\r\nblew up"
    assert rows[_case_id(SA.by_key("quote_cell"))]["name"] == 'The "easy" ride, mostly'
    bad_date = rows[_case_id(SA.by_key("failed_bad_activity_date"))]
    assert (bad_date["outcome"], bad_date["start_time"], bad_date["linked_session_id"]) == ("failed", None, None)


# --- AC8: one bad row fails one row ----------------------------------------------------------------


def test_a_duplicate_activity_id_is_failed(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Two Ride rows with one ID, the newer listed first: the newer (by ``Activity Date``) is
    ``failed`` (``duplicate_activity_id``) and writes nothing; the stored row is the older's."""
    start = SA.session_start(SA.fixture("sample_run"))
    newer = SA.row(**{"Activity ID": "77", "Activity Date": SA.strava_date(start), "Activity Type": "Ride",
                      "Activity Name": "newer"})
    older = SA.row(**{"Activity ID": "77", "Activity Date": SA.strava_date(start - timedelta(days=3)),
                      "Activity Type": "Ride", "Activity Name": "older"})
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "dup.zip", [newer, older]))
    first, second = report["files"]
    assert first["outcome"] == "not_running" and first["entry"] == f"{CSV}#77"
    assert second["outcome"] == "failed" and second["detail"] == "duplicate_activity_id"
    assert second["strava_activity_id"] == 77
    assert first["time_created"] < second["time_created"]
    rows = _strava_rows(isolated_data_dir)
    assert list(rows) == [77]
    assert (rows[77]["name"], rows[77]["outcome"]) == ("older", "not_running")
    assert report["counts"]["failed"] == 1 and report["counts"]["strava"]["failed"] == 1


def test_a_failed_row_does_not_stop_later_rows(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Every named failure, oldest first in the archive, then a run that stores: each failure is one
    ``failed`` row (413 for the ``.gz`` past the cap, the only status), the run is stored."""
    keys = ("failed_file_not_in_archive", "failed_corrupt_gz", "failed_over_cap", "failed_fit_does_not_decode",
            "failed_no_session_message", "failed_bad_activity_date", "failed_short_row")
    cases = _cases(*keys)
    # A Strava-only run newer than every failed row, so it is processed after them all.
    sample = SA.fixture("sample_run")
    days = -5
    run = _run_row(SA.session_start(sample) + timedelta(days=days),
                   **{"Activity ID": "5", "Filename": "activities/5.fit.gz"})
    for case in cases:
        if isinstance(case.cells, dict) and case.failure != "bad_activity_date":
            assert case.cells["Activity Date"] != run["Activity Date"]
            assert strava.strava_csv.parse_activity_date(case.cells["Activity Date"]) < (
                strava.strava_csv.parse_activity_date(run["Activity Date"])
            ), case.key
    path = SA.strava_zip(
        tmp_path / "failures.zip",
        [*(c.cells for c in cases), run],
        [*(c.file for c in cases if c.file), ("activities/5.fit.gz", SA.gz(SA.shifted(sample, days * SA.DAY_S)))],
    )
    with TestClient(app) as client:
        monkeypatch.setattr(archive, "FIT_ENTRY_CAP", SA.GZ_BOMB_CAP)
        report = _import(client, path)
    assert report["status"] == "completed", report["error"]
    outcomes = [(r["outcome"], r["status"], r["entry"]) for r in report["files"]]
    print("  rows:", outcomes)
    assert Counter(o for o, _, _ in outcomes) == Counter({"failed": 7, "stored": 1})
    over_cap = _one(report, _case_id(SA.by_key("failed_over_cap")))
    assert over_cap["status"] == 413 and over_cap["detail"].startswith("file_too_large")
    assert [s for _, s, _ in outcomes if s is not None] == [413]
    assert sum(1 for _, _, e in outcomes if e.startswith(f"{CSV} line ")) == 2
    assert len(_sessions(isolated_data_dir)) == 1


def test_an_activity_id_past_64_bits_is_a_failed_row(isolated_data_dir: Path, tmp_path: Path) -> None:
    """``Activity ID`` 2**63 does not fit SQLite's INTEGER: the row fails as one that does not
    parse (``activities.csv line <n>``, no ``strava_activities`` row, null ``strava_activity_id``)
    and the next row is processed."""
    start = SA.session_start(SA.fixture("sample_run"))
    huge = str(2**63)
    rows = [
        SA.row(**{"Activity ID": huge, "Activity Date": SA.strava_date(start), "Activity Type": "Ride"}),
        SA.row(**{"Activity ID": str(2**63 - 1), "Activity Date": SA.strava_date(start + timedelta(days=1)),
                  "Activity Type": "Ride"}),
    ]
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "huge.zip", rows))
    assert report["status"] == "completed", report["error"]
    print("  rows:", [(r["entry"], r["outcome"], r["detail"]) for r in report["files"]])
    failed = next(r for r in report["files"] if r["outcome"] == "failed")
    assert failed["entry"] == f"{CSV} line 2"
    assert failed["strava_activity_id"] is None
    assert huge in failed["detail"]
    ok = next(r for r in report["files"] if r["outcome"] == "not_running")
    assert ok["strava_activity_id"] == 2**63 - 1
    assert list(_strava_rows(isolated_data_dir)) == [2**63 - 1]


def test_an_activity_id_past_64_bits_on_a_row_that_does_not_parse_fails_that_row_only(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """``Activity ID`` 2**63 on a row whose ``Activity Date`` does not parse: the row is ``failed``
    with a null ``strava_activity_id`` and no ``strava_activities`` row, and the next row that
    does not parse (file order) is still processed and keeps its row."""
    bad_date = "18 avr. 2022 14:26:14"
    rows = [
        SA.row(**{"Activity ID": str(2**63), "Activity Date": bad_date, "Activity Type": "Run"}),
        SA.row(**{"Activity ID": "88", "Activity Date": bad_date, "Activity Type": "Ride"}),
    ]
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "huge_unparsed.zip", rows))
    assert report["status"] == "completed", report["error"]
    print("  rows:", [(r["entry"], r["outcome"], r["strava_activity_id"], r["detail"]) for r in report["files"]])
    huge, after = report["files"]
    assert (huge["entry"], huge["outcome"], huge["strava_activity_id"]) == (f"{CSV} line 2", "failed", None)
    # It fails for its own cause, not for an overflow writing the ID.
    assert huge["detail"].startswith("Activity Date does not parse:") and "Overflow" not in huge["detail"]
    assert (after["entry"], after["outcome"], after["strava_activity_id"]) == (f"{CSV} line 3", "failed", 88)
    assert list(_strava_rows(isolated_data_dir)) == [88]
    assert report["counts"]["failed"] == 2


def test_an_error_on_a_row_that_does_not_parse_fails_that_row_only(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception while a row that did not parse is checked against the store fails that row
    only (``failed``, no ``strava_activities`` row written for it); the next row is processed."""
    bad_date = "18 avr. 2022 14:26:14"
    rows = [
        SA.row(**{"Activity ID": "66", "Activity Date": bad_date, "Activity Type": "Run"}),
        SA.row(**{"Activity ID": "67", "Activity Date": bad_date, "Activity Type": "Ride"}),
    ]
    real = db.read_strava_row

    def failing(conn, activity_id):
        if activity_id == 66:
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, activity_id)

    monkeypatch.setattr(db, "read_strava_row", failing)
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "erring.zip", rows))
    assert report["status"] == "completed", report["error"]
    print("  rows:", [(r["entry"], r["outcome"], r["strava_activity_id"], r["detail"]) for r in report["files"]])
    erred, after = report["files"]
    assert (erred["outcome"], erred["strava_activity_id"]) == ("failed", 66)
    assert "disk I/O error" in erred["detail"]
    assert (after["outcome"], after["strava_activity_id"]) == ("failed", 67)
    assert list(_strava_rows(isolated_data_dir)) == [67]


class _Stop(BaseException):
    """Stands in for the process stopping mid-row: not an ``Exception``, so no handler takes it."""


def test_an_interrupted_job_leaves_no_half_written_row(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writes of the second row (a Strava-only run, inside its session's transaction) raise:
    that row's session, ``strava_activities`` row and ``import_files`` row are all absent, the
    first row's are present, and nothing later was written."""
    sample = SA.fixture("sample_run")
    base = SA.session_start(sample)
    ride = SA.row(**{"Activity ID": "1", "Activity Date": SA.strava_date(base - timedelta(days=90)),
                     "Activity Type": "Ride"})
    rows, files = [ride], []
    for aid, days in (("2", -80), ("3", -70)):
        rows.append(_run_row(base + timedelta(days=days), **{"Activity ID": aid, "Filename": f"activities/{aid}.fit.gz"}))
        files.append((f"activities/{aid}.fit.gz", SA.gz(SA.shifted(sample, days * SA.DAY_S))))
    path = SA.strava_zip(tmp_path / "stop.zip", rows, files)

    with TestClient(app):
        pass  # the schema
    conn = db.get_connection(isolated_data_dir)
    try:
        import_id = job.create_job(conn, path.name, path.stat().st_size)
    finally:
        conn.close()
    target = job.archive_path(isolated_data_dir, import_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)

    real = strava._write_row
    calls = []

    def stopping(conn, import_id, record, file_row, counts, **kw):
        calls.append(file_row.seq)
        if len(calls) == 2:
            assert kw.get("stored") is True, "the second row should be written inside its session's transaction"
            raise _Stop
        return real(conn, import_id, record, file_row, counts, **kw)

    monkeypatch.setattr(strava, "_write_row", stopping)
    with pytest.raises(_Stop):
        job.run_job(import_id, isolated_data_dir, [], ZoneInfo("UTC"))

    assert calls == [1, 2]
    rows_stored = _strava_rows(isolated_data_dir)
    assert list(rows_stored) == [1]
    assert _sessions(isolated_data_dir) == []
    conn = sqlite3.connect(isolated_data_dir / db.DB_FILENAME)
    try:
        files_rows = conn.execute("SELECT seq, outcome FROM import_files WHERE import_id = ?", (import_id,)).fetchall()
        status = conn.execute("SELECT status, activities_processed FROM imports WHERE import_id = ?", (import_id,)).fetchone()
    finally:
        conn.close()
    assert files_rows == [(1, "not_running")]
    assert status == ("running", 1)


def _staged_job(isolated_data_dir: Path, path: Path) -> str:
    """A job created for ``path`` with its archive in place, for ``job.run_job`` to run here."""
    with TestClient(app):
        pass  # the schema
    conn = db.get_connection(isolated_data_dir)
    try:
        import_id = job.create_job(conn, path.name, path.stat().st_size)
    finally:
        conn.close()
    target = job.archive_path(isolated_data_dir, import_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return import_id


@pytest.mark.parametrize("where", ["_insert_file_row", "_write_stored_row"])
def test_a_row_stopped_after_its_strava_row_is_written_leaves_none_of_it(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """One transaction per row, inside the row's writes: the second row's report-row write (a
    ``Ride`` row's ``import_files`` row, or a stored run's inside its session's transaction) stops
    the process after its ``strava_activities`` row was written. That row, and for the run its
    session, are absent; the first row's are present."""
    sample = SA.fixture("sample_run")
    base = SA.session_start(sample)
    first = SA.row(**{"Activity ID": "1", "Activity Date": SA.strava_date(base - timedelta(days=90)),
                      "Activity Type": "Ride"})
    if where == "_insert_file_row":
        second = SA.row(**{"Activity ID": "2", "Activity Date": SA.strava_date(base - timedelta(days=80)),
                           "Activity Type": "Ride"})
        files = []
    else:
        second = _run_row(base - timedelta(days=80), **{"Activity ID": "2", "Filename": "activities/2.fit.gz"})
        files = [("activities/2.fit.gz", SA.gz(SA.shifted(sample, -80 * SA.DAY_S)))]
    import_id = _staged_job(isolated_data_dir, SA.strava_zip(tmp_path / "stop.zip", [first, second], files))
    real = getattr(job, where)
    written: list[int] = []

    def stopping(*args, **kwargs):
        file_row = next(a for a in args if isinstance(a, job.FileRow))
        if file_row.seq == 2:
            conn = args[0]
            # The strava row is already written inside this transaction.
            written.extend(r[0] for r in conn.execute("SELECT activity_id FROM strava_activities WHERE activity_id = 2"))
            raise _Stop
        return real(*args, **kwargs)

    monkeypatch.setattr(job, where, stopping)
    with pytest.raises(_Stop):
        job.run_job(import_id, isolated_data_dir, [], ZoneInfo("UTC"))
    print(f"  {where}: strava row 2 seen inside the row's transaction: {written}")
    assert written == [2]
    assert list(_strava_rows(isolated_data_dir)) == [1]
    assert _sessions(isolated_data_dir) == []


def test_a_second_row_on_a_session_this_import_stored_is_ambiguous(isolated_data_dir: Path, tmp_path: Path) -> None:
    """One row per session for a link the import's own store made: two ``Run`` rows on one second
    carrying the same Strava-only FIT (one run uploaded to Strava twice). The first stores it; the
    second, refused by the store's key on that session, is ``ambiguous`` and links nothing."""
    sample = SA.fixture("sample_run")
    start = SA.session_start(sample)
    rows, files = [], []
    for aid in ("801", "802"):
        rows.append(_run_row(start, f"dup {aid}", **{"Activity ID": aid, "Filename": f"activities/{aid}.fit.gz"}))
        files.append((f"activities/{aid}.fit.gz", SA.gz(sample)))
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "dup.zip", rows, files))
    got = [(r["strava_activity_id"], r["outcome"], r["session_id"], r["detail"]) for r in report["files"]]
    print("  rows:", got)
    assert report["status"] == "completed", report["error"]
    (session,) = [s["session_id"] for s in _sessions(isolated_data_dir)]
    assert got == [(801, "stored", session, None), (802, "ambiguous", None, f"candidates: {session}")]
    stored = _strava_rows(isolated_data_dir)
    assert (stored[801]["linked_session_id"], stored[802]["linked_session_id"]) == (session, None)


def test_an_oversized_cell_and_a_long_activity_id_fail_one_row_not_the_job(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """A 200,000-character ``Activity Description`` (past ``csv``'s default 131,072 field limit)
    is read and kept; an ``Activity ID`` of 5,000 digits is the past-64-bit ``failed`` row, not a
    job failure; an ID with 30 leading zeros, or with 5,000 (past the 4,300 digits CPython converts),
    parses by its value. Every other row is processed."""
    start = SA.session_start(SA.fixture("sample_run"))
    note = "x" * 200_000
    rows = [
        SA.row(**{"Activity ID": "101", "Activity Date": SA.strava_date(start), "Activity Type": "Ride"}),
        SA.row(**{"Activity ID": "102", "Activity Date": SA.strava_date(start + timedelta(days=1)),
                  "Activity Type": "Ride", "Activity Description": note}),
        SA.row(**{"Activity ID": "9" * 5000, "Activity Date": SA.strava_date(start + timedelta(days=2)),
                  "Activity Type": "Ride"}),
        SA.row(**{"Activity ID": "0" * 30 + "104", "Activity Date": SA.strava_date(start + timedelta(days=3)),
                  "Activity Type": "Ride"}),
        # Past the 4,300 digits CPython converts, all but three of them zeros (review 2026-11-06).
        SA.row(**{"Activity ID": "0" * 5000 + "105", "Activity Date": SA.strava_date(start + timedelta(days=4)),
                  "Activity Type": "Ride"}),
    ]
    with TestClient(app) as client:
        report = _import(client, SA.strava_zip(tmp_path / "big.zip", rows))
    got = [(r["entry"], r["outcome"], r["strava_activity_id"], (r["detail"] or "")[:60]) for r in report["files"]]
    print("  status:", report["status"], report["error"], "rows:", got)
    assert report["status"] == "completed", report["error"]
    assert [(o, i) for _, o, i, _ in got] == [
        ("not_running", 101), ("not_running", 102), ("failed", None), ("not_running", 104), ("not_running", 105)]
    failed = report["files"][2]
    assert failed["entry"] == f"{CSV} line 4"
    assert failed["detail"].startswith("Activity ID is past the 64-bit integer range")
    stored = _strava_rows(isolated_data_dir)
    assert sorted(stored) == [101, 102, 104, 105]
    assert stored[102]["description"] == note


# --- AC1: a Garmin archive is unchanged ------------------------------------------------------------


def _garmin_view(report: dict) -> tuple:
    files = [{k: v for k, v in r.items() if k != "session_id"} | {"stored": r["session_id"] is not None}
             for r in report["files"]]
    return report["status"], report["counts"], files, report["progress"]


def test_a_garmin_archive_still_imports_as_garmin(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F020's section 9 archive imports with ``source`` ``garmin`` and the same ``files[]`` and
    ``counts`` as the Garmin path with Strava recognition switched off (a second store); a job
    stored before ``source`` existed is served ``garmin``."""
    archive_path = IA.build_archive(tmp_path)
    with TestClient(app) as client:
        report = _import(client, archive_path)
    assert report["source"] == "garmin"
    assert report["status"] == "completed", report["error"]
    assert all(r["strava_activity_id"] is None for r in report["files"])
    assert report["counts"]["strava"] == {k: 0 for k in report["counts"]["strava"]}

    store_b = tmp_path / "store_b"
    _use_store(monkeypatch, store_b)
    monkeypatch.setattr(job, "strava_prefix", lambda path: None)
    with TestClient(app) as client:
        baseline = _import(client, archive_path)
    print("  garmin counts:", report["counts"])
    assert _garmin_view(report) == _garmin_view(baseline)

    conn = sqlite3.connect(store_b / db.DB_FILENAME)
    try:
        with conn:
            conn.execute("UPDATE imports SET source = NULL WHERE import_id = ?", (baseline["import_id"],))
    finally:
        conn.close()
    with TestClient(app) as client:
        assert client.get(f"/imports/{baseline['import_id']}").json()["source"] == "garmin"
