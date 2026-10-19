"""``runcoach sync laps``: attach laps and workout steps to stored runs from their Connect originals (F025).

Every test drives the command through ``CliRunner`` against ``conftest``'s ``FakeGarmin`` (handed to
the command through ``sync._adapter``) and ``FakeApi`` (``/openapi.json``, ``POST /laps/candidates``
and ``PUT /sessions/{id}/laps``; every other request fails the test). No test imports the Garmin
library; the socket block below keeps any stray connection off the network all the same.

Adversarial table (what was fed, what came out):

| input | result |
|---|---|
| an empty listing | no candidates call, every count 0, exit 0 |
| a summary with no ``startTimeGMT`` | failed (permanent), never sent, exit 1 |
| a summary that cannot be read at all | failed, day unknown, never sent |
| the same activity listed twice | sent once |
| a manual activity | ``no FIT original``, never sent, never downloaded |
| an activity of another sport | never sent |
| a candidates reply missing an item | stop, nothing downloaded, exit 1 |
| a candidates reply with an unknown outcome | stop, nothing downloaded, exit 1 |
| a ``matched`` answer with no session id | stop |
| a ``matched`` answer with a numeric ``laps_read_at`` | stop, not counted as already read |
| Garmin 429 on the second download | stop naming that day, the first item counted, exit 1 |
| a download answering 404 | ``no FIT original`` |
| a 400 or 413 to the attach | failed, permanent (``file_refused``) |
| a 404 or 503 to the attach | failed, temporary |
| a two-FIT-entry zip; one FIT entry beside a text file | failed ``zip_entries``; attached |
| every F022 zip safety case | failed with the shared entry reader's detail |
| a web page for a download | stop with F022's web page message |
"""

from __future__ import annotations

import io
import socket
import zipfile
from datetime import date, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli import garmin
from runcoach_cli.commands import sessions, sync, sync_laps
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 12)
SINCE = "2026-10-01"
ZONE = timezone(timedelta(hours=-7))
LOGIN_LINE = "Garmin login needed: run `runcoach sync login`"
OPERATIONS = ("attachSessionLaps", "matchLapCandidates")
READ_AT = "2026-10-05T08:00:00Z"


def _summary(
    activity_id: int,
    local: str = "2026-10-10 07:00:00",
    *,
    type_key: str = "running",
    type_id: int = 1,
    manual: bool = False,
    gmt: Any = ...,
) -> dict[str, Any]:
    """A listed summary as ``get_activities_by_date`` returns it; the computer's zone is UTC-7, so
    ``startTimeGMT`` is seven hours on (``gmt=None`` drops it, any other value replaces it)."""
    hour = int(local[11:13]) + 7
    default_gmt = f"{local[:10]} {hour:02d}{local[13:]}" if hour < 24 else None
    summary: dict[str, Any] = {
        "activityId": activity_id,
        "activityName": f"run {activity_id}",
        "startTimeLocal": local,
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": 17},
        "manualActivity": manual,
    }
    value = default_gmt if gmt is ... else gmt
    if value is not None:
        summary["startTimeGMT"] = value
    return summary


def _zip(entries: list[tuple[str, bytes]], compress_type: int = zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress_type) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # set after construction, so a trailing "/" is kept
            info.compress_type = compress_type
            archive.writestr(info, data)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"a test opened a network connection: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client, prewritten_config, fake_api, openapi_with):
    """``env(garmin_or_error, **fake_api_options)`` installs both fakes and returns the API; the
    API's ``doc`` defaults to one listing both laps operations. ``opened`` counts client opens."""
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)
    opened: list[int] = []

    def setup(fake: Any, **options: Any):
        options.setdefault("doc", openapi_with(*OPERATIONS))
        api = fake_api(**options)
        install_mock_client(api)

        def open_client() -> Any:
            opened.append(1)
            if isinstance(fake, Exception):
                raise fake
            return fake

        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=open_client))
        return api

    setup.opened = opened  # type: ignore[attr-defined]
    setup.state_file = prewritten_config.parent / "sync-state.json"  # type: ignore[attr-defined]
    return setup


def _laps(*args: str):
    return runner.invoke(app, ["sync", "laps", *args])


