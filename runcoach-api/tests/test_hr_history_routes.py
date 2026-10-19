"""F021 AC1: dated HR entries through ``POST``, ``GET`` and ``DELETE /me/hr-history``.

An entry is ``{field, value, effective_from}``: one of resting, max and threshold HR, a strict whole
number of bpm above 0 (``PATCH /me``'s HR screen), and the ``YYYY-MM-DD`` date it took effect, not
after today in ``athlete_timezone``. A second entry for the same field and date replaces the value
and keeps the ``entry_id`` (200, where a new entry is 201), so the store never holds two entries for
one field and date; ``hr_history.borrow`` relies on that. ``GET`` lists the entries by field, then
date; ``DELETE`` removes one (204) or answers 404. Entries change neither ``GET /me`` nor the HR
anchors F016 serves.

Every test enters the app lifespan (``with TestClient(app)``), which runs ``db.init_schema`` against
the isolated store, as uvicorn does at startup.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
COLLECTION = "/me/hr-history"
ITEM = "/me/hr-history/{entry_id}"
ENTRY_KEYS = {"entry_id", "field", "value", "effective_from", "set_at"}


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_walk = _load_support("contract_walk")


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _rows() -> list[tuple]:
    """Every ``hr_history`` row as ``(entry_id, field, value JSON text, effective_from)``, read outside the app."""
    with _connection() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                f"SELECT entry_id, field, value, effective_from FROM {db_module.HR_HISTORY_TABLE} ORDER BY entry_id"
            )
        ]


def _schema_and_rows() -> tuple[list[tuple], dict[str, list[tuple]]]:
    """``sqlite_master`` and every table's rows, read outside the app."""
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}
    return schema, rows


def _zone(monkeypatch: pytest.MonkeyPatch, data_dir: Path, name: str) -> None:
    """Install an ``AppConfig`` whose ``athlete_timezone`` is ``name``, on the isolated store."""
    zoned = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=name,
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: zoned)
    db_module._load_config_cached.cache_clear()


def _clock(monkeypatch: pytest.MonkeyPatch, instant: datetime.datetime) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: instant)


def _post(client: TestClient, field: str, value: object, effective_from: str):
    return client.post(COLLECTION, json={"field": field, "value": value, "effective_from": effective_from})


# ---------------------------------------------------------------------------
# AC1: stored, replaced, listed, deleted
# ---------------------------------------------------------------------------


def test_an_entry_is_201_and_the_same_field_and_date_replaces_with_200() -> None:
    with TestClient(app) as client:
        first = _post(client, "max_hr_bpm", 195, "2016-01-01")
        rows_after_first = _rows()
        second = _post(client, "max_hr_bpm", 191, "2016-01-01")
        other_field = _post(client, "resting_hr_bpm", 48, "2016-01-01")
        other_date = _post(client, "max_hr_bpm", 190, "2016-01-02")
        listed = client.get(COLLECTION).json()
    print(f"  first={first.status_code} {first.text}")
    print(f"  second={second.status_code} {second.text}")
    print(f"  rows={_rows()}")
    assert first.status_code == 201, first.text
    body = first.json()
    assert set(body) == ENTRY_KEYS, body
    assert body["field"] == "max_hr_bpm" and body["value"] == 195 and body["effective_from"] == "2016-01-01"
    assert isinstance(body["entry_id"], int), body
    assert rows_after_first == [(body["entry_id"], "max_hr_bpm", "195", "2016-01-01")]

    assert second.status_code == 200, "the same field and date replaces the entry, which is a 200"
    replaced = second.json()
    assert replaced["entry_id"] == body["entry_id"], "the replacement keeps the entry_id"
    assert replaced["value"] == 191 and replaced["effective_from"] == "2016-01-01"

    assert other_field.status_code == 201 and other_date.status_code == 201
    ids = {body["entry_id"], other_field.json()["entry_id"], other_date.json()["entry_id"]}
    assert len(ids) == 3, "another field on that date, or that field on another date, is a new entry"
    assert _rows() == [
        (body["entry_id"], "max_hr_bpm", "191", "2016-01-01"),
        (other_field.json()["entry_id"], "resting_hr_bpm", "48", "2016-01-01"),
        (other_date.json()["entry_id"], "max_hr_bpm", "190", "2016-01-02"),
    ], "one row per field and date: the store never holds two for the same pair"
    assert [e["value"] for e in listed if e["field"] == "max_hr_bpm"] == [191, 190]


