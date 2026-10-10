"""The wrist path of the F017 reference (AC5, gates 7-8; user rulings R2 and 2026-10-08).

**The uploads** patch ``sample_run.fit`` through ``tests/support/fit_patch.py``:
its heart-rate ``device_info`` (23) is removed so ``_infer_hr_source`` finds no
connected sensor and ``quality_gates.apply`` stores ``wrist_ppg`` (the file
carries no ``hrv`` message, so there is no RR to remove). Removing
``device_info`` changes ``source_device`` and so the session id: every id here
is read from the upload response. On the wrist path the cadence-lock gate
marks 154 records of ``sample_run``, so the served ``avg_hr_bpm`` is below the
strap run's 146.81 and ``hr_time_s`` is 154 s shorter; the thresholds of the
at/below rows are therefore chosen from the served average of the patched
file (``floor(avg)`` is at or below it, ``floor(avg) + 1`` above), written
into the file's ``zones_target.threshold_heart_rate`` (7/2) and uploaded
after the first session is deleted (the threshold byte is not part of the
session id).

**The rows:** at or above the usable threshold -> ``wrist_hr_at_threshold``;
below -> computed and flagged; no threshold (the field set to the FIT invalid
``0xFF``, which ``profile_values`` reads as none, with no earlier upload to
lend an anchor) -> ``wrist_hr_threshold_unknown``; a file threshold of 200
above the max 188 -> ``wrist_hr_threshold_unknown``. ``flags`` carries
``wrist_hr`` on each. The equality row (``avg == threshold``) and a null
``hr_source`` are pure-function rows, since no file averages to an exact
integer and the ingest never stores a null ``hr_source``.

**The adversarial table** (gates 7-8 on the pure function): the threshold
absent, equal to resting, equal to max, above max, ``10**400`` (gate 7 before
gate 9 on the wrist path); and ``hr_source`` ``chest_strap``, ``wrist_ppg``,
null and an unknown string, each under a threshold the average sits above.
Only ``chest_strap`` escapes the wrist path; a non-running wrist session is
refused by gate 1 and carries no flag, as the reference's flag rule names
running sessions. One row, a wrist threshold whose span with resting collapses
in float, was added at review with the 2026-10-09 ruling; it tests the
reference step after TRIMP, not gates 7-8.
"""

from __future__ import annotations

import functools
import importlib.util
import math
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api.main import app
from runcoach_api.metrics import session_load
from runcoach_api.metrics.session_load import compute_session_load

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
RUN = "sample_run.fit"

DEVICE_INFO = 23
ZONES_TARGET = 7
THRESHOLD_HEART_RATE = 2
UINT8_INVALID = 0xFF
FILE_MAX_HR = 188
STRAP_AVG_HR = 146.81
CADENCE_LOCKED_RECORDS = 154

HUGE = 10**400


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _wrist_bytes(threshold: int | None = None) -> bytes:
    """``sample_run`` with no heart-rate ``device_info``; ``threshold`` overwrites the file's 169
    (``None`` leaves 169; ``UINT8_INVALID`` makes the file carry no threshold)."""
    raw = fit_patch.remove_messages((FIXTURES / RUN).read_bytes(), DEVICE_INFO)
    if threshold is not None:
        raw = fit_patch.set_field(raw, ZONES_TARGET, THRESHOLD_HEART_RATE, threshold)
    return raw


