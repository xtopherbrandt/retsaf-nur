"""T039: Tier 2 -- the Health Snapshot routing table (F004 reference doc §1).

Three rows, one decision table, one branch of ``hrv_classification.classify``:

| ``session.rmssd_hrv`` | raw sport | Outcome                                                |
|---|---|---|
| present (> 0)         | 60        | Resting-HRV reading, Tier 2. Both signals agree.       |
| present               | **not** 60| **Not** a reading. Fields stay ``None``; the           |
|                       |           | disagreement goes to provenance. No quality flag.      |
| absent, or ``<= 0``   | 60        | Tagged ``health_snapshot``, no reading,                |
|                       |           | ``hrv_reading_unavailable`` raised.                    |

**Both signals matter.** Routing on the capability signal (``rmssd_hrv``)
alone would let any firmware attaching it to a 90-minute run feed in-run
wrist-PPG rMSSD into E003's readiness trend -- an absolute §2.2.3 / §2.4.5
prohibition. Row 2 is that guard, and it is asserted here directly.

**Fixture note -- the reference document was stale about the second file.**
``spec/references/F004-detection-and-quality-rules.md`` §6 claimed
``strap_health_snapshot.fit`` carries "no ``rmssd_hrv``" and "yields
*nothing*", naming it the fixture for the ``hrv_reading_unavailable`` path.
Re-measured directly against the file in the tree, it carries
``rmssd_hrv = 51`` / ``sdrr_hrv = 97`` / sport 60 -- a **second** Tier-2
happy path, not a no-reading case. The doc has been corrected as part of
this task. Its real evidentiary value is unchanged: it still carries zero
``hrv`` messages despite an HRM-Pro-Plus paired over ANT+, which is what the
tier-precedence Decision Log actually leans on.

The corpus therefore has **no** real fixture for row 3, and none for row 2
either. Both are covered synthetically with ``_FakeMsg`` stand-ins, following
``test_mapping_sport_handling.py``'s pattern: this is classifier logic over
already-mapped values, not fitdecode parsing behaviour, so
``.claude/rules/project-testing.md``'s real-fixture requirement does not bite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser, hrv_classification, mapping
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

# Re-measured 2026-09-05 by decoding the single ``session`` message of each:
#   sample_health_snapshot.fit -> rmssd_hrv 37, sdrr_hrv 98, sport 60, 120.177 s
#   strap_health_snapshot.fit  -> rmssd_hrv 51, sdrr_hrv 97, sport 60, 120.171 s
SNAPSHOT_FIXTURES = {
    "sample_health_snapshot.fit": 37,
    "strap_health_snapshot.fit": 51,
}
# Files that must never route: two ordinary runs (one of them carrying 7220
# raw beats) and a wrist-PPG run with no RR carrier at all.
NON_SNAPSHOT_FIXTURES = ("dev_fields_run.fit", "sample_run.fit", "wrist_ppg_run.fit")

# ``synthetic``, ``classified`` and ``ingest`` come from ``conftest.py``,
# shared with the four other ``test_resting_hrv_*`` modules. This module's
# own copy of ``synthetic`` made ``sport`` a *required* positional; the
# shared one defaults it to ``"running"`` for the Tier-1 modules. Every call
# site here already passed ``sport`` positionally as its first argument --
# ``synthetic(60, ...)``, ``synthetic(11, ...)``, ``synthetic("running", ...)``
# -- so all of them bind exactly as before and the default is never taken.


# ---------------------------------------------------------------------------
# Row 1 -- rmssd_hrv present AND raw sport 60: a Tier-2 resting-HRV reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("filename", "expected_rmssd"), sorted(SNAPSHOT_FIXTURES.items()))
def test_health_snapshot_becomes_a_tier_2_reading(
    filename: str,
    expected_rmssd: int,
    ingest,
) -> None:
    """F004 @must: "A Health Snapshot carrying an rMSSD becomes a resting-HRV reading"."""
    with TestClient(app) as client:
        body = ingest(client, filename)

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == expected_rmssd
    assert body["rr_source"] == "health_snapshot_ppg"
    # T065. The resolved column E003 actually reads, populated from the device
    # value on this tier. ``rmssd_precomputed`` keeps its device-only audit
    # meaning; this one is the field a consumer queries without knowing the tier.
    assert body["resting_rmssd_ms"] == expected_rmssd


@pytest.mark.parametrize("filename", sorted(SNAPSHOT_FIXTURES))
def test_a_tier_2_reading_creates_no_beat_rows(filename: str, ingest) -> None:
    """§2.2.3: a precomputed scalar "bypasses the artefact filter... there are
    no beats to filter". Nothing may synthesise ``rr_intervals`` rows for it."""
    with TestClient(app) as client:
        body = ingest(client, filename)

    assert body["rr_intervals"] == []


