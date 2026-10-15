"""F022 AC5 and AC6 end to end: ``runcoach sync`` against the served app, not a mock.

``runcoach sync`` runs through Typer's ``CliRunner`` with ``runcoach_cli.api_client.client`` set to
a ``TestClient(app)`` (an ``httpx.Client``), so its ``/openapi.json`` switch check, its range walk
over ``GET /sessions`` and every ``POST /sessions`` upload are answered by the real routes on the
isolated store (the autouse ``isolated_data_dir``), and so is the race tags step that follows a
run that did not stop: its ``setRaceTags`` check on the same ``/openapi.json`` read and its
``PUT /race-tags``. ``runcoach_cli.config.CONFIG_PATH`` points at a
``cli.toml`` under the test's tmp dir, so ``garmin.token_dir()`` never resolves to the athlete's
home. A fake ``GarminClient`` stands in for Garmin Connect, serving zips of corpus files; nothing
reaches the network and no token is read.

The files, by their rows in ``fixtures/README.md``'s provenance table: ``sample_run``,
``hilly_run_8k_fr945`` and ``wrist_ppg_run`` are real runs (run proof yes); ``strap_hrv_capture`` is
an HRV capture on the HRV Snapshot profile; ``sample_health_snapshot`` is a health snapshot. The ski
day is ``sample_run`` with its session sport patched to alpine skiing (13) and its timestamps moved,
so it is a different file from the run rather than a duplicate of it. The four monitoring-type
entries are one-message files built by ``support/import_archive.minimal_fit``.

Resting HR is the observable input of the order tests: F021 borrows max and threshold HR from
running files only, but resting HR from any stored file, so a snapshot stored before a run that
carries no resting value of its own supplies it.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

pytest.importorskip("runcoach_cli")

from runcoach_cli import api_client, garmin
from runcoach_cli import config as cli_config
from runcoach_cli.commands import sync
from runcoach_cli.main import app as cli_app
from typer.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
BASE_URL = "http://testserver"

USER_PROFILE = 3  # FIT user_profile: resting_heart_rate is field 8
RESTING_HEART_RATE = 8
ZONES_TARGET = 7  # FIT zones_target: the file's max and threshold HR
SESSION = 18  # FIT session: sport is field 5
SPORT_FIELD = 5
ALPINE_SKIING = 13
SNAPSHOT_RESTING = 52
# The FIT file types of a snapshot zip's non-activity entries: monitoring_b (32) and three the FIT
# profile fitdecode ships does not name.
NON_ACTIVITY_TYPES = (32, 44, 68, 49)

TODAY = date(2026, 10, 12)
DAY_1 = date(2026, 10, 10)
DAY_2 = date(2026, 10, 11)
DAY_3 = date(2026, 10, 12)

runner = CliRunner()


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
import_archive = _load_support("import_archive")


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _activity(
    activity_id: int, local: str, type_key: str, type_id: int, parent: int, manual: bool = False
) -> dict[str, Any]:
    """A listed summary. ``startTimeGMT`` repeats the local start, as no stored file starts then: the
    race tags step reads every listed run as not stored, and none is a race."""
    return {
        "activityId": activity_id,
        "activityName": f"activity {activity_id}",
        "startTimeLocal": local,
        "startTimeGMT": local,
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": parent},
        "eventType": {"typeKey": "uncategorized", "typeId": 9},
        "manualActivity": manual,
    }


class FakeGarmin:
    """Serves listed activities, their ORIGINAL zips and each day's snapshot zip; 404 otherwise."""

    def __init__(
        self, activities: list[dict[str, Any]], originals: dict[int, bytes], snapshots: dict[date, bytes]
    ) -> None:
        self.activities = activities
        self.originals = originals
        self.snapshots = snapshots
        self.downloads: list[tuple[str, Any]] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        self.downloads.append(("original", activity_id))
        if activity_id not in self.originals:
            raise garmin.NotFound(f"no original for {activity_id}")
        return self.originals[activity_id]

    def download_snapshot(self, day: date) -> bytes:
        self.downloads.append(("snapshot", day))
        if day not in self.snapshots:
            raise garmin.NotFound(f"no snapshot on {day}")
        return self.snapshots[day]


