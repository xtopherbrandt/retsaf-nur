"""T045: Tier 1 outranks Tier 2 on a capture carrying both signals (F004 §3).

F004 `@should`: "Raw beats outrank a device-supplied value on the same capture".
§2.4.5 orders the source hierarchy **highest-fidelity-first**, and Tier 1 is the
fully-owned computation -- the system reduced the beats itself, through its own
artefact filter, rather than trusting a black-box scalar. So a file carrying
*both* `hrv` (#78) beat-to-beat arrays satisfying the Tier-1 discriminator *and*
a session `rmssd_hrv` resolves to `chest_strap_raw`, computes its own rMSSD,
leaves `rmssd_precomputed` `None`, and records the unused device value in
provenance under `unused_device_rmssd_hrv` rather than throwing it away.

**These scenarios are synthetic, and deliberately so.** *No fixture in the
corpus carries both raw beats and a session `rmssd_hrv`* -- `strap_hrv_sample_run.fit`
has 156 beats and no `rmssd_hrv`; `sample_health_snapshot.fit` (37) and
`strap_health_snapshot.fit` (51) carry the scalar and **zero** `hrv` messages,
strap paired or not. Fabricating FIT bytes is forbidden by
`.claude/rules/project-testing.md`, so the both-signals capture is built from
`_FakeMsg` stand-ins, the pattern `test_rr_reconstruction.py`,
`test_resting_hrv_tier2.py` and `test_resting_hrv_tier1.py` already use. That is
legitimate here because what is under test is *classifier logic over
already-mapped values*, not fitdecode parsing behaviour. Nothing in this module
should be read as claiming fixture coverage for the combination -- the real
fixtures appear only in the regression guards at the bottom, which pin the two
single-signal cases that *do* exist.

**Why this file exists at all when the branch order already decides it.** The
`return` after the Tier-1 branch in `classify()` gives Tier 1 structural
precedence today. That is an accident of statement order a later refactor could
silently invert, and it does nothing about the second half of the `@should`
clause -- the device value would simply vanish. These tests pin the precedence
as a contract and pin the audit record that proves what was ignored.

**The two provenance keys must never collide.** `computed_resting_rmssd_ms`
(T041) is *this system's* Tier-1 statistic; `unused_device_rmssd_hrv` (T045) is
the *device's* rejected scalar. They mean opposite things -- one is the reading,
one is what lost -- and they land in the same loosely typed
`Context.provenance` dict alongside T040's `hrv_signal_disagreement`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runcoach_api.ingestion import fit_parser, hrv_classification, mapping, rr_reconstruction
from runcoach_api.ingestion.rmssd import resting_rmssd
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The Tier-1 gate fixture: 156 beats, 150.797 s, 108.21 m, avg HR 60, and
# **no** rmssd_hrv. Do not rename it -- the name reads oddly because the capture
# was recorded on the running profile while lying still.
GATE_FIXTURE = "strap_hrv_sample_run.fit"

# T039's Tier-2 pair: both carry the device scalar and zero ``hrv`` messages.
SNAPSHOT_FIXTURES = {"sample_health_snapshot.fit": 37, "strap_health_snapshot.fit": 51}

# The device scalar the both-signals capture carries. Its exact value matters
# only in that it must reappear in provenance and never in ``rmssd_precomputed``.
DEVICE_RMSSD = 42

# The empirically observed Garmin Health Snapshot ``sport`` value -- the Tier-2
# identity signal. Present on the both-signals capture so Tier 2 would otherwise
# route it: precedence is only meaningful when the losing branch would have won.
SNAPSHOT_SPORT = 60

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    Same shape ``test_resting_hrv_tier1.py`` and ``test_resting_hrv_tier2.py``
    use: only ``.name``, ``get_value(name, fallback=None)`` and ``.fields`` are
    read by ``mapping.to_canonical`` and ``hrv_classification.classify``.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _synthetic(sport=SNAPSHOT_SPORT, **session_extra):
    """A two-message file whose ``session`` roll-up carries only what is passed.

    Routed through the real ``mapping.to_canonical``, so ``_build_summary``'s
    ``None``-stripping applies exactly as it does to a real file, and
    ``context.provenance["raw_sport_value"]`` is written by ``mapping.py``
    itself rather than injected by the test.
    """
    values = {"sport": sport, "start_time": _START}
    values.update(session_extra)
    return [
        _FakeMsg("session", values),
        _FakeMsg("record", {"timestamp": _START, "heart_rate": 60}),
    ]


def _resting_capture(**session_extra):
    """The gate fixture's measured profile, carrying a device ``rmssd_hrv`` too.

    150 s / 108 m (0.72 m/s) / avg HR 60 -- inside the Tier-1 discriminator and
    past the 120 s minimum-duration gate, so the capture yields a real reading
    rather than a flag.
    """
    values = {
        "total_timer_time": 150.0,
        "total_distance": 108.21,
        "avg_heart_rate": 60,
        "rmssd_hrv": DEVICE_RMSSD,
    }
    values.update(session_extra)
    return _synthetic(**values)


def _beats(count: int = 12) -> list[RRInterval]:
    """A short unflagged beat stream: non-empty, valid fraction 1.0, real rMSSD."""
    return [
        RRInterval(
            seq=i,
            rr_ms=1000.0 + (20.0 if i % 2 else 0.0),
            rr_source="chest_strap_ecg",
            is_artefact=False,
        )
        for i in range(count)
    ]


def _classified(messages, rr_intervals=None):
    session, _records = mapping.to_canonical(messages)
    hrv_classification.classify(messages, session, rr_intervals or [])
    return session


def _provenance(session) -> dict:
    return session.context.provenance if session.context else {}


def _classify_fixture(filename: str):
    """Decode a real fixture and drive the full mapping + reconstruction + classify path."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())
    session, _records = mapping.to_canonical(messages)
    rr_intervals = rr_reconstruction.reconstruct(messages)
    hrv_classification.classify(messages, session, rr_intervals)
    return session, rr_intervals


