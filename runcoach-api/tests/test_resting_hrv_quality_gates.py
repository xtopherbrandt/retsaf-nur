"""T043: the Tier-1 quality gates -- rows 1-3 of F004's quality-gate outline.

```gherkin
Scenario Outline: A capture failing a quality gate yields no reading, but is still stored
  Given a resting capture whose <condition>
  When it is uploaded to POST /sessions
  Then the upload succeeds and the session is stored with its quality flags
  And hrv_source_tier and rmssd_precomputed are both null
  And the <flag> quality flag is raised rather than failing silently

  Examples:
    | condition                                          | flag                  |
    | total_timer_time is under 120 seconds              | hrv_capture_too_short |
    | rr_valid_fraction is below 0.80                    | hrv_capture_low_quality |
    | beat stream is empty, so rr_valid_fraction is null | hrv_capture_no_beats  |
    | computed rMSSD is zero, on a declared Tier-1 capture | hrv_reading_unavailable |
```

(The two *device*-scalar rows -- "rmssd_hrv is zero or negative" and "rmssd_hrv is
absent on a sport-60 file" -- are Tier 2's, owned by T039 and asserted in
``test_resting_hrv_tier2.py``. The computed-zero row above is Tier 1's and is
**T065's**, which added it to the outline when it extended Tier 2's non-positive
gate to the computed value; before T065 a computed zero took the success path.)

**The "Then" of every row gained ``resting_rmssd_ms is null`` at T065** -- see
``_assert_no_reading``. That is the half of the column's contract this module owns:
its twin, "every successful reading has a positive value", is asserted on the
success paths here and in ``test_resting_hrv_tier1.py`` / ``_tier2.py``.

**The three thresholds are cited, not invented** (F004 reference document §5):

* **120 s** -- the *lower* bound of §2.4.5's "2-5 minute resting measurement" protocol,
  the same sentence T041 took the 300 s upper bound from.
* **0.80** -- §2.4.3 / spec §3's "reject a capture retaining <80% of beats".
* **no third constant.** Row 3 is not a threshold at all: it is the ``None``/``0.0``
  distinction ``models.Session.rr_valid_fraction`` documents.

**The ``None`` vs ``0.0`` trap is the whole reason row 3 exists.** ``pipeline.py`` assigns
``session.rr_valid_fraction`` only when beats exist, leaving it ``None`` -- not ``0.0`` --
for a session with no RR stream at all. A naive ``if session.rr_valid_fraction < 0.80:``
therefore raises ``TypeError`` on ``None`` and surfaces to the client as a **500 on a
perfectly valid upload**. The two rows are also genuinely different findings: "no beats at
all" and "beats recorded, none survived filtering" are not the same event, and E003 reads
them differently, so they are never collapsed by defaulting ``None`` to ``0.0``.

**No sentinel, ever.** A failed gate leaves ``rmssd_precomputed`` ``None``. It is never
``0`` or ``-1``: E003 takes ``ln(rMSSD)``, and ``rmssd.resting_rmssd`` was built around the
same rule -- ``0.0`` is a genuine measurement of zero variability, absence is ``None``.

**A flag on a file that was never a candidate is noise.** The gates run *after* the
Tier-1 contract has decided the file is a resting capture, so a 90-second ordinary run
raises no ``hrv_capture_too_short``; that is pinned below and is the reason the gates live
inside the Tier-1 branch rather than beside ``quality_gates.apply``.

**T069: every capture in this module is declared, and that is a fixture change, not a
behavioural one.** Since the inference arm was removed, "the Tier-1 contract decided the
file is a resting capture" requires an explicit athlete declaration -- ``_classify_tier_1``
returns *before* ``_apply_quality_gates`` on an undeclared file, so an undeclared capture
raises no gate flag at all. The two fixtures ``resting`` and ``declared_classify`` supply
the two halves of that declaration; the gates, their thresholds and their citations are
untouched.

**T066 answered the question T069 left open here, in two halves.**

*Half one -- row 3's precondition.* ``_gate_a_beatless_resting_capture`` is now
declaration-gated. It used to fire on any beatless, resting-*shaped*, unclaimed file with
no reference to the declaration, so a file the system had explicitly not recognised
carried a flag asserting a finding about a recognised capture -- T058 **Finding 17 / row
D5**, pinned by T064 rather than fixed so this task would change it deliberately. The gate
is on the whole of ``_declared``: an upload-time override is a declaration too, and F004
states the disjunction once.

*Half two -- resolution **R4**, "beats present".* The Tier-1 beats gate now reads ``hrv``
*messages*, not the reconstructed stream, so a file carrying ``hrv`` messages that
reconstruct to **zero** beats claims Tier 1, is flagged, and does not fall through to
Tier 2. That is what makes §5's row 3 reachable *on the Tier-1 path* -- with a non-empty
``rr_intervals`` in hand ``_surviving_fraction`` always answers a float, so under the
reference document's wording the row could never fire there and would be a false promise.

Adversarial probe table (``.claude/rules/learnings/adversarial-input-probes...``)
================================================================================

Every row driven through ``mapping.to_canonical`` -> ``classify`` this session, or through
a real fixture end to end. The inputs the gated predicate actually reads: the presence of
``hrv`` messages, the reconstructed beat stream, the three ``session.summary`` intensity
signals (through ``_intensity_signal``), ``context.provenance["sport_profile_name"]``, the
configured name list and the override boolean.

**The invariant being proved: no file the system did not recognise carries an ``hrv_*``
flag.** It holds on every row below.

================================== ================= ======================= ============================ =====
input                              route             flag                    note                         tier
================================== ================= ======================= ============================ =====
0 ``hrv`` msgs, clean 150 s        undeclared        **none**                --                           --
0 ``hrv`` msgs, clean 150 s        config            hrv_capture_no_beats    -- (Finding 5, deferred)     --
0 ``hrv`` msgs, clean 150 s        override          hrv_capture_no_beats    override, ``no_beats``       --
0 ``hrv`` msgs, **each of the 8    all three         **none**                override note only, and only --
vetoes**                                                                     on the override route
``hrv`` msgs -> 0 beats, clean     undeclared        **none**                undeclared, ``vetoed_by``    --
                                                                             null
``hrv`` msgs -> 0 beats, clean     config            hrv_capture_no_beats    --                           --
``hrv`` msgs -> 0 beats, clean     override          hrv_capture_no_beats    override, **honoured**       --
``hrv`` msgs -> 0 beats, 6000 s    undeclared        **none**                undeclared,                  --
                                                                             duration_out_of_range
``hrv`` msgs -> 0 beats, 6000 s    config            **none**                declared-vetoed              --
``hrv`` msgs -> 0 beats, 6000 s    override          **none**                override, not honoured,      --
                                                                             ``duration_out_of_range``
``hrv`` msgs -> 0 beats,           undeclared        **none**                undeclared                   **T2**
sport 60 + rmssd_hrv 37            config/override   hrv_capture_no_beats    (override honoured)          --
0 ``hrv`` msgs, sport 60 +         all three         **none**                override note only           **T2**
rmssd_hrv 37
single beat, clean                 undeclared        **none**                undeclared                   --
single beat, clean                 config/override   hrv_reading_unavailable (override honoured)          --
eight beats, clean                 undeclared        **none**                undeclared                   --
eight beats, clean                 config/override   **none**                (override honoured)          **T1**
================================== ================= ======================= ============================ =====

Four results argued rather than merely passed:

1. **The override route keeps its flag, and the T064 pin therefore stays green.**
   ``test_an_override_on_a_beatless_file_reports_that_it_did_nothing`` asserts the
   ``{"honoured": false, "reason": "no_beats"}`` note and ``hrv_capture_no_beats``
   together, and both still hold. Gating on the config half alone would have removed the
   flag there, but ``declared`` is one disjunction in F004 and the split would rest on
   T058's deferred **Finding 5**: were a beatless *configured* declaration to gain a note
   later, the two routes become indistinguishable and the split indefensible. The note and
   the flag are not in tension -- the note says the Tier-1 *route* could not be taken, the
   flag says what the declared capture recorded.
2. **A ``hrv``-carrying file that reconstructs to zero beats and is override-declared
   records ``{"honoured": true}``**, where the same file with **zero** ``hrv`` messages
   records ``{"honoured": false, "reason": "no_beats"}``. That is R4 working exactly as
   ratified: the first file has beats present and *is* claimed by Tier 1, and ``honoured``
   is a statement about the declaration rather than about the reading.
3. **R4's revisit condition was checked against the real corpus and does not fire.** R4
   says its reasoning fails if a file carries ``hrv`` messages *and* a usable
   ``rmssd_hrv``. All ten fixtures were decoded this session: the five ``rmssd_hrv``
   carriers (``sample_health_snapshot`` 37, ``strap_health_snapshot`` 51,
   ``strap_health_snapshot_hrv`` 90, and neither run) hold **zero** ``hrv`` messages, and
   the five ``hrv`` carriers hold no ``rmssd_hrv``. No corpus file reconstructs to zero
   beats from a non-empty ``hrv`` set either, which is why the R4 rows above are
   synthetic: nothing real occupies that state, and saying so is the finding.
4. **An undeclared beatless resting-shaped file now has neither a flag nor a note.**
   Removing the flag is this task's point, and the absent note is contract row A8 (the
   undeclared note is gated on beats, or every Health Snapshot and wrist-PPG run would
   carry one). The two together mean such a file is silent -- which is R5's observability
   goal pushed against, and is recorded here rather than papered over. It is *not*
   reopened here: R5 explicitly scoped its notes to files with beats, and T058's
   **Finding 5** owns the declared half of the same question.

**Perturbation evidence, both halves separately** (the discipline
``contract-tables-need-an-independent-oracle`` asks for -- delete the rule, watch the row
go red). Run at T066 against the 431-test ``-k resting_hrv`` selection:

* Remove the **declaration gate** from ``_gate_a_beatless_resting_capture`` and exactly two
  rows go red -- this module's ``..._undeclared_beatless_resting_shaped_file_raises_no_gate_flag``
  and ``test_resting_hrv_tier2``'s ``..._undeclared_resting_shaped_row_2_file_is_not_flagged_beatless``.
  Every declared row stays green, so the gate is pinned by its *negative* class and not by
  a row that would pass either way.
* Revert the **R4 beats gate** to ``rr_intervals``-only and exactly one row goes red --
  ``test_a_beatless_hrv_carrier_claims_tier_1_and_does_not_fall_to_tier_2`` -- while its
  zero-``hrv``-message control stays green. The two changes are therefore independent and
  neither is carrying the other's evidence.

Synthetic ``_FakeMsg`` message sets are used for the threshold rows, following
``test_quality_gates_smart_recording.py``: these are threshold checks over already-mapped
summary values, not fitdecode parsing behaviour, so ``.claude/rules/project-testing.md``'s
real-fixture requirement does not bite. The real gate fixture is still asserted end to end
-- it must pass all three gates and keep its reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import hrv_classification, mapping, rmssd
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The athlete's own custom activity profile, and the declaration every capture in
# this module carries. Since T069 a Tier-1 route requires one: ``_classify_tier_1``
# returns before ``_apply_quality_gates`` on an undeclared file, so **without a
# declaration not one gate in this module can fire**. That is why the migration is
# a fixture change rather than a rewrite -- the gates themselves are untouched.
DECLARED_PROFILE = "HRV Snapshot"

# The declared Tier-1 positive: 150.476 s, 165 beats, 0.0 m, avg HR 64, recorded on
# the ``'HRV Snapshot'`` profile. It clears all three gates -- 150.476 >= 120, and
# its beats survive reconstruction -- so it is the positive control for every
# negative below. It replaced ``strap_hrv_sample_run.fit`` at T069, which is now
# the corpus's undeclared negative and can no longer route at all.
GATE_FIXTURE = "strap_hrv_capture.fit"

# The Tier-2 fixture whose *declared* route must still be Tier 2: zero ``hrv``
# messages, so R4's beats gate never claims it. Named here because T066's own
# verification list calls for it end to end and not only at the unit boundary.
SNAPSHOT_FIXTURE = "sample_health_snapshot.fit"
SNAPSHOT_PROFILE = "Health Snapshot"
SNAPSHOT_DEVICE_RMSSD = 37

# The empirically observed Garmin Health Snapshot ``sport`` value -- the Tier-2
# identity signal, spelled here as ``conftest``'s ``SNAPSHOT_SPORT`` is, so the
# R4 rows below can build a file that Tier 2 *would* read.
SNAPSHOT_SPORT = 60

FLAG_TOO_SHORT = "hrv_capture_too_short"
FLAG_LOW_QUALITY = "hrv_capture_low_quality"
FLAG_NO_BEATS = "hrv_capture_no_beats"
GATE_FLAGS = (FLAG_TOO_SHORT, FLAG_LOW_QUALITY, FLAG_NO_BEATS)

# Row 4 of the same scenario outline. Named there for the Tier-2 device scalar
# ("device rmssd_hrv is zero or negative" / "absent on a sport-60 file"), it is the
# feature's existing vocabulary for "this capture was recognised but yielded no
# usable number" -- which is exactly the Tier-1 case where the beat stream holds no
# contributing pair. ``hrv_capture_no_beats`` would be a false statement about a
# stream that does have beats, and inventing a fourth flag for the same finding is
# what §5's fixed vocabulary exists to prevent.
FLAG_READING_UNAVAILABLE = "hrv_reading_unavailable"

# ``synthetic``, ``classified`` and ``ingest`` come from ``conftest.py``,
# shared with the four other ``test_resting_hrv_*`` modules. The shared
# ``classified`` carries this module's three-parameter signature --
# ``classified(messages, rr_intervals=None, valid_fraction=None)`` -- which
# was the drifted one: the other four modules' copies lacked
# ``valid_fraction`` entirely, and omitting it leaves ``rr_valid_fraction``
# exactly as ``mapping.to_canonical`` set it, which is what they were
# already asserting against. ``synthetic`` defaults ``sport`` to
# ``"running"``, as this module's own copy did.
#
# This module drives it through ``declared_classify`` rather than directly, so the
# declaration is stated once and every call site names it. The declaration is
# **not** defaulted in ``conftest`` -- T069's task file forbids that, because a
# global default would hide a regression on the undeclared path in every suite at
# once. Here it is module-local and its name is at each call site.


@pytest.fixture
def resting(synthetic):
    """A message set that clears the Tier-1 contract: 150 s, no distance, avg HR 60,
    on the declared ``'HRV Snapshot'`` profile.

    Every gate test starts from a file the Tier-1 branch *would* route, so a failure
    below can only be the gate under test and never the contract.

    ``sport_profile_name`` is part of that "would route" since T069 -- an undeclared
    capture is refused before ``_apply_quality_gates`` is ever reached, so without
    it every assertion in this module would be asserting the absence of a flag that
    could not have been raised for an unrelated reason.
    """

    def _resting(**session_extra):
        values = {
            "total_timer_time": 150.0,
            "avg_heart_rate": 60,
            "sport_profile_name": DECLARED_PROFILE,
        }
        values.update(session_extra)
        return synthetic(**values)

    return _resting


@pytest.fixture
def declared_classify(classified):
    """``classified`` with this module's Tier-1 declaration attached.

    The other half of what ``resting`` supplies: the file names the profile, and
    this names the same profile as the configured ``resting_hrv_profile_names``
    list. Both are needed -- ``_declared`` is an exact match of one against the
    other -- and keeping them in two fixtures side by side is what lets the two
    "never a candidate" tests below opt out of the *file* half while still driving
    a configured classifier.
    """

    def _declared_classify(messages, **kwargs):
        return classified(messages, profile_names=[DECLARED_PROFILE], **kwargs)

    return _declared_classify


def _beats(count: int = 8, artefacts: int = 0) -> list[RRInterval]:
    """A short beat stream; the first ``artefacts`` beats are flagged.

    Flagged beats are *retained and flagged*, never excised -- that is
    ``rr_reconstruction``'s contract and what makes ``valid_fraction`` meaningful.
    """
    return [
        RRInterval(
            seq=i,
            rr_ms=1000.0 + (20.0 if i % 2 else 0.0),
            rr_source="chest_strap_ecg",
            is_artefact=i < artefacts,
        )
        for i in range(count)
    ]


def _assert_no_reading(session) -> None:
    """The shared "Then" of all three rows: stored, flagged, but no reading.

    ``rmssd_precomputed`` is asserted ``is None`` rather than falsy on purpose -- ``0``
    and ``-1`` are the sentinels this feature must never use, and both are falsy.

    ``resting_rmssd_ms`` joined it at T065 and is the reason this helper is now
    load-bearing rather than tidy: it is the column E003 actually reads, so "no
    reading" is only true if *that* field is null. Asserting it here rather than at
    each call site means every gate row -- present and future -- inherits the check.
    """
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.resting_rmssd_ms is None
    assert session.rr_source is None
    provenance = session.context.provenance if session.context else {}
    assert "computed_resting_rmssd_ms" not in provenance


# ---------------------------------------------------------------------------
# Row 1 -- minimum duration, 120 s (§2.4.5's "2-5 minute" lower bound)
# ---------------------------------------------------------------------------


def test_a_capture_under_two_minutes_raises_too_short(resting, declared_classify) -> None:
    """The first failing test of T043: a Tier-1 capture whose ``total_timer_time`` is
    under 120 s is stored with its flag and yields no reading."""
    session = declared_classify(
        resting(total_timer_time=90.0), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


def test_the_minimum_duration_bound_is_inclusive(resting, declared_classify) -> None:
    """120 s is the protocol's lower bound, so a capture *of* two minutes is inside it.
    Only "shorter than 120 s" fails -- the same inclusive treatment T041 gave 300 s."""
    session = declared_classify(
        resting(total_timer_time=120.0), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_one_millisecond_under_the_bound_fails(resting, declared_classify) -> None:
    """119.999 s is the first step outside the protocol. Pinned so the comparison can
    never be relaxed to ``<=`` without a test going red."""
    session = declared_classify(
        resting(total_timer_time=119.999), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# Row 2 -- artefact survival, rr_valid_fraction < 0.80 (§2.4.3 / spec §3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fraction", [0.0, 0.5, 0.799])
def test_a_capture_retaining_under_eighty_percent_raises_low_quality(
    fraction: float,
    resting,
    declared_classify,
) -> None:
    """§2.4.3's default rejects a sample retaining <80% of beats.

    ``0.0`` is included deliberately: "beats recorded, none survived filtering" is a
    *low quality* finding, not a *no beats* one. Collapsing it into row 3 would lose the
    distinction ``models.Session.rr_valid_fraction`` documents as load-bearing."""
    session = declared_classify(
        resting(), rr_intervals=_beats(), valid_fraction=fraction
    )

    assert FLAG_LOW_QUALITY in session.quality_flags
    assert FLAG_NO_BEATS not in session.quality_flags
    _assert_no_reading(session)


@pytest.mark.parametrize("fraction", [0.80, 0.998, 1.0])
def test_a_capture_retaining_at_least_eighty_percent_still_reads(
    fraction: float,
    resting,
    declared_classify,
) -> None:
    """The bound is inclusive: exactly 80% retained is not "under 80%". 0.998 is
    ``dev_fields_run.fit``'s own measured fraction, kept here as a realistic value."""
    session = declared_classify(resting(), rr_intervals=_beats(), valid_fraction=fraction)

    assert FLAG_LOW_QUALITY not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_the_surviving_fraction_is_read_from_the_beats_when_the_session_lacks_it(
    resting,
    declared_classify,
) -> None:
    """``classify()`` is public and reachable without ``pipeline.py`` -- T041's own suite
    calls it that way. When the session carries no precomputed fraction but beats are in
    hand, the fraction is derived from them rather than mistaken for "no beats"."""
    session = declared_classify(resting(), rr_intervals=_beats(count=10, artefacts=4))

    assert session.rr_valid_fraction is None
    assert FLAG_LOW_QUALITY in session.quality_flags
    assert FLAG_NO_BEATS not in session.quality_flags
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# Row 3 -- no surviving beats: rr_valid_fraction is None, not 0.0
# ---------------------------------------------------------------------------


