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
values at one timestamp; and ``night_before_snapshot`` with its summary moved
onto ``sample_health_snapshot``'s start second, the same-second collision the
export holds no real pair of.

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

FIXTURES = Path(__file__).parent / "fixtures"
NIGHTS = FIXTURES / "nights"
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
RECENT_ID = "f866f4fe46b0a62f032a1a0f1c3fd435"
RECENT_START = "2026-10-10T11:33:29+00:00"
RECENT_VALUES = 57
SHORT_ID = "80d94287ea6ec2be566d1a4d575ce2ae"
EARLIER_ID = "531adcef7df4e7d034e716630b3f4832"
LATER_ID = "1da122c0bb21c420b56c8d962a4981f5"
LATER_START = "2024-01-01T15:30:53+00:00"
LATER_VALUES = 104
SIDECAR_FIELDS = 5

# The wrist Health Snapshot ``sample_health_snapshot`` (a health snapshot, run proof "no") and
# ``nights/night_before_snapshot``, the night of the same morning, summarised 59 s before the
# snapshot started. Moving the summary timestamp (field 253) onto the snapshot's start second,
# written as FIT seconds since 1989-12-31, makes the two start in one second. The ids are
# ``derive_session_id`` of the watch's string and of the night's labelled one with that start.
TIMESTAMP = 253
SNAPSHOT_START = "2026-09-01T12:11:26+00:00"
SNAPSHOT_FIT_SECONDS = 1_157_199_086
SNAPSHOT_ID = "e25a77b6fa3e126533bbe348a0060460"
SNAPSHOT_FLAGS: list[str] = []
NIGHT_AT_SNAPSHOT_ID = "911d5b5999f2328c12d5605b481f7f17"


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


def _snapshot() -> bytes:
    return (FIXTURES / "sample_health_snapshot.fit").read_bytes()


def _night_at_snapshot() -> bytes:
    """``night_before_snapshot`` with its summary timestamp moved onto the snapshot's start second."""
    return fit_patch.set_field(
        _night("night_before_snapshot"), HRV_STATUS_SUMMARY, TIMESTAMP, SNAPSHOT_FIT_SECONDS
    )


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


@pytest.mark.parametrize("order", [("snapshot", "night"), ("night", "snapshot")], ids=["snapshot-first", "night-first"])
def test_a_night_and_a_snapshot_in_one_second_both_upload(order: tuple[str, str]) -> None:
    """A wrist Health Snapshot and a night summarised in the snapshot's start
    second are two 201s in either order, and the same night file again is a
    409 naming the night, not the snapshot."""
    files = {"snapshot": _snapshot(), "night": _night_at_snapshot()}
    with TestClient(app) as client:
        replies = {name: _post(client, files[name]) for name in order}
        again = _post(client, files["night"])
    for name, reply in replies.items():
        assert reply.status_code == 201, (name, reply.text)
    assert replies["snapshot"].json() == {"session_id": SNAPSHOT_ID, "quality_flags": SNAPSHOT_FLAGS}
    assert replies["night"].json() == {"session_id": NIGHT_AT_SNAPSHOT_ID, "quality_flags": []}
    assert again.status_code == 409, again.text
    assert again.json() == {"detail": f"already ingested as session {NIGHT_AT_SNAPSHOT_ID}"}
    assert _row(NIGHT_AT_SNAPSHOT_ID)["start_time"] == _row(SNAPSHOT_ID)["start_time"] == SNAPSHOT_START
    assert _counts()["sessions"] == 2


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
        (LATER_ID, 39.0),
        (SHORT_ID, 57.0),
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


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_value_with_no_timestamp_is_dropped_and_the_night_stored(route: str) -> None:
    """``night_recent`` with its 11th value's timestamp blanked (synthetic: the export has no such
    value). The summary is untouched, so the night keeps its id; 56 of its 57 values are stored."""
    raw = fit_patch.blank_field_at(_night("night_recent"), HRV_VALUE, 10, TIMESTAMP)
    with TestClient(app) as client:
        response = _post(client, raw, ROUTES[route])
    assert response.status_code == 201, response.text
    assert response.json()["session_id"] == RECENT_ID
    assert _values(RECENT_ID) == RECENT_VALUES - 1
    assert _counts() == {
        "sessions": 1,
        "overnight_hrv_values": RECENT_VALUES - 1,
        "quarantine_sidecar": SIDECAR_FIELDS,
        "records": 0,
    }


def test_deleting_a_night_removes_its_values_and_sidecar() -> None:
    with TestClient(app) as client:
        stored = _post(client, _night("night_recent"))
        assert stored.status_code == 201, stored.text
        before = _counts()
        deleted = client.delete(f"/sessions/{RECENT_ID}")
    assert before == {
        "sessions": 1,
        "overnight_hrv_values": RECENT_VALUES,
        "quarantine_sidecar": SIDECAR_FIELDS,
        "records": 0,
    }
    assert deleted.status_code == 204, deleted.text
    assert _counts() == {"sessions": 0, "overnight_hrv_values": 0, "quarantine_sidecar": 0, "records": 0}
