"""``runcoach sync``: each item's outcome, the summary and the exit code (F022 reference section 5).

A fake Garmin client serves the range 2026-10-10 to 10-12 and a mock API answers each upload by
the bytes it was sent (every file is opaque bytes naming itself; nothing is decoded). The summary
follows the ``From`` line: ``Stored``, ``Replaced`` (only when a night replaced an earlier summary of
itself), ``Already stored``, one ``Skipped <sport>`` line per sport
naming each item, ``Not an activity`` (a count), ``No FIT file`` (naming each) and one ``Failed``
line per failed item ending with ``runcoach sync --since <its day>``. Exit 0 when nothing failed,
else 1. A sync state file holding no dates is present (``fresh_sync_state``), so no line about a
missing one comes between the ``From`` line and the summary.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli import garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("fresh_sync_state")

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
    activity_id: int, local: str, name: str, type_key: str = "running", manual: bool = False
) -> dict[str, Any]:
    type_id = {"running": 1, "other": 4}[type_key]
    return {
        "activityId": activity_id,
        "activityName": name,
        "startTimeLocal": local,
        "startTimeGMT": _gmt(local),
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": 17},
        "eventType": {"typeKey": "uncategorized", "typeId": 9},
        "manualActivity": manual,
    }


def _skipped_sport(sport: str) -> httpx.Response:
    return httpx.Response(
        200, json={"skipped": True, "reason": "skipped_sport", "sport": sport, "filter_applied": True}
    )


def _non_activity(file_type: str) -> httpx.Response:
    return httpx.Response(
        200, json={"skipped": True, "reason": "non_activity", "file_type": file_type, "filter_applied": True}
    )


def _no_night() -> httpx.Response:
    """The API's 200 for an HRV_STATUS file that holds no night, as the switch's reply carries it."""
    return httpx.Response(
        200, json={"skipped": True, "reason": "no_night", "file_type": "hrv_status", "filter_applied": True}
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client):
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))

    def setup(fake: Any, api: Any) -> Any:
        install_mock_client(api)
        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))
        return api

    return setup


def _sync(*args: str):
    return runner.invoke(app, ["sync", "--since", "2026-10-10", *args], input=b"")


def _tags_line(unchanged: int = 0) -> str:
    """The race tags step's counts line, which follows the summary of a run that did not stop."""
    return (
        f"Race tags: tagged 0, retagged 0, cleared 0, unchanged {unchanged}, not stored 0, mismatch 0, "
        "ambiguous 0, other tag 0, skipped 0, workouts tagged 0"
    )


def _summary(output: str) -> list[str]:
    """The lines after the ``From`` line."""
    lines = output.splitlines()
    assert lines[0] == "From 2026-10-10 to 2026-10-12", output
    return lines[1:]


def test_each_outcome_row_has_its_summary_line(env, fake_garmin, fake_api) -> None:
    snapshot = _zip(
        [("1_WELLNESS.fit", b"MON-1"), ("2_METRICS.fit", b"MON-2"), ("3_ACTIVITY.fit", b"SNAP-ACT")]
    )
    fake = fake_garmin(
        [
            _activity(101, "2026-10-10 07:00:00", "Morning Run"),
            _activity(102, "2026-10-10 08:00:00", "Old Run"),
            _activity(301, "2026-10-11 09:00:00", "Whistler Alpine Skiing", type_key="other"),
            _activity(302, "2026-10-11 10:00:00", "Whistler Again", type_key="other"),
            _activity(303, "2026-10-11 11:00:00", "Yoga\x1b[2J", type_key="other"),
            _activity(203, "2026-10-12 06:00:00", "Typed In", manual=True),
            _activity(204, "2026-10-12 07:00:00", "Gone"),
            _activity(205, "2026-10-12 08:00:00", "Empty"),
        ],
        originals={204: garmin.NotFound("404"), 205: b""},
        snapshots={START: snapshot},
    )
    api = env(
        fake,
        fake_api(
            replies={
                b"MON-1": _non_activity("monitoring_b"),
                b"MON-2": _non_activity("44"),
                b"ACT-102": httpx.Response(409, json={"detail": "duplicate"}),
                b"ACT-301": _skipped_sport("alpine_skiing"),
                b"ACT-302": _skipped_sport("alpine_skiing"),
                b"ACT-303": _skipped_sport("yoga"),
            }
        ),
    )

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 2",
        "Already stored: 1",
        "Skipped alpine_skiing: 2 (activity 301 (Whistler Alpine Skiing), activity 302 (Whistler Again))",
        "Skipped yoga: 1 (activity 303 (Yoga?[2J))",
        "Not an activity: 2",
        "No night: 0",
        "No FIT file: 3 (activity 203 (Typed In), activity 204 (Gone), activity 205 (Empty))",
        # The race tags step sends the seven non-manual activities; none is a race.
        _tags_line(unchanged=7),
    ]
    assert len(api.uploads) == 8


