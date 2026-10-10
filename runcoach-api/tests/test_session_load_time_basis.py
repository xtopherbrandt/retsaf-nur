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
- **A 60 s record gap with the timer running, three ways** (constructed
  records through ``tests/support/session_load_inputs.py``, no bytes): the
  segment across the gap is over 5 s. Crossed at running speed it is a moving
  gap (IDEA-126, ruled 2026-10-10) that carries the HR at its start, so
  ``hr_time_s`` and ``hr_time_fraction`` equal the gap-free run's exactly
  (the fraction reads 1.0008, never 1, because the timer is fractional); the
  same gap with the distance frozen is a break, and ``hr_time_s`` drops by
  the gap's time; the rule before the ruling read the moving gap as that
  break, and its value is stated as a literal that the new one differs from.
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
from runcoach_api.metrics.session_load import compute_session_load

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
TOLERANCE = 0.01

RECORD_HEART_RATE_FIELD = 3
DEVICE_INFO = 23
CADENCE_LOCKED_RECORDS = 154

# The constructed run of the three-way gap row: sample_run's shape (2819 s recorded at 1 Hz, timer
# 2816.667 s, resting 47, max 188, threshold 169, male) at 3 m/s with a constant HR.
RECORDED_S = 2819
TIMER_TIME_S = 2816.667
GAP_START_S, GAP_END_S = 1500, 1560  # records [1500, 1560) dropped: the gap runs 1499 -> 1560, 61 s
GAP_DT_S = float(GAP_END_S - (GAP_START_S - 1))
OLD_RULE_HR_TIME_S = 2758.0  # RECORDED_S - 61: the gap left out, the rule before IDEA-126's carry ruling


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")
session_load_inputs = _load_support("session_load_inputs")


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


def _constructed_run(rows: list[dict]) -> dict:
    session = {
        "session_id": "constructed",
        "sport": "running",
        "activity_tag": None,
        "hr_source": "chest_strap",
        "quality_flags": [],
        "context": {},
        "timer_time_s": TIMER_TIME_S,
        "resting_hr_bpm": 47,
        "max_hr_bpm": 188,
        "threshold_hr_bpm": 169,
        "sex": "male",
    }
    return compute_session_load(session_load_inputs.inputs_for(session, rows))


def _one_hz_rows() -> list[dict]:
    return [{"t": float(t), "distance": 3.0 * t, "heart_rate": 150.0, "sample_quality": []} for t in range(RECORDED_S + 1)]


def test_a_sixty_second_record_gap_three_ways() -> None:
    plain = _one_hz_rows()
    dropped = [row for row in plain if not (GAP_START_S <= row["t"] < GAP_END_S)]
    moving = [dict(row) for row in dropped]  # the runner kept going: distance advanced 183 m across the gap
    frozen = [dict(row) for row in dropped]  # the same gap with the distance frozen: a standstill
    gap_m = 3.0 * (GAP_END_S - (GAP_START_S - 1))
    for row in frozen:
        if row["t"] >= GAP_END_S:
            row["distance"] -= gap_m
    base, p_moving, p_frozen = (_constructed_run(rows) for rows in (plain, moving, frozen))
    b, m, f = base["inputs"], p_moving["inputs"], p_frozen["inputs"]
    print(
        f"  hr_time_s plain {b['hr_time_s']} moving {m['hr_time_s']} frozen {f['hr_time_s']} old rule {OLD_RULE_HR_TIME_S}; "
        f"fraction plain {b['hr_time_fraction']:.4f} moving {m['hr_time_fraction']:.4f} frozen {f['hr_time_fraction']:.4f}"
    )
    assert b["hr_time_s"] == float(RECORDED_S) and b["timer_time_s"] == TIMER_TIME_S
    assert b["hr_time_fraction"] == pytest.approx(1.0008, abs=1e-4) and b["hr_time_fraction"] != 1
    # (1) The gap at running speed: the moving gap carries its start HR, so nothing changes.
    assert "moving_gap" in p_moving["input_flags"] and "moving_gap" not in base["input_flags"]
    assert m["hr_time_s"] == b["hr_time_s"]
    assert m["hr_time_fraction"] == b["hr_time_fraction"]
    assert m["avg_hr_bpm"] == b["avg_hr_bpm"] == 150.0
    assert p_moving["metrics"]["hr_trimp"]["value"] == base["metrics"]["hr_trimp"]["value"]
    assert p_moving["session_load"]["driver"] == "hr_trimp"
    # (2) The same gap with the distance frozen is a break: hr_time_s drops by the gap's time.
    assert "moving_gap" not in p_frozen["input_flags"]
    assert f["hr_time_s"] == b["hr_time_s"] - GAP_DT_S
    assert f["hr_time_fraction"] < b["hr_time_fraction"]
    assert f["hr_time_fraction"] == pytest.approx(f["hr_time_s"] / TIMER_TIME_S)
    assert p_frozen["metrics"]["hr_trimp"]["value"] < base["metrics"]["hr_trimp"]["value"]
    # (3) The rule before the ruling read case 1 as case 2; the new value differs from that literal.
    assert f["hr_time_s"] == OLD_RULE_HR_TIME_S
    assert m["hr_time_s"] != OLD_RULE_HR_TIME_S
    assert m["hr_time_s"] == OLD_RULE_HR_TIME_S + GAP_DT_S


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
