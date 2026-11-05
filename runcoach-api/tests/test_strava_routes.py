"""F030 AC9 and the serving halves of AC2, AC5 and AC7: ``GET /strava-activities/{activity_id}`` and
the ``strava`` block of ``GET /sessions/{id}`` (F030 reference section 4).

Every row is written by the real import: each test posts a Strava archive built in ``tmp_path`` by
``tests/support/strava_archive.py`` to ``POST /imports`` under ``with TestClient(app)`` against the
autouse ``isolated_data_dir`` store, drains the worker, then reads the routes. Sessions a row links
to are uploaded first through ``POST /sessions`` with the catalogue's copies of ``sample_run``
(README provenance: real run, run proof yes).

The expected values are the CSV cells the test wrote, compared with what the routes serve; the
section 4 key sets are written out here from the reference, not read from the code.

**Degenerate inputs** (each driven through the route):

| input | served |
|---|---|
| an Activity ID no row has | 404 ``{"detail": "no_strava_activity"}`` |
| an id past the 64-bit range (2**63), and -1 | 404, the same body; nothing is read past the range |
| a non-integer id (``abc``) | 422, FastAPI's path validation |
| a session with no linked row | ``strava`` null |
| a session whose only row is ``deleted_in_strava`` | ``strava`` null |
| a newline, a CR LF and a quote inside a cell | served as the CSV held them |
| an empty cell | ``""`` |
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import job
from runcoach_api.main import app

SUPPORT = Path(__file__).parent / "support"
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
DRAIN_TIMEOUT_S = 120.0

# Reference section 4, written out: the row's served fields and the session's block.
STRAVA_ACTIVITY_KEYS = {
    "activity_id",
    "start_time",
    "activity_type",
    "name",
    "description",
    "private_note",
    "filename",
    "columns",
    "linked_session_id",
    "match",
    "warnings",
    "deleted_in_strava",
}
SESSION_STRAVA_KEYS = {"activity_id", "activity_type", "name", "description", "private_note", "match", "warnings"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SA = _load("strava_archive")
CV = _load("contract_validate")
CW = _load("contract_walk")


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _import(client: TestClient, path: Path) -> dict:
    with path.open("rb") as fh:
        started = client.post("/imports", files={"file": (path.name, fh, "application/zip")})
    assert started.status_code == 202, started.text
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"
    done = client.get(f"/imports/{started.json()['import_id']}")
    assert done.status_code == 200, done.text
    report = done.json()
    assert report["status"] == "completed", report["error"]
    return report


def _upload(client: TestClient, files) -> dict[str, str]:
    ids = {}
    for name, raw in files:
        response = client.post("/sessions", files={"file": (name, raw)})
        assert response.status_code == 201, (name, response.text)
        ids[name] = response.json()["session_id"]
    return ids


def _id(case) -> int:
    return int(case.cells["Activity ID"])


def _kept_cases():
    """The linked run (its first ``Distance`` set apart from its second, its description empty), a
    linked run with the distance warning, and three rides whose cells hold a newline, a CR LF and a
    quote."""
    cases = [SA.by_key(k) for k in ("attached", "attached_distance_differs", "lf_cell", "crlf_cell", "quote_cell")]
    attached = dict(cases[0].cells)
    attached.update(
        {
            "Distance": "6.21",
            "Activity Name": "Morning Run",
            "Activity Description": "",
            "Activity Private Note": "left calf tight",
            "Relative Effort": "57",
        }
    )
    cases[0] = cases[0]._replace(cells=attached)
    return cases


def _import_kept(client: TestClient, tmp_path: Path, name: str = "kept.zip", cases=None):
    cases = _kept_cases() if cases is None else cases
    ids = _upload(client, SA.stored_files(cases))
    path = SA.strava_zip(tmp_path / name, [c.cells for c in cases], [c.file for c in cases if c.file])
    return _import(client, path), ids, cases


# --- getStravaActivity --------------------------------------------------------------------------


def test_get_strava_activity_serves_every_column(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _report, ids, cases = _import_kept(client, tmp_path)
        response = client.get(f"/strava-activities/{_id(cases[0])}")
    assert response.status_code == 200, response.text
    body = response.json()
    print("  served keys:", sorted(body), "columns:", len(body["columns"]))
    assert set(body) == STRAVA_ACTIVITY_KEYS
    cells = cases[0].cells
    # Every header cell as text, in header order, keyed per reference section 2.3.
    assert list(body["columns"]) == list(SA.KEYS)
    assert body["columns"] == dict(cells)
    assert all(isinstance(v, str) for v in body["columns"].values())
    assert body["activity_id"] == _id(cases[0])
    assert body["activity_type"] == "Run"
    assert body["name"] == "Morning Run"
    assert body["description"] == ""
    assert body["private_note"] == "left calf tight"
    assert body["filename"] == cells["Filename"]
    assert body["start_time"] == SA.session_start(cases[0].stored[0][1]).isoformat()
    assert body["linked_session_id"] == ids["attached.fit"]
    assert (body["match"], body["warnings"], body["deleted_in_strava"]) == ("csv_start", [], False)
    # The display-only score is served as the text the CSV held.
    assert body["columns"]["Relative Effort"] == "57"


def test_a_newline_cell_is_served_intact_and_an_empty_cell_blank(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _report, _ids, cases = _import_kept(client, tmp_path)
        by_key = {c.key: client.get(f"/strava-activities/{_id(c)}").json() for c in cases}
    lf, crlf, quote = by_key["lf_cell"], by_key["crlf_cell"], by_key["quote_cell"]
    assert lf["description"] == lf["columns"]["Activity Description"] == "Bailed due to calf problems.\nWalked home."
    assert crlf["private_note"] == crlf["columns"]["Activity Private Note"] == "Foot pain\r\nblew up"
    assert quote["name"] == quote["columns"]["Activity Name"] == 'The "easy" ride, mostly'
    # Empty cells are "" (never null, never dropped): every key is present on every row.
    attached = by_key["attached"]
    assert attached["columns"]["Activity Description"] == "" and attached["description"] == ""
    assert lf["columns"]["Filename"] == "" and lf["columns"]["Activity Gear"] == ""
    for body in by_key.values():
        assert list(body["columns"]) == list(SA.KEYS)
    # A ride links nothing and has no match.
    assert (lf["linked_session_id"], lf["match"], lf["activity_type"]) == (None, None, "Ride")


def test_repeated_distance_columns_serve_two_values(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _report, _ids, cases = _import_kept(client, tmp_path)
        body = client.get(f"/strava-activities/{_id(cases[0])}").json()
    print("  Distance:", body["columns"]["Distance"], "Distance (2):", body["columns"]["Distance (2)"])
    assert (body["columns"]["Distance"], body["columns"]["Distance (2)"]) == ("6.21", "10000.0")
    for name in SA.REPEATED:
        assert name in body["columns"] and f"{name} (2)" in body["columns"], name
    assert "Distance (3)" not in body["columns"]


def test_get_strava_activity_404_for_an_unknown_id(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _report, _ids, cases = _import_kept(client, tmp_path)
        known = {_id(c) for c in cases}
        unknown = max(known) + 1
        assert unknown not in known
        for activity_id in (unknown, -1, 2**63, 10**30):
            response = client.get(f"/strava-activities/{activity_id}")
            assert response.status_code == 404, (activity_id, response.text)
            assert response.json() == {"detail": "no_strava_activity"}, activity_id
        assert client.get("/strava-activities/abc").status_code == 422
        # The known row is still served: the 404 is the id, not the route.
        assert client.get(f"/strava-activities/{min(known)}").status_code == 200


# --- the session's strava block ------------------------------------------------------------------


def test_session_detail_serves_its_strava_block(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _report, ids, cases = _import_kept(client, tmp_path)
        linked = client.get(f"/sessions/{ids['attached.fit']}").json()
        differs = client.get(f"/sessions/{ids['differs.fit']}").json()
    print("  strava:", linked["strava"])
    assert set(linked["strava"]) == SESSION_STRAVA_KEYS
    assert linked["strava"] == {
        "activity_id": _id(cases[0]),
        "activity_type": "Run",
        "name": "Morning Run",
        "description": "",
        "private_note": "left calf tight",
        "match": "csv_start",
        "warnings": [],
    }
    assert differs["strava"]["activity_id"] == _id(cases[1])
    assert differs["strava"]["warnings"] == ["distance_differs"]


def test_session_detail_strava_is_null_without_a_link(tmp_path: Path, isolated_data_dir: Path) -> None:
    with TestClient(app) as client:
        _report, ids, cases = _import_kept(client, tmp_path)
        # A session no row links: the pre-feature upload path.
        (unlinked,) = _upload(client, [("plain.fit", SA.shifted(SA.fixture("sample_run"), -400 * SA.DAY_S))]).values()
        assert "strava" in client.get(f"/sessions/{unlinked}").json()
        assert client.get(f"/sessions/{unlinked}").json()["strava"] is None
        # A row marked deleted in Strava that still names the session is not served.
        conn = db.get_connection(isolated_data_dir)
        try:
            row = db.read_strava_row(conn, _id(cases[1]))
            row["deleted_in_strava"] = True
            db.write_strava_row(conn, row)
            conn.commit()
        finally:
            conn.close()
        deleted = client.get(f"/sessions/{ids['differs.fit']}").json()
        still = client.get(f"/sessions/{ids['attached.fit']}").json()
        served_row = client.get(f"/strava-activities/{_id(cases[1])}").json()
    assert deleted["strava"] is None
    assert still["strava"]["activity_id"] == _id(cases[0])
    # The row itself is kept and served, with its flag.
    assert served_row["deleted_in_strava"] is True and served_row["linked_session_id"] == ids["differs.fit"]


def test_an_updated_row_serves_its_new_text_with_the_link_unchanged(tmp_path: Path) -> None:
    """AC7 through the routes: a newer archive with a new description on a linked row reports that
    row ``updated``, and ``GET /sessions/{id}`` and ``GET /strava-activities/{id}`` serve the new
    text with the link and ``match`` unchanged."""
    with TestClient(app) as client:
        _report, ids, cases = _import_kept(client, tmp_path)
        before = client.get(f"/sessions/{ids['attached.fit']}").json()["strava"]
        newer = [c for c in cases]
        cells = dict(newer[0].cells)
        cells["Activity Description"] = "Felt strong; negative split."
        newer[0] = newer[0]._replace(cells=cells)
        path = SA.strava_zip(tmp_path / "newer.zip", [c.cells for c in newer], [c.file for c in newer if c.file])
        report = _import(client, path)
        after = client.get(f"/sessions/{ids['attached.fit']}").json()["strava"]
        row = client.get(f"/strava-activities/{_id(cases[0])}").json()
    print("  before:", before["description"], "after:", after["description"])
    # Step 0 names a linked row whose cells changed ``updated``; the others read ``unchanged``.
    reported = {r["strava_activity_id"]: (r["outcome"], r["session_id"]) for r in report["files"]}
    assert reported.pop(_id(cases[0])) == ("updated", ids["attached.fit"])
    assert {outcome for outcome, _ in reported.values()} == {"unchanged"}
    assert before["description"] == ""
    assert after["description"] == row["description"] == "Felt strong; negative split."
    assert row["columns"]["Activity Description"] == "Felt strong; negative split."
    assert after["activity_id"] == before["activity_id"] == _id(cases[0])
    assert row["linked_session_id"] == ids["attached.fit"]
    assert after["match"] == "csv_start"


# --- the contract -------------------------------------------------------------------------------


def test_responses_validate_against_the_contract(tmp_path: Path) -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schemas = contract["components"]["schemas"]
    op = contract["paths"]["/strava-activities/{activity_id}"]["get"]
    assert op["operationId"] == "getStravaActivity" and op["x-readiness"] == "implemented"
    assert set(op["responses"]) == {"200", "404", "422"}
    (param,) = op["parameters"]
    assert (param["name"], param["in"], param["required"], param["schema"]) == (
        "activity_id",
        "path",
        True,
        {"type": "integer", "format": "int64"},
    )
    assert op["responses"]["404"]["content"]["application/json"]["schema"]["properties"]["detail"]["enum"] == [
        "no_strava_activity"
    ]
    assert set(schemas["StravaActivity"]["properties"]) == set(schemas["StravaActivity"]["required"])
    assert set(schemas["StravaActivity"]["properties"]) == STRAVA_ACTIVITY_KEYS
    assert set(schemas["SessionStrava"]["properties"]) == set(schemas["SessionStrava"]["required"])
    assert set(schemas["SessionStrava"]["properties"]) == SESSION_STRAVA_KEYS
    assert schemas["StravaActivity"]["properties"]["activity_id"]["format"] == "int64"
    assert schemas["SessionStrava"]["properties"]["activity_id"]["format"] == "int64"
    strava_prop = schemas["SessionDetail"]["properties"]["strava"]
    assert CW.normalised(strava_prop) == {"nullable": True, "ref": "SessionStrava"}
    assert "strava" in schemas["SessionDetail"]["required"]

    # The served document's StravaActivity matches the contract's, property by property.
    built = app.openapi()
    served_op = built["paths"]["/strava-activities/{activity_id}"]["get"]
    assert served_op["operationId"] == "getStravaActivity"
    assert served_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/StravaActivity"
    }
    seen, _compared = CW.walk(contract, built, ["StravaActivity"])
    assert seen == {"StravaActivity"}

    with TestClient(app) as client:
        _report, ids, cases = _import_kept(client, tmp_path)
        bodies = [client.get(f"/strava-activities/{_id(c)}").json() for c in cases]
        with_block = client.get(f"/sessions/{ids['attached.fit']}").json()
        (plain,) = _upload(client, [("plain.fit", SA.shifted(SA.fixture("sample_run"), -400 * SA.DAY_S))]).values()
        without_block = client.get(f"/sessions/{plain}").json()
        missing = client.get(f"/strava-activities/{max(_id(c) for c in cases) + 1}")
    for body in bodies:
        assert CV.validate(contract, schemas["StravaActivity"], body) == [], body["activity_id"]
    assert with_block["strava"] is not None and without_block["strava"] is None
    for body in (with_block, without_block):
        assert set(body) == set(schemas["SessionDetail"]["properties"])
        assert CV.validate(contract, schemas["SessionDetail"], body) == []
    assert CV.validate(contract, schemas["SessionStrava"], with_block["strava"]) == []
    four = op["responses"]["404"]["content"]["application/json"]["schema"]
    assert CV.validate(contract, four, missing.json()) == []

    # The validator can fail: a number where the columns' text belongs, and a missing key.
    broken = dict(bodies[0], columns={**bodies[0]["columns"], "Distance": 6.21})
    assert CV.validate(contract, schemas["StravaActivity"], broken) != []
    short = {k: v for k, v in with_block["strava"].items() if k != "match"}
    assert CV.validate(contract, schemas["SessionStrava"], short) != []
