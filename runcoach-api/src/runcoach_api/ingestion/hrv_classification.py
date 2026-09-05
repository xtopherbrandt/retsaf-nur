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
  from the capture's own sport/duration/distance profile. Raw-RR
  presence alone must never be the rule: ``dev_fields_run.fit`` is an
  ordinary run with 7220 beats that such a rule would convert into a
  resting reading. The discriminator's values are a GO-gate deliverable
  and are fixed against the Tier-1 fixture when it lands (T041).
- **Tier 2, ``health_snapshot``** -- ``session.rmssd_hrv`` present
  *and* ``raw_sport_value == 60``, read from the provenance
  ``mapping.py`` already records. Both signals matter: capability alone
  would let any firmware attaching ``rmssd_hrv`` to a 90-minute run
  feed in-run wrist PPG into the readiness trend, an absolute §2.2.3 /
  §2.4.5 prohibition (T039, implemented below).
- **Tier 1 outranks Tier 2** on a capture carrying both, because
  §2.4.5 orders the hierarchy highest-fidelity-first (T043).

T038 fixed only the seam -- the module, its import path, the
``classify`` signature and the unconditional call site in
``pipeline.py``. T039 adds the Tier-2 branch below; a file matching no
branch is still left exactly as ``mapping.py`` produced it, which is
today's behaviour unchanged.
"""

from __future__ import annotations

from typing import Any

import fitdecode

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

    Tier 1 (``chest_strap_raw``) is not implemented yet -- T041 inserts
    it *ahead* of the Tier-2 branch below, since §2.4.5 orders the
    hierarchy highest-fidelity-first.
    """
    _classify_tier_2(messages, session)
    return None


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
