"""T024: chest-strap RR reconstruction and artefact filtering.

Own file per T024's task notes (not shared with any other test file).

``dev_fields_run.fit`` is the only fixture in this repo's corpus that
carries ``hrv`` (#78) messages -- confirmed via a one-off ``fitdecode``
inspection: 3127 ``hrv`` messages with real beat-to-beat ``time``
arrays like ``(0.641, 0.634, None, None, None)``. Despite their names,
neither ``chest_strap_run.fit`` nor ``T024_chest_strap_HRV.fit``
contains any ``hrv`` message or RR-bearing developer field, so the
hrv-carrier path is exercised against ``dev_fields_run.fit``'s real
``fitdecode`` output and ``chest_strap_run.fit`` covers the
legitimate "no RR carrier present" case its real data actually is.

The developer-field carrier has no real fixture to test against (no
fixture in the corpus carries an RR-bearing developer field), so its
matching rules are covered with hand-built stand-in message objects --
the same accepted exception ``test_mapping_gps_degraded.py`` already
uses for the GPS-accuracy field. Those stand-ins deliberately assert
only on values the real ``fitdecode`` surface can actually produce.

There is no ``event`` (#21) carrier test because that carrier is not
implemented -- see ``rr_reconstruction``'s module docstring for why
(no FIT profile enum value exists for it, so a matching rule cannot be
written without inventing one).
"""

from __future__ import annotations

from pathlib import Path

import fitdecode

from runcoach_api.ingestion import fit_parser, mapping, rr_reconstruction

HRV_FIXTURE = Path(__file__).parent / "fixtures" / "dev_fields_run.fit"
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
    # Tier enum (§2.2.3), not the carrier -- see module docstring.
    assert all(iv.rr_source == "chest_strap_ecg" for iv in intervals)
    assert all(iv.rr_carrier == "hrv" for iv in intervals)
    # Contiguous 0-indexed seq, in merge order.
    assert [iv.seq for iv in intervals] == list(range(len(intervals)))
    # Plausible human RR range for a runner (this fixture's real
    # min/max, confirmed via inspection, is 374-897ms).
    for iv in intervals[:50]:
        assert 300 <= iv.rr_ms <= 2000


def test_rr_source_is_the_spec_tier_enum_not_the_carrier_name() -> None:
    # Regression: an earlier revision wrote "hrv" into rr_source, which
    # is not in §2.2.3's enum, so §3's chest_strap_ecg tier check would
    # have matched none of these rows.
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    intervals = rr_reconstruction.reconstruct(messages)

    assert {iv.rr_source for iv in intervals} == {"chest_strap_ecg"}


def test_rr_valid_fraction_reflects_surviving_proportion_on_real_fixture() -> None:
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    intervals = rr_reconstruction.reconstruct(messages)

    n_artefact = sum(1 for iv in intervals if iv.is_artefact)
    assert n_artefact > 0, "fixture should exercise the artefact path at least once"
    # A real chest-strap recording should be overwhelmingly valid.
    assert rr_reconstruction.valid_fraction(intervals) > 0.95


def test_hr_source_set_to_chest_strap_when_rr_data_present() -> None:
    messages = fit_parser.decode(_raw(HRV_FIXTURE))

    session, _records = mapping.to_canonical(messages)

    assert session.hr_source == "chest_strap"


# ---------------------------------------------------------------------------
# Real fixture: no RR carrier present (a real, non-error outcome)
# ---------------------------------------------------------------------------


def test_no_rr_carrier_present_returns_empty_list_on_real_fixture() -> None:
    # chest_strap_run.fit is a real Garmin export that, despite its
    # name, carries no hrv message and no RR developer field --
    # reconstruct() must not fabricate data, just return [].
    messages = fit_parser.decode(_raw(NO_RR_FIXTURE))

    assert rr_reconstruction.reconstruct(messages) == []


def test_hr_source_left_for_wrist_ppg_default_when_no_rr_carrier() -> None:
    messages = fit_parser.decode(_raw(NO_RR_FIXTURE))

    session, _records = mapping.to_canonical(messages)

    # Not clobbered to "chest_strap" -- quality_gates.apply()'s
    # not-already-set default fills this in as "wrist_ppg" later.
    assert session.hr_source is None


# ---------------------------------------------------------------------------
# Artefact filtering (synthetic RR array -- pure math, no fitdecode
# parsing behavior involved; sanctioned by T024's own Test Strategy)
# ---------------------------------------------------------------------------


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    Only implements what ``rr_reconstruction`` actually reads:
    ``.name``, ``get_value(name, fallback=None)`` and ``.fields``.
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


def _dev_field(name: str, units: str | None = "ms") -> fitdecode.types.DevField:
    return fitdecode.types.DevField(
        dev_data_index=0, name=name, def_num=0, type=None, units=units, native_field_num=None
    )


def test_implausibly_short_interval_flagged_below_absolute_minimum() -> None:
    # 11 values: ten plausible ~800ms beats plus one 50ms outlier.
    seconds = (0.80, 0.80, 0.80, 0.80, 0.80, 0.05, 0.80, 0.80, 0.80, 0.80, 0.80)

    intervals = rr_reconstruction.reconstruct([_FakeMsg("hrv", values={"time": seconds})])

    assert len(intervals) == 11
    assert intervals[5].rr_ms == 50.0
    assert intervals[5].is_artefact is True
    assert rr_reconstruction.valid_fraction(intervals) == 10 / 11


