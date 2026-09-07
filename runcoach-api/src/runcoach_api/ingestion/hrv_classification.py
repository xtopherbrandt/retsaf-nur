"""Resting-HRV detection and tier routing (F004).

The policy half of the F004 seam: it reads the decoded FIT messages and
the reconstructed beat stream, decides whether the file is a
resting-HRV *reading* rather than a training session, and writes the
answer onto the ``Session`` -- ``activity_tag``, ``hrv_source_tier``,
``rmssd_precomputed`` and the session-level ``rr_source``. It delegates
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
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

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
    _gate_a_beatless_resting_capture(session, rr_intervals)
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


def _resting_profile_duration(session: Session) -> float | None:
    """The capture's duration if **no veto fires**, otherwise ``None``
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

    ``None`` is unambiguous as the refusal answer because a file that clears
    every veto always has a duration strictly greater than zero.

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
    # branch some other arm happens to take.
    if _VETO in (duration_s, distance_m, avg_heart_rate):
        return None

    # The divisor is established here, before any speed is computed: a
    # degenerate file can carry ``total_timer_time`` 0, and a duration that
    # fails this check can never reach the division below. ``_ABSENT`` covers
    # both the missing key and that degenerate zero, which the ``0 <`` bound
    # would refuse anyway -- the two readings agree here, and only here.
    if duration_s is _ABSENT or not 0 < duration_s <= _RESTING_MAX_DURATION_S:
        return None

    # The heart rate is *required*, because it is the only signal that can
    # corroborate rest. A distance can only veto. Absent it there is nothing to
    # discriminate on, and the conservative outcome is no reading. A
    # present-and-zero heart rate is absent by rule 1: a zero is a device
    # reporting nothing, never a measured heart rate of zero.
    if avg_heart_rate is _ABSENT:
        return None
    if avg_heart_rate > _RESTING_MAX_AVG_HEART_RATE_BPM:
        return None

    # The distance's veto, and only its veto. Every distance reaching this line
    # is strictly positive -- ``_intensity_signal`` has already answered
    # ``_ABSENT`` for zero and ``_VETO`` for anything impossible -- so the
    # ``> 0`` guard the old body carried is now part of the normalisation
    # rather than a second, separately-maintained copy of it. The check is
    # against the *speed*, not the distance, and it runs only after the
    # duration check above has established a usable divisor.
    if distance_m is not _ABSENT:
        if distance_m / duration_s > _RESTING_MAX_MEAN_SPEED_MS:
            return None

    return duration_s


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


def _gate_a_beatless_resting_capture(session: Session, rr_intervals: list[RRInterval]) -> None:
    """Row 3, on the only path that can actually reach it.

    ``rr_valid_fraction`` is ``None`` exactly when the beat stream is empty --
    that is ``pipeline.py``'s rule -- and an empty beat stream is precisely
    what keeps a file out of ``_classify_tier_1``. So the "beat stream is
    empty, so ``rr_valid_fraction`` is null" row of the scenario outline can
    only be reported here: on a file whose duration/intensity profile *is* a
    resting capture but which recorded no beats -- a strap that dropped out,
    or a capture started before the sensor paired.

    **Runs after the Tier-2 branch, and only if Tier 2 declined.** Both Health
    Snapshot fixtures are zero-beat, ~120 s, low-heart-rate files -- the same
    profile -- and they are perfectly good Tier-2 readings. Flagging them as
    failed captures, or running before ``_classify_tier_2`` and pre-empting
    them, would destroy a working reading; ``activity_tag`` being set is the
    signal that Tier 2 recognised the file.

    No ``activity_tag`` is written here. The file yielded nothing and was
    claimed by no tier, so tagging it ``resting_hrv_check`` would assert a
    classification the gates have just declined to make.
    """
    if rr_intervals or session.activity_tag is not None:
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
    if resting_capture_override:
        return True

    sport_profile_name = _provenance(session).get("sport_profile_name")
    if not isinstance(sport_profile_name, str):
        return False

    return sport_profile_name in (profile_names or ())


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

    The predicate, as amended 2026-09-06, landed additively by T063 and
    completed by T069::

        tier1 := rr_intervals is non-empty                        # beats
             AND _resting_profile_duration(session) is not None   # the vetoes
             AND _declared(...)                                   # the authoriser

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
    scenario behind it.

    *Vetoes before the declaration decides refusal.* ``beats AND declared AND
    NOT vetoed`` reads naturally as beats -> declared -> vetoed, under which an
    **undeclared** file never reaches the veto evaluation at all. T064's
    provenance note needs the opposite: it fires only for a file that has beats
    and passes every veto but is not declared, so it must be able to tell
    ``strap_hrv_sample_run.fit`` (clean, undeclared -> note) from
    ``strap_run_hrv.fit`` (6000 s, 140 bpm -> no note). Evaluating
    beats -> vetoes -> declaration is what lets T064 read the answer already
    computed here instead of calling ``_resting_profile_duration`` a second
    time -- and a duplicated predicate is exactly the drift the note below
    records having already happened once in this function.

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
    value. A stream of a single beat -- a strap that paired and then
    dropped -- has zero contributing pairs and no statistic, yet
    ``valid_fraction`` on one unflagged beat is ``1.0`` and clears the
    0.80 gate; stamping ``chest_strap_raw`` on it would assert a
    completed chest-strap reading carrying no number and no explanation.
    ``0.0`` is *not* that case: it is a genuine measurement of zero
    variability, which is why the test is ``is None`` and never
    falsiness.
    """
    # 1. Beats, first and normatively so -- see the docstring. A beatless file
    #    must fall through to Tier 2 rather than be claimed and answered here.
    if not rr_intervals:
        return False

    # 2. The vetoes, before the declaration is consulted: a file that
    #    contradicts its own declaration is refused whichever route declared
    #    it, and T064 needs this answer computed once, here.
    duration_s = _resting_profile_duration(session)
    if duration_s is None:
        return False

    # 3. The declaration -- the athlete saying this file was *meant* as a
    #    measurement, and since T069 the **only** thing that can authorise a
    #    Tier-1 route. There is no fall-through arm here and there must never be
    #    one again: the pre-amendment predicate is exactly the veto evaluation
    #    two lines above, so an ``or`` here would make the declaration a no-op
    #    and restore ``IDEA-010`` verbatim.
    if not _declared(session, profile_names, resting_capture_override):
        return False

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

    # ``is None`` above, never falsiness: ``0.0`` is a genuine measurement of
    # zero beat-to-beat variability and takes the success path, the same
    # ``None``/``0.0`` distinction ``models.Session.rr_valid_fraction``
    # documents and ``rmssd.resting_rmssd`` was built around.
    session.activity_tag = _ACTIVITY_TAG_RESTING_HRV_CHECK
    session.hrv_source_tier = _TIER_CHEST_STRAP_RAW
    session.rr_source = _RR_SOURCE_CHEST_STRAP
    # ``rmssd_precomputed`` is left ``None`` on purpose -- see
    # ``_PROVENANCE_COMPUTED_RMSSD``.
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
    session.hrv_source_tier = _TIER_HEALTH_SNAPSHOT
    session.rr_source = _RR_SOURCE_HEALTH_SNAPSHOT
