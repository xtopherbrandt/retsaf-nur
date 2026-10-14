"""``runcoach sync``: the sync state file and the range it moves (F022 reference section 2).

``sync-state.json`` beside the CLI config holds ``last_completed`` and ``oldest_failed``; the range
starts at the earliest of the store rule, the day before ``last_completed`` and ``oldest_failed``.
Each row of the reference's state table and both of the critic's timelines is a test through the
``sync`` command. Garmin is a fake client (snapshots only, unless a test lists an activity) and the
API a mock that serves ``GET /sessions`` and refuses any upload, so nothing is uploaded. The config
home is the autouse ``isolated_config_path`` tmp folder, so the real ``~/.runcoach`` is never read.
"""

from __future__ import annotations

import json
from datetime import date, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli import config, garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

ZONE = timezone(timedelta(hours=-7))


def d(text: str) -> date:
    return date.fromisoformat(text)


class FakeGarmin:
    """Lists ``activities``; a day's snapshot is ``snapshots[day]`` (an exception is raised), else 404."""

    def __init__(
        self, snapshots: dict[date, Exception] | None = None, activities: list | None = None
    ) -> None:
        self.snapshots = snapshots or {}
        self.activities = activities or []
        self.listed: list[tuple[date, date]] = []
        self.snapshot_days: list[date] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        self.listed.append((start, end))
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        raise AssertionError("no activity is downloaded in these tests")

    def download_snapshot(self, day: date) -> bytes:
        self.snapshot_days.append(day)
        value = self.snapshots.get(day)
        if value is not None:
            raise value
        raise garmin.NotFound(f"no snapshot on {day}")


def _row(session_id: str, local_day: date, tag: str | None = None) -> dict[str, Any]:
    # 08:00 local (UTC-7) is 15:00 UTC on the same day.
    return {
        "session_id": session_id,
        "start_time": f"{local_day.isoformat()}T15:00:00+00:00",
        "sport": "running" if tag is None else "other",
        "activity_tag": tag,
        "name": None,
    }


def _store(rows: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/sessions":
            return httpx.Response(200, json={"items": rows, "next_cursor": None})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return handler


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, install_mock_client):
    """``run(today, rows, client, *args)``: one ``runcoach sync`` on ``today`` against a store of ``rows``.

    ``client`` is a ``FakeGarmin`` or an exception ``open_client`` raises (a login needed).
    """
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)

    def invoke(today: date, rows: list[dict[str, Any]], client: Any, *args: str):
        monkeypatch.setattr(sync, "today", lambda: today)
        install_mock_client(_store(rows))

        def open_client():
            if isinstance(client, Exception):
                raise client
            return client

        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=open_client))
        return runner.invoke(app, ["sync", *args])

    return invoke


def _state_path() -> Path:
    return config.CONFIG_PATH.parent / "sync-state.json"


def _write_state(last_completed: str | None, oldest_failed: str | None) -> None:
    _state_path().write_text(
        json.dumps({"last_completed": last_completed, "oldest_failed": oldest_failed}), encoding="utf-8"
    )


def _state() -> dict[str, Any]:
    return json.loads(_state_path().read_bytes().decode("utf-8"))


def _lines(output: str) -> list[str]:
    return output.splitlines()


RUN_10_12 = [_row("r1", d("2026-10-12"))]
TODAY = d("2026-10-13")


# --- the state table, one test per row -----------------------------------------------------------


