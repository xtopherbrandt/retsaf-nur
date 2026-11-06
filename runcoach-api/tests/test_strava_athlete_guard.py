"""F030, the athlete ruling of 2026-11-06 (4): a Strava export of another athlete is refused.

The archive's ``profile.csv`` (beside ``activities.csv``, at the root or in the one folder) carries
``Athlete ID``. The first Strava import that reads an id records it in the store
(``strava_athlete``); a later export whose id differs fails the job before any row is written,
naming both ids; the same id proceeds; a missing ``profile.csv``, one without the column, or an
empty id blocks nothing and records nothing.

Every test posts archives built in ``tmp_path`` by ``tests/support/strava_archive.py`` to the real
app under ``with TestClient(app)`` (the store is the autouse ``isolated_data_dir``). The runs are
``sample_run`` (README provenance: real run, run proof yes). The expectations are written from the
ruling's text, before the guard; the same author wrote both, so they are not an independent oracle.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import job
from runcoach_api.main import app


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "support" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")

DRAIN_TIMEOUT_S = 120.0
RUN_ID, RIDE_ID, NEW_ID = 9_200_000_001, 9_200_000_002, 9_200_000_003
OURS, THEIRS = "1347737", "2468024"
# The tables an import writes whatever it finds (the job's own report).
REPORT_TABLES = frozenset({"imports", "import_files"})


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _post(client: TestClient, path: Path) -> dict:
    with path.open("rb") as fh:
        started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
    assert started.status_code == 202, started.text
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    return done.json()


def _import(client: TestClient, path: Path) -> dict:
    report = _post(client, path)
    assert report["status"] == "completed", report["error"]
    return report


def _recorded(data_dir: Path) -> str | None:
    conn = db.get_connection(data_dir)
    try:
        return db.read_strava_athlete_id(conn)
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
            out[name] = [sql, *sorted(repr(r) for r in conn.execute(f'SELECT * FROM "{name}"'))]
        return out
    finally:
        conn.close()


def _rows(start, *, description: str = "Easy loop", with_ride: bool = True, with_new: bool = False) -> list:
    rows = [SA.row(**{"Activity ID": str(RUN_ID), "Activity Date": SA.strava_date(start),
                      "Activity Type": "Run", "Activity Name": "Morning Run",
                      "Activity Description": description})]
    if with_ride:
        rows.append(SA.row(**{"Activity ID": str(RIDE_ID), "Activity Date": SA.strava_date(start + timedelta(days=1)),
                              "Activity Type": "Ride", "Activity Name": "Lunch Ride"}))
    if with_new:
        rows.append(SA.row(**{"Activity ID": str(NEW_ID), "Activity Date": SA.strava_date(start + timedelta(days=2)),
                              "Activity Type": "Hike", "Activity Name": "Hike"}))
    return rows


def _stored_run(client: TestClient, post_fit_bytes) -> tuple[str, object]:
    sample = SA.fixture("sample_run")
    response = post_fit_bytes(client, "run.fit", sample)
    assert response.status_code == 201, response.text
    return response.json()["session_id"], SA.session_start(sample)


def test_the_first_import_records_the_id_and_the_same_id_proceeds(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """The first export with an id records it; a newer export with the same id is imported (its
    changed row ``updated``)."""
    with TestClient(app) as client:
        sid, start = _stored_run(client, post_fit_bytes)
        assert _recorded(isolated_data_dir) is None
        first = _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), profile=OURS))
        recorded = _recorded(isolated_data_dir)
        second = _import(client, SA.strava_zip(tmp_path / "b.zip", _rows(start, description="Edited"), profile=OURS))
    print("  recorded:", recorded, " second:", [(r["strava_activity_id"], r["outcome"]) for r in second["files"]])
    assert recorded == OURS
    assert [(r["strava_activity_id"], r["outcome"], r["session_id"]) for r in first["files"]] == [
        (RUN_ID, "attached", sid), (RIDE_ID, "not_running", None)]
    assert [(r["strava_activity_id"], r["outcome"]) for r in second["files"]] == [
        (RUN_ID, "updated"), (RIDE_ID, "unchanged")]
    assert _recorded(isolated_data_dir) == OURS


def test_another_athletes_export_fails_the_job_and_writes_no_row(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """An export whose ``Athlete ID`` differs from the recorded one, holding a changed row, a new
    row and missing a stored one (each of which an accepted export would write): the job fails
    naming both ids, its report holds no row, and every table but the report's is byte-equal to
    before, the Strava rows, their links, the recorded id and the run's load included."""
    with TestClient(app) as client:
        sid, start = _stored_run(client, post_fit_bytes)
        _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), profile=OURS))
        before = _dump(isolated_data_dir)
        theirs = SA.strava_zip(tmp_path / "theirs.zip",
                               _rows(start, description="Not mine", with_ride=False, with_new=True), profile=THEIRS)
        refused = _post(client, theirs)
        after = _dump(isolated_data_dir)
        block = client.get(f"/sessions/{sid}").json()["strava"]
    print("  refused:", refused["status"], refused["error"], "files", refused["files"], "counts", refused["counts"])
    assert refused["status"] == "failed"
    assert refused["files"] == []
    assert OURS in refused["error"] and THEIRS in refused["error"], refused["error"]
    print("  tables compared:", sorted(before))
    assert {"strava_activities", "strava_athlete", "sessions", "session_loads"} <= set(before)
    assert after == before
    assert (block["activity_id"], block["description"]) == (RUN_ID, "Easy loop")
    assert _recorded(isolated_data_dir) == OURS


