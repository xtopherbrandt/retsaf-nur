"""Tests for ``runcoach loads recompute`` (F021 AC5's CLI sentences, against a mocked API).

The API is an ``httpx.MockTransport``: ``GET /sessions`` serves ``SessionPage`` bodies (``items`` of
``SessionSummary`` rows and an opaque ``next_cursor``), and ``POST /sessions/{id}/load/recompute``
serves the ``SessionLoad`` body plus a top-level boolean ``changed``. A session counts as
``out_of_period`` when any of its three HR input blocks carries ``out_of_period: true``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli import api_client
from runcoach_cli.commands import loads
from runcoach_cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")

HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")


def _summary(session_id: str, sport: str = "running") -> dict[str, Any]:
    return {
        "session_id": session_id,
        "start_time": "2026-10-01T07:00:00+00:00",
        "sport": sport,
        "activity_tag": None,
        "name": None,
        "distance_m": 5000.0 if sport == "running" else None,
        "duration_s": 1500.0 if sport == "running" else None,
        "avg_pace_s_per_km": 300.0 if sport == "running" else None,
        "quality_flags": [],
        "workout_id": None,
    }


def _hr_input(out_of_period: bool = False) -> dict[str, Any]:
    return {
        "value": 188,
        "source": "anchor",
        "session_id": None,
        "anchor_version": 1,
        "anchor_unavailable": None,
        "entry_id": None,
        "effective_from": None,
        "out_of_period": out_of_period,
    }


def _load(session_id: str, changed: bool = False, out_of_period: str | None = None) -> dict[str, Any]:
    """A ``SessionLoad`` body plus ``changed``; ``out_of_period`` names the HR block marked so."""
    inputs: dict[str, Any] = {
        "hr_time_s": 1500.0,
        "recorded_time_s": 1500.0,
        "timer_time_s": 1500.0,
        "hr_time_fraction": 1.0,
        "avg_hr_bpm": 150.0,
        "hr_source": "chest_strap",
        "sex": {
            "value": "male",
            "source": "anchor",
            "session_id": None,
            "anchor_version": 1,
            "anchor_unavailable": None,
            "entry_id": None,
            "effective_from": None,
            "out_of_period": False,
        },
    }
    for name in HR_FIELDS:
        inputs[name] = _hr_input(name == out_of_period)
    return {
        "session_id": session_id,
        "sport": "running",
        "session_load": {"value": 50.0, "unavailable": None, "driver": "hr_trimp"},
        "flags": [],
        "input_flags": [],
        "metrics": {
            "hr_trimp": {
                "value": 41.5,
                "unavailable": None,
                "unavailable_fields": [],
                "threshold_hour_reference": 83.0,
                "coefficients": "male",
            },
            "rtss": {"value": None, "unavailable": "no_threshold_pace"},
            "srpe": {"value": None, "unavailable": "no_rpe"},
        },
        "inputs": inputs,
        "changed": changed,
    }


class _Api:
    """Serves ``pages`` (a list of item lists, chained by cursors ``c1``, ``c2``...) and answers each
    recompute with ``recompute(session_id)``."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        recompute: Callable[[str], httpx.Response],
    ) -> None:
        self.pages = pages
        self.recompute = recompute
        self.list_requests: list[httpx.Request] = []
        self.recomputed: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/sessions":
            self.list_requests.append(request)
            since = request.url.params.get("since")
            index = 0 if since is None else int(since[1:])
            cursor = f"c{index + 1}" if index + 1 < len(self.pages) else None
            return httpx.Response(200, json={"items": self.pages[index], "next_cursor": cursor})
        prefix, suffix = "/sessions/", "/load/recompute"
        if request.method == "POST" and path.startswith(prefix) and path.endswith(suffix):
            session_id = path[len(prefix) : -len(suffix)]
            self.recomputed.append(session_id)
            return self.recompute(session_id)
        return httpx.Response(599, text=f"unexpected {request.method} {path}")


@pytest.fixture
def serve(install_mock_client) -> Callable[..., _Api]:
    def _serve(pages: list[list[dict[str, Any]]], recompute: Callable[[str], httpx.Response]) -> _Api:
        api = _Api(pages, recompute)
        install_mock_client(api)
        return api

    return _serve


def _ok(session_id: str) -> httpx.Response:
    return httpx.Response(200, json=_load(session_id))


