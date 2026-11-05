"""F030 AC7 and reference section 3 step 0: importing a Strava archive whose rows are already stored.

Every test posts archives built in ``tmp_path`` by ``tests/support/strava_archive.py`` to the real
app under ``with TestClient(app)`` (the store is the autouse ``isolated_data_dir``), drains the
worker with ``job.wait_idle`` and reads the reports, the ``strava_activities`` rows and the store.

Step 0 as the reference states it (athlete ruling 2026-10-31): before any row is matched, every
stored, not-yet-marked row absent from the archive is marked ``deleted_in_strava``, unlinked and
reported once after the CSV's rows in ``Activity ID`` order; then a stored row with a live link
keeps it (``unchanged`` or ``updated``), a link to a deleted session is cleared and the row matched
from step 1, an unlinked row whose cells changed is matched from step 1, an unlinked unchanged
``ambiguous`` row re-runs step 1 without reading its file, and any other unlinked unchanged row
reads ``unchanged`` with nothing decoded or stored. A marked row present again is cleared and
matched from step 1.

The expectations are written from the reference's section 3 text, before the job's step 0; the
same author wrote both, so they are not an independent oracle. The catalogue's two rows sharing one
``Activity ID`` are left out of every archive here: which of them fails depends on their dates,
not their labels.

Sessions a test stores first are uploaded through ``POST /sessions`` with the copies the catalogue
builds from ``sample_run`` (README provenance: real run, run proof yes).
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import archive, job, strava_files
from runcoach_api.ingestion import pipeline
from runcoach_api.main import app


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "support" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")

DRAIN_TIMEOUT_S = 120.0
CSV = "activities.csv"
# The tables an import writes whatever it finds (the job's own report).
REPORT_TABLES = frozenset({"imports", "import_files"})
# Catalogue rows left out: one row has no Activity ID, and the duplicate-ID pair's labels do not
# say which of the two fails.
LEFT_OUT = frozenset({"failed_bad_activity_id", "failed_duplicate_activity_id"})


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _import(client: TestClient, path: Path) -> dict:
    with path.open("rb") as fh:
        started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
    assert started.status_code == 202, started.text
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    report = done.json()
    assert report["status"] == "completed", report["error"]
    return report


def _upload(client: TestClient, post_fit_bytes, files) -> dict[str, str]:
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


def _session_ids(data_dir: Path) -> list[str]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY upload_order")]
    finally:
        conn.close()


def _dump(data_dir: Path) -> dict[str, list[str]]:
    """Every table but the report's, as the ``repr`` of each row in a fixed order, and its DDL."""
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        out: dict[str, list[str]] = {}
        for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            if name in REPORT_TABLES:
                continue
            rows = sorted(repr(r) for r in conn.execute(f'SELECT * FROM "{name}"'))
            out[name] = [sql, *rows]
        return out
    finally:
        conn.close()


def _case_id(case) -> int:
    return int(case.cells["Activity ID"])


def _cases(*keys: str):
    return [SA.by_key(k) for k in keys]


def _zip(path: Path, cases) -> Path:
    return SA.case_zip(path, cases)


def _with(case, **cells: str):
    return case._replace(cells={**case.cells, **cells})


def _by_id(report: dict) -> dict[int | None, dict]:
    out: dict[int | None, dict] = {}
    for row in report["files"]:
        assert row["strava_activity_id"] not in out, ("one report row per Activity ID", row)
        out[row["strava_activity_id"]] = row
    return out


def _summary(report: dict) -> list[tuple]:
    return [(r["seq"], r["strava_activity_id"], r["outcome"], r["session_id"], r["entry"]) for r in report["files"]]


# --- the same archive again ------------------------------------------------------------------------


