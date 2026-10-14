"""``runcoach sync``: the sync state file and the range it moves (F022 reference section 2).

``sync-state.json`` beside the CLI config holds ``last_completed`` and ``oldest_failed``; the range
starts at the earliest of the store rule, the day before ``last_completed`` and ``oldest_failed``.
Each row of the reference's state table and each of its timelines is a test through the ``sync``
command; the temporary and permanent failure rows are in ``test_sync_hostile.py``. Garmin is a fake client (snapshots only, unless a test lists an activity) and the
API a mock that serves ``GET /sessions`` and refuses any upload, so nothing is uploaded. The config
home is the autouse ``isolated_config_path`` tmp folder, so the real ``~/.runcoach`` is never read.
"""

from __future__ import annotations

import ast
import json
import re
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


def test_a_future_last_completed_does_not_discard_a_valid_oldest_failed(run) -> None:
    """The clock moved back a day: ``last_completed`` is after today, ``oldest_failed`` still counts."""
    _write_state("2026-10-13", "2026-10-01")
    fake = FakeGarmin()

    result = run(d("2026-10-12"), [_row("r1", d("2026-10-11"))], fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-01 to 2026-10-12"
    assert lines[1] == "Starting at 2026-10-01: a failed item on that day"
    assert not any("sync state" in line for line in lines), result.output
    assert fake.snapshot_days[0] == d("2026-10-01")
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": None}


@pytest.mark.parametrize(
    ("last_completed", "oldest_failed"),
    [
        # last_completed read as today (10-13): its day before, 10-12, is after the store rule's 10-11.
        ("2026-10-14", None),
        # oldest_failed read as today: after the store rule's start, and fetched cleanly today.
        (None, "2119-01-01"),
        ("2119-01-01", "2026-10-14"),
    ],
)
def test_a_date_after_today_is_read_as_today_without_a_warning(run, last_completed, oldest_failed) -> None:
    _write_state(last_completed, oldest_failed)
    fake = FakeGarmin()

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0] == "From 2026-10-11 to 2026-10-13"
    assert not any(line.startswith("Starting at") for line in lines)
    assert not any("sync state" in line for line in lines), result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


def test_a_date_after_today_is_kept_as_today_by_a_stop(run) -> None:
    """Read as today, not dropped: a stop before any day keeps both keys, now dated today."""
    _write_state("2119-01-01", "2119-01-01")

    result = run(TODAY, RUN_10_12, garmin.LoginNeeded("no token"))

    assert result.exit_code == 1, result.output
    assert not any("sync state" in line for line in _lines(result.output)), result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": "2026-10-13"}


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


def test_a_stop_after_the_failed_day_was_fetched_cleanly_moves_it_to_the_stop_day(run) -> None:
    """The run fetched 10-08 and 10-09 cleanly but not 10-10 on: a failed day after 10-08 that the
    state does not record may lie there, so the stop day is kept, and last_completed is unchanged."""
    _write_state("2026-10-12", "2026-10-08")
    fake = FakeGarmin({d("2026-10-10"): garmin.RateLimited("429")})

    result = run(TODAY, RUN_10_12, fake)

    assert result.exit_code == 1, result.output
    assert d("2026-10-08") in fake.snapshot_days
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": "2026-10-10"}


def test_timeline_a_stop_never_drops_a_newer_failed_day(run) -> None:
    """Failed days 10-03 and 10-07; a run fetches 10-03 cleanly and stops on 10-05; 10-07 is still
    fetched by the run after it (the review scan's three-run timeline)."""
    store = [_row("r1", d("2026-10-12"))]
    broken = {
        d("2026-10-03"): garmin.GarminError("Daily snapshot HTTP error: 503"),
        d("2026-10-07"): garmin.GarminError("Daily snapshot HTTP error: 503"),
    }
    first = FakeGarmin(broken)
    result = run(d("2026-10-12"), store, first, "--since", "2026-10-01")
    assert result.exit_code == 1, result.output
    assert _state() == {"last_completed": "2026-10-12", "oldest_failed": "2026-10-03"}

    second = FakeGarmin({d("2026-10-05"): garmin.RateLimited("429")})
    result = run(d("2026-10-13"), store, second)
    assert result.exit_code == 1, result.output
    assert _lines(result.output)[0] == "From 2026-10-03 to 2026-10-13"
    assert second.snapshot_days == [d("2026-10-03"), d("2026-10-04"), d("2026-10-05")]

    third = FakeGarmin()
    result = run(d("2026-10-14"), store, third)
    assert result.exit_code == 0, result.output
    assert d("2026-10-07") in third.snapshot_days, (result.output, third.snapshot_days)
    assert _state() == {"last_completed": "2026-10-14", "oldest_failed": None}


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


