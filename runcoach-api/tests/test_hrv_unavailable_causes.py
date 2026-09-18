"""Why the trend says ``hrv_unavailable``, derived from the code and held
against the blocks that enumerate it (T128, review cycle 8, gap G-C8-4).

Three normative blocks say when the HRV input goes unavailable, and all three
were stale or false on 2026-09-16:

* ``spec/06``'s §6.2.4 Guardrails bullet enumerated *"skipped, or failed the
  §2.4.3 valid-fraction gate"* -- two reasons a **capture** is missing, which
  is upstream of every reason ``judge`` has;
* ``spec/02``'s Degradation paragraph and ``spec/03``'s §3.7.4 degradation
  bullet both carried a universal claim that the HRV input goes unavailable
  only when no tier can sustain a trend, which T116 (the establishment gate)
  and T125 (the withheld week) made false: a tier can sustain a trend, arrive
  with a full band and a week mean, and the verdict still reads
  ``hrv_unavailable``.

The defect is not that three sentences were wrong once. It is that the rule
moved in ``judge`` and nothing in the tree could notice three documents had
stopped describing it -- ``review-catches-what-tests-cannot`` and
``normative-docs-must-be-checked-against-each-other`` in one shape. Correcting
the prose by hand fixes the instance and leaves the mechanism, so the cause set
is **derived from the source** here (the T118 precedent,
``test_the_thresholds_description_names_the_tier_constant_the_block_omits``)
and the blocks are checked against it. A cause added to or removed from
``judge``, a second endpoint-level withhold beside ``_withhold_future``, or a
tier resolver that can no longer fail, all turn this module red rather than
falsifying the documents in silence.

**What this cannot see.** It asserts that each block *names* every cause the
code can produce; it cannot assert the block's account of a cause is true, and
it says nothing about what §6 is told to *do* with one. Those stay review's.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from runcoach_api import main as main_module
from runcoach_api.metrics import hrv_trend

# ---------------------------------------------------------------------------
# The roots, and the flattening
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The same two roots ``test_hrv_trend_endpoint.py`` walks, for the same
#: reason: **F005 lives behind the gitignored, machine-local ``.shipyard``
#: junction, and a walk from the repo root does not follow it.** That blind
#: spot has cost this feature at least three missed findings. The pair is
#: restated rather than imported because importing that module executes a
#: 2,500-line suite's worth of module-level setup to read one tuple; what
#: keeps the two copies in step is
#: ``test_the_roots_match_the_scan_the_endpoint_suite_walks``, which reads the
#: other file's assignment out of its source without running it.
SCAN_ROOTS = (_REPO_ROOT, _REPO_ROOT / ".shipyard")

_ENDPOINT_SUITE = Path(__file__).with_name("test_hrv_trend_endpoint.py")

#: Removed, not spaced: markdown emphasis and code spans sit **inside** words
#: and between them, and the blocks mark the same phrase up differently (one
#: bolds a word of it, another wraps ``min_window_readings`` in a code span).
#: A phrase has to survive that or it is pinning typography.
_DROPPED = str.maketrans("", "", "\"'`*_")

_TYPOGRAPHY = {"≥": ">=", "≤": "<=", "—": "--", "–": "--", "−": "-"}


def _flat(text: str) -> str:
    """Whitespace-normalised lowercase with quotes, emphasis and code spans
    dropped and the typography folded to ASCII."""
    folded = text.translate(_DROPPED)
    for symbol, ascii_form in _TYPOGRAPHY.items():
        folded = folded.replace(symbol, ascii_form)
    return " ".join(folded.split()).lower()


# ---------------------------------------------------------------------------
# The cause set, read out of the source
# ---------------------------------------------------------------------------

_VERDICT_UNAVAILABLE = "VERDICT_UNAVAILABLE"


def _module_tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer a module-level function: this oracle has nothing to read")


def _assigns_an_asserted_verdict(statements: list[ast.stmt]) -> bool:
    """Does this branch bind ``verdict`` to a verdict that is **not**
    ``hrv_unavailable``? That is what separates a *guard* (its false side
    leaves the verdict unavailable) from the suppressed/normal *split* (both
    sides assert something), and it is why ``window_mean < band.lo`` is not a
    cause of unavailability."""
    for statement in statements:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "verdict" for t in node.targets):
                continue
            if (
                isinstance(node.value, ast.Name)
                and node.value.id.startswith("VERDICT_")
                and node.value.id != _VERDICT_UNAVAILABLE
            ):
                return True
    return False


def _judge_guards() -> tuple[str, ...]:
    """Every condition in ``judge`` whose failure leaves the verdict at
    ``hrv_unavailable``, unparsed from the source.

    An ``if`` qualifies when its body reaches an asserted verdict and its
    ``else`` does not, and an ``and``-chain contributes each conjunct
    separately -- so the guards are read at the granularity the causes are
    named at, and a fifth conjunct added to the chain arrives here on its own
    rather than hiding inside a sixth's text.
    """
    guards: list[str] = []
    for node in ast.walk(_function(_module_tree(hrv_trend), "judge")):
        if not isinstance(node, ast.If):
            continue
        if not _assigns_an_asserted_verdict(node.body) or _assigns_an_asserted_verdict(node.orelse):
            continue
        test = node.test
        terms = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
        guards += [ast.unparse(term) for term in terms]
    return tuple(sorted(guards))


def _endpoint_withholders() -> tuple[str, ...]:
    """Every function in ``main.py`` that puts ``VERDICT_UNAVAILABLE`` on a
    verdict the pure rule already decided -- the causes that exist outside
    ``judge`` because they need the route's clock or the route's store."""
    tree = _module_tree(main_module)
    found = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(sub, ast.Attribute) and sub.attr == _VERDICT_UNAVAILABLE for sub in ast.walk(node)
        )
    }
    return tuple(sorted(found))


def _tier_resolution_may_fail() -> bool:
    """Can ``resolve_baseline_tier`` answer "no tier at all"? That is the
    structural cause -- the only one of the six ``spec/02`` and ``spec/03``
    described before T128 -- and it disappears the day the resolver is made
    total."""
    returns = _function(_module_tree(hrv_trend), "resolve_baseline_tier").returns
    assert returns is not None, "resolve_baseline_tier lost its return annotation"
    return "None" in ast.unparse(returns)


@dataclass(frozen=True)
class Cause:
    """One reason a response reads ``hrv_unavailable``: what in the code
    produces it, and the phrase every enumerating block must carry for it.

    The phrase is the block's, not the code's -- prose cannot be derived --
    but *which* phrases must be present is decided by the code, through
    ``guards`` / ``withholders`` / ``structural``. A row whose code side no
    longer matches the source is red before its phrase is ever looked for.
    """

    key: str
    phrase: str
    guards: tuple[str, ...] = ()
    withholders: tuple[str, ...] = ()
    structural: bool = False
    note: str = field(default="", compare=False)


#: The six causes, in ``judge``'s own order, then the two outside it.
CAUSES = (
    Cause(
        key="no band",
        guards=("band is not None",),
        phrase="fewer than two baseline readings, so no band exists",
        note="build_band returns None below two readings (T126)",
    ),
    Cause(
        key="the judged week is too thin",
        guards=("readings_in_window >= MIN_WINDOW_READINGS", "window_mean is not None"),
        phrase="fewer than min_window_readings (3) readings in the judged week",
        note=(
            "two guards, one cause: an empty week is the only way window_mean is None, so the "
            "None-narrowing conjunct is the same rule stated for the type checker. If the two "
            "ever come apart -- a week mean that can be absent with three readings present -- "
            "this row is where that has to be argued, and the assertion below makes it be."
        ),
    ),
    Cause(
        key="the judged week is not the athlete's",
        guards=("not series.withheld",),
        phrase="the judged week is not a fair sample of the resolved tier",
        note="T125: computed in build_series (verdict_withheld), read here",
    ),
    Cause(
        key="the baseline is unestablished",
        guards=("established",),
        phrase="a baseline below min_baseline_readings (14), reported as established: false",
        note="T116: symmetric since 2026-09-15; the dominant cause after every reset",
    ),
    Cause(
        key="the day has not happened",
        withholders=("_withhold_future",),
        phrase="the judged day is after the athlete's local today",
        note="enforced at the route, on the route's clock, not in the pure rule",
    ),
    Cause(
        key="no tier sustains a trend at all",
        structural=True,
        phrase="no resting-HRV reading of any tier can sustain a trend",
        note="resolve_baseline_tier returns None; the only cause the pre-T128 prose described",
    ),
)


# ---------------------------------------------------------------------------
# The blocks that must enumerate them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One block that tells a reader when the HRV input goes unavailable.

    ``lead`` identifies the block's line rather than a line number, which
    moves; ``committed`` is the ``withdrawn_phrasings`` convention -- a row
    under the ``.shipyard`` root is absent from a fresh checkout and skips
    there loudly instead of failing.
    """

    label: str
    root_index: int
    rel: str
    lead: str
    committed: bool