def test_a_resting_capture_with_an_empty_beat_stream_raises_no_beats(
    resting,
    declared_classify,
) -> None:
    """``pipeline.py`` leaves ``rr_valid_fraction`` ``None`` for a session with no RR
    stream at all. A resting-shaped capture that recorded no beats is reported as such
    rather than failing silently."""
    session = declared_classify(resting(), rr_intervals=[])

    assert session.rr_valid_fraction is None
    assert FLAG_NO_BEATS in session.quality_flags
    _assert_no_reading(session)


def test_no_beats_does_not_raise_type_error_on_a_null_fraction(resting, declared_classify) -> None:
    """The 500-on-a-valid-upload regression: ``is None`` must be checked *before* the
    float comparison. Asserted as a behavioural test rather than a comment, because a
    naive ``< 0.80`` is a ``TypeError`` here and nothing else in the suite catches it."""
    session = declared_classify(resting(total_timer_time=90.0), rr_intervals=[])

    # Both findings are independent and both apply; neither masks the other.
    assert FLAG_NO_BEATS in session.quality_flags
    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


def test_no_beats_is_not_raised_when_beats_survived(resting, declared_classify) -> None:
    """The negative half of row 3: a healthy capture must not carry the flag."""
    session = declared_classify(resting(), rr_intervals=_beats(), valid_fraction=1.0)

    assert FLAG_NO_BEATS not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_beatless_capture_does_not_reach_a_tier_1_reading(resting, declared_classify) -> None:
    """Belt and braces on the Tier-1 necessary condition: flagging the beatless case
    must not have turned it into a route."""
    session = declared_classify(resting(), rr_intervals=[])

    assert session.activity_tag is None
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# Row 3's precondition -- the flag is a finding about a *declared* capture
# (T066; T058 Finding 17 / row D5)
# ---------------------------------------------------------------------------


