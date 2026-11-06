"""F030 AC7 and reference section 3 step 0: importing a Strava archive whose rows are already stored.

Every test posts archives built in ``tmp_path`` by ``tests/support/strava_archive.py`` to the real
app under ``with TestClient(app)`` (the store is the autouse ``isolated_data_dir``), drains the
worker with ``job.wait_idle`` and reads the reports, the ``strava_activities`` rows and the store.

Step 0 as the reference states it (athlete ruling 2026-10-31): before any row is matched, every
stored, not-yet-marked row absent from the archive is marked ``deleted_in_strava``, unlinked and
reported once after the CSV's rows in ``Activity ID`` order; then a stored row with a live link
keeps it (``unchanged`` or ``updated``), a link to a deleted session is cleared and the row matched
from step 1, an unlinked row whose cells changed is matched from step 1, and a marked row present
again is cleared and matched from step 1. The athlete's rulings of 2026-11-06 refine the rest: an
unlinked ``failed`` row is matched from step 1 on every import, its file read again, and any other
unlinked unchanged row re-runs step 1 without reading its file, linking when step 1 now finds one
free session and otherwise reading ``unchanged`` with nothing decoded or stored.

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
from runcoach_api.imports import archive, job, strava, strava_files
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


def _without_strava_rows(dump: dict[str, list[str]]) -> dict[str, list[str]]:
    return {name: rows for name, rows in dump.items() if name != "strava_activities"}


def test_reimport_reads_every_row_unchanged_but_a_failed_one_and_tables_are_byte_equal(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """The catalogue (every outcome, rows that did not parse included) imported twice: every row
    of the second report is ``unchanged`` but the ``failed`` ones, which are processed again and
    fail again (athlete ruling 2026-11-06); the stored run keeps ``match`` ``stored``; every table
    but ``imports`` and ``import_files`` is byte-equal to before, but the failed rows'
    ``import_id`` and ``updated_at``, now the second import's."""
    cases = [c for c in SA.outcome_rows() if c.key not in LEFT_OUT]
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", SA.GZ_BOMB_CAP)
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, SA.stored_files(cases))
        first = _import(client, _zip(tmp_path / "a.zip", cases))
        before = _dump(isolated_data_dir)
        before_rows = _strava_rows(isolated_data_dir)
        second = _import(client, _zip(tmp_path / "b.zip", cases))
    after = _dump(isolated_data_dir)
    after_rows = _strava_rows(isolated_data_dir)
    first_outcomes = sorted({r["outcome"] for r in first["files"]})
    print("  first import outcomes:", first_outcomes)
    print("  second import:", sorted({(r["outcome"]) for r in second["files"]}), len(second["files"]), "rows")
    # The first import exercised every outcome the step-0 branches read.
    assert {"attached", "stored", "stored_retyped", "ambiguous", "failed", "not_running"} <= set(first_outcomes)
    assert len(second["files"]) == len(first["files"]) == len(cases)
    failed = [r["outcome"] == "failed" for r in first["files"]]
    assert sum(failed) >= 5
    assert [r["outcome"] for r in second["files"]] == ["failed" if f else "unchanged" for f in failed]
    assert [r["detail"] for r, f in zip(second["files"], failed, strict=True) if f] == [
        r["detail"] for r, f in zip(first["files"], failed, strict=True) if f]
    # A linked row reports its link; an unlinked one none.
    assert [r["session_id"] for r in second["files"]] == [r["session_id"] for r in first["files"]]
    assert second["counts"]["strava"]["unchanged"] == len(cases) - sum(failed)
    assert second["counts"]["stored"] == 0 and second["counts"]["failed"] == sum(failed)
    stored = after_rows[_case_id(SA.by_key("stored"))]
    assert (stored["outcome"], stored["match"]) == ("stored", "stored")
    assert stored["import_id"] == first["import_id"]
    print("  tables compared:", sorted(before), {k: len(v) - 1 for k, v in before.items() if len(v) > 1})
    assert "strava_activities" in before and "sessions" in before
    assert _without_strava_rows(before) == _without_strava_rows(after)
    retried = {r["strava_activity_id"] for r, f in zip(first["files"], failed, strict=True) if f} - {None}
    assert retried and set(after_rows) == set(before_rows)
    moved = ("import_id", "updated_at")
    for aid, row in after_rows.items():
        if aid in retried:
            assert row["import_id"] == second["import_id"] and row["outcome"] == "failed", aid
            assert {k: v for k, v in row.items() if k not in moved} == {
                k: v for k, v in before_rows[aid].items() if k not in moved}, aid
        else:
            assert row == before_rows[aid], aid


