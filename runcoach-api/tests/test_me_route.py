"""``GET /me`` and its contract entry (F016 AC3's API part, AC8's ``getMe`` part).

The route serves one object: the contract's existing ``id``, ``display_name``,
``units`` and ``created_at`` (the singleton athlete-settings row that
``db.init_schema`` seeds), one entry per profile field
(``value``, ``unavailable``, ``source``, ``session_id``, ``recorded_at``,
``entered_value``) as ``profile.resolve`` computes it, and the four HR anchors
as ``db.read_hr_anchors`` serves them, with their version or their
``unavailable`` reason.

**The lifespan.** Every test here enters the app lifespan explicitly
(``with TestClient(app)``), which runs ``db.init_schema`` against the
isolated store, as uvicorn does at startup. A GET does not write, so the
route never creates the schema or the settings row itself; a store that has
not been through ``init_schema`` is not served.

Entries are written with ``db.write_profile_entries``, the function
``PATCH /me`` calls, so these tests read the GET alone (``test_me_patch_route``
drives the entries through ``PATCH /me``). Values are pinned
against the fixtures' decoded settings: ``sample_run`` carries resting 47,
max 188, threshold 169 and ``male``.
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import profile
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"

ENTRY_KEYS = {"value", "unavailable", "source", "session_id", "recorded_at", "entered_value"}
ANCHOR_KEYS = ENTRY_KEYS | {"version"}
ATHLETE_KEYS = {"id", "display_name", "units", "created_at", "anchors", *profile.ENTERED_FIELDS}
DEFAULT_UNITS = {"distance": "km", "pace": "min_per_km", "temperature": "c"}
MISSING_ENTRY = {
    "value": None,
    "unavailable": "missing",
    "source": None,
    "session_id": None,
    "recorded_at": None,
    "entered_value": None,
}
MISSING_ANCHOR = {**MISSING_ENTRY, "version": None}

# The served component behind each property of the contract's Athlete.
ENTRY_COMPONENTS = {
    "sex": "ProfileSexEntry",
    "birth_date": "ProfileDateEntry",
    "body_mass_kg": "ProfileNumberEntry",
    "height_cm": "ProfileNumberEntry",
    "resting_hr_bpm": "ProfileIntegerEntry",
    "max_hr_bpm": "ProfileIntegerEntry",
    "threshold_hr_bpm": "ProfileIntegerEntry",
}
ANCHOR_COMPONENTS = {
    "resting_hr_bpm": "HrAnchor",
    "max_hr_bpm": "HrAnchor",
    "threshold_hr_bpm": "HrAnchor",
    "sex": "SexAnchor",
}


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _enter(values: dict) -> str:
    with _connection() as conn:
        return db_module.write_profile_entries(conn, values)


def _schema_and_rows() -> tuple[list[tuple], dict[str, list[tuple]]]:
    """``sqlite_master`` and every table's rows, read outside the app."""
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}
    return schema, rows


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC3: an entry is shown when a file shadows it
# ---------------------------------------------------------------------------


def test_get_me_shows_a_shadowed_entry() -> None:
    with TestClient(app) as client:
        _enter({"max_hr_bpm": 195})
        session_id = _upload(client, "sample_run.fit")
        start_time = client.get(f"/sessions/{session_id}").json()["start_time"]
        response = client.get("/me")
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"  max_hr_bpm={body['max_hr_bpm']}")
    assert body["max_hr_bpm"] == {
        "value": 188,
        "unavailable": None,
        "source": "fit",
        "session_id": session_id,
        "recorded_at": start_time,
        "entered_value": 195,
    }
    anchor = body["anchors"]["max_hr_bpm"]
    assert (anchor["value"], anchor["source"], anchor["session_id"], anchor["entered_value"]) == (
        188,
        "fit",
        session_id,
        195,
    )
    assert anchor["unavailable"] is None and anchor["version"] >= 1, anchor


def test_get_me_serves_an_entered_value_with_its_set_time() -> None:
    """``birth_date`` has no FIT source, so an entry is the effective value, with source ``entered``."""
    with TestClient(app) as client:
        set_at = _enter({"birth_date": "1980-05-01"})
        body = client.get("/me").json()
    assert body["birth_date"] == {
        "value": "1980-05-01",
        "unavailable": None,
        "source": "entered",
        "session_id": None,
        "recorded_at": set_at,
        "entered_value": "1980-05-01",
    }


