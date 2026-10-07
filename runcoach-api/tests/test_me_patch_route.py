"""``PATCH /me`` and its contract entry (F016 AC7's API part, AC8's ``updateMe`` part).

The route takes a partial object: any of the entered profile fields
(``profile.ENTERED_FIELDS``) plus ``display_name`` and ``units``. Each profile
field present writes one entered-value row through ``db.write_profile_entries``
(``null`` writes a clear), in the same transaction as the settings change and
the anchor version log; ``{}`` writes nothing. The response is the ``GET /me``
shape, read after the write.

Every rejected body is a 422 that writes no row. The screens are the
reference's: an unknown field; an HR value that is not a strict whole number
above 0; a body value that is not a finite number above 0 (a JSON ``true`` is
not a number); a ``sex`` outside the enum; a ``birth_date`` that is not a
``YYYY-MM-DD`` date or lies after today in ``athlete_timezone``. Ordering
across fields is not screened here; it is the lookup's rule, and ``GET /me``
shows a conflicting entry as ``order_conflict``.

Every test enters the app lifespan (``with TestClient(app)``), which runs
``db.init_schema`` against the isolated store, as uvicorn does at startup.
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api import profile
from runcoach_api.config import AppConfig
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
UPDATE_KEYS = {"display_name", "units", *profile.ENTERED_FIELDS}


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _entries() -> list[tuple]:
    """Every entered-value row as ``(field, value JSON text)``, in write order."""
    with _connection() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                f"SELECT field, value FROM {db_module.PROFILE_ENTRIES_TABLE} ORDER BY entry_id"
            )
        ]


def _schema_and_rows() -> tuple[list[tuple], dict[str, list[tuple]]]:
    """``sqlite_master`` and every table's rows, read outside the app."""
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}
    return schema, rows


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _zone(monkeypatch: pytest.MonkeyPatch, data_dir: Path, name: str) -> None:
    """Install an ``AppConfig`` whose ``athlete_timezone`` is ``name``, on the isolated store."""
    zoned = AppConfig(
        host="127.0.0.1", port=8000, data_dir=data_dir, resting_hrv_profile_names=[], athlete_timezone=name
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: zoned)
    db_module._load_config_cached.cache_clear()


def _clock(monkeypatch: pytest.MonkeyPatch, instant: datetime.datetime) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: instant)


# ---------------------------------------------------------------------------
# AC7: entries are stored, cleared, or nothing is written
# ---------------------------------------------------------------------------


def test_patch_me_stores_one_entry_per_field() -> None:
    with TestClient(app) as client:
        response = client.patch("/me", json={"max_hr_bpm": 190, "body_mass_kg": 72.5, "sex": "male"})
        got = client.get("/me").json()
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"  entries={_entries()}")
    assert sorted(_entries()) == [("body_mass_kg", "72.5"), ("max_hr_bpm", "190"), ("sex", '"male"')]
    assert body == got, "PATCH /me returns the GET /me shape, read after the write"
    for field, value in (("max_hr_bpm", 190), ("body_mass_kg", 72.5), ("sex", "male")):
        assert body[field]["value"] == value and body[field]["source"] == "entered", body[field]
        assert body[field]["entered_value"] == value, body[field]
    assert body["anchors"]["max_hr_bpm"]["value"] == 190 and body["anchors"]["max_hr_bpm"]["version"] == 1
    assert body["resting_hr_bpm"]["unavailable"] == "missing"


def test_patch_me_stores_every_entered_field_with_one_set_time() -> None:
    values = {
        "sex": "female",
        "birth_date": "1980-05-01",
        "body_mass_kg": 60,
        "height_cm": 170.5,
        "resting_hr_bpm": 45,
        "max_hr_bpm": 185,
        "threshold_hr_bpm": 165,
    }
    with TestClient(app) as client:
        body = client.patch("/me", json=values).json()
    assert len(_entries()) == len(profile.ENTERED_FIELDS)
    assert {field: body[field]["value"] for field in profile.ENTERED_FIELDS} == {
        **values,
        "body_mass_kg": 60.0,
    }
    assert len({body[field]["recorded_at"] for field in profile.ENTERED_FIELDS}) == 1


def test_patch_me_null_clears_a_field_with_a_null_row() -> None:
    """A clear writes a row whose value is NULL; the field falls to unavailable, not to the earlier entry."""
    with TestClient(app) as client:
        client.patch("/me", json={"max_hr_bpm": 190})
        response = client.patch("/me", json={"max_hr_bpm": None})
    assert response.status_code == 200, response.text
    with _connection() as conn:
        rows = [tuple(r) for r in conn.execute(f"SELECT field, value FROM {db_module.PROFILE_ENTRIES_TABLE}")]
    print(f"  rows={rows}")
    assert rows == [("max_hr_bpm", "190"), ("max_hr_bpm", None)]
    body = response.json()
    assert body["max_hr_bpm"]["unavailable"] == "missing" and body["max_hr_bpm"]["entered_value"] is None
    assert body["anchors"]["max_hr_bpm"]["unavailable"] == "missing"


