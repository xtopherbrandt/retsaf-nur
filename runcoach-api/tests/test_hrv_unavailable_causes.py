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
#: reason: **F005's original lives behind the gitignored, machine-local
#: ``.shipyard`` junction, and a walk from the repo root does not follow it.**
#: That blind spot cost this feature at least three missed findings, and
#: since T124 every F005 row below reads the committed copy under
#: ``spec-mirror/`` (root 0) instead -- byte-equal to the original by
#: ``test_normative_mirror.py`` -- so no row skips anywhere. The pair is
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
    ``judge`` because they need the route's clock or the route's store.

    **Mentions inside a comparison are not withholds (T167, 2026-09-21).**
    Until then this matched *any* mention of the attribute, which conflated
    writing the value with reading it. ``main._disagreed_with`` now asks
    ``verdict.verdict == VERDICT_UNAVAILABLE`` -- the one condition
    ``research/00`` §5.4 (iii) states for the dissent list, which **empties a
    report** on a verdict something else already withheld and confers no
    cause of its own. Counted as a withholder it put a cause in this oracle
    that no spec block can enumerate, because there is none.

    What the oracle checks, and no more: it walks **synchronous** ``def``
    functions only (``ast.FunctionDef``, not ``ast.AsyncFunctionDef``) and
    matches only the **attribute spelling** ``<module>.VERDICT_UNAVAILABLE``
    (an ``ast.Attribute``), not a bare name or a string literal. Within that
    scope, a withhold that *constructs* the unavailable verdict -- a keyword
    argument, an assignment, a ``replace`` call, a return -- is found, and
    the exemption covers only an attribute that is **itself a direct
    operand** of an ``ast.Compare`` (its ``left`` or one of its
    ``comparators``), never a node nested deeper inside one. So a withhold
    constructed *inside* a comparison -- e.g. a walrus
    ``(w := dataclasses.replace(verdict, verdict=VERDICT_UNAVAILABLE))
    != verdict`` in a sync ``def`` -- is caught and red until the table and
    the spec blocks name it (cycle-2 review, S2: the exemption previously
    covered every node under a ``Compare`` and that mutant stayed green).
    An ``async def`` withholder, or one spelling the value as a string
    literal, is **not** caught here.
    """
    tree = _module_tree(main_module)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        compared = {
            id(operand)
            for comparison in ast.walk(node)
            if isinstance(comparison, ast.Compare)
            for operand in (comparison.left, *comparison.comparators)
        }
        if any(
            isinstance(sub, ast.Attribute) and sub.attr == _VERDICT_UNAVAILABLE and id(sub) not in compared
            for sub in ast.walk(node)
        ):
            found.add(node.name)
    return tuple(sorted(found))


def _tier_resolution_may_fail() -> bool:
    """Can the presentation answer "no dataset at all"? That is the
    structural cause -- the only one of the six ``spec/02`` and ``spec/03``
    described before T128 -- and it disappears the day the presentation is
    made total.

    Re-anchored at T156 (F006, 2026-09-19): F005's ``resolve_baseline_tier``
    answered "no tier at all" with ``None``, and ``selected_view`` built the
    empty ``tier is None`` view on it. Under F006 nothing on the route calls
    the resolver; the empty view is built when ``_presentation_fallback`` --
    the AC9 fallback, asked once ``select_dataset`` has selected nothing --
    returns ``None``, which it does exactly when the series holds no dataset.
    Reading the retired resolver's annotation would keep this row green
    forever, on a function the cause no longer passes through.
    """
    returns = _function(_module_tree(hrv_trend), "_presentation_fallback").returns
    assert returns is not None, "_presentation_fallback lost its return annotation"
    return "None" in ast.unparse(returns)


@dataclass(frozen=True)
class Cause:
    """One reason a response reads ``hrv_unavailable``: what in the code
    produces it, and the phrase every enumerating block must carry for it.

    The phrase is the block's, not the code's -- prose cannot be derived --
    but *which* phrases must be present is decided by the code, through
    ``guards`` / ``withholders`` / ``structural``. A row whose code side no
    longer matches the source is red before its phrase is ever looked for.

    **``note`` carries claims that nothing checks (T142, 2026-09-18).** The
    ``note`` on "the baseline is unestablished" asserted, falsely, that this
    cause dominates after *every* kind of reset -- false of a tier change,
    where it never fires -- and it survived T128, T138 and every review
    since. ``compare=False`` was
    *not* the mechanism, and flipping it would not have caught this: no test
    in this module compares, hashes or sorts a ``Cause``, so the exclusion
    protects an equality that does not exist. The real gap is that ``note``
    is never **read** by an assertion, while ``phrase`` is (see
    ``_the_block_names_every_cause``, which looks each ``phrase`` up in the
    document). ``compare=False`` is therefore left exactly as it was --
    changing it would alter this dataclass's comparison semantics to no
    effect. A ``note`` here is commentary for the reader, not a pinned claim;
    a claim that must hold belongs in ``phrase``, where a probe reaches it.
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
        phrase="fewer than min_window_readings (3) readings of baseline.tier in the judged week",
        note=(
            "two guards, one cause: an empty week is the only way window_mean is None, so the "
            "None-narrowing conjunct is the same rule stated for the type checker. If the two "
            "ever come apart -- a week mean that can be absent with three readings present -- "
            "this row is where that has to be argued, and the assertion below makes it be. "
            "The phrase gained 'of baseline.tier' in T148, carrying the qualifier T146 added to "
            "the contract: readings of other tiers sit in the judged week and are excluded from "
            "readings_in_window, so a count of all readings in the week is not the rule. The "
            "literal (3) is load-bearing -- _flat does not strip parentheses -- and the six "
            "blocks below were swept to the qualified wording in the same pass."
        ),
    ),
    Cause(
        key="the judged week is not the athlete's",
        guards=("not series.withheld",),
        phrase="the judged week is not a fair sample of",
        note=(
            "T125: computed in build_series (verdict_withheld), read here. The phrase lost its "
            "object in T168 (2026-09-22): the two contract copies now say 'of the dataset being "
            "judged', retiring F005's 'resolved tier', while the markdown sites -- F005's Negative "
            "Class among them, a record of the rule as shipped -- keep it. The claim that names "
            "the cause is the unfair sample; the object is vocabulary."
        ),
    ),
    Cause(
        key="the baseline is unestablished",
        guards=("established",),
        phrase="a baseline below min_baseline_readings (14), reported as established: false",
        note=(
            "T116: symmetric since 2026-09-15; the dominant cause after a coverage-gap "
            "reset. Corrected in place 2026-09-18 (T142), which had claimed it dominant "
            "after every kind of reset: a clean source-tier change leaves established "
            "true (n decays 60 -> 47), so this cause never fires for it at all -- its "
            "silence is 'the judged week is too thin' (T138). Nothing asserts on a note; "
            "see the dataclass docstring."
        ),
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
        note=(
            "_presentation_fallback returns None (no dataset in the series; since T156 -- F005's "
            "resolve_baseline_tier answered it before); the only cause the pre-T128 prose described"
        ),
    ),
)


