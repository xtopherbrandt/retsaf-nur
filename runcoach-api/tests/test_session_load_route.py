"""``GET /sessions/{session_id}/load`` and its contract entry (F017 AC8's served half, AC10's contract bullet).

The route serves the body ``db._save_session_load`` wrote in the upload's own
transaction, and nothing else: it reads no anchor, computes nothing and
writes nothing. These tests read the saved row from the isolated store and
require the route's JSON to be that body exactly; one row corrupts the HR
anchor version log after the upload (the write that makes ``GET /me`` a
named 500) and shows the load still answers 200 with the saved body.

**The contract walk.** ``test_me_route.py``'s walker compares every component
reachable from an operation property by property, but its ``_normalised``
reads a property's ``type``, ``enum``, ``format``, nullability and ``$ref``
only: an array's ``items`` is never opened, so a contract that narrows or
drops the enum behind ``flags[]`` or ``unavailable_fields[]`` would pass it
vacuously. The walker here recurses into ``items``, compares an enum
component (``SessionLoadReason``) by its own enum, and asserts that every
new component was visited.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.main import app
from runcoach_api.metrics import session_load as session_load_module

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
RUN_FIXTURE = "sample_run.fit"
LOAD_PATH = "/sessions/{session_id}/load"
TOLERANCE = 0.01

# Every reason of the F017 reference's gate table and the two reference reasons after it, in order.
GATE_REASONS = (
    "sport_not_running",
    "declared_capture",
    "no_hr",
    "missing_anchor",
    "order_conflict",
    "avg_hr_below_resting",
    "avg_hr_above_max",
    "wrist_hr_threshold_unknown",
    "wrist_hr_at_threshold",
    "not_representable",
    "no_threshold_hr",
    "threshold_order_conflict",
)
RESPONSE_KEYS = {"session_id", "sport", "session_load", "flags", "input_flags", "metrics", "inputs"}
INPUT_KEYS = {
    "hr_time_s",
    "recorded_time_s",
    "timer_time_s",
    "hr_time_fraction",
    "avg_hr_bpm",
    "hr_source",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
    "sex",
}
VALUE_BLOCK_KEYS = {"value", "source", "session_id", "anchor_version", "anchor_unavailable"}
NEW_COMPONENTS = {
    "SessionLoad",
    "SessionLoadValue",
    "SessionLoadReason",
    "SessionLoadMetrics",
    "HrTrimp",
    "Rtss",
    "Srpe",
    "SessionLoadInputs",
    "SessionLoadHrInput",
    "SessionLoadSexInput",
}


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """A connection to the isolated store, opened outside the app and closed on exit."""
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _saved_body(session_id: str) -> dict:
    """The ``session_loads.body`` the upload saved, JSON-decoded, read outside the app."""
    with _connection() as conn:
        row = conn.execute(
            f"SELECT body FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,)
        ).fetchone()
    assert row is not None, f"no saved load for {session_id}"
    return json.loads(row["body"])


def _schema_and_counts() -> tuple[list[tuple], dict[str, int]]:
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    return schema, counts


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC8: served from the saved row
# ---------------------------------------------------------------------------


def test_get_session_load_returns_the_saved_body() -> None:
    """The route's JSON is the saved body, key for key; the figures are AC1's (sample_run, male)."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        before = _schema_and_counts()
        response = client.get(f"/sessions/{session_id}/load")
        after = _schema_and_counts()
    assert response.status_code == 200, response.text
    body = response.json()
    saved = _saved_body(session_id)
    print(
        f"served load {body['session_load']['value']:.4f} trimp {body['metrics']['hr_trimp']['value']:.4f} "
        f"reference {body['metrics']['hr_trimp']['threshold_hour_reference']:.4f} | "
        f"saved load {saved['session_load']['value']:.4f}"
    )
    assert body == saved
    assert set(body) == RESPONSE_KEYS
    assert set(body["inputs"]) == INPUT_KEYS
    assert body["session_id"] == session_id
    assert body["session_load"]["value"] == pytest.approx(47.36, abs=TOLERANCE)
    assert body["session_load"]["driver"] == "hr_trimp"
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(82.86, abs=TOLERANCE)
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}
    for field_name in ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex"):
        assert set(body["inputs"][field_name]) == VALUE_BLOCK_KEYS, field_name
        assert body["inputs"][field_name]["source"] == "session_file", field_name
    # A GET writes nothing: the schema and every row count are unchanged.
    assert after == before


def test_unknown_session_returns_the_session_detail_404_body() -> None:
    with TestClient(app) as client:
        load = client.get("/sessions/nope/load")
        detail = client.get("/sessions/nope")
    assert load.status_code == 404
    assert detail.status_code == 404
    assert load.json() == detail.json()
    assert "nope" in load.text


