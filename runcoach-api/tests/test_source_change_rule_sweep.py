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
  superseded phrasings as a live claim. Each file is read **whole and
  flattened** rather than line by line, because a claim that wraps across a
  newline is invisible to a line-oriented scan and three of the seven
  ``SUPERSEDED_FORMS`` are longer than the column at which one scan root is
  wrapped (sprint-006 review iteration 1, S1; ``_flat_text``).
  ``research/00``'s Sec 5.4 entries
  legitimately quote the withdrawn form as the thing that was amended
  away; a mention inside a markdown quote (``"..."``) or a code span
  (`` `...` ``) is a quotation, not a claim, and the guard that tells the two
  apart is checked on synthetic text rather than assumed.

**``off_baseline_tier``.** [[T152]] retires the exclusion reason from the
code. ``research/00``'s two reproductions (the 2026-09-16 bullets) name it
normatively, so unless the authority marks it retired it outlives the code.
Every mention in either tree must sit on a line that says so.

**A witness must print the slice it compared.** Each test prints the
``file@offset`` -- a character offset into the flattened file, since a line
number is a fiction once the line breaks are gone -- and the text it read the
claim in, so a run with ``-rA`` (or a failure) shows what was actually held to
the rule rather than an exit code.
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

#: A markdown paragraph boundary: a blank line, whatever whitespace it holds.
#: The unit ``_flat_paragraphs`` cuts on, because a claim can wrap across a
#: line break and cannot wrap across a blank one.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _normalize(text: str) -> str:
    folded = text.translate(_MD_EMPHASIS)
    folded = " ".join(folded.split())
    return folded.lower()


def _live_superseded_hits(text: str) -> list[tuple[int, str]]:
    """Every superseded phrasing in ``text`` that is **not** inside a quoted
    or code span -- a live claim, not a quotation of the form amended away --
    each with its **character offset** into ``_normalize(text)``.

    An offset rather than a line number, because the text this is run over is
    flattened whole-file (see ``_flat_text``): once the line breaks are gone a
    line number is a fiction, and the offset locates the hit in the very
    string the guard read."""
    normalized = _normalize(text)
    quoted_spans = [match.span() for match in _QUOTE_SPAN.finditer(normalized)]

    def _is_quoted(start: int, end: int) -> bool:
        return any(q_start <= start and end <= q_end for q_start, q_end in quoted_spans)

    hits: list[tuple[int, str]] = []
    for form in SUPERSEDED_FORMS:
        for match in re.finditer(re.escape(form), normalized):
            if not _is_quoted(*match.span()):
                hits.append((match.start(), form))
    return sorted(hits)


def _live_superseded_claims(text: str) -> list[str]:
    """The forms alone, for the callers that report membership rather than
    position."""
    return [form for _offset, form in _live_superseded_hits(text)]


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


def test_the_file_scan_reads_a_claim_that_wraps_across_a_line_break(tmp_path) -> None:
    """The defect this module carried until sprint-006 review iteration 1
    (S1). The tree walk read each file **line by line** and normalised
    whitespace only *within* a line, so a superseded phrasing hard-wrapped
    across a newline was invisible to it. Three of the seven
    ``SUPERSEDED_FORMS`` are 78-103 characters and one scan root
    (``.claude/rules/**``) is wrapped at 95-130 columns, so the all-clear was
    one reflow away from being a report over nothing --
    ``sweep-the-claim-not-the-diff`` is named for exactly that false
    all-clear, and this is the one module written to prevent it.

    Asserted through ``_offenders_in``, the function the tree walk itself
    calls, so the fix is pinned at the scope the real sweep runs at rather
    than in a parallel reimplementation. The files are synthetic because
    Scanner C re-ran both roots flattened and found **no live miss** in the
    tree as it stands: what is closed here is a latent guard failure, and a
    latent failure can only be pinned by a case the corpus does not supply.

    Three files, because the fix has three parts that can regress separately:
    the wrap, the case folding, and the quotation guard that must survive both.
    """
    wrapped = tmp_path / "wrapped.md"
    wrapped.write_text(
        "When the primary source tier changes the system treats it as a\nbaseline re-establishment.\n",
        encoding="utf-8",
    )
    shouted = tmp_path / "shouted.md"
    shouted.write_text(
        "The system TREATS IT AS A BASELINE RE-ESTABLISHMENT, not a continuation.\n",
        encoding="utf-8",
    )
    quoted = tmp_path / "quoted.md"
    quoted.write_text(
        'Sec 3.3 said a source change "treats it as a\nbaseline re-establishment"; superseded 2026-09-18.\n',
        encoding="utf-8",
    )

    for path in (wrapped, shouted, quoted):
        print(f"[slice compared] {path.name}: {_offenders_in(path) or 'no live claim'}")

    assert _offenders_in(wrapped), (
        "a superseded claim wrapped across a line break was not flagged: the scan is "
        "line-oriented again and will pass vacuously on the next reflow of a rules file"
    )
    assert _offenders_in(shouted), "the sweep is case-sensitive again"
    assert not _offenders_in(quoted), (
        "flattening must not cost the quotation guard: a historical quotation that happens to "
        "wrap is still a quotation"
    )


