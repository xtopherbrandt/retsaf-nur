"""T041: Tier 1 -- the chest-strap resting-capture discriminator (F004 ref doc §2).

The predicate ratified 2026-09-05, amended 2026-09-06 after Stage-0 review found
the original OR shape routed a present-and-zero distance as stillness, and amended
again the same day after goal verification found that a present-and-zero distance
was *still* satisfying the conjunction's presence test on its own:

```
tier1 := rr_intervals is non-empty
     AND duration_s is not None AND 0 < duration_s <= 300
     AND at_rest, where:

         distance_signal   = distance_m is present AND distance_m > 0
         heart_rate_signal = avg_heart_rate is present

         if not (distance_signal or heart_rate_signal):
             at_rest = False        # no usable intensity signal at all
         else:
             at_rest = True
             if distance_signal:
                 at_rest = distance_m / duration_s <= 1.0
             if at_rest and heart_rate_signal:
                 at_rest = avg_heart_rate <= 100
```

Every **present** intensity signal must agree, and a distance is only a *signal*
when it is a positive measurement. **A zero distance is not evidence of stillness
-- it is a device reporting no distance**, so it cannot be the sole reason a
capture routes: the heart-rate arm must be there and must agree. The whole rule is
pinned row by row by ``test_the_resting_profile_contract``.

**Raw-RR presence alone must never route**, and that is the single bright line
the reference document draws in boldface. ``dev_fields_run.fit`` is an ordinary
52-minute run carrying 3127 ``hrv`` messages / 7220 beats at ``rr_valid_fraction``
0.998; a "beats present -> Tier 1" rule converts it into a resting-HRV reading and
poisons E003's readiness trend -- the §2.2.3 prohibition this guard enforces.

**The gate fixture, measured with fitdecode (2026-09-04, re-verified 2026-09-05):**
``strap_hrv_sample_run.fit`` -- 149 ``hrv`` messages / 156 beats, ``total_timer_time``
150.797 s, ``total_distance`` 108.21 m (mean 0.718 m/s), ``sport`` running (raw 1),
``sub_sport`` generic, ``avg_heart_rate`` 60, **no** ``rmssd_hrv``. It was recorded
strap-paired, lying still, *on the running activity profile* -- which is why the file
name reads oddly and why ``sport``/``sub_sport`` carry no signal at all here and are
deliberately not gated on. The 108 m of "distance" is GPS drift.

**The heart-rate arm is not decoration.** Without it, a file with no ``distance_m``
reduces the predicate to "beats present AND duration <= 300 s", and a chest-strap-paired
4-minute indoor-trainer FTP test, rowing-erg piece or treadmill interval rep all satisfy
that -- feeding a *maximal effort* into the readiness ladder as rest. The allowance for
missing distance exists so an indoor waking capture still routes; the heart-rate arm is
what stops that allowance from swallowing every GPS-less hard effort. No fixture in the
corpus is a GPS-less capture, so that class is covered synthetically here -- it is
threshold logic over already-mapped summary values, not fitdecode parsing behaviour, so
``.claude/rules/project-testing.md``'s real-fixture requirement does not bite.

**No usable intensity signal means no route.** Distance absent, zero or negative *and*
average heart rate absent leaves nothing to discriminate on, and the conservative outcome
is no reading. A negative distance is impossible in an unsigned FIT field, so it can only
be corruption and is answered exactly as an absent one is.

**``rmssd_precomputed`` stays ``None`` on Tier 1.** Per §2.2.3 it is "populated only for
the numeric wrist tiers"; the Tier-1 reading *is* the computed value, and mixing the two
would leave E003 unable to tell which tier produced the number it is reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser, hrv_classification, mapping, rr_reconstruction
from runcoach_api.ingestion.rmssd import resting_rmssd
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The GO/NO-GO gate fixture. Do not rename it: the name reads oddly because the
# capture was recorded on the running profile while lying still, which is exactly
# the discriminator problem this module solves. A rename would touch every reference.
GATE_FIXTURE = "strap_hrv_sample_run.fit"

# Files that must never reach a Tier-1 reading:
#   dev_fields_run.fit  -- 7220 beats, 3117.009 s, 3.215 m/s: the R3 trap itself.
#   sample_run.fit      -- an ordinary run.
#   wrist_ppg_run.fit   -- no RR carrier at all, so zero beats never reach the predicate.
NON_RESTING_FIXTURES = ("dev_fields_run.fit", "sample_run.fit", "wrist_ppg_run.fit")

# T039's Tier-2 pair, asserted here as a regression guard on branch ordering: both
# carry zero ``hrv`` messages, so inserting Tier 1 ahead of Tier 2 must not move them.
SNAPSHOT_FIXTURES = {"sample_health_snapshot.fit": 37, "strap_health_snapshot.fit": 51}

# ``fake_msg``, ``synthetic``, ``classified`` and ``ingest`` come from
# ``conftest.py``, shared with the four other ``test_resting_hrv_*`` modules.
# ``synthetic`` defaults ``sport`` to ``"running"``, which is what this module's
# own copy did -- ``sport`` carries no signal for the Tier-1 discriminator and is
# deliberately not gated on, so no call site here passes it.


def _beats(count: int = 8) -> list[RRInterval]:
    """A short unflagged beat stream: enough to be non-empty and to yield an rMSSD."""
    return [
        RRInterval(seq=i, rr_ms=1000.0 + (20.0 if i % 2 else 0.0), rr_source="chest_strap_ecg")
        for i in range(count)
    ]


def _classify_fixture(filename: str):
    """Decode a real fixture and drive the full mapping + reconstruction + classify path."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())
    session, _records = mapping.to_canonical(messages)
    rr_intervals = rr_reconstruction.reconstruct(messages)
    hrv_classification.classify(messages, session, rr_intervals)
    return session, rr_intervals


