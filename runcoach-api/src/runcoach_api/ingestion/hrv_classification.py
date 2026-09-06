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
  beat-to-beat ``time`` arrays *plus* a resting discriminator drawn
  from the capture's own duration/distance/heart-rate profile. Raw-RR
  presence alone must never be the rule: ``dev_fields_run.fit`` is an
  ordinary run with 7220 beats that such a rule would convert into a
  resting reading. The discriminator was a GO-gate deliverable and was
  ratified 2026-09-05 against ``strap_hrv_sample_run.fit`` (T041,
  implemented in ``_classify_tier_1`` below).
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
discriminator has decided the file is a resting capture, so a
90-second ordinary run is never flagged; the beatless row runs after
the Tier-2 branch instead, because a Health Snapshot is a zero-beat
resting-shaped file that Tier 2 reads perfectly well.
"""

from __future__ import annotations

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

# --- the Tier-1 discriminator (ratified 2026-09-05, F004 reference doc §2) ---
#
# Fixed against the gate fixture ``strap_hrv_sample_run.fit``, measured with
# fitdecode: 149 ``hrv`` (#78) messages / 156 beats, ``total_timer_time``
# 150.797 s, ``total_distance`` 108.21 m (mean 0.718 m/s), ``sport`` running
# (raw 1), ``sub_sport`` generic, ``avg_heart_rate`` 60, no ``rmssd_hrv``.

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

# SPEC-INTRODUCED IMPLEMENTATION DEFAULT, same treatment. The GPS-less
# fallback, chosen for **separation, not precision**: the gate fixture is
# avg HR 60 and any short hard effort is 150+, which leaves generous headroom
# for a stressed or unwell resting morning.
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
HRV_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "rmssd_hrv",
        "raw_sport_value",
        "duration_s",
        "distance_m",
        "avg_heart_rate",
        "rr_valid_fraction",
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
) -> None:
    """Route one ingested file to a resting-HRV tier, or leave it alone.

    Mutates ``session`` in place and returns ``None`` -- the
    ``quality_gates.apply`` contract.

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
    """
    if _classify_tier_1(messages, session, rr_intervals):
        return None
    _classify_tier_2(messages, session)
    _gate_a_beatless_resting_capture(session, rr_intervals)
    return None


def _resting_profile_duration(session: Session) -> float | None:
    """The capture's duration if its duration/intensity profile is a resting
    one, otherwise ``None`` (F004 reference document §2)::

        duration_s is not None AND 0 < duration_s <= 300
        AND at least one intensity signal is present
        AND every intensity signal that IS present agrees:
              (distance_m present  ->  distance_m / duration_s <= 1.0)
          AND (avg_heart_rate present  ->  avg_heart_rate <= 100)

    **The two arms are an AND over the signals that exist, not a fallback
    chain, and that is a correctness fix rather than a stylistic one.**
    ``mapping._build_summary`` strips only ``None``, so an indoor session
    carrying ``total_distance = 0.0`` has a ``distance_m`` key holding ``0.0``
    -- present-and-zero, not absent. A treadmill, rowing-erg or
    indoor-trainer interval logs exactly that, and ``0.0 / 240 = 0.0 <= 1.0``
    satisfies the distance arm outright. Under a fallback reading the
    heart-rate arm was then never consulted, and a **maximal effort** routed
    as a resting capture -- the §2.2.3 prohibition the heart-rate arm exists
    to enforce, leaking through the one branch a fallback never takes.

    The direction the old wording *did* get right is preserved: a capture
    that demonstrably moved is still not rescued by a low average heart rate.
    Under an AND neither signal can rescue the other; each can only veto.

    Returning the duration rather than a bare ``bool`` is what lets the
    quality gates re-use the same reading of ``session.summary`` instead of
    re-deriving it; ``None`` is unambiguous here because a profile that
    matches always has a duration strictly greater than zero.

    All three values are read through ``_numeric``: a ``tuple`` or ``str``
    from a crafted definition record is treated as absent rather than
    compared, because comparing it is a ``TypeError`` and an unhandled 500.

    Deliberately *not* including the beats condition. Tier-1 candidacy needs
    both -- ``_classify_tier_1`` checks the beats itself -- but the beatless
    half of the same profile is what row 3 of the quality-gate outline is
    about, and that case has to stay recognisable after the Tier-2 branch has
    had its turn.

    ``session.summary`` is read with ``.get()`` throughout, never subscripted:
    ``mapping._build_summary`` strips its ``None`` values, so a file with no
    distance has **no** ``"distance_m"`` key at all -- not a key holding
    ``None``. Subscripting would turn every indoor capture into a 500 on a
    perfectly valid upload.
    """
    summary = session.summary or {}
    duration_s = _numeric(summary.get("duration_s"))
    distance_m = _numeric(summary.get("distance_m"))
    avg_heart_rate = _numeric(summary.get("avg_heart_rate"))

    # The divisor is established here, before any speed is computed: a
    # degenerate file can carry ``total_timer_time`` 0, and a duration that
    # fails this check can never reach the division below.
    if duration_s is None or not 0 < duration_s <= _RESTING_MAX_DURATION_S:
        return None

    if distance_m is None and avg_heart_rate is None:
        # No usable intensity signal at all, so there is nothing to
        # discriminate on and the conservative outcome is no reading.
        return None

    # Every signal that is present must agree the capture is at rest. A
    # present-and-zero distance is a real reading of "did not move", not a
    # missing one, so it must not stand in for the heart-rate check the way an
    # absent distance does.
    if distance_m is not None and distance_m / duration_s > _RESTING_MAX_MEAN_SPEED_MS:
        return None
    if avg_heart_rate is not None and avg_heart_rate > _RESTING_MAX_AVG_HEART_RATE_BPM:
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


def _classify_tier_1(
    messages: list[fitdecode.FitDataMessage],
    session: Session,
    rr_intervals: list[RRInterval],
) -> bool:
    """The chest-strap resting capture (F004 reference document §2).

    Returns whether the file was routed, so ``classify`` can stop before
    the Tier-2 branch -- which is how §2.4.5's highest-fidelity-first
    hierarchy is enforced: on a capture carrying both raw beats and a
    device ``rmssd_hrv``, this branch wins and the device's scalar is
    left unused. It takes ``messages`` for that reason alone: to see the
    device value it is about to ignore, so it can record it (T045).

    The predicate, ratified 2026-09-05 and fixed against
    ``strap_hrv_sample_run.fit``::

        tier1 := rr_intervals is non-empty
             AND _resting_profile_duration(session) is not None

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

    **``sport`` and ``sub_sport`` are deliberately not gated on.** The
    gate fixture was recorded strap-paired, lying still, *on the running
    activity profile* -- ``sport`` is ``running`` (raw 1) on both it and
    ``dev_fields_run.fit``, so it carries no signal at all here. That is
    also why the fixture's name reads oddly; renaming it would touch
    every reference, so the reason is documented instead.

    **Why the heart-rate arm exists.** Without it, a file with no
    ``distance_m`` reduces the predicate to "beats present AND duration
    <= 300 s", and since sport is not gated on, a chest-strap-paired
    4-minute indoor-trainer FTP test, rowing-erg piece or treadmill
    interval rep would all satisfy it -- feeding a *maximal effort* into
    the readiness ladder as rest. The allowance for a missing distance
    is needed so an indoor waking capture still routes; the heart-rate
    arm is what keeps that allowance from swallowing every GPS-less hard
    effort. It is **not** consulted only when distance is missing: a
    present-and-zero distance is what an indoor session actually logs,
    and treating it as a satisfied distance arm let that maximal effort
    straight through. See ``_resting_profile_duration``.

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
    if not rr_intervals:
        return False

    duration_s = _resting_profile_duration(session)
    if duration_s is None:
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

    The string ``"Health Snapshot"`` in ``sport.name`` /
    ``sport_profile_name`` is deliberately never matched on: it is free
    text and locale-dependent (reference document §1).

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