def _ski_day() -> bytes:
    """``sample_run`` with its session sport set to alpine skiing, a day later, so it does not 409."""
    skied = fit_patch.set_field(_fixture("sample_run"), SESSION, SPORT_FIELD, ALPINE_SKIING)
    return fit_patch.shift_timestamps(skied, 86_400)


def _three_days() -> FakeGarmin:
    """The AC5 range: three runs, a strap check, a ski day, a manual activity and one snapshot zip."""
    activities = [
        # Listed newest first, as Connect lists them.
        _activity(301, "2026-10-12 07:00:00", "trail_running", 6, 1),  # wrist_ppg_run
        _activity(302, "2026-10-12 09:00:00", "running", 1, 17, manual=True),  # manual: no file
        _activity(201, "2026-10-11 10:00:00", "other", 4, 17),  # ski day
        _activity(202, "2026-10-11 18:00:00", "treadmill_running", 18, 1),  # hilly_run_8k_fr945
        _activity(101, "2026-10-10 06:30:00", "other", 4, 17),  # strap check
        _activity(102, "2026-10-10 07:00:00", "running", 1, 17),  # sample_run
    ]
    originals = {
        101: _zip([("101_ACTIVITY.fit", _fixture("strap_hrv_capture"))]),
        102: _zip([("102_ACTIVITY.fit", _fixture("sample_run"))]),
        201: _zip([("201_ACTIVITY.fit", _ski_day())]),
        202: _zip([("202_ACTIVITY.fit", _fixture("hilly_run_8k_fr945"))]),
        301: _zip([("301_ACTIVITY.fit", _fixture("wrist_ppg_run"))]),
    }
    snapshot = [("1_ACTIVITY.fit", _fixture("sample_health_snapshot"))] + [
        (f"{n + 2}_WELLNESS.fit", import_archive.minimal_fit(file_type))
        for n, file_type in enumerate(NON_ACTIVITY_TYPES)
    ]
    return FakeGarmin(activities, originals, {DAY_1: _zip(snapshot)})


@pytest.fixture
def cli_against_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path):
    """A TestClient on the app, installed as the CLI's HTTP client, and a tmp ``cli.toml`` naming it."""
    data_dir = Path(db._load_config_cached().data_dir).resolve()
    assert data_dir.is_relative_to(tmp_path.resolve()), data_dir
    config_path = tmp_path / "cli-home" / "cli.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'api_url = "{BASE_URL}"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "CONFIG_PATH", config_path)
    assert garmin.token_dir().resolve().is_relative_to(tmp_path.resolve())
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    with TestClient(app, base_url=BASE_URL) as client:
        monkeypatch.setattr(api_client, "client", client)
        yield client


def _serve(monkeypatch: pytest.MonkeyPatch, fake: FakeGarmin) -> None:
    monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))


def _run(*args: str):
    result = runner.invoke(cli_app, list(args))
    print(f"$ runcoach {' '.join(args)} -> exit {result.exit_code}\n{result.output}")
    return result


