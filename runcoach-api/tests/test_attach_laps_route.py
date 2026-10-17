"""F025 AC3, AC4 and the attach half of AC7: ``PUT /sessions/{session_id}/laps`` and the detail fields.

A run stored before laps were kept has no ``laps`` or ``workout_steps`` rows and a null
``laps_read_at``. Each test builds one by uploading a fixture through the real ``POST /sessions``
and then deleting its lap and step rows and clearing ``laps_read_at`` (F025 reference section 4).
Everything runs under ``TestClient`` against the per-test store ``conftest.isolated_data_dir``
points ``RUNCOACH_DATA_DIR`` at.

**The oracle.** Expected rows come from ``tests/support/lap_oracle.py``, which reads the bytes with
``fitdecode`` alone and imports nothing from ``runcoach_api``; the statuses a refused file must
get are the ones ``POST /sessions`` gives the same bytes, asked of the live app in the same test.
The ids and start instants a 409 must name are the ones ``POST /sessions`` stored for each file.

**The clock seam.** ``db.laps_read_now`` is fixed, so ``laps_read_at`` is asserted exactly.

**Refusal table** (each sent to a stored pre-feature session; nothing is written in any row):

| input | status |
|---|---|
| unknown session id, any bytes (non-FIT included) | 404, before the file is decoded |
| non-FIT bytes, a truncated file, a file with no session or sport message | ``POST /sessions``'s 400 |
| a file over a decode ceiling (``fit_parser.MAX_RECORD_MESSAGES``) | ``POST /sessions``'s 413 |
| a file over ``MAX_UPLOAD_BYTES`` | ``POST /sessions``'s 413 |
| another run's file | 409 naming both ids and both start instants |
| the same file with another ``garmin_product`` (another device, same start) | 409, as above |
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic.json_schema import models_json_schema
from runcoach_api import db
from runcoach_api import main as main_module
from runcoach_api.ingestion import fit_parser
from runcoach_api.main import app
from runcoach_api.schemas import SessionLap, SessionLapsAttached, SessionWorkoutStep

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"

PATH = "/sessions/{session_id}/laps"
FIXED_READ_AT = "2026-10-16T12:34:56+00:00"
SECOND_READ_AT = "2026-10-17T08:00:00+00:00"

FILE_ID, SESSION_MSG, SPORT_MSG, WORKOUT_STEP = 0, 18, 12, 27
GARMIN_PRODUCT = 2
MESSAGE_INDEX = 254

# The entry shapes F025 reference section 2 lists, without session_id (the entry sits under its session).
LAP_KEYS = frozenset(
    {"lap_index", "start_t", "end_t", "timer_s", "elapsed_s", "distance_m", "intensity", "wkt_step_index", "lap_trigger"}
)
STEP_KEYS = frozenset(
    {
        "step_index",
        "workout_name",
        "step_name",
        "intensity",
        "duration_type",
        "duration_value",
        "target_type",
        "target_value",
        "target_low",
        "target_high",
    }
)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
lap_oracle = _load_support("lap_oracle")
contract_walk = _load_support("contract_walk")
contract_validate = _load_support("contract_validate")


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "laps_read_now", lambda: FIXED_READ_AT)


def _bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _named(value):
    """The oracle's enum value as stored: a name, or None for an int fitdecode cannot name."""
    return value if isinstance(value, str) else None


def _expected(raw: bytes) -> tuple[list[dict], list[dict]]:
    """The lap and step entries the oracle reads from ``raw``, in file order."""
    oracle = lap_oracle.read_laps_and_steps(raw)
    laps = [
        {
            "lap_index": index,
            "start_t": lap.start_t,
            "end_t": lap.end_t,
            "timer_s": lap.timer_s,
            "elapsed_s": lap.elapsed_s,
            "distance_m": lap.distance_m,
            "intensity": _named(lap.intensity),
            "wkt_step_index": lap.wkt_step_index,
            "lap_trigger": _named(lap.lap_trigger),
        }
        for index, lap in enumerate(oracle.laps)
    ]
    steps = [
        {
            "step_index": step.step_index,
            "workout_name": oracle.workout_name,
            "step_name": step.step_name,
            "intensity": _named(step.intensity),
            "duration_type": _named(step.duration_type),
            "duration_value": step.duration_value,
            "target_type": _named(step.target_type),
            "target_value": step.target_value,
            "target_low": step.target_low,
            "target_high": step.target_high,
        }
        for step in oracle.steps
    ]
    return laps, steps