# ---------------------------------------------------------------------------
# The @should scenario: both signals present -> Tier 1 wins
# ---------------------------------------------------------------------------


def test_both_signals_resolve_to_tier_1() -> None:
    """F004 @should, clause 1: "it resolves to Tier 1, because §2.4.5 orders the
    hierarchy highest-fidelity-first"."""
    session = _classified(_resting_capture(), _beats())

    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.activity_tag == "resting_hrv_check"
    assert session.rr_source == "chest_strap_ecg"


def test_both_signals_compute_the_rmssd_from_the_beats() -> None:
    """F004 @should, clause 2: "the rMSSD is computed from the beats".

    Not merely tagged Tier 1 -- the value actually recorded is the one
    ``rmssd.resting_rmssd`` derives from the beat stream, not the device's 42.
    """
    rr_intervals = _beats()
    session = _classified(_resting_capture(), rr_intervals)

    expected = resting_rmssd(rr_intervals)
    assert expected is not None
    computed = _provenance(session)["computed_resting_rmssd_ms"]
    assert computed == pytest.approx(expected)
    assert computed != pytest.approx(float(DEVICE_RMSSD))


def test_the_device_value_stays_out_of_rmssd_precomputed() -> None:
    """F004 @should, clause 3, negative half; task "Don't do" #1.

    §2.2.3 reserves ``rmssd_precomputed`` for a *device-supplied* scalar on the
    numeric wrist tiers. On a Tier-1 capture it must stay ``None`` -- both
    because the device's number lost, and because E003 reads the field's
    populated-ness to tell which tier produced the reading it is looking at.
    """
    session = _classified(_resting_capture(), _beats())

    assert session.rmssd_precomputed is None


def test_the_unused_device_value_is_recorded_in_provenance() -> None:
    """F004 @should, clause 3, positive half; task "Don't do" #2: the device
    value is not dropped silently, it is recorded as what was ignored."""
    session = _classified(_resting_capture(), _beats())

    assert _provenance(session)["unused_device_rmssd_hrv"] == DEVICE_RMSSD