# ---------------------------------------------------------------------------
# The blocks that must enumerate them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One block that tells a reader when the HRV input goes unavailable.

    ``lead`` identifies the block's line rather than a line number, which
    moves. Every row's file is committed and an absent one fails (T124): the
    ``committed: False`` rows that used to skip under the absent ``.shipyard``
    root now point at the committed copies under ``spec-mirror/``.

    ``end`` is T140's addition and is empty for every markdown row but one,
    which is the behaviour those rows had before it existed: a prose block in
    a document is one long line, so the lead line *is* the block. The one is
    ``COST_SITES``' research/00 row (sprint-007 T175): research/00 states one
    rule per line, so FIG-01 and FIG-02 are two blocks and the row reads from
    FIG-01's rule line to the rule after FIG-02, exclusive. A block in
    ``contracts/openapi.yaml`` or ``schemas.py`` is not -- a folded YAML
    scalar and an implicitly concatenated Python literal both wrap, and a
    cause named across a wrap is invisible to a one-line read. ``end`` names
    the first line **after** the block, exclusive, and those rows are required
    to carry one **because an unbounded read is how this defect was nearly
    mismeasured**: T140's own first probe sliced a fixed character count from
    the ``verdict`` enum line, ran past the description into
    ``unavailable_reason``'s enum below it, and reported a cause present that
    the description does not contain. The window is bounded at the next key,
    here as there.
    """

    label: str
    root_index: int
    rel: str
    lead: str
    end: str = ""


SITES = (
    Site(
        label="spec/06 6.2.4 Guardrails",
        root_index=0,
        rel="specification/spec/06-adaptation-logic.md",
        lead="**Guardrails.** The gate may **down-regulate freely",
    ),
    Site(
        label="spec/02 Degradation",
        root_index=0,
        rel="specification/spec/02-canonical-data-schema-ingestion.md",
        lead="**Degradation.** Degradation is decided at **tier level**",
    ),
    Site(
        label="spec/03 3.7.4 graceful degradation",
        root_index=0,
        rel="specification/spec/03-derived-metric-formulas.md",
        lead="**Graceful degradation across tiers, then unavailable.**",
    ),
    Site(
        label="F005 Negative Class verdict cost table",
        root_index=0,
        rel="spec-mirror/features/F005-resting-hrv-trend.md",
        lead="The verdict still cannot say *why* it is unavailable",
    ),
)

#: T140. The two **published** copies of the same enumeration, which ``SITES``
#: could not reach: it held four markdown documents and no contract copy, so
#: ``spec/03`` §3.7.4's own sentence -- "the enumeration is pinned to the code
#: by ``test_hrv_unavailable_causes.py``" -- was false of the contract clients
#: actually read. ``contracts/openapi.yaml``'s
#: ``HrvTrend.properties.verdict.description`` and its hand-synchronised twin
#: ``schemas.HrvTrendResponse.verdict`` both named **four** of the six causes,
#: while ``unavailable_reason`` -- added by T137 twenty lines below the first
#: of them -- published all six. Two fields of one schema object contradicted
#: each other and every gate in the tree was green:
#: ``test_the_two_copies_of_the_verdict_contract_publish_the_same_claims``
#: compares the two copies **to each other**, so it passed *because* both were
#: wrong identically, and ``check_drift.py`` never reads prose.
#:
#: Kept a sibling tuple rather than folded into ``SITES`` because the rows are
#: read differently: a markdown block is one line and these are wrapped, so
#: each carries the ``end`` that bounds it (see ``Site``).
CONTRACT_SITES = (
    Site(
        label="openapi.yaml HrvTrend.verdict description",
        root_index=0,
        rel="contracts/openapi.yaml",
        lead="hrv_normal and hrv_suppressed both assert an established baseline",
        end="unavailable_reason:",
    ),
    Site(
        label="schemas.py HrvTrendResponse.verdict description",
        root_index=0,
        rel="runcoach-api/src/runcoach_api/schemas.py",
        lead="hrv_normal and hrv_suppressed both assert an established baseline",
        end="unavailable_reason:",
    ),
    #: T148. The two rows above stop at ``unavailable_reason:``, which is
    #: exactly where the block a client reads to learn what each **enum member**
    #: means begins -- so until these two rows existed that block was covered by
    #: no site at all, and T146's defect in it could return unseen. Measured
    #: 2026-09-18: a cause phrase gutted inside the ``unavailable_reason``
    #: description left this module green at 20 passed, while the same mutation
    #: in the ``verdict`` description above reddened it. Both rows are bounded
    #: at ``ln_rmssd_7d_mean:``, the next field in each copy, for the reason
    #: ``Site`` gives: unbounded, the read would run on into the fields below
    #: and report their text as this block's.
    Site(
        label="openapi.yaml HrvTrend.unavailable_reason description",
        root_index=0,
        rel="contracts/openapi.yaml",
        lead="Why verdict is hrv_unavailable; null whenever it is not",
        end="ln_rmssd_7d_mean:",
    ),
    Site(
        label="schemas.py HrvTrendResponse.unavailable_reason description",
        root_index=0,
        rel="runcoach-api/src/runcoach_api/schemas.py",
        lead="Why verdict is hrv_unavailable; null whenever it is not",
        end="ln_rmssd_7d_mean:",
    ),
)

#: Every block that enumerates the causes, documents and contract alike. The
#: two tuples stay separate for how they are *read*; there is one population to
#: check, and it is this.
ENUMERATING_SITES = SITES + CONTRACT_SITES

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
    block has been copied and this would pin whichever came first.

    With no ``end`` the block is the lead line alone, which is what a markdown
    paragraph is. With an ``end`` it runs from the lead line to the first line
    below it carrying that marker, exclusive -- and the marker is required to
    be found, so a renamed terminator reds here rather than silently widening
    the window to the rest of the file.
    """
    path = SCAN_ROOTS[site.root_index] / site.rel
    lead = _flat(site.lead)
    source = path.read_text(encoding="utf-8").splitlines()
    leads = [index for index, line in enumerate(source) if lead in _flat(line)]
    assert len(leads) == 1, (
        f"{site.label}: {len(leads)} lines carry the lead {site.lead!r}, not 1 -- the block has "
        f"moved, been reworded or been duplicated, and this oracle is reading the wrong text"
    )
    if not site.end:
        return _flat(source[leads[0]])
    end = _flat(site.end)
    stops = [index for index in range(leads[0] + 1, len(source)) if end in _flat(source[index])]
    assert stops, (
        f"{site.label}: no line below the lead carries the terminator {site.end!r}, so the block "
        f"has no bound -- reading to the end of the file would find the causes named in the "
        f"fields below this one and report them as this one's"
    )
    return _flat(" ".join(source[leads[0] : stops[0]]))