@pytest.mark.parametrize("filename", sorted(SNAPSHOT_FIXTURES))
def test_a_routed_snapshot_raises_no_quality_flag(filename: str, ingest) -> None:
    """Row 1 produced a reading, so there is no capture failure to report."""
    with TestClient(app) as client:
        body = ingest(client, filename)

    assert "hrv_reading_unavailable" not in body["quality_flags"]


def test_the_precomputed_value_is_stored_as_the_device_gave_it(classified) -> None:
    """``rmssd_hrv`` is an **integer** on both fixtures (37, not 37.0).
    Coercing it to float breaks the equality the acceptance probe asserts."""
    messages = fit_parser.decode((FIXTURES / "sample_health_snapshot.fit").read_bytes())

    session = classified(messages)

    assert session.rmssd_precomputed == 37
    assert not isinstance(session.rmssd_precomputed, bool)
    # T065: resolved from the same uncoerced device value, not a second reading
    # of the message -- the two columns can never disagree on Tier 2.
    assert session.resting_rmssd_ms == 37
    assert not isinstance(session.resting_rmssd_ms, bool)


def test_row_1_is_reached_via_rmssd_hrv_not_the_profile_name(synthetic, classified) -> None:
    """Reference doc §7: build against ``session.rmssd_hrv``, the name
    fitdecode actually surfaces -- **not** ``RmssdAvgValue``, the Connect/SDK
    profile name the spec text uses. A synthetic snapshot carrying only the
    profile-cased name must NOT route; taking the spec name at face value
    would produce a green test over a dead code path (F003's fabricated-enum
    ``event``-carrier bug is the cited precedent)."""
    session = classified(synthetic(60, RmssdAvgValue=44))

    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    # ...and it lands on row 3 instead, because rmssd_hrv really is absent.
    assert session.activity_tag == "health_snapshot"
    assert "hrv_reading_unavailable" in session.quality_flags


def test_the_locale_dependent_sport_name_is_never_matched_on(synthetic, classified) -> None:
    """Reference doc §1: ``sport.name`` / ``sport_profile_name`` is free text
    and locale-dependent. A file calling itself "Health Snapshot" without the
    sport-60 identity signal must not route."""
    messages = synthetic("running", sport_profile_name="Health Snapshot", rmssd_hrv=44)

    session = classified(messages)

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


# ---------------------------------------------------------------------------
# Row 2 -- rmssd_hrv present, raw sport NOT 60: the safety guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sport", ["running", 1, 11])
def test_precomputed_rmssd_on_a_non_snapshot_file_is_not_routed(
    sport,
    synthetic,
    classified,
) -> None:
    """F004 @must: "A precomputed rMSSD on a non-snapshot file is not routed".

    ``"running"`` is the dangerous case the Decision Log's Stage-4.95
    correction is about: a firmware attaching ``rmssd_hrv`` to a 90-minute run
    would otherwise feed in-run wrist PPG into E003's readiness trend.
    """
    session = classified(synthetic(sport, rmssd_hrv=44, total_timer_time=5400.0))

    assert session.rmssd_precomputed is None
    assert session.resting_rmssd_ms is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert session.activity_tag is None