def test_deleted_session_returns_404() -> None:
    """``DELETE /sessions/{id}`` removes the saved load with the session, so the read after it is a 404."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        assert client.get(f"/sessions/{session_id}/load").status_code == 200
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        load = client.get(f"/sessions/{session_id}/load")
        detail = client.get(f"/sessions/{session_id}")
    assert load.status_code == 404
    assert load.json() == detail.json()


def test_the_route_reads_no_anchor() -> None:
    """After the upload the HR anchor version log is corrupted so that it no longer holds a
    served value: ``db.read_hr_anchors`` raises on it and ``GET /me`` is the named 500. The
    load route still answers 200 with the saved body, because it reads the saved row alone."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        saved = _saved_body(session_id)
        with _connection() as conn:
            conn.execute(
                f"UPDATE {db_module.ANCHOR_VERSIONS_TABLE} SET value = '999' WHERE anchor = 'max_hr_bpm'"
            )
            conn.commit()
        me = client.get("/me", headers={"accept": "application/json"})
        load = client.get(f"/sessions/{session_id}/load")
    print(f"  /me {me.status_code}; /load {load.status_code}")
    assert me.status_code == 500, "the corruption did not take: /me still serves the anchors"
    assert load.status_code == 200, load.text
    assert load.json() == saved
    assert load.json()["inputs"]["max_hr_bpm"]["value"] == 188


def test_a_saved_hr_value_of_two_to_the_63_is_served_exactly() -> None:
    """HR values are unbounded ints (AC9): a saved body carrying ``2**63`` as the max HR used is
    served as that exact integer, which a ``format: int64`` client would not represent."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        saved = _saved_body(session_id)
        saved["inputs"]["max_hr_bpm"] = {
            "value": 2**63,
            "source": "anchor",
            "session_id": None,
            "anchor_version": 3,
            "anchor_unavailable": None,
        }
        with _connection() as conn:
            with conn:
                conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
                db_module._write_session_load(conn, session_id, saved)
        response = client.get(f"/sessions/{session_id}/load")
    assert response.status_code == 200, response.text
    assert response.json()["inputs"]["max_hr_bpm"]["value"] == 2**63
    assert response.json() == saved


# ---------------------------------------------------------------------------
# AC10: the contract moves with the route
# ---------------------------------------------------------------------------


def test_the_contract_operation_matches_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"][LOAD_PATH]["get"]
    built_op = built["paths"][LOAD_PATH]["get"]
    assert op["operationId"] == built_op["operationId"] == "getSessionLoad"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert "security" not in op, "getSessionLoad inherits the global bearerAuth, as the session operations do"
    assert op["parameters"] == [{"$ref": "#/components/parameters/SessionId"}]
    assert [p["name"] for p in built_op["parameters"] if p["in"] == "path"] == ["session_id"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionLoad"
    }
    assert built_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionLoad"
    }
    assert op["responses"]["404"] == {"$ref": "#/components/responses/NotFound"}
    assert set(op["responses"]) == {"200", "404"}
    # The path sits inside paths:, right after the features path.
    paths = list(contract["paths"])
    assert paths.index(LOAD_PATH) == paths.index("/sessions/{session_id}/features") + 1
    tag = contract["tags"][[t["name"] for t in contract["tags"]].index("Sessions")]["description"]
    assert "load" in tag and "implemented" in tag, tag


def test_the_load_blocks_of_the_contract_are_ascii_only() -> None:
    """``check_drift.py`` opens the contract without an encoding, so a Windows console reads it as
    cp1252. The header and the planned operations carry non-ASCII prose from before F017; the load
    operation and its components, the blocks this feature wrote, add none."""
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("  /sessions/{session_id}/load:")
    end = text.index("  # ---", start)
    assert text[start:end].isascii(), "the load operation carries non-ASCII text"
    components = text.index("    SessionLoadReason:")
    assert text[components:].isascii(), "the load components carry non-ASCII text"


def test_the_reason_enum_lists_every_gate_reason() -> None:
    """The closed enum carries every reason of the reference's gate table, the later gates included."""
    served = app.openapi()["components"]["schemas"]["SessionLoadReason"]
    contract = _contract()["components"]["schemas"]["SessionLoadReason"]
    assert set(served["enum"]) == set(contract["enum"]) == set(GATE_REASONS)
    assert len(served["enum"]) == len(GATE_REASONS)


def test_every_reason_the_module_serves_is_a_member_of_the_closed_enum() -> None:
    """``session_load.SERVED_REASONS`` lists every reason ``compute_session_load`` can write into a
    body (the gate tests check each expected row against it); each is in the contract's enum and
    in the reference's order. The enum may be wider while a gate is not yet built; the module may
    never be."""
    enum = _contract()["components"]["schemas"]["SessionLoadReason"]["enum"]
    served = session_load_module.SERVED_REASONS
    print(f"  served {len(served)} of {len(enum)} enum reasons: {served}")
    assert set(served) <= set(enum), set(served) - set(enum)
    assert [reason for reason in GATE_REASONS if reason in served] == list(served)
    assert "not_representable" in served and "avg_hr_above_max" in served and "avg_hr_below_resting" in served


def test_the_hr_inputs_are_unbounded_integers_in_the_contract() -> None:
    contract = _contract()["components"]["schemas"]
    served = app.openapi()["components"]["schemas"]
    for name in ("SessionLoadHrInput",):
        for doc in (contract[name], served[name]):
            value = doc["properties"]["value"]
            flat = json.dumps(value)
            assert '"integer"' in flat, value
            assert "format" not in flat, f"{name}.value must not carry a format: {value}"