def _beatless_hrv_carrier(fake_msg, messages):
    """``messages`` plus one ``hrv`` message whose every ``time`` slot is the FIT
    invalid sentinel.

    ``fitdecode`` parses an invalid slot to ``None`` and
    ``rr_reconstruction._hrv_candidates`` drops it, so the file **carries ``hrv``
    messages and reconstructs to zero beats** -- the single input on which F004's
    two normative wordings of the beats gate disagree (T058 Finding 17), and the
    one R4 resolves toward "beats present".
    """
    return [*messages, fake_msg("hrv", {"time": (None, None, None)})]


def test_an_undeclared_beatless_resting_shaped_file_raises_no_gate_flag(
    synthetic, classified
) -> None:
    """T066's first failing test. A flag asserts a finding **about a recognised
    capture**; an undeclared file has not been recognised as one, so
    ``hrv_capture_no_beats`` on it asserts a finding about something the system
    does not claim.

    Before T066 ``_gate_a_beatless_resting_capture`` fired on any beatless,
    resting-*shaped*, unclaimed file with no reference to the declaration -- the
    note/flag boundary the rest of this feature rests on, crossed from the other
    side. It is the same invariant the multi-session refusal and T064's flagless
    undeclared note already keep; the spec's outline lists the beatless row with
    no declaration qualifier, so consistency with the stated principle decides
    it rather than the literal text.
    """
    session = classified(
        synthetic(total_timer_time=150.0, avg_heart_rate=60, sport_profile_name="Run"),
        rr_intervals=[],
    )

    assert session.rr_valid_fraction is None
    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []
    assert FLAG_READING_UNAVAILABLE not in session.quality_flags
    _assert_no_reading(session)


