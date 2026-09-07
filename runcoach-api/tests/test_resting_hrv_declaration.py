"""T063: an explicit athlete **declaration** routes a Tier-1 resting capture.

F004's 2026-09-06 amendment stops inferring intent. A Tier-1 route now requires
the athlete to have said so -- either by naming the activity profile in
``resting_hrv_profile_names``, or by flagging the upload itself::

    tier_1_candidate = beats present          # evaluated FIRST
                       AND declared
                       AND NOT vetoed

    declared = session.sport_profile_name is in config resting_hrv_profile_names
               OR the upload carried an explicit resting-capture override

This module is the positive half of that rule. The **provenance notes** the
amendment also specifies -- ``hrv_undeclared_capture_candidate`` and
``hrv_resting_capture_override`` -- are **T064's**, deliberately: this task
lands routing and the transport seam, and T064 lands observability on the
refusal paths. Nothing here asserts a provenance note exists, and nothing here
asserts one does *not* exist, so T064 is free to add them.


The shadowing is over (T069)
============================

T063 was the **first half** of the routing inversion: the declaration arm was
``OR``-ed with the pre-amendment inference predicate. T069 deleted that arm, and
this section records what changed for this module, because it is the reason many
of the tests below no longer request a fixture that no longer exists.

**While both arms existed the declaration was provably shadowed.** The veto set
and the pre-amendment predicate are *the same rules* -- V1..V4 of the contract
table are exactly what ``_resting_profile_duration`` answers ``None`` for -- so

    (declared OR inferred) AND NOT vetoed  ==  NOT vetoed  ==  inferred

and no input existed that the declaration arm routed and the inference arm
refused. The T063 task file's suggested discriminating fixture ("declared, beats
present, distance absent, ``avg_heart_rate`` 95") did not discriminate; that was
reported as a finding (``IDEA-018``) rather than worked around, and T063 pinned
the mechanism by **perturbation** instead -- stubbing one arm and watching the
outcome hold.

**Since T069 the tests discriminate on their own.** ``_inference_authorises_tier_1``
is gone, so an undeclared capture that passes every veto simply does not route,
and every row below that asserts a refusal can now fail for the right reason.
The ``inference_stubbed`` fixture went with it. What is kept is the *other*
perturbation, which is still the only way to show a **positive** is
load-bearing rather than incidental:

* stub ``_declared`` -> a clean, declared capture stops routing. Delete the
  declaration check and this is the row that says so
  (``test_with_the_declaration_stubbed_nothing_routes``).
* ``test_the_inference_arm_is_gone`` asserts the removal directly, at the module
  level and behaviourally, so a well-meaning restoration of the fallback fails
  here and is told why.

The real-file half of the same argument lives in ``test_resting_hrv_tier1.py``:
``strap_hrv_capture.fit`` declared routes, ``strap_hrv_sample_run.fit``
undeclared does not, and the two differ in nothing else.


Reconciliation with ``spec/references/F004-declaration-contract.md`` (T058)
==========================================================================

T058 authored the declaration contract table from the spec alone, before this
implementation existed, and listed seventeen places where the spec does not
determine an answer. Six were resolved on 2026-09-06 in
``F004-contract-resolutions.md``; those resolutions **govern** and are not
re-litigated here. This section records how every OPEN row and finding that
touches T063's scope was discharged.

**Table A rows.**

===== ================================================================================
Row   Disposition here
===== ================================================================================
A1    Implemented and pinned: config match + beats + clean -> T1.
A2    Implemented and pinned: override + beats + clean -> T1. *Provenance half: T064.*
A3    **Finding 7 (OPEN).** Both routes at once. ``declared`` is an ``OR``, so it
      routes; pinned. *Which note is recorded is T064's to decide* -- this module
      asserts the route only, which is the half the pseudocode does determine.
A4    Undeclared + beats + clean -> no route. Pinned with no stub since T069; it
      needed the inference arm stubbed to mean anything before. *Note: T064.*
A5    **Finding 5 (OPEN).** Beatless config declaration -> Tier 2. Pinned end to end
      on ``sample_health_snapshot.fit``: this is the ordering trap, and it is the one
      row where getting it wrong routes a valid snapshot **nowhere**. *Whether a
      "declared but no beats" note is also written is T064's.*
A6    Override + no beats -> Tier 1 does not claim; Tier 2 still evaluated. Pinned.
      *The "could not be honoured" note is T064's.*
A7    Derived from A5+A6; both-declared + beatless -> Tier 2. Pinned.
A8    Undeclared + beatless -> Tier 2 evaluated. Pinned.
A9    Empty config list + configured-looking file -> no route. Pinned.
A10   **Finding 6 (OPEN), resolved by R3.** Empty list **plus** an override routes.
      The empty list is "no profile declares Tier 1", never a global kill switch.
      Pinned; its control (A9, empty list without an override) is what stops the
      row being vacuous now that no second arm can route either one.
A11   ``"Health Snapshot"`` does not match ``"HRV Snapshot"`` -- exact, not substring.
A12   ``"hrv snapshot"`` does not match -- case-sensitive.
A13   Trailing space does not match -- not trimmed.
A14   Absent name -> undeclared, no error.
A15   **Finding 13 (OPEN).** ``""`` -> undeclared. *Whether the note records ``null``
      or ``""`` is T064's; matching is settled here and pinned.*
A16   ``"   "`` -> undeclared, and unmatchable besides: T054's validator already
      refuses a blank config entry, which is asserted here rather than assumed.
A17   **Finding 12 (OPEN).** A non-``str`` name -- ``int``, ``bytes``, ``tuple``,
      ``bool`` -- fails the exact match and **does not veto**. D-3 is scoped to the
      three intensity signals, so a corrupt *identity* field is simply not a
      declaration. Pinned for all four types.
A18   ``[""]`` / ``["  "]`` unreachable: pinned by asserting ``AppConfig`` rejects it.
A19   Missing key -> startup validation error. T054/T060's; not re-asserted here.
===== ================================================================================

**Table B (the veto profile).** Every row's *values* are already pinned by
``test_resting_hrv_tier1.py``'s ``_PROFILE_CONTRACT``, which this module does
not duplicate. What is new at T063 is that a veto must refuse a **declared**
file on **both** routes -- the override is a claim about intent, never about the
data -- so B10 (6000 s), B20 (140 bpm), B27 (negative distance) and B35
(unparseable distance) are re-run here across the config route *and* the
override route.

**Table C / D.** C4 is the ordering trap and is pinned. C1's multi-session
refusal precedes both tiers and is unchanged by a declaration, which is pinned
here because "declared" is exactly the input someone would expect to bypass it.
Findings 8, 9, 14, 15, 16 and 17 were out of T063's scope: 8 is R6/T065, 17 is
R4 and reachable only through the quality gates, and the rest concern notes
(T064) or Tier-2 precedence. **T069 closed Finding 9** -- see the addendum
below.

**Findings 1 and 2** (the sentinel reading, and a negative ``avg_heart_rate``)
were resolved by R1/R2 and implemented by T061; this module inherits them and
adds one new instance of the same discipline -- ``sport_profile_name`` is read
through ``context.provenance``, never through ``has_field()``.

**Finding 3 / R5** -- a declared file killed by a veto records a provenance note
naming the veto. Ratified, and **not implemented here**: T064 owns the notes.
This module pins the refusal; the note is the boundary, and it is reported.

**Added by T069.** Removing the inference arm settles two more of T058's rows and
changes how the whole of Table B must be read.

* **C6 / Finding 9 -- RESOLVED, derived.** "``sport == 60`` + ``rmssd_hrv`` +
  beats, **undeclared**" was open because §3 says such a capture "resolves to
  Tier 1", which the declaration rule makes false. The derived answer is now
  forced rather than chosen: Tier 1 cannot *claim* an undeclared file, so §3's
  precedence never engages and the file routes **Tier 2** on its numeric
  identity, with ``rmssd_precomputed`` written and no ``unused_device_rmssd_hrv``
  entry -- nothing was unused by a computation that never happened. Pinned in
  ``test_resting_hrv_tier_precedence.py::test_an_undeclared_both_signals_capture_falls_to_tier_2``,
  with the argument for why it is acceptable to prefer the lower-fidelity source
  there. The *note* half of C6 (whether an undeclared-candidate note also fires)
  remains **T064's**.
* **Table B is now the declared column only.** Every B row's stated outcome
  assumes the file is declared; undeclared, every one of them is "—".
  ``_PROFILE_CONTRACT`` is therefore run on two axes (see its own comment): the
  declared axis is Table B as written, and the undeclared axis is uniformly a
  refusal. Without the second axis the table would stop discriminating between
  the veto rules entirely, since after T069 every negative is also refused for
  being undeclared.
* **B4, B32 and B34 stay flagged "over-determined -- not a pin"**, exactly as
  T058 marked them, and T069 adds no new pins on them.
  ``strap_cool_down_walk.fit`` (B32) and ``strap_run_hrv.fit`` (B34) are used
  only where a *declaration* is supplied, which removes one of the two reasons
  they were over-determined and is stated at each such call site.
* **D5 / Finding 17** is untouched here. R4 ratified "beats present", so the
  beatless row stays reachable through ``_gate_a_beatless_resting_capture``,
  which is **not** declaration-gated today: an undeclared beatless
  resting-shaped file still raises ``hrv_capture_no_beats``. That contradicts
  the recognised-capture principle and is **T066's**, by that task's own
  statement; T069 deliberately leaves it alone rather than red-flagging two
  waves early.


Adversarial probe table (``.claude/rules/learnings/adversarial-input-probes...``)
================================================================================

The inputs the declaration predicate actually reads, after mapping, are two:
``context.provenance["sport_profile_name"]`` (lifted verbatim by T056, any
type, key absent when the file carried nothing) and the boolean override. Every
degenerate form below is driven through ``mapping.to_canonical`` -> ``classify``
by the parametrised tests in this module, not asserted at the unit boundary.

===================================== ============ ==================================
``sport_profile_name`` (config ``["HRV Snapshot"]``)  declared?   why that is intended
===================================== ============ ==================================
``"HRV Snapshot"``                     yes          the exact match
``"Health Snapshot"``                  no           substring collision is the reason
                                                    matching is exact -- and it already
                                                    routes Tier 2 by ``sport == 60``
``"hrv snapshot"`` / ``"HRV SNAPSHOT"`` no          case-sensitive
``"HRV Snapshot "`` / ``" HRV Snapshot"`` no        not trimmed; "trimmed" was the
                                                    rejected option
``"HRV  Snapshot"`` (double space)     no           exact means exact
``""``                                 no           "absent or empty is simply not a
                                                    declaration"
``"   "``                              no           and no config entry can match it
``None`` (key absent)                  no           not an error, just undeclared
``"HRV Snapshöt"``                no           non-ASCII is compared like any
                                                    other string, no normalisation
``"HRV Snapshot" * 200``               no           length is not special-cased
``42`` / ``b"HRV Snapshot"`` / ``(1,)`` / ``True``  no  T058 F12: a corrupt identity
                                                    field is not a veto (D-3 is scoped
                                                    to the intensity signals) -- it
                                                    simply fails the match
===================================== ============ ==================================

And the override axis, crossed with the config axis (R3, T058 F6/F7):

===================== ========== ========= ===========================================
config list           file name  override  outcome
===================== ========== ========= ===========================================
``["HRV Snapshot"]``  match      no        declared (A1)
``["HRV Snapshot"]``  ``"Run"``  yes       declared (A2)
``["HRV Snapshot"]``  match      yes       declared -- ``OR``, not ``XOR`` (A3)
``["HRV Snapshot"]``  ``"Run"``  no        undeclared (A4)
``[]``                match      no        undeclared (A9)
``[]``                ``"Run"``  yes       **declared** -- R3, the row no scenario
                                           covered (A10)
``[]``                anything   no        undeclared
===================== ========== ========= ===========================================

Two probes were run and produced a result worth recording rather than a bare
pass:

1. A **declared multi-session file** is still refused before either tier. The
   declaration cannot reach the refusal, because the refusal precedes it -- but
   "the athlete declared it" is the most plausible reason someone would later
   move that guard, so the row is pinned rather than left to the ordering.
2. A **declared beatless file that Tier 2 also declines** routes nowhere and is
   silent. That is correct here (Tier 1 never claimed it) and is precisely
   T058's Finding 5; the observability half is T064's.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from runcoach_api import db
from runcoach_api.config import AppConfig
from runcoach_api.ingestion import hrv_classification, pipeline
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The athlete's own custom FR945 LTE activity profile, and the name that must be
# configured for its captures to route. Garmin's built-in 'Health Snapshot' is
# deliberately never offered as a config value: it already routes via Tier 2's
# numeric ``sport == 60`` identity, and listing it would give one file two
# routes into the same decision.
DECLARED_PROFILE = "HRV Snapshot"

# The Tier-1 positive, recorded on that profile: 149 ``hrv`` messages / 165
# beats, 150.476 s, 0.0 m, avg HR 64, no ``rmssd_hrv``.
DECLARED_FIXTURE = "strap_hrv_capture.fit"

# A genuine resting capture recorded on the **'Run'** profile: 149 ``hrv``
# messages / 156 beats, 150.797 s, 108.21 m (0.718 m/s), avg HR 60. The corpus's
# undeclared negative, and the only real file the upload-time override has
# anything to say about.
UNDECLARED_FIXTURE = "strap_hrv_sample_run.fit"

# A real Garmin Health Snapshot: sport 60, ``rmssd_hrv`` 37, **zero** ``hrv``
# messages. The ordering trap's fixture.
SNAPSHOT_FIXTURE = "sample_health_snapshot.fit"
SNAPSHOT_PROFILE = "Health Snapshot"
SNAPSHOT_DEVICE_RMSSD = 37

# A clean declared synthetic's profile, matching the real fixture's numbers so a
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

    Built the way ``test_resting_hrv_quality_gates.py`` builds one, so a change
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


@pytest.fixture
def declaration_stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The surviving perturbation: no file is ever declared.

    T063's ``inference_stubbed`` mirror was removed with the arm it stubbed
    (T069). This one is kept, and is now the *only* thing in the suite that can
    show a positive row is load-bearing rather than incidental -- the same
    discipline ``test_the_disjointness_assertion_actually_bites`` and the
    property suites use: the mechanism is proved by breaking it and watching the
    outcome change.
    """
    monkeypatch.setattr(hrv_classification, "_declared", lambda *a, **k: False)


