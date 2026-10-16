"""``runcoach state`` against the served app, not a hand-written body (F026 AC5, the CLI half).

``runcoach state`` runs through Typer's ``CliRunner`` on the harness in
``tests/support/against_app.py``: ``runcoach_cli.api_client.client`` is a ``TestClient(app)``, so
``GET /state`` is answered by the real route on the isolated store (the autouse
``isolated_data_dir``). ``state`` never imports the Garmin library and reads no token; the autouse
``_no_network`` below refuses every socket connection off the loopback and every ``curl_cffi`` entry
point anyway. The clock is frozen through ``main._utcnow``.

The file, by its row in ``fixtures/README.md``'s provenance table:

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| sample_run | real run | | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | yes |

``sample_run.fit`` is a structured workout: uploaded with ``POST /sessions`` its laps and workout
steps are stored, laps 1-7 name active steps 1 and 2 (one block) and their ``timer_s`` sum to
2100 s, written by hand from the file in ``test_threshold_workout_qualifying.py``. Tagged
``threshold_workout`` through ``PUT /race-tags``, it is the only effort used.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api import main as main_module

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ACTIVE_S = 2100.0


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


against_app = _load_support("against_app")

# The harness; cli_against_app is a fixture pytest finds here.
cli_against_app = against_app.cli_against_app
_run = against_app._run


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Refuse every socket connection to an address off the loopback (the event loop's own
    socketpair stays allowed) and every ``curl_cffi`` entry point the Garmin library uses."""
    seen: list[Any] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    loopback = {"127.0.0.1", "::1", "localhost"}

    def _host(address: Any) -> Any:
        return address[0] if isinstance(address, tuple) else address

    def connect(self: socket.socket, address: Any) -> Any:
        if _host(address) not in loopback:
            seen.append(address)
            raise OSError(f"network blocked in tests: {address!r}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if _host(address) not in loopback:
            seen.append(address)
            raise OSError(f"network blocked in tests: {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    try:
        from curl_cffi import requests as cffi_requests
        from curl_cffi.requests.session import AsyncSession, Session
    except ImportError:
        return seen

    def refuse(*args: object, **kwargs: object) -> None:
        seen.append(kwargs.get("url") or args)
        raise OSError("curl_cffi blocked in tests")

    for name in ("request", "get", "post", "put", "patch", "delete", "head", "options"):
        if hasattr(cffi_requests, name):
            monkeypatch.setattr(cffi_requests, name, refuse)
    monkeypatch.setattr(Session, "request", refuse)
    monkeypatch.setattr(AsyncSession, "request", refuse)
    return seen


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


def _mss(seconds_per_km: float) -> str:
    """``m:ss`` rounded half up, written here rather than taken from the CLI."""
    whole = int(Decimal(repr(seconds_per_km)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"{whole // 60}:{whole % 60:02d}"


def _stored(session_id: str) -> tuple[str, dict[str, Any]]:
    conn = sqlite3.connect(Path(db._load_config_cached().data_dir) / db.DB_FILENAME)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["start_time"], json.loads(row["summary"])


def test_state_against_the_app_prints_a_workout(cli_against_app: TestClient, _no_network: list[Any]) -> None:
    client = cli_against_app
    uploaded = client.post(
        "/sessions", files={"file": ("sample_run.fit", (FIXTURES / "sample_run.fit").read_bytes())}
    )
    assert uploaded.status_code == 201, uploaded.text
    workout = uploaded.json()["session_id"]
    start_time, summary = _stored(workout)
    start = datetime.fromisoformat(start_time).astimezone(UTC)
    tagged = client.put(
        "/race-tags",
        json={
            "items": [
                {
                    "connect_activity_id": 24557261242,
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "distance_m": summary["distance_m"],
                    "duration_s": summary["duration_s"],
                    "tag": "threshold_workout",
                }
            ]
        },
    )
    assert tagged.status_code == 200, tagged.text
    assert [(i["outcome"], i["session_id"]) for i in tagged.json()["items"]] == [("tagged", workout)]

    body = client.get("/state").json()
    [det] = [d for d in body["determinants"] if d["name"] == "functional_threshold_pace"]
    [effort] = det["efforts"]
    print(f"  served timezone {body['timezone']}; effort {effort}; excluded {det['excluded']}")
    assert (effort["session_id"], effort["kind"], effort["active_blocks"]) == (
        workout,
        "threshold_workout",
        1,
    )
    assert effort["duration_s"] == pytest.approx(ACTIVE_S, abs=1e-9)
    assert effort["gain"] == 1.0
    assert effort["gps_degraded_fraction"] == 0.0
    day = start.astimezone(ZoneInfo(body["timezone"])).strftime("%Y-%m-%d")

    result = _run("state")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith(f"Threshold pace {_mss(det['estimate']['value'])}/km (80 %: ")
    assert lines[0].endswith(f", from 1 workout, the last on {day}")
    assert lines[1:] == [
        (
            f"Workout used {day}: 0:35:00 active in 1 block, GAP {_mss(effort['gap_pace_s_per_km'])}/km, "
            "gain 1.00, degraded GPS 0.0 %, weight 100 %"
        )
    ]
    assert _no_network == []