def _resolve(site: Site) -> Path:
    """The site's file, which must exist in every checkout. This used to
    ``pytest.skip`` an absent ``committed: False`` row, and that skip was how
    F005's rows went unchecked on every machine but one (T124)."""
    path = SCAN_ROOTS[site.root_index] / site.rel
    assert path.exists(), f"{path} is committed and must be in every checkout"
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


def test_a_site_that_is_absent_fails_to_resolve_rather_than_skipping() -> None:
    """The pin on T124's deletion of the ``pytest.skip`` in ``_resolve``. Every
    row in ``SITES`` exists in a committed checkout, so the deletion is
    invisible to the rows themselves: put ``if not path.exists():
    pytest.skip(...)`` back and every site test stays green on every machine.
    This drives one absent row through ``_resolve`` and requires the failure.
    A skip raised there is converted to a failure rather than allowed to
    escape, because a skip *is* the outcome this pin refuses."""
    absent = Site(
        label="a site in no checkout",
        root_index=0,
        rel="spec-mirror/features/F999-in-no-checkout.md",
        lead="a lead nothing carries",
    )
    path = SCAN_ROOTS[absent.root_index] / absent.rel
    assert not path.exists(), f"{path} exists; this pin needs an absent path"
    with pytest.raises(AssertionError, match="must be in every checkout"):
        try:
            _resolve(absent)
        except pytest.skip.Exception as skipped:
            pytest.fail(f"an absent site was skipped rather than failed: {skipped}")


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
        f"_presentation_fallback {'can' if _tier_resolution_may_fail() else 'cannot'} answer 'no "
        f"dataset' and the table declares {structural}: the structural cause and the code disagree"
    )

    keys = [cause.key for cause in CAUSES]
    assert len(set(keys)) == len(keys), f"two causes share a key: {keys}"
    phrases = [_flat(cause.phrase) for cause in CAUSES]
    assert len(set(phrases)) == len(phrases), (
        f"two causes share a phrase, so one of them is proved present by the other's text: {phrases}"
    )


