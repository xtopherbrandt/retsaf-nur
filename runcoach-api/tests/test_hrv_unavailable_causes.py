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
    ``hrv_unavailable`` after every reset (T116, re-confirmed at the corrected
    duration by T126) -- and both acceptances rest on the same sentence: that
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