def test_reimport_reads_only_the_failed_rows_files_and_stores_nothing(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Rows whose first import read their file (stored, retyped, multisport, by file start) are not
    read, decoded or stored again; the two ``failed`` ones (no session message, a corrupt ``.gz``)
    are processed again (athlete ruling 2026-11-06): their files read, one decoded, nothing stored."""
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
    print("  second import's reads and decodes:", calls)
    assert sorted(calls) == ["read_activity_file", "read_activity_file", "session_starts"]
    expected = {_case_id(c): "failed" if c.key.startswith("failed_") else "unchanged" for c in cases}
    assert {r["strava_activity_id"]: r["outcome"] for r in second["files"]} == expected
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


def test_an_error_on_a_linked_row_in_step_0_fails_it_and_keeps_its_link(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """A live link is kept whatever steps 1-3 would find (reference section 3, step 0), and one
    bad row fails one row (AC8): an exception while step 0 handles a linked row whose cells
    changed makes that row ``failed`` with its link, ``match`` and warnings kept; the next row is
    processed."""
    attached, differs = _cases("attached", "attached_distance_differs")
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached, differs]))
        _import(client, _zip(tmp_path / "a.zip", [attached, differs]))
        old = _strava_rows(isolated_data_dir)
        a, d = _case_id(attached), _case_id(differs)
        real = strava._same_cells

        def failing(stored, columns):
            if stored["activity_id"] in (a, d):
                raise RuntimeError("step 0 broke")
            return real(stored, columns)

        monkeypatch.setattr(strava, "_same_cells", failing)
        newer = [_with(c, **{"Activity Description": "Bailed due to calf problems."}) for c in (attached, differs)]
        second = _import(client, _zip(tmp_path / "b.zip", newer))
    rows = _by_id(second)
    print("  second report:", _summary(second))
    assert (rows[a]["outcome"], rows[a]["session_id"]) == ("failed", ids["attached.fit"])
    assert (rows[d]["outcome"], rows[d]["session_id"]) == ("failed", ids["differs.fit"])
    assert rows[a]["detail"] == "RuntimeError: step 0 broke"
    new = _strava_rows(isolated_data_dir)
    for aid, name in ((a, "attached.fit"), (d, "differs.fit")):
        assert new[aid]["outcome"] == "failed"
        assert (new[aid]["linked_session_id"], new[aid]["match"], new[aid]["warnings"]) == (
            ids[name], "csv_start", old[aid]["warnings"])
    assert new[d]["warnings"] == ["distance_differs"]
    assert second["counts"]["failed"] == 2


BAD_DATE = "18 avr. 2022 14:26:14"


def test_a_linked_row_that_no_longer_parses_is_failed_and_keeps_its_link(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A linked row whose ``Activity Date`` no longer parses, its cells changed: rewritten
    ``failed`` with the new cells, its live link, ``match`` and warnings kept, reported on its
    ``activities.csv line <n>`` entry with that session."""
    differs = SA.by_key("attached_distance_differs")
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([differs]))
        _import(client, _zip(tmp_path / "a.zip", [differs]))
        second = _import(client, _zip(tmp_path / "b.zip", [_with(differs, **{"Activity Date": BAD_DATE})]))
    print("  second:", _summary(second))
    aid, session = _case_id(differs), ids["differs.fit"]
    assert _summary(second) == [(1, aid, "failed", session, f"{CSV} line 2")]
    stored = _strava_rows(isolated_data_dir)[aid]
    assert stored["outcome"] == "failed"
    assert stored["columns"]["Activity Date"] == BAD_DATE
    assert (stored["linked_session_id"], stored["match"], stored["warnings"]) == (
        session, "csv_start", ["distance_differs"])
    assert stored["import_id"] == second["import_id"]