def test_a_declared_beatless_resting_shaped_file_still_raises_no_beats(
    resting, declared_classify
) -> None:
    """The other direction of the same precondition, and the reason T066 *gates*
    the flag rather than deleting it: the athlete who declared the profile and
    whose strap recorded nothing is exactly who §5 row 3 exists to tell.

    The same file as the undeclared row above in every respect the data can
    show; only the declaration differs."""
    session = declared_classify(resting(), rr_intervals=[])

    assert FLAG_NO_BEATS in session.quality_flags
    _assert_no_reading(session)


def test_an_override_declared_beatless_file_still_raises_no_beats(
    resting, classified
) -> None:
    """``declared`` is a **disjunction** and T066 gates on the whole of it, not on
    the config half.

    F004 states the rule once -- a configured profile name **OR** an upload-time
    override -- and splitting it here would make the flag mean "recognised on one
    route only", a distinction no scenario draws. It would also rest on T058's
    **Finding 5** (whether a beatless *configured* declaration records a note),
    which the resolutions document leaves explicitly deferred: if that finding
    later resolves toward a note, the two routes become indistinguishable and a
    route-split gate becomes indefensible.

    So the ``{"honoured": false, "reason": "no_beats"}`` note and this flag
    coexist deliberately. They say different things: the note reports that the
    *Tier-1 route* could not be taken, and the flag reports what the declared
    capture recorded."""
    session = classified(
        resting(sport_profile_name="Run"),
        rr_intervals=[],
        resting_capture_override=True,
    )

    assert FLAG_NO_BEATS in session.quality_flags
    _assert_no_reading(session)


