"""``runcoach goal`` against the served app, not a hand-written body (F032 AC8, the against-the-app
half).

The three subcommands run through Typer's ``CliRunner`` on the harness in
``tests/support/against_app.py``: ``runcoach_cli.api_client.client`` is a ``TestClient(app)``, so
``PUT /goal``, ``GET /goal`` and ``POST /goal/preview`` are answered by the real routes on the
isolated store (the autouse ``isolated_data_dir``). The seeding helpers are
``test_goal_preview_route.py``'s, loaded by path: reference section 8's E2 (four tight races)
stored as flat constant-speed runs, tagged through ``PUT /race-tags`` and given a saved load each.
The clock is frozen through ``main._utcnow``; the autouse ``_no_network`` refuses every socket
connection off the loopback. Every figure the lines are compared with is read back from the served
body and formatted here, with the ``decimal`` module's half-up rounding, not with the CLI's own
helpers.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import main as main_module

HERE = Path(__file__).parent


def _load_by_path(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


against_app = _load_by_path("against_app", HERE / "support" / "against_app.py")
preview = _load_by_path("goal_preview_route_helpers", HERE / "test_goal_preview_route.py")

cli_against_app = against_app.cli_against_app
_run = against_app._run
NOW: datetime = preview.NOW
HALF_M: float = preview.HALF_M
E2 = preview.E2
_races = preview._races

RACE_DATE = "2027-04-11"
GOAL_S = 5500.0
NO_GOAL_LINE = "No goal set. Set one with runcoach goal set."
ASSUMES_LINE = (
    "Assumes: a flat course, still air, cool conditions, no economy adjustment, no durability adjustment, "
    "projected for today, not race day, watch timer time (an auto-pause shortens it; chip time may differ)"
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Refuse every socket connection to an address off the loopback."""
    seen: list[Any] = []
    real_connect = socket.socket.connect
    loopback = {"127.0.0.1", "::1", "localhost"}

    def connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in loopback:
            seen.append(address)
            raise OSError(f"network blocked in tests: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    return seen


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


def _whole(value: float) -> int:
    return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _hms(seconds: float) -> str:
    total = _whole(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _mss(seconds: float) -> str:
    total = _whole(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _pct(fraction: float, signed: bool = True) -> str:
    tenths = int(Decimal(repr(fraction * 1000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if tenths == 0:
        return "0.0 %"
    return f"{tenths / 10:+.1f} %" if signed else f"{tenths / 10:.1f} %"


def _day(start_time: str, zone: str) -> str:
    return datetime.fromisoformat(start_time).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")


def _expected(head: str, target: dict[str, Any]) -> list[str]:
    """Section 7's lines for a served fitted-line ``PaceTarget`` with a goal time, each figure read
    from the body."""
    zone = target["timezone"]
    time, pace = target["projected_time_s"], target["avg_pace_s_per_km"]
    confidence, gap, prov, loo = target["confidence"], target["goal_time_gap"], target["provenance"], target["leave_one_out"]
    assert target["unavailable"] is None and target["route"] == "fitted_line", target
    assert confidence["basis"] == "range" and confidence["reasons"] == [], confidence
    assert gap["gap_s"] < 0 and gap["within_range"] is False, gap
    without = {row["session_id"]: row for row in loo["races"]}
    newest = prov["newest_race"]
    largest = loo["largest"]
    [rests] = [r for r in prov["races"] if r["session_id"] == largest["session_id"]]
    return [
        head,
        f"Projected {_hms(time['value'])} ({_mss(pace['value'])}/km), 80 %: {_hms(time['interval']['low'])} to "
        f"{_hms(time['interval']['high'])} ({_mss(pace['interval']['low'])} to {_mss(pace['interval']['high'])}/km), "
        f"{confidence['label']}",
        f"Confidence {confidence['label']}: from the range, {_pct(confidence['uncertainty'], signed=False)}",
        f"Goal time {_hms(GOAL_S)} is {_hms(-gap['gap_s'])} faster than the projection ({_pct(gap['gap_fraction'])}), "
        "outside the 80 % range",
        "Route: the fitted line through the races used",
        f"b {prov['b']:.3f} (error {prov['error']:.3f}), {prov['label']}",
        f"Newest race {_day(newest['start_time'], zone)}",
        f"Fitness now {'-' if prov['fitness_now'] is None else _whole(prov['fitness_now'])}, "
        f"at the races {'-' if prov['fitness_at_races'] is None else _whole(prov['fitness_at_races'])}",
        *[
            f"Race {_day(r['start_time'], zone)}: {r['gap_equivalent_m'] / 1000:.1f} km in {_hms(r['duration_s'])}, "
            f"weight {r['weight']:.2f}, {_pct(r['residual'])} against the line, "
            f"without it {_hms(without[r['session_id']]['projected_time_s'])} ({_pct(without[r['session_id']]['shift'])})"
            for r in prov["races"]
        ],
        f"Largest shift the label reads: {_day(rests['start_time'], zone)}, without it {_hms(largest['projected_time_s'])} "
        f"({_pct(largest['shift'])})",
        ASSUMES_LINE,
    ]


def test_goal_against_the_app(cli_against_app: TestClient, _no_network: list[Any]) -> None:
    """With no goal the bare command says so and exits 0. ``preview half`` prints E2's half
    projection (the reference's 5542.806803 s, 1:32:23 at 4:23/km) with a null goal time. ``set``
    stores the goal and prints it; the bare command then reads it back and prints the same lines
    as ``GET /goal`` serves, every race line led by its date (the stored sessions carry no name)."""
    client = cli_against_app
    ids = _races(client, *E2)
    assert len(ids) == 4

    none = _run("goal")
    assert none.exit_code == 0, none.output
    assert none.stdout.splitlines() == [NO_GOAL_LINE]
    assert client.get("/goal").status_code == 404

    previewed = client.post("/goal/preview", json={"race": {"distance_m": HALF_M}}).json()
    print(f"  served preview {previewed}")
    assert previewed["projected_time_s"]["value"] == pytest.approx(5542.806803, abs=1e-3)
    assert previewed["race"] == {"date": NOW.date().isoformat(), "distance_m": HALF_M, "name": None}
    assert previewed["goal_time_gap"] is None
    result = _run("goal", "preview", "half")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == f"Preview {NOW.date().isoformat()}: 21.1 km, no goal time"
    assert lines[1] == "Projected 1:32:23 (4:23/km), 80 %: 1:31:53 to 1:32:53 (4:21 to 4:24/km), high"
    assert lines[3] == "Goal time: none set"
    zone = previewed["timezone"]
    prov = previewed["provenance"]
    largest = previewed["leave_one_out"]["largest"]
    [rests] = [r for r in prov["races"] if r["session_id"] == largest["session_id"]]
    assert [line.split(":")[0] for line in lines[4:]] == [
        "Route",
        "b 1.064 (error 0.002), high",
        f"Newest race {_day(E2[3][0].isoformat(), zone)}",
        f"Fitness now {'-' if prov['fitness_now'] is None else _whole(prov['fitness_now'])}, "
        f"at the races {'-' if prov['fitness_at_races'] is None else _whole(prov['fitness_at_races'])}",
        *[f"Race {_day(start.isoformat(), zone)}" for start, _d, _t in E2],
        "Largest shift the label reads",
        "Assumes",
    ], result.stdout

    result = _run("goal", "set", "half", "--date", RACE_DATE, "--time", "1:31:40", "--name", "Vancouver Half")
    assert result.exit_code == 0, result.output
    served = client.get("/goal")
    assert served.status_code == 200, served.text
    body = served.json()
    print(f"  served goal {body}")
    assert body["race"] == {"date": RACE_DATE, "distance_m": HALF_M, "name": "Vancouver Half"}
    assert body["target_time_s"] == GOAL_S and body["status"] == "active"
    assert body["target"]["goal_time_gap"]["gap_s"] == pytest.approx(-42.806803, abs=1e-3)
    expected = _expected(f"Goal {RACE_DATE} Vancouver Half: 21.1 km, goal time 1:31:40", body["target"])
    assert result.stdout.splitlines() == expected, result.stdout

    shown = _run("goal")
    assert shown.exit_code == 0, shown.output
    assert shown.stdout.splitlines() == expected, shown.stdout
    assert shown.stdout.splitlines()[3] == (
        "Goal time 1:31:40 is 0:00:43 faster than the projection (-0.8 %), outside the 80 % range"
    )

    # A bad distance and a bad time are refused before the app sees a request: the goal is unchanged.
    for args in (["goal", "set", "0km", "--date", RACE_DATE], ["goal", "set", "half", "--date", RACE_DATE, "--time", "1:60:00"]):
        # Invoked directly: the usage box Typer prints holds characters the harness's echo cannot
        # always encode on a Windows console.
        refused = against_app.runner.invoke(against_app.cli_app, args)
        assert refused.exit_code == 2, refused.output
    assert client.get("/goal").json() == body
    assert _no_network == []
