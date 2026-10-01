"""Scenario 34 (``@must``), the band's *documentation* half, pinned.

T136 (F005 review cycle 9, gap G-C9-7). The scenario's own words: *"verified
by a property sweep, not by the absence of one string"* -- that
``specification/spec/02`` (Sec 2.4.5), ``specification/spec/03`` (Sec
3.7.1-3.7.4, Sec 3.10), ``specification/spec/06`` (Sec 6.2.4),
``specification/spec_outline.md``, ``specification/research/00``,
``specification/research/05`` and the project rule
(``.claude/rules/project-domain-and-spec-fidelity.md``) all state the HRV
smallest-worthwhile-change band as **SWC_FACTOR * SD(ln rMSSD)**, never as
**SWC_FACTOR * CV(ln rMSSD)** -- the form the 2026-09-09 sweep withdrew
across those same seven files (``research/00-history.md`` H-09, which F008
moved there out of ``research/00``).

The **behavioural** half of this is solid and pinned:
``hrv_trend.build_band``, ``SWC_FACTOR``,
``test_the_band_is_unit_invariant`` and
``test_the_band_uses_the_sample_estimator_not_the_population_one``. The
**documentation** half -- the seven files above, restating the same formula
in prose -- has had no regression test at all. T126 and T133 are the same
defect shape one feature over: a figure corrected by hand went stale again
in a file the correction missed, until T133 derived it from the code instead
of retyping it. [[T128]] built a code-derived oracle for the
``hrv_unavailable`` cause enumeration. This is that pattern applied to the
oldest and broadest claim this feature makes.

**How the form is derived, not hardcoded.** ``SWC_FACTOR`` is read from
``hrv_trend`` directly (not retyped as the literal ``0.5``), and
``_build_band_multiplies_swc_factor_by_a_stdev_call`` reads ``build_band``'s
own AST to confirm it actually computes ``SWC_FACTOR * statistics.stdev(...)``
-- not ``pstdev`` (population SD), not a different constant's name. If either
moves, the derivation function goes red before any document is opened, and
the phrase every surface is held to (``f"{SWC_FACTOR}·SD(ln rMSSD)"``)
moves with the constant.

**Property, not allowlist ([[T119]]).** The seven named surfaces are checked
for *presence* of the derived form (a claim this specific has to be stated
explicitly enough to find), but *absence* of the withdrawn CV form is swept
over ``specification/**.md`` **and** ``.claude/rules/**.md`` as a walk, not a
list of seven paths -- ``.claude/rules/`` is not under ``specification/``, so
both roots are walked, each with its own floor, so a walk that has stopped
descending cannot report a silent all-clear.

**Quotation vs. claim.** ``research/00-history.md``'s H-09 entry is the record of
the 2026-09-09 clarification and legitimately quotes the withdrawn form as the
thing that was clarified away -- exactly what this module must not flag. Six
of the seven historical mentions in the corpus are wrapped in a markdown
quote (``"..."``) or a code span (`` `...` ``), and the property that
generalises over all of them is checked directly, on synthetic text, by
``test_the_quotation_guard_tells_a_live_claim_from_a_historical_quotation``
below -- not assumed. The one historical mention that survives *unquoted*
(the H-09 entry's own numeric comparison, "the literal
``0.5·CV(ln rMSSD)`` is 0.0138 (about 4× too narrow)", argued right
there in the same clause as *why* the form is wrong) is the one exclusion
this module carries by line-identity, mirroring
``test_hrv_trend_endpoint.py``'s own ``SCAN_EXCLUDED_HISTORY`` convention --
and, like that table, it is checked rather than trusted: it must still
carry a withdrawn form, or it is deleted (``test_...`` at the bottom of this
file).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from runcoach_api.metrics import hrv_trend

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# The derivation: read out of the code, not retyped as "0.5*SD"
# ---------------------------------------------------------------------------

#: Read from the module the corpus is describing, not typed as a literal --
#: a changed constant carries the phrase built below with it.
SWC_FACTOR = hrv_trend.SWC_FACTOR


def _module_tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer a module-level function: this oracle has nothing to read")


def _is_stdev_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "stdev")
        or (isinstance(node.func, ast.Name) and node.func.id == "stdev")
    )


def _build_band_multiplies_swc_factor_by_a_stdev_call() -> bool:
    """Does ``build_band`` bind some name to ``statistics.stdev(...)`` (sample
    SD -- never ``pstdev``, population SD, which T126 also pins against) and
    later multiply ``SWC_FACTOR`` by that same name, read from its own AST?

    The two live in separate statements in the real function (``dispersion =
    statistics.stdev(values)`` ... ``computed = SWC_FACTOR * dispersion``), so
    name bindings are traced across the function body rather than looked for
    in one expression. This is what ties the required phrase to *build_band's
    own arithmetic* rather than to a docstring both could drift from
    together: a renamed factor, a swap to ``pstdev``, or a formula that no
    longer multiplies the two at all turns this false, and every assertion
    below that depends on it is unreachable until it is fixed."""
    tree = _module_tree(hrv_trend)
    fn = _function(tree, "build_band")

    stdev_bound_names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_stdev_call(node.value):
            continue
        stdev_bound_names |= {target.id for target in node.targets if isinstance(target, ast.Name)}

    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.BinOp):
            continue
        if not isinstance(node.value.op, ast.Mult):
            continue
        sides = (node.value.left, node.value.right)
        names = {side.id for side in sides if isinstance(side, ast.Name)}
        if "SWC_FACTOR" in names and names & stdev_bound_names:
            return True
        # Also accept the call inlined directly in the product, in case a
        # future rewrite collapses the two statements into one.
        calls = [side for side in sides if isinstance(side, ast.Call)]
        if "SWC_FACTOR" in names and any(_is_stdev_call(call) for call in calls):
            return True
    return False


def test_the_derived_form_comes_from_build_bands_own_arithmetic() -> None:
    """The precondition for every assertion below: if ``build_band`` no
    longer literally computes ``SWC_FACTOR * statistics.stdev(...)``, this is
    where that shows up, before any spec file is ever opened."""
    assert _build_band_multiplies_swc_factor_by_a_stdev_call(), (
        "build_band no longer multiplies SWC_FACTOR by a statistics.stdev(...) call: this "
        "oracle's derivation of the band's documented form has nothing left to read, and every "
        "assertion below it would be pinning a stale phrase rather than the code"
    )


# ---------------------------------------------------------------------------
# Text normalisation and the two patterns it is read against
# ---------------------------------------------------------------------------

#: Markdown emphasis only -- unlike the shared ``_flat`` helper the sibling
#: HRV modules restate, quotes and backticks are kept here on purpose: the
#: quotation-guard below needs them to tell a historical quotation from a
#: live claim.
_MD_EMPHASIS = str.maketrans("", "", "*_")

#: ``·`` (U+00B7) and ``×`` are the only multiplication signs the corpus
#: uses for this formula (confirmed by grep across every site below); a bare
#: markdown ``*`` is never the operator here, only emphasis, which is why it
#: is safe to strip ``*`` above without confusing the two.
_MULT = "[·×]"
_FACTOR = re.escape(str(SWC_FACTOR))
_SD_PATTERN = re.compile(rf"{_FACTOR}\s*{_MULT}\s*sd\(ln rmssd\)")
_CV_PATTERN = re.compile(rf"{_FACTOR}\s*{_MULT}\s*cv\b")
_QUOTE_SPAN = re.compile(r'"[^"]*"|`[^`]*`')


def _normalize(text: str) -> str:
    """Lowercase, markdown emphasis stripped, whitespace collapsed. Quotes
    and code-span backticks are deliberately **not** stripped -- see the
    module docstring."""
    folded = text.translate(_MD_EMPHASIS)
    folded = " ".join(folded.split())
    return folded.lower()


def _live_cv_claims(text: str) -> list[str]:
    """Every match of the withdrawn ``<SWC_FACTOR>·CV`` form in ``text``
    that is **not** inside a quoted (``"..."``) or code (`` `...` ``) span --
    i.e. a live claim, not a quotation of the form the register clarified
    away on 2026-09-09."""
    normalized = _normalize(text)
    quoted_spans = [match.span() for match in _QUOTE_SPAN.finditer(normalized)]

    def _is_quoted(start: int, end: int) -> bool:
        return any(q_start <= start and end <= q_end for q_start, q_end in quoted_spans)

    return [match.group(0) for match in _CV_PATTERN.finditer(normalized) if not _is_quoted(*match.span())]


def test_the_quotation_guard_tells_a_live_claim_from_a_historical_quotation() -> None:
    """The classifier ``_live_cv_claims`` relies on, checked on synthetic
    text it did not come from -- a check authored by the thing it checks is
    not a check (``contract-tables-need-an-independent-oracle``). Every
    historical mention of the withdrawn form in this corpus is wrapped in a
    markdown quote or a code span; this is the property that generalises
    over all of them, verified directly rather than assumed."""
    live = "The SWC band is defined as 0.5·CV(ln rMSSD) in this release."
    quoted = 'the register originally read "0.5·CV(ln rMSSD)", since clarified 2026-09-09.'
    code_span = "spec/03 §3.7.3 transcribed it as `0.5 · CV(ln rMSSD)`."

    assert _live_cv_claims(live), (
        "an unquoted, unhedged CV claim was not flagged: the classifier would never catch a "
        "real reintroduction"
    )
    assert not _live_cv_claims(quoted), (
        "a double-quoted historical mention was flagged as a live claim: every historical "
        "mention in this corpus is quoted exactly this way, and the sweep below could never "
        "pass with research/00's own amendment paragraph in the tree if this were wrong"
    )
    assert not _live_cv_claims(code_span), "a backtick-quoted historical mention was flagged as a live claim"


# ---------------------------------------------------------------------------
# Presence: the seven named surfaces state the derived form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    label: str
    rel: str


#: The seven files research/00-history.md's H-09 names as restated by the
#: 2026-09-09 sweep: spec Sec 2.4.5, Sec 3.7.3/3.7.4/3.10 (one file, spec/03),
#: Sec 6.2.4, spec_outline.md Section 3, research/05 Sec 2.4 and Sec 6, and the
#: project rule -- research/00 itself is the eighth party (its HRV-07 states
#: the band), included here because it is a surface a reader meets the band's
#: form in. The record of the correction moved to the history file (F008).
SITES = (
    Site("spec/02 Sec 2.4.5 confidence and anti-mixing", "specification/spec/02-canonical-data-schema-ingestion.md"),
    Site("spec/03 Sec 3.7.1-3.7.4 and Sec 3.10", "specification/spec/03-derived-metric-formulas.md"),
    Site("spec/06 Sec 6.2.4 readiness fusion inputs", "specification/spec/06-adaptation-logic.md"),
    Site("spec_outline.md Section 3", "specification/spec_outline.md"),
    Site("research/00 the SWC band rule HRV-07", "specification/research/00-design-decisions.md"),
    Site("research/05 the HRV-guided-training rule", "specification/research/05-data-to-adaptation.md"),
    Site("the project rule", ".claude/rules/project-domain-and-spec-fidelity.md"),
)


@pytest.mark.parametrize("site", SITES, ids=[s.label for s in SITES])
def test_band_reconciliation_every_named_surface_states_the_derived_form(site: Site) -> None:
    path = _REPO_ROOT / site.rel
    assert path.exists(), f"{site.rel} is committed and must be present in every checkout"
    text = _normalize(path.read_text(encoding="utf-8"))
    assert _SD_PATTERN.search(text), (
        f"{site.label} ({site.rel}) does not state the band's form as "
        f"{SWC_FACTOR}·SD(ln rMSSD) -- derived from SWC_FACTOR and build_band's own "
        f"statistics.stdev call above, not hardcoded here as '0.5*SD'"
    )


# ---------------------------------------------------------------------------
# Absence: the withdrawn CV form, swept as a property over both trees
# ---------------------------------------------------------------------------

#: Two roots because ``.claude/rules/`` is **not** under ``specification/``
#: -- a sweep of one alone would leave the project rule (one of the seven
#: named surfaces) reachable only by the allowlist above, not by this walk.
SCAN_ROOTS = (_REPO_ROOT / "specification", _REPO_ROOT / ".claude" / "rules")

#: Measured 2026-09-17: 22 markdown files under specification/, 10 under
#: .claude/rules/. Set well under both, on the SCAN_ROOT_FLOORS convention
#: test_hrv_trend_endpoint.py uses: the floors' job is not to pin a count --
#: ordinary growth must not trip them -- it is to catch a walk that has
#: stopped descending and is reporting all-clear over nothing.
SCAN_ROOT_FLOORS = (15, 6)

#: The one historical-record exception ([[sweep-the-claim-not-the-diff]]
#: step 3, and test_hrv_trend_endpoint.py's own SCAN_EXCLUDED_HISTORY):
#: research/00-history.md's H-09 entry (the 2026-09-09 reconciliation, moved
#: out of research/00 by F008 and kept verbatim there) states two numerical
#: comparisons -- "0.5*CV of the *raw* rMSSD series is 0.0525" and "the
#: literal 0.5*CV(ln rMSSD) is 0.0138" -- neither wrapped in a quote or a
#: code span, both there only to argue the withdrawn form was numerically
#: close but wrong. Every other historical mention in the corpus is quote-
#: or backtick-wrapped and the general guard above handles it; this is the
#: one line that idiom does not cover. Excluded by line-identity, not by
#: file, and checked below rather than trusted: an exclusion that shelters
#: nothing must be deleted, not kept "just in case".
_HISTORICAL_RECORD_LINES = (
    ("specification/research/00-history.md", "SWC band statistic (clarification, 2026-09-09)"),
)


def _is_historical_record_line(rel: str, line: str) -> bool:
    normalized_line = _normalize(line)
    return any(rel == hist_rel and _normalize(lead) in normalized_line for hist_rel, lead in _HISTORICAL_RECORD_LINES)


def _markdown_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.md")))


def _live_cv_claims_in_file(path: Path, rel: str) -> list[str]:
    """``_live_cv_claims`` applied per line rather than to the whole file, so
    the one historical-record line above can be excluded surgically while
    everything else in the same file is still swept."""
    offenders: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _is_historical_record_line(rel, line):
            continue
        offenders.extend(_live_cv_claims(line))
    return offenders


def test_band_reconciliation_the_sweep_still_walks_whole_trees() -> None:
    """The floor half of the walk: a mistyped suffix, a root that stopped
    resolving, or a prune that swallowed the tree would leave the negative
    sweep below vacuously true. Checked before it runs."""
    assert len(SCAN_ROOT_FLOORS) == len(SCAN_ROOTS), (
        f"{len(SCAN_ROOT_FLOORS)} floors against {len(SCAN_ROOTS)} roots: a root with no floor "
        f"row is walked with nothing checking that the walk descended into it"
    )
    for root, floor in zip(SCAN_ROOTS, SCAN_ROOT_FLOORS, strict=True):
        assert root.exists(), f"{root} does not exist: the sweep has no root to walk"
        count = len(_markdown_files(root))
        assert count >= floor, (
            f"{root} yielded {count} markdown files, under the floor of {floor}: the walk has "
            f"stopped descending, so the all-clear below is a report over nothing"
        )


def test_band_reconciliation_the_withdrawn_cv_form_is_absent_from_every_swept_document() -> None:
    """[[T119]]'s lesson: a sweep keyed to the seven named surfaces above is
    an allowlist, and an allowlist passes as soon as the sentence *moves
    file*. Both committed trees are walked whole instead -- every markdown
    file under ``specification/`` and ``.claude/rules/``, not a known list of
    paragraphs -- so a reintroduction anywhere in either tree is what this
    reads, not just at the seven addresses the presence check above already
    knows about."""
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in _markdown_files(root):
            rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            hits = _live_cv_claims_in_file(path, rel)
            if hits:
                offenders.append(f"{rel}: {hits}")
    assert not offenders, (
        f"the withdrawn {SWC_FACTOR}·CV(ln rMSSD) form (clarified away 2026-09-09, "
        f"research/00-history.md H-09) is back, unquoted, in: " + "; ".join(offenders)
    )


def test_band_reconciliation_the_one_historical_record_exclusion_still_shelters_a_withdrawn_form() -> None:
    """Mirrors test_hrv_trend_endpoint.py's own check on its
    SCAN_EXCLUDED_HISTORY table: an exclusion that shelters nothing is
    widening the blind spot for nothing and must be deleted rather than kept
    in case it is needed again."""
    for rel, lead in _HISTORICAL_RECORD_LINES:
        path = _REPO_ROOT / rel
        assert path.exists(), f"{path} is committed and must be present in every checkout"
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if _normalize(lead) in _normalize(line)]
        assert len(lines) == 1, (
            f"{rel}: {len(lines)} lines carry the lead {lead!r}, not 1 -- the entry has moved, "
            f"been reworded or been duplicated"
        )
        assert _CV_PATTERN.search(_normalize(lines[0])), (
            f"{rel}'s historical-record exclusion for {lead!r} shelters no withdrawn CV form: "
            f"delete the exclusion, it is protecting nothing"
        )