def test_a_failed_item_with_no_day_is_permanent_and_not_remembered(run) -> None:
    """An unreadable activity summary fails the same way on every run: reported, not remembered."""
    unreadable = {"activityId": 77, "activityName": "broken", "activityType": {"typeKey": "running"}}
    fake = FakeGarmin(activities=[unreadable])

    result = run(TODAY, RUN_10_12, fake, "--since", "2026-10-11")

    assert result.exit_code == 1, result.output
    assert "Failed: day unknown" in result.output
    assert NOT_REVISITED in result.output
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}


NOT_REVISITED = "later runs do not return for it: it would fail the same way again"


def test_timeline_a_permanent_failure_is_fetched_again_while_a_range_covers_its_day(run) -> None:
    """A Garmin 410 on today's snapshot in a daily sync (the review scan's timeline): the next two
    days' ranges still cover that day, so it is fetched and reported again with the same line; no
    run goes back for it once the range has moved past it, since it never sets ``oldest_failed``."""
    refused = {d("2026-10-13"): garmin.GarminError("Download client error (410)", status=410)}
    store = [_row("r1", d("2026-10-12"))]
    _write_state("2026-10-12", None)

    first = FakeGarmin(dict(refused))
    result = run(d("2026-10-13"), store, first)
    assert result.exit_code == 1, result.output
    failed = [line for line in _lines(result.output) if line.startswith("Failed:")]
    assert failed == [
        (
            "Failed: 2026-10-13, snapshot 2026-10-13, Garmin: Download client error (410), "
            f"{NOT_REVISITED}, runcoach sync --since 2026-10-13"
        )
    ]
    assert _state() == {"last_completed": "2026-10-13", "oldest_failed": None}

    store.insert(0, _row("r2", d("2026-10-13")))
    second = FakeGarmin(dict(refused))
    result = run(d("2026-10-14"), store, second)
    assert result.exit_code == 1, result.output
    assert _lines(result.output)[0] == "From 2026-10-12 to 2026-10-14"
    assert d("2026-10-13") in second.snapshot_days
    again = [line for line in _lines(result.output) if line.startswith("Failed:")]
    assert again == failed
    assert _state() == {"last_completed": "2026-10-14", "oldest_failed": None}

    # The day before the last completed sync (10-14) still covers 10-13 on 10-15.
    store.insert(0, _row("r3", d("2026-10-14")))
    third = FakeGarmin(dict(refused))
    result = run(d("2026-10-15"), store, third)
    assert result.exit_code == 1, result.output
    assert _lines(result.output)[0] == "From 2026-10-13 to 2026-10-15"
    assert [line for line in _lines(result.output) if line.startswith("Failed:")] == failed
    assert _state() == {"last_completed": "2026-10-15", "oldest_failed": None}

    store.insert(0, _row("r4", d("2026-10-15")))
    later = FakeGarmin(dict(refused))
    result = run(d("2026-10-16"), store, later)
    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-10-14 to 2026-10-16"
    assert d("2026-10-13") not in later.snapshot_days


