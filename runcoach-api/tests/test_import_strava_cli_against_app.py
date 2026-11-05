"""``runcoach import`` of a Strava archive against the served app, not a hand-written body (F030
AC10, the against-the-app half).

The command runs through Typer's ``CliRunner`` on the harness in ``tests/support/against_app.py``:
``runcoach_cli.api_client.client`` is a ``TestClient(app)``, so ``POST /imports``, ``GET
/imports/{id}`` and ``GET /strava-activities/{id}`` are answered by the real routes on the isolated
store (the autouse ``isolated_data_dir``). The CLI's poll sleep waits for the import worker to go
idle, so each poll after the first reads a finished job.

The archive is built here with ``tests/support/strava_archive.py``'s ``strava_zip`` (not its
catalogue): a ride with no file (``not_running``), a run whose file is not in the archive
(``failed``), a run on the second two stored sessions share with no distance to break the tie
(``ambiguous``; the sessions are ``sample_run``, README provenance real run, run proof yes, and its
other-device copy) and a short row (did not parse). A second archive without the ride makes it
``deleted_in_strava``. The expected lines are built from the CSV cells written here and the served
body's fields, with the dates converted in the test through ``ZoneInfo``, so the fake API's shape
in the CLI tests cannot drift from the served one unseen.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api.imports import job
from runcoach_cli.commands import imports, sessions

HERE = Path(__file__).parent
DRAIN_TIMEOUT_S = 120.0
ZONE = ZoneInfo("America/Vancouver")


def _load_by_path(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


against_app = _load_by_path("against_app", HERE / "support" / "against_app.py")
SA = _load_by_path("strava_archive", HERE / "support" / "strava_archive.py")

cli_against_app = against_app.cli_against_app
_run = against_app._run

RIDE_ID, LOST_ID, TWIN_ID = 9_100_000_001, 9_100_000_002, 9_100_000_003
RIDE_DATE = datetime(2022, 3, 1, 3, 15, 0, tzinfo=ZoneInfo("UTC"))  # the evening before, in Vancouver
LOST_DATE = datetime(2022, 3, 2, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
SHORT_ROW = ["77", "Apr 1, 2022, 1:00:00 PM", "Short"]


@pytest.fixture(autouse=True)
def _drain_and_zone(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(imports, "sleep", lambda _s: job.wait_idle(DRAIN_TIMEOUT_S))
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _rows(twin_start: datetime, *, with_ride: bool) -> list:
    rows: list = []
    if with_ride:
        rows.append(
            SA.row(
                **{
                    "Activity ID": str(RIDE_ID),
                    "Activity Date": SA.strava_date(RIDE_DATE),
                    "Activity Name": "Lunch Ride",
                    "Activity Type": "Ride",
                }
            )
        )
    rows += [
        SA.row(
            **{
                "Activity ID": str(LOST_ID),
                "Activity Date": SA.strava_date(LOST_DATE),
                "Activity Name": "Lost Run",
                "Activity Type": "Run",
                "Filename": f"activities/{LOST_ID}.fit.gz",
            }
        ),
        SA.row(
            **{
                "Activity ID": str(TWIN_ID),
                "Activity Date": SA.strava_date(twin_start),
                "Activity Name": "Twin Run",
                "Activity Type": "Run",
            }
        ),
        SHORT_ROW,
    ]
    return rows


def _local(when: datetime) -> str:
    return when.astimezone(ZONE).date().isoformat()


def _import(client: TestClient, path: Path) -> tuple[list[str], dict]:
    result = _run("import", str(path))
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    import_id = lines[0].split()[1]
    served = client.get(f"/imports/{import_id}")
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["status"] == "completed" and body["source"] == "strava", body
    return lines[lines.index("Status: completed") + 1 :], body


def _counts_line(strava: dict) -> str:
    keys = (
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
    )
    return "Strava: " + ", ".join(f"{key} {strava[key]}" for key in keys)


def _by_outcome(body: dict, outcome: str) -> list[dict]:
    return [row for row in body["files"] if row["outcome"] == outcome]


def test_the_strava_report_against_the_real_app(cli_against_app: TestClient, tmp_path: Path) -> None:
    client = cli_against_app
    sample = SA.fixture("sample_run")
    twin_start = SA.session_start(sample)
    for name, data in (("sample_run.fit", sample), ("other_device.fit", SA.other_device_copy(sample))):
        stored = client.post("/sessions", files={"file": (name, data)})
        assert stored.status_code == 201, stored.text

    first = SA.strava_zip(tmp_path / "first.zip", _rows(twin_start, with_ride=True))
    report, body = _import(client, first)

    strava = body["counts"]["strava"]
    assert (strava["not_running"], strava["failed"], strava["ambiguous"]) == (1, 2, 1), strava
    [lost] = [row for row in _by_outcome(body, "failed") if row["strava_activity_id"] == LOST_ID]
    [short] = [row for row in _by_outcome(body, "failed") if row["entry"].startswith("activities.csv line")]
    [twin] = _by_outcome(body, "ambiguous")
    assert short["entry"] == "activities.csv line 5", short
    for cell in SHORT_ROW:
        assert f'"{cell}"' in short["detail"], short
    lost_status = "-" if lost["status"] is None else str(lost["status"])
    # One line per listed row, in the served seq order; the ride prints nothing.
    expected = {
        twin["seq"]: f"  ambiguous  {_local(twin_start)}  Twin Run  {twin['detail']}",
        lost["seq"]: f"  failed  {_local(LOST_DATE)}  Lost Run  {lost_status}  {lost['detail']}",
        short["seq"]: f"  failed  activities.csv line 5  {short['detail']}",
    }
    assert report == [_counts_line(strava)] + [expected[seq] for seq in sorted(expected)], report
    assert twin["detail"].startswith("candidates: "), twin

    second = SA.strava_zip(tmp_path / "second.zip", _rows(twin_start, with_ride=False))
    report, body = _import(client, second)

    strava = body["counts"]["strava"]
    assert strava["deleted_in_strava"] == 1, strava
    [deleted] = _by_outcome(body, "deleted_in_strava")
    assert deleted["strava_activity_id"] == RIDE_ID, deleted
    assert report[0] == _counts_line(strava)
    # 03:15 UTC on 1 March is the evening of 28 February in Vancouver.
    assert _local(RIDE_DATE) == "2022-02-28"
    assert "  deleted_in_strava  2022-02-28  Lunch Ride" in report, report
