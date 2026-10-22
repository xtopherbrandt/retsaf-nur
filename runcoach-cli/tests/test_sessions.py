"""Tests for the ``runcoach sessions`` command (F019 AC7).

Only the CLI's own responsibilities are covered: which ``GET /sessions`` requests it sends (the
``limit`` and ``since`` query parameters, page by page), and how it renders the page shape the
feature's AC1 names (``items`` of ``session_id``, ``start_time``, ``sport``, ``activity_tag``, ``name``,
``distance_m``, ``duration_s``, ``avg_pace_s_per_km``, ``quality_flags``, ``workout_id``, inside the
contract's ``Page`` envelope with ``next_cursor``). The server is a mocked client throughout.

The local zone is injected through ``sessions.LOCAL_ZONE``: ``TZ`` does not change ``astimezone()``
on Windows, and the CLI package declares no tzdata, so most tests use a fixed
``timezone(timedelta(...))``. One row sets ``LOCAL_ZONE`` to ``ZoneInfo("America/New_York")`` (and
skips where no zone database is found) to show the seam takes a zone with daylight saving; another
leaves it ``None`` and compares against ``astimezone()`` for a January and a July instant.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone, tzinfo

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.commands import sessions
from runcoach_cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")

# UTC-7: 07:15 UTC is 00:15 the same day; 05:30 UTC is 22:30 the day before.
ZONE = timezone(timedelta(hours=-7))


@pytest.fixture(autouse=True)
def fixed_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)


def _session(n: int, **overrides) -> dict:
    row = {
        "session_id": f"sid-{n:04d}",
        "start_time": "2026-09-20T07:15:03+00:00",
        "sport": "running",
        "activity_tag": None,
        "name": f"Run {n}",
        "distance_m": 10000.0,
        "duration_s": 3000.0,
        "avg_pace_s_per_km": 300.0,
        "quality_flags": [],
        "workout_id": None,
    }
    row.update(overrides)
    return row


class _PagedServer:
    """Serve ``rows`` in AC3's page shape: ``since`` is the index cursor of the next row."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/sessions"
        start = int(request.url.params.get("since", "0"))
        limit = int(request.url.params["limit"])
        page = self.rows[start : start + limit]
        end = start + len(page)
        next_cursor = str(end) if end < len(self.rows) else None
        return httpx.Response(200, json={"items": page, "next_cursor": next_cursor})

    def limits(self) -> list[int]:
        return [int(r.url.params["limit"]) for r in self.requests]

    def sinces(self) -> list[str | None]:
        return [r.url.params.get("since") for r in self.requests]


