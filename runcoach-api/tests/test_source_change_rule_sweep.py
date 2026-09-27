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
  quoted the withdrawn form as the thing that was amended away until F008
  (sprint-007 T175) moved them to ``research/00-history.md``, which
  paraphrases; a mention inside a markdown quote (``"..."``) or a code span
  (`` `...` ``) is a quotation, not a claim, and the guard that tells the two
  apart is checked on synthetic text rather than assumed.

**``off_baseline_tier``.** [[T152]] retires the exclusion reason from the
code. ``research/00``'s HRV-44 names it (the two 2026-09-16 reproductions that
named it normatively were rewritten by F008), so unless the authority marks it
retired it outlives the code. Every mention in either tree must sit on a line that says so.

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
from markdown_it import MarkdownIt

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
        "research/00 HRV-01 the three input tiers",
        "specification/research/00-design-decisions.md",
        "**HRV-01.** Resting HRV MUST come through three input tiers",
    ),
    Site(
        "research/00 HRV-06 the anti-mixing constraint",
        "specification/research/00-design-decisions.md",
        "**HRV-06.** Because different sources carry different systematic biases",
    ),
    Site(
        "research/00 HRV-10 every reading feeds its own tier's dataset",
        "specification/research/00-design-decisions.md",
        "**HRV-10.** Every resting-HRV reading MUST feed the dataset of its own tier",
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
    built out of them. How much of a file that guard may suppress is **not**
    published here as prose. It is asserted, per file, by
    ``test_no_quoted_span_can_swallow_a_document`` against
    ``QUOTED_SHARE_CEILING`` and ``LONGEST_QUOTED_SPAN_CEILING``, which carry
    the re-measurement, the roots it spans and the margin above it.

    This docstring previously published "at most 9.13% of a file inside a
    quoted span ... measured over both roots". The figure was measured over
    ``specification/`` alone -- the true maximum over both roots is 13.11%,
    and all five widest files are under ``.claude/rules/**`` -- and nothing
    in the tree checked either number (sprint-006 review iteration 2, M4).
    A number in a docstring that nothing checks is a claim, not a measurement.

    **Fenced code blocks are stripped first** (``_swept_source``), so this is
    the very string ``_span_profile`` measures. Until sprint-007 review
    iteration 1 (M1) only ``_span_profile`` stripped them: a fence whose body
    held an odd number of backticks was invisible to the parity, crossing and
    ceiling arms, while here its stray backtick paired with a later code span
    and a live claim between them read as a quotation -- every guard green
    over a claim the sweep could not see.

    Stripping loses nothing the sweep could report **only if each fence ends
    where CommonMark ends it**: a fence's body is code, and prose between two
    fences is not. The first stripper was a regex that closed a fence only on
    a run of exactly the opener's length, so ``~~~`` closed by ``~~~~`` (or
    a three-backtick fence closed by four backticks) stayed open to the next
    fence and took the prose between them, claim included (sprint-007 review
    iteration 2, S2). The fences are now markdown-it's ``fence`` tokens
    (``_strip_fences``), and
    ``test_a_longer_closing_fence_cannot_hide_a_claim_from_the_sweep`` holds
    both red cases.
    """
    return _normalize(_swept_source(path))


def _swept_source(path: Path) -> str:
    """The file's raw text with every fenced code block removed: the one
    string the absence sweep, the presence rows and the span guards all read
    (sprint-007 review iteration 1, M1). The fences are markdown-it's
    ``fence`` tokens (``_strip_fences``; iteration 2, S2)."""
    return _strip_fences(path.read_text(encoding="utf-8"))


def _flat_paragraphs(path: Path) -> tuple[tuple[int, str], ...]:
    """Each blank-line-separated block of the file, normalised, with its
    character offset into ``_flat_text(path)``.

    The presence half needs a **paragraph** rather than a whole file: "this
    surface states the rule" is a claim about the paragraph the anchor leads,
    not about the document holding the words somewhere. Joining these blocks
    with single spaces reproduces ``_flat_text`` exactly, which is what makes
    the offsets real rather than approximate, and which
    ``test_the_flattened_paragraphs_reconstruct_the_flattened_file`` asserts.
    Cut from ``_swept_source``, fences stripped, like ``_flat_text``.
    """
    return _paragraphs_of(_swept_source(path))


def _paragraphs_of(source: str) -> tuple[tuple[int, str], ...]:
    """``_flat_paragraphs`` over a string already read."""
    blocks: list[tuple[int, str]] = []
    offset = 0
    for raw in _PARAGRAPH_BREAK.split(source):
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


# ---------------------------------------------------------------------------
# What the quotation guard is allowed to hide
# ---------------------------------------------------------------------------

#: Ceilings on the quotation guard's reach over a whole-file flattening,
#: asserted per file by ``test_no_quoted_span_can_swallow_a_document``.
#:
#: **Re-measured 2026-09-21 over both scan roots** -- ``specification/`` and
#: ``.claude/rules/``, the two entries of ``SCAN_ROOTS``, every ``*.md`` under
#: each, 34 files -- with this module's own ``_QUOTE_SPAN`` and ``_flat_text``.
#: Those are the axes the measurement ranges over; nothing else is held fixed
#: (``a-sweep-must-name-the-axes-it-holds-constant``). Observed maxima: share
#: **13.11%** (``.claude/rules/project-api-contract.md``) and longest single
#: span **207** characters (``specification/research/00-design-decisions.md``).
#:
#: **Re-measured 2026-09-25 (sprint-007 T169) after the fence strip**, which
#: ``_span_profile`` now applies before measuring (then a regex, now ``_strip_fences``; B-CR-001
#: Sec 3). Same roots, same 34 files, same ``_QUOTE_SPAN``, over the
#: fence-stripped flattening rather than ``_flat_text``. The one fenced file,
#: ``project-api-contract.md``, falls from 13.11% to **10.53%**, so the maximum
#: share is now **11.51%**
#: (``.claude/rules/learnings/a-sweep-must-name-the-axes-it-holds-constant.md``)
#: and the longest span is still **207** (research/00).
#:
#: **Re-measured 2026-09-25 (sprint-007 T175) after the research/00 cut-over.**
#: Same roots, same ``_QUOTE_SPAN``, fences stripped; **36 files** now, the two
#: added being ``research/00-history.md`` and ``research/00-traceability.md``.
#: The maximum share is unchanged at **11.51%** (the same file), and the
#: longest span falls from 207 to **161**
#: (``specification/spec/05-training-plan-generation.md``): the rewrite removed
#: research/00's 207-character span.
#:
#: **Re-measured 2026-09-25 (sprint-007 review iteration 1)** at 9cb592c, the
#: fence strip now in ``_flat_text`` itself (M1): same roots, same
#: ``_QUOTE_SPAN``; **37 files**, the meaning review (T180) added. Maximum share
#: **11.51%** (the same file), longest span **161** (the same file), 0
#: paragraph-crossing spans.
#:
#: The 9.13% this module published before, as "over both roots", was measured
#: over ``specification/`` alone: all five widest files are under
#: ``.claude/rules/**`` (sprint-006 review iteration 2, M4).
#:
#: **The margin, and why there is one.** Set at ~1.5x the 13.11% share and
#: ~2x the measured longest span, deliberately not the measured values, and
#: left unchanged by the re-measurement (now ~1.7x the 11.51% share). The widest
#: files are 1.5-3 kB rules documents, where adding one quoted sentence or code
#: span moves the share by a point or two and the longest span by a few dozen
#: characters; a ceiling pinned at the measured share reds on the next ordinary prose edit
#: and is then raised without anyone thinking about it, which is how a ceiling
#: stops being a guard. What these exist to catch is not incremental: one
#: unpairable delimiter pairs with a distant one and the span grows by
#: kilobytes, clearing both ceilings by an order of magnitude.
QUOTED_SHARE_CEILING = 0.20
LONGEST_QUOTED_SPAN_CEILING = 400


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """The spans ``_live_superseded_hits`` treats as quotation, over text that
    has already been flattened by ``_flat_text``."""
    return [match.span() for match in _QUOTE_SPAN.finditer(text)]


def _delimiter_imbalance(text: str) -> list[str]:
    """Every quote delimiter in ``text`` whose occurrences cannot pair off.

    This names the **cause** whose effect the ceilings measure. An unpairable
    delimiter does not open a span by itself -- every ``_QUOTE_SPAN``
    alternative needs a closing one -- it makes the *next* delimiter close the
    wrong span, so a pair that should have covered a short quotation instead
    covers everything between the stray and the next one, and a live claim in
    between is silently read as a quotation.
    """
    imbalance: list[str] = []
    for delimiter in ('"', "`"):
        count = text.count(delimiter)
        if count % 2:
            imbalance.append(f"{delimiter} x{count} (odd)")
    opened, closed = text.count("“"), text.count("”")
    if opened != closed:
        imbalance.append(f"“ x{opened} vs ” x{closed}")
    return imbalance


def _spliced_claim_is_visible(text: str, at: int) -> bool:
    """Whether a superseded form spliced into ``text`` at character offset
    ``at`` is still visible to ``_live_superseded_hits``.

    **Positional**: a live hit must sit at the offset the splice put the form
    at, so the answer is about the spliced form rather than about whatever the
    text already said -- which matters for the synthetic control, whose text
    carries a claim of its own. The spliced form contains no quote or
    backtick, so splicing never moves a span boundary: it only decides whether
    ``at`` is inside one.

    The offset is ``len(_normalize(text[:at])) + 1``, not ``at + 1``:
    ``_normalize`` collapses a space the prefix ends on into the one the splice
    adds, which moves the form to ``at``. Measured (sprint-007 T169): at every
    paragraph boundary, whose offset follows the joining space, and at the
    in-span midpoint of research/06 and spec/06. ``at + 1`` would have read
    all 33 boundary splices as swallowed and those two in-span splices as
    suppressed whatever the guard did.

    It replaces a *cardinal* check, "exactly one more live hit than ``text``
    already yields" (B-CR-001 Sec 2). On spec/02 and spec/03 the longest span
    **is** a quoted ``SUPERSEDED_FORMS`` entry, so a splice at its midpoint cut
    that occurrence in half: under ``_is_quoted -> return False`` one hit was
    destroyed and one added, the count did not move, and the ``leaked`` arm
    could not fire on either file.

    This is also the corrected form of the probe M3 found vacuous. That one
    *appended* the form at the tail, and text past the last delimiter can never
    be inside a span, so it reported not-blind on every input -- including the
    stray-unclosed-quote file its own docstring named.
    """
    probe = SUPERSEDED_FORMS[0]
    expected = len(_normalize(text[:at])) + 1
    hits = _live_superseded_hits(f"{text[:at]} {probe} {text[at:]}")
    return any(off == expected for off, _ in hits)


def test_a_splice_that_cuts_a_live_claim_in_half_is_still_seen_positionally() -> None:
    """B-CR-001 fix direction 2, red in-suite (T191). Text holding one
    **unquoted** ``SUPERSEDED_FORMS[0]``, spliced at the middle of that form:
    the splice cuts the existing hit in half and adds one of its own, so the
    live-hit count does not move. The positional check sees the spliced form;
    the cardinal check it replaced, ``after == before + 1``, does not.

    **Mutation that turns this red**: make ``_spliced_claim_is_visible``
    return the cardinal ``len(hits) == len(_live_superseded_hits(text)) + 1``.
    """
    form = SUPERSEDED_FORMS[0]
    text = f"When the source changes the system {form} for the new tier."
    start = text.index(form)
    at = start + len(form) // 2
    spliced = f"{text[:at]} {form} {text[at:]}"
    before, after = _live_superseded_hits(text), _live_superseded_hits(spliced)
    positional = _spliced_claim_is_visible(text, at)
    cardinal = len(after) == len(before) + 1
    print(f"[slice compared] splice @{at} inside {form!r} (@{start}): {text[:at]!r} | {text[at:]!r}; "
          f"hits before {before}, after {after}; positional {positional}, cardinal {cardinal}")
    assert before == [(start, form)], "the unspliced text does not hold exactly one live claim"
    assert start < at < start + len(form) and text[at - 1] != " " and text[at] != " "
    assert len(after) == len(before) == 1, "the splice did not destroy one hit and add one"
    assert positional, (
        f"a claim spliced at @{at}, inside a live claim it cuts in half, was not seen: the check in "
        f"_spliced_claim_is_visible is counting hits, not placing the spliced one (hits after {after})"
    )
    assert not cardinal, "the cardinal check saw the splice, so this text cannot tell the two checks apart"


@dataclass(frozen=True)
class SpanProfile:
    """One file's answer to: what can the quotation guard hide here?"""

    rel: str
    chars: int
    share: float
    longest: int
    imbalance: tuple[str, ...]
    #: Spans that reach across a blank-line paragraph boundary. A quotation
    #: does not span paragraphs; a runaway span does, and this is the shape it
    #: has while it is still too small to trip either ceiling.
    crossings: tuple[str, ...]
    #: The probed span is the **longest span that has a paragraph boundary
    #: after its start** (sprint-007 T182). A claim spliced at the first
    #: boundary at or after that span's start must be seen; one spliced in
    #: that span's middle must not. Both offsets are carried so a failure names
    #: where it looked. The two arms read the **same** span, because the inside
    #: arm is what shows the guard suppressing on the span whose reach the
    #: boundary arm bounds.
    #:
    #: ``None`` only when **no** span has a paragraph boundary after it -- all
    #: sit in the final paragraph, or the file is one paragraph, or has no
    #: span. The file then **abstains** by name (``_abstentions``), and the
    #: inside arm reads the longest span. It does not fall back to
    #: ``len(text)``: that is the one position no span can reach, so the probe
    #: there could not fail and read as ``seen`` (B-CR-001 Sec 1; spec/04 read
    #: ``@41762 boundary splice -> seen`` with ``len(text)`` 41762). spec/04's
    #: longest span is in its final paragraph, but shorter spans precede
    #: boundaries, so it is probed at ``@19624`` (2026-09-25).
    boundary_at: int | None
    boundary_visible: bool | None
    #: ``None`` only when the file has **no span at all**: there is nothing
    #: to splice inside, so the inside arm **abstains** by name
    #: (``_abstentions``) rather than printing ``suppressed`` over a probe
    #: that never ran (sprint-007 review iteration 1, S2; research/00's
    #: meaning review read ``@0 in-span splice -> suppressed`` with
    #: ``_is_quoted`` mutated).
    inside_at: int | None
    inside_visible: bool | None


#: The CommonMark parser whose ``fence`` tokens ``_strip_fences`` removes.
#: ``_QUOTE_SPAN``'s backtick alternative pairs an opening fence's third
#: backtick with the closing fence's first, so a code body holding a blank line
#: reads as a span crossing a paragraph boundary (B-CR-001 Sec 3). A fence is
#: code, which ``_live_superseded_hits`` already treats as quotation.
#:
#: The fences were first matched by a regex that demanded a closer of exactly
#: the opener's run. CommonMark closes a fence on a run of the same character
#: **at least as long**, so ``~~~`` closed by ``~~~~`` stayed open to the next
#: fence and stripped the prose between them (sprint-007 review iteration 2,
#: S2). markdown-it-py (CommonMark) is installed in the test environment; its
#: ``fence`` tokens carry each block's line map, so the parser the rule is
#: written in decides where a fence ends, including an unclosed one, which
#: runs to the end of the document.
_MARKDOWN = MarkdownIt("commonmark")

#: One line with its ending, as markdown-it counts lines for a token's map: it
#: normalizes ``\r\n`` and a lone ``\r`` to ``\n`` and breaks on nothing else.
#: ``str.splitlines`` also breaks on ``\x0b``, ``\x0c``, ``\x1c``-``\x1e``,
#: ``\x85``, U+2028 and U+2029, so one such character before a fence shifted
#: every index after it: the wrong lines were cut and a stray fence was left
#: (sprint-007 review iteration 3, S3).
_MARKDOWN_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def _strip_fences(source: str) -> str:
    """``source`` with every fenced code block's lines removed, from its
    opening fence line to its closing one. Each block leaves the line break
    that ended its closing line, so the text around it keeps the paragraph
    break CommonMark gives it. Lines are split as markdown-it splits them
    (``_MARKDOWN_LINE``), so a token's line map indexes the same lines."""
    lines = _MARKDOWN_LINE.findall(source)
    kept: list[str] = []
    cursor = 0
    for token in _MARKDOWN.parse(source):
        if token.type != "fence" or token.map is None:
            continue
        start, end = token.map
        kept.extend(lines[cursor:start])
        kept.append("\n" if end > start and lines[end - 1].endswith(("\n", "\r")) else "")
        cursor = end
    kept.extend(lines[cursor:])
    return "".join(kept)


def _span_profile(path: Path) -> SpanProfile:
    """Measure one file, through the helpers the tree walk itself uses.

    Shared by the corpus sweep and by
    ``test_the_span_guards_fire_on_text_that_defeats_the_sweep``, so the
    synthetic red cases are produced by the same code that reads the tree
    rather than by a parallel reimplementation of it.

    It measures ``_flat_text`` and cuts ``_flat_paragraphs``: the **same
    string** the absence sweep reads (sprint-007 review iteration 1, M1).
    Stripping fences here alone, as B-CR-001 Sec 3 first did, let a stray
    backtick inside a fence pass every arm below while the sweep, reading the
    fence, paired it with a later code span and lost the claim in between.
    """
    text = _flat_text(path)
    spans = _quoted_spans(text)
    boundaries = [offset for offset, _block in _flat_paragraphs(path)][1:]
    quoted = sum(end - start for start, end in spans)
    longest_span = max(spans, key=lambda span: span[1] - span[0], default=(0, 0))
    crossings = tuple(
        f"[{start},{end}) reaches across the paragraph starting at {boundary}"
        for start, end in spans
        for boundary in boundaries
        if start < boundary < end
    )
    # The span both splice arms read: the longest one that has a paragraph
    # boundary after its start (sprint-007 T182). Only when no span has one
    # does the file abstain, and the inside arm then reads the longest span.
    bounded = [span for span in spans if boundaries and boundaries[-1] >= span[0]]
    probed_span = max(bounded, key=lambda span: span[1] - span[0], default=longest_span)
    boundary_at = next((b for b in boundaries if bounded and b >= probed_span[0]), None)
    inside_at = (probed_span[0] + probed_span[1]) // 2 if spans else None
    return SpanProfile(
        rel=_rel(path),
        chars=len(text),
        share=quoted / max(len(text), 1),
        longest=longest_span[1] - longest_span[0],
        imbalance=tuple(_delimiter_imbalance(text)),
        crossings=crossings,
        boundary_at=boundary_at,
        boundary_visible=(
            None if boundary_at is None else _spliced_claim_is_visible(text, boundary_at)
        ),
        inside_at=inside_at,
        inside_visible=(
            None if inside_at is None else _spliced_claim_is_visible(text, inside_at)
        ),
    )


def _abstentions(profiles: list[SpanProfile]) -> list[str]:
    """One named line per file an arm could not probe: the boundary arm
    when no paragraph boundary follows any span's start, the inside arm when
    the file has no span at all. Reported, never passed silently: an
    exemption nobody can see is the shape ``test_hrv_trend_endpoint``'s
    "shelters something" discipline refuses."""
    return [
        f"ABSTAIN {p.rel}: no span precedes a paragraph boundary"
        for p in profiles
        if p.boundary_at is None
    ] + [
        f"ABSTAIN {p.rel}: no span to splice inside (inside arm)"
        for p in profiles
        if p.inside_at is None
    ]


#: The files each splice arm abstains on, pinned by name and asserted
#: **equal** by ``test_the_scan_of_every_file_can_still_see_a_claim``
#: (sprint-007 review iteration 1, S1). The only guard before was "not every
#: file abstains", so removing the blank lines of spec/02 and spec/03 took the
#: boundary arm from 2 to 4 abstaining files and stayed green. Measured
#: 2026-09-25 at 9cb592c, 37 swept files:
#:
#: * boundary arm: ``research/00-traceability.md`` (a table with no blank
#:   line is one paragraph, so every span sits in its final paragraph) and
#:   ``research/00-meaning-review.md`` (no span at all);
#: * inside arm: ``research/00-meaning-review.md``, the one swept file with no
#:   span.
#:
#: A file joining or leaving either set reds, and the set is re-measured and
#: re-argued here rather than widened to fit.
BOUNDARY_ARM_ABSTAINING = frozenset({
    "specification/research/00-meaning-review.md",
    "specification/research/00-traceability.md",
})
INSIDE_ARM_ABSTAINING = frozenset({
    "specification/research/00-meaning-review.md",
})


def test_no_quoted_span_can_swallow_a_document() -> None:
    r"""The measurement ``_flat_text`` used to publish as prose, asserted.

    Flattening a whole file widens the quotation guard's reach: an unpairable
    quote or backtick makes the next one close the wrong span, and everything
    between is suppressed wholesale -- the shape the fence in
    ``test_hrv_trend_endpoint._scannable`` failed at twice, which is why there
    is no fence there now. Three arms, one per way it shows up:

    * **delimiter parity**, so the cause reds before the effect does;
    * **no span reaches across a paragraph boundary** -- a quotation does not
      span paragraphs, and this catches a runaway while it is still small;
    * the **share** of a file and the **longest single span**, against
      ``QUOTED_SHARE_CEILING`` and ``LONGEST_QUOTED_SPAN_CEILING``.

    The arms assert in that order, so **one mutation per arm**, each chosen to
    leave the earlier arms green, each run on a scratch copy of both roots
    (sprint-006 review iteration 2) rather than asserted to work:

    * **parity** -- delete the closing ``"`` of the quoted phrase
      ``"always > 0 when set"`` in
      ``.claude/rules/learnings/a-published-invariant-needs-a-test-that-can-break-it.md``.
      Observed: ``... cannot pair off ...: a-published-invariant-...md: " x11
      (odd)``.
    * **crossing** -- widen ``_QUOTE_SPAN``'s straight-quote alternative to the
      newline-crossing ``r'"[\s\S]*"'``. Parity still holds; 29 files report a
      span reaching across a paragraph boundary, e.g. ``research/00``'s
      ``[4817,130241)``.
    * **longest span** -- add one *balanced* 661-character quotation inside a
      single paragraph of ``.claude/rules/project-commit-format.md``. Parity
      holds, no boundary is crossed, and the arm reds with ``longest span
      661``.
    * **share** -- add six balanced 91-character quotations to one paragraph of
      the same file. The longest span stays at 91, under its ceiling, and the
      share arm reds with ``51.94% of 1109 chars``.

    ``test_the_span_guards_fire_on_text_that_defeats_the_sweep`` holds the same
    arms on synthetic text, so their red branches run in the suite rather than
    only by hand.
    """
    profiles = [_span_profile(path) for path in _swept_files()]
    for profile in sorted(profiles, key=lambda p: -p.share)[:5]:
        print(
            f"[slice compared] {profile.rel}: {profile.share:.2%} of {profile.chars} chars "
            f"quoted (ceiling {QUOTED_SHARE_CEILING:.0%}), longest span {profile.longest} "
            f"(ceiling {LONGEST_QUOTED_SPAN_CEILING}), "
            f"delimiters {profile.imbalance or 'paired'}"
        )
    print(
        f"[slice compared] {len(profiles)} files over "
        f"{[_rel(root) for root in SCAN_ROOTS]}: max share "
        f"{max(p.share for p in profiles):.2%}, max span "
        f"{max(p.longest for p in profiles)}, "
        f"{sum(len(p.crossings) for p in profiles)} paragraph-crossing spans"
    )

    unbalanced = [f"{p.rel}: {', '.join(p.imbalance)}" for p in profiles if p.imbalance]
    assert not unbalanced, (
        "a quote delimiter in these files cannot pair off, so the next one closes the wrong "
        "span and every live claim in between is read as a quotation -- balance it or write "
        "it as a code span: " + "; ".join(unbalanced)
    )

    crossing = [f"{p.rel} {c}" for p in profiles for c in p.crossings[:3]]
    assert not crossing, (
        "a quoted span reaches across a paragraph boundary in these files: a quotation does "
        "not span paragraphs, so this is a stray delimiter pairing with a distant one, and "
        "the sweep's all-clear over the swallowed text is a report over nothing: "
        + "; ".join(crossing)
    )

    over_span = [
        f"{p.rel} longest span {p.longest}"
        for p in profiles
        if p.longest > LONGEST_QUOTED_SPAN_CEILING
    ]
    assert not over_span, (
        f"a single quoted span runs longer than {LONGEST_QUOTED_SPAN_CEILING} characters, the "
        f"ceiling set with margin over the 207 measured 2026-09-21 across both roots (207 again "
        f"2026-09-25, fences stripped). Either a "
        f"delimiter is straddling text it does not quote, or the ceiling needs re-measuring and "
        f"re-arguing -- not raising: " + "; ".join(over_span)
    )

    over_share = [
        f"{p.rel} {p.share:.2%} of {p.chars} chars"
        for p in profiles
        if p.share > QUOTED_SHARE_CEILING
    ]
    assert not over_share, (
        f"more than {QUOTED_SHARE_CEILING:.0%} of these files is inside a quoted span, over the "
        f"ceiling set with margin above the 13.11% measured 2026-09-21 across both roots (11.51% "
        f"re-measured 2026-09-25, fences stripped). The "
        f"quotation guard is suppressing a document rather than a quotation, and the absence "
        f"sweep below reads only what is left: " + "; ".join(over_share)
    )


def test_the_scan_of_every_file_can_still_see_a_claim() -> None:
    """The non-vacuity guard the flattening needs, probed **where a claim can
    actually be hidden** -- the defect M3 named.

    The probe this replaces appended a superseded form to the end of each
    flattened file and asserted it was seen. Every ``_QUOTE_SPAN`` alternative
    requires a closing delimiter and the form carries none, so appended text
    can never be inside a span: it reported not-blind on every input, including
    the stray-unclosed-quote file its own docstring named. A guard that cannot
    fire is what
    ``a-published-invariant-needs-a-test-that-can-break-it`` is about.

    Two-sided instead, per file, through ``_live_superseded_hits`` -- the
    function the tree walk itself calls:

    * a form spliced at the first **paragraph boundary** at or after the start
      of the **longest span that has such a boundary** **must be seen**. If
      that span ran past the boundary, the claim is swallowed and this reds.
    * a form spliced into the **middle of that same span must not be seen**.
      This is what makes the first arm mean anything: it shows the quotation
      guard really is suppressing on this very file, so "seen at the boundary"
      is a fact about position rather than about a guard suppressing nothing.

    A file where **no** span has a paragraph boundary after it has nowhere to
    splice the first arm, and **abstains by name**: an ``ABSTAIN`` line is
    printed and counted, and it carries no boundary verdict. A file with no
    span at all has nowhere to splice the second arm either, and abstains from
    it by name too (``inside_at`` is ``None``; sprint-007 review iteration 1,
    S2). Both sets are asserted **equal** to ``BOUNDARY_ARM_ABSTAINING`` and
    ``INSIDE_ARM_ABSTAINING`` (S1). Measured 2026-09-25 at 9cb592c, after the
    meaning review (T180) landed: **37** files swept, **35 of 37** probed by
    the boundary arm and **2** abstaining --
    ``specification/research/00-traceability.md``, a table with no blank line
    and so one paragraph, where every span sits in the final paragraph; and
    ``specification/research/00-meaning-review.md``, which has no span. **36
    of 37** are probed by the inside arm, the meaning review abstaining. That
    is coverage knowingly lost on those files; the traceability table's
    in-span arm still reads ``suppressed``. (T175's cut-over left 36 files,
    one abstaining; T180 added the meaning review. The docstring said "exactly
    one abstains" through T180, and the meaning review printed ``@0 in-span
    splice -> suppressed`` over a probe that never ran.) spec/04, whose longest span
    is in its final paragraph, was silently probed at ``len(text)`` until
    sprint-007 T169, where nothing can be swallowed (B-CR-001 Sec 1), and
    abstained under T169; since T182 it is probed at the longest of its spans
    that a boundary follows, ``@19624``. The test reds if every file abstains.

    **Mutations that turn these red**, each run on a scratch copy:

    * **boundary arm** -- delete the closing ``"`` of ``"always > 0 when set"``
      in
      ``.claude/rules/learnings/a-published-invariant-needs-a-test-that-can-break-it.md``,
      so the stray pairs with a distant one and the span covers the next
      paragraph boundary. Observed: that file prints ``boundary splice ->
      SWALLOWED`` and the arm reds with ``...a-published-invariant-...md@1512``.
      The probe this replaces reported not-blind on that same file
      (sprint-006 review iteration 2).
    * **inside arm** -- replace ``_is_quoted``'s body in
      ``_live_superseded_hits`` with ``return False``, in a scratch copy of
      this module (sprint-007 T169). Observed: **34 of 34** files report
      ``LEAKED``, spec/02 and spec/03 included, and the arm names every one of
      them. Every swept file has a span. The sprint-006 log recorded this run
      as 32 files, which read as though two had none: those two were spec/02
      and spec/03, whose longest span is a quoted ``SUPERSEDED_FORMS`` entry,
      and the cardinal check then in ``_spliced_claim_is_visible`` could not
      fire on them (B-CR-001 Sec 2). The check is positional now.
      **Re-run 2026-09-25 (sprint-007 T182)** after the arms moved to the
      longest span that has a following boundary: again **34 of 34**
      ``LEAKED``, 0 abstaining, spec/04 now among them at ``@19178`` (its
      boundary splice ``@19624`` still ``seen``).
      **Re-run 2026-09-25 (sprint-007 review iteration 1)** at 9cb592c, 37
      files: **36 of 36** probed files ``LEAKED`` and the inside arm names
      every one; the meaning review, with no span, prints ``no span
      (abstains)`` rather than a verdict. Before S2 it printed ``@0 in-span
      splice -> suppressed`` under this same mutant.
    * **abstaining sets** -- remove every blank line of spec/02 and spec/03
      (each becomes one paragraph). Observed at 9cb592c: ``33 of 37 files
      probed, 4 abstaining by name``, and the test stayed green; it now reds on
      the equality with ``BOUNDARY_ARM_ABSTAINING``.
    """
    profiles = [_span_profile(path) for path in _swept_files()]
    for line in _abstentions(profiles):
        print(line)
    for profile in profiles:
        boundary = (
            "no boundary to splice at (abstains)"
            if profile.boundary_at is None
            else f"@{profile.boundary_at} boundary splice -> "
            f"{'seen' if profile.boundary_visible else 'SWALLOWED'}"
        )
        inside = (
            "no span (abstains)"
            if profile.inside_at is None
            else f"@{profile.inside_at} in-span splice -> "
            f"{'LEAKED' if profile.inside_visible else 'suppressed'}"
        )
        print(f"[slice compared] {profile.rel} {boundary}; {inside}")
    boundary_abstaining = {p.rel for p in profiles if p.boundary_at is None}
    inside_abstaining = {p.rel for p in profiles if p.inside_at is None}
    print(
        f"[slice compared] boundary arm: {len(profiles) - len(boundary_abstaining)} of "
        f"{len(profiles)} files probed, {len(boundary_abstaining)} abstaining by name "
        f"{sorted(boundary_abstaining)}; inside arm: {len(profiles) - len(inside_abstaining)} of "
        f"{len(profiles)} files probed, {len(inside_abstaining)} abstaining by name "
        f"{sorted(inside_abstaining)}"
    )
    assert len(boundary_abstaining) < len(profiles), (
        "every swept file abstains from the boundary arm, so it is a report over nothing"
    )
    assert boundary_abstaining == BOUNDARY_ARM_ABSTAINING, (
        f"the boundary arm abstains on {sorted(boundary_abstaining)}, not the pinned "
        f"{sorted(BOUNDARY_ARM_ABSTAINING)}: joined "
        f"{sorted(boundary_abstaining - BOUNDARY_ARM_ABSTAINING)}, left "
        f"{sorted(BOUNDARY_ARM_ABSTAINING - boundary_abstaining)} -- re-measure and re-argue the "
        f"set, do not widen it"
    )
    assert inside_abstaining == INSIDE_ARM_ABSTAINING, (
        f"the inside arm abstains on {sorted(inside_abstaining)}, not the pinned "
        f"{sorted(INSIDE_ARM_ABSTAINING)}: joined "
        f"{sorted(inside_abstaining - INSIDE_ARM_ABSTAINING)}, left "
        f"{sorted(INSIDE_ARM_ABSTAINING - inside_abstaining)} -- re-measure and re-argue the "
        f"set, do not widen it"
    )

    blind = [f"{p.rel}@{p.boundary_at}" for p in profiles if p.boundary_visible is False]
    assert not blind, (
        "the flattened scan of these files cannot see a claim spliced at a paragraph "
        "boundary, so their all-clear below is a report over nothing -- a quoted span has "
        "grown past the paragraph it quotes: " + "; ".join(blind)
    )

    leaked = [f"{p.rel}@{p.inside_at}" for p in profiles if p.inside_visible]
    assert not leaked, (
        "a claim spliced into the middle of a quoted span was reported as live in these "
        "files, so the quotation guard is suppressing nothing and the arm above is vacuous "
        "-- research/00 Sec 5.4's historical reproductions would be flagged next: "
        + "; ".join(leaked)
    )


def test_the_span_guards_fire_on_text_that_defeats_the_sweep(tmp_path) -> None:
    """Every arm of the two tests above **except ``leaked``**, shown red on
    synthetic text.

    The corpus is clean -- 37 files, no unpairable delimiter, no
    paragraph-crossing span, 11.51% and 161 at the maxima (fences stripped,
    re-measured 2026-09-25 at 9cb592c, after the meaning review landed) -- so the corpus alone can never show any of those arms
    failing, and an arm never seen failing is indistinguishable from the arm
    M3 found. Synthetic files supply the case the tree does not, measured
    through ``_span_profile``: the same function the sweep runs, not a
    reimplementation of it.

    ``leaked`` is the exception, and no synthetic text can remove it:
    ``inside_at`` is the midpoint of the longest span by construction, so
    ``inside_visible`` turns ``True`` only if the guard itself is broken. Its
    red branch needs ``_is_quoted`` mutated, and is recorded in
    ``test_the_scan_of_every_file_can_still_see_a_claim``'s mutation log
    instead of being run here.
    """
    filler = "ordinary prose about nothing in particular. " * 30
    clean = tmp_path / "clean.md"
    clean.write_text(
        f'A document quoting "a short phrase" once.\n\n{filler}\n\nA final paragraph.\n',
        encoding="utf-8",
    )
    runaway = tmp_path / "runaway.md"
    runaway.write_text(
        f'A document with a stray opener " here.\n\n{filler}\n\nAnd "a real quotation" later.\n',
        encoding="utf-8",
    )

    for path in (clean, runaway):
        profile = _span_profile(path)
        print(
            f"[slice compared] {profile.rel}: share {profile.share:.2%}, longest "
            f"{profile.longest}, imbalance {profile.imbalance or 'paired'}, crossings "
            f"{len(profile.crossings)}, boundary@{profile.boundary_at} "
            f"{'seen' if profile.boundary_visible else 'SWALLOWED'}, "
            f"inside@{profile.inside_at} "
            f"{'LEAKED' if profile.inside_visible else 'suppressed'}"
        )

    good = _span_profile(clean)
    assert not good.imbalance, "the parity arm reports an imbalance on balanced text"
    assert not good.crossings, "the crossing arm reports a crossing on a one-paragraph quotation"
    assert good.share <= QUOTED_SHARE_CEILING, "ordinary text is over the share ceiling"
    assert good.longest <= LONGEST_QUOTED_SPAN_CEILING, "a short quotation is over the span ceiling"
    assert good.boundary_visible, "a claim at a paragraph boundary of ordinary text was not seen"
    assert not good.inside_visible, "a claim inside a real quotation was reported as live"

    bad = _span_profile(runaway)
    assert bad.imbalance, "an odd number of straight quotes was not reported as unpairable"
    assert bad.crossings, "a span reaching across two paragraph boundaries was not reported"
    assert bad.longest > LONGEST_QUOTED_SPAN_CEILING, (
        f"the runaway span is {bad.longest} characters and the ceiling of "
        f"{LONGEST_QUOTED_SPAN_CEILING} did not catch it"
    )
    assert bad.share > QUOTED_SHARE_CEILING, (
        f"the runaway span covers {bad.share:.2%} of the file and the ceiling of "
        f"{QUOTED_SHARE_CEILING:.0%} did not catch it"
    )
    assert not bad.boundary_visible, (
        "a claim spliced at the paragraph boundary the runaway span swallowed was still "
        "reported as live, so the boundary arm cannot fire"
    )


def test_a_file_whose_spans_all_sit_in_its_final_paragraph_abstains_by_name(tmp_path) -> None:
    """The boundary arm's silent fallback (B-CR-001 Sec 1), pinned on the shape
    that triggered it.

    When every span starts after the file's last paragraph boundary there is no
    boundary to splice at. The arm used to fall back to ``len(text)``, the
    one position no span can reach, so it reported ``seen`` over a probe that
    could not fail -- spec/04 read ``@41762 boundary splice -> seen`` with
    ``len(text)`` 41762. Such a file must instead carry no boundary offset,
    no boundary verdict, and a named ``ABSTAIN`` line in the report.

    Two files. ``last-only.md`` has every span in its final paragraph, so no
    span has a boundary after it and it abstains. ``longest-first.md`` is
    probed, at a real boundary short of ``len(text)``. The shape between them
    -- longest span last, a shorter one before a boundary, spec/04's shape --
    is probed at the shorter span, not abstained
    (``test_a_file_whose_longest_span_is_last_is_probed_at_an_earlier_span``).
    """
    filler = "ordinary prose about nothing in particular. " * 10
    last_only = tmp_path / "last-only.md"
    last_only.write_text(
        f"{filler}\n\n{filler}\n\nThe table: `one code span` and \"a quotation here\".\n",
        encoding="utf-8",
    )
    longest_first = tmp_path / "longest-first.md"
    longest_first.write_text(
        f'A "much longer quotation that sits first".\n\n{filler}\n\nAnd a "short" one.\n',
        encoding="utf-8",
    )

    abstaining = [_span_profile(last_only)]
    probed = _span_profile(longest_first)
    report = _abstentions([*abstaining, probed])
    for profile in (*abstaining, probed):
        print(
            f"[slice compared] {profile.rel}: {profile.chars} chars, "
            f"boundary@{profile.boundary_at} -> {profile.boundary_visible}"
        )
    print(f"[slice compared] report: {report}")

    for profile in abstaining:
        assert profile.boundary_at is None, (
            f"{profile.rel}: no boundary follows the longest span, yet the arm probed at "
            f"{profile.boundary_at} (len {profile.chars}): the silent tail fallback is back"
        )
        assert profile.boundary_visible is None, f"{profile.rel} abstains but carries a verdict"
    assert report == [
        f"ABSTAIN {p.rel}: no span precedes a paragraph boundary" for p in abstaining
    ], f"the abstaining files are not each named, or a probed file is: {report}"
    assert probed.boundary_at is not None and probed.boundary_at < probed.chars, (
        f"a file with a span before a paragraph boundary was not probed at one: "
        f"{probed.boundary_at} of {probed.chars}"
    )
    assert probed.boundary_visible is True, "a claim at an ordinary paragraph boundary was not seen"


def test_a_file_whose_longest_span_is_last_is_probed_at_an_earlier_span(tmp_path) -> None:
    """spec/04's shape (sprint-007 T182): the longest span sits in the final
    paragraph, and a shorter one sits before a paragraph boundary.

    The arms probe the **longest span that has a following paragraph
    boundary**, so this file is probed at the shorter span -- boundary arm at
    the first boundary after it, inside arm at its midpoint -- and does not
    abstain. A file abstains only when **no** span has a boundary after it.
    T169 abstained this shape, because it took the longest span first and then
    looked for a boundary; that left spec/04 unprobed although it has spans a
    boundary follows.
    """
    filler = "ordinary prose about nothing in particular. " * 10
    earlier = tmp_path / "longest-last.md"
    earlier.write_text(
        f'A "short" quotation.\n\n{filler}\n\nAnd "a much longer quotation that sits last".\n',
        encoding="utf-8",
    )
    profile = _span_profile(earlier)
    text = _flat_text(earlier)
    short_start = text.index('"short"')
    short_mid = (short_start + short_start + len('"short"')) // 2
    first_boundary = len(_normalize("A \"short\" quotation.")) + 1
    print(
        f"[slice compared] {profile.rel}: {profile.chars} chars, longest {profile.longest}, "
        f"boundary@{profile.boundary_at} -> {profile.boundary_visible} (expected "
        f"@{first_boundary}), inside@{profile.inside_at} -> "
        f"{'LEAKED' if profile.inside_visible else 'suppressed'} (expected @{short_mid}), "
        f"report {_abstentions([profile])}"
    )

    assert profile.longest == len('"a much longer quotation that sits last"'), (
        "the longest-span measurement must still read the file's longest span, probed or not"
    )
    assert profile.boundary_at == first_boundary, (
        f"the earlier span has a paragraph boundary after it at {first_boundary}, yet the arm "
        f"probed at {profile.boundary_at}: it is not taking the longest span that has one"
    )
    assert profile.boundary_visible is True, "a claim at the boundary after the earlier span was not seen"
    assert profile.inside_at == short_mid, (
        f"the inside arm splices at {profile.inside_at}, not the midpoint {short_mid} of the span "
        f"the boundary arm bounds: the two arms read different spans"
    )
    assert profile.inside_visible is False, "a claim inside the earlier quotation was reported as live"
    assert _abstentions([profile]) == [], "a file with a probeable span was reported as abstaining"


def test_a_fenced_code_block_with_a_blank_line_is_not_a_crossing_span(tmp_path) -> None:
    """B-CR-001 Sec 3: ``_QUOTE_SPAN``'s backtick alternative pairs the third
    backtick of an opening fence with the first of the closing one, so a code
    body holding a blank line read as a span reaching across a paragraph
    boundary, and the crossing arm blamed a stray delimiter that did not exist.

    B-CR-001 stripped fences inside ``_span_profile`` only and asserted here
    that ``_flat_text`` still read the fenced body. That split was the defect
    sprint-007 review iteration 1 found (M1): the guards and the sweep read
    two strings, so a stray backtick in a fence hid a claim from one and was
    invisible to the other. Both now read ``_swept_source``, and this asserts
    it: the fenced body is gone from ``_flat_text`` and the profile measured
    exactly ``_flat_text``.
    ``test_a_stray_backtick_in_a_fence_cannot_hide_a_claim_from_the_sweep``
    holds the red case.
    """
    fenced = tmp_path / "fenced.md"
    fenced.write_text(
        'A rules file quoting "a phrase" once.\n\n'
        "```python\n"
        "def first():\n"
        "    return 1\n"
        "\n"
        "def second():\n"
        "    return 2\n"
        "```\n\n"
        "A closing paragraph.\n",
        encoding="utf-8",
    )
    profile = _span_profile(fenced)
    print(
        f"[slice compared] {profile.rel}: imbalance {profile.imbalance or 'paired'}, "
        f"crossings {list(profile.crossings)}, longest {profile.longest}, "
        f"boundary@{profile.boundary_at} -> {profile.boundary_visible}"
    )

    assert not profile.imbalance, "a balanced fence was reported as an unpairable delimiter"
    assert not profile.crossings, (
        f"a fenced code block with a blank line was read as a paragraph-crossing span: "
        f"{list(profile.crossings)}"
    )
    assert profile.boundary_visible is True, "a claim at a boundary beside a fence was not seen"
    assert "def second():" not in _flat_text(fenced), (
        "_flat_text still reads the fenced body, so the sweep and the span guards read two "
        "different strings (M1)"
    )
    assert profile.chars == len(_flat_text(fenced)), (
        f"the span profile measured {profile.chars} characters and the sweep reads "
        f"{len(_flat_text(fenced))}: they are not reading one string (M1)"
    )


def test_a_stray_backtick_in_a_fence_cannot_hide_a_claim_from_the_sweep(tmp_path) -> None:
    """Sprint-007 review iteration 1, M1, on scanner A's own input: a fence
    whose body holds **one stray backtick**, then a live superseded claim,
    then a code span.

    With the fence stripped only in ``_span_profile``, every guard read
    balanced text (parity paired, no crossing, a short longest span) while
    ``_flat_text`` kept the fence: the stray paired the closing fence's
    backticks off by one, the last of them paired with the code span, and the
    claim between sat inside a "quotation". Three guards green, the claim
    invisible. Now both read ``_swept_source``, so the claim is reported at
    its offset and the guards measured that same text.
    """
    claim = "When the tier changes the system treats it as a baseline re-establishment."
    stray = tmp_path / "stray-in-fence.md"
    stray.write_text(
        f"A rules file.\n\n```bash\necho `date\n```\n\n{claim}\n\nRun it with `uv run pytest`.\n",
        encoding="utf-8",
    )
    text = _flat_text(stray)
    at = text.index("treats it as a baseline re-establishment")
    offenders = _offenders_in(stray)
    profile = _span_profile(stray)
    print(
        f"[slice compared] {_slice(stray.name, 0, text)} -> offenders {offenders}; "
        f"imbalance {profile.imbalance or 'paired'}, crossings {list(profile.crossings)}, "
        f"longest {profile.longest}, chars {profile.chars}"
    )
    assert offenders == [f"stray-in-fence.md@{at}: 'treats it as a baseline re-establishment'"], (
        f"a live claim after a fence holding a stray backtick is hidden from the absence sweep: "
        f"{offenders}"
    )
    assert profile.chars == len(text) and not profile.imbalance, (
        f"the span guards did not measure the string the sweep read: chars {profile.chars} vs "
        f"{len(text)}, imbalance {profile.imbalance}"
    )


@pytest.mark.parametrize(
    ("opener", "closer", "plain"),
    [
        pytest.param("~~~bash", "~~~~", "~~~", id="tilde-closer-one-longer"),
        pytest.param("```bash", "````", "````", id="backtick-closer-one-longer"),
    ],
)
def test_a_longer_closing_fence_cannot_hide_a_claim_from_the_sweep(
    tmp_path, opener: str, closer: str, plain: str
) -> None:
    """Sprint-007 review iteration 2, S2. CommonMark closes a fence on a run
    of the opener's character **at least as long** as the opener's. The
    stripper's regex demanded exactly the opener's run, so ``~~~bash`` closed
    by ``~~~~`` stayed open, ran to the next block's ``~~~`` opener, and
    stripped the live claim between them as code: every guard green, the
    claim gone from the sweep (it reported the claim at 9cb592c). Now the
    fences are markdown-it's ``fence`` tokens, so each block closes where
    CommonMark closes it and the claim between them is prose, reported at its
    offset."""
    claim = "When the tier changes the system treats it as a baseline re-establishment."
    longer = tmp_path / "longer-closer.md"
    longer.write_text(
        f"A rules file.\n\n{opener}\necho hi\n{closer}\n\n{claim}\n\n{plain}\nmore code\n{plain}\n",
        encoding="utf-8",
    )
    text = _flat_text(longer)
    offenders = _offenders_in(longer)
    profile = _span_profile(longer)
    print(
        f"[slice compared] {_slice(longer.name, 0, text)} -> offenders {offenders}; "
        f"imbalance {profile.imbalance or 'paired'}, chars {profile.chars}"
    )
    assert "treats it as a baseline re-establishment" in text, (
        f"the claim between two fences was stripped as code: {text!r}"
    )
    at = text.index("treats it as a baseline re-establishment")
    assert offenders == [f"longer-closer.md@{at}: 'treats it as a baseline re-establishment'"], (
        f"a live claim after a fence closed by a longer run is hidden from the absence sweep: {offenders}"
    )
    assert text == "a rules file. when the tier changes the system treats it as a baseline re-establishment.", (
        f"the fences were not stripped to exactly the prose around them: {text!r}"
    )
    assert profile.chars == len(text) and not profile.imbalance, (
        f"the span guards did not measure the string the sweep read: chars {profile.chars} vs "
        f"{len(text)}, imbalance {profile.imbalance}"
    )


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param(" ", id="u2028-line-separator"),
        pytest.param(" ", id="u2029-paragraph-separator"),
        pytest.param("\x0c", id="form-feed"),
        pytest.param("\x85", id="next-line"),
    ],
)
def test_a_line_separator_markdown_it_does_not_count_cannot_shift_the_fence_strip(
    tmp_path, separator: str
) -> None:
    """Sprint-007 review iteration 3, S3. ``_strip_fences`` cut the lines a
    fence token's map names, but split them with ``str.splitlines``, which
    also breaks on characters markdown-it does not count. One such character
    before a fence moved every index by one: the stripper cut the fence's
    first two lines (the intro's tail and the opener) and left the closing
    fence standing as a stray run of backticks. Now the lines are split as
    markdown-it splits them, so exactly the fence goes."""
    claim = "When the tier changes the system treats it as a baseline re-establishment."
    source = f"Intro line.{separator}\n\n```\ncode\n```\n\n{claim}\n"
    stripped = _strip_fences(source)
    shifted = tmp_path / "separator-before-fence.md"
    shifted.write_text(source, encoding="utf-8")
    text = _flat_text(shifted)
    offenders = _offenders_in(shifted)
    print(f"[slice compared] {source!r} -> {stripped!r}; {_slice(shifted.name, 0, text)} -> {offenders}")
    assert stripped == f"Intro line.{separator}\n\n\n\n{claim}\n", (
        f"the fence strip cut lines other than the fence's: {stripped!r}"
    )
    at = text.index("treats it as a baseline re-establishment")
    assert "`" not in text and offenders == [
        f"separator-before-fence.md@{at}: 'treats it as a baseline re-establishment'"
    ], f"the fence strip left a stray fence or lost the claim: {text!r} -> {offenders}"


def test_a_cr_only_file_keeps_the_line_break_a_fence_leaves() -> None:
    """Sprint-007 review iteration 4, S3. A fence leaves the line break that
    ended its closing line, read by ``lines[end - 1].endswith(("\\n", "\\r"))``.
    markdown-it also breaks a line on a lone CR, so in a CR-only file the
    closing fence line ends in CR; narrowing the test to LF alone dropped the
    break, gluing the intro's blank line to the claim, and 34 passed. Called
    directly, the exact result is the lines outside the fence with one LF in
    the fence's place."""
    claim = "When the tier changes the system treats it as a baseline re-establishment."
    source = f"Intro line.\r\r```\rcode\r```\r{claim}\r"
    fences = [token.map for token in _MARKDOWN.parse(source) if token.type == "fence"]
    stripped = _strip_fences(source)
    print(f"[slice compared] {source!r}: fence map {fences} -> {stripped!r}")
    assert fences == [[2, 5]], f"markdown-it no longer reads this CR-only fence as lines 2-5: {fences}"
    assert stripped == f"Intro line.\r\r\n{claim}\r", (
        f"the fence in a CR-only file did not leave its line break: {stripped!r}"
    )


def test_a_file_with_no_span_abstains_from_the_inside_arm_by_name(tmp_path) -> None:
    """Sprint-007 review iteration 1, S2. A file with no quoted or code span
    has nothing to splice inside. The inside arm used to read
    ``bool(spans) and ...``, so it printed ``@0 in-span splice ->
    suppressed`` -- a verdict over a probe that never ran, and one that stayed
    ``suppressed`` with ``_is_quoted`` mutated. It must carry no offset, no
    verdict, and a named ``ABSTAIN`` line."""
    filler = "ordinary prose about nothing in particular. " * 10
    spanless = tmp_path / "spanless.md"
    spanless.write_text(f"{filler}\n\n{filler}\n", encoding="utf-8")
    profile = _span_profile(spanless)
    report = _abstentions([profile])
    print(
        f"[slice compared] {profile.rel}: inside@{profile.inside_at} -> {profile.inside_visible}, "
        f"boundary@{profile.boundary_at} -> {profile.boundary_visible}; report {report}"
    )
    assert profile.inside_at is None, (
        f"a file with no span was probed inside a span at @{profile.inside_at}"
    )
    assert profile.inside_visible is None, (
        f"a file with no span carries an inside-arm verdict {profile.inside_visible!r}"
    )
    assert report == [
        "ABSTAIN spanless.md: no span precedes a paragraph boundary",
        "ABSTAIN spanless.md: no span to splice inside (inside arm)",
    ], f"the spanless file does not abstain from both arms by name: {report}"


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