# ---------------------------------------------------------------------------
# The gate fixture -- a real chest-strap resting capture routes to Tier 1
# ---------------------------------------------------------------------------


def test_the_gate_fixture_round_trips_as_a_tier_1_reading(ingest) -> None:
    """F004 @must: "A chest-strap resting capture has its rMSSD computed from the
    raw beats". Asserted end to end through the API, because the tag is what E003
    reads back out of the store."""
    with TestClient(app) as client:
        body = ingest(client, GATE_FIXTURE)

    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"


def test_the_gate_fixture_leaves_the_device_field_null(ingest) -> None:
    """§2.2.3: ``rmssd_precomputed`` is "populated only for the numeric wrist tiers".
    The Tier-1 reading is the *computed* value; putting it in the device-supplied
    field is the §2.4.5 violation -- E003 could no longer tell the tiers apart."""
    with TestClient(app) as client:
        body = ingest(client, GATE_FIXTURE)

    assert body["rmssd_precomputed"] is None


def test_the_gate_fixture_still_carries_its_beat_rows(ingest) -> None:
    """Unlike Tier 2, a Tier-1 reading has real beats and they are persisted: the
    computation is the system's own, over the artefact-filtered stream."""
    with TestClient(app) as client:
        body = ingest(client, GATE_FIXTURE)

    assert len(body["rr_intervals"]) == 156


def test_the_gate_fixtures_rmssd_is_computed_from_its_beats() -> None:
    """The reading is not merely tagged -- ``rmssd.resting_rmssd`` (T042, strict
    pairwise adjacency) is actually wired, and its value is recorded rather than
    discarded. ``rmssd_precomputed`` cannot carry it, so provenance does."""
    session, rr_intervals = _classify_fixture(GATE_FIXTURE)

    expected = resting_rmssd(rr_intervals)
    assert expected is not None
    assert session.context.provenance["computed_resting_rmssd_ms"] == pytest.approx(expected)


def test_the_gate_fixture_is_not_discriminated_by_sport() -> None:
    """The capture was recorded on the *running* profile while lying still, so
    ``sport`` carries no signal here. Pinned so a future reader does not "simplify"
    the predicate into a sport check that would silently stop working."""
    session, _rr = _classify_fixture(GATE_FIXTURE)

    assert session.sport == "running"
    assert session.hrv_source_tier == "chest_strap_raw"


# ---------------------------------------------------------------------------
# R3 -- the trap. Raw-RR presence alone must never route.
# ---------------------------------------------------------------------------


def test_an_ordinary_run_with_7220_beats_is_never_routed(ingest) -> None:
    """F004 @must: "An ordinary run is never treated as a resting-HRV reading".

    ``dev_fields_run.fit`` carries more raw beats than any other fixture in the
    corpus. If beat presence routed, this run would become a resting reading."""
    with TestClient(app) as client:
        body = ingest(client, "dev_fields_run.fit")

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["rmssd_precomputed"] is None
    assert body["rr_source"] is None


