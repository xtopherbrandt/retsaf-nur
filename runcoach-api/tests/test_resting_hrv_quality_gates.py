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
```

(Rows 4 and 5 -- the device-scalar rows -- are Tier 2's, owned by T039 and asserted in
``test_resting_hrv_tier2.py``.)

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
untouched. The behavioural question this raises -- whether the beatless gate should still
be reachable at all now that a declared beatless file falls through to Tier 2 -- is
**T066's**, deliberately not this module's.

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

from runcoach_api.ingestion import hrv_classification, mapping
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
    """
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
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


def test_a_zero_rmssd_is_a_reading_and_still_takes_the_success_path(
    resting,
    declared_classify,
) -> None:
    """The regression this check must not cause. ``0.0`` is a genuine measurement of zero
    beat-to-beat variability, not an absence -- the ``None``/``0.0`` distinction
    ``models.Session.rr_valid_fraction`` documents and ``rmssd.resting_rmssd`` was built
    around. ``is None`` is therefore the only correct test; a falsiness check would
    silently convert a real reading into a quality flag."""
    beats = [
        RRInterval(seq=i, rr_ms=1000.0, rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(6)
    ]
    session = declared_classify(resting(), rr_intervals=beats)

    assert FLAG_READING_UNAVAILABLE not in session.quality_flags
    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rr_source == "chest_strap_ecg"
    assert session.context.provenance["computed_resting_rmssd_ms"] == 0.0


def test_a_healthy_capture_does_not_raise_the_unavailable_flag(resting, declared_classify) -> None:
    """The negative control: the flag is raised only when the statistic is genuinely
    underivable, never on an ordinary Tier-1 reading."""
    session = declared_classify(resting(), rr_intervals=_beats())

    assert FLAG_READING_UNAVAILABLE not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"