# ---------------------------------------------------------------------------
# Presence: one row per surface that states the source-change rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    label: str
    rel: str
    #: The lead phrase of the paragraph that states the rule. Must occur
    #: exactly once in the file's flattened text, and is matched there --
    #: case-insensitively, through emphasis, and across a line break.
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


def _flat_text(path: Path) -> str:
    """The file's **whole** text, normalised and flattened to one string.

    This is the fix for the line-oriented scope this module shipped with
    (sprint-006 review iteration 1, S1): ``_normalize`` collapses every run of
    whitespace to a single space, so applied to the whole file rather than to
    one line it erases the line breaks a hard-wrapped claim hides behind. It
    is the approach ``test_hrv_trend_endpoint._scannable`` already takes, for
    the same reason and after the same false all-clear.

    Unlike ``_flat`` there, quote characters are **kept**: the guard that
    tells a live claim from research/00's historical reproduction of it is
    built out of them. Measured 2026-09-21 over both roots, whole-file
    flattening leaves at most 9.13% of a file inside a quoted span and the
    longest single span at 207 characters, so no runaway span swallows a
    document -- and ``test_the_scan_of_every_file_can_still_see_a_claim``
    asserts that per file rather than trusting the measurement.
    """
    return _normalize(path.read_text(encoding="utf-8"))


def _flat_paragraphs(path: Path) -> tuple[tuple[int, str], ...]:
    """Each blank-line-separated block of the file, normalised, with its
    character offset into ``_flat_text(path)``.

    The presence half needs a **paragraph** rather than a whole file: "this
    surface states the rule" is a claim about the paragraph the anchor leads,
    not about the document holding the words somewhere. Joining these blocks
    with single spaces reproduces ``_flat_text`` exactly, which is what makes
    the offsets real rather than approximate, and which
    ``test_the_flattened_paragraphs_reconstruct_the_flattened_file`` asserts.
    """
    blocks: list[tuple[int, str]] = []
    offset = 0
    for raw in _PARAGRAPH_BREAK.split(path.read_text(encoding="utf-8")):
        block = _normalize(raw)
        if not block:
            continue
        blocks.append((offset, block))
        offset += len(block) + 1
    return tuple(blocks)


def _anchored_paragraph(site: Site) -> tuple[int, str]:
    """The flattened paragraph its lead phrase anchors, and where it starts.

    Both the anchor and the text are normalised, so the anchor is matched
    case-insensitively, through markdown emphasis and across a line break --
    the three ways the old ``site.anchor in line`` form could fail to find a
    paragraph that had merely been reflowed, each of which would have reported
    "the paragraph has moved" over a paragraph that had not."""
    path = _REPO_ROOT / site.rel
    assert path.exists(), f"{site.rel} is committed and must be present in every checkout"
    anchor = _normalize(site.anchor)
    text = _flat_text(path)
    occurrences = text.count(anchor)
    assert occurrences == 1, (
        f"{site.rel}: the anchor {site.anchor!r} occurs {occurrences} times in the flattened "
        f"file, not 1 -- the paragraph has moved, been reworded or been duplicated, and this row "
        f"is reading nothing"
    )
    at = text.index(anchor)
    holding = [
        (offset, block)
        for offset, block in _flat_paragraphs(path)
        if offset <= at < offset + len(block)
    ]
    assert len(holding) == 1, (
        f"{site.rel}: the anchor at offset {at} falls inside {len(holding)} paragraphs, not 1"
    )
    return holding[0]


def _slice(rel: str, offset: int, text: str, width: int = 240) -> str:
    """One compared slice, named by character offset into the flattened file.

    A line number would be a fiction over flattened text; the offset is a
    position in the very string the assertion read, recoverable by hand as
    ``_flat_text(path)[offset:]``."""
    shown = text if len(text) <= width else text[:width] + " …"
    return f"{rel}@{offset} ({len(text)} chars): {shown}"


@pytest.mark.parametrize("site", SITES, ids=[s.label for s in SITES])
def test_every_surface_states_the_per_tier_dataset_form(site: Site) -> None:
    offset, paragraph = _anchored_paragraph(site)
    print(f"[slice compared] {_slice(site.rel, offset, paragraph)}")
    assert REQUIRED_FORM.search(paragraph), (
        f"{site.label} ({site.rel}@{offset}) does not state the source-change rule in its "
        f"per-tier dataset form (research/00 Sec 5.4, 2026-09-18): it still describes one baseline"
    )