def _routed(session) -> bool:
    return (
        session.activity_tag == "resting_hrv_check"
        and session.hrv_source_tier == "chest_strap_raw"
    )


# ---------------------------------------------------------------------------
# 1. the declaration routes -- and is provably the thing that routed
# ---------------------------------------------------------------------------


def test_the_declared_fixture_routes_as_a_tier_1_reading(declared_config, ingest) -> None:
    """A1, end to end, on the real file the GO/NO-GO gate recorded."""
    declared_config(DECLARED_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, DECLARED_FIXTURE)

    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"
    # "And no quality flag is raised" -- scoped to F004's own flags. The file
    # carries F003's ``smart_recording`` (it is a real Garmin capture with a
    # variable-rate record stream), which is a finding about the *recording*
    # and says nothing about the capture's HRV quality.
    assert [flag for flag in body["quality_flags"] if flag.startswith("hrv_")] == []


def test_the_declaration_routes(synthetic, classified) -> None:
    """The load-bearing assertion: declaration alone authorises the route.

    Until T069 this test requested ``inference_stubbed``, because it could not
    otherwise tell which arm had routed the capture. It needs no stub now --
    there is only one arm."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert _routed(session)


def test_with_the_declaration_stubbed_nothing_routes(
    declaration_stubbed, synthetic, classified
) -> None:
    """The assertion above could have failed -- here is it failing.

    This is T069's "delete the declaration check by hand and confirm the rows go
    red", run as a test rather than as a one-off manual step: with ``_declared``
    forced to ``False`` the clean, configured, veto-clean capture stops routing.
    Nothing else in the classifier is touched, so a restored fallback arm would
    keep this green and be caught by ``test_the_inference_arm_is_gone`` instead --
    the two rows fail in different ways on purpose."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert session.activity_tag is None
    assert session.hrv_source_tier is None