def test_a_beatless_hrv_carrier_claims_tier_1_and_does_not_fall_to_tier_2(
    fake_msg, synthetic, classified
) -> None:
    """**R4**, ratified 2026-09-06, on the one input the two wordings disagree on.

    F004's normative block says the gate is "beats present"; the reference
    document says "``rr_intervals`` is non-empty". They differ for a file
    carrying ``hrv`` messages that reconstruct to **zero** beats, and R4 resolves
    to the feature file's wording -- consistent with the authority rule and with
    the amendment's own justification, which argues in terms of ``hrv``
    *messages* ("real snapshots carry zero ``hrv`` messages").

    Both consequences are intended and both are asserted here: such a file
    **claims Tier 1 and is flagged**, and it **does not fall through to Tier 2**.
    The file is built Tier-2 eligible on purpose -- ``sport`` 60 plus a device
    ``rmssd_hrv`` -- because that is the only way to tell the two readings apart:
    under the reference document's wording Tier 1 declines, Tier 2 reads the
    device scalar, and no flag is raised at all.

    R4's fall-through concern was weighed and judged theoretical on the evidence,
    and this file is the shape it was weighed against: a degenerate strap capture
    has no ``rmssd_hrv`` and nothing to gain from Tier 2. **The corpus was
    re-measured at T066 and no fixture carries ``hrv`` messages alongside a
    usable ``rmssd_hrv``** -- the five ``rmssd_hrv`` carriers all have zero
    ``hrv`` messages -- which is the condition under which R4 said it must be
    revisited. This synthetic is therefore deliberately *not* a claim that such a
    file exists; it is the discriminating input, and it is synthetic because
    nothing real occupies that state."""
    messages = _beatless_hrv_carrier(
        fake_msg,
        synthetic(
            SNAPSHOT_SPORT,
            total_timer_time=150.0,
            avg_heart_rate=60,
            rmssd_hrv=SNAPSHOT_DEVICE_RMSSD,
            sport_profile_name=DECLARED_PROFILE,
        ),
    )

    session = classified(messages, rr_intervals=[], profile_names=[DECLARED_PROFILE])

    # Consequence 1 -- §5 row 3 is reachable on the Tier-1 path, which is what
    # keeps it from being a false promise.
    assert FLAG_NO_BEATS in session.quality_flags
    # Consequence 2 -- Tier 1 claimed the file, so Tier 2 never ran.
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.activity_tag is None
    _assert_no_reading(session)