def test_the_device_value_is_recorded_exactly_as_the_device_gave_it() -> None:
    """An audit record of a rejected scalar is worthless if it is coerced: the
    stored value must be the observed one, ``int`` and all, so a later reader can
    compare it against what the device reported."""
    session = _classified(_resting_capture(rmssd_hrv=51), _beats())

    recorded = _provenance(session)["unused_device_rmssd_hrv"]
    assert recorded == 51
    assert isinstance(recorded, int)


def test_tier_2_never_claims_a_capture_tier_1_routed() -> None:
    """The losing branch leaves no trace. ``activity_tag`` must not be
    ``health_snapshot`` and ``rr_source`` must not be ``health_snapshot_ppg``,
    even though the capture carries the full Tier-2 signal pair (``rmssd_hrv``
    present AND raw sport 60) and would route Tier 2 on its own."""
    session = _classified(_resting_capture(), _beats())

    assert session.activity_tag != "health_snapshot"
    assert session.hrv_source_tier != "health_snapshot"
    assert session.rr_source != "health_snapshot_ppg"
    assert "hrv_signal_disagreement" not in _provenance(session)


def test_the_losing_branch_really_would_have_won_on_its_own() -> None:
    """Guards the guard. If this same message set did *not* route Tier 2 without
    beats, every assertion above would pass vacuously and the precedence contract
    would be untested. Identical session, empty beat stream -> Tier 2."""
    session = _classified(_resting_capture(), [])

    assert session.hrv_source_tier == "health_snapshot"
    assert session.rmssd_precomputed == DEVICE_RMSSD


# ---------------------------------------------------------------------------
# The two provenance keys are distinct and coexist
# ---------------------------------------------------------------------------


def test_both_provenance_keys_coexist_without_colliding() -> None:
    """The computed reading and the rejected device scalar land in the same
    ``dict[str, Any]`` and mean opposite things. They must both survive."""
    session = _classified(_resting_capture(), _beats())
    provenance = _provenance(session)

    assert "computed_resting_rmssd_ms" in provenance
    assert "unused_device_rmssd_hrv" in provenance
    assert provenance["computed_resting_rmssd_ms"] != provenance["unused_device_rmssd_hrv"]


def test_the_two_provenance_key_constants_are_different_strings() -> None:
    """Pinned at the source, not just at one call site: a future rename that
    collapsed the two keys onto one name would silently overwrite the reading
    with the value that lost to it."""
    assert (
        hrv_classification._PROVENANCE_COMPUTED_RMSSD
        != hrv_classification._PROVENANCE_UNUSED_DEVICE_RMSSD
    )
    assert hrv_classification._PROVENANCE_COMPUTED_RMSSD == "computed_resting_rmssd_ms"
    assert hrv_classification._PROVENANCE_UNUSED_DEVICE_RMSSD == "unused_device_rmssd_hrv"


def test_the_unused_key_is_distinct_from_the_signal_disagreement_key() -> None:
    """T040 writes ``hrv_signal_disagreement`` into the same dict for a different
    finding -- a device value on a file that is not a snapshot at all. Sharing a
    key would make the two indistinguishable to E003."""
    assert (
        hrv_classification._PROVENANCE_UNUSED_DEVICE_RMSSD
        != hrv_classification._PROVENANCE_SIGNAL_DISAGREEMENT
    )


# ---------------------------------------------------------------------------
# The key is absent, not null, when there was nothing to record
# ---------------------------------------------------------------------------


def test_a_tier_1_capture_with_no_device_value_records_no_unused_key() -> None:
    """Absent, not ``None``. A key holding ``None`` asserts "a device value was
    observed and ignored", which is false of a strap capture that never carried
    one -- and it is the same ``None``-stripping distinction
    ``mapping._build_summary`` maintains for ``distance_m``."""
    session = _classified(_resting_capture(rmssd_hrv=None), _beats())

    assert session.hrv_source_tier == "chest_strap_raw"
    assert "unused_device_rmssd_hrv" not in _provenance(session)