def _upload(client: TestClient, raw: bytes) -> str:
    response = client.post("/sessions", files={"file": (RUN, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _load(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/load")
    assert response.status_code == 200, response.text
    return response.json()


def _served_average(client: TestClient) -> float:
    """The served ``avg_hr_bpm`` of the wrist-patched file, read from an upload that is then deleted."""
    session_id = _upload(client, _wrist_bytes())
    body = _load(client, session_id)
    assert body["inputs"]["hr_source"] == "wrist_ppg"
    assert client.delete(f"/sessions/{session_id}").status_code == 204
    return body["inputs"]["avg_hr_bpm"]


def _assert_wrist_outcome(body: dict, reason: str | None) -> None:
    assert "wrist_hr" in body["flags"], body["flags"]
    assert body["inputs"]["hr_source"] == "wrist_ppg"
    assert body["sport"] == "running"
    assert body["metrics"]["hr_trimp"]["unavailable"] == reason
    assert body["session_load"]["unavailable"] == reason
    assert (body["metrics"]["hr_trimp"]["value"] is not None) == (reason is None)
    assert (body["session_load"]["driver"] == "hr_trimp") == (reason is None)
    if reason is not None:
        assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] is None
        assert body["metrics"]["hr_trimp"]["coefficients"] is None
        assert reason in session_load.SERVED_REASONS


# --- AC5 through real uploads --------------------------------------------------------------------


def test_a_wrist_run_at_threshold_is_unavailable() -> None:
    with TestClient(app) as client:
        avg = _served_average(client)
        threshold = math.floor(avg)
        session_id = _upload(client, _wrist_bytes(threshold))
        body = _load(client, session_id)
    print(f"  served avg {avg:.4f} (strap {STRAP_AVG_HR}); file threshold {threshold} -> {body['session_load']}")
    assert avg < STRAP_AVG_HR, "the wrist path did not mark cadence-lock records: the average is the strap's"
    assert body["inputs"]["avg_hr_bpm"] == avg
    assert body["inputs"]["threshold_hr_bpm"]["value"] == threshold
    assert body["inputs"]["threshold_hr_bpm"]["source"] == "session_file"
    assert avg >= threshold
    _assert_wrist_outcome(body, "wrist_hr_at_threshold")
    # The inputs are still reported, and the other two metrics keep their own reasons.
    assert body["inputs"]["resting_hr_bpm"]["value"] == 47
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}


def test_a_wrist_run_below_threshold_is_computed_and_flagged() -> None:
    with TestClient(app) as client:
        avg = _served_average(client)
        threshold = math.floor(avg) + 1
        session_id = _upload(client, _wrist_bytes(threshold))
        body = _load(client, session_id)
    print(f"  served avg {avg:.4f}; file threshold {threshold} -> {body['session_load']} flags {body['flags']}")
    assert avg < threshold
    assert body["inputs"]["threshold_hr_bpm"]["value"] == threshold
    _assert_wrist_outcome(body, None)
    assert body["flags"] == ["wrist_hr"]
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] > 0
    assert body["session_load"]["value"] > 0
    # The load is the wrist path's: cadence-locked seconds are not counted.
    assert body["inputs"]["hr_time_s"] == body["inputs"]["recorded_time_s"] - CADENCE_LOCKED_RECORDS


def test_a_wrist_run_with_no_threshold_is_unknown() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, _wrist_bytes(UINT8_INVALID))
        body = _load(client, session_id)
    print(f"  threshold block {body['inputs']['threshold_hr_bpm']} -> {body['session_load']}")
    assert body["inputs"]["threshold_hr_bpm"]["value"] is None
    assert body["inputs"]["threshold_hr_bpm"]["anchor_unavailable"] == "missing"
    _assert_wrist_outcome(body, "wrist_hr_threshold_unknown")


def test_a_wrist_run_whose_file_threshold_is_above_max_is_unknown() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, _wrist_bytes(200))
        body = _load(client, session_id)
    print(f"  threshold {body['inputs']['threshold_hr_bpm']['value']} max {body['inputs']['max_hr_bpm']['value']} -> {body['session_load']}")
    assert body["inputs"]["threshold_hr_bpm"]["value"] == 200
    assert body["inputs"]["max_hr_bpm"]["value"] == FILE_MAX_HR
    _assert_wrist_outcome(body, "wrist_hr_threshold_unknown")