def test_reimport_reads_every_row_unchanged_and_tables_are_byte_equal(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """The catalogue (every outcome, rows that did not parse included) imported twice: every row
    of the second report is ``unchanged``, the stored run keeps ``match`` ``stored``, and every
    table but ``imports`` and ``import_files`` is byte-equal to before."""
    cases = [c for c in SA.outcome_rows() if c.key not in LEFT_OUT]
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", SA.GZ_BOMB_CAP)
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, SA.stored_files(cases))
        first = _import(client, _zip(tmp_path / "a.zip", cases))
        before = _dump(isolated_data_dir)
        second = _import(client, _zip(tmp_path / "b.zip", cases))
    after = _dump(isolated_data_dir)
    first_outcomes = sorted({r["outcome"] for r in first["files"]})
    print("  first import outcomes:", first_outcomes)
    print("  second import:", sorted({(r["outcome"]) for r in second["files"]}), len(second["files"]), "rows")
    # The first import exercised every outcome the step-0 branches read.
    assert {"attached", "stored", "stored_retyped", "ambiguous", "failed", "not_running"} <= set(first_outcomes)
    assert len(second["files"]) == len(first["files"]) == len(cases)
    assert [r["outcome"] for r in second["files"]] == ["unchanged"] * len(cases)
    # A linked row reports its link; an unlinked one none.
    assert [r["session_id"] for r in second["files"]] == [r["session_id"] for r in first["files"]]
    assert second["counts"]["strava"]["unchanged"] == len(cases)
    assert second["counts"]["stored"] == 0 and second["counts"]["failed"] == 0
    stored = _strava_rows(isolated_data_dir)[_case_id(SA.by_key("stored"))]
    assert (stored["outcome"], stored["match"]) == ("stored", "stored")
    assert stored["import_id"] == first["import_id"]
    print("  tables compared:", sorted(before), {k: len(v) - 1 for k, v in before.items() if len(v) > 1})
    assert "strava_activities" in before and "sessions" in before
    assert before == after


def test_reimport_decodes_and_stores_nothing(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Rows whose first import read their file (stored, retyped, multisport, no session message,
    by file start, a corrupt ``.gz``) are not read, decoded or stored again."""
    cases = _cases("stored", "stored_retyped", "multisport_not_stored", "failed_no_session_message",
                   "attached_by_file_start", "failed_corrupt_gz", "ambiguous_neither_within", "attached")
    calls: list[str] = []
    for module, name in ((strava_files, "read_activity_file"), (strava_files, "session_starts"),
                         (pipeline, "ingest_fit_bytes")):
        real = getattr(module, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, spy)
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, SA.stored_files(cases))
        _import(client, _zip(tmp_path / "a.zip", cases))
        first_calls = list(calls)
        sessions = _session_ids(isolated_data_dir)
        calls.clear()
        second = _import(client, _zip(tmp_path / "b.zip", cases))
    print("  first import's reads and decodes:", sorted(set(first_calls)), len(first_calls))
    # The spies are live: the first import read, decoded and stored through them.
    assert {"read_activity_file", "session_starts", "ingest_fit_bytes"} <= set(first_calls)
    assert calls == []
    assert [r["outcome"] for r in second["files"]] == ["unchanged"] * len(cases)
    assert _session_ids(isolated_data_dir) == sessions


# --- a newer archive -------------------------------------------------------------------------------


def test_a_newer_archive_updates_one_row_and_keeps_its_link(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A new description on a linked row: ``updated``, the text replaced, the link and ``match``
    kept, ``import_id`` and ``updated_at`` this import's; the other row's untouched."""
    attached, stored = _cases("attached", "stored")
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached]))
        first = _import(client, _zip(tmp_path / "a.zip", [attached, stored]))
        old = _strava_rows(isolated_data_dir)
        newer = _with(attached, **{"Activity Description": "Bailed due to calf problems."})
        second = _import(client, _zip(tmp_path / "b.zip", [newer, stored]))
    rows = _by_id(second)
    print("  second report:", _summary(second))
    a, s = _case_id(attached), _case_id(stored)
    assert (rows[a]["outcome"], rows[a]["session_id"]) == ("updated", ids["attached.fit"])
    assert rows[s]["outcome"] == "unchanged"
    assert second["counts"]["strava"]["updated"] == 1 and second["counts"]["strava"]["unchanged"] == 1
    new = _strava_rows(isolated_data_dir)
    assert new[a]["description"] == "Bailed due to calf problems."
    assert new[a]["columns"]["Activity Description"] == "Bailed due to calf problems."
    assert (new[a]["linked_session_id"], new[a]["match"]) == (ids["attached.fit"], "csv_start")
    assert new[a]["import_id"] == second["import_id"] and new[a]["updated_at"] != old[a]["updated_at"]
    assert new[s] == old[s] and new[s]["import_id"] == first["import_id"]