def _connect() -> sqlite3.Connection:
    return db.get_connection()


def _stored_rows(session_id: str) -> dict:
    conn = _connect()
    try:
        laps = [
            {k: r[k] for k in LAP_KEYS}
            for r in conn.execute("SELECT * FROM laps WHERE session_id = ? ORDER BY lap_index", (session_id,))
        ]
        steps = [
            {k: r[k] for k in STEP_KEYS}
            for r in conn.execute("SELECT * FROM workout_steps WHERE session_id = ? ORDER BY seq", (session_id,))
        ]
        read_at = conn.execute("SELECT laps_read_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()
    return {"laps": laps, "steps": steps, "laps_read_at": read_at}


def _upload(client: TestClient, raw: bytes, filename: str = "upload.fit") -> dict:
    response = client.post("/sessions", files={"file": (filename, raw)})
    assert response.status_code == 201, response.text
    session_id = response.json()["session_id"]
    detail = client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200, detail.text
    return {"session_id": session_id, "start_time": detail.json()["start_time"]}


def _make_pre_feature(session_id: str) -> None:
    """The state of a run stored before laps were kept: no lap or step row, ``laps_read_at`` null."""
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM laps WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM workout_steps WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE sessions SET laps_read_at = NULL WHERE session_id = ?", (session_id,))
    finally:
        conn.close()
    assert _stored_rows(session_id) == {"laps": [], "steps": [], "laps_read_at": None}


def _pre_feature(client: TestClient, name: str = "sample_run") -> dict:
    stored = _upload(client, _bytes(name), f"{name}.fit")
    _make_pre_feature(stored["session_id"])
    return stored


def _put(client: TestClient, session_id: str, raw: bytes, filename: str = "upload.fit"):
    return client.put(f"/sessions/{session_id}/laps", files={"file": (filename, raw)})


def _snapshot() -> dict[str, list[tuple]]:
    """Every table's rows, by table name, each row a tuple of its column values in sorted order.

    ``sessions`` rows carry a ``laps_read_at`` entry separately under ``"sessions.laps_read_at"``,
    so a comparison can leave that one column out and still compare the rest byte for byte.
    """
    conn = _connect()
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        out: dict[str, list[tuple]] = {}
        for table in tables:
            columns = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
            kept = [c for c in columns if not (table == "sessions" and c == "laps_read_at")]
            rows = conn.execute(f"SELECT {', '.join(kept)} FROM {table}").fetchall()
            out[table] = sorted((tuple(r) for r in rows), key=repr)
        out["sessions.laps_read_at"] = sorted(
            (tuple(r) for r in conn.execute("SELECT session_id, laps_read_at FROM sessions")), key=repr
        )
    finally:
        conn.close()
    assert out["sessions"] and out["records"], "an empty store would make the comparison vacuous"
    return out


def _without_lap_state(snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if k not in ("laps", "workout_steps", "sessions.laps_read_at")}


# --- AC3: the attach endpoint ------------------------------------------------------------------


def test_attach_to_a_pre_feature_session_returns_counts_and_ac1_rows():
    raw = _bytes("sample_run")
    with TestClient(app) as client:
        stored = _pre_feature(client)
        response = _put(client, stored["session_id"], raw, "sample_run.fit")

    assert response.status_code == 200, response.text
    assert response.json() == {"session_id": stored["session_id"], "laps": 10, "workout_steps": 4}
    rows = _stored_rows(stored["session_id"])
    laps, steps = _expected(raw)
    assert len(laps) == 10 and len(steps) == 4
    assert rows["laps"] == laps
    assert rows["steps"] == steps
    assert rows["laps_read_at"] == FIXED_READ_AT
    # AC1's literal rows (reference section 1).
    assert [s["intensity"] for s in rows["steps"]] == ["warmup", "active", "active", "cooldown"]
    assert [lap["wkt_step_index"] for lap in rows["laps"]] == [0, 1, 1, 1, 1, 2, 2, 2, 3, None]
    assert rows["laps"][0]["start_t"] == 0


def test_a_second_attach_gives_the_same_rows(monkeypatch):
    raw = _bytes("sample_run")
    with TestClient(app) as client:
        stored = _pre_feature(client)
        first = _put(client, stored["session_id"], raw)
        assert first.status_code == 200, first.text
        rows_first = _stored_rows(stored["session_id"])
        monkeypatch.setattr(db, "laps_read_now", lambda: SECOND_READ_AT)
        second = _put(client, stored["session_id"], raw)

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    rows_second = _stored_rows(stored["session_id"])
    assert rows_second["laps"] == rows_first["laps"] and len(rows_second["laps"]) == 10
    assert rows_second["steps"] == rows_first["steps"] and len(rows_second["steps"]) == 4
    # The call replaces rows rather than adding to them, and re-reads the clock.
    assert rows_second["laps_read_at"] == SECOND_READ_AT


def _assert_409_names(response, path_session: dict, file_session: dict) -> None:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert path_session["session_id"] != file_session["session_id"]
    for value in (
        path_session["session_id"],
        path_session["start_time"],
        file_session["session_id"],
        file_session["start_time"],
    ):
        assert value in detail, (value, detail)


def test_another_runs_file_is_409_naming_both_ids_and_instants():
    other = _bytes("hilly_run_8k_fr945")
    with TestClient(app) as client:
        stored = _pre_feature(client)
        other_stored = _upload(client, other, "hilly_run_8k_fr945.fit")
        assert other_stored["start_time"] != stored["start_time"]
        before = _snapshot()
        response = _put(client, stored["session_id"], other)

    _assert_409_names(response, stored, other_stored)
    assert _snapshot() == before


def test_another_device_same_start_is_409():
    raw = _bytes("sample_run")
    product = lap_oracle_product(raw)
    patched = fit_patch.set_field(raw, FILE_ID, GARMIN_PRODUCT, product + 1)
    with TestClient(app) as client:
        stored = _pre_feature(client)
        # The patched file stores as its own session: another device, the same start.
        twin = _upload(client, patched, "twin.fit")
        assert twin["start_time"] == stored["start_time"]
        before = _snapshot()
        response = _put(client, stored["session_id"], patched)

    _assert_409_names(response, stored, twin)
    assert _snapshot() == before


def lap_oracle_product(raw: bytes) -> int:
    """The file_id ``garmin_product`` (field 2) as an int, read with fitdecode alone."""
    import io

    import fitdecode

    with fitdecode.FitReader(io.BytesIO(raw)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.global_mesg_num == FILE_ID:
                return int(frame.get_field(GARMIN_PRODUCT).raw_value)
    raise AssertionError("no file_id message")


def _refused_forms(raw: bytes) -> dict[str, bytes]:
    return {
        "non_fit": b"this is not a FIT file at all\n" * 8,
        "truncated": raw[: len(raw) // 2],
        "no_session_or_sport": fit_patch.remove_messages(fit_patch.remove_messages(raw, SESSION_MSG), SPORT_MSG),
    }


def test_a_truncated_or_non_fit_file_gets_the_post_status(monkeypatch):
    raw = _bytes("sample_run")
    seen: dict[str, tuple[int, int]] = {}
    with TestClient(app) as client:
        stored = _pre_feature(client)
        before = _snapshot()
        for form, data in _refused_forms(raw).items():
            post = client.post("/sessions", files={"file": ("upload.fit", data)})
            put = _put(client, stored["session_id"], data)
            seen[form] = (post.status_code, put.status_code)
            assert put.status_code == post.status_code, (form, post.text, put.text)
            assert put.json()["detail"] == post.json()["detail"], form
            assert _snapshot() == before, form
        # A decode ceiling is POST /sessions's too: a file over it is a 413 on both.
        with monkeypatch.context() as m:
            m.setattr(fit_parser, "MAX_RECORD_MESSAGES", 10)
            post = client.post("/sessions", files={"file": ("upload.fit", raw)})
            put = _put(client, stored["session_id"], raw)
        seen["decode_ceiling"] = (post.status_code, put.status_code)
        assert put.status_code == post.status_code, (post.text, put.text)
        assert put.json()["detail"] == post.json()["detail"]
        assert _snapshot() == before
        # The size limit is POST /sessions's, read at request time.
        monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", len(raw) - 1)
        post = client.post("/sessions", files={"file": ("upload.fit", raw)})
        put = _put(client, stored["session_id"], raw)
        seen["oversize"] = (post.status_code, put.status_code)
        assert put.status_code == post.status_code == 413, (post.text, put.text)
        assert _snapshot() == before
    print(seen)
    assert seen == {
        "non_fit": (400, 400),
        "truncated": (400, 400),
        "no_session_or_sport": (400, 400),
        "decode_ceiling": (413, 413),
        "oversize": (413, 413),
    }


def test_an_unknown_id_is_404_before_decoding(monkeypatch):
    from runcoach_api.ingestion import pipeline

    decoded: list[bytes] = []
    real_read_laps = pipeline.read_laps

    def spy(raw: bytes):
        decoded.append(raw)
        return real_read_laps(raw)

    monkeypatch.setattr(pipeline, "read_laps", spy)
    with TestClient(app) as client:
        stored = _pre_feature(client)
        before = _snapshot()
        for data in (b"not a FIT file", _bytes("sample_run"), _bytes("hilly_run_8k_fr945")):
            response = _put(client, "no-such-session", data)
            assert response.status_code == 404, response.text
            assert "no-such-session" in response.json()["detail"]
        assert decoded == []
        assert _snapshot() == before
        # The spy is live: a known id does decode.
        assert _put(client, stored["session_id"], _bytes("sample_run")).status_code == 200
    assert len(decoded) == 1


def test_attach_writes_nothing_else(monkeypatch):
    raw = _bytes("sample_run")
    with TestClient(app) as client:
        stored = _pre_feature(client)
        # A second, untouched session, so a write to the wrong row would show.
        neighbour = _pre_feature(client, "strap_run_hrv")
        before = _snapshot()

        response = _put(client, stored["session_id"], raw)
        assert response.status_code == 200, response.text
        after = _snapshot()
        assert _without_lap_state(after) == _without_lap_state(before)
        read_at = dict(after["sessions.laps_read_at"])
        assert read_at == {stored["session_id"]: FIXED_READ_AT, neighbour["session_id"]: None}
        assert len(after["laps"]) == 10 and len(after["workout_steps"]) == 4
        assert {row[0] for row in after["laps"]} == {stored["session_id"]}

        monkeypatch.setattr(db, "laps_read_now", lambda: SECOND_READ_AT)
        assert _put(client, stored["session_id"], raw).status_code == 200
        again = _snapshot()
        assert _without_lap_state(again) == _without_lap_state(before)
        assert again["laps"] == after["laps"] and again["workout_steps"] == after["workout_steps"]

        for session_id, data, status in (
            ("no-such-session", raw, 404),
            (stored["session_id"], b"not a FIT file", 400),
            (neighbour["session_id"], raw, 409),
        ):
            settled = _snapshot()
            assert _put(client, session_id, data).status_code == status
            assert _snapshot() == settled, status


def test_a_write_fault_rolls_the_attach_back(monkeypatch):
    """A fault inside the write leaves the old rows and ``laps_read_at``: one transaction."""
    raw = _bytes("sample_run")
    with TestClient(app) as client:
        stored = _upload(client, raw, "sample_run.fit")
        before = _snapshot()
        real = db.replace_session_laps

        def failing(conn, session_id, laps, steps, read_at):
            real(conn, session_id, laps, steps, read_at)
            raise RuntimeError("injected after the write")

        monkeypatch.setattr(db, "replace_session_laps", failing)
        monkeypatch.setattr(db, "laps_read_now", lambda: SECOND_READ_AT)
        with pytest.raises(RuntimeError, match="injected"):
            _put(client, stored["session_id"], raw)
    assert _snapshot() == before


# --- AC4: the detail ----------------------------------------------------------------------------


def test_detail_serves_laps_steps_and_laps_read_at():
    raw = _bytes("sample_run")
    # Step 0's message_index set to 9: file order and step_index order now differ, so the
    # detail's order is visibly the file's.
    reordered = fit_patch.set_field_at(raw, WORKOUT_STEP, 0, MESSAGE_INDEX, 9)
    with TestClient(app) as client:
        stored = _pre_feature(client)
        never_read = client.get(f"/sessions/{stored['session_id']}").json()
        assert _put(client, stored["session_id"], raw).status_code == 200
        body = client.get(f"/sessions/{stored['session_id']}").json()
        # Same device and start: the slot is freed before the reordered file is uploaded.
        assert client.delete(f"/sessions/{stored['session_id']}").status_code == 204
        other = _upload(client, reordered, "reordered.fit")
        reordered_body = client.get(f"/sessions/{other['session_id']}").json()

    assert never_read["laps"] == [] and never_read["workout_steps"] == []
    assert never_read["laps_read_at"] is None

    laps, steps = _expected(raw)
    assert body["laps"] == laps and len(laps) == 10
    assert body["workout_steps"] == steps and len(steps) == 4
    assert body["laps_read_at"] == FIXED_READ_AT
    assert {frozenset(entry) for entry in body["laps"]} == {LAP_KEYS}
    assert {frozenset(entry) for entry in body["workout_steps"]} == {STEP_KEYS}

    assert [s["step_index"] for s in reordered_body["workout_steps"]] == [9, 1, 2, 3]
    assert reordered_body["workout_steps"] == _expected(reordered)[1]


def test_detail_body_validates_against_the_contract():
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schema = contract["components"]["schemas"]["SessionDetail"]
    with TestClient(app) as client:
        stored = _pre_feature(client)
        never_read = client.get(f"/sessions/{stored['session_id']}").json()
        assert _put(client, stored["session_id"], _bytes("sample_run")).status_code == 200
        read = client.get(f"/sessions/{stored['session_id']}").json()

    props = schema["properties"]
    assert props["laps"] == {"type": "array", "items": {"$ref": "#/components/schemas/SessionLap"}, **_desc(props["laps"])}
    assert props["workout_steps"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/SessionWorkoutStep"},
        **_desc(props["workout_steps"]),
    }
    assert props["laps_read_at"]["type"] == "string" and props["laps_read_at"]["nullable"] is True
    assert {"laps", "workout_steps", "laps_read_at"} <= set(schema["required"])
    for body in (never_read, read):
        # The contract lists exactly what the route serves.
        assert set(body) == set(props)
        assert contract_validate.validate(contract, schema, body) == []
    assert read["laps"] and read["workout_steps"], "the validated body must carry entries"
    lap_schema = contract["components"]["schemas"]["SessionLap"]
    step_schema = contract["components"]["schemas"]["SessionWorkoutStep"]
    assert set(lap_schema["properties"]) == set(lap_schema["required"]) == LAP_KEYS
    assert set(step_schema["properties"]) == set(step_schema["required"]) == STEP_KEYS

    # The validator can fail: a lap entry with a string where a number belongs, and a missing key.
    broken = copy.deepcopy(read)
    broken["laps"][0]["timer_s"] = "fast"
    del broken["workout_steps"][0]["target_low"]
    errors = contract_validate.validate(contract, schema, broken)
    assert any("$.laps[0].timer_s" in e for e in errors), errors
    assert any("$.workout_steps[0]" in e and "target_low" in e for e in errors), errors


def _desc(prop: dict) -> dict:
    return {"description": prop["description"]} if "description" in prop else {}


# --- AC7: the contract ----------------------------------------------------------------------------


def test_served_attach_schema_matches_the_contract():
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    built = app.openapi()
    methods = {"get", "post", "put", "patch", "delete"}
    assert {m for m in built["paths"][PATH] if m in methods} == {m for m in contract["paths"][PATH] if m in methods} == {
        "put"
    }
    op, built_op = contract["paths"][PATH]["put"], built["paths"][PATH]["put"]
    assert op["operationId"] == built_op["operationId"] == "attachSessionLaps"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert op["x-owner-spec"] == "runcoach-api F025"
    assert set(op["responses"]) == set(built_op["responses"]) == {"200", "400", "404", "409", "413", "422"}
    for operation in (op, built_op):
        assert operation["requestBody"]["required"] is True
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/SessionLapsAttached"
        }
    contract_form = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    built_ref = built_op["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    built_form = built["components"]["schemas"][contract_walk.ref_name(built_ref)]
    assert contract_form["required"] == built_form["required"] == ["file"]
    assert set(contract_form["properties"]) == set(built_form["properties"]) == {"file"}
    assert all(ord(ch) < 128 for ch in yaml.safe_dump(op)), "the operation is ASCII only"

    seen, compared = contract_walk.walk(contract, built, ["SessionLapsAttached"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {"SessionLapsAttached"}

    # The detail's entry components against the pydantic models of the same names.
    _, defs = models_json_schema(
        [(SessionLap, "serialization"), (SessionWorkoutStep, "serialization"), (SessionLapsAttached, "serialization")],
        ref_template="#/components/schemas/{model}",
    )
    models = {"components": {"schemas": defs["$defs"]}}
    seen, compared = contract_walk.walk(contract, models, ["SessionLap", "SessionWorkoutStep"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {"SessionLap", "SessionWorkoutStep"}
    assert len(compared) == len(LAP_KEYS) + len(STEP_KEYS)
    for name in ("SessionLap", "SessionWorkoutStep", "SessionLapsAttached"):
        component = contract["components"]["schemas"][name]
        assert all(ord(ch) < 128 for ch in yaml.safe_dump(component)), name
    # Never the planned-workout component's name: yaml.safe_load keeps only the last duplicate key.
    text = CONTRACT.read_text(encoding="utf-8")
    for name in ("WorkoutStep", "SessionLap", "SessionWorkoutStep", "SessionLapsAttached"):
        assert len(re.findall(rf"^    {name}:\s*$", text, flags=re.MULTILINE)) == 1, name


def _assert_422_documents_the_served_body(op: dict, contract: dict, response) -> None:
    """The operation's 422 is getState's: the component describing FastAPI's {detail: [...]}, which
    is what the route serves."""
    ref = op["responses"]["422"]["$ref"]
    assert op["responses"]["422"] == contract["paths"]["/state"]["get"]["responses"]["422"], ref
    schema = contract["components"]["responses"][ref.rsplit("/", 1)[1]]["content"]["application/json"]["schema"]
    assert schema["required"] == ["detail"] and schema["properties"]["detail"]["type"] == "array", schema
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"detail"}, body
    assert isinstance(body["detail"], list) and body["detail"], body
    for entry in body["detail"]:
        assert {"loc", "msg", "type"} <= set(entry), entry


def test_the_422_the_contract_documents_is_the_body_served() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    with TestClient(app) as client:
        response = client.put("/sessions/any-session/laps", data={"not_file": "x"})
    _assert_422_documents_the_served_body(contract["paths"][PATH]["put"], contract, response)
