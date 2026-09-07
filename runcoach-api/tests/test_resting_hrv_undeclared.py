"""T064: what a **refused** file records, so no refusal is invisible.

F004's 2026-09-06 amendment made "everything not declared" the negative class.
That closes ``IDEA-010``, and it makes the feature's worst property worse: **the
false-negative direction is invisible by construction** -- a missing reading is
indistinguishable from a day the athlete did not measure, and an unconfigured
profile refuses everything. This module owns the answer to that, which is a
**provenance note on every refusal path Tier 1 owns**::

    hrv_undeclared_capture_candidate  {"sport_profile_name": <as read>,
                                       "vetoed_by": <veto or null>}
    hrv_declared_capture_vetoed       {"sport_profile_name": <as read>,
                                       "vetoed_by": <veto>}
    hrv_resting_capture_override      {"honoured": true}
                                      {"honoured": false, "reason": <veto|no_beats>}

**A note is not a flag, and the distinction is load-bearing** (T066 depends on
it). A ``hrv_capture_*`` flag asserts a finding *about a recognised capture*;
every file in this module was **refused**, so it was never recognised as one.
Flagging a refusal would put an HRV quality flag on every ordinary run carrying
``hrv`` messages and train the reader to ignore the flag -- the same argument
``_classify_tier_2``'s row 2 and the multi-session refusal already made, in the
same module, for the same reason. Every test below that asserts a note also
asserts no ``hrv_`` flag was raised.


R5 changed this task's scope after its file was written
=======================================================

``spec/references/F004-contract-resolutions.md`` **R5** was ratified on
2026-09-06, resolving T058's Findings 3 and 4:

* **Finding 4.** The note as first specified fired only for a file that "has
  beats **and passes every veto**" but is undeclared. So a genuine resting
  capture recorded on the wrong profile that *also* trips a veto got no route,
  no flag and **no note** -- precisely the invisible false negative the note
  exists to eliminate. ``strap_cool_down_walk.fit`` landed in exactly that hole
  and ingested with no observable record whatsoever;
  ``test_the_cool_down_walk_now_leaves_an_observable_record`` is that file,
  before/after.
* **Finding 3.** A **declared** file killed by a veto got no route, no flag and
  no note either -- the athlete who *did* the setup step the whole amendment
  asks of them, silently discarded. R5 resolves that both sides record a note.

So the note fires on the refusal path **including when a veto fired, and it must
name which veto** -- a note that does not say why does not discharge the goal.
Two consequences are deliberate and are pinned here:

1. ``vetoed_by`` is written **explicitly as ``null``** on a clean refusal rather
   than omitted. That is the whole diagnostic difference between "configure this
   profile name and captures like this one will route" (``vetoed_by`` null) and
   "this file contradicted itself anyway" (a named veto), and a consumer that
   has to infer it from a missing key will get it wrong.
2. The note now appears on **ordinary runs that carry beats** --
   ``strap_run_hrv.fit``, ``dev_fields_run.fit`` -- because they are undeclared
   *and* vetoed. The original task file called that noise and gated the note on
   the vetoes to avoid it; R5 overruled that, and the named ``vetoed_by`` is
   what keeps the two populations filterable in one key.
   ``test_the_note_separates_the_actionable_refusal_from_the_ordinary_one``
   pins the separation rather than trusting it.


Two limits on the note, both load-bearing (F004, and unchanged by R5)
=====================================================================

* **The recorded name is diagnostic, never a recommendation.** On this corpus
  every non-snapshot file reads ``'Run'``, and adding *that* to config restores
  the pre-amendment behaviour exactly -- every short easy activity on the
  running profile routes again, caught only by the demoted vetoes, which is the
  design ``IDEA-010`` exists to kill. **A general-purpose activity profile must
  never be listed.** The note reports what the file *claimed*, not that the
  claim is safe to trust, and it never asserts the claim is *true*.
* **The note is prospective and cannot rescue the session that produced it.**
  ``db.py``'s ``UNIQUE (source_device, start_time)`` answers a re-upload with a
  409 and adding a name to config reclassifies nothing already stored. Acting on
  the note means *configure the profile, then delete the session (T072) and
  upload again, or capture again* -- never *re-upload over the top*.


Rows deliberately **not** claimed here
======================================

* **A beatless file records no undeclared note** (Table A8): the beats gate
  precedes everything, and R5 moved only the *veto* gate. Without that, every
  beatless upload -- every Health Snapshot, every wrist-PPG run -- would carry a
  note, which is noise no ``vetoed_by`` value could filter.
* **A beatless file on a configured profile records nothing either.** That is
  T058's **Finding 5**, which the resolutions document explicitly leaves
  deferred; inventing an answer here would pre-empt it. Pinned as absence, with
  the finding named, so the next reader knows it was seen and left.
* **Finding 7** (both declaration routes satisfied at once) is deferred too.
  This module records what is true of each route independently -- the override
  note says the override was honoured; the config-route note says a configured
  name was vetoed -- so a doubly-declared file gets both, and neither claims
  exclusivity. See ``test_a_doubly_declared_vetoed_file_records_both_notes``.
* **``activity_tag`` on a recognised-but-flagged capture is R6/T075**, not this
  task. Nothing here asserts it in either direction on that path.


Adversarial probe table (``.claude/rules/learnings/adversarial-input-probes...``)
================================================================================

The inputs the note logic actually reads, after mapping: the three
``session.summary`` intensity signals (through ``_intensity_signal``),
``context.provenance["sport_profile_name"]`` (lifted verbatim by T056, any type,
key absent when the file carried nothing), the configured name list, the
override boolean, and whether the beat stream is empty. Every row below is
driven through ``mapping.to_canonical`` -> ``classify``, or through a real
fixture end to end.

============================== ======== ============================== ===== =====
input                          routes?  note                           veto  flag
============================== ======== ============================== ===== =====
undeclared, clean, beats       no       undeclared, ``vetoed_by`` null  --    none
undeclared, duration absent    no       undeclared                     duration_absent none
undeclared, duration 0.0       no       undeclared                     duration_absent none
undeclared, duration 6000 s    no       undeclared                     duration_out_of_range none
undeclared, HR absent          no       undeclared                     heart_rate_absent none
undeclared, HR 0               no       undeclared                     heart_rate_absent none
undeclared, HR 140             no       undeclared                     heart_rate_too_high none
undeclared, 1.04 m/s           no       undeclared                     mean_speed_too_high none
undeclared, duration ``(1,)``  no       undeclared                     duration_unreadable none
undeclared, distance ``-5``    no       undeclared                     distance_unreadable none
undeclared, HR ``"60"``        no       undeclared                     heart_rate_unreadable none
declared (config), each veto   no       declared-vetoed                as above none
declared (override), each veto no       override not honoured          as above none
declared, clean, beats         **T1**   none (or override honoured)    --    none
declared, clean, 100 s         no*      none -- recognised, flagged    --    hrv_capture_too_short
override, no beats             no       override not honoured          no_beats no_beats+
undeclared, no beats           no       **none** (A8)                  --    no_beats+
declared (config), no beats    no       **none** (Finding 5, deferred) --    no_beats+
multi-session, declared        no       **none** (Finding 14)          --    none
============================== ======== ============================== ===== =====

\\* "recognised and answered with a flag" rather than refused -- the §5 quality
band, which is a different band from the veto band and is not this module's.

+ **Not this task's flag, and a finding rather than a footnote.**
``_gate_a_beatless_resting_capture`` raises ``hrv_capture_no_beats`` on any
resting-*shaped* beatless file Tier 2 declined, with no reference to the
declaration -- so a file that was never recognised as a capture carries a flag
asserting a finding about one. That is the note/flag boundary this module rests
on, crossed from the other side; it is T058's Finding 17 / row D5, and **T066
owns it by that task's own statement**. Nothing here changes it, and
``test_an_override_on_a_beatless_file_reports_that_it_did_nothing`` pins the
coexistence so T066 changes it deliberately rather than discovering it.

Three probe results worth recording rather than a bare pass:

1. **``strap_cool_down_walk.fit`` was the whole point and it now has a record.**
   Driven through the real classifier before this task it produced: no route, no
   flag, and a provenance dict holding nothing but ``mapping.py``'s own three
   keys plus ``sport_profile_name``. It is undeclared *and* vetoed at 1.0356 m/s,
   so both the old gates hid it. It is used here as **evidence of the hole**, and
   never as a pin for the declaration rule -- it is over-determined three ways
   over (speed veto, 118.874 s under the §5 minimum, undeclared), exactly the
   mistake ``contract-tables-need-an-independent-oracle`` names.
2. **Five of the ten corpus fixtures now carry an undeclared note** when nothing
   is configured (the two genuine captures with ``vetoed_by`` null, plus
   ``strap_run_hrv``, ``dev_fields_run`` and ``strap_cool_down_walk`` with a
   named veto); the five beatless fixtures carry none. That distribution is
   asserted directly in ``test_the_corpus_note_distribution_is_what_r5_intends``
   so the noise cost R5 accepted is visible in the suite rather than argued
   about in prose.
3. **A beatless file is flagged while being refused** -- the ``+`` footnote
   above. Found by probing the override-on-a-beatless-file scenario and
   expecting silence; the flag was already there, from a gate that predates the
   declaration rule. Reported to T066 rather than fixed here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api.ingestion import (
    fit_parser,
    hrv_classification,
    mapping,
    rr_reconstruction,
)
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The three note keys, spelled out here independently of the module's own
# constants -- the ``KNOWN_CLASSIFICATION_INPUTS`` discipline. Asserting through
# ``hrv_classification._PROVENANCE_*`` alone would keep passing if a constant
# were quietly renamed, and these strings are what E003 and the athlete read.
UNDECLARED_NOTE = "hrv_undeclared_capture_candidate"
DECLARED_VETOED_NOTE = "hrv_declared_capture_vetoed"
OVERRIDE_NOTE = "hrv_resting_capture_override"

NOTE_KEYS = (UNDECLARED_NOTE, DECLARED_VETOED_NOTE, OVERRIDE_NOTE)

# The athlete's own custom FR945 LTE profile and its capture (149 ``hrv``
# messages / 165 beats, 150.476 s, 0.0 m, avg HR 64).
DECLARED_PROFILE = "HRV Snapshot"
DECLARED_FIXTURE = "strap_hrv_capture.fit"

# The corpus's undeclared negative: a genuine resting capture recorded on the
# **'Run'** profile. 156 beats, 150.797 s, 108.21 m (0.718 m/s), avg HR 60. It
# passes every veto, so its note is the ``vetoed_by`` null one -- the actionable
# refusal.
UNDECLARED_FIXTURE = "strap_hrv_sample_run.fit"
UNDECLARED_PROFILE = "Run"

# An ordinary 6000 s / 19.2 km run on the same 'Run' profile, carrying 13659
# beats. Undeclared *and* vetoed: the population R5 knowingly admitted into the
# note.
ORDINARY_RUN_FIXTURE = "strap_run_hrv.fit"

# 118.874 s / 123.1 m / avg HR 99 / 195 beats on 'Run'. Undeclared and vetoed at
# 1.0356 m/s -- the file that landed in Finding 4's hole.
COOL_DOWN_WALK_FIXTURE = "strap_cool_down_walk.fit"

# A real Garmin Health Snapshot: sport 60, ``rmssd_hrv`` 37, **zero** ``hrv``
# messages. The beatless file both the override note and Tier 2 have something
# to say about.
SNAPSHOT_FIXTURE = "sample_health_snapshot.fit"
SNAPSHOT_PROFILE = "Health Snapshot"
SNAPSHOT_DEVICE_RMSSD = 37

# A clean declared synthetic, matching the real declared fixture's numbers so a
# synthetic row and the real file cannot drift apart.
CLEAN_CAPTURE = {
    "total_timer_time": 150.476,
    "total_distance": 0.0,
    "avg_heart_rate": 64,
    "sport_profile_name": DECLARED_PROFILE,
}


def _beats(count: int = 40):
    """A clean beat stream: every beat retained, successive intervals differing,
    so ``valid_fraction`` is 1.0 and ``resting_rmssd`` yields a positive value.

    Built exactly as ``test_resting_hrv_declaration.py`` builds one, so a change
    to what the gates want does not have to be discovered twice.
    """
    return [
        RRInterval(
            seq=index,
            rr_ms=1000.0 + (20.0 if index % 2 else 0.0),
            rr_source="chest_strap_ecg",
            is_artefact=False,
        )
        for index in range(count)
    ]


def _classify_fixture(filename: str, profile_names=None, resting_capture_override=False):
    """Decode a real fixture and drive the full mapping + reconstruction +
    classify path -- the same helper shape ``test_resting_hrv_tier1.py`` uses.

    The probe rule asks for the *real* path: the note reads a provenance key
    ``mapping.py`` lifts and a summary ``_build_summary`` strips ``None`` from,
    and neither behaviour is visible to a hand-built ``Session``.
    """
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())
    session, _records = mapping.to_canonical(messages)
    rr_intervals = rr_reconstruction.reconstruct(messages)
    hrv_classification.classify(
        messages,
        session,
        rr_intervals,
        profile_names=profile_names,
        resting_capture_override=resting_capture_override,
    )
    return session


def _provenance(session) -> dict:
    return session.context.provenance if session.context else {}


def _hrv_flags(session) -> list[str]:
    """F004's own flags only. F003's ``smart_recording`` is a finding about the
    record stream and fires on real Garmin captures for unrelated reasons."""
    return [flag for flag in session.quality_flags if flag.startswith("hrv_")]


def _routed(session) -> bool:
    return (
        session.activity_tag == "resting_hrv_check"
        and session.hrv_source_tier == "chest_strap_raw"
    )


def _notes(provenance: dict) -> dict:
    """Only this task's keys, so an assertion about "which note" cannot be
    satisfied by ``mapping.py``'s own provenance entries."""
    return {key: value for key, value in provenance.items() if key in NOTE_KEYS}