def test_a_file_with_zero_hrv_messages_still_falls_through_to_tier_2(
    synthetic, classified
) -> None:
    """The control for the row above, and the normative ordering it must not
    defeat: **the beats gate is evaluated before the declaration**.

    Identical in every field to the R4 row -- declared, resting-shaped, sport 60,
    a device ``rmssd_hrv`` -- except that it carries no ``hrv`` message at all.
    That is what a real Health Snapshot is, and claiming it for Tier 1 would
    route a valid snapshot **nowhere**. Without this control the R4 row above
    would stay green against an implementation that simply deleted the beats
    gate."""
    session = classified(
        synthetic(
            SNAPSHOT_SPORT,
            total_timer_time=150.0,
            avg_heart_rate=60,
            rmssd_hrv=SNAPSHOT_DEVICE_RMSSD,
            sport_profile_name=DECLARED_PROFILE,
        ),
        rr_intervals=[],
        profile_names=[DECLARED_PROFILE],
    )

    assert session.hrv_source_tier == "health_snapshot"
    assert session.rmssd_precomputed == SNAPSHOT_DEVICE_RMSSD
    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []


def test_a_declared_health_snapshot_routes_tier_2_unflagged(declared_config, ingest) -> None:
    """The same control on the real file, end to end -- T063's ordering contract,
    which T066 must not defeat.

    ``sample_health_snapshot.fit`` carries **zero** ``hrv`` messages and the
    profile name ``'Health Snapshot'``, so declaring that name is the exact
    configuration under which a beats gate read as "declared first" would strip a
    working Tier-2 reading and answer it with a failed-capture flag."""
    declared_config(SNAPSHOT_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, SNAPSHOT_FIXTURE)

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == SNAPSHOT_DEVICE_RMSSD
    assert body["resting_rmssd_ms"] == SNAPSHOT_DEVICE_RMSSD
    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []


# ---------------------------------------------------------------------------
# The gates never fire on a file that was never a candidate
# ---------------------------------------------------------------------------


def test_a_ninety_second_ordinary_run_raises_no_gate_flag(synthetic, declared_classify) -> None:
    """The noise guard, and the reason the gates sit *inside* the Tier-1 branch: a 90 s
    run at 3.2 m/s is under 120 s, but it was never a resting capture, so
    ``hrv_capture_too_short`` would be a meaningless finding on it.

    **Declared**, so the vetoes are what refuse it. Undeclared it would be refused
    twice over and the row would stay green with all three thresholds deleted."""
    session = declared_classify(
        synthetic(
            total_timer_time=90.0,
            total_distance=289.0,
            avg_heart_rate=165,
            sport_profile_name=DECLARED_PROFILE,
        ),
        rr_intervals=_beats(),
        valid_fraction=0.5,
    )

    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []


@pytest.mark.parametrize("filename", ["dev_fields_run.fit", "sample_run.fit", "wrist_ppg_run.fit"])
def test_no_ordinary_fixture_picks_up_a_gate_flag(
    filename: str, declared_config, ingest
) -> None:
    """Real files, through the real API. ``wrist_ppg_run.fit`` carries no RR stream at
    all -- so its ``rr_valid_fraction`` is genuinely ``None`` -- and it must still not
    be flagged: it is a run, not a resting capture.

    All three sit on the ``'Run'`` profile, which is **declared** here so the row
    keeps testing the gates rather than the declaration. It is also the exact
    misconfiguration F004 warns about -- the undeclared-candidate note hands the
    athlete the string ``'Run'`` and adding it restores the pre-amendment
    behaviour for every short easy activity -- so this row is where that
    configuration is probed rather than assumed harmless."""
    declared_config("Run")

    with TestClient(app) as client:
        body = ingest(client, filename)

    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []
    assert body["hrv_source_tier"] is None
    assert body["rmssd_precomputed"] is None


@pytest.mark.parametrize(
    ("filename", "rmssd"),
    [("sample_health_snapshot.fit", 37), ("strap_health_snapshot.fit", 51)],
)
def test_a_health_snapshot_keeps_its_tier_2_reading(filename: str, rmssd: int, ingest) -> None:
    """Regression guard on T039. Both snapshots are zero-beat, 120.1 s, low-heart-rate
    files -- exactly the shape rows 1 and 3 describe -- so a gate that ran before the
    Tier-2 branch, or that ignored whether Tier 2 had claimed the file, would strip a
    working reading and flag it as a failed capture."""
    with TestClient(app) as client:
        body = ingest(client, filename)

    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == rmssd
    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []


def test_a_long_resting_shaped_file_is_not_gated(resting, declared_classify) -> None:
    """A capture outside the 300 s upper bound is not a Tier-1 candidate at all, so it
    is silently not routed -- never flagged. The gates report on captures, not on files."""
    session = declared_classify(
        resting(total_timer_time=1800.0), rr_intervals=[], valid_fraction=None
    )

    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []


# ---------------------------------------------------------------------------
# Flag mechanics -- bare strings, deduped, surfaced verbatim
# ---------------------------------------------------------------------------