def test_the_store_refuses_a_second_row_for_one_field_and_date() -> None:
    """The replacement rule is the table's own: ``UNIQUE (field, effective_from)``."""
    with TestClient(app):
        pass
    with _connection() as conn:
        conn.execute(
            f"INSERT INTO {db_module.HR_HISTORY_TABLE} (field, value, effective_from, set_at) "
            "VALUES ('max_hr_bpm', '195', '2016-01-01', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {db_module.HR_HISTORY_TABLE} (field, value, effective_from, set_at) "
                "VALUES ('max_hr_bpm', '191', '2016-01-01', 'x')"
            )
    assert db_module.HR_HISTORY_TABLE not in db_module._child_tables(), "delete_session never sweeps an entry"


def test_the_list_is_by_field_then_date() -> None:
    with TestClient(app) as client:
        for field, value, effective_from in (
            ("threshold_hr_bpm", 170, "2020-01-01"),
            ("max_hr_bpm", 191, "2021-01-01"),
            ("resting_hr_bpm", 50, "2018-06-01"),
            ("max_hr_bpm", 195, "2016-01-01"),
            ("resting_hr_bpm", 46, "2015-03-01"),
        ):
            assert _post(client, field, value, effective_from).status_code == 201
        response = client.get(COLLECTION)
    assert response.status_code == 200, response.text
    listed = response.json()
    print(f"  listed={[(e['field'], e['effective_from']) for e in listed]}")
    assert all(set(e) == ENTRY_KEYS for e in listed), listed
    assert [(e["field"], e["effective_from"], e["value"]) for e in listed] == [
        ("max_hr_bpm", "2016-01-01", 195),
        ("max_hr_bpm", "2021-01-01", 191),
        ("resting_hr_bpm", "2015-03-01", 46),
        ("resting_hr_bpm", "2018-06-01", 50),
        ("threshold_hr_bpm", "2020-01-01", 170),
    ]


def test_an_empty_list_is_200() -> None:
    with TestClient(app) as client:
        response = client.get(COLLECTION)
    assert response.status_code == 200 and response.json() == []