def test_outcomes_are_recorded_on_each_result(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin(
        [_activity(101, "2026-10-10 07:00:00", "Run"), _activity(102, "2026-10-10 08:00:00", "Dup")],
        snapshots={START: _zip([("1_WELLNESS.fit", b"MON-1")])},
    )
    replies = {b"MON-1": _non_activity("monitoring_b"), b"ACT-102": httpx.Response(409, json={})}
    env(fake, fake_api(replies=replies))

    results = sync.fetch(fake, API_URL, START, TODAY)

    assert [(r.item, r.outcome) for r in results] == [
        ("snapshot 2026-10-10: 1_WELLNESS.fit", "non_activity"),
        ("activity 101 (Run): 101_ACTIVITY.fit", "stored"),
        ("activity 102 (Dup): 102_ACTIVITY.fit", "already_stored"),
    ]


def test_an_empty_range_prints_zero_counts_and_exits_0(env, fake_garmin, fake_api) -> None:
    env(fake_garmin([]), fake_api())

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 0",
        "Already stored: 0",
        "Not an activity: 0",
        "No night: 0",
        "No FIT file: 0",
        _tags_line(),
    ]


def test_the_summary_prints_a_no_night_line(env, fake_garmin, fake_api) -> None:
    """A ``no_night`` reply is counted on its own ``No night`` line, after ``Not an activity``,
    not on that line and not as a failure."""
    snapshot = _zip(
        [("1_WELLNESS.fit", b"MON-1"), ("2_HRV_STATUS.fit", b"NIGHT-NONE-1"), ("3_HRV_STATUS.fit", b"NIGHT-NONE-2")]
    )
    fake = fake_garmin([_activity(101, "2026-10-11 07:00:00", "Run")], snapshots={START: snapshot})
    replies = {b"MON-1": _non_activity("monitoring_b"), b"NIGHT-NONE-1": _no_night(), b"NIGHT-NONE-2": _no_night()}
    env(fake, fake_api(replies=replies))

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 1",
        "Already stored: 0",
        "Not an activity: 1",
        "No night: 2",
        "No FIT file: 0",
        _tags_line(unchanged=1),
    ]


def test_a_no_night_reply_is_counted_and_not_retried(env, fake_garmin, fake_api, fresh_sync_state) -> None:
    """A sync whose only item is a no-night file exits 0 and remembers no failed day, so the next
    default sync starts where the store and the completed run put it, not on that day."""
    night_day = START
    fake = fake_garmin([], snapshots={night_day: _zip([("1_HRV_STATUS.fit", b"NIGHT-NONE")])})
    api = env(fake, fake_api(replies={b"NIGHT-NONE": _no_night()}))

    result = _sync()

    assert result.exit_code == 0, result.output
    summary = _summary(result.output)
    assert "No night: 1" in summary
    assert "Stored: 0" in summary
    assert not any(line.startswith("Failed") for line in summary), summary
    assert api.uploads == [b"NIGHT-NONE"]
    results = sync.fetch(fake, API_URL, START, TODAY)
    assert [(r.item, r.outcome, r.detail) for r in results] == [
        ("snapshot 2026-10-10: 1_HRV_STATUS.fit", "no_night", "hrv_status")
    ]
    assert fresh_sync_state.read_bytes() == b'{"last_completed": "2026-10-12", "oldest_failed": null}'

    # The next default sync: the store's newest run is on 10-12, so the store rule and the completed
    # run both start on 10-11; a remembered no-night day would have moved the start back to 10-10.
    newest = {"session_id": "r1", "start_time": "2026-10-12T14:00:00+00:00", "sport": "running"}
    env(fake_garmin([]), fake_api(pages=[[newest]]))

    again = runner.invoke(app, ["sync"], input=b"")

    assert again.exit_code == 0, again.output
    assert again.output.splitlines()[0] == "From 2026-10-11 to 2026-10-12", again.output
    assert "a failed item on that day" not in again.output