def test_a_non_resting_run_carrying_beats_and_a_device_value_records_no_unused_key() -> None:
    """Precedence applies only when Tier 1 actually fires. A 90-minute run with
    beats and a device scalar is not a Tier-1 capture, so nothing was "unused" by
    a computation that never happened -- Tier 2 takes it, and its value goes to
    ``rmssd_precomputed`` where it belongs."""
    session = _classified(
        _resting_capture(total_timer_time=5400.0, total_distance=18000.0), _beats()
    )

    assert session.hrv_source_tier == "health_snapshot"
    assert session.rmssd_precomputed == DEVICE_RMSSD
    assert "unused_device_rmssd_hrv" not in _provenance(session)


# ---------------------------------------------------------------------------
# A failed Tier-1 gate still does not fall through, and still does not
# silently drop the device value
# ---------------------------------------------------------------------------


def test_a_gate_failed_tier_1_capture_does_not_fall_through_to_tier_2() -> None:
    """T043's contract, re-pinned from the precedence side: a failed Tier-1
    capture yields *no reading*, never a downgraded one. §2.4.5's hierarchy is
    highest-fidelity-first, so the device scalar is not a consolation prize."""
    session = _classified(_resting_capture(total_timer_time=90.0, total_distance=5.0), _beats())

    assert "hrv_capture_too_short" in session.quality_flags
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert "computed_resting_rmssd_ms" not in _provenance(session)


def test_a_gate_failed_tier_1_capture_still_records_the_unused_device_value() -> None:
    """The device value is unused on this path too -- the capture was claimed by
    Tier 1 and answered with a flag. Dropping it here would be the silent loss
    the @should scenario forbids, and it is the only remaining record that the
    file carried a scalar at all."""
    session = _classified(_resting_capture(total_timer_time=90.0, total_distance=5.0), _beats())

    assert _provenance(session)["unused_device_rmssd_hrv"] == DEVICE_RMSSD


def test_a_non_positive_device_value_does_not_disturb_the_tier_1_route() -> None:
    """The non-positive check is Tier 2's validity gate on a value it is about to
    *store*. Tier 1 stores nothing from the device, so a zero is simply recorded
    as observed and the computed reading is unaffected."""
    session = _classified(_resting_capture(rmssd_hrv=0), _beats())

    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rmssd_precomputed is None
    assert _provenance(session)["unused_device_rmssd_hrv"] == 0
    assert "hrv_reading_unavailable" not in session.quality_flags


# ---------------------------------------------------------------------------
# Regression guards on the two single-signal cases that DO have real fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("filename", "expected"), sorted(SNAPSHOT_FIXTURES.items()))
def test_a_snapshot_with_no_beats_still_resolves_tier_2(filename: str, expected: int) -> None:
    """Task verification step 2: T039 must not regress. Both snapshots carry the
    device scalar and **zero** ``hrv`` messages, so Tier 1 never sees them and the
    precedence work must leave them exactly where they were."""
    session, rr_intervals = _classify_fixture(filename)

    assert rr_intervals == []
    assert session.hrv_source_tier == "health_snapshot"
    assert session.rmssd_precomputed == expected
    assert "unused_device_rmssd_hrv" not in _provenance(session)


def test_the_gate_fixture_still_resolves_tier_1_with_no_unused_entry() -> None:
    """Task verification step 3: ``strap_hrv_sample_run.fit`` has beats and no
    device value, so it stays Tier 1 and gains no ``unused_device_rmssd_hrv``
    entry -- there was never a device value to leave unused."""
    session, rr_intervals = _classify_fixture(GATE_FIXTURE)

    assert len(rr_intervals) == 156
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rmssd_precomputed is None
    assert "computed_resting_rmssd_ms" in _provenance(session)
    assert "unused_device_rmssd_hrv" not in _provenance(session)
