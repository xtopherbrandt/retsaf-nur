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
so it is a different file from the run rather than a duplicate of it. The three monitoring-type
entries are one-message files built by ``support/import_archive.minimal_fit``. The race tag tests
(F023) use ``sample_run`` and ``dev_fields_run``, two real runs (run proof yes) recorded on
different watches, with ``dev_fields_run`` moved to start on ``sample_run``'s second; the workout
tag test (F026) uses ``sample_run`` alone.

Resting HR is the observable input of the order tests: F021 borrows max and threshold HR from
running files only, but resting HR from any stored file, so a snapshot stored before a run that
carries no resting value of its own supplies it.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.config import AppConfig

pytest.importorskip("runcoach_cli")

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

USER_PROFILE = 3  # FIT user_profile: resting_heart_rate is field 8
RESTING_HEART_RATE = 8
ZONES_TARGET = 7  # FIT zones_target: the file's max and threshold HR
SESSION = 18  # FIT session: sport is field 5
SPORT_FIELD = 5
ALPINE_SKIING = 13
SNAPSHOT_RESTING = 52
# The FIT file types of a snapshot zip's non-activity entries: monitoring_b (32) and two the FIT
# profile fitdecode ships does not name. Type 68 (HRV_STATUS) is not among them: the API answers a
# type-68 file without a summary as a night skipped (``no_night``), not as a non-activity.
NON_ACTIVITY_TYPES = (32, 44, 49)

DAY_1 = date(2026, 10, 10)
DAY_2 = date(2026, 10, 11)
DAY_3 = date(2026, 10, 12)


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
import_archive = _load_support("import_archive")
against_app = _load_support("against_app")

# The harness, from tests/support/against_app.py; cli_against_app is a fixture pytest finds here.
HARNESS = ("cli_against_app", "_activity", "_zip", "_run", "_counts", "FakeGarmin", "_serve")
cli_against_app = against_app.cli_against_app
FakeGarmin = against_app.FakeGarmin
_activity = against_app._activity
_zip = against_app._zip
_run = against_app._run
_counts = against_app._counts
_serve = against_app._serve
TODAY = against_app.TODAY


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


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
    assert "Not an activity: 3" in lines
    assert "No FIT file: 1 (activity 302 (activity 302))" in lines
    assert not any(line.startswith("Failed:") for line in lines)
    assert _tags_line(not_stored=5) in lines


def _tags_line(not_stored: int) -> str:
    """The race tags step's counts line: every listed non-manual activity sent, none matched."""
    return (
        f"Race tags: tagged 0, retagged 0, cleared 0, unchanged 0, not stored {not_stored}, mismatch 0, "
        "ambiguous 0, other tag 0, skipped 0, workouts tagged 0"
    )


def test_first_sync_stores_the_runs_the_strap_check_and_the_snapshot(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, declared_config
) -> None:
    """The served ``/openapi.json`` passes the switch check; the three runs, the strap check (as
    ``resting_hrv_check``) and the snapshot (as ``health_snapshot``) are stored; the ski day is
    skipped by sport, the three monitoring-type entries are not activities, the manual activity has
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
    stored, and the ski day, the three entries and the manual activity are reported as before."""
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
    assert "Not an activity: 3" in lines
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


# --- F023: race tags read from Connect's event type, end to end --------------------------------

# Connect's own distance (m) and duration (s) for each run, to Connect's precision: the decoded
# session values are 8325.91 m and 2816.667 s for ``sample_run``, 10021.48 m and 3117.009 s for
# ``dev_fields_run``. Each agrees within 1 % with its own run only.
SAMPLE_RUN_CONNECT = (8325.9, 2816.7)
DEV_FIELDS_RUN_CONNECT = (10021.5, 3117.0)
# ``sample_run`` starts 2026-09-01T12:34:21Z, before the default 30-day tags window of TODAY.
SHARED_START_GMT = "2026-09-01 12:34:21"
SHARED_START_LOCAL = "2026-09-01 14:34:21"
SHARED_START_STORED = "2026-09-01T12:34:21+00:00"
BACKFILL = ("sync", "tags", "--since", "2016-01-01")
RACE = {"typeKey": "race", "typeId": 1}
NOT_RACE = {"typeKey": "uncategorized", "typeId": 9}


def _connect_run(
    activity_id: int, local: str, gmt: str, measured: tuple[float, float], event: dict[str, Any]
) -> dict[str, Any]:
    """A listed Connect running activity with its UTC start, distance, duration and event type."""
    distance, duration = measured
    return {
        "activityId": activity_id,
        "activityName": f"activity {activity_id}",
        "startTimeLocal": local,
        "startTimeGMT": gmt,
        "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
        "eventType": dict(event),
        "distance": distance,
        "duration": duration,
        "manualActivity": False,
    }


