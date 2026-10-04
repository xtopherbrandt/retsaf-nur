"""``GET /sessions/{session_id}/features`` and its contract (F013 AC8's 404 clause, AC9, AC10).

The route computes the features on read from the stored records through
``metrics.session_features.compute_session_features`` and stores nothing.
These tests drive the real app in-process against the autouse isolated data
dir: the 404 envelope for an unknown and a deleted id, the schema and row
counts before and after the read, the ``sample_run.fit`` response shape of
the F013 reference (section 8), the contract operation and component against
``app.openapi()``, and the exact key sets the new ``db`` reader hands the pure
module. Values are not asserted here; the real-fixture suite does that.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.main import app
from runcoach_api.metrics import session_features

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
RUN_FIXTURE = "sample_run.fit"
# A resting-HRV capture recorded on a non-running profile: every feature unavailable, still a 200.
NON_RUNNING_FIXTURE = "strap_hrv_capture.fit"
FEATURES_PATH = "/sessions/{session_id}/features"

RESPONSE_KEYS = {
    "session_id",
    "sport",
    "flags",
    "gap_coverage",
    "grade_clamped_fraction",
    "gps_degraded_fraction",
    "features",
}
FEATURE_NAMES = {
    "duration_s",
    "distance_m",
    "avg_pace_s_per_km",
    "gap_avg_pace_s_per_km",
    "ngp_speed_m_s",
    "ngp_pace_s_per_km",
    "avg_hr_bpm",
    "avg_cadence_spm",
    "avg_power_w",
    "total_ascent_m",
    "total_descent_m",
    "env_temperature_c",
    "env_humidity_pct",
    "env_wind_ms",
}
SESSION_MAPPING_KEYS = {"session_id", "sport", "quality_flags", "context"}
RECORD_KEYS = {
    "t",
    "distance",
    "speed",
    "altitude",
    "heart_rate",
    "cadence",
    "power",
    "power_model",
    "gps_degraded",
    "sample_quality",
}
# The note the contract and the served schema both carry about SessionSummary's shared names.
SHARED_NAMES_NOTE = (
    "duration_s, distance_m and avg_pace_s_per_km are the same quantities SessionSummary names: "
    "recorded time, not elapsed"
)

_WHITESPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _schema_and_counts() -> tuple[list[tuple], dict[str, int]]:
    """``sqlite_master`` and every table's row count, read outside the app."""
    conn = db_module.get_connection()
    try:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()
    return schema, counts


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------


def test_the_database_path_resolves_under_tmp_path(tmp_path: Path) -> None:
    """The autouse isolation holds before the first request: the store the route opens is under ``tmp_path``."""
    conn = db_module.get_connection()
    try:
        (db_path,) = [row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"]
    finally:
        conn.close()
    resolved = Path(db_path).resolve()
    assert resolved.is_relative_to(tmp_path.resolve()), db_path
    assert not resolved.is_relative_to(Path.home() / ".runcoach"), db_path


# ---------------------------------------------------------------------------
# 404: the envelope of GET /sessions/{id}
# ---------------------------------------------------------------------------


def test_unknown_session_returns_the_session_detail_404_body() -> None:
    with TestClient(app) as client:
        features = client.get("/sessions/nope/features")
        detail = client.get("/sessions/nope")
    assert features.status_code == 404
    assert detail.status_code == 404
    assert features.json() == detail.json()
    assert "nope" in features.text


def test_deleted_session_returns_the_session_detail_404_body() -> None:
    """AC9's second clause: nothing is stored, so a deleted session has no features to serve."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        assert client.get(f"/sessions/{session_id}/features").status_code == 200
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        features = client.get(f"/sessions/{session_id}/features")
        detail = client.get(f"/sessions/{session_id}")
    assert features.status_code == 404
    assert detail.status_code == 404
    assert features.json() == detail.json()


# ---------------------------------------------------------------------------
# AC9: computed on read
# ---------------------------------------------------------------------------


def test_the_read_leaves_the_schema_and_every_row_count_unchanged() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        before = _schema_and_counts()
        response = client.get(f"/sessions/{session_id}/features")
        after = _schema_and_counts()
    assert response.status_code == 200, response.text
    assert after == before
    assert before[1]["records"] > 0, "the fixture stored no records; the comparison would be vacuous"


# ---------------------------------------------------------------------------
# the 200 response shape (reference section 8)
# ---------------------------------------------------------------------------


def test_sample_run_returns_200_with_the_reference_shape() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        response = client.get(f"/sessions/{session_id}/features")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert body["session_id"] == session_id
    assert body["sport"] == "running"
    assert isinstance(body["flags"], list)
    assert set(body["features"]) == FEATURE_NAMES
    for name, feature in body["features"].items():
        expected_keys = {"value", "unavailable", "power_model"} if name == "avg_power_w" else {"value", "unavailable"}
        assert set(feature) == expected_keys, name
        assert (feature["value"] is None) != (feature["unavailable"] is None), name


def test_the_route_serves_the_pure_module_result_unchanged() -> None:
    """The response is ``compute_session_features`` over the reader's output, field for field."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
        body = client.get(f"/sessions/{session_id}/features").json()
    conn = db_module.get_connection()
    try:
        session, rows = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert body == session_features.compute_session_features(session, rows)


def test_a_non_running_session_is_a_200_with_every_feature_unavailable() -> None:
    """An all-unavailable session is an answer, not an error."""
    with TestClient(app) as client:
        session_id = _upload(client, NON_RUNNING_FIXTURE)
        response = client.get(f"/sessions/{session_id}/features")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sport"] != "running"
    assert {f["unavailable"] for f in body["features"].values()} == {"sport_not_running"}
    assert body["gap_coverage"] is None
    assert body["grade_clamped_fraction"] is None
    assert body["gps_degraded_fraction"] is None


# ---------------------------------------------------------------------------
# the db reader returns only what it should
# ---------------------------------------------------------------------------


def test_the_reader_session_mapping_has_exactly_the_four_keys() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
    conn = db_module.get_connection()
    try:
        session, rows = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert set(session) == SESSION_MAPPING_KEYS
    assert session["session_id"] == session_id
    assert session["sport"] == "running"
    assert isinstance(session["quality_flags"], list)
    assert isinstance(session["context"], dict)
    assert rows, "the fixture stored no records"


def test_the_reader_context_is_an_empty_mapping_when_the_stored_context_is_null() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
    conn = db_module.get_connection()
    try:
        conn.execute("UPDATE sessions SET context = NULL WHERE session_id = ?", (session_id,))
        conn.commit()
        session, _rows = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert session["context"] == {}


def test_the_reader_records_carry_only_the_feature_columns_decoded() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
    conn = db_module.get_connection()
    try:
        _session, rows = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert rows
    assert {frozenset(r) for r in rows} == {frozenset(RECORD_KEYS)}
    assert all(isinstance(r["sample_quality"], list) for r in rows)
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)


