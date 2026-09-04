"""T031: the §2.4.1 recording-mode gate (``recording_interval`` descriptor).

Real-fixture coverage for the ``1hz`` classification -- per
``.claude/rules/project-testing.md`` this is fitdecode timestamp
behaviour, so it is asserted against the real user-supplied ``.fit``
corpus, never a mocked byte stream. The synthetic smart/irregular path
lives in ``test_quality_gates_smart_recording.py`` (no smart-recorded
fixture exists in the corpus).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

RUNNING_FIXTURES = ("chest_strap_run.fit", "sample_run.fit", "dev_fields_run.fit")


@pytest.mark.parametrize("fixture_name", RUNNING_FIXTURES)
def test_real_running_fixture_classifies_as_1hz(fixture_name: str) -> None:
    """All three real Garmin running exports are 1Hz recordings: a
    handful of auto-pause/dropped-sample gaps (2 of ~2000 at worst)
    must not tip the predominance rule into ``smart``.
    """
    raw = (FIXTURES / fixture_name).read_bytes()

    with TestClient(app) as client:
        response = client.post("/sessions", files={"file": (fixture_name, raw)})
        assert response.status_code == 201, response.text
        session_id = response.json()["session_id"]
        detail = client.get(f"/sessions/{session_id}")

    assert detail.status_code == 200
    body = detail.json()
    # Survives the round trip through GET /sessions/{id} as the
    # §2.2.3 descriptor string, not a float and not null.
    assert body["recording_interval"] == "1hz"
    assert "smart_recording" not in body["quality_flags"]
    # POST's own quality-flag summary agrees with the persisted session.
    assert "smart_recording" not in response.json()["quality_flags"]


@pytest.mark.parametrize("fixture_name", RUNNING_FIXTURES)
def test_recording_interval_is_a_string_descriptor(fixture_name: str) -> None:
    raw = (FIXTURES / fixture_name).read_bytes()

    with TestClient(app) as client:
        response = client.post("/sessions", files={"file": (fixture_name, raw)})
        session_id = response.json()["session_id"]
        body = client.get(f"/sessions/{session_id}").json()

    assert isinstance(body["recording_interval"], str)
    assert body["recording_interval"] in {"1hz", "smart", "irregular"}
