"""The source-change rule, swept tree-wide: every surface that states what
happens when the athlete's resting-HRV source tier changes states the
**per-tier dataset** form, and none of them still states the superseded
single-baseline re-establishment.

T149 (F006, sprint-006). ``research/00`` Sec 3.3 said that when the primary
source tier changes -- the athlete adopts or abandons the chest strap -- the
system *treats it as a baseline re-establishment*: one baseline, owned by one
tier, every other tier's readings excluded ``off_baseline_tier``, every
verdict withheld until the new tier's baseline is established. F006 gives
each tier its own baseline and band, so a *return* to an already-established
dataset is free, which contradicts that clause directly. The authority is
amended first (``research/00`` Sec 5.4), then ``spec/03`` Sec 3.7.3/3.7.4 and
``spec/02`` Sec 2.4.5 are restated to match (project rule: ``research_00``
governs), and this module is the witness that the claim was swept tree-wide
and not just at the sites the diff happened to touch
(``sweep-the-claim-not-the-diff``).

**Modelled on ``test_band_reconciliation.py``.** Two halves, and the same
two-root walk with a floor per root:

* **Presence**, one parametrised row per surface: the paragraph anchored by
  its lead phrase must carry the per-tier-dataset form. The anchor is
  required to be unique in its file, so a paragraph that moves or is
  duplicated is reported, not silently passed over.
* **Absence**, as a property over both committed trees: no markdown file
  under ``specification/`` or ``.claude/rules/`` states any of the
  superseded phrasings as a live claim. ``research/00``'s Sec 5.4 entries
  legitimately quote the withdrawn form as the thing that was amended
  away; a mention inside a markdown quote (``"..."``) or a code span
  (`` `...` ``) is a quotation, not a claim, and the guard that tells the two
  apart is checked on synthetic text rather than assumed.

**``off_baseline_tier``.** [[T152]] retires the exclusion reason from the
code. ``research/00``'s two reproductions (the 2026-09-16 bullets) name it
normatively, so unless the authority marks it retired it outlives the code.
Every mention in either tree must sit on a line that says so.

**A witness must print the slice it compared.** Each test prints the
``file:line`` and the text it read the claim in, so a run with ``-rA`` (or a
failure) shows what was actually held to the rule rather than an exit code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# The two forms: the one every surface must state, the ones none may
# ---------------------------------------------------------------------------

#: The per-tier-dataset form, as research/00 Sec 5.4's 2026-09-18 amendment
#: names it and as the task's acceptance probe greps for it. Matched on the
#: normalised (lowercased, emphasis-stripped) text, so "per-tier datasets"
#: and "**per-tier dataset** model" both count.
REQUIRED_FORM = re.compile(r"per-tier dataset")

#: The superseded single-baseline phrasings, one per surface they were
#: measured at on 2026-09-18 (grep over both roots before the amendment):
#: research/00 Sec 3.3 (two paragraphs), spec/03 Sec 3.7.3 and Sec 3.7.4,
#: spec/02 Sec 2.4.5 (two paragraphs). Each is the clause that made a source
#: change re-establish the one baseline, or that made one tier own it.
SUPERSEDED_FORMS = (
    "treats it as a baseline re-establishment",
    "re-establishes the baseline rather than reading the source switch",
    "re-establishing the baseline there only when that is a sustained source change",
    "begins accumulating a fresh baseline for the new source",
    "degradation moves the whole baseline to the next tier",
    "the baseline is built on the highest tier that sustains one",
    "the trend is built on the highest-fidelity tier that is present densely enough to sustain a baseline",
)

#: The exclusion reason T152 retires; a live mention must say it is retired.
RETIRED_EXCLUSION = "off_baseline_tier"
_RETIRED_MARK = re.compile(r"\bretired\b")

#: Markdown emphasis only. Underscores are kept, because ``off_baseline_tier``
#: is a token this module reads; quotes and backticks are kept, because the
#: quotation guard needs them.
_MD_EMPHASIS = str.maketrans("", "", "*")
_QUOTE_SPAN = re.compile(r'"[^"]*"|“[^”]*”|`[^`]*`')


def _normalize(text: str) -> str:
    folded = text.translate(_MD_EMPHASIS)
    folded = " ".join(folded.split())
    return folded.lower()


def _live_superseded_claims(text: str) -> list[str]:
    """Every superseded phrasing in ``text`` that is **not** inside a quoted
    or code span -- a live claim, not a quotation of the form amended away."""
    normalized = _normalize(text)
    quoted_spans = [match.span() for match in _QUOTE_SPAN.finditer(normalized)]

    def _is_quoted(start: int, end: int) -> bool:
        return any(q_start <= start and end <= q_end for q_start, q_end in quoted_spans)

    hits: list[str] = []
    for form in SUPERSEDED_FORMS:
        for match in re.finditer(re.escape(form), normalized):
            if not _is_quoted(*match.span()):
                hits.append(form)
    return hits


def test_the_quotation_guard_tells_a_live_claim_from_a_historical_quotation() -> None:
    """Checked on text it did not come from: research/00 Sec 5.4 must be able
    to quote the superseded clause as the thing it amended, and a bare
    restatement anywhere must still be caught."""
    live = "When the primary source tier changes the system treats it as a baseline re-establishment."
    quoted = 'Sec 3.3 said a source change "treats it as a baseline re-establishment"; superseded 2026-09-18.'
    curly = "Sec 3.3 said a source change “treats it as a baseline re-establishment”; superseded."
    code_span = "the clause read `treats it as a baseline re-establishment` until F006."
    emphasised = "the system treats it as a **baseline re-establishment**, not a continuation"

    assert _live_superseded_claims(live), "an unquoted restatement was not flagged"
    assert _live_superseded_claims(emphasised), "markdown emphasis inside the clause hid it from the guard"
    assert not _live_superseded_claims(quoted), "a double-quoted historical mention was flagged as live"
    assert not _live_superseded_claims(curly), "a curly-quoted historical mention was flagged as live"
    assert not _live_superseded_claims(code_span), "a backtick-quoted historical mention was flagged as live"


# ---------------------------------------------------------------------------
# Presence: one row per surface that states the source-change rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    label: str
    rel: str
    #: The lead phrase of the paragraph (one markdown line) that states the
    #: rule. Must occur on exactly one line of the file.
    anchor: str


SITES = (
    Site(
        "research/00 Sec 3.3 the four-tier hierarchy",
        "specification/research/00-design-decisions.md",
        "**The resolution — a four-tier resting-HRV source hierarchy",
    ),
    Site(
        "research/00 Sec 3.3 the anti-mixing constraint",
        "specification/research/00-design-decisions.md",
        "**Two constraints the tiering carries.**",
    ),
    Site(
        "research/00 Sec 5.4 the 2026-09-18 amendment",
        "specification/research/00-design-decisions.md",
        "(amendment, 2026-09-18).**",
    ),
    Site(
        "spec/03 Sec 3.7.3 per-source baseline discipline",
        "specification/spec/03-derived-metric-formulas.md",
        "**Per-source baseline discipline (the anti-mixing rule).**",
    ),
    Site(
        "spec/03 Sec 3.7.4 graceful degradation",
        "specification/spec/03-derived-metric-formulas.md",
        "**Graceful degradation across tiers, then unavailable.**",
    ),
    Site(
        "spec/02 Sec 2.4.5 confidence and the anti-mixing rule",
        "specification/spec/02-canonical-data-schema-ingestion.md",
        "**Confidence and the anti-mixing rule.**",
    ),
    Site(
        "spec/02 Sec 2.4.5 degradation",
        "specification/spec/02-canonical-data-schema-ingestion.md",
        "**Degradation.** Degradation is decided at",
    ),
)


def _anchored_line(site: Site) -> tuple[int, str]:
    path = _REPO_ROOT / site.rel
    assert path.exists(), f"{site.rel} is committed and must be present in every checkout"
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [(number, line) for number, line in enumerate(lines, start=1) if site.anchor in line]
    assert len(hits) == 1, (
        f"{site.rel}: {len(hits)} lines carry the anchor {site.anchor!r}, not 1 -- the paragraph "
        f"has moved, been reworded or been duplicated, and this row is reading nothing"
    )
    return hits[0]


def _slice(rel: str, number: int, line: str, width: int = 240) -> str:
    text = _normalize(line)
    shown = text if len(text) <= width else text[:width] + " …"
    return f"{rel}:{number} ({len(text)} chars): {shown}"


@pytest.mark.parametrize("site", SITES, ids=[s.label for s in SITES])
def test_every_surface_states_the_per_tier_dataset_form(site: Site) -> None:
    number, line = _anchored_line(site)
    print(f"[slice compared] {_slice(site.rel, number, line)}")
    assert REQUIRED_FORM.search(_normalize(line)), (
        f"{site.label} ({site.rel}:{number}) does not state the source-change rule in its "
        f"per-tier dataset form (research/00 Sec 5.4, 2026-09-18): it still describes one baseline"
    )


@pytest.mark.parametrize("site", SITES, ids=[s.label for s in SITES])
def test_no_surface_still_states_the_single_baseline_form_as_a_live_claim(site: Site) -> None:
    number, line = _anchored_line(site)
    print(f"[slice compared] {_slice(site.rel, number, line)}")
    live = _live_superseded_claims(line)
    assert not live, (
        f"{site.label} ({site.rel}:{number}) still states the superseded single-baseline "
        f"re-establishment as a live claim: {live}"
    )


# ---------------------------------------------------------------------------
# Absence: swept as a property over both trees, with floors
# ---------------------------------------------------------------------------

SCAN_ROOTS = (_REPO_ROOT / "specification", _REPO_ROOT / ".claude" / "rules")

#: Measured 2026-09-18: 22 markdown files under specification/, 12 under
#: .claude/rules/. Set well under both, on test_band_reconciliation.py's
#: convention: the floors catch a walk that has stopped descending, not
#: ordinary growth.
SCAN_ROOT_FLOORS = (15, 6)


def _markdown_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.md")))


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


def test_the_sweep_still_walks_whole_trees() -> None:
    assert len(SCAN_ROOT_FLOORS) == len(SCAN_ROOTS), (
        f"{len(SCAN_ROOT_FLOORS)} floors against {len(SCAN_ROOTS)} roots: a root with no floor "
        f"row is walked with nothing checking that the walk descended into it"
    )
    for root, floor in zip(SCAN_ROOTS, SCAN_ROOT_FLOORS, strict=True):
        assert root.exists(), f"{root} does not exist: the sweep has no root to walk"
        count = len(_markdown_files(root))
        print(f"[slice compared] {_rel(root)}: {count} markdown files (floor {floor})")
        assert count >= floor, (
            f"{root} yielded {count} markdown files, under the floor of {floor}: the walk has "
            f"stopped descending, so the all-clear below is a report over nothing"
        )


def test_the_single_baseline_form_is_absent_from_every_swept_document() -> None:
    """An allowlist passes as soon as the sentence moves file; both trees are
    walked whole, per line, so a live restatement anywhere is what this
    reads -- not only the seven anchored paragraphs above."""
    offenders: list[str] = []
    files_read = 0
    for root in SCAN_ROOTS:
        for path in _markdown_files(root):
            files_read += 1
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                live = _live_superseded_claims(line)
                if live:
                    offenders.append(f"{_rel(path)}:{number}: {live}")
    print(f"[slice compared] {files_read} markdown files across {[_rel(r) for r in SCAN_ROOTS]}")
    assert not offenders, (
        "the superseded single-baseline re-establishment (amended away 2026-09-18, research/00 "
        "Sec 5.4) is stated as a live claim in: " + "; ".join(offenders)
    )


def test_every_off_baseline_tier_mention_is_marked_retired() -> None:
    """T152 retires the exclusion reason; the authority's two reproductions
    that name it must say so on the same line, or the document outlives the
    code. Also asserts the mentions still exist: the historical record is
    marked, not erased."""
    mentions: list[tuple[str, int, str]] = []
    for root in SCAN_ROOTS:
        for path in _markdown_files(root):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if RETIRED_EXCLUSION in line:
                    mentions.append((_rel(path), number, line))
    for rel, number, line in mentions:
        print(f"[slice compared] {_slice(rel, number, line, width=160)}")
    assert mentions, (
        f"no swept document mentions {RETIRED_EXCLUSION}: research/00's two 2026-09-16 reproductions "
        f"were erased rather than marked retired, and this test is reading nothing"
    )
    unmarked = [f"{rel}:{number}" for rel, number, line in mentions if not _RETIRED_MARK.search(line)]
    assert not unmarked, (
        f"{RETIRED_EXCLUSION} is named without being marked retired (T152, research/00 Sec 5.4 "
        f"2026-09-18) at: {unmarked}"
    )
