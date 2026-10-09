"""The time basis of the F017 reference on patched uploads (AC2): running time with usable HR, never stopped time.

Each row patches real bytes through ``tests/support/fit_patch.py`` and uploads
them; the saved body comes back from ``GET /sessions/{id}/load``. A patch to
the records leaves ``source_device`` and ``start_time`` alone, so the patched
file has the unpatched file's session id: a row that compares the two uploads
the unpatched file first, reads its body and deletes it before uploading the
patched bytes.

- **10 minutes of HR blanked mid-run** (``heart_rate`` on ``[600, 1200)`` of
  ``sample_run``): ``hr_time_s`` is 600 s lower than the unpatched run's, TRIMP
  is lower, ``hr_time_fraction`` is below 1. Nothing is imputed.
- **A 60 s record gap with the timer running** (``[1500, 1560)`` dropped):
  the segment across the gap is over 5 s and is not counted, so
  ``hr_time_fraction`` is below 1 while ``timer_time_s`` is the file's
  unchanged ``total_timer_time``.
- **A watch pause adds nothing**: ``wrist_ppg_run`` (81 s and 11 s pauses,
  both timed) has ``hr_time_s`` equal to its recorded time and a fraction
  within 0.01 of 1.
- **The wrist path excludes cadence-locked seconds** (the ``hr_excluded``
  rule): ``sample_run`` without its heart-rate ``device_info`` is stored
  ``wrist_ppg``, the cadence-lock gate marks 154 of its records, and
  ``hr_time_s`` is the sum of ``dt`` over the counted segments whose start
  record is not excluded: 154 s below the recorded time, where a rule that
  counted excluded rows would give the recorded time itself. The excluded
  rows are counted from ``db.read_session_load_inputs``'s ``segment_rows``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
TOLERANCE = 0.01

RECORD_HEART_RATE_FIELD = 3
DEVICE_INFO = 23
CADENCE_LOCKED_RECORDS = 154


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _upload(client: TestClient, name: str, raw: bytes | None = None) -> str:
    response = client.post("/sessions", files={"file": (name, raw or (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _load(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/load")
    assert response.status_code == 200, response.text
    return response.json()


def _unpatched_then_patched(client: TestClient, name: str, raw: bytes) -> tuple[dict, dict]:
    """The bodies of the unpatched upload (deleted after its read) and of the patched one."""
    base_id = _upload(client, name)
    base = _load(client, base_id)
    assert client.delete(f"/sessions/{base_id}").status_code == 204
    patched = _load(client, _upload(client, name, raw))
    return base, patched


def _segment_rows(session_id: str) -> list[dict]:
    conn = db.get_connection()
    try:
        inputs = db.read_session_load_inputs(conn, session_id)
    finally:
        conn.close()
    assert inputs is not None
    return inputs["segment_rows"]


def test_ten_minutes_of_blanked_hr_adds_nothing() -> None:
    raw = fit_patch.blank_record_field((FIXTURES / "sample_run.fit").read_bytes(), RECORD_HEART_RATE_FIELD, 600, 1200)
    with TestClient(app) as client:
        base, patched = _unpatched_then_patched(client, "sample_run.fit", raw)
    b, p = base["inputs"], patched["inputs"]
    print(
        f"  hr_time_s {b['hr_time_s']} -> {p['hr_time_s']}; trimp {base['metrics']['hr_trimp']['value']:.4f} -> "
        f"{patched['metrics']['hr_trimp']['value']:.4f}; fraction {b['hr_time_fraction']:.4f} -> {p['hr_time_fraction']:.4f}"
    )
    assert p["hr_time_s"] == b["hr_time_s"] - 600
    assert p["timer_time_s"] == b["timer_time_s"]
    assert p["recorded_time_s"] == b["recorded_time_s"]
    assert p["hr_time_fraction"] < 1
    assert p["hr_time_fraction"] == pytest.approx(p["hr_time_s"] / p["timer_time_s"])
    assert patched["metrics"]["hr_trimp"]["value"] < base["metrics"]["hr_trimp"]["value"]
    assert patched["session_load"]["value"] < base["session_load"]["value"]
    assert patched["session_load"]["driver"] == "hr_trimp"
    assert patched["flags"] == []


def test_a_sixty_second_record_gap_with_the_timer_running_lowers_the_fraction() -> None:
    raw = fit_patch.drop_records((FIXTURES / "sample_run.fit").read_bytes(), 1500, 1560)
    with TestClient(app) as client:
        base, patched = _unpatched_then_patched(client, "sample_run.fit", raw)
    b, p = base["inputs"], patched["inputs"]
    print(f"  hr_time_s {b['hr_time_s']} -> {p['hr_time_s']}; fraction {b['hr_time_fraction']:.4f} -> {p['hr_time_fraction']:.4f}")
    assert p["timer_time_s"] == b["timer_time_s"] == pytest.approx(2816.667)
    assert p["hr_time_fraction"] < 1
    assert p["hr_time_s"] <= b["hr_time_s"] - 60
    assert p["hr_time_fraction"] == pytest.approx(p["hr_time_s"] / p["timer_time_s"])
    assert patched["session_load"]["driver"] == "hr_trimp"


def test_a_watch_pause_adds_nothing() -> None:
    with TestClient(app) as client:
        body = _load(client, _upload(client, "wrist_ppg_run.fit"))
    inputs = body["inputs"]
    print(f"  hr_time_s {inputs['hr_time_s']} recorded {inputs['recorded_time_s']} timer {inputs['timer_time_s']} fraction {inputs['hr_time_fraction']:.4f}")
    assert inputs["hr_time_s"] == pytest.approx(inputs["recorded_time_s"], abs=TOLERANCE)
    assert inputs["hr_time_fraction"] == pytest.approx(1.0, abs=TOLERANCE)
    assert inputs["timer_time_s"] == pytest.approx(1958.594, abs=0.001)


def test_the_wrist_path_excludes_cadence_locked_seconds() -> None:
    raw = fit_patch.remove_messages((FIXTURES / "sample_run.fit").read_bytes(), DEVICE_INFO)
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit", raw)
        body = _load(client, session_id)
    inputs = body["inputs"]
    rows = _segment_rows(session_id)
    excluded = sum(1 for row in rows if row["hr_excluded"])
    counted = sum(row["dt"] for row in rows if not row["hr_excluded"])
    everything = sum(row["dt"] for row in rows)
    print(
        f"  hr_source {inputs['hr_source']}; segments {len(rows)}, hr_excluded {excluded}; "
        f"hr_time_s {inputs['hr_time_s']} counted {counted} all {everything}; fraction {inputs['hr_time_fraction']:.4f}"
    )
    assert inputs["hr_source"] == "wrist_ppg"
    assert excluded == CADENCE_LOCKED_RECORDS
    assert inputs["hr_time_s"] == counted
    assert inputs["hr_time_s"] == everything - CADENCE_LOCKED_RECORDS
    assert inputs["hr_time_s"] == inputs["recorded_time_s"] - CADENCE_LOCKED_RECORDS
    assert inputs["hr_time_fraction"] < 1
    assert inputs["hr_time_fraction"] == pytest.approx(inputs["hr_time_s"] / inputs["timer_time_s"])
    assert "wrist_hr" in body["flags"]