def _stored(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/sessions", params={"limit": 200})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] is None
    return body["items"]


def _shape(rows: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return sorted((row["sport"], row.get("activity_tag")) for row in rows)


FIRST_SYNC_SHAPE = sorted(
    [
        ("running", None),
        ("running", None),
        ("running", None),
        ("other", "resting_hrv_check"),
        ("other", "health_snapshot"),
    ]
)


def _first_sync(client: TestClient, monkeypatch: pytest.MonkeyPatch, declared_config) -> None:
    declared_config("HRV Snapshot")
    _serve(monkeypatch, _three_days())
    result = _run("sync")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "Stored: 5" in lines
    assert "Already stored: 0" in lines
    assert "Skipped alpine_skiing: 1 (activity 201 (activity 201))" in lines
    assert "Not an activity: 4" in lines
    assert "No FIT file: 1 (activity 302 (activity 302))" in lines
    assert not any(line.startswith("Failed:") for line in lines)
    assert _tags_line(not_stored=5) in lines


def _tags_line(not_stored: int) -> str:
    """The race tags step's counts line: every listed non-manual activity sent, none matched."""
    return (
        f"Race tags: tagged 0, cleared 0, unchanged 0, not stored {not_stored}, mismatch 0, "
        "ambiguous 0, other tag 0, skipped 0"
    )


def test_first_sync_stores_the_runs_the_strap_check_and_the_snapshot(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, declared_config
) -> None:
    """The served ``/openapi.json`` passes the switch check; the three runs, the strap check (as
    ``resting_hrv_check``) and the snapshot (as ``health_snapshot``) are stored; the ski day is
    skipped by sport, the four monitoring-type entries are not activities, the manual activity has
    no file, and the store holds exactly those five sessions."""
    client = cli_against_app
    _first_sync(client, monkeypatch, declared_config)
    stored = _stored(client)
    print(_shape(stored))
    assert _shape(stored) == FIRST_SYNC_SHAPE


def test_resync_of_the_ac5_range_stores_nothing(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, declared_config
) -> None:
    """Syncing the same range again from its first day stores nothing new: the five are already
    stored, and the ski day, the four entries and the manual activity are reported as before."""
    client = cli_against_app
    _first_sync(client, monkeypatch, declared_config)
    before = sorted(row["session_id"] for row in _stored(client))
    _serve(monkeypatch, _three_days())
    result = _run("sync", "--since", DAY_1.isoformat())
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert f"From {DAY_1.isoformat()} to {TODAY.isoformat()}" in lines
    assert "Stored: 0" in lines
    assert "Already stored: 5" in lines
    assert "Skipped alpine_skiing: 1 (activity 201 (activity 201))" in lines
    assert "Not an activity: 4" in lines
    assert "No FIT file: 1 (activity 302 (activity 302))" in lines
    assert not any(line.startswith("Failed:") for line in lines)
    assert _tags_line(not_stored=5) in lines
    assert sorted(row["session_id"] for row in _stored(client)) == before


# --- The order pair: one run with no settings of its own and the same morning's snapshot ---------


def _bare_run() -> bytes:
    """``sample_run`` without its ``user_profile`` and ``zones_target`` messages: no resting, max or
    threshold HR of its own."""
    return fit_patch.remove_messages(
        fit_patch.remove_messages(_fixture("sample_run"), USER_PROFILE), ZONES_TARGET
    )


def _resting_52_snapshot() -> bytes:
    """``sample_health_snapshot`` with its resting HR patched from 47 to 52."""
    return fit_patch.set_field(
        _fixture("sample_health_snapshot"), USER_PROFILE, RESTING_HEART_RATE, SNAPSHOT_RESTING
    )


def _start(data: bytes) -> int:
    """The first record timestamp (raw FIT seconds) of ``data``."""
    timestamps = [value for _, value in fit_patch._record_timestamps(data)[1]]
    return min(timestamps)


def _resting_input(client: TestClient, session_id: str) -> dict[str, Any]:
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
    finally:
        conn.close()
    assert body is not None
    return body["inputs"]["resting_hr_bpm"]


def _resting_anchor(client: TestClient) -> tuple[str | None, int | None, int | None]:
    """``(session_id, value, version)`` of the resting HR anchor as ``GET /me`` serves it: the file
    that supplies the athlete's resting HR, and the anchor version a saved load names."""
    me = client.get("/me")
    assert me.status_code == 200, me.text
    body = me.json()
    anchor = body["anchors"]["resting_hr_bpm"]
    return body["resting_hr_bpm"]["session_id"], anchor["value"], anchor["version"]


def _assert_borrowed_from_snapshot(client: TestClient, resting: dict[str, Any], snapshot_id: str) -> None:
    """The run's resting input is the snapshot's 52.

    With the snapshot the only stored file carrying resting HR, it is also the file F016's anchor
    resolution names, and F021's source rule (``hr_history``: "when the winner is the very file
    F016's resolution names, the block is the anchor block") labels that winner ``anchor`` with the
    anchor's version, not ``session_file_dated``. The snapshot is named through the anchor:
    ``GET /me`` serves the resting anchor from the snapshot's session at the version the load saved.
    """
    supplier, value, version = _resting_anchor(client)
    assert (supplier, value) == (snapshot_id, SNAPSHOT_RESTING)
    assert (resting["value"], resting["source"], resting["anchor_version"]) == (
        SNAPSHOT_RESTING,
        "anchor",
        version,
    )
    assert resting["out_of_period"] is False


def _run_and_snapshot(client: TestClient) -> tuple[str, str]:
    """The ids of the store's only two sessions: the run and the health snapshot."""
    stored = _stored(client)
    assert _shape(stored) == [("other", "health_snapshot"), ("running", None)], stored
    by_sport = {row["sport"]: row["session_id"] for row in stored}
    return by_sport["running"], by_sport["other"]


def test_a_run_without_settings_borrows_the_same_mornings_snapshot(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, declared_config
) -> None:
    """Sync uploads the day's snapshot before its activities, so the run, recorded after the
    snapshot that morning and carrying no resting HR of its own, saves with the snapshot's 52 as
    its resting input; a recompute changes nothing."""
    client = cli_against_app
    declared_config("HRV Snapshot")
    run, snapshot = _bare_run(), _resting_52_snapshot()
    assert _start(snapshot) < _start(run)
    assert _start(run) - _start(snapshot) < 86_400
    fake = FakeGarmin(
        [_activity(102, "2026-10-12 07:00:00", "running", 1, 17)],
        {102: _zip([("102_ACTIVITY.fit", run)])},
        {DAY_3: _zip([("1_ACTIVITY.fit", snapshot)])},
    )
    _serve(monkeypatch, fake)
    result = _run("sync")
    assert result.exit_code == 0, result.output
    assert "Stored: 2" in result.output.splitlines()
    assert _tags_line(not_stored=1) in result.output.splitlines()
    run_id, snapshot_id = _run_and_snapshot(client)
    resting = _resting_input(client, run_id)
    print(f"resting input: {resting}")
    _assert_borrowed_from_snapshot(client, resting, snapshot_id)
    walk = _run("loads", "recompute")
    assert walk.exit_code == 0, walk.output
    assert "Recomputed: 1" in walk.output.splitlines()
    assert "Changed: 0" in walk.output.splitlines()


def test_uploaded_before_the_snapshot_the_run_recomputes_changed(
    cli_against_app: TestClient, declared_config
) -> None:
    """The inverse order, uploaded directly: the run first saves with no resting value from a file
    before it; once the snapshot is stored, its recompute changes (the snapshot's 52 is borrowed)."""
    client = cli_against_app
    declared_config("HRV Snapshot")
    response = client.post(
        "/sessions",
        files={"file": ("run.fit", _bare_run(), "application/octet-stream")},
        data={"runs_and_captures_only": "true"},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["session_id"]
    before = _resting_input(client, run_id)
    print(f"resting input before the snapshot: {before}")
    assert (before["value"], before["source"], before["anchor_unavailable"]) == (None, None, "missing")
    response = client.post(
        "/sessions",
        files={"file": ("snapshot.fit", _resting_52_snapshot(), "application/octet-stream")},
        data={"runs_and_captures_only": "true"},
    )
    assert response.status_code == 201, response.text
    snapshot_id = response.json()["session_id"]
    walk = _run("loads", "recompute")
    assert walk.exit_code == 0, walk.output
    assert "Recomputed: 1" in walk.output.splitlines()
    assert "Changed: 1" in walk.output.splitlines()
    after = _resting_input(client, run_id)
    print(f"resting input after the recompute: {after}")
    _assert_borrowed_from_snapshot(client, after, snapshot_id)