def _upload(client: TestClient, name: str, data: bytes) -> str:
    response = client.post(
        "/sessions",
        files={"file": (name, data, "application/octet-stream")},
        data={"runs_and_captures_only": "true"},
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _two_runs_at_one_second(client: TestClient) -> tuple[str, str]:
    """Upload ``sample_run`` and ``dev_fields_run`` moved onto its first second; return their ids.

    Both are stored as sport ``running`` with the same ``start_time``, so each Connect item at that
    second has both as candidates and only distance and duration can tell them apart."""
    sample = _fixture("sample_run")
    dev = _fixture("dev_fields_run")
    moved = fit_patch.shift_timestamps(dev, _start(sample) - _start(dev))
    sample_id = _upload(client, "sample_run.fit", sample)
    dev_id = _upload(client, "dev_fields_run.fit", moved)
    rows = {row["session_id"]: row for row in _stored(client)}
    assert {rows[sample_id]["start_time"], rows[dev_id]["start_time"]} == {SHARED_START_STORED}
    assert (rows[sample_id]["sport"], rows[dev_id]["sport"]) == ("running", "running")
    return sample_id, dev_id


def _shared_second_listing(sample_event: dict[str, Any]) -> FakeGarmin:
    """Connect lists both runs at the shared second, ``sample_run`` with ``sample_event``."""
    return FakeGarmin(
        [
            _connect_run(501, SHARED_START_LOCAL, SHARED_START_GMT, SAMPLE_RUN_CONNECT, sample_event),
            _connect_run(502, SHARED_START_LOCAL, SHARED_START_GMT, DEV_FIELDS_RUN_CONNECT, NOT_RACE),
        ],
        {},
        {},
    )


def _tags(client: TestClient) -> dict[str, str | None]:
    return {row["session_id"]: row.get("activity_tag") for row in _stored(client)}


def test_of_two_real_runs_at_one_second_only_the_agreeing_one_is_tagged(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two real runs from two watches start on one second; Connect marks one of them a race with
    its own distance and duration. Only that run is tagged, and ``GET /sessions`` shows it; the
    other run's item agrees with the other run and leaves it untagged."""
    client = cli_against_app
    sample_id, dev_id = _two_runs_at_one_second(client)
    fake = _shared_second_listing(RACE)
    _serve(monkeypatch, fake)
    result = _run(*BACKFILL)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert _counts(tagged=1, unchanged=1) in lines
    assert not any(line.startswith(("Race ambiguous", "Race mismatch")) for line in lines)
    assert _tags(client) == {sample_id: "race", dev_id: None}
    assert fake.downloads == []


def test_sync_tags_backfill_tags_then_resend_is_unchanged(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backfill ``runcoach sync tags --since 2016-01-01`` reaches a run older than the default
    30 days and tags it; sending the same listing again changes nothing."""
    client = cli_against_app
    sample_id, dev_id = _two_runs_at_one_second(client)
    _serve(monkeypatch, _shared_second_listing(RACE))
    default = _run("sync", "tags")
    assert default.exit_code == 0, default.output
    assert _counts() in default.output.splitlines()
    assert _tags(client) == {sample_id: None, dev_id: None}
    first = _run(*BACKFILL)
    assert first.exit_code == 0, first.output
    assert "From 2016-01-01 to 2026-10-12" in first.output.splitlines()
    assert _counts(tagged=1, unchanged=1) in first.output.splitlines()
    again = _run(*BACKFILL)
    assert again.exit_code == 0, again.output
    assert _counts(unchanged=2) in again.output.splitlines()
    assert _tags(client) == {sample_id: "race", dev_id: None}


def test_untagged_in_connect_clears_the_race_tag(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the race is un-marked in Connect, the next read clears the stored race tag."""
    client = cli_against_app
    sample_id, dev_id = _two_runs_at_one_second(client)
    _serve(monkeypatch, _shared_second_listing(RACE))
    assert _run(*BACKFILL).exit_code == 0
    assert _tags(client) == {sample_id: "race", dev_id: None}
    _serve(monkeypatch, _shared_second_listing(NOT_RACE))
    result = _run(*BACKFILL)
    assert result.exit_code == 0, result.output
    assert _counts(cleared=1, unchanged=1) in result.output.splitlines()
    assert _tags(client) == {sample_id: None, dev_id: None}


def test_a_run_uploaded_and_tagged_in_one_sync(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``runcoach sync`` uploads a new race and its tags step, reusing the upload listing, tags it:
    the run is stored and tagged by the one command."""
    client = cli_against_app
    run = fit_patch.shift_timestamps(_fixture("sample_run"), 40 * 86_400)  # 2026-10-11T12:34:21Z
    fake = FakeGarmin(
        [_connect_run(601, "2026-10-11 14:34:21", "2026-10-11 12:34:21", SAMPLE_RUN_CONNECT, RACE)],
        {601: _zip([("601_ACTIVITY.fit", run)])},
        {},
    )
    _serve(monkeypatch, fake)
    result = _run("sync")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "Stored: 1" in lines
    assert _counts(tagged=1) in lines
    stored = _stored(client)
    assert [(row["start_time"], row["activity_tag"]) for row in stored] == [
        ("2026-10-11T12:34:21+00:00", "race")
    ]
    assert ("original", 601) in fake.downloads


TRAINING = {"typeKey": "training", "typeId": 2}


def test_sync_tags_against_the_app_retags_race_to_threshold_workout(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F026 AC1 end to end: a stored run tagged ``race`` that Connect now lists as Training is
    retagged ``threshold_workout`` by ``runcoach sync tags``; the served ``/openapi.json`` is the
    oracle for the CLI's ``tag`` check (no fake document), and ``GET /sessions`` reads the new tag
    back. Listed as Race again, it is retagged ``race``; as uncategorized, cleared."""
    client = cli_against_app
    sample_id = _upload(client, "sample_run.fit", _fixture("sample_run"))

    def listing(event: dict[str, Any]) -> FakeGarmin:
        return FakeGarmin(
            [_connect_run(501, SHARED_START_LOCAL, SHARED_START_GMT, SAMPLE_RUN_CONNECT, event)], {}, {}
        )

    _serve(monkeypatch, listing(RACE))
    assert _run(*BACKFILL).exit_code == 0
    assert _tags(client) == {sample_id: "race"}

    fake = listing(TRAINING)
    _serve(monkeypatch, fake)
    result = _run(*BACKFILL)
    assert result.exit_code == 0, result.output
    assert _counts(retagged=1, workouts_tagged=1) in result.output.splitlines()
    unmatched = ("Workout ", "Race not stored", "Race mismatch", "Race ambiguous")
    assert not any(line.startswith(unmatched) for line in result.output.splitlines())
    assert _tags(client) == {sample_id: "threshold_workout"}
    assert fake.downloads == []

    again = _run(*BACKFILL)
    assert again.exit_code == 0, again.output
    assert _counts(unchanged=1) in again.output.splitlines()

    _serve(monkeypatch, listing(RACE))
    back = _run(*BACKFILL)
    assert _counts(retagged=1) in back.output.splitlines(), back.output
    assert _tags(client) == {sample_id: "race"}

    _serve(monkeypatch, listing(NOT_RACE))
    cleared = _run(*BACKFILL)
    assert _counts(cleared=1) in cleared.output.splitlines(), cleared.output
    assert _tags(client) == {sample_id: None}


# --- F025 AC1: a sync stores the laps a one-file upload stores ----------------------------------

# The laps_read_at clock, fixed through db.laps_read_now so the column is compared, not excluded.
LAPS_READ_AT = "2026-10-12T08:00:00+00:00"


def test_a_sync_stores_the_same_laps_as_one_file_upload(
    cli_against_app: TestClient, monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path, tmp_path: Path
) -> None:
    """``sample_run`` synced from a Connect listing at its real UTC start stores the same ``laps``
    and ``workout_steps`` rows, every column but ``session_id``, and the same ``laps_read_at`` as
    the same file uploaded with ``POST /sessions`` into a separate isolated store."""
    monkeypatch.setattr(db, "laps_read_now", lambda: LAPS_READ_AT)
    fake = FakeGarmin(
        [_activity(701, SHARED_START_LOCAL, "running", 1, 17, gmt=SHARED_START_GMT)],
        {701: _zip([("701_ACTIVITY.fit", _fixture("sample_run"))])},
        {},
    )
    _serve(monkeypatch, fake)
    result = _run("sync", "--since", "2026-09-01")
    assert result.exit_code == 0, result.output
    assert "Stored: 1" in result.output.splitlines()
    assert ("original", 701) in fake.downloads
    assert [row["start_time"] for row in _stored(cli_against_app)] == [SHARED_START_STORED]
    synced = against_app._stored_laps(isolated_data_dir)

    store_b = tmp_path / "store_b"
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=store_b,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone="UTC",
    )
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: config)
    db._load_config_cached.cache_clear()
    assert db._load_config_cached().data_dir == store_b
    with TestClient(against_app.app) as direct:
        _upload(direct, "sample_run.fit", _fixture("sample_run"))
    uploaded = against_app._stored_laps(store_b)

    print(f"synced: {len(synced['laps'])} laps, {len(synced['workout_steps'])} steps")
    assert synced["laps"] and synced["workout_steps"], synced
    assert synced["laps_read_at"] == LAPS_READ_AT
    assert synced == uploaded


def test_the_harness_lives_in_support() -> None:
    """The harness is ``tests/support/against_app.py``'s, bound here, and this file defines none of it."""
    assert Path(against_app.__file__).resolve() == (SUPPORT / "against_app.py").resolve()
    for name in HARNESS:
        assert globals()[name] is getattr(against_app, name), name
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert defined.isdisjoint(HARNESS), defined & set(HARNESS)