def _lines(output: str) -> list[str]:
    return output.rstrip().splitlines()


def _counts(**overrides: int) -> str:
    keys = (
        "attached",
        "already read",
        "not stored",
        "ambiguous",
        "identity mismatch",
        "no FIT original",
        "failed",
    )
    counts = dict.fromkeys(keys, 0)
    counts.update({key.replace("_", " "): value for key, value in overrides.items()})
    return "Laps: " + ", ".join(f"{key} {value}" for key, value in counts.items())


def _matched(session_id: str, laps_read_at: str | None = None) -> tuple[str, str, str | None]:
    return ("matched", session_id, laps_read_at)


# --- the main flow ------------------------------------------------------------------------------


def test_a_matched_unread_run_is_downloaded_once_and_attached(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(101)])
    api = env(fake, lap_candidates={101: _matched("s-101")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert _lines(result.output) == ["From 2026-10-01 to 2026-10-12", _counts(attached=1)]
    assert fake.listed == [(date(2026, 10, 1), TODAY)]
    assert fake.downloads == [("original", 101)]
    assert api.candidate_batches == [[{"connect_activity_id": 101, "start_time": "2026-10-10T14:00:00Z"}]]
    assert [(session_id, content) for session_id, content, _ in api.lap_puts] == [("s-101", b"ACT-101")]
    head = api.lap_puts[0][2]
    assert b"101_ACTIVITY" not in head and b'filename="garmin-' in head
    assert api.calls == ["GET /openapi.json", "POST /laps/candidates", "PUT /sessions/s-101/laps"]
    assert not env.state_file.exists()


def test_an_already_read_run_is_skipped_without_a_download(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(102)], allow_originals=False)
    api = env(fake, lap_candidates={102: _matched("s-102", READ_AT)})

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[1:] == [_counts(already_read=1)]
    assert fake.downloads == []
    assert api.lap_puts == []


def _refused(session_id: str, content: bytes) -> httpx.Response:
    if session_id == "s-105":
        return httpx.Response(
            409,
            json={
                "detail": "the file is session other-id (2026-10-10T15:00:00Z), not s-105 (2026-10-10T14:00:00Z)"
            },
        )
    return httpx.Response(200, json={"session_id": session_id, "laps": 1, "workout_steps": 0})


def test_outcomes_are_counted_and_reported_as_the_reference_prints(env, fake_garmin) -> None:
    fake = fake_garmin(
        [
            _summary(101, "2026-10-03 07:00:00"),
            _summary(102, "2026-10-04 07:00:00"),
            _summary(103, "2026-10-05 07:00:00"),
            _summary(104, "2026-10-06 07:00:00"),
            _summary(105, "2026-10-07 07:00:00"),
            _summary(106, "2026-10-08 07:00:00", manual=True),
            _summary(107, "2026-10-09 07:00:00"),
        ],
        originals={107: garmin.GarminError("Garmin answered 500", status=500)},
    )
    api = env(
        fake,
        lap_candidates={
            101: _matched("s-101"),
            102: _matched("s-102", READ_AT),
            104: ("ambiguous", None, None),
            105: _matched("s-105"),
            107: _matched("s-107"),
        },
        laps_reply=_refused,
    )

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert _lines(result.output) == [
        "From 2026-10-01 to 2026-10-12",
        (
            "Laps: attached 1, already read 1, not stored 1, ambiguous 1, identity mismatch 1, "
            "no FIT original 1, failed 1"
        ),
        (
            "Identity mismatch: 2026-10-07, activity 105 (run 105), API returned 409: the file is session "
            "other-id (2026-10-10T15:00:00Z), not s-105 (2026-10-10T14:00:00Z)"
        ),
        (
            "Failed: 2026-10-09, activity 107 (run 107), Garmin: Garmin answered 500, "
            "run runcoach sync laps --since 2026-10-09"
        ),
    ]
    assert [item["connect_activity_id"] for item in api.candidates_sent] == [101, 102, 103, 104, 105, 107]
    assert fake.downloads == [("original", 101), ("original", 105), ("original", 107)]
    assert [session_id for session_id, _, _ in api.lap_puts] == ["s-101", "s-105"]


def test_a_409_is_an_identity_mismatch_and_exits_1(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(105)])
    api = env(fake, lap_candidates={105: _matched("s-105")}, laps_reply=_refused)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[1] == _counts(identity_mismatch=1)
    assert lines[2].startswith("Identity mismatch: 2026-10-10, activity 105 (run 105), API returned 409: ")
    assert "Already stored" not in result.output  # F022's reading of a 409
    assert len(lines) == 3
    assert len(api.lap_puts) == 1


