"""T025: vendor-derived inference values are routed to the quarantine
sidecar, never into the canonical schema.

Own file per T025's task notes (not shared with any other test file).
Uses the real fixture (``tests/fixtures/sample_run.fit``) per
``.claude/rules/project-testing.md`` -- ``quarantine.extract`` is
exercised against real ``fitdecode``-decoded messages, never mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion import fit_parser, quarantine
from runcoach_api.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"
TRAINING_EFFECT_FIXTURE = Path(__file__).parent / "fixtures" / "wrist_ppg_run.fit"
STRESS_FIXTURE = Path(__file__).parent / "fixtures" / "sample_health_snapshot.fit"


def _raw_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _raw_training_effect_fixture_bytes() -> bytes:
    return TRAINING_EFFECT_FIXTURE.read_bytes()


def _raw_stress_fixture_bytes() -> bytes:
    return STRESS_FIXTURE.read_bytes()


# ---------------------------------------------------------------------------
# extract() against the real fixture's decoded messages
# ---------------------------------------------------------------------------


def test_extract_finds_training_load_peak_on_real_fixture() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())

    result = quarantine.extract(messages)

    assert "training_load_peak" in result
    assert result["training_load_peak"] not in (None, "")
    # Known-good value for this fixture's session message (verified via a
    # one-off fitdecode inspection: session.training_load_peak == 128.06...).
    assert float(result["training_load_peak"]) == 128.06283569335938


def test_extract_finds_training_effect_fields_on_real_fixture() -> None:
    # wrist_ppg_run.fit's session message carries real non-null
    # Garmin Training Effect values (verified via a one-off fitdecode
    # inspection: total_training_effect == 2.8,
    # total_anaerobic_training_effect == 0.0).
    messages = fit_parser.decode(_raw_training_effect_fixture_bytes())

    result = quarantine.extract(messages)

    assert float(result["total_training_effect"]) == 2.8
    assert float(result["total_anaerobic_training_effect"]) == 0.0


def test_extract_finds_stress_score_on_real_fixture() -> None:
    # "Stress score" is a named §2.3.6 quarantine item.
    # sample_health_snapshot.fit's session message carries a real
    # non-null avg_stress (19), profile-resolved as session field 195
    # -- not an unknown_NNN guess.
    messages = fit_parser.decode(_raw_stress_fixture_bytes())

    result = quarantine.extract(messages)

    assert float(result["avg_stress"]) == 19


def test_stress_score_never_reaches_the_canonical_schema() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions",
            files={"file": ("sample_health_snapshot.fit", _raw_stress_fixture_bytes())},
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body_lower = str(get_response.json()).lower()
    assert "avg_stress" not in body_lower
    assert "current_stress" not in body_lower


def test_extract_returns_plain_string_values() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())

    result = quarantine.extract(messages)

    for field_name, value in result.items():
        assert isinstance(field_name, str)
        assert isinstance(value, str)


# ---------------------------------------------------------------------------
# canonical schema stays clean -- vendor-derived values never leak into
# GET /sessions/{id}
# ---------------------------------------------------------------------------


def test_canonical_get_response_never_contains_vendor_derived_fields() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body_lower = str(get_response.json()).lower()
    assert "training_load_peak" not in body_lower
    assert "vo2max" not in body_lower
    assert "training_status" not in body_lower


# ---------------------------------------------------------------------------
# the quarantine_sidecar table is where the value actually lands
# ---------------------------------------------------------------------------


def test_quarantine_sidecar_table_receives_training_load_peak() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions", files={"file": ("sample_run.fit", _raw_fixture_bytes())}
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT field_name, value FROM quarantine_sidecar WHERE session_id = ?",
            (session_id,),
        )
        rows = {field_name: value for field_name, value in cur.fetchall()}
    finally:
        conn.close()

    assert "training_load_peak" in rows
    # db.persist() stores quarantine values via json.dumps(...) (see
    # db._insert_quarantine_sidecar), so read it back the same way.
    stored_value = json.loads(rows["training_load_peak"])
    assert float(stored_value) == 128.06283569335938


def test_canonical_get_response_never_contains_training_effect() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions",
            files={"file": ("wrist_ppg_run.fit", _raw_training_effect_fixture_bytes())},
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body = get_response.json()
    body_lower = str(body).lower()
    assert "training_effect" not in body_lower

    # T036 item 2 -- confirm the inferred hr_source survives the full
    # round trip, not just the quarantine check above. This file has no
    # RR carrier, but its HR came from a connected ANT+ heart-rate strap
    # (the name is historical; see the fixtures README), so spec/02
    # section 2.4.2 step 1 reads it as chest_strap, not the wrist default.
    assert body["hr_source"] == "chest_strap"
