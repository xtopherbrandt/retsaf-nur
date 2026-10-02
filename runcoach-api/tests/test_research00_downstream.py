"""F011: the downstream gate -- no live document states a meaning research/00's rewrite changed.

This file is the gate's scanner (T197): which files are live, how many there are per root, which text
in them is a record, and where the quotation spans lie. The hit test over ``OLD_MEANINGS`` (T199),
the census rows (T200) and the presence rows (T201) read what is defined here.

Authority: ``spec/references/F011-sweep-decisions.md`` S1-S3 and S15. CI has no data dir, so nothing
here reads it but AC3's IDEA rows: every fact taken from the decisions is frozen as a literal.

- **Live files (S2).** The roots below, restricted to ``.md``, ``.py``, ``.yaml`` and ``.yml``
  (``runcoach-api/src/`` to ``.py``, never ``.pyc``), minus ``RECORD_SET`` (S1, in
  ``support/research00_records.py``, shared with F009).
- **Floors (S2).** ``ROOT_FLOORS`` is each root's live count as this gate measured it at ``a15610d``
  (58 in all; rules 13). A walk that stops descending reports all-clear over nothing; a root below its
  floor turns the gate red. A record added inside a root lowers that root's floor in the same commit.
- **Section records (S1).** Text under exactly ``^## Decision Log\\s*$`` in a ``SECTION_RECORD_FILES``
  file, and a CHANGELOG entry under a released ``## [x.y.z]`` heading or a dated sprint heading
  (``## <date> through <date> — Sprint NNN``, sprint-009 D11), is not swept. Every other file
  under the roots that carries the Decision Log heading is swept, and a test lists them.
- **Quotation (S3).** In ``.md`` only, a hit wholly inside a straight or curly double-quoted span, a
  code span or a fenced block is quotation. Spans are found before ``normalize()`` in one CommonMark
  reading (markdown-it) of the raw text with each unclosed fence's opener line, info string included,
  read as letters from its fence run on: a fence no closing line ends -- run to the end of the file, a
  blockquote or a list item -- is prose. A fenced block is a closed fence token and a code span a
  ``code_inline`` token of that reading; only a paragraph or a heading holds a span. Quotes pair once
  per paragraph or heading, never inside a code span: one with an odd count of straight quotes, or
  curly quotes that do not run ``“ ” “ ”``, pairs none of them, and a quote, straight or curly, pairs
  only a left-flanking opener with a right-flanking closer, flanking read on the paragraph's CommonMark
  content with CommonMark's whitespace. A code span in an image description holds its quote too, and an
  escaped ``\\"`` counts but neither opens nor closes, except in raw HTML or an autolink, where
  CommonMark reads no escape. Raw HTML, autolinks and HTML blocks are read by CommonMark 0.31.2's
  grammar, not markdown-it's, and every link destination is a link's. A ``>`` blockquote is not quotation.
  ``.py``, ``.yaml`` and ``.yml`` are scanned whole.
- **Wrapped line markers (T219; sprint-008 F011 review).** In ``.py``, ``.yaml`` and ``.yml``, each
  comment line's marker (indentation, a ``#`` run, a ``#:`` colon) is removed before ``normalize()``
  (``gate_text``), so a meaning wrapped over two comment lines reads as one sentence; in ``.md`` each
  blockquote line's ``>`` marker, after any indentation or list marker, is removed for the same reason,
  and every ``#`` is kept. Every census and exception check compares against this same text.
- **Hits (T199, AC1).** Each ``OLD_MEANINGS`` key's pattern is searched in every live file its
  ``KEY_ROOTS`` entry lets it reach (S12: the four T-07 keys reach ``.claude/rules/`` only). A hit is
  quotation (S3), sheltered by an ``EXCEPTIONS`` excerpt that spans the whole of it in that one file
  (S4; IDEA-106 item 8: an excerpt that overlaps a hit by a character shelters nothing, and a census
  row is ``sheltered`` only when every occurrence of its excerpt lies inside one), a pending site (S15:
  ``tests/data/research00_pending/<task id>.csv``, strict xfail, owned per ``OWNERSHIP``), or a failure.
- **Presence rows (T201, S6).** Each census site whose key cites a decision in F008 AC7's ``OPERATIVE``
  (read from the traceability test by path, never copied) has one row per operative string: the ``.md``
  paragraph holding the site's frozen anchor (``PRESENCE_ANCHORS``) states the string after
  ``normalize()``. A site or string that gets no row is in ``DROPPED_PRESENCE_ROWS`` with its reason.
  ``test_decisions_01_conforms`` (S9) and the no_regression comment's absence row sit beside them.
- **IDEA end states (T215, AC3, S7).** ``IDEA_END_STATES`` freezes each IDEA's row; ``test_idea_end_state``
  reads its file in the data dir (found as ``test_normative_mirror`` finds it) and skips, naming
  ``IDEA_SKIP_REASON``, only where there is none. ``test_the_gate_runs_without_the_data_dir`` runs this
  module in a copy of the tree with no data dir and holds those rows, plus the strict xfails S15's
  pending files put on it (``pending_xfail_case_ids``, derived from the same parametrizers), to be its
  only skips (AC5); with no pending file that is the IDEA rows alone.

**Blind spots, stated rather than argued away:**

- **Records (S1).** Nothing under a ``RECORD_SET`` path, and no text under a section record, is swept,
  whatever it says. A live claim written into a record is invisible here by construction -- the same
  class as the endpoint test's ``CHANGELOG.md`` row.
- **Test comments and docstrings (S2).** ``runcoach-api/tests/`` is not a root, so comments and
  docstrings in the test files are not swept. The spec critic found keyed phrases in 7 test files;
  only the ``test_hrv_no_regression_gate.py`` comment has a row (S6).
- **What survives ``normalize()`` (IDEA-106 items 5 and 6; T229).** ``normalize()`` is NFKC, the dropped
  characters, the typography folds, a whitespace collapse and a casefold, and no more. A zero-width
  character (U+200B, U+200C, U+200D, U+2060, U+FEFF), a soft hyphen (U+00AD), a hard-break backslash at
  a line end, a ``[text](url)`` link's brackets and destination, and an HTML entity (``&amp;``,
  ``&#39;``) all survive it, so a site that spells a key's phrase with one of them splits the phrase
  and no pattern reaches it. ``_paragraphs`` uses ``str.splitlines``, which also breaks a paragraph on
  ``\\x0c`` and ``\\x85``, where CommonMark reads neither as a line ending. A stray single backtick
  pairs with the next code span's opener, as CommonMark reads it, so the prose between them is a code
  span and any hit inside it is quotation. T219's user decision stands: these are named here, not fixed.
- **Census phrases (IDEA-106 item 10).** A census row's ``excerpt`` is a literal of the old text, matched
  after ``normalize()`` and nothing else; a rewording that keeps the old meaning but changes a word reads
  as ``gone``, and only a key whose pattern still reaches the new wording turns the site red. The
  ``PRESENCE_ANCHORS`` paragraphs are literals too: a heading or a lead sentence rewritten in place
  breaks the anchor, which fails loudly rather than passing (``test_presence_anchors_resolve_to_one_paragraph_each``).
- **Paraphrases (IDEA-106 item 25; sprint-009 D8).** The gate and the census prove that the exact old
  phrasings ``OLD_MEANINGS`` encodes are absent from the live files, not that paraphrases of them are.
  At sprint-008's review 13 of 13 real paraphrases passed every pattern, and a read-only sweep of all 54
  keys found about 30 live sites (fixed in ``99dcc1f..f212d42``). A paraphrase family per key is
  T231's and a periodic paraphrase sweep is the control D8 files as an IDEA; a green gate is not a
  statement that no live document paraphrases a changed meaning.
"""

import csv
import fnmatch
import importlib.util
import bisect
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest
from markdown_it import MarkdownIt
from markdown_it import rules_block as _markdown_block_rules
from markdown_it import rules_inline as _markdown_inline_rules
from markdown_it.common.html_blocks import block_names as _markdown_block_names
from markdown_it.common.utils import isLinkClose as _markdown_is_link_close
from markdown_it.common.utils import isLinkOpen as _markdown_is_link_open
from markdown_it.parser_block import _rules as _markdown_block_rule_table

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = Path(__file__).parent / "support"


def _load_module(name: str, path: Path) -> ModuleType:
    """Import a module from its path: the workspace runs pytest with ``--import-mode=importlib``,
    under which nothing in ``tests/`` is importable by name (as at test_research00_traceability.py)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OM = _load_module("research00_old_meanings", _SUPPORT / "research00_old_meanings.py")
_REC = _load_module("research00_records", _SUPPORT / "research00_records.py")
normalize = _OM.normalize
RECORD_SET = _REC.RECORD_SET
SECTION_RECORD_FILES = _REC.SECTION_RECORD_FILES
is_record = _REC.is_record

# --------------------------------------------------------------------------------------------------
# S2: the roots, the walk and the floors.
# --------------------------------------------------------------------------------------------------

SCANNED_SUFFIXES = (".md", ".py", ".yaml", ".yml")

#: ``(label, directory, recursive, name glob, suffixes)``. The label is what the gate prints and what
#: ``ROOT_FLOORS`` is keyed on.
ROOTS = (
    ("spec", "specification/spec", True, "*", SCANNED_SUFFIXES),
    ("decisions", "specification/decisions", True, "*", SCANNED_SUFFIXES),
    ("spec_star", "specification", False, "spec_*", SCANNED_SUFFIXES),
    ("future", "specification/future", True, "*", SCANNED_SUFFIXES),
    ("research", "specification/research", False, "0[1-6]-*", SCANNED_SUFFIXES),
    ("contracts", "contracts", True, "*", SCANNED_SUFFIXES),
    ("src", "runcoach-api/src", True, "*", (".py",)),
    ("rules", ".claude/rules", True, "*", SCANNED_SUFFIXES),
    ("spec_mirror", "spec-mirror", True, "*", SCANNED_SUFFIXES),
)

#: Frozen from this gate's own count at ``a15610d`` (T197), not copied from S2's 2026-09-24 list:
#: 58 live files. Lower a floor only in the commit that adds a record inside that root (S1).
ROOT_FLOORS = (
    ("spec", 9),
    ("decisions", 1),
    ("spec_star", 2),
    ("future", 1),
    ("research", 6),
    ("contracts", 3),
    ("src", 20),
    ("rules", 13),
    ("spec_mirror", 3),
)


def live_files(repo_root: Path = _REPO_ROOT) -> dict[str, tuple[str, ...]]:
    """Each root's live files, as sorted repo-relative ``/`` paths: S2's roots and suffixes, minus
    every ``repo`` row of ``RECORD_SET``. A missing root yields no files (and so fails its floor)."""
    result: dict[str, tuple[str, ...]] = {}
    for label, directory, recursive, name_glob, suffixes in ROOTS:
        base = repo_root / directory
        found: list[str] = []
        if base.is_dir():
            if recursive:
                candidates = (Path(dirpath) / name for dirpath, _dirs, names in os.walk(base)
                              for name in names)
            else:
                candidates = (entry for entry in base.iterdir() if entry.is_file())
            for path in candidates:
                if not path.match(name_glob) or path.suffix not in suffixes:
                    continue
                rel = path.relative_to(repo_root).as_posix()
                if not is_record("repo", rel):
                    found.append(rel)
        result[label] = tuple(sorted(found))
    return result


def floor_shortfalls(files: dict[str, tuple[str, ...]], floors=ROOT_FLOORS) -> list[str]:
    """One message per root whose live count is below its floor, or that has no floor at all."""
    floor_of = dict(floors)
    problems = [f"{label}: {len(paths)} live, floor {floor_of[label]}"
                for label, paths in files.items()
                if label in floor_of and len(paths) < floor_of[label]]
    problems += [f"{label}: no floor" for label in files if label not in floor_of]
    problems += [f"{label}: floor for a root the walk does not have" for label in floor_of
                 if label not in files]
    return problems


# --------------------------------------------------------------------------------------------------
# S1: section records.
# --------------------------------------------------------------------------------------------------

_DECISION_LOG = re.compile(r"## Decision Log\s*")
#: A released CHANGELOG entry: ``## [x.y.z]``, or this repo's dated sprint heading ``## <date> through
#: <date> — Sprint NNN`` (sprint-009 D11; sprints 001-004 carry one date). Only ``## Unreleased`` is live.
_RELEASED_ENTRY = re.compile(
    r"## \[\d+\.\d+\.\d+\].*|## 20\d\d-\d\d-\d\d(?: through 20\d\d-\d\d-\d\d)? — Sprint \d{3}\b.*")
_SECTION_END = re.compile(r"#{1,2} \S.*")


def _lines_with_offsets(raw: str):
    offset = 0
    for line in raw.splitlines(keepends=True):
        yield offset, line, line.rstrip("\r\n")
        offset += len(line)


def record_ranges(rel_path: str, raw: str) -> list[tuple[int, int]]:
    """Raw ``(start, end)`` ranges of ``raw`` that are section records and are not swept: text under
    exactly ``^## Decision Log\\s*$`` when ``rel_path`` is in ``SECTION_RECORD_FILES``, and a
    ``CHANGELOG.md`` entry under a released ``## [x.y.z]`` or dated ``## <date> through <date> — Sprint
    NNN`` heading (``_RELEASED_ENTRY``, D11). A section runs from its heading to the next level-1 or
    level-2 heading, or to the end of the file."""
    if rel_path in SECTION_RECORD_FILES:
        opens = _DECISION_LOG
    elif rel_path.rsplit("/", 1)[-1] == "CHANGELOG.md":
        opens = _RELEASED_ENTRY
    else:
        return []
    ranges: list[tuple[int, int]] = []
    start = None
    for offset, _line, text in _lines_with_offsets(raw):
        if start is not None and _SECTION_END.fullmatch(text):
            ranges.append((start, offset))
            start = None
        if start is None and opens.fullmatch(text):
            start = offset
    if start is not None:
        ranges.append((start, len(raw)))
    return ranges


def carries_decision_log(raw: str) -> list[int]:
    """1-based line numbers of every exact ``## Decision Log`` heading in ``raw``."""
    return [n for n, (_offset, _line, text) in enumerate(_lines_with_offsets(raw), start=1)
            if _DECISION_LOG.fullmatch(text)]


# --------------------------------------------------------------------------------------------------
# S3: quotation spans on the raw text, and the map from normalized text back to it.
# --------------------------------------------------------------------------------------------------

def _fence_marking_closure(state, start_line: int, end_line: int, silent: bool) -> bool:
    """markdown-it's CommonMark fence rule, which also records on each fence token whether a closing
    fence line ended it (``token.meta["closed"]``). The rule runs an unclosed fence to the end of its
    container -- the document, a blockquote or a list item -- and its token does not say which ending it
    met, so the last line the token holds is put to the rule's own closing test: the fence's marker
    character, at most three columns past the block indent, a run at least as long as the opener's, and
    spaces alone after it. Had that line passed, the rule's scan would have stopped at it as the closer,
    so it passes exactly when the fence closed."""
    first = len(state.tokens)
    if not _COMMONMARK_FENCE(state, start_line, end_line, silent):
        return False
    if not silent:
        token = state.tokens[first]
        last = state.line - 1
        closed = False
        if last > start_line and not state.is_code_block(last):
            pos = state.bMarks[last] + state.tShift[last]
            run = state.skipCharsStr(pos, token.markup[0])
            closed = run - pos >= len(token.markup) and state.skipSpaces(run) >= state.eMarks[last]
        token.meta["closed"] = closed
    return True


_COMMONMARK_FENCE = _markdown_block_rules.fence
_MARKDOWN = MarkdownIt("commonmark")
_MARKDOWN.block.ruler.at("fence", _fence_marking_closure,
                         {"alt": next(alt for name, _fn, alt in _markdown_block_rule_table if name == "fence")})

#: markdown-it's line ends (its ``normalize`` core rule): a fence token's line numbers count these.
_MARKDOWN_NEWLINE = re.compile(r"\r\n?|\n")


#: A line holding only blockquote markers: a blockquote's blank line (sprint-008 F011 review, iteration 2).
_BARE_BLOCKQUOTE = re.compile(r"[ \t]*(?:>[ \t]*)+")

def _blank_line(text: str) -> bool:
    """True for a blank line, and for a line holding only ``>`` markers -- a blank line inside a
    blockquote (sprint-008 F011 review, iteration 2: read as text, it joined the paragraphs it
    separates)."""
    return not text.strip() or bool(_BARE_BLOCKQUOTE.fullmatch(text))


def _lines_with_source(state, begin: int, end: int, indent: int) -> tuple[str, list[int]]:
    """``state.getLines(begin, end, indent, False)`` -- the text markdown-it's paragraph and setext-heading
    rules take as a block's content, container markers and indentation cut -- with the ``state.src``
    offset of each of its characters. The loop is ``StateBlock.getLines``'s own; a space that a tab only
    partly consumed by ``indent`` expands to takes the tab's offset. The result is asserted equal to
    ``getLines``, so the offsets can never describe a different string from the one markdown-it parses."""
    pieces: list[str] = []
    where: list[int] = []
    for line in range(begin, end):
        column = 0
        line_start = first = state.bMarks[line]
        last = state.eMarks[line] + 1 if line + 1 < end else state.eMarks[line]
        while first < last and column < indent:
            char = state.src[first]
            if char == "\t":
                column += 4 - (column + state.bsCount[line]) % 4
            elif char == " " or first - line_start < state.tShift[line]:
                column += 1
            else:
                break
            first += 1
        if column > indent:
            pieces.append(" " * (column - indent))
            where += [first - 1] * (column - indent)
        pieces.append(state.src[first:last])
        where += range(first, last)
    text = "".join(pieces)
    assert text == state.getLines(begin, end, indent, False), "the line map drifted from getLines()"
    return text, where


def _source_recording(rule):
    """markdown-it's paragraph, ATX-heading or setext-heading ``rule``, which also records on the inline
    token it pushes, as ``token.meta["source"]``, the ``state.src`` offset of each character of its
    ``content``: the text the rule took from the block's lines, stripped as the rule strips it. Only
    ``state`` knows where each line's container markers end (``bMarks``) while the rule runs, and the
    inline token keeps none of it.

    It also records, as ``token.meta["edges"]``, the character just before the content and the one just
    after it in that text, or a line end where there is none. Each rule strips its content with Python's
    ``str.strip``, which also removes ``\\x0b``, ``\\x1c`` to ``\\x1f``, ``\\x85``, U+2028 and U+2029;
    CommonMark strips only spaces and tabs, so such a character is still the content's first or last
    character there, and the neighbour of a quote at the edge (sprint-008 F011 review, iteration 8: read as
    a line end, it let ``\\x0b"Say`` open). A stripped space, tab or line end is whitespace either way. An
    ATX heading's text runs on to its closing sequence, which the rule cut before stripping, and a space or
    tab always stands before that sequence, so the character after the content is still a stripped one."""
    def recording(state, start_line: int, end_line: int, silent: bool) -> bool:
        first = len(state.tokens)
        if not rule(state, start_line, end_line, silent):
            return False
        if not silent:
            token = next(t for t in state.tokens[first:] if t.type == "inline")
            if rule is _markdown_block_rules.heading:
                pos = state.bMarks[start_line] + state.tShift[start_line]
                while pos < state.eMarks[start_line] and state.src[pos] == "#":
                    pos += 1
                text, where = state.src[pos:state.eMarks[start_line]], range(pos, state.eMarks[start_line])
            else:
                text, where = _lines_with_source(state, token.map[0], token.map[1], state.blkIndent)
            lead = len(text) - len(text.lstrip())
            end = lead + len(token.content)
            assert text[lead:end] == token.content, "the content map drifted"
            token.meta["source"] = list(where[lead:end])
            token.meta["edges"] = (text[lead - 1] if lead else "\n", text[end] if end < len(text) else "\n")
        return True
    return recording


def _code_span_recording(state, silent: bool) -> bool:
    """markdown-it's CommonMark backtick rule, which also records on each code span token it pushes, as
    ``token.meta["at"]``, the span's ``(start, end)`` in the inline content, backtick runs included. The
    rule owns escapes (``\\```), run-length matching and the rest of CommonMark 6.1; this only reads
    where it stopped."""
    start, count = state.pos, len(state.tokens)
    if not _COMMONMARK_BACKTICK(state, silent):
        return False
    if len(state.tokens) > count and state.tokens[-1].type == "code_inline":
        state.tokens[-1].meta["at"] = (start, state.pos)
    return True


def _image_recording(state, silent: bool) -> bool:
    """markdown-it's CommonMark image rule, which also records on each image token it pushes, as
    ``token.meta["at"]``, where its description starts in the text the rule read. The rule parses the
    description on an inline state of its own, into the image token's ``children``, so a code span found
    there carries offsets in the description (``_code_span_recording``), and ``_image_code_spans`` adds
    this offset to place it in the content."""
    start, count = state.pos, len(state.tokens)
    if not _COMMONMARK_IMAGE(state, silent):
        return False
    if len(state.tokens) > count and state.tokens[-1].type == "image":
        token = state.tokens[-1]
        assert state.src[start + 2:start + 2 + len(token.content)] == token.content, "the image map drifted"
        token.meta["at"] = start + 2
    return True