def test_an_unlinked_row_that_no_longer_parses_is_failed_with_no_link(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """An unlinked ``no_file`` row whose ``Activity Date`` no longer parses: rewritten ``failed``
    with the new cells and no link."""
    no_file = SA.by_key("no_file")
    with TestClient(app) as client:
        _import(client, _zip(tmp_path / "a.zip", [no_file]))
        second = _import(client, _zip(tmp_path / "b.zip", [_with(no_file, **{"Activity Date": BAD_DATE})]))
    print("  second:", _summary(second))
    aid = _case_id(no_file)
    assert _summary(second) == [(1, aid, "failed", None, f"{CSV} line 2")]
    stored = _strava_rows(isolated_data_dir)[aid]
    assert (stored["outcome"], stored["linked_session_id"], stored["match"], stored["warnings"]) == (
        "failed", None, None, [])
    assert stored["columns"]["Activity Date"] == BAD_DATE
    assert stored["import_id"] == second["import_id"]


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


# --- a linked row that no longer parses keeps what it parsed (review 2026-11-06) -------------------

LINKED_ID = 9_100_000_555


def _linked_run_row(start) -> dict[str, str]:
    return SA.row(**{
        "Activity ID": str(LINKED_ID),
        "Activity Date": SA.strava_date(start),
        "Activity Type": "Run",
        "Activity Name": "Morning Run",
        "Activity Description": "Easy loop",
        "Activity Private Note": "calf fine",
    })


def _short(row: dict[str, str]) -> list[str]:
    """The row cut before ``Activity Type``: ``Activity ID``, ``Activity Date``, ``Activity Name``."""
    cells = list(row.values())
    assert SA.KEYS[:3] == ("Activity ID", "Activity Date", "Activity Name"), SA.KEYS[:4]
    return cells[:3]


def _long(row: dict[str, str]) -> list[str]:
    """The row with an unquoted comma in ``Activity Name``: every later cell one key to the right."""
    cells = list(row.values())
    at = SA.KEYS.index("Activity Name")
    return [*cells[:at], "Morning", " Run", *cells[at + 1:]]


def _trailing(row: dict[str, str]) -> list[str]:
    """The same cells plus one empty trailing cell (a stray trailing comma): its first cells equal
    the stored columns, so only the cell count tells it from the row that parsed (review
    2026-11-06, reference section 2 item 4)."""
    return [*row.values(), ""]


@pytest.mark.parametrize("cut", [_short, _long, _trailing], ids=["short_row", "long_row", "trailing_cell"])
def test_a_linked_run_whose_row_no_longer_parses_keeps_its_type_and_its_load(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, cut
) -> None:
    """A run linked to a ``Run`` row, then a newer export whose row for it does not parse (cut
    short before ``Activity Type``, one cell too long by a split name, or the same cells plus an
    empty trailing one): the row is ``failed`` with its live link, never ``unchanged``, only its
    raw ``columns`` are replaced, and the fields it parsed before (type, name, description, note,
    filename, start) are kept, so the run's load stays counted and the session's ``strava`` block
    keeps its text."""
    sample = SA.fixture("sample_run")
    start = SA.session_start(sample)
    good = _linked_run_row(start)
    with TestClient(app) as client:
        (sid,) = _upload(client, post_fit_bytes, [("run.fit", sample)]).values()
        first = _import(client, SA.strava_zip(tmp_path / "a.zip", [good]))
        assert (first["files"][0]["outcome"], first["files"][0]["session_id"]) == ("attached", sid)
        before_load = client.get(f"/sessions/{sid}/load").json()
        before_row = _strava_rows(isolated_data_dir)[LINKED_ID]
        bad = cut(good)
        second = _import(client, SA.strava_zip(tmp_path / "b.zip", [bad]))
        after_load = client.get(f"/sessions/{sid}/load").json()
        block = client.get(f"/sessions/{sid}").json()["strava"]
    print("  second:", _summary(second), "load_changes", second["load_changes"])
    print("  load before", before_load["session_load"], "after", after_load["session_load"])
    assert before_load["session_load"]["value"] is not None
    assert _summary(second) == [(1, LINKED_ID, "failed", sid, f"{CSV} line 2")]
    assert after_load == before_load
    assert second["load_changes"] == []
    assert second["counts"]["strava"]["excluded_from_load"] == 0
    stored = _strava_rows(isolated_data_dir)[LINKED_ID]
    assert stored["outcome"] == "failed"
    assert stored["columns"] == dict(zip(SA.KEYS, bad, strict=False))
    for key in ("activity_type", "name", "description", "private_note", "filename", "start_time",
                "linked_session_id", "match", "warnings"):
        assert stored[key] == before_row[key], key
    assert (block["activity_type"], block["name"], block["description"], block["private_note"]) == (
        "Run", "Morning Run", "Easy loop", "calf fine")


def test_a_linked_row_whose_activity_id_no_longer_parses_reads_absent(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A linked row, then a newer export whose row for it has ``Activity ID`` ``<id>x``: the row
    cannot be found by its ID, so it is ``failed`` with no ``strava_activity_id``, and its stored
    row reads absent: marked ``deleted_in_strava`` and unlinked, reported once (review 2026-11-06,
    reference section 3 step 0)."""
    sample = SA.fixture("sample_run")
    good = _linked_run_row(SA.session_start(sample))
    with TestClient(app) as client:
        (sid,) = _upload(client, post_fit_bytes, [("run.fit", sample)]).values()
        _import(client, SA.strava_zip(tmp_path / "a.zip", [good]))
        bad = dict(good, **{"Activity ID": f"{LINKED_ID}x"})
        second = _import(client, SA.strava_zip(tmp_path / "b.zip", [bad]))
    print("  second:", _summary(second))
    assert [(r["strava_activity_id"], r["outcome"], r["session_id"], r["entry"]) for r in second["files"]] == [
        (None, "failed", None, f"{CSV} line 2"), (LINKED_ID, "deleted_in_strava", None, f"{CSV}#{LINKED_ID}")]
    stored = _strava_rows(isolated_data_dir)
    assert list(stored) == [LINKED_ID]
    assert (stored[LINKED_ID]["deleted_in_strava"], stored[LINKED_ID]["linked_session_id"]) == (True, None)
    assert sid


# --- the 2026-11-06 rulings: failed rows are retried, unlinked rows re-run step 1 ------------------


def test_a_failed_row_whose_file_arrives_later_is_stored(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """Ruling (2): a Strava-only run whose file is missing from the first archive is ``failed``;
    the same ``activities.csv`` with the file present stores it, though the row's cells are
    unchanged."""
    sample = SA.fixture("sample_run")
    fit = SA.shifted(sample, -40 * SA.DAY_S)
    row = SA.row(**{"Activity ID": "201", "Activity Date": SA.strava_date(SA.session_start(fit)),
                    "Activity Type": "Run", "Activity Name": "strava only",
                    "Filename": "activities/201.fit.gz"})
    with TestClient(app) as client:
        first = _import(client, SA.strava_zip(tmp_path / "a.zip", [row]))
        second = _import(client, SA.strava_zip(tmp_path / "b.zip", [row], [("activities/201.fit.gz", SA.gz(fit))]))
    print("  first:", _summary(first), " second:", _summary(second))
    assert first["files"][0]["outcome"] == "failed"
    (session,) = _session_ids(isolated_data_dir)
    assert (second["files"][0]["outcome"], second["files"][0]["session_id"]) == ("stored", session)
    stored = _strava_rows(isolated_data_dir)[201]
    assert (stored["outcome"], stored["linked_session_id"], stored["match"]) == ("stored", session, "stored")


def test_a_row_that_still_fails_is_reported_failed_again(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling (2): the same archive twice, its row's file still missing and a short row still
    short: each is processed again (the file looked for again) and reported ``failed`` both times,
    so ``runcoach import`` keeps listing it; nothing is stored."""
    row = SA.row(**{"Activity ID": "202", "Activity Type": "Run", "Activity Name": "missing file",
                    "Filename": "activities/202.fit.gz"})
    short = ["203", SA.strava_date(SA.session_start(SA.fixture("sample_run"))), "short"]
    reads: list[str] = []
    real = strava_files.read_activity_file

    def counting(zf, prefix, filename):
        reads.append(filename)
        return real(zf, prefix, filename)

    monkeypatch.setattr(strava_files, "read_activity_file", counting)
    with TestClient(app) as client:
        first = _import(client, SA.strava_zip(tmp_path / "a.zip", [row, short]))
        reads.clear()
        second = _import(client, SA.strava_zip(tmp_path / "b.zip", [row, short]))
    print("  first:", _summary(first), " second:", _summary(second))
    assert [(r["strava_activity_id"], r["outcome"]) for r in first["files"]] == [(202, "failed"), (203, "failed")]
    assert [(r["strava_activity_id"], r["outcome"], r["session_id"]) for r in second["files"]] == [
        (202, "failed", None), (203, "failed", None)]
    assert second["files"][0]["detail"] == first["files"][0]["detail"]
    assert reads == ["activities/202.fit.gz"]
    assert second["counts"]["failed"] == 2 and second["counts"]["strava"]["unchanged"] == 0
    rows = _strava_rows(isolated_data_dir)
    assert rows[202]["import_id"] == rows[203]["import_id"] == second["import_id"]
    assert _session_ids(isolated_data_dir) == []


def test_an_unlinked_row_links_a_session_stored_after_it_and_excludes_its_load(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Ruling (1): an ``Alpine Ski`` row imported before its session is stored is ``not_running``;
    the session the watch stored as running is uploaded; the same archive again re-runs step 1
    (no file read), attaches the row, and F031 excludes the run's load."""
    sample = SA.fixture("sample_run")
    fit = SA.shifted(sample, -50 * SA.DAY_S)
    ski = SA.row(**{"Activity ID": "301", "Activity Date": SA.strava_date(SA.session_start(fit)),
                    "Activity Type": "Alpine Ski", "Activity Name": "Ski day",
                    "Filename": "activities/301.fit.gz", "Distance (2)": "10000.0"})
    path = SA.strava_zip(tmp_path / "ski.zip", [ski], [("activities/301.fit.gz", SA.gz(fit))])
    reads: list[str] = []
    real = strava_files.read_activity_file

    def counting(zf, prefix, filename):
        reads.append(filename)
        return real(zf, prefix, filename)

    monkeypatch.setattr(strava_files, "read_activity_file", counting)
    with TestClient(app) as client:
        first = _import(client, path)
        (sid,) = _upload(client, post_fit_bytes, [("ski.fit", fit)]).values()
        counted = client.get(f"/sessions/{sid}/load").json()["session_load"]
        second = _import(client, path)
        load = client.get(f"/sessions/{sid}/load").json()
    print("  first:", _summary(first), " second:", _summary(second), "load", load["session_load"])
    assert (first["files"][0]["outcome"], first["files"][0]["session_id"]) == ("not_running", None)
    assert (second["files"][0]["outcome"], second["files"][0]["session_id"]) == ("attached", sid)
    assert reads == []
    stored = _strava_rows(isolated_data_dir)[301]
    assert (stored["outcome"], stored["linked_session_id"], stored["match"]) == ("attached", sid, "csv_start")
    assert counted["value"] is not None
    assert load["session_load"]["unavailable"] == "strava_not_running" and load["strava_activity_id"] == 301
    assert second["counts"]["strava"]["excluded_from_load"] == 1


# --- an updated row's distance warning is checked again --------------------------------------------


def test_an_updated_row_rechecks_its_distance_warning_both_ways(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """A newer export moves a linked row's ``Distance (2)`` 50 % off its session: ``updated`` with
    ``distance_differs``; the export after it moves it back: ``updated`` with the warning gone."""
    attached = SA.by_key("attached")
    far = _with(attached, **{"Distance (2)": str(float(attached.cells["Distance (2)"]) * 1.5)})
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached]))
        first = _import(client, _zip(tmp_path / "a.zip", [attached]))
        second = _import(client, _zip(tmp_path / "b.zip", [far]))
        warned = _strava_rows(isolated_data_dir)[_case_id(attached)]
        third = _import(client, _zip(tmp_path / "c.zip", [attached]))
    print("  first:", _summary(first), " second:", _summary(second), " third:", _summary(third))
    aid, session = _case_id(attached), ids["attached.fit"]
    assert first["files"][0]["detail"] is None
    assert (second["files"][0]["outcome"], second["files"][0]["session_id"]) == ("updated", session)
    assert second["files"][0]["detail"] == "warnings: distance_differs"
    assert second["counts"]["strava"]["distance_differs"] == 1
    assert warned["warnings"] == ["distance_differs"]
    assert (third["files"][0]["outcome"], third["files"][0]["detail"]) == ("updated", None)
    assert third["counts"]["strava"]["distance_differs"] == 0
    assert _strava_rows(isolated_data_dir)[aid]["warnings"] == []


# --- a linked row stored failed is processed again (ruling of 2026-11-06, critic B2) --------------


def test_a_linked_row_failed_by_a_fault_is_processed_again_with_the_same_cells(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    """Ruling (2) covers a linked row stored ``failed``. A newer export moves a linked row's
    ``Distance (2)`` 50 % off its session, and a fault raised while its warning is read fails the
    row (link kept, old warnings kept, stored ``failed``). The same export again, the fault gone:
    the row is processed again, not read ``unchanged``: ``updated`` with its link, its stored
    outcome back to the one its ``match`` gives (``attached``) and ``distance_differs`` now
    checked. A third import of it reads ``unchanged``."""
    attached = SA.by_key("attached")
    far = _with(attached, **{"Distance (2)": str(float(attached.cells["Distance (2)"]) * 1.5)})
    aid = _case_id(attached)
    with TestClient(app) as client:
        ids = _upload(client, post_fit_bytes, SA.stored_files([attached]))
        session = ids["attached.fit"]
        first = _import(client, _zip(tmp_path / "a.zip", [attached]))
        real = strava._warnings

        def faulty(conn, session_id, row):
            raise RuntimeError("transient fault in the warning read")

        monkeypatch.setattr(strava, "_warnings", faulty)
        faulted = _import(client, _zip(tmp_path / "b.zip", [far]))
        after_fault = _strava_rows(isolated_data_dir)[aid]
        monkeypatch.setattr(strava, "_warnings", real)
        retried = _import(client, _zip(tmp_path / "b.zip", [far]))
        after_retry = _strava_rows(isolated_data_dir)[aid]
        third = _import(client, _zip(tmp_path / "b.zip", [far]))
    print("  first:", _summary(first), " faulted:", _summary(faulted), " retried:", _summary(retried),
          " third:", _summary(third))
    print("  stored after the fault:", {k: after_fault[k] for k in ("outcome", "linked_session_id", "warnings")},
          " after the retry:", {k: after_retry[k] for k in ("outcome", "linked_session_id", "warnings")})
    assert (first["files"][0]["outcome"], first["files"][0]["session_id"]) == ("attached", session)
    assert (faulted["files"][0]["outcome"], faulted["files"][0]["session_id"]) == ("failed", session)
    assert (after_fault["outcome"], after_fault["linked_session_id"], after_fault["warnings"]) == (
        "failed", session, [])
    assert (retried["files"][0]["outcome"], retried["files"][0]["session_id"]) == ("updated", session)
    assert retried["files"][0]["detail"] == "warnings: distance_differs"
    assert retried["counts"]["strava"]["distance_differs"] == 1 and retried["counts"]["failed"] == 0
    assert (after_retry["outcome"], after_retry["linked_session_id"], after_retry["match"]) == (
        "attached", session, "csv_start")
    assert after_retry["warnings"] == ["distance_differs"]
    assert after_retry["import_id"] == retried["import_id"]
    assert (third["files"][0]["outcome"], third["files"][0]["session_id"]) == ("unchanged", session)
    assert _strava_rows(isolated_data_dir)[aid] == after_retry