SITES = (
    Site(
        label="spec/06 6.2.4 Guardrails",
        root_index=0,
        rel="specification/spec/06-adaptation-logic.md",
        lead="**Guardrails.** The gate may **down-regulate freely",
        committed=True,
    ),
    Site(
        label="spec/02 Degradation",
        root_index=0,
        rel="specification/spec/02-canonical-data-schema-ingestion.md",
        lead="**Degradation.** Degradation is decided at **tier level**",
        committed=True,
    ),
    Site(
        label="spec/03 3.7.4 graceful degradation",
        root_index=0,
        rel="specification/spec/03-derived-metric-formulas.md",
        lead="**Graceful degradation across tiers, then unavailable.**",
        committed=True,
    ),
    Site(
        label="F005 Negative Class verdict cost table",
        root_index=1,
        rel="spec/features/F005-resting-hrv-trend.md",
        lead="The verdict still cannot say *why* it is unavailable",
        committed=False,
    ),
)

#: Withdrawn by T128 as **false**, not merely stale: T116 and T125 both emit
#: ``hrv_unavailable`` on a tier that is sustaining a trend, with the band and
#: the week mean in the same response. Quoted here because this module is not
#: one of the files the endpoint suite's walk reaches for withdrawn phrasings.
FALSE_UNIVERSAL = (
    "Only when **no** resting-HRV reading of any tier can sustain a trend does the HRV input go unavailable"
)