def test_the_signal_disagreement_is_recorded_in_provenance(synthetic, classified) -> None:
    """The audit trail must say *what* disagreed with *what*: both the
    observed ``rmssd_hrv`` and the observed raw sport value."""
    session = classified(synthetic(11, rmssd_hrv=44))

    disagreement = session.context.provenance["hrv_signal_disagreement"]
    assert disagreement["rmssd_hrv"] == 44
    assert disagreement["raw_sport_value"] == 11


def test_the_disagreement_records_a_null_raw_sport_for_a_named_sport(
    synthetic,
    classified,
) -> None:
    """``mapping.py`` only writes ``raw_sport_value`` for a sport it had to
    bucket; ``"running"`` passes through and leaves the key absent. The
    disagreement entry must still record what it observed -- ``None`` -- so
    the trail is not silently truncated on precisely the run case that
    motivated the guard."""
    session = classified(synthetic("running", rmssd_hrv=44))

    assert "raw_sport_value" not in session.context.provenance
    assert session.context.provenance["hrv_signal_disagreement"] == {
        "rmssd_hrv": 44,
        "raw_sport_value": None,
    }


def test_row_2_raises_no_quality_flag(synthetic, classified) -> None:
    """Row 2 raises no flag **of its own**, and that is materially different from
    row 3: the file is not a Health Snapshot, so there is no Tier-2 reading for
    ``hrv_reading_unavailable`` to be about. Row 3's file genuinely *is* a snapshot
    and genuinely yielded nothing.

    The first case alone could not tell that apart from "row 2 suppresses every
    flag". With no ``total_timer_time`` the summary carries no ``duration_s``, so
    the beatless resting-capture gate that runs after this branch exits before it
    can raise anything -- the assertion would hold against an implementation that
    flagged every row-2 file with a duration. The second case supplies one."""
    session = classified(synthetic(11, rmssd_hrv=44))

    assert session.summary is not None and "duration_s" not in session.summary
    assert session.quality_flags == []

    # Same row, now with a duration, so the gate downstream genuinely runs -- and
    # still finds nothing to report, because a 90-minute 18 km file is not a
    # resting-shaped capture whatever its sport says.
    moving = classified(
        synthetic(11, rmssd_hrv=44, total_timer_time=5400.0, total_distance=18000.0)
    )

    assert moving.summary["duration_s"] == 5400.0
    assert moving.quality_flags == []


def test_a_resting_shaped_row_2_file_with_no_beats_is_still_flagged_beatless(
    synthetic,
    classified,
) -> None:
    """The limit of the sentence above, pinned so the comment cannot drift back into
    claiming more than it means.

    A file that declined Tier 2 on the identity signal can still *be* a resting-shaped
    **declared** capture: 150 s, avg HR 58, no beats, on a configured profile. §5's
    third gate is about the capture's own shape -- "beat stream is empty, so
    ``rr_valid_fraction`` is null" -- and it does not ask which tier declined the file
    first. So ``hrv_capture_no_beats`` is correct here and is raised; what row 2
    withholds is ``hrv_reading_unavailable``, the flag that would assert this file was
    a snapshot whose reading came out empty.

    **The declaration became load-bearing at T066** (T058 Finding 17 / row D5): the
    beatless gate is now reachable only for a file the athlete declared, because a flag
    asserts a finding about a *recognised* capture. Before T066 this row passed
    undeclared, which meant it would have stayed green against an implementation that
    flagged every beatless file in the corpus. Its undeclared control is the row
    below.

    **T075/R6 completes that thought on the tag axis.** T066 established that the
    flag below asserts a finding about a *recognised* capture; R6 then says a
    recognised capture that yielded no number is still a resting capture, so this
    file is tagged ``resting_hrv_check``. The tag is the one that matters to E003 --
    it must **not** be ``health_snapshot``, because Tier 2 declined the file, and it
    must not be null, because a declared veto-clean capture whose strap recorded
    nothing is not training load. The undeclared control below stays null, which is
    what keeps the two states apart."""
    session = classified(
        synthetic(
            11,
            rmssd_hrv=44,
            total_timer_time=150.0,
            avg_heart_rate=58,
            sport_profile_name="HRV Snapshot",
        ),
        profile_names=["HRV Snapshot"],
    )

    assert "hrv_capture_no_beats" in session.quality_flags
    assert "hrv_reading_unavailable" not in session.quality_flags
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.activity_tag == "resting_hrv_check"