def _raw_recording(rule):
    """The ``rule`` for raw HTML (``_commonmark_html_inline``) or markdown-it's for autolinks, which also
    records on the token it opens with -- ``html_inline``, or an autolink's ``link_open`` -- as
    ``token.meta["raw"]``, the ``(start, end)`` of the whole ``<...>`` in the text the rule read. CommonMark
    2.4 processes no backslash escape inside either (``_quote_pairs``). The token is found by its type:
    ``push()`` first flushes any pending text as a token of its own. A link's destination and title are not
    recorded: CommonMark processes escapes there."""
    def recording(state, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        if not rule(state, silent):
            return False
        opened = next((t for t in state.tokens[count:] if t.type in ("html_inline", "link_open")), None)
        if opened is not None:
            opened.meta["raw"] = (start, state.pos)
        return True
    return recording


#: CommonMark 0.31.2's raw HTML (6.6), in markdown-it's ``src``, where every line end is ``\n``. markdown-it's
#: own grammar (``common/html_re.py``) departs from it (sprint-008 F011 review, iteration 10): between a tag's
#: parts it takes Python's ``\s``, which also holds U+00A0, ``\x0b``, ``\x0c``, ``\x1c`` to ``\x1f``, ``\x85``,
#: U+2028 and more, where 0.31.2 allows spaces, tabs and at most one line ending; its unquoted attribute value
#: refuses ``\x01`` to ``\x1f``, where 0.31.2 refuses only spaces, tabs, line endings, quotes, ``=``, ``<``,
#: ``>`` and a backtick; and its comment refuses ``<!-- a--->``, where 0.31.2 takes any text up to the first
#: ``-->`` (or ``<!-->`` or ``<!--->``). Before an attribute at least one space, tab or line ending stands.
#: The whitespace splits one way only: ``[ \t]*\n?[ \t]*`` read one space two ways, and tag-shaped prose
#: with no ``>`` backtracked 2^n (iteration 11).
_HTML_SPACE = r"[ \t]*(?:\n[ \t]*)?"
_HTML_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
_HTML_ATTRIBUTE = (r"(?=[ \t\n])" + _HTML_SPACE + r"[A-Za-z_:][A-Za-z0-9_.:-]*"
                   r"(?:" + _HTML_SPACE + "=" + _HTML_SPACE + r"""(?:[^ \t\n\r"'=<>`]+|'[^']*'|"[^"]*"))?""")
_HTML_OPEN_TAG = "<" + _HTML_TAG_NAME + "(?:" + _HTML_ATTRIBUTE + ")*" + _HTML_SPACE + "/?>"
_HTML_CLOSING_TAG = "</" + _HTML_TAG_NAME + _HTML_SPACE + ">"
_HTML_RAW = re.compile("|".join([_HTML_OPEN_TAG, _HTML_CLOSING_TAG, r"<!-->", r"<!--->", r"<!--[\s\S]*?-->",
                                 r"<\?[\s\S]*?\?>", r"<![A-Za-z][^>]*>", r"<!\[CDATA\[[\s\S]*?\]\]>"]))


def _commonmark_html_inline(state, silent: bool) -> bool:
    """markdown-it's html_inline rule (``rules_inline/html_inline.py``) with CommonMark 0.31.2's raw-HTML
    grammar (``_HTML_RAW``) in place of markdown-it's. The rest is the rule's own: it matches from
    ``state.pos`` as the rule does, and counts an ``<a ...>`` or ``</a>`` toward ``state.linkLevel``."""
    pos = state.pos
    if state.src[pos] != "<" or pos + 2 >= state.posMax:
        return False
    match = _HTML_RAW.match(state.src, pos)
    if not match:
        return False
    if not silent:
        token = state.push("html_inline", "", 0)
        token.content = match.group(0)
        if _markdown_is_link_open(token.content):
            state.linkLevel += 1
        if _markdown_is_link_close(token.content):
            state.linkLevel -= 1
    state.pos = match.end()
    return True


#: CommonMark 0.31.2's HTML-block conditions (4.6) as markdown-it's html_block rule reads them: a start
#: pattern matched at the line's first non-indent character, an end pattern searched in each line, and
#: whether the block may interrupt a paragraph. markdown-it's own (``rules_block/html_block.py``) departs
#: from 0.31.2 (iteration 10): it starts a declaration block (type 4) only on an uppercase letter; it takes
#: Python's ``\s`` after a type-1 or type-6 name and after a type-7 tag, where 0.31.2 takes a space or a
#: tab; its type-7 tag is its own tag grammar (``_HTML_RAW`` holds 0.31.2's); its type 7 takes a ``pre``,
#: ``script``, ``style`` or ``textarea`` tag, which 0.31.2 leaves to type 1 alone; and its case-insensitive
#: names match non-ASCII letters (``<ſtyle``), where 0.31.2 folds ASCII case only.
_HTML_RAW_TEXT_NAMES = r"(?:pre|script|style|textarea)"
_HTML_BLOCK_CONDITIONS = [
    (re.compile("<" + _HTML_RAW_TEXT_NAMES + r"(?:[ \t>]|$)", re.I | re.A),
     re.compile("</" + _HTML_RAW_TEXT_NAMES + ">", re.I | re.A), True),
    (re.compile(r"<!--"), re.compile(r"-->"), True),
    (re.compile(r"<\?"), re.compile(r"\?>"), True),
    (re.compile(r"<![A-Za-z]"), re.compile(r">"), True),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>"), True),
    (re.compile(r"</?(?:" + "|".join(_markdown_block_names) + r")(?:[ \t]|/?>|$)", re.I | re.A),
     re.compile(r"^$"), True),
    (re.compile(r"(?!</?" + _HTML_RAW_TEXT_NAMES + r"(?![A-Za-z0-9-]))(?:" + _HTML_OPEN_TAG + "|"
                + _HTML_CLOSING_TAG + r")[ \t]*$", re.I | re.A), re.compile(r"^$"), False),
]


def _commonmark_html_block(state, start_line: int, end_line: int, silent: bool) -> bool:
    """markdown-it's html_block rule (``rules_block/html_block.py``) with CommonMark 0.31.2's start and end
    conditions (``_HTML_BLOCK_CONDITIONS``) in place of markdown-it's. The rest is the rule's own: the
    start line and each later line are read from their first non-indent character, a block ends at the
    first line meeting its end condition (that line included, unless blank) or where its container ends,
    and in silent mode it answers whether it may interrupt a paragraph."""
    if state.is_code_block(start_line):
        return False
    pos = state.bMarks[start_line] + state.tShift[start_line]
    line_text = state.src[pos:state.eMarks[start_line]]
    if not line_text.startswith("<"):
        return False
    condition = next((c for c in _HTML_BLOCK_CONDITIONS if c[0].match(line_text)), None)
    if condition is None:
        return False
    if silent:
        return condition[2]
    next_line = start_line + 1
    if not condition[1].search(line_text):
        while next_line < end_line:
            if state.sCount[next_line] < state.blkIndent:
                break
            pos = state.bMarks[next_line] + state.tShift[next_line]
            line_text = state.src[pos:state.eMarks[next_line]]
            if condition[1].search(line_text):
                if line_text:
                    next_line += 1
                break
            next_line += 1
    state.line = next_line
    token = state.push("html_block", "", 0)
    token.map = [start_line, next_line]
    token.content = state.getLines(start_line, next_line, state.blkIndent, True)
    return True


_COMMONMARK_BACKTICK = _markdown_inline_rules.backtick
_COMMONMARK_IMAGE = _markdown_inline_rules.image
for _name in ("paragraph", "heading", "lheading"):
    _MARKDOWN.block.ruler.at(_name, _source_recording(getattr(_markdown_block_rules, _name)),
                             {"alt": next(alt for name, _fn, alt in _markdown_block_rule_table if name == _name)})
_MARKDOWN.inline.ruler.at("backticks", _code_span_recording)
_MARKDOWN.inline.ruler.at("image", _image_recording)
_MARKDOWN.block.ruler.at("html_block", _commonmark_html_block,
                         {"alt": next(alt for name, _fn, alt in _markdown_block_rule_table if name == "html_block")})
_MARKDOWN.inline.ruler.at("autolink", _raw_recording(_markdown_inline_rules.autolink))
_MARKDOWN.inline.ruler.at("html_inline", _raw_recording(_commonmark_html_inline))
#: markdown-it refuses a ``javascript:``, ``vbscript:``, ``file:`` or non-image ``data:`` link, autolink or
#: image, a guard for rendering. CommonMark reads each as one, and the gate never renders (iteration 10).
_MARKDOWN.validateLink = lambda url: True


def _gate_reading(raw: str) -> tuple[str, list[int], list]:
    """``(text, starts, tokens)``: the one CommonMark reading the gate takes of the ``.md`` text ``raw``.

    ``text`` is ``raw`` with each unclosed fence's opener line read as letters of the same length, from
    its fence run to its line end: markdown-it (``_MARKDOWN``) parses ``raw``, the first fence no closing
    line ends (``_fence_marking_closure``) has its line turned to letters, and the text is parsed again,
    until every fence left is closed. So ``text`` has ``raw``'s length and offsets, an unclosed fence and
    its info string are prose, and a later closed fence is still code. ``tokens`` is markdown-it's
    reading of ``text``, and every span the gate finds comes from it alone (sprint-008 F011 review,
    iteration 6: the per-reading block grouping this replaced cut a delimiter off from its CommonMark
    partner, and pairing restarted inside the narrower region). ``starts`` holds the raw offset of each
    line's start over markdown-it's own line ends (``_MARKDOWN_NEWLINE``), and ``len(raw)`` last."""
    starts = [0] + [m.end() for m in _MARKDOWN_NEWLINE.finditer(raw)]
    if starts[-1] != len(raw):
        starts.append(len(raw))
    text = raw
    while True:
        tokens = _MARKDOWN.parse(text)
        unclosed = next((t for t in tokens if t.type == "fence" and not t.meta["closed"]), None)
        if unclosed is None:
            return text, starts, tokens
        at = text.index(unclosed.markup, starts[unclosed.map[0]])
        line_end = _MARKDOWN_NEWLINE.search(text, at)
        stop = line_end.start() if line_end else len(text)
        text = text[:at] + "x" * (stop - at) + text[stop:]


def _fenced_blocks(raw: str) -> list[tuple[int, int]]:
    """Raw ranges of fenced code blocks, fence lines included, as CommonMark reads them: markdown-it's
    fence tokens, container markers and all, each token's line range mapped back to raw offsets over
    markdown-it's own line ends (``_MARKDOWN_NEWLINE``), so CRLF, CR and mixed endings map as it counts
    them (sprint-008 F011 review, iteration 3: the hand-written parser missed fences behind a list marker
    or a ``>``, and misread items, lazy lines, thematic breaks and indented closers).

    A fence no closing line ends opens no block (sprint-008 F011 review: a block run to the end of the
    file sheltered all the prose after it). CommonMark runs it to the end of its container -- the
    document, a blockquote or a list item (``_fence_marking_closure`` tells which) -- and here its opener
    line is read as prose instead (``_gate_reading``). So its lines, and a list item's fence the item
    ends before any closer, stay prose, and a later closed fence is still code. Indented code blocks are
    not fenced blocks and shelter nothing, as before."""
    _text, starts, tokens = _gate_reading(raw)
    return _fence_ranges(starts, tokens)


def _fence_ranges(starts: list[int], tokens) -> list[tuple[int, int]]:
    """The raw range of each fence token of a reading, over its line ``starts``."""
    lines = len(starts) - 1
    return [(starts[a], starts[min(b, lines)]) for a, b in (t.map for t in tokens if t.type == "fence")]


def _paragraphs(raw: str, fenced: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Raw ranges of runs of non-blank lines outside fenced blocks: the unit a presence row reads
    (``presence_block``). A line of ``>`` markers alone is blank (``_blank_line``). Quote and code spans
    are CommonMark's paragraphs and headings, not these (``quote_spans``)."""
    paragraphs: list[tuple[int, int]] = []
    start = None
    for offset, line, text in _lines_with_offsets(raw):
        inside_fence = any(a <= offset < b for a, b in fenced)
        if inside_fence or _blank_line(text):
            if start is not None:
                paragraphs.append((start, offset))
                start = None
        elif start is None:
            start = offset
    if start is not None:
        paragraphs.append((start, len(raw)))
    return paragraphs


def _quote_roles(content: str, i: int, edges: tuple[str, str] = ("\n", "\n")) -> tuple[bool, bool]:
    """``(can open, can close)`` for the straight or curly quote ``content[i]`` in a paragraph's or
    heading's inline content, by CommonMark 6.2's delimiter rule as it reads ``_``: it can open when
    left-flanking and either not right-flanking or preceded by punctuation, and close when right-flanking
    and either not left-flanking or followed by punctuation. So ``5" strap`` and ``5” strap`` can only
    close, a ``“`` between spaces can do neither, and an intraword ``x"y`` is ambiguous and can do
    neither.

    A quote's neighbours are characters of the content markdown-it parses, never of the raw lines
    (sprint-008 F011 review, iteration 7: in ``>"`` the blockquote marker read as punctuation before the
    quote, where CommonMark's content has a line end). A line end is whitespace. Past the content's first
    and last character the neighbours are ``edges``: the characters markdown-it's strip removed there, or
    line ends (``_source_recording``; iteration 8). Whitespace is CommonMark's Unicode whitespace, the Zs
    category plus tab, line feed, form feed and carriage return, not ``str.isspace`` (iteration 7:
    ``\\x0b``, ``\\x1c`` to ``\\x1f``, ``\\x85``, U+2028 and U+2029 are not whitespace in CommonMark).
    Punctuation is CommonMark 0.31's Unicode punctuation, the P and S categories, which hold every ASCII
    punctuation character."""
    before = content[i - 1] if i > 0 else edges[0]
    after = content[i + 1] if i + 1 < len(content) else edges[1]

    def space(char: str) -> bool:
        return char in "\t\n\f\r" or unicodedata.category(char) == "Zs"

    def punct(char: str) -> bool:
        return unicodedata.category(char)[0] in "PS"

    left = not space(after) and (not punct(after) or space(before) or punct(before))
    right = not space(before) and (not punct(before) or space(after) or punct(after))
    return left and (not right or punct(before)), right and (not left or punct(after))


def _quote_pairs(content: str, code: list[tuple[int, int]], edges: tuple[str, str] = ("\n", "\n"),
                 raw: list[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """S3's quote spans in a paragraph's or heading's inline ``content``, as ``(start, end)`` in it,
    delimiters included, never counting a quote inside one of its ``code`` spans (content offsets, backtick
    runs included). Straight quotes pair only with an even count of them: with an odd count one is stray,
    and greedy pairing would shelter the prose between it and the next quote (sprint-008 F011 review). Curly quotes pair only when they run strictly
    ``“ ” “ ”``: a stray ``“`` or ``”`` pairs none of them (iteration 2). A quote, straight or curly,
    pairs only a left-flanking opener with the next right-flanking closer (``_quote_roles``); an
    ambiguous quote pairs nothing (iteration 2: two stray inch marks, ``5"`` and ``3"``, made an even
    count and sheltered the prose between them; iteration 3: a stray ``“`` and a ``5”`` did the same).
    ``edges`` are the content's outer neighbours (``_quote_roles``).

    An escaped ``\\"`` -- a straight quote after an odd run of backslashes, outside a code span and outside
    its ``raw`` ranges -- is literal text to CommonMark 2.4 and never a delimiter, so it neither opens nor
    closes (iteration 8: the backslash read as punctuation, and ``x\\"a`` opened where ``x"a`` cannot). It
    still counts toward the parity and still stands between an opener and a later closer, as the ``"`` it
    renders as would. ``raw`` holds the content's raw HTML and autolinks (``_raw_ranges``), where
    CommonMark 2.4 processes no escape: a quote there keeps its roles, backslash or not (iteration 9: read
    as literal, it lost them, and a later quote opened and paired over the live line). A link destination
    or title is not raw; CommonMark processes the escape there. A curly quote is not ASCII punctuation, so
    a backslash never escapes one. A literal quote changes which quotes pair, so this reading can shelter
    a line 8e2a08e left bare as well as bare one it sheltered: in ``a" x\\"b c)"(`` the ``"(`` opens,
    as it does in the twin ``a" x"b c)"(``."""
    coded = [False] * len(content)
    for a, b in code:
        coded[a:b] = [True] * (b - a)
    quotes = [i for i, char in enumerate(content) if char in "\"“”" and not coded[i]]
    unescaped = [False] * len(content)
    for a, b in raw:
        unescaped[a:b] = [True] * (b - a)
    literal = set()
    for i in quotes:
        run = i
        while run > 0 and content[run - 1] == "\\":
            run -= 1
        if content[i] == '"' and (i - run) % 2 and not unescaped[i]:
            literal.add(i)
    curly = "".join(content[i] for i in quotes if content[i] != '"')
    closers = {}
    if sum(content[i] == '"' for i in quotes) % 2 == 0:
        closers['"'] = '"'
    if curly == "“”" * (len(curly) // 2):
        closers["“"] = "”"
    pairs: list[tuple[int, int]] = []
    k = 0
    while k < len(quotes):
        i = quotes[k]
        closer = closers.get(content[i])
        if closer is not None and i not in literal and _quote_roles(content, i, edges)[0]:
            m = next((m for m in range(k + 1, len(quotes)) if content[quotes[m]] == closer), None)
            if m is not None and quotes[m] not in literal and _quote_roles(content, quotes[m], edges)[1]:
                pairs.append((i, quotes[m] + 1))
                k = m + 1
                continue
        k += 1
    return pairs


def _image_code_spans(children, base: int = 0) -> list[tuple[int, int]]:
    """The code spans in every image description among an inline token's ``children``, at any depth, as
    ``(start, end)`` in the text ``base`` places ``children`` in. markdown-it parses a description into the
    image token's own ``children``, so its code spans are not the inline token's; CommonMark still reads
    each as a code span, and a quote inside one is no delimiter (sprint-008 F011 review, iteration 8: the
    quote in ``![`"`](i.png)`` was counted and made three quotes an even four). A link in a description is
    parsed there with it; an image in link text is one of the inline token's own children."""
    spans: list[tuple[int, int]] = []
    for child in children or ():
        if child.type == "image":
            at = base + child.meta["at"]
            spans += [(at + a, at + b) for a, b in (c.meta["at"] for c in child.children or ()
                                                     if c.type == "code_inline")]
            spans += _image_code_spans(child.children, at)
    return spans


def _raw_ranges(children, base: int = 0) -> list[tuple[int, int]]:
    """The raw HTML and autolinks among an inline token's ``children`` (``_raw_recording``), in image
    descriptions at any depth too, as ``(start, end)`` in the text ``base`` places ``children`` in. Link
    text is parsed on the inline token's own state, so one there is among its children; an image
    description is parsed on a state of its own (``_image_code_spans``)."""
    ranges: list[tuple[int, int]] = []
    for child in children or ():
        if "raw" in child.meta:
            a, b = child.meta["raw"]
            ranges.append((base + a, base + b))
        if child.type == "image":
            ranges += _raw_ranges(child.children, base + child.meta["at"])
    return ranges


def _raw_offset_map(text: str, starts: list[int]):
    """The map from an offset in markdown-it's ``src`` for ``text`` (line ends made ``\\n``) to the raw
    offset, over the same line numbers: markdown-it changes nothing else a line's length depends on."""
    src_starts = [0] + [m.end() for m in re.finditer("\n", _MARKDOWN_NEWLINE.sub("\n", text))]

    def raw_offset(offset: int) -> int:
        line = bisect.bisect_right(src_starts, offset) - 1
        return starts[line] + offset - src_starts[line]
    return raw_offset


def quote_spans(raw: str) -> list[tuple[int, int]]:
    """S3 on the raw text, before ``normalize()``: raw ``(start, end)`` ranges, delimiters included, of
    fenced blocks, code spans, and straight (``"..."``) and curly (``“...”``) double-quoted spans, all
    read from the one CommonMark reading ``_gate_reading`` takes, where an unclosed fence is prose.

    - Fenced blocks are its closed fence tokens (``_fenced_blocks``).
    - Code spans are its ``code_inline`` tokens: markdown-it's backtick rule decides escapes and run
      lengths (sprint-008 F011 review, iteration 6: a hand-written pairing opened a span on an escaped
      ``\\```). Only a paragraph or a heading holds one; an HTML block and an indented code block hold
      no span.
    - Quote spans are paired once per paragraph or heading, over the inline content markdown-it parses
      (``token.content``: container markers and indentation cut, line ends ``\\n``), never on a quote in
      a code span (``_quote_pairs``). A blank line, a ``>``-only line and every other block edge end the
      paragraph, as CommonMark reads it; ``>`` opens nothing, so a blockquote is not quotation, and a
      quote's neighbours are content characters, never a container marker (iteration 7). A quote in a
      code span of an image description is not counted either, and that code span is not listed as a
      span of its own (``_image_code_spans``). A content edge's neighbour is the character markdown-it's
      strip removed there (iteration 8). An escaped ``\\"`` is literal and never a delimiter, except in
      raw HTML or an autolink, where CommonMark reads no escape (``_raw_ranges``; iteration 9). Raw HTML
      and HTML blocks are read by CommonMark 0.31.2's grammar, and every link destination is a link's
      (``_commonmark_html_inline``, ``_commonmark_html_block``; iteration 10).

    markdown-it keeps no offsets for inline content, so each paragraph's and heading's content carries
    the ``src`` offset of every character (``_source_recording``) and each code span its offsets in that
    content (``_code_span_recording``); a ``src`` offset maps back to ``raw`` over the same line numbers,
    since markdown-it changes only line ends. Code and quote spans are found in the content and mapped
    back through those offsets. A code span inside a quote span is not listed again."""
    text, starts, tokens = _gate_reading(raw)
    raw_offset = _raw_offset_map(text, starts)
    spans = _fence_ranges(starts, tokens)
    for token in tokens:
        if token.type != "inline":
            continue
        source = token.meta["source"]
        at = [child.meta["at"] for child in token.children if child.type == "code_inline"]
        code = [(raw_offset(source[a]), raw_offset(source[b - 1]) + 1) for a, b in at]
        quoted = [(raw_offset(source[a]), raw_offset(source[b - 1]) + 1)
                  for a, b in _quote_pairs(token.content, at + _image_code_spans(token.children),
                                           token.meta["edges"], _raw_ranges(token.children))]
        spans += quoted + [(a, b) for a, b in code if not any(c <= a and b <= d for c, d in quoted)]
    return sorted(spans)


def normalize_with_offsets(raw: str) -> tuple[str, list[int]]:
    """``normalize(raw)`` together with, for each of its characters, the raw offset it came from.

    It applies ``normalize()``'s own steps (NFKC, the module's dropped characters and typography
    folds, whitespace collapse, casefold) one character cluster at a time -- a base character with
    its combining marks -- and then asserts the result is exactly ``normalize(raw)``, so the map can
    never describe a different string from the one the gate matches."""
    out: list[str] = []
    offsets: list[int] = []
    pending_space = None
    i = 0
    while i < len(raw):
        j = i + 1
        while j < len(raw) and unicodedata.combining(raw[j]):
            j += 1
        piece = unicodedata.normalize("NFKC", raw[i:j]).translate(_OM._DROPPED)
        for symbol, ascii_form in _OM._TYPOGRAPHY.items():
            piece = piece.replace(symbol, ascii_form)
        for char in piece:
            if char.isspace():
                if pending_space is None:
                    pending_space = i
                continue
            if pending_space is not None and out:
                out.append(" ")
                offsets.append(pending_space)
            pending_space = None
            for folded in char.casefold():
                out.append(folded)
                offsets.append(i)
        i = j
    text = "".join(out)
    assert text == normalize(raw), "the offset map drifted from normalize()"
    return text, offsets


def hit_is_quoted(raw: str, norm_start: int, norm_end: int, offsets=None, spans=None) -> bool:
    """S3: a hit at ``[norm_start, norm_end)`` of ``normalize(raw)`` is quotation only when its raw
    extent lies wholly inside one quote span. Half inside is not sheltered. A caller that scans one
    file for many hits passes that file's ``offsets`` and ``spans`` once instead of re-deriving them."""
    if offsets is None:
        _text, offsets = normalize_with_offsets(raw)
    if spans is None:
        spans = quote_spans(raw)
    raw_start, raw_end = offsets[norm_start], offsets[norm_end - 1] + 1
    return any(a <= raw_start and raw_end <= b for a, b in spans)


#: T219: a Python comment line's marker -- optional indentation, a ``#`` run and a ``#:`` doc-comment
#: colon -- at the start of a line. ``normalize()`` keeps it, so a meaning wrapped over two ``#`` lines
#: reads "... reference # and is never struck" and no pattern matches it.
_WRAPPED_COMMENT = re.compile(r"^[ \t]*#+:?", re.MULTILINE)

#: Sprint-008 F011 review: a Markdown blockquote line's marker -- up to three spaces of indentation and a
#: run of ``>``, each with an optional following space or tab. ``normalize()`` keeps it, so a meaning
#: wrapped over two ``>`` lines reads "... reference > and is never struck" and no pattern matches it. A
#: blockquote stays swept, not quotation (S3); only its marker goes. Iteration 2: a ``>`` run after list
#: markers (``- > ``, ``10. > ``) or after a continuation indent of any width (``    > ``) is a marker too. The
#: ``item`` group holds the list markers, which stay; the indentation before a bare ``>`` run goes with it.
_BLOCKQUOTE_MARKER = re.compile(
    r"^(?P<item>[ \t]*(?:(?:[-+*]|[0-9]{1,9}[.)])[ \t]+)+)?[ \t]*(?:>[ \t]?)+", re.MULTILINE)


def _line_markers(path: str):
    """The line-marker pattern ``gate_source`` removes from the file ``path``: comment markers in ``.py``
    (T219) and, since the sprint-008 F011 review, ``.yaml``/``.yml``; blockquote markers in ``.md``."""
    if path.endswith((".py", ".yaml", ".yml")):
        return _WRAPPED_COMMENT
    if path.endswith(".md"):
        return _BLOCKQUOTE_MARKER
    return None


def gate_source(path: str, raw: str) -> tuple[str, list[int]]:
    """The file ``path``'s text before ``normalize()``, with each character's raw offset: ``raw`` with
    every line marker ``_line_markers`` names removed and each newline kept (so line numbers hold) --
    for ``.py``, ``.yaml`` and ``.yml`` each comment line's ``#`` marker (``_WRAPPED_COMMENT``), for
    ``.md`` each blockquote line's ``>`` marker (``_BLOCKQUOTE_MARKER``), keeping any list marker before
    it (its ``item`` group)."""
    markers = _line_markers(path)
    if markers is None:
        return raw, list(range(len(raw)))
    pos = 0
    pieces: list[str] = []
    source_to_raw: list[int] = []
    for marker in markers.finditer(raw):
        cut = marker.end("item") if "item" in markers.groupindex and marker.group("item") else marker.start()
        pieces.append(raw[pos:cut])
        source_to_raw += range(pos, cut)
        pos = marker.end()
    pieces.append(raw[pos:])
    source_to_raw += range(pos, len(raw))
    return "".join(pieces), source_to_raw


def gate_text(path: str, raw: str) -> tuple[str, list[int]]:
    """The text the gate matches in the file ``path``, with each character's raw offset:
    ``normalize_with_offsets`` of ``gate_source``. For a file with no line marker that is
    ``normalize_with_offsets(raw)`` unchanged. ``normalize()`` itself is shared with research/00's
    checker and is not touched (T219)."""
    source, source_to_raw = gate_source(path, raw)
    text, offsets = normalize_with_offsets(source)
    return text, [source_to_raw[o] for o in offsets]


def gate_normal(path: str, raw: str) -> str:
    """``gate_text``'s text alone: what every census and exception check compares an excerpt against."""
    return gate_text(path, raw)[0]


# --------------------------------------------------------------------------------------------------
# S15: test ids.
# --------------------------------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^A-Za-z0-9_]")


def slug(text: str) -> str:
    """``text`` with every character outside ``[A-Za-z0-9_]`` replaced by ``_``."""
    return _NON_WORD.sub("_", text)


def site_id(path: str, key: str, n: int) -> str:
    """S15's parametrize id, ``<path slug>__<key slug>__<n>``; ``path`` is compared as posix."""
    return f"{slug(Path(path).as_posix())}__{slug(key)}__{n}"


# --------------------------------------------------------------------------------------------------
# AC1 (T199): the hit scan over OLD_MEANINGS, the key roots (S12), exceptions (S4) and pending
# sites (S15).
# --------------------------------------------------------------------------------------------------

OLD_MEANINGS = _OM.OLD_MEANINGS

#: S12's mechanism: a key listed here reaches only the files under its root prefixes; a key not listed
#: reaches every root. All four T-07 keys (``research00_old_meanings.py``) reach the rule files only:
#: that is the glossary agents read first, and the other ~30 "band" sites go to IDEA-100.
KEY_ROOTS = MappingProxyType({
    "T07-acwr-band": (".claude/rules/",),
    "T07-ctl-rise-band": (".claude/rules/",),
    "T07-tolerance-band": (".claude/rules/",),
    "T07-tsb-target-form-band": (".claude/rules/",),
})


def key_reaches(key: str, path: str, key_roots=KEY_ROOTS) -> bool:
    """True when ``key``'s pattern is searched in the live file ``path`` (S12)."""
    roots = key_roots.get(key)
    return roots is None or any(path.startswith(root) for root in roots)


@dataclass(frozen=True)
class Hit:
    """One match of one key's pattern in one live file, outside every section record. ``n`` counts
    the key's matches in that file from 1; ``start`` and ``end`` are offsets into the file's
    ``normalize()``d text. ``quoted`` is S3; ``sheltered_by`` lists the ``EXCEPTIONS`` indexes whose
    excerpt spans the whole hit in this file (S4; IDEA-106 item 8: an overlap shelters nothing)."""
    path: str
    key: str
    n: int
    line: int
    start: int
    end: int
    matched: str
    quoted: bool
    sheltered_by: tuple[int, ...]

    @property
    def ident(self) -> str:
        return site_id(self.path, self.key, self.n)

    @property
    def live(self) -> bool:
        """Neither quotation nor sheltered: the gate's red unless a pending file covers it."""
        return not self.quoted and not self.sheltered_by


def _is_exception_triple(entry) -> bool:
    return isinstance(entry, tuple) and len(entry) == 3 and all(isinstance(x, str) for x in entry)


def _excerpt_ranges(text: str, path: str, exceptions) -> list[tuple[int, int, int]]:
    """``(index, start, end)`` of each occurrence of each exception's normalized excerpt in ``text``,
    the normalized text of ``path``. A malformed entry, or one naming another file, covers nothing."""
    ranges = []
    for i, entry in enumerate(exceptions):
        if not _is_exception_triple(entry) or Path(entry[0]).as_posix() != path:
            continue
        excerpt = normalize(entry[1])
        if not excerpt:
            continue
        ranges += [(i, m.start(), m.end()) for m in re.finditer(re.escape(excerpt), text)]
    return ranges


def scan(repo_root: Path = _REPO_ROOT, old_meanings=None, exceptions=None,
         key_roots=KEY_ROOTS) -> list[Hit]:
    """Every hit of every key over the live files of ``repo_root`` (S2), after ``normalize()``,
    skipping text under a section record (S1). Quoted and sheltered hits are returned, flagged; the
    caller decides what is red. Keys are taken in ``OLD_MEANINGS`` order, files in walk order."""
    old_meanings = OLD_MEANINGS if old_meanings is None else old_meanings
    exceptions = _OM.EXCEPTIONS if exceptions is None else exceptions
    hits: list[Hit] = []
    for path in (p for paths in live_files(repo_root).values() for p in paths):
        raw = (repo_root / path).read_text(encoding="utf-8")
        text, offsets = gate_text(path, raw)
        records = record_ranges(path, raw)
        spans = quote_spans(raw) if path.endswith(".md") else None
        excerpts = _excerpt_ranges(text, path, exceptions)
        for key, meaning in old_meanings.items():
            if not key_reaches(key, path, key_roots):
                continue
            n = 0
            for match in re.finditer(meaning.pattern, text):
                start, end = match.start(), match.end()
                raw_start = offsets[start]
                if any(a <= raw_start < b for a, b in records):
                    continue
                n += 1
                hits.append(Hit(
                    path=path, key=key, n=n, line=raw.count("\n", 0, raw_start) + 1,
                    start=start, end=end, matched=match.group(0),
                    quoted=spans is not None and hit_is_quoted(raw, start, end, offsets, spans),
                    # IDEA-106 item 8 (T229): an excerpt shelters a hit only when it spans the whole hit.
                    sheltered_by=tuple(sorted({i for i, a, b in excerpts if a <= start and end <= b})),
                ))
    return hits


def exception_shelter_errors(hits: list[Hit], exceptions) -> list[str]:
    """S4: each exception must shelter at least one hit, in its own file."""
    sheltering = {i for hit in hits for i in hit.sheltered_by}
    return [f"EXCEPTIONS #{i} shelters no hit: {entry!r}"
            for i, entry in enumerate(exceptions) if i not in sheltering]


#: S15: one ``<task id>.csv`` per owning site task, columns ``path,key``; ``*`` is every key.
PENDING_DIR = Path(__file__).parent / "data" / "research00_pending"
PENDING_HEADER = ["path", "key"]


def read_pending(pending_dir: Path = PENDING_DIR) -> dict[str, list[tuple[str, str]]]:
    """``{task id: [(path, key), ...]}`` from every ``*.csv`` in ``pending_dir``; paths as posix. An
    absent directory, or a directory with no file, is the end state: nothing is pending."""
    pending: dict[str, list[tuple[str, str]]] = {}
    if not pending_dir.is_dir():
        return pending
    for csv_path in sorted(pending_dir.glob("*.csv")):
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows and rows[0] == PENDING_HEADER, f"{csv_path.name}: header is not path,key: {rows[:1]}"
        assert all(len(row) == 2 for row in rows[1:]), f"{csv_path.name}: a row is not path,key"
        pending[csv_path.stem] = [(Path(p).as_posix(), k) for p, k in rows[1:]]
    return pending


def pending_tasks(hit: Hit, pending: dict[str, list[tuple[str, str]]]) -> list[str]:
    """The task ids whose pending file covers ``hit``: its ``(path, key)``, or ``(path, *)``."""
    return sorted(task for task, rows in pending.items()
                  if (hit.path, hit.key) in rows or (hit.path, "*") in rows)


#: The planned owner of every ``(path, key)`` that hits at ``a15610d``/HEAD (T199's table, from the
#: sprint-008 plan). Rows are ``(path glob, key, owner)``; the key is an exact ``OLD_MEANINGS`` name or
#: ``*``. For a named key, rows naming that key win over ``*`` rows for the same path. The C19 and
#: HRV-01 families are expanded to their exact names. T218's rows are gone: it narrowed C19-hrv-04 off
#: the five correct-prose sites (``NARROWED_FROM``), and ``EXCEPTIONS`` shelters ``hrv_trend.py`` for
#: F009 (S4). Hits outside every row fail the gate.
OWNERSHIP = (
    ("specification/spec/03-derived-metric-formulas.md", "*", "T202"),
    ("specification/spec/03-derived-metric-formulas.md", "C19-hrv-03-tag-and-confidence", "T213"),
    ("specification/spec/03-derived-metric-formulas.md", "C19-hrv-04-reduced-confidence", "T213"),
    ("specification/spec/03-derived-metric-formulas.md", "HRV-01-R13-four-tier-hierarchy", "T213"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C19-hrv-03-tag-and-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C19-hrv-04-reduced-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "HRV-01-R13-four-tier-hierarchy", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "PRIN-10-C19-reduced-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "*", "T214"),
    ("specification/spec/04-*", "*", "T204"),
    ("specification/spec/05-*", "*", "T204"),
    ("specification/spec/08-*", "*", "T204"),
    ("specification/spec/09-*", "*", "T204"),
    # T200: the census found spec/07's preamble (DOC-06, the sentence T204 fixes in spec/04 and spec/05).
    ("specification/spec/07-*", "*", "T204"),
    ("specification/future/future-directions.md", "*", "T204"),
    # F011 review iteration 12: spec/01's cold-start sentence (C26), the sibling of T204's spec/04 site. The
    # row names the key, so spec/01 stays outside the table for every other key.
    ("specification/spec/01-*", "C26-cold01-hrv-input", "T204"),
    ("specification/spec/06-adaptation-logic.md", "*", "T205"),
    ("specification/spec_outline.md", "*", "T205"),
    # T200: the census found C30 in the development plan, spec_outline's spec_* sibling.
    ("specification/spec_development_plan.md", "*", "T205"),
    ("specification/decisions/01-*.md", "*", "T205"),
    ("specification/research/02-*", "*", "T206"),
    ("specification/research/05-*", "*", "T206"),
    (".claude/rules/project-domain-and-spec-fidelity.md", "*", "T207"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "*", "T208"),
    ("spec-mirror/references/F006-dataset-model.md", "*", "T209"),
    ("contracts/openapi.yaml", "*", "T210"),
    ("runcoach-api/src/runcoach_api/schemas.py", "*", "T210"),
    ("runcoach-api/src/runcoach_api/main.py", "*", "T210"),
    # F011 review iteration 12: two C19 comments in src, siblings of T210's; each row names the key.
    ("runcoach-api/src/runcoach_api/models.py", "C19-hrv-04-reduced-confidence", "T210"),
    ("runcoach-api/src/runcoach_api/ingestion/rr_reconstruction.py", "C19-hrv-04-reduced-confidence", "T210"),
    ("runcoach-api/tests/test_hrv_no_regression_gate.py", "C05-gate02-worse-rate-reopens", "T212"),
    # T223 (F012 AC1, D6-D8): the three keys with sites, each row naming its key so the `*` rows above keep
    # their own owners. T224 owns the spec root's sites, T225 the contract's and src's; ``db.py`` and
    # ``spec/02`` hold no hit of these patterns today (T225 and T224 reword them by hand, IDEA-103 item 8).
    ("specification/spec/02-canonical-data-schema-ingestion.md", "HRV-42-R13-reset-in-force-persists-through-it", "T224"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T224"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "HRV-31-R13-broad-withhold-of-any-verdict", "T224"),
    ("specification/spec/03-derived-metric-formulas.md", "HRV-42-R13-reset-in-force-persists-through-it", "T224"),
    ("specification/spec/03-derived-metric-formulas.md", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T224"),
    ("specification/spec/03-derived-metric-formulas.md", "HRV-31-R13-broad-withhold-of-any-verdict", "T224"),
    ("specification/spec/06-adaptation-logic.md", "HRV-42-R13-reset-in-force-persists-through-it", "T224"),
    ("specification/spec/06-adaptation-logic.md", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T224"),
    ("specification/spec/06-adaptation-logic.md", "HRV-31-R13-broad-withhold-of-any-verdict", "T224"),
    ("contracts/openapi.yaml", "HRV-42-R13-reset-in-force-persists-through-it", "T225"),
    ("contracts/openapi.yaml", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T225"),
    ("contracts/openapi.yaml", "HRV-31-R13-broad-withhold-of-any-verdict", "T225"),
    ("runcoach-api/src/runcoach_api/schemas.py", "HRV-42-R13-reset-in-force-persists-through-it", "T225"),
    ("runcoach-api/src/runcoach_api/schemas.py", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T225"),
    ("runcoach-api/src/runcoach_api/schemas.py", "HRV-31-R13-broad-withhold-of-any-verdict", "T225"),
    ("runcoach-api/src/runcoach_api/db.py", "HRV-42-R13-reset-in-force-persists-through-it", "T225"),
    ("runcoach-api/src/runcoach_api/db.py", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T225"),
    ("runcoach-api/src/runcoach_api/db.py", "HRV-31-R13-broad-withhold-of-any-verdict", "T225"),
    ("runcoach-api/src/runcoach_api/main.py", "HRV-42-R13-reset-in-force-persists-through-it", "T225"),
    ("runcoach-api/src/runcoach_api/main.py", "PRIN-12-R13-reproducible-by-hand-without-exceptions", "T225"),
    ("runcoach-api/src/runcoach_api/main.py", "HRV-31-R13-broad-withhold-of-any-verdict", "T225"),
)


def owners_of(path: str, key: str, ownership=OWNERSHIP) -> set[str]:
    """The planned owners of ``(path, key)``: the rows naming ``key`` for a path glob matching
    ``path`` if there are any, otherwise that path's ``*`` rows. ``key`` may itself be ``*``."""
    rows = [(k, owner) for glob, k, owner in ownership if fnmatch.fnmatchcase(path, glob)]
    named = {owner for k, owner in rows if k == key and key != "*"}
    return named or {owner for k, owner in rows if k == "*"}


def gate_failures(hits: list[Hit], pending: dict[str, list[tuple[str, str]]],
                  ownership=OWNERSHIP) -> list[Hit]:
    """The gate's red: each live hit that no pending file covers, or whose path and key have no
    owner in ``ownership`` (a hit outside the table is never pending)."""
    return [hit for hit in hits if hit.live
            and not (pending_tasks(hit, pending) and owners_of(hit.path, hit.key, ownership))]


def stale_pending_rows(hits: list[Hit], pending: dict[str, list[tuple[str, str]]],
                       red_sites=()) -> list[str]:
    """S15: each pending row with nothing red left to cover -- a ``(path, *)`` row needs a live hit or a
    red census site (``red_sites``, ``(path, key)`` pairs, T200) in ``path``, a ``(path, key)`` row one
    of that key in ``path``."""
    red = [(h.path, h.key) for h in hits if h.live] + list(red_sites)
    return [f"{task}.csv: {path},{key}" for task, rows in pending.items() for path, key in rows
            if not any(p == path and key in ("*", k) for p, k in red)]


#: S4 and S11: each ``OLD_MEANINGS`` key whose pattern F011 narrowed, with the pattern it had before. A
#: hit on correct prose is fixed by narrowing the key's pattern, never by an exception (S4), and the
#: census records every site a narrowing stops matching as a ``narrowed`` row (S11). Frozen here, not
#: read from git, so CI needs no history.
#:
#: Each value is the tuple of the key's earlier patterns, oldest first; ``narrowed_extras`` re-runs
#: every one of them, so a site any earlier pattern reached and the current one misses is an extra.
#:
#: - ``C19-hrv-04-reduced-confidence`` (T218, from ``b4479c4``): the bare phrase matched five sites of
#:   correct prose that carry something other than a numeric rMSSD at reduced confidence (spec/03's
#:   altitude-less features and CTL seed, spec/04's lone VO2max model, spec/02 §2.4.3 and
#:   ``rr_reconstruction.py``'s low-valid-fraction series). T218's pattern needed the literal rMSSD in
#:   the same sentence, so "numeric HRV tiers are admitted at reduced confidence" escaped it (IDEA-106
#:   item 9); T243's pattern also takes "tier"/"tiers" in the sentence, which every site of the old
#:   HRV-tier meaning names and none of the five correct-prose sites does.
NARROWED_FROM = MappingProxyType({
    "C19-hrv-04-reduced-confidence": (
        "at reduced confidence",
        "rmssd(?:(?!\\. |;).)*?at reduced confidence|at reduced confidence(?:(?!\\. |;).)*?rmssd",
    ),
})

#: The paths of the sites each narrowing stopped matching, one entry per site, as T218 listed them
#: before and after its narrowing (spec/02:191, spec/03:81 and :155, spec/04:123,
#: ``rr_reconstruction.py``:418). Paths only, so a line moved by another task's edit does not red.
NARROWED_SITE_PATHS = MappingProxyType({
    "C19-hrv-04-reduced-confidence": (
        "runcoach-api/src/runcoach_api/ingestion/rr_reconstruction.py",
        "specification/spec/02-canonical-data-schema-ingestion.md",
        "specification/spec/03-derived-metric-formulas.md",
        "specification/spec/03-derived-metric-formulas.md",
        "specification/spec/04-physiological-state-model.md",
    ),
})

#: S11: the census T200 writes, columns ``key,path,excerpt,source``.
CENSUS_PATH = Path(__file__).parent / "data" / "research00_census.csv"


def narrowed_extras(repo_root: Path = _REPO_ROOT, narrowed_from=NARROWED_FROM, old_meanings=None) -> list[Hit]:
    """The sites each narrowing stopped matching: every hit of any of a key's old patterns over the
    live files of ``repo_root`` that overlaps no hit of its current pattern in the same file (one hit
    per span, however many old patterns reach it). Exceptions play no part (a narrowing is judged on
    the text, sheltered or not)."""
    old_meanings = OLD_MEANINGS if old_meanings is None else old_meanings
    current = {key: old_meanings[key] for key in narrowed_from}
    now = scan(repo_root, old_meanings=current, exceptions=())
    was: list[Hit] = []
    for key, olds in narrowed_from.items():
        for old in olds:
            for h in scan(repo_root, old_meanings={key: replace(old_meanings[key], pattern=old)}, exceptions=()):
                if not any(w.path == h.path and w.start == h.start and w.end == h.end for w in was):
                    was.append(h)
    return [h for h in was if not any(n.path == h.path and n.key == h.key and n.start < h.end and h.start < n.end
                                      for n in now)]


def read_census(path: Path = CENSUS_PATH) -> list[dict[str, str]]:
    """The census rows, as dicts keyed by its header. A missing census raises (T200 writes it)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def narrowed_census_errors(extras: list[Hit], census_rows, narrowed_from=NARROWED_FROM,
                           repo_root: Path = _REPO_ROOT) -> list[str]:
    """S11 for each narrowed key: the census's ``narrowed`` rows for it name exactly the paths of its
    old pattern's extra matches, one row per site, and each row's excerpt overlaps an extra match of
    that key in that file."""
    errors = []
    for key in narrowed_from:
        rows = [r for r in census_rows if r.get("key") == key and r.get("source") == "narrowed"]
        want = sorted(h.path for h in extras if h.key == key)
        got = sorted(Path(r["path"]).as_posix() for r in rows)
        if want != got:
            errors.append(f"{key}: the old pattern's extra matches are in {want}; "
                          f"the census's narrowed rows name {got}")
        for row in rows:
            path = Path(row["path"]).as_posix()
            if not (repo_root / path).is_file():
                errors.append(f"{key}: narrowed row {path}: no such file")
                continue
            text = gate_normal(path, (repo_root / path).read_text(encoding="utf-8"))
            excerpt = normalize(row.get("excerpt") or "")
            spans = [(m.start(), m.end()) for m in re.finditer(re.escape(excerpt), text)] if excerpt else []
            if not any(a < h.end and h.start < b for a, b in spans
                       for h in extras if h.key == key and h.path == path):
                errors.append(f"{key}: narrowed row {path}: excerpt {row.get('excerpt')!r} overlaps none of "
                              f"the old pattern's extra matches there")
    return errors


# --------------------------------------------------------------------------------------------------
# S11 (T200): the census, checked both ways.
# --------------------------------------------------------------------------------------------------

#: ``key,path,excerpt,source``. ``grep``: a pattern hits the site; ``inventory`` / ``loose``: no pattern
#: reaches it, a site to fix; ``narrowed``: correct prose a narrowing stopped matching, a record.
#: Built once by ``support/build_research00_census.py``; the committed CSV is the record.
CENSUS_HEADER = ["key", "path", "excerpt", "source"]
CENSUS_SOURCES = ("grep", "inventory", "loose", "narrowed")
#: The one census row outside S2's roots (S6): the no_regression gate's C05 comment.
CENSUS_TEST_ROW = "runcoach-api/tests/test_hrv_no_regression_gate.py"

#: S11 ("a row cannot be dropped silently"): the rows removed from the census after its commit
#: (``8e6b787``, T200's wave-4 review), as ``(key, path, excerpt, reason)``. S11 names no drop mechanism,
#: so each removal is frozen here, printed by ``test_census_removed_rows_are_recorded_and_absent``, and
#: resolved out through the builder (which reads this literal into ``LOOSE_RESOLUTIONS``).
CENSUS_REMOVED = (
    ("C05-gate02-worse-rate-reopens", "specification/spec/03-derived-metric-formulas.md",
     "The one population this reopens",
     ("'reopens' is T132's widening re-exposing a device-switch population; spec/03 never mentions "
      "hysteresis, and C05's old meaning is a worse rate reopening the deferred hysteresis decision. No "
      "other key fits")),
    ("C19-hrv-04-reduced-confidence", "spec-mirror/features/F006-per-tier-hrv-datasets.md",
     "arbitrates while the confidence weight never does",
     ("correct: fidelity rank arbitrates, the confidence weight never does and no weight is emitted "
      "(HRV-04, HRV-54); HRV-04 defers the weight to Section 6, it does not abolish it")),
    ("C19-hrv-04-reduced-confidence", "spec-mirror/references/F006-dataset-model.md",
     "Fidelity rank arbitrates; the confidence weight never does",
     "correct: as the feature file's §13 note (HRV-04, HRV-54)"),
    # F011 sprint-008 review, iteration 2: a MANUAL_ROWS row (a1eee93) keyed to a wording that is live.
    ("HRV-40-R13-now-sustaining-tier", "runcoach-api/src/runcoach_api/schemas.py",
     "was also not in use in the judged week [date-6, date]",
     ("correct: the report condition as HRV-80 states it (the other tier not in use in the judged week), "
      "which HRV-78's week half measures; it names no now-sustaining tier, and no other key fits")),
)


def _occurrences(text: str, excerpt: str) -> list[tuple[int, int]]:
    needle = normalize(excerpt)
    return [(m.start(), m.end()) for m in re.finditer(re.escape(needle), text)] if needle else []


def census_state(row, repo_root: Path = _REPO_ROOT, exceptions=None) -> str:
    """Where a census row stands now: ``missing`` (no such file), ``gone`` (its excerpt no longer
    occurs), ``sheltered`` (every occurrence lies wholly inside an ``EXCEPTIONS`` excerpt in that file,
    S4; IDEA-106 item 8), or ``present`` -- the site still states what it did."""
    exceptions = _OM.EXCEPTIONS if exceptions is None else exceptions
    path = Path(row["path"]).as_posix()
    if not (repo_root / path).is_file():
        return "missing"
    text = gate_normal(path, (repo_root / path).read_text(encoding="utf-8"))
    found = _occurrences(text, row.get("excerpt") or "")
    if not found:
        return "gone"
    sheltering = _excerpt_ranges(text, path, exceptions)
    if all(any(a <= s and e <= b for _i, a, b in sheltering) for s, e in found):
        return "sheltered"
    return "present"


def census_coverage(hits: list[Hit], rows, repo_root: Path = _REPO_ROOT):
    """S11's count, ``(a, b, c, uncovered)``: ``a`` the ``grep`` rows, ``b`` the unquoted hits (live or
    sheltered: every site a pattern reaches), ``c`` the ``grep`` rows whose excerpt is gone (or whose
    file is), and the hits no ``grep`` row covers -- same path and key, an excerpt occurrence
    overlapping the hit. ``a == b + c`` holds before the work (``c == 0``), during it and after it."""
    grep_rows = [r for r in rows if r.get("source") == "grep"]
    unquoted = [h for h in hits if not h.quoted]
    texts: dict[str, str] = {}

    def text_of(path: str) -> str | None:
        if path not in texts:
            full = repo_root / path
            texts[path] = gate_normal(path, full.read_text(encoding="utf-8")) if full.is_file() else None
        return texts[path]

    gone = 0
    spans: list[tuple[str, str, int, int]] = []
    for row in grep_rows:
        path = Path(row["path"]).as_posix()
        text = text_of(path)
        found = _occurrences(text, row.get("excerpt") or "") if text is not None else []
        if not found:
            gone += 1
        spans += [(path, row["key"], s, e) for s, e in found]
    uncovered = [h for h in unquoted
                 if not any(p == h.path and k == h.key and s < h.end and h.start < e for p, k, s, e in spans)]
    return len(grep_rows), len(unquoted), gone, uncovered


def census_balance_errors(a: int, b: int, c: int, uncovered: list[Hit]) -> list[str]:
    """S11's accounting over ``census_coverage``'s count: every hit covered, and ``a == b + c`` exactly --
    a surplus ``grep`` row (two rows over one hit) is as wrong as a missing one."""
    errors = [f"uncovered: {h.path}:{h.line} {h.key} {h.matched!r}" for h in uncovered]
    if a != b + c:
        errors.append(f"grep-sourced rows {a} != hits {b} + cleared {c}")
    return errors


@dataclass(frozen=True)
class CensusSite:
    """One census row as a test parameter; ``n`` counts the rows of its path and key from 1."""
    key: str
    path: str
    excerpt: str
    source: str
    n: int

    @property
    def ident(self) -> str:
        return site_id(self.path, self.key, self.n)

    def row(self) -> dict[str, str]:
        return {"key": self.key, "path": self.path, "excerpt": self.excerpt, "source": self.source}


def census_sites(rows, narrowed: bool) -> list[CensusSite]:
    """The census's ``narrowed`` rows, or every other row, in file order."""
    counts: dict[tuple[str, str], int] = {}
    sites = []
    for row in rows:
        if (row.get("source") == "narrowed") != narrowed:
            continue
        path = Path(row["path"]).as_posix()
        counts[(path, row["key"])] = counts.get((path, row["key"]), 0) + 1
        sites.append(CensusSite(row["key"], path, row["excerpt"], row["source"], counts[(path, row["key"])]))
    return sites


def census_pending_tasks(site: CensusSite, pending) -> list[str]:
    """The task ids whose pending file covers the site's ``(path, key)`` or ``(path, *)`` (S15)."""
    return sorted(task for task, rows in pending.items()
                  if (site.path, site.key) in rows or (site.path, "*") in rows)


def red_census_sites(rows, repo_root: Path = _REPO_ROOT) -> list[tuple[str, str]]:
    """``(path, key)`` of every non-``narrowed`` row whose site still states what it did."""
    return [(s.path, s.key) for s in census_sites(rows, narrowed=False)
            if census_state(s.row(), repo_root) == "present"]


def narrowed_row_errors(site: CensusSite, hits: list[Hit], repo_root: Path = _REPO_ROOT):
    """S4 and S11 for one ``narrowed`` row, ``(errors, found, matched)``: its file exists, its excerpt
    occurs there exactly once, and no hit of the row's own key overlaps it (``matched``). Another key's
    hit over the same text is that key's business, not the narrowing's."""
    path = repo_root / site.path
    if not path.is_file():
        return [f"{site.path}: the narrowed row's file is gone"], [], []
    found = _occurrences(gate_normal(site.path, path.read_text(encoding="utf-8")), site.excerpt)
    matched = [h for h in hits if h.path == site.path and h.key == site.key
               and any(h.start < e and s < h.end for s, e in found)]
    errors = []
    if len(found) != 1:
        errors.append(f"the correct prose {site.excerpt!r} is no longer in {site.path} exactly once")
    if matched:
        errors.append(f"the current {site.key} pattern still matches {site.excerpt!r} in {site.path}: "
                      f"{[(h.line, h.matched) for h in matched]}")
    return errors, found, matched


def census_row_errors(rows, header, repo_root: Path = _REPO_ROOT) -> list[str]:
    """The census's shape: the header; a source, an ``OLD_MEANINGS`` key and a one-line, non-empty
    excerpt per row; paths inside S2's roots (or the one test-comment row); no duplicate row; each
    excerpt still present occurs exactly once in its file; ``narrowed`` rows only for a narrowed key."""
    errors = [] if header == CENSUS_HEADER else [f"header {header} is not {CENSUS_HEADER}"]
    live = {p for paths in live_files(repo_root).values() for p in paths}
    seen = set()
    for i, row in enumerate(rows, start=2):
        path = Path(row.get("path") or "").as_posix()
        where = f"row {i} ({path}, {row.get('key')})"
        excerpt = row.get("excerpt") or ""
        if row.get("source") not in CENSUS_SOURCES:
            errors.append(f"{where}: source {row.get('source')!r}")
        if row.get("key") not in OLD_MEANINGS:
            errors.append(f"{where}: not an OLD_MEANINGS key")
        if row.get("source") == "narrowed" and row.get("key") not in NARROWED_FROM:
            errors.append(f"{where}: a narrowed row for a key no narrowing touched")
        if not normalize(excerpt) or "\n" in excerpt or "\r" in excerpt:
            errors.append(f"{where}: excerpt empty or not one line")
        if path not in live and path != CENSUS_TEST_ROW:
            errors.append(f"{where}: outside S2's roots")
        ident = (row.get("key"), path, normalize(excerpt))
        if ident in seen:
            errors.append(f"{where}: duplicate row")
        seen.add(ident)
        if (repo_root / path).is_file() and normalize(excerpt):
            count = len(_occurrences(gate_normal(path, (repo_root / path).read_text(encoding="utf-8")), excerpt))
            if count > 1:
                errors.append(f"{where}: excerpt occurs {count} times, so it does not name one site")
    return errors


def _read_census_or_empty() -> list[dict[str, str]]:
    """The census for collection: no file (before T200) collects no row, and the census tests red."""
    return read_census() if CENSUS_PATH.exists() else []


_REAL_SCAN: list[Hit] = []


def real_scan() -> list[Hit]:
    """The scan of the checkout, once per session: collection and several tests read it."""
    if not _REAL_SCAN:
        _REAL_SCAN.extend(scan(_REPO_ROOT))
    return _REAL_SCAN


# ==================================================================================================
# Tests.
# ==================================================================================================


def test_scanner_counts_every_root_at_or_above_its_floor():
    files = live_files(_REPO_ROOT)
    total = sum(len(paths) for paths in files.values())
    print(f"scanned {total} files")
    floor_of = dict(ROOT_FLOORS)
    for label, paths in files.items():
        print(f"  root {label}: {len(paths)} live (floor {floor_of.get(label)})")
    print(f"[slice compared] every root's live count against ROOT_FLOORS, {len(files)} roots")
    assert [label for label, *_ in ROOTS] == [label for label, _floor in ROOT_FLOORS]
    assert not floor_shortfalls(files), floor_shortfalls(files)


def _tree(tmp_path: Path, rel_paths) -> Path:
    for rel in rel_paths:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    return tmp_path


def test_scanner_walk_keeps_scanned_suffixes_and_drops_records_and_other_files(tmp_path):
    live = [
        "specification/spec/03-derived.md",
        "specification/spec/sub/deep.yml",
        "specification/decisions/01-coach.md",
        "specification/spec_outline.md",
        "specification/future/future-directions.md",
        "specification/research/01-physiology.md",
        "specification/research/06-landscape.md",
        "contracts/openapi.yaml",
        "contracts/check_drift.py",
        "runcoach-api/src/runcoach_api/main.py",
        ".claude/rules/learnings/a-rule.md",
        "spec-mirror/features/F006-per-tier-hrv-datasets.md",
    ]
    dropped = [
        "specification/research/00-design-decisions.md",
        "specification/research/00-history.md",
        "specification/research/07-future.md",
        "specification/research/drafts/01-draft.md",
        "specification/other.md",
        "specification/review/spec-review-findings.md",
        "specification/spec/notes.txt",
        "contracts/__pycache__/check_drift.cpython-312.pyc",
        "runcoach-api/src/runcoach_api/README.md",
        "runcoach-api/src/runcoach_api/config.yaml",
        "runcoach-api/tests/test_x.py",
        *_REPO_RECORD_FILES.values(),
    ]
    files = live_files(_tree(tmp_path, live + dropped))
    walked = sorted(p for paths in files.values() for p in paths)
    print(f"[slice compared] {len(walked)} walked of {len(live) + len(dropped)} planted: {walked}")
    assert walked == sorted(live)
    assert files["research"] == ("specification/research/01-physiology.md",
                                 "specification/research/06-landscape.md")


#: S1's by-path list and F009 AC2's list, copied from the F011 decisions reference and the F009 feature
#: file (2026-09-28, spec review wave 1; sprint-009 D13's two modules added 2026-09-30 by T232), not from
#: ``research00_records.py``. Each spec pattern is written
#: in the module's prefix form (the row matches ``prefix + "*"``): ``F005-*`` is ``F005-``, ``sprints/
#: sprint-*`` except ``current`` is ``sprints/sprint-``, and ``verify/*-verdict-cycle*.md`` is
#: ``verify/*-verdict-cycle``.
_SPEC_RECORD_ROWS = frozenset({
    # S1, by path (repo); also F009 AC2's research/00 files, F005-* (spec-mirror) and F006 copies.
    ("repo", "specification/research/00-history.md"),
    ("repo", "specification/research/00-traceability.md"),
    ("repo", "specification/research/00-meaning-review.md"),
    ("repo", "spec-mirror/features/F005-"),
    ("repo", "spec-mirror/references/F005-"),
    ("repo", "spec-mirror/references/F006-research-draft-archived-2026-09-23.md"),
    ("repo", "spec-mirror/references/F006-no-regression-report.md"),
    ("repo", "spec-mirror/references/F006-sweep-findings.md"),
    # F009 AC2, repo files outside F011's roots.
    ("repo", "runcoach-api/tests/support/research00_old_meanings.py"),
    ("repo", "runcoach-api/tests/test_research00_traceability.py"),
    # F009 AC2 under sprint-009 D13: the two literal-bearing support modules, outside F011's roots.
    ("repo", "runcoach-api/tests/support/build_research00_census.py"),
    ("repo", "runcoach-api/tests/support/withdrawn_phrasings.py"),
    # F009 AC2, the data dir: F005-*, F006's three, F004's archived draft (T235), the inventory, F008's decisions, sprints/sprint-* except current, verify/*-verdict-cycle*.md.
    ("data", "spec/references/F004-research-draft-archived-2026-09-06.md"),
    ("data", "spec/features/F005-"),
    ("data", "spec/references/F005-"),
    ("data", "spec/references/F006-research-draft-archived-2026-09-23.md"),
    ("data", "spec/references/F006-no-regression-report.md"),
    ("data", "spec/references/F006-sweep-findings.md"),
    ("data", "spec/references/research00-rewrite-inventory.md"),
    ("data", "spec/references/F008-rewrite-decisions.md"),
    ("data", "sprints/sprint-"),
    ("data", "verify/*-verdict-cycle"),
})

#: One planted file per repo-space row of ``_SPEC_RECORD_ROWS``, keyed by the row's prefix.
_REPO_RECORD_FILES = {
    "specification/research/00-history.md": "specification/research/00-history.md",
    "specification/research/00-traceability.md": "specification/research/00-traceability.md",
    "specification/research/00-meaning-review.md": "specification/research/00-meaning-review.md",
    "spec-mirror/features/F005-": "spec-mirror/features/F005-resting-hrv-trend.md",
    "spec-mirror/references/F005-": "spec-mirror/references/F005-decision-log.md",
    "spec-mirror/references/F006-research-draft-archived-2026-09-23.md":
        "spec-mirror/references/F006-research-draft-archived-2026-09-23.md",
    "spec-mirror/references/F006-no-regression-report.md": "spec-mirror/references/F006-no-regression-report.md",
    "spec-mirror/references/F006-sweep-findings.md": "spec-mirror/references/F006-sweep-findings.md",
    "runcoach-api/tests/support/research00_old_meanings.py": "runcoach-api/tests/support/research00_old_meanings.py",
    "runcoach-api/tests/test_research00_traceability.py": "runcoach-api/tests/test_research00_traceability.py",
    "runcoach-api/tests/support/build_research00_census.py": "runcoach-api/tests/support/build_research00_census.py",
    "runcoach-api/tests/support/withdrawn_phrasings.py": "runcoach-api/tests/support/withdrawn_phrasings.py",
}


def test_scanner_record_set_rows_are_exactly_s1_and_f009_ac2():
    rows = [(space, prefix) for space, prefix, _reason in RECORD_SET]
    missing = sorted(_SPEC_RECORD_ROWS - set(rows))
    extra = sorted(set(rows) - _SPEC_RECORD_ROWS)
    print(f"[slice compared] {len(rows)} RECORD_SET rows against {len(_SPEC_RECORD_ROWS)} spec rows; "
          f"missing {missing}; extra {extra}")
    assert len(rows) == len(set(rows)), "a RECORD_SET row is duplicated"
    assert missing == [] and extra == []
    assert sorted(_REPO_RECORD_FILES) == sorted(p for s, p in _SPEC_RECORD_ROWS if s == "repo")


def test_scanner_walk_drops_each_repo_record_by_its_own_row(tmp_path, monkeypatch):
    """Every repo-space record, planted, is dropped from the walk. Walked again with only its own row
    removed, it comes back exactly when it lies under an F011 root (S1's five spec-mirror rows); the
    research/00 files and the two test files lie outside every root (S2), so their rows guard F009 only."""
    root = _tree(tmp_path, list(_REPO_RECORD_FILES.values()))
    walked = sorted(p for paths in live_files(root).values() for p in paths)
    returned = {}
    for prefix, planted in _REPO_RECORD_FILES.items():
        monkeypatch.setattr(_REC, "RECORD_SET", tuple(r for r in RECORD_SET if (r[0], r[1]) != ("repo", prefix)))
        returned[planted] = sorted(p for paths in live_files(root).values() for p in paths)
        monkeypatch.setattr(_REC, "RECORD_SET", RECORD_SET)
    print(f"[slice compared] {len(_REPO_RECORD_FILES)} planted; walked with every row {walked}; "
          f"walked without each file's own row {returned}")
    assert walked == []
    in_roots = sorted(planted for planted, again in returned.items() if again)
    assert all(again in ([], [planted]) for planted, again in returned.items())
    assert in_roots == sorted(planted for planted in _REPO_RECORD_FILES.values() if planted.startswith("spec-mirror/"))


def test_scanner_floor_fails_for_a_root_below_it_and_for_a_missing_root(tmp_path):
    files = live_files(_tree(tmp_path, ["specification/spec/a.md", "specification/spec/b.md"]))
    floors = tuple((label, 1) for label, *_ in ROOTS)
    problems = floor_shortfalls(files, floors)
    print(f"[slice compared] shortfalls on a tree with only spec/ populated: {problems}")
    assert not any(p.startswith("spec:") for p in problems)
    assert sorted(p.split(":")[0] for p in problems) == sorted(
        label for label, *_ in ROOTS if label != "spec")
    assert floor_shortfalls({"spec": ("a",)}, (("spec", 1),)) == []
    assert floor_shortfalls({"spec": ("a",)}, (("spec", 2),)) == ["spec: 1 live, floor 2"]
    assert floor_shortfalls({"new": ()}, ()) == ["new: no floor"]


def test_scanner_record_set_rows_have_the_s1_shape():
    spaces = sorted({space for space, _prefix, _reason in RECORD_SET})
    print(f"[slice compared] {len(RECORD_SET)} RECORD_SET rows, spaces {spaces}")
    assert spaces == ["data", "repo"]
    assert all(isinstance(p, str) and p and isinstance(r, str) and r for _s, p, r in RECORD_SET)
    assert SECTION_RECORD_FILES == frozenset({"spec-mirror/features/F006-per-tier-hrv-datasets.md"})
    assert is_record("repo", "specification/research/00-history.md")
    assert is_record("repo", "spec-mirror/references/F005-trend-construction.md")
    assert not is_record("repo", "spec-mirror/references/F006-dataset-model.md")
    assert not is_record("data", "specification/research/00-history.md")
    assert is_record("data", "verify/F005-verdict-cycle3.md")
    assert not is_record("data", "verify/F005-verdict.md")
    assert is_record("data", "sprints/sprint-007/PROGRESS.md")
    assert not is_record("data", "sprints/current/PROGRESS.md")


def test_scanner_section_records_cover_only_the_exact_heading_in_a_frozen_file():
    record = "spec-mirror/features/F006-per-tier-hrv-datasets.md"
    raw = ("# F006\r\n\r\nlive text\r\n\r\n## Decision Log\r\n\r\n- old meaning\r\n"
           "### sub\r\nstill log\r\n## Next\r\nlive again\r\n")
    ranges = record_ranges(record, raw)
    covered = "".join(raw[a:b] for a, b in ranges)
    print(f"[slice compared] record ranges {ranges}: {covered!r}")
    assert covered == "## Decision Log\r\n\r\n- old meaning\r\n### sub\r\nstill log\r\n"
    assert record_ranges("spec-mirror/references/F006-dataset-model.md", raw) == []
    assert record_ranges(record, raw.replace("## Decision Log", "## Decision Log (old)")) == []
    assert record_ranges(record, raw.replace("## Decision Log", "### Decision Log")) == []
    trailing = "## Decision Log   \nto the end"
    assert record_ranges(record, trailing) == [(0, len(trailing))]


def test_scanner_changelog_released_entries_are_records_and_unreleased_is_swept():
    raw = "# Changelog\n\n## [Unreleased]\nnew live\n\n## [1.2.0] - 2026-09-01\nold\n## [1.1.0]\nolder\n"
    ranges = record_ranges("CHANGELOG.md", raw)
    covered = "".join(raw[a:b] for a, b in ranges)
    print(f"[slice compared] CHANGELOG record ranges {ranges}: {covered!r}")
    assert covered == "## [1.2.0] - 2026-09-01\nold\n## [1.1.0]\nolder\n"
    assert "new live" not in covered
    assert record_ranges("specification/spec/notes.md", raw) == []


def test_scanner_changelog_dated_sprint_heading_is_a_released_entry():
    """Sprint-009 D11: this repo's CHANGELOG dates its releases (``## 2026-09-28 through 2026-09-30 —
    Sprint 008: ...``; sprints 001-004 carry one date) instead of ``## [x.y.z]``, so those entries are
    released records too. Only an ``## Unreleased`` section is live. A heading that is dated but names no
    sprint, or a sprint heading at level 3, opens no record."""
    raw = ("# Changelog\n\n## Unreleased\nnew live §1.7\n\n"
           "## 2026-09-28 through 2026-09-30 — Sprint 008: research/00 downstream sweep\nold §1.7\n"
           "### detail\nstill old\n"
           "## 2026-09-07 — Sprint 004: Resting-HRV Capture, cycle 2\nolder\n"
           "## 2026-09-06 notes without a sprint\nlive note\n"
           "### 2026-09-05 — Sprint 002: level three\nlive too\n")
    ranges = record_ranges("CHANGELOG.md", raw)
    covered = "".join(raw[a:b] for a, b in ranges)
    print(f"[slice compared] dated CHANGELOG record ranges {ranges}: {ascii(covered)}")
    assert covered == ("## 2026-09-28 through 2026-09-30 — Sprint 008: research/00 downstream sweep\nold §1.7\n"
                       "### detail\nstill old\n"
                       "## 2026-09-07 — Sprint 004: Resting-HRV Capture, cycle 2\nolder\n")
    assert "new live" not in covered and "live note" not in covered and "live too" not in covered
    real = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    real_ranges = record_ranges("CHANGELOG.md", real)
    first_record = real[real_ranges[0][0]:].splitlines()[0] if real_ranges else None
    print(f"[slice compared] the checkout's CHANGELOG: {len(real_ranges)} record ranges, first {first_record!r}")
    assert len(real_ranges) >= 8 and real_ranges[-1][1] == len(real)
    assert real.index("## Unreleased") < real_ranges[0][0]


def test_scanner_lists_and_sweeps_every_other_file_with_a_decision_log():
    files = live_files(_REPO_ROOT)
    carriers = {}
    for path in (p for paths in files.values() for p in paths):
        lines = carries_decision_log((_REPO_ROOT / path).read_text(encoding="utf-8"))
        if lines:
            carriers[path] = lines
    others = {p: n for p, n in carriers.items() if p not in SECTION_RECORD_FILES}
    for path, lines in sorted(others.items()):
        print(f"  swept Decision Log: {path}:{','.join(map(str, lines))}")
    print(f"[slice compared] {len(carriers)} live files carry the heading; "
          f"{len(others)} outside SECTION_RECORD_FILES")
    assert "spec-mirror/references/F006-dataset-model.md" in others
    for path in others:
        assert record_ranges(path, (_REPO_ROOT / path).read_text(encoding="utf-8")) == []
    for path in SECTION_RECORD_FILES:
        assert path in carriers, f"{path} is a section-record file but is not live or has no heading"
        assert record_ranges(path, (_REPO_ROOT / path).read_text(encoding="utf-8"))


def _spans(raw: str) -> list[str]:
    return [raw[a:b] for a, b in quote_spans(raw)]


def test_scanner_quote_spans_oracle():
    cases = [
        ('a "straight quote" b', ['"straight quote"']),
        ("a “curly quote” b", ["“curly quote”"]),
        ("a `code span` b and ``two `ticks` here`` c", ["`code span`", "``two `ticks` here``"]),
        ('a "broken\n\nby a blank" b', []),
        ('a "across\none newline" b', ['"across\none newline"']),
        ('> a blockquote line\n', []),
        ("text\n```\ncode \"x\"\n\nmore\n```\nafter", ["```\ncode \"x\"\n\nmore\n```\n"]),
        # An unclosed fence is no block (sprint-008 F011 review): its lines are prose.
        ("text\n~~~py\nunclosed", []),
        # CommonMark 4.5: a backtick fence's info string holds no backtick, so this line is a code span.
        ("```x``` is the literal.\n\nprose", ["```x```"]),
        ('an "unclosed quote', []),
        # An odd count of straight quotes in a paragraph pairs none of them (sprint-008 F011 review).
        ('a " stray and a "pair" here', []),
        ('a " stray, a `"` in code and a "pair"', ['`"`']),
        ('odd " here\n\nand "even" here', ['"even"']),
        # Sprint-008 F011 review, iteration 2. A curly run that is not strictly “ ” “ ” pairs none.
        ("A stray “ opens. Then “a pair”.", []),
        ("a ” stray closer\n\nthen “one” and “two”", ["“one”", "“two”"]),
        # A straight quote opens only left-flanking and closes only right-flanking; ambiguous pairs none.
        ('The 5" strap and a 3" band', []),
        ('x"y and z"w', []),
        ('a ("paren") and "`code`" b', ['"paren"', '"`code`"']),
        # Punctuation on both sides still lets a quote close (or open), as CommonMark reads "_".
        ('asks ("why?", "how?") and ["**/*"]', ['"why?"', '"how?"', '"**/*"']),
        # A ">"-only line is a blockquote's blank line: no span crosses it.
        ('> He wrote "a\n>\n> b" ends.', []),
        # A fence opened in a list item ends with the item and never pairs with a later top-level fence.
        # Iteration 3: the item's end closes no fence, so this one is unclosed and its lines are prose.
        ("- item\n  ```\n  code\n\nprose\n\n```\nmore\n```\n", ["```\nmore\n```\n"]),
        # Sprint-008 F011 review, iteration 3: fenced blocks are CommonMark's (markdown-it), each input
        # an item's reproducer with "X" for the example.
        ("- item\n  ```\n  code\n>\n  Live X.\n  ```\n", []),
        ("- item\nlazy\n  ```\n  code\n\nLive X.\n\n```\nmore\n```\n", ["```\nmore\n```\n"]),
        ("- a\n  ```\n  code\n     ```\n  Live X.\n\nNext para.\n", ["  ```\n  code\n     ```\n"]),
        ("> ```\n> code\n> ````\n> Live X.\n> ```\n", ["> ```\n> code\n> ````\n"]),
        ("- - -\n  ```\ncode\n```\nLive X.\n```\nmore\n```\n", ["  ```\ncode\n```\n", "```\nmore\n```\n"]),
        ("- ```\n  code\n  ```\n  Live X.\n  ```\n  more\n  ```\n",
         ["- ```\n  code\n  ```\n", "  ```\n  more\n  ```\n"]),
        ("1. ```\n   code\n   ```\n   Live X.\n   ```\n   more\n   ```\n",
         ["1. ```\n   code\n   ```\n", "   ```\n   more\n   ```\n"]),
        # CRLF, and mixed CR, LF and CRLF endings, map a fence's lines back to the raw text.
        ("a\r\n```\r\ncode\r\n```\r\nb\r\n", ["```\r\ncode\r\n```\r\n"]),
        ("a\n```\r\ncode\r```\nb", ["```\r\ncode\r```\n"]),
        # A quote's closer inside a code span closes nothing; the search steps over the span.
        ('"x `"` y" Live X. `z`.\n', ['"x `"` y"', "`z`"]),
        ("“x `”` y” Live X. `z`.\n", ["“x `”` y”", "`z`"]),
        # A curly quote pairs only a left-flanking opener with a right-flanking closer, as a straight one.
        ("A stray “ here. Live X. A 5” strap.\n", []),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


def test_scanner_hit_half_inside_a_span_is_not_sheltered():
    raw = 'He said "old rule" here, and old rule again. Half: "old" rule.'
    text, _offsets = normalize_with_offsets(raw)
    starts = [m.start() for m in re.finditer("old rule", text)]
    verdicts = [hit_is_quoted(raw, s, s + len("old rule")) for s in starts]
    print(f"[slice compared] {text!r}: hits at {starts} quoted {verdicts}")
    assert verdicts == [True, False, False]
    crlf = '> "quoted in a blockquote"\r\n> old rule outside'
    text, _offsets = normalize_with_offsets(crlf)
    starts = [m.start() for m in re.finditer("old rule", text)]
    assert [hit_is_quoted(crlf, s, s + 8) for s in starts] == [False]


def test_scanner_offset_map_reproduces_normalize_on_every_live_file():
    files = live_files(_REPO_ROOT)
    checked = 0
    for path in (p for paths in files.values() for p in paths):
        raw = (_REPO_ROOT / path).read_text(encoding="utf-8")
        text, offsets = gate_text(path, raw)
        assert len(offsets) == len(text)
        assert offsets == sorted(offsets)
        checked += 1
    print(f"[slice compared] offset map equals normalize() on {checked} live files")
    assert checked == sum(len(paths) for paths in files.values())


def test_scanner_site_ids_use_only_word_characters():
    ident = site_id(r"spec-mirror\references\F006-dataset-model.md", "T07-acwr-band", 3)
    print(f"[slice compared] {ident}")
    assert ident == "spec_mirror_references_F006_dataset_model_md__T07_acwr_band__3"
    assert re.fullmatch(r"[A-Za-z0-9_]+", site_id(".claude/rules/x y.md", "*", 0))


# --------------------------------------------------------------------------------------------------
# AC1 (T199): the hit test on the checkout.
# --------------------------------------------------------------------------------------------------


def _hit_params(hits=None, pending=None):
    """One param per live hit of the checkout, with its S15 id. A hit covered by a pending file and
    owned in ``OWNERSHIP`` is a strict xfail naming the covering task(s); any other hit is red. With no
    live hit at all (the end state), a single ``no_live_hits`` param re-checks that, so the release
    gate never sees an empty-parameter skip. ``hits`` and ``pending`` default to the checkout's."""
    pending = read_pending() if pending is None else pending
    hits = real_scan() if hits is None else hits
    params = []
    for hit in (h for h in hits if h.live):
        tasks = pending_tasks(hit, pending)
        marks = ()
        if tasks and owners_of(hit.path, hit.key):
            verb = "fixes" if len(tasks) == 1 else "fix"
            marks = pytest.mark.xfail(strict=True, reason=f"{', '.join(tasks)} {verb} these sites")
        params.append(pytest.param(hit, id=hit.ident, marks=marks))
    return params or [pytest.param(None, id="no_live_hits")]


@pytest.mark.parametrize("hit", _hit_params())
def test_live_hit_states_an_old_meaning(hit):
    """F011 AC1: a live file states an old meaning -- unquoted (S3) and sheltered by no exception (S4).
    Each such hit is red; the ones a pending file covers are expected red until their task lands."""
    if hit is None:
        assert [h.ident for h in real_scan() if h.live] == []
        return
    owners = sorted(owners_of(hit.path, hit.key))
    pytest.fail(f"{hit.path}:{hit.line} states old meaning {hit.key} ({hit.matched!r}); "
                f"planned owner {owners or 'none: the path and key are outside OWNERSHIP'}")


def test_every_live_hit_is_pending_under_an_owner():
    hits = real_scan()
    live = [h for h in hits if h.live]
    pending = read_pending()
    per_key: dict[str, int] = {}
    per_path: dict[str, int] = {}
    for hit in live:
        per_key[hit.key] = per_key.get(hit.key, 0) + 1
        per_path[hit.path] = per_path.get(hit.path, 0) + 1
    for key, count in sorted(per_key.items()):
        print(f"  key {key}: {count}")
    for path, count in sorted(per_path.items()):
        owners = sorted({o for h in live if h.path == path for o in owners_of(path, h.key)})
        print(f"  path {path}: {count} (owners {owners})")
    failures = gate_failures(hits, pending)
    print(f"[slice compared] {len(hits)} hits: {len(live)} live, "
          f"{sum(h.quoted for h in hits)} quoted, {sum(bool(h.sheltered_by) for h in hits)} sheltered; "
          f"{len(pending)} pending files; unpended {[h.ident for h in failures]}")
    assert failures == []


def test_every_pending_row_is_still_needed():
    """S15: a pending row whose sites are all fixed must go with its file. Each ``(path, key)`` (or
    ``(path, *)``) row needs a live hit, a census site (T200) or a presence row (T201) that is still red."""
    live = [h for h in real_scan() if h.live]
    census = _read_census_or_empty()
    red = red_census_sites(census) + red_presence_sites(census)
    stale = stale_pending_rows(live, read_pending(), red)
    print(f"[slice compared] {sum(len(r) for r in read_pending().values())} pending rows against "
          f"{len(live)} live hits and {len(red)} red census sites; stale {stale}")
    assert stale == []


def test_pending_files_match_ownership():
    """Every row of ``<id>.csv`` is owned by ``<id>`` in ``OWNERSHIP``, and names ``*`` or an
    ``OLD_MEANINGS`` key. An absent file is fine: that is the end state."""
    pending = read_pending()
    errors = []
    for task, rows in pending.items():
        for path, key in rows:
            if key != "*" and key not in OLD_MEANINGS:
                errors.append(f"{task}.csv: {path},{key}: not * and not an OLD_MEANINGS key")
            owners = owners_of(path, key)
            if task not in owners:
                errors.append(f"{task}.csv: {path},{key}: owned by {sorted(owners) or 'nobody'}")
    print(f"[slice compared] {sum(len(r) for r in pending.values())} rows in {sorted(pending)}; errors {errors}")
    assert errors == []


def test_ownership_expands_the_c19_and_hrv01_families_to_their_exact_keys():
    """T199's table names two families; ``OWNERSHIP`` must list every member by name for each family
    row (spec/03 for T213, spec/02 for T203), and every named key must exist."""
    families = {prefix: sorted(k for k in OLD_MEANINGS if k.startswith(prefix)) for prefix in ("C19", "HRV-01")}
    listed = {(path, owner): sorted(k for p, k, o in OWNERSHIP if (p, o) == (path, owner))
              for path, owner in (("specification/spec/03-derived-metric-formulas.md", "T213"),
                                  ("specification/spec/02-canonical-data-schema-ingestion.md", "T203"))}
    print(f"[slice compared] families {families}; listed {listed}")
    assert listed[("specification/spec/03-derived-metric-formulas.md", "T213")] == sorted(
        families["C19"] + families["HRV-01"])
    assert listed[("specification/spec/02-canonical-data-schema-ingestion.md", "T203")] == sorted(
        families["C19"] + families["HRV-01"] + ["PRIN-10-C19-reduced-confidence"])
    assert [k for _p, k, _o in OWNERSHIP if k != "*" and k not in OLD_MEANINGS] == []


def test_every_exception_shelters_a_hit():
    """S4: each ``EXCEPTIONS`` triple shelters at least one hit in its own file. Vacuous while
    ``EXCEPTIONS == ()``; T218 adds the F009 triples, F010 its C33 ones."""
    exceptions = _OM.EXCEPTIONS
    errors = exception_shelter_errors(real_scan(), exceptions)
    print(f"[slice compared] {len(exceptions)} exceptions; shelter nothing: {errors}")
    assert errors == []


def test_key_roots_hold_the_four_t07_keys_to_the_rule_files(tmp_path):
    t07 = sorted(k for k in OLD_MEANINGS if k.startswith("T07-"))
    print(f"[slice compared] KEY_ROOTS {dict(KEY_ROOTS)} against the T07 keys {t07}")
    assert sorted(KEY_ROOTS) == t07 == ["T07-acwr-band", "T07-ctl-rise-band", "T07-tolerance-band",
                                        "T07-tsb-target-form-band"]
    assert all(roots == (".claude/rules/",) for roots in KEY_ROOTS.values())
    for rel in ("specification/spec/99-planted.md", ".claude/rules/planted.md"):
        _plant(tmp_path, rel, "".join(f"{OLD_MEANINGS[k].example}. " for k in t07))
    reached = sorted((h.path, h.key) for h in scan(tmp_path))
    assert reached == [(".claude/rules/planted.md", k) for k in t07]


# --------------------------------------------------------------------------------------------------
# AC1's "proving it" demonstrations, each in a tmp_path world (the census one is T200's).
# --------------------------------------------------------------------------------------------------

_PLANT_BODY = {
    "md": "Planted prose before. {example}. Planted prose after.\n",
    "py": '"""Planted module."""\n\n# {example}\nVALUE = 1\n',
    "yaml": "planted:\n  description: >\n    {example}\n",
}


def _plant(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _plant_path(key: str, suffix: str) -> str:
    """A live site for ``key``: in a rule file for a ``KEY_ROOTS`` key, elsewhere in a root otherwise."""
    if key in KEY_ROOTS:
        return f".claude/rules/planted.{suffix}"
    return {"md": "specification/spec/99-planted.md", "py": "runcoach-api/src/runcoach_api/planted.py",
            "yaml": "contracts/planted.yaml"}[suffix]


@pytest.mark.parametrize(("key", "suffix"), [
    pytest.param(key, suffix, id=f"{key}-{suffix}") for key in OLD_MEANINGS for suffix in _PLANT_BODY
])
def test_reinserting_an_example_at_a_live_site_turns_the_gate_red(tmp_path, key, suffix):
    rel = _plant_path(key, suffix)
    _plant(tmp_path, rel, _PLANT_BODY[suffix].format(example="neutral text"))
    assert gate_failures(scan(tmp_path), {}) == []
    _plant(tmp_path, rel, _PLANT_BODY[suffix].format(example=OLD_MEANINGS[key].example))
    red = gate_failures(scan(tmp_path), {})
    print(f"[slice compared] {rel} holding {key}'s example: red {[(h.path, h.key, h.line) for h in red]}")
    assert (rel, key) in [(h.path, h.key) for h in red]
    assert all(h.path == rel for h in red)


def test_scan_skips_quotation_in_markdown_only_and_section_records(tmp_path):
    """S3 and S1 through ``scan``: on the checkout both a quoted hit and a record hit fall in files a
    ``*`` pending row covers, so only this world shows the skips are applied at all."""
    key = "C05-gate02-worse-rate-reopens"
    example = OLD_MEANINGS[key].example
    record = "spec-mirror/features/F006-per-tier-hrv-datasets.md"
    _plant(tmp_path, "specification/spec/99-quoted.md", f'Quoted: "{example}".\n\nBare: {example}.\n')
    _plant(tmp_path, "runcoach-api/src/runcoach_api/quoted.py", f'TEXT = "{example}"\n')
    _plant(tmp_path, record, f"# F006\n\nLive: {example}.\n\n## Decision Log\n\n- {example}\n")
    hits = scan(tmp_path)
    seen = sorted((h.path, h.line, h.quoted) for h in hits)
    print(f"[slice compared] {seen}; red {[(h.path, h.line) for h in gate_failures(hits, {})]}")
    assert seen == [("runcoach-api/src/runcoach_api/quoted.py", 1, False),
                    (record, 3, False),
                    ("specification/spec/99-quoted.md", 1, True),
                    ("specification/spec/99-quoted.md", 3, False)]
    assert sorted((h.path, h.line) for h in gate_failures(hits, {})) == [
        ("runcoach-api/src/runcoach_api/quoted.py", 1), (record, 3), ("specification/spec/99-quoted.md", 3)]


_WRAPPED_KEY = "C10-lone-candidate-never-struck"
_WRAPPED_HEAD, _WRAPPED_TAIL = "a lone candidate is its own reference", "and is never struck however old it is"
#: The example split over two ``#`` lines, as hrv_trend.py:925-926 states C10.
_WRAPPED_BODY = f"VALUE = 1\n\n    # Rule 1: {_WRAPPED_HEAD}\n    #: {_WRAPPED_TAIL}.\nOTHER = 2\n"


def _wrapped_world(tmp_path: Path, suffix: str) -> tuple[str, list[Hit]]:
    rel = {"py": "runcoach-api/src/runcoach_api/planted.py", "md": "specification/spec/99-planted.md",
           "yaml": "contracts/planted.yaml"}[suffix]
    _plant(tmp_path, rel, _WRAPPED_BODY)
    return rel, [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]


def test_wrapped_comment_py_example_split_over_two_hash_lines_is_a_hit(tmp_path):
    """T219: in ``.py`` the gate reads a wrapped ``#`` comment as one sentence, so an old meaning split
    over two comment lines is a hit, reported on its first line, and the map back to the raw text holds
    (the matched span starts at the raw ``lone``; an EXCEPTIONS excerpt written without the ``#``
    shelters it)."""
    rel, hits = _wrapped_world(tmp_path, "py")
    raw = (tmp_path / rel).read_text(encoding="utf-8")
    text, offsets = gate_text(rel, raw)
    print(f"[slice compared] {rel} gate text {text!r}; hits {[(h.line, h.matched, h.ident) for h in hits]}")
    assert [(h.path, h.line, h.ident) for h in hits] == [(rel, 3, site_id(rel, _WRAPPED_KEY, 1))]
    assert raw[offsets[hits[0].start]:].startswith("lone candidate")
    assert (rel, _WRAPPED_KEY) in [(h.path, h.key) for h in gate_failures(hits, {})]
    excerpt = f"{_WRAPPED_HEAD} {_WRAPPED_TAIL}"
    sheltered = [h for h in scan(tmp_path, exceptions=((rel, excerpt, "F009"),)) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] sheltered by an excerpt without the marker: {[h.sheltered_by for h in sheltered]}")
    assert [h.sheltered_by for h in sheltered] == [(0,)]
    assert census_state({"path": rel, "excerpt": excerpt}, tmp_path, exceptions=()) == "present"


def test_wrapped_comment_markers_stay_in_md(tmp_path):
    """T219: ``.md`` keeps its ``#`` (a heading marker), so the split example is no hit there."""
    rel, hits = _wrapped_world(tmp_path, "md")
    raw = (tmp_path / rel).read_text(encoding="utf-8")
    text, offsets = gate_text(rel, raw)
    print(f"[slice compared] {rel} gate text {text!r}; hits {hits}")
    assert (text, offsets) == normalize_with_offsets(raw)
    assert "reference #: and" in text
    assert hits == []


@pytest.mark.parametrize("suffix", ["yaml", "yml"])
def test_wrapped_comment_yaml_example_split_over_two_hash_lines_is_a_hit(tmp_path, suffix):
    """Sprint-008 F011 review, changing T219's pin: a ``.yaml``/``.yml`` comment wraps like a ``.py`` one,
    and with its ``#`` kept an old meaning split over two comment lines was no hit, a gate bypass. The
    gate now removes the markers there too, keeping the map to the raw text."""
    rel = f"contracts/planted.{suffix}"
    _plant(tmp_path, rel, f"# {_WRAPPED_HEAD}\n# {_WRAPPED_TAIL}.\nx: 1\n")
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    raw = (tmp_path / rel).read_text(encoding="utf-8")
    text, offsets = gate_text(rel, raw)
    print(f"[slice compared] {rel} gate text {text!r}; hits {[(h.line, h.matched) for h in hits]}")
    assert [(h.path, h.line, h.quoted) for h in hits] == [(rel, 1, False)]
    assert raw[offsets[hits[0].start]:].startswith("lone candidate")
    assert (rel, _WRAPPED_KEY) in [(h.path, h.key) for h in gate_failures(hits, {})]
    # T219's body in a .yaml file: its indented "#" and "#:" markers go as in .py.
    body, hits = _wrapped_world(tmp_path, "yaml")
    assert [(h.path, h.line) for h in hits if h.path == body] == [(body, 3)]


@pytest.mark.parametrize("marker, continuation", [
    ("> ", "> "), (">", ">"), ("> > ", "> > "), ("   > ", "   > "),
    ("- > ", "    > "), ("10. > ", "    > "),
], ids=["> ", ">", "> > ", "   > ", "- > ", "10. > "])
def test_a_blockquote_wrapped_example_is_a_hit(tmp_path, marker, continuation):
    """S3: a ``>`` blockquote is swept, not quotation. Its markers survive ``normalize()``, so an old
    meaning wrapped over two blockquote lines read "... reference > and is never struck" and was no hit
    (sprint-008 F011 review). The gate removes them in ``.md``, keeping the map to the raw text. A
    blockquote inside a list item (``- > ``, ``10. > ``, continued under a 4-space indent) loses its
    ``>`` run too, and keeps its list marker (sprint-008 F011 review, iteration 2)."""
    rel = "specification/spec/99-planted.md"
    _plant(tmp_path, rel, f"Intro.\n\n{marker}{_WRAPPED_HEAD}\n{continuation}{_WRAPPED_TAIL}.\n")
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    raw = (tmp_path / rel).read_text(encoding="utf-8")
    text, offsets = gate_text(rel, raw)
    print(f"[slice compared] {marker!r}: gate text {text!r}; hits {[(h.line, h.quoted) for h in hits]}")
    assert [(h.line, h.quoted) for h in hits] == [(3, False)]
    assert raw[offsets[hits[0].start]:].startswith("lone candidate")
    assert (rel, _WRAPPED_KEY) in [(h.path, h.key) for h in gate_failures(hits, {})]
    assert "a > b" in gate_text(rel, "a > b\n")[0], "a > inside a line is text, not a marker"


def test_a_prose_line_opening_with_an_inline_code_span_opens_no_fence(tmp_path):
    """CommonMark 4.5: a backtick fence's info string may not contain a backtick, so "```x``` is the
    literal." is a code span, not a fence. Read as a fence it never closed, and every later line of the
    file was quotation (sprint-008 F011 review)."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = f"```x``` is the literal.\n\nLive prose: {example}.\n"
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] fenced {_fenced_blocks(raw)}; hits {[(h.line, h.quoted) for h in hits]}")
    assert _fenced_blocks(raw) == []
    assert [(h.line, h.quoted) for h in hits] == [(3, False)]
    assert (rel, _WRAPPED_KEY) in [(h.path, h.key) for h in gate_failures(hits, {})]


def test_an_unclosed_fence_shelters_no_prose_and_a_closed_one_still_does(tmp_path):
    """An unclosed fence is prose, not a block to the end of the file (sprint-008 F011 review): its
    opener and every later line are swept, and a closed fence later in the file is still code."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = f"Intro.\n\n````\nnever closed\n\nLive prose: {example}.\n\n```\nIn code: {example}.\n```\n"
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    blocks = [raw[a:b] for a, b in _fenced_blocks(raw)]
    print(f"[slice compared] fenced {blocks}; hits {[(h.line, h.quoted) for h in hits]}")
    assert blocks == [f"```\nIn code: {example}.\n```\n"]
    assert [(h.line, h.quoted) for h in hits] == [(6, False), (9, True)]
    assert [h.line for h in gate_failures(hits, {})] == [6]


def test_an_odd_count_of_straight_quotes_shelters_no_prose(tmp_path):
    """S3's straight-quote pairing is greedy within a paragraph, so one stray ``"`` paired with the next
    one and sheltered the live prose between them (sprint-008 F011 review). A paragraph with an odd
    count of straight double quotes (outside code spans) pairs none: its prose is swept. A paragraph with
    an even count still shelters its quotation."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = f'A stray " opens nothing. Live: {example}. Then "a pair".\n\nQuoted: "{example}".\n'
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] spans {_spans(raw)}; hits {[(h.line, h.quoted) for h in hits]}")
    assert [(h.line, h.quoted) for h in hits] == [(1, False), (3, True)]
    assert [h.line for h in gate_failures(hits, {})] == [1]


def test_an_unbalanced_curly_quote_run_shelters_no_prose(tmp_path):
    """The odd-count rule's curly twin (sprint-008 F011 review, iteration 2): a stray ``“`` paired with
    the next ``”`` and sheltered the live prose between them. A paragraph whose curly quotes (outside
    code spans) do not run strictly ``“ ” “ ”`` pairs none of them. A balanced run still shelters."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = f"A stray “ opens. Live: {example}. Then “a pair”.\n\nQuoted: “{example}”.\n"
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] spans {_spans(raw)}; hits {[(h.line, h.quoted) for h in hits]}")
    assert [(h.line, h.quoted) for h in hits] == [(1, False), (3, True)]
    assert [h.line for h in gate_failures(hits, {})] == [1]


def test_a_straight_quote_pairs_only_a_left_flanking_opener_with_a_right_flanking_closer(tmp_path):
    """CommonMark's delimiter rule (sprint-008 F011 review, iteration 2): two stray inch marks made an even
    count, so ``5"`` paired with ``3"`` and sheltered the prose between them. A straight ``"`` opens only
    when left-flanking and closes only when right-flanking, read as CommonMark reads ``_``
    (``_quote_roles``), so an intraword ``x"y`` is ambiguous and pairs nothing. A real quotation still
    shelters."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = (f'The 5" strap. Live: {example}. A 3" band.\n\n'
           f'Both x"y. Live: {example}. Then z"w.\n\n'
           f'Quoted: "{example}".\n')
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] spans {_spans(raw)}; hits {[(h.line, h.quoted) for h in hits]}")
    assert [(h.line, h.quoted) for h in hits] == [(1, False), (3, False), (5, True)]
    assert [h.line for h in gate_failures(hits, {})] == [1, 3]


def test_a_bare_blockquote_marker_line_separates_paragraphs(tmp_path):
    """A ``>``-only line is a blockquote's blank line (sprint-008 F011 review, iteration 2). Read as text
    it joined three blockquote paragraphs into one, and a quote opened in the first and closed in the
    third sheltered the live prose of the second. ``_paragraphs`` now breaks at it."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = f'> He wrote "a\n>\n> Live: {example}.\n>\n> b" ends.\n\n> Quoted: "{example}".\n'
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    paragraphs = [raw[a:b] for a, b in _paragraphs(raw, _fenced_blocks(raw))]
    print(f"[slice compared] paragraphs {paragraphs}; spans {_spans(raw)}; "
          f"hits {[(h.line, h.quoted) for h in hits]}")
    assert paragraphs == ['> He wrote "a\n', f"> Live: {example}.\n", '> b" ends.\n',
                          f'> Quoted: "{example}".\n']
    assert [(h.line, h.quoted) for h in hits] == [(3, False), (7, True)]
    assert [h.line for h in gate_failures(hits, {})] == [3]


def test_a_fence_opened_in_a_list_item_ends_with_the_item(tmp_path):
    """CommonMark 5.2 (sprint-008 F011 review, iteration 2): a fence opened inside a list item ends where
    the item ends, at the first non-blank line indented less than the item's content. Before, it ran on
    past the item, paired with a later top-level fence and sheltered the prose between them. The later
    top-level block stays code. Iteration 3: the item's end is no closing fence line, so the item's fence
    is unclosed and, like any unclosed fence, its lines are prose; its example is now red too."""
    example = OLD_MEANINGS[_WRAPPED_KEY].example
    rel = "specification/spec/99-planted.md"
    raw = (f"- item\n  ```\n  In code: {example}.\n\nLive prose: {example}.\n\n"
           f"```\nIn code: {example}.\n```\n")
    _plant(tmp_path, rel, raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    blocks = [raw[a:b] for a, b in _fenced_blocks(raw)]
    print(f"[slice compared] fenced {blocks}; hits {[(h.line, h.quoted) for h in hits]}")
    assert blocks == [f"```\nIn code: {example}.\n```\n"]
    assert [(h.line, h.quoted) for h in hits] == [(3, False), (5, False), (8, True)]
    assert [h.line for h in gate_failures(hits, {})] == [3, 5]


def _iteration_3_world(tmp_path: Path, template: str) -> tuple[str, list[Hit]]:
    """``template`` with ``{EX}`` as the C10 example, planted in a live ``.md`` file; its C10 hits."""
    raw = template.format(EX=OLD_MEANINGS[_WRAPPED_KEY].example)
    _plant(tmp_path, "specification/spec/99-planted.md", raw)
    hits = [h for h in scan(tmp_path) if h.key == _WRAPPED_KEY]
    print(f"[slice compared] fenced {[raw[a:b] for a, b in _fenced_blocks(raw)]}; spans {_spans(raw)}; "
          f"hits {[(h.line, h.quoted) for h in hits]}; red {[h.line for h in gate_failures(hits, {})]}")
    return raw, hits


def test_a_list_item_fence_ends_at_a_column_zero_blockquote_marker(tmp_path):
    """Sprint-008 F011 review, iteration 3 (N1): a ``>`` line at column 0 ends the list item and the fence
    opened in it, so the fence is unclosed and the prose after it is swept. The hand-written parser read
    ``>`` as a blank line and ran the fence on to the indented closer below."""
    _raw, hits = _iteration_3_world(tmp_path, "- item\n  ```\n  code\n>\n  Live {EX}.\n  ```\n")
    assert [(h.line, h.quoted) for h in hits] == [(5, False)]
    assert [h.line for h in gate_failures(hits, {})] == [5]


def test_a_fence_opened_after_a_lazy_continuation_line_ends_with_its_item(tmp_path):
    """Iteration 3 (N2): a lazy continuation line keeps the item open, so a fence opened after it is in
    the item and ends with it. The hand-written walk back stopped at the lazy line, read the fence as
    top-level and paired it with a later fence."""
    _raw, hits = _iteration_3_world(tmp_path, "- item\nlazy\n  ```\n  code\n\nLive {EX}.\n\n```\nmore\n```\n")
    assert [(h.line, h.quoted) for h in hits] == [(6, False)]
    assert [h.line for h in gate_failures(hits, {})] == [6]


def test_a_list_item_fence_closer_indented_three_past_the_item_content_closes_it(tmp_path):
    """Iteration 3 (N3): a closer may sit up to three columns past the item's content column; five spaces
    in a ``- `` item is three past column 2, so it closes the fence and the next line is prose."""
    raw, hits = _iteration_3_world(tmp_path, "- a\n  ```\n  code\n     ```\n  Live {EX}.\n\nNext para.\n")
    assert [raw[a:b] for a, b in _fenced_blocks(raw)] == ["  ```\n  code\n     ```\n"]
    assert [(h.line, h.quoted) for h in hits] == [(5, False)]
    assert [h.line for h in gate_failures(hits, {})] == [5]


def test_a_blockquote_fence_closes_on_a_longer_closer(tmp_path):
    """Iteration 3 (N4): inside a blockquote a closer at least as long as the opener closes the fence
    (```` after ```), so the next quoted line is prose. The hand-written parser saw no fence line
    behind ``> `` and never closed it."""
    raw, hits = _iteration_3_world(tmp_path, "> ```\n> code\n> ````\n> Live {EX}.\n> ```\n")
    assert [raw[a:b] for a, b in _fenced_blocks(raw)] == ["> ```\n> code\n> ````\n"]
    assert [(h.line, h.quoted) for h in hits] == [(4, False)]
    assert [h.line for h in gate_failures(hits, {})] == [4]


@pytest.mark.parametrize("opener, closer", [('"', '"'), ("“", "”")], ids=["straight", "curly"])
def test_a_quote_closer_inside_a_code_span_closes_nothing(tmp_path, opener, closer):
    """Iteration 3 (N5): the closer search found the quote inside a code span, and the span's orphaned
    closing backtick then paired with a later one and sheltered the prose between. A quote inside a code
    span is never counted or paired (``_quote_pairs``)."""
    template = opener + "x `" + closer + "` y" + closer + " Live {EX}. `z`.\n"
    raw, hits = _iteration_3_world(tmp_path, template)
    assert _spans(raw) == [f"{opener}x `{closer}` y{closer}", "`z`"]
    assert [(h.line, h.quoted) for h in hits] == [(1, False)]
    assert [h.line for h in gate_failures(hits, {})] == [1]


def test_a_curly_quote_pairs_only_a_left_flanking_opener_with_a_right_flanking_closer(tmp_path):
    """Iteration 3 (N6): the straight twin's flanking rule (``_quote_roles``) applies to curly quotes too.
    A stray ``“`` between spaces cannot open, and ``5”`` can only close, so they pair nothing. A real
    curly quotation still shelters."""
    raw, hits = _iteration_3_world(
        tmp_path, "A stray “ here. Live {EX}. A 5” strap.\n\nQuoted: “{EX}”.\n")
    assert [(h.line, h.quoted) for h in hits] == [(1, False), (3, True)]
    assert [h.line for h in gate_failures(hits, {})] == [1]


def test_a_spaced_thematic_break_opens_no_list_item(tmp_path):
    """Iteration 3 (N7): ``- - -`` is a thematic break, not a list item, so the indented fence after it is
    top-level and closes at the column-0 closer. Read as an item, it swallowed the closer and paired the
    next fence."""
    raw, hits = _iteration_3_world(tmp_path, "- - -\n  ```\ncode\n```\nLive {EX}.\n```\nmore\n```\n")
    assert [raw[a:b] for a, b in _fenced_blocks(raw)] == ["  ```\ncode\n```\n", "```\nmore\n```\n"]
    assert [(h.line, h.quoted) for h in hits] == [(5, False)]
    assert [h.line for h in gate_failures(hits, {})] == [5]


@pytest.mark.parametrize("marker", ["- ", "1. "], ids=["bullet", "ordered"])
def test_a_fence_opened_on_a_list_marker_line_closes_inside_its_item(tmp_path, marker):
    """Iteration 3 (NEW): a fence opened on the list marker's own line is in the item and closes there,
    so the next item line is prose. The hand-written parser never saw an opener behind a marker."""
    pad = " " * len(marker)
    template = f"{marker}```\n{pad}code\n{pad}```\n{pad}Live {{EX}}.\n{pad}```\n{pad}more\n{pad}```\n"
    raw, hits = _iteration_3_world(tmp_path, template)
    assert [raw[a:b] for a, b in _fenced_blocks(raw)] == [
        f"{marker}```\n{pad}code\n{pad}```\n", f"{pad}```\n{pad}more\n{pad}```\n"]
    assert [(h.line, h.quoted) for h in hits] == [(4, False)]
    assert [h.line for h in gate_failures(hits, {})] == [4]


def _iteration_4_verdict(tmp_path: Path, template: str, line: int) -> str:
    """``template`` planted (``_iteration_3_world``); asserts its one C10 hit is on ``line``, unquoted and
    red, and returns the raw text."""
    raw, hits = _iteration_3_world(tmp_path, template)
    assert [(h.line, h.quoted) for h in hits] == [(line, False)]
    assert [h.line for h in gate_failures(hits, {})] == [line]
    return raw


def test_an_unclosed_fence_opener_closes_no_code_span_opened_before_it(tmp_path):
    """Sprint-008 F011 review, iteration 4 (M1): CommonMark ends a paragraph at a fence line, so the run of
    an unclosed fence's opener never closes a code span opened before it. The gate found spans on the raw
    text, where that run survived, in blank-line paragraphs that ran on through the fence line, and the
    span sheltered the live line between them. Spans are now found on the substituted text, where the
    opener is letters, and inside one markdown-it block."""
    _iteration_4_verdict(tmp_path, "Use ``` to open a fence.\nLive {EX}.\n```\n", 2)


def test_an_unclosed_list_item_fence_opener_closes_no_code_span_opened_before_it(tmp_path):
    """Iteration 4 (M1), the regression in 1188ac7: the item's fence is unclosed when the next item ends
    it, so its opener's run closed the code span the item's first line opened. 5614990 read the fence as
    a block, which ended the paragraph, and was red here."""
    _iteration_4_verdict(tmp_path, "- Use ``` to open a fence.\n  Live {EX}.\n  ```\n- next item\n", 2)


def test_an_unclosed_blockquote_fence_opener_closes_no_code_span_opened_before_it(tmp_path):
    """Iteration 4 (M1): the blockquote twin; the quote's end leaves the fence unclosed."""
    _iteration_4_verdict(tmp_path, "> Use ``` to open a fence.\n> Live {EX}.\n> ```\n\nAfter.\n", 2)


def test_an_unclosed_fence_info_string_closes_no_straight_quote_opened_before_it(tmp_path):
    """Iteration 4 (M1), the straight-quote sibling: the ``"`` in an unclosed opener's info string closed
    a quote opened in the paragraph before the fence line. CommonMark ends that paragraph at the fence
    line, and a span never crosses a block boundary of any reading the gate takes."""
    _iteration_4_verdict(tmp_path, 'A "quote opens\nLive {EX}.\n``` x"\n', 2)


def test_a_trailing_text_near_closer_leaves_the_unclosed_fence_prose(tmp_path):
    """Iteration 4 (M1): ``` py cannot close a fence, so the fence is unclosed and its lines are prose;
    the near-closer's run paired with the opener's as one code span and sheltered them."""
    raw = _iteration_4_verdict(tmp_path, "```\nLive {EX}.\n``` py\n", 2)
    assert _fenced_blocks(raw) == []


def test_an_indented_near_closer_leaves_the_unclosed_fence_prose(tmp_path):
    """Iteration 4 (M1): a closer indented four columns is code, closes nothing, and paired with the
    unclosed opener's run as one code span."""
    raw = _iteration_4_verdict(tmp_path, "```\nLive {EX}.\n    ```\n", 2)
    assert _fenced_blocks(raw) == []


def test_an_atx_heading_is_its_own_block_and_no_code_span_leaves_it(tmp_path):
    """Iteration 4 (M1), the heading sibling (no fence at all): an ATX heading is a block of its own, so a
    run opened in it pairs with nothing after it. The gate's blank-line paragraph ran through it."""
    _iteration_4_verdict(tmp_path, "# The ``` marker\nLive {EX}.\nThen ``` again.\n", 2)


def test_a_fence_closer_indented_four_columns_closes_no_fence(tmp_path):
    """Iteration 4 (S1): pins ``_fence_marking_closure``'s ``is_code_block`` clause. An indented closer is
    code and ends nothing, so the fence is unclosed and its prose is swept; read as a closer, the whole
    fence was one block and sheltered the live line."""
    raw = _iteration_4_verdict(tmp_path, "```\ncode\n\nLive {EX}.\n\n    ```\n", 4)
    assert _fenced_blocks(raw) == []


def test_a_fence_closer_with_trailing_text_closes_no_fence(tmp_path):
    """Iteration 4 (S1): pins the closing test's spaces-only tail: ``` py opens a fence and closes none."""
    raw = _iteration_4_verdict(tmp_path, "```\ncode\n\nLive {EX}.\n\n``` py\n", 4)
    assert _fenced_blocks(raw) == []


def test_a_backtick_run_closes_no_tilde_fence(tmp_path):
    """Iteration 4 (S1): pins the closing test's marker character: a backtick run cannot close a ``~~~``
    fence, which stays unclosed, and the backtick line opens a fence of its own that nothing closes."""
    raw = _iteration_4_verdict(tmp_path, "~~~\ncode\n\nLive {EX}.\n\n```\n", 4)
    assert _fenced_blocks(raw) == []


def test_scanner_quote_spans_oracle_iteration_4():
    """Iteration 4's inputs as ``quote_spans`` oracle cases, "X" for the example, with the line-end
    controls the block reading must keep exact."""
    cases = [
        # M1: an unclosed opener's run closes no span, and no span leaves a markdown-it block.
        ("Use ``` to open a fence.\nLive X.\n```\n", []),
        ("- Use ``` to open a fence.\n  Live X.\n  ```\n- next item\n", []),
        ("> Use ``` to open a fence.\n> Live X.\n> ```\n\nAfter.\n", []),
        ('A "quote opens\nLive X.\n``` x"\n', []),
        ("```\nLive X.\n``` py\n", []),
        ("```\nLive X.\n    ```\n", []),
        ("# The ``` marker\nLive X.\nThen ``` again.\n", []),
        # S1: near-closers close no fence.
        ("```\ncode\n\nLive X.\n\n    ```\n", []),
        ("```\ncode\n\nLive X.\n\n``` py\n", []),
        ("~~~\ncode\n\nLive X.\n\n```\n", []),
        # Controls: spans inside one block still shelter, a closed fence after a paragraph is still code,
        # and a span over CRLF, CR and mixed line ends maps back to the raw text.
        ("# The `x` marker\nLive X.\n", ["`x`"]),
        ('Use ``` to open.\nThen "a\nquote" and ``` here.\n', ['``` to open.\nThen "a\nquote" and ```']),
        ("Use `a\r\nb` here\r\n```\r\ncode\r\n```\r\n", ["`a\r\nb`", "```\r\ncode\r\n```\r\n"]),
        ('A "x\ry" b\r\n\r\n```\ncode\r```\n"z"', ['"x\ry"', "```\ncode\r```\n", '"z"']),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("template", [
    "The x\n~~~ y` z\nLive {EX}.\nThen `code` here.\n",
    "- The x\n  ~~~ y` z\n  Live {EX}.\n  Then `code` here.\n- next\n",
    "> The x\n> ~~~ y` z\n> Live {EX}.\n> Then `code` here.\n\nAfter.\n",
], ids=["plain", "list_item", "blockquote"])
def test_an_unclosed_fence_info_string_backtick_opens_no_code_span(tmp_path, template):
    """Sprint-008 F011 review, iteration 5 (M1), a regression in ca963a0: only an unclosed opener's fence
    run was read as letters, so the backtick in its info string (``~~~ y` z``) survived. The raw reading's
    block edge at the fence line cut it off from the ``x`` it pairs with in the gate's own reading, and it
    paired forward with the next code span's opener and sheltered the live line, which lies inside the
    unclosed fence and is prose. The whole opener line, info string included, is now letters: CommonMark
    never reads an info string as inline text. 1e8444c was red here.

    Iteration 6 re-points it: iteration 5's inputs opened ``The `x`` before the fence line, and in the
    gate's one reading (``_gate_reading``) that run pairs with ``Then ``` -- markdown-it renders
    ``<code>x xxxxxxxx Live X. Then </code>`` -- so the live line is a CommonMark code span and those
    inputs are sheltered oracle cases now. Without the ``x`` run, only the info string's backtick could
    pair with ``Then ```, and read as letters it pairs with nothing."""
    _iteration_4_verdict(tmp_path, template, 3)


def test_an_unclosed_fence_info_string_quote_opens_no_quote_span(tmp_path):
    """Iteration 5 (M1), the straight-quote sibling: the info string's ``"`` survived, and the raw
    reading's edge left it in a region of its own with ``end"`` -- an even count -- so the two paired
    over the live line. The info string is letters now, and the paragraph's two quotes, ``5"`` and ``end"``,
    can only close, so they pair nothing."""
    _iteration_4_verdict(tmp_path, 'A 5" strap.\n``` "z\nLive {EX}.\nend" now.\n', 3)


def test_a_heading_in_an_unclosed_fence_ends_a_region_in_the_gates_own_reading(tmp_path):
    """Iteration 5 (S1): pins the gate's own reading (``_gate_reading``). The raw reading holds all four
    lines in one fence; read with the opener as letters, line 2 is an ATX heading, a block of its own,
    and the run opened in it pairs with nothing after it. Read by the raw text's blocks alone, the four
    lines were one region and the two runs sheltered the live line."""
    _iteration_4_verdict(tmp_path, "```\n# The ` marker\nLive {EX}.\nThen ` again.\n", 3)


def test_a_nested_unclosed_fence_info_string_closes_no_quote(tmp_path):
    """Iteration 5 (S1), the scanner's middle-reading input: ```` opens a fence that ``` cannot close,
    and read as prose it lets ``` x" open a second unclosed fence. The quote in that info string is
    letters, so the quote opened on line 2 pairs with nothing."""
    _iteration_4_verdict(tmp_path, '````\nA "quote opens\nLive {EX}.\n``` x"\n', 3)


def test_a_nested_unclosed_fence_opener_leaves_one_commonmark_paragraph_whose_quotes_pair(tmp_path):
    """Iteration 5 (S1) pinned a middle reading's block edge at line 3 here, which kept the quotes on
    lines 2 and 5 apart. Iteration 6 takes one reading, the gate's own, where ```` and ``` x are both
    letters and markdown-it reads all five lines as one paragraph
    (``<p>xxxx\\nA &quot;quote opens\\nxxxxx\\nLive X.\\nend&quot; now.</p>``); S3 pairs its two quotes,
    so the live line is quotation. On the raw text CommonMark reads it inside the unclosed ```` fence
    (``<pre>``). A reading that restored the middle edge would leave line 4 bare and fail here."""
    raw, hits = _iteration_3_world(tmp_path, '````\nA "quote opens\n``` x\nLive {EX}.\nend" now.\n')
    assert _spans(raw) == ['"quote opens\n``` x\nLive ' + OLD_MEANINGS[_WRAPPED_KEY].example + '.\nend"']
    assert [(h.line, h.quoted) for h in hits] == [(4, True)]
    assert gate_failures(hits, {}) == []


def test_scanner_quote_spans_oracle_iteration_5():
    """Iteration 5's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: a span
    after an unclosed opener line still pairs, and a closed fence keeps its info string."""
    cases = [
        # M1: an unclosed opener's info string pairs with nothing.
        ("The x\n~~~ y` z\nLive X.\nThen `code` here.\n", ["`code`"]),
        ("- The x\n  ~~~ y` z\n  Live X.\n  Then `code` here.\n- next\n", ["`code`"]),
        ("> The x\n> ~~~ y` z\n> Live X.\n> Then `code` here.\n\nAfter.\n", ["`code`"]),
        ('A 5" strap.\n``` "z\nLive X.\nend" now.\n', []),
        # Iteration 6: with the opener line as letters, CommonMark pairs "The `x" with "Then `"
        # (<code>x xxxxxxxx Live X. Then </code>); "`code`" was a span it does not have.
        ("The `x\n~~~ y` z\nLive X.\nThen `code` here.\n", ["`x\n~~~ y` z\nLive X.\nThen `"]),
        ("- The `x\n  ~~~ y` z\n  Live X.\n  Then `code` here.\n- next\n", ["`x\n  ~~~ y` z\n  Live X.\n  Then `"]),
        ("> The `x\n> ~~~ y` z\n> Live X.\n> Then `code` here.\n\nAfter.\n", ["`x\n> ~~~ y` z\n> Live X.\n> Then `"]),
        # S1: the gate's own reading's block edges hold.
        ("```\n# The ` marker\nLive X.\nThen ` again.\n", []),
        ('````\nA "quote opens\nLive X.\n``` x"\n', []),
        # Iteration 6: one reading, one paragraph (<p>xxxx\nA &quot;quote opens\nxxxxx\nLive X.\nend&quot;
        # now.</p>), and S3 pairs its two quotes; no middle reading's edge keeps them apart.
        ('````\nA "quote opens\n``` x\nLive X.\nend" now.\n', ['"quote opens\n``` x\nLive X.\nend"']),
        # Iteration 6: markdown-it ends no line at U+2028, and reads one code span
        # (<code>a\u2028\u2028Live X.\u2028b</code>); iteration 5 cut it at str.splitlines' blank line.
        ("The `a\u2028\u2028Live X.\u2028b` end\n", ["`a\u2028\u2028Live X.\u2028b`"]),
        # Controls.
        ("~~~ y`\nLive `X.` here\n", ["`X.`"]),
        ("~~~ y`\ncode\n~~~\nLive X.\n", ["~~~ y`\ncode\n~~~\n"]),
        ('``` "z\nSay "c" d\n', ['"c"']),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("template", [
    "See ````a\n~~~\nb ```` c\nLive {EX}.\nd ```` e\n",
    "Run `ls\n```\nthen ` now\nLive {EX}.\nand `x` done\n",
    'Say "a\n~~~\nx "c\nLive {EX}.\nd" e\n',
    "Say “a\n```\nx “c\nLive {EX}.\nd” e\n",
    "- See `a\n  ```\n  b ` c\n  Live {EX}.\n  d ` e\n",
    "> See `a\n> ```\n> b ` c\n> Live {EX}.\n> d ` e\n",
], ids=["code_run", "single_backtick", "straight", "curly", "list_item", "blockquote"])
def test_a_delimiter_before_an_unclosed_fence_opener_keeps_its_commonmark_partner(tmp_path, template):
    """Sprint-008 F011 review, iteration 6 (M1): the gate grouped lines by the leaf block of every
    markdown-it reading, so the raw reading's edge at an unclosed opener line cut the delimiter on the
    line before it off from its partner after it. CommonMark reads that text, the opener as letters, as
    one paragraph and pairs the two; the region below the edge paired the partner forward instead and
    sheltered the live line. Spans now come from the gate's own reading alone (``_gate_reading``), and
    quotes pair once per CommonMark paragraph."""
    _iteration_4_verdict(tmp_path, template, 4)


@pytest.mark.parametrize("template", [
    "Type \\`a\nLive {EX}.\nthen b` c\n",
    "Type \\``a\nLive {EX}.\nthen b`` c\n",
], ids=["single", "double_run"])
def test_an_escaped_backtick_opens_no_code_span(tmp_path, template):
    """Iteration 6 (M2): CommonMark 6.1 reads ``\\``` outside a code span as a literal backtick, never a
    delimiter, so it opens no span, and with a double run the second backtick is a run of one that a
    later ````` cannot close. The hand-written pairing opened a span at it and sheltered the live line.
    Code spans are now markdown-it's ``code_inline`` tokens."""
    _iteration_4_verdict(tmp_path, template, 2)


@pytest.mark.parametrize("template, line", [
    ('# A "x\nLive {EX}.\nb" c\n', 2),
    ('A "x\n---\nLive {EX}.\nb" c\n', 3),
    ('- A "x\n- Live {EX}.\n  b" c\n', 2),
    ('A "x\n> Live {EX}.\n> b" c\n', 2),
], ids=["atx_heading", "setext_heading", "list_item", "blockquote"])
def test_a_quote_pairs_only_inside_its_commonmark_paragraph_or_heading(tmp_path, template, line):
    """Iteration 6: S3 pairs once per CommonMark paragraph or heading, and a heading, a list item or a
    blockquote ends one without a blank line. Each block here holds one quote, so none pairs; paired over
    the blank-line paragraph, which runs through every such edge, the two sheltered the live line."""
    _iteration_4_verdict(tmp_path, template, line)


@pytest.mark.parametrize("template, line", [
    ("<div>\nSay `a Live {EX}. b` and \"c Live {EX}. d\".\n</div>\n", 2),
    ("Intro.\n\n    Say `a Live {EX}. b` and \"c Live {EX}. d\".\n", 3),
], ids=["html_block", "indented_code_block"])
def test_an_html_block_or_an_indented_code_block_holds_no_span(tmp_path, template, line):
    """Iteration 6: only a paragraph or a heading holds a code or quote span, as markdown-it reads it: an
    HTML block and an indented code block have no inline content. The gate never read either block as
    quotation, and 4246643 still found spans in their lines; now it finds none, and both examples are
    red (the F012-routed item, resolved in the stricter direction)."""
    raw, hits = _iteration_3_world(tmp_path, template)
    assert _spans(raw) == []
    assert [(h.line, h.quoted) for h in hits] == [(line, False), (line, False)]
    assert [h.line for h in gate_failures(hits, {})] == [line, line]


def test_scanner_quote_spans_oracle_iteration_6():
    """Iteration 6's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: code
    spans are markdown-it's (escapes, run lengths), an HTML block or an indented code block holds no
    span, and a span behind container markers or tabs maps back to exact raw offsets."""
    cases = [
        # M1: a delimiter before an unclosed opener line pairs with its CommonMark partner.
        ("See ````a\n~~~\nb ```` c\nLive X.\nd ```` e\n", ["````a\n~~~\nb ````"]),
        ("Run `ls\n```\nthen ` now\nLive X.\nand `x` done\n", ["`ls\n```\nthen `", "`x`"]),
        ('Say "a\n~~~\nx "c\nLive X.\nd" e\n', []),
        ("- See `a\n  ```\n  b ` c\n  Live X.\n  d ` e\n", ["`a\n  ```\n  b `"]),
        # M2: an escaped backtick is a literal; inside a code span a backslash escapes nothing.
        ("Type \\`a\nLive X.\nthen b` c\n", []),
        ("Type \\``a\nLive X.\nthen b`` c\n", []),
        ("Type `a\nLive X.\nthen b` c\n", ["`a\nLive X.\nthen b`"]),
        ("Type `a\nLive X.\nthen b\\` c\n", ["`a\nLive X.\nthen b\\`"]),
        ("A ``run with ` inside`` here\n", ["``run with ` inside``"]),
        # S3 pairs once per paragraph or heading; a heading, an item or a blockquote ends one.
        ('# A "x\nLive X.\nb" c\n', []),
        ('A "x\n---\nLive X.\nb" c\n', []),
        ('- A "x\n- Live X.\n  b" c\n', []),
        ('A "x\n> Live X.\n> b" c\n', []),
        # Only a paragraph or a heading holds a span.
        ("<div>\n`a` and \"b\" here\n</div>\n", []),
        ("    `a` and \"b\" here\n", []),
        # Offsets behind blockquote and list markers, tabs, and CRLF, CR and mixed line ends.
        ("> a `b\n> c` d\n", ["`b\n> c`"]),
        ("- a\n  - b `c\n    d` e\n", ["`c\n    d`"]),
        ("> - a \"b\n>   c\" d\n", ['"b\n>   c"']),
        ("-\ta `b\n\tc` d\n", ["`b\n\tc`"]),
        ("> x `a\r\n> b` y\r\n", ["`a\r\n> b`"]),
        ("a `b\rc` \"d\r\ne\" f\n", ["`b\rc`", '"d\r\ne"']),
        ("# Head `x` and \"y\" ##\n", ["`x`", '"y"']),
        ("Set `x`\n===\n", ["`x`"]),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


#: Inputs whose inline content sits behind container markers, tabs and every line end markdown-it counts,
#: and a BOM, which markdown-it keeps as content (iteration 7: a normalize that stripped it would shift
#: every offset on its line, and this test would fail).
_MAPPING_INPUTS = (
    "> a `b\r\n> c` d\r\n", "- a\n  - b `c\n    d` e\n", "-\ta `b\n\tc` d\n", "* a\n\tb `c` d\n",
    "> - a \"b\r>   c\" d\r", "1. a\r\n   b `c`\n\n   d\re\n", "# Head `x` ##\n", "Set `x`\r\n===\r\n",
    "a \u2028 `b`\n\u2028\n", "\x1c a `b`\n", "a \0 `b`\n", "  a\n\t\tb `c`\n",
    "\ufeffa `b`\n\ufeffc `d`\n",
)
#: A line's leading run of container markers and indentation, which no content character maps into.
_CONTAINER_PREFIX = re.compile(r"[ \t>]*(?:(?:[-+*]|[0-9]{1,9}[.)])[ \t>]*)*")


def test_scanner_inline_content_maps_back_to_the_raw_text():
    """``quote_spans`` places each code span through the ``src`` offsets ``_source_recording`` keeps for a
    paragraph's or heading's content (sprint-008 F011 review, iteration 6). Over every live ``.md`` file
    and ``_MAPPING_INPUTS``: each content character is the raw character at its mapped offset (``\\n`` a
    line end's first character, a space a partly consumed tab, U+FFFD a NUL), the offsets never go back,
    and every raw character the map skips inside the block is a line end or a line's leading container
    markers and indentation (``_CONTAINER_PREFIX``). So the raw span with its prefixes removed is the
    content markdown-it parsed."""
    inputs = [("input", raw) for raw in _MAPPING_INPUTS]
    inputs += [(path, (_REPO_ROOT / path).read_text(encoding="utf-8"))
               for paths in live_files(_REPO_ROOT).values() for path in paths if path.endswith(".md")]
    blocks = 0
    for name, raw in inputs:
        text, starts, tokens = _gate_reading(raw)
        raw_offset = _raw_offset_map(text, starts)
        for token in (t for t in tokens if t.type == "inline" and t.content):
            where = [raw_offset(o) for o in token.meta["source"]]
            assert len(where) == len(token.content), (name, token.map)
            for k, (char, at) in enumerate(zip(token.content, where)):
                found = raw[at]
                assert found == char or (char == "\n" and found in "\r\n") or (char == " " and found == "\t") \
                    or (char == "\ufffd" and found == "\0"), (name, token.map, k, char, found)
                assert k == 0 or where[k - 1] < at or (at == where[k - 1] and found == "\t"), (name, token.map, k)
            kept = set(where)
            for line in range(token.map[0], token.map[1]):
                a, b = max(starts[line], where[0]), min(starts[line + 1], where[-1] + 1)
                skipped = "".join(raw[i] for i in range(a, b) if i not in kept)
                lead = raw[a:next((i for i in range(a, b) if i in kept), b)]
                assert _CONTAINER_PREFIX.fullmatch(lead.rstrip("\r\n")) or not lead.strip(" \t\r\n>") \
                    or line == token.map[0], (name, line, lead)
                assert skipped[len(lead):].strip("\r\n") == "", (name, line, skipped)
            blocks += 1
    print(f"[slice compared] {blocks} paragraphs and headings in {len(inputs)} inputs map back to the raw text")
    assert blocks > len(_MAPPING_INPUTS)


@pytest.mark.parametrize("template", [
    '> Say "a\n> Live {EX}.\n>" b\n',
    '> Say “a\n> Live {EX}.\n>” b\n',
    '- > Say "a\n  > Live {EX}.\n  >" b\n',
    '> > Say "a\n> > Live {EX}.\n>>" b\n',
], ids=["straight", "curly", "list_item_blockquote", "double_marker"])
def test_a_container_marker_is_no_neighbour_of_a_quote(tmp_path, template):
    """Sprint-008 F011 review, iteration 7 (B1): S3 read a quote's neighbours on the paragraph's raw source
    lines, so in ``>"`` the blockquote marker was the character before the quote -- punctuation -- and the
    quote could close. CommonMark's paragraph content has a line end there
    (``Say "a\\nLive X.\\n" b``), and a line end is whitespace, so a quote after it and before a space is
    neither left- nor right-flanking and closes nothing. Roles are now read on the content markdown-it
    parses (``_quote_roles`` over ``token.content``)."""
    _iteration_4_verdict(tmp_path, template, 2)


@pytest.mark.parametrize("space", ["\x0b", "\u2028", "\x1f", "\x85"], ids=["vt", "u2028", "x1f", "x85"])
def test_only_commonmark_whitespace_makes_a_quote_flanking(tmp_path, space):
    """Iteration 7 (W): CommonMark's Unicode whitespace is the Zs category plus tab, line feed, form feed
    and carriage return. Python's ``str.isspace`` also holds ``\\x0b``, ``\\x1c`` to ``\\x1f``, ``\\x85``,
    U+2028 and U+2029, which read the ``b"`` before one as right-flanking. In CommonMark none of them is
    whitespace, so that quote is intraword, ambiguous, and closes nothing."""
    _iteration_4_verdict(tmp_path, 'Say "a\nLive {EX}.\nb"' + space + "c now.\n", 2)


def test_an_unclosed_fence_opener_keeps_the_list_marker_before_its_fence_run(tmp_path):
    """Iteration 7 (S1): pins "letters from its fence run" (``_gate_reading``). The opener line ``- ````
    becomes ``- xxx``, so the list item still interrupts the paragraph on line 1, and the ``a`` run there
    pairs with nothing. Read as letters from the line start, the list marker went too, the three lines
    were one paragraph, and its code span sheltered the live line."""
    _iteration_4_verdict(tmp_path, "Para `a\n- ```\n  Live {EX}. b` c\n", 3)


def test_a_symbol_next_to_a_quote_counts_as_punctuation(tmp_path):
    """Iteration 7 (A1): pins CommonMark 0.31's punctuation, the P and S categories (``_quote_roles``). A
    quote after the symbol ``=`` and before a letter is left- and not right-flanking, so it opens and the
    quotation shelters its line. Read as a letter, ``=`` made the quote ambiguous and it opened nothing."""
    raw, hits = _iteration_3_world(tmp_path, 'Set ="a\nLive {EX}.\nb" c\n')
    assert _spans(raw) == ['"a\nLive ' + OLD_MEANINGS[_WRAPPED_KEY].example + '.\nb"']
    assert [(h.line, h.quoted) for h in hits] == [(2, True)]
    assert gate_failures(hits, {}) == []


def test_scanner_quote_spans_oracle_iteration_7():
    """Iteration 7's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: a quote's
    neighbours are the characters of CommonMark's paragraph content, a line end is whitespace, and
    whitespace is CommonMark's."""
    cases = [
        # B1: a container marker is not a neighbour; the content has a line end there.
        ('> Say "a\n> Live X.\n>" b\n', []),
        ('> Say “a\n> Live X.\n>” b\n', []),
        ('- > Say "a\n  > Live X.\n  >" b\n', []),
        ('> > Say "a\n> > Live X.\n>>" b\n', []),
        ('> Say "a\n> Live X.\n> " b\n', []),
        ('Say "a\nLive X.\n" b\n', []),
        # W: only Zs, tab, line feed, form feed and carriage return are whitespace.
        ('Say "a\nLive X.\nb"\x0bc now.\n', []),
        ('Say "a\nLive X.\nb"\u2028c now.\n', []),
        ('Say "a\nLive X.\nb"\u2029c now.\n', []),
        ('Say "a\nLive X.\nb"\x1cc now.\n', []),
        ('Say "a\nLive X.\nb"\x85c now.\n', []),
        # Controls: a content neighbour behind the marker, CommonMark whitespace, and ASCII and
        # non-ASCII punctuation and symbols (=, ©, an em dash).
        ('> Say "a\n> Live X.\n>b" c\n', ['"a\n> Live X.\n>b"']),
        ('> > Say "a\n> > Live X.\n>>b" c\n', ['"a\n> > Live X.\n>>b"']),
        ('Say "a\nLive X.\nb"\tc now.\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"\fc now.\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"\xa0c now.\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"\u3000c now.\n', ['"a\nLive X.\nb"']),
        ('Set ="a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Set "a\nLive X.\nb"= c\n', ['"a\nLive X.\nb"']),
        ('Set ©"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"— c\n', ['"a\nLive X.\nb"']),
        ('# A "b" ##\n', ['"b"']),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("template, line", [
    ('\x0b"Say\nLive {EX}.\nend" c\n', 2),
    ('\x1f"Say\nLive {EX}.\nend" c\n', 2),
    ('\u2028"Say\nLive {EX}.\nend" c\n', 2),
    ('\x85"Say\nLive {EX}.\nend" c\n', 2),
    ('a "Say\nLive {EX}.\nend"\x0b\n', 2),
    ('# \x0b"Live {EX}. b" c\n', 1),
    ('# a "Live {EX}. b"\x0b ##\n', 1),
    ('\x0b"Say\nLive {EX}.\nend" c\n===\n', 2),
    ('> \x0b"Say\n> Live {EX}.\n> end" c\n', 2),
], ids=["vt", "x1f", "u2028", "x85", "trailing_vt", "atx", "atx_closing_sequence", "setext", "blockquote"])
def test_a_character_markdown_it_strips_from_a_content_edge_is_the_quotes_neighbour(tmp_path, template, line):
    """Sprint-008 F011 review, iteration 8 (E1): markdown-it strips a paragraph's and a heading's content
    with Python's ``str.strip``, which also removes ``\\x0b``, ``\\x1c`` to ``\\x1f``, ``\\x85``, U+2028 and
    U+2029. CommonMark strips only spaces and tabs, so in ``\\x0b"Say`` the quote's neighbour is ``\\x0b``,
    neither whitespace nor punctuation: the quote is ambiguous and opens nothing. The gate read the content
    edge as whitespace, the quote opened, and the quotation sheltered the live line. A content edge's
    neighbour is now the character the strip removed next to it (``_source_recording``)."""
    _iteration_4_verdict(tmp_path, template, line)


@pytest.mark.parametrize("template", [
    'Say "a ![`"`](i.png) "b\nLive {EX}.\nc" d\n',
    'Say "a [![`"`](i.png)](u) "b\nLive {EX}.\nc" d\n',
    'Say "a ![![`"`](j.png)](i.png) "b\nLive {EX}.\nc" d\n',
    'Say "a ![[`"`](u)](i.png) "b\nLive {EX}.\nc" d\n',
], ids=["image", "image_in_link", "image_in_image", "link_in_image"])
def test_a_code_span_in_an_image_description_holds_its_quote(tmp_path, template):
    """Iteration 8 (I1): markdown-it parses an image description into the image token's own children, so a
    code span there was not among the inline token's code spans, and its quote was counted: three straight
    quotes became an even four and paired over the live line. CommonMark reads it as a code span, and the
    quote inside is no delimiter. Code spans are now gathered from image descriptions too, at any depth
    (``_image_code_spans``)."""
    _iteration_4_verdict(tmp_path, template, 2)


def test_an_escaped_quote_is_no_delimiter(tmp_path):
    """Iteration 8 (Q1): CommonMark 2.4 makes ``\\"`` a literal quote, never a delimiter, and it renders
    byte-identically to its unescaped twin. The gate read the backslash as the quote's punctuation
    neighbour, so ``x\\"a`` could open where ``x"a`` is intraword and ambiguous. A quote after an odd run
    of backslashes, outside a code span, now neither opens nor closes (``_quote_pairs``); it still counts
    toward the straight quotes' parity."""
    escaped, twin = 'Say x\\"a\nLive {EX}.\nb" c\n', 'Say x"a\nLive {EX}.\nb" c\n'
    assert MarkdownIt("commonmark").render(escaped) == MarkdownIt("commonmark").render(twin)
    _iteration_4_verdict(tmp_path, escaped, 2)
    _iteration_4_verdict(tmp_path, twin, 2)


def test_an_escaped_quote_closes_nothing(tmp_path):
    """Iteration 8 (Q1): the closing side. ``b\\" c`` closed the quotation over the live line, the backslash
    read as punctuation before a right-flanking quote."""
    _iteration_4_verdict(tmp_path, 'Say "a\nLive {EX}.\nb\\" c\n', 2)


def test_scanner_quote_spans_oracle_iteration_8():
    """Iteration 8's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: a content
    edge's neighbour is the character markdown-it's strip removed there; a code span in an image
    description holds its quote; an escaped quote is no delimiter but counts toward parity and stands
    between an opener and a later closer; and the categories iteration 7 named but no row pinned: ``$``
    (Sc), ``^`` (Sk), ``«`` and ``»`` (Pi, Pf), U+2009 (Zs) and a NUL, which markdown-it reads as U+FFFD
    (So)."""
    cases = [
        # E1: a character markdown-it strips from a content edge is the quote's neighbour.
        ('\x0b"Say\nLive X.\nend" c\n', []),
        ('\u2029"Say\nLive X.\nend" c\n', []),
        ('\x1c"Say\nLive X.\nend" c\n', []),
        ('a "Say\nLive X.\nend"\u2028\n', []),
        ('# a "Live X. b"\x0b\n', []),
        ('a "Say\nLive X.\nend"\x0b\n---\n', []),
        ('- \x0b"Say\n  Live X.\n  end" c\n', []),
        # Controls: the stripped character next to the content is whitespace, or nothing was stripped.
        ('\x0b "Say\nLive X.\nend" c\n', ['"Say\nLive X.\nend"']),
        ('a "Say\nLive X.\nend" \x0b\n', ['"Say\nLive X.\nend"']),
        ('\x0b\n"Say\nLive X.\nend" c\n', ['"Say\nLive X.\nend"']),
        ('  "Say\nLive X.\nend" c\n', ['"Say\nLive X.\nend"']),
        ('# a "Live X. b" ##\n', ['"Live X. b"']),
        # Not an edge: no closing sequence follows ``\x0b`` here, so it stays in the content.
        ('# a "Live X. b"\x0b#\n', []),
        # I1: a code span in an image description holds its quote.
        ('Say "a ![`"`](i.png) "b\nLive X.\nc" d\n', []),
        ('Say "a ![xy `"` z](i.png) "b\nLive X.\nc" d\n', []),
        ('Say ![`"`](i.png) "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Nested ![![`"`](j.png)](i.png) "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Say "a ![`x`"](i.png) "b\nLive X.\nc" d\n', ['"a ![`x`"', '"b\nLive X.\nc"']),
        # Controls: a code span in link text is the inline token's own; an image without one.
        ('Say "a [`"`](i.png) "b\nLive X.\nc" d\n', ['`"`']),
        ('Say ![q](i.png) "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # Q1: an escaped quote neither opens nor closes, counts toward parity, and blocks.
        ('Say x\\"a\nLive X.\nb" c\n', []),
        ('Say "a\nLive X.\nb\\" c\n', []),
        ('Say x![\\"a](i.png)\nLive X.\nb" c\n', []),
        ('Say "a \\"\nLive X.\nb" c\n', []),
        ('Say "a\nLive X.\nb" and \\"\n', []),
        ('Say "a \\" b\nLive X.\nc" d "e\n', []),
        # Controls: an even run of backslashes escapes none; a curly quote is not escapable; a code span.
        ('Say x\\\\"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Say x\\“a\nLive X.\nb” c\n', ['“a\nLive X.\nb”']),
        ('Say `\\"` "a\nLive X.\nb" c\n', ['`\\"`', '"a\nLive X.\nb"']),
        # A1: symbols and punctuation outside ASCII and the categories iteration 7 left unpinned.
        ('Set $"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Set ^"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Set «"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"» c\n', ['"a\nLive X.\nb"']),
        ('Say "a\nLive X.\nb"\u2009c now.\n', ['"a\nLive X.\nb"']),
        ('Set \0"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("template", [
    'a" <x y=\'\\"\'> c)"(\nLive {EX}.\n)" d\n',
    'a" <http://e.x/\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <!-- \\"( --> c)"(\nLive {EX}.\n)" d\n',
    'a" <?p \\"( ?> c)"(\nLive {EX}.\n)" d\n',
    'a" <![CDATA[ \\"( ]]> c)"(\nLive {EX}.\n)" d\n',
    'a" [<x y=\'\\"\'>](u) c)"(\nLive {EX}.\n)" d\n',
    'a" ![<http://e.x/\\"(>](i.png) c)"(\nLive {EX}.\n)" d\n',
    'a" ![![<!-- \\"( -->](j.png)](i.png) c)"(\nLive {EX}.\n)" d\n',
    # Controls: the unescaped twin in raw HTML, and plain prose.
    'a" <x y=\'"\'> c)"(\nLive {EX}.\n)" d\n',
    'Plain.\nLive {EX}.\n',
], ids=["tag_attribute", "autolink", "comment", "processing_instruction", "cdata", "tag_in_link_text",
        "autolink_in_image", "comment_in_nested_image", "unescaped_twin", "plain"])
def test_a_backslash_quote_in_raw_html_or_an_autolink_keeps_its_roles(tmp_path, template):
    """Sprint-008 F011 review, iteration 9 (R1): CommonMark 2.4 processes no backslash escape in raw HTML
    or an autolink, so a ``\\"`` there is no escaped quote: S3 reads it with its flanking roles, as it did
    before iteration 8. Iteration 8 read every quote after an odd backslash run outside code as literal,
    raw HTML and autolinks included; without its roles the quote no longer paired with the ``a"`` before
    it, and the ``"(`` after it opened and paired over the live line. Raw HTML and autolinks are now
    recorded where markdown-it reads them (``_raw_recording``), in image descriptions too
    (``_raw_ranges``), and an escape inside one is not read (``_quote_pairs``)."""
    _iteration_4_verdict(tmp_path, template, 2)


def test_scanner_quote_spans_oracle_iteration_9():
    """Iteration 9's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: a
    ``\\"`` in raw HTML or an autolink, at any depth of image descriptions, keeps its roles; a ``<...>``
    that is neither stays text, and so does a link destination or title, where CommonMark processes the
    escape; an escaped quote just before a tag is outside it. A2: the text escape ``x\\"b`` and its twin
    ``x"b`` render byte-identically and pair the same quotes, which 8e2a08e did not."""
    escaped, twin = 'a" x\\"b c)"(\nLive X.\n)" d\n', 'a" x"b c)"(\nLive X.\n)" d\n'
    assert MarkdownIt("commonmark").render(escaped) == MarkdownIt("commonmark").render(twin)
    over = ['"(\nLive X.\n)"']
    cases = [
        # R1: raw HTML (tag, comment, processing instruction, declaration, CDATA) and autolinks.
        ('a" <x y=\'\\"\'> c)"(\nLive X.\n)" d\n', ['"\'> c)"']),
        ('a" <http://e.x/\\"(> c)"(\nLive X.\n)" d\n', ['"(> c)"']),
        ('a" <!-- \\"( --> c)"(\nLive X.\n)" d\n', ['"( --> c)"']),
        ('a" <?p \\"( ?> c)"(\nLive X.\n)" d\n', ['"( ?> c)"']),
        ('a" <!X \\"( > c)"(\nLive X.\n)" d\n', ['"( > c)"']),
        ('a" <![CDATA[ \\"( ]]> c)"(\nLive X.\n)" d\n', ['"( ]]> c)"']),
        # R1 in link text and image descriptions, nested.
        ('a" [<x y=\'\\"\'>](u) c)"(\nLive X.\n)" d\n', ['"\'>](u) c)"']),
        ('a" ![<x y=\'\\"\'>](i.png) c)"(\nLive X.\n)" d\n', ['"\'>](i.png) c)"']),
        ('a" ![<http://e.x/\\"(>](i.png) c)"(\nLive X.\n)" d\n', ['"(>](i.png) c)"']),
        ('a" [a ![<!-- \\"( -->](i.png)](u) c)"(\nLive X.\n)" d\n', ['"( -->](i.png)](u) c)"']),
        ('a" ![![<!-- \\"( -->](j.png)](i.png) c)"(\nLive X.\n)" d\n', ['"( -->](j.png)](i.png) c)"']),
        # Controls: the unescaped twin; plain prose.
        ('a" <x y=\'"\'> c)"(\nLive X.\n)" d\n', ['"\'> c)"']),
        ('Plain.\nLive X.\n', []),
        # A2: the text escape and its twin pair the same quotes.
        (escaped, over),
        (twin, over),
        # Controls: neither an autolink nor a tag, so the escape is processed.
        ('a" <e.x/\\"(> c)"(\nLive X.\n)" d\n', over),
        ('a" <1x \\"( > c)"(\nLive X.\n)" d\n', over),
        # Controls: an escaped quote just before a tag or an autolink is outside it.
        ('a" x\\"<b> c)"(\nLive X.\n)" d\n', over),
        ('a" x\\"<http://e.x/> c)"(\nLive X.\n)" d\n', over),
        # Controls: next to a tag in an image description, placed at the description's offset at each depth.
        ('a" ![x\\"<b>](i.png) c)"(\nLive X.\n)" d\n', over),
        ('a" ![<b>\\"(](i.png) c)"(\nLive X.\n)" d\n', over),
        ('a" ![![<b>\\"(](j.png)](i.png) c)"(\nLive X.\n)" d\n', over),
        # Controls: CommonMark processes escapes in link destinations and titles.
        ('a" [t](u "\\"(") c)"(\nLive X.\n)" d\n', ['") c)"']),
        ('a" [t](u \'\\"(\') c)"(\nLive X.\n)" d\n', over),
        ('a" [t](u (\\"()) c)"(\nLive X.\n)" d\n', over),
        ('a" [t](u\\"() c)"(\nLive X.\n)" d\n', over),
        ('a" [t](<u\\"(>) c)"(\nLive X.\n)" d\n', over),
        ('a" ![t](u \'\\"(\') c)"(\nLive X.\n)" d\n', over),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("template", [
    'a" <javascript:x\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <VBScript:x\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <file:///x\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <data:text/html,\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <!-- \\"( a---> c)"(\nLive {EX}.\n)" d\n',
    'a" <!-- \\"( a----> c)"(\nLive {EX}.\n)" d\n',
    'a" <x y=q\x01 z=\'\\"(\'> c)"(\nLive {EX}.\n)" d\n',
    'Say "a [t](javascript:`"`) b\nLive {EX}.\nc" d\n',
    # Controls: an autolink and a comment markdown-it already read as CommonMark does.
    'a" <http://e.x/\\"(> c)"(\nLive {EX}.\n)" d\n',
    'a" <!-- \\"( a --> c)"(\nLive {EX}.\n)" d\n',
], ids=["javascript_autolink", "vbscript_autolink", "file_autolink", "data_autolink", "comment_ending_3_dashes",
        "comment_ending_4_dashes", "unquoted_value_with_a_control_character", "javascript_link_destination",
        "http_autolink", "comment"])
def test_raw_html_and_links_markdown_it_refuses_are_read_as_commonmark_reads_them(tmp_path, template):
    """Sprint-008 F011 review, iteration 10 (D1): markdown-it's ``validateLink`` refuses ``javascript:``,
    ``vbscript:``, ``file:`` and ``data:`` destinations, and its raw-HTML grammar refuses a comment ending
    ``--->`` and an unquoted attribute value holding ``\\x01`` to ``\\x1f``. CommonMark 0.31.2 reads an
    autolink, a comment and a tag there, where no escape is processed, so the ``\\"`` keeps its roles and
    pairs before the live line. The gate read text, made the ``\\"`` literal, and the ``"(`` after it opened
    over the live line; in a refused link the destination's code span held a quote CommonMark counts. The
    gate's parser now accepts every link destination and reads raw HTML by 0.31.2's grammar
    (``_commonmark_html_inline``)."""
    _iteration_4_verdict(tmp_path, template, 2)


@pytest.mark.parametrize("template", [
    '[r]: <javascript:x>\n"a\nLive {EX}.\nb"\n',
    '[r]: data:x\n"a\nLive {EX}.\nb"\n',
], ids=["javascript", "data"])
def test_a_link_reference_definition_markdown_it_refuses_holds_no_span(tmp_path, template):
    """Iteration 10 (D1): ``validateLink`` also gates markdown-it's link reference definitions. CommonMark
    reads ``[r]: <javascript:x>`` and its title on the next lines as a definition, which renders nothing and
    holds no span; markdown-it read a paragraph, and the title's quotes paired over the live line."""
    _iteration_4_verdict(tmp_path, template, 3)


@pytest.mark.parametrize("space", ["\x1f", "\xa0", "\x0c", "\x85", "\u2028", "\x0b"],
                         ids=["x1f", "nbsp", "form_feed", "x85", "u2028", "vertical_tab"])
def test_a_tag_spaced_by_what_commonmark_calls_no_whitespace_is_text(tmp_path, space):
    """Iteration 10 (D2): markdown-it's tag grammar takes Python's ``\\s`` between a tag's name and its
    attributes, where CommonMark 0.31.2 allows only spaces, tabs and at most one line ending. So
    ``<a\\x1fb='\\"'>`` was raw HTML to the gate and text to CommonMark: the gate kept the ``\\"``'s roles,
    and it paired over the live line, where CommonMark reads an escaped quote that pairs nothing."""
    _iteration_4_verdict(tmp_path, "Say <a" + space + "b='\\\"'>\nLive {EX}.\nc\" d\n", 2)


@pytest.mark.parametrize("template", [
    '<!x\nSay "a\nLive {EX}.\nb" c\n>\n',
    '<a b=q\x01>\nSay "a\nLive {EX}.\nb" c\n',
    # Control: markdown-it already reads an uppercase declaration as an HTML block.
    '<!X\nSay "a\nLive {EX}.\nb" c\n>\n',
], ids=["lowercase_declaration", "open_tag_with_a_control_character", "uppercase_declaration"])
def test_an_html_block_commonmark_starts_holds_no_span(tmp_path, template):
    """Iteration 10: markdown-it's HTML-block rule starts a declaration block only on ``<!`` and an uppercase
    letter, and reads a whole-line tag by its own tag grammar. CommonMark 0.31.2 starts one on any ASCII
    letter (type 4) and on a tag its grammar accepts (type 7), so these lines are an HTML block, which holds
    no span; markdown-it read a paragraph, and its quotes paired over the live line. The gate's parser now
    starts and ends HTML blocks by 0.31.2 (``_commonmark_html_block``)."""
    _iteration_4_verdict(tmp_path, template, 3)


def test_scanner_quote_spans_oracle_iteration_10():
    """Iteration 10's inputs as ``quote_spans`` oracle cases, "X" for the example, with controls: raw HTML,
    autolinks and HTML blocks are read by CommonMark 0.31.2's grammar, not markdown-it's, and every link
    destination is a link's. S1: a pending text token that ``push()`` flushes before the raw token is not
    the token the range is recorded on (``_raw_recording``); recorded there, an escape before it made
    ``text_join`` drop the range, and the ``"(`` opened over the live line."""
    over = ['"(\nLive X.\n)"']
    cases = [
        # S1: the text before a tag is flushed as its own token first.
        ('a" \\*x <x y=\'\\"\'> c)"(\nLive X.\n)" d\n', ['"\'> c)"']),
        # D1a: every autolink and link destination, and in an image description.
        ('a" <javascript:x\\"(> c)"(\nLive X.\n)" d\n', ['"(> c)"']),
        ('a" ![<data:x,\\"(>](i.png) c)"(\nLive X.\n)" d\n', ['"(>](i.png) c)"']),
        ('Say "a [t](javascript:`"`) b\nLive X.\nc" d\n', []),
        ('Say "a [t](http:`"`) b\nLive X.\nc" d\n', []),
        ('[r]: <javascript:x>\n"a\nLive X.\nb"\n', []),
        ('[r]: http:x\n"a\nLive X.\nb"\n', []),
        ('[r]: javascript:x\n"a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # D1b: a comment is any text up to the first -->, or <!--> or <!--->.
        ('a" <!-- \\"( a---> c)"(\nLive X.\n)" d\n', ['"( a---> c)"']),
        ('a" <!-- x ---> \\"( --> c)"(\nLive X.\n)" d\n', over),
        ('a" <!--> \\"( --> c)"(\nLive X.\n)" d\n', over),
        ('a" <!---> \\"( --> c)"(\nLive X.\n)" d\n', over),
        ('a" <!---- \\"( --> c)"(\nLive X.\n)" d\n', ['"( --> c)"']),
        # D2: tag whitespace is spaces, tabs and at most one line ending, wherever the grammar has it.
        ("Say <a\x1fb='\\\"'>\nLive X.\nc\" d\n", []),
        ("Say <a b\x0c='\\\"'>\nLive X.\nc\" d\n", []),
        ("Say <a b=\xa0'\\\"'>\nLive X.\nc\" d\n", []),
        ("Say <a b='\\\"'\x0c>\nLive X.\nc\" d\n", []),
        ('a" <x y=q\x01 z=\'\\"(\'> c)"(\nLive X.\n)" d\n', ['"(\'> c)"']),
        # An attribute needs whitespace before it; an unquoted value holds no quote and no space.
        ('a" <x:y=\'\\"(\'> c)"(\nLive X.\n)" d\n', over),
        ('a" <x y=q\\"(> c)"(\nLive X.\n)" d\n', over),
        ('a" <x y=a . z=\'\\"(\'> c)"(\nLive X.\n)" d\n', over),
        # Controls: a space, a tab, a line ending and a space-line-space are tag whitespace.
        ("Say <a b='\\\"'>\nLive X.\nc\" d\n", ['"\'>\nLive X.\nc"']),
        ("Say <a\tb='\\\"'>\nLive X.\nc\" d\n", ['"\'>\nLive X.\nc"']),
        ("Say <a\nb='\\\"'>\nLive X.\nc\" d\n", ['"\'>\nLive X.\nc"']),
        ("Say <a \n b = '\\\"' />\nLive X.\nc\" d\n", ['"\' />\nLive X.\nc"']),
        # HTML blocks: 0.31.2's start conditions, in both directions.
        ('<!x\nSay "a\nLive X.\nb" c\n>\n', []),
        ('<a b=q\x01>\nSay "a\nLive X.\nb" c\n', []),
        ('<div\x0c "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('<pre\x0c "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('<\u017ftyle "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('<pre/>\nSay "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('<a>\xa0\nSay "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # Controls: blocks markdown-it and 0.31.2 both start.
        ('<!X\nSay "a\nLive X.\nb" c\n>\n', []),
        ('<div "a\nLive X.\nb" c\n', []),
        ('<a b="c">\nSay "a\nLive X.\nb" c\n', []),
        ('<pre>\nSay "a\n\nLive X.\nb" c\n</pre>\n', []),
        ('<!-- a\nb --> "Live X." c\n', []),
        # Control: a whole-line tag (type 7) interrupts no paragraph, in markdown-it and 0.31.2 alike.
        ('Say "a\n<b>\nLive X.\nb" c\n', ['"a\n<b>\nLive X.\nb"']),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


def test_scanner_quote_spans_oracle_iteration_11():
    """Iteration 11's 17 rows: each 0.31.2 grammar clause and HTML-block condition the iteration-10 rows
    left unpinned, as ``quote_spans`` oracle cases. Every row agrees with oracle10h, which shares no code
    with the gate; each is red when the clause its comment names is broken (IDEA-106 item 2 counts them)."""
    over = ['"(\nLive X.\n)"']
    cases = [
        # A closing tag takes whitespace before its ">", so a whole line of one starts a type-7 block.
        ('</x >\nSay "a\nLive X.\nb" c\n', []),
        # A comment, a processing instruction and a CDATA section run over a line ending.
        ('a" <!-- x\n\\"( --> c)"(\nLive X.\n)" d\n', ['"( --> c)"']),
        ('a" <?p x\n\\"( ?> c)"(\nLive X.\n)" d\n', ['"( ?> c)"']),
        ('a" <![CDATA[ x\n\\"( ]]> c)"(\nLive X.\n)" d\n', ['"( ]]> c)"']),
        # An attribute name starts with a letter, "_" or ":", so a digit makes the tag text.
        ('a" <x 1y=\'\\"(\'> c)"(\nLive X.\n)" d\n', over),
        # An inline declaration starts on any ASCII letter.
        ('a" <!x \\"( > c)"(\nLive X.\n)" d\n', ['"( > c)"']),
        # Whitespace, and one line ending, stand on either side of "=".
        ('a" <x y = \'\\"(\'> c)"(\nLive X.\n)" d\n', ['"(\'> c)"']),
        ('a" <x y\n=\'\\"(\'> c)"(\nLive X.\n)" d\n', ['"(\'> c)"']),
        # A type-1 end folds ASCII case only, and is searched on the start line too.
        ('<style>\n</\u017ftyle>\n\nSay "a\nLive X.\nb" c\n', []),
        ('<style>\n</STYLE>\n\nSay "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        ('<style>a</style>\nSay "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # A type-2 end is searched on the start line too.
        ('<!-- a -->\nSay "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # A type-6 name folds ASCII case only.
        ('<d\u0131v "a\nLive X.\nb" c\n', ['"a\nLive X.\nb"']),
        # A type-7 tag may be followed by spaces or tabs.
        ('<a> \nSay "a\nLive X.\nb" c\n', []),
        # Types 2, 3 and 5 start a block.
        ('<!--\nSay "a\nLive X.\nb" c\n-->\n', []),
        ('<?p\nSay "a\nLive X.\nb" c\n?>\n', []),
        ('<![CDATA[\nSay "a\nLive X.\nb" c\n]]>\n', []),
    ]
    for raw, expected in cases:
        print(f"[slice compared] {raw!r} -> {_spans(raw)!r}")
        assert _spans(raw) == expected, raw


@pytest.mark.parametrize("raw", [
    "Say <a" + " word" * 200 + ", end\n",
    "<a" + " word" * 200 + "> x\n",
    "<a" + " " * 20000,
    "Say <a" + " " * 20000 + "b, end\n",
    "Say </a" + " " * 60000 + "b, end\n",
], ids=["attributes_inline", "attributes_html_block", "spaces_html_block", "spaces_inline", "spaces_closing_tag"])
def test_tag_shaped_prose_is_read_in_linear_time(raw):
    """Iteration 11 (S1): ``_HTML_SPACE`` read ``[ \\t]*\\n?[ \\t]*``, so one run of spaces split two ways, and
    ``<a`` followed by attribute-shaped words and no ``>`` backtracked 2^n (20 words took 2.5s, 30 about 40
    minutes); ``<a`` and 20000 spaces was quadratic, and ``</a`` and 60000 took about 2s. The inline rule
    and the type-7 block condition share it. ``quote_spans`` runs in a child process, killed at 60s so a
    blow-up cannot hang the suite, and must take under 0.5s."""
    import subprocess
    import sys

    here = Path(__file__).resolve()
    child = ("import sys, time\n"
             f"sys.path.insert(0, {str(here.parent)!r})\n"
             f"import {here.stem} as gate\n"
             "raw = sys.stdin.buffer.read().decode('utf-8')\n"
             "start = time.perf_counter()\n"
             "gate.quote_spans(raw)\n"
             "print(time.perf_counter() - start)\n")
    try:
        done = subprocess.run([sys.executable, "-c", child], input=raw.encode("utf-8"), capture_output=True,
                              cwd=here.parent, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        returncode, tail, elapsed = None, "killed at 60s", None
    else:
        # IDEA-106 item 1 (T229): a child that crashes shows its return code and stderr, not an IndexError.
        returncode = done.returncode
        tail = done.stderr.decode("utf-8", "replace")[-600:]
        printed = done.stdout.decode("utf-8").split()
        elapsed = float(printed[-1]) if returncode == 0 and printed else None
    print(f"[slice compared] {raw[:12]!r}... ({len(raw)} characters): quote_spans took {elapsed}s, "
          f"child returncode {returncode}")
    assert returncode == 0, f"quote_spans child exited with returncode {returncode}; stderr tail:\n{tail}"
    assert elapsed is not None and elapsed < 0.5


def _marked(marks):
    def decorate(func):
        for mark in marks:
            func = mark(func)
        return func
    return decorate


#: The site tasks ``OWNERSHIP`` names, as pending file names (S15, T223).
_PENDING_OWNERS = frozenset(f"{owner}.csv" for _path, _key, owner in OWNERSHIP)


def _pending_dir_marks(pending=None) -> tuple:
    """S15's end-state test: a strict xfail naming the site tasks whose pending files are still there,
    while every one of them is an ``OWNERSHIP`` owner; none once the directory is empty or absent, and
    none when a file's stem owns nothing (the test is then plainly red). ``pending`` is as
    ``read_pending()`` returns it, the checkout's when ``None``."""
    pending = read_pending() if pending is None else pending
    left = sorted(f"{task}.csv" for task in pending)
    if not left or not set(left) <= _PENDING_OWNERS:
        return ()
    return (pytest.mark.xfail(strict=True,
                              reason=f"{', '.join(name[:-4] for name in left)} delete their pending files (S15)"),)


@_marked(_pending_dir_marks())
def test_the_pending_directory_holds_no_csv():
    """S15's end state, enforced by the suite and not only by the demo probe: every site task has
    deleted its pending file, so ``research00_pending/`` holds no ``*.csv`` (an absent directory is the
    same state). A file left there would turn its covered rows into strict xfails. While a site task
    named in ``OWNERSHIP`` still has its file (T223 recreated the directory for T224 and T225), this test
    is a strict xfail naming it, as T218's census test was until T200 wrote the census; a file whose
    stem owns nothing keeps it red."""
    left = sorted(p.name for p in PENDING_DIR.glob("*.csv")) if PENDING_DIR.is_dir() else []
    print(f"[slice compared] {PENDING_DIR.name}/ is_dir={PENDING_DIR.is_dir()}: csv files {left}")
    assert left == []


def test_wrapped_comment_mutant_without_the_stripping_loses_the_py_hit(tmp_path, monkeypatch):
    """T219's mutant: with the marker pattern disabled, the ``.py`` world's split example is no hit."""
    monkeypatch.setitem(globals(), "_WRAPPED_COMMENT", re.compile(r"(?!)"))
    rel, hits = _wrapped_world(tmp_path, "py")
    print(f"[slice compared] mutant: {rel} hits {hits}")
    assert hits == []


def test_reinserting_an_example_inside_an_excepted_file_outside_its_excerpt_turns_the_gate_red(tmp_path):
    rel = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
    key = "C10-lone-candidate-never-struck"
    example = OLD_MEANINGS[key].example
    sheltered = f"# Sheltered for F009: {example}.\nVALUE = 1\n"
    exceptions = ((rel, f"Sheltered for F009: {example}", "F009"),)
    _plant(tmp_path, rel, sheltered)
    hits = scan(tmp_path, exceptions=exceptions)
    assert [(h.key, h.sheltered_by) for h in hits] == [(key, (0,))]
    assert gate_failures(hits, {}) == [] and exception_shelter_errors(hits, exceptions) == []
    _plant(tmp_path, rel, sheltered + f"\n# Reinserted later in the file: {example}.\n")
    hits = scan(tmp_path, exceptions=exceptions)
    red = gate_failures(hits, {})
    print(f"[slice compared] {[(h.line, h.sheltered_by) for h in hits]}; red {[(h.line, h.key) for h in red]}")
    assert [(h.line, h.key) for h in red] == [(4, key)]


def test_an_exception_that_shelters_nothing_turns_the_gate_red(tmp_path):
    key = "C05-gate02-worse-rate-reopens"
    example = OLD_MEANINGS[key].example
    _plant(tmp_path, "specification/spec/98-a.md", f"Prose: {example}.\n")
    _plant(tmp_path, "specification/spec/99-b.md", "Nothing old here.\n")
    cases = {
        "shelters its hit": (("specification/spec/98-a.md", example, "F010"),),
        "excerpt not in the file": (("specification/spec/98-a.md", "text this file lacks", "F010"),),
        "excerpt in another file": (("specification/spec/99-b.md", example, "F010"),),
    }
    verdicts = {}
    for name, exceptions in cases.items():
        hits = scan(tmp_path, exceptions=exceptions)
        verdicts[name] = (exception_shelter_errors(hits, exceptions), [h.ident for h in gate_failures(hits, {})])
    print(f"[slice compared] {verdicts}")
    assert verdicts["shelters its hit"] == ([], [])
    for name in ("excerpt not in the file", "excerpt in another file"):
        errors, red = verdicts[name]
        assert len(errors) == 1 and errors[0].startswith("EXCEPTIONS #0 shelters no hit")
        assert red == ["specification_spec_98_a_md__C05_gate02_worse_rate_reopens__1"]


# IDEA-106's gate items (T229; F012 AC6, D9). Each test was red on the unfixed gate.

def test_idea106_item1_the_timing_pin_fails_on_a_child_that_exits_non_zero(monkeypatch):
    """IDEA-106 item 1: the timing pin's child prints its elapsed time last, so a child that exits
    non-zero after printing must fail the pin with its return code and stderr tail in the message,
    not pass on the number it printed."""
    import subprocess

    stderr = b"Traceback (most recent call last):\n  File \"<string>\", line 6\nIndexError: list index out of range\n"

    def exited_non_zero(args, **_kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout=b"0.001\n", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", exited_non_zero)
    with pytest.raises(AssertionError) as info:
        test_tag_shaped_prose_is_read_in_linear_time("Say <a word, end\n")
    message = str(info.value)
    print(f"[slice compared] {message!r}")
    assert "returncode 1" in message and "IndexError: list index out of range" in message


def test_idea106_item2_the_iteration_11_oracle_rows_are_counted_and_each_comment_sits_over_its_rows():
    """IDEA-106 item 2: the iteration-11 oracle's docstring states its row count, which matches the
    rows; every row under the type-1 comment is a ``<style>`` block, and the ``<!-- a -->`` row (type 2)
    sits under a comment naming type 2."""
    import inspect

    source = inspect.getsource(test_scanner_quote_spans_oracle_iteration_11)
    body = re.search(r"cases = \[\n(.*?)\n    \]", source, re.S).group(1)
    groups: dict[str, list[str]] = {}
    comment = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            comment = line
        elif line.startswith("("):
            groups.setdefault(comment, []).append(line)
    rows = sum(len(v) for v in groups.values())
    stated = re.search(r"(?<![-\d])(\d+) rows", inspect.getdoc(test_scanner_quote_spans_oracle_iteration_11) or "")
    print(f"[slice compared] {rows} rows, docstring states {stated and stated.group(1)}; "
          f"groups {[(c, len(v)) for c, v in groups.items()]}")
    assert stated is not None and int(stated.group(1)) == rows
    type_1 = [c for c in groups if c and "type-1" in c]
    assert len(type_1) == 1 and all(r.startswith("('<style>") for r in groups[type_1[0]]), groups[type_1[0]]
    comment_row = [c for c, v in groups.items() if any(r.startswith("('<!-- a -->") for r in v)]
    assert len(comment_row) == 1 and "type-2" in comment_row[0], comment_row


def test_idea106_item4_markdown_it_py_is_bounded_below_five_and_the_installed_one_fits():
    """IDEA-106 item 4: this gate wraps markdown-it's private block and inline rule tables, so the
    package declares ``markdown-it-py>=4,<5`` (``runcoach-api/pyproject.toml``, the dev group); the
    installed version sits inside the bound."""
    import tomllib

    import markdown_it

    data = tomllib.loads((_REPO_ROOT / "runcoach-api" / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [d for d in data["dependency-groups"]["dev"] if d.startswith("markdown-it-py")]
    print(f"[slice compared] declared {declared}; installed markdown-it-py {markdown_it.__version__}")
    assert len(declared) == 1 and re.fullmatch(r"markdown-it-py>=4(\.0)?,<5", declared[0]), declared
    assert int(markdown_it.__version__.split(".")[0]) == 4


def test_idea106_item8_a_one_character_overlap_is_not_sheltered(tmp_path):
    """IDEA-106 item 8: an exception excerpt, and the census ``sheltered`` state, cover a hit only when
    the excerpt spans the whole hit; an excerpt that reaches one character into the hit leaves it live
    and the census row ``present``."""
    rel = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
    key = "C10-lone-candidate-never-struck"
    example = OLD_MEANINGS[key].example
    _plant(tmp_path, rel, f"# Lead-in text: {example}.\nVALUE = 1\n")
    text = gate_normal(rel, (tmp_path / rel).read_text(encoding="utf-8"))
    (hit,) = scan(tmp_path, exceptions=())
    excerpts = {"one-character overlap": text[:hit.start + 1], "whole hit": text[:hit.end]}
    row = {"key": key, "path": rel, "excerpt": hit.matched, "source": "grep"}
    verdicts = {}
    for name, excerpt in excerpts.items():
        exceptions = ((rel, excerpt, "F009"),)
        verdicts[name] = ([h.sheltered_by for h in scan(tmp_path, exceptions=exceptions)],
                          census_state(row, tmp_path, exceptions))
    print(f"[slice compared] hit [{hit.start}, {hit.end}) of {text!r}; {verdicts}")
    assert verdicts["whole hit"] == ([(0,)], "sheltered")
    assert verdicts["one-character overlap"] == ([()], "present")


def test_a_root_below_its_floor_turns_the_gate_red(tmp_path):
    names = {"spec_star": "specification/spec_{i}.md", "research": "specification/research/0{j}-planted.md",
             "src": "runcoach-api/src/f{i}.py"}
    for label, directory, *_rest in ROOTS:
        floor = dict(ROOT_FLOORS)[label]
        for i in range(floor):
            rel = names.get(label, f"{directory}/f{{i}}.md").format(i=i, j=i + 1)
            _plant(tmp_path, rel, "x\n")
    assert floor_shortfalls(live_files(tmp_path)) == []
    (tmp_path / ".claude/rules/f0.md").unlink()
    problems = floor_shortfalls(live_files(tmp_path))
    print(f"[slice compared] every root at its floor, then one rule file removed: {problems}")
    assert problems == [f"rules: {dict(ROOT_FLOORS)['rules'] - 1} live, floor {dict(ROOT_FLOORS)['rules']}"]


def test_pending_file_parsing_and_coverage(tmp_path):
    (tmp_path / "T900.csv").write_bytes(b"path,key\r\nspec/a.md,*\r\nspec\\b.md,C05-gate02-worse-rate-reopens\r\n")
    pending = read_pending(tmp_path)
    other = Hit("spec/b.md", "C03-return-is-free", 1, 1, 0, 1, "x", False, ())
    whole = Hit("spec/a.md", "C03-return-is-free", 1, 1, 0, 1, "x", False, ())
    print(f"[slice compared] {pending}")
    assert pending == {"T900": [("spec/a.md", "*"), (Path("spec\\b.md").as_posix(), "C05-gate02-worse-rate-reopens")]}
    assert read_pending(tmp_path / "absent") == {}
    assert pending_tasks(whole, pending) == ["T900"] and pending_tasks(other, pending) == []
    # A pended hit outside OWNERSHIP is still red: a hit whose path is not in the table fails.
    assert gate_failures([whole], pending) == [whole]
    assert gate_failures([whole], pending, ownership=(("spec/a.md", "*", "T900"),)) == []
    assert owners_of("specification/spec/03-derived-metric-formulas.md", "C03-return-is-free") == {"T202"}
    assert owners_of("specification/spec/03-derived-metric-formulas.md", "C19-hrv-04-reduced-confidence") == {
        "T213"}
    assert owners_of("specification/spec/04-physiological-state-model.md", "*") == {"T204"}
    assert owners_of("specification/spec/01-scope-inputs-pace-target.md", "*") == set()


def test_pending_named_key_row_goes_stale_while_another_key_still_hits_its_path(tmp_path):
    """S15 on spec/03's split (T199's table): T202 and T213 both pend named keys in one path. When
    T213's C19 site is fixed while T202's C03 site still hits, T213's row is stale -- a row matching on
    path alone would stay needed for as long as any other owner's key hits there."""
    spec03 = "specification/spec/03-derived-metric-formulas.md"
    c03, c19 = "C03-return-is-free", "C19-hrv-04-reduced-confidence"
    pending = {"T202": [(spec03, c03)], "T213": [(spec03, c19)], "T900": [(spec03, "*")]}
    _plant(tmp_path, spec03, f"Both: {OLD_MEANINGS[c03].example}.\n\nAnd: {OLD_MEANINGS[c19].example}.\n")
    both = scan(tmp_path)
    _plant(tmp_path, spec03, f"Only: {OLD_MEANINGS[c03].example}.\n\nThe C19 site is fixed.\n")
    fixed = scan(tmp_path)
    print(f"[slice compared] both {[(h.key, h.line) for h in both]}: stale {stale_pending_rows(both, pending)}; "
          f"C19 fixed {[(h.key, h.line) for h in fixed]}: stale {stale_pending_rows(fixed, pending)}")
    assert {h.key for h in both} == {c03, c19} and {h.key for h in fixed} == {c03}
    assert stale_pending_rows(both, pending) == []
    assert stale_pending_rows(fixed, pending) == [f"T213.csv: {spec03},{c19}"]


def test_no_live_hits_is_the_single_param_when_the_scan_finds_no_live_hit(tmp_path):
    """The end state: with no live hit the hit test still runs, as exactly one ``no_live_hits`` case
    carrying ``None`` and no mark, rather than an empty parametrization pytest would skip."""
    _plant(tmp_path, "specification/spec/99-clean.md", "Nothing old here.\n")
    quoted = Hit("specification/spec/99-clean.md", "C03-return-is-free", 1, 1, 0, 1, "x", True, ())
    cases = {"empty hit list": ([], {}), "clean tmp world": (scan(tmp_path), {}),
             "only a quoted hit": ([quoted], {})}
    shapes = {name: [(p.id, p.values, tuple(p.marks)) for p in _hit_params(hits, pending)]
              for name, (hits, pending) in cases.items()}
    live = Hit("specification/spec/99-clean.md", "C03-return-is-free", 1, 1, 0, 1, "x", False, ())
    with_live = [p.id for p in _hit_params([live], {})]
    print(f"[slice compared] {shapes}; one live hit gives {with_live}")
    for name, shape in shapes.items():
        assert shape == [("no_live_hits", (None,), ())], name
    assert with_live == [live.ident]


@pytest.mark.parametrize("body", [
    pytest.param(b"key,path\r\nspec/a.md,*\r\n", id="columns_swapped"),
    pytest.param(b"spec/a.md,*\r\n", id="no_header_row"),
    pytest.param(b"path,key,owner\r\nspec/a.md,*,T900\r\n", id="three_column_header"),
    pytest.param(b"", id="empty_file"),
])
def test_read_pending_rejects_a_file_without_the_path_key_header(tmp_path, body):
    (tmp_path / "T900.csv").write_bytes(body)
    with pytest.raises(AssertionError, match="T900.csv: header is not path,key") as raised:
        read_pending(tmp_path)
    print(f"[slice compared] {body!r} -> {raised.value}")


@pytest.mark.parametrize("body", [
    pytest.param(b"path,key\r\nspec/a.md\r\n", id="one_column_row"),
    pytest.param(b"path,key\r\nspec/a.md,*,extra\r\n", id="three_column_row"),
])
def test_read_pending_rejects_a_row_that_is_not_path_key(tmp_path, body):
    (tmp_path / "T900.csv").write_bytes(body)
    with pytest.raises(AssertionError, match="T900.csv: a row is not path,key") as raised:
        read_pending(tmp_path)
    print(f"[slice compared] {body!r} -> {raised.value}")


# --------------------------------------------------------------------------------------------------
# S4 and S11 (T218): narrowed patterns, and the F009 exceptions in hrv_trend.py.
# --------------------------------------------------------------------------------------------------

_HRV_TREND = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"


#: T218's C19-hrv-04 pattern, the "old" T243 re-narrowed from (IDEA-106 item 9).
_T218_C19_PATTERN = "rmssd(?:(?!\\. |;).)*?at reduced confidence|at reduced confidence(?:(?!\\. |;).)*?rmssd"


def test_narrowed_from_names_real_keys_whose_old_pattern_was_wider():
    """Each ``NARROWED_FROM`` entry names an ``OLD_MEANINGS`` key, each of its old patterns differs
    from the current one and from the others, and every one matches the key's example (a narrowing
    keeps F008's positive control). C19's olds are the bare phrase, then T218's pattern (T243)."""
    rows = {key: (olds, OLD_MEANINGS[key].pattern if key in OLD_MEANINGS else None)
            for key, olds in NARROWED_FROM.items()}
    print(f"[slice compared] NARROWED_FROM (olds, current): {rows}")
    assert sorted(NARROWED_FROM) == sorted(NARROWED_SITE_PATHS)
    for key, (olds, current) in rows.items():
        assert isinstance(olds, tuple) and olds and len(set(olds)) == len(olds), key
        example = normalize(OLD_MEANINGS[key].example)
        assert current is not None and re.search(current, example), key
        for old in olds:
            assert current != old and re.search(old, example), (key, old)
    assert NARROWED_FROM["C19-hrv-04-reduced-confidence"] == ("at reduced confidence", _T218_C19_PATTERN)


#: IDEA-106 item 9: the C19 wording T218's pattern missed, and T218's five correct-prose controls as
#: its commit (``78de5b4``) quoted them (spec/02:191, spec/03:81, spec/03:155, spec/04:123,
#: ``rr_reconstruction.py``:418), the clause boundary kept where the line has one.
_C19_RMSSD_FREE_WORDING = "numeric HRV tiers are admitted at reduced confidence"
_T218_C19_CONTROLS = (
    "An HRV figure from a low-valid-fraction series is carried at reduced confidence rather than presented as clean.",
    "A session with no usable altitude at all yields raw-pace-based features carried at reduced confidence",
    "the seed is flagged provisional, carried at reduced confidence",
    "when only one can be evaluated, it stands alone at reduced confidence.",
    "carries a low-valid-fraction series at reduced confidence rather than presenting it as clean; §3's raw-RR tier",
)


def test_idea106_item9_rmssd_free_wording_is_a_hit():
    """IDEA-106 item 9 (F012 AC6, D9): C19-hrv-04's current pattern hits the rmssd-free wording that
    T218's pattern let through, keeps F008's positive control, and still misses all five of T218's
    correct-prose sites; T218's pattern, kept as the second ``NARROWED_FROM`` old, misses the wording."""
    key = "C19-hrv-04-reduced-confidence"
    current = re.compile(OLD_MEANINGS[key].pattern)
    t218 = re.compile(_T218_C19_PATTERN)
    wording = normalize(_C19_RMSSD_FREE_WORDING)
    controls_hit = [c for c in _T218_C19_CONTROLS if current.search(normalize(c))]
    print(f"[slice compared] current {current.pattern!r}: wording hit {bool(current.search(wording))}, "
          f"example hit {bool(current.search(normalize(OLD_MEANINGS[key].example)))}, "
          f"controls hit {controls_hit}; T218 wording hit {bool(t218.search(wording))}")
    assert current.search(wording), "the rmssd-free wording escapes the current pattern"
    assert current.search(normalize(OLD_MEANINGS[key].example))
    assert controls_hit == []
    assert not t218.search(wording)
    assert not any(t218.search(normalize(c)) for c in _T218_C19_CONTROLS)


def test_narrowing_loses_exactly_the_named_correct_prose_sites():
    """T218's AC: every hit the narrowing lost is one of the five named sites, and it lost all five."""
    extras = narrowed_extras()
    for hit in extras:
        print(f"  lost {hit.key}: {hit.path}:{hit.line} {hit.matched!r}")
    got = {key: tuple(sorted(h.path for h in extras if h.key == key)) for key in NARROWED_FROM}
    print(f"[slice compared] lost paths per narrowed key {got} against NARROWED_SITE_PATHS")
    assert got == {key: tuple(sorted(paths)) for key, paths in NARROWED_SITE_PATHS.items()}


@pytest.mark.xfail(condition=not CENSUS_PATH.exists(), strict=True, reason="T200 writes the census")
def test_narrowed_patterns_extra_matches_are_the_census_narrowed_rows():
    """S11: re-run each old pattern over the live files; its extra matches are exactly the census's
    ``narrowed`` rows for that key."""
    extras = narrowed_extras()
    rows = read_census()
    errors = narrowed_census_errors(extras, rows)
    print(f"[slice compared] {len(extras)} extra matches against "
          f"{sum(r.get('source') == 'narrowed' for r in rows)} narrowed census rows: {errors}")
    assert errors == []


def test_narrowed_extras_and_census_rows_in_a_tmp_world(tmp_path):
    """The two helpers on a planted world: the old pattern's extra match is the correct-prose site
    only, and the census check reds on a missing row, a surplus row and an excerpt elsewhere."""
    key = "C19-hrv-04-reduced-confidence"
    old_site = f"Old: {OLD_MEANINGS[key].example}.\n"
    correct = "A seed is flagged provisional, carried at reduced confidence until history accrues.\n"
    _plant(tmp_path, "specification/spec/98-old.md", old_site)
    _plant(tmp_path, "specification/spec/99-correct.md", correct)
    extras = narrowed_extras(tmp_path)
    print(f"[slice compared] extras {[(h.path, h.line, h.matched) for h in extras]}")
    assert [(h.path, h.key) for h in extras] == [("specification/spec/99-correct.md", key)]
    row = {"key": key, "path": "specification/spec/99-correct.md",
           "excerpt": "carried at reduced confidence", "source": "narrowed"}
    other = {"key": key, "path": "specification/spec/98-old.md", "excerpt": "at reduced confidence",
             "source": "grep"}
    cases = {
        "exact": [row, other],
        "missing": [other],
        "surplus": [row, dict(row, path="specification/spec/98-old.md")],
        "excerpt elsewhere": [dict(row, excerpt="A seed is flagged provisional")],
    }
    verdicts = {name: narrowed_census_errors(extras, rows, repo_root=tmp_path) for name, rows in cases.items()}
    print(f"[slice compared] {verdicts}")
    assert verdicts["exact"] == []
    assert all(len(verdicts[name]) == 1 for name in ("missing", "excerpt elsewhere"))
    assert len(verdicts["surplus"]) == 2


def test_hrv_trend_py_yields_no_hits_and_the_f009_exceptions_are_gone():
    """S4 for F009 after T238 (F009 AC5): the single ``hrv_trend.py`` commit rewrote every sheltered
    site, so the real scan yields **zero** hits in the file and ``EXCEPTIONS`` is empty -- no F009
    triple remains, and F010 never held one here. Until T238 this test
    (``test_the_f009_exceptions_shelter_every_hrv_trend_hit_each_excerpt_once``) asserted the
    sheltered state: 11, then 12, triples each sheltering a hit and occurring once in the file."""
    exceptions = _OM.EXCEPTIONS
    f009 = [e for e in exceptions if e[2] == "F009"]
    hits = [h for h in real_scan() if h.path == _HRV_TREND]
    for hit in hits:
        print(f"  {hit.path}:{hit.line} {hit.key} {hit.matched!r} sheltered by {hit.sheltered_by}")
    print(f"[slice compared] {len(exceptions)} EXCEPTIONS triples ({len(f009)} F009); "
          f"{len(hits)} hrv_trend.py hits in the real scan")
    assert exceptions == ()
    assert hits == []


def test_narrowed_census_row_excerpt_must_overlap_an_extra_match_in_its_own_file(tmp_path):
    """``narrowed_census_errors`` credits a row only for an extra match in the row's own file. Two
    correct-prose files each hold one extra match, and the census names both; file X's row carries an
    excerpt of X's text that lies at exactly the normalized offsets of Y's extra match. Judged by offsets
    alone it would overlap Y's match and pass; judged in its own file it overlaps nothing."""
    key = "C19-hrv-04-reduced-confidence"
    correct = "carried at reduced confidence."
    x, y = "specification/spec/98-x.md", "specification/spec/99-y.md"
    filler = " ".join(f"w{i:02d}" for i in range(30))
    _plant(tmp_path, y, f"{filler} {correct}\n")
    _plant(tmp_path, x, f"{correct} {filler}\n")
    extras = narrowed_extras(tmp_path)
    at = {h.path: (h.start, h.end) for h in extras}
    x_text, y_text = (normalize((tmp_path / p).read_text(encoding="utf-8")) for p in (x, y))
    ys, ye = at[y]
    borrowed = x_text[ys:ye]
    print(f"[slice compared] extras {sorted(at.items())}; X's text at Y's match offsets: {borrowed!r}")
    assert sorted(at) == [x, y] and at[x][1] < ys and x_text.count(borrowed) == 1
    assert y_text[ys:ye] == "at reduced confidence"
    rows = [{"key": key, "path": y, "excerpt": correct, "source": "narrowed"},
            {"key": key, "path": x, "excerpt": borrowed, "source": "narrowed"}]
    errors = narrowed_census_errors(extras, rows, repo_root=tmp_path)
    print(f"[slice compared] {errors}")
    assert errors == [(f"{key}: narrowed row {x}: excerpt {borrowed!r} overlaps none of the old pattern's "
                       f"extra matches there")]
    rows[1] = dict(rows[1], excerpt=correct)
    assert narrowed_census_errors(extras, rows, repo_root=tmp_path) == []


# --------------------------------------------------------------------------------------------------
# S11 (T200): the census on the checkout, and its proof in a tmp_path world.
# --------------------------------------------------------------------------------------------------


def _census_site_params(marked: bool, rows=None, pending=None):
    """One param per non-``narrowed`` census row, with its S15 id. With ``marked``, a row a pending file
    covers under an ``OWNERSHIP`` owner is a strict xfail naming the covering task(s): its site is red
    until that task lands, and a fixed site under a pending row XPASSes (S15). With no census at all
    (before T200) a single ``no_census`` param reds."""
    rows = _read_census_or_empty() if rows is None else rows
    pending = read_pending() if pending is None else pending
    params = []
    for site in census_sites(rows, narrowed=False):
        marks = ()
        tasks = census_pending_tasks(site, pending)
        if marked and tasks and owners_of(site.path, site.key):
            verb = "fixes" if len(tasks) == 1 else "fix"
            marks = pytest.mark.xfail(strict=True, reason=f"{', '.join(tasks)} {verb} these sites")
        params.append(pytest.param(site, id=site.ident, marks=marks))
    return params or [pytest.param(None, id="no_census")]


def _narrowed_site_params(rows=None):
    rows = _read_census_or_empty() if rows is None else rows
    return [pytest.param(s, id=s.ident) for s in census_sites(rows, narrowed=True)] or [
        pytest.param(None, id="no_census")]


def test_census_covers_every_hit(capsys):
    """S11 as amended 2026-09-27: every site a pattern reaches has a ``grep`` row, and the ``grep`` rows
    number the hits plus the rows already cleared. Before the work that is ``a == b`` (``c == 0``). The
    count line goes to the terminal uncaptured as well, so a run records it whatever its ``-r`` flags
    (pytest keeps only the last ``-r``: ``-rP -rxX`` reports no passes)."""
    rows = read_census()
    a, b, c, uncovered = census_coverage(real_scan(), rows)
    line = f"grep-sourced rows: {a} hits: {b} cleared: {c}"
    with capsys.disabled():
        print(f"\n{line}")
    print(line)
    errors = census_balance_errors(a, b, c, uncovered)
    print(f"[slice compared] {len(rows)} census rows ({a} grep) against {b} unquoted hits of "
          f"{len(OLD_MEANINGS)} keys; uncovered {len(uncovered)}; errors {errors}")
    assert errors == []


def test_census_rows_are_well_formed():
    with CENSUS_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = reader.fieldnames
    errors = census_row_errors(rows, header)
    per_source = {s: sum(r.get("source") == s for r in rows) for s in CENSUS_SOURCES}
    print(f"[slice compared] {len(rows)} census rows {per_source}; errors {errors}")
    assert errors == []


def test_census_removed_rows_are_recorded_and_absent(capsys):
    """S11: a row cannot be dropped silently. Each removal is in ``CENSUS_REMOVED`` with its reason, printed
    to the terminal on every run, and no census row -- under any key -- still names its path and excerpt."""
    rows = read_census()
    present = {(Path(r["path"]).as_posix(), normalize(r["excerpt"])): r["key"] for r in rows}
    with capsys.disabled():
        print(f"\ncensus rows removed (CENSUS_REMOVED): {len(CENSUS_REMOVED)}")
        for key, path, excerpt, reason in CENSUS_REMOVED:
            print(f"  removed: {path} {key} {excerpt!r} -- {reason}")
    still = [(path, key, present[(path, normalize(excerpt))]) for key, path, excerpt, _reason in CENSUS_REMOVED
             if (path, normalize(excerpt)) in present]
    malformed = [(key, path) for key, path, excerpt, reason in CENSUS_REMOVED
                 if key not in OLD_MEANINGS or not normalize(excerpt) or not reason.strip()]
    print(f"[slice compared] {len(CENSUS_REMOVED)} removals against {len(rows)} census rows; "
          f"still present {still}; malformed {malformed}")
    assert still == [] and malformed == []


@pytest.mark.parametrize("site", _census_site_params(marked=False))
def test_census_row_file_exists(site):
    """S11 after the work: a census row cannot be dropped silently by deleting or renaming its file."""
    assert site is not None, "no census: T200 writes runcoach-api/tests/data/research00_census.csv"
    assert (_REPO_ROOT / site.path).is_file(), f"{site.path} (census row {site.key}) no longer exists"


@pytest.mark.parametrize("site", _census_site_params(marked=True))
def test_census_row_excerpt_is_gone_or_sheltered(site):
    """S11 after the work: each census site no longer states what it did -- its excerpt is gone, or an
    ``EXCEPTIONS`` excerpt shelters it (S4). A present excerpt is red; pending ones are expected red."""
    assert site is not None, "no census: T200 writes runcoach-api/tests/data/research00_census.csv"
    state = census_state(site.row())
    owners = sorted(owners_of(site.path, site.key))
    assert state in ("gone", "sheltered"), (
        f"{site.path} still states {site.key} ({site.source}): {site.excerpt!r} is {state}; "
        f"planned owner {owners or 'none: the path and key are outside OWNERSHIP'}")


@pytest.mark.parametrize("site", _narrowed_site_params())
def test_narrowed_row_is_still_present_and_unmatched(site):
    """S4 and S11: a ``narrowed`` row is correct prose -- it stays (its excerpt occurs once in its file),
    and the key's current pattern misses it (no hit of that key there overlaps it)."""
    assert site is not None, "no census: T200 writes runcoach-api/tests/data/research00_census.csv"
    errors, found, matched = narrowed_row_errors(site, real_scan())
    print(f"[slice compared] {site.path} {site.excerpt!r}: {len(found)} occurrence(s); current-pattern "
          f"hits over it {[(h.line, h.matched) for h in matched]}; errors {errors}")
    assert errors == []


def test_a_census_site_whose_text_is_still_present_turns_the_gate_red(tmp_path):
    """AC1's proof: in a planted world, a census site whose text is still there is red, whatever its
    source; fixed, it clears; an exception shelters it; a deleted file is not a cleared row. The count
    ``a == b + c`` holds before and after the fix, and fails when a hit has no ``grep`` row."""
    key = "C05-gate02-worse-rate-reopens"
    example = OLD_MEANINGS[key].example
    rel = "specification/spec/99-site.md"
    original = f"Loose: the deferred decision stays open here.\n\nGrep: {example}.\n"
    grep_row = {"key": key, "path": rel, "excerpt": example, "source": "grep"}
    loose_row = {"key": key, "path": rel, "excerpt": "the deferred decision stays open", "source": "loose"}
    rows = [grep_row, loose_row]
    _plant(tmp_path, rel, original)
    before = scan(tmp_path)
    states = {"before": [census_state(r, tmp_path, ()) for r in rows]}
    coverage = {"before": census_coverage(before, rows, tmp_path)[:3],
                "no grep row": census_coverage(before, [loose_row], tmp_path),
                "another key's row": census_coverage(before, [dict(grep_row, key="C03-return-is-free")], tmp_path)}
    states["sheltered"] = [census_state(r, tmp_path, ((rel, f"Grep: {example}", "F009"),)) for r in rows]
    _plant(tmp_path, rel, "Loose: the decision was taken.\n\nGrep: no hysteresis.\n")
    states["fixed"] = [census_state(r, tmp_path, ()) for r in rows]
    coverage["fixed"] = census_coverage(scan(tmp_path), rows, tmp_path)[:3]
    (tmp_path / rel).unlink()
    states["deleted"] = [census_state(r, tmp_path, ()) for r in rows]
    print(f"[slice compared] states {states}; coverage (a, b, c) {coverage}")
    assert states["before"] == ["present", "present"]
    assert states["sheltered"] == ["sheltered", "present"]
    assert states["fixed"] == ["gone", "gone"]
    assert states["deleted"] == ["missing", "missing"]
    assert coverage["before"] == (1, 1, 0) and coverage["fixed"] == (1, 0, 1)
    a, b, c, uncovered = coverage["no grep row"]
    assert (a, b, c) == (0, 1, 0) and [(h.path, h.key) for h in uncovered] == [(rel, key)]
    a, b, c, uncovered = coverage["another key's row"]
    assert (a, b, c) == (1, 1, 0) and [(h.path, h.key) for h in uncovered] == [(rel, key)]
    params = _census_site_params(marked=True, rows=rows, pending={"T900": [(rel, "*")]})
    assert [p.id for p in params] == [site_id(rel, key, 1), site_id(rel, key, 2)]
    assert all(not p.marks for p in params), "a path outside OWNERSHIP is never pending"
    assert stale_pending_rows([], {"T900": [(rel, key)]}, [(rel, key)]) == []
    assert stale_pending_rows([], {"T900": [(rel, key)]}, []) == [f"T900.csv: {rel},{key}"]


def test_census_site_params_mark_pending_rows_and_only_owned_ones():
    """S15 on census rows: an owned path under a pending row is a strict xfail naming its task; the
    same row unpended, or pended outside ``OWNERSHIP``, is a plain (red) test; the unmarked variant
    never marks; and no census at all is one ``no_census`` param."""
    spec03 = "specification/spec/03-derived-metric-formulas.md"
    rows = [{"key": "C12-same-baseline-window", "path": spec03, "excerpt": "x", "source": "inventory"},
            {"key": "C12-same-baseline-window", "path": "specification/spec/01-scope-inputs-pace-target.md",
             "excerpt": "y", "source": "loose"},
            {"key": "C19-hrv-04-reduced-confidence", "path": spec03, "excerpt": "z", "source": "narrowed"}]
    pending = {"T202": [(spec03, "C12-same-baseline-window")],
               "T900": [("specification/spec/01-scope-inputs-pace-target.md", "*")]}
    marked = _census_site_params(marked=True, rows=rows, pending=pending)
    shapes = [(p.id, [m.kwargs.get("reason") for m in p.marks]) for p in marked]
    print(f"[slice compared] {shapes}")
    assert shapes == [(site_id(spec03, "C12-same-baseline-window", 1), ["T202 fixes these sites"]),
                      (site_id("specification/spec/01-scope-inputs-pace-target.md", "C12-same-baseline-window", 1),
                       [])]
    assert all(m.kwargs.get("strict") for p in marked for m in p.marks)
    assert all(not p.marks for p in _census_site_params(marked=False, rows=rows, pending=pending))
    assert [p.id for p in _census_site_params(marked=True, rows=[], pending={})] == ["no_census"]
    assert [p.id for p in _narrowed_site_params(rows)] == [site_id(spec03, "C19-hrv-04-reduced-confidence", 1)]


def test_census_row_errors_catch_each_malformed_row(tmp_path):
    rel = "specification/spec/99-site.md"
    _plant(tmp_path, rel, "Once here. Twice. Twice.\n")
    for other in ("contracts/c.yaml",):
        _plant(tmp_path, other, "x: 1\n")
    good = {"key": "C03-return-is-free", "path": rel, "excerpt": "Once here", "source": "loose"}
    cases = {
        "good": ([good], CENSUS_HEADER),
        "bad header": ([good], ["key", "path", "source", "excerpt"]),
        "bad source": ([dict(good, source="manual")], CENSUS_HEADER),
        "bad key": ([dict(good, key="C99-nothing")], CENSUS_HEADER),
        "narrowed, key never narrowed": ([dict(good, source="narrowed")], CENSUS_HEADER),
        "empty excerpt": ([dict(good, excerpt="  ")], CENSUS_HEADER),
        "two-line excerpt": ([dict(good, excerpt="Once\nhere")], CENSUS_HEADER),
        "outside the roots": ([dict(good, path="runcoach-api/tests/test_x.py")], CENSUS_HEADER),
        "duplicate": ([good, dict(good, excerpt="once   HERE")], CENSUS_HEADER),
        "ambiguous excerpt": ([dict(good, excerpt="Twice")], CENSUS_HEADER),
    }
    verdicts = {name: census_row_errors(rows, header, tmp_path) for name, (rows, header) in cases.items()}
    print(f"[slice compared] {verdicts}")
    assert verdicts.pop("good") == []
    assert all(len(errors) == 1 for errors in verdicts.values()), verdicts


def test_census_sites_number_rows_per_path_and_key():
    """S15's ``__<n>`` counts the rows of one path *and key* from 1: a second key on the same path
    restarts at 1, and a third row of the first key continues its own count."""
    p, q = "specification/spec/03-derived-metric-formulas.md", "specification/spec/02-canonical-data-schema-ingestion.md"
    a, b = "C12-same-baseline-window", "C13-era-clip-becomes-hole-clip"
    rows = [{"key": a, "path": p, "excerpt": "x1", "source": "inventory"},
            {"key": b, "path": p, "excerpt": "y1", "source": "loose"},
            {"key": a, "path": p, "excerpt": "x2", "source": "loose"},
            {"key": a, "path": q, "excerpt": "x3", "source": "grep"},
            {"key": a, "path": p, "excerpt": "z", "source": "narrowed"}]
    sites = census_sites(rows, narrowed=False)
    got = [(s.path, s.key, s.n) for s in sites]
    print(f"[slice compared] {got}")
    assert got == [(p, a, 1), (p, b, 1), (p, a, 2), (q, a, 1)]
    assert [s.ident for s in sites] == [site_id(p, a, 1), site_id(p, b, 1), site_id(p, a, 2), site_id(q, a, 1)]
    assert [(s.key, s.n) for s in census_sites(rows, narrowed=True)] == [(a, 1)]


def test_census_balance_rejects_a_surplus_grep_row(tmp_path):
    """``a == b + c`` is an equality: two ``grep`` rows with different excerpts over one hit cover it, so
    nothing is uncovered, yet ``a`` (2) exceeds ``b + c`` (1) and the balance is red. One row balances;
    no row leaves the hit uncovered."""
    key = "C05-gate02-worse-rate-reopens"
    example = OLD_MEANINGS[key].example
    rel = "specification/spec/99-site.md"
    _plant(tmp_path, rel, f"Grep: {example}.\n")
    hits = [h for h in scan(tmp_path) if not h.quoted]
    row = {"key": key, "path": rel, "excerpt": example, "source": "grep"}
    cases = {"one row": [row], "two rows over one hit": [row, dict(row, excerpt=f"Grep: {example}")], "no row": []}
    coverage = {name: census_coverage(hits, rows, tmp_path) for name, rows in cases.items()}
    verdicts = {name: census_balance_errors(*cov) for name, cov in coverage.items()}
    print(f"[slice compared] hits {[(h.key, h.matched) for h in hits]}; (a, b, c) "
          f"{ {name: cov[:3] for name, cov in coverage.items()} }; verdicts {verdicts}")
    assert [(h.path, h.key) for h in hits] == [(rel, key)]
    assert coverage["one row"][:3] == (1, 1, 0) and verdicts["one row"] == []
    assert coverage["two rows over one hit"][:3] == (2, 1, 0) and coverage["two rows over one hit"][3] == []
    assert verdicts["two rows over one hit"] == ["grep-sourced rows 2 != hits 1 + cleared 0"]
    assert coverage["no row"][:3] == (0, 1, 0) and len(verdicts["no row"]) == 2


def test_narrowed_row_is_red_only_under_its_own_key_current_pattern(tmp_path):
    """The narrowed-row check in a planted world. Correct prose the current C19 pattern misses is green.
    Text the current C19 pattern still matches is red -- the "current pattern misses it" half can fail.
    Text only another key's pattern (C05) matches is green: that hit is not the narrowing's. The
    correct prose is spec/03:155's CTL seed (T218's control); a "tier" in the sentence is C19's wording
    since T243, so the planted sentence names the seed, not a tier."""
    c19, c05 = "C19-hrv-04-reduced-confidence", "C05-gate02-worse-rate-reopens"
    rel = "specification/spec/99-site.md"
    correct = "carried at reduced confidence until it is established"
    _plant(tmp_path, rel, (f"Correct: the seed is {correct}.\n\nOld: {OLD_MEANINGS[c19].example}.\n\n"
                           f"Other: {OLD_MEANINGS[c05].example}.\n"))
    hits = scan(tmp_path)
    sites = {"correct prose": CensusSite(c19, rel, correct, "narrowed", 1),
             "still matched": CensusSite(c19, rel, OLD_MEANINGS[c19].example, "narrowed", 2),
             "another key's text": CensusSite(c19, rel, OLD_MEANINGS[c05].example, "narrowed", 3),
             "file gone": CensusSite(c19, "specification/spec/98-gone.md", correct, "narrowed", 1)}
    verdicts = {name: narrowed_row_errors(site, hits, tmp_path)[0] for name, site in sites.items()}
    print(f"[slice compared] hits {[(h.key, h.matched) for h in hits]}; verdicts {verdicts}")
    assert sorted({h.key for h in hits}) == [c05, c19]
    assert verdicts["correct prose"] == []
    assert len(verdicts["still matched"]) == 1 and "still matches" in verdicts["still matched"][0]
    assert verdicts["another key's text"] == []
    assert verdicts["file gone"] == ["specification/spec/98-gone.md: the narrowed row's file is gone"]


# --------------------------------------------------------------------------------------------------
# F012 AC1 (T223): the four keys of D6-D8, each proved on a planted world.
# --------------------------------------------------------------------------------------------------

#: Each T223 key with the correct prose its pattern must miss (T223's negative controls): HRV-42's
#: current rule line (T-16); PRIN-12's own "from its response" and the sibling key's example, which the
#: D7 family must not reach; spec/03:247's narrow "whose verdict is withheld because a returning
#: dataset's judged week is entirely later" and HRV-31's rule line, both naming
#: ``week_not_representative`` in the sentence; and the rule file's ladder line as 8646fd0 fixed it.
F012_KEY_CONTROLS = MappingProxyType({
    "HRV-42-R13-reset-in-force-persists-through-it": (
        "What an empty judged week holds MUST neither create, move nor end a reported reset (T-16).",
    ),
    "PRIN-12-R13-reproducible-by-hand-without-exceptions": (
        "Every derived verdict MUST be reproducible by hand from its response, whose `thresholds` block "
        "MUST serve `baseline_days`, and every unserved verdict-affecting input IS an OPEN exception.",
        "§1.6, the response stays reproducible by hand",
    ),
    "HRV-31-R13-broad-withhold-of-any-verdict": (
        "a dataset that **is** selected whose verdict is **withheld** because a returning dataset's judged "
        "week is entirely later than its own (`week_not_representative`, `research/00` §5.4 (v)); and a "
        "day that has not yet happened (`day_not_happened`).",
        "A per-tier dataset's verdict MUST be withheld (`week_not_representative`) when another dataset "
        "that could not have been selected holds at least `min_window_readings` later judged-week days.",
    ),
    "T-27-ladder-order-for-the-loop": (
        "This is the fixed precedence order the ladder applies when signals from the five timescale loops "
        "(ARCH-03) conflict; do not reorder or shortcut it.",
    ),
})


@pytest.mark.parametrize(("key", "suffix"), [
    pytest.param(key, suffix, id=f"{key}-{suffix}") for key in F012_KEY_CONTROLS for suffix in ("md", "py")
])
def test_f012_key_example_planted_in_a_tmp_world_is_a_hit(tmp_path, key, suffix):
    """T223: each new key's verbatim example, planted in a ``.md`` and in a ``.py`` file under a root, is
    one live hit of that key and nothing else in the world."""
    rel = _plant_path(key, suffix)
    _plant(tmp_path, rel, _PLANT_BODY[suffix].format(example=OLD_MEANINGS[key].example))
    hits = scan(tmp_path, old_meanings={key: OLD_MEANINGS[key]})
    print(f"[slice compared] {rel} holding {key}'s example: "
          f"{[(h.path, h.line, h.matched[:70], h.live) for h in hits]}")
    assert [(h.path, h.key, h.live) for h in hits] == [(rel, key, True)]


@pytest.mark.parametrize(("key", "control"), [
    pytest.param(key, control, id=f"{key}-{i}")
    for key, controls in F012_KEY_CONTROLS.items() for i, control in enumerate(controls, start=1)
])
def test_f012_key_negative_control_planted_in_a_tmp_world_is_no_hit(tmp_path, key, control):
    """T223: the correct prose each key must miss, planted in a ``.md`` and a ``.py`` file, gives no hit
    of that key at all -- not a quoted one either -- while the same world with the example in place of
    the control does (the control's counterpart, so the world itself is not what keeps it green)."""
    for suffix in ("md", "py"):
        _plant(tmp_path, _plant_path(key, suffix), _PLANT_BODY[suffix].format(example=control))
    hits = scan(tmp_path, old_meanings={key: OLD_MEANINGS[key]})
    print(f"[slice compared] {key} over {control[:70]!r}: hits {[(h.path, h.matched[:70]) for h in hits]}")
    assert hits == []
    for suffix in ("md", "py"):
        _plant(tmp_path, _plant_path(key, suffix), _PLANT_BODY[suffix].format(example=OLD_MEANINGS[key].example))
    assert len(scan(tmp_path, old_meanings={key: OLD_MEANINGS[key]})) == 2


# --------------------------------------------------------------------------------------------------
# S6 and S9 (T201): presence rows, decisions/01's conformance and the no_regression comment.
# --------------------------------------------------------------------------------------------------

_TRACEABILITY = Path(__file__).parent / "test_research00_traceability.py"
_OPERATIVE_CACHE: dict[str, dict] = {}


def operative() -> dict:
    """F008 AC7's ``OPERATIVE`` (``{decision: (rule id, strings)}``), read from the traceability test by
    path once per session. It is bound there and is not moved or copied here (S6: only F008's strings)."""
    if "OPERATIVE" not in _OPERATIVE_CACHE:
        _OPERATIVE_CACHE["OPERATIVE"] = _load_module("research00_traceability_operative", _TRACEABILITY).OPERATIVE
    return _OPERATIVE_CACHE["OPERATIVE"]


def operative_key(key: str, operative_map) -> str | None:
    """The ``OPERATIVE`` decision an ``OLD_MEANINGS`` key's ``decision`` cites, or ``None``."""
    cited = [c for c in re.findall(r"\bC\d{2}\b", OLD_MEANINGS[key].decision) if c in operative_map]
    return cited[0] if cited else None


#: S6: each census site with an AC7 string, as ``(path, census key, census excerpt, anchor)``. The block is
#: the blank-line paragraph (``.md`` only) holding the anchor, which occurs exactly once in the file after
#: ``normalize()``. The anchor is a label of that paragraph the site's fix leaves standing, not the
#: census excerpt, which the fix removes. Several census rows in one paragraph share one anchor and so one
#: row per string. Ordered as the rows are numbered (``__<n>`` per path and decision).
PRESENCE_ANCHORS = (
    (".claude/rules/project-domain-and-spec-fidelity.md", "C32-band-without-floor",
     "±0.5·SD(ln rMSSD) smallest-worthwhile-change (SWC) band", "**HRV trend**"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C04-hole-at-least",
     "capture hole of at least `GAP_RESET_DAYS", "**AC17 —"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C05-gate02-worse-rate-reopens",
     "a worse rate triggers the deferred hysteresis decision", "**AC23 —"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C01-withhold-not-judgeable-only",
     "not** judgeable but holds at least `MIN_WINDOW_READINGS", "**AC24 —"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C02-withhold-against-selected",
     "later than every judged-week day of the selected dataset", "**AC24 —"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06-hrv-25-accepted-cost",
     "Accepted because quality-first promotes", "| cost | direction and why it is accepted |"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "PRIN-15-C06-accepted-as-priced",
     "Accepted because quality-first promotes the *best available* instrument",
     "| cost | direction and why it is accepted |"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06-gate01-one-exception",
     "save AC21's one counted exception", "| cost | direction and why it is accepted |"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06-gate01-one-exception",
     "except the one deferred rate carried as a named, counted exception", "**AC21 —"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C05-gate02-worse-rate-reopens",
     "conditional`): `research/00` still says a worse rate *reopens* the decision and does not",
     "**The withhold is retained, not retired (T158, AC24).**"),
    ("spec-mirror/references/F006-dataset-model.md", "C06-hrv-25-accepted-cost",
     "Accepted because quality-first promotes", "| cost | direction and why it is accepted |"),
    ("spec-mirror/references/F006-dataset-model.md", "PRIN-15-C06-accepted-as-priced",
     "Accepted because quality-first promotes the *best available* instrument",
     "| cost | direction and why it is accepted |"),
    ("spec-mirror/references/F006-dataset-model.md", "C06-gate01-one-exception",
     "save AC21's one counted exception", "| cost | direction and why it is accepted |"),
    ("spec-mirror/references/F006-dataset-model.md", "C05-gate02-worse-rate-reopens",
     "Explicit hysteresis deferred**, now with a trigger it can actually fire",
     "**Dataset key is the tier, not the device**"),
    ("spec-mirror/references/F006-dataset-model.md", "C04-hole-at-least",
     "at an internal hole of at least `GAP_RESET_DAYS", "**AC17 split into two mechanisms**"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C32-band-without-floor",
     "the SWC band the trend already uses — ±0.5·SD(ln rMSSD), the sample SD",
     "**Fidelity and the anti-mixing rule.**"),
    ("specification/spec/03-derived-metric-formulas.md", "PRIN-14-C07-weak-evidence-only",
     "up-regulation on weak evidence, which §1.7 forbids",
     "**Either position on a baseline that is not yet established**"),
    ("specification/spec/03-derived-metric-formulas.md", "PRIN-14-C07-weak-evidence-only",
     "reading a genuinely suppressed week as normal (up-regulation on weak evidence",
     "**Per-source baseline discipline (the anti-mixing rule).**"),
    ("specification/spec/03-derived-metric-formulas.md", "PRIN-14-C07-weak-evidence-only",
     "on both sides — up-regulation on weak evidence, which `research/00` §1.7 forbids",
     "**Per-source baseline discipline (the anti-mixing rule).**"),
    ("specification/spec/03-derived-metric-formulas.md", "C04-hole-at-least",
     "an internal capture hole of at least `gap_reset_days` (`research/00`",
     "**Graceful degradation across tiers, then unavailable.**"),
    ("specification/spec/03-derived-metric-formulas.md", "C32-band-without-floor",
     "±0.5·SD(ln rMSSD) smallest-worthwhile-change band",
     "Section 3 delivers the system's own transparent metric layer"),
    ("specification/spec/06-adaptation-logic.md", "C32-band-without-floor",
     "±0.5·SD(ln rMSSD) smallest-worthwhile-change band", "**Morning HRV verdict**"),
    ("specification/spec/06-adaptation-logic.md", "PRIN-14-C07-weak-evidence-only",
     "would be up-regulation on weak evidence, the one direction `research/00` §1.7 forbids",
     "The gate may **down-regulate freely"),
    ("specification/spec_outline.md", "C32-band-without-floor",
     "±0.5·SD(ln rMSSD) smallest-worthwhile-change band", "**Defines, each formula stated in full.**"),
    ("specification/research/05-data-to-adaptation.md", "C32-band-without-floor",
     "(default ± 0.5 × SD(ln rMSSD), the sample standard deviation", "**Morning ln rMSSD trend**"),
    ("specification/research/05-data-to-adaptation.md", "C32-band-without-floor",
     "Default:* ± 0.5·SD(ln rMSSD) — the sample SD", "**HRV-guided training rule — ADOPT.**"),
    # F011 review iteration 12: rows added by the iteration's MANUAL_ROWS.
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C02-withhold-against-selected",
     "judged-week days every one later than the selected dataset's",
     "**The withhold is retained, not retired (T158, AC24).**"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06-gate01-one-exception",
     "carried as the release gate's one named, counted and conditioned exception",
     "**The withhold is retained, not retired (T158, AC24).**"),
    ("specification/spec/03-derived-metric-formulas.md", "PRIN-15-C06-accepted-as-priced",
     "The accepted cost and the residual at one and two mornings back, which no measured form closes, are "
     "priced in F005's Negative Class",
     "**A judged week that is not a fair sample of the tier being judged**"),
    ("specification/spec/03-derived-metric-formulas.md", "C01-withhold-not-judgeable-only",
     'word the set as "not judgeable" alone; the skipped arm is T125\'s own returning strap, judgeable and '
     "skipped, and that wording gap is open as [[IDEA-083]]",
     "**A judged week that is not a fair sample of the tier being judged**"),
    # T231 (F012 AC6, IDEA-106 item 23a): the "measured/swept and priced" sites now name HRV-25's population.
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "PRIN-15-C06-accepted-as-priced",
     "the §1.7 promotion exposure is measured and priced", "**AC22 —"),
    ("spec-mirror/references/F006-dataset-model.md", "PRIN-15-C06-accepted-as-priced",
     "AC20 requires it swept and priced before release", "**This rate is newly measurable.**"),
)

_C33_REASON = ("C33's prose and its EXCEPTIONS are F010's, not F011's (F011 Not in scope); this site is "
               "hrv_trend.py (F009's, sheltered by EXCEPTIONS) and a .py block, which T201 does not build")
#: T240 (F009 AC7): the Pinned string OPERATIVE["C06"] carries, after F009 replaced 'none (F009)'.
_HRV25_PIN = ("Pinned: runcoach-api/tests/test_hrv25_population.py::"
              "test_hrv_25_population_is_counted_and_does_not_grow")
_PINNED_REASON = ("a Pinned line is research/00's own rule-block field, checker metadata naming the test "
                  "node that pins HRV-25's population (T240); no downstream prose restates it, so the row "
                  "could never pass")
_T125_RESIDUAL_REASON = ("T125's residual ships inside one PRIN-15 exception, the F005-parity population "
                         "(IDEA-087), which the site names; the other two exceptions are not this bullet's")
_ONE_EXCEPTION_REASON = ("T231: the site states HRV-25's population alone (HRV-25, IDEA-099, 'may not grow'); "
                         "the other two PRIN-15 exceptions are AC21's and the cost table's, whose rows carry them")
_CONTRACT_REASON = ("a .yaml or .py description, which T201 does not build a block for; the census row's absence "
                    "check covers the site, whose fix names HRV-25, PRIN-15 and IDEA-099")

#: S6: what gets no presence row, as ``(path, decision, locator, string, reason)``. With string ``*`` the
#: locator is a census excerpt and the whole census row is dropped; otherwise the locator is an anchor of
#: ``PRESENCE_ANCHORS`` and only that string's row under it is dropped.
DROPPED_PRESENCE_ROWS = (
    ("runcoach-api/src/runcoach_api/metrics/hrv_trend.py", "C33",
     "``D-7``. The constant is not published in ``thresholds`` ([[IDEA-070]], 2026-09-15).", "*", _C33_REASON),
    ("runcoach-api/src/runcoach_api/metrics/hrv_trend.py", "C33",
     "constant is not published in ``thresholds`` ([[IDEA-070]], 2026-09-15). 2.", "*", _C33_REASON),
    ("runcoach-api/src/runcoach_api/metrics/hrv_trend.py", "C33",
     "``D-7``. The constant is not published in ``thresholds`` ([[IDEA-070]],", "*", _C33_REASON),
    ("runcoach-api/src/runcoach_api/metrics/hrv_trend.py", "C33",
     "struck. The constant is not published in ``thresholds`` ([[IDEA-070]],", "*", _C33_REASON),
    ("runcoach-api/tests/test_hrv_no_regression_gate.py", "C05",
     'still reads "a worse rate **reopens** the deferred hysteresis decision",', "*",
     "S6 gives this .py test comment its own absence row instead (test_no_regression_comment_drops_reopen)"),
    ("specification/spec/03-derived-metric-formulas.md", "C04",
     "an internal capture hole of at least `gap_reset_days` —", "*",
     "S6: the site's paragraph (:238, 13.4k characters) already says 'more than' twice at HEAD, so the row "
     "could not be red; the census row's absence check covers the site"),
    ("specification/spec/03-derived-metric-formulas.md", "C32",
     "a Section 3 heuristic default like every other constant here", "*",
     "S6: the site's paragraph (:240) already states max(0.5 · SD(ln rMSSD), 0.01) at HEAD"),
    ("specification/spec/03-derived-metric-formulas.md", "C38",
     "they are left standing as the history of the reported reset", "*",
     "DOC-09's strings ('only current rules', 'dated summary') govern research/00's own form; spec/03's "
     "corrected history sentence has no reason to state them"),
    ("specification/spec/06-adaptation-logic.md", "C06",
     "or when the state estimate is low-confidence — the more conservative reading wins", "*",
     "the key's old meaning is PRIN-05's unscoped conservative-wins; its correct statement is PRIN-05's "
     "scope (HRV-14's fidelity rank decides between datasets), which carries none of PRIN-15's strings"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "| cost | direction and why it is accepted |",
     "DEFERRED_EXCEPTION", "S6: already in the Negative Class table at HEAD"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "| cost | direction and why it is accepted |",
     "IDEA-087", "S6: already in the Negative Class table at HEAD"),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "| cost | direction and why it is accepted |",
     _HRV25_PIN, _PINNED_REASON),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "**AC21 —", _HRV25_PIN,
     _PINNED_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     "DEFERRED_EXCEPTION", "S6: already in the cost table at HEAD"),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     "IDEA-087", "S6: already in the cost table at HEAD"),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     _HRV25_PIN, _PINNED_REASON),
    # F011 review iteration 12.
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06",
     "**The withhold is retained, not retired (T158, AC24).**", _HRV25_PIN, _PINNED_REASON),
    ("specification/spec/03-derived-metric-formulas.md", "C06",
     "**A judged week that is not a fair sample of the tier being judged**", "DEFERRED_EXCEPTION",
     _T125_RESIDUAL_REASON),
    ("specification/spec/03-derived-metric-formulas.md", "C06",
     "**A judged week that is not a fair sample of the tier being judged**", "HRV-25", _T125_RESIDUAL_REASON),
    ("specification/spec/03-derived-metric-formulas.md", "C06",
     "**A judged week that is not a fair sample of the tier being judged**", "IDEA-099", _T125_RESIDUAL_REASON),
    ("specification/spec/03-derived-metric-formulas.md", "C06",
     "**A judged week that is not a fair sample of the tier being judged**", _HRV25_PIN,
     _PINNED_REASON),
    ("contracts/openapi.yaml", "C06", "This is the response's one report of the exposure F006 accepts", "*",
     _CONTRACT_REASON),
    ("contracts/openapi.yaml", "C06",
     "forbidden direction, accepted and measured against shipped F005 rather than denied", "*", _CONTRACT_REASON),
    ("runcoach-api/src/runcoach_api/schemas.py", "C06",
     "This is the response's one report of the exposure F006 accepts", "*", _CONTRACT_REASON),
    ("runcoach-api/src/runcoach_api/schemas.py", "C06",
     "forbidden direction, accepted, measured against shipped F005 rather than denied", "*", _CONTRACT_REASON),
    # T231 (IDEA-106 item 23a): the two "priced" sites.
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "**AC22 —", "F005-parity", _ONE_EXCEPTION_REASON),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "**AC22 —", "DEFERRED_EXCEPTION",
     _ONE_EXCEPTION_REASON),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "**AC22 —", "IDEA-087", _ONE_EXCEPTION_REASON),
    ("spec-mirror/features/F006-per-tier-hrv-datasets.md", "C06", "**AC22 —", _HRV25_PIN, _PINNED_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "**This rate is newly measurable.**", "F005-parity",
     _ONE_EXCEPTION_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "**This rate is newly measurable.**",
     "DEFERRED_EXCEPTION", _ONE_EXCEPTION_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "**This rate is newly measurable.**", "IDEA-087",
     _ONE_EXCEPTION_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "**This rate is newly measurable.**",
     _HRV25_PIN, _PINNED_REASON),
)


@dataclass(frozen=True)
class PresenceRow:
    """One S6 row: the paragraph holding ``anchor`` in ``path`` states ``string`` after ``normalize()``.
    ``key`` is the ``OPERATIVE`` decision; ``census_keys`` are the census rows' keys it stands for."""
    path: str
    key: str
    anchor: str
    string: str
    census_keys: tuple[str, ...]
    n: int

    @property
    def ident(self) -> str:
        return site_id(self.path, self.key, self.n)


def presence_plan(rows, operative_map, anchors=PRESENCE_ANCHORS, dropped=DROPPED_PRESENCE_ROWS):
    """``(presence rows, errors)``. Every non-``narrowed`` census row whose key cites an ``OPERATIVE``
    decision is anchored or dropped whole, never both and never neither; every anchor and drop entry is
    used; and only ``.md`` paths get a row."""
    errors: list[str] = []
    ops = {}
    for site in census_sites(rows, narrowed=False):
        op = operative_key(site.key, operative_map)
        if op is not None:
            ops[(site.path, site.key, normalize(site.excerpt))] = op
    whole = {(p, d, normalize(loc)) for p, d, loc, s, _r in dropped if s == "*"}
    strings = {(p, d, normalize(loc), normalize(s)) for p, d, loc, s, _r in dropped if s != "*"}
    used = set()
    anchored = set()
    groups: dict[tuple[str, str, str], list[str]] = {}
    for path, key, excerpt, anchor in anchors:
        ident = (path, key, normalize(excerpt))
        if ident not in ops:
            errors.append(f"anchor entry {path} {key} {excerpt!r}: no census row cites an OPERATIVE decision")
            continue
        anchored.add(ident)
        if not path.endswith(".md"):
            errors.append(f"{path} {key}: a presence row needs a .md block (T201 returns BLOCKED)")
            continue
        keys = groups.setdefault((path, ops[ident], anchor), [])
        if key not in keys:
            keys.append(key)
    for (path, key, excerpt), op in ops.items():
        if (path, op, excerpt) in whole:
            used.add(("*", path, op, excerpt))
            if (path, key, excerpt) in anchored:
                errors.append(f"{path} {key} {excerpt!r}: both anchored and dropped")
        elif (path, key, excerpt) not in anchored:
            errors.append(f"{path} {key} ({op}) {excerpt!r}: census site with no presence row and no drop")
    result: list[PresenceRow] = []
    counts: dict[tuple[str, str], int] = {}
    for (path, op, anchor), keys in groups.items():
        for string in operative_map[op][1]:
            if (path, op, normalize(anchor), normalize(string)) in strings:
                used.add((normalize(string), path, op, normalize(anchor)))
                continue
            counts[(path, op)] = counts.get((path, op), 0) + 1
            result.append(PresenceRow(path, op, anchor, string, tuple(keys), counts[(path, op)]))
    for p, d, loc, s, _r in dropped:
        mark = ("*" if s == "*" else normalize(s), p, d, normalize(loc))
        if mark not in used:
            errors.append(f"DROPPED_PRESENCE_ROWS {p} {d} {loc!r} {s!r}: drops nothing")
    return result, errors


def presence_block(path: str, anchor: str, repo_root: Path = _REPO_ROOT) -> tuple[str, str | None]:
    """``(state, block)``: the raw blank-line paragraph holding ``anchor``'s one occurrence (after
    ``normalize()``), or a state naming why there is none -- ``no file``, ``anchor missing`` or
    ``anchor ambiguous``."""
    full = repo_root / path
    if not full.is_file():
        return "no file", None
    raw = full.read_text(encoding="utf-8")
    text, offsets = gate_text(path, raw)
    found = [m.start() for m in re.finditer(re.escape(normalize(anchor)), text)]
    if len(found) != 1:
        return ("anchor missing" if not found else "anchor ambiguous"), None
    at = offsets[found[0]]
    for a, b in _paragraphs(raw, _fenced_blocks(raw)):
        if a <= at < b:
            return "ok", raw[a:b]
    return "anchor missing", None


def presence_state(row: PresenceRow, repo_root: Path = _REPO_ROOT) -> str:
    """``present`` when the anchor's block states the row's string after ``normalize()``; ``absent`` when
    it does not; otherwise ``presence_block``'s state."""
    state, block = presence_block(row.path, row.anchor, repo_root)
    if block is None:
        return state
    return "present" if normalize(row.string) in normalize(block) else "absent"


def _pending_marks(pairs, pending=None) -> tuple:
    """S15: a strict xfail naming the task(s) whose pending file covers one of ``pairs`` (``(path,
    census key)``) under an ``OWNERSHIP`` owner; none otherwise."""
    pending = read_pending() if pending is None else pending
    tasks = sorted({task for path, key in pairs for task, rows in pending.items()
                    if ((path, key) in rows or (path, "*") in rows) and owners_of(path, key)})
    if not tasks:
        return ()
    verb = "fixes" if len(tasks) == 1 else "fix"
    return (pytest.mark.xfail(strict=True, reason=f"{', '.join(tasks)} {verb} these sites"),)


def _presence_params(rows=None, pending=None):
    rows = _read_census_or_empty() if rows is None else rows
    plan, _errors = presence_plan(rows, operative())
    return [pytest.param(row, id=row.ident, marks=_pending_marks([(row.path, k) for k in row.census_keys], pending))
            for row in plan] or [pytest.param(None, id="no_presence_rows")]


def red_presence_sites(rows) -> list[tuple[str, str]]:
    """``(path, census key)`` of every presence row not yet ``present`` (S15's stale-row check)."""
    plan, _errors = presence_plan(rows, operative())
    return [(r.path, k) for r in plan if presence_state(r) != "present" for k in r.census_keys]


@pytest.mark.parametrize("row", _presence_params())
def test_presence_row(row):
    """F011 AC2 and S6: the site's block states its rule's F008 AC7 operative string after ``normalize()``.
    Each row is red (a strict xfail under its site task's pending file) until that site is edited."""
    assert row is not None, "no presence rows: the census or PRESENCE_ANCHORS is empty"
    state, block = presence_block(row.path, row.anchor)
    head = normalize(block)[:80] if block else None
    print(f"[slice compared] {row.path} block at {row.anchor!r} ({state}; {len(block or '')} chars, "
          f"starts {head!r}) for {row.string!r}")
    assert presence_state(row) == "present", (
        f"{row.path}: the block at {row.anchor!r} does not state {row.key}'s {row.string!r} ({state}); "
        f"census rows {list(row.census_keys)}, planned owner {sorted(owners_of(row.path, row.census_keys[0]))}")


def test_presence_rows_account_for_every_operative_census_site():
    """S6: each census site whose key cites an ``OPERATIVE`` decision has rows or a reasoned drop; only
    ``.md`` blocks are built; and the decisions with rows are the ones T201 names (C33 and C38 are
    dropped with reasons)."""
    rows = read_census()
    operative_map = operative()
    plan, errors = presence_plan(rows, operative_map)
    for row in plan:
        print(f"  {row.ident}: {row.string!r} at {row.anchor!r} for {list(row.census_keys)}")
    for p, d, loc, s, reason in DROPPED_PRESENCE_ROWS:
        print(f"  dropped: {p} {d} {s!r} at {loc!r} -- {reason}")
    keys = sorted({r.key for r in plan})
    print(f"[slice compared] OPERATIVE {sorted(operative_map)}; {len(plan)} presence rows over {keys}; "
          f"{len(DROPPED_PRESENCE_ROWS)} drops; errors {errors}")
    assert errors == []
    assert keys == ["C01", "C02", "C04", "C05", "C06", "C07", "C32"]
    assert all(r.path.endswith(".md") for r in plan)


def test_presence_anchors_resolve_to_one_paragraph_each():
    """Each anchor occurs exactly once in its file and lies in a paragraph, before and after its site's
    edit: an anchor lost to an edit would otherwise hide inside a strict xfail as a red row."""
    states = {(p, a): presence_block(p, a)[0] for p, _k, _e, a in PRESENCE_ANCHORS}
    print(f"[slice compared] {len(states)} anchors: {sorted(set(states.values()))}; "
          f"not ok {[k for k, s in states.items() if s != 'ok']}")
    assert all(s == "ok" for s in states.values())


def test_presence_row_is_red_without_its_string_in_the_anchor_block(tmp_path):
    """S6 in a planted world: the string outside the anchor's paragraph is red; inside it, whatever its
    case and wrapping, it is green; a lost or doubled anchor is red and says so."""
    rel = "specification/spec/99-site.md"
    row = PresenceRow(rel, "C05", "**AC23 —", "MUST NOT add hysteresis", ("C05-gate02-worse-rate-reopens",), 1)
    cases = {
        "elsewhere": "**AC23 — flip rate.** A worse rate triggers the deferred decision.\n\nThe system MUST NOT add hysteresis.\n",
        "in block": "**AC23 — flip rate.** The system must not add\r\nhysteresis to selection.\r\n\r\nOther.\r\n",
        "no anchor": "**AC22 — flip rate.** The system MUST NOT add hysteresis.\n",
        "two anchors": "**AC23 — a.** MUST NOT add hysteresis.\n\n**AC23 — b.**\n",
    }
    verdicts = {}
    (tmp_path / rel).parent.mkdir(parents=True)
    for name, body in cases.items():
        (tmp_path / rel).write_bytes(body.encode("utf-8"))  # bytes as written: the CRLF case stays CRLF
        verdicts[name] = presence_state(row, tmp_path)
    print(f"[slice compared] {verdicts}")
    assert verdicts == {"elsewhere": "absent", "in block": "present", "no anchor": "anchor missing",
                        "two anchors": "anchor ambiguous"}


def test_presence_plan_rejects_an_unaccounted_site_a_dead_entry_and_a_py_block():
    """``presence_plan`` on a census of its own: a site with no anchor and no drop, an anchor naming no
    census row, a drop that drops nothing and a ``.py`` anchor each produce one error."""
    op = {"C05": ("GATE-02", ("MUST NOT add hysteresis",))}
    key = "C05-gate02-worse-rate-reopens"
    md, py = "specification/spec/99-a.md", "runcoach-api/src/runcoach_api/planted.py"
    rows = [{"key": key, "path": md, "excerpt": "x one", "source": "loose"},
            {"key": key, "path": py, "excerpt": "x two", "source": "loose"}]
    good_anchors = ((md, key, "x one", "**A —"),)
    good_drops = ((py, "C05", "x two", "*", "a .py site"),)
    cases = {
        "good": (good_anchors, good_drops),
        "unaccounted": (good_anchors, ()),
        "dead anchor": (good_anchors + ((md, key, "x three", "**B —"),), good_drops),
        "dead drop": (good_anchors, good_drops + ((md, "C05", "**A —", "other", "r"),)),
        "py block": (good_anchors + ((py, key, "x two", "# A"),), ()),
    }
    verdicts = {name: presence_plan(rows, op, a, d) for name, (a, d) in cases.items()}
    print(f"[slice compared] {[(n, [r.ident for r in v[0]], v[1]) for n, v in verdicts.items()]}")
    plan, errors = verdicts.pop("good")
    assert errors == [] and [r.ident for r in plan] == [site_id(md, "C05", 1)]
    assert all(len(errors) == 1 for _plan, errors in verdicts.values()), verdicts


_DECISIONS_01 = "specification/decisions/01-"


def _decisions_01_pairs():
    return [(Path(r["path"]).as_posix(), r["key"]) for r in _read_census_or_empty()
            if r["path"].startswith(_DECISIONS_01)]


@_marked(_pending_marks(_decisions_01_pairs()))
def test_decisions_01_conforms():
    """S9 as amended: AC1's sweep restricted to decisions/01 finds no live hit, and every decisions/01
    census row is cleared (zero hits alone holds today and proves nothing: its sites are manual rows)."""
    files = [p for p in live_files(_REPO_ROOT)["decisions"] if p.startswith(_DECISIONS_01)]
    hits = [h for h in real_scan() if h.path.startswith(_DECISIONS_01) and h.live]
    sites = [s for s in census_sites(read_census(), narrowed=False) if s.path.startswith(_DECISIONS_01)]
    states = {s.ident: census_state(s.row()) for s in sites}
    print(f"[slice compared] {files}: live hits {[h.ident for h in hits]}; census rows {states}")
    assert files and sites
    assert hits == []
    assert all(state in ("gone", "sheltered") for state in states.values()), states


_NO_REGRESSION = "runcoach-api/tests/test_hrv_no_regression_gate.py"
#: The comment block S6 names is the ``#`` run directly above this assignment (it was :267 at HEAD).
_NO_REGRESSION_ANCHOR = "AC23_METRIC = "
_REOPEN = re.compile(r"re-?\s*open", re.IGNORECASE)


def comment_block_above(raw: str, anchor: str) -> str | None:
    """The contiguous ``#`` lines directly above the first line that starts with ``anchor``, with their
    comment markers removed and joined by ``normalize()``; ``None`` without the anchor or the comment."""
    lines = raw.splitlines()
    at = next((i for i, line in enumerate(lines) if line.startswith(anchor)), None)
    if at is None:
        return None
    start = at
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    if start == at:
        return None
    return normalize(_WRAPPED_COMMENT.sub("", "\n".join(lines[start:at])))


@_marked(_pending_marks([(_NO_REGRESSION, "C05-gate02-worse-rate-reopens")]))
def test_no_regression_comment_drops_reopen():
    """S6 and AC2: the no_regression gate's AC23 comment no longer says the decision "reopens" -- no
    ``re-?open`` in any case, so "re-opens" and "reopen" are red too."""
    block = comment_block_above((_REPO_ROOT / _NO_REGRESSION).read_text(encoding="utf-8"), _NO_REGRESSION_ANCHOR)
    found = [m.group(0) for m in _REOPEN.finditer(block or "")]
    print(f"[slice compared] {_NO_REGRESSION} comment above {_NO_REGRESSION_ANCHOR!r}: "
          f"{len(block or '')} chars; re-?open matches {found}")
    assert block, f"no comment block above {_NO_REGRESSION_ANCHOR!r} in {_NO_REGRESSION}"
    assert found == []


def test_no_regression_comment_matcher_catches_each_spelling():
    """The absence row's matcher: every spelling of "reopen" inside the block is red, a wrapped one too;
    the same word below the anchor, or a comment with none, is not."""
    def matches(comment: str) -> list[str]:
        raw = f"X = 1\n\n{comment}\n{_NO_REGRESSION_ANCHOR}\"walk_flips\"\n# reopens, below the anchor\n"
        return [m.group(0) for m in _REOPEN.finditer(comment_block_above(raw, _NO_REGRESSION_ANCHOR) or "")]
    cases = {"reopens": "#: a worse rate reopens it", "Re-opens": "#: a worse rate Re-opens it",
             "REOPEN": "# REOPEN", "wrapped": "#: a worse rate re-\n#: opens it",
             "clean": "#: the system MUST NOT add hysteresis"}
    verdicts = {name: matches(comment) for name, comment in cases.items()}
    print(f"[slice compared] {verdicts}")
    assert verdicts.pop("clean") == []
    assert all(len(found) == 1 for found in verdicts.values()), verdicts
    assert comment_block_above("X = 1\nAC23_METRIC = 1\n", _NO_REGRESSION_ANCHOR) is None


# --- AC3 (T215): the research/00 chain's IDEAs reach their end states ------------------------------
#
# The one part of this gate that reads the data dir: the IDEA files live only there. The rows are the
# F008 decisions reference's "IDEA end states" table as S7 amends it, frozen here as literals. Where
# the reference names an IDEA S7 does not (088, 102), the reference's row is taken as written. 070 is
# not a row: F010 AC4 owns its end state.


@dataclass(frozen=True)
class IdeaEndState:
    idea: str
    status: str
    #: A phrase a dated note in the file must name, or None where the row asserts the status only.
    note: str | None
    why: str


IDEA_END_STATES = (
    IdeaEndState("IDEA-048", "resolved", "F008", "resolved -> F008 (C19)"),
    IdeaEndState("IDEA-079", "resolved", "F011", "fixed by F011 (T208, AC4's 'earliest')"),
    IdeaEndState("IDEA-083", "resolved", "F008", "resolved -> F008 (C01/C02)"),
    IdeaEndState("IDEA-085", "resolved", "F008", "resolved -> F008 (C04)"),
    IdeaEndState("IDEA-087", "open", "R5", "also owns the F005-parity population (C06, R5)"),
    IdeaEndState("IDEA-088", "open", None, "reference row: T-25 stays undefined until decided"),
    IdeaEndState("IDEA-089", "open", "(a) resolved → F008 C05; (b) open", "conditional -> open (S7)"),
    IdeaEndState("IDEA-090", "resolved", "F011", "fixed by F011 (T202, spec/03 §3.7.3)"),
    IdeaEndState("IDEA-092", "open", "F011", "owns the two C09 questions (S7)"),
    IdeaEndState("IDEA-093", "open", None, "owns C10's 22-28-day trailing-silence regime (S7)"),
    IdeaEndState("IDEA-095", "resolved", "F009",
                 "resolved -> F009 (T238: option 2, the docstring cross-reference; S7)"),
    IdeaEndState("IDEA-099", "open", None, "owns HRV-25's population; F009 counts it (S7)"),
    IdeaEndState("IDEA-102", "open", "F011", "reference row: owns every unserved verdict-affecting input"),
    IdeaEndState("IDEA-103", "resolved", "F012", "owned by F012 (S7 as amended 2026-09-27); open -> resolved (T228)"),
    IdeaEndState("IDEA-106", "resolved", "T243",
                 "F012 AC6: every item fixed or ruled won't-fix (D9); item 9, the last open one, closes with T243"),
)

#: The reason every ``test_idea_end_state`` row skips with where the data dir is absent (AC3, AC5).
IDEA_SKIP_REASON = "AC3's IDEA end states live only in the Shipyard data dir, which is unreachable"
_NOTE_DATE = re.compile(r"2026-(?:09-2\d|10-\d\d)")
_STATUS = re.compile(r"status:\s*[\"']?([A-Za-z_-]+)")


def _mirror_data_dir() -> Path | None:
    """The data dir exactly as ``test_normative_mirror._data_dir()`` finds it (``SHIPYARD_DATA_DIR``,
    else the ``.shipyard`` breadcrumb), so the two data-dir gates cannot disagree about where it is."""
    return _load_module("normative_mirror", Path(__file__).parent / "test_normative_mirror.py")._data_dir()


def _ideas_dir_or_skip() -> Path:
    data_dir = _mirror_data_dir()
    if data_dir is None:
        pytest.skip(f"{IDEA_SKIP_REASON}: neither $SHIPYARD_DATA_DIR nor {_REPO_ROOT / '.shipyard'} "
                    f"names a directory with spec/features under it")
    return data_dir / "spec" / "ideas"


def idea_end_state_errors(ideas_dir: Path, row: IdeaEndState) -> list[str]:
    """Why ``row``'s IDEA file is not at its end state: the frontmatter ``status`` differs, or no line
    dated ``2026-09-2x`` or ``2026-10-xx`` (T238 widened it: F009's notes are October's) names the
    row's note phrase (as a whole word). Empty when it is."""
    files = sorted(ideas_dir.glob(f"{row.idea}-*.md"))
    if len(files) != 1:
        return [f"{row.idea}: {len(files)} files match {row.idea}-*.md in {ideas_dir}"]
    lines = files[0].read_text(encoding="utf-8").splitlines()
    closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if not lines or lines[0].strip() != "---" or closing is None:
        return [f"{row.idea}: no frontmatter"]
    statuses = [m.group(1) for line in lines[1:closing] if (m := _STATUS.fullmatch(line.strip()))]
    errors = []
    if statuses != [row.status]:
        errors.append(f"{row.idea}: frontmatter status {statuses}, row expects {row.status!r}")
    if row.note is not None:
        phrase = re.compile(rf"(?<!\w){re.escape(row.note)}(?!\w)")
        if not any(_NOTE_DATE.search(line) and phrase.search(line) for line in lines):
            errors.append(f"{row.idea}: no line dated 2026-09-2x or 2026-10-xx names {row.note!r}")
    return errors


@pytest.mark.parametrize("row", IDEA_END_STATES, ids=[row.idea.replace("-", "_") for row in IDEA_END_STATES])
def test_idea_end_state(row):
    """F011 AC3 and S7: the IDEA's frontmatter ``status`` is its row's, and a dated note names the row's
    phrase. Skips, with ``IDEA_SKIP_REASON``, only when the data dir is absent."""
    ideas_dir = _ideas_dir_or_skip()
    errors = idea_end_state_errors(ideas_dir, row)
    path = next(iter(sorted(ideas_dir.glob(f"{row.idea}-*.md"))), None)
    status = [line for line in (path.read_text(encoding="utf-8").splitlines() if path else [])
              if line.startswith("status:")]
    print(f"[slice compared] {path}: {status} vs {row.status!r}; note {row.note!r} ({row.why})")
    assert errors == []


def test_idea_end_state_check_fails_a_wrong_status_and_a_missing_note(tmp_path):
    """The check's negative class: a wrong status, a status only in the body, an undated note, a note
    naming a longer token, a missing file, a file with no frontmatter. The right file passes."""
    row = IdeaEndState("IDEA-900", "resolved", "F011", "fixture")
    good = "---\nid: IDEA-900\nstatus: resolved\n---\n\n**Resolved 2026-09-28 (F011, T215).**\n"
    cases = {
        "good": good,
        "good, CRLF": good.replace("\n", "\r\n"),
        "wrong status": good.replace("status: resolved", "status: open"),
        "status in body only": good.replace("status: resolved\n", "") + "status: resolved\n",
        "undated note": good.replace("2026-09-28", "later"),
        "note names F0110": good.replace("F011,", "F0110,"),
        "note dated a month early": good.replace("2026-09-28", "2026-08-28"),
        "no frontmatter": good.replace("---\n", "", 1),
    }
    verdicts = {}
    for name, text in cases.items():
        ideas = tmp_path / slug(name)
        ideas.mkdir()
        (ideas / "IDEA-900-fixture.md").write_bytes(text.encode("utf-8"))
        verdicts[name] = idea_end_state_errors(ideas, row)
    (tmp_path / "empty").mkdir()
    verdicts["missing file"] = idea_end_state_errors(tmp_path / "empty", row)
    print(f"[slice compared] {verdicts}")
    assert verdicts.pop("good") == [] and verdicts.pop("good, CRLF") == []
    assert all(len(errors) == 1 for errors in verdicts.values()), verdicts


def _copy_of_the_repo(dest: Path) -> Path:
    """The working tree's tracked and unignored files, so no ``.shipyard`` breadcrumb, copied to ``dest``."""
    import shutil
    import subprocess

    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=_REPO_ROOT,
                            capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    for rel in filter(None, listed):
        source = _REPO_ROOT / rel
        if source.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / rel)
    return dest


#: The reason S15 puts on a pending strict xfail: ``_hit_params``, ``_census_site_params``,
#: ``_pending_marks`` and ``_pending_dir_marks`` write nothing else.
_PENDING_XFAIL_REASON = re.compile(
    r"T\d+(?:, T\d+)* (?:fixes these sites|fix these sites|delete their pending files \(S15\))")


def pending_xfail_case_ids(pending=None) -> set[str]:
    """The junit ``name`` of every case this module marks as a strict xfail under S15's pending files
    (``pending`` as ``read_pending()`` returns it, the checkout's when ``None``): the hit, census-row and
    presence rows a pending file covers under an ``OWNERSHIP`` owner, the two module-level tests under
    ``_pending_marks``, and the end-state test while every pending file's stem is an owner. Each comes
    from the parametrizer its test uses, with the same ``pending``, so nothing is listed by hand and the
    set is empty once the pending directory is (S15's end state)."""
    pending = read_pending() if pending is None else pending
    names: set[str] = set()
    for test, params in ((test_live_hit_states_an_old_meaning, _hit_params(pending=pending)),
                         (test_census_row_excerpt_is_gone_or_sheltered,
                          _census_site_params(marked=True, pending=pending)),
                         (test_presence_row, _presence_params(pending=pending))):
        names.update(f"{test.__name__}[{param.id}]" for param in params
                     if any(mark.name == "xfail" for mark in param.marks))
    for test, marks in ((test_decisions_01_conforms, _pending_marks(_decisions_01_pairs(), pending)),
                        (test_no_regression_comment_drops_reopen,
                         _pending_marks([(_NO_REGRESSION, "C05-gate02-worse-rate-reopens")], pending)),
                        (test_the_pending_directory_holds_no_csv, _pending_dir_marks(pending))):
        if marks:
            names.add(test.__name__)
    return names


def test_the_gate_runs_without_the_data_dir(tmp_path):
    """AC5 on CI: in a copy of the repo with no ``.shipyard``, ``SHIPYARD_DATA_DIR`` naming an empty
    directory, this module runs with nothing failed, and the only skips are the ``test_idea_end_state``
    rows, each naming ``IDEA_SKIP_REASON``, plus the strict xfails the checkout's pending files put on
    it (``pending_xfail_case_ids``, each naming its site task; junit records an xfail as a skip). The
    pending half is derived, not listed: with no pending file it is empty, and the expectation is the
    IDEA rows alone again."""
    import subprocess
    import sys
    import xml.etree.ElementTree as ET

    repo = _copy_of_the_repo(tmp_path / "repo")
    assert not (repo / ".shipyard").exists()
    (tmp_path / "no-data-dir").mkdir()
    report = tmp_path / "report.xml"
    env = {**os.environ, "SHIPYARD_DATA_DIR": str(tmp_path / "no-data-dir")}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "runcoach-api/tests/test_research00_downstream.py", "-q",
         "-p", "no:cacheprovider", f"--junitxml={report}", "-k", "not test_the_gate_runs_without_the_data_dir"],
        cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    cases = ET.parse(report).getroot().iter("testcase")
    outcomes = {}
    for case in cases:
        name = case.get("name")
        bad = [child for child in case if child.tag in ("failure", "error")]
        skipped = case.find("skipped")
        outcomes[name] = ("failed" if bad else "skipped" if skipped is not None else "passed",
                          "" if skipped is None else skipped.get("message", ""))
    skipped = {name: why for name, (state, why) in outcomes.items() if state == "skipped"}
    failed = [name for name, (state, _why) in outcomes.items() if state == "failed"]
    tail = [re.sub(r"(\d+) (\w+)", r"\2=\1", line) for line in done.stdout.strip().splitlines()[-1:]]
    idea_rows = {f"test_idea_end_state[{row.idea.replace('-', '_')}]" for row in IDEA_END_STATES}
    pending_rows = pending_xfail_case_ids()
    expected = idea_rows | pending_rows
    print(f"[slice compared] rc={done.returncode}; {len(outcomes)} cases; failed {failed}; "
          f"{len(skipped)} skipped against {len(expected)} expected = {len(idea_rows)} IDEA rows + "
          f"{len(pending_rows)} pending xfails under {sorted(read_pending())} (with no pending file: "
          f"{len(pending_xfail_case_ids(pending={}))}); skipped {skipped}; tail {tail}")
    assert done.returncode == 0 and failed == []
    assert pending_xfail_case_ids(pending={}) == set()
    assert set(skipped) == expected
    assert all(IDEA_SKIP_REASON in skipped[name] for name in idea_rows), skipped
    assert all(_PENDING_XFAIL_REASON.fullmatch(skipped[name]) for name in pending_rows), skipped
    assert sum(state == "passed" for state, _why in outcomes.values()) > len(expected)


# --------------------------------------------------------------------------------------------------
# T230 (IDEA-106 items 11-13): the one-time census builder fails loud on what it cannot read.
# --------------------------------------------------------------------------------------------------

_BUILDER_MODULE: list[ModuleType] = []


def _census_builder() -> ModuleType:
    """``support/build_research00_census.py``, loaded once and lazily: it loads this file under its
    own name (``GATE``) at import, so a module-level load here would recurse."""
    if not _BUILDER_MODULE:
        _BUILDER_MODULE.append(_load_module("research00_census_builder", _SUPPORT / "build_research00_census.py"))
    return _BUILDER_MODULE[0]


@pytest.mark.parametrize("citation", ["`:792`", "`hrv_trend.txt:12`"],
                         ids=["bare-before-any-file", "unmapped-suffix"])
def test_builder_unparsed_citation_raises(tmp_path, citation):
    """IDEA-106 item 12: a citation on a Code line that the parser cannot read -- a bare ``:n`` with no
    file named before it on the line, or a ``name:n`` whose suffix the citation pattern does not know --
    raises instead of being skipped silently (the builder's docstring: every dropped candidate is
    printed with its reason). A well-formed line beside it still parses."""
    builder = _census_builder()
    inventory = tmp_path / "inventory.md"
    bad = f"- Code: the skipped arm ({citation}) and `hrv_trend.py:1450-1453`."
    inventory.write_text(f"**C01. An item**\n{bad}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=re.escape(citation.strip("`"))):
        builder.inventory_citations(inventory)
    good = "- Code: `hrv_trend.py:1450-1453` and `:792`."
    (tmp_path / "parsed.md").write_text(f"**C01. An item**\n{good}\n", encoding="utf-8")
    parsed = builder.inventory_citations(tmp_path / "parsed.md")
    print(f"[slice compared] unparsed {citation} raises; parsed {parsed}")
    assert parsed == [("C01", "hrv_trend.py", [1450, 1451, 1452, 1453], good), ("C01", "hrv_trend.py", [792], good)]


def test_builder_loose_grep_must_reach_a_deferred_inventory_citation(tmp_path, monkeypatch):
    """IDEA-106 item 11: step 4 leaves an S2-root inventory citation to the loose grep, saying it
    "reaches it and resolves it below". The builder now checks that: a deferred citation that no loose
    hit reaches (same key, path and line) is reported, not dropped. On a tmp world of one file: line 1
    holds the key's loose noun, line 2 does not."""
    builder = _census_builder()
    key = "C03-return-is-free"
    (tmp_path / "x.md").write_text("A return is free here.\nThe code the item describes.\n", encoding="utf-8")
    monkeypatch.setattr(builder.GATE, "live_files", lambda repo_root=None: {"root": ("x.md",)})
    hits = builder.loose_hits(tmp_path, {key: builder.LOOSE_NOUNS[key]})
    reached = (f"C03 x.md:1 {key}", key, "x.md", 1)
    missed = (f"C03 x.md:2 {key}", key, "x.md", 2)
    verdicts = {"reached": builder.unresolved_citations([reached], hits),
                "reached and missed": builder.unresolved_citations([reached, missed], hits)}
    print(f"[slice compared] hits {[(k, p, line) for k, p, _s, _e, line, _q in hits]}; {verdicts}")
    assert [(k, p, line) for k, p, _s, _e, line, _q in hits] == [(key, "x.md", 1)]
    assert verdicts == {"reached": [], "reached and missed": [missed[0]]}


def test_builder_writes_after_pending_appends(tmp_path, monkeypatch):
    """IDEA-106 item 13: ``append_pending`` can SystemExit partway (a row with no single owner), so the
    CSV is written only after it returns. On a tmp world: one loose row with no owner; ``main()``
    exits and leaves no census file."""
    builder = _census_builder()
    census = builder.Census()
    census.add("C03-return-is-free", "specification/spec/02-canonical-data-schema-ingestion.md",
               "a return is free", "loose")
    (tmp_path / "inventory.md").write_text("**C03. An item**\n", encoding="utf-8")
    monkeypatch.setattr(builder, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(builder, "INVENTORY", tmp_path / "inventory.md")
    monkeypatch.setattr(builder, "CENSUS_PATH", tmp_path / "census.csv")
    monkeypatch.setattr(builder, "build", lambda: census)
    monkeypatch.setattr(builder, "check", lambda _census: [])
    monkeypatch.setattr(builder.GATE, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(builder.GATE, "read_pending", lambda *_a, **_k: {})
    monkeypatch.setattr(builder.GATE, "owners_of", lambda *_a, **_k: set())
    with pytest.raises(SystemExit, match="no owner"):
        builder.main()
    print(f"[slice compared] after SystemExit: census.csv exists={(tmp_path / 'census.csv').exists()}; "
          f"pending dir exists={(tmp_path / 'pending').exists()}")
    assert not (tmp_path / "census.csv").exists()