class _Scripted:
    """Answer each request with the next scripted response (a body, text, or an exception)."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            item.request = request
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)


def _lines(result) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# the line format
# ---------------------------------------------------------------------------


def test_sessions_prints_one_line_per_session_with_local_date_and_dashes_for_nulls(
    install_mock_client,
) -> None:
    rows = [
        _session(
            1,
            name="Long Easy Run",
            start_time="2026-09-20T07:15:03+00:00",
            distance_m=12345.678,
            duration_s=3598.6,
            avg_pace_s_per_km=298.49,
        ),
        _session(
            2,
            name=None,
            sport="other",
            start_time="2026-09-19T05:30:00+00:00",
            distance_m=None,
            duration_s=None,
            avg_pace_s_per_km=None,
        ),
    ]
    install_mock_client(_PagedServer(rows))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert len(lines) == 2
    first, second = lines
    # Display name = name + local date and time; UTC-7 turns 07:15 into 00:15.
    assert first.startswith("Long Easy Run 2026-09-20 00:15")
    assert "running" in first
    assert "12.35" in first  # km to 2 decimals
    assert "0:59:59" in first  # 3598.6 s rounds half up to 3599 s, under an hour
    assert "4:58" in first  # 298.49 s/km
    assert first.rstrip().endswith("sid-0001")
    # A null name: sport + local date and time; 05:30 UTC is 22:30 the day before.
    assert second.startswith("other 2026-09-18 22:30")
    assert second.rstrip().endswith("sid-0002")
    assert second.split().count("-") == 3, second  # distance, duration, pace


def test_sessions_prints_a_non_ascii_name_as_stored_in_utf8(install_mock_client) -> None:
    name = "Café run — été, " + "x" * 120  # long and non-ASCII: printed whole
    install_mock_client(_PagedServer([_session(1, name=name)]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert name.encode("utf-8") in result.stdout_bytes
    assert name in result.stdout


@pytest.mark.parametrize(
    ("pace", "shown"),
    [
        (298.49, "4:58"),
        (298.5, "4:59"),  # half up: Python's half-even round(298.5) is 298 -> "4:58"
        (299.5, "5:00"),  # never "4:60"
        (299.4, "4:59"),
        (600.0, "10:00"),
    ],
)
def test_sessions_pace_rounds_total_seconds_half_up_first(install_mock_client, pace, shown) -> None:
    install_mock_client(_PagedServer([_session(1, avg_pace_s_per_km=pace)]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert f" {shown}/km " in _lines(result)[0], result.stdout


@pytest.mark.parametrize(
    ("duration", "shown"),
    [
        (3599.5, "1:00:00"),  # half up across the hour
        (3599.4, "0:59:59"),
        (3600.0, "1:00:00"),
        (7322.0, "2:02:02"),
        (59.5, "0:01:00"),
        (0.4, "0:00:00"),
        (36000.0, "10:00:00"),
    ],
)
def test_sessions_duration_is_h_mm_ss_rounded_half_up(install_mock_client, duration, shown) -> None:
    install_mock_client(_PagedServer([_session(1, duration_s=duration)]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert f" {shown} " in _lines(result)[0], result.stdout


def test_sessions_reads_a_z_suffixed_start_time(install_mock_client) -> None:
    install_mock_client(_PagedServer([_session(1, start_time="2026-09-20T07:15:03Z")]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert _lines(result)[0].startswith("Run 1 2026-09-20 00:15")


WINTER = "2026-01-15T17:00:00+00:00"
SUMMER = "2026-07-15T17:00:00+00:00"


def test_sessions_default_zone_applies_the_computers_rules_to_each_instant(
    install_mock_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # LOCAL_ZONE None is the computer's zone, applied per instant: a January and a July start each
    # print in the offset in force on their own date, not in today's offset.
    monkeypatch.setattr(sessions, "LOCAL_ZONE", None)
    install_mock_client(
        _PagedServer([_session(1, start_time=WINTER), _session(2, start_time=SUMMER)])
    )

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    winter, summer = _lines(result)
    for line, start in ((winter, WINTER), (summer, SUMMER)):
        expected = datetime.fromisoformat(start).astimezone().strftime("%Y-%m-%d %H:%M")
        stamp = " ".join(line.split()[2:4])
        print(start, "->", stamp, "expected", expected)
        assert stamp == expected, line


class _EasternLike(tzinfo):
    """UTC-5, and UTC-4 from April to October: a zone whose offset depends on the date.

    Hand-written so the row runs without a time zone database (the CLI environment has none).
    """

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=-5) + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=1) if dt is not None and 4 <= dt.month <= 10 else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "EDT" if self.dst(dt) else "EST"


def test_sessions_zone_seam_accepts_a_real_zone_with_daylight_saving(
    install_mock_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "LOCAL_ZONE", _EasternLike())
    install_mock_client(
        _PagedServer([_session(1, start_time=WINTER), _session(2, start_time=SUMMER)])
    )

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    winter, summer = _lines(result)
    assert winter.startswith("Run 1 2026-01-15 12:00"), winter  # EST, UTC-5
    assert summer.startswith("Run 2 2026-07-15 13:00"), summer  # EDT, UTC-4


def test_sessions_prints_control_characters_in_a_name_as_question_marks(
    install_mock_client,
) -> None:
    # ESC, BEL, CR, LF, TAB, DEL and the C1 CSI (U+009B): each prints as "?", one line in all.
    name = "a\x1bb\x07c\rd\ne\tf\x7fg\x9bh"
    install_mock_client(_PagedServer([_session(1, name=name)]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1, repr(result.stdout)
    assert result.stdout.startswith("a?b?c?d?e?f?g?h 2026-09-20 00:15"), repr(result.stdout)
    for control in ("\x1b", "\x07", "\r", "\t", "\x7f", "\x9b"):
        assert control not in result.stdout


def test_sessions_prints_ordinary_non_ascii_in_a_name_unchanged(install_mock_client) -> None:
    name = "Lauf über 10 km – Zürich"
    install_mock_client(_PagedServer([_session(1, name=name)]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert _lines(result)[0].startswith(f"{name} 2026-09-20 00:15")


# ---------------------------------------------------------------------------
# paging: --limit and --all
# ---------------------------------------------------------------------------


def test_sessions_default_is_one_request_of_50(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(80)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [50]
    assert len(_lines(result)) == 50


def test_sessions_limit_120_is_one_request_of_120(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(300)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--limit", "120"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [120]
    assert server.sinces() == [None]
    assert len(_lines(result)) == 120


def test_sessions_limit_450_fetches_200_200_50_following_the_cursor(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(600)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--limit", "450"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [200, 200, 50]
    assert server.sinces() == [None, "200", "400"]
    lines = _lines(result)
    assert len(lines) == 450
    assert lines[0].endswith("sid-0000") and lines[-1].endswith("sid-0449")


def test_sessions_limit_above_the_store_stops_at_a_null_cursor(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(7)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--limit", "450"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [200]
    assert len(_lines(result)) == 7


def test_all_follows_next_cursor_until_null(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(450)])
    install_mock_client(server)

    # --all wins over --limit: every page of 200 until next_cursor is null.
    result = runner.invoke(app, ["sessions", "--all", "--limit", "3"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [200, 200, 200]
    assert server.sinces() == [None, "200", "400"]
    lines = _lines(result)
    assert len(lines) == 450
    assert lines[-1].endswith("sid-0449")


def test_all_on_an_exact_multiple_of_200_stops_on_the_null_cursor(install_mock_client) -> None:
    server = _PagedServer([_session(n) for n in range(400)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 0, result.output
    assert server.limits() == [200, 200]
    assert len(_lines(result)) == 400


@pytest.mark.parametrize("value", ["0", "-1"])
def test_sessions_limit_zero_or_negative_is_a_usage_error(install_mock_client, value) -> None:
    server = _PagedServer([_session(1)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--limit", value])

    assert result.exit_code == 2, result.output
    assert "--limit" in result.stderr, result.stderr
    assert server.requests == []
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# failures: stderr and exit 1, with --all's earlier lines kept
# ---------------------------------------------------------------------------


def test_sessions_unreachable_api_prints_to_stderr_and_exits_1(install_mock_client) -> None:
    install_mock_client(_Scripted(httpx.ConnectError("connection refused")))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert "could not reach API at http://example.test" in result.stderr
    assert result.stdout == ""


def test_sessions_non2xx_prints_to_stderr_and_exits_1(install_mock_client) -> None:
    install_mock_client(_Scripted(httpx.Response(500, text="records table is missing")))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert "API returned 500" in result.stderr
    assert "records table is missing" in result.stderr
    assert result.stdout == ""


def test_sessions_unreadable_body_prints_to_stderr_and_exits_1(install_mock_client) -> None:
    install_mock_client(_Scripted(httpx.Response(200, text="not json")))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert "could not be understood" in result.stderr
    assert result.stdout == ""


def test_sessions_a_200_body_without_items_is_an_unparseable_body(install_mock_client) -> None:
    install_mock_client(_Scripted({"next_cursor": None}))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert "could not be understood" in result.stderr
    assert result.stdout == ""


def test_sessions_a_row_missing_a_field_is_an_unparseable_body(install_mock_client) -> None:
    row = _session(1)
    del row["start_time"]
    install_mock_client(_Scripted({"items": [row], "next_cursor": None}))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert "could not be understood" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("duration_s", "1e999"),  # JSON parses it to inf; flooring inf overflows
        ("start_time", '"0001-01-01T00:00:00+00:00"'),  # UTC-7 moves it before year 1
    ],
)
def test_sessions_an_overflowing_row_is_an_unparseable_body(
    install_mock_client, field, raw
) -> None:
    row = _session(1)
    row[field] = "PLACEHOLDER"
    text = json.dumps({"items": [row], "next_cursor": None}).replace('"PLACEHOLDER"', raw)
    install_mock_client(_Scripted(httpx.Response(200, content=text.encode("utf-8"))))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "could not be understood" in result.stderr
    assert "Traceback" not in result.output
    assert result.stdout == ""


@pytest.mark.parametrize(
    "start_time",
    [
        "0001-01-01T00:00:00+00:00",
        "1900-01-01T00:00:00+00:00",
        "1969-12-31T23:00:00+00:00",
        "3001-01-20T00:00:00+00:00",
    ],
)
def test_sessions_a_start_time_the_computers_zone_cannot_convert_never_tracebacks(
    install_mock_client, monkeypatch: pytest.MonkeyPatch, start_time: str
) -> None:
    # In the computer's zone (LOCAL_ZONE None), Windows' astimezone() raises OSError for an instant
    # before 1970 or after 3000; the row is then an unparseable body. Elsewhere it prints normally.
    monkeypatch.setattr(sessions, "LOCAL_ZONE", None)
    install_mock_client(_Scripted({"items": [_session(1, start_time=start_time)], "next_cursor": None}))

    result = runner.invoke(app, ["sessions"])

    print(start_time, "->", result.exit_code, repr(result.stdout), repr(result.stderr))
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    if result.exit_code == 1:
        assert "could not be understood" in result.stderr
        assert result.stdout == ""
    else:
        assert result.exit_code == 0, result.output
        assert len(_lines(result)) == 1


def test_all_a_repeated_next_cursor_is_an_unparseable_body_with_earlier_lines_kept(
    install_mock_client,
) -> None:
    page1 = {"items": [_session(n) for n in range(200)], "next_cursor": "abc"}
    page2 = {"items": [_session(n) for n in range(200, 400)], "next_cursor": "abc"}
    server = _Scripted(page1, page2, page2)
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 1
    assert "could not be understood" in result.stderr
    assert len(server.requests) == 2  # the loop stops at the repeat; no third request
    lines = _lines(result)
    assert len(lines) == 400  # both pages were printed before the repeat was seen
    assert lines[0].endswith("sid-0000") and lines[-1].endswith("sid-0399")


def test_all_keeps_the_first_page_when_the_second_fails(install_mock_client) -> None:
    page1 = {"items": [_session(n) for n in range(200)], "next_cursor": "c1"}
    install_mock_client(_Scripted(page1, httpx.Response(502, text="bad gateway")))

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 1
    assert "API returned 502" in result.stderr
    assert len(_lines(result)) == 200


def test_all_keeps_the_first_page_when_the_api_goes_away(install_mock_client) -> None:
    page1 = {"items": [_session(n) for n in range(200)], "next_cursor": "c1"}
    install_mock_client(_Scripted(page1, httpx.ReadError("gone")))

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 1
    assert "could not reach API" in result.stderr
    assert len(_lines(result)) == 200


# ---------------------------------------------------------------------------
# the empty store
# ---------------------------------------------------------------------------


def test_sessions_empty_store_prints_no_sessions_stored_and_exits_0(install_mock_client) -> None:
    install_mock_client(_PagedServer([]))

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No sessions stored."


def test_all_on_an_empty_store_prints_no_sessions_stored(install_mock_client) -> None:
    install_mock_client(_PagedServer([]))

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No sessions stored."


def test_sessions_requests_pass_the_cursor_through_untouched(install_mock_client) -> None:
    # The cursor is opaque to the CLI: whatever next_cursor says is what `since` carries back.
    cursor = "MjAyNi0wOS0yMFQwNzoxNTowMyswMDowMHxzaWQ="
    page1 = {"items": [_session(n) for n in range(200)], "next_cursor": cursor}
    page2 = {"items": [_session(200)], "next_cursor": None}
    server = _Scripted(page1, page2)
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--all"])

    assert result.exit_code == 0, result.output
    assert server.requests[1].url.params["since"] == cursor


# ---------------------------------------------------------------------------
# overnight HRV nights (--nights)
# ---------------------------------------------------------------------------


def _night(n: int) -> dict:
    return _session(
        n,
        start_time="2026-09-20T04:00:00+00:00",
        sport="other",
        activity_tag="overnight_hrv",
        name="Overnight HRV",
        distance_m=None,
        duration_s=25200.0,
        avg_pace_s_per_km=None,
    )


class _NightsServer(_PagedServer):
    """Serve ``rows`` as the API does: an ``overnight_hrv`` row only when ``include_nights=true``."""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__(rows)
        self.all_rows = rows

    def __call__(self, request: httpx.Request) -> httpx.Response:
        nights = request.url.params.get("include_nights") == "true"
        self.rows = [r for r in self.all_rows if nights or r["activity_tag"] != "overnight_hrv"]
        return super().__call__(request)


def test_sessions_without_nights_prints_no_night(install_mock_client) -> None:
    server = _NightsServer([_session(1), _night(2), _session(3)])
    install_mock_client(server)

    result = runner.invoke(app, ["sessions"])

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert [line.split("  ")[-1] for line in lines] == ["sid-0001", "sid-0003"]
    assert not any("Overnight HRV" in line for line in lines)
    assert [r.url.query for r in server.requests] == [b"limit=50"]


def test_sessions_nights_prints_overnight_hrv(install_mock_client) -> None:
    # 201 day rows and one night: --all --nights asks for the nights on every page it follows.
    rows = [_session(n) for n in range(100)] + [_night(100)] + [_session(n) for n in range(101, 202)]
    server = _NightsServer(rows)
    install_mock_client(server)

    result = runner.invoke(app, ["sessions", "--all", "--nights"])

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert len(lines) == 202
    # A night prints as any row does: its name and local date, the sport, dashes for the nulls.
    assert lines[100] == "Overnight HRV 2026-09-19 21:00  other  -  7:00:00  -  sid-0100"
    assert [line for line in lines if "Overnight HRV" in line] == [lines[100]]
    assert len(server.requests) == 2
    assert all(r.url.params["include_nights"] == "true" for r in server.requests)