def test_an_undeclared_resting_shaped_row_2_file_is_not_flagged_beatless(
    synthetic,
    classified,
) -> None:
    """The control for the row above, and T066's half of the note/flag boundary.

    Byte-for-byte the same file, minus the declaration. Row 2 already refuses to write
    a reading; a ``hrv_capture_no_beats`` flag on top would assert a finding about a
    capture nothing has recognised -- the same reason row 2 withholds
    ``hrv_reading_unavailable`` from it, applied to the gate that runs afterwards.
    The signal disagreement is still recorded, because that is a provenance note about
    a file that was examined, which is exactly what a note is for."""
    session = classified(
        synthetic(11, rmssd_hrv=44, total_timer_time=150.0, avg_heart_rate=58)
    )

    assert session.quality_flags == []
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.activity_tag is None
    assert "hrv_signal_disagreement" in session.context.provenance


def test_the_disagreement_key_does_not_collide_with_the_t045_key(synthetic, classified) -> None:
    """T045 later writes ``unused_device_rmssd_hrv`` into the same loosely
    typed provenance dict for a different reason (Tier 1 outranking a device
    value). The two must stay distinguishable."""
    session = classified(synthetic(11, rmssd_hrv=44))

    assert "unused_device_rmssd_hrv" not in session.context.provenance


# ---------------------------------------------------------------------------
# Row 3 -- raw sport 60 with no usable value: tagged, flagged, no reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_extra",
    [{}, {"rmssd_hrv": None}, {"rmssd_hrv": 0}, {"rmssd_hrv": -5}],
)
def test_a_snapshot_without_a_usable_value_yields_no_reading(
    session_extra: dict,
    synthetic,
    classified,
) -> None:
    """F004 @must Scenario Outline rows 4-5. Non-positive values are rejected
    to close the ``ln(0)`` hazard in E003's ``ln(rMSSD)`` trend and the
    decoded-sentinel case -- deliberately without inventing a plausibility
    band, since none is citable."""
    session = classified(synthetic(60, **session_extra))

    assert session.rmssd_precomputed is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    # T065. The resolved column is the one E003 reads, so a rejected value must
    # not reach it either -- ``{"rmssd_hrv": None}`` is the corpus-real state
    # T057 decoded (the field is *declared* on every fixture and carries the FIT
    # invalid sentinel), and a presence test rather than a value test would
    # resolve that sentinel straight into the column.
    assert session.resting_rmssd_ms is None


@pytest.mark.parametrize(
    "session_extra",
    [{}, {"rmssd_hrv": None}, {"rmssd_hrv": 0}, {"rmssd_hrv": -5}],
)
def test_a_snapshot_without_a_usable_value_is_still_tagged(
    session_extra: dict,
    synthetic,
    classified,
) -> None:
    """The file genuinely *is* a Health Snapshot, so E003 must be able to
    exclude it from rTSS and the PMC as a two-minute non-session either way."""
    session = classified(synthetic(60, **session_extra))

    assert session.activity_tag == "health_snapshot"


@pytest.mark.parametrize(
    "session_extra",
    [{}, {"rmssd_hrv": None}, {"rmssd_hrv": 0}, {"rmssd_hrv": -5}],
)
def test_a_snapshot_without_a_usable_value_raises_the_flag(
    session_extra: dict,
    synthetic,
    classified,
) -> None:
    """"...rather than failing silently"."""
    session = classified(synthetic(60, **session_extra))

    assert session.quality_flags.count("hrv_reading_unavailable") == 1


