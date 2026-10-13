"""F021 AC5 (API) and AC1's saved-value row: ``POST /sessions/{session_id}/load/recompute``.

A saved load is computed once, at upload, and never moves on its own: a dated entry, a replaced or
deleted entry, or a ``PATCH /me`` changes no saved body. The recompute route is the one thing that
replaces it: it recomputes the session's load with the current files, entries and anchors under the
run-date lookup, replaces the saved body and its ``load_value`` mirror in one transaction, and answers
200 with the ``SessionLoad`` fields at the top level plus a boolean ``changed``. ``changed`` compares
after filling the new input keys' defaults on the old body, so a body saved before dated entries
existed is not "changed" by those keys alone. An unknown id is a 404 with a string ``detail``.

Every row uploads ``sample_run`` with its ``zones_target`` message removed
(``tests/support/fit_patch.py``), so the file carries resting 47 and sex but no max and no threshold;
``PATCH /me`` enters max 189 and threshold 169 as the anchors. The expected loads come from
``tests/support/trimp_oracle.py``'s pieces, which import nothing from ``runcoach_api``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.models import Session
from runcoach_api.schemas import SessionLoad

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
RUN = "sample_run.fit"
ZONES_TARGET = 7
TOLERANCE = 0.01
HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")
NEW_INPUT_KEYS = ("entry_id", "effective_from", "out_of_period")


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


trimp_oracle = _load_support("trimp_oracle")
fit_patch = _load_support("fit_patch")


def _without_zones() -> bytes:
    return fit_patch.remove_messages((FIXTURES / RUN).read_bytes(), ZONES_TARGET)


def _oracle_load(resting: int, max_hr: int, threshold: int) -> float:
    run = trimp_oracle.decode_run(FIXTURES / RUN)
    hr_time_s, avg_hr = trimp_oracle.time_basis(run.records, run.distances)
    span = float(max_hr - resting)
    value = trimp_oracle.trimp(hr_time_s / 60.0, (avg_hr - resting) / span, "male")
    reference = trimp_oracle.threshold_hour_reference((threshold - resting) / span, "male")
    return value / reference * 100.0


def _setup(client: TestClient) -> str:
    """Anchors max 189 and threshold 169, then the zoneless run; its saved max is the anchor's 189."""
    response = client.patch("/me", json={"max_hr_bpm": 189, "threshold_hr_bpm": 169})
    assert response.status_code == 200, response.text
    response = client.post("/sessions", files={"file": (RUN, _without_zones())})
    assert response.status_code == 201, response.text
    session_id = response.json()["session_id"]
    assert _saved(session_id)["inputs"]["max_hr_bpm"]["value"] == 189
    return session_id


def _add_entry(
    client: TestClient, value: int, effective_from: str = "2016-01-01", expected: int = 201
) -> dict:
    response = client.post(
        "/me/hr-history", json={"field": "max_hr_bpm", "value": value, "effective_from": effective_from}
    )
    assert response.status_code == expected, response.text
    return response.json()


def _recompute(client: TestClient, session_id: str) -> dict:
    response = client.post(f"/sessions/{session_id}/load/recompute")
    assert response.status_code == 200, response.text
    body = response.json()
    print(
        f"recompute {session_id}: changed {body['changed']} load {body['session_load']} max {body['inputs']['max_hr_bpm']}"
    )
    return body


