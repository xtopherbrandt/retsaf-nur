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
  §2.4.5 orders the hierarchy highest-fidelity-first -- so the Tier-1
  branch runs first and short-circuits (T045 pins the full precedence
  contract).

T038 fixed only the seam -- the module, its import path, the
``classify`` signature and the unconditional call site in
``pipeline.py``. T039 added the Tier-2 branch and T041 the Tier-1
branch ahead of it; a file matching no branch is still left exactly as
``mapping.py`` produced it, which is today's behaviour unchanged.
"""

from __future__ import annotations

from typing import Any

import fitdecode

from runcoach_api.ingestion import rmssd
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

# Where the Tier-1 reading itself is recorded. ``rmssd_precomputed`` cannot
# carry it -- §2.2.3 reserves that field for a *device-supplied* scalar on the
# numeric wrist tiers, and overloading it would leave E003 unable to tell which
# tier produced the number. F004's data model defines no session column for the
# system-computed value, so the audit dict is its only home today; E003 will
# need a real column before it can read the Tier-1 statistic back out of the
# store. Deliberately distinct from T045's ``unused_device_rmssd_hrv``, which
# records the opposite thing: a device value that *lost* to this computation.
_PROVENANCE_COMPUTED_RMSSD = "computed_resting_rmssd_ms"

# Bare string literal on ``session.quality_flags``, matching
# ``quality_gates.py``'s "smart_recording" convention: no enum, no
# registry, no flags table.
_FLAG_READING_UNAVAILABLE = "hrv_reading_unavailable"

# Provenance key for row 2. Deliberately distinct from T045's
# ``unused_device_rmssd_hrv``, which records a device value that lost to
# a Tier-1 computation -- a different situation that must stay
# distinguishable in the same loosely typed dict.
_PROVENANCE_SIGNAL_DISAGREEMENT = "hrv_signal_disagreement"


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
    """
    if _classify_tier_1(session, rr_intervals):
        return None
    _classify_tier_2(messages, session)
    return None


def _classify_tier_1(session: Session, rr_intervals: list[RRInterval]) -> bool:
    """The chest-strap resting capture (F004 reference document §2).

    Returns whether the file was routed, so ``classify`` can stop before
    the Tier-2 branch.

    The predicate, ratified 2026-09-05 and fixed against
    ``strap_hrv_sample_run.fit``::

        tier1 := rr_intervals is non-empty
             AND duration_s is not None AND duration_s <= 300
             AND (
                   (distance_m present AND distance_m / duration_s <= 1.0)
                OR (distance_m absent AND avg_heart_rate present
                    AND avg_heart_rate <= 100)
                 )

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
    effort. It is a **fallback for absent distance**, not a second
    chance: a capture that demonstrably moved is not rescued by a low
    average heart rate.

    **No usable intensity signal means no route.** Distance absent *and*
    average heart rate absent leaves nothing to discriminate on, and the
    conservative outcome is no reading.

    ``session.summary`` is read with ``.get()`` throughout, never
    subscripted: ``mapping._build_summary`` strips its ``None`` values,
    so a file with no distance has **no** ``"distance_m"`` key at all --
    not a key holding ``None``. Both Health Snapshot fixtures are in
    that state, and subscripting would turn every indoor capture into a
    500 on a perfectly valid upload.
    """
    if not rr_intervals:
        return False

    summary = session.summary or {}
    duration_s = summary.get("duration_s")
    distance_m = summary.get("distance_m")
    avg_heart_rate = summary.get("avg_heart_rate")

    # The divisor is established here, before any speed is computed: a
    # degenerate file can carry ``total_timer_time`` 0, and a duration
    # that fails this check can never reach the division below.
    if duration_s is None or not 0 < duration_s <= _RESTING_MAX_DURATION_S:
        return False

    if distance_m is not None:
        at_rest = distance_m / duration_s <= _RESTING_MAX_MEAN_SPEED_MS
    elif avg_heart_rate is not None:
        at_rest = avg_heart_rate <= _RESTING_MAX_AVG_HEART_RATE_BPM
    else:
        at_rest = False

    if not at_rest:
        return False

    session.activity_tag = _ACTIVITY_TAG_RESTING_HRV_CHECK
    session.hrv_source_tier = _TIER_CHEST_STRAP_RAW
    session.rr_source = _RR_SOURCE_CHEST_STRAP
    # ``rmssd_precomputed`` is left ``None`` on purpose -- see
    # ``_PROVENANCE_COMPUTED_RMSSD``.
    computed = rmssd.resting_rmssd(rr_intervals)
    if computed is not None:
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
    """
    rmssd_hrv = _session_rmssd_hrv(messages)
    raw_sport_value = _provenance(session).get("raw_sport_value")
    is_snapshot = raw_sport_value == _SNAPSHOT_RAW_SPORT_VALUE

    if not is_snapshot:
        if rmssd_hrv is not None:
            # Row 2. No quality flag: a non-snapshot file is not a
            # resting capture at all, so it has no capture quality to
            # report -- materially different from row 3, where the file
            # *is* a snapshot and genuinely yielded nothing. Both
            # observed values are recorded so the audit trail says what
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