def test_row_last_completed_10_05_starts_the_day_before_it(run) -> None:
    _write_state("2026-10-05", None)
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-04 to 2026-10-13"
    assert lines[1] == "Starting at 2026-10-04: the last completed sync ended 2026-10-05"
    assert fake.listed == [(d("2026-10-04"), TODAY)]
    assert fake.snapshot_days[0] == d("2026-10-04")
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_row_oldest_failed_10_08_starts_on_that_day(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-08 to 2026-10-13"
    assert lines[1] == "Starting at 2026-10-08: a failed item on that day"
    assert fake.listed == [(d("2026-10-08"), TODAY)]


def test_row_state_after_the_store_rule_leaves_the_store_rule(run) -> None:
    _write_state("2026-10-12", None)
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-11 to 2026-10-13"
    assert not any(line.startswith("Starting at") for line in lines)
    assert "sync state" not in result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_row_no_state_file_uses_the_store_rule_names_the_file_and_writes_it(run) -> None:
    assert not _state_path().exists()
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-10-11 to 2026-10-13"
    notes = [line for line in _lines(result.output) if line.startswith("No sync state at ")]
    assert len(notes) == 1 and str(_state_path()) in notes[0], result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"\xff\xfe\x00{",
        b"[]",
        b'"2026-10-01"',
        b'{"last_completed": "2026-10-01"}',
        b'{"oldest_failed": "2026-10-01"}',
        b'{"last_completed": "2026-13-01", "oldest_failed": null}',
        b'{"last_completed": "2026-10-1", "oldest_failed": null}',
        b'{"last_completed": "2026-W40-1", "oldest_failed": null}',
        b'{"last_completed": 20261001, "oldest_failed": null}',
        b'{"last_completed": null, "oldest_failed": ["2026-10-01"]}',
        b'{"last_completed": "2026-10-14", "oldest_failed": null}',
        b'{"last_completed": null, "oldest_failed": "2119-01-01"}',
    ],
)
def test_row_a_bad_state_file_warns_uses_the_store_rule_and_is_rewritten(run, content: bytes) -> None:
    _state_path().write_bytes(content)
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-11 to 2026-10-13"
    assert not any(line.startswith("Starting at") for line in lines)
    warnings = [line for line in lines if line.startswith("Warning:") and "sync state" in line]
    assert len(warnings) == 1 and str(_state_path()) in warnings[0], result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_a_state_path_that_cannot_be_read_or_written_warns_and_keeps_the_exit_code(run) -> None:
    _state_path().mkdir()
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-10-11 to 2026-10-13"
    flat = " ".join(result.output.split())
    assert "could not save the sync state" in flat, result.output
    assert _state_path().is_dir()
    # The temporary file the write made is removed again.
    assert sorted(p.name for p in _state_path().parent.iterdir()) == ["cli.toml", "sync-state.json"]


# --- what a run writes ---------------------------------------------------------------------------