def _block(site: Site) -> str:
    """The site's block, flattened. Located by its lead rather than by line
    number, and required to be unique: two lines carrying the lead means the
    block has been copied and this would pin whichever came first."""
    path = SCAN_ROOTS[site.root_index] / site.rel
    lead = _flat(site.lead)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if lead in _flat(line)]
    assert len(lines) == 1, (
        f"{site.label}: {len(lines)} lines carry the lead {site.lead!r}, not 1 -- the block has "
        f"moved, been reworded or been duplicated, and this oracle is reading the wrong text"
    )
    return _flat(lines[0])


def _resolve(site: Site) -> Path:
    path = SCAN_ROOTS[site.root_index] / site.rel
    if not path.exists():
        assert not site.committed, f"{path} is committed and must be in every checkout"
        pytest.skip(f"{path} is absent (the .shipyard breadcrumb is machine-local and gitignored)")
    return path


# ---------------------------------------------------------------------------
# The assertions
# ---------------------------------------------------------------------------


def test_the_roots_match_the_scan_the_endpoint_suite_walks() -> None:
    """Two copies of the two roots, kept in step without executing the other
    suite. If the endpoint module ever stops reaching the ``.shipyard`` root,
    or starts reaching a third, this says so here too -- otherwise F005 would
    quietly drop out of one scan and stay in the other."""
    tree = ast.parse(_ENDPOINT_SUITE.read_text(encoding="utf-8"))
    theirs = [
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SCAN_ROOTS" for t in node.targets)
    ]
    assert theirs == ["(_REPO_ROOT, _REPO_ROOT / '.shipyard')"], (
        f"test_hrv_trend_endpoint.py's SCAN_ROOTS is now {theirs}: this module restates that "
        f"tuple and the two have drifted"
    )


