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
#: The 9.13% this module published before, as "over both roots", was measured
#: over ``specification/`` alone: all five widest files are under
#: ``.claude/rules/**`` (sprint-006 review iteration 2, M4).
#:
#: **The margin, and why there is one.** ~1.5x the measured share and ~2x the
#: measured longest span, deliberately not the measured values. The widest
#: files are 1.5-3 kB rules documents, where adding one quoted sentence or code
#: span moves the share by a point or two and the longest span by a few dozen
#: characters; a ceiling pinned at 13.11% reds on the next ordinary prose edit
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

    **Exactly one more** live hit than ``text`` already yields, so the answer
    is about the spliced form rather than about whatever the text already said
    -- which matters for the synthetic control, whose text carries a claim of
    its own. The spliced form contains no quote or backtick, so splicing never
    moves a span boundary: it only decides whether ``at`` is inside one.

    This is the corrected form of the probe M3 found vacuous. That one
    *appended* the form at the tail, and text past the last delimiter can never
    be inside a span, so it reported not-blind on every input -- including the
    stray-unclosed-quote file its own docstring named.
    """
    probe = SUPERSEDED_FORMS[0]
    before = len(_live_superseded_hits(text))
    after = len(_live_superseded_hits(f"{text[:at]} {probe} {text[at:]}"))
    return after == before + 1


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
    #: A claim spliced at the first paragraph boundary at or after the longest
    #: span's start must be seen; one spliced in that span's middle must not.
    #: Both offsets are carried so a failure names where it looked.
    boundary_at: int
    boundary_visible: bool
    inside_at: int
    inside_visible: bool


def _span_profile(path: Path) -> SpanProfile:
    """Measure one file, through the helpers the tree walk itself uses.

    Shared by the corpus sweep and by
    ``test_the_span_guards_fire_on_text_that_defeats_the_sweep``, so the
    synthetic red cases are produced by the same code that reads the tree
    rather than by a parallel reimplementation of it.
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
    boundary_at = next((b for b in boundaries if b >= longest_span[0]), len(text))
    inside_at = (longest_span[0] + longest_span[1]) // 2
    return SpanProfile(
        rel=_rel(path),
        chars=len(text),
        share=quoted / max(len(text), 1),
        longest=longest_span[1] - longest_span[0],
        imbalance=tuple(_delimiter_imbalance(text)),
        crossings=crossings,
        boundary_at=boundary_at,
        boundary_visible=_spliced_claim_is_visible(text, boundary_at),
        inside_at=inside_at,
        inside_visible=bool(spans) and _spliced_claim_is_visible(text, inside_at),
    )


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
        f"ceiling set with margin over the 207 measured 2026-09-21 across both roots. Either a "
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
        f"ceiling set with margin above the 13.11% measured 2026-09-21 across both roots. The "
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

    * a form spliced at the first **paragraph boundary** at or after the
      longest span's start **must be seen**. If that span ran past the
      boundary, the claim is swallowed and this reds.
    * a form spliced into the **middle of that same span must not be seen**.
      This is what makes the first arm mean anything: it shows the quotation
      guard really is suppressing on this very file, so "seen at the boundary"
      is a fact about position rather than about a guard suppressing nothing.

    **Mutations that turn these red**, both run on a scratch copy of both roots
    (sprint-006 review iteration 2):

    * **boundary arm** -- delete the closing ``"`` of ``"always > 0 when set"``
      in
      ``.claude/rules/learnings/a-published-invariant-needs-a-test-that-can-break-it.md``,
      so the stray pairs with a distant one and the span covers the next
      paragraph boundary. Observed: that file prints ``boundary splice ->
      SWALLOWED`` and the arm reds with ``...a-published-invariant-...md@1512``.
      The probe this replaces reported not-blind on that same file.
    * **inside arm** -- replace ``_is_quoted``'s body in
      ``_live_superseded_hits`` with ``return False``. Observed: all 32 files
      with a span report ``LEAKED`` and the arm names every one of them.
    """
    profiles = [_span_profile(path) for path in _swept_files()]
    for profile in profiles:
        print(
            f"[slice compared] {profile.rel}@{profile.boundary_at} boundary splice -> "
            f"{'seen' if profile.boundary_visible else 'SWALLOWED'}; "
            f"@{profile.inside_at} in-span splice -> "
            f"{'LEAKED' if profile.inside_visible else 'suppressed'}"
        )

    blind = [f"{p.rel}@{p.boundary_at}" for p in profiles if not p.boundary_visible]
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
    """Every arm of the two tests above, shown red on synthetic text.

    The corpus is clean -- 34 files, no unpairable delimiter, no
    paragraph-crossing span, 13.11% and 207 at the maxima -- so the corpus
    alone can never show any of those arms failing, and an arm never seen
    failing is indistinguishable from the arm M3 found. Synthetic files supply
    the case the tree does not, measured through ``_span_profile``: the same
    function the sweep runs, not a reimplementation of it.
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