def test_a_stop_leaves_the_state_unchanged(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    before = _state()

    result = run(TODAY, RUN_10_12, garmin.LoginNeeded("no token"))

    assert result.exit_code == 1, result.output
    assert "Garmin login needed" in result.output
    assert _state() == before


def test_a_stop_on_the_failed_day_keeps_it_and_last_completed(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin({d("2026-10-08"): garmin.RateLimited("429")})

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 1, result.output
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": "2026-10-08"}


def test_a_stop_after_the_failed_day_was_fetched_cleanly_clears_it_but_not_last_completed(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin({d("2026-10-10"): garmin.RateLimited("429")})

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 1, result.output
    assert d("2026-10-08") in fake.snapshot_days
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": None}


def test_a_stop_after_a_new_failure_records_that_day(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin(
        {
            d("2026-10-09"): garmin.GarminError("Daily snapshot HTTP error: 503"),
            d("2026-10-10"): garmin.RateLimited("429"),
        }
    )

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 1, result.output
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": "2026-10-09"}


def test_the_failed_day_is_cleared_once_fetched_cleanly(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    assert d("2026-10-08") in fake.snapshot_days
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_a_failure_on_a_later_day_than_the_old_one_still_clears_the_old_one(run) -> None:
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin({d("2026-10-12"): garmin.GarminError("Daily snapshot HTTP error: 503")})

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 1, result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": "2026-10-12"}


def test_a_failed_item_with_no_day_counts_as_the_runs_start(run) -> None:
    unreadable = {"activityId": 77, "activityName": "broken", "activityType": {"typeKey": "running"}}
    fake = FakeGarmin(activities=[unreadable])

    result = run(TODAY, RUN_10_12, fake, "--since", "2026-10-11")

    assert result.exit_code == 1, result.output
    assert "Failed: day unknown" in result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": "2026-10-11"}


def test_since_replaces_the_start_and_keeps_an_older_failed_day(run) -> None:
    _write_state("2026-10-12", "2026-10-05")
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake, "--since", "2026-10-10")

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-10 to 2026-10-13"
    assert not any(line.startswith("Starting at") for line in lines)
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": "2026-10-05"}


def test_since_covering_the_failed_day_clears_it(run) -> None:
    _write_state("2026-10-12", "2026-10-05")
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake, "--since", "2026-10-05")

    assert result.exit_code == 0, result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_logout_keeps_the_sync_state(run) -> None:
    _write_state("2026-10-12", "2026-10-08")

    result = runner.invoke(app, ["sync", "logout"])

    assert result.exit_code == 0, result.output
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": "2026-10-08"}


# --- the critic's two timelines, end to end -------------------------------------------------------


def test_timeline_a_failed_day_is_retried_every_run_until_fetched(run) -> None:
    """A daily sync fails D's snapshot; D is fetched again on D+1, D+2 and D+3, and then not."""
    failed_day = d("2026-10-10")
    broken = {failed_day: garmin.GarminError("Daily snapshot HTTP error: 503")}

    def snapshot_rows(today: date) -> list[dict[str, Any]]:
        # Each day's own snapshot reached the store, so the store rule moves on daily.
        return [_row(f"s{today.isoformat()}", today, tag="health_snapshot")]

    seen: dict[date, tuple[str, bool, int]] = {}
    for offset, fixed in [(0, False), (1, False), (2, False), (3, False), (4, True), (5, True)]:
        today = failed_day + timedelta(days=offset)
        fake = FakeGarmin({} if fixed else broken)
        result = run(today, snapshot_rows(today), fake)
        seen[today] = (_lines(result.output)[0], failed_day in fake.snapshot_days, result.exit_code)

    assert seen[d("2026-10-10")] == ("From 2026-10-09 to 2026-10-10", True, 1)
    assert seen[d("2026-10-11")] == ("From 2026-10-09 to 2026-10-11", True, 1)
    # The store rule alone would start on 10-11 from here on: the failed day starts the range.
    assert seen[d("2026-10-12")] == ("From 2026-10-10 to 2026-10-12", True, 1)
    assert seen[d("2026-10-13")] == ("From 2026-10-10 to 2026-10-13", True, 1)
    # Garmin answers again: D is fetched cleanly, which clears it.
    assert seen[d("2026-10-14")] == ("From 2026-10-10 to 2026-10-14", True, 0)
    assert seen[d("2026-10-15")] == ("From 2026-10-13 to 2026-10-15", False, 0)
    assert _state() == {"last_completed": "2026-10-15", "oldest_failed": None}


def test_timeline_ten_days_of_login_needed_then_a_hand_upload_still_fetches_the_gap(run) -> None:
    store = [_row("r0", d("2026-10-01"))]
    first = FakeGarmin()
    result = run(d("2026-10-02"), store, first)
    assert result.exit_code == 0, result.output
    assert _state() == {"last_completed": "2026-10-02", "oldest_failed": None}

    for offset in range(1, 11):
        stopped = run(d("2026-10-02") + timedelta(days=offset), store, garmin.LoginNeeded("refused"))
        assert stopped.exit_code == 1
        assert "Garmin login needed" in stopped.output
    assert _state() == {"last_completed": "2026-10-02", "oldest_failed": None}

    # A run dated 10-12 reaches the store by hand; the store rule alone would start on 10-11.
    store = [_row("hand", d("2026-10-12")), *store]
    fake = FakeGarmin()
    result = run(TODAY, store, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-01 to 2026-10-13"
    assert lines[1] == "Starting at 2026-10-01: the last completed sync ended 2026-10-02"
    assert fake.snapshot_days == [d("2026-10-01") + timedelta(days=n) for n in range(13)]
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}