def test_recompute_walks_every_page_and_recomputes_running_sessions(serve) -> None:
    pages = [
        [_summary("r1"), _summary("w1", sport="other"), _summary("r2")],
        [_summary("r3"), _summary("w2", sport="other")],
        [_summary("r4")],
    ]

    def answer(session_id: str) -> httpx.Response:
        return httpx.Response(
            200,
            json=_load(
                session_id,
                changed=session_id in {"r2", "r4"},
                out_of_period="max_hr_bpm" if session_id == "r3" else None,
            ),
        )

    api = serve(pages, answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    assert [r.url.params.get("since") for r in api.list_requests] == [None, "c1", "c2"]
    assert {r.url.params.get("limit") for r in api.list_requests} == {"200"}
    assert api.recomputed == ["r1", "r2", "r3", "r4"]
    out = result.output
    assert "Recomputed: 4" in out
    assert "Changed: 2" in out
    assert "Out of period: 1" in out
    assert "Failed: 0" in out
    assert "Skipped (not running): 2" in out


def test_recompute_counts_a_session_once_however_many_blocks_are_out_of_period(serve) -> None:
    def answer(session_id: str) -> httpx.Response:
        body = _load(session_id)
        for name in HR_FIELDS:
            body["inputs"][name]["out_of_period"] = session_id == "r1"
        return httpx.Response(200, json=body)

    serve([[_summary("r1"), _summary("r2")]], answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    assert "Out of period: 1" in result.output


def test_recompute_does_not_count_the_sex_block_as_out_of_period(serve) -> None:
    def answer(session_id: str) -> httpx.Response:
        body = _load(session_id)
        body["inputs"]["sex"]["out_of_period"] = True
        return httpx.Response(200, json=body)

    serve([[_summary("r1")]], answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    assert "Out of period: 0" in result.output


def test_recompute_prints_a_progress_line_every_100(serve) -> None:
    pages = [[_summary(f"a{i:03d}") for i in range(200)], [_summary(f"b{i:03d}") for i in range(50)]]
    serve(pages, _ok)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    progress = [line for line in result.output.splitlines() if line.startswith("Progress:")]
    assert progress == ["Progress: 100 of 250", "Progress: 200 of 250"]
    assert "Recomputed: 250" in result.output


def test_recompute_continues_past_a_failure_and_exits_1(serve) -> None:
    def answer(session_id: str) -> httpx.Response:
        if session_id == "r2":
            return httpx.Response(500, text="boom")
        if session_id == "r3":
            return httpx.Response(200, json={"session_id": "r3"})
        return _ok(session_id)

    api = serve([[_summary("r1"), _summary("r2"), _summary("r3"), _summary("r4")]], answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 1, result.output
    assert api.recomputed == ["r1", "r2", "r3", "r4"]
    out = result.output
    assert "Recomputed: 2" in out
    assert "Failed: 2" in out
    lines = out.splitlines()
    failed_at = lines.index("Failed: 2")
    assert lines[failed_at + 1] == "  r2: API returned 500: boom"
    assert lines[failed_at + 2].startswith("  r3: the API's response could not be understood")


def test_recompute_names_a_404_as_a_failed_session(serve) -> None:
    def answer(session_id: str) -> httpx.Response:
        return httpx.Response(404, json={"detail": f"session {session_id} not found"})

    serve([[_summary("gone")]], answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 1, result.output
    assert "  gone: API returned 404: session gone not found" in result.output.splitlines()


def test_recompute_exits_1_at_once_when_the_list_is_unreachable(install_mock_client) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    install_mock_client(refuse)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 1, result.output
    assert "could not reach API at http://example.test" in result.output
    assert "Recomputed:" not in result.output


def test_recompute_exits_1_at_once_when_a_recompute_is_unreachable(serve) -> None:
    def answer(session_id: str) -> httpx.Response:
        if session_id == "r2":
            raise httpx.ConnectError("refused")
        return _ok(session_id)

    api = serve([[_summary("r1"), _summary("r2"), _summary("r3")]], answer)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 1, result.output
    assert api.recomputed == ["r1", "r2"]
    assert "could not reach API at http://example.test" in result.output


def test_recompute_exits_1_when_a_page_is_refused(serve, install_mock_client) -> None:
    install_mock_client(lambda request: httpx.Response(500, text="list broke"))

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 1, result.output
    assert "API returned 500: list broke" in result.output


def test_recompute_says_so_when_no_session_is_running(serve) -> None:
    api = serve([[_summary("w1", sport="other")]], _ok)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    assert api.recomputed == []
    assert "Recomputed: 0" in result.output


def test_recompute_uses_its_own_timeout(serve, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    real_post = httpx.Client.post

    def post(self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(kwargs.get("timeout"))
        return real_post(self, url, **kwargs)

    serve([[_summary("r1")]], _ok)
    monkeypatch.setattr(httpx.Client, "post", post)

    result = runner.invoke(app, ["loads", "recompute"])

    assert result.exit_code == 0, result.output
    assert seen == [api_client.RECOMPUTE_TIMEOUT]
    assert api_client.RECOMPUTE_TIMEOUT > 5.0
    assert loads.PROGRESS_STEP == 100