def _row(session_id: str):
    conn = db.get_connection()
    try:
        return conn.execute(
            f"SELECT load_value, load_reason, body FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()


def _saved_text(session_id: str) -> str:
    row = _row(session_id)
    assert row is not None, f"no saved load for {session_id}"
    return row["body"]


def _saved(session_id: str) -> dict:
    return json.loads(_saved_text(session_id))


def _without_changed(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != "changed"}


# --- AC5: the recompute -------------------------------------------------------------------------


def test_after_a_2016_entry_recompute_gives_195_and_changed_true() -> None:
    """The run's saved max is the anchor's 189. A max entry of 195 from 2016-01-01 is on or before the
    run's date and is the only dated candidate, so the recompute uses 195 (``history``), answers 200
    with ``changed`` true, and saves exactly the body it served."""
    with TestClient(app) as client:
        session_id = _setup(client)
        before = _saved(session_id)
        entry = _add_entry(client, 195)
        response = client.post(f"/sessions/{session_id}/load/recompute")
        served = client.get(f"/sessions/{session_id}/load")
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"before max {before['inputs']['max_hr_bpm']} load {before['session_load']}")
    print(f"after  max {body['inputs']['max_hr_bpm']} load {body['session_load']} changed {body['changed']}")
    assert set(body) == set(SessionLoad.model_fields) | {"changed"}
    assert body["changed"] is True
    assert body["session_id"] == session_id
    assert body["inputs"]["max_hr_bpm"] == {
        "value": 195,
        "source": "history",
        "session_id": None,
        "anchor_version": None,
        "anchor_unavailable": None,
        "entry_id": entry["entry_id"],
        "effective_from": "2016-01-01",
        "out_of_period": False,
    }
    for field_name in HR_FIELDS:
        assert isinstance(body["inputs"][field_name]["out_of_period"], bool), field_name
    assert body["inputs"]["threshold_hr_bpm"]["value"] == 169
    assert body["inputs"]["resting_hr_bpm"]["value"] == 47
    expected = _oracle_load(47, 195, 169)
    print(f"oracle 47/195/169 load {expected:.4f}; 47/189/169 load {_oracle_load(47, 189, 169):.4f}")
    assert body["session_load"]["value"] == pytest.approx(expected, abs=TOLERANCE)
    assert before["session_load"]["value"] == pytest.approx(_oracle_load(47, 189, 169), abs=TOLERANCE)
    assert _without_changed(body) == _saved(session_id)
    assert served.status_code == 200
    assert served.json() == _without_changed(body)
    row = _row(session_id)
    assert row["load_value"] == body["session_load"]["value"]
    assert row["load_reason"] is None


def test_a_second_recompute_with_nothing_moved_is_changed_false_and_saves_the_same_body() -> None:
    with TestClient(app) as client:
        session_id = _setup(client)
        _add_entry(client, 195)
        first = _recompute(client, session_id)
        text = _saved_text(session_id)
        second = _recompute(client, session_id)
    assert first["changed"] is True
    assert second["changed"] is False
    assert _without_changed(second) == _without_changed(first)
    assert json.loads(_saved_text(session_id)) == json.loads(text)


def test_a_recompute_with_nothing_entered_since_upload_is_changed_false() -> None:
    with TestClient(app) as client:
        session_id = _setup(client)
        before = _saved(session_id)
        body = _recompute(client, session_id)
    assert body["changed"] is False
    assert _without_changed(body) == before


def test_recompute_updates_the_load_value_the_chart_reads() -> None:
    """``GET /metrics/load`` reads ``load_value``: after the recompute the run's counted load is the
    new value, and its day's load moves with it."""
    with TestClient(app) as client:
        session_id = _setup(client)
        before_chart = client.get("/metrics/load")
        _add_entry(client, 195)
        body = _recompute(client, session_id)
        after_chart = client.get("/metrics/load")
    assert before_chart.status_code == 200, before_chart.text
    assert after_chart.status_code == 200, after_chart.text

    def counted(chart: dict) -> list[float]:
        return [c["load"] for day in chart["days"] for c in day["counted"] if c["session_id"] == session_id]

    before_loads, after_loads = counted(before_chart.json()), counted(after_chart.json())
    print(f"chart load before {before_loads} after {after_loads}")
    assert before_loads == [pytest.approx(_oracle_load(47, 189, 169), abs=TOLERANCE)]
    assert after_loads == [body["session_load"]["value"]]
    assert after_loads[0] == pytest.approx(_oracle_load(47, 195, 169), abs=TOLERANCE)
    assert after_loads[0] != pytest.approx(before_loads[0], abs=1e-6)


def test_an_entry_alone_changes_no_saved_body() -> None:
    """Nothing but the recompute replaces a saved load: a dated entry and a ``PATCH /me`` that moves
    the max anchor leave the stored text byte-identical and the route serving it."""
    with TestClient(app) as client:
        session_id = _setup(client)
        text = _saved_text(session_id)
        served_before = client.get(f"/sessions/{session_id}/load").json()
        _add_entry(client, 195)
        assert _saved_text(session_id) == text
        assert client.patch("/me", json={"max_hr_bpm": 190}).status_code == 200
        served_after = client.get(f"/sessions/{session_id}/load").json()
    assert _saved_text(session_id) == text
    assert served_after == served_before
    assert served_after["inputs"]["max_hr_bpm"]["value"] == 189