def test_every_unavailable_cause_is_claimed_by_exactly_one_code_element() -> None:
    """The half that makes the enumeration an oracle rather than a second
    transcription of the prose.

    The code says how many causes there are; the table below says what each is
    called and how a document must name it. A guard added to ``judge``, a
    guard removed from it, a second endpoint-level withhold, or a tier
    resolver that can no longer answer "no tier", each leaves one side of this
    assertion holding something the other does not -- which is red *before*
    any spec block is opened, and is the whole point: T116 moved the rule and
    three documents went on describing the one it replaced.
    """
    derived_guards = set(_judge_guards())
    assert derived_guards, "no guard was read out of judge: the oracle is reading nothing"
    claimed_guards = {guard for cause in CAUSES for guard in cause.guards}
    assert derived_guards == claimed_guards, (
        f"judge guards the verdict with {sorted(derived_guards)} and the cause table claims "
        f"{sorted(claimed_guards)}: unclaimed {sorted(derived_guards - claimed_guards)}, "
        f"claimed but absent from the code {sorted(claimed_guards - derived_guards)}"
    )

    derived_withholders = set(_endpoint_withholders())
    claimed_withholders = {name for cause in CAUSES for name in cause.withholders}
    assert derived_withholders == claimed_withholders, (
        f"main.py withholds the verdict in {sorted(derived_withholders)} and the cause table "
        f"claims {sorted(claimed_withholders)}: a cause of hrv_unavailable lives at the route "
        f"and is in neither the table nor the spec blocks"
    )

    structural = [cause.key for cause in CAUSES if cause.structural]
    assert bool(structural) == _tier_resolution_may_fail(), (
        f"resolve_baseline_tier {'can' if _tier_resolution_may_fail() else 'cannot'} answer 'no "
        f"tier' and the table declares {structural}: the structural cause and the code disagree"
    )

    keys = [cause.key for cause in CAUSES]
    assert len(set(keys)) == len(keys), f"two causes share a key: {keys}"
    phrases = [_flat(cause.phrase) for cause in CAUSES]
    assert len(set(phrases)) == len(phrases), (
        f"two causes share a phrase, so one of them is proved present by the other's text: {phrases}"
    )


@pytest.mark.parametrize("site", SITES, ids=[site.label for site in SITES])
def test_each_block_enumerates_every_unavailable_cause_the_code_can_produce(site: Site) -> None:
    """The blocks a reader of the spec meets ``hrv_unavailable`` in, held
    against the code's own cause set.

    Every offending cause is collected before the assertion: an enumeration
    that has fallen behind has usually fallen behind by more than one cause
    (``spec/06`` named **none** of the six on 2026-09-16), and reporting the
    first turns one correction into several passes.
    """
    _resolve(site)
    block = _block(site)
    missing = [f"{cause.key} -> {cause.phrase}" for cause in CAUSES if _flat(cause.phrase) not in block]
    assert not missing, (
        f"{site.label} ({site.rel}) tells a reader when the HRV input goes unavailable and "
        f"does not name {len(missing)} of the {len(CAUSES)} causes the code can produce: "
        + "; ".join(missing)
    )