def test_the_reader_orders_by_t_then_stored_order() -> None:
    """Ties on ``t`` keep stored order (reference section 2): ``ORDER BY t, rowid``."""
    with TestClient(app) as client:
        session_id = _upload(client, RUN_FIXTURE)
    conn = db_module.get_connection()
    try:
        last_t = conn.execute("SELECT MAX(t) FROM records WHERE session_id = ?", (session_id,)).fetchone()[0]
        for marker in (101.0, 102.0, 103.0):
            conn.execute(
                "INSERT INTO records (session_id, t, distance) VALUES (?, ?, ?)",
                (session_id, last_t + 1.0, marker),
            )
        conn.execute(
            "INSERT INTO records (session_id, t, distance) VALUES (?, ?, ?)",
            (session_id, last_t + 0.5, 100.0),
        )
        conn.commit()
        _session, rows = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert [r["distance"] for r in rows[-4:]] == [100.0, 101.0, 102.0, 103.0]


def test_the_reader_returns_none_for_an_unknown_session() -> None:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        assert db_module.read_session_feature_inputs(conn, "nope") is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC10: the contract moves with the route
# ---------------------------------------------------------------------------


def test_the_contract_operation_matches_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"][FEATURES_PATH]["get"]
    built_op = built["paths"][FEATURES_PATH]["get"]
    assert op["operationId"] == built_op["operationId"] == "getSessionFeatures"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert op["parameters"] == [{"$ref": "#/components/parameters/SessionId"}]
    assert [p["name"] for p in built_op["parameters"] if p["in"] == "path"] == ["session_id"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionFeatures"
    }
    assert op["responses"]["404"] == {"$ref": "#/components/responses/NotFound"}
    assert "200" in built_op["responses"]
    assert "features" in contract["tags"][[t["name"] for t in contract["tags"]].index("Sessions")]["description"]


def test_the_contract_component_matches_the_response_model() -> None:
    contract = _contract()
    built = app.openapi()["components"]["schemas"]
    component = contract["components"]["schemas"]["SessionFeatures"]
    served = built["SessionFeaturesResponse"]
    assert set(component["properties"]) == set(served["properties"]) == RESPONSE_KEYS

    features = component["properties"]["features"]
    assert set(features["properties"]) == set(built["SessionFeatureValues"]["properties"]) == FEATURE_NAMES
    value_shape = {"value", "unavailable"}
    for name, feature in features["properties"].items():
        expected = value_shape | {"power_model"} if name == "avg_power_w" else value_shape
        assert set(_deref(contract, feature)["properties"]) == expected, name
    assert set(built["FeatureValue"]["properties"]) == value_shape
    assert set(built["PowerFeatureValue"]["properties"]) == value_shape | {"power_model"}


def _deref(contract: dict, node: dict) -> dict:
    """The component a property points at, through ``$ref`` or a one-member ``allOf``."""
    if "allOf" in node:
        (node,) = node["allOf"]
    ref = node["$ref"]
    assert ref.startswith("#/components/schemas/"), ref
    return contract["components"]["schemas"][ref.rsplit("/", 1)[1]]


def test_the_contract_component_states_the_session_summary_shared_names() -> None:
    """The note that a future listSessions projects these values, pinned by a whitespace-flattened phrase."""
    contract = _contract()
    component = contract["components"]["schemas"]["SessionFeatures"]
    served = app.openapi()["components"]["schemas"]["SessionFeaturesResponse"]
    assert _flat(SHARED_NAMES_NOTE) in _flat(component["description"])
    assert _flat(SHARED_NAMES_NOTE) in _flat(served["description"])
