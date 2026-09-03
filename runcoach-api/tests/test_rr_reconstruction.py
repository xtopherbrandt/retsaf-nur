"""T024: chest-strap RR reconstruction and artefact filtering.

Own file per T024's task notes (not shared with any other test file).

``Fr955-Stryd-running.fit`` is the only fixture in this repo's corpus
that actually carries ``hrv`` (#78) messages (confirmed via a one-off
``fitdecode`` inspection before writing this file: 3127 ``hrv``
messages, real beat-to-beat ``time`` arrays like ``(0.641, 0.634,
None, None, None)``) -- despite their names, neither
``chest_strap_run.fit`` nor ``T024_chest_strap_HRV.fit`` contains any
``hrv`` message, RR-carrying ``event`` message, or RR-related
developer field (same inspection, zero hits on all three carriers).
Per ``.claude/rules/project-testing.md``, the hrv-message carrier path
is therefore exercised against ``Fr955-Stryd-running.fit``'s real
``fitdecode`` output; ``chest_strap_run.fit`` instead covers the
legitimate "no RR carrier present" case its real data actually is.
The event-message and developer-field carrier paths have no real
fixture in this repo's corpus to test against (same module-docstring
caveat as ``rr_reconstruction.py``), so they're covered with hand-built
stand-in message objects -- the same accepted exception
``test_mapping_gps_degraded.py`` already uses for the GPS-accuracy
field.
"""

from __future__ import annotations

from pathlib import Path

import fitdecode

from runcoach_api.ingestion import fit_parser, mapping, rr_reconstruction

HRV_FIXTURE = Path(__file__).parent / "fixtures" / "Fr955-Stryd-running.fit"
NO_RR_FIXTURE = Path(__file__).parent / "fixtures" / "chest_strap_run.fit"