REF_PREFIX = "#/components/schemas/"
# The keywords compared per property. A description, a title or a default is prose and is not compared.
COMPARED_KEYWORDS = ("type", "enum", "format", "exclusiveMinimum")


def _ref_name(ref: str) -> str:
    assert ref.startswith(REF_PREFIX), ref
    return ref[len(REF_PREFIX) :]


def _normalised(prop: dict) -> dict:
    """One schema node as ``{type, nullable, enum, format, exclusiveMinimum, ref, items}``, in either
    document's spelling: the contract's ``nullable: true`` and FastAPI's ``anyOf: [X, {type: null}]``
    both read as nullable X, a one-member ``allOf`` is its member, a ``const`` reads as a one-value
    ``enum``, an enum's order is not compared, and an array's ``items`` is normalised the same way
    (the extension over ``test_me_route.py``'s walker, which never opens ``items``)."""
    prop = dict(prop)
    nullable = bool(prop.pop("nullable", False))
    if "anyOf" in prop:
        branches = prop.pop("anyOf")
        rest = [b for b in branches if b != {"type": "null"}]
        assert len(rest) == 1 and len(rest) < len(branches), f"not an optional single type: {branches}"
        nullable = True
        prop = {**prop, **rest[0]}
    if "const" in prop:
        prop["enum"] = [prop.pop("const")]
    if "allOf" in prop:
        (only,) = prop.pop("allOf")
        prop = {**prop, **only}
    out = {"nullable": nullable}
    if "$ref" in prop:
        out["ref"] = _ref_name(prop["$ref"])
    for key in COMPARED_KEYWORDS:
        if key in prop:
            out[key] = sorted(prop[key]) if key == "enum" else prop[key]
    if "items" in prop:
        out["items"] = _normalised(prop["items"])
    assert "ref" in out or "type" in out, f"a node with neither a type nor a $ref: {prop}"
    return out


def _refs(node: dict) -> list[str]:
    """Every component a normalised node names, through ``ref`` and nested ``items``."""
    found = [node["ref"]] if "ref" in node else []
    if "items" in node:
        found.extend(_refs(node["items"]))
    return found


def _load_root(doc: dict) -> str:
    schema = doc["paths"][LOAD_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return _ref_name(schema["$ref"])


def test_every_load_component_matches_the_served_model_property_by_property() -> None:
    """Walk every component reachable from ``getSessionLoad``, in the contract and in ``app.openapi()``
    alike, and compare each property's type, nullability, enum, format and ``$ref`` target, recursing into
    array ``items`` so the enums behind ``flags[]`` and ``unavailable_fields[]`` are compared rather than
    skipped. An enum component (``SessionLoadReason``) has no properties and is compared as one node. Each
    object component's ``required`` list is compared too, and every new component must have been visited."""
    contract = _contract()
    built = app.openapi()
    assert _load_root(contract) == _load_root(built) == "SessionLoad"
    queue, seen, compared = ["SessionLoad"], set(), []
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        ours, theirs = contract["components"]["schemas"][name], built["components"]["schemas"][name]
        if "properties" not in theirs:
            assert "properties" not in ours, name
            got, want = _normalised(ours), _normalised(theirs)
            assert got == want, f"{name}: contract {got} != served {want}"
            compared.append(name)
            continue
        assert set(ours["properties"]) == set(theirs["properties"]), name
        for field in ours["properties"]:
            want = _normalised(theirs["properties"][field])
            got = _normalised(ours["properties"][field])
            assert got == want, f"{name}.{field}: contract {got} != served {want}"
            compared.append(f"{name}.{field}")
            queue.extend(_refs(got))
        assert set(ours.get("required", [])) == set(theirs.get("required", [])), name
        assert ours.get("additionalProperties", True) == theirs.get("additionalProperties", True), name
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == NEW_COMPONENTS, seen
    # The array enums were opened: the walk compared the items behind flags and unavailable_fields.
    flags = _normalised(contract["components"]["schemas"]["SessionLoad"]["properties"]["flags"])
    assert flags["items"]["enum"] == ["sex_defaulted", "wrist_hr"], flags
    fields = _normalised(contract["components"]["schemas"]["HrTrimp"]["properties"]["unavailable_fields"])
    assert fields["items"]["enum"] == ["max_hr_bpm", "resting_hr_bpm"], fields


def test_the_walker_opens_array_items() -> None:
    """The extension itself, perturbed: two array nodes that agree on everything but their items' enum
    are unequal here, where ``test_me_route.py``'s normaliser would call them equal."""
    narrow = {"type": "array", "items": {"type": "string", "enum": ["wrist_hr"]}}
    full = {"type": "array", "items": {"type": "string", "enum": ["wrist_hr", "sex_defaulted"]}}
    assert _normalised(narrow) != _normalised(full)
    assert _normalised(full) == _normalised({"items": {"enum": ["sex_defaulted", "wrist_hr"], "type": "string"}, "title": "Flags", "type": "array"})