def test_the_flag_is_not_duplicated_when_already_present(synthetic) -> None:
    """``quality_gates.py``'s dedup convention: a bare string literal behind an
    ``if flag not in session.quality_flags`` guard, no enum, no registry."""
    messages = synthetic(60)
    session, _records = mapping.to_canonical(messages)
    session.quality_flags.append("hrv_reading_unavailable")

    hrv_classification.classify(messages, session, [])

    assert session.quality_flags.count("hrv_reading_unavailable") == 1


def test_a_declared_but_sentinel_rmssd_hrv_never_populates_the_resolved_column(
    synthetic,
    classified,
) -> None:
    """T057 decoded the real corpus and found ``rmssd_hrv`` **declared on every
    fixture**, including files with no device value: ``msg.has_field("rmssd_hrv")``
    is ``True`` while ``get_value("rmssd_hrv")`` is ``None``.

    This is the single most dangerous state for the resolved column. An
    implementation that gated on *presence* rather than on a non-``None`` **value**
    would write the FIT invalid sentinel into the field E003 is told it may
    ``ln()``. Pinned as its own test rather than only as a parametrised row so the
    reason survives a future edit of the parameter list."""
    session = classified(synthetic(60, rmssd_hrv=None))

    assert session.resting_rmssd_ms is None
    assert session.rmssd_precomputed is None
    assert session.hrv_source_tier is None
    assert "hrv_reading_unavailable" in session.quality_flags


@pytest.mark.parametrize("device_value", [0, 0.0, -0.0, -5, -0.001])
def test_a_non_positive_device_value_never_reaches_the_resolved_column(
    device_value,
    synthetic,
    classified,
) -> None:
    """The invariant T065 exists to establish: ``resting_rmssd_ms`` is **always
    strictly positive when set**, on either tier. That is what lets E003 take
    ``ln(resting_rmssd_ms)`` on a post-amendment row without first asking which tier
    produced it. It says nothing about pre-amendment rows, which are null with a
    non-null tier -- see T077 and ``test_db_schema``'s residual-state pin.

    ``-0.0`` is here because it is a distinct float that compares equal to ``0.0``
    and is *not* caught by an ``is 0.0`` style test; ``ln(-0.0)`` is as undefined
    as ``ln(0.0)``."""
    session = classified(synthetic(60, rmssd_hrv=device_value))

    assert session.resting_rmssd_ms is None
    assert "hrv_reading_unavailable" in session.quality_flags


@pytest.mark.parametrize("device_value", [0.001, 1, 37, 51.5])
def test_a_positive_device_value_resolves_and_stays_positive(
    device_value,
    synthetic,
    classified,
) -> None:
    """The other half of the invariant. No plausibility band is invented -- a
    physiologically odd but positive value still routes, exactly as row 1 has
    always behaved -- so what is asserted is only that a set value is > 0."""
    session = classified(synthetic(60, rmssd_hrv=device_value))

    assert session.resting_rmssd_ms == device_value
    assert session.resting_rmssd_ms > 0
    assert session.hrv_source_tier == "health_snapshot"


def test_the_resolved_column_survives_the_round_trip_and_is_positive(ingest) -> None:
    """The verification step from T065's task file, driven end to end: the value
    must come back out of SQLite through ``GET /sessions/{id}``, not merely exist
    in the in-memory ``Session``. ``resting_rmssd_ms`` is a ``REAL`` column, so
    ``37`` may legitimately return as ``37.0``; equality against the number is
    asserted, never against its type."""
    with TestClient(app) as client:
        body = ingest(client, "sample_health_snapshot.fit")

    assert body["resting_rmssd_ms"] == 37
    assert body["resting_rmssd_ms"] > 0
    assert body["hrv_source_tier"] == "health_snapshot"


