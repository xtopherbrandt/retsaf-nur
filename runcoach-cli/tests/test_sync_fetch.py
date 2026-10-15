"""``runcoach sync``: what is fetched and the upload order (F022 reference section 3).

A fake ``GarminClient`` serves three days (2026-10-10 to 10-12): a run, a treadmill run
(parentTypeId 1), a trail run, a strap check (``other``), a ski day (``other``), a cycling ride, a
manual activity and Health Snapshot zips with monitoring entries, plus a run the day before the
range. ``POST /sessions`` is served by ``install_mock_client``. Every file is opaque bytes naming
itself (the API is mocked, so nothing is decoded); the upload order is read back from those bytes.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 12)
START = date(2026, 10, 10)
API_URL = "http://example.test"


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _gmt(local: str) -> str:
    """``startTimeGMT`` for a local start in the tests' UTC-7 zone, as Connect lists it."""
    return (datetime.fromisoformat(local) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")


def _activity(
    activity_id: int, local: str, type_key: str, type_id: int, parent: int, manual: bool = False
) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": f"activity {activity_id}",
        "startTimeLocal": local,
        "startTimeGMT": _gmt(local),
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": parent},
        "eventType": {"typeKey": "uncategorized", "typeId": 9},
        "manualActivity": manual,
    }


ACTIVITIES = [
    # Listed newest first, as Connect lists them; sync must reorder.
    _activity(301, "2026-10-12 08:00:00", "other", 4, 17),  # ski day
    _activity(203, "2026-10-11 09:00:00", "running", 1, 17, manual=True),  # manual
    _activity(202, "2026-10-11 18:00:00", "treadmill_running", 18, 1),
    _activity(201, "2026-10-11 18:00:00", "trail_running", 6, 1),  # same start: id order
    _activity(103, "2026-10-10 12:00:00", "cycling", 2, 17),  # ride: never downloaded
    _activity(101, "2026-10-10 07:00:00", "running", 1, 17),  # run
    _activity(102, "2026-10-10 06:30:00", "other", 4, 17),  # strap check, before the run
    _activity(99, "2026-10-09 07:00:00", "running", 1, 17),  # before start: ignored
]

SNAPSHOTS = {
    date(2026, 10, 10): _zip(
        [
            ("1_ACTIVITY.fit", b"SNAP-1010-activity"),
            ("2_WELLNESS.fit", b"SNAP-1010-wellness"),
            ("3_METRICS.fit", b"SNAP-1010-metrics"),
        ]
    ),
    date(2026, 10, 11): _zip([("4_WELLNESS.fit", b"SNAP-1011-wellness")]),
}


def _part(body: bytes, field: str) -> tuple[bytes, bytes]:
    """The headers and content of the multipart part named ``field``."""
    marker = f'name="{field}"'.encode()
    for part in body.split(b"\r\n--"):
        head, _, content = part.partition(b"\r\n\r\n")
        if marker in head:
            return head, content.removesuffix(b"\r\n")
    raise AssertionError(f"no part {field}")


def _check_upload(body: bytes) -> None:
    """Every upload carries the switch and never the retired ``resting_capture`` field."""
    _part(body, "runs_and_captures_only")
    assert b'name="resting_capture"' not in body


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client, fake_api):
    def setup(fake: Any) -> Any:
        api = fake_api(check=_check_upload)
        install_mock_client(api)
        monkeypatch.setattr(sync, "_library_missing", lambda: False)
        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))
        monkeypatch.setattr(sync, "today", lambda: TODAY)
        monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))
        return api

    return setup


EXPECTED_UPLOADS = [
    b"SNAP-1010-activity",
    b"SNAP-1010-wellness",
    b"SNAP-1010-metrics",
    b"ACT-102",
    b"ACT-101",
    b"SNAP-1011-wellness",
    b"ACT-201",
    b"ACT-202",
    b"ACT-301",
]


def test_snapshot_zip_first_then_activities_by_start_then_id(env, fake_garmin) -> None:
    fake = fake_garmin(ACTIVITIES, snapshots=SNAPSHOTS)
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "From 2026-10-10 to 2026-10-12"
    assert fake.events == [
        ("list", (START, TODAY)),
        ("snapshot", date(2026, 10, 10)),
        ("original", 102),
        ("original", 101),
        ("snapshot", date(2026, 10, 11)),
        ("original", 201),
        ("original", 202),
        ("snapshot", date(2026, 10, 12)),
        ("original", 301),
        # The race tags step: the range starts after today - 29 days, so one more listing.
        ("list", (TODAY - timedelta(days=29), TODAY)),
    ]
    assert api.uploads == EXPECTED_UPLOADS