def test_the_strap_run_takes_no_wrist_path() -> None:
    """Control: the unpatched file reads ``chest_strap`` and carries no flag with the same threshold rule."""
    with TestClient(app) as client:
        session_id = _upload(client, (FIXTURES / RUN).read_bytes())
        body = _load(client, session_id)
    assert body["inputs"]["hr_source"] == "chest_strap"
    assert body["flags"] == []
    assert body["session_load"]["unavailable"] is None


# --- the pure-function rows ------------------------------------------------------------------------


session_load_inputs = _load_support("session_load_inputs")
# The pure-function rows run on the wrist path unless a row names another ``hr_source``.
_synthetic = functools.partial(session_load_inputs.synthetic, hr_source="wrist_ppg")
_outcome = session_load_inputs.wrist_outcome


def test_average_equal_to_threshold_is_unavailable_on_the_wrist_path() -> None:
    body = compute_session_load(_synthetic(heart_rate=170.0, threshold=170))
    assert body["inputs"]["avg_hr_bpm"] == 170.0
    assert _outcome(body) == ("wrist_hr_at_threshold", "wrist_hr_at_threshold", True)
    # One bpm under is computed; the same inputs on a strap are computed at either.
    assert _outcome(compute_session_load(_synthetic(heart_rate=169.0, threshold=170))) == (None, None, True)
    assert _outcome(compute_session_load(_synthetic(heart_rate=170.0, threshold=170, hr_source="chest_strap"))) == (None, None, False)


def test_a_null_hr_source_takes_the_wrist_path() -> None:
    body = compute_session_load(_synthetic(hr_source=None, heart_rate=175.0))
    assert _outcome(body) == ("wrist_hr_at_threshold", "wrist_hr_at_threshold", True)
    assert body["inputs"]["hr_source"] is None
    assert _outcome(compute_session_load(_synthetic(hr_source=None, heart_rate=150.0))) == (None, None, True)
    assert _outcome(compute_session_load(_synthetic(hr_source=None, threshold=None))) == (
        "wrist_hr_threshold_unknown",
        "wrist_hr_threshold_unknown",
        True,
    )


def test_wrist_hr_is_flagged_on_every_running_wrist_outcome() -> None:
    """Gates 2-6 and 9 refuse before the wrist gates; the flag is still set (REG-23: PPG input is flagged)."""
    refused = {
        "declared_capture": {"activity_tag": "health_snapshot"},
        "no_hr": {"heart_rate": None},
        "missing_anchor": {"resting": None},
        "order_conflict": {"resting": 190, "max_hr": 190},
        "avg_hr_below_resting": {"heart_rate": 40.0},
        "avg_hr_above_max": {"heart_rate": 200.0},
        "not_representable": {"max_hr": HUGE},
    }
    for reason, overrides in refused.items():
        body = compute_session_load(_synthetic(**overrides))
        got = _outcome(body)
        print(f"  {overrides} -> {got}")
        assert got[:2] == (reason, reason), overrides
        assert got[2], f"no wrist_hr flag under {reason}"
    # Gate 1 refuses the session as a whole; the flag names running sessions only.
    assert compute_session_load(_synthetic(sport="other"))["flags"] == []


def test_wrist_hr_precedes_sex_defaulted() -> None:
    body = compute_session_load(_synthetic(sex=None))
    assert body["flags"] == ["wrist_hr", "sex_defaulted"]
    # sex_defaulted is set only when TRIMP is computed; wrist_hr whatever the outcome.
    assert compute_session_load(_synthetic(sex=None, heart_rate=175.0))["flags"] == ["wrist_hr"]