def _raw(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Real fixture: hrv-message carrier
# ---------------------------------------------------------------------------


def test_hrv_message_carrier_reconstructed_from_real_fixture() -> None:
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    intervals = rr_reconstruction.reconstruct(messages)

    assert len(intervals) > 1000
    assert all(iv.rr_source == "hrv" for iv in intervals)
    # Contiguous 0-indexed seq, in merge order.
    assert [iv.seq for iv in intervals] == list(range(len(intervals)))
    # Plausible human RR range for a runner (this fixture's real
    # min/max, confirmed via inspection, is 374-897ms).
    for iv in intervals[:50]:
        assert 300 <= iv.rr_ms <= 2000


def test_rr_valid_fraction_reflects_surviving_proportion_on_real_fixture() -> None:
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    intervals = rr_reconstruction.reconstruct(messages)
    fraction = rr_reconstruction.valid_fraction(intervals)

    n_artefact = sum(1 for iv in intervals if iv.is_artefact)
    assert fraction == (len(intervals) - n_artefact) / len(intervals)
    # A real chest-strap recording should be overwhelmingly valid.
    assert fraction > 0.95


def test_hr_source_set_to_chest_strap_when_rr_data_present() -> None:
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    session, _records = mapping.to_canonical(messages)

    assert session.hr_source == "chest_strap"


def test_full_ingest_of_hrv_fixture_succeeds_end_to_end() -> None:
    from fastapi.testclient import TestClient

    from runcoach_api.main import app

    with TestClient(app) as client:
        response = client.post(
            "/sessions", files={"file": ("Fr955-Stryd-running.fit", _raw(HRV_FIXTURE))}
        )

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Real fixture: no RR carrier present (a real, non-error outcome)
# ---------------------------------------------------------------------------


def test_no_rr_carrier_present_returns_empty_list_on_real_fixture() -> None:
    # chest_strap_run.fit is a real Garmin export that, despite its
    # name, carries no hrv message, RR-carrying event, or RR developer
    # field -- reconstruct() must not fabricate data, just return [].
    messages = fit_parser.decode(_raw(NO_RR_FIXTURE))

    intervals = rr_reconstruction.reconstruct(messages)

    assert intervals == []


def test_hr_source_left_for_wrist_ppg_default_when_no_rr_carrier() -> None:
    messages = fit_parser.decode(_raw(NO_RR_FIXTURE))

    session, _records = mapping.to_canonical(messages)

    # Not clobbered to "chest_strap" -- quality_gates.apply()'s
    # not-already-set default fills this in as "wrist_ppg" later in
    # the pipeline.
    assert session.hr_source is None


# ---------------------------------------------------------------------------
# Artefact filtering (synthetic RR array -- pure math, no fitdecode
# parsing behavior involved; sanctioned by T024's own Test Strategy)
# ---------------------------------------------------------------------------


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    Only implements what ``rr_reconstruction`` actually reads:
    ``get_value(name, fallback=None)`` and ``.fields``.
    """

    def __init__(self, name, values=None, fields=None):
        self.name = name
        self._values = values or {}
        self.fields = fields or []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


class _FakeFieldData:
    def __init__(self, field, name, value):
        self.field = field
        self.name = name
        self.value = value


def _dev_field(name: str) -> fitdecode.types.DevField:
    return fitdecode.types.DevField(
        dev_data_index=0, name=name, def_num=0, type=None, units=None, native_field_num=None
    )


def test_implausibly_short_interval_flagged_below_absolute_minimum() -> None:
    # 11 values: ten plausible ~800ms beats plus one 50ms outlier.
    seconds = (0.80, 0.80, 0.80, 0.80, 0.80, 0.05, 0.80, 0.80, 0.80, 0.80, 0.80)
    hrv_msg = _FakeMsg("hrv", values={"time": seconds})

    intervals = rr_reconstruction.reconstruct([hrv_msg])

    assert len(intervals) == 11
    outlier = intervals[5]
    assert outlier.rr_ms == 50.0
    assert outlier.is_artefact is True
    assert rr_reconstruction.valid_fraction(intervals) == 10 / 11


def test_interval_deviating_from_local_median_flagged_even_within_absolute_range() -> None:
    # 11 values: ten steady ~800ms beats plus one 1000ms beat (25%
    # deviation from the local 800ms median) -- both endpoints are
    # individually plausible (300-2000ms), only the local-median check
    # should catch this one.
    seconds = (0.80, 0.80, 0.80, 0.80, 0.80, 1.00, 0.80, 0.80, 0.80, 0.80, 0.80)
    hrv_msg = _FakeMsg("hrv", values={"time": seconds})

    intervals = rr_reconstruction.reconstruct([hrv_msg])

    deviant = intervals[5]
    assert deviant.rr_ms == 1000.0
    assert deviant.is_artefact is True
    assert all(iv.is_artefact is False for iv in intervals if iv.seq != 5)


def test_sentinel_none_slots_discarded_not_treated_as_zero() -> None:
    hrv_msg = _FakeMsg("hrv", values={"time": (0.80, None, 0.80, None, None)})

    intervals = rr_reconstruction.reconstruct([hrv_msg])

    assert len(intervals) == 2
    assert all(iv.rr_ms == 800.0 for iv in intervals)


def test_valid_fraction_of_empty_list_is_zero_not_a_division_error() -> None:
    assert rr_reconstruction.valid_fraction([]) == 0.0


# ---------------------------------------------------------------------------
# Multi-carrier merge + provenance (synthetic messages -- no real
# fixture exercises the event/dev-field carriers, see module docstring)
# ---------------------------------------------------------------------------


def test_multi_carrier_merge_preserves_file_order_and_provenance() -> None:
    hrv_msg = _FakeMsg("hrv", values={"time": (0.80, 0.81)})
    event_rr_msg = _FakeMsg("event", values={"event": "rr_interval", "data": 850})
    event_other_msg = _FakeMsg("event", values={"event": "timer", "event_type": "start"})
    dev_field_rr = _FakeFieldData(field=_dev_field("RR Interval"), name="RR Interval", value=900)
    dev_field_other = _FakeFieldData(field=_dev_field("Power"), name="Power", value=250)
    native_field = _FakeFieldData(field=object(), name="heart_rate", value=150)
    record_msg = _FakeMsg("record", fields=[dev_field_rr, dev_field_other, native_field])

    messages = [hrv_msg, event_rr_msg, event_other_msg, record_msg]

    intervals = rr_reconstruction.reconstruct(messages)

    assert [(iv.rr_ms, iv.rr_source) for iv in intervals] == [
        (800.0, "hrv"),
        (810.0, "hrv"),
        (850.0, "event"),
        (900.0, "developer_field"),
    ]
    assert [iv.seq for iv in intervals] == [0, 1, 2, 3]


def test_event_message_without_rr_interval_type_ignored() -> None:
    event_msg = _FakeMsg("event", values={"event": "recovery_hr", "data": 92})

    intervals = rr_reconstruction.reconstruct([event_msg])

    assert intervals == []


def test_developer_field_without_rr_in_name_ignored() -> None:
    field = _FakeFieldData(field=_dev_field("Ground Time"), name="Ground Time", value=245)
    record_msg = _FakeMsg("record", fields=[field])

    intervals = rr_reconstruction.reconstruct([record_msg])

    assert intervals == []