# ---------------------------------------------------------------------------
# 1. the undeclared note: a clean capture nobody declared
# ---------------------------------------------------------------------------


def test_an_undeclared_clean_capture_records_the_profile_name_it_claimed() -> None:
    """The task's first failing test, on the real file it names.

    ``strap_hrv_sample_run.fit`` uploaded with nothing configured: no route, and
    the note carries the string the athlete would have to configure. ``vetoed_by``
    is ``null`` because this capture passes every veto -- it is refused for one
    reason only, that it was never declared, which is what makes it the
    actionable case."""
    session = _classify_fixture(UNDECLARED_FIXTURE)

    assert not _routed(session)
    assert _provenance(session)[UNDECLARED_NOTE] == {
        "sport_profile_name": UNDECLARED_PROFILE,
        "vetoed_by": None,
    }


def test_the_undeclared_note_raises_no_quality_flag() -> None:
    """"And no quality flag is raised, because it was never recognised as a
    capture." A flag asserts a finding about a *recognised* capture; this file
    was explicitly not recognised as one, which is the same reasoning §1 row 2
    and the multi-session refusal already applied."""
    session = _classify_fixture(UNDECLARED_FIXTURE)

    assert _hrv_flags(session) == []


def test_the_undeclared_note_reaches_stored_provenance(ingest) -> None:
    """The integration half: what ``classify()`` left in memory is not what the
    athlete can act on. This is the round trip E003 and a human both read."""
    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE)

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["resting_rmssd_ms"] is None
    assert body["context"]["provenance"][UNDECLARED_NOTE] == {
        "sport_profile_name": UNDECLARED_PROFILE,
        "vetoed_by": None,
    }