def test_get_me_on_an_empty_database_reads_every_field_missing() -> None:
    with TestClient(app) as client:
        response = client.get("/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ATHLETE_KEYS
    for field in profile.ENTERED_FIELDS:
        assert body[field] == MISSING_ENTRY, field
    assert set(body["anchors"]) == set(profile.ANCHOR_FIELDS)
    for field in profile.ANCHOR_FIELDS:
        assert body["anchors"][field] == MISSING_ANCHOR, field
    assert isinstance(body["id"], str) and body["id"]
    assert body["display_name"] is None
    assert body["units"] == DEFAULT_UNITS
    datetime.datetime.fromisoformat(body["created_at"])


def test_get_me_serves_an_order_conflict_anchor_with_its_reason() -> None:
    """Entered resting 190 above entered max 188: both are ``order_conflict``; threshold 169 is checked
    against neither and is served; the per-field entries still show the values themselves."""
    with TestClient(app) as client:
        _enter({"resting_hr_bpm": 190, "max_hr_bpm": 188, "threshold_hr_bpm": 169})
        body = client.get("/me").json()
    anchors = body["anchors"]
    print(f"  anchors={anchors}")
    for field, entered in (("resting_hr_bpm", 190), ("max_hr_bpm", 188)):
        assert anchors[field] == {**MISSING_ANCHOR, "unavailable": "order_conflict", "entered_value": entered}
        assert body[field]["value"] == entered and body[field]["source"] == "entered", body[field]
    assert anchors["threshold_hr_bpm"]["value"] == 169
    assert anchors["threshold_hr_bpm"]["version"] == 1
    assert anchors["sex"]["unavailable"] == "missing"


# ---------------------------------------------------------------------------
# a GET does not write
# ---------------------------------------------------------------------------


def test_the_read_leaves_the_schema_and_every_row_unchanged() -> None:
    with TestClient(app) as client:
        _upload(client, "sample_run.fit")
        _enter({"max_hr_bpm": 195})
        before = _schema_and_rows()
        response = client.get("/me")
        after = _schema_and_rows()
    assert response.status_code == 200, response.text
    assert after == before
    assert before[1][db_module.ATHLETE_SETTINGS_TABLE], "no settings row; the comparison would miss it"


def test_the_settings_row_is_seeded_once_by_init_schema() -> None:
    with TestClient(app) as client:
        first = client.get("/me").json()
    with _connection() as conn:
        db_module.init_schema(conn)
        db_module.init_schema(conn)
        count = conn.execute(f"SELECT COUNT(*) FROM {db_module.ATHLETE_SETTINGS_TABLE}").fetchone()[0]
    with TestClient(app) as client:
        second = client.get("/me").json()
    assert count == 1
    assert (second["id"], second["created_at"]) == (first["id"], first["created_at"])


def test_a_version_log_out_of_step_is_a_named_500_that_writes_nothing() -> None:
    """``db.read_hr_anchors`` raises when the log does not hold a served value (only a write outside
    the three write paths causes it). The route answers 500 naming the repair, and does not repair:
    the log row is the one the out-of-band write left."""
    with TestClient(app) as client:
        _upload(client, "sample_run.fit")
        with _connection() as conn:
            conn.execute(
                f"UPDATE {db_module.ANCHOR_VERSIONS_TABLE} SET value = '999' WHERE anchor = 'max_hr_bpm'"
            )
            conn.commit()
        before = _schema_and_rows()
        response = client.get("/me", headers={"accept": "application/json"})
        after = _schema_and_rows()
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "max_hr_bpm" in detail and "restart" in detail, detail
    assert after == before


# ---------------------------------------------------------------------------
# AC8: the contract moves with the route
# ---------------------------------------------------------------------------


def test_the_contract_operation_matches_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"]["/me"]["get"]
    built_op = built["paths"]["/me"]["get"]
    assert op["operationId"] == built_op["operationId"] == "getMe"
    assert op["tags"] == built_op["tags"] == ["Auth & Athlete"]
    assert op["x-readiness"] == "implemented"
    assert "security" not in op, "getMe inherits the global bearerAuth, as the session operations do"
    assert set(op["responses"]) == {"200"}
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Athlete"
    }
    assert built_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Athlete"
    }
    tag = contract["tags"][[t["name"] for t in contract["tags"]].index("Auth & Athlete")]
    assert "GET /me" in tag["description"] and "implemented" in tag["description"]


def test_the_contract_component_matches_the_response_model() -> None:
    contract = _contract()
    components = contract["components"]["schemas"]
    built = app.openapi()["components"]["schemas"]
    athlete = components["Athlete"]
    assert set(athlete["properties"]) == set(built["Athlete"]["properties"]) == ATHLETE_KEYS
    assert set(athlete["required"]) == set(built["Athlete"]["required"]) == ATHLETE_KEYS
    for field, name in ENTRY_COMPONENTS.items():
        assert athlete["properties"][field] == {"$ref": f"#/components/schemas/{name}"}, field
        assert set(components[name]["properties"]) == set(built[name]["properties"]) == ENTRY_KEYS, name
    anchors = athlete["properties"]["anchors"]
    assert anchors == {"$ref": "#/components/schemas/HrAnchors"}
    assert set(components["HrAnchors"]["properties"]) == set(built["HrAnchors"]["properties"])
    assert set(built["HrAnchors"]["properties"]) == set(profile.ANCHOR_FIELDS)
    for field, name in ANCHOR_COMPONENTS.items():
        assert components["HrAnchors"]["properties"][field] == {"$ref": f"#/components/schemas/{name}"}, field
        assert set(components[name]["properties"]) == set(built[name]["properties"]) == ANCHOR_KEYS, name
    assert (
        set(components["UnitPrefs"]["properties"])
        == set(built["UnitPrefs"]["properties"])
        == set(DEFAULT_UNITS)
    )


def test_create_athlete_stays_planned_with_its_own_security() -> None:
    op = _contract()["paths"]["/athletes"]["post"]
    assert op["x-readiness"] == "planned"
    assert op["security"] == []
    assert op["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Athlete"
    }
