"""``runcoach state`` against the served app, not a hand-written body (F026 AC5 and F027 AC6, the
CLI halves).

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
| hilly_run_8k_fr945 | real run | Smart selected, watch wrote 1 Hz; positions stripped | 1 Hz, 82 s pause | FR945 LTE; HRM-Pro Plus | no | yes |

``sample_run.fit`` is a structured workout: uploaded with ``POST /sessions`` its laps and workout
steps are stored, laps 1-7 name work steps 1 and 2 (one block) and their ``timer_s`` sum to
2100 s, written by hand from the file in ``test_threshold_workout_qualifying.py``. Tagged
``threshold_workout`` through ``PUT /race-tags``, it is the only effort used. Both reps count
because their GAP paces sit at a 1.0992 ratio, inside the 1.10 band by under a percent; this
file pins the served shape, never the band.

Both files' ``workout`` message names the workout ``Easy Run`` (read from the files with
``fitdecode``), so declaring ``threshold_workout_names = ["Easy Run"]`` admits the tagged
``sample_run`` by tag and name and the untagged ``hilly_run_8k_fr945`` by name alone, and
``runcoach state`` shows each admission after the date.

Neither file is tagged a race, so the served ``riegel_exponent`` entry is the population default
with no race used and the endurance exponent block is two lines. ``test_cli_prints_b_from_the_real_app``
stores three synthetic flat races instead (as ``test_riegel_b_route.py`` stores them: ``db.persist``
with 1 Hz records, so F013's GAP pace is the pace run), the F029 reference section 4 set A, B, C
(5,000 m in 1,200 s, 10,000 m in 2,520 s, 21,100 m in 5,600 s), whose b the reference gives as
1.069870 with 80 % 1.068992 to 1.070748.
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
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.models import Context, Record, Session

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ACTIVE_S = 2100.0

# F029 reference section 6's lines with no race used (the population default).
RIEGEL_NO_RACES = [
    "Endurance exponent b 1.06 (population default, 80 %: 0.996 to 1.124), low: 0 races in the last 1095 days; "
    "3 are needed",
    "Mark a race as Race in Garmin Connect and run runcoach sync tags --since <its date>",
]


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


against_app = _load_support("against_app")
session_loads = _load_support("session_loads")

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


def _upload(client: TestClient, name: str) -> tuple[str, datetime, dict[str, Any]]:
    """Upload a fixture; its session id, UTC start and stored summary."""
    uploaded = client.post("/sessions", files={"file": (name, (FIXTURES / name).read_bytes())})
    assert uploaded.status_code == 201, uploaded.text
    session_id = uploaded.json()["session_id"]
    start_time, summary = _stored(session_id)
    return session_id, datetime.fromisoformat(start_time).astimezone(UTC), summary


def _tag_workout(client: TestClient, session_id: str, start: datetime, summary: dict[str, Any]) -> None:
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
    assert [(i["outcome"], i["session_id"]) for i in tagged.json()["items"]] == [("tagged", session_id)]


def _ftp(client: TestClient) -> tuple[dict[str, Any], str]:
    body = client.get("/state").json()
    [det] = [d for d in body["determinants"] if d["name"] == "functional_threshold_pace"]
    print(f"  served timezone {body['timezone']}; efforts {det['efforts']}; excluded {det['excluded']}")
    return det, body["timezone"]


def test_state_against_the_app_prints_a_workout(cli_against_app: TestClient, _no_network: list[Any]) -> None:
    """With no workout name listed (what ``runcoach-api init`` writes), the tagged ``sample_run`` is
    admitted by its tag alone and reads ``(tag)`` after the date."""
    client = cli_against_app
    workout, start, summary = _upload(client, "sample_run.fit")
    _tag_workout(client, workout, start, summary)

    det, zone = _ftp(client)
    [effort] = det["efforts"]
    assert (effort["session_id"], effort["kind"], effort["active_blocks"]) == (
        workout,
        "threshold_workout",
        1,
    )
    assert (effort["admitted_by"], effort["workout_name"]) == (["tag"], "Easy Run")
    assert effort["duration_s"] == pytest.approx(ACTIVE_S, abs=1e-9)
    assert effort["gain"] == 1.0
    assert effort["gps_degraded_fraction"] == 0.0
    day = start.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")

    result = _run("state")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith(f"Threshold pace {_mss(det['estimate']['value'])}/km (80 %: ")
    assert lines[0].endswith(f", from 1 workout, the last on {day}")
    assert lines[1:] == [
        (
            f"Workout used {day} (tag): 0:35:00 active in 1 block, GAP {_mss(effort['gap_pace_s_per_km'])}/km, "
            "gain 1.00, degraded GPS 0.0 %, weight 100 %"
        ),
        *RIEGEL_NO_RACES,
    ], result.stdout
    assert _no_network == []


def test_runcoach_state_against_the_app_shows_tag_and_name_workouts(
    cli_against_app: TestClient, declared_config, _no_network: list[Any]
) -> None:
    """With ``Easy Run`` listed, the tagged ``sample_run`` reads ``(tag, name Easy Run)`` and the
    untagged ``hilly_run_8k_fr945`` (2026-09-29) is admitted by name alone and reads ``(name Easy
    Run)``: the route counts its one work rep of 2400 s, so both are used, the later one entering
    with the served gain and taking its weight (the first keeps the rest)."""
    client = cli_against_app
    declared_config(threshold_workout_names=["Easy Run"])
    workout, start, summary = _upload(client, "sample_run.fit")
    _tag_workout(client, workout, start, summary)
    named, named_start, _summary = _upload(client, "hilly_run_8k_fr945.fit")

    det, zone = _ftp(client)
    assert det["excluded"] == []
    [tagged, by_name] = det["efforts"]
    assert (tagged["session_id"], tagged["admitted_by"], tagged["workout_name"]) == (workout, ["tag", "name"], "Easy Run")
    assert (by_name["session_id"], by_name["kind"], by_name["admitted_by"], by_name["workout_name"]) == (
        named,
        "threshold_workout",
        ["name"],
        "Easy Run",
    )
    assert (by_name["duration_s"], by_name["active_blocks"], by_name["gps_degraded_fraction"]) == (2400.0, 1, 0.0)
    gain = by_name["gain"]
    assert 0.0 < gain < 1.0
    weight = int(Decimal(repr(gain * 100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    day = start.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")
    named_day = named_start.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")

    result = _run("state")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith(f"Threshold pace {_mss(det['estimate']['value'])}/km (80 %: ")
    assert lines[0].endswith(f", from 2 workouts, the last on {named_day}")
    assert lines[1:] == [
        (
            f"Workout used {day} (tag, name Easy Run): 0:35:00 active in 1 block, "
            f"GAP {_mss(tagged['gap_pace_s_per_km'])}/km, gain 1.00, degraded GPS 0.0 %, weight {100 - weight} %"
        ),
        (
            f"Workout used {named_day} (name Easy Run): 0:40:00 active in 1 block, "
            f"GAP {_mss(by_name['gap_pace_s_per_km'])}/km, gain {gain:.2f}, degraded GPS 0.0 %, weight {weight} %"
        ),
        *RIEGEL_NO_RACES,
    ], result.stdout
    assert _no_network == []


def _store_race(start: datetime, distance_m: float, duration_s: int) -> str:
    """An untagged running session of ``distance_m`` in ``duration_s`` with 1 Hz flat records and a
    saved load of 50."""
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        timer_time_s=float(duration_s),
        quality_flags=[],
        summary={"duration_s": float(duration_s), "distance_m": distance_m},
        context=Context(),
    )
    speed = distance_m / duration_s
    records = [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
        for t in range(duration_s + 1)
    ]
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, records, [], {})
        session_loads.plant_load(conn, session.session_id, value=50.0)
    finally:
        conn.close()
    return session.session_id


def _whole(value: float) -> str:
    return str(int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def test_cli_prints_b_from_the_real_app(cli_against_app: TestClient, _no_network: list[Any]) -> None:
    """Three races tagged through ``PUT /race-tags`` at 5, 10 and 21.1 km: the individual b prints
    to three decimals with its interval, then one line per race. The chart starts on the 5K's day,
    so the 5K has no fitness figure and the others no figure 29 days back; the fitness printed is
    the served day-before CTL, rounded half up here."""
    client = cli_against_app
    races = [
        (datetime(2026, 9, 6, 7, 0, tzinfo=UTC), 5000.0, 1200),
        (datetime(2026, 9, 13, 7, 0, tzinfo=UTC), 10000.0, 2520),
        (datetime(2026, 9, 20, 7, 0, tzinfo=UTC), 21100.0, 5600),
    ]
    ids = [_store_race(*race) for race in races]
    tagged = client.put(
        "/race-tags",
        json={
            "items": [
                {
                    "connect_activity_id": 7000 + n,
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "distance_m": distance,
                    "duration_s": float(duration),
                    "race": True,
                }
                for n, (start, distance, duration) in enumerate(races)
            ]
        },
    )
    assert tagged.status_code == 200, tagged.text
    assert [(i["outcome"], i["session_id"]) for i in tagged.json()["items"]] == [("tagged", s) for s in ids]

    body = client.get("/state").json()
    [riegel] = [d for d in body["determinants"] if d["name"] == "riegel_exponent"]
    print(f"  served riegel_exponent {riegel}")
    assert [e["session_id"] for e in riegel["efforts"]] == ids
    assert riegel["excluded"] == []
    before = [e["fitness_before"] for e in riegel["efforts"]]
    assert before[0] is None and None not in before[1:], before
    assert [e["fitness_drop"] for e in riegel["efforts"]] == [None, None, None]

    result = _run("state")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("Endurance exponent"))
    assert lines[first:] == [
        "Endurance exponent b 1.070 (80 %: 1.069 to 1.071), high, from 3 races in the last 1095 days, 5.0 to 21.1 km",
        "Race in b 2026-09-06: 5.0 km (5.0 km adjusted), 0:20:00, fitness -, weight 1.00, 0.0 % against the line",
        f"Race in b 2026-09-13: 10.0 km (10.0 km adjusted), 0:42:00, fitness {_whole(before[1])} (-), weight 1.00, "
        "0.0 % against the line",
        f"Race in b 2026-09-20: 21.1 km (21.1 km adjusted), 1:33:20, fitness {_whole(before[2])} (-), weight 1.00, "
        "0.0 % against the line",
    ], result.stdout
    assert _no_network == []