@pytest.mark.parametrize("site", ENUMERATING_SITES, ids=[site.label for site in ENUMERATING_SITES])
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


@pytest.mark.parametrize("site", ENUMERATING_SITES, ids=[site.label for site in ENUMERATING_SITES])
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


#: F006 review cycle 3, S4. T168's defect was a **direction**, not a missing
#: cause: both contract copies said "a returning or brand-new device's week
#: entirely predates the tier being judged", the order ``verdict_withheld``
#: reverses (it withholds when the *other* dataset's week days are all later
#: than every judged-week day of the dataset being judged). The cause phrase
#: above, old or shortened, names the unfair sample and is silent on which
#: week comes first, so the inverted text satisfied it. The pin below requires
#: the judged dataset's own readings as the subject that predates the return,
#: and the absence check forbids the inverted sentence. Both are flattened
#: the way the blocks are.
WEEK_NOT_REPRESENTATIVE_DIRECTION = re.compile(
    r"not a fair sample of the dataset being judged, (baseline\.tier -- t125/t132: )?"
    r"its (judged-week )?readings all predat(e|ing) (the athletes return|a returning)"
)
WEEK_NOT_REPRESENTATIVE_INVERTED = (
    _flat("entirely predates the tier being judged"),
    _flat("device's week entirely predates"),
)