# (id, inputs, expected hr_trimp reason, expected session_load reason, wrist_hr flagged)
# Written from the reference's gate table rows 7 and 8 and its hr_source rule before the gates were built.
ADVERSARIAL_ROWS = [
    # the threshold, with the average (150) between resting (50) and max (190)
    ("threshold_absent_is_unknown", {"threshold": None}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_equal_resting_is_unknown", {"threshold": 50}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_equal_max_is_unknown", {"threshold": 190}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_above_max_is_unknown", {"threshold": 200}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_below_resting_is_unknown", {"threshold": 40}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_ten_to_the_400_is_unknown_before_gate_9", {"threshold": HUGE}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("threshold_one_above_resting_is_at_threshold", {"threshold": 51}, "wrist_hr_at_threshold", "wrist_hr_at_threshold", True),
    ("threshold_one_below_max_is_computed", {"threshold": 189}, None, None, True),
    ("threshold_at_the_average_is_at_threshold", {"threshold": 150}, "wrist_hr_at_threshold", "wrist_hr_at_threshold", True),
    ("threshold_one_above_the_average_is_computed", {"threshold": 151}, None, None, True),
    # hr_source: only chest_strap escapes the wrist path
    ("chest_strap_at_threshold_is_computed", {"hr_source": "chest_strap", "threshold": 150}, None, None, False),
    ("chest_strap_without_threshold_is_no_threshold_hr", {"hr_source": "chest_strap", "threshold": None}, None, "no_threshold_hr", False),
    ("wrist_ppg_at_threshold", {"hr_source": "wrist_ppg", "threshold": 150}, "wrist_hr_at_threshold", "wrist_hr_at_threshold", True),
    ("null_source_at_threshold", {"hr_source": None, "threshold": 150}, "wrist_hr_at_threshold", "wrist_hr_at_threshold", True),
    ("unknown_source_at_threshold", {"hr_source": "optical_armband", "threshold": 150}, "wrist_hr_at_threshold", "wrist_hr_at_threshold", True),
    ("unknown_source_without_threshold", {"hr_source": "optical_armband", "threshold": None}, "wrist_hr_threshold_unknown", "wrist_hr_threshold_unknown", True),
    ("empty_string_source_below_threshold", {"hr_source": "", "threshold": 151}, None, None, True),
    # added at review with the 2026-10-09 ruling; it tests the reference step, not gates 7-8:
    # a threshold that passes gate 7 as an integer but is one float with resting: only session_load is withheld
    (
        "wrist_threshold_span_collapsed_in_float_is_not_representable",
        {"hr_source": "wrist_ppg", "resting": 2**53, "threshold": 2**53 + 1, "max_hr": 2**53 + 2**31, "heart_rate": float(2**53)},
        None,
        "not_representable",
        True,
    ),
    # the earlier gates win; the flag stays
    ("6_before_8_avg_above_max_and_above_threshold", {"heart_rate": 200.0, "threshold": 150}, "avg_hr_above_max", "avg_hr_above_max", True),
    ("5_before_7_order_conflict_without_threshold", {"resting": 190, "max_hr": 190, "threshold": None}, "order_conflict", "order_conflict", True),
    ("3_before_7_no_hr_without_threshold", {"heart_rate": None, "threshold": None}, "no_hr", "no_hr", True),
    ("1_before_7_not_running_carries_no_flag", {"sport": "other", "threshold": None}, "sport_not_running", "sport_not_running", False),
]


@pytest.mark.parametrize(
    ("overrides", "trimp_reason", "load_reason", "flagged"),
    [pytest.param(*row[1:], id=row[0]) for row in ADVERSARIAL_ROWS],
)
def test_adversarial_wrist_rows(overrides: dict, trimp_reason, load_reason, flagged: bool) -> None:
    body = compute_session_load(_synthetic(**overrides))
    got = _outcome(body)
    print(f"  {overrides} -> {got}")
    assert got == (trimp_reason, load_reason, flagged)
    assert (body["metrics"]["hr_trimp"]["value"] is not None) == (trimp_reason is None)
    assert (body["session_load"]["driver"] == "hr_trimp") == (body["session_load"]["value"] is not None)
    for reason in (trimp_reason, load_reason):
        assert reason is None or reason in session_load.SERVED_REASONS, reason
