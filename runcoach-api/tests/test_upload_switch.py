"""``POST /sessions`` with ``runs_and_captures_only``: F022 AC1, reference section 4.

With the switch the upload is screened as the archive import screens a file:
the ``file_id`` first (``imports.archive.screen_file_id``), then the classified
session through ``imports.job.keep_session``. A file the rule does not keep is a
200 ``IngestSkipped`` and nothing is stored; a kept file is the usual 201, 409 or
400, with ``filter_applied: true`` on the 201. Without the switch every
activity's reply is as it was before the field existed (an HRV_STATUS file takes
the night path either way, F028).

Fixtures, by their provenance rows in ``tests/fixtures/README.md``:
``sample_run`` (a real run, run proof yes); ``sample_health_snapshot`` (a Health
Snapshot); ``strap_hrv_capture`` (a strap HRV check on the "HRV Snapshot"
profile, kept only when that profile is declared). The ski day is
``sample_run`` with its session sport patched to 13 (``alpine_skiing``), and the
monitoring file a minimal FIT whose ``file_id.type`` is 32 (``monitoring_b``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)
IA = import_archive

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE_PROFILE = "HRV Snapshot"
# The FIT session message (18) and its sport field (5); 13 is alpine_skiing.
SESSION_MESSAGE = 18
SESSION_SPORT_FIELD = 5
SPORT_ALPINE_SKIING = 13
MONITORING_B = 32
SWITCH = {"runs_and_captures_only": "true"}


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _post(client: TestClient, raw: bytes, data: dict | None = None):
    return client.post("/sessions", files={"file": ("upload.fit", raw)}, data=data)


def _session_count() -> int:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


def test_switch_monitoring_b_returns_200_non_activity_and_stores_nothing() -> None:
    with TestClient(app) as client:
        response = _post(client, IA.minimal_fit(MONITORING_B), SWITCH)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "skipped": True,
            "reason": "non_activity",
            "file_type": "monitoring_b",
            "filter_applied": True,
        }
        assert _session_count() == 0


def test_switch_non_activity_never_decodes_past_file_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-activity reply is decided by the screen alone: the pipeline is never called."""

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("ingest_fit_bytes ran for a non-activity file")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", must_not_run)
    with TestClient(app) as client:
        response = _post(client, IA.minimal_fit(44), SWITCH)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "skipped": True,
        "reason": "non_activity",
        "file_type": "44",
        "filter_applied": True,
    }


def test_switch_rejected_activity_returns_200_skipped_sport_with_the_fit_sport() -> None:
    ski_day = IA.fit_patch.set_field(
        _fixture("sample_run.fit"), SESSION_MESSAGE, SESSION_SPORT_FIELD, SPORT_ALPINE_SKIING
    )
    with TestClient(app) as client:
        response = _post(client, ski_day, SWITCH)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "skipped": True,
            "reason": "skipped_sport",
            "sport": "alpine_skiing",
            "filter_applied": True,
        }
        assert _session_count() == 0
        # The same file without the switch is stored as today: the switch alone refused it.
        unfiltered = _post(client, ski_day)
        assert unfiltered.status_code == 201, unfiltered.text
        assert set(unfiltered.json()) == {"session_id", "quality_flags"}
        assert _session_count() == 1


def test_switch_keeps_a_run_a_snapshot_and_a_configured_strap_check_with_filter_applied(
    declared_config,
) -> None:
    declared_config(CAPTURE_PROFILE)
    with TestClient(app) as client:
        for count, name in enumerate(
            ("sample_run.fit", "sample_health_snapshot.fit", "strap_hrv_capture.fit"), start=1
        ):
            response = _post(client, _fixture(name), SWITCH)
            assert response.status_code == 201, (name, response.text)
            body = response.json()
            assert set(body) == {"session_id", "quality_flags", "filter_applied"}, name
            assert body["filter_applied"] is True, name
            assert client.get(f"/sessions/{body['session_id']}").status_code == 200, name
            assert _session_count() == count, name