@pytest.mark.parametrize("site", SITES, ids=[s.label for s in SITES])
def test_no_surface_still_states_the_single_baseline_form_as_a_live_claim(site: Site) -> None:
    offset, paragraph = _anchored_paragraph(site)
    print(f"[slice compared] {_slice(site.rel, offset, paragraph)}")
    live = _live_superseded_claims(paragraph)
    assert not live, (
        f"{site.label} ({site.rel}@{offset}) still states the superseded single-baseline "
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
    """The repository-relative path, or the bare name for a file outside the
    tree: ``_offenders_in`` is pinned on synthetic files in a tmp dir, and a
    reporting helper must not raise on the input its own pin hands it."""
    try:
        return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


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


def _swept_files() -> tuple[Path, ...]:
    return tuple(path for root in SCAN_ROOTS for path in _markdown_files(root))


def _offenders_in(path: Path) -> list[str]:
    """Every live superseded claim in one file, named by character offset.

    The tree walk below and
    ``test_the_file_scan_reads_a_claim_that_wraps_across_a_line_break`` call
    **this** function, so the synthetic wrapped case pins the scope the real
    sweep runs at."""
    return [
        f"{_rel(path)}@{offset}: {form!r}"
        for offset, form in _live_superseded_hits(_flat_text(path))
    ]


def test_the_flattened_paragraphs_reconstruct_the_flattened_file() -> None:
    """The offsets ``_flat_paragraphs`` reports are positions in the string
    ``_flat_text`` returns, and that is asserted rather than assumed: joining
    the paragraphs with single spaces must reproduce the flattened file
    exactly. Without it the presence rows would report a location that does
    not exist, which is the quiet half of a sweep nobody can re-derive."""
    files = _swept_files()
    for path in files:
        rebuilt = " ".join(block for _offset, block in _flat_paragraphs(path))
        assert rebuilt == _flat_text(path), (
            f"{_rel(path)}: the paragraph split does not reconstruct the flattened file, so the "
            f"character offsets this module reports are not positions in it"
        )
    print(f"[slice compared] {len(files)} files reconstruct from their own paragraphs")


def test_the_scan_of_every_file_can_still_see_a_claim() -> None:
    """The non-vacuity guard the flattening needs.

    Flattening a whole file widens the reach of the quotation guard's spans: a
    stray quote or backtick pairing with a distant one could in principle
    swallow a live claim and turn this module's all-clear into a report over
    nothing -- the shape the fence in ``test_hrv_trend_endpoint._scannable``
    failed at twice, which is why there is no fence there now.

    So rather than resting on the 2026-09-21 measurement in ``_flat_text``,
    every swept file is probed: a superseded form appended to its flattened
    text must be flagged. A file whose scan ends inside an open span fails
    here instead of passing silently."""
    probe = SUPERSEDED_FORMS[0]
    blind = [
        _rel(path)
        for path in _swept_files()
        if not _live_superseded_claims(_flat_text(path) + " " + probe)
    ]
    shares = []
    for path in _swept_files():
        text = _flat_text(path)
        quoted = sum(m.end() - m.start() for m in _QUOTE_SPAN.finditer(text))
        shares.append((quoted / max(len(text), 1), _rel(path)))
    print("[slice compared] widest quoted spans: "
          + ", ".join(f"{rel} {share:.2%}" for share, rel in sorted(shares, reverse=True)[:3]))
    assert not blind, (
        "the flattened scan of these files cannot see a claim appended to them, so their "
        "all-clear below is a report over nothing -- an unbalanced quote or backtick has opened "
        "a span running to end of file: " + "; ".join(blind)
    )


def test_the_single_baseline_form_is_absent_from_every_swept_document() -> None:
    """An allowlist passes as soon as the sentence moves file; both trees are
    walked whole, so a live restatement anywhere is what this reads -- not
    only the seven anchored paragraphs above.

    **Flattened whole-file, case-folded, reported by character offset**
    (sprint-006 review iteration 1, S1). This walked the trees *per line* and
    normalised whitespace only within a line, so a superseded phrasing
    hard-wrapped across a newline was invisible to it -- the false all-clear
    ``sweep-the-claim-not-the-diff`` is named for, in the one module written
    to prevent it. Scanner C re-ran the sweep flattened and found no live miss
    in the tree as it stands, so what is closed is a latent guard failure: it
    was one reflow of a rules file away from passing vacuously.
    ``test_the_file_scan_reads_a_claim_that_wraps_across_a_line_break`` holds
    the wrapped case, on synthetic files, since the tree supplies none.
    """
    offenders: list[str] = []
    files = _swept_files()
    for path in files:
        offenders.extend(_offenders_in(path))
    print(f"[slice compared] {len(files)} markdown files across {[_rel(r) for r in SCAN_ROOTS]}, "
          f"flattened whole-file, {sum(len(_flat_text(path)) for path in files)} characters read")
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
