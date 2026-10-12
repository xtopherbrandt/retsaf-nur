"""Tests for ``runcoach profile history add|list|remove`` (F021 AC6, against a mocked API).

The API is an ``httpx.MockTransport`` serving bodies shaped as the served ``/me/hr-history`` routes:
``POST`` answers 201 with a new ``HrHistoryEntry`` or 200 when it replaced the entry for that field
and date (keeping its ``entry_id``), ``GET`` the list, ``DELETE`` 204 or 404, and a refused body 422
with FastAPI's ``detail`` list.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")


def _entry(entry_id: int = 3, field: str = "max_hr_bpm", value: int = 195, date: str = "2016-01-01") -> dict:
    return {
        "entry_id": entry_id,
        "field": field,
        "value": value,
        "effective_from": date,
        "set_at": "2026-10-11T08:00:00+00:00",
    }


class _Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


@pytest.fixture
def serve(install_mock_client) -> Callable[[Callable[[httpx.Request], httpx.Response]], _Recorder]:
    def _serve(respond: Callable[[httpx.Request], httpx.Response]) -> _Recorder:
        recorder = _Recorder(respond)
        install_mock_client(recorder)
        return recorder

    return _serve


def test_history_add_posts_the_entry_and_prints_it(serve) -> None:
    api = serve(lambda request: httpx.Response(201, json=_entry()))

    result = runner.invoke(app, ["profile", "history", "add", "max_hr_bpm", "195", "--from", "2016-01-01"])

    assert result.exit_code == 0, result.output
    [request] = api.requests
    assert request.method == "POST"
    assert request.url == "http://example.test/me/hr-history"
    assert json.loads(request.content) == {"field": "max_hr_bpm", "value": 195, "effective_from": "2016-01-01"}
    assert "Added entry 3: max_hr_bpm 195 from 2016-01-01" in result.output


def test_history_add_says_replaced_on_a_200(serve) -> None:
    serve(lambda request: httpx.Response(200, json=_entry(value=191)))

    result = runner.invoke(app, ["profile", "history", "add", "max_hr_bpm", "191", "--from", "2016-01-01"])

    assert result.exit_code == 0, result.output
    assert "Replaced entry 3: max_hr_bpm 191 from 2016-01-01" in result.output


def test_history_add_prints_a_422_detail_and_exits_1(serve) -> None:
    detail = [
        {
            "type": "value_error",
            "loc": ["body", "effective_from"],
            "msg": "Value error, effective_from 2099-01-01 is after today (2026-10-11) in the athlete's timezone",
            "input": "2099-01-01",
        }
    ]
    serve(lambda request: httpx.Response(422, json={"detail": detail}))

    result = runner.invoke(app, ["profile", "history", "add", "max_hr_bpm", "195", "--from", "2099-01-01"])

    assert result.exit_code == 1, result.output
    assert "API returned 422" in result.output
    assert "body.effective_from: Value error, effective_from 2099-01-01 is after today" in result.output
    assert "Added" not in result.output


def test_history_add_refuses_a_field_that_has_no_dated_entries(serve) -> None:
    api = serve(lambda request: httpx.Response(201, json=_entry()))

    result = runner.invoke(app, ["profile", "history", "add", "sex", "1", "--from", "2016-01-01"])

    assert result.exit_code == 2, result.output
    assert api.requests == []


def test_history_add_needs_from(serve) -> None:
    api = serve(lambda request: httpx.Response(201, json=_entry()))

    result = runner.invoke(app, ["profile", "history", "add", "max_hr_bpm", "195"])

    assert result.exit_code == 2, result.output
    assert api.requests == []


def test_history_list_prints_each_entry_in_the_served_order(serve) -> None:
    entries = [
        _entry(1, "max_hr_bpm", 195, "2016-01-01"),
        _entry(2, "max_hr_bpm", 191, "2021-01-01"),
        _entry(4, "resting_hr_bpm", 48, "2019-05-02"),
    ]
    api = serve(lambda request: httpx.Response(200, json=entries))

    result = runner.invoke(app, ["profile", "history", "list"])

    assert result.exit_code == 0, result.output
    [request] = api.requests
    assert (request.method, request.url.path) == ("GET", "/me/hr-history")
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[0].split() == ["1", "max_hr_bpm", "195", "from", "2016-01-01", "set", "2026-10-11T08:00:00+00:00"]
    assert [line.split()[0] for line in lines] == ["1", "2", "4"]
    assert lines[2].split()[1:3] == ["resting_hr_bpm", "48"]


def test_history_list_says_so_when_empty(serve) -> None:
    serve(lambda request: httpx.Response(200, json=[]))

    result = runner.invoke(app, ["profile", "history", "list"])

    assert result.exit_code == 0, result.output
    assert "No dated HR entries." in result.output


def test_history_list_reports_a_body_that_is_not_a_list(serve) -> None:
    serve(lambda request: httpx.Response(200, json={"items": []}))

    result = runner.invoke(app, ["profile", "history", "list"])

    assert result.exit_code == 1, result.output
    assert "could not be understood" in result.output


def test_history_remove_deletes_the_entry(serve) -> None:
    api = serve(lambda request: httpx.Response(204))

    result = runner.invoke(app, ["profile", "history", "remove", "3"])

    assert result.exit_code == 0, result.output
    [request] = api.requests
    assert (request.method, request.url.path) == ("DELETE", "/me/hr-history/3")
    assert "Removed entry 3." in result.output


def test_history_remove_prints_a_404_detail_and_exits_1(serve) -> None:
    serve(lambda request: httpx.Response(404, json={"detail": "hr history entry 9 not found"}))

    result = runner.invoke(app, ["profile", "history", "remove", "9"])

    assert result.exit_code == 1, result.output
    assert "API returned 404: hr history entry 9 not found" in result.output
    assert "Removed" not in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "history", "add", "max_hr_bpm", "195", "--from", "2016-01-01"],
        ["profile", "history", "list"],
        ["profile", "history", "remove", "3"],
    ],
    ids=["add", "list", "remove"],
)
def test_history_commands_exit_1_when_the_api_is_unreachable(serve, argv: list[str]) -> None:
    def refuse(request: httpx.Request) -> Any:
        raise httpx.ConnectError("refused")

    serve(refuse)

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    assert "could not reach API at http://example.test" in result.output


@pytest.mark.parametrize(
    ("argv", "answer", "status"),
    [
        (["profile", "history", "remove", "3"], httpx.Response(200, json=_entry()), 200),
        (["profile", "history", "list"], httpx.Response(204), 204),
        (["profile", "history", "add", "max_hr_bpm", "195", "--from", "2016-01-01"], httpx.Response(204), 204),
    ],
    ids=["remove-200", "list-204", "add-204"],
)
def test_history_commands_refuse_a_2xx_they_do_not_expect(
    serve, argv: list[str], answer: httpx.Response, status: int
) -> None:
    serve(lambda request: answer)

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    assert f"API returned {status}" in result.output