def test_an_undeclared_capture_does_not_route(synthetic, classified) -> None:
    """A4. The same clean capture, on a profile the config does not name.

    Before T069 this row needed the inference arm stubbed to mean anything --
    unstubbed, the capture routed. It is now the plain behaviour, which is the
    entire point of the amendment."""
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": "Run"}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)


def test_the_inference_arm_is_gone(synthetic, classified) -> None:
    """T069, asserted directly. This test replaces T063's
    ``test_the_inference_arm_still_routes_an_undeclared_capture_today``, which
    asserted the opposite and was written to be flipped here.

    Two assertions, deliberately of different kinds. The **structural** one --
    ``_inference_authorises_tier_1`` no longer exists on the module -- catches a
    restoration that a behavioural test could miss, because the arm is
    *provably* shadowed the instant it is ``or``-ed back in: the veto set and the
    pre-amendment predicate are the same rules, so ``(declared OR inferred) AND
    NOT vetoed == NOT vetoed`` and every other row in this file would stay green.
    The **behavioural** one is the capture that arm used to route, refused. A
    name check alone would pass against a differently-named fallback; an outcome
    check alone would pass against a dead function left in place. Together they
    say the arm is gone and stayed gone."""
    assert not hasattr(hrv_classification, "_inference_authorises_tier_1")

    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": "Run"}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)
    assert session.activity_tag is None
    assert session.hrv_source_tier is None
    assert "computed_resting_rmssd_ms" not in session.context.provenance