@pytest.mark.parametrize("filename", NON_RESTING_FIXTURES)
def test_no_ordinary_fixture_reaches_a_tier_1_reading(filename: str) -> None:
    """``sample_run.fit`` and ``wrist_ppg_run.fit`` carry zero beats, so they never
    reach the predicate at all; ``dev_fields_run.fit`` reaches it and is rejected on
    duration and mean speed alike."""
    session, _rr = _classify_fixture(filename)

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


def test_a_resting_profile_with_no_beats_does_not_route(synthetic, classified) -> None:
    """Necessary condition: the file must carry ``hrv`` (#78) beat-to-beat arrays.
    A two-minute non-session with no beats is Tier 2's business, never Tier 1's."""
    session = classified(
        synthetic(total_timer_time=150.0, total_distance=10.0, avg_heart_rate=55),
        rr_intervals=[],
    )

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


# ---------------------------------------------------------------------------
# The duration arm -- 300 s, the upper bound of §2.4.5's "2-5 minute" protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duration", [150.797, 300.0])
def test_a_capture_within_the_protocol_window_routes(
    duration: float,
    synthetic,
    classified,
) -> None:
    """300 s is a citation, not an invented number: the upper bound of §2.4.5's
    stated "2-5 minute resting measurement" protocol. The boundary itself routes."""
    session = classified(
        synthetic(total_timer_time=duration, total_distance=100.0, avg_heart_rate=58),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_capture_longer_than_the_protocol_window_does_not_route(
    synthetic,
    classified,
) -> None:
    """Not widened to 600 s "for margin": any value between 150.8 and 3117 separates
    the two fixtures, and only the citable one may be chosen."""
    session = classified(
        synthetic(total_timer_time=300.001, total_distance=10.0, avg_heart_rate=55),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


def test_a_capture_with_no_duration_does_not_route_and_does_not_raise(
    synthetic,
    classified,
) -> None:
    """``_build_summary`` strips its ``None`` values, so a file with no
    ``total_timer_time`` has **no** ``duration_s`` key. Subscripting would be a 500
    on a perfectly valid upload."""
    session = classified(
        synthetic(total_distance=10.0, avg_heart_rate=55), rr_intervals=_beats()
    )

    assert session.summary is not None and "duration_s" not in session.summary
    assert session.hrv_source_tier is None


def test_a_zero_duration_capture_does_not_divide_by_zero(synthetic, classified) -> None:
    """A degenerate file can carry ``total_timer_time`` 0. The speed must be computed
    only after the duration check has established a usable divisor."""
    session = classified(
        synthetic(total_timer_time=0.0, total_distance=10.0, avg_heart_rate=55),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# The distance arm -- 1.0 m/s, below walking pace
# ---------------------------------------------------------------------------


def test_gps_drift_below_walking_pace_routes(synthetic, classified) -> None:
    """The gate fixture's own profile: 108.21 m over 150.797 s = 0.718 m/s, which is
    drift, not travel."""
    session = classified(
        synthetic(total_timer_time=150.797, total_distance=108.21, avg_heart_rate=60),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"


@pytest.mark.parametrize(
    ("distance", "duration"),
    [(200.001, 200.0), (300.0, 200.0), (10021.48, 3117.009)],
)
def test_movement_above_walking_pace_does_not_route(
    distance: float,
    duration: float,
    synthetic,
    classified,
) -> None:
    """1.0 m/s is a **spec-introduced implementation default** (no direct citation),
    flagged as such in the module. The first row is the first step past the inclusive
    bound; the last is ``dev_fields_run.fit``'s own 3.215 m/s profile."""
    session = classified(
        synthetic(total_timer_time=duration, total_distance=distance, avg_heart_rate=58),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


def test_a_present_distance_above_walking_pace_vetoes_regardless_of_heart_rate(
    synthetic, classified
) -> None:
    """A capture that genuinely moved is not rescued by a low average heart rate.

    Under the conjunction rule every present signal must agree, so the distance
    veto stands on its own — this is the direction that was always correct, and
    it is unchanged by the 2026-09-06 amendment."""
    session = classified(
        synthetic(total_timer_time=200.0, total_distance=600.0, avg_heart_rate=52),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


def test_a_boundary_speed_of_exactly_one_metre_per_second_routes(synthetic, classified) -> None:
    """The bound is inclusive: ``<= 1.0``."""
    session = classified(
        synthetic(total_timer_time=200.0, total_distance=200.0), rr_intervals=_beats()
    )

    assert session.hrv_source_tier == "chest_strap_raw"


# ---------------------------------------------------------------------------
# The heart-rate arm -- the GPS-less fallback, and the false-positive class
# ---------------------------------------------------------------------------


def test_a_gps_less_hard_effort_does_not_route(synthetic, classified) -> None:
    """**The case the heart-rate arm exists for**, and the one a fixture-only suite
    would miss entirely: a 240 s chest-strap-paired indoor effort with no
    ``distance_m`` and an average heart rate of 165. Without this arm the predicate
    collapses to "beats present AND duration <= 300 s", and an indoor-trainer FTP
    test, rowing-erg piece or treadmill interval rep would feed a *maximal effort*
    into the readiness ladder as rest."""
    session = classified(
        synthetic(total_timer_time=240.0, avg_heart_rate=165), rr_intervals=_beats()
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None


def test_a_gps_less_resting_capture_routes(synthetic, classified) -> None:
    """The allowance for missing distance is what lets an indoor waking capture
    route at all: 180 s, no ``distance_m``, average heart rate 58."""
    session = classified(
        synthetic(total_timer_time=180.0, avg_heart_rate=58), rr_intervals=_beats()
    )

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rr_source == "chest_strap_ecg"
    assert session.rmssd_precomputed is None


@pytest.mark.parametrize("avg_hr", [58, 100])
def test_the_heart_rate_bound_is_inclusive(avg_hr: int, synthetic, classified) -> None:
    """100 bpm is a spec-introduced default chosen for **separation, not precision**:
    the gate fixture is 60 and any short hard effort is 150+, leaving generous
    headroom for a stressed or unwell resting morning."""
    session = classified(
        synthetic(total_timer_time=180.0, avg_heart_rate=avg_hr), rr_intervals=_beats()
    )

    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_heart_rate_above_the_bound_does_not_route(synthetic, classified) -> None:
    session = classified(
        synthetic(total_timer_time=180.0, avg_heart_rate=101), rr_intervals=_beats()
    )

    assert session.hrv_source_tier is None


def test_no_intensity_signal_at_all_does_not_route(synthetic, classified) -> None:
    """Distance absent *and* average heart rate absent leaves nothing to discriminate
    on. The conservative outcome is no reading -- never a route on beats and duration
    alone, which is the rule the whole reference document is written around."""
    session = classified(synthetic(total_timer_time=180.0), rr_intervals=_beats())

    assert session.summary is not None
    assert "distance_m" not in session.summary
    assert "avg_heart_rate" not in session.summary
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None


# ---------------------------------------------------------------------------
# Branch ordering -- Tier 1 is inserted ahead of Tier 2 (T039 must not regress)
# ---------------------------------------------------------------------------


def test_tier_1_is_reached_before_the_tier_2_branch(synthetic, classified) -> None:
    """§2.4.5 orders the hierarchy highest-fidelity-first, so a capture carrying both
    raw beats and a device ``rmssd_hrv`` resolves to Tier 1 and leaves
    ``rmssd_precomputed`` null. T045 proves the full precedence contract, including
    where the unused device value is recorded; this only pins the branch order."""
    session = classified(
        synthetic(sport=60, total_timer_time=180.0, avg_heart_rate=58, rmssd_hrv=42),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rmssd_precomputed is None


@pytest.mark.parametrize(("filename", "expected_rmssd"), sorted(SNAPSHOT_FIXTURES.items()))
def test_the_health_snapshots_still_take_the_tier_2_path(
    filename: str, expected_rmssd: int
) -> None:
    """Regression guard on T039. Both snapshots carry **zero** ``hrv`` messages
    despite one of them being recorded with an HRM-Pro-Plus paired over ANT+, so the
    Tier-1 branch cannot capture them however it is ordered."""
    session, rr_intervals = _classify_fixture(filename)

    assert rr_intervals == []
    assert session.hrv_source_tier == "health_snapshot"
    assert session.rmssd_precomputed == expected_rmssd
    assert session.rr_source == "health_snapshot_ppg"


# ---------------------------------------------------------------------------
# Present-and-zero distance -- the R3 leak the two arms' AND closes
# ---------------------------------------------------------------------------


def test_a_present_zero_distance_still_consults_the_heart_rate_arm(
    synthetic,
    classified,
) -> None:
    """**The R3 leak.** ``mapping._build_summary`` strips only ``None``, so an indoor
    session carrying ``total_distance = 0.0`` has a ``distance_m`` key holding ``0.0``
    -- present-and-zero, not absent. A treadmill, rowing-erg or indoor-trainer interval
    logs exactly that, and ``0.0 / 240 = 0.0 <= 1.0`` satisfies the distance arm
    outright. If the distance arm short-circuits, a **maximal effort** at avg HR 165 is
    written as ``resting_hrv_check`` / ``chest_strap_raw`` carrying an rMSSD derived
    from it -- the §2.2.3 prohibition the heart-rate arm exists to enforce, leaking
    through the one branch a fallback-shaped predicate never takes.

    The two signals must therefore **agree** whenever both are present."""
    session = classified(
        synthetic(total_timer_time=240.0, total_distance=0.0, avg_heart_rate=165),
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert session.summary["distance_m"] == 0.0  # present, not stripped
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert "computed_resting_rmssd_ms" not in (
        session.context.provenance if session.context else {}
    )


def test_a_present_zero_distance_resting_capture_still_routes(synthetic, classified) -> None:
    """The twin, and the reason the fix is an AND rather than a heart-rate-only rule: a
    genuine indoor morning capture also logs ``total_distance = 0.0``. It agrees with
    both arms -- 0.0 m/s and 55 bpm -- so it must still produce a Tier-1 reading."""
    session = classified(
        synthetic(total_timer_time=240.0, total_distance=0.0, avg_heart_rate=55),
        rr_intervals=_beats(),
    )

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rr_source == "chest_strap_ecg"
    assert session.rmssd_precomputed is None


def test_a_moving_capture_with_a_resting_heart_rate_still_does_not_route(
    synthetic,
    classified,
) -> None:
    """The other half of the AND, carried over unchanged from the old fallback reading:
    a low average heart rate does not rescue a capture that demonstrably moved."""
    session = classified(
        synthetic(total_timer_time=200.0, total_distance=600.0, avg_heart_rate=52),
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# The ratified profile contract, as one table (F004 ref doc §2, 2026-09-06)
# ---------------------------------------------------------------------------

# Every row of the discriminator's contract, in one place. The single-case tests
# above still carry the argument for *why* each arm exists; this table is what
# the predicate as a whole is pinned to, so a future amendment has one place to
# be argued with rather than a dozen scattered assertions to reconcile.
#
# "absent" is expressed the way a real file expresses it -- by omitting the
# ``session`` field, which ``mapping._build_summary`` then strips, leaving no
# key at all. Present-and-zero is expressed by passing ``0.0``, which survives
# that stripping. That distinction is the entire subject of the 2026-09-06
# amendment and must never be flattened into ``None`` here.
_PROFILE_CONTRACT = [
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 175},
        False,
        id="zero-distance-hard-effort--the-Stage-0-case-stays-fixed",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0},
        False,
        id="zero-distance-no-heart-rate--the-2026-09-06-fix",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 55},
        True,
        id="zero-distance-resting-heart-rate--genuine-indoor-capture",
    ),
    pytest.param(
        {"total_timer_time": 150.797, "total_distance": 108.21, "avg_heart_rate": 60},
        True,
        id="the-gate-fixture-profile",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "avg_heart_rate": 175},
        False,
        id="no-distance-hard-effort",
    ),
    pytest.param(
        {"total_timer_time": 240.0},
        False,
        id="no-distance-no-heart-rate--no-usable-signal",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 800.0, "avg_heart_rate": 175},
        False,
        id="moving-and-working",
    ),
    pytest.param(
        {"total_timer_time": 0.0, "total_distance": 108.0, "avg_heart_rate": 60},
        False,
        id="degenerate-zero-duration",
    ),
    pytest.param(
        {"total_timer_time": 300.001, "total_distance": 0.0, "avg_heart_rate": 50},
        False,
        id="one-millisecond-over-the-ceiling",
    ),
    pytest.param(
        {"total_timer_time": 300.0, "total_distance": 300.0, "avg_heart_rate": 100},
        True,
        id="both-bounds-inclusive-by-design",
    ),
]


@pytest.mark.parametrize(("session_extra", "routes"), _PROFILE_CONTRACT)
def test_the_resting_profile_contract(
    session_extra: dict,
    routes: bool,
    synthetic,
    classified,
) -> None:
    """The whole discriminator, row by row, driven through the real mapping path.

    Row 2 -- zero distance, no heart rate -- is the 2026-09-06 amendment. Before
    it, ``0.0 / 240 = 0.0 <= 1.0`` satisfied "at least one intensity signal is
    present" while carrying no intensity information at all; the heart-rate arm
    was skipped as absent; and the conjunction collapsed to a single arm that
    **any** zero-distance capture satisfied unconditionally. It was reproduced
    end-to-end against the real API during the sprint-003 review by patching the
    gate fixture's ``session`` message: a 240 s capture with 156 beats and no
    ``avg_heart_rate`` came back a full Tier-1 reading, unflagged.

    Rows 1 and 3 are the pair that constrains the fix from both sides: the same
    zero distance must not rescue a maximal effort, and must not condemn a
    genuine indoor waking capture. Only the heart-rate arm can tell those two
    apart -- which is exactly why a zero distance may not stand in for it."""
    session = classified(synthetic(**session_extra), rr_intervals=_beats())
    provenance = session.context.provenance if session.context else {}

    if routes:
        assert session.activity_tag == "resting_hrv_check"
        assert session.hrv_source_tier == "chest_strap_raw"
        assert session.rr_source == "chest_strap_ecg"
        assert session.rmssd_precomputed is None
        assert "computed_resting_rmssd_ms" in provenance
    else:
        assert session.activity_tag is None
        assert session.hrv_source_tier is None
        assert session.rr_source is None
        assert "computed_resting_rmssd_ms" not in provenance


# ---------------------------------------------------------------------------
# Negative distance -- impossible, therefore not a measurement
# ---------------------------------------------------------------------------


def test_a_negative_distance_is_not_an_intensity_signal(synthetic, classified) -> None:
    """``total_distance`` is an unsigned FIT field, so a negative value can only
    arrive from a crafted or corrupt definition record -- the same provenance as
    the ``tuple`` and ``str`` values ``_numeric`` already answers ``None`` for.

    It is therefore treated the way every other uninterpretable value is treated:
    as **absent**, not as evidence. Reading it literally would be strictly worse,
    and in the same direction the zero case was wrong -- ``-500 / 240`` is
    comfortably ``<= 1.0``, so a garbage field would satisfy the stillness test
    outright and stand in for the heart-rate arm it must never replace.

    Absent means the heart-rate arm decides, and here it agrees: 55 bpm routes."""
    session = classified(
        synthetic(total_timer_time=240.0, total_distance=-500.0, avg_heart_rate=55),
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert session.summary["distance_m"] == -500.0  # present, and still not a signal
    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_negative_distance_alone_does_not_route(synthetic, classified) -> None:
    """The other half of treating it as absent, and the half that matters: with no
    heart rate to fall through to there is no usable intensity signal at all, so a
    corrupt distance field can never route a capture on its own."""
    session = classified(
        synthetic(total_timer_time=240.0, total_distance=-500.0), rr_intervals=_beats()
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# Non-numeric summary values -- a crafted definition record must not be a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_extra",
    [
        {"total_timer_time": (150.0, 1.0), "total_distance": 10.0, "avg_heart_rate": 60},
        {"total_timer_time": "150", "total_distance": 10.0, "avg_heart_rate": 60},
        {"total_timer_time": 150.0, "total_distance": (10.0, 2.0)},
        {"total_timer_time": 150.0, "avg_heart_rate": ("60", "61")},
        {"total_timer_time": 150.0, "avg_heart_rate": "60"},
    ],
)
def test_a_non_numeric_summary_value_is_treated_as_absent_not_a_500(
    session_extra: dict,
    synthetic,
    classified,
) -> None:
    """``fitdecode`` types a field by the **file's own declared base type**, not the
    global profile: ``reader.py`` (verified at lines 797-806 of the vendored copy)
    returns ``tuple(base_type.parse(v) for v in raw_value)`` whenever the declared size
    holds more than one element, so a crafted or corrupt definition record can make any
    of these three a ``tuple`` -- or a ``str``, from a string base type. Comparing one
    raises ``TypeError``, which ``main.py`` does not catch: a **500 on a malformed
    upload** rather than a stored session. ``rr_reconstruction._hrv_candidates`` already
    guards the identical hazard with ``isinstance``; this is the same convention.

    A non-numeric value is treated as **absent**, so each row above loses an arm and no
    reading is derived."""
    session = classified(synthetic(**session_extra), rr_intervals=_beats())

    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_boolean_heart_rate_is_not_a_numeric_intensity_signal(synthetic, classified) -> None:
    """``bool`` is an ``int`` subclass, so ``True <= 100`` is ``True`` and a bare
    ``isinstance(value, (int, float))`` would accept it as an average heart rate of
    1 bpm. It is not a measurement, so it is treated as absent -- which leaves this
    GPS-less file with no intensity signal at all."""
    session = classified(
        synthetic(total_timer_time=180.0, avg_heart_rate=True), rr_intervals=_beats()
    )

    assert session.hrv_source_tier is None