def test_patch_me_clear_falls_to_the_fit_value() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
        client.patch("/me", json={"max_hr_bpm": 195})
        body = client.patch("/me", json={"max_hr_bpm": None}).json()
    assert body["max_hr_bpm"]["value"] == 188 and body["max_hr_bpm"]["session_id"] == session_id
    assert body["max_hr_bpm"]["entered_value"] is None


def test_patch_me_empty_object_is_a_noop_200() -> None:
    with TestClient(app) as client:
        _upload(client, "sample_run.fit")
        client.patch("/me", json={"max_hr_bpm": 195, "display_name": "Chris"})
        before = _schema_and_rows()
        response = client.patch("/me", json={})
        after = _schema_and_rows()
        got = client.get("/me").json()
    assert response.status_code == 200, response.text
    assert after == before
    assert response.json() == got


def test_patch_me_updates_display_name_and_units() -> None:
    units = {"distance": "mi", "pace": "min_per_mi", "temperature": "f"}
    with TestClient(app) as client:
        first = client.get("/me").json()
        response = client.patch("/me", json={"display_name": "Chris", "units": units})
        got = client.get("/me").json()
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["display_name"], body["units"]) == ("Chris", units)
    assert (got["display_name"], got["units"]) == ("Chris", units)
    assert (body["id"], body["created_at"]) == (first["id"], first["created_at"])
    assert _entries() == [], "display_name and units are settings, not entered profile values"


def test_patch_me_null_display_name_clears_it() -> None:
    with TestClient(app) as client:
        client.patch("/me", json={"display_name": "Chris"})
        body = client.patch("/me", json={"display_name": None}).json()
    assert body["display_name"] is None


def test_patch_me_conflicting_entry_shows_as_order_conflict() -> None:
    """A snapshot supplies resting HR from the file; an entered max HR below it conflicts. The entry is
    stored (ordering is not screened at the route) and both anchors read ``order_conflict``."""
    with TestClient(app) as client:
        _upload(client, "sample_health_snapshot.fit")
        response = client.patch("/me", json={"max_hr_bpm": 30})
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"  resting={body['resting_hr_bpm']} anchors={body['anchors']}")
    assert body["resting_hr_bpm"]["source"] == "fit" and body["resting_hr_bpm"]["value"] > 30
    assert body["max_hr_bpm"]["value"] == 30 and body["max_hr_bpm"]["source"] == "entered"
    assert body["anchors"]["resting_hr_bpm"]["unavailable"] == "order_conflict"
    assert body["anchors"]["max_hr_bpm"]["unavailable"] == "order_conflict"


# ---------------------------------------------------------------------------
# AC7: every rejected body is a 422 that writes nothing
# ---------------------------------------------------------------------------


def _assert_422(client: TestClient, field: str, *, json: object = None, content: bytes | None = None) -> None:
    before = _schema_and_rows()
    if content is None:
        response = client.patch("/me", json=json)
    else:
        response = client.patch("/me", content=content, headers={"content-type": "application/json"})
    after = _schema_and_rows()
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 422, response.text
    locs = [error["loc"] for error in response.json()["detail"]]
    assert any(field in loc for loc in locs), locs
    assert after == before, "a rejected body writes no row"


def test_patch_me_unknown_field_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "weight_kg", json={"max_hr_bpm": 190, "weight_kg": 70})


def test_patch_me_hr_true_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "max_hr_bpm", json={"max_hr_bpm": True})


def test_patch_me_hr_string_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "max_hr_bpm", json={"max_hr_bpm": "188"})


@pytest.mark.parametrize("value", [188.5, 188.0])
def test_patch_me_hr_fraction_is_422(value: float) -> None:
    with TestClient(app) as client:
        _assert_422(client, "resting_hr_bpm", json={"resting_hr_bpm": value})


def test_patch_me_hr_zero_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "threshold_hr_bpm", json={"threshold_hr_bpm": 0})


def test_patch_me_negative_hr_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "max_hr_bpm", json={"max_hr_bpm": -188})


@pytest.mark.parametrize(
    ("field", "value"), [("body_mass_kg", 0), ("height_cm", -180.0), ("body_mass_kg", True)]
)
def test_patch_me_non_positive_body_value_is_422(field: str, value: object) -> None:
    """0 and a negative number are non-positive; a JSON ``true`` is not a number (lax float takes it as 1.0)."""
    with TestClient(app) as client:
        _assert_422(client, field, json={field: value})