# ---------------------------------------------------------------------------
# 2. matching is exact and case-sensitive (A11-A17)
# ---------------------------------------------------------------------------

# Every degenerate form of the one identity field the declaration reads. Driven
# through the real mapping, so "absent" is genuinely a missing provenance key
# rather than a hand-injected None.
_NON_MATCHING_PROFILE_NAMES = [
    pytest.param(SNAPSHOT_PROFILE, id="substring-collision-Health-Snapshot"),
    pytest.param("hrv snapshot", id="lowercased"),
    pytest.param("HRV SNAPSHOT", id="uppercased"),
    pytest.param("Hrv Snapshot", id="title-cased-differently"),
    pytest.param("HRV Snapshot ", id="trailing-space"),
    pytest.param(" HRV Snapshot", id="leading-space"),
    pytest.param("HRV  Snapshot", id="doubled-inner-space"),
    pytest.param("HRV\tSnapshot", id="tab-for-space"),
    pytest.param("HRV Snapsho", id="truncated"),
    pytest.param("HRV Snapshots", id="pluralised"),
    pytest.param("Snapshot", id="proper-substring"),
    pytest.param("", id="empty-string"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param("HRV Snapshöt", id="non-ascii"),
    pytest.param("HRV Snapshot" * 200, id="very-long"),
    pytest.param(42, id="non-str-int"),
    pytest.param(b"HRV Snapshot", id="non-str-bytes"),
    pytest.param((1, 2), id="non-str-tuple"),
    pytest.param(True, id="non-str-bool"),
]


@pytest.mark.parametrize("profile_name", _NON_MATCHING_PROFILE_NAMES)
def test_a_name_that_is_not_exactly_the_configured_one_does_not_declare(
    profile_name, synthetic, classified
) -> None:
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": profile_name}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert not _routed(session)


def test_a_file_carrying_no_profile_name_at_all_is_simply_undeclared(
    synthetic, classified
) -> None:
    """A14. Absence is not an error, and it is not a veto either."""
    values = dict(CLEAN_CAPTURE)
    del values["sport_profile_name"]

    session = classified(
        synthetic(**values), rr_intervals=_beats(), profile_names=[DECLARED_PROFILE]
    )

    assert not _routed(session)
    assert "sport_profile_name" not in session.context.provenance
    assert session.quality_flags == []


@pytest.mark.parametrize("profile_name", [42, b"x", (1, 2), True])
def test_a_non_string_profile_name_is_undeclared_rather_than_a_veto(
    profile_name, synthetic, classified
) -> None:
    """T058 Finding 12, made observable.

    D-3's veto is scoped to the three *intensity* signals, so a corrupt
    identity field must not refuse the file the way a corrupt distance does --
    it simply fails the exact match. Shown by declaring the same file via the
    **override**: if the garbage name vetoed, no declaration could rescue it
    (that is what a veto means), so the route is the proof it did not.
    """
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": profile_name}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert _routed(session)


def test_the_exact_name_declares(synthetic, classified) -> None:
    """The positive control for the table above: it could have failed."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
    )

    assert _routed(session)


def test_one_configured_name_among_several_still_declares(
    synthetic, classified
) -> None:
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=["Morning HRV", DECLARED_PROFILE, "Lie Down"],
    )

    assert _routed(session)


def test_a_blank_config_entry_is_rejected_before_it_can_match_anything(
    isolated_data_dir,
) -> None:
    """A18. The reason ``""`` in the list is unreachable rather than harmless.

    Asserted here rather than assumed, because the exact-match rule is only
    safe *because* T054's validator holds: a ``[""]`` entry would otherwise
    match every file whose profile name is empty or absent.
    """
    for blank in ("", "   ", "\t"):
        with pytest.raises(ValidationError):
            AppConfig(
                host="127.0.0.1",
                port=8000,
                data_dir=isolated_data_dir,
                resting_hrv_profile_names=[blank],
            )


# ---------------------------------------------------------------------------
# 3. the upload-time override (A2, A3, A10 / R3)
# ---------------------------------------------------------------------------


def test_the_override_declares_a_file_the_config_does_not_cover(
    synthetic, classified
) -> None:
    """A2."""
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, "sport_profile_name": "Run"}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert _routed(session)


def test_an_empty_config_list_plus_an_override_still_routes(
    synthetic, classified
) -> None:
    """A10, resolved by **R3**: ``[]`` is not a global Tier-1 kill switch.

    The only scenario covering an empty list carries no override, and the
    Decision Log's prose ("the explicit Tier-2-only declaration") reads as
    though it should refuse. The pseudocode's ``OR`` is normative and the
    resolution ratifies it: an override is a separate, deliberate act of
    intent about one specific file.
    """
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=[],
        resting_capture_override=True,
    )

    assert _routed(session)


def test_an_empty_config_list_without_an_override_does_not_route(
    synthetic, classified
) -> None:
    """A9, and the control that makes the row above mean something."""
    session = classified(
        synthetic(**CLEAN_CAPTURE), rr_intervals=_beats(), profile_names=[]
    )

    assert not _routed(session)


def test_no_configured_names_at_all_behaves_like_an_empty_list(
    synthetic, classified
) -> None:
    """``profile_names=None`` -- the parameter's own degenerate form."""
    session = classified(
        synthetic(**CLEAN_CAPTURE), rr_intervals=_beats(), profile_names=None
    )

    assert not _routed(session)


def test_both_declaration_routes_at_once_still_routes(
    synthetic, classified
) -> None:
    """A3 / T058 Finding 7. ``declared`` is an ``OR``, not an ``XOR``."""
    session = classified(
        synthetic(**CLEAN_CAPTURE),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert _routed(session)


# ---------------------------------------------------------------------------
# 4. both routes are subject to every veto (Table B, on both arms)
# ---------------------------------------------------------------------------

# The four contradictions the feature file and the contract table name, each
# perturbing exactly one field of the clean declared capture.
_CONTRADICTIONS = [
    pytest.param({"total_timer_time": 6000.0}, id="B10-forgot-to-stop-the-timer"),
    pytest.param({"avg_heart_rate": 140}, id="B20-140-bpm"),
    pytest.param({"total_distance": -500.0}, id="B27-negative-distance"),
    pytest.param({"total_distance": (1, 2)}, id="B35-unparseable-distance"),
    pytest.param({"avg_heart_rate": (1, 2)}, id="B22-unparseable-heart-rate"),
    pytest.param({"total_timer_time": "150"}, id="B11-unparseable-duration"),
    pytest.param({"total_distance": 800.0, "total_timer_time": 240.0}, id="B33-3.3-m-s"),
    pytest.param({"avg_heart_rate": 0}, id="B14-present-and-zero-heart-rate"),
]

# The two ways a file can be declared. Both are run against every row above:
# the override is a claim about *intent*, never about the data, so it must not
# rescue a 6000 s file -- and it is otherwise the branch tests do not take,
# which is precisely how the zero-distance bug survived six waves.
_DECLARATION_ROUTES = [
    pytest.param({"profile_names": [DECLARED_PROFILE]}, id="via-configured-profile"),
    pytest.param(
        {"profile_names": [], "resting_capture_override": True}, id="via-override"
    ),
]


@pytest.mark.parametrize("declaration", _DECLARATION_ROUTES)
@pytest.mark.parametrize("contradiction", _CONTRADICTIONS)
def test_a_declared_file_that_contradicts_its_declaration_is_refused(
    contradiction, declaration, synthetic, classified
) -> None:
    session = classified(
        synthetic(**{**CLEAN_CAPTURE, **contradiction}),
        rr_intervals=_beats(),
        **declaration,
    )

    assert not _routed(session)
    assert session.hrv_source_tier is None


@pytest.mark.parametrize("declaration", _DECLARATION_ROUTES)
def test_the_clean_capture_routes_on_both_declaration_routes(
    declaration, synthetic, classified
) -> None:
    """The control for the veto grid: without a contradiction, both route."""
    session = classified(
        synthetic(**CLEAN_CAPTURE), rr_intervals=_beats(), **declaration
    )

    assert _routed(session)


def test_a_declared_multi_session_file_is_still_refused_before_either_tier(
    multi_session, classified
) -> None:
    """C1 with a declaration on top -- the guard runs before the declaration.

    Probed deliberately: "but the athlete declared it" is the single most
    plausible reason a later change would move this refusal after the
    declaration, and the summary and beat stream still describe different
    spans whatever the athlete says.
    """
    session = classified(
        multi_session(CLEAN_CAPTURE, {"total_timer_time": 7200.0}),
        rr_intervals=_beats(),
        profile_names=[DECLARED_PROFILE],
        resting_capture_override=True,
    )

    assert not _routed(session)
    assert "hrv_multi_session_unclassified" in session.context.provenance


# ---------------------------------------------------------------------------
# 5. beats before declaration -- the ordering trap (A5-A8, C4)
# ---------------------------------------------------------------------------


def test_a_declared_health_snapshot_still_routes_tier_2(declared_config, ingest) -> None:
    """C4 / A5, end to end, and the one row that must be run deliberately.

    ``sample_health_snapshot.fit`` carries ``sport_profile_name`` 'Health
    Snapshot' and **zero** ``hrv`` messages. Put the declaration check above
    the beats gate -- the natural reading of "declaration routes" -- and this
    file claims Tier 1, ``_classify_tier_1`` returns True, ``classify()``
    short-circuits, and a perfectly good snapshot routes **nowhere**. It is
    one config line from real, which is why the order is normative.
    """
    declared_config(SNAPSHOT_PROFILE)

    with TestClient(app) as client:
        body = ingest(client, SNAPSHOT_FIXTURE)

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == SNAPSHOT_DEVICE_RMSSD
    assert body["rr_source"] == "health_snapshot_ppg"


def test_an_override_on_a_beatless_snapshot_still_routes_tier_2(
    declared_config, ingest
) -> None:
    """A6/A7 end to end: the override cannot conjure beats either."""
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, SNAPSHOT_FIXTURE, data={"resting_capture": "true"})

    assert body["activity_tag"] == "health_snapshot"
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == SNAPSHOT_DEVICE_RMSSD


@pytest.mark.parametrize("declaration", _DECLARATION_ROUTES)
def test_a_declared_file_with_no_beats_is_not_claimed_by_tier_1(
    declaration, synthetic, classified
) -> None:
    session = classified(synthetic(**CLEAN_CAPTURE), rr_intervals=[], **declaration)

    assert not _routed(session)


def test_the_beats_gate_is_evaluated_before_the_declaration_in_source_order() -> None:
    """The order is a stated contract, so it is asserted as one.

    A behavioural assertion alone cannot distinguish "beats first" from "beats
    happened to be checked first"; this reads ``_classify_tier_1``'s own source
    and pins that the beats guard precedes both the veto evaluation and the
    declaration call, so a refactor that reorders those lines fails here and
    names the reason.

    The docstring is stripped first -- it *describes* the ordering, in the
    opposite textual order, so searching the whole source would pin the prose
    rather than the code.

    The veto call is ``_resting_profile(`` since T064, which needed the veto's
    *name* as well as its answer; ``_resting_profile_duration`` is now a
    projection of it and is still what the beatless gate and the veto suites
    call. The assertion is unchanged in meaning -- only in which call it looks
    for.
    """
    source = inspect.getsource(hrv_classification._classify_tier_1)
    body = source[source.rindex('"""') + 3 :]

    beats_gate = body.index("if not rr_intervals")
    vetoes = body.index("_resting_profile(")
    declaration = body.index("_declared(")

    assert beats_gate < vetoes < declaration


# ---------------------------------------------------------------------------
# 6. the transport seam -- config and override actually reach classify()
# ---------------------------------------------------------------------------


def test_the_configured_names_reach_the_classifier_through_the_pipeline(
    declared_config,
) -> None:
    """``ingest_fit_bytes`` reads the config; with inference gone, the route
    it produces can only have come from the declaration."""
    declared_config(DECLARED_PROFILE)

    pipeline.ingest_fit_bytes((FIXTURES / DECLARED_FIXTURE).read_bytes())

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT activity_tag, hrv_source_tier FROM sessions"
        ).fetchall()
    finally:
        conn.close()

    assert [tuple(row) for row in rows] == [("resting_hrv_check", "chest_strap_raw")]