def test_row_3_does_not_record_a_signal_disagreement(synthetic, classified) -> None:
    """Nothing disagreed: the identity signal is present and the capability
    signal is simply absent."""
    session = classified(synthetic(60))

    assert "hrv_signal_disagreement" not in session.context.provenance


# ---------------------------------------------------------------------------
# Neither row -- ordinary files stay exactly as they are today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", NON_SNAPSHOT_FIXTURES)
def test_ordinary_files_are_not_classified_at_all(filename: str, classified) -> None:
    """F004 @must: "An ordinary run is never treated as a resting-HRV
    reading". ``dev_fields_run.fit`` carries 3127 ``hrv`` messages / 7220
    beats, which a raw-RR-presence rule would wrongly convert."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())

    session = classified(messages)

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.rr_source is None
    assert "hrv_reading_unavailable" not in session.quality_flags
    assert "hrv_signal_disagreement" not in session.context.provenance


@pytest.mark.parametrize("filename", NON_SNAPSHOT_FIXTURES)
def test_ordinary_files_still_ingest_unchanged_end_to_end(filename: str, ingest) -> None:
    with TestClient(app) as client:
        body = ingest(client, filename)

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["rmssd_precomputed"] is None
    assert body["rr_source"] is None


def test_no_row_returns_a_4xx_every_file_is_stored() -> None:
    """All three rows store the session -- F003's posture is to flag quality,
    never to refuse a valid FIT file."""
    with TestClient(app) as client:
        for filename in (*SNAPSHOT_FIXTURES, *NON_SNAPSHOT_FIXTURES):
            raw = (FIXTURES / filename).read_bytes()
            response = client.post("/sessions", files={"file": (filename, raw)})
            assert response.status_code == 201, (filename, response.text)


# ---------------------------------------------------------------------------
# The contract classify() shares with quality_gates.apply()
# ---------------------------------------------------------------------------


def test_classify_still_mutates_by_reference_and_returns_none() -> None:
    messages = fit_parser.decode((FIXTURES / "sample_health_snapshot.fit").read_bytes())
    session, _records = mapping.to_canonical(messages)

    assert hrv_classification.classify(messages, session, []) is None
    assert session.hrv_source_tier == "health_snapshot"


# ---------------------------------------------------------------------------
# A non-numeric rmssd_hrv -- a crafted definition record must not be a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [(37, 38), "37", ("37",), True, False])
def test_a_non_numeric_device_value_is_treated_as_absent(value, synthetic, classified) -> None:
    """``fitdecode`` types a field by the **file's own declared base type**, not the
    global profile: ``reader.py`` (lines 797-806 of the vendored copy) returns
    ``tuple(base_type.parse(v) for v in raw_value)`` whenever the declared size holds
    more than one element. A crafted or corrupt definition record can therefore make
    ``rmssd_hrv`` a ``tuple`` -- or a ``str``, from a string base type -- and
    ``(37, 38) <= 0`` raises ``TypeError``. ``main.py`` catches only
    ``NotAFitFileError`` / ``FitParseFailure`` / ``TooManyRecordsError`` /
    ``MissingCanonicalFieldError`` / ``DuplicateSessionError``, so it would surface as a
    **500 on a malformed upload**. ``rr_reconstruction._hrv_candidates`` guards the same
    hazard with ``isinstance`` and says so in a comment; this is that convention.

    ``bool`` is included because it is an ``int`` subclass: ``True <= 0`` is perfectly
    legal and would store ``True`` as a millisecond rMSSD.

    Treated as **absent**, which puts a sport-60 file on row 3: tagged, no reading,
    flag raised."""
    session = classified(synthetic(60, rmssd_hrv=value))

    assert session.activity_tag == "health_snapshot"
    assert session.rmssd_precomputed is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert "hrv_reading_unavailable" in session.quality_flags


@pytest.mark.parametrize("value", [(44, 45), "44"])
def test_a_non_numeric_device_value_on_a_non_snapshot_file_is_not_a_disagreement(
    value,
    synthetic,
    classified,
) -> None:
    """The row-2 half of the same guard. A value that is not a number is not a
    capability signal, so nothing disagreed with the identity signal and there is
    nothing to record -- the file is left exactly as ``mapping.py`` produced it."""
    session = classified(synthetic(11, rmssd_hrv=value))

    assert "hrv_signal_disagreement" not in session.context.provenance
    assert session.hrv_source_tier is None
    assert session.activity_tag is None


# ---------------------------------------------------------------------------
# Multi-session files -- refused here too, and for a reason of Tier 2's own
# (F004 ref doc §2.1, 2026-09-06 critic pass)
# ---------------------------------------------------------------------------


def test_a_multi_session_file_never_pairs_the_two_signals_across_messages(
    multi_session,
    classified,
) -> None:
    """**The Tier-2 hazard, which is different in kind from Tier 1's and just as
    real.** Tier 2's two signals are read from two different places:
    ``raw_sport_value`` comes from the provenance ``mapping.py`` wrote off
    ``_first_of(by_name, "session")`` -- the *first* session message -- while
    ``_session_rmssd_hrv`` scans the messages and returns the first ``rmssd_hrv``
    it finds, **skipping** a session message that does not carry one.

    On the file below those are not the same message. Leg 1 is a genuine sport-60
    Health Snapshot with no device value; leg 2 is a 90-minute run whose firmware
    attached an ``rmssd_hrv`` computed from in-run wrist PPG. The identity signal
    is taken from leg 1 and the capability signal from leg 2, they "agree", and
    the run's number is stored as a resting-HRV reading -- precisely the §2.2.3
    prohibition row 2's identity guard exists to make impossible, reached by
    crossing two messages instead of by disagreeing on one.

    So Tier 2 refuses as well. The signals are only corroborating when they come
    off the same session, and with more than one session nothing establishes that
    they do."""
    session = classified(
        multi_session(
            {"sport": 60, "total_timer_time": 120.0},
            {"sport": "running", "total_timer_time": 5400.0, "rmssd_hrv": 44},
        )
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.rr_source is None
    assert session.quality_flags == []
    assert session.context.provenance["hrv_multi_session_unclassified"] == {
        "session_message_count": 2
    }


def test_a_multi_session_file_of_two_snapshots_is_refused_too(
    multi_session, classified
) -> None:
    """Not rescued by both legs agreeing either: two snapshots in one file are two
    readings, and the canonical schema stores one ``rmssd_precomputed`` per session
    row. Picking leg 1's number and discarding leg 2's would silently drop a
    reading; F004 refuses instead and leaves the choice to a real multi-session
    task."""
    session = classified(
        multi_session(
            {"sport": 60, "total_timer_time": 120.0, "rmssd_hrv": 37},
            {"sport": 60, "total_timer_time": 120.0, "rmssd_hrv": 51},
        )
    )

    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None


def test_the_row_2_disagreement_record_is_not_written_for_a_multi_session_file(
    multi_session,
    classified,
) -> None:
    """The refusal precedes the routing table rather than sitting inside it. A
    ``hrv_signal_disagreement`` entry asserts that this file's identity and
    capability signals were compared and disagreed; on a multi-session file they
    were never comparable in the first place, so recording one would be a false
    statement about what was observed."""
    session = classified(
        multi_session(
            {"sport": "running", "total_timer_time": 5400.0, "rmssd_hrv": 44},
            {"sport": 60, "total_timer_time": 120.0},
        )
    )

    assert "hrv_signal_disagreement" not in session.context.provenance
    assert "hrv_multi_session_unclassified" in session.context.provenance


@pytest.mark.parametrize("filename", sorted(SNAPSHOT_FIXTURES))
def test_both_snapshot_fixtures_carry_exactly_one_session_message(filename: str) -> None:
    """The guard must not be able to reach either Tier-2 happy path. Asserted
    against the real decoded files rather than assumed."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())

    assert sum(1 for m in messages if m.name == "session") == 1