def test_every_applicable_gate_raises_its_own_flag(resting, declared_classify) -> None:
    """"Raise each that applies" -- they are independent findings. A 90 s capture whose
    beats mostly failed filtering is both too short *and* low quality."""
    session = declared_classify(
        resting(total_timer_time=60.0), rr_intervals=_beats(), valid_fraction=0.25
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    assert FLAG_LOW_QUALITY in session.quality_flags
    _assert_no_reading(session)


def test_a_gate_flag_is_not_duplicated(resting) -> None:
    """``quality_gates.py``'s ``if flag not in session.quality_flags`` convention: bare
    string literals on a ``list[str]``, no enum, no registry, no flags table."""
    messages = resting(total_timer_time=90.0)
    session, _records = mapping.to_canonical(messages)
    session.quality_flags.append(FLAG_TOO_SHORT)
    hrv_classification.classify(
        messages, session, _beats(), profile_names=[DECLARED_PROFILE]
    )
    hrv_classification.classify(
        messages, session, _beats(), profile_names=[DECLARED_PROFILE]
    )

    assert session.quality_flags.count(FLAG_TOO_SHORT) == 1


def test_gate_flags_are_plain_strings(resting, declared_classify) -> None:
    """They are persisted through ``db.py``'s ``_json_dump``/``_json_load`` TEXT
    convention and surface verbatim in both responses -- no new plumbing."""
    session = declared_classify(resting(total_timer_time=90.0), rr_intervals=[])

    assert all(isinstance(flag, str) for flag in session.quality_flags)
    assert set(session.quality_flags) >= {FLAG_TOO_SHORT, FLAG_NO_BEATS}


# ---------------------------------------------------------------------------
# The gate fixture -- a real capture clears all three and keeps its reading
# ---------------------------------------------------------------------------


def test_the_gate_fixture_passes_all_three_gates(declared_config, ingest) -> None:
    """``strap_hrv_capture.fit`` is 150.476 s of real chest-strap beats on the
    declared ``'HRV Snapshot'`` profile. If any gate were mis-signed -- ``>`` for
    ``<``, or the 120/300 bounds swapped -- this reading would disappear, so it is
    the positive control the whole file is written around."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, GATE_FIXTURE)

    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []
    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"
    assert body["rmssd_precomputed"] is None
    # T065. A successful Tier-1 reading resolves the *computed* value into the
    # column E003 reads, while ``rmssd_precomputed`` stays null because no device
    # supplied a number. Before T065 this was the null-shaped trap of IDEA-007:
    # E003's natural query saw only wrist-PPG rows.
    assert body["resting_rmssd_ms"] > 0
    assert body["resting_rmssd_ms"] == pytest.approx(41.52, abs=0.01)


def test_a_failing_capture_uploads_successfully_and_is_stored() -> None:
    """F004 is explicit that the upload succeeds: the *capture* is rejected as an HRV
    input, not the file. Refusing a valid FIT would break F003's flag-don't-refuse
    posture, so no gate may become a 4xx or a 5xx.

    ``wrist_ppg_run.fit`` stands in for the failing upload because no sub-120 s Tier-1
    fixture exists in the corpus; what is asserted here is the HTTP contract -- 201, a
    readable session, no reading -- which is identical on every failing row."""
    with TestClient(app) as client:
        raw = (FIXTURES / "wrist_ppg_run.fit").read_bytes()
        post = client.post("/sessions", files={"file": ("wrist_ppg_run.fit", raw)})

        assert post.status_code == 201, post.text
        assert isinstance(post.json()["quality_flags"], list)
        detail = client.get(f"/sessions/{post.json()['session_id']}")

    assert detail.status_code == 200
    assert detail.json()["rmssd_precomputed"] is None


def test_a_failed_gate_never_writes_a_zero_or_negative_sentinel(resting, declared_classify) -> None:
    """E003 takes ``ln(rMSSD)``. ``0`` and ``-1`` are both falsy *and* both catastrophic
    there -- ``ln(0)`` is undefined and ``ln(-1)`` is a domain error -- which is why
    ``rmssd.resting_rmssd`` returns ``None`` rather than ``0.0`` for "no value" and why
    a failed gate must do the same."""
    for messages, beats, fraction in (
        (resting(total_timer_time=90.0), _beats(), 1.0),
        (resting(), _beats(), 0.5),
        (resting(), [], None),
    ):
        session = declared_classify(messages, rr_intervals=beats, valid_fraction=fraction)

        assert session.rmssd_precomputed is None
        assert session.rmssd_precomputed is not False
        assert session.resting_rmssd_ms is None
        assert session.resting_rmssd_ms is not False
        assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# No derivable statistic -- a tier claiming a reading that does not exist
# ---------------------------------------------------------------------------


def test_a_single_beat_capture_yields_no_reading(resting, declared_classify) -> None:
    """A strap that paired and then dropped: one beat, so **zero contributing pairs**
    and ``rmssd.resting_rmssd`` answers ``None``.

    Every gate above passes -- ``valid_fraction`` on one unflagged beat is ``1.0``, which
    clears 0.80, and 150 s clears 120 s -- so nothing else stops it. Without this check
    the session is stamped ``resting_hrv_check`` / ``chest_strap_raw`` /
    ``chest_strap_ecg``: a completed chest-strap reading carrying no number and no
    explanation, which E003 would read as a tier that produced a value it cannot find.
    "No derivable statistic" is a gate failure like any other -- flagged, not stamped."""
    session = declared_classify(resting(), rr_intervals=_beats(count=1))

    assert session.rr_valid_fraction is None
    assert FLAG_READING_UNAVAILABLE in session.quality_flags
    assert session.activity_tag is None
    _assert_no_reading(session)


def test_a_beat_stream_with_no_usable_pair_yields_no_reading(resting, declared_classify) -> None:
    """The same finding by a different route, and the reason the check is on the
    statistic rather than on ``len(rr_intervals)``: two beats, neither flagged -- so
    ``valid_fraction`` is ``1.0`` and the artefact gate is silent -- but one carries a
    null ``rr_ms``, which ``resting_rmssd`` treats as non-contributing exactly like a
    flagged beat. No pair contributes, so there is no statistic."""
    beats = [
        RRInterval(seq=0, rr_ms=None, rr_source="chest_strap_ecg", is_artefact=False),
        RRInterval(seq=1, rr_ms=1000.0, rr_source="chest_strap_ecg", is_artefact=False),
    ]
    session = declared_classify(resting(), rr_intervals=beats)

    assert FLAG_READING_UNAVAILABLE in session.quality_flags
    _assert_no_reading(session)


def test_a_computed_zero_rmssd_is_not_a_reading_on_either_tier(
    resting,
    declared_classify,
) -> None:
    """F004 @must: "A computed rMSSD of zero is not a reading on either tier".

    **This inverts T042's deliberate call, and the amendment is ratified rather than
    an oversight being corrected.** T042 argued ``0.0`` is "a genuine measurement of
    zero variability", so the test was ``is None`` and never falsiness. Tier 2 had
    always rejected a non-positive *device* ``rmssd_hrv`` to close the ``ln(0)``
    hazard in E003's ``ln(rMSSD)`` trend. Once T065 resolves both tiers into one
    column those two rules cannot both hold: a ``0.0`` would mean success on one
    tier and failure on the other in the field a consumer is told to ``ln()``
    without knowing which tier produced it -- a sibling of the very trap IDEA-007
    closed, wearing a different shape.

    The gate wins on the merits too: a true rMSSD of ``0.0`` requires literally
    identical successive intervals, which is a degenerate beat stream -- a device
    artefact -- not a physiological state.

    Six beats at exactly 1000.0 ms produce five successive differences of exactly
    zero, so ``rmssd.resting_rmssd`` returns a genuine ``0.0`` rather than ``None``.
    That structural distinction inside ``resting_rmssd`` is **unchanged** and still
    load-bearing: the classifier needs to tell "no value derivable" from "a measured
    zero" because the two reach the same flag from different branches, and only one
    of them may claim there were beats."""
    beats = [
        RRInterval(seq=i, rr_ms=1000.0, rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(6)
    ]

    # Guards the fixture itself: if this ever answered ``None`` the test below would
    # pass through the *other* branch and prove nothing about the new gate.
    assert rmssd.resting_rmssd(beats) == 0.0

    session = declared_classify(resting(), rr_intervals=beats)

    assert FLAG_READING_UNAVAILABLE in session.quality_flags
    _assert_no_reading(session)


def test_a_computed_zero_leaves_the_column_null_rather_than_zero(
    resting,
    declared_classify,
) -> None:
    """The distinction the inverted test above is *for*, stated so it cannot be
    satisfied by a sentinel. ``0.0`` is falsy, so ``assert not session.resting_rmssd_ms``
    would pass on both the correct null and the forbidden stored zero."""
    beats = [
        RRInterval(seq=i, rr_ms=1000.0, rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(6)
    ]
    session = declared_classify(resting(), rr_intervals=beats)

    assert session.resting_rmssd_ms is None
    assert session.resting_rmssd_ms != 0.0
    assert "computed_resting_rmssd_ms" not in session.context.provenance


def test_a_tiny_positive_computed_rmssd_is_still_a_reading(resting, declared_classify) -> None:
    """The negative control for the gate: the boundary is ``> 0``, not "large enough".

    No plausibility band is invented on this tier either -- that would be the
    uncitable constant F004 refuses everywhere else -- so a hair above zero routes
    and resolves. A gate written as ``<= 0.1``, or with a float tolerance, would
    still pass the zero test above while silently eating real readings; this pins
    that it does not."""
    beats = [
        RRInterval(
            seq=i,
            rr_ms=1000.0 + (0.001 if i % 2 else 0.0),
            rr_source="chest_strap_ecg",
            is_artefact=False,
        )
        for i in range(6)
    ]
    computed = rmssd.resting_rmssd(beats)
    assert computed is not None and 0 < computed < 0.01

    session = declared_classify(resting(), rr_intervals=beats)

    assert FLAG_READING_UNAVAILABLE not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.resting_rmssd_ms == computed
    assert session.resting_rmssd_ms > 0


def test_a_healthy_capture_does_not_raise_the_unavailable_flag(resting, declared_classify) -> None:
    """The negative control: the flag is raised only when the statistic is genuinely
    underivable, never on an ordinary Tier-1 reading."""
    session = declared_classify(resting(), rr_intervals=_beats())

    assert FLAG_READING_UNAVAILABLE not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"
    # T065. The single success point writes both: the audit record in provenance --
    # which keeps its Tier-1 role as the twin of ``rmssd_precomputed``'s Tier-2 one,
    # and is what proves the number came from the beats rather than from a device --
    # and the resolved column, from the *same* value, so the two cannot drift.
    computed = session.context.provenance["computed_resting_rmssd_ms"]
    assert session.resting_rmssd_ms == computed
    assert session.resting_rmssd_ms > 0
    # ``rmssd_precomputed`` keeps its device-only meaning: Tier 1 leaves it null,
    # which is this feature's own ratified rule and the reason a second parallel
    # column was rejected rather than reusing this one.
    assert session.rmssd_precomputed is None