def test_the_upload_override_routes_a_file_the_config_does_not_cover(
    declared_config, ingest
) -> None:
    """The override's end-to-end proof, on the one real file it exists for.

    ``strap_hrv_sample_run.fit`` is a genuine resting capture recorded on the
    'Run' profile -- the file the config path cannot rescue, which is why the
    override exists at all.
    """
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE, data={"resting_capture": "true"})

    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"


def test_an_upload_without_the_override_field_defaults_to_no_override(
    declared_config, post_fit
) -> None:
    """The field is optional: every existing client keeps working unchanged."""
    declared_config()

    with TestClient(app) as client:
        response = post_fit(client, UNDECLARED_FIXTURE)

    assert response.status_code == 201


# Verified against the installed pydantic (``TypeAdapter(bool)``) rather than
# assumed -- ``.claude/rules/project-testing.md``. ``""`` is **not** in the
# accepted set and raises, which is why the client always sends a spelled-out
# value rather than an empty part.
_TRUTHY_FORM_VALUES = ["true", "True", "1", "on", "yes"]
_FALSY_FORM_VALUES = ["false", "False", "0", "off", "no"]


@pytest.mark.parametrize("form_value", _TRUTHY_FORM_VALUES)
def test_the_form_field_coerces_every_truthy_spelling(
    form_value, declared_config, ingest
) -> None:
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE, data={"resting_capture": form_value})

    assert body["activity_tag"] == "resting_hrv_check"


@pytest.mark.parametrize("form_value", _FALSY_FORM_VALUES)
def test_the_form_field_coerces_every_falsy_spelling(
    form_value, declared_config, ingest
) -> None:
    """The mirror of the row above, and one that only became meaningful at T069.

    ``strap_hrv_sample_run.fit`` is a genuine resting capture on an unconfigured
    profile, so with the override spelled falsy there is nothing left to declare
    it and it must not route. While the inference arm existed this row needed that
    arm stubbed to say anything at all; it now asserts the real behaviour of a
    real upload."""
    declared_config()

    with TestClient(app) as client:
        body = ingest(client, UNDECLARED_FIXTURE, data={"resting_capture": form_value})

    assert body["activity_tag"] is None
    assert body["hrv_source_tier"] is None


def test_an_uninterpretable_override_value_is_rejected_rather_than_guessed(
    declared_config, post_fit
) -> None:
    """A malformed flag is a 422, never a silent "probably false".

    The override is the one input on this path that carries the athlete's
    intent and nothing else, so guessing at it is the one thing that must not
    happen.
    """
    declared_config()

    with TestClient(app) as client:
        response = post_fit(
            client, UNDECLARED_FIXTURE, data={"resting_capture": "maybe"}
        )

    assert response.status_code == 422