@pytest.mark.parametrize("site", SITES, ids=[site.label for site in SITES])
def test_the_false_universal_about_unavailable_causes_is_gone_from_every_block(site: Site) -> None:
    """The stronger half of the defect: ``spec/02`` and ``spec/03`` did not
    merely under-enumerate, they asserted the complement. Checked over each
    site's **whole file**, not its block, so the sentence cannot survive by
    moving one paragraph."""
    path = _resolve(site)
    assert _flat(FALSE_UNIVERSAL) not in _flat(path.read_text(encoding="utf-8")), (
        f"withdrawn as false by T128 (T116 and T125 both emit hrv_unavailable on a tier that is "
        f"sustaining a trend), back in {site.rel}: {FALSE_UNIVERSAL}"
    )


def test_the_false_universal_is_gone_from_every_spec_document() -> None:
    """The site list is an allowlist, and an allowlist passes as soon as the
    sentence moves file (T119's lesson, one feature over). The committed
    ``specification/`` tree is swept whole."""
    specification = _REPO_ROOT / "specification"
    documents = sorted(specification.rglob("*.md"))
    assert len(documents) >= 6, (
        f"{len(documents)} markdown documents under {specification}: the sweep has stopped "
        f"descending and its all-clear is a report over nothing"
    )
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in documents
        if _flat(FALSE_UNIVERSAL) in _flat(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "withdrawn as false by T128, back in: " + ", ".join(offenders)


def test_f005_prices_t116_and_t126_as_net_cost_until_e007() -> None:
    """T128 deliverable 2. Both accepted costs are silence -- 20 days of
    ``hrv_unavailable`` after every **coverage-gap** reset (T116, re-confirmed
    at the corrected duration by T126; narrowed from "every reset" on
    2026-09-17 by T138, which measured the other reset kind at 18 days and by
    a different mechanism) -- and both acceptances rest on the same sentence: that
    down-regulation is the direction ``research/00`` §1.7 tolerates freely,
    because §6 widens its guardrails rather than being told readiness is
    intact.

    **§6 is E007, and E007 does not exist.** Until it does, the widening is
    not a behaviour the system performs, so the accepted cost is the whole
    cost and there is nothing on the other side of it. That is a material
    qualifier on two accepted costs and F005 stated it nowhere. The phrases
    are checked, not the bare epic id: F005 already mentions E007 in a scope
    sentence, so an ``"E007" in text`` assertion would have passed before the
    qualifier was written -- the T123 defect, and the reason this names what
    must be said instead.
    """
    path = SCAN_ROOTS[1] / "spec/features/F005-resting-hrv-trend.md"
    if not path.exists():
        pytest.skip(f"{path} is absent (the .shipyard breadcrumb is machine-local and gitignored)")
    text = _flat(path.read_text(encoding="utf-8"))
    for phrase in ("net cost with no offsetting benefit", "deferred to e007", "e007 does not exist"):
        assert phrase in text, (
            f"F005's Negative Class does not say {phrase!r}: T116's and T126's costs are "
            f"accepted on a compensating behaviour that has not been built"
        )


# ---------------------------------------------------------------------------
# The tier-change silence is stated beside the coverage-gap figure (T138)
#
# Every cost table in this feature priced the quiet "after every reset" on a
# figure established by a coverage-gap walk. T138 measured the other reset
# kind -- a clean, gapless, permanent source-tier change -- at 18 silent days
# against the gap's 20, and the two must now appear together everywhere the
# gap's figure appears, in authority order: research/00, then spec/03, then
# F005's cost table.
#
# **Both figures are derived here, not typed**, for the same reason T133 gave:
# a number in a document is a consequence of the constants it was computed
# from, so the pin has to red when a constant moves rather than track it. Move
# MIN_BASELINE_READINGS to 15 and the two figures become 19 and 21, neither of
# which the documents carry, and every row below goes red -- which is the
# signal that three documents are now stale, not that this test is wrong.
# ---------------------------------------------------------------------------

#: The quiet after a clean source-tier change: it begins when the withhold
#: arms (``MIN_WINDOW_READINGS - 1``) and ends when clause (a)'s candidacy
#: resolves (``MIN_BASELINE_READINGS + WINDOW_DAYS - 1``). Derived here as the
#: difference; derived again, independently and against a walked series, in
#: ``test_hrv_trend_reset.py``.
TIER_CHANGE_SILENCE_DAYS = (
    hrv_trend.MIN_BASELINE_READINGS + hrv_trend.WINDOW_DAYS - hrv_trend.MIN_WINDOW_READINGS
)

#: The quiet after a coverage gap (T126), and the tier change's reporting lag,
#: which are the same length for the same reason: both end when a baseline of
#: ``MIN_BASELINE_READINGS`` distinct days has reached ``D-7``.
COVERAGE_GAP_QUIET_DAYS = hrv_trend.MIN_BASELINE_READINGS + hrv_trend.WINDOW_DAYS - 1

#: The closed form the three documents quote, so that a reader can recompute
#: the figure rather than take it. Flattened the way the blocks are.
#: ``_flat`` drops underscores along with the emphasis, so the form is
#: flattened here rather than retyped in its flattened spelling.
CLOSED_FORM = _flat("min_baseline_readings + 7 - min_window_readings")

COST_SITES = (
    Site(
        label="research/00 5.4 T138 measurement",
        root_index=0,
        rel="specification/research/00-design-decisions.md",
        lead="**The 20-day quiet was measured for one of the two reset kinds",
        committed=True,
    ),
    Site(
        label="spec/03 3.7.3 establishment gate",
        root_index=0,
        rel="specification/spec/03-derived-metric-formulas.md",
        lead="- **Either position on a baseline that is not yet established**",
        committed=True,
    ),
    Site(
        label="F005 Negative Class tier-change silence row",
        root_index=1,
        rel="spec/features/F005-resting-hrv-trend.md",
        lead="| The cost nobody had measured:",
        committed=False,
    ),
)


@pytest.mark.parametrize("site", COST_SITES, ids=[site.label for site in COST_SITES])
def test_the_tier_change_silence_is_stated_beside_the_coverage_gap_figure(site: Site) -> None:
    """Each block that prices the quiet after a reset carries **both** figures
    and the closed form that produces the tier-change one.

    T138, deliverable 1. Four things are required of every block, and each of
    them is a separate way the amendment could have been done badly:

    * the tier-change figure (**18**), so the cost exists in the document at
      all;
    * the coverage-gap figure (**20**) in the same block, so the two are
      stated *beside* each other rather than in separate places a reader
      would have to find -- the precedence inversion T133 caught one cycle
      ago was exactly a corrected figure landing in one document and not its
      neighbours;
    * the **closed form**, so the number is recomputable rather than quoted
      (``research/00`` §1.6's reproduce-it-by-hand property, applied to the
      feature's own cost table);
    * both reset kinds named in the block, because the defect being fixed is
      one figure standing for two mechanisms.

    Both figures come from ``hrv_trend``'s constants, never from a literal, so
    this parametrization reds when a constant moves and the documents go
    stale -- it does not quietly follow the constant to a new number the
    documents never stated.
    """
    _resolve(site)
    block = _block(site)

    assert CLOSED_FORM in block, (
        f"{site.label}: the block quotes a number without the closed form {CLOSED_FORM!r} that "
        f"produces it, so a reader cannot recompute it when a constant moves"
    )
    # The figure is required **as the closed form's stated result**, not as a
    # bare digit anywhere in the block. A bare ``str(18) in block`` is the
    # vacuous-grep shape T128 caught: these blocks are long, and 18, 19, 20 and
    # 21 all occur in them incidentally (``R+2 .. R+19``, ``gap_reset_days``),
    # so under a mutation of MIN_BASELINE_READINGS two of the three sites
    # stayed green on a number that was never the figure. Measured.
    stated = re.search(
        re.escape(CLOSED_FORM) + r"[^|]*?= ?" + str(TIER_CHANGE_SILENCE_DAYS) + r"(?![0-9])",
        block,
    )
    assert stated, (
        f"{site.label}: the block carries the closed form but does not state its result as "
        f"{TIER_CHANGE_SILENCE_DAYS}. Either the amendment is missing here, or a constant moved "
        f"and this document still publishes the old figure -- amend research/00, then spec/03, "
        f"then F005, in that order"
    )
    # The coverage-gap figure has to appear as ``R+20`` -- the day the reset is
    # finally reported, which is also the length of the gap's own quiet -- and
    # not as a bare "20 days". "more than 21 days of no captures" and the like
    # already sit in these blocks, so a looser form stays green at the mutated
    # constant and proves nothing. Measured both ways.
    assert f"r+{COVERAGE_GAP_QUIET_DAYS}" in block, (
        f"{site.label}: the block states the tier-change silence without naming R+"
        f"{COVERAGE_GAP_QUIET_DAYS}, the day the reset is finally reported -- which is the "
        f"coverage gap's own quiet, and the comparison this whole amendment is for"
    )
    assert "coverage gap" in block and "tier change" in block, (
        f"{site.label}: the block does not name both reset kinds, which is the distinction the "
        f"whole amendment exists to draw"
    )


def test_the_tier_change_delay_is_named_and_accepted_in_the_negative_class() -> None:
    """F005's Negative Class names the ~20-day reporting lag **and records a
    decision on it**, where before T138 it did neither.

    T138, deliverable 3. Naming a cost and accepting it are different acts,
    and this feature's own history is the argument for asserting both: the
    stale-candidacy row was named in T094, priced in one direction only, and
    its acceptance had to be re-opened in T110 once the second direction was
    written down. So this requires the lag to be *stated* (the response says
    no reset happened while one has), *decided* (accepted, not left open),
    and decided **on a reason** -- that clause (a) accumulates over time and
    an earlier report would be a prediction that must be withdrawn.

    Skips loudly rather than failing where F005 is absent: it lives under the
    machine-local ``.shipyard`` breadcrumb, which is gitignored (the
    ``committed: False`` convention of ``SITES`` above).
    """
    site = COST_SITES[-1]
    path = _resolve(site)
    text = _flat(path.read_text(encoding="utf-8"))

    assert "the response says no reset happened when one did" in text, (
        "F005's Negative Class does not state the reporting lag: for the whole delay "
        "baseline.reset_reason is null while a reset has in fact happened"
    )
    assert "named and accepted 2026-09-17" in text, (
        "the lag is stated but no decision is recorded on it; T138's deliverable is a deliberate "
        "acceptance, not a mention"
    )
    assert "latency, not inaccuracy" in text, (
        "the lag must be distinguished from a wrong date -- reset_on is correct when it arrives"
    )
    assert "time-accumulating" in text, (
        "the acceptance must carry its reason: clause (a) accumulates, so an earlier report would "
        "be a prediction and would have to be withdrawn on every abandoned trial"
    )


def test_the_feature_composes_its_silences_against_a_rate() -> None:
    """F005 sums the four silences it has accumulated and answers the question
    none of its documents asked.

    T138, deliverable 4. The feature added silence in T116/T126, T125, T132
    and now T138 and never composed them; the critic's question is at what
    point a rule that mostly says nothing stops being conservative. This pins
    that the paragraph exists, that it is arithmetic rather than a gesture (it
    names a worst realistic case with a total), and that it commits to an
    answer rather than restating the question.
    """
    site = COST_SITES[-1]
    path = _resolve(site)
    text = _flat(path.read_text(encoding="utf-8"))

    assert "composing the silences" in text, "F005 never sums the silences it has added"
    assert "stop being conservative and start being useless" in text, (
        "the critic's question is not asked in the document"
    )
    assert "129 of 365 days" in text, (
        "the composition must be arithmetic over a worst realistic case, not a qualitative worry"
    )
    assert "length is not the test" in text and "nullity" in text, (
        "the paragraph must commit to an answer; answering it imperfectly is better than leaving "
        "it unasked, but restating the question is not answering it"
    )