def test_a_row_absent_from_a_newer_archive_is_unlinked_and_reported_once(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """Two linked rows absent from the second archive: each marked ``deleted_in_strava``,
    unlinked and kept, reported after the CSV's row in ``Activity ID`` order with ``entry``
    ``activities.csv#<id>``."""
    attached, differs, no_file = _cases("attached", "attached_distance_differs", "no_file")
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, SA.stored_files([attached, differs]))
        # CSV order puts the higher ID first, so the report's ID order is not the CSV's.
        _import(client, _zip(tmp_path / "a.zip", [differs, attached, no_file]))
        second = _import(client, _zip(tmp_path / "b.zip", [no_file]))
    print("  second report:", _summary(second))
    a, d = sorted((_case_id(attached), _case_id(differs)))
    assert _summary(second) == [
        (1, _case_id(no_file), "unchanged", None, CSV + f"#{_case_id(no_file)}"),
        (2, a, "deleted_in_strava", None, f"{CSV}#{a}"),
        (3, d, "deleted_in_strava", None, f"{CSV}#{d}"),
    ]
    assert second["counts"]["strava"]["deleted_in_strava"] == 2
    assert second["progress"]["activities_total"] == 3
    rows = _strava_rows(isolated_data_dir)
    for aid in (a, d):
        assert rows[aid]["deleted_in_strava"] is True
        assert (rows[aid]["linked_session_id"], rows[aid]["match"]) == (None, None)
        assert rows[aid]["columns"]  # the row is kept, every cell with it
    assert rows[_case_id(no_file)]["deleted_in_strava"] is False


def test_a_row_still_absent_is_not_reported_again(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    attached, no_file = _cases("attached", "no_file")
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, SA.stored_files([attached]))
        _import(client, _zip(tmp_path / "a.zip", [attached, no_file]))
        second = _import(client, _zip(tmp_path / "b.zip", [no_file]))
        before = _dump(isolated_data_dir)
        third = _import(client, _zip(tmp_path / "c.zip", [no_file]))
    print("  second:", _summary(second), " third:", _summary(third))
    assert [r["outcome"] for r in second["files"]] == ["unchanged", "deleted_in_strava"]
    assert _summary(third) == [(1, _case_id(no_file), "unchanged", None, f"{CSV}#{_case_id(no_file)}")]
    assert third["counts"]["strava"]["deleted_in_strava"] == 0
    assert _dump(isolated_data_dir) == before


def test_a_row_that_returns_is_cleared_and_matched_again(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    attached, no_file = _cases("attached", "no_file")
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached]))
        _import(client, _zip(tmp_path / "a.zip", [attached, no_file]))
        _import(client, _zip(tmp_path / "b.zip", [no_file]))
        assert _strava_rows(isolated_data_dir)[_case_id(attached)]["deleted_in_strava"] is True
        third = _import(client, _zip(tmp_path / "c.zip", [attached, no_file]))
    print("  third:", _summary(third))
    row = _by_id(third)[_case_id(attached)]
    assert (row["outcome"], row["session_id"]) == ("attached", ids["attached.fit"])
    stored = _strava_rows(isolated_data_dir)[_case_id(attached)]
    assert stored["deleted_in_strava"] is False
    assert (stored["linked_session_id"], stored["match"], stored["outcome"]) == (
        ids["attached.fit"], "csv_start", "attached")
    assert stored["import_id"] == third["import_id"]


def test_a_reuploaded_activity_takes_the_link_of_the_deleted_one(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """Archive 2 drops ID 1 and adds ID 2 on the same second: ID 1 is ``deleted_in_strava`` and
    unlinked before matching, so ID 2 attaches to the session (one row per session would
    otherwise make it ``ambiguous``)."""
    attached = SA.by_key("attached")
    reuploaded = _with(attached, **{"Activity ID": "9100000001", "Filename": "activities/9100000001.fit.gz"})
    reuploaded = reuploaded._replace(file=("activities/9100000001.fit.gz", attached.file[1]))
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached]))
        _import(client, _zip(tmp_path / "a.zip", [attached]))
        second = _import(client, _zip(tmp_path / "b.zip", [reuploaded]))
    print("  second:", _summary(second))
    old_id, new_id = _case_id(attached), 9100000001
    assert _summary(second) == [
        (1, new_id, "attached", ids["attached.fit"], "activities/9100000001.fit.gz"),
        (2, old_id, "deleted_in_strava", None, f"{CSV}#{old_id}"),
    ]
    rows = _strava_rows(isolated_data_dir)
    assert (rows[old_id]["deleted_in_strava"], rows[old_id]["linked_session_id"]) == (True, None)
    assert (rows[new_id]["linked_session_id"], rows[new_id]["match"]) == (ids["attached.fit"], "csv_start")