def test_recompute_of_an_unknown_session_is_404() -> None:
    with TestClient(app) as client:
        session_id = _setup(client)
        text = _saved_text(session_id)
        response = client.post("/sessions/no-such-session/load/recompute")
    print(f"unknown id: {response.status_code} {response.text}")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "no-such-session" in detail
    assert _row("no-such-session") is None
    assert _saved_text(session_id) == text


# --- AC1: a replaced or deleted entry keeps the saved value until recomputed --------------------


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_a_replaced_or_deleted_entry_leaves_the_saved_value(change: str) -> None:
    """The saved load cites the 2016 entry (195). Replacing that entry's value with 197 (same field
    and date: 200, same ``entry_id``) or deleting it (204) leaves the saved body byte-identical; the
    next recompute uses 197 under the same ``entry_id``, or, with no entry left, the anchor's 189."""
    with TestClient(app) as client:
        session_id = _setup(client)
        entry = _add_entry(client, 195)
        cited = _recompute(client, session_id)
        assert cited["inputs"]["max_hr_bpm"]["entry_id"] == entry["entry_id"]
        text = _saved_text(session_id)
        if change == "replace":
            replaced = _add_entry(client, 197, expected=200)
            assert replaced["entry_id"] == entry["entry_id"]
        else:
            assert client.delete(f"/me/hr-history/{entry['entry_id']}").status_code == 204
        unchanged_text = _saved_text(session_id)
        served = client.get(f"/sessions/{session_id}/load").json()
        again = _recompute(client, session_id)
    assert unchanged_text == text
    assert served["inputs"]["max_hr_bpm"]["value"] == 195
    assert served["inputs"]["max_hr_bpm"]["entry_id"] == entry["entry_id"]
    assert again["changed"] is True
    block = again["inputs"]["max_hr_bpm"]
    if change == "replace":
        assert (block["value"], block["source"], block["entry_id"]) == (197, "history", entry["entry_id"])
        assert again["session_load"]["value"] == pytest.approx(_oracle_load(47, 197, 169), abs=TOLERANCE)
    else:
        assert (block["value"], block["source"], block["entry_id"]) == (189, "anchor", None)
        assert block["out_of_period"] is False
        assert again["session_load"]["value"] == pytest.approx(_oracle_load(47, 189, 169), abs=TOLERANCE)


# --- AC5's other rows: a non-running session, no saved load, an old body, a fault ----------------


def test_a_non_running_session_recomputes_as_sport_not_running(persist_sessions) -> None:
    persist_sessions(
        [
            Session(
                session_id="ride",
                sport="cycling",
                source_vendor="garmin",
                start_time="2024-05-01T07:00:00+00:00",
                source_device="edge",
            )
        ]
    )
    before = _saved("ride")
    with TestClient(app) as client:
        body = _recompute(client, "ride")
    assert body["session_load"]["unavailable"] == "sport_not_running"
    assert body["changed"] is False
    assert _without_changed(body) == before


