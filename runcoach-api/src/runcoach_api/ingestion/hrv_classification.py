"""Resting-HRV detection and tier routing (F004).

The policy half of the F004 seam: it reads the decoded FIT messages and
the reconstructed beat stream, decides whether the file is a
resting-HRV *reading* rather than a training session, and writes the
answer onto the ``Session`` -- ``activity_tag``, ``hrv_source_tier``,
``rmssd_precomputed``, ``resting_rmssd_ms`` and the session-level
``rr_source``. It delegates
the statistic itself to ``rmssd.resting_rmssd``; it does no arithmetic
of its own, the same way ``quality_gates.py`` delegates the
retained-beat fraction to ``rr_reconstruction.valid_fraction``.

Contract, matching ``quality_gates.apply(session, records)`` exactly:
mutate ``session`` by reference, return ``None``. Callers do not read a
return value, so a later tier can be added without touching
``pipeline.py`` again.

**The tiers it will route** (F004 reference document §1-§3):

- **Tier 1, ``chest_strap_raw``** -- ``hrv`` (#78) messages carrying
  beat-to-beat ``time`` arrays, *plus the athlete's explicit
  declaration* that the file was meant as a measurement, and no veto
  from the capture's own duration/distance/heart-rate profile. Raw-RR
  presence alone must never be the rule: ``dev_fields_run.fit`` is an
  ordinary run with 7220 beats that such a rule would convert into a
  resting reading. The duration/distance/heart-rate rules were a
  GO-gate deliverable ratified 2026-09-05 (T041) and **demoted to
  vetoes by the 2026-09-06 amendment**: they keep their exact
  thresholds and citations and can no longer authorise a route. See
  ``_classify_tier_1`` below, and ``_declared`` for the declaration
  itself.
- **Tier 2, ``health_snapshot``** -- ``session.rmssd_hrv`` present
  *and* ``raw_sport_value == 60``, read from the provenance
  ``mapping.py`` already records. Both signals matter: capability alone
  would let any firmware attaching ``rmssd_hrv`` to a 90-minute run
  feed in-run wrist PPG into the readiness trend, an absolute §2.2.3 /
  §2.4.5 prohibition (T039, implemented below).
- **Tier 1 outranks Tier 2** on a capture carrying both, because
  §2.4.5 orders the hierarchy highest-fidelity-first: the fully-owned
  computation beats the black-box scalar. The Tier-1 branch therefore
  runs first and short-circuits, and the device's unused ``rmssd_hrv``
  is recorded in provenance under ``unused_device_rmssd_hrv`` rather
  than dropped -- ``rmssd_precomputed`` stays ``None``, because §2.2.3
  reserves it for a device-supplied value on the numeric wrist tiers
  and E003 reads its populated-ness to tell the tiers apart (T045).

T038 fixed only the seam -- the module, its import path, the
``classify`` signature and the unconditional call site in
``pipeline.py``. T039 added the Tier-2 branch and T041 the Tier-1
branch ahead of it; a file matching no branch is still left exactly as
``mapping.py`` produced it, which is today's behaviour unchanged.

T043 added the **quality gates** (reference document §5) on top of the
Tier-1 branch: a capture that is too short, retained too few of its
beats, or recorded none at all is stored with its flags but yields no
reading -- ``hrv_source_tier`` and ``rmssd_precomputed`` both stay
``None``, and never a ``0``/``-1`` sentinel. The gates run *after* the
Tier-1 contract has claimed the file, so a 90-second ordinary run is
never flagged; the beatless row runs after the Tier-2 branch instead,
because a Health Snapshot is a zero-beat resting-shaped file that
Tier 2 reads perfectly well.

**T063 and T069 replaced inference with declaration** (F004's
2026-09-06 amendment, closing ``IDEA-010``). T063 added ``_declared``
alongside the old predicate so every commit stayed green; T069 deleted
the old arm, which is the change that actually closes the idea. The
consequence is observable and is the point: **a capture the athlete
never declared does not route, however restful it looks.** The reason
is recorded in F004's Decision Log and is the project's own history --
Tier 2 has routed on identity (``sport == 60``) since day one and has
needed zero patches, while Tier 1 routed on inference and needed four
(zero distance, tiny distance, multi-session, short easy activity).
The negative class is now "everything not declared", and the
population between accept and reject is empty by construction.

**T065 resolved ``resting_rmssd_ms``, the column E003 actually reads.**
Every successful reading of either tier writes it -- the device value on
Tier 2, the system-computed value on Tier 1 -- and every unsuccessful
outcome leaves it ``None`` and raises a flag. There is therefore no
successful reading for which the column is null, which is how
``IDEA-007``'s null-shaped trap is closed *structurally* rather than by
asking E003 to remember a ``COALESCE``. ``rmssd_precomputed`` keeps its
device-only meaning as the Tier-2 audit record and
``_PROVENANCE_COMPUTED_RMSSD`` is its Tier-1 twin; ``hrv_source_tier``
says which tier produced the resolved number.

The column carries one invariant, and both tiers enforce it at their own
success point: **``resting_rmssd_ms`` is always strictly positive when
set.** Tier 2 has always refused a non-positive device value to close the
``ln(0)`` hazard in E003's ``ln(rMSSD)`` trend, and T065 extended the same
gate to the Tier-1 computed value -- amending T042, which had argued the
opposite for a value that then lived only in provenance. See
``_classify_tier_1`` for that argument in full. The invariant is what lets
a consumer take ``ln(resting_rmssd_ms)`` unguarded without first asking
which tier produced it, which is the entire point of a resolved column.

**T064 made the refusals observable** (F004; resolution **R5**, ratified
2026-09-06). Declaration closed ``IDEA-010`` and made this feature's worst
property worse: **the false-negative direction is invisible by
construction** -- a missing reading is indistinguishable from a day the
athlete did not measure, and an unconfigured profile refuses everything.
Every Tier-1 refusal on a file that carries beats now writes a
**provenance note** saying what the file claimed and what refused it:
``hrv_undeclared_capture_candidate`` when the profile was not configured,
``hrv_declared_capture_vetoed`` when a configured name was contradicted by
the file's own duration/heart-rate/distance profile, and
``hrv_resting_capture_override`` for what a per-upload override did. R5
settled two things the first specification left open (T058's Findings 3 and
4): the note fires **even when a veto fired**, and it **names which veto** --
without which ``strap_cool_down_walk.fit``, undeclared *and* vetoed, ingested
with no route, no flag and no record at all.

**A note is not a flag, and that distinction is load-bearing.** A
``hrv_capture_*`` flag asserts a finding about a *recognised* capture; a
refused file was never recognised as one. See the ``_PROVENANCE_*`` block
below, which states the same rule the multi-session refusal and
``_classify_tier_2``'s row 2 already follow.

**T066 applied that distinction to the last place still breaking it, and
implemented resolution R4** (T058 **Finding 17 / row D5**, the highest-severity
row of the declaration contract). Two halves:

- ``_gate_a_beatless_resting_capture`` is **declaration-gated**. It used to fire
  on any beatless, resting-*shaped*, unclaimed file with no reference to the
  declaration, so a file the system had explicitly not recognised carried a flag
  asserting a finding about a recognised one -- the note/flag boundary above,
  crossed from the other side.
- The Tier-1 beats gate reads ``hrv`` **messages** rather than the reconstructed
  stream. F004's normative block says "beats present" while the reference
  document says "``rr_intervals`` is non-empty", and the two differ on exactly
  one input: a file whose ``hrv`` messages reconstruct to zero beats. R4 ratified
  the feature file's wording, which keeps §5's ``hrv_capture_no_beats`` row
  reachable *on the Tier-1 path* instead of leaving it a false promise. See
  ``_hrv_messages_present`` for the argument and for R4's revisit condition.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

import fitdecode

from runcoach_api.ingestion import rmssd, rr_reconstruction
from runcoach_api.models import Context, RRInterval, Session

# The empirically observed Garmin Health Snapshot ``sport`` value.
# Unconfirmed in Garmin's published enum and left unresolved by
# fitdecode's profile, so it is cited to the fixtures that carry it
# (``sample_health_snapshot.fit``, ``strap_health_snapshot.fit``) --
# the same evidentiary treatment ``_BOUNDARY_RATIO_MULTIPLES`` got.
_SNAPSHOT_RAW_SPORT_VALUE = 60

_ACTIVITY_TAG_HEALTH_SNAPSHOT = "health_snapshot"
_TIER_HEALTH_SNAPSHOT = "health_snapshot"
_RR_SOURCE_HEALTH_SNAPSHOT = "health_snapshot_ppg"

_ACTIVITY_TAG_RESTING_HRV_CHECK = "resting_hrv_check"
_TIER_CHEST_STRAP_RAW = "chest_strap_raw"
_RR_SOURCE_CHEST_STRAP = "chest_strap_ecg"

# --- the Tier-1 veto set (ratified 2026-09-05, F004 reference doc §2) --------
#
# **Demoted 2026-09-06, unchanged in value.** These three were the ratified
# Tier-1 *discriminator*; the declaration amendment (T063/T069) took away their
# vote and left them their veto, so each keeps its exact threshold and citation
# and none of them can any longer cause a reading to exist. Their thresholds
# were fixed against the file the 2026-09-05 GO/NO-GO gate recorded,
# ``strap_hrv_sample_run.fit``, measured with fitdecode: 149 ``hrv`` (#78)
# messages / 156 beats, ``total_timer_time`` 150.797 s, ``total_distance``
# 108.21 m (mean 0.718 m/s), ``sport`` running (raw 1), ``sub_sport`` generic,
# ``avg_heart_rate`` 60, no readable ``rmssd_hrv``. That file is now the
# corpus's *undeclared negative* -- it passes all three of these and does not
# route -- which is what makes the declaration observable; the declared
# positive is ``strap_hrv_capture.fit``. The measurements below still describe
# what the thresholds were calibrated against and are kept for that reason.

# CITED. The upper bound of §2.4.5's "2-5 minute resting measurement"
# protocol -- a citation, not an invented number. Deliberately not widened
# to 600 s "for margin": any value between 150.8 s and 3117 s separates the
# gate fixture from ``dev_fields_run.fit``, so only the citable one may be
# chosen.
_RESTING_MAX_DURATION_S = 300.0

# SPEC-INTRODUCED IMPLEMENTATION DEFAULT -- no direct citation exists, so it
# carries the same flag ``_UNIFORM_1HZ_MIN_FRACTION`` and
# ``_BOUNDARY_RATIO_MULTIPLES`` carry, plus a Decision Log entry citing the
# fixture. Below walking pace: the gate fixture's 108 m over 150.797 s is
# 0.718 m/s of GPS drift, not travel.
_RESTING_MAX_MEAN_SPEED_MS = 1.0

# SPEC-INTRODUCED IMPLEMENTATION DEFAULT, same treatment. Chosen for
# **separation, not precision**: the gate fixture is avg HR 60 and any short
# hard effort is 150+, which leaves generous headroom for a stressed or unwell
# resting morning.
#
# Amended 2026-09-06 (critic pass): this used to be described as "the GPS-less
# fallback". It is not a fallback -- it was the *only* corroborating signal the
# discriminator had, and it is required on every Tier-1 route. Since T069 what it
# corroborates is a **declaration** rather than a guess: an athlete who declared
# the file and then recorded a 140 bpm effort on it is refused here. See
# ``_resting_profile_duration``.
_RESTING_MAX_AVG_HEART_RATE_BPM = 100

# --- the Tier-1 quality gates (T043, F004 reference document §5) -------------
#
# A capture that fails any of these is stored as a session with its quality
# flags, but yields no resting-HRV reading: ``hrv_source_tier`` and
# ``rmssd_precomputed`` are both left ``None``. The upload always succeeds --
# F003's posture is to flag quality, never to refuse a valid FIT file, and the
# Decision Log records "store the session, derive no reading" explicitly.

# CITED. The *lower* bound of §2.4.5's "2-5 minute resting measurement"
# protocol -- the same sentence ``_RESTING_MAX_DURATION_S`` takes its 300 s
# from. Inclusive: a capture *of* two minutes is inside the protocol, only one
# shorter than 120 s falls outside it.
_RESTING_MIN_DURATION_S = 120.0

# CITED. §2.4.3's default rejects a sample retaining under 80% of its beats
# (spec §3 for the raw-RR tier). Inclusive at the bound: exactly 0.80 retained
# is not "below 0.80".
_RESTING_MIN_VALID_FRACTION = 0.80

_FLAG_CAPTURE_TOO_SHORT = "hrv_capture_too_short"
_FLAG_CAPTURE_LOW_QUALITY = "hrv_capture_low_quality"
_FLAG_CAPTURE_NO_BEATS = "hrv_capture_no_beats"

# Where the Tier-1 reading itself is recorded. ``rmssd_precomputed`` cannot
# carry it -- §2.2.3 reserves that field for a *device-supplied* scalar on the
# numeric wrist tiers, and overloading it would leave E003 unable to tell which
# tier produced the number. F004's data model defines no session column for the
# system-computed value, so the audit dict is its only home today; E003 will
# need a real column before it can read the Tier-1 statistic back out of the
# store. Deliberately distinct from T045's ``unused_device_rmssd_hrv``, which
# records the opposite thing: a device value that *lost* to this computation.
_PROVENANCE_COMPUTED_RMSSD = "computed_resting_rmssd_ms"

# The device-supplied scalar that *lost* to the Tier-1 computation above
# (T045, F004 reference document §3). §2.4.5 orders the hierarchy
# highest-fidelity-first, so a capture carrying both raw beats and a session
# ``rmssd_hrv`` resolves to the fully-owned computation -- but the device's
# number is not thereby thrown away: it is recorded here so the audit trail
# says what was ignored and why the two numbers might differ.
#
# Deliberately distinct from ``_PROVENANCE_COMPUTED_RMSSD`` above and from
# ``_PROVENANCE_SIGNAL_DISAGREEMENT`` below. All three land in the same
# ``dict[str, Any]`` and mean different things -- this system's reading, a
# rejected device scalar, and a device scalar on a file that is not a snapshot
# at all -- so collapsing any two onto one key would make them
# indistinguishable to E003, and would silently overwrite the reading with the
# value that lost to it.
_PROVENANCE_UNUSED_DEVICE_RMSSD = "unused_device_rmssd_hrv"

# Bare string literal on ``session.quality_flags``, matching
# ``quality_gates.py``'s "smart_recording" convention: no enum, no
# registry, no flags table.
_FLAG_READING_UNAVAILABLE = "hrv_reading_unavailable"

# Provenance key for row 2. Deliberately distinct from T045's
# ``unused_device_rmssd_hrv``, which records a device value that lost to
# a Tier-1 computation -- a different situation that must stay
# distinguishable in the same loosely typed dict.
_PROVENANCE_SIGNAL_DISAGREEMENT = "hrv_signal_disagreement"

# Provenance key for the multi-session refusal (2026-09-06 critic pass). A
# *provenance* entry and deliberately not a quality flag: see ``classify`` for
# the argument, and ``_PROVENANCE_SIGNAL_DISAGREEMENT`` above for the
# precedent -- F004's existing channel for "the classifier saw something and
# declined to act on it" is the audit trail, not the flag list.
_PROVENANCE_MULTI_SESSION_UNCLASSIFIED = "hrv_multi_session_unclassified"

# --- T064: what a refused file records (F004; resolution R5) -----------------
#
# The three keys below are the third, fourth and fifth instances of the same
# convention ``_PROVENANCE_SIGNAL_DISAGREEMENT`` and
# ``_PROVENANCE_MULTI_SESSION_UNCLASSIFIED`` established: **a refusal that is
# not a finding goes to provenance, never to a quality flag.** A
# ``hrv_capture_*`` flag asserts a finding about a *recognised* capture; every
# file these keys describe was refused before it could be recognised as one, so
# a flag would be a false statement about it -- and it would fire on every
# ordinary run carrying ``hrv`` messages, training the reader to ignore it.
# ``_gate_a_beatless_resting_capture`` and ``_apply_quality_gates`` own the
# other side of that line: they speak about captures Tier 1 *claimed*.
#
# They exist because the declaration amendment made F004's worst property worse.
# The false-negative direction is invisible by construction -- a missing reading
# is indistinguishable from a day the athlete did not measure -- and requiring a
# declaration makes that direction *more* likely, because an unconfigured
# profile refuses everything. These notes convert "no reading, no explanation"
# into "no reading, and here is what the file claimed and what refused it".

# The undeclared refusal: a file with beats whose profile the athlete never
# configured. Payload ``{"sport_profile_name": <as read>, "vetoed_by": <veto or
# None>}``.
#
# Distinct from ``_PROVENANCE_DECLARED_CAPTURE_VETOED`` below, which is the
# opposite athlete: one who *did* the setup step. The two lead to different
# actions -- "configure this name" versus "this file contradicted your
# declaration" -- so collapsing them onto one key would make the athlete who
# configured their profile indistinguishable from the one who did not.
#
# **The recorded name is diagnostic, never a recommendation.** On this corpus
# every non-snapshot file reads ``'Run'``, and adding *that* to config restores
# the pre-amendment behaviour exactly: every short easy activity on the running
# profile routes again, caught only by the demoted vetoes, which is the design
# ``IDEA-010`` exists to kill. **A general-purpose activity profile must never
# be listed.** The note reports what the file claimed; it never asserts the
# claim is true or safe to trust.
#
# **The note is prospective and cannot rescue the session that produced it.**
# ``db.py``'s ``UNIQUE (source_device, start_time)`` answers a re-upload with a
# 409 and adding a name to config reclassifies nothing already stored. Acting on
# it means *configure the profile, then delete the session (T072) and upload
# again, or capture again* -- never *re-upload over the top*.
_PROVENANCE_UNDECLARED_CANDIDATE = "hrv_undeclared_capture_candidate"

# The declared refusal: a configured profile name, killed by a veto. Payload
# ``{"sport_profile_name": <as read>, "vetoed_by": <veto>}``.
#
# **Added by R5** (T058 Finding 3). Before it, the athlete who did declare -- who
# did the setup step the whole amendment asks of them -- got no route, no flag
# and no note, and was silently discarded. The observability argument that
# justifies the undeclared note applies here with *more* force, not less.
#
# ``vetoed_by`` is never ``None`` on this key: a declared file that passes every
# veto routes, so there is no refusal to record.
_PROVENANCE_DECLARED_CAPTURE_VETOED = "hrv_declared_capture_vetoed"

# What the per-upload override did. Payload ``{"honoured": True}``, or
# ``{"honoured": False, "reason": <a veto name, or _OVERRIDE_NOT_HONOURED_NO_BEATS>}``.
#
# **One key rather than three**, though F004 names three override notes: it
# keeps the "distinguishable in one dict" convention manageable at five keys and
# makes the honoured and refused cases queryable together. It is separate from
# the two above because the override is a *per-upload act* the athlete performed
# on one specific file, and its answer belongs to that act rather than to the
# file's profile name -- which the override route need not even have.
#
# ``honoured`` is a statement about the **declaration**, not about the reading:
# ``True`` means the override was accepted and Tier 1 claimed the file. Whether
# a number came out of it is ``resting_rmssd_ms``'s business, and a capture that
# was claimed and then failed a §5 gate is a *recognised* capture answered with a
# flag -- the note/flag split this whole block rests on.
_PROVENANCE_RESTING_CAPTURE_OVERRIDE = "hrv_resting_capture_override"

# The one non-veto reason an override can go unhonoured: the beats gate, which
# precedes the declaration and is normatively ordered that way. F004 states it
# as its own scenario -- "an override on a file with no beats reports that it did
# nothing" -- because a beatless file must fall through to Tier 2, so the
# override genuinely cannot be honoured and saying so is the entire content.
_OVERRIDE_NOT_HONOURED_NO_BEATS = "no_beats"

# --- the veto vocabulary (T064; R5's "must name which veto") -----------------
#
# R5 requires the note to name **which** veto fired: a note that does not say
# why does not discharge the goal, because "configure your profile" and "this
# file was 6000 seconds long" are different diagnoses and the athlete cannot act
# on the wrong one. These are the names, one per branch of
# ``_resting_profile``, which is the only place they are produced.
#
# They are the *reasons*, not the rules: the rules, their thresholds and their
# citations live on ``_resting_profile_duration`` and are unchanged. Splitting
# "absent" from "out of range" (and from "too high") is deliberate -- a file with
# no ``avg_heart_rate`` at all and one reading 140 bpm are refused by the same
# clause and are entirely different situations to the person reading the note.
#
# Named after the *canonical summary key* rather than the FIT field, because the
# note is read alongside ``session.summary``, which is where a reader will go to
# check it.
_VETO_DURATION_UNREADABLE = "duration_unreadable"
_VETO_DISTANCE_UNREADABLE = "distance_unreadable"
_VETO_HEART_RATE_UNREADABLE = "heart_rate_unreadable"
_VETO_DURATION_ABSENT = "duration_absent"
_VETO_DURATION_OUT_OF_RANGE = "duration_out_of_range"
_VETO_HEART_RATE_ABSENT = "heart_rate_absent"
_VETO_HEART_RATE_TOO_HIGH = "heart_rate_too_high"
_VETO_MEAN_SPEED_TOO_HIGH = "mean_speed_too_high"

#: Every reason a note's ``vetoed_by`` / ``reason`` can name, declared once so
#: the suite can reconcile it in **both** directions -- a name the module can
#: emit that no probe produces is an unexercised branch, and a name in this set
#: that nothing can reach is a false promise of the kind ``IDEA-013`` names.
#: Same discipline as ``HRV_INPUT_FIELDS``, and for the same reason: a set
#: phrased over code needs an independent list to be reconciled against.
HRV_VETO_REASONS: frozenset[str] = frozenset(
    {
        _VETO_DURATION_UNREADABLE,
        _VETO_DISTANCE_UNREADABLE,
        _VETO_HEART_RATE_UNREADABLE,
        _VETO_DURATION_ABSENT,
        _VETO_DURATION_OUT_OF_RANGE,
        _VETO_HEART_RATE_ABSENT,
        _VETO_HEART_RATE_TOO_HIGH,
        _VETO_MEAN_SPEED_TOO_HIGH,
    }
)

# --- the quarantine boundary, made mechanically checkable (T046) -------------
#
# Every name this module reads a value under, split into the two things such a
# name can be. F004 asserts the quarantine boundary as a **negative
# invariant** -- no field registered in ``quarantine._VENDOR_DERIVED_FIELDS``
# may ever populate ``rmssd_precomputed`` or ``hrv_source_tier`` -- and an
# invariant phrased over code rather than over data needs a set to be phrased
# over. Spec §2.2.3 draws the line these two constants sit on: Garmin's
# overnight HRV *Status classification* is a vendor-derived composite and is
# quarantined; the numeric rMSSD underneath it is a standard statistic and is
# admissible. The label is a black box, the number is not.
#
# ``test_hrv_quarantine_boundary.py`` reconciles both constants against this
# module's *actual* field reads, recovered from its AST -- so a later task that
# adds a read without declaring it fails that test rather than silently
# narrowing the invariant to something that proves nothing. Adding a read means
# adding its name to exactly one of these two sets.

#: The values this module reads **as HRV inputs**: names whose value can decide
#: whether a resting-HRV reading is produced, or what it is. This is the set
#: the quarantine registry must stay disjoint from.
#:
#: - ``rmssd_hrv`` -- the Tier-2 capability signal, off the ``session``
#:   message. The admissible numeric statistic, never the black-box label.
#: - ``raw_sport_value`` -- the Tier-2 identity signal, read from the
#:   provenance ``mapping.py`` already records rather than re-parsed.
#: - ``duration_s`` / ``distance_m`` / ``avg_heart_rate`` -- the three
#:   ``session.summary`` keys of the Tier-1 resting discriminator (reference
#:   document §2).
#: - ``rr_valid_fraction`` -- the artefact-survival quality gate (§5).
#: - ``sport_profile_name`` -- the Tier-1 **declaration** signal (T063), read
#:   from the provenance ``mapping.py`` lifts it into (T056), never re-parsed
#:   and never read through ``has_field()``. It is an *input* rather than
#:   plumbing because it decides whether a Tier-1 reading is produced at all,
#:   which is exactly the property this set is defined over. It is deliberately
#:   **not** in ``HRV_NON_INPUT_READS``: that set's own docstring calls itself
#:   "not an escape hatch", and only membership here puts the name inside the
#:   disjointness invariant.
HRV_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "rmssd_hrv",
        "raw_sport_value",
        "duration_s",
        "distance_m",
        "avg_heart_rate",
        "rr_valid_fraction",
        "sport_profile_name",
    }
)

#: Names this module reads that are **not** HRV inputs: plumbing on the
#: canonical ``Session`` carrying no measurement of its own. ``summary`` and
#: ``context`` are the containers whose keys ``HRV_INPUT_FIELDS`` enumerates
#: above; ``quality_flags`` and ``activity_tag`` are this module's own output,
#: read back only to dedup a flag and to see whether an earlier tier already
#: claimed the file.
#:
#: Declared rather than merely omitted, so the AST reconciliation has a bucket
#: for every read. It is **not** an escape hatch for a quarantined name: the
#: boundary test asserts the quarantine registry is disjoint from every name
#: this module reads, both sets together.
HRV_NON_INPUT_READS: frozenset[str] = frozenset(
    {
        "summary",
        "context",
        "quality_flags",
        "activity_tag",
    }
)


def _session_rmssd_hrv(messages: list[fitdecode.FitDataMessage]) -> Any:
    """The capability signal: ``rmssd_hrv`` off the ``session`` message.

    Read straight from the decoded messages -- the way
    ``rr_reconstruction.py`` and ``quarantine.py`` already take
    ``messages`` as input -- rather than by extending
    ``mapping._build_summary``, which keeps ``mapping.py`` untouched.

    The name is ``rmssd_hrv``, what fitdecode actually surfaces. It is
    **not** ``RmssdAvgValue``, the Connect/SDK profile name §2.4.5 uses:
    building against that name yields a green test over a dead code
    path, the failure F003's fabricated-enum ``event``-carrier bug is
    the cited precedent for.

    Returned as-is, without coercion: the value is an ``int`` on both
    snapshot fixtures (37 and 51, not 37.0/51.0).
    """
    for message in messages:
        if message.name == "session":
            value = message.get_value("rmssd_hrv", fallback=None)
            if value is not None:
                return value
    return None


def _session_message_count(messages: list[fitdecode.FitDataMessage]) -> int:
    """How many ``session`` messages the decoded file carries.

    Counted off the decoded messages rather than added to ``mapping.py``: the
    ambiguity below is F004's problem, not F003's, and ``mapping.to_canonical``
    is untouched by this guard exactly as it is by the rest of this module.
    """
    return sum(1 for message in messages if message.name == "session")


def _hrv_messages_present(messages: list[fitdecode.FitDataMessage]) -> bool:
    """Whether the file carries any ``hrv`` (#78) message at all.

    **This is F004's "beats present", and resolution R4 is why it is spelled over
    *messages* rather than over the reconstructed stream** (T058 **Finding 17**,
    the highest-severity row of the declaration contract). The two normative
    blocks word the Tier-1 beats gate differently -- the feature file says
    ``beats present``, the reference document says ``rr_intervals is
    non-empty`` -- and they differ on exactly one input: a file carrying ``hrv``
    messages that reconstruct to **zero** beats, because every ``time`` slot was
    an invalid sentinel or every candidate was dropped.

    R4 ratified the feature file's wording, on the authority rule and on the
    amendment's own justification, which argues in terms of ``hrv`` *messages*
    ("real snapshots carry **zero** ``hrv`` messages"). Two consequences, both
    intended and both pinned in ``test_resting_hrv_quality_gates.py``:

    1. §5's ``hrv_capture_no_beats`` gate is **reachable on the Tier-1 path**.
       Under the other reading ``_apply_quality_gates``' third row could never
       fire there -- with a non-empty ``rr_intervals`` in hand
       ``_surviving_fraction`` always answers a float -- so the row would be a
       false promise and would have to be deleted.
    2. Such a file **claims Tier 1 and does not fall through to Tier 2**. The
       fall-through concern was weighed and judged theoretical: T057 decoded
       Health Snapshots as carrying zero ``hrv`` messages, so they never reach
       this branch, and a degenerate strap capture has no ``rmssd_hrv`` and
       nothing to gain from Tier 2. **R4 carries a revisit condition** -- a
       fixture with ``hrv`` messages *and* a usable ``rmssd_hrv`` breaks the
       reasoning. T066 re-measured all ten corpus fixtures against it: the five
       ``rmssd_hrv`` carriers each hold **zero** ``hrv`` messages, so the
       condition does not fire today.

    ``rr_intervals`` is still consulted alongside this in ``_classify_tier_1``,
    and the disjunction is not a hedge. On a real file a non-empty reconstructed
    stream *implies* ``hrv`` messages, so the two agree; ``classify()`` is also a
    public function that F004's own suites drive directly with a hand-built beat
    stream and no ``hrv`` messages behind it, and refusing those would be
    refusing a beat stream the caller is holding. Beats in hand, or the messages
    that were meant to produce them, are both "beats present".

    Counted off the decoded messages exactly as ``_session_message_count`` counts
    ``session`` ones -- ``mapping.to_canonical`` is untouched by this module.
    """
    return any(message.name == "hrv" for message in messages)


def _numeric(value: Any) -> float | int | None:
    """``value`` when it is a real number, otherwise ``None``.

    ``fitdecode`` types a field by the **file's own declared base type**, not by
    the global profile: ``reader.py`` returns
    ``tuple(base_type.parse(v) for v in raw_value)`` whenever the declared size
    holds more than one element, so a crafted or corrupt definition record can
    hand this module a ``tuple`` (or a ``str``, from a string base type) where a
    scalar was expected. Comparing one -- ``(1, 2) <= 0`` -- raises ``TypeError``,
    and ``main.py`` catches only ``NotAFitFileError``, ``FitParseFailure``,
    ``TooManyRecordsError``, ``MissingCanonicalFieldError`` and
    ``DuplicateSessionError``, so it would reach the client as a **500 on a
    malformed upload** rather than a stored session with flags.

    ``rr_reconstruction._hrv_candidates`` already guards the identical hazard the
    same way; this is that convention, applied at the two places this module
    compares a decoded value.

    ``bool`` is excluded explicitly: it is an ``int`` subclass, so ``True <= 100``
    is legal and an unguarded numeric check would read a flag as a heart rate of
    1 bpm or store ``True`` as a millisecond rMSSD.

    A non-numeric value is answered ``None`` -- **treated as absent** -- rather
    than raising: F003's posture is to flag quality, never to refuse a valid FIT
    file, and an uninterpretable field is exactly a field the file did not supply.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _provenance(session: Session) -> dict[str, Any]:
    """``session.context.provenance``, creating the ``Context`` if absent.

    ``mapping.to_canonical`` always builds one, but ``classify`` is also
    reachable with a directly constructed ``Session``; dropping the
    audit trail silently in that case is exactly what row 2 exists to
    prevent.
    """
    if session.context is None:
        session.context = Context()
    return session.context.provenance


def _raise_flag(session: Session, flag: str) -> None:
    if flag not in session.quality_flags:
        session.quality_flags.append(flag)


def classify(
    messages: list[fitdecode.FitDataMessage],
    session: Session,
    rr_intervals: list[RRInterval],
    *,
    profile_names: Sequence[str] | None = None,
    resting_capture_override: bool = False,
) -> None:
    """Route one ingested file to a resting-HRV tier, or leave it alone.

    Mutates ``session`` in place and returns ``None`` -- the
    ``quality_gates.apply`` contract.

    **The athlete's declaration arrives as parameters, never by importing
    config** (T063, F004's stated implementation seam). ``profile_names`` is
    the configured ``resting_hrv_profile_names`` list and
    ``resting_capture_override`` is the per-upload flag; ``pipeline.py`` reads
    the first from ``db._load_config_cached()`` and threads the second down
    from ``POST /sessions``. Keeping the seam here is what lets the
    ``test_resting_hrv_*`` suites drive this function directly, and it is why
    this module has no config import to go stale.

    Both are keyword-only and both default to "nothing is declared", so a
    caller that predates the declaration keeps its exact previous meaning
    rather than acquiring a route by accident.

    Must be called for **every** file, including ones with zero beats:
    a Garmin Health Snapshot is precisely a zero-beat file, so the whole
    Tier-2 path lives behind an unconditional call. ``rr_intervals`` is
    ``[]`` for those, not a signal to skip.

    Tier 1 (``chest_strap_raw``) runs **first**, and short-circuits when
    it routes: §2.4.5 orders the hierarchy highest-fidelity-first, so a
    capture carrying both raw beats and a device ``rmssd_hrv`` resolves
    to the fully-owned computation rather than to the black-box scalar.

    **That precedence is a contract, not an accident of statement
    order.** The ``return`` below is what implements it, and a refactor
    that reordered these two lines would silently invert the hierarchy;
    ``test_resting_hrv_tier_precedence.py`` pins the outcome so it
    cannot. It also pins the second half of the rule: the device value
    that lost is written to provenance by ``_classify_tier_1``, never
    discarded and never smuggled into ``rmssd_precomputed``. No corpus
    fixture carries both signals -- the strap captures have beats and no
    scalar, the snapshots a scalar and zero ``hrv`` messages -- so that
    suite is necessarily synthetic and says so.

    **A file carrying more than one ``session`` message is refused before
    either tier** (2026-09-06, sprint-003 critic pass; reference document
    §2.1). ``mapping.to_canonical`` builds the canonical ``Session`` from
    ``_first_of(by_name, "session")`` -- the *first* session message only --
    while ``rr_reconstruction.reconstruct`` walks **every** ``hrv`` message in
    the file. On a multi-session file the summary this module discriminates on
    and the beat stream it would compute from therefore describe different
    spans, and the reproduction is not subtle: a 240 s / 150 m / 92 bpm leg
    followed by a two-hour run, 7000 beats file-wide, routed as an *unflagged*
    ``resting_hrv_check`` / ``chest_strap_raw`` reading whose rMSSD came from
    in-run beats -- and the tag then excluded that two-hour run from training
    load as well.

    ``_first_of`` is F003 code and is unchanged; what F004 changed is its
    status, from a summary-*fidelity* choice into a
    classification-*correctness* dependency. F004 therefore owns the
    consequence, and answers it the only way a review fix may: **it refuses to
    route a file it cannot confidently classify.** No segmentation, no
    per-session beat attribution, no "pick the best leg" heuristic -- that is a
    feature, and it belongs in its own task.

    **Both tiers refuse, not only Tier 1.** Tier 2's hazard is different in
    kind and equally real. Its two signals come from two different places:
    ``raw_sport_value`` off the provenance ``mapping.py`` wrote for the *first*
    session message, and ``rmssd_hrv`` off the first session message that
    happens to *carry* one -- ``_session_rmssd_hrv`` skips those that do not.
    With more than one session those need not be the same message, so a
    sport-60 snapshot leg can supply the identity while a 90-minute run leg
    supplies the number, the two "agree", and in-run wrist-PPG rMSSD is stored
    as a resting reading. That is exactly the §2.2.3 prohibition row 2's
    identity guard exists to make impossible, reached by crossing two messages
    rather than by disagreeing on one. Two signals only corroborate when they
    come off the same session, and nothing here establishes that they do.

    **Recorded in provenance, and deliberately not as a quality flag.** The
    refusal must be visible rather than silent, but a ``hrv_capture_*`` flag
    asserts a finding *about a resting capture*, and a multi-session file has
    not been established to be one -- that is the whole reason it is refused.
    Multi-session FIT files are also entirely ordinary: every multisport and
    multi-leg activity is one, so flagging them would put a permanent HRV
    quality flag on every triathlon upload and train the reader to ignore the
    flag. ``_classify_tier_2``'s row 2 already settled this same question the
    same way, in the same module, for the same reason ("there is no Tier-2
    reading for ``hrv_reading_unavailable`` to be about"): the audit trail is
    where F004 records what it saw and declined to act on. The count goes in
    because it is the entire reason for the refusal and is **not** otherwise
    recoverable from the stored session -- the canonical summary is the first
    leg's and says nothing about a second leg existing.

    **No T064 note is written on this path**, and that is a consequence of the
    ordering rather than a second decision (T058 **Finding 14**, derived): the
    refusal precedes both tiers, and the three notes describe a *Tier-1*
    refusal -- what the file claimed, and which veto contradicted it. Neither
    question has an answer here, because the summary and the beat stream
    describe different spans, which is the whole reason the file is refused. The
    multi-session entry above is already the record of what happened, and a
    second note asserting "undeclared" would attribute the refusal to the wrong
    cause.
    """
    session_message_count = _session_message_count(messages)
    if session_message_count > 1:
        _provenance(session)[_PROVENANCE_MULTI_SESSION_UNCLASSIFIED] = {
            "session_message_count": session_message_count
        }
        return None

    if _classify_tier_1(
        messages,
        session,
        rr_intervals,
        profile_names=profile_names,
        resting_capture_override=resting_capture_override,
    ):
        return None
    _classify_tier_2(messages, session)
    _gate_a_beatless_resting_capture(
        session,
        rr_intervals,
        profile_names=profile_names,
        resting_capture_override=resting_capture_override,
    )
    return None


# The three sentinels the reading convention needs.
#
# ``_MISSING`` distinguishes "the summary has no such key" from "the summary
# holds a falsy value": a bare ``summary.get(key)`` cannot, because ``0`` and a
# missing key both answer falsy, and telling those two apart is the whole
# subject of the convention on ``_intensity_signal``.
#
# ``_ABSENT`` and ``_VETO`` are the two answers that are not a value. They are
# distinct objects rather than ``None`` for the same reason: ``None`` is one of
# the states being told apart, not a way to report them.
_MISSING = object()
_ABSENT = object()
_VETO = object()


def _intensity_signal(raw: Any) -> Any:
    """One summary field, normalised by F004's stated ``session.summary``
    reading convention (feature file Data Model; reference document
    "Reading ``session.summary``"; resolutions R1 and R2)::

        _MISSING -- i.e. no such key      ->  _ABSENT   (rule 1)
        present, non-numeric              ->  _VETO     (rule 2, unparseable)
        present, numeric, exactly zero    ->  _ABSENT   (rule 1, degenerate)
        present, numeric, negative        ->  _VETO     (rule 2, impossible)
        present, numeric, positive        ->  the value

    **Stated once and applied to all three fields**, which is the entire point:
    this is the third instance of one shape -- a present-but-uninformative value
    read as an absence -- and answering it per field as each residue surfaced is
    what guaranteed the third. ``IDEA-009``.

    **Rule 1, absence.** ``mapping._build_summary`` strips its ``None`` values,
    so a field the device did not write has **no key at all** -- and, crucially,
    so does a field the device *declared* while writing the FIT **invalid
    sentinel**, because ``get_value(name, fallback=None)`` answers ``None`` for
    it. T057 measured that state across the whole corpus: it holds for
    ``rmssd_hrv`` on every fixture including the Tier-1 positive, and for
    ``total_distance`` on ``strap_health_snapshot_hrv.fit``. The convention is
    therefore scoped to the **dict**, whose key set that stripping defines: the
    caller reads ``summary.get("<name>", _MISSING)`` and never
    ``msg.has_field("<name>")``.

    **It takes the already-read value rather than the dict and a key**, so that
    every lookup key in this module stays a string *literal*. The quarantine
    boundary guard (``test_hrv_quarantine_boundary``) walks this file's AST and
    refuses any lookup whose key it cannot resolve statically -- a
    ``summary.get(key, ...)`` over a parameter would make the whole
    ``HRV_INPUT_FIELDS`` reconciliation blind, which is a strictly worse
    outcome than three call sites naming their own fields.

    That scoping is load-bearing rather than stylistic (resolution **R1**). A
    ``has_field()`` reading turns the sentinel into "a value that exists and
    cannot be read", i.e. rule 2's veto, and the two readings then disagree
    **materially** for ``distance_m``: absent lets the heart rate decide and the
    capture routes, while a veto refuses it. For ``duration_s`` and
    ``avg_heart_rate`` both readings refuse, so the divergence is invisible --
    which is precisely why it would survive a green suite.

    **Rule 1, degeneracy.** A zero is a device writing that it had nothing to
    report, never a measurement of zero. Ratified first for ``distance_m`` (a
    present-and-zero distance was standing in for the heart-rate arm on every
    indoor capture) and extended here to ``avg_heart_rate`` as a consequence of
    stating the rule, rather than as a guard invented for a case no Garmin
    device produces -- an absent heart rate encodes as the uint8 invalid
    sentinel, not as ``0``.

    ``-0.0`` is **exactly zero**, so it is an absence and not a veto (T058
    Finding 11, decided rather than left to fall out). IEEE-754 -- which the FIT
    float encodings are -- makes ``-0.0 == 0`` true and ``-0.0 < 0`` false, so
    the language's own comparisons already answer it; and the provenance
    argument that makes a negative value impossible does not reach a signed
    zero, which is what arithmetic on a scaled zero produces and carries exactly
    as much information as ``0.0``: none. The zero test is written first, above
    the sign test, so that this is visible in the code and not an accident of
    operator semantics.

    **Rule 2, the veto.** A value that exists and cannot be read -- or that
    reads cleanly but could not have been written by a working device -- is
    evidence something is wrong with the file. It must never be silently
    downgraded into the "signal missing" branch, because another arm can then
    satisfy the predicate alone. Two forms, and they are the same finding:

    * **Unparseable.** ``fitdecode`` types a field by the file's own declared
      base type, so a crafted or corrupt definition record hands this module a
      ``tuple`` or a ``str`` where a scalar was expected. ``bool`` is in this
      class too, and deliberately: it is an ``int`` subclass, so ``True <= 100``
      is legal and an unguarded numeric check would read a flag as a heart rate
      of 1 bpm.
    * **Structurally impossible.** All three of ``total_distance``,
      ``total_timer_time`` and ``avg_heart_rate`` are **unsigned** FIT fields,
      so a negative value can only come from a crafted or corrupt definition
      record -- the *identical* provenance ``_numeric`` reports for a ``tuple``.
      The convention's first draft ignored one and vetoed the other; treating
      them differently is the per-value inconsistency it exists to end. This
      **changed ratified behaviour** for ``total_distance`` (F004 Decision Log,
      2026-09-06) and settles ``avg_heart_rate``, which both normative veto
      blocks accept as written (resolution **R2**, T058 Finding 2).

    Reading a negative distance *literally* was never the alternative and is
    worth restating, because it is why the value cannot simply be compared:
    ``-500 / 240`` clears the 1.0 m/s bound comfortably, so a garbage field
    would read as a **satisfied** stillness test.

    **Scope.** ``_resting_profile_duration`` only. ``_numeric`` itself is
    unchanged and shared with ``_classify_tier_2``, where an unparseable
    ``rmssd_hrv`` genuinely does mean "no usable device value" -- the presence
    discrimination lives *around* ``_numeric``, never inside it.
    """
    if raw is _MISSING:
        return _ABSENT

    value = _numeric(raw)
    if value is None:
        return _VETO
    if value == 0:
        return _ABSENT
    if value < 0:
        return _VETO
    return value


class _RestingProfile(NamedTuple):
    """What the veto set answered about one file: a duration, or a reason.

    Exactly one field is ever set -- ``duration_s`` when nothing here
    contradicts a resting capture, ``vetoed_by`` when something does. They are
    returned **together** rather than by two functions because T064's notes need
    the reason and ``_classify_tier_1`` needs the duration, and F004 defines the
    veto set *as* the demoted pre-amendment predicate: a second function
    computing "why" beside one computing "whether" would be two copies of one
    rule, which is exactly the drift ``_classify_tier_1`` records having already
    happened once in this module.
    """

    duration_s: float | None
    vetoed_by: str | None


def _resting_profile_duration(session: Session) -> float | None:
    """The capture's duration if **no veto fires**, otherwise ``None``.

    The predicate's answer without its reason, for the two callers that only
    need to know *whether* the file contradicts a resting capture:
    ``_gate_a_beatless_resting_capture``, and the suites that drive the veto set
    directly. ``_resting_profile`` below is the implementation and the only
    place the rules are written; this is a projection of it, never a second copy.
    """
    return _resting_profile(session).duration_s


def _resting_profile(session: Session) -> _RestingProfile:
    """The capture's duration if **no veto fires**, otherwise the veto's name
    (F004 reference document §2).

    **This is the veto set, and only the veto set** (F004's 2026-09-06
    amendment; T069 removed the arm that let it also authorise). Answering with a
    duration means "nothing here contradicts a resting capture" -- never "this is
    one". What makes a file a resting capture is ``_declared``; this function can
    only refuse. Its three thresholds keep their exact values and citations, and
    that is deliberate: what changed is their *role*, not their content, so the
    reasoning preserved below still constrains them.

    Every field is first normalised by ``_intensity_signal`` -- the stated
    ``session.summary`` reading convention -- so "present" below means
    *present and informative*, and a veto has already refused the file::

        NOT (duration_s or distance_m or avg_heart_rate is a rule-2 veto)
        AND duration_s is present AND 0 < duration_s <= 300
        AND avg_heart_rate is present AND avg_heart_rate <= 100
        AND (distance_m present  ->  distance_m / duration_s <= 1.0)

    Returning the duration rather than a bare ``bool`` is what lets the quality
    gates re-use the same reading of ``session.summary`` instead of re-deriving it,
    and what lets T064's undeclared-candidate note tell a clean-but-undeclared file
    from a vetoed one without evaluating the set twice.

    **Returning the veto's name beside it is R5's requirement** (T064): a refused
    file records a provenance note that must say *which* veto fired, and the only
    place that answer exists is here. The names are the ``_VETO_*`` constants and
    are reconciled against ``HRV_VETO_REASONS``; each ``return`` below carries
    exactly one. They are a *report*, not a rule -- the rules, their thresholds
    and their citations are unchanged, and remain the only thing this function
    decides.

    **Order matters to the name, not to the answer.** More than one veto can be
    true of one file -- ``strap_run_hrv.fit`` is both 6000 s and 140 bpm -- and
    the note names the first in the fixed order below (rule 2 over the three
    fields, then duration, then heart rate, then speed), which is the order the
    refusal was already evaluated in. Naming the first is not a claim that it is
    the only one.

    **A heart rate is required; a distance can only veto** (amended 2026-09-06,
    sprint-003 critic pass). The 2026-09-06 zero-distance amendment drew its
    line at exactly ``distance_m > 0``, which tests a *sentinel* rather than
    informativeness: 5 m over 240 s is not evidence of stillness either -- it is
    GPS jitter -- yet it satisfied the presence test and routed a capture with
    no heart-rate corroboration at all.

    No threshold on the distance can fix that, and reaching for one is the trap.
    The gate fixture is 108.21 m over 150.797 s of **pure GPS drift**, which the
    reference document says in so many words; 5 m over 240 s is the same
    observation at a smaller magnitude, and any cut between them would be
    invented rather than measured. The line is drawn on the other axis instead.

    A distance below walking pace is the **absence of counter-evidence, never
    evidence**: a stationary maximal effort -- indoor trainer, rowing erg,
    treadmill rep -- produces exactly the same reading as lying still, which is
    the very argument "Why the heart-rate arm exists" has always made. Nothing a
    distance field can say distinguishes rest from effort. A heart rate can, so
    it must be present and must agree; the distance keeps its veto and loses its
    vote. This is the same principle the zero-distance amendment stated, carried
    to its conclusion rather than stopped at the sentinel.

    Neither real capture is affected -- ``strap_hrv_sample_run.fit`` carries
    ``avg_heart_rate = 60`` and ``strap_hrv_capture.fit`` 64 -- and nor is the
    genuine indoor waking capture (``0.0`` m at 55 bpm), which never depended on
    the distance arm. What changed is that a capture whose *only* evidence was a
    distance stopped clearing the veto set; since T069 no evidence of any kind
    clears it into a route, because clearing it is not what routes a file.

    Historically this was a two-armed conjunction, and that shape's own history
    is preserved below because both amendments' reasoning still constrains the
    rule.

    **The two arms were an AND over the signals that exist, not a fallback
    chain, and that was a correctness fix rather than a stylistic one.**
    ``mapping._build_summary`` strips only ``None``, so an indoor session
    carrying ``total_distance = 0.0`` has a ``distance_m`` key holding ``0.0``
    -- present-and-zero, not absent. A treadmill, rowing-erg or
    indoor-trainer interval logs exactly that, and ``0.0 / 240 = 0.0 <= 1.0``
    satisfies the distance *comparison* outright. Under a fallback reading the
    heart-rate arm was then never consulted, and a **maximal effort** routed
    as a resting capture -- the §2.2.3 prohibition the heart-rate arm exists
    to enforce, leaking through the one branch a fallback never takes.

    The direction the old wording *did* get right is preserved: a capture
    that demonstrably moved is still not rescued by a low average heart rate.
    Under an AND neither signal can rescue the other; each can only veto.

    **A zero distance is not evidence of stillness -- it is a device
    reporting no distance** (amended 2026-09-06). Making the predicate a
    conjunction closed the case where the heart rate disagreed, but left one
    arm's worth of residue: a present-and-zero ``distance_m`` still satisfied
    "at least one intensity signal is present" while carrying no intensity
    information at all. With ``avg_heart_rate`` absent, the heart-rate arm was
    skipped as missing, and the conjunction collapsed to the single arm that
    **every** zero-distance capture satisfies unconditionally -- so any
    <= 300 s GPS-less capture with beats and no session-level average heart
    rate routed as rest regardless of its actual intensity. It was found by
    driving the real API during the sprint-003 review: the gate fixture's
    ``session`` message was patched to ``total_distance = 0`` with no
    ``avg_heart_rate``, and its 156 beats came back a full unflagged Tier-1
    reading. Zero is therefore **not a signal**; it falls through to requiring
    a heart rate, exactly as an absent distance does.

    A **negative** ``distance_m`` was answered the same way until 2026-09-06 --
    read as an absence -- and **that is no longer true.** It is now a rule-2
    veto, and so is a negative ``avg_heart_rate`` or ``total_timer_time``: all
    three are unsigned FIT fields, so a negative value can only come from a
    crafted or corrupt definition record, which is the *identical* provenance
    ``_numeric`` reports for a ``tuple``. Answering one as absent and the other
    as a veto was a per-value inconsistency inside a convention written to end
    exactly that. See ``_intensity_signal``, and the F004 Decision Log entry
    *"Reconciling the [[IDEA-009]] convention's treatment of a negative distance
    against an unparseable one"*.

    What has not changed is why the value cannot simply be compared: reading it
    literally would repeat the zero mistake in a worse form, since ``-500 / 240``
    clears the speed bound comfortably and a garbage field would read as a
    *satisfied* stillness test.

    The mirror case -- a present-and-zero ``avg_heart_rate`` -- was knowingly
    left alone as ``IDEA-009`` and is **now closed**, as a consequence of
    stating the convention rather than as a guard invented for it: a zero is
    "nothing reported", and a heart rate is required, so the file does not
    route. It stays near-unreachable on Garmin hardware, which encodes an absent
    heart rate as the uint8 invalid sentinel rather than as ``0`` -- and that
    sentinel is stripped to no key at all, which is the *reachable* path to the
    same answer.

    A ``None`` ``duration_s`` is unambiguous as the refusal answer because a file
    that clears every veto always has a duration strictly greater than zero.

    All three values are read through ``_intensity_signal``, which wraps
    ``_numeric``: a ``tuple`` or ``str`` from a crafted definition record is
    never compared -- comparing it is a ``TypeError`` and an unhandled 500 --
    and is answered as a **veto** rather than as an absence, so it cannot
    quietly hand the predicate to whichever arm is left. ``_numeric`` itself is
    untouched, because ``_classify_tier_2`` shares it and needs the older
    meaning there.

    Deliberately *not* including the beats condition. Tier-1 candidacy needs
    both -- ``_classify_tier_1`` checks the beats itself -- but the beatless
    half of the same profile is what row 3 of the quality-gate outline is
    about, and that case has to stay recognisable after the Tier-2 branch has
    had its turn.

    ``session.summary`` is read with ``.get()`` throughout, never subscripted
    and never via ``msg.has_field()``: ``mapping._build_summary`` strips its
    ``None`` values, so a file with no distance -- **or one that declared the
    field and wrote the FIT invalid sentinel** -- has no ``"distance_m"`` key at
    all, not a key holding ``None``. Subscripting would turn every indoor
    capture into a 500 on a perfectly valid upload, and ``has_field()`` would
    silently invert the sentinel's answer (resolution R1). The ``_MISSING``
    default is what lets ``.get()`` tell that state from a legitimate ``0``.
    """
    summary = session.summary or {}
    duration_s = _intensity_signal(summary.get("duration_s", _MISSING))
    distance_m = _intensity_signal(summary.get("distance_m", _MISSING))
    avg_heart_rate = _intensity_signal(summary.get("avg_heart_rate", _MISSING))

    # Rule 2, evaluated first and over all three fields at once. A veto is a
    # statement about the *file*, not about one signal, so it is answered
    # before any signal is weighed -- and it must not be reachable only on the
    # branch some other arm happens to take. The loop is over a fixed literal
    # pairing so that each field's name travels with its value; it changes no
    # outcome the ``_VETO in (...)`` membership test gave, only what is
    # reported about it.
    for signal, unreadable in (
        (duration_s, _VETO_DURATION_UNREADABLE),
        (distance_m, _VETO_DISTANCE_UNREADABLE),
        (avg_heart_rate, _VETO_HEART_RATE_UNREADABLE),
    ):
        if signal is _VETO:
            return _RestingProfile(None, unreadable)

    # The divisor is established here, before any speed is computed: a
    # degenerate file can carry ``total_timer_time`` 0, and a duration that
    # fails this check can never reach the division below. ``_ABSENT`` covers
    # both the missing key and that degenerate zero, which the ``0 <`` bound
    # would refuse anyway -- the two readings agree here, and only here. They
    # are two branches rather than one ``or`` so the note can tell "the file
    # reported no duration" from "the file reported one and it is outside the
    # protocol"; a present-and-zero duration is reported as *absent*, which is
    # the reading convention's own answer for it and not a second opinion.
    if duration_s is _ABSENT:
        return _RestingProfile(None, _VETO_DURATION_ABSENT)
    if not 0 < duration_s <= _RESTING_MAX_DURATION_S:
        return _RestingProfile(None, _VETO_DURATION_OUT_OF_RANGE)

    # The heart rate is *required*, because it is the only signal that can
    # corroborate rest. A distance can only veto. Absent it there is nothing to
    # discriminate on, and the conservative outcome is no reading. A
    # present-and-zero heart rate is absent by rule 1: a zero is a device
    # reporting nothing, never a measured heart rate of zero.
    if avg_heart_rate is _ABSENT:
        return _RestingProfile(None, _VETO_HEART_RATE_ABSENT)
    if avg_heart_rate > _RESTING_MAX_AVG_HEART_RATE_BPM:
        return _RestingProfile(None, _VETO_HEART_RATE_TOO_HIGH)

    # The distance's veto, and only its veto. Every distance reaching this line
    # is strictly positive -- ``_intensity_signal`` has already answered
    # ``_ABSENT`` for zero and ``_VETO`` for anything impossible -- so the
    # ``> 0`` guard the old body carried is now part of the normalisation
    # rather than a second, separately-maintained copy of it. The check is
    # against the *speed*, not the distance, and it runs only after the
    # duration check above has established a usable divisor.
    if distance_m is not _ABSENT:
        if distance_m / duration_s > _RESTING_MAX_MEAN_SPEED_MS:
            return _RestingProfile(None, _VETO_MEAN_SPEED_TOO_HIGH)

    return _RestingProfile(duration_s, None)


def _surviving_fraction(session: Session, rr_intervals: list[RRInterval]) -> float | None:
    """The retained-beat fraction for this capture, or ``None`` when there is
    no beat stream to have a fraction of.

    ``pipeline.py`` computes it with ``rr_reconstruction.valid_fraction`` and
    parks it on the session *only when beats exist*, deliberately leaving it
    ``None`` -- not ``0.0`` -- otherwise; ``models.Session`` documents that
    distinction as load-bearing, and this function preserves it rather than
    defaulting one to the other.

    The recompute is for callers that reach ``classify()`` without going
    through ``pipeline.py`` -- it is a public function, and F004's own suites
    drive it directly. With beats in hand the fraction is derivable, so
    deriving it is strictly better than mistaking an unpopulated field for
    "no beats"; ``valid_fraction`` is the same function ``pipeline.py`` calls,
    so the two paths cannot drift apart.
    """
    if session.rr_valid_fraction is not None:
        return session.rr_valid_fraction
    if rr_intervals:
        return rr_reconstruction.valid_fraction(rr_intervals)
    return None


def _apply_quality_gates(
    session: Session, duration_s: float, rr_intervals: list[RRInterval]
) -> bool:
    """Rows 1-3 of F004's quality-gate outline. Returns whether any gate failed.

    | Gate               | Rule                       | Flag                    |
    |---|---|---|
    | Minimum duration   | shorter than 120 s         | hrv_capture_too_short   |
    | Artefact survival  | valid fraction below 0.80  | hrv_capture_low_quality |
    | No surviving beats | valid fraction is ``None`` | hrv_capture_no_beats    |

    Every gate that applies raises its own flag -- they are independent
    findings, and the ``_raise_flag`` dedup guard already prevents repeats.
    None of them writes a value: a failed gate leaves ``hrv_source_tier`` and
    ``rmssd_precomputed`` ``None``, never ``0`` or ``-1``. E003 takes
    ``ln(rMSSD)``, so a sentinel there is undefined at best and a silent
    domain error at worst -- the same reason ``rmssd.resting_rmssd`` answers
    "no value" with ``None`` rather than ``0.0``.

    **``is None`` is checked before the float comparison, and that ordering is
    the point of row 3.** A naive ``if fraction < 0.80:`` raises ``TypeError``
    on the ``None`` a beatless session legitimately carries, and that surfaces
    to the client as a **500 on a perfectly valid upload**. The two rows are
    also never collapsed by defaulting ``None`` to ``0.0``: "no beats at all"
    and "beats recorded, none survived filtering" are genuinely different
    findings and E003 reads them differently.
    """
    failed = False

    if duration_s < _RESTING_MIN_DURATION_S:
        _raise_flag(session, _FLAG_CAPTURE_TOO_SHORT)
        failed = True

    fraction = _surviving_fraction(session, rr_intervals)
    if fraction is None:
        _raise_flag(session, _FLAG_CAPTURE_NO_BEATS)
        failed = True
    elif fraction < _RESTING_MIN_VALID_FRACTION:
        _raise_flag(session, _FLAG_CAPTURE_LOW_QUALITY)
        failed = True

    return failed


def _gate_a_beatless_resting_capture(
    session: Session,
    rr_intervals: list[RRInterval],
    *,
    profile_names: Sequence[str] | None = None,
    resting_capture_override: bool = False,
) -> None:
    """Row 3 for the file that carries no ``hrv`` message at all.

    ``rr_valid_fraction`` is ``None`` exactly when the beat stream is empty --
    that is ``pipeline.py``'s rule -- and since **R4** the file that recorded no
    ``hrv`` message at all is precisely the one ``_classify_tier_1`` refuses at
    its beats gate. So this is where the "beat stream is empty, so
    ``rr_valid_fraction`` is null" row is reported for such a file: a strap that
    dropped out, or a capture started before the sensor paired. Its sibling case
    -- ``hrv`` messages that reconstruct to zero beats -- is claimed by Tier 1
    and answered by ``_apply_quality_gates`` on that path instead; see
    ``_hrv_messages_present`` for why the two are split there.

    **Runs after the Tier-2 branch, and only if Tier 2 declined.** Both Health
    Snapshot fixtures are zero-beat, ~120 s, low-heart-rate files -- the same
    profile -- and they are perfectly good Tier-2 readings. Flagging them as
    failed captures, or running before ``_classify_tier_2`` and pre-empting
    them, would destroy a working reading; ``activity_tag`` being set is the
    signal that Tier 2 recognised the file.

    **Declaration-gated (T066; T058 Finding 17 / row D5), and this is the whole
    subject of that task.** A ``hrv_capture_*`` flag asserts a finding *about a
    recognised capture*; a provenance note records a file that was examined and
    refused. Before T066 this function fired on any beatless, resting-*shaped*,
    unclaimed file with no reference to the declaration -- so a file the system
    had explicitly not recognised carried a flag asserting a finding about one,
    which is the note/flag boundary this module rests on, crossed from the other
    side. It is the same invariant ``classify``'s multi-session refusal and
    T064's flagless undeclared note already keep, and F004 states it in its own
    words twice ("a flag asserts a finding about a *recognised* capture, and this
    file has not been recognised as one"). The spec's scenario outline lists the
    beatless row with no declaration qualifier, so the literal text permits
    either reading; consistency with the stated principle decides it. Recorded
    here so it is not re-litigated by a reader who finds the outline first.

    **Gated on the whole of ``_declared``, never on the config half alone.** F004
    states the declaration once, as a disjunction, and an upload-time override is
    a deliberate act of intent about one specific file. Splitting it here would
    make the flag mean "recognised on one route only", a distinction no scenario
    draws -- and it would rest on T058's **Finding 5**, which the resolutions
    document leaves explicitly deferred: were that finding to resolve toward a
    note for a beatless *configured* declaration, the two routes would become
    indistinguishable and the split indefensible. The consequence is deliberate
    and pinned: an override on a beatless file records
    ``{"honoured": false, "reason": "no_beats"}`` *and* raises this flag. The two
    say different things -- the note reports that the Tier-1 *route* could not be
    taken, the flag reports what the declared capture recorded.

    The vetoes are still evaluated after the declaration, via
    ``_resting_profile_duration``: a declared file that contradicts its own
    declaration is not a recognised capture either, and T064 has already written
    it a note naming the veto.

    No ``activity_tag`` is written here. The file yielded nothing and was
    claimed by no tier, so tagging it ``resting_hrv_check`` would assert a
    classification the gates have just declined to make.
    """
    if rr_intervals or session.activity_tag is not None:
        return

    if not _declared(session, profile_names, resting_capture_override):
        return

    duration_s = _resting_profile_duration(session)
    if duration_s is None:
        return

    _apply_quality_gates(session, duration_s, rr_intervals)


def _declared(
    session: Session,
    profile_names: Sequence[str] | None,
    resting_capture_override: bool,
) -> bool:
    """Whether the athlete declared this file a resting capture (T063).

    F004's 2026-09-06 amendment, stated as a **disjunction** in the feature
    file's normative block::

        declared = session.sport_profile_name is in config
                       `resting_hrv_profile_names`
                   OR the upload carried an explicit resting-capture override

    **The empty list is not a kill switch.** ``resting_hrv_profile_names = []``
    is the athlete saying "no activity profile means a resting capture" -- an
    explicit Tier-2-only declaration -- and it says nothing at all about a
    per-upload override, which is a separate, deliberate act of intent about
    one specific file. So an empty list *plus* an override still declares
    (resolution **R3**; T058 Finding 6, the row no scenario covered and the one
    a reader of the Decision Log's prose would get backwards).

    **Matching is exact and case-sensitive, and that is forced by a real
    collision rather than a hypothetical one.** The corpus holds the athlete's
    own ``'HRV Snapshot'`` and Garmin's built-in ``'Health Snapshot'``, and both
    contain "Snapshot": a substring match would send a Health Snapshot down the
    Tier-1 branch, reaching the §2.2.3/§2.4.5 anti-mixing prohibition by way of
    a config convenience. No trimming and no case folding either -- "case
    insensitive and trimmed" was the rejected option, and the usual objection
    (a typo fails silently) is answered by T064's provenance note rather than
    by loosening the comparison. ``'Health Snapshot'`` must never be offered as
    a config value: it already routes via Tier 2's numeric ``sport == 60``
    identity, and listing it would give one file two routes into one decision.

    **A profile name that is not a ``str`` is undeclared, never a veto**
    (T058 Finding 12). ``fitdecode`` returns an ``int`` where it cannot resolve
    an enum, so a non-``str`` value off the ``session`` message is not
    hypothetical -- but the reading convention's veto is scoped to the three
    *intensity* signals, because a veto says the file's data is corrupt. A
    corrupt *identity* field says only that the athlete did not declare, which
    is the conservative answer and the one the exact match already gives. The
    ``isinstance`` guard states that rather than leaving it to fall out of
    ``in``, so a later reader does not have to re-derive why it is safe.

    **Read through ``context.provenance``, never ``has_field()``** -- the same
    discipline resolution R1 imposes on ``session.summary``. ``mapping.py``
    lifts the field once (T056) and omits the key entirely when the file
    carried nothing, so ``.get`` answering ``None`` *is* "the file claimed no
    profile", which matches nothing. The key is a string **literal** on
    purpose: ``test_hrv_quarantine_boundary`` recovers this module's field
    reads from its AST and cannot resolve a computed key, and one unresolvable
    key blinds the entire ``HRV_INPUT_FIELDS`` reconciliation.
    """
    return resting_capture_override or _declared_by_profile_name(session, profile_names)


def _declared_by_profile_name(
    session: Session, profile_names: Sequence[str] | None
) -> bool:
    """The config half of the disjunction, on its own.

    Split out of ``_declared`` by T064 because the two routes need **different
    notes** when a veto refuses the file: the config route's note reports the
    profile name that was declared, while the override route's reports that a
    per-upload act could not be honoured. ``_declared`` remains the predicate
    ``_classify_tier_1`` routes on, so the disjunction is still stated once and
    the split cannot change what routes.

    Every word of the matching rule is documented on ``_declared`` above.
    """
    sport_profile_name = _claimed_profile_name(session)
    if not isinstance(sport_profile_name, str):
        return False

    return sport_profile_name in (profile_names or ())


def _claimed_profile_name(session: Session) -> Any:
    """The activity-profile name the file claimed, exactly as ``mapping.py``
    lifted it -- or ``None`` when it claimed nothing.

    ``.get`` with no default is the whole point: T056's lift **omits the key**
    when the file carried no ``sport_profile_name``, so ``None`` here means "the
    file claimed nothing" rather than "the file claimed null". T064's note then
    records that ``None`` **explicitly**, which is the deliberate asymmetry
    against the lift: the lift records only what was there, while the note
    asserts a finding -- *we looked, and the file claimed nothing* -- which is
    what makes a third-party exporter that omits the field diagnosable rather
    than mysterious. Both halves are right and neither should be "fixed" to
    match the other.

    Returned **uncoerced**, whatever type the file carried. A non-``str`` name is
    not a veto (T058 Finding 12) and normalising it would destroy the diagnostic:
    the note's job is to hand back the precise value that failed to match --
    trailing space, wrong case, wrong type and all -- which is what makes exact,
    case-sensitive matching tolerable. The value is already in provenance
    verbatim under the same name, so recording it a second time adds no
    serialisation hazard that T056's lift did not already carry.

    The key is a string **literal** for the AST guard, exactly as in ``_declared``.
    """
    return _provenance(session).get("sport_profile_name")


def _record_refused_declaration(
    session: Session,
    vetoed_by: str,
    profile_names: Sequence[str] | None,
    resting_capture_override: bool,
) -> None:
    """Write the note for a file with beats that a **veto** refused (R5).

    R5, ratified 2026-09-06, resolves T058's Findings 3 and 4 together: *any
    file examined and not routed records a provenance note naming the veto that
    fired*, on both the declared and the undeclared side. Before it, a vetoed
    file got no route, no flag and no note on either side --
    ``strap_cool_down_walk.fit`` is the corpus's proof, undeclared *and* vetoed
    at 1.0356 m/s, ingesting with no observable record whatsoever.

    Which note depends on **how the athlete declared**, because that is what
    decides the action the note implies:

    * an **override** -- the per-upload act could not be honoured, and the
      reason is the veto. The override is a claim about *intent*, never about
      the data, so it cannot rescue a 6000 s file or a 140 bpm one.
    * a **configured profile name** -- the declaration was made and the file
      contradicted it. This is the athlete who did the setup step, and R5's
      argument for observability applies to them with more force, not less.
    * **neither** -- the undeclared candidate, now carrying the veto that fired
      alongside the name the file claimed.

    Both routes can be satisfied at once, and then **both notes are written**.
    T058's **Finding 7** (which note a doubly-declared file records) is
    explicitly among the findings the resolutions document left deferred, so
    each note here states only what is true of its own route -- the override was
    not honoured; a configured name was vetoed -- and neither claims the other
    route would have behaved differently. Choosing one would be answering a
    question that was deliberately not answered.
    """
    if resting_capture_override:
        _provenance(session)[_PROVENANCE_RESTING_CAPTURE_OVERRIDE] = {
            "honoured": False,
            "reason": vetoed_by,
        }
    if _declared_by_profile_name(session, profile_names):
        _provenance(session)[_PROVENANCE_DECLARED_CAPTURE_VETOED] = {
            "sport_profile_name": _claimed_profile_name(session),
            "vetoed_by": vetoed_by,
        }
    if not _declared(session, profile_names, resting_capture_override):
        _record_undeclared_candidate(session, vetoed_by)


def _record_undeclared_candidate(session: Session, vetoed_by: str | None) -> None:
    """The undeclared note: a file with beats whose profile is not configured.

    ``vetoed_by`` is written **explicitly as ``None``** on a clean refusal
    rather than omitted, and that is the whole diagnostic value of the key. A
    clean undeclared capture is the *actionable* refusal -- configure that
    profile and captures like it will route -- while a vetoed one contradicted
    itself anyway and configuring the name would change nothing. A consumer that
    has to infer which it is from a missing key will get it wrong, and this note
    exists precisely so that a false negative is not left to inference.

    It is also what keeps the population R5 knowingly admitted filterable. Since
    the note no longer waits for the vetoes to pass, every ordinary run carrying
    ``hrv`` messages records one -- 3 of this corpus's 10 fixtures on top of the
    2 genuine captures -- and ``vetoed_by is null`` is the query that separates
    the two.
    """
    _provenance(session)[_PROVENANCE_UNDECLARED_CANDIDATE] = {
        "sport_profile_name": _claimed_profile_name(session),
        "vetoed_by": vetoed_by,
    }


def _classify_tier_1(
    messages: list[fitdecode.FitDataMessage],
    session: Session,
    rr_intervals: list[RRInterval],
    *,
    profile_names: Sequence[str] | None = None,
    resting_capture_override: bool = False,
) -> bool:
    """The chest-strap resting capture (F004 reference document §2).

    Returns whether the file was routed, so ``classify`` can stop before
    the Tier-2 branch -- which is how §2.4.5's highest-fidelity-first
    hierarchy is enforced: on a capture carrying both raw beats and a
    device ``rmssd_hrv``, this branch wins and the device's scalar is
    left unused. It takes ``messages`` for that reason alone: to see the
    device value it is about to ignore, so it can record it (T045).

    The predicate, as amended 2026-09-06, landed additively by T063, completed by
    T069 and given R4's beats gate by T066::

        tier1 := beats present                                    # R4; see below
             AND _resting_profile_duration(session) is not None   # the vetoes
             AND _declared(...)                                   # the authoriser

    where ``beats present`` is ``rr_intervals or _hrv_messages_present(messages)``
    -- **``hrv`` messages, not the reconstructed stream**. That is resolution
    **R4**, settling T058's Finding 17; the whole argument, including why
    ``rr_intervals`` is consulted beside the messages and R4's own revisit
    condition, lives on ``_hrv_messages_present``. The consequence that matters
    here: a declared file whose ``hrv`` messages reconstruct to **zero** beats is
    claimed by this branch and answered by ``_apply_quality_gates`` with
    ``hrv_capture_no_beats``, rather than falling through to Tier 2.

    **Nothing is inferred.** ``_declared`` is the only term that can say yes; the
    other two can only say no. The demoted duration/distance/heart-rate rules keep
    their exact thresholds and citations and have lost their vote -- see
    ``_resting_profile_duration``, which owns them and documents why each is worth
    keeping as a veto.

    **There must never be a fall-through arm here again, and the reason is
    arithmetic rather than taste.** F004 defines the veto set *as* the demoted
    predicate, so ``_resting_profile_duration(session) is not None`` and "the
    pre-amendment predicate accepts this file" are the same statement over the
    same inputs. An ``or`` on the declaration line is therefore not a fallback at
    all -- it reduces the whole conjunction to ``NOT vetoed`` and restores
    ``IDEA-010`` verbatim, invisibly, with every test still green. T063 proved
    exactly that by perturbation and filed ``IDEA-018``; T069 removed the arm and
    the suites can now tell the two rules apart.

    **Two orderings are normative, and only one of them is obvious.**

    *Beats before declaration.* ``classify()`` returns early when this branch
    claims a file, so a declared-but-beatless file that "claimed" Tier 1 would
    never reach Tier 2. That is reachable the moment ``'Health Snapshot'`` is
    listed in config -- real snapshots carry **zero** ``hrv`` messages, and
    ``sample_health_snapshot.fit`` carries that exact profile name -- and it
    would route a perfectly valid snapshot **nowhere**. The order used to hold
    by accident of statement order; it is a stated contract now, with a
    scenario behind it. R4 does not weaken it: "beats present" is still evaluated
    first and a snapshot still has no ``hrv`` message to satisfy it.

    *Vetoes before the declaration decides refusal.* ``beats AND declared AND
    NOT vetoed`` reads naturally as beats -> declared -> vetoed, under which an
    **undeclared** file never reaches the veto evaluation at all. T064's
    provenance notes need the opposite: since R5 a refused file records *which
    veto fired* on both the declared and the undeclared side, so the veto's
    answer must exist before the declaration is consulted. It is also what lets
    the undeclared note tell ``strap_hrv_sample_run.fit`` (clean, undeclared ->
    ``vetoed_by`` null, the actionable refusal) from ``strap_run_hrv.fit``
    (6000 s -> ``vetoed_by`` named). Evaluating beats -> vetoes -> declaration
    is what lets T064 read the answer already computed here instead of
    evaluating the set a second time -- and a duplicated predicate is exactly
    the drift this function records having already happened once, which is why
    ``_resting_profile`` returns the reason beside the duration rather than
    having a second function derive it.

    **Both declaration routes are subject to every veto.** The override is a
    claim about *intent*, never about the data: it cannot rescue a 6000 s file
    or a 140 bpm one. It is otherwise the branch tests do not take, which is
    precisely how the zero-distance bug survived six waves.

    The duration/intensity half lives on
    ``_resting_profile_duration`` and is documented there, once. It used to be
    restated here in full and the two copies had already drifted -- this one had
    lost the ``0 <`` lower bound that stops a degenerate ``total_timer_time`` 0
    from reaching the division. Only the conditions this function owns are stated
    here.

    **Raw-RR presence alone must never route**, and this is the one
    bright line the reference document draws in boldface.
    ``dev_fields_run.fit`` is an ordinary 52-minute run carrying 3127
    ``hrv`` messages / 7220 beats at ``rr_valid_fraction`` 0.998; a
    "beats present -> Tier 1" rule converts it into a resting-HRV
    reading and poisons E003's readiness trend -- the §2.2.3
    prohibition this guard exists to enforce. ``mapping._infer_hr_source``
    already uses RR presence as the chest-strap signature *for
    activities*, which is exactly why it cannot also mean "resting".

    **``sport`` and ``sub_sport`` are deliberately not gated on, and the
    declaration is not a sport check in disguise.** ``sub_sport`` reads
    ``generic`` on every fixture in the corpus -- it is a Garmin enum the
    athlete cannot set -- and ``sport`` is ``running`` on both
    ``strap_hrv_sample_run.fit`` and ``dev_fields_run.fit``, so it carries
    no signal. The declared positive ``strap_hrv_capture.fit`` reports
    ``sport = generic`` because it was created through "Other", which is
    distinctive across today's corpus and therefore tempting; it is true
    of *any* custom activity profile, so it is corroboration only and
    never the key. The key is ``sport_profile_name``, which the athlete
    owns -- see ``_declared``.

    **Why the heart-rate veto exists**, and why it survived the
    demotion. Without it, a file with no ``distance_m`` reduced the old
    predicate to "beats present AND duration <= 300 s", and since sport
    is not gated on, a chest-strap-paired 4-minute indoor-trainer FTP
    test, rowing-erg piece or treadmill interval rep all satisfied it --
    feeding a *maximal effort* into the readiness ladder as rest. That
    hole is closed twice over now, because such a file is also
    undeclared; the veto is kept anyway because a **declared** file can
    still be wrong. The athlete forgets to stop the timer on the HRV
    profile and runs 10 km, and the vetoes are the only thing between
    that file and E003. It is **not** consulted only when distance is
    missing: a present-and-zero distance is what an indoor session
    actually logs, and treating it as a satisfied distance arm let that
    maximal effort straight through. Since the 2026-09-06 critic pass it
    is not an "arm" at all: the heart rate is **required** on every
    Tier-1 route, because it is the only signal that can tell rest from
    a stationary maximal effort, and a distance can only veto. See
    ``_resting_profile_duration``.

    **A reading that cannot be computed is not a reading.** The tier
    fields are written only once ``rmssd.resting_rmssd`` has produced a
    **positive** value. A stream of a single beat -- a strap that paired
    and then dropped -- has zero contributing pairs and no statistic, yet
    ``valid_fraction`` on one unflagged beat is ``1.0`` and clears the
    0.80 gate; stamping ``chest_strap_raw`` on it would assert a
    completed chest-strap reading carrying no number and no explanation.

    **A computed ``0.0`` is refused too, and that amends T042 rather than
    forgetting it** (F004 Decision Log, 2026-09-06; T065). T042 wrote the
    opposite here in so many words -- *"``0.0`` is not that case: it is a
    genuine measurement of zero variability, which is why the test is
    ``is None`` and never falsiness"* -- and that reasoning was sound while
    the computed value lived only in provenance. It stopped being sound the
    moment ``resting_rmssd_ms`` resolved **both** tiers into one column:
    Tier 2 has always refused a non-positive device ``rmssd_hrv`` to close
    the ``ln(0)`` hazard in E003's ``ln(rMSSD)`` trend, so keeping T042's
    rule would have made ``0.0`` mean *success on Tier 1 and failure on
    Tier 2* in the one field a consumer is told it may ``ln()`` without
    knowing which tier produced it. That is a sibling of the null-shaped
    trap ``IDEA-007`` closed, and a resolved column whose meaning depends
    on the tier is not resolved. The gate is defensible on its own merits
    as well: a true rMSSD of ``0.0`` requires literally identical
    successive intervals, which is a degenerate beat stream -- a device
    artefact -- and not a physiological state.

    So the post-gate test is on the **value**, ``computed is None or
    computed <= 0``, and the two branches raise the *same* flag for the
    same finding: this capture was recognised and yielded no usable number.
    They stay separate branches only because their explanations differ --
    one had no contributing pair at all, the other measured a zero.

    **``rmssd.resting_rmssd`` is untouched by this, and its structural
    ``None``/``0.0`` distinction still matters.** It is the function that
    can tell "no value is derivable" from "a value was derived and it is
    zero"; collapsing them there would lose the ability to say which
    happened. What T065 changed is only what *this* function does with a
    measured zero at the boundary.

    **The single success point writes ``resting_rmssd_ms``.** There are
    three ``return True`` paths above it -- the gate failure, the
    underivable statistic, and the computed zero -- and every one of them
    must leave the column ``None``, because the column's contract is that
    it is populated for exactly the successful readings. That invariant is
    what closes ``IDEA-007`` structurally: there is no successful reading of
    either tier for which the field E003 reads is null, so E003 cannot
    silently build the readiness trend from wrist-PPG alone.
    ``rmssd_precomputed`` still stays ``None`` here -- it keeps its
    device-only meaning as the Tier-2 audit record, and
    ``_PROVENANCE_COMPUTED_RMSSD`` is its Tier-1 twin.
    """
    # 1. Beats, first and normatively so -- see the docstring. A beatless file
    #    must fall through to Tier 2 rather than be claimed and answered here.
    #
    #    **"Beats present" means ``hrv`` messages, not a non-empty reconstructed
    #    stream** -- resolution R4, settling T058's Finding 17. The whole
    #    argument, including R4's revisit condition and why ``rr_intervals`` is
    #    still consulted beside it, lives on ``_hrv_messages_present``.
    if not rr_intervals and not _hrv_messages_present(messages):
        # T064. The one note the beats gate owes: an override the athlete
        # applied to this specific upload could not be honoured, and F004 makes
        # that its own scenario ("an override on a file with no beats reports
        # that it did nothing"). Tier 2 is still evaluated afterwards and may
        # well route the file -- the two are not alternatives.
        #
        # No *undeclared* note here (contract table A8): the note is gated on
        # beats, R5 moved only the veto gate, and without that gate every
        # beatless upload -- every Health Snapshot, every wrist-PPG run -- would
        # carry one, which is noise no ``vetoed_by`` value could filter. And no
        # note for a beatless **configured** declaration either: that is T058's
        # Finding 5, which the resolutions document leaves explicitly deferred,
        # so answering it here would pre-empt a decision that is not this
        # module's to take today.
        #
        # Since R4 this branch is reached only by a file carrying **no ``hrv``
        # message at all**, which is exactly the population A8 was arguing about.
        # A file whose ``hrv`` messages reconstruct to zero beats now passes this
        # gate and does get an undeclared note further down -- correctly, because
        # under "beats present" it *has* beats and is a capture the athlete
        # simply never declared.
        if resting_capture_override:
            _provenance(session)[_PROVENANCE_RESTING_CAPTURE_OVERRIDE] = {
                "honoured": False,
                "reason": _OVERRIDE_NOT_HONOURED_NO_BEATS,
            }
        return False

    # 2. The vetoes, before the declaration is consulted: a file that
    #    contradicts its own declaration is refused whichever route declared
    #    it, and T064 needs this answer computed once, here.
    profile = _resting_profile(session)
    if profile.vetoed_by is not None:
        _record_refused_declaration(
            session, profile.vetoed_by, profile_names, resting_capture_override
        )
        return False
    duration_s = profile.duration_s

    # 3. The declaration -- the athlete saying this file was *meant* as a
    #    measurement, and since T069 the **only** thing that can authorise a
    #    Tier-1 route. There is no fall-through arm here and there must never be
    #    one again: the pre-amendment predicate is exactly the veto evaluation
    #    two lines above, so an ``or`` here would make the declaration a no-op
    #    and restore ``IDEA-010`` verbatim.
    if not _declared(session, profile_names, resting_capture_override):
        # T064's headline note, and the clean case it was specified for: this
        # file is a capture in every respect the system can check, and the only
        # thing missing is the athlete's declaration. ``vetoed_by`` is null,
        # which is what makes it the actionable one.
        _record_undeclared_candidate(session, None)
        return False

    # T064. The override was accepted and this branch is about to claim the
    # file, which is what ``honoured`` asserts -- not that a reading came out.
    # Written here rather than at the success point below so that a capture
    # claimed by the override and then refused by a §5 quality gate still says
    # where its route came from; a gate failure is a finding about a recognised
    # capture, and losing the declaration's provenance to it would recreate the
    # silence R5 was ratified to end.
    if resting_capture_override:
        _provenance(session)[_PROVENANCE_RESTING_CAPTURE_OVERRIDE] = {"honoured": True}

    # T045. The file is a Tier-1 capture, so this branch has claimed it and
    # ``classify`` will not reach Tier 2 -- which means any device-supplied
    # ``rmssd_hrv`` on the same capture is now unused. Recorded *before* the
    # gates rather than after the reading, because the gate-failure path
    # short-circuits too: a capture answered with a flag has ignored the
    # device's number just as thoroughly as one answered with a computation,
    # and this entry is then the only surviving record that the file carried a
    # scalar at all. Absent, never ``None``, when there was no device value --
    # a key holding ``None`` would assert that one was observed and ignored.
    device_rmssd_hrv = _session_rmssd_hrv(messages)
    if device_rmssd_hrv is not None:
        # Stored exactly as observed, uncoerced and unvalidated: Tier 2's
        # non-positive check gates a value it is about to *store* as a reading,
        # and nothing here is being stored as one. An audit record that
        # normalised what it saw could not be compared against what the device
        # reported.
        _provenance(session)[_PROVENANCE_UNUSED_DEVICE_RMSSD] = device_rmssd_hrv

    # T043's gates run *here* -- after the discriminator above has decided
    # this file is a resting capture, and never before it. A flag on a file
    # that was never a candidate is noise: a 90-second ordinary run is under
    # 120 s but has no capture quality to report.
    if _apply_quality_gates(session, duration_s, rr_intervals):
        # Routed in the sense that matters to ``classify`` -- the file has
        # been recognised and answered -- but no reading is derived. The
        # session keeps its flags and both HRV fields stay ``None``; there is
        # deliberately no fall-through to the Tier-2 device scalar, because
        # §2.4.5's hierarchy is highest-fidelity-first and a failed Tier-1
        # capture yields *no reading*, not a downgraded one.
        return True

    # The statistic is computed *before* the tier is stamped, and "no derivable
    # statistic" is a gate failure like any other. ``resting_rmssd`` answers
    # ``None`` when no pair contributes -- a single-beat stream, or one whose
    # beats carry no ``rr_ms`` -- and neither case is caught by the gates above:
    # ``valid_fraction`` on one unflagged beat is ``1.0``. Writing the tier
    # fields first would leave a session stamped as a completed chest-strap
    # reading carrying no number and no explanation.
    computed = rmssd.resting_rmssd(rr_intervals)
    if computed is None:
        # Row 4's flag, which is the feature's existing vocabulary for "this
        # capture was recognised but yielded no usable number". Not
        # ``_FLAG_CAPTURE_NO_BEATS``: there *are* beats here, and saying
        # otherwise would be a false statement about the stream.
        _raise_flag(session, _FLAG_READING_UNAVAILABLE)
        return True

    # T065, amending T042. The non-positive gate Tier 2 has always applied to a
    # device ``rmssd_hrv`` now applies to the computed value too, because both
    # resolve into one column and ``0.0`` cannot mean success on one tier and
    # failure on the other in the field E003 is told it may ``ln()``. Kept a
    # separate branch from ``computed is None`` above deliberately: the two are
    # different findings -- "no pair contributed" versus "a zero was measured" --
    # that happen to share a flag, and ``rmssd.resting_rmssd`` exists to keep
    # them distinguishable. The test is ``<= 0`` rather than falsiness, so
    # ``-0.0`` -- which ``math.sqrt`` really can return, and for which ``ln`` is
    # just as undefined -- is refused by the same line.
    if computed <= 0:
        _raise_flag(session, _FLAG_READING_UNAVAILABLE)
        return True

    # The single success point. Everything above it returns without writing
    # ``resting_rmssd_ms``, which is the whole contract of the column: it is
    # populated for exactly the successful readings and null for every other
    # outcome, on both tiers.
    session.activity_tag = _ACTIVITY_TAG_RESTING_HRV_CHECK
    session.hrv_source_tier = _TIER_CHEST_STRAP_RAW
    session.rr_source = _RR_SOURCE_CHEST_STRAP
    # The resolved column E003 reads (T055's column, given its meaning by T065),
    # carrying the *system-computed* value on this tier. Written from the same
    # local as the provenance record below, never recomputed, so the audit trail
    # and the column can never disagree about what this capture measured.
    session.resting_rmssd_ms = computed
    # ``rmssd_precomputed`` is left ``None`` on purpose -- see
    # ``_PROVENANCE_COMPUTED_RMSSD``. The provenance entry is Tier 1's audit
    # record, the twin of ``rmssd_precomputed``'s Tier-2 role, and is kept rather
    # than folded into the column: it is what proves the number came from the
    # beats rather than from a device.
    _provenance(session)[_PROVENANCE_COMPUTED_RMSSD] = computed
    return True


def _classify_tier_2(messages: list[fitdecode.FitDataMessage], session: Session) -> None:
    """The Health Snapshot routing table (F004 reference document §1).

    | ``rmssd_hrv``  | raw sport | Outcome                                    |
    |---|---|---|
    | present, > 0   | 60        | Resting-HRV reading, Tier 2. Both agree.   |
    | present        | not 60    | Not a reading. Fields stay ``None``; the   |
    |                |           | disagreement goes to provenance. No flag.  |
    | absent or <= 0 | 60        | Tagged, no reading, flag raised.           |

    **Both signals matter, and the capability signal alone never
    routes.** An earlier draft routed on ``rmssd_hrv`` presence alone;
    the Stage 4.95 critic showed that cancelled the very guard
    ``sport == 60`` was introduced to provide, letting any firmware that
    attaches ``rmssd_hrv`` to a 90-minute run feed in-run wrist-PPG
    rMSSD into E003's readiness trend -- an absolute §2.2.3 / §2.4.5
    prohibition ("HRV is never computed from in-run wrist PPG").
    Accepted cost, recorded in the Decision Log: another vendor's
    snapshot sport value must be added to ``_SNAPSHOT_RAW_SPORT_VALUE``
    explicitly before its readings are recognised.

    The identity signal is read from the provenance ``mapping.py``
    already records for a sport it had to bucket -- not re-parsed from
    the messages. A named sport (``"running"``) leaves the key absent,
    which is correctly *not* 60.

    **Tier 2 never matches on a name**, and that is now a statement about
    *this branch* rather than about the module (corrected 2026-09-06, T063).
    ``"Health Snapshot"`` in ``sport.name`` or ``sport_profile_name`` is free
    text and locale-dependent, so it would be wrong on any non-English watch
    where the numeric ``sport == 60`` identity is not (reference document §1).
    Tier **1** does match ``sport_profile_name``, against the athlete's own
    configured list -- see ``_declared`` -- and the two facts are consistent
    rather than in tension: it is exactly because ``'Health Snapshot'`` already
    has a numeric route here that it must never be listed as a Tier-1 profile
    name, which would give one file two routes into the same decision.

    **There is deliberately no duration bound here, and its absence is a
    decision rather than an oversight.** Tier 1 caps a capture at 300 s,
    but Tier 2 routes a sport-60 file of any length: ``sport == 60`` is
    treated as sufficient identity on its own, because a device only
    emits that sport code for a Health Snapshot -- a two-minute,
    zero-calorie non-session by construction. The counter-argument is
    recorded with the decision (F004 Decision Log, 2026-09-06): the value
    60 is *unconfirmed in Garmin's published enum* -- see
    ``_SNAPSHOT_RAW_SPORT_VALUE`` -- so that identity rests on an
    unratified enum, and a duration bound would have been a cheap second
    guard. The user chose to keep the current behaviour; do not add one
    without reopening that entry.

    ``rmssd_hrv`` is read through ``_numeric``. It is compared against
    zero and then *stored* as a reading, and a ``tuple`` or ``str`` from a
    crafted definition record would raise ``TypeError`` on that
    comparison -- an unhandled 500 on a malformed upload. A non-numeric
    value is treated as absent, which puts the file on row 3.

    **That is deliberately not the Tier-1 reading convention, and must not be
    "fixed" to match it.** ``_intensity_signal`` answers an unparseable value
    with a veto because a Tier-1 signal that cannot be read is evidence the
    *file* is wrong and another arm would otherwise satisfy the predicate
    alone. Here there is no other arm: an unreadable ``rmssd_hrv`` genuinely
    means "no usable device value", which is exactly row 3. F004 pins the
    distinction as a scenario of its own -- *"The Tier-2 coercion keeps its own
    meaning"* -- and the convention states its own scope as
    ``_resting_profile_duration`` only.
    """
    rmssd_hrv = _numeric(_session_rmssd_hrv(messages))
    raw_sport_value = _provenance(session).get("raw_sport_value")
    is_snapshot = raw_sport_value == _SNAPSHOT_RAW_SPORT_VALUE

    if not is_snapshot:
        if rmssd_hrv is not None:
            # Row 2. This branch raises no flag *of its own*: the file is
            # not a Health Snapshot, so there is no Tier-2 reading for
            # ``hrv_reading_unavailable`` to be about -- materially
            # different from row 3, where the file *is* a snapshot and
            # genuinely yielded nothing.
            #
            # It does **not** claim the file is exempt from every gate.
            # ``classify`` runs ``_gate_a_beatless_resting_capture`` after
            # this branch returns, and a row-2 file whose own profile is a
            # resting one -- short, low intensity, no beats -- is genuinely
            # the "beat stream is empty" case §5 describes, whichever tier
            # declined it first. It is flagged ``hrv_capture_no_beats``
            # there, correctly, and
            # ``test_a_resting_shaped_row_2_file_with_no_beats_is_still_flagged_beatless``
            # pins that so this comment cannot drift back into over-claiming.
            #
            # Both observed values are recorded so the audit trail says what
            # disagreed with what.
            _provenance(session)[_PROVENANCE_SIGNAL_DISAGREEMENT] = {
                "rmssd_hrv": rmssd_hrv,
                "raw_sport_value": raw_sport_value,
            }
        return

    # Rows 1 and 3 both tag: the file genuinely *is* a Health Snapshot
    # whether or not a reading came out of it, and E003 must be able to
    # exclude a two-minute non-session from rTSS and the PMC either way.
    session.activity_tag = _ACTIVITY_TAG_HEALTH_SNAPSHOT

    if rmssd_hrv is None or rmssd_hrv <= 0:
        # Row 3. Non-positive is rejected to close the ``ln(0)`` hazard
        # in E003's ``ln(rMSSD)`` trend and the decoded-sentinel case.
        # Deliberately minimal -- no plausibility band is invented,
        # because none is citable; physiologically odd but positive
        # values still reach E003, whose SWC band absorbs outliers.
        _raise_flag(session, _FLAG_READING_UNAVAILABLE)
        return

    # Row 1. Stored exactly as the device gave it (an ``int`` on both
    # fixtures). No ``rr_intervals`` rows are created: §2.2.3 says the
    # precomputed scalar "bypasses the artefact filter... there are no
    # beats to filter".
    session.rmssd_precomputed = rmssd_hrv
    # T065. The resolved column, carrying the *device* value on this tier -- the
    # same object, not a re-read of the message, so the audit column and the
    # resolved one cannot disagree. It is written only here, past the
    # ``rmssd_hrv is None or rmssd_hrv <= 0`` gate above, which is what makes the
    # column's invariant hold: **always > 0 when set**, on either tier. That gate
    # is also why a sentinel can never reach it -- ``_session_rmssd_hrv`` reads a
    # *value* and answers ``None`` for the FIT invalid sentinel that T057 found
    # declared on every fixture in the corpus, and ``None`` lands on row 3.
    session.resting_rmssd_ms = rmssd_hrv
    session.hrv_source_tier = _TIER_HEALTH_SNAPSHOT
    session.rr_source = _RR_SOURCE_HEALTH_SNAPSHOT