def test_a_ride_retyped_run_links_on_the_next_import(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A Ride row 50 minutes off a stored run's start is ``not_running`` and its file is never
    read; retyped ``Run``, its cells changed, it is matched from step 1 and links by its file's
    start."""
    case = SA.by_key("attached_by_file_start")
    ride = _with(case, **{"Activity Type": "Ride"})
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([case]))
        first = _import(client, _zip(tmp_path / "a.zip", [ride]))
        second = _import(client, _zip(tmp_path / "b.zip", [case]))
    print("  first:", _summary(first), " second:", _summary(second))
    assert (first["files"][0]["outcome"], first["files"][0]["session_id"]) == ("not_running", None)
    row = second["files"][0]
    assert (row["outcome"], row["session_id"]) == ("attached_by_file_start", ids["by_file_start.fit"])
    stored = _strava_rows(isolated_data_dir)[_case_id(case)]
    assert (stored["activity_type"], stored["match"], stored["linked_session_id"]) == (
        "Run", "file_start", ids["by_file_start.fit"])
    assert len(_session_ids(isolated_data_dir)) == 1


def test_an_unchanged_ambiguous_row_links_once_the_duplicate_is_gone(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Two sessions on the row's second, neither within 1 %: ``ambiguous``; imported again with
    both still stored it reads ``unchanged``; once the device ``3476`` session is deleted, the
    unchanged row re-runs step 1 without reading its file and attaches to the one left."""
    case = SA.by_key("ambiguous_neither_within")
    reads: list[str] = []
    real = strava_files.read_activity_file

    def counting(zf, prefix, filename):
        reads.append(filename)
        return real(zf, prefix, filename)

    monkeypatch.setattr(strava_files, "read_activity_file", counting)
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([case]))
        first = _import(client, _zip(tmp_path / "a.zip", [case]))
        still = _import(client, _zip(tmp_path / "b.zip", [case]))
        gone = client.delete(f"/sessions/{ids['ambiguous_neither_within_3476.fit']}")
        assert gone.status_code == 204, gone.text
        third = _import(client, _zip(tmp_path / "c.zip", [case]))
    print("  first:", _summary(first), " still:", _summary(still), " third:", _summary(third))
    assert first["files"][0]["outcome"] == "ambiguous"
    assert (still["files"][0]["outcome"], still["files"][0]["session_id"]) == ("unchanged", None)
    row = third["files"][0]
    assert (row["outcome"], row["session_id"]) == ("attached", ids["ambiguous_neither_within_fr945.fit"])
    assert reads == []
    stored = _strava_rows(isolated_data_dir)[_case_id(case)]
    assert (stored["outcome"], stored["match"], stored["linked_session_id"]) == (
        "attached", "csv_start", ids["ambiguous_neither_within_fr945.fit"])
    assert stored["import_id"] == third["import_id"]


def test_a_deleted_session_clears_its_link_and_the_row_is_matched_again(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A row linked to a session the athlete then deleted: the stale link is cleared and the row
    matched from step 1; with no session at either start its file is stored again."""
    case = SA.by_key("attached")
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([case]))
        _import(client, _zip(tmp_path / "a.zip", [case]))
        gone = client.delete(f"/sessions/{ids['attached.fit']}")
        assert gone.status_code == 204, gone.text
        assert _session_ids(isolated_data_dir) == []
        second = _import(client, _zip(tmp_path / "b.zip", [case]))
    print("  second:", _summary(second))
    row = second["files"][0]
    assert row["outcome"] == "stored"
    (session,) = _session_ids(isolated_data_dir)
    # Session ids are derived from the file, so the stored-again session has the deleted one's id.
    assert row["session_id"] == session == ids["attached.fit"]
    stored = _strava_rows(isolated_data_dir)[_case_id(case)]
    assert (stored["linked_session_id"], stored["match"], stored["outcome"]) == (session, "stored", "stored")
    assert stored["import_id"] == second["import_id"]
