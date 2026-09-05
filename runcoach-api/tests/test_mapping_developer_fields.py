"""T022: decode developer fields (running dynamics) into canonical
record fields.

Scoped exclusively to developer-field resolution in
``mapping.to_canonical()`` -- native-field mapping is out of scope
(already correct from T019). Own fixture, own file (see T019's note on
avoiding shared-test-filename collisions).

The fixture (``tests/fixtures/dev_fields_run.fit``) is real, user-
supplied FIT data from a Garmin Forerunner 955 + Stryd pod export --
per ``.claude/rules/project-testing.md`` this decodes it for real,
never mocks ``fitdecode``.

Investigated directly via a one-off ``fitdecode`` inspection before
writing this file:

- ``fitdecode`` already resolves developer field names onto
  ``FieldData.name`` (e.g. ``"Ground Time"``, ``"Vertical
  Oscillation"``) -- no manual ``developer_data_id``/
  ``field_description`` lookup is needed, and ``get_value()`` accepts
  those resolved names directly, same as native field names.
- This fixture's native ``vertical_oscillation`` field (present on
  3114/3118 records, from the watch's own running-dynamics sensor) is
  reported by fitdecode in **millimeters** (e.g. 82.0, 89.3, ...) --
  the developer field ``"Vertical Oscillation"`` is in **centimeters**
  (e.g. 7.5, 11.125, ...) per its FIT ``field_description`` units.
  Since native wins on precedence and the two must be comparable, the
  developer-field value needs a x10 conversion to millimeters when it
  is the one used.
- ``"Ground Time"`` (developer field, Milliseconds) and native
  ``stance_time`` (also ms) already share units -- no conversion
  needed there.
- Records 498-501 (1-indexed) are native-vertical-oscillation-absent
  but developer-field-present -- used below as the gap-fill precedence
  case.
- ``gct_balance`` (native ``stance_time_percent``) and ``step_length``
  are not exercised by this task's developer-field work: this fixture
  does not broadcast ``gct_balance`` as a developer field at all (dev
  field precedence is a no-op here), and ``step_length`` already
  arrives as a *native* field (unrelated, prior task).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "dev_fields_run.fit"


def _raw_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_developer_fields_resolve_into_canonical_running_dynamics() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())
    session, records = mapping.to_canonical(messages)

    assert session.sport == "running"
    assert len(records) > 0

    vo_records = [r for r in records if r.vertical_oscillation is not None]
    gct_records = [r for r in records if r.ground_contact_time is not None]

    assert len(vo_records) > 100
    assert len(gct_records) > 100

    # Plausible running ranges. vertical_oscillation is in millimeters
    # (matching the native field's unit, see module docstring) so the
    # window is 10x the "5-15cm" guidance in centimeters.
    for r in vo_records[:20]:
        assert 30 <= r.vertical_oscillation <= 200, r.vertical_oscillation

    for r in gct_records[:20]:
        assert 100 <= r.ground_contact_time <= 500, r.ground_contact_time


def test_gct_balance_and_step_length_not_fabricated_from_absent_dev_fields() -> None:
    # This fixture's Stryd pod does not broadcast gct_balance as a
    # developer field at all -- confirms the implementation doesn't
    # invent a value for a field with nothing to resolve.
    messages = fit_parser.decode(_raw_fixture_bytes())
    _session, records = mapping.to_canonical(messages)

    assert all(r.gct_balance is None for r in records)


def test_developer_field_fills_gap_when_native_field_absent() -> None:
    # Records 498-501 (1-indexed record-message order) have no native
    # vertical_oscillation but do carry the Stryd developer field
    # ("Vertical Oscillation" = 7.5cm on the first two of them) --
    # confirms the developer-field path fills genuine gaps, converted
    # to the native field's millimeter unit (7.5cm -> 75.0mm).
    messages = fit_parser.decode(_raw_fixture_bytes())
    _session, records = mapping.to_canonical(messages)

    record_msgs = [m for m in messages if m.name == "record"]
    gap_record_msg = record_msgs[497]  # 0-indexed 497 == 1-indexed 498
    gap_ts = gap_record_msg.get_value("timestamp", fallback=None)

    expected_t = (gap_ts - _session_start(messages)).total_seconds()
    gap_record = next(r for r in records if r.t == expected_t)
    assert gap_record.vertical_oscillation == 75.0


def _session_start(messages):
    from runcoach_api.ingestion import mapping as mapping_mod

    session_msg = mapping_mod._first_named(messages, "session")
    return session_msg.get_value("start_time", fallback=None)


def test_unresolvable_developer_fields_preserved_in_provenance_not_dropped() -> None:
    messages = fit_parser.decode(_raw_fixture_bytes())
    session, _records = mapping.to_canonical(messages)

    unresolved = session.context.provenance.get("unresolved_developer_fields")
    assert unresolved is not None
    assert "Power" in unresolved or "Form Power" in unresolved


def test_full_ingest_of_real_fixture_succeeds_end_to_end() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions", files={"file": ("dev_fields_run.fit", _raw_fixture_bytes())}
        )

        assert response.status_code == 201
        session_id = response.json()["session_id"]
        assert session_id

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body = get_response.json()

    # T036 item 1 -- confirm the chest-strap RR reconstruction survives
    # the full mapping -> persist -> GET round trip, not just the unit
    # level. Exact string: mapping._infer_hr_source sets "chest_strap"
    # (mapping.py:252-266); "chest_strap_ecg" is RRInterval.rr_source's
    # tier enum, a different field entirely.
    assert body["hr_source"] == "chest_strap"

    # This fixture's hrv messages are the carrier -- confirm rr_carrier
    # provenance made it through persistence onto at least one interval.
    assert any(iv["rr_carrier"] == "hrv" for iv in body["rr_intervals"])

    assert body["rr_valid_fraction"] is not None
    assert 0.0 <= body["rr_valid_fraction"] <= 1.0
