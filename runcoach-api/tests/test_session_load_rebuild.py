"""A saved load survives a rebuild of the store (F017 AC11, IDEA-139).

The store is built twice from the same files: every fixture with run proof
``yes`` in ``fixtures/README.md`` uploaded in alphabetical order, then the
store deleted and the same files uploaded newest first. The anchor version log
differs between the two orders (the corpus carries 188 and 189 as max, and the
latest-by-start-time rule moves the log in one order and not the other), yet
each run's saved body is identical, because every running fixture carries all
four HR values in its own file and the save reads no anchor for a field the
file carries. ``dev_fields_run`` reads 47/189/162, another athlete's values, so
a leaked anchor read would show as a different body in one of the orders.

A run that borrows an anchor (``sample_run`` with its ``zones_target`` message
removed through ``tests/support/fit_patch.py``) records the value and version
it borrowed, and a later ``PATCH /me`` leaves that record unchanged (user
ruling C1): the saved body is the store's own record of what was used, so a
rebuild in another anchor state is the only thing that can change it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

# Every fixture with run proof ``yes`` in fixtures/README.md, alphabetically.
RUN_FIXTURES = (
    "dev_fields_run.fit",
    "hilly_long_run_17k_fr945.fit",
    "hilly_run_8k_fr945.fit",
    "sample_run.fit",
    "strap_run_hrv.fit",
    "wrist_ppg_run.fit",
)
HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")
ZONES_TARGET = 7


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _upload(client: TestClient, name: str, raw: bytes | None = None) -> str:
    response = client.post("/sessions", files={"file": (name, raw or (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _saved_text(session_id: str) -> str:
    conn = db.get_connection()
    try:
        row = conn.execute(f"SELECT body FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no saved load for {session_id}"
    return row["body"]


def _anchor_log() -> dict[str, tuple[object, int]]:
    conn = db.get_connection()
    try:
        return db._read_anchor_log(conn)
    finally:
        conn.close()


def _build(order: tuple[str, ...]) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[object, int]]]:
    """Upload ``order`` into the current store: ``({file: session_id}, {file: start_time}, anchor log)``."""
    ids: dict[str, str] = {}
    starts: dict[str, str] = {}
    with TestClient(app) as client:
        for name in order:
            ids[name] = _upload(client, name)
            detail = client.get(f"/sessions/{ids[name]}")
            assert detail.status_code == 200, detail.text
            starts[name] = detail.json()["start_time"]
    return ids, starts, _anchor_log()


def test_a_rebuild_in_another_order_saves_identical_bodies(isolated_data_dir: Path) -> None:
    alphabetical_ids, starts, alphabetical_log = _build(RUN_FIXTURES)
    alphabetical = {name: _saved_text(session_id) for name, session_id in alphabetical_ids.items()}

    # The store deleted, then the same files newest first.
    (isolated_data_dir / db.DB_FILENAME).unlink()
    newest_first = tuple(sorted(RUN_FIXTURES, key=lambda name: starts[name], reverse=True))
    assert newest_first != RUN_FIXTURES
    rebuilt_ids, _, rebuilt_log = _build(newest_first)
    rebuilt = {name: _saved_text(session_id) for name, session_id in rebuilt_ids.items()}

    print(f"alphabetical order {RUN_FIXTURES}: anchor log {alphabetical_log}")
    print(f"newest first {newest_first}: anchor log {rebuilt_log}")
    assert alphabetical_log != rebuilt_log, "the premise: the two orders leave different anchor versions"

    for name in RUN_FIXTURES:
        # The ids are derived from (device, start_time), so they are the same in both stores.
        assert rebuilt_ids[name] == alphabetical_ids[name], name
        body = json.loads(alphabetical[name])
        print(
            f"{name}: load {body['session_load']['value']} | "
            + " ".join(f"{f}={body['inputs'][f]['value']}/{body['inputs'][f]['source']}" for f in HR_FIELDS)
            + f" | identical after the rebuild: {rebuilt[name] == alphabetical[name]}"
        )
        for field_name in HR_FIELDS:
            assert body["inputs"][field_name]["source"] == "session_file", (name, field_name)
            assert body["inputs"][field_name]["anchor_version"] is None, (name, field_name)
        assert rebuilt[name] == alphabetical[name], f"{name}: the saved body differs between the two orders"

    # Apart from session ids, the bodies are identical across the two stores pairwise too.
    for name in RUN_FIXTURES:
        a, b = json.loads(alphabetical[name]), json.loads(rebuilt[name])
        a.pop("session_id"), b.pop("session_id")
        assert a == b, name


def test_a_run_that_borrowed_an_anchor_records_it_and_a_later_change_leaves_the_record() -> None:
    raw = fit_patch.remove_messages((FIXTURES / "sample_run.fit").read_bytes(), ZONES_TARGET)
    with TestClient(app) as client:
        assert client.patch("/me", json={"max_hr_bpm": 189, "threshold_hr_bpm": 169}).status_code == 200
        session_id = _upload(client, "sample_run.fit", raw)
        before = _saved_text(session_id)
        assert client.patch("/me", json={"max_hr_bpm": 190, "threshold_hr_bpm": 170}).status_code == 200
        after = _saved_text(session_id)
        served = client.get(f"/sessions/{session_id}/load")
    log = _anchor_log()
    body = json.loads(after)
    print(
        f"borrowed max {body['inputs']['max_hr_bpm']} threshold {body['inputs']['threshold_hr_bpm']}; "
        f"anchor log now {log}"
    )
    assert body["inputs"]["max_hr_bpm"] == {
        "value": 189,
        "source": "anchor",
        "session_id": None,
        "anchor_version": 1,
        "anchor_unavailable": None,
    }
    assert body["inputs"]["threshold_hr_bpm"]["value"] == 169
    assert body["inputs"]["threshold_hr_bpm"]["source"] == "anchor"
    assert body["inputs"]["resting_hr_bpm"]["source"] == "session_file"
    assert log["max_hr_bpm"] == (190, 2)
    assert after == before
    assert served.status_code == 200 and served.json() == body