@pytest.mark.parametrize(
    "profile",
    [None, "", b"Email Address,First Name\nrunner@example.com,Test\n", b"Athlete ID\n"],
    ids=["no_profile_csv", "empty_id", "no_athlete_id_column", "header_only"],
)
def test_an_export_with_no_id_proceeds_and_records_nothing(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes, profile
) -> None:
    """With no id recorded, an export with none (no ``profile.csv``, an empty id, no ``Athlete
    ID`` column, no row) is imported and records nothing; with an id recorded, it is imported too
    and the recorded id is kept."""
    with TestClient(app) as client:
        _sid, start = _stored_run(client, post_fit_bytes)
        first = _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), profile=profile))
        unrecorded = _recorded(isolated_data_dir)
        _import(client, SA.strava_zip(tmp_path / "b.zip", _rows(start), profile=OURS))
        third = _import(client, SA.strava_zip(tmp_path / "c.zip", _rows(start, description="Edited"), profile=profile))
    print("  first:", [(r["strava_activity_id"], r["outcome"]) for r in first["files"]], "recorded", unrecorded,
          " third:", [(r["strava_activity_id"], r["outcome"]) for r in third["files"]])
    assert [r["outcome"] for r in first["files"]] == ["attached", "not_running"]
    assert unrecorded is None
    assert [r["outcome"] for r in third["files"]] == ["updated", "unchanged"]
    assert _recorded(isolated_data_dir) == OURS


@pytest.mark.parametrize("layout", ["root", "folder"])
@pytest.mark.parametrize("bom", [False, True], ids=["no_bom", "bom"])
def test_the_id_is_read_with_a_bom_and_in_the_one_folder(
    isolated_data_dir: Path, tmp_path: Path, layout: str, bom: bool
) -> None:
    """``profile.csv`` is found where ``activities.csv`` is (the root or the one folder) and read
    as UTF-8 with an optional BOM; a different id in the same layout is then refused."""
    start = SA.session_start(SA.fixture("sample_run"))
    with TestClient(app) as client:
        _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), layout=layout,
                                      profile=SA.profile_csv(OURS, bom=bom)))
        recorded = _recorded(isolated_data_dir)
        refused = _post(client, SA.strava_zip(tmp_path / "b.zip", _rows(start), layout=layout,
                                              profile=SA.profile_csv(THEIRS, bom=bom)))
    print("  recorded:", recorded, " refused:", refused["status"], refused["error"])
    assert recorded == OURS
    assert refused["status"] == "failed" and THEIRS in refused["error"]


@pytest.mark.parametrize(("first_id", "later_id"), [(OURS, "0" + OURS), ("000" + OURS, OURS)],
                         ids=["padded_later", "padded_first"])
def test_an_id_of_ascii_digits_is_compared_by_its_value(
    isolated_data_dir: Path, tmp_path: Path, first_id: str, later_id: str
) -> None:
    """An ``Athlete ID`` of ASCII digits reads by its value, as an ``Activity ID`` does: leading
    zeros are dropped, the id is recorded in that form, and an export whose id differs only by
    them is imported."""
    start = SA.session_start(SA.fixture("sample_run"))
    with TestClient(app) as client:
        _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), profile=first_id))
        recorded = _recorded(isolated_data_dir)
        later = _post(client, SA.strava_zip(tmp_path / "b.zip", _rows(start, description="Edited"), profile=later_id))
    print("  recorded:", recorded, " later:", later["status"], later["error"],
          [(r["strava_activity_id"], r["outcome"]) for r in later["files"]])
    assert recorded == OURS
    assert later["status"] == "completed", later["error"]
    assert [r["strava_activity_id"] for r in later["files"]] == [RUN_ID, RIDE_ID]
    assert _recorded(isolated_data_dir) == OURS


def test_an_id_not_all_ascii_digits_is_compared_as_text(isolated_data_dir: Path, tmp_path: Path) -> None:
    """An id that is not all ASCII digits keeps its text: recorded as read, and refused against
    the same digits without its letter."""
    start = SA.session_start(SA.fixture("sample_run"))
    with TestClient(app) as client:
        _import(client, SA.strava_zip(tmp_path / "a.zip", _rows(start), profile="0" + OURS + "x"))
        recorded = _recorded(isolated_data_dir)
        refused = _post(client, SA.strava_zip(tmp_path / "b.zip", _rows(start), profile=OURS))
    print("  recorded:", recorded, " refused:", refused["status"], refused["error"])
    assert recorded == "0" + OURS + "x"
    assert refused["status"] == "failed" and OURS in refused["error"]
