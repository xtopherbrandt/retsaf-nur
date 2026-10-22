"""``POST /sessions`` with an HRV_STATUS file (``file_id.type`` 68): F028 AC1, AC2 and AC4 at the route.

Every upload is screened by ``imports.archive.screen_file_id``. An ``hrv_status``
file is mapped by ``ingestion.overnight.map_night`` and written by
``db.persist_night``, with or without ``runs_and_captures_only``; any other
file keeps the activity path (``test_upload_switch.py`` pins that it is
unchanged).

The nights are the real HRV_STATUS fixtures under ``fixtures/nights/``
(provenance rows in ``fixtures/README.md``: real nights from the wellness FIT,
run proof "no"). The synthetic files are ``night_recent`` patched with
``support/fit_patch.py`` for the cases the export lacks: an average of 0, a
negative one and a non-finite one, a series truncated inside a value and two
values at one timestamp.

The session ids, start times and value counts below were read from a decode
of each fixture and written as literals, and every reply body is asserted
whole, so a reply that gains, loses or renames a key goes red here.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

NIGHTS = Path(__file__).parent / "fixtures" / "nights"
SUPPORT = Path(__file__).parent / "support"

# FIT global message numbers and field numbers: the summary's
# ``last_night_average`` is field 1 of message 370; ``hrv_value`` is 371.
HRV_STATUS_SUMMARY, HRV_VALUE = 370, 371
LAST_NIGHT_AVERAGE = 1
# FIT base types: a signed 16-bit integer and a 32-bit float.
SINT16, FLOAT32 = 0x83, 0x88

SWITCH = {"runs_and_captures_only": "true"}
ROUTES = {"plain": None, "switch": SWITCH}

# Read from a decode of each fixture.
RECENT_ID = "fe1b9839829c6200cc65ec6d239cac15"
RECENT_START = "2026-10-10T11:33:29+00:00"
RECENT_VALUES = 57
SHORT_ID = "77eff81792e10fed993ce302d36b5a3a"
EARLIER_ID = "d9271616992e11dae23c1a4fe1a47980"
LATER_ID = "c4ea4e8e303cee715d151d723910777a"
LATER_START = "2024-01-01T15:30:53+00:00"
LATER_VALUES = 104
SIDECAR_FIELDS = 5


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _night(name: str) -> bytes:
    return (NIGHTS / f"{name}.fit").read_bytes()


def _post(client: TestClient, raw: bytes, data: dict | None = None):
    return client.post("/sessions", files={"file": ("upload.fit", raw)}, data=data)


def _counts() -> dict[str, int]:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "overnight_hrv_values", "quarantine_sidecar", "records")
        }
    finally:
        conn.close()


def _row(session_id: str) -> dict | None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        row = conn.execute(
            "SELECT session_id, start_time, activity_tag, hrv_source_tier, rr_source,"
            " resting_rmssd_ms, rmssd_precomputed, name FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _values(session_id: str) -> int:
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM overnight_hrv_values WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _average_zero() -> bytes:
    return fit_patch.set_field(_night("night_recent"), HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, 0)


def _average_negative() -> bytes:
    return fit_patch.retype_field(_night("night_recent"), HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, SINT16, -128)


def _average_infinite() -> bytes:
    return fit_patch.retype_field(
        _night("night_recent"), HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, FLOAT32, math.inf
    )


NO_NIGHT_FILES = {
    "midnight": lambda: _night("night_midnight_no_average"),
    "lone morning": lambda: _night("night_lone_morning_no_average"),
    "average 0": _average_zero,
    "average -1.0": _average_negative,
    "average +inf": _average_infinite,
}


# ---------------------------------------------------------------------------
# AC1: a night is stored, once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_night_is_stored_with_and_without_the_switch(route: str) -> None:
    with TestClient(app) as client:
        response = _post(client, _night("night_recent"), ROUTES[route])
    assert response.status_code == 201, response.text
    expected = {"session_id": RECENT_ID, "quality_flags": []}
    if route == "switch":
        expected["filter_applied"] = True
    assert response.json() == expected
    assert _row(RECENT_ID) == {
        "session_id": RECENT_ID,
        "start_time": RECENT_START,
        "activity_tag": "overnight_hrv",
        "hrv_source_tier": "health_api_overnight",
        "rr_source": "overnight_ppg",
        "resting_rmssd_ms": 43.0,
        "rmssd_precomputed": 43.0,
        "name": "Overnight HRV",
    }
    assert _counts() == {
        "sessions": 1,
        "overnight_hrv_values": RECENT_VALUES,
        "quarantine_sidecar": SIDECAR_FIELDS,
        "records": 0,
    }


@pytest.mark.parametrize("first", sorted(ROUTES))
def test_the_same_night_again_is_409_on_both_routes(first: str) -> None:
    with TestClient(app) as client:
        stored = _post(client, _night("night_recent"), ROUTES[first])
        assert stored.status_code == 201, stored.text
        before = _counts()
        for route in sorted(ROUTES):
            again = _post(client, _night("night_recent"), ROUTES[route])
            assert again.status_code == 409, (route, again.text)
            assert again.json() == {"detail": f"already ingested as session {RECENT_ID}"}, route
    assert _counts() == before


@pytest.mark.parametrize("name", ["night_recent", "night_midnight_no_average"])
def test_resting_capture_with_a_night_is_422(name: str) -> None:
    with TestClient(app) as client:
        response = _post(client, _night(name), {"resting_capture": "true"})
        assert response.status_code == 422, response.text
        assert response.json() == {
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body", "resting_capture"],
                    "msg": "Value error, resting_capture cannot be declared for an HRV_STATUS file",
                    "input": True,
                }
            ]
        }
        # With the switch too the pair is refused, by the switch's own 422.
        both = _post(client, _night(name), {"resting_capture": "true", **SWITCH})
        assert both.status_code == 422, both.text
        assert [tuple(e["loc"]) for e in both.json()["detail"]] == [("body", "runs_and_captures_only")]
    assert _counts()["sessions"] == 0


# ---------------------------------------------------------------------------
# AC2: a file without a night is skipped, and nothing stored is ever <= 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("label", list(NO_NIGHT_FILES))
def test_a_file_without_a_night_is_skipped_as_no_night(label: str, route: str) -> None:
    with TestClient(app) as client:
        # A night already stored, so an unchanged count is not an empty store's zero.
        assert _post(client, _night("night_short")).status_code == 201
        before = _counts()
        response = _post(client, NO_NIGHT_FILES[label](), ROUTES[route])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "skipped": True,
        "reason": "no_night",
        "file_type": "hrv_status",
        "filter_applied": route == "switch",
    }
    assert before == {"sessions": 1, "overnight_hrv_values": 39, "quarantine_sidecar": SIDECAR_FIELDS, "records": 0}
    assert _counts() == before


def test_every_stored_night_has_a_positive_value() -> None:
    with TestClient(app) as client:
        for name in (
            "night_recent",
            "night_short",
            "night_resummary_earlier",
            "night_resummary_later",
            "night_midnight_no_average",
            "night_lone_morning_no_average",
        ):
            assert _post(client, _night(name)).status_code in (200, 201), name
        for label, build in NO_NIGHT_FILES.items():
            assert _post(client, build(), SWITCH).status_code == 200, label
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT session_id, resting_rmssd_ms FROM sessions WHERE activity_tag = 'overnight_hrv' ORDER BY session_id"
        ).fetchall()
        non_positive = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE resting_rmssd_ms IS NOT NULL AND NOT resting_rmssd_ms > 0"
        ).fetchone()[0]
    finally:
        conn.close()
    assert [(r[0], r[1]) for r in rows] == [
        (SHORT_ID, 57.0),
        (LATER_ID, 39.0),
        (RECENT_ID, 43.0),
    ]
    assert non_positive == 0


# ---------------------------------------------------------------------------
# AC4: a re-summary replaces, a fault refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["earlier first", "later first"])
def test_the_resummary_pair_replies_in_either_order(order: str) -> None:
    with TestClient(app) as client:
        if order == "earlier first":
            first = _post(client, _night("night_resummary_earlier"))
            second = _post(client, _night("night_resummary_later"))
            assert first.status_code == 201, first.text
            assert first.json() == {"session_id": EARLIER_ID, "quality_flags": []}
            assert second.status_code == 201, second.text
            assert second.json() == {
                "session_id": LATER_ID,
                "quality_flags": [],
                "replaced_session_id": EARLIER_ID,
            }
        else:
            first = _post(client, _night("night_resummary_later"))
            second = _post(client, _night("night_resummary_earlier"))
            assert first.status_code == 201, first.text
            assert first.json() == {"session_id": LATER_ID, "quality_flags": []}
            assert second.status_code == 409, second.text
            assert second.json() == {"detail": f"already ingested as session {LATER_ID}"}
    # One row stands, the later file's, with its own series.
    assert _row(EARLIER_ID) is None
    assert _row(LATER_ID)["start_time"] == LATER_START
    assert _values(LATER_ID) == LATER_VALUES
    assert _counts() == {
        "sessions": 1,
        "overnight_hrv_values": LATER_VALUES,
        "quarantine_sidecar": SIDECAR_FIELDS,
        "records": 0,
    }


def test_a_resummary_with_the_switch_carries_both_keys() -> None:
    with TestClient(app) as client:
        assert _post(client, _night("night_resummary_earlier"), SWITCH).status_code == 201
        second = _post(client, _night("night_resummary_later"), SWITCH)
    assert second.status_code == 201, second.text
    assert second.json() == {
        "session_id": LATER_ID,
        "quality_flags": [],
        "filter_applied": True,
        "replaced_session_id": EARLIER_ID,
    }


FAULTY = {
    "truncated inside a value": (
        lambda: fit_patch.truncate_inside(_night("night_recent"), HRV_VALUE, 30),
        "file could not be parsed: expected 4 bytes, got 3 @ 690",
    ),
    "two values at one timestamp": (
        lambda: fit_patch.duplicate_timestamp(_night("night_recent"), HRV_VALUE, 10),
        "overnight HRV file refused: two hrv_value messages at 2026-10-10T05:01:00+00:00",
    ),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("label", list(FAULTY))
def test_a_faulty_series_is_400_and_writes_nothing(label: str, route: str) -> None:
    build, detail = FAULTY[label]
    with TestClient(app) as client:
        response = _post(client, build(), ROUTES[route])
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": detail}
    assert _counts() == {"sessions": 0, "overnight_hrv_values": 0, "quarantine_sidecar": 0, "records": 0}