def test_delete_removes_one_entry_with_204_and_an_unknown_id_is_404() -> None:
    with TestClient(app) as client:
        kept = _post(client, "max_hr_bpm", 195, "2016-01-01").json()
        gone = _post(client, "max_hr_bpm", 191, "2021-01-01").json()
        deleted = client.delete(f"{COLLECTION}/{gone['entry_id']}")
        again = client.delete(f"{COLLECTION}/{gone['entry_id']}")
        unknown = client.delete(f"{COLLECTION}/999999")
        listed = client.get(COLLECTION).json()
    print(f"  deleted={deleted.status_code} again={again.status_code} {again.text} unknown={unknown.status_code}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert again.status_code == 404 and str(gone["entry_id"]) in again.json()["detail"], again.text
    assert unknown.status_code == 404, unknown.text
    assert [e["entry_id"] for e in listed] == [kept["entry_id"]]
    assert _rows() == [(kept["entry_id"], "max_hr_bpm", "195", "2016-01-01")]


@pytest.mark.parametrize("entry_id", [2**63, 2**70, -(2**63) - 1, 2**63 - 1, -(2**63)])
def test_an_entry_id_at_or_past_the_store_integer_range_is_404(entry_id: int) -> None:
    """An id no row can carry (past SQLite's signed 64-bit range) is unknown, like any other: 404."""
    with TestClient(app) as client:
        kept = _post(client, "max_hr_bpm", 195, "2016-01-01").json()
        response = client.delete(f"{COLLECTION}/{entry_id}")
    print(f"  {entry_id}: {response.status_code} {response.text}")
    assert response.status_code == 404, response.text
    assert str(entry_id) in response.json()["detail"]
    assert [row[0] for row in _rows()] == [kept["entry_id"]]


def test_a_non_integer_entry_id_is_422() -> None:
    with TestClient(app) as client:
        response = client.delete(f"{COLLECTION}/abc")
    assert response.status_code == 422, response.text


def test_a_value_patch_me_accepts_is_stored_exactly() -> None:
    """``PATCH /me`` takes 10**400; the value is JSON text, as ``profile_entries`` keeps it, not an INTEGER."""
    huge = 10**400
    with TestClient(app) as client:
        response = client.post(
            COLLECTION,
            content=f'{{"field": "max_hr_bpm", "value": {huge}, "effective_from": "2016-01-01"}}',
            headers={"content-type": "application/json"},
        )
        listed = client.get(COLLECTION)
    assert response.status_code == 201, response.text
    assert json.loads(listed.text)[0]["value"] == huge
    assert _rows()[0][2] == str(huge)


def test_an_effective_date_of_today_in_the_athlete_timezone_is_stored(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """12:00 UTC on 2026-10-07 is already 2026-10-08 in Kiritimati (UTC+14), so 2026-10-08 is today there."""
    _zone(monkeypatch, isolated_data_dir, "Pacific/Kiritimati")
    _clock(monkeypatch, datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.UTC))
    with TestClient(app) as client:
        response = _post(client, "resting_hr_bpm", 47, "2026-10-08")
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# AC1: each rejected row of the Negative Class is a 422 that writes nothing
# ---------------------------------------------------------------------------


def _assert_422(client: TestClient, loc_field: str, *, json: object = None, content: bytes | None = None) -> None:
    before = _schema_and_rows()
    if content is None:
        response = client.post(COLLECTION, json=json)
    else:
        response = client.post(COLLECTION, content=content, headers={"content-type": "application/json"})
    after = _schema_and_rows()
    print(f"  {response.status_code} {response.text}")
    assert response.status_code == 422, response.text
    locs = [error["loc"] for error in response.json()["detail"]]
    assert any(loc_field in loc for loc in locs), locs
    assert after == before, "a rejected body writes no row"


def test_an_unknown_field_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "field", json={"field": "sex", "value": 1, "effective_from": "2016-01-01"})
        _assert_422(client, "field", json={"field": "body_mass_kg", "value": 70, "effective_from": "2016-01-01"})


def test_an_extra_key_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(
            client, "note", json={"field": "max_hr_bpm", "value": 190, "effective_from": "2016-01-01", "note": "x"}
        )


def test_a_true_value_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "value", json={"field": "max_hr_bpm", "value": True, "effective_from": "2016-01-01"})


def test_a_string_value_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "value", json={"field": "max_hr_bpm", "value": "188", "effective_from": "2016-01-01"})


@pytest.mark.parametrize("value", [188.5, 188.0])
def test_a_fractional_value_is_422(value: float) -> None:
    with TestClient(app) as client:
        _assert_422(client, "value", json={"field": "max_hr_bpm", "value": value, "effective_from": "2016-01-01"})


@pytest.mark.parametrize("value", [0, -188])
def test_a_value_of_zero_or_less_is_422(value: int) -> None:
    with TestClient(app) as client:
        _assert_422(
            client, "value", json={"field": "threshold_hr_bpm", "value": value, "effective_from": "2016-01-01"}
        )


def test_a_missing_value_is_422() -> None:
    with TestClient(app) as client:
        _assert_422(client, "value", json={"field": "max_hr_bpm", "effective_from": "2016-01-01"})


@pytest.mark.parametrize(
    "value",
    ["2016-13-01", "2016-02-30", "20160101", "2016-01-01T00:00:00", "Jan 1 2016", 20160101, None],
    ids=["month-13", "feb-30", "basic-format", "date-time", "prose", "integer", "null"],
)
def test_a_malformed_effective_date_is_422(value: object) -> None:
    with TestClient(app) as client:
        _assert_422(client, "effective_from", json={"field": "max_hr_bpm", "value": 190, "effective_from": value})