@pytest.mark.parametrize(
    ("field", "token"), [("body_mass_kg", "NaN"), ("height_cm", "Infinity"), ("body_mass_kg", "-Infinity")]
)
def test_patch_me_non_finite_body_value_raw_body_is_422(field: str, token: str) -> None:
    """Sent as a raw body: a JSON encoder will not write the token, but the request parser reads it."""
    with TestClient(app) as client:
        _assert_422(client, field, content=f'{{"{field}": {token}}}'.encode())


def test_patch_me_sex_outside_the_enum_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "sex", json={"sex": "other"})


@pytest.mark.parametrize(
    "value",
    ["1980-13-01", "19800501", "1980-05-01T00:00:00", "May 1 1980", 19800501],
    ids=["month-13", "basic-format", "date-time", "prose", "integer"],
)
def test_patch_me_malformed_birth_date_is_422(value: object) -> None:
    with TestClient(app) as client:
        _assert_422(client, "birth_date", json={"birth_date": value})


def test_patch_me_birth_date_after_today_in_the_athlete_timezone_is_422(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """05:00 UTC on 2026-10-07 is still 2026-10-06 in Pago Pago (UTC-11), so 2026-10-07 lies after
    today there, though it is today in UTC."""
    _zone(monkeypatch, isolated_data_dir, "Pacific/Pago_Pago")
    _clock(monkeypatch, datetime.datetime(2026, 10, 7, 5, 0, tzinfo=datetime.UTC))
    with TestClient(app) as client:
        _assert_422(client, "birth_date", json={"birth_date": "2026-10-07"})


def test_patch_me_birth_date_today_in_the_athlete_timezone_is_accepted(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """12:00 UTC on 2026-10-07 is already 2026-10-08 in Kiritimati (UTC+14), so 2026-10-08 is today
    there and is stored, though it lies after today in UTC."""
    _zone(monkeypatch, isolated_data_dir, "Pacific/Kiritimati")
    _clock(monkeypatch, datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.UTC))
    with TestClient(app) as client:
        response = client.patch("/me", json={"birth_date": "2026-10-08"})
    assert response.status_code == 200, response.text
    assert response.json()["birth_date"]["value"] == "2026-10-08"


def test_patch_me_unknown_units_key_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(
            client, "distanc", json={"units": {"distanc": "mi", "pace": "min_per_mi", "temperature": "f"}}
        )


def test_patch_me_null_units_is_422() -> None:
    """``units`` has no clear: the settings row always holds them."""
    with TestClient(app) as client:
        _assert_422(client, "units", json={"units": None})


# ---------------------------------------------------------------------------
# AC8: the contract moves with the route
# ---------------------------------------------------------------------------


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def test_the_patch_contract_operation_matches_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"]["/me"]["patch"]
    built_op = built["paths"]["/me"]["patch"]
    assert op["operationId"] == built_op["operationId"] == "updateMe"
    assert op["tags"] == built_op["tags"] == ["Auth & Athlete"]
    assert op["x-readiness"] == "implemented"
    assert "security" not in op, "updateMe inherits the global bearerAuth, as the session operations do"
    assert set(op["responses"]) == {"200", "422"}
    assert set(built_op["responses"]) == {"200", "422"}
    for doc, operation in ((contract, op), (built, built_op)):
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/AthleteProfileUpdate"
        }, doc is contract
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Athlete"
        }, doc is contract
    assert op["requestBody"]["required"] is True and built_op["requestBody"]["required"] is True


def test_the_patch_contract_component_matches_the_request_model() -> None:
    contract = _contract()["components"]["schemas"]["AthleteProfileUpdate"]
    built = app.openapi()["components"]["schemas"]["AthleteProfileUpdate"]
    assert set(contract["properties"]) == set(built["properties"]) == UPDATE_KEYS
    assert not contract.get("required") and not built.get("required")
    assert contract["additionalProperties"] is False and built["additionalProperties"] is False
    assert contract["properties"]["units"] == {"$ref": "#/components/schemas/UnitPrefs"}
    assert contract["properties"]["sex"]["enum"] == ["female", "male", "unspecified"]
    for field in ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm"):
        assert contract["properties"][field]["type"] == "integer", field
        assert contract["properties"][field]["exclusiveMinimum"] == 0, field
    for field in ("body_mass_kg", "height_cm"):
        assert contract["properties"][field]["type"] == "number", field
        assert contract["properties"][field]["exclusiveMinimum"] == 0, field
    assert contract["properties"]["birth_date"]["format"] == "date"