def test_a_session_with_no_saved_load_gets_one() -> None:
    with TestClient(app) as client:
        session_id = _setup(client)
        conn = db.get_connection()
        try:
            with conn:
                conn.execute(f"DELETE FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
        finally:
            conn.close()
        assert client.get(f"/sessions/{session_id}/load").status_code == 404
        body = _recompute(client, session_id)
        served = client.get(f"/sessions/{session_id}/load")
    assert body["changed"] is True
    assert served.status_code == 200
    assert served.json() == _without_changed(body)
    assert _row(session_id)["load_value"] == body["session_load"]["value"]


def test_a_body_saved_before_dated_entries_is_compared_with_its_defaults_filled() -> None:
    """A saved body without ``entry_id``, ``effective_from`` and ``out_of_period`` (as F017 saved it)
    recomputes to the same values plus those keys at their defaults: ``changed`` is false, and the
    saved body now carries the keys."""
    with TestClient(app) as client:
        session_id = _setup(client)
        old = _saved(session_id)
        for block in old["inputs"].values():
            if isinstance(block, dict):
                for key in NEW_INPUT_KEYS:
                    block.pop(key, None)
        conn = db.get_connection()
        try:
            with conn:
                conn.execute(
                    f"UPDATE {db.SESSION_LOADS_TABLE} SET body = ? WHERE session_id = ?",
                    (json.dumps(old), session_id),
                )
        finally:
            conn.close()
        body = _recompute(client, session_id)
    assert body["changed"] is False
    for field_name in (*HR_FIELDS, "sex"):
        assert set(NEW_INPUT_KEYS) <= set(_saved(session_id)["inputs"][field_name]), field_name


def test_an_out_of_step_anchor_log_is_the_named_500_and_writes_nothing() -> None:
    """The run lacks max, so the recompute needs the anchors; a log whose max row no longer holds the
    served value is the named 500 ``GET /me`` serves, and the saved body is untouched."""
    with TestClient(app) as client:
        session_id = _setup(client)
        text = _saved_text(session_id)
        conn = db.get_connection()
        try:
            with conn:
                conn.execute(
                    f"UPDATE {db.ANCHOR_VERSIONS_TABLE} SET value = '190' WHERE anchor = 'max_hr_bpm'"
                )
        finally:
            conn.close()
        me = client.get("/me")
        response = client.post(f"/sessions/{session_id}/load/recompute")
    print(f"out of step: {response.status_code} {response.text}")
    assert me.status_code == 500
    assert response.status_code == 500
    assert response.json()["detail"].startswith("the profile store is out of step (")
    assert "restart the API" in response.json()["detail"]
    assert _saved_text(session_id) == text


def test_a_fault_after_the_write_rolls_the_recompute_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replacing write and anything after it share one transaction: a fault raised straight after
    the write leaves the saved body and its ``load_value`` as they were."""
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id = _setup(client)
        _add_entry(client, 195)
        text = _saved_text(session_id)
        value = _row(session_id)["load_value"]
        original = db._replace_session_load

        def write_then_fail(conn, sid, body):
            original(conn, sid, body)
            raise RuntimeError("fault after the replacing write")

        monkeypatch.setattr(db, "_replace_session_load", write_then_fail)
        response = client.post(f"/sessions/{session_id}/load/recompute")
    assert response.status_code == 500
    assert _saved_text(session_id) == text
    assert _row(session_id)["load_value"] == value


def test_a_body_that_does_not_read_as_a_load_is_a_500_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's response is built and validated inside the recompute's transaction, before the
    replacing write: a computed body that ``SessionLoad`` refuses is a 500, and the saved body and
    its ``load_value`` stay as they were rather than being replaced by a body the route cannot serve."""
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id = _setup(client)
        text = _saved_text(session_id)
        value = _row(session_id)["load_value"]
        refused = {"session_load": {"value": 1.0, "unavailable": None}, "not_a_load_field": True}
        monkeypatch.setattr(db, "_session_load_body", lambda conn, sid, zone=None: refused)
        response = client.post(f"/sessions/{session_id}/load/recompute")
    print(f"refused body: {response.status_code} {response.text}")
    assert response.status_code == 500
    assert _saved_text(session_id) == text
    assert _row(session_id)["load_value"] == value


def test_the_recompute_takes_the_write_lock_before_its_first_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recompute holds the write lock from its ``BEGIN`` on, not from its first write: at the
    moment it first reads the store (the existence check), a second connection that does not wait
    is refused the write lock. A deferred ``BEGIN`` would let that connection in, and the body would
    no longer be computed and written from one snapshot."""
    import sqlite3

    with TestClient(app) as client:
        session_id = _setup(client)
    real = db.session_exists
    outcomes: list[str] = []

    def probe(conn, sid):
        if not outcomes:
            other = sqlite3.connect(db._load_config_cached().data_dir / db.DB_FILENAME, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                outcomes.append(f"refused: {exc}")
            else:
                other.rollback()
                outcomes.append("taken")
            finally:
                other.close()
        return real(conn, sid)

    monkeypatch.setattr(db, "session_exists", probe)
    conn = db.get_connection()
    try:
        assert db.recompute_session_load(conn, session_id) is not None
    finally:
        conn.close()
    print(f"\n  second connection's BEGIN IMMEDIATE at the first read: {outcomes}")
    assert len(outcomes) == 1, outcomes
    assert outcomes[0].startswith("refused") and "database is locked" in outcomes[0], outcomes