@pytest.mark.parametrize(
    ("status", "retry"),
    [(400, False), (413, False), (404, True), (500, True), (503, True)],
)
def test_an_attach_reply_other_than_200_or_409_is_failed(env, fake_garmin, status, retry) -> None:
    fake = fake_garmin([_summary(101)])
    env(
        fake,
        lap_candidates={101: _matched("s-101")},
        laps_reply=lambda session_id, content: httpx.Response(status, json={"detail": "nope"}),
    )

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[1] == _counts(failed=1)
    tail = "run runcoach sync laps --since 2026-10-10" if retry else "it would fail the same way again"
    detail = f"API returned {status}: nope"
    if not retry:
        detail = f"the API refused the file: {detail}"
    assert lines[2] == f"Failed: 2026-10-10, activity 101 (run 101), {detail}, {tail}"


def test_a_two_entry_zip_is_failed_zip_entries(env, fake_garmin) -> None:
    two = _zip([("101_ACTIVITY.fit", b"ACT-a"), ("101_ACTIVITY_2.FIT", b"ACT-b"), ("notes.txt", b"x")])
    fake = fake_garmin([_summary(101)], originals={101: two})
    api = env(fake, lap_candidates={101: _matched("s-101")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert _lines(result.output)[1:] == [
        _counts(failed=1),
        (
            "Failed: 2026-10-10, activity 101 (run 101), zip_entries: the download holds 2 FIT entries, "
            "not one, it would fail the same way again"
        ),
    ]
    assert api.lap_puts == []


def test_one_fit_entry_beside_other_files_is_attached(env, fake_garmin) -> None:
    data = _zip([("readme.txt", b"hello"), ("empty/", b""), ("101_ACTIVITY.fit", b"ACT-only")])
    fake = fake_garmin([_summary(101)], originals={101: data})
    api = env(fake, lap_candidates={101: _matched("s-101")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert [content for _, content, _ in api.lap_puts] == [b"ACT-only"]


# --- the zip safety cases of F022 ---------------------------------------------------------------


def _mixed(method: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.fit", b"ACT-x", compress_type=method)
    return buffer.getvalue()


def _encrypted() -> bytes:
    raw = bytearray(_zip([("a.fit", b"ACT-enc")]))
    raw[raw.find(b"PK\x03\x04") + 6] |= 0x01
    raw[raw.find(b"PK\x01\x02") + 8] |= 0x01
    return bytes(raw)


def _corrupt() -> bytes:
    # 50 bytes, under the lowered cap, so the whole entry is read and its CRC checked.
    return _zip([("a.fit", b"ACT-a" * 10)]).replace(b"ACT-aACT-a", b"XXXXXXXXXX", 1)


# name -> (the download, the expected failure code, or None when the one entry is attached).
HOSTILE = {
    "not-a-zip": (b"\x00\x01garbage", "not_a_zip"),
    "truncated-zip": (b"PK\x03\x04truncated", "not_a_zip"),
    "empty": (_zip([]), "zip_empty"),
    "too-many-entries": (_zip([(f"{i:03d}.txt", b"x") for i in range(65)]), "zip_too_many_entries"),
    "over-budget": (
        _zip([("a.fit", b"A" * 60), ("b.txt", b"B" * 60), ("c.txt", b"C" * 60)]),
        "zip_over_budget",
    ),
    "over-cap": (_zip([("a.fit", b"B" * 65)]), "zip_entry_over_cap"),
    "bzip2": (_mixed(zipfile.ZIP_BZIP2), "zip_compression"),
    "lzma": (_mixed(zipfile.ZIP_LZMA), "zip_compression"),
    "encrypted": (_encrypted(), "zip_encrypted"),
    "corrupt": (_corrupt(), "zip_entry_unreadable"),
    "folder-with-data": (_zip([("a.fit/", b"ACT-hidden")]), "zip_entries"),
    "dot-dot-slash": (_zip([("../x.fit", b"ACT-dotdot")]), None),
    "drive-name": (_zip([("C:\\x.fit", b"ACT-drive")]), None),
}


@pytest.mark.parametrize("case", HOSTILE.values(), ids=HOSTILE.keys())
def test_hostile_zips_fail_through_the_shared_entry_reader(
    env, fake_garmin, monkeypatch: pytest.MonkeyPatch, tmp_path, case
) -> None:
    data, code = case
    monkeypatch.setattr(sync, "FIT_ENTRY_CAP", 64)
    monkeypatch.setattr(sync, "ZIP_BYTE_BUDGET", 150)
    # The command reads zips only through the shared reader: it never names ``zipfile`` itself.
    assert not hasattr(sync_laps, "zipfile")
    real_reader = sync.read_zip_entries
    read_calls: list[bytes] = []

    def spying_reader(raw: bytes, *, activity: bool):
        read_calls.append(raw)
        assert activity is True
        return real_reader(raw, activity=activity)

    monkeypatch.setattr(sync, "read_zip_entries", spying_reader)
    fake = fake_garmin([_summary(101)], originals={101: data})
    api = env(fake, lap_candidates={101: _matched("s-101")})
    before = sorted(p.name for p in tmp_path.rglob("*"))

    result = _laps("--since", SINCE)

    assert read_calls == [data]
    assert sorted(p.name for p in tmp_path.rglob("*")) == before  # no entry is ever written
    lines = _lines(result.output)
    if code is None:
        assert result.exit_code == 0, result.output
        assert lines[1] == _counts(attached=1)
        assert len(api.lap_puts) == 1
        return
    assert result.exit_code == 1, result.output
    assert lines[1] == _counts(failed=1)
    assert api.lap_puts == []
    failure = lines[2]
    assert failure.startswith(f"Failed: 2026-10-10, activity 101 (run 101), {code}: ")
    if code != "zip_entries":
        expected = next(r for r in real_reader(data, activity=True) if isinstance(r, sync.ZipFailure))
        assert expected.code == code
        assert f"{code}: {sessions._printable(expected.detail)}, " in failure
        tail = (
            "run runcoach sync laps --since 2026-10-10"
            if expected.retry
            else "it would fail the same way again"
        )
        assert failure.endswith(tail)


def test_a_web_page_for_a_download_stops(env, fake_garmin) -> None:
    page = b"<!doctype html><html>login</html>"
    fake = fake_garmin([_summary(101), _summary(102, "2026-10-11 07:00:00")], originals={101: page})
    api = env(fake, lap_candidates={101: _matched("s-101"), 102: _matched("s-102")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert sync.WEB_PAGE in result.output
    assert fake.downloads == [("original", 101)]
    assert api.lap_puts == []


# --- failed downloads ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "detail", "retry"),
    [
        (garmin.GarminError("Garmin answered 500", status=500), "Garmin: Garmin answered 500", True),
        (garmin.GarminError("Garmin answered 410", status=410), "Garmin: Garmin answered 410", False),
        (garmin.GarminError("no status"), "Garmin: no status", True),
        ("not bytes", "the download is not bytes but str", True),
    ],
    ids=["500", "410-refused", "no-status", "not-bytes"],
)
def test_a_failed_download_is_counted_failed(env, fake_garmin, answer, detail, retry) -> None:
    fake = fake_garmin([_summary(101), _summary(102, "2026-10-11 07:00:00")], originals={101: answer})
    api = env(fake, lap_candidates={101: _matched("s-101"), 102: _matched("s-102")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    tail = "run runcoach sync laps --since 2026-10-10" if retry else "it would fail the same way again"
    assert _lines(result.output)[1:] == [
        _counts(attached=1, failed=1),
        f"Failed: 2026-10-10, activity 101 (run 101), {detail}, {tail}",
    ]
    assert [session_id for session_id, _, _ in api.lap_puts] == ["s-102"]


def test_a_download_answering_404_is_no_fit_original(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(101)], originals={101: garmin.NotFound("gone")})
    api = env(fake, lap_candidates={101: _matched("s-101")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[1:] == [_counts(no_FIT_original=1)]
    assert api.lap_puts == []


def test_a_rate_limit_mid_run_stops_naming_that_day(env, fake_garmin) -> None:
    fake = fake_garmin(
        [_summary(101), _summary(102, "2026-10-11 07:00:00"), _summary(103, "2026-10-12 07:00:00")],
        originals={102: garmin.RateLimited("429 Too Many Requests")},
    )
    api = env(fake, lap_candidates={101: _matched("s-101"), 102: _matched("s-102"), 103: _matched("s-103")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert _counts(attached=1) in result.output
    assert (
        "Garmin is limiting requests (429 Too Many Requests): wait, then run "
        "`runcoach sync laps --since 2026-10-11`"
    ) in result.output
    assert fake.downloads == [("original", 101), ("original", 102)]
    assert [session_id for session_id, _, _ in api.lap_puts] == ["s-101"]


# --- the API's capability check -----------------------------------------------------------------


@pytest.mark.parametrize("listed", [(), ("attachSessionLaps",), ("matchLapCandidates",), ("setRaceTags",)])
def test_an_api_without_the_laps_operations_prints_too_old(env, fake_garmin, openapi_with, listed) -> None:
    fake = fake_garmin([_summary(101)], allow_originals=False)
    api = env(fake, doc=openapi_with(*listed))

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert "the API does not support sync laps: update it and restart the API" in result.output
    assert api.calls == ["GET /openapi.json"]
    assert fake.events == [] and env.opened == []


def test_a_503_laps_openapi_document_names_the_status(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(101)], allow_originals=False)
    api = env(fake, doc_status=503)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert (
        "the API answered 503 at /openapi.json: run `runcoach sync laps --since 2026-10-01`" in result.output
    )
    assert "too old" not in result.output and "does not support" not in result.output
    assert api.calls == ["GET /openapi.json"]
    assert fake.events == []


def test_an_unreachable_api_stops_before_garmin(env, fake_garmin, install_mock_client) -> None:
    fake = fake_garmin([_summary(101)], allow_originals=False)
    env(fake)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_mock_client(refused)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert (
        "could not reach API at http://example.test: start it with `runcoach-api`, then run "
        "`runcoach sync laps --since 2026-10-01`"
    ) in result.output
    assert fake.events == []


# --- Garmin login and the listing ---------------------------------------------------------------


@pytest.mark.parametrize("where", ["open", "list", "download"])
def test_a_401_prints_the_login_message(env, fake_garmin, where) -> None:
    error = garmin.LoginNeeded("401 Unauthorized")
    if where == "open":
        fake: Any = error
    elif where == "list":
        fake = fake_garmin([_summary(101)], list_error=error)
    else:
        fake = fake_garmin([_summary(101)], originals={101: error})
    api = env(fake, lap_candidates={101: _matched("s-101")})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert f"Error: {LOGIN_LINE}" in result.output
    assert "401 Unauthorized" not in result.output
    assert api.lap_puts == []


def test_an_empty_listing_sends_nothing(env, fake_garmin) -> None:
    fake = fake_garmin([], allow_originals=False)
    api = env(fake)

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert _lines(result.output) == ["From 2026-10-01 to 2026-10-12", _counts()]
    assert api.calls == ["GET /openapi.json"]


def test_unreadable_summaries_are_failed_and_never_sent(env, fake_garmin) -> None:
    fake = fake_garmin(
        [
            _summary(101, gmt=None),
            _summary(102, gmt="2026-10-10"),
            {"activityId": 103},
            _summary(104),
            _summary(104),  # listed twice
            _summary(105, type_key="cycling", type_id=2),
            _summary(106, "2026-09-30 07:00:00"),  # before the range
        ],
        allow_originals=False,
    )
    api = env(fake)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[1] == _counts(not_stored=1, failed=3)
    assert lines[2].startswith("Failed: day unknown, activity 103, unreadable summary: ")
    assert lines[3] == (
        "Failed: 2026-10-10, activity 101 (run 101), startTimeGMT None is missing or not a date and time, "
        "it would fail the same way again"
    )
    assert lines[4].startswith("Failed: 2026-10-10, activity 102 (run 102), startTimeGMT '2026-10-10' ")
    assert [item["connect_activity_id"] for item in api.candidates_sent] == [104]


# --- the candidates reply -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        lambda items: httpx.Response(200, json={"items": []}),
        lambda items: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "connect_activity_id": i["connect_activity_id"],
                        "outcome": "tagged",
                        "session_id": None,
                        "laps_read_at": None,
                    }
                    for i in items
                ]
            },
        ),
        lambda items: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "connect_activity_id": i["connect_activity_id"],
                        "outcome": "matched",
                        "session_id": None,
                        "laps_read_at": None,
                    }
                    for i in items
                ]
            },
        ),
        lambda items: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "connect_activity_id": 999,
                        "outcome": "not_stored",
                        "session_id": None,
                        "laps_read_at": None,
                    }
                    for i in items
                ]
            },
        ),
        lambda items: httpx.Response(200, content=b"not json"),
    ],
    ids=["missing-item", "unknown-outcome", "matched-without-session", "other-activity", "not-json"],
)
def test_a_candidates_reply_that_does_not_answer_every_item_stops(env, fake_garmin, reply) -> None:
    fake = fake_garmin([_summary(101)], allow_originals=False)
    api = env(fake, candidates_reply=reply)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert "the API's answer to the laps candidates could not be understood" in result.output
    assert "run `runcoach sync laps --since 2026-10-10`" in result.output
    assert api.lap_puts == []