def test_a_failed_item_prints_its_since_command_and_exits_1(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin(
        [
            _activity(101, "2026-10-10 07:00:00", "Run"),
            _activity(201, "2026-10-11 07:00:00", "Bad"),
            _activity(301, "2026-10-12 07:00:00", "Later"),
        ]
    )
    api = env(fake, fake_api(replies={b"ACT-201": httpx.Response(400, json={"detail": "not a FIT file"})}))

    result = _sync()

    assert result.exit_code == 1, result.output
    summary = _summary(result.output)
    assert "Stored: 2" in summary
    failed = [line for line in summary if line.startswith("Failed")]
    assert failed == [
        (
            "Failed: 2026-10-11, activity 201 (Bad): 201_ACTIVITY.fit, API returned 400: not a FIT file, "
            "later runs do not return for it: it would fail the same way again, runcoach sync --since 2026-10-11"
        )
    ]
    # A failed item does not stop the sync.
    assert api.uploads == [b"ACT-101", b"ACT-201", b"ACT-301"]


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (
            httpx.Response(413, text="too large"),
            # A 413 rejects the file itself (over a size or decode ceiling): permanent.
            "API returned 413: too large, later runs do not return for it: it would fail the same way again",
        ),
        (httpx.Response(500, json={"detail": ["x"]}), 'API returned 500: {"detail":["x"]}'),
        (
            httpx.Response(200, json={"skipped": True, "reason": "mystery", "filter_applied": True}),
            "API returned 200 with an unknown skip reason 'mystery'",
        ),
        (httpx.Response(201, text="not json"), None),
    ],
)
def test_any_other_reply_is_failed(
    env, reply: httpx.Response, detail: str | None, fake_garmin, fake_api
) -> None:
    fake = fake_garmin([_activity(101, "2026-10-10 07:00:00", "Run")])
    env(fake, fake_api(replies={b"ACT-101": reply}))

    result = _sync()

    assert result.exit_code == 1, result.output
    if detail is None:
        # A 201 whose body cannot be read carries no filter_applied: the old-API stop.
        assert "the API does not support runs_and_captures_only: update it" in result.output
    else:
        failed = [line for line in _summary(result.output) if line.startswith("Failed")]
        assert failed == [
            f"Failed: 2026-10-10, activity 101 (Run): 101_ACTIVITY.fit, {detail}, runcoach sync --since 2026-10-10"
        ]


