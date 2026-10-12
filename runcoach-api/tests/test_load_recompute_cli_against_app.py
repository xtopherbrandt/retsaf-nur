"""F021 AC5 and AC6 end to end: the CLI's commands against the served app, not a mock.

``runcoach profile history add`` and ``runcoach loads recompute`` run through Typer's ``CliRunner``
with ``runcoach_cli.api_client.client`` set to a ``TestClient(app)`` (an ``httpx.Client``), so every
request the CLI sends is answered by the real routes on the isolated store. The CLI's own tests mock
the recompute body; this file is what keeps those mocks from drifting from the shape the app serves.
``runcoach_cli.config.CONFIG_PATH`` points at a ``cli.toml`` under the test's tmp dir, never the
athlete's.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.models import Session

pytest.importorskip("runcoach_cli")

from runcoach_cli import api_client
from runcoach_cli import config as cli_config
from runcoach_cli.main import app as cli_app
from typer.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
RUN = "sample_run.fit"
ZONES_TARGET = 7
BASE_URL = "http://testserver"

runner = CliRunner()


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


@pytest.fixture
def cli_against_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A TestClient on the app, installed as the CLI's HTTP client, and a tmp ``cli.toml`` naming it."""
    config_path = tmp_path / "cli-home" / "cli.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'api_url = "{BASE_URL}"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "CONFIG_PATH", config_path)
    with TestClient(app, base_url=BASE_URL) as client:
        monkeypatch.setattr(api_client, "client", client)
        yield client


def _run(*args: str):
    result = runner.invoke(cli_app, list(args))
    print(f"$ runcoach {' '.join(args)} -> exit {result.exit_code}\n{result.output}")
    return result


def _saved(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
    finally:
        conn.close()
    assert body is not None
    return body


def test_history_add_then_loads_recompute_through_the_app(cli_against_app: TestClient) -> None:
    """A zoneless run recomputed with the later file's max and threshold (188, 169) borrowed out of
    period; ``history add`` enters max 195 and threshold 170 from 2016-01-01, and ``loads recompute``
    reads the app's own bodies: two recomputed (the run and the later file), the run changed, none
    out of period, none failed, and the ride skipped. A second walk changes nothing."""
    client = cli_against_app
    response = client.post(
        "/sessions",
        files={"file": (RUN, fit_patch.remove_messages((FIXTURES / RUN).read_bytes(), ZONES_TARGET))},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["session_id"]
    run_start = datetime.datetime.fromisoformat(client.get(f"/sessions/{run_id}").json()["start_time"])
    later = (run_start + datetime.timedelta(days=1)).isoformat()
    conn = db.get_connection()
    try:
        db.persist(
            conn,
            Session(
                session_id="later-file",
                sport="running",
                source_vendor="garmin",
                start_time=later,
                source_device="device-later",
                max_hr_bpm=188,
                threshold_hr_bpm=169,
            ),
            [],
            [],
            {},
        )
        db.persist(
            conn,
            Session(
                session_id="ride",
                sport="cycling",
                source_vendor="garmin",
                start_time=later,
                source_device="device-ride",
            ),
            [],
            [],
            {},
        )
    finally:
        conn.close()
    # The run was saved before the later file existed; its recompute borrows that file's max.
    assert client.post(f"/sessions/{run_id}/load/recompute").status_code == 200
    borrowed = _saved(run_id)["inputs"]["max_hr_bpm"]
    print(f"before history: max {borrowed}")
    assert (borrowed["value"], borrowed["source"], borrowed["out_of_period"]) == (188, "anchor", True)

    first_walk = _run("loads", "recompute")
    assert first_walk.exit_code == 0, first_walk.output
    assert "Out of period: 1" in first_walk.output
    assert "Changed: 0" in first_walk.output

    added = _run("profile", "history", "add", "max_hr_bpm", "195", "--from", "2016-01-01")
    assert added.exit_code == 0, added.output
    assert "Added entry" in added.output
    assert "max_hr_bpm 195 from 2016-01-01" in added.output
    added = _run("profile", "history", "add", "threshold_hr_bpm", "170", "--from", "2016-01-01")
    assert added.exit_code == 0, added.output

    walk = _run("loads", "recompute")
    assert walk.exit_code == 0, walk.output
    lines = walk.output.splitlines()
    assert "Recomputed: 2" in lines
    assert "Changed: 1" in lines
    assert "Out of period: 0" in lines
    assert "Skipped (not running): 1" in lines
    assert "Failed: 0" in lines
    block = _saved(run_id)["inputs"]["max_hr_bpm"]
    assert (block["value"], block["source"], block["effective_from"], block["out_of_period"]) == (
        195,
        "history",
        "2016-01-01",
        False,
    )

    again = _run("loads", "recompute")
    assert again.exit_code == 0, again.output
    assert "Changed: 0" in again.output.splitlines()
