"""T041/T069: Tier 1 -- the chest-strap resting capture (F004 ref doc §2).

**T069 removed the inference arm.** Nothing about a file's shape can authorise a
Tier-1 route any more. The three profile rules ratified 2026-09-05 keep their
exact thresholds and citations and have lost their vote: they can only *veto*.
What routes is the athlete's own declaration::

    tier1 := beats present                                        # R4, FIRST
         AND no veto fires                                        # the demoted rules
         AND declared                                             # the only authoriser

    beats present := rr_intervals is non-empty
                  OR an ``hrv`` (#78) message is present          # R4 (T066)

    declared := session.sport_profile_name is in the configured
                    ``resting_hrv_profile_names``
                OR the upload carried an explicit resting-capture override

    vetoed  := any summary field is present-but-unparseable or structurally
                    impossible
            OR NOT (duration_s present AND 0 < duration_s <= 300)
            OR NOT (avg_heart_rate present AND avg_heart_rate <= 100)
            OR (distance_m present AND distance_m / duration_s > 1.0)

**The beats gate reads the messages, not the reconstructed stream** -- resolution
**R4**, landed by T066. A declared file whose ``hrv`` messages all reconstruct to
**zero** beats therefore *claims* Tier 1 and is answered by
``hrv_capture_no_beats``; it does not fall through to Tier 2. See
``_hrv_messages_present`` for the full argument and for R4's revisit condition,
and ``_classify_tier_1`` for why "beats present" is still evaluated first.

**A capture the athlete never declared does not route, however restful it looks.**
That is the observable consequence of T069 and the reason this module was
rewritten rather than extended: before it, every routing assertion here passed on
the *inference* arm, so not one of them could tell the two rules apart. They can
now, because every capture that routes below says out loud why it routes -- the
fixtures are built by ``declared_capture``, its mirror ``undeclared_capture``
exists for the negative class, and ``_PROFILE_CONTRACT`` is driven across both.

The veto set's own history is preserved below, because its reasoning still
constrains the rule even though its role has changed. It was amended 2026-09-06
after Stage-0 review found the original OR shape routed a present-and-zero
distance as stillness; again the same day after goal verification found that a
present-and-zero distance was *still* satisfying the conjunction's presence test
on its own; a third time by the sprint-003 critic pass, which observed that the
previous fix had drawn its line at a **sentinel** (``distance_m > 0``) rather than
at informativeness -- 5 m over 240 s is GPS jitter and routed unflagged with no
heart rate at all; and a fourth time by T061, which stated the ``session.summary``
reading convention once instead of deciding it per field, so a
present-but-unparseable or structurally impossible value is a **veto** rather than
an absence.

"Present" throughout means **present and informative**: a key absent from the
summary and a key holding exactly zero are both "nothing reported" (and a FIT
field declared with the invalid sentinel is the former, because
``mapping._build_summary`` strips ``None``).

**A heart rate is required; a distance can only veto.** No threshold on the
distance could have closed the gap: ``strap_hrv_sample_run.fit`` is 108.21 m over 150.797 s of
*pure GPS drift*, so 5 m over 240 s is the same observation at a smaller magnitude
and any cut between them would be invented. A distance below walking pace is the
absence of counter-evidence, never evidence -- a stationary maximal effort on an
erg or a trainer reads identically to lying still. Only a heart rate separates the
two, so it must be present and must agree; the distance keeps its veto and loses
its vote. Neither real capture is affected -- ``strap_hrv_sample_run.fit`` carries
``avg_heart_rate = 60`` and ``strap_hrv_capture.fit`` 64 -- and nor is the genuine
indoor waking capture (``0.0`` m at 55 bpm). The whole rule is pinned row by
row by ``test_the_resting_profile_contract``.

**A file with more than one ``session`` message is refused outright**, before
either tier. ``mapping.to_canonical`` maps the *first* session; RR reconstruction
walks the *whole file*; so the summary this discriminator reads and the beats it
would compute from describe different spans. See the multi-session section at the
foot of this module.

**Raw-RR presence alone must never route**, and that is the single bright line
the reference document draws in boldface. ``dev_fields_run.fit`` is an ordinary
52-minute run carrying 3127 ``hrv`` messages / 7220 beats at ``rr_valid_fraction``
0.998; a "beats present -> Tier 1" rule converts it into a resting-HRV reading and
poisons E003's readiness trend -- the §2.2.3 prohibition this guard enforces.

**The two real files that isolate the declaration, measured with fitdecode (T057):**

* ``strap_hrv_capture.fit`` -- the **declared positive**. 149 ``hrv`` messages / 165
  beats, ``total_timer_time`` 150.476 s, ``total_distance`` **0.0**,
  ``avg_heart_rate`` 64, ``sport`` generic, ``sport_profile_name``
  **``'HRV Snapshot'``** -- the athlete's own custom FR945 LTE profile, which is the
  declaration the amendment routes on.
* ``strap_hrv_sample_run.fit`` -- the **undeclared negative**, and the file that
  makes T069 observable. 149 ``hrv`` messages / 156 beats, ``total_timer_time``
  150.797 s, ``total_distance`` 108.21 m (mean 0.718 m/s, pure GPS drift),
  ``avg_heart_rate`` 60, ``sport`` running (raw 1), ``sport_profile_name``
  **``'Run'``**. It is a genuine resting capture, recorded strap-paired and lying
  still on the running activity profile, and it passes **every veto**: it is
  refused for one reason only, that the athlete never declared it. Until T069 it
  was this module's Tier-1 *positive*. Do not rename it -- the odd name is the
  discriminator problem itself, and a rename would touch every reference.

``strap_cool_down_walk.fit`` is deliberately **not** used as a pin anywhere here.
It is refused three ways over (1.0356 m/s, 118.874 s, undeclared), so an assertion
that it does not route would pass with any one of those rules deleted --
``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``, using
this exact file. It is evidence, not a check.

**The heart-rate arm is not decoration.** Without it, a file with no ``distance_m``
reduces the predicate to "beats present AND duration <= 300 s", and a chest-strap-paired
4-minute indoor-trainer FTP test, rowing-erg piece or treadmill interval rep all satisfy
that -- feeding a *maximal effort* into the readiness ladder as rest. The allowance for
missing distance exists so an indoor waking capture still routes; the heart-rate arm is
what stops that allowance from swallowing every GPS-less hard effort. No fixture in the
corpus is a GPS-less capture, so that class is covered synthetically here -- it is
threshold logic over already-mapped summary values, not fitdecode parsing behaviour, so
``.claude/rules/project-testing.md``'s real-fixture requirement does not bite.

**No usable intensity signal means no route.** A distance absent or zero *and* an
average heart rate absent or zero leaves nothing to discriminate on, and the conservative
outcome is no reading.

**Corruption is refused, not ignored.** A negative value is impossible in an unsigned FIT
field and a ``tuple``, ``str`` or ``bool`` cannot be compared at all; both mean the file
is wrong, and since T061 both **veto** rather than reading as absent -- an absence lets
another arm satisfy the predicate alone, which is how ``-500 m`` used to route on a
55 bpm heart rate. See the reading-convention section below.

**``rmssd_precomputed`` stays ``None`` on Tier 1.** Per §2.2.3 it is "populated only for
the numeric wrist tiers"; the Tier-1 reading *is* the computed value, and mixing the two
would leave E003 unable to tell which tier produced the number it is reading.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser, hrv_classification, mapping, rr_reconstruction
from runcoach_api.ingestion.rmssd import resting_rmssd
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The athlete's own custom FR945 LTE activity profile. Garmin's built-in
# 'Health Snapshot' is deliberately never used as a Tier-1 declaration: it already
# routes via Tier 2's numeric ``sport == 60`` identity, and listing it would give
# one file two routes into the same decision.
DECLARED_PROFILE = "HRV Snapshot"

# The Tier-1 **positive**, recorded on that profile: 149 ``hrv`` messages / 165
# beats, 150.476 s, 0.0 m, avg HR 64, no ``rmssd_hrv``. It replaced
# ``strap_hrv_sample_run.fit`` as this module's positive control at T069.
DECLARED_FIXTURE = "strap_hrv_capture.fit"

# The corpus's **undeclared negative** -- the file T069 made observable. A genuine
# resting capture on the 'Run' profile: 156 beats, 150.797 s, 108.21 m (0.718 m/s),
# avg HR 60. It passes every veto and is refused solely because it was never
# declared, which is what no synthetic and no other fixture can prove. Do not
# rename it: the odd name *is* the discriminator problem this module solves.
UNDECLARED_FIXTURE = "strap_hrv_sample_run.fit"

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
#
# ``declared_capture`` and ``undeclared_capture`` below wrap the pair, and the
# **name at the call site is the point**. T069's task file forbids defaulting a
# declaration inside ``conftest._synthetic``/``_classified``, because a global
# default makes a regression on the undeclared path -- the direction this
# amendment introduces -- invisible in every suite at once. These two are the
# opposite of that: module-local, mutually exclusive, and each call site says
# which one it is, so a row that routes visibly says *why* it routes.


def _beats(count: int = 8) -> list[RRInterval]:
    """A short unflagged beat stream: enough to be non-empty and to yield an rMSSD."""
    return [
        RRInterval(seq=i, rr_ms=1000.0 + (20.0 if i % 2 else 0.0), rr_source="chest_strap_ecg")
        for i in range(count)
    ]


@pytest.fixture
def declared_capture(synthetic, classified):
    """``declared_capture(rr_intervals=..., **session_extra)`` -- a synthetic
    capture the athlete **declared**, classified.

    Both halves of the declaration are spelled here once: the session message
    carries ``sport_profile_name = 'HRV Snapshot'`` (through the real
    ``mapping.to_canonical``, so the name reaches ``context.provenance`` the way a
    real file's does), and the same name is passed to ``classify`` as the
    configured ``resting_hrv_profile_names`` list.

    Every routing assertion in this module goes through it, because since T069 a
    Tier-1 route has exactly one authoriser and a test that routed without naming
    it would be asserting an outcome it cannot explain.
    """

    def _declared_capture(rr_intervals=None, **session_extra):
        return classified(
            synthetic(sport_profile_name=DECLARED_PROFILE, **session_extra),
            rr_intervals=rr_intervals,
            profile_names=[DECLARED_PROFILE],
        )

    return _declared_capture


@pytest.fixture
def undeclared_capture(synthetic, classified):
    """The mirror: the identical file on the ``'Run'`` profile, with the same
    configured list.

    ``'Run'`` rather than "no profile name at all" on purpose -- it is what every
    non-snapshot file in the corpus actually reads, so the undeclared rows here
    are the real population rather than a degenerate one. The absent-name and
    non-``str`` forms are enumerated in ``test_resting_hrv_declaration.py``.
    """

    def _undeclared_capture(rr_intervals=None, **session_extra):
        return classified(
            synthetic(sport_profile_name="Run", **session_extra),
            rr_intervals=rr_intervals,
            profile_names=[DECLARED_PROFILE],
        )

    return _undeclared_capture


def _classify_fixture(filename: str, profile_names=None, resting_capture_override=False):
    """Decode a real fixture and drive the full mapping + reconstruction + classify path.

    ``profile_names`` defaults to "nothing is declared", which is the posture the
    autouse ``isolated_data_dir`` config installs and the one every negative below
    wants. A caller expecting a route passes the name explicitly.
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
    return session, rr_intervals


# ---------------------------------------------------------------------------
# The declared positive -- a real declared chest-strap capture routes to Tier 1
# ---------------------------------------------------------------------------


def test_the_declared_fixture_round_trips_as_a_tier_1_reading(declared_config, ingest) -> None:
    """F004 @must: "A chest-strap resting capture has its rMSSD computed from the
    raw beats", and the amendment's "A declared capture on a configured profile
    routes as Tier 1". Asserted end to end through the API, because the tag is what
    E003 reads back out of the store -- and through ``declared_config``, because
    since T069 the config list is what authorises the route."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"
    # T065. "And ``resting_rmssd_ms`` carries the rMSSD computed from the
    # artefact-filtered beats" -- asserted on the round trip rather than only in
    # memory, because the column is the one E003 queries out of SQLite. 41.52 ms
    # is the value F004's Decision Log records for this fixture's 165 beats.
    assert body["resting_rmssd_ms"] == pytest.approx(41.52, abs=0.01)
    assert body["resting_rmssd_ms"] > 0


def test_the_declared_fixture_leaves_the_device_field_null(declared_config, ingest) -> None:
    """§2.2.3: ``rmssd_precomputed`` is "populated only for the numeric wrist tiers".
    The Tier-1 reading is the *computed* value; putting it in the device-supplied
    field is the §2.4.5 violation -- E003 could no longer tell the tiers apart."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["rmssd_precomputed"] is None


def test_the_declared_fixture_still_carries_its_beat_rows(declared_config, ingest) -> None:
    """Unlike Tier 2, a Tier-1 reading has real beats and they are persisted: the
    computation is the system's own, over the artefact-filtered stream."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert len(body["rr_intervals"]) == 165


def test_the_declared_fixtures_rmssd_is_computed_from_its_beats() -> None:
    """The reading is not merely tagged -- ``rmssd.resting_rmssd`` (T042, strict
    pairwise adjacency) is actually wired, and its value is recorded rather than
    discarded. ``rmssd_precomputed`` cannot carry it, so provenance does."""
    session, rr_intervals = _classify_fixture(
        DECLARED_FIXTURE, profile_names=[DECLARED_PROFILE]
    )

    expected = resting_rmssd(rr_intervals)
    assert expected is not None
    assert session.context.provenance["computed_resting_rmssd_ms"] == pytest.approx(expected)


def test_the_declared_fixture_is_not_discriminated_by_sport() -> None:
    """A custom "Other" profile decodes ``sport = 'generic'``, which is distinctive
    across today's corpus and therefore tempting -- but it is true of *any* custom
    activity, so it is corroboration and never the key. Pinned so a future reader
    does not "simplify" the declaration into a sport check that would silently
    admit every custom profile the athlete ever creates.

    The canonical value asserted here is ``'other'``, not ``'generic'``:
    ``mapping.to_canonical`` buckets the decoded ``'generic'`` into F003's
    vendor-neutral vocabulary, and this module reads the canonical ``Session``.
    Worth pinning as its own fact -- a sport-based shortcut written from the
    fixture-corpus table's raw ``'generic'`` would not even match what reaches the
    classifier, which is a second, quieter reason not to write one."""
    session, _rr = _classify_fixture(DECLARED_FIXTURE, profile_names=[DECLARED_PROFILE])

    assert session.sport == "other"
    assert session.hrv_source_tier == "chest_strap_raw"


def test_the_declared_fixture_does_not_route_without_the_declaration() -> None:
    """The control that makes every assertion above mean something.

    The identical bytes, with an empty configured list -- the posture a fresh
    install ships -- do not route. Without this row the five tests above would pass
    on any rule that happened to accept the file, which is exactly how the
    inference arm survived six waves."""
    session, rr_intervals = _classify_fixture(DECLARED_FIXTURE, profile_names=[])

    assert len(rr_intervals) == 165
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


# ---------------------------------------------------------------------------
# The undeclared negative -- T069's whole subject, on a real file
# ---------------------------------------------------------------------------
#
# ``strap_hrv_sample_run.fit`` is the only artefact in the corpus that isolates the
# declaration mechanism *alone*: a genuine resting capture, real beats, passing
# every demoted veto, refused for one reason and one reason only. Delete the
# declaration check and these rows go red; delete any veto and they stay green.


def test_an_undeclared_capture_does_not_route_however_restful_it_looks(ingest) -> None:
    """F004 @must: "An undeclared capture does not route, however restful it looks".

    End to end, on the file that was this module's Tier-1 positive until T069. It
    is 150.797 s at 0.718 m/s with an average heart rate of 60 and 156 real
    chest-strap beats -- it satisfies every rule the pre-amendment predicate had --
    and it is refused because ``'Run'`` is not a declaration."""
    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE)

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["resting_rmssd_ms"] is None
    assert body["rmssd_precomputed"] is None
    assert body["rr_source"] is None


def test_the_undeclared_capture_raises_no_quality_flag(ingest) -> None:
    """"And no quality flag is raised, because it was never recognised as a
    capture." A flag asserts a finding *about a recognised capture*; this file was
    refused, so it is not one. Scoped to F004's own ``hrv_`` flags -- F003's
    ``smart_recording`` is a finding about the record stream and is unrelated."""
    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE)

    assert [flag for flag in body["quality_flags"] if flag.startswith("hrv_")] == []


def test_the_undeclared_capture_passes_every_veto() -> None:
    """The assumption the two rows above rest on, asserted rather than believed.

    If this file tripped a veto it would be refused twice over and could not pin
    the declaration at all -- the ``strap_cool_down_walk.fit`` mistake. Driving
    ``_resting_profile_duration`` directly is what shows the veto set is silent on
    it: the refusal upstream can only be the declaration."""
    session, rr_intervals = _classify_fixture(UNDECLARED_FIXTURE)

    assert len(rr_intervals) == 156
    assert hrv_classification._resting_profile_duration(session) == pytest.approx(150.797)
    assert session.context.provenance["sport_profile_name"] == "Run"


def test_the_undeclared_capture_routes_once_the_athlete_declares_it() -> None:
    """The same bytes, declared. Together with the row above this is the whole
    mechanism in two lines: identical file, identical vetoes, opposite outcome, and
    the *only* thing that differs is what the athlete configured.

    Listing ``'Run'`` is precisely what F004 tells the athlete never to do -- a
    general-purpose activity profile restores the pre-amendment behaviour for every
    short easy activity on it. It is used here because holding the *file* constant
    is the only way to show the configured list is the variable, and the next test
    makes the same point via the override, which carries no such warning."""
    session, _rr = _classify_fixture(UNDECLARED_FIXTURE, profile_names=["Run"])

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    # T065: a reading is a reading whichever declaration route produced it, so the
    # resolved column is populated on both. Only ``_classify_tier_1``'s single
    # success point writes it, which is what makes that true by construction
    # rather than by two call sites agreeing.
    assert session.resting_rmssd_ms > 0


def test_an_empty_config_list_refuses_the_declared_fixture_end_to_end(
    declared_config, ingest
) -> None:
    """F004 @must: "An athlete who uses only Health Snapshot declares that
    explicitly" -- ``resting_hrv_profile_names = []``, through the real API.

    The end-to-end half of the control above. ``declared_config()`` with no
    arguments writes the empty list *explicitly*, which is the posture a fresh
    install ships and the one E001's no-zero-config rule requires: an athlete who
    wants no Tier-1 route says so rather than getting it by omission. The file is
    a perfectly good declared-shaped capture and still yields nothing."""
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["resting_rmssd_ms"] is None
    assert body["rmssd_precomputed"] is None


def test_an_empty_config_list_leaves_tier_2_untouched(declared_config, ingest) -> None:
    """"And Tier 2 routing is unaffected." The same empty list, a real Health
    Snapshot: Tier 2 routes on the numeric ``sport == 60`` identity and has never
    consulted a profile name, so the Tier-1 kill switch cannot reach it.

    Asserted in the same configuration as the row above rather than in the default
    one, because "unaffected" is a claim about *that* configuration."""
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, "sample_health_snapshot.fit")

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == 37


def test_the_undeclared_capture_routes_on_an_upload_time_override() -> None:
    """F004 @must: "An upload-time override declares a file the config does not
    cover" -- the second declaration route, on the one real file it exists for.
    ``strap_hrv_sample_run.fit`` was already recorded on the wrong profile, and no
    config edit can reach back and fix that."""
    session, _rr = _classify_fixture(
        UNDECLARED_FIXTURE, profile_names=[], resting_capture_override=True
    )

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    # The override route resolves the column too -- see the config-list test above.
    assert session.resting_rmssd_ms > 0


# ---------------------------------------------------------------------------
# R3 -- the trap. Raw-RR presence alone must never route.
# ---------------------------------------------------------------------------


def test_an_ordinary_run_with_7220_beats_is_never_routed(
    declared_config, ingest
) -> None:
    """F004 @must: "An ordinary run is never treated as a resting-HRV reading",
    **including its amendment clause that ``resting_rmssd_ms`` is null**.

    ``dev_fields_run.fit`` carries more raw beats than any other fixture in the
    corpus. If beat presence routed, this run would become a resting reading.

    Uploaded with its own profile name ``'Run'`` **declared**, which is the whole
    point of the row since T069: undeclared it would be refused twice over, and the
    assertion would pass with every veto deleted. Declared, the demoted rules are
    all that stand between a 52-minute run and E003's readiness trend -- and this
    is not a contrived configuration, it is exactly the mistake F004 warns the
    athlete against, because the ``hrv_undeclared_capture_candidate`` note hands
    them the string ``'Run'`` to configure."""
    declared_config("Run")

    with TestClient(app) as client:
        body = ingest(client, "dev_fields_run.fit")

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None
    assert body["rmssd_precomputed"] is None
    assert body["resting_rmssd_ms"] is None
    assert body["rr_source"] is None


@pytest.mark.parametrize("filename", NON_RESTING_FIXTURES)
def test_no_ordinary_fixture_reaches_a_tier_1_reading(filename: str) -> None:
    """The same three files, driven with ``'Run'`` declared so each is refused by
    the rule it is here to pin rather than by its profile name.

    ``sample_run.fit`` and ``wrist_ppg_run.fit`` carry zero beats, so they never
    reach the predicate at all; ``dev_fields_run.fit`` reaches it and is rejected on
    duration and mean speed alike."""
    session, _rr = _classify_fixture(filename, profile_names=["Run"])

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


def test_a_declared_ordinary_run_carrying_beats_is_still_vetoed() -> None:
    """``strap_run_hrv.fit``, the strongest form of the same row: a real 6000 s /
    19.2 km run on the ``'Run'`` profile carrying **6145** ``hrv`` messages and
    13659 beats, at avg HR 140.

    With ``'Run'`` declared it is a *declared* file with real beats, so only the
    demoted vetoes refuse it -- the duration ceiling, the speed bound and the heart
    rate all fire. This is the row that proves the vetoes did not lose their teeth
    when they lost their vote: delete them and a declared marathon becomes a
    resting-HRV reading."""
    session, rr_intervals = _classify_fixture("strap_run_hrv.fit", profile_names=["Run"])

    assert len(rr_intervals) > 6000
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


def test_a_resting_profile_with_no_beats_does_not_route(declared_capture) -> None:
    """Necessary condition: the file must carry ``hrv`` (#78) beat-to-beat arrays.
    A two-minute non-session with no beats is Tier 2's business, never Tier 1's.

    **``hrv_source_tier`` is what "does not route" means here, and since T075 it is
    the only field that can say so.** The file is declared and veto-clean, so once
    Tier 2 also declines it reaches ``_gate_a_beatless_resting_capture``, which
    recognises it and raises ``hrv_capture_no_beats`` -- and R6 tags a recognised
    capture whether or not a number came out of it. So the tag is now present and
    the *route* is still absent, which is exactly the shape of a
    recognised-but-flagged capture. ``test_resting_hrv_quality_gates.py`` owns that
    row; this one keeps its own subject, the beats precondition."""
    session = declared_capture(
        total_timer_time=150.0,
        total_distance=10.0,
        avg_heart_rate=55,
        rr_intervals=[],
    )

    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert session.resting_rmssd_ms is None
    assert session.activity_tag == "resting_hrv_check"
    assert "hrv_capture_no_beats" in session.quality_flags


# ---------------------------------------------------------------------------
# The duration arm -- 300 s, the upper bound of §2.4.5's "2-5 minute" protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duration", [150.797, 300.0])
def test_a_capture_within_the_protocol_window_routes(
    duration: float,
    declared_capture,
) -> None:
    """300 s is a citation, not an invented number: the upper bound of §2.4.5's
    stated "2-5 minute resting measurement" protocol. The boundary itself routes."""
    session = declared_capture(
        total_timer_time=duration,
        total_distance=100.0,
        avg_heart_rate=58,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_capture_longer_than_the_protocol_window_does_not_route(
    declared_capture,
) -> None:
    """Not widened to 600 s "for margin": any value between 150.8 and 3117 separates
    the two fixtures, and only the citable one may be chosen."""
    session = declared_capture(
        total_timer_time=300.001,
        total_distance=10.0,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


def test_a_capture_with_no_duration_does_not_route_and_does_not_raise(
    declared_capture,
) -> None:
    """``_build_summary`` strips its ``None`` values, so a file with no
    ``total_timer_time`` has **no** ``duration_s`` key. Subscripting would be a 500
    on a perfectly valid upload."""
    session = declared_capture(total_distance=10.0, avg_heart_rate=55, rr_intervals=_beats())

    assert session.summary is not None and "duration_s" not in session.summary
    assert session.hrv_source_tier is None


def test_a_zero_duration_capture_does_not_divide_by_zero(declared_capture) -> None:
    """A degenerate file can carry ``total_timer_time`` 0. The speed must be computed
    only after the duration check has established a usable divisor."""
    session = declared_capture(
        total_timer_time=0.0,
        total_distance=10.0,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# The distance arm -- 1.0 m/s, below walking pace
# ---------------------------------------------------------------------------


def test_gps_drift_below_walking_pace_routes(declared_capture) -> None:
    """``strap_hrv_sample_run.fit``'s own profile, declared: 108.21 m over 150.797 s =
    0.718 m/s, which is drift, not travel. The real file is refused for want of a
    declaration; the synthetic here carries one, so the speed bound is what decides."""
    session = declared_capture(
        total_timer_time=150.797,
        total_distance=108.21,
        avg_heart_rate=60,
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
    declared_capture,
) -> None:
    """1.0 m/s is a **spec-introduced implementation default** (no direct citation),
    flagged as such in the module. The first row is the first step past the inclusive
    bound; the last is ``dev_fields_run.fit``'s own 3.215 m/s profile."""
    session = declared_capture(
        total_timer_time=duration,
        total_distance=distance,
        avg_heart_rate=58,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None
    assert session.activity_tag is None


def test_a_present_distance_above_walking_pace_vetoes_regardless_of_heart_rate(
    declared_capture,
) -> None:
    """A capture that genuinely moved is not rescued by a low average heart rate.

    Under the conjunction rule every present signal must agree, so the distance
    veto stands on its own — this is the direction that was always correct, and
    it is unchanged by the 2026-09-06 amendment."""
    session = declared_capture(
        total_timer_time=200.0,
        total_distance=600.0,
        avg_heart_rate=52,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


def test_a_boundary_speed_of_exactly_one_metre_per_second_routes(declared_capture) -> None:
    """The bound is inclusive: ``<= 1.0``.

    **Amended 2026-09-06 (sprint-003 critic pass).** This capture used to carry no
    ``avg_heart_rate`` at all, and its passing was the incidental pin on exactly the
    behaviour that pass removed: a distance arm routing a capture on its own. A
    distance can now only *veto*, never corroborate, so an HR-less file cannot route
    whatever its distance -- and the test could no longer express its own subject,
    which is the inclusivity of the ``<= 1.0`` bound. A corroborating 60 bpm is
    supplied so that bound is still what decides the outcome; the HR-less profile it
    used to carry is now pinned deliberately, and in the opposite direction, as
    ``walking-pace-no-heart-rate`` in ``_PROFILE_CONTRACT``."""
    session = declared_capture(
        total_timer_time=200.0,
        total_distance=200.0,
        avg_heart_rate=60,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"


# ---------------------------------------------------------------------------
# The heart-rate arm -- the GPS-less fallback, and the false-positive class
# ---------------------------------------------------------------------------


def test_a_gps_less_hard_effort_does_not_route(declared_capture) -> None:
    """**The case the heart-rate arm exists for**, and the one a fixture-only suite
    would miss entirely: a 240 s chest-strap-paired indoor effort with no
    ``distance_m`` and an average heart rate of 165. Without this arm the predicate
    collapses to "beats present AND duration <= 300 s", and an indoor-trainer FTP
    test, rowing-erg piece or treadmill interval rep would feed a *maximal effort*
    into the readiness ladder as rest."""
    session = declared_capture(
        total_timer_time=240.0,
        avg_heart_rate=165,
        rr_intervals=_beats(),
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None


def test_a_gps_less_resting_capture_routes(declared_capture) -> None:
    """The allowance for missing distance is what lets an indoor waking capture
    route at all: 180 s, no ``distance_m``, average heart rate 58."""
    session = declared_capture(total_timer_time=180.0, avg_heart_rate=58, rr_intervals=_beats())

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rr_source == "chest_strap_ecg"
    assert session.rmssd_precomputed is None
    assert session.resting_rmssd_ms > 0


@pytest.mark.parametrize("avg_hr", [58, 100])
def test_the_heart_rate_bound_is_inclusive(avg_hr: int, declared_capture) -> None:
    """100 bpm is a spec-introduced default chosen for **separation, not precision**:
    ``strap_hrv_sample_run.fit`` is 60 and any short hard effort is 150+, leaving generous
    headroom for a stressed or unwell resting morning."""
    session = declared_capture(
        total_timer_time=180.0,
        avg_heart_rate=avg_hr,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_heart_rate_above_the_bound_does_not_route(declared_capture) -> None:
    session = declared_capture(
        total_timer_time=180.0,
        avg_heart_rate=101,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


def test_no_intensity_signal_at_all_does_not_route(declared_capture) -> None:
    """Distance absent *and* average heart rate absent leaves nothing to discriminate
    on. The conservative outcome is no reading -- never a route on beats and duration
    alone, which is the rule the whole reference document is written around."""
    session = declared_capture(total_timer_time=180.0, rr_intervals=_beats())

    assert session.summary is not None
    assert "distance_m" not in session.summary
    assert "avg_heart_rate" not in session.summary
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None


# ---------------------------------------------------------------------------
# Branch ordering -- Tier 1 is inserted ahead of Tier 2 (T039 must not regress)
# ---------------------------------------------------------------------------


def test_tier_1_is_reached_before_the_tier_2_branch(declared_capture) -> None:
    """§2.4.5 orders the hierarchy highest-fidelity-first, so a capture carrying both
    raw beats and a device ``rmssd_hrv`` resolves to Tier 1 and leaves
    ``rmssd_precomputed`` null. T045 proves the full precedence contract, including
    where the unused device value is recorded; this only pins the branch order."""
    session = declared_capture(
        sport=60,
        total_timer_time=180.0,
        avg_heart_rate=58,
        rmssd_hrv=42,
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
    declared_capture,
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
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=0.0,
        avg_heart_rate=165,
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


def test_a_present_zero_distance_resting_capture_still_routes(declared_capture) -> None:
    """The twin, and the reason the fix is an AND rather than a heart-rate-only rule: a
    genuine indoor morning capture also logs ``total_distance = 0.0``. It agrees with
    both arms -- 0.0 m/s and 55 bpm -- so it must still produce a Tier-1 reading."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=0.0,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    assert session.activity_tag == "resting_hrv_check"
    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.rr_source == "chest_strap_ecg"
    assert session.rmssd_precomputed is None
    assert session.resting_rmssd_ms > 0


def test_a_moving_capture_with_a_resting_heart_rate_still_does_not_route(
    declared_capture,
) -> None:
    """The other half of the AND, carried over unchanged from the old fallback reading:
    a low average heart rate does not rescue a capture that demonstrably moved."""
    session = declared_capture(
        total_timer_time=200.0,
        total_distance=600.0,
        avg_heart_rate=52,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier is None


# ---------------------------------------------------------------------------
# The ratified contract, as one table, on TWO axes (F004 ref doc §2; T069)
# ---------------------------------------------------------------------------

# Every row of the veto set's contract, in one place. The single-case tests
# above still carry the argument for *why* each rule exists; this table is what
# the predicate as a whole is pinned to, so a future amendment has one place to
# be argued with rather than a dozen scattered assertions to reconcile.
#
# **Provenance of the rows** (per
# ``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``, which
# asks a table to say which it is): the *veto* rows below were written alongside
# the sprint-003 fixes they pin and reconciled afterwards against T058's
# independently-authored ``spec/references/F004-declaration-contract.md``; the
# *declaration* axis added at T069 is reconciled against that same document's
# Table A. Neither is an independent oracle on its own, which is why the second
# axis exists at all -- see below.
#
# **Why two axes** (T069). Removing the inference arm made every ``False`` row
# ``False`` for a **new** reason: "undeclared", rather than the specific veto the
# row was written to isolate. Run on one axis the table would therefore stop
# discriminating between the veto rules entirely -- every negative would pass with
# all three thresholds deleted, and "a check that cannot fail is not a check". So
# each row is run twice:
#
#   * **declared** -- the file carries ``sport_profile_name = 'HRV Snapshot'`` and
#     that name is configured. The expected outcome is the row's own ``routes``
#     value, so a ``False`` row still isolates its veto and a ``True`` row still
#     proves the veto set stays silent on a clean capture.
#   * **undeclared** -- the identical summary on the ``'Run'`` profile, with the
#     same configured list. The expected outcome is **always** refusal. These are
#     the rows that pin the declaration itself: delete the declaration check and
#     every undeclared twin of a ``True`` row goes red, while the veto rows stay
#     green. The two axes fail in disjoint sets, which is what makes each one a
#     check on a different rule.
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
    # --- 2026-09-06, the critic pass: a distance never corroborates rest -----
    #
    # The zero-distance amendment drew its line at exactly ``distance_m > 0``,
    # which tests a *sentinel* rather than informativeness. Five metres over four
    # minutes is GPS jitter; it is no more evidence of stillness than ``0.0`` is.
    # And ``strap_hrv_sample_run.fit``'s own 108.21 m over 150.797 s is the same
    # observation at a larger magnitude -- the reference document calls it drift
    # in so many words -- so no threshold on the distance itself can separate the
    # two without being invented. The line is drawn on the other axis instead: a
    # present distance below walking pace is the **absence of counter-evidence,
    # never evidence**, because a stationary maximal effort -- erg, indoor
    # trainer, treadmill rep -- produces exactly the same reading. Only a heart
    # rate can tell rest from effort, so it must be present and must agree. The
    # distance keeps its veto and loses its vote.
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 5.0},
        False,
        id="gps-jitter-no-heart-rate--the-critic-pass-case",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 100.0},
        False,
        id="slow-drift-no-heart-rate",
    ),
    pytest.param(
        {"total_timer_time": 200.0, "total_distance": 200.0},
        False,
        id="walking-pace-no-heart-rate",
    ),
    pytest.param(
        {"total_timer_time": 150.797, "total_distance": 108.21},
        False,
        id="the-gate-fixture-profile-stripped-of-its-heart-rate",
    ),
    # --- T061, 2026-09-06: the ``session.summary`` reading convention --------
    #
    # Every row below is one degenerate form of one summary field, enumerated
    # per ``.claude/rules/learnings/adversarial-input-probes-are-a-task-deliverable.md``
    # rather than collected as cases surfaced. The convention, ratified in
    # ``spec/references/F004-contract-resolutions.md``:
    #
    #   key absent                      -> ABSENT   (rule 1)
    #   FIT field declared, sentinel    -> ABSENT   (rule 1 via ``_build_summary``'s
    #                                                ``None``-stripping -- R1/D-1)
    #   present, numeric, exactly zero  -> ABSENT   (rule 2; ``-0.0`` included)
    #   present, numeric, negative      -> VETO     (rule 3, structurally impossible)
    #   present, non-numeric            -> VETO     (rule 3, unparseable)
    #   present, numeric, positive      -> the value
    #
    # "Sentinel" is written here as an explicit ``None``, which is exactly what
    # ``msg.get_value(name, fallback=None)`` answers for a declared-but-invalid
    # field on the real bytes -- pinned against the corpus by
    # ``test_fixture_corpus.py`` (``rmssd_hrv`` on every fixture, and
    # ``total_distance`` on ``strap_health_snapshot_hrv.fit``). Reading such a
    # field with ``has_field()`` instead would land on the opposite answer for
    # ``distance_m``; see the sentinel rows below and R1.
    #
    # -- duration_s (``total_timer_time``) ---------------------------------
    pytest.param(
        {"total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-absent--no-key-at-all",
    ),
    pytest.param(
        {"total_timer_time": None, "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-invalid-sentinel--stripped-to-absent",
    ),
    pytest.param(
        {"total_timer_time": -0.0, "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-negative-zero--degenerate-not-impossible",
    ),
    pytest.param(
        {"total_timer_time": -1.0, "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-negative--veto-and-also-outside-the-band",
    ),
    pytest.param(
        {"total_timer_time": (150.0, 1.0), "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-tuple--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": "150", "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-string--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": True, "total_distance": 0.0, "avg_heart_rate": 55},
        False,
        id="duration-bool--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 120.0, "total_distance": 0.0, "avg_heart_rate": 55},
        True,
        id="duration-at-the-quality-gate-floor--still-a-full-reading",
    ),
    # -- avg_heart_rate -----------------------------------------------------
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": None},
        False,
        id="heart-rate-invalid-sentinel--stripped-to-absent",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 0},
        False,
        id="heart-rate-zero--nothing-reported-not-a-heart-rate-of-zero",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": -0.0},
        False,
        id="heart-rate-negative-zero--exactly-zero-so-absent",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": -5},
        False,
        id="heart-rate-negative--structurally-impossible-vetoes-R2",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": True},
        False,
        id="heart-rate-bool--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": ("55", "56")},
        False,
        id="heart-rate-tuple--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 1},
        True,
        id="heart-rate-one-bpm--no-plausibility-band-is-invented",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 100.5},
        False,
        id="heart-rate-just-over-the-ceiling",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 0.0, "avg_heart_rate": 101},
        False,
        id="heart-rate-one-bpm-over-the-ceiling",
    ),
    # -- distance_m (``total_distance``) ------------------------------------
    #
    # These are the rows where the convention is *observable*: an absent
    # distance routes on the heart rate alone, a vetoing one does not. For
    # ``duration_s`` and ``avg_heart_rate`` both readings refuse, so the
    # divergence is invisible there and shows up only here (R1).
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": None, "avg_heart_rate": 55},
        True,
        id="distance-invalid-sentinel--absent-so-the-heart-rate-decides-R1",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": -0.0, "avg_heart_rate": 55},
        True,
        id="distance-negative-zero--exactly-zero-so-absent",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": -500.0, "avg_heart_rate": 55},
        False,
        id="distance-negative--structurally-impossible-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": (10.0, 2.0), "avg_heart_rate": 55},
        False,
        id="distance-tuple--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": "108", "avg_heart_rate": 55},
        False,
        id="distance-string--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": True, "avg_heart_rate": 55},
        False,
        id="distance-bool--present-but-unparseable-vetoes",
    ),
    pytest.param(
        {"total_timer_time": 240.0, "total_distance": 5.0, "avg_heart_rate": 55},
        True,
        id="distance-gps-jitter-with-an-agreeing-heart-rate--routes",
    ),
    pytest.param(
        {"total_timer_time": 300.0, "total_distance": 300.03, "avg_heart_rate": 60},
        False,
        id="distance-a-whisker-over-walking-pace",
    ),
    pytest.param(
        {"total_distance": 100.0, "avg_heart_rate": 55},
        False,
        id="distance-present-duration-absent--no-usable-divisor",
    ),
]


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
@pytest.mark.parametrize(("session_extra", "routes"), _PROFILE_CONTRACT)
def test_the_resting_profile_contract(
    session_extra: dict,
    routes: bool,
    declared: bool,
    declared_capture,
    undeclared_capture,
) -> None:
    """The whole contract, row by row and axis by axis, through the real mapping
    path.

    **The declaration axis (T069).** A route requires the athlete to have said so,
    so the expected outcome is ``routes AND declared``: nothing on the undeclared
    half routes, however restful its summary looks. That half is the only thing in
    this module that can fail when the declaration check is deleted, and the
    ``True`` rows are the ones that do -- an undeclared 240 s / 0.0 m / 55 bpm
    capture with clean beats is the exact input the pre-amendment predicate
    accepted.

    **The veto axis.** Row 2 -- zero distance, no heart rate -- is the 2026-09-06
    amendment. Before it, ``0.0 / 240 = 0.0 <= 1.0`` satisfied "at least one
    intensity signal is present" while carrying no intensity information at all;
    the heart-rate arm was skipped as absent; and the conjunction collapsed to a
    single arm that **any** zero-distance capture satisfied unconditionally. It was
    reproduced end-to-end against the real API during the sprint-003 review by
    patching a real capture's ``session`` message: a 240 s file with 156 beats and
    no ``avg_heart_rate`` came back a full Tier-1 reading, unflagged.

    Rows 1 and 3 are the pair that constrains the fix from both sides: the same
    zero distance must not rescue a maximal effort, and must not condemn a
    genuine indoor waking capture. Only the heart-rate arm can tell those two
    apart -- which is exactly why a zero distance may not stand in for it."""
    build = declared_capture if declared else undeclared_capture
    session = build(**session_extra, rr_intervals=_beats())
    provenance = session.context.provenance if session.context else {}

    if routes and declared:
        assert session.activity_tag == "resting_hrv_check"
        assert session.hrv_source_tier == "chest_strap_raw"
        assert session.rr_source == "chest_strap_ecg"
        assert session.rmssd_precomputed is None
        assert "computed_resting_rmssd_ms" in provenance
        # T065: the resolved column tracks the route exactly, over the whole
        # matrix -- so a row that starts routing (or stops) can never take the
        # column out of step with ``hrv_source_tier``.
        assert session.resting_rmssd_ms == provenance["computed_resting_rmssd_ms"]
        assert session.resting_rmssd_ms > 0
    else:
        assert session.activity_tag is None
        assert session.hrv_source_tier is None
        assert session.rr_source is None
        assert "computed_resting_rmssd_ms" not in provenance
        assert session.resting_rmssd_ms is None


# ---------------------------------------------------------------------------
# The ``session.summary`` reading convention (T061, closes IDEA-009)
# ---------------------------------------------------------------------------
#
# One rule, stated once, applied to every field ``_resting_profile_duration``
# reads -- rather than decided per field as each case surfaced, which is what
# produced three instances of the same shape. Ratified in
# ``spec/references/F004-contract-resolutions.md`` (R1, R2) and stated in
# ``spec/references/F004-detection-and-quality-rules.md``:
#
#   1. absent, or present-and-exactly-zero  -> nothing was reported
#   2. present-but-unparseable, or present-and-structurally-impossible
#                                           -> a **veto**, never an absence
#
# The tests below are the per-mechanism arguments; ``_PROFILE_CONTRACT``'s
# T061 section is the exhaustive enumeration.


def test_a_negative_distance_vetoes_rather_than_reading_as_absent(declared_capture) -> None:
    """**This reverses the rule this test previously asserted, deliberately.**

    Until 2026-09-06 a negative ``total_distance`` was read as *absent* -- the
    same answer ``_numeric`` gives a ``tuple`` -- and the docstring here argued
    for it at length: absent means the heart-rate arm decides, and at 55 bpm it
    agreed, so the file routed. That is now spec-contradicting and the argument
    is replaced rather than merely re-asserted, because a module carrying two
    ratified rationales for opposite answers is the drift this file has already
    had to fix once.

    The argument that replaces it is the one that was always available and was
    applied to only half the evidence: ``total_distance`` is an **unsigned** FIT
    field, so a negative value can only come from a crafted or corrupt
    definition record -- the *identical* provenance ``_numeric`` reports for a
    ``tuple``. One was ignored and the other vetoed. Evidence of corruption must
    never be silently downgraded into the "signal missing" branch, so both are
    now answered the same way: **veto**.

    Reading it literally was never on the table and is worth restating, because
    it is why this value cannot simply be compared: ``-500 / 240`` clears the
    1.0 m/s bound comfortably, so a garbage field would read as a *satisfied*
    stillness test.

    F004 Decision Log, 2026-09-06: *"Reconciling the [[IDEA-009]] convention's
    treatment of a negative distance against an unparseable one"*."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=-500.0,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    # Present in the summary -- ``_build_summary`` strips only ``None`` -- and
    # refused *because* it is present, which is the whole distinction.
    assert session.summary is not None
    assert session.summary["distance_m"] == -500.0
    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_negative_distance_vetoes_even_with_no_heart_rate(declared_capture) -> None:
    """The same input with the corroborating heart rate removed.

    It is kept as an assumption check rather than a mechanism pin: with no heart
    rate the file is refused twice over, so it would stay green with the veto
    deleted. The row above is the one that isolates the mechanism."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=-500.0,
        rr_intervals=_beats(),
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_negative_average_heart_rate_vetoes(declared_capture) -> None:
    """R2, and the finding that produced it (T058, Finding 2).

    Evaluated **as written**, both normative veto blocks *accept* ``-5``: the
    feature file's is ``avg_heart_rate absent or > 100`` and the reference
    document's is ``present AND <= 100``, and a ``-5`` is present, is not
    ``> 100``, and is ``<= 100``. The enumerated Scenario Outline lists a
    negative ``total_distance`` and three non-numeric fields, and a negative
    ``avg_heart_rate`` is not among them -- so an implementer working from the
    scenario list alone lets it straight through into a resting reading, and
    nothing in the spec makes that visible.

    It is refused here because ``avg_heart_rate`` is a **uint8** FIT field, so a
    negative value has exactly the provenance the negative distance has, and the
    convention exists precisely to stop that question being answered per field.
    The general disjunct in the feature file's ``vetoed`` block -- *"or any
    intensity signal is present but unparseable or structurally impossible"* --
    reaches it by reference; this is that reference made concrete.

    The row is otherwise clean (240 s, zero distance), so it isolates the
    heart-rate veto and nothing else: under the pre-T061 reading it **routed**."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=0.0,
        avg_heart_rate=-5,
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert session.summary["avg_heart_rate"] == -5
    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_present_and_zero_average_heart_rate_is_not_evidence_of_rest(
    declared_capture,
) -> None:
    """Rule 1 extended from ``distance_m`` to ``avg_heart_rate``, which is the
    residue ``IDEA-009`` left open and this task closes.

    A zero is a device writing that it had nothing to report, never a
    measurement of a heart rate of zero -- and a heart rate is *required* on
    every Tier-1 route, so "nothing reported" means no route. Under the previous
    reading ``0`` was a number, ``0 > 100`` was ``False``, and a capture with no
    heart-rate information at all routed as a full unflagged reading.

    Knowingly near-unreachable on Garmin hardware: an absent heart rate encodes
    as the uint8 invalid sentinel, not as ``0`` (which is why the sibling row
    ``heart-rate-invalid-sentinel`` exists and is the *reachable* one). It ships
    as a consequence of the stated convention rather than as a guard invented
    for an unproducible case -- that distinction is the reason it is worth
    shipping at all."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=0.0,
        avg_heart_rate=0,
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert session.summary["avg_heart_rate"] == 0  # present, and still not a signal
    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_an_invalid_sentinel_distance_reads_as_absent_and_still_routes(
    declared_capture,
) -> None:
    """**R1, and the trap it names.** The one row where the two candidate
    readings of the convention give *opposite* answers.

    A FIT session field is commonly **declared while carrying an invalid
    sentinel**: ``msg.has_field(name)`` is ``True`` and
    ``msg.get_value(name, fallback=None)`` is ``None``. T057 established this
    against the real corpus -- it holds for ``rmssd_hrv`` on *every* fixture
    including the Tier-1 positive, and for ``total_distance`` on
    ``strap_health_snapshot_hrv.fit``.

    The convention is scoped to ``session.summary``, whose key set is produced
    by ``mapping._build_summary``, which **strips ``None``**. A sentinel field
    therefore produces **no key at all**, and the state is ABSENT (rule 1) -- it
    never reaches the veto clause. An implementer who reaches for
    ``has_field()`` instead lands on "a value that exists and cannot be read",
    i.e. a veto, and gets the opposite behaviour on a real, common corpus state.

    For ``duration_s`` and ``avg_heart_rate`` both readings refuse the file, so
    the divergence is invisible. **For ``distance_m`` they disagree
    materially**, and this is that row: absent means the heart rate decides, and
    at 55 bpm it agrees, so the capture **routes**. Under the ``has_field()``
    reading it would be refused.

    The assertion on the key's *absence* is deliberate: asserting only the route
    would pass for the wrong reason on a file that simply had no distance."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=None,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert "distance_m" not in session.summary  # stripped, not a key holding None
    assert session.hrv_source_tier == "chest_strap_raw"


def test_negative_zero_is_exactly_zero_and_therefore_absent(declared_capture) -> None:
    """T058's Finding 11, decided explicitly rather than left to fall out.

    ``-0.0`` sits exactly on the boundary between rule 1's *degenerate* (zero)
    and rule 2's *structurally impossible* (negative). It is read as **zero, and
    therefore absent**, for two reasons that agree:

    * IEEE-754, which the FIT float encodings are, defines ``-0.0 == 0.0`` as
      true and ``-0.0 < 0`` as false. Rule 1's test is "**exactly** zero" and
      ``-0.0`` satisfies it under the only comparison the language offers.
      Rule 2's test is "negative", and ``-0.0`` is not negative: the sign bit is
      not a magnitude.
    * The provenance argument that makes a negative value a veto does not reach
      it. A negative *magnitude* on an unsigned field can only come from a
      crafted or corrupt definition record; a signed zero cannot -- it is what
      arithmetic on a scaled zero produces, and it carries exactly as much
      information as ``0.0``, which is none.

    Pinned on ``distance_m``, where the two answers differ observably: absent
    lets the heart rate decide and the capture routes; a veto would refuse it."""
    session = declared_capture(
        total_timer_time=240.0,
        total_distance=-0.0,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )

    assert session.summary is not None
    assert session.summary["distance_m"] == 0.0  # present, signed, and still nothing reported
    assert session.hrv_source_tier == "chest_strap_raw"


# ---------------------------------------------------------------------------
# Non-numeric summary values -- a veto, and never a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_extra",
    [
        {"total_timer_time": (150.0, 1.0), "total_distance": 10.0, "avg_heart_rate": 60},
        {"total_timer_time": "150", "total_distance": 10.0, "avg_heart_rate": 60},
        {"total_timer_time": 150.0, "total_distance": (10.0, 2.0), "avg_heart_rate": 60},
        {"total_timer_time": 150.0, "total_distance": "10", "avg_heart_rate": 60},
        {"total_timer_time": 150.0, "total_distance": 10.0, "avg_heart_rate": ("60", "61")},
        {"total_timer_time": 150.0, "total_distance": 10.0, "avg_heart_rate": "60"},
    ],
)
def test_a_non_numeric_summary_value_vetoes_and_is_never_a_500(
    session_extra: dict,
    declared_capture,
) -> None:
    """``fitdecode`` types a field by the **file's own declared base type**, not the
    global profile: ``reader.py`` (verified at lines 797-806 of the vendored copy)
    returns ``tuple(base_type.parse(v) for v in raw_value)`` whenever the declared
    size holds more than one element, so a crafted or corrupt definition record can
    make any of these three a ``tuple`` -- or a ``str``, from a string base type.
    Comparing one raises ``TypeError``, which ``main.py`` does not catch: a **500 on
    a malformed upload** rather than a stored session.
    ``rr_reconstruction._hrv_candidates`` already guards the identical hazard with
    ``isinstance``; this is the same convention.

    **What changed in T061 is the meaning, not the outcome.** Such a value used to
    be treated as *absent*, which meant the file lost an arm and was refused for
    want of a signal. It is now a **veto**: refused because the value is there and
    cannot be read. Every row above is therefore given an otherwise-clean profile
    with an agreeing 60 bpm heart rate and a 10 m distance -- under the old reading
    the two ``total_distance`` rows **routed**, because an unreadable distance
    simply vanished and the heart rate carried the file on its own.

    ``_numeric`` itself is unchanged, and deliberately: it is shared with
    ``_classify_tier_2``, where an unparseable ``rmssd_hrv`` genuinely does mean
    "no usable device value" (``test_resting_hrv_tier2.py``). The presence
    discrimination lives *around* it, in ``_resting_profile_duration``."""
    session = declared_capture(**session_extra, rr_intervals=_beats())

    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_boolean_summary_value_is_unparseable_and_therefore_vetoes(
    declared_capture,
) -> None:
    """``bool`` is an ``int`` subclass, so ``True <= 100`` is ``True`` and a bare
    ``isinstance(value, (int, float))`` would read a flag as an average heart rate
    of 1 bpm. ``_numeric`` answers ``None`` for it, and **that answer is now read as
    "present but unparseable", not as "absent"** -- a ``True`` in a numeric field is
    evidence something is wrong with the file in exactly the way a ``tuple`` is.

    The assertion below did not change when the rule did, and the docstring is why
    this test exists in its own right: before T061 the file was refused because a
    bool heart rate *vanished*, leaving a GPS-less capture with no intensity signal
    at all. It is now refused because the bool **vetoes**. Same outcome, different
    mechanism -- and the distance row makes the difference observable, since a bool
    distance with an agreeing heart rate used to route."""
    session = declared_capture(
        total_timer_time=180.0,
        avg_heart_rate=True,
        rr_intervals=_beats(),
    )
    assert session.hrv_source_tier is None

    routed_before = declared_capture(
        total_timer_time=240.0,
        total_distance=True,
        avg_heart_rate=55,
        rr_intervals=_beats(),
    )
    assert routed_before.summary is not None
    assert routed_before.summary["distance_m"] is True
    assert routed_before.hrv_source_tier is None


# ---------------------------------------------------------------------------
# Multi-session files -- refused, because the summary and the beats describe
# different spans (F004 ref doc §2.1, 2026-09-06 critic pass)
# ---------------------------------------------------------------------------

# The reproduction the critic pass filed, verbatim: a short easy leg followed by
# a two-hour run in one file. ``mapping.to_canonical`` builds its canonical
# ``Session`` from ``_first_of(by_name, "session")``, so the discriminator sees
# leg 1's 240 s / 150 m / 92 bpm; ``rr_reconstruction.reconstruct`` walks every
# ``hrv`` message in the file, so the rMSSD would be computed over both legs.
# Before the refusal this routed as an **unflagged** ``resting_hrv_check`` /
# ``chest_strap_raw`` reading -- a two-hour run turned into a resting-HRV
# datapoint whose number came from in-run beats, and simultaneously excluded
# from training load by its own tag.
#
# **Leg 1 is declared** (T069). Without the declaration the file would now be
# refused twice over -- once by this guard and once for being undeclared -- and an
# assertion that it does not route would pass with the guard deleted, which is "a
# check that cannot fail is not a check". Declaring it puts the guard back on its
# own: the file carries the athlete's own profile name and is still refused.
_MULTI_SESSION_REPRODUCTION = (
    {
        "total_timer_time": 240.0,
        "total_distance": 150.0,
        "avg_heart_rate": 92,
        "sport_profile_name": DECLARED_PROFILE,
    },
    {"total_timer_time": 7200.0, "total_distance": 20000.0, "avg_heart_rate": 150},
)


def test_the_multi_session_reproduction_is_not_routed_by_tier_1(
    multi_session, classified
) -> None:
    """**The gap.** Leg 1 alone satisfies every arm of the discriminator -- 240 s,
    0.625 m/s, 92 bpm -- and there is nothing in the first ``session`` message to
    say a second leg exists. The beats are the whole file's.

    F004 does not attempt to segment the file, attribute beats to a leg, or pick a
    "best" session: it refuses to route a file it cannot confidently classify.
    Multi-session support is a feature, not a review fix."""
    session = classified(
        multi_session(*_MULTI_SESSION_REPRODUCTION),
        rr_intervals=_beats(64),
        profile_names=[DECLARED_PROFILE],
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.rr_source is None
    assert session.rmssd_precomputed is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


def test_the_refusal_is_recorded_in_provenance_rather_than_left_silent(
    multi_session, classified
) -> None:
    """The refusal is **visible**, and it is visible in provenance rather than as a
    quality flag.

    Provenance is this module's established channel for "F004 saw something and
    declined to act on it" -- ``hrv_signal_disagreement`` is the precedent, and it
    raises no flag either, for the same reason spelled out in ``_classify_tier_2``:
    a quality flag asserts a finding *about a capture*, and this file has not been
    established to be one. The count is recorded because it is the whole reason for
    the refusal and is not otherwise recoverable from the stored session -- the
    canonical summary is leg 1's and says nothing about leg 2 existing."""
    session = classified(
        multi_session(*_MULTI_SESSION_REPRODUCTION),
        rr_intervals=_beats(64),
        profile_names=[DECLARED_PROFILE],
    )

    assert session.context.provenance["hrv_multi_session_unclassified"] == {
        "session_message_count": 2
    }


def test_the_refusal_raises_no_quality_flag(multi_session, classified) -> None:
    """A multi-session FIT file is **ordinary** -- every multisport and multi-leg
    activity is one. Flagging each of them ``hrv_capture_*`` would assert an HRV
    finding about files that never claimed to be HRV captures, and would put a
    permanent HRV quality flag on every triathlon upload. The existing flag
    vocabulary is all about a capture that *was* recognised (§5's gate table plus
    ``hrv_reading_unavailable``), and none of it fits a file whose identity was
    never established. No new flag is invented for it either."""
    session = classified(
        multi_session(*_MULTI_SESSION_REPRODUCTION),
        rr_intervals=_beats(64),
        profile_names=[DECLARED_PROFILE],
    )

    assert session.quality_flags == []


def test_a_beatless_multi_session_file_is_not_gated_either(
    multi_session, classified
) -> None:
    """The beatless quality gate is refused on the same grounds and by the same
    guard. ``_gate_a_beatless_resting_capture`` reports "this resting capture
    recorded no beats" -- a statement about a capture, and leg 1's profile is not
    evidence that the *file* is one."""
    session = classified(
        multi_session(*_MULTI_SESSION_REPRODUCTION),
        rr_intervals=[],
        profile_names=[DECLARED_PROFILE],
    )

    assert session.quality_flags == []
    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_multi_session_file_of_two_resting_legs_is_still_refused(
    multi_session, classified
) -> None:
    """Not a heuristic on whether the *other* legs look restful either. Two resting
    legs in one file are two readings, and F004 stores one session per file with one
    ``rmssd_precomputed``; deriving a single number over both legs' beats and
    stamping it with leg 1's start time would be the same fabrication in a
    friendlier disguise. Refuse, and let a real multi-session task decide."""
    session = classified(
        multi_session(
            {
                "total_timer_time": 150.0,
                "total_distance": 10.0,
                "avg_heart_rate": 58,
                "sport_profile_name": DECLARED_PROFILE,
            },
            {
                "total_timer_time": 150.0,
                "total_distance": 10.0,
                "avg_heart_rate": 57,
                "sport_profile_name": DECLARED_PROFILE,
            },
        ),
        rr_intervals=_beats(64),
        profile_names=[DECLARED_PROFILE],
    )

    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_a_single_session_file_carries_no_refusal_marker(declared_capture) -> None:
    """The guard is scoped to the case it is about: one ``session`` message routes
    exactly as before and gains no provenance entry. Without this the marker could
    be written unconditionally and every assertion above would still pass."""
    session = declared_capture(
        total_timer_time=150.797,
        total_distance=108.21,
        avg_heart_rate=60,
        rr_intervals=_beats(),
    )

    assert session.hrv_source_tier == "chest_strap_raw"
    assert "hrv_multi_session_unclassified" not in session.context.provenance


@pytest.mark.parametrize("filename", [DECLARED_FIXTURE, UNDECLARED_FIXTURE])
def test_the_real_captures_carry_exactly_one_session_message(filename: str) -> None:
    """The guard must not be able to reach either real capture. Asserted against
    the decoded files rather than assumed, because "how many ``session`` messages
    does a real capture carry" is precisely a fitdecode question -- and it is
    asserted for **both**, because the declared positive and the undeclared
    negative are only a matched pair if this holds of each."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())

    assert sum(1 for m in messages if m.name == "session") == 1


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
        (float("nan"), "nan"),
    ],
)
def test_a_non_finite_beat_does_not_reach_the_tier_1_success_point(
    value: float, label: str, declared_capture
) -> None:
    """A non-finite ``rr_ms`` must not become a Tier-1 reading.

    The mirror of ``test_a_non_finite_device_value_is_treated_as_absent`` on the
    other tier. ``_numeric`` closed the Tier-2 hole, but it is not on this path:
    Tier 1's value comes from ``rmssd.resting_rmssd`` over the beat stream, and
    its gate is the same *comparison* shape -- ``inf <= 0`` and ``nan <= 0`` are
    both ``False``, so both cleared it and reached the single success point.

    All three forms poison the walk rather than one of them: a difference taken
    against ``-inf`` squares to ``inf`` just as one against ``inf`` does, so the
    sign accident that saved ``-inf`` on Tier 2 does not save it here.

    The consequences are the two the amendment published a promise about:

    * ``inf`` is stored as ``resting_rmssd_ms``, and ``ln(inf)`` is ``inf`` --
      E003's rolling trend and SWC band are poisoned with no error raised.
    * ``nan`` is stored by SQLite as ``NULL`` while ``hrv_source_tier`` stays
      set, which is precisely the shape T076's amendment-window predicate
      selects -- so a genuine post-amendment reading is misfiled as a
      pre-amendment inference-era row. It also violates T077's scoped invariant
      directly: a non-null tier that does *not* imply a positive column.

    ``_out_of_band`` does not save this. It flags ``inf`` but returns ``False``
    for ``nan`` (a NaN comparison is false in both directions), so a NaN beat is
    never marked an artefact and contributes to the walk.

    The fix treats a non-finite beat the way the module already treats a null
    one -- **non-contributing, not fatal** -- so the surrounding beats still
    yield a reading rather than the whole capture being discarded for one bad
    value.
    """
    beats = _beats(8)
    beats[3] = RRInterval(seq=3, rr_ms=value, rr_source="chest_strap_ecg")

    session = declared_capture(
        total_timer_time=240.0,
        total_distance=100.0,
        avg_heart_rate=58,
        rr_intervals=beats,
    )

    assert session.hrv_source_tier == "chest_strap_raw"
    assert session.resting_rmssd_ms is not None
    assert math.isfinite(session.resting_rmssd_ms), (
        f"{label} reached the success point as {session.resting_rmssd_ms!r}"
    )
    assert session.resting_rmssd_ms > 0


@pytest.mark.parametrize(
    "field",
    ["total_distance", "avg_heart_rate"],
)
def test_a_nan_intensity_field_does_not_route(field: str, declared_capture) -> None:
    """A `nan` in the veto set refuses the file rather than routing it.

    This is the one non-finite case that changed *routing* rather than the name
    of a refusal, and it is F004's headline failure mode reached by a new road:
    every comparison against a ``nan`` is ``False``, so before the 2026-09-07
    screen the stillness ratio (``distance_m / duration_s <= 1.0``) and the
    100 bpm ceiling **both declined**, every veto passed, and the file routed as
    a full ``chest_strap_raw`` reading with `activity_tag = resting_hrv_check`
    and an empty ``quality_flags`` -- reproduced by removing the screen and
    driving the real mapping and classifier.

    ``avg_heart_rate`` is the sharper of the two: it is the arm that exists to
    tell a resting capture from a stationary maximal effort, so a value that
    silently declines it is exactly the discriminator failure F004's negative
    class was written to prevent.
    """
    fields = {"total_timer_time": 240.0, "total_distance": 100.0, "avg_heart_rate": 58}
    fields[field] = float("nan")

    session = declared_capture(rr_intervals=_beats(), **fields)

    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert session.resting_rmssd_ms is None
