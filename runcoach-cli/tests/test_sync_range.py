"""``runcoach sync``: the date range (F022 reference section 2).

Each row of the reference's range table is a test. ``GET /sessions`` is served by
``install_mock_client``, with ``/openapi.json`` and ``PUT /race-tags`` for the race tags step that
follows a run; Garmin is a fake client that lists nothing and has no snapshot for any day, so no
test reaches Garmin or uploads anything. A range starting after today - 29 days is followed by one
more listing, from today - 29 days, for that step. ``sync.today`` fixes today and
``sessions.LOCAL_ZONE`` fixes the computer zone (Vancouver summer time, UTC-7).
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 13)
# The race tags step covers today and the 29 days before it.
TAGS_START = TODAY - timedelta(days=29)
ZONE = timezone(timedelta(hours=-7))


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


@pytest.fixture
def sessions_store(fake_api):
    """``sessions_store(pages)``: an API serving ``GET /sessions`` from ``pages`` (a cursor chain),
    ``/openapi.json`` and ``PUT /race-tags``, that fails any upload."""
    return lambda pages: fake_api(pages=pages, accept_uploads=False)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, fake_garmin) -> Any:
    """A Garmin client that lists no activity and has no snapshot on any day."""
    client = fake_garmin(allow_originals=False)
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: client))
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)
    return client


def _run(install_mock_client, store: Any, *args: str):
    install_mock_client(store)
    return runner.invoke(app, ["sync", *args])


def _first_line(output: str) -> str:
    return output.splitlines()[0]


def test_newest_run_2026_10_11_covers_10_10_to_10_13(install_mock_client, fake: Any, sessions_store) -> None:
    # 07:38 local on 10-11 is 14:38 UTC. A newer cycling session is passed over.
    store = sessions_store(
        [[_row("c1", "2026-10-12T16:00:00+00:00", sport="other"), _row("r1", "2026-10-11T14:38:00+00:00")]]
    )

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-10 to 2026-10-13"
    assert fake.listed == [(date(2026, 10, 10), TODAY), (TAGS_START, TODAY)]
    assert fake.snapshot_days == [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12), TODAY]
    assert store.requests[0].url.params["limit"] == "50"


def test_a_store_whose_newest_row_is_a_night_starts_the_day_before(
    install_mock_client, fake: Any, sessions_store
) -> None:
    """A stored night counts for the range as a capture does. The mock API, as the API does, lists
    it only when the walk sends ``include_nights=true``; without the night the older run on 10-08
    would start the range on 10-07."""
    # 13:30 UTC on 10-12 is 06:30 on 10-12 in UTC-7.
    night = _row("n1", "2026-10-12T13:30:00+00:00", sport="other", tag="overnight_hrv", name="Overnight HRV")
    store = sessions_store([[night, _row("r1", "2026-10-08T14:00:00+00:00")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-11 to 2026-10-13"
    assert fake.listed == [(date(2026, 10, 11), TODAY), (TAGS_START, TODAY)]


def test_the_range_walk_sends_include_nights(install_mock_client, fake: Any, sessions_store) -> None:
    """Every ``GET /sessions`` page of the walk asks for the nights too."""
    pages = [[_row(f"c{page}", f"2026-10-{12 - page:02d}T10:00:00+00:00", sport="other")] for page in range(3)]
    store = sessions_store(pages)

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    gets = [r for r in store.requests if r.method == "GET" and r.url.path == "/sessions"]
    assert len(gets) == 3
    assert [r.url.params.get("include_nights") for r in gets] == ["true", "true", "true"]


def test_newest_run_date_is_taken_in_the_computer_zone(
    install_mock_client, fake: Any, sessions_store
) -> None:
    # 02:30 UTC on 10-12 is 19:30 on 10-11 in UTC-7, so newest is 10-11, not 10-12.
    store = sessions_store([[_row("r1", "2026-10-12T02:30:00+00:00")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-10 to 2026-10-13"


def test_newest_snapshot_today_covers_yesterday_and_today(
    install_mock_client, fake: Any, sessions_store
) -> None:
    store = sessions_store([[_row("s1", "2026-10-13T15:00:00+00:00", sport="other", tag="health_snapshot")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-12 to 2026-10-13"
    assert "after today" not in " ".join(result.output.split())
    assert fake.listed == [(date(2026, 10, 12), TODAY), (TAGS_START, TODAY)]


def test_newest_strap_check_counts_as_a_capture(install_mock_client, fake: Any, sessions_store) -> None:
    store = sessions_store(
        [[_row("h1", "2026-10-08T14:00:00+00:00", sport="other", tag="resting_hrv_check")]]
    )

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-07 to 2026-10-13"


def test_empty_store_covers_30_days(install_mock_client, fake: Any, sessions_store) -> None:
    store = sessions_store([[]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-14 to 2026-10-13"
    assert fake.listed == [(date(2026, 9, 14), TODAY)]
    assert len(fake.snapshot_days) == 30


def test_only_cycling_pages_every_session_and_covers_30_days(
    install_mock_client, fake: Any, sessions_store
) -> None:
    pages = [
        [
            _row(f"c{page}-{i}", f"2026-10-{12 - page:02d}T{10 + i:02d}:00:00+00:00", sport="other")
            for i in range(3)
        ]
        for page in range(3)
    ]
    store = sessions_store(pages)

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-14 to 2026-10-13"
    gets = [r for r in store.requests if r.method == "GET" and r.url.path == "/sessions"]
    assert [r.url.params.get("since") for r in gets] == [None, "cursor-1", "cursor-2"]
    assert all(r.url.params["limit"] == "50" for r in gets)


def test_future_dated_newest_is_capped_with_a_warning(install_mock_client, fake: Any, sessions_store) -> None:
    store = sessions_store([[_row("bad-clock-1", "2119-01-01T12:00:00+00:00", name="Morning Run")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "From 2026-10-12 to 2026-10-13"
    warning = " ".join(result.output.split())
    assert "bad-clock-1" in warning
    assert "2119-01-01" in warning
    assert "after today" in warning
    assert fake.listed == [(date(2026, 10, 12), TODAY), (TAGS_START, TODAY)]


def test_the_future_dated_warning_prints_the_name_with_controls_replaced(
    install_mock_client, fake: Any, sessions_store
) -> None:
    """The stored name and id are printed as ``runcoach sessions`` prints them: controls become ``?``."""
    store = sessions_store([[_row("bad\x07id", "2119-01-01T12:00:00+00:00", name="Run\x1b[2J\r\nFake line")]])

    result = _run(install_mock_client, store)

    assert result.exit_code == 0, result.output
    warning = [line for line in result.output.splitlines() if line.startswith("Warning")]
    assert warning and "Run?[2J??Fake line (bad?id)" in warning[0], result.output
    assert "\x1b" not in result.output and "\x07" not in result.output


def test_since_replaces_the_start(install_mock_client, fake: Any, sessions_store) -> None:
    store = sessions_store([[_row("r1", "2026-10-11T14:38:00+00:00")]])

    result = _run(install_mock_client, store, "--since", "2026-09-01")

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-09-01 to 2026-10-13"
    assert fake.listed == [(date(2026, 9, 1), TODAY)]


def test_since_today_covers_today_only(install_mock_client, fake: Any, sessions_store) -> None:
    store = sessions_store([[]])

    result = _run(install_mock_client, store, "--since", "2026-10-13")

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-13 to 2026-10-13"
    assert fake.snapshot_days == [TODAY]


@pytest.mark.parametrize("since", ["2026-10-14", "2027-01-01"])
def test_since_after_today_is_a_usage_error(
    install_mock_client, fake: Any, since: str, sessions_store
) -> None:
    store = sessions_store([[]])

    result = _run(install_mock_client, store, "--since", since)

    assert result.exit_code == 2, result.output
    assert "after today" in " ".join(result.output.split())
    assert fake.listed == []
    assert store.requests == []


@pytest.mark.parametrize(
    "since", ["2026-13-01", "yesterday", "2026/10/01", "2026-W41-1", "20261010", "2026-10-1 ", "\uff12026-10-10"]
)
def test_since_not_a_date_is_a_usage_error(
    install_mock_client, fake: Any, since: str, sessions_store
) -> None:
    store = sessions_store([[]])

    result = _run(install_mock_client, store, "--since", since)

    assert result.exit_code == 2, result.output
    assert fake.listed == []
    assert store.requests == []


def test_api_unreachable_while_finding_the_range_exits_1(install_mock_client, fake: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    install_mock_client(handler)
    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "could not reach API" in result.output
    assert fake.listed == []


def test_api_error_while_finding_the_range_exits_1(install_mock_client, fake: Any) -> None:
    install_mock_client(lambda request: httpx.Response(500, text="boom"))

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "500" in result.output
    assert fake.listed == []


def test_since_skips_the_walk_and_never_warns_about_a_future_dated_session(
    install_mock_client, fake: Any, sessions_store
) -> None:
    # The walk only finds the start that --since replaces: it is not made, so the newest stored
    # session (here dated 2119) can neither warn nor stop the sync.
    store = sessions_store([[_row("bad-clock-1", "2119-01-01T08:00:00+00:00", name="Run")]])

    result = _run(install_mock_client, store, "--since", "2026-10-12")

    assert result.exit_code == 0, result.output
    assert _first_line(result.output) == "From 2026-10-12 to 2026-10-13"
    assert "Warning" not in result.output
    assert "after today" not in result.output
    # The only request is the race tags step's ``/openapi.json`` read (nothing is listed to send).
    assert store.calls == ["GET /openapi.json"]


def test_an_api_error_body_while_finding_the_range_is_printed_safely_and_cut(
    install_mock_client, fake: Any
) -> None:
    body = "boom\x1b[2J\r\nforged line" + "x" * 5000
    install_mock_client(lambda request: httpx.Response(500, text=body))

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "\x1b" not in result.output and "\r" not in result.output
    assert "boom?[2J??forged line" in result.output
    assert "x" * 201 not in result.output
    assert fake.listed == []