def test_switch_skips_an_undeclared_strap_check() -> None:
    """With no profile declared the strap check is neither a run nor a recognised capture."""
    with TestClient(app) as client:
        response = _post(client, _fixture("strap_hrv_capture.fit"), SWITCH)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["skipped"] is True and body["reason"] == "skipped_sport"
        assert body["filter_applied"] is True
        assert _session_count() == 0


def test_switch_duplicate_is_409() -> None:
    run = _fixture("sample_run.fit")
    with TestClient(app) as client:
        first = _post(client, run, SWITCH)
        assert first.status_code == 201, first.text
        second = _post(client, run, SWITCH)
        assert second.status_code == 409, second.text
        assert first.json()["session_id"] in second.json()["detail"]
        assert _session_count() == 1


def test_switch_typeless_file_id_is_400() -> None:
    with TestClient(app) as client:
        response = _post(client, IA.minimal_fit(0xFF), SWITCH)
        assert response.status_code == 400, response.text
        assert response.json() == {"detail": "file_id carries no type"}
        text = _post(client, b"this is not a FIT file at all\n", SWITCH)
        assert text.status_code == 400, text.text
        assert text.json()["detail"].startswith("file_id could not be read: ")
        empty = _post(client, b"", SWITCH)
        assert empty.status_code == 400, empty.text
        assert _session_count() == 0


def test_switch_with_resting_capture_is_422() -> None:
    with TestClient(app) as client:
        response = _post(
            client,
            _fixture("sample_run.fit"),
            {"runs_and_captures_only": "true", "resting_capture": "true"},
        )
        assert response.status_code == 422, response.text
        locs = [tuple(error["loc"]) for error in response.json()["detail"]]
        assert ("body", "runs_and_captures_only") in locs
        assert _session_count() == 0
        # Either flag alone is accepted.
        alone = _post(
            client, _fixture("sample_run.fit"), {"runs_and_captures_only": "false", "resting_capture": "true"}
        )
        assert alone.status_code == 201, alone.text


@pytest.mark.parametrize("value", ["maybe", "2", ""])
def test_switch_value_maybe_is_422(value: str) -> None:
    with TestClient(app) as client:
        response = _post(client, _fixture("sample_run.fit"), {"runs_and_captures_only": value})
        if value == "":
            # An empty form value is the field left out (FastAPI's form handling): the default, false.
            assert response.status_code == 201, response.text
            assert set(response.json()) == {"session_id", "quality_flags"}
            return
        assert response.status_code == 422, response.text
        locs = [tuple(error["loc"]) for error in response.json()["detail"]]
        assert ("body", "runs_and_captures_only") in locs
        assert _session_count() == 0


@pytest.mark.parametrize(
    "data", [None, {"runs_and_captures_only": "false"}, {"runs_and_captures_only": "no"}]
)
def test_switch_false_replies_exactly_as_today(data: dict | None) -> None:
    with TestClient(app) as client:
        stored = _post(client, _fixture("sample_run.fit"), data)
        assert stored.status_code == 201, stored.text
        # The pre-switch 201 shape: no filter_applied key at all, not a false one.
        assert set(stored.json()) == {"session_id", "quality_flags"}
        duplicate = _post(client, _fixture("sample_run.fit"), data)
        assert duplicate.status_code == 409, duplicate.text
        # A monitoring file is not screened without the switch: it fails decoding as today.
        monitoring = _post(client, IA.minimal_fit(MONITORING_B), data)
        assert monitoring.status_code == 400, monitoring.text
        assert "skipped" not in monitoring.json()


def test_switch_over_cap_body_is_413_before_the_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    import runcoach_api.main as main_module

    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 16)
    with TestClient(app) as client:
        response = _post(client, IA.minimal_fit(MONITORING_B), SWITCH)
    assert response.status_code == 413, response.text