def test_an_effective_date_after_today_is_422(monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path) -> None:
    """05:00 UTC on 2026-10-07 is still 2026-10-06 in Pago Pago (UTC-11), so 2026-10-07 lies after
    today there, though it is today in UTC."""
    _zone(monkeypatch, isolated_data_dir, "Pacific/Pago_Pago")
    _clock(monkeypatch, datetime.datetime(2026, 10, 7, 5, 0, tzinfo=datetime.UTC))
    with TestClient(app) as client:
        _assert_422(
            client, "effective_from", json={"field": "max_hr_bpm", "value": 190, "effective_from": "2026-10-07"}
        )
        accepted = _post(client, "max_hr_bpm", 190, "2026-10-06")
    assert accepted.status_code == 201, "today in the athlete's timezone is not after today"


def test_an_after_today_replacement_is_422_and_keeps_the_stored_value(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    _zone(monkeypatch, isolated_data_dir, "UTC")
    _clock(monkeypatch, datetime.datetime(2026, 10, 7, 5, 0, tzinfo=datetime.UTC))
    with TestClient(app) as client:
        stored = _post(client, "max_hr_bpm", 190, "2026-10-07").json()
        _clock(monkeypatch, datetime.datetime(2026, 10, 6, 5, 0, tzinfo=datetime.UTC))
        _assert_422(
            client, "effective_from", json={"field": "max_hr_bpm", "value": 185, "effective_from": "2026-10-07"}
        )
    assert _rows() == [(stored["entry_id"], "max_hr_bpm", "190", "2026-10-07")]


# ---------------------------------------------------------------------------
# AC1: GET /me and F016's anchors are unchanged by entries
# ---------------------------------------------------------------------------


def test_entries_leave_get_me_and_the_anchors_unchanged() -> None:
    with TestClient(app) as client:
        assert client.patch("/me", json={"max_hr_bpm": 188, "resting_hr_bpm": 50}).status_code == 200
        me_before = client.get("/me").json()
        with _connection() as conn:
            anchors_before = db_module.read_hr_anchors(conn)
        posted = [
            _post(client, "max_hr_bpm", 195, "2016-01-01").json(),
            _post(client, "resting_hr_bpm", 44, "2016-01-01").json(),
            _post(client, "threshold_hr_bpm", 171, "2025-01-01").json(),
        ]
        _post(client, "max_hr_bpm", 199, "2016-01-01")
        me_after_posts = client.get("/me").json()
        with _connection() as conn:
            anchors_after_posts = db_module.read_hr_anchors(conn)
        for entry in posted:
            assert client.delete(f"{COLLECTION}/{entry['entry_id']}").status_code == 204
        me_after_deletes = client.get("/me").json()
        with _connection() as conn:
            anchors_after_deletes = db_module.read_hr_anchors(conn)
    print(f"  anchors={anchors_before}")
    assert me_before["anchors"]["max_hr_bpm"]["value"] == 188, me_before["anchors"]
    assert me_before["threshold_hr_bpm"]["unavailable"] == "missing", "the entry did not fill an empty field"
    assert me_after_posts == me_before, "a dated entry does not change GET /me"
    assert me_after_deletes == me_before, "deleting a dated entry does not change GET /me"
    assert anchors_after_posts == anchors_before
    assert anchors_after_deletes == anchors_before


# ---------------------------------------------------------------------------
# AC7's part for these routes: the contract operations are exact to the served ones
# ---------------------------------------------------------------------------


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def test_hr_history_contract_matches_served_routes() -> None:
    contract = _contract()
    built = app.openapi()
    expected = {
        (COLLECTION, "post"): ("addHrHistoryEntry", {"200", "201", "422"}),
        (COLLECTION, "get"): ("listHrHistory", {"200"}),
        (ITEM, "delete"): ("deleteHrHistoryEntry", {"204", "404", "422"}),
    }
    for path in (COLLECTION, ITEM):
        served = {m for m in built["paths"][path] if m in {"get", "post", "put", "patch", "delete"}}
        documented = {m for m in contract["paths"][path] if m in {"get", "post", "put", "patch", "delete"}}
        assert served == documented == {m for p, m in expected if p == path}, (path, served, documented)
    for (path, method), (operation_id, codes) in expected.items():
        op, built_op = contract["paths"][path][method], built["paths"][path][method]
        print(f"  {method.upper()} {path}: contract {sorted(op['responses'])} served {sorted(built_op['responses'])}")
        assert op["operationId"] == built_op["operationId"] == operation_id
        assert op["tags"] == built_op["tags"] == ["Auth & Athlete"]
        assert op["x-readiness"] == "implemented"
        assert set(op["responses"]) == set(built_op["responses"]) == codes, (path, method)
        for code in codes - {"204", "404", "422"}:
            assert code.startswith("2")
    post, built_post = contract["paths"][COLLECTION]["post"], built["paths"][COLLECTION]["post"]
    for doc, operation in ((contract, post), (built, built_post)):
        assert operation["requestBody"]["required"] is True, doc is contract
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/HrHistoryEntryCreate"
        }, doc is contract
        for code in ("200", "201"):
            assert operation["responses"][code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/HrHistoryEntry"
            }, (doc is contract, code)
    listed = contract_walk.normalised(
        contract["paths"][COLLECTION]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    )
    served_list = contract_walk.normalised(
        built["paths"][COLLECTION]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    )
    assert listed == served_list == {"nullable": False, "type": "array", "items": {"nullable": False, "ref": "HrHistoryEntry"}}
    param = contract["paths"][ITEM]["delete"]["parameters"][0]
    built_param = built["paths"][ITEM]["delete"]["parameters"][0]
    assert param["name"] == built_param["name"] == "entry_id"
    assert param["in"] == built_param["in"] == "path" and param["required"] is True
    assert param["schema"]["type"] == built_param["schema"]["type"] == "integer"

    seen, compared = contract_walk.walk(contract, built, ["HrHistoryEntry", "HrHistoryEntryCreate"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {"HrHistoryEntry", "HrHistoryEntryCreate"}, seen
    create = contract["components"]["schemas"]["HrHistoryEntryCreate"]
    assert create["additionalProperties"] is False
    assert set(create["required"]) == {"field", "value", "effective_from"}
    assert create["properties"]["value"]["exclusiveMinimum"] == 0
    assert sorted(create["properties"]["field"]["enum"]) == ["max_hr_bpm", "resting_hr_bpm", "threshold_hr_bpm"]
    assert set(contract["components"]["schemas"]["HrHistoryEntry"]["required"]) == ENTRY_KEYS


# ---------------------------------------------------------------------------
# A second writer of the same field and date while a POST is writing
# ---------------------------------------------------------------------------


def test_an_entry_written_by_another_connection_mid_write_is_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """``write_hr_history_entry`` takes the write lock before it reads whether the pair exists, so
    another connection writing the same field and date in the middle of the POST is refused at its
    insert ("database is locked") and the POST answers 201 with the store holding one row. Without
    the lock, that writer's row lands between the read and the insert and the POST's insert breaks
    ``UNIQUE (field, effective_from)``: a 500. With only a deferred ``BEGIN``, the read takes a
    shared lock that lets the writer insert and refuses it only at its commit, after the read: the
    lock is not held from the read on."""
    with TestClient(app, raise_server_exceptions=False) as client:
        real = db_module._json_dump
        outcomes: list[str] = []

        def second_writer(value):
            if not outcomes:
                other = sqlite3.connect(db_module._load_config_cached().data_dir / db_module.DB_FILENAME, timeout=0)
                stage = "insert"
                try:
                    other.execute(
                        f"INSERT INTO {db_module.HR_HISTORY_TABLE} (field, value, effective_from, set_at) "
                        "VALUES ('max_hr_bpm', '191', '2016-01-01', 'other')"
                    )
                    stage = "commit"
                    other.commit()
                except sqlite3.OperationalError as exc:
                    other.rollback()
                    outcomes.append(f"refused at its {stage}: {exc}")
                else:
                    outcomes.append("written")
                finally:
                    other.close()
            return real(value)

        monkeypatch.setattr(db_module, "_json_dump", second_writer)
        response = _post(client, "max_hr_bpm", 195, "2016-01-01")
    print(f"  POST: {response.status_code} {response.text}")
    print(f"  second writer: {outcomes}")
    print(f"  rows={_rows()}")
    assert response.status_code != 500, response.text
    assert response.status_code == 201, response.text
    assert _rows() == [(response.json()["entry_id"], "max_hr_bpm", "195", "2016-01-01")]
    assert outcomes == ["refused at its insert: database is locked"], outcomes