def test_only_the_running_family_and_other_are_downloaded(env, fake_garmin) -> None:
    fake = fake_garmin(ACTIVITIES, snapshots=SNAPSHOTS)
    env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    downloaded = [arg for kind, arg in fake.events if kind == "original"]
    assert sorted(downloaded) == [101, 102, 201, 202, 301]
    assert 103 not in downloaded  # cycling
    assert 203 not in downloaded  # manual
    assert 99 not in downloaded  # before the range


def test_a_manual_activity_is_recorded_as_no_fit_original_and_never_downloaded(env, fake_garmin) -> None:
    fake = fake_garmin(ACTIVITIES, snapshots=SNAPSHOTS)
    env(fake)

    results = sync.fetch(fake, API_URL, START, TODAY)

    manual = [r for r in results if r.activity_id == 203]
    assert len(manual) == 1
    assert manual[0].outcome == "no_fit_original"
    assert manual[0].status is None
    assert manual[0].day == date(2026, 10, 11)
    assert ("original", 203) not in fake.events
    assert all(r.activity_id != 103 for r in results)
    uploaded = [r for r in results if r.status is not None]
    assert [r.status for r in uploaded] == [201] * len(EXPECTED_UPLOADS)
    assert [r.day for r in uploaded] == [START] * 5 + [date(2026, 10, 11)] * 3 + [TODAY]


def test_an_item_result_keeps_no_reply_and_no_upload_body_alive(env, monkeypatch, fake_garmin) -> None:
    """Each ``ItemResult`` holds the reply's status, never the ``httpx.Response`` (or the 1 MiB
    multipart request body it references), so a long sync does not keep every upload in memory."""
    import dataclasses
    import gc
    import weakref

    from runcoach_cli import api_client

    big = b"\x00" * (1024 * 1024)
    originals = {
        activity_id: _zip([(f"{activity_id}_{n}.fit", big + bytes([n])) for n in range(10)])
        for activity_id in (101, 102, 201, 202, 301)
    }
    fake = fake_garmin(ACTIVITIES, snapshots=SNAPSHOTS, originals=originals)
    env(fake)
    replies: list[weakref.ref] = []
    real_upload = api_client.upload_fit_bytes

    def tracking_upload(*args, **kwargs):
        resp = real_upload(*args, **kwargs)
        replies.append(weakref.ref(resp))
        return resp

    monkeypatch.setattr(api_client, "upload_fit_bytes", tracking_upload)

    results = sync.fetch(fake, API_URL, START, TODAY)
    gc.collect()

    assert len(replies) == 50 + 4  # 50 activity entries of 1 MiB, 4 snapshot entries
    assert [r.status for r in results if r.status is not None] == [201] * 54
    for result in results:
        for field in dataclasses.fields(result):
            assert not isinstance(getattr(result, field.name), httpx.Response | httpx.Request), field.name
    assert [ref for ref in replies if ref() is not None] == []


def test_every_zip_entry_is_uploaded_with_the_switch(env, fake_garmin) -> None:
    odd = _zip(
        [
            ("readme.txt", b"ENTRY-readme"),
            ("../escape.fit", b"ENTRY-escape"),
            ("noext", b"ENTRY-noext"),
            ("sub/dir/x.FIT", b"ENTRY-nested"),
        ]
    )
    fake = fake_garmin([], snapshots={START: odd})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert api.uploads == [
        b"ENTRY-readme",
        b"ENTRY-escape",
        b"ENTRY-noext",
        b"ENTRY-nested",
    ]
    assert all(switch == b"true" for switch in api.switches)
    for head in api.heads:
        assert b'filename="garmin-' in head
        for name in (b"readme", b"escape", b"noext", b"x.FIT"):
            assert name not in head


def test_activities_outside_the_range_are_ignored(env, fake_garmin) -> None:
    after = _activity(401, "2026-10-13 07:00:00", "running", 1, 17)
    fake = fake_garmin([after, ACTIVITIES[-1]], snapshots={})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in fake.events].count("original") == 0
    assert api.uploads == []


def test_a_day_with_no_snapshot_uploads_only_its_activities(env, fake_garmin) -> None:
    fake = fake_garmin([_activity(101, "2026-10-10 07:00:00", "running", 1, 17)], snapshots={START: b""})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-101"]