def test_a_capture_carrying_no_profile_name_records_null(synthetic, classified) -> None:
    """"A file carrying no profile name is simply undeclared" -- and the note
    records an explicit ``null``.

    The asymmetry against T056's provenance *lift* is deliberate and both halves
    are right: the lift omits the key entirely when the file carried nothing,
    because it records only what was there; the note asserts a finding -- "we
    looked, and the file claimed nothing" -- which is exactly what makes a
    third-party exporter that omits the field diagnosable rather than
    mysterious."""
    session = classified(
        synthetic(
            total_timer_time=150.0,
            total_distance=0.0,
            avg_heart_rate=60,
        ),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert "sport_profile_name" not in _provenance(session)
    assert _provenance(session)[UNDECLARED_NOTE] == {
        "sport_profile_name": None,
        "vetoed_by": None,
    }


@pytest.mark.parametrize(
    "claimed",
    [
        pytest.param("Health Snapshot", id="substring-collision-A11"),
        pytest.param("hrv snapshot", id="case-A12"),
        pytest.param("HRV Snapshot ", id="trailing-space-A13"),
        pytest.param("", id="empty-string-A15-Finding-13"),
        pytest.param("   ", id="whitespace-only-A16"),
        pytest.param(42, id="non-str-int-A17-Finding-12"),
        pytest.param(b"HRV Snapshot", id="non-str-bytes-A17"),
        pytest.param((1, 2), id="non-str-tuple-A17"),
    ],
)
def test_the_note_records_the_claimed_name_exactly_as_read(
    claimed, synthetic, classified
) -> None:
    """Every near-miss of the exact, case-sensitive match, recorded verbatim.

    This is what makes exact matching tolerable: a config typo becomes
    self-diagnosing, because the note hands back the precise string that failed
    to match -- trailing space, wrong case and all. Normalising it here would
    destroy the only diagnostic the athlete has.

    ``""`` is T058's **Finding 13**, left open by the resolutions: the note
    records ``""`` rather than ``null``, because "the recorded name reports what
    the file claimed" and the file did claim an empty name. ``null`` is reserved
    for the genuinely different state where the file carried no field at all,
    which the row above pins.

    A non-``str`` name (Finding 12) is recorded as read too. It is not a veto --
    the reading convention's veto is scoped to the three *intensity* signals,
    because a veto says the file's data is corrupt, while a corrupt identity
    field says only that the athlete did not declare. The value is already in
    provenance verbatim under ``sport_profile_name`` (T056's lift), so recording
    it a second time introduces no serialisation hazard that was not already
    there."""
    session = classified(
        synthetic(
            total_timer_time=150.0,
            total_distance=0.0,
            avg_heart_rate=60,
            sport_profile_name=claimed,
        ),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert _provenance(session)[UNDECLARED_NOTE] == {
        "sport_profile_name": claimed,
        "vetoed_by": None,
    }


def test_an_empty_config_list_still_records_what_the_file_claimed(
    declared_config, ingest
) -> None:
    """"An athlete who uses only Health Snapshot declares that explicitly."

    ``resting_hrv_profile_names = []`` is an explicit "no activity profile means
    a resting capture". The declared positive then does not route -- and the note
    reports ``'HRV Snapshot'``, which is *what the file claimed*, not what config
    should contain. The distinction matters: the note is diagnostic, and reading
    it as a recommendation is how a general-purpose profile ends up configured."""
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["hrv_source_tier"] is None
    assert body["context"]["provenance"][UNDECLARED_NOTE] == {
        "sport_profile_name": DECLARED_PROFILE,
        "vetoed_by": None,
    }


# ---------------------------------------------------------------------------
# 2. R5, Finding 4: the note fires when a veto fired, and names it
# ---------------------------------------------------------------------------

# One row per veto in ``_resting_profile``'s vocabulary, driven at the canonical
# layer through the real mapping. The pairs are (session fields, veto name).
_VETO_ROWS = [
    pytest.param({"total_timer_time": (1, 2)}, "duration_unreadable", id="duration-tuple"),
    pytest.param(
        {"total_timer_time": 150.0, "total_distance": -5.0, "avg_heart_rate": 60},
        "distance_unreadable",
        id="distance-negative-R2",
    ),
    pytest.param(
        {"total_timer_time": 150.0, "avg_heart_rate": "60"},
        "heart_rate_unreadable",
        id="heart-rate-str",
    ),
    pytest.param(
        {"total_timer_time": -1.0, "avg_heart_rate": 60},
        "duration_unreadable",
        id="duration-negative-structurally-impossible",
    ),
    pytest.param({"avg_heart_rate": 60}, "duration_absent", id="duration-key-absent"),
    pytest.param(
        {"total_timer_time": 0.0, "avg_heart_rate": 60},
        "duration_absent",
        id="duration-present-and-zero-is-nothing-reported",
    ),
    pytest.param(
        {"total_timer_time": 6000.0, "avg_heart_rate": 60},
        "duration_out_of_range",
        id="duration-6000s",
    ),
    pytest.param(
        {"total_timer_time": 300.001, "avg_heart_rate": 60},
        "duration_out_of_range",
        id="one-millisecond-over-the-ceiling",
    ),
    pytest.param(
        {"total_timer_time": 150.0, "total_distance": 0.0},
        "heart_rate_absent",
        id="heart-rate-key-absent",
    ),
    pytest.param(
        {"total_timer_time": 150.0, "avg_heart_rate": 0},
        "heart_rate_absent",
        id="heart-rate-present-and-zero",
    ),
    pytest.param(
        {"total_timer_time": 150.0, "avg_heart_rate": 140},
        "heart_rate_too_high",
        id="heart-rate-140",
    ),
    pytest.param(
        {"total_timer_time": 100.0, "total_distance": 200.0, "avg_heart_rate": 60},
        "mean_speed_too_high",
        id="2-m-per-s",
    ),
]


@pytest.mark.parametrize(("fields", "veto"), _VETO_ROWS)
def test_an_undeclared_vetoed_capture_records_a_note_naming_the_veto(
    fields, veto, synthetic, classified
) -> None:
    """T058 Finding 4, resolved by R5.

    Before this, a genuine resting capture recorded on the wrong profile that
    *also* tripped a veto got no route, no flag and no note -- precisely the
    invisible false negative the note exists to eliminate. The note now fires,
    and it **names which veto**, because a note that does not say why does not
    discharge the goal: "configure your profile" and "this file was 6000 seconds
    long" are different diagnoses and the athlete cannot act on the wrong one."""
    session = classified(
        synthetic(sport_profile_name=UNDECLARED_PROFILE, **fields),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert _hrv_flags(session) == []
    assert _provenance(session)[UNDECLARED_NOTE] == {
        "sport_profile_name": UNDECLARED_PROFILE,
        "vetoed_by": veto,
    }


def test_the_note_separates_the_actionable_refusal_from_the_ordinary_one() -> None:
    """R5 accepted a real cost: the note now fires on ordinary runs that carry
    beats -- 4 of the 10 corpus fixtures. ``vetoed_by`` is what keeps the two
    populations apart in one key, so it is asserted as a *separation* rather than
    row by row.

    ``strap_hrv_sample_run.fit`` is a genuine capture on the wrong profile:
    ``vetoed_by`` null, and configuring 'Run' would route captures like it (which
    is exactly why the note is diagnostic and not a recommendation).
    ``strap_run_hrv.fit`` is a 6000 s run on the same profile: the same claimed
    name, a named veto. A reader filtering on ``vetoed_by is null`` sees the
    first and not the second."""
    capture = _classify_fixture(UNDECLARED_FIXTURE)
    ordinary_run = _classify_fixture(ORDINARY_RUN_FIXTURE)

    assert _provenance(capture)[UNDECLARED_NOTE]["sport_profile_name"] == UNDECLARED_PROFILE
    assert _provenance(ordinary_run)[UNDECLARED_NOTE]["sport_profile_name"] == UNDECLARED_PROFILE

    assert _provenance(capture)[UNDECLARED_NOTE]["vetoed_by"] is None
    assert _provenance(ordinary_run)[UNDECLARED_NOTE]["vetoed_by"] == "duration_out_of_range"


def test_the_cool_down_walk_now_leaves_an_observable_record(ingest) -> None:
    """The file R5 was ratified for, driven end to end.

    ``strap_cool_down_walk.fit`` is undeclared *and* vetoed (1.0356 m/s over
    118.874 s), so before R5 it ingested with **no route, no flag and no note** --
    a genuine short activity indistinguishable from a day nothing was recorded.
    It now carries a note that names the speed veto.

    It is used here as *evidence of the hole*, never as a pin for the declaration
    rule: it is refused three ways over, so an assertion that it does not route
    would pass with any one of those rules deleted."""
    with TestClient(app) as client:
        body = ingest(client, COOL_DOWN_WALK_FIXTURE)

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert [flag for flag in body["quality_flags"] if flag.startswith("hrv_")] == []
    assert body["context"]["provenance"][UNDECLARED_NOTE] == {
        "sport_profile_name": UNDECLARED_PROFILE,
        "vetoed_by": "mean_speed_too_high",
    }


def test_every_veto_reason_is_reachable_and_declared() -> None:
    """The vocabulary is reconciled in both directions, the way
    ``HRV_INPUT_FIELDS`` is.

    A veto name the module can emit but the table above never produces is an
    unprobed branch; a name in the vocabulary nothing can reach is a false
    promise of the kind ``IDEA-013`` names. Both are failures here."""
    declared = frozenset(hrv_classification.HRV_VETO_REASONS)
    exercised = frozenset(param.values[1] for param in _VETO_ROWS)

    assert exercised - declared == frozenset()
    assert declared - exercised == frozenset()


# ---------------------------------------------------------------------------
# 3. R5, Finding 3: the declared side records a note too
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fields", "veto"), _VETO_ROWS)
def test_a_declared_and_vetoed_file_records_a_note_naming_the_veto(
    fields, veto, synthetic, classified
) -> None:
    """T058 Finding 3, resolved by R5, and the half a review found assigned to
    no task at all.

    This is the athlete who **did** declare -- who did the setup step the whole
    amendment asks of them -- and whose file was then killed by a veto. Before
    R5 that produced no route, no flag and no note: silently discarded. The
    argument for observability applies with *more* force here, not less."""
    session = classified(
        synthetic(sport_profile_name=DECLARED_PROFILE, **fields),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert _hrv_flags(session) == []
    assert _provenance(session)[DECLARED_VETOED_NOTE] == {
        "sport_profile_name": DECLARED_PROFILE,
        "vetoed_by": veto,
    }


def test_the_declared_veto_note_is_a_different_key_from_the_undeclared_one(
    synthetic, classified
) -> None:
    """Five keys now share one loosely-typed dict, so "which note" must be
    answerable without reading the payload.

    The two say genuinely different things -- "you never declared this, and here
    is the name it claimed" versus "you declared this and it contradicted the
    declaration" -- and they lead to different actions. Collapsing them onto one
    key would make an athlete who configured their profile indistinguishable from
    one who did not."""
    vetoed_fields = {"total_timer_time": 6000.0, "avg_heart_rate": 60}

    declared = classified(
        synthetic(sport_profile_name=DECLARED_PROFILE, **vetoed_fields),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )
    undeclared = classified(
        synthetic(sport_profile_name=UNDECLARED_PROFILE, **vetoed_fields),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert set(_notes(_provenance(declared))) == {DECLARED_VETOED_NOTE}
    assert set(_notes(_provenance(undeclared))) == {UNDECLARED_NOTE}


def test_the_declared_veto_note_reaches_stored_provenance(declared_config, ingest) -> None:
    """The declared-and-vetoed round trip, on a real file: an ordinary 6000 s run
    recorded on a profile the athlete *has* configured -- the "forgot to stop the
    timer on the HRV profile" case the vetoes exist for.

    Configuring ``'Run'`` here is a **test posture, not a recommendation**: F004
    says in boldface that a general-purpose activity profile must never be listed,
    and this row is what that mistake looks like from the inside."""
    declared_config(UNDECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, ORDINARY_RUN_FIXTURE)

    assert body["hrv_source_tier"] is None
    assert [flag for flag in body["quality_flags"] if flag.startswith("hrv_")] == []
    assert body["context"]["provenance"][DECLARED_VETOED_NOTE] == {
        "sport_profile_name": UNDECLARED_PROFILE,
        "vetoed_by": "duration_out_of_range",
    }
    assert UNDECLARED_NOTE not in body["context"]["provenance"]


# ---------------------------------------------------------------------------
# 4. the override note -- one key, saying what the override did
# ---------------------------------------------------------------------------


def test_an_honoured_override_records_that_the_route_came_from_it(ingest) -> None:
    """"Provenance records that the route came from an override rather than the
    profile name" (A2), on the one real file the override exists for.

    ``honoured`` is a statement about the **declaration**, not about the reading:
    it says the override was accepted and Tier 1 claimed the file. Whether a
    number came out is ``resting_rmssd_ms``'s business and a failed gate's flag,
    which is the same note/flag split this whole module rests on."""
    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE, data={"resting_capture": "true"})

    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["context"]["provenance"][OVERRIDE_NOTE] == {"honoured": True}
    assert UNDECLARED_NOTE not in body["context"]["provenance"]


def test_an_override_on_a_beatless_file_reports_that_it_did_nothing(
    synthetic, classified
) -> None:
    """"An override on a file with no beats reports that it did nothing."

    The beats gate precedes the declaration and that order is normative, so the
    override cannot be honoured -- and saying so is the entire content of this
    scenario. Without the note the athlete's deliberate per-upload act vanishes
    without trace.

    **Probe result worth recording rather than a bare pass.** This file *also*
    comes back carrying ``hrv_capture_no_beats``, and that flag does not come
    from this task. ``_gate_a_beatless_resting_capture`` raises it on any
    resting-shaped beatless file Tier 2 declined, with no reference to the
    declaration -- so a file that was never recognised as a capture carries a
    quality flag asserting a finding about one, which is the contradiction
    T058's Finding 17 / D5 names and **T066 owns by its own statement**. It is
    asserted here rather than worked around, so the note and the flag are seen
    to coexist and T066 has a row to change deliberately."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=[],
        resting_capture_override=True,
    )

    assert not _routed(session)
    assert _hrv_flags(session) == ["hrv_capture_no_beats"]
    assert _provenance(session)[OVERRIDE_NOTE] == {"honoured": False, "reason": "no_beats"}


def test_an_override_on_a_beatless_snapshot_still_routes_tier_2(ingest) -> None:
    """The not-honoured note and a Tier-2 route are not alternatives.

    F004 states the two scenarios over different declaration routes and never
    combines them; this is the reading that satisfies both. A real Health
    Snapshot carries zero ``hrv`` messages, so an override on one cannot be
    honoured -- and Tier 2 reads it perfectly well regardless, which is exactly
    what the normative beats-first ordering exists to protect."""
    with TestClient(app) as client:
        body = ingest(client, SNAPSHOT_FIXTURE, data={"resting_capture": "true"})

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == SNAPSHOT_DEVICE_RMSSD
    assert body["context"]["provenance"][OVERRIDE_NOTE] == {
        "honoured": False,
        "reason": "no_beats",
    }


@pytest.mark.parametrize(("fields", "veto"), _VETO_ROWS)
def test_an_override_cannot_rescue_a_vetoed_file_and_says_which_veto_stopped_it(
    fields, veto, synthetic, classified
) -> None:
    """"Both declaration routes are subject to every veto" -- and the refusal is
    recorded on the route the athlete actually used.

    The override is a claim about *intent*, never about the data: it cannot
    rescue a 6000 s file or a 140 bpm one. It is otherwise the branch tests do
    not take, which is precisely how the zero-distance bug survived six waves, so
    every veto row is re-run across it."""
    session = classified(
        synthetic(**fields),
        rr_intervals=_beats(),
        resting_capture_override=True,
    )

    assert not _routed(session)
    assert _hrv_flags(session) == []
    assert set(_notes(_provenance(session))) == {OVERRIDE_NOTE}
    assert _provenance(session)[OVERRIDE_NOTE] == {"honoured": False, "reason": veto}


def test_a_doubly_declared_vetoed_file_records_both_notes(synthetic, classified) -> None:
    """T058 **Finding 7** is deferred, so this records rather than resolves.

    Both declaration routes are satisfied at once and a veto fires. Each note
    states only what is true of its own route -- the override was not honoured,
    and a configured name was vetoed -- so writing both asserts nothing about
    which route "would have" routed the file. Picking one would be an answer the
    resolutions document deliberately did not give."""
    session = classified(
        synthetic(sport_profile_name=DECLARED_PROFILE, total_timer_time=6000.0, avg_heart_rate=60),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert set(_notes(_provenance(session))) == {DECLARED_VETOED_NOTE, OVERRIDE_NOTE}
    assert _provenance(session)[OVERRIDE_NOTE]["reason"] == "duration_out_of_range"
    assert _provenance(session)[DECLARED_VETOED_NOTE]["vetoed_by"] == "duration_out_of_range"


# ---------------------------------------------------------------------------
# 5. where no note belongs -- the rows this task deliberately leaves silent
# ---------------------------------------------------------------------------


def test_a_routed_declared_capture_records_no_note(declared_config, ingest) -> None:
    """A1. The file routed, so there is nothing to explain. A note on a
    successful reading would be the same over-reporting the veto gate was
    originally meant to prevent, in the one place it genuinely does not belong."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert _notes(body["context"]["provenance"]) == {}


def test_a_recognised_but_flagged_capture_records_no_note(synthetic, classified) -> None:
    """The §5 quality band is a different band from the veto band, and the
    distinction is load-bearing.

    A 100 s declared capture **passes** every veto -- the veto bounds duration at
    ``(0, 300]`` -- so Tier 1 *claims* it and the §5 minimum-duration gate then
    raises ``hrv_capture_too_short``. It is a **recognised** capture that yielded
    no number: a flag is exactly the right instrument and a refusal note would be
    a false statement about it.

    ``activity_tag`` is deliberately not asserted here in either direction: R6
    resolved that a recognised-but-flagged capture is tagged, and implementing it
    is T075's, not this task's."""
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "total_timer_time": 100.0}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert _hrv_flags(session) == ["hrv_capture_too_short"]
    assert _notes(_provenance(session)) == {}


def test_an_undeclared_beatless_file_records_no_note(synthetic, classified) -> None:
    """Table A8. The beats gate precedes everything and R5 moved only the *veto*
    gate.

    Without this the note would fire on every beatless upload -- every Health
    Snapshot, every wrist-PPG run, every ordinary activity -- which is noise no
    ``vetoed_by`` value could filter, and the note would train the reader to
    ignore it. That is the same argument the multi-session refusal makes about
    flagging every triathlon."""
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": UNDECLARED_PROFILE}),
        rr_intervals=[],
        profile_names=[DECLARED_PROFILE],
    )

    assert _notes(_provenance(session)) == {}


def test_a_declared_beatless_file_records_no_note_because_finding_5_is_deferred(
    synthetic, classified
) -> None:
    """T058 **Finding 5**, pinned as an absence rather than answered.

    A configured profile name on a beatless file that Tier 2 also declines
    disappears entirely -- the override route gets a note in the same situation
    and the config route does not. The resolutions document lists Finding 5 among
    those explicitly **not** resolved, so inventing a note here would pre-empt a
    decision that is not this task's to take. Recorded as a test so the silence
    is deliberate and visible rather than an oversight."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=[],
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert _notes(_provenance(session)) == {}


def test_a_multi_session_file_records_no_note(multi_session, classified) -> None:
    """T058 **Finding 14**, derived: the multi-session refusal runs in
    ``classify()`` *before either tier*, so no tier-owned note is reached.

    The declaration is supplied here precisely because "the athlete declared it"
    is the most plausible reason someone would later move that guard -- and the
    refusal is about not being able to say *which span* the beats belong to,
    which a declaration cannot answer."""
    session = classified(
        multi_session(
            {"total_timer_time": 240.0, "total_distance": 150.0, "avg_heart_rate": 92,
             "sport_profile_name": DECLARED_PROFILE},
            {"total_timer_time": 7200.0, "total_distance": 20000.0, "avg_heart_rate": 145},
        ),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert "hrv_multi_session_unclassified" in _provenance(session)
    assert _notes(_provenance(session)) == {}


def test_no_note_is_ever_written_as_a_quality_flag() -> None:
    """The note/flag boundary, asserted over the whole corpus rather than per row.

    Every fixture is driven undeclared -- the posture in which the most notes
    fire -- and no note key may appear in ``quality_flags``, on any of them. This
    is the invariant T066 builds on: a flag is a finding about a *recognised*
    capture, and a note is a record about a file that was refused."""
    for fixture in sorted(path.name for path in FIXTURES.glob("*.fit")):
        session = _classify_fixture(fixture)

        assert not set(session.quality_flags) & set(NOTE_KEYS), fixture


def test_the_corpus_note_distribution_is_what_r5_intends() -> None:
    """The cost R5 accepted, made visible in the suite instead of argued in prose.

    Undeclared, every fixture carrying beats now records a note: the two genuine
    captures with ``vetoed_by`` null, and three ordinary activities with a named
    veto. The five beatless files record nothing. If a later change makes the
    note fire more widely -- on beatless files, say -- this row is what says so,
    with the file names."""
    noted = {}
    for fixture in sorted(path.name for path in FIXTURES.glob("*.fit")):
        note = _provenance(_classify_fixture(fixture)).get(UNDECLARED_NOTE)
        if note is not None:
            noted[fixture] = note["vetoed_by"]

    assert noted == {
        "dev_fields_run.fit": "duration_out_of_range",
        "strap_cool_down_walk.fit": "mean_speed_too_high",
        "strap_hrv_capture.fit": None,
        "strap_hrv_sample_run.fit": None,
        "strap_run_hrv.fit": "duration_out_of_range",
    }


# ---------------------------------------------------------------------------
# 6. the constants themselves -- spelled the way the module's other notes are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("constant_name", "expected"),
    [
        ("_PROVENANCE_UNDECLARED_CANDIDATE", UNDECLARED_NOTE),
        ("_PROVENANCE_DECLARED_CAPTURE_VETOED", DECLARED_VETOED_NOTE),
        ("_PROVENANCE_RESTING_CAPTURE_OVERRIDE", OVERRIDE_NOTE),
    ],
)
def test_each_note_has_a_module_level_constant(constant_name, expected) -> None:
    """The convention the two existing "refusal that is not a finding" keys
    already follow (``_PROVENANCE_SIGNAL_DISAGREEMENT``,
    ``_PROVENANCE_MULTI_SESSION_UNCLASSIFIED``): a module-level constant, a dict
    payload, written through ``_provenance(session)``.

    It is also a hard requirement of the quarantine boundary guard, which
    resolves lookup keys statically and is blinded by a computed one."""
    assert getattr(hrv_classification, constant_name) == expected


def test_the_three_note_keys_are_distinct() -> None:
    """Five keys now share one loosely-typed ``dict[str, Any]``. Two that
    collided would silently overwrite each other, and the reader could not tell
    which situation produced the survivor."""
    assert len(set(NOTE_KEYS)) == len(NOTE_KEYS)