def _string_constants(path: Path) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_output_string_or_changelog_line_says_a_failure_is_not_retried() -> None:
    """A permanent failure is fetched again by any run whose range covers its day; what is true is
    that later runs do not go back for it. Whitespace is flattened so a wrapped phrase is found."""
    old = re.compile(r"not\s+retried", re.IGNORECASE)
    sync_source = Path(sync.__file__)
    changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    assert changelog.is_file(), changelog
    strings = _string_constants(sync_source)
    assert any("later runs do not return for it" in s for s in strings)
    hits = [s for s in strings if old.search(s)]
    assert hits == [], hits
    flat = re.sub(r"\s+", " ", changelog.read_text(encoding="utf-8"))
    assert "Garmin Connect sync" in flat or "runcoach sync" in flat
    assert old.findall(flat) == [], [flat[m.start() - 80 : m.end() + 40] for m in old.finditer(flat)]


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


# --- the reference's timelines, end to end --------------------------------------------------------


def test_timeline_a_failed_day_is_retried_every_run_until_fetched(run) -> None:
    """A daily sync fails D's snapshot (temporary); D is fetched again every day until it is fetched."""
    failed_day = d("2026-10-10")
    broken = {failed_day: garmin.GarminError("Daily snapshot HTTP error: 503")}

    def store_rows(today: date) -> list[dict[str, Any]]:
        # A daily sync: the newest stored item is the run the previous day's sync stored.
        yesterday = today - timedelta(days=1)
        return [_row(f"r{yesterday.isoformat()}", yesterday)]

    seen: dict[date, tuple[str, bool, int]] = {}
    for offset, fixed in [(0, False), (1, False), (2, False), (3, False), (4, False), (5, True), (6, True)]:
        today = failed_day + timedelta(days=offset)
        fake = FakeGarmin({} if fixed else broken)
        result = run(today, store_rows(today), fake)
        seen[today] = (_lines(result.output)[0], failed_day in fake.snapshot_days, result.exit_code)

    assert seen[d("2026-10-10")] == ("From 2026-10-08 to 2026-10-10", True, 1)
    # On D+1 and D+2 the store rule still covers D.
    assert seen[d("2026-10-11")] == ("From 2026-10-09 to 2026-10-11", True, 1)
    assert seen[d("2026-10-12")] == ("From 2026-10-10 to 2026-10-12", True, 1)
    # From D+3 the store rule alone would start after D: the failed day starts the range.
    assert seen[d("2026-10-13")] == ("From 2026-10-10 to 2026-10-13", True, 1)
    assert seen[d("2026-10-14")] == ("From 2026-10-10 to 2026-10-14", True, 1)
    # Garmin answers again: D is fetched cleanly, which clears it.
    assert seen[d("2026-10-15")] == ("From 2026-10-10 to 2026-10-15", True, 0)
    assert seen[d("2026-10-16")] == ("From 2026-10-14 to 2026-10-16", False, 0)
    assert _state() == {"last_completed": "2026-10-16", "oldest_failed": None}


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


def test_timeline_a_since_past_the_gap_does_not_move_last_completed(run) -> None:
    """As above, but the athlete runs ``sync --since 10-11`` after logging in: it completes, but
    10-03 to 10-10 were never fetched, so the next sync still starts the day before 10-02."""
    _write_state("2026-10-02", None)
    store = [_row("hand", d("2026-10-12")), _row("r0", d("2026-10-01"))]

    since = run(TODAY, store, FakeGarmin(), "--since", "2026-10-11")
    assert since.exit_code == 0, since.output
    assert _state() == {"last_completed": "2026-10-02", "oldest_failed": None}

    fake = FakeGarmin()
    result = run(d("2026-10-14"), store, fake)
    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-10-01 to 2026-10-14"
    assert _state() == {"last_completed": "2026-10-14", "oldest_failed": None}


@pytest.mark.parametrize(
    ("since", "last_completed"),
    [
        ("2026-10-03", "2026-10-13"),  # the day after the last completed sync: joins on
        ("2026-10-02", "2026-10-13"),
        ("2026-09-20", "2026-10-13"),
        ("2026-10-04", "2026-10-02"),  # one day past it: a gap of one day
    ],
)
def test_a_since_run_moves_last_completed_only_when_it_joins_on(run, since, last_completed) -> None:
    _write_state("2026-10-02", None)

    result = run(TODAY, RUN_10_12, FakeGarmin(), "--since", since)

    assert result.exit_code == 0, result.output
    assert _state() == {"last_completed": last_completed, "oldest_failed": None}