def test_no_fit_original_is_not_a_failure(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin(
        [
            _activity(203, "2026-10-10 06:00:00", "Typed In", manual=True),
            _activity(204, "2026-10-11 07:00:00", "Gone"),
            _activity(205, "2026-10-12 08:00:00", "Empty"),
        ],
        originals={204: garmin.NotFound("404"), 205: b""},
    )
    api = env(fake, fake_api())

    result = _sync()

    assert result.exit_code == 0, result.output
    summary = _summary(result.output)
    assert "No FIT file: 3 (activity 203 (Typed In), activity 204 (Gone), activity 205 (Empty))" in summary
    assert not any(line.startswith("Failed") for line in summary)
    assert api.uploads == []


def test_a_failure_with_no_day_names_the_range_start(env, fake_garmin, fake_api) -> None:
    """An unreadable summary has no day: its Failed line says so and recovers from the range start."""
    fake = fake_garmin(
        [{"activityName": "No Id", "startTimeLocal": "2026-10-11 07:00:00", "activityType": {"typeId": 1}}]
    )
    env(fake, fake_api())

    result = _sync()

    assert result.exit_code == 1, result.output
    failed = [line for line in _summary(result.output) if line.startswith("Failed")]
    assert len(failed) == 1
    assert failed[0].startswith("Failed: day unknown, activity None (No Id), unreadable summary: ")
    assert failed[0].endswith(", runcoach sync --since 2026-10-10")


def test_hostile_names_and_details_are_printed_with_controls_replaced(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin([_activity(101, "2026-10-10 07:00:00", "Run\r\x07")])
    env(fake, fake_api(replies={b"ACT-101": httpx.Response(400, json={"detail": "bad\x1b[31m"})}))

    result = _sync()

    failed = [line for line in _summary(result.output) if line.startswith("Failed")]
    assert failed == [
        (
            "Failed: 2026-10-10, activity 101 (Run??): 101_ACTIVITY.fit, API returned 400: bad?[31m, "
            "later runs do not return for it: it would fail the same way again, runcoach sync --since 2026-10-10"
        )
    ]
    assert "\x1b" not in result.output and "\x07" not in result.output


REPLACED_LINE = (
    "Replaced: 1 (night summaries replaced by a later summary of the same night, not extra nights)"
)


def test_a_replaced_night_is_counted_as_replaced(env, fake_garmin, fake_api) -> None:
    """A day zip holding both summaries of one night: the earlier is stored, and the later's 201
    names the session it replaced, so it is counted on a ``Replaced`` line after ``Stored``, not as a
    second stored night."""
    snapshot = _zip([("1_HRV_STATUS.fit", b"NIGHT-EARLY"), ("2_HRV_STATUS.fit", b"NIGHT-LATE")])
    fake = fake_garmin([], snapshots={START: snapshot})
    api = env(fake, fake_api(replies={b"NIGHT-LATE": _created(replaced_session_id="s1")}))

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 1",
        REPLACED_LINE,
        "Already stored: 0",
        "Not an activity: 0",
        "No night: 0",
        "No FIT file: 0",
        _tags_line(),
    ]
    assert api.uploads == [b"NIGHT-EARLY", b"NIGHT-LATE"]
    results = sync.fetch(fake, API_URL, START, TODAY)
    assert [r.outcome for r in results] == ["stored", "replaced"]


def _created(**fields: Any) -> httpx.Response:
    """A 201 for an upload sent with the switch, carrying ``fields`` besides."""
    body = {"session_id": "s2", "quality_flags": [], "filter_applied": True}
    return httpx.Response(201, json={**body, **fields})


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (_created(replaced_session_id="s1"), ("replaced", True)),
        # Anything but a non-empty string names no replaced session: the file was stored.
        (_created(replaced_session_id=None), ("stored", True)),
        (_created(replaced_session_id=""), ("stored", True)),
        (_created(replaced_session_id=7), ("stored", True)),
        (_created(replaced_session_id=0), ("stored", True)),
        (_created(replaced_session_id=True), ("stored", True)),
        (_created(replaced_session_id=["s1"]), ("stored", True)),
        (_created(), ("stored", True)),
        # A 201 lacking the switch is the old-API stop whatever else it names.
        (httpx.Response(201, json={"session_id": "s2", "replaced_session_id": "s1"}), ("replaced", False)),
        (httpx.Response(201), ("stored", False)),
        (httpx.Response(201, text="not json"), ("stored", False)),
    ],
)
def test_only_a_named_replaced_session_makes_a_201_replaced(
    reply: httpx.Response, expected: tuple[str, bool]
) -> None:
    outcome, detail, applied = sync._classify(reply)
    assert (outcome, applied) == expected
    assert detail == ""


def test_no_replaced_line_when_nothing_was_replaced() -> None:
    results = [sync.ItemResult(day=START, item="night", outcome=sync.STORED, name="night")]
    assert sync.summary_lines(results, START)[:2] == ["Stored: 1", "Already stored: 0"]