def test_interval_deviating_from_local_median_flagged_even_within_absolute_range() -> None:
    # One 1000ms beat among steady 800ms beats: 25% deviation from the
    # local median. Both values are individually inside the 300-2000ms
    # band, so only the local-median check can catch this.
    seconds = (0.80, 0.80, 0.80, 0.80, 0.80, 1.00, 0.80, 0.80, 0.80, 0.80, 0.80)

    intervals = rr_reconstruction.reconstruct([_FakeMsg("hrv", values={"time": seconds})])

    assert intervals[5].rr_ms == 1000.0
    assert intervals[5].is_artefact is True
    assert all(iv.is_artefact is False for iv in intervals if iv.seq != 5)


def test_sentinel_none_slots_discarded_not_treated_as_zero() -> None:
    intervals = rr_reconstruction.reconstruct(
        [_FakeMsg("hrv", values={"time": (0.80, None, 0.80, None, None)})]
    )

    assert len(intervals) == 2
    assert all(iv.rr_ms == 800.0 for iv in intervals)


def test_scalar_time_value_handled_not_iterated_as_float() -> None:
    # fitdecode returns a bare scalar (not a tuple) when an array field
    # carries exactly one element -- see reader.py's
    # "elif len(raw_value) > 1: tuple(...) else: base_type.parse(...)".
    # An hrv message defined with one time slot is legal FIT, and
    # iterating the float used to raise TypeError as an unhandled 500.
    intervals = rr_reconstruction.reconstruct([_FakeMsg("hrv", values={"time": 0.80})])

    assert len(intervals) == 1
    assert intervals[0].rr_ms == 800.0


def test_valid_fraction_of_empty_list_is_zero_not_a_division_error() -> None:
    assert rr_reconstruction.valid_fraction([]) == 0.0


# ---------------------------------------------------------------------------
# Developer-field carrier: matching rules (synthetic -- no real fixture
# carries an RR-bearing developer field, see module docstring)
# ---------------------------------------------------------------------------


def test_developer_field_carrier_merges_with_hrv_in_file_order() -> None:
    hrv_msg = _FakeMsg("hrv", values={"time": (0.80, 0.81)})
    rr_field = _FakeFieldData(field=_dev_field("RR Interval"), name="RR Interval", value=900)
    record_msg = _FakeMsg("record", fields=[rr_field])

    intervals = rr_reconstruction.reconstruct([hrv_msg, record_msg])

    assert [(iv.rr_ms, iv.rr_carrier) for iv in intervals] == [
        (800.0, "hrv"),
        (810.0, "hrv"),
        (900.0, "developer_field"),
    ]
    assert [iv.seq for iv in intervals] == [0, 1, 2]
    # Carrier differs per beat; the tier enum stays constant.
    assert {iv.rr_source for iv in intervals} == {"chest_strap_ecg"}


def test_developer_field_name_match_is_word_bounded_not_substring() -> None:
    # A bare "rr" substring previously matched all of these, injecting
    # a power/elevation value into the RR stream as milliseconds *and*
    # flipping hr_source to chest_strap, which un-gates HR metrics on a
    # wrist-only session (§2.4.2).
    for name in ("Corrected Power", "Horizontal Error", "Terrain", "Current Lap Pace"):
        field = _FakeFieldData(field=_dev_field(name, units=None), name=name, value=250)
        record_msg = _FakeMsg("record", fields=[field])

        assert rr_reconstruction.reconstruct([record_msg]) == [], name


def test_developer_field_with_non_millisecond_units_ignored() -> None:
    # Name matches, but the field declares watts -- not a beat interval.
    field = _FakeFieldData(field=_dev_field("RR Power", units="W"), name="RR Power", value=250)

    assert rr_reconstruction.reconstruct([_FakeMsg("record", fields=[field])]) == []


def test_non_numeric_developer_field_value_skipped_not_coerced() -> None:
    # Developer fields can legally carry strings and arrays; float() on
    # those used to raise out of the pipeline as an unhandled 500.
    for bad_value in ("high", (800, 810), None):
        field = _FakeFieldData(field=_dev_field("RR"), name="RR", value=bad_value)

        assert rr_reconstruction.reconstruct([_FakeMsg("record", fields=[field])]) == []


def test_native_non_developer_field_never_treated_as_carrier() -> None:
    native = _FakeFieldData(field=object(), name="rr_placeholder", value=800)

    assert rr_reconstruction.reconstruct([_FakeMsg("record", fields=[native])]) == []


def test_event_messages_are_not_a_carrier() -> None:
    # The event (#21) carrier is deliberately unimplemented: the FIT
    # profile has no RR-related event enum value, so no honest matching
    # rule exists. Documents the current contract so a future
    # implementation has to update this test consciously.
    event_msg = _FakeMsg("event", values={"event": "timer", "data": 850})

    assert rr_reconstruction.reconstruct([event_msg]) == []