@pytest.mark.parametrize("site", CONTRACT_SITES, ids=[site.label for site in CONTRACT_SITES])
def test_each_contract_block_states_week_not_representative_in_the_codes_direction(site: Site) -> None:
    """The judged dataset's week predates the returning device's, never the
    reverse. Red on T168's inverted wording in either contract copy."""
    _resolve(site)
    block = _block(site)
    inverted = [phrase for phrase in WEEK_NOT_REPRESENTATIVE_INVERTED if phrase in block]
    assert not inverted, (
        f"{site.label}: states week_not_representative backwards ({inverted}); verdict_withheld "
        f"withholds when another dataset's judged-week days are all LATER than the judged one's"
    )
    assert WEEK_NOT_REPRESENTATIVE_DIRECTION.search(block), (
        f"{site.label}: does not say that the judged dataset's own readings all predate the "
        f"returning or newly adopted device's -- the direction verdict_withheld asks"
    )


#: F006 review cycle 3, CR-C3-INLINE-1 G1. The two ``unavailable_reason``
#: blocks state the direction a second time, **formally**: the other dataset's
#: judged-week days are "every one later than every judged-week day of the
#: dataset being judged". The prose pin above is satisfied by the sentence
#: before it, so flipping ``later`` to ``earlier`` in both copies left this
#: module green (measured 2026-09-22). Only the two sites that carry the
#: formal clause are held to it; the ``verdict`` descriptions never stated it.
UNAVAILABLE_REASON_SITES = tuple(site for site in CONTRACT_SITES if "unavailable_reason" in site.label)
WEEK_NOT_REPRESENTATIVE_FORMAL = _flat(
    "every one later than every judged-week day of the dataset being judged"
)
WEEK_NOT_REPRESENTATIVE_FORMAL_INVERTED = _flat("earlier than every judged-week day")


@pytest.mark.parametrize(
    "site", UNAVAILABLE_REASON_SITES, ids=[site.label for site in UNAVAILABLE_REASON_SITES]
)
def test_each_unavailable_reason_block_states_the_formal_withhold_direction(site: Site) -> None:
    """The other dataset's judged-week days all come **later** than the judged
    dataset's, never earlier. Red on ``later`` flipped to ``earlier``."""
    assert len(UNAVAILABLE_REASON_SITES) == 2, [site.label for site in UNAVAILABLE_REASON_SITES]
    _resolve(site)
    block = _block(site)
    assert WEEK_NOT_REPRESENTATIVE_FORMAL_INVERTED not in block, (
        f"{site.label}: states the formal week_not_representative clause backwards; "
        f"verdict_withheld withholds when another dataset's judged-week days are all LATER"
    )
    assert WEEK_NOT_REPRESENTATIVE_FORMAL in block, (
        f"{site.label}: no longer says the other dataset's judged-week days are every one later "
        f"than every judged-week day of the dataset being judged"
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
    path = SCAN_ROOTS[0] / "spec-mirror/features/F005-resting-hrv-trend.md"
    assert path.exists(), f"{path} is committed and must be in every checkout"
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
        label="research/00 5.4 FIG-01 and FIG-02",
        root_index=0,
        rel="specification/research/00-design-decisions.md",
        lead="**FIG-01.** After a coverage-gap re-establishment",
        end="**FIG-03.** The spec MUST publish that during a layoff",
    ),
    Site(
        label="spec/03 3.7.3 establishment gate",
        root_index=0,
        rel="specification/spec/03-derived-metric-formulas.md",
        lead="- **Either position on a baseline that is not yet established**",
    ),
    Site(
        label="F005 Negative Class tier-change silence row",
        root_index=0,
        rel="spec-mirror/features/F005-resting-hrv-trend.md",
        lead="| The cost nobody had measured:",
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

    Reads the committed copy under ``spec-mirror/`` (T124), so it fails
    rather than skips where the machine-local ``.shipyard`` breadcrumb is
    absent.
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
