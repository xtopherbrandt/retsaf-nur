"""An integer from the file beyond SQLite's INTEGER range is stored null, and the upload stores.

A non-conforming writer can declare any integer field ``uint64`` and put 2**63 or more in it;
``fitdecode`` hands the value on as written, and sqlite3 cannot bind it ("Python int too large to
convert to SQLite INTEGER"), so a value kept as it is makes ``POST /sessions`` a 500 and stores
nothing. The spec's "invalid field stored as null" rule applies instead: the session, its records
and its other fields store, and that one field is null. ``mapping.storable`` is the one screen;
the lap and step fields it guards are pinned in ``test_ingest_laps.py`` and
``test_mapping_laps.py``.

Each upload-path case widens one real field with ``tests/support/fit_patch.widen_to_uint64`` and
uploads the file through ``POST /sessions``:

| field | file | message | stored |
|---|---|---|---|
| ``hr_sensor_serial`` | ``strap_run_hrv`` | the three ANT+ heart-rate ``device_info`` (3, 13, 21) | null |
| ``heart_rate``, ``cadence``, ``power`` | ``sample_run`` | record 5 | that record's field null |
| ``height_cm``, ``resting_hr_bpm`` | ``sample_run`` | ``user_profile`` | null |
| ``max_hr_bpm``, ``threshold_hr_bpm`` | ``sample_run`` | ``zones_target`` | null |
| ``rmssd_precomputed`` | ``strap_health_snapshot_hrv`` | ``session.rmssd_hrv`` | null |

The bound itself is pinned on the serial: 2**63 - 1 is stored as written and 2**63 is null.
``garmin_activity_class`` is an enum, which ``widen_to_uint64`` refuses, so it is pinned at the
``profile_values.extract`` seam with a stand-in message.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.ingestion import profile_values
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

DEVICE_INFO, SERIAL_NUMBER = 23, 3
HR_SENSOR_ENTRIES = (3, 13, 21)
RECORD, PATCHED_RECORD = 20, 5
USER_PROFILE, ZONES_TARGET = 3, 7
SESSION, RMSSD_HRV = 18, 198
BEYOND = 2**63 + 5


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _widened(raw: bytes, global_num: int, field_num: int, indices: tuple[int, ...], value: int) -> bytes:
    raw = fit_patch.widen_to_uint64(raw, global_num, field_num)
    for index in indices:
        raw = fit_patch.set_field_at(raw, global_num, index, field_num, value)
    return raw


def _upload_and_read(client: TestClient, raw: bytes, query: str) -> tuple[list[dict], int]:
    """Upload ``raw``, read ``query`` (one ``?`` for the session id) and its record count, then
    delete the session so the next upload of the same file can store."""
    response = client.post("/sessions", files={"file": ("upload.fit", raw)})
    assert response.status_code == 201, response.text
    session_id = response.json()["session_id"]
    conn = db.get_connection()
    try:
        rows = [dict(r) for r in conn.execute(query, (session_id,))]
        records = conn.execute("SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)).fetchone()[
            0
        ]
    finally:
        conn.close()
    assert client.delete(f"/sessions/{session_id}").status_code == 204
    return rows, records


SERIAL_QUERY = "SELECT hr_sensor_serial FROM sessions WHERE session_id = ?"


@pytest.mark.parametrize(("value", "stored"), [(2**63 - 1, 2**63 - 1), (2**63, None), (BEYOND, None)])
def test_a_strap_serial_beyond_the_store_range_is_stored_null(value: int, stored: int | None) -> None:
    raw = _bytes("strap_run_hrv")
    patched = _widened(raw, DEVICE_INFO, SERIAL_NUMBER, HR_SENSOR_ENTRIES, value)
    with TestClient(app) as client:
        rows, records = _upload_and_read(client, patched, SERIAL_QUERY)
        base_rows, base_records = _upload_and_read(client, raw, SERIAL_QUERY)

    assert base_rows == [{"hr_sensor_serial": 3611410126}]
    assert rows == [{"hr_sensor_serial": stored}]
    assert records == base_records > 0


@pytest.mark.parametrize(("field_num", "column"), [(3, "heart_rate"), (4, "cadence"), (7, "power")])
def test_a_record_integer_beyond_the_store_range_is_stored_null(field_num: int, column: str) -> None:
    raw = _bytes("sample_run")
    patched = _widened(raw, RECORD, field_num, (PATCHED_RECORD,), BEYOND)
    query = f"SELECT {column} FROM records WHERE session_id = ? ORDER BY t"
    with TestClient(app) as client:
        rows, records = _upload_and_read(client, patched, query)
        base_rows, base_records = _upload_and_read(client, raw, query)

    assert records == base_records == len(rows) == len(base_rows) > PATCHED_RECORD
    assert base_rows[PATCHED_RECORD][column] is not None
    expected = [dict(row) for row in base_rows]
    expected[PATCHED_RECORD][column] = None
    assert rows == expected


@pytest.mark.parametrize(
    ("global_num", "field_num", "column"),
    [
        (USER_PROFILE, 3, "height_cm"),
        (USER_PROFILE, 8, "resting_hr_bpm"),
        (ZONES_TARGET, 1, "max_hr_bpm"),
        (ZONES_TARGET, 2, "threshold_hr_bpm"),
    ],
)
def test_a_profile_integer_beyond_the_store_range_is_stored_null(
    global_num: int, field_num: int, column: str
) -> None:
    raw = _bytes("sample_run")
    patched = _widened(raw, global_num, field_num, (0,), BEYOND)
    query = f"SELECT {column} FROM sessions WHERE session_id = ?"
    with TestClient(app) as client:
        rows, records = _upload_and_read(client, patched, query)
        base_rows, base_records = _upload_and_read(client, raw, query)

    assert base_rows[0][column] is not None
    assert rows == [{column: None}]
    assert records == base_records > 0


RMSSD_QUERY = "SELECT rmssd_precomputed, resting_rmssd_ms, hrv_source_tier FROM sessions WHERE session_id = ?"


def test_a_device_rmssd_beyond_the_store_range_is_stored_null() -> None:
    """A Health Snapshot's ``session.rmssd_hrv`` out of range reads as no usable device value."""
    raw = _bytes("strap_health_snapshot_hrv")
    patched = _widened(raw, SESSION, RMSSD_HRV, (0,), BEYOND)
    with TestClient(app) as client:
        rows, records = _upload_and_read(client, patched, RMSSD_QUERY)
        base_rows, base_records = _upload_and_read(client, raw, RMSSD_QUERY)

    assert base_rows[0]["rmssd_precomputed"] is not None
    assert rows[0]["rmssd_precomputed"] is None
    assert records == base_records > 0


class _Msg:
    """A stand-in decoded message: ``name`` and raw values by field name."""

    def __init__(self, name: str, **raw: object) -> None:
        self.name = name
        self._raw = raw

    def get_value(self, field_name: str, fallback=None, raw_value: bool = False):
        return self._raw.get(field_name, fallback)


@pytest.mark.parametrize(("raw", "stored"), [(2**63 - 1, 2**63 - 1), (2**63, None), (50, 50)])
def test_an_activity_class_beyond_the_store_range_is_absent(raw: int, stored: int | None) -> None:
    values = profile_values.extract([_Msg("user_profile", activity_class=raw)])
    assert values["garmin_activity_class"] == stored
