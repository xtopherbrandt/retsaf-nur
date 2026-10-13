"""``runcoach sync``: the date range (F022 reference section 2).

Each row of the reference's range table is a test. ``GET /sessions`` is served by
``install_mock_client``; Garmin is a fake client that lists nothing and has no snapshot for any
day, so no test reaches Garmin or uploads anything. ``sync.today`` fixes today and
``sessions.LOCAL_ZONE`` fixes the computer zone (Vancouver summer time, UTC-7).
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli import garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 13)
ZONE = timezone(timedelta(hours=-7))


class EmptyGarmin:
    """Lists no activity and has no snapshot on any day; records what it was asked."""

    def __init__(self) -> None:
        self.listed: list[tuple[date, date]] = []
        self.snapshot_days: list[date] = []
        self.originals: list[int] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        self.listed.append((start, end))
        return []

    def download_original(self, activity_id: int) -> bytes:
        self.originals.append(activity_id)
        raise AssertionError("nothing is listed, so nothing is downloaded")

    def download_snapshot(self, day: date) -> bytes:
        self.snapshot_days.append(day)
        raise garmin.NotFound(f"no snapshot on {day}")


def _row(
    session_id: str, start_time: str, sport: str = "running", tag: str | None = None, name: str | None = None
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "start_time": start_time,
        "sport": sport,
        "activity_tag": tag,
        "name": name,
        "distance_m": None,
        "duration_s": None,
        "avg_pace_s_per_km": None,
    }


class Store:
    """Serves ``GET /sessions`` from ``pages`` (cursor chain) and fails any upload."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/sessions":
            since = request.url.params.get("since")
            index = 0 if since is None else int(since.removeprefix("cursor-"))
            next_cursor = f"cursor-{index + 1}" if index + 1 < len(self.pages) else None
            return httpx.Response(200, json={"items": self.pages[index], "next_cursor": next_cursor})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> EmptyGarmin:
    client = EmptyGarmin()
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: client))
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)
    return client


def _run(install_mock_client, store: Store, *args: str):
    install_mock_client(store)
    return runner.invoke(app, ["sync", *args])


def _first_line(output: str) -> str:
    return output.splitlines()[0]


def test_newest_run_2026_10_11_covers_10_10_to_10_13(install_mock_client, fake: EmptyGarmin) -> None:
    # 07:38 local on 10-11 is 14:38 UTC. A newer cycling session is passed over.
    store = Store(
        [[_row("c1", "2026-10-12T16:00:00+00:00", sport="other"), _row("r1", "2026-10-11T14:38:00+00:00")]]
    )

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-10 to 2026-10-13"
    assert fake.listed == [(date(2026, 10, 10), TODAY)]
    assert fake.snapshot_days == [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12), TODAY]
    assert store.requests[0].url.params["limit"] == "50"


def test_newest_run_date_is_taken_in_the_computer_zone(install_mock_client, fake: EmptyGarmin) -> None:
    # 02:30 UTC on 10-12 is 19:30 on 10-11 in UTC-7, so newest is 10-11, not 10-12.
    store = Store([[_row("r1", "2026-10-12T02:30:00+00:00")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-10 to 2026-10-13"


def test_newest_snapshot_today_covers_yesterday_and_today(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[_row("s1", "2026-10-13T15:00:00+00:00", sport="other", tag="health_snapshot")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-12 to 2026-10-13"
    assert "after today" not in " ".join(result.output.split())
    assert fake.listed == [(date(2026, 10, 12), TODAY)]


def test_newest_strap_check_counts_as_a_capture(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[_row("h1", "2026-10-08T14:00:00+00:00", sport="other", tag="resting_hrv_check")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-07 to 2026-10-13"


def test_empty_store_covers_30_days(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-14 to 2026-10-13"
    assert fake.listed == [(date(2026, 9, 14), TODAY)]
    assert len(fake.snapshot_days) == 30


def test_only_cycling_pages_every_session_and_covers_30_days(install_mock_client, fake: EmptyGarmin) -> None:
    pages = [
        [
            _row(f"c{page}-{i}", f"2026-10-{12 - page:02d}T{10 + i:02d}:00:00+00:00", sport="other")
            for i in range(3)
        ]
        for page in range(3)
    ]
    store = Store(pages)

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-14 to 2026-10-13"
    gets = [r for r in store.requests if r.method == "GET"]
    assert [r.url.params.get("since") for r in gets] == [None, "cursor-1", "cursor-2"]
    assert all(r.url.params["limit"] == "50" for r in gets)


def test_future_dated_newest_is_capped_with_a_warning(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[_row("bad-clock-1", "2119-01-01T12:00:00+00:00", name="Morning Run")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "From 2026-10-12 to 2026-10-13"
    warning = " ".join(result.output.split())
    assert "bad-clock-1" in warning
    assert "2119-01-01" in warning
    assert "after today" in warning
    assert fake.listed == [(date(2026, 10, 12), TODAY)]


def test_since_replaces_the_start(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[_row("r1", "2026-10-11T14:38:00+00:00")]])

    result = _run(install_mock_client, store, "--since", "2026-09-01")

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-01 to 2026-10-13"
    assert fake.listed == [(date(2026, 9, 1), TODAY)]


def test_since_today_covers_today_only(install_mock_client, fake: EmptyGarmin) -> None:
    store = Store([[]])

    result = _run(install_mock_client, store, "--since", "2026-10-13")

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-13 to 2026-10-13"
    assert fake.snapshot_days == [TODAY]


@pytest.mark.parametrize("since", ["2026-10-14", "2027-01-01"])
def test_since_after_today_is_a_usage_error(install_mock_client, fake: EmptyGarmin, since: str) -> None:
    store = Store([[]])

    result = _run(install_mock_client, store, "--since", since)

    assert result.exit_code == 2, result.output
    assert "after today" in " ".join(result.output.split())
    assert fake.listed == []
    assert store.requests == []


@pytest.mark.parametrize("since", ["2026-13-01", "yesterday", "2026/10/01"])
def test_since_not_a_date_is_a_usage_error(install_mock_client, fake: EmptyGarmin, since: str) -> None:
    store = Store([[]])

    result = _run(install_mock_client, store, "--since", since)

    assert result.exit_code == 2, result.output
    assert fake.listed == []
    assert store.requests == []


def test_api_unreachable_while_finding_the_range_exits_1(install_mock_client, fake: EmptyGarmin) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    install_mock_client(handler)
    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "could not reach API" in result.output
    assert fake.listed == []


def test_api_error_while_finding_the_range_exits_1(install_mock_client, fake: EmptyGarmin) -> None:
    install_mock_client(lambda request: httpx.Response(500, text="boom"))

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "500" in result.output
    assert fake.listed == []