def test_a_non_string_laps_read_at_is_reported_not_trusted(env, fake_garmin) -> None:
    """A ``matched`` answer whose ``laps_read_at`` is a number is a reply the command cannot
    understand, reported as the table above reports its rows: never counted as already read."""
    fake = fake_garmin([_summary(101)], allow_originals=False)
    api = env(fake, lap_candidates={101: _matched("s-101", 123)})

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert "the API's answer to the laps candidates could not be understood" in result.output
    assert '"laps_read_at":123' in result.output
    assert "run `runcoach sync laps --since 2026-10-10`" in result.output
    assert "already read 1" not in result.output
    assert fake.downloads == []
    assert api.lap_puts == []


def test_a_candidates_reply_other_than_200_stops(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(101)], allow_originals=False)
    env(fake, candidates_reply=lambda items: httpx.Response(503, json={"detail": "the store is busy"}))

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert "API returned 503: the store is busy: run `runcoach sync laps --since 2026-10-10`" in result.output


def test_candidates_are_sent_in_batches_oldest_first(
    env, fake_garmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert sync_laps.CANDIDATES_BATCH == 500  # the operation takes 1 to 500 items, as setRaceTags does
    monkeypatch.setattr(sync_laps, "CANDIDATES_BATCH", 2)
    days = [6, 2, 5, 3, 4]
    fake = fake_garmin([_summary(100 + d, f"2026-10-0{d} 07:00:00") for d in days], allow_originals=False)
    api = env(fake)

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert [[item["connect_activity_id"] for item in batch] for batch in api.candidate_batches] == [
        [102, 103],
        [104, 105],
        [106],
    ]
    assert _lines(result.output)[1:] == [_counts(not_stored=5)]


def _connect_error() -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _read_timeout() -> httpx.Response:
    raise httpx.ReadTimeout("timed out")


# Each stop inside the candidates step, as the second batch answers it, and the words it prints.
LATER_BATCH_STOPS = {
    "503": (
        lambda: httpx.Response(503, json={"detail": "the store is busy"}),
        "API returned 503: the store is busy",
    ),
    "unreachable": (_connect_error, "could not reach API at http://example.test"),
    "timeout": (_read_timeout, "did not answer within"),
    "wrong-item-count": (
        lambda: httpx.Response(200, json={"items": []}),
        "the API's answer to the laps candidates could not be understood",
    ),
}


@pytest.mark.parametrize("stop", sorted(LATER_BATCH_STOPS))
def test_a_stop_on_a_later_candidates_batch_names_the_first_day_sent(
    env, fake_garmin, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    """Every batch is sent before any download, so a stop on the second batch has downloaded
    nothing from the first: whichever way that batch stops, the retry starts at the first run
    sent, not at its own batch."""
    monkeypatch.setattr(sync_laps, "CANDIDATES_BATCH", 2)
    fake = fake_garmin([_summary(100 + d, f"2026-10-0{d} 07:00:00") for d in (2, 3, 4, 5, 6)])
    answer, words = LATER_BATCH_STOPS[stop]

    def reply(items: list[dict[str, Any]]) -> httpx.Response:
        if items[0]["connect_activity_id"] != 102:
            return answer()
        answers = [
            {
                "connect_activity_id": item["connect_activity_id"],
                "outcome": "matched",
                "session_id": f"s-{item['connect_activity_id']}",
                "laps_read_at": None,
            }
            for item in items
        ]
        return httpx.Response(200, json={"items": answers})

    api = env(fake, candidates_reply=reply)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert len(api.candidate_batches) == 2
    assert words in result.output
    assert "run `runcoach sync laps --since 2026-10-02`" in result.output
    assert "--since 2026-10-04" not in result.output
    assert api.lap_puts == []
    assert fake.downloads == []


def test_an_api_unreachable_on_attach_stops(env, fake_garmin) -> None:
    fake = fake_garmin([_summary(101)])
    env(fake, lap_candidates={101: _matched("s-101")}, unreachable_on_laps=1)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert (
        "could not reach API at http://example.test: start it with `runcoach-api`, then run "
        "`runcoach sync laps --since 2026-10-10`"
    ) in result.output


# --- the options --------------------------------------------------------------------------------


def test_since_is_required(env, fake_garmin) -> None:
    fake = fake_garmin([], allow_originals=False)
    api = env(fake)

    result = _laps()

    assert result.exit_code == 2, result.output
    assert "Missing option '--since'" in result.output
    assert api.requests == [] and env.opened == []


def test_the_parent_since_guard_covers_laps(env, fake_garmin) -> None:
    fake = fake_garmin([], allow_originals=False)
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", SINCE, "laps", "--since", SINCE])

    assert result.exit_code == 2, result.output
    assert "the laps command takes its own date" in result.output
    assert api.requests == [] and env.opened == []


def test_until_bounds_the_listing_and_defaults_to_today(env, fake_garmin) -> None:
    fake = fake_garmin(
        [
            _summary(101, "2026-10-04 07:00:00"),
            _summary(103, "2026-10-05 07:00:00"),  # on the --until day itself
            _summary(102, "2026-10-06 07:00:00"),
        ]
    )
    api = env(fake)

    result = _laps("--since", SINCE, "--until", "2026-10-05")

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-10-01 to 2026-10-05"
    assert fake.listed == [(date(2026, 10, 1), date(2026, 10, 5))]
    assert [item["connect_activity_id"] for item in api.candidates_sent] == [101, 103]


def test_an_activity_on_the_since_day_is_sent(env, fake_garmin) -> None:
    fake = fake_garmin(
        [
            _summary(106, "2026-09-30 07:00:00"),  # the day before --since
            _summary(107, "2026-10-01 07:00:00"),  # on the --since day itself
        ],
        allow_originals=False,
    )
    api = env(fake)

    result = _laps("--since", SINCE)

    assert result.exit_code == 0, result.output
    assert [item["connect_activity_id"] for item in api.candidates_sent] == [107]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--since", "2026-10-13"), "after today"),
        (("--since", "2026-W41-1"), "not a date in YYYY-MM-DD form"),
        (("--since", SINCE, "--until", "2026-10-13"), "after today"),
        (("--since", "2026-10-05", "--until", "2026-10-04"), "before --since"),
    ],
)
def test_bad_dates_are_usage_errors(env, fake_garmin, args, message) -> None:
    fake = fake_garmin([], allow_originals=False)
    api = env(fake)

    result = _laps(*args)

    assert result.exit_code == 2, result.output
    assert message in result.output
    assert api.requests == []


def test_without_the_garmin_library_it_prints_the_install_command(env, fake_garmin, monkeypatch) -> None:
    api = env(fake_garmin([]))
    monkeypatch.setattr(sync, "_library_missing", lambda: True)

    result = _laps("--since", SINCE)

    assert result.exit_code == 1, result.output
    assert sync.INSTALL_COMMAND in result.output
    assert api.requests == []
