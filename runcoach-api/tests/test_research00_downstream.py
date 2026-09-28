"""F011: the downstream gate -- no live document states a meaning research/00's rewrite changed.

This file is the gate's scanner (T197): which files are live, how many there are per root, which text
in them is a record, and where the quotation spans lie. The hit test over ``OLD_MEANINGS`` (T199),
the census rows (T200) and the presence rows (T201) read what is defined here.

Authority: ``spec/references/F011-sweep-decisions.md`` S1-S3 and S15. CI has no data dir, so nothing
here reads it: every fact taken from the decisions is frozen as a literal.

- **Live files (S2).** The roots below, restricted to ``.md``, ``.py``, ``.yaml`` and ``.yml``
  (``runcoach-api/src/`` to ``.py``, never ``.pyc``), minus ``RECORD_SET`` (S1, in
  ``support/research00_records.py``, shared with F009).
- **Floors (S2).** ``ROOT_FLOORS`` is each root's live count as this gate measured it at ``a15610d``
  (58 in all; rules 13). A walk that stops descending reports all-clear over nothing; a root below its
  floor turns the gate red. A record added inside a root lowers that root's floor in the same commit.
- **Section records (S1).** Text under exactly ``^## Decision Log\\s*$`` in a ``SECTION_RECORD_FILES``
  file, and a CHANGELOG entry under a released ``## [x.y.z]`` heading, is not swept. Every other file
  under the roots that carries the Decision Log heading is swept, and a test lists them.
- **Quotation (S3).** In ``.md`` only, a hit wholly inside a straight or curly double-quoted span or a
  code span is quotation. Spans are found on the raw text, before ``normalize()``; they never cross a
  blank line; a fenced block is code; a ``>`` blockquote is not quotation. ``.py``, ``.yaml`` and
  ``.yml`` are scanned whole.
- **Wrapped ``#`` comments (T219).** In ``.py`` only, each comment line's marker (indentation, a ``#``
  run, a ``#:`` colon) is removed before ``normalize()`` (``gate_text``), so a meaning wrapped over two
  comment lines reads as one sentence. ``.md``, ``.yaml`` and ``.yml`` keep every ``#``. Every census and
  exception check compares against this same text.
- **Hits (T199, AC1).** Each ``OLD_MEANINGS`` key's pattern is searched in every live file its
  ``KEY_ROOTS`` entry lets it reach (S12: the four T-07 keys reach ``.claude/rules/`` only). A hit is
  quotation (S3), sheltered by an ``EXCEPTIONS`` excerpt overlapping it in that one file (S4), a
  pending site (S15: ``tests/data/research00_pending/<task id>.csv``, strict xfail, owned per
  ``OWNERSHIP``), or a failure.
- **Presence rows (T201, S6).** Each census site whose key cites a decision in F008 AC7's ``OPERATIVE``
  (read from the traceability test by path, never copied) has one row per operative string: the ``.md``
  paragraph holding the site's frozen anchor (``PRESENCE_ANCHORS``) states the string after
  ``normalize()``. A site or string that gets no row is in ``DROPPED_PRESENCE_ROWS`` with its reason.
  ``test_decisions_01_conforms`` (S9) and the no_regression comment's absence row sit beside them.

**Blind spots, stated rather than argued away:**

- **Records (S1).** Nothing under a ``RECORD_SET`` path, and no text under a section record, is swept,
  whatever it says. A live claim written into a record is invisible here by construction -- the same
  class as the endpoint test's ``CHANGELOG.md`` row.
- **Test comments and docstrings (S2).** ``runcoach-api/tests/`` is not a root, so comments and
  docstrings in the test files are not swept. The spec critic found keyed phrases in 7 test files;
  only the ``test_hrv_no_regression_gate.py`` comment has a row (S6).
"""

import csv
import fnmatch
import importlib.util
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest

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
_RELEASED_ENTRY = re.compile(r"## \[\d+\.\d+\.\d+\].*")
_SECTION_END = re.compile(r"#{1,2} \S.*")


def _lines_with_offsets(raw: str):
    offset = 0
    for line in raw.splitlines(keepends=True):
        yield offset, line, line.rstrip("\r\n")
        offset += len(line)


def record_ranges(rel_path: str, raw: str) -> list[tuple[int, int]]:
    """Raw ``(start, end)`` ranges of ``raw`` that are section records and are not swept: text under
    exactly ``^## Decision Log\\s*$`` when ``rel_path`` is in ``SECTION_RECORD_FILES``, and a
    ``CHANGELOG.md`` entry under a released ``## [x.y.z]`` heading. A section runs from its heading to
    the next level-1 or level-2 heading, or to the end of the file."""
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

_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def _fenced_blocks(raw: str) -> list[tuple[int, int]]:
    """Raw ranges of fenced code blocks, fence lines included. An unclosed fence runs to the end."""
    blocks: list[tuple[int, int]] = []
    opener = None
    start = 0
    for offset, line, text in _lines_with_offsets(raw):
        match = _FENCE.match(text)
        if opener is None:
            if match:
                opener, start = match.group(1), offset
        elif match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener) \
                and not text.strip().strip(opener[0]):
            blocks.append((start, offset + len(line)))
            opener = None
    if opener is not None:
        blocks.append((start, len(raw)))
    return blocks


def _paragraphs(raw: str, fenced: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Raw ranges of runs of non-blank lines outside fenced blocks: a span never leaves one."""
    paragraphs: list[tuple[int, int]] = []
    start = None
    for offset, line, text in _lines_with_offsets(raw):
        inside_fence = any(a <= offset < b for a, b in fenced)
        if inside_fence or not text.strip():
            if start is not None:
                paragraphs.append((start, offset))
                start = None
        elif start is None:
            start = offset
    if start is not None:
        paragraphs.append((start, len(raw)))
    return paragraphs


def quote_spans(raw: str) -> list[tuple[int, int]]:
    """S3 on the raw text, before ``normalize()``: raw ``(start, end)`` ranges, delimiters included,
    of straight (``"..."``) and curly (``“...”``) double-quoted spans, code spans (a backtick run to
    the next run of the same length), and fenced blocks. No span crosses a blank line; ``>`` opens
    nothing, so a blockquote is not quotation. An unmatched opener is a literal character."""
    fenced = _fenced_blocks(raw)
    spans = list(fenced)
    for start, end in _paragraphs(raw, fenced):
        i = start
        while i < end:
            char = raw[i]
            if char == "`":
                run = i
                while run < end and raw[run] == "`":
                    run += 1
                width = run - i
                close = re.compile(rf"(?<!`)`{{{width}}}(?!`)").search(raw, run, end)
                if close:
                    spans.append((i, close.end()))
                    i = close.end()
                else:
                    i = run
                continue
            closer = {'"': '"', "“": "”"}.get(char)
            if closer is not None:
                j = raw.find(closer, i + 1, end)
                if j != -1:
                    spans.append((i, j + 1))
                    i = j + 1
                    continue
            i += 1
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


def gate_source(path: str, raw: str) -> tuple[str, list[int]]:
    """The file ``path``'s text before ``normalize()``, with each character's raw offset: for ``.py``,
    ``raw`` with every comment line's marker (``_WRAPPED_COMMENT``) removed and each newline kept (so
    line numbers hold); for ``.md``, ``.yaml`` and ``.yml``, ``raw`` itself."""
    if not path.endswith(".py"):
        return raw, list(range(len(raw)))
    pos = 0
    pieces: list[str] = []
    source_to_raw: list[int] = []
    for marker in _WRAPPED_COMMENT.finditer(raw):
        pieces.append(raw[pos:marker.start()])
        source_to_raw += range(pos, marker.start())
        pos = marker.end()
    pieces.append(raw[pos:])
    source_to_raw += range(pos, len(raw))
    return "".join(pieces), source_to_raw


def gate_text(path: str, raw: str) -> tuple[str, list[int]]:
    """The text the gate matches in the file ``path``, with each character's raw offset:
    ``normalize_with_offsets`` of ``gate_source``. For ``.md``, ``.yaml`` and ``.yml`` that is
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
    excerpt overlaps the hit in this file (S4)."""
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
                    sheltered_by=tuple(sorted({i for i, a, b in excerpts if start < b and a < end})),
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
    ("runcoach-api/tests/test_hrv_no_regression_gate.py", "C05-gate02-worse-rate-reopens", "T212"),
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
#: - ``C19-hrv-04-reduced-confidence`` (T218, from ``b4479c4``): the bare phrase matched five sites of
#:   correct prose that carry something other than a numeric rMSSD at reduced confidence (spec/03's
#:   altitude-less features and CTL seed, spec/04's lone VO2max model, spec/02 §2.4.3 and
#:   ``rr_reconstruction.py``'s low-valid-fraction series). The narrowed pattern needs rMSSD in the
#:   same sentence, which every site of the old HRV-tier meaning names.
NARROWED_FROM = MappingProxyType({
    "C19-hrv-04-reduced-confidence": "at reduced confidence",
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
    """The sites each narrowing stopped matching: every hit of a key's old pattern over the live files
    of ``repo_root`` that overlaps no hit of its current pattern in the same file. Exceptions play no
    part (a narrowing is judged on the text, sheltered or not)."""
    old_meanings = OLD_MEANINGS if old_meanings is None else old_meanings
    current = {key: old_meanings[key] for key in narrowed_from}
    before = {key: replace(old_meanings[key], pattern=old) for key, old in narrowed_from.items()}
    now = scan(repo_root, old_meanings=current, exceptions=())
    was = scan(repo_root, old_meanings=before, exceptions=())
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
)


def _occurrences(text: str, excerpt: str) -> list[tuple[int, int]]:
    needle = normalize(excerpt)
    return [(m.start(), m.end()) for m in re.finditer(re.escape(needle), text)] if needle else []


def census_state(row, repo_root: Path = _REPO_ROOT, exceptions=None) -> str:
    """Where a census row stands now: ``missing`` (no such file), ``gone`` (its excerpt no longer
    occurs), ``sheltered`` (every occurrence overlaps an ``EXCEPTIONS`` excerpt in that file, S4), or
    ``present`` -- the site still states what it did."""
    exceptions = _OM.EXCEPTIONS if exceptions is None else exceptions
    path = Path(row["path"]).as_posix()
    if not (repo_root / path).is_file():
        return "missing"
    text = gate_normal(path, (repo_root / path).read_text(encoding="utf-8"))
    found = _occurrences(text, row.get("excerpt") or "")
    if not found:
        return "gone"
    sheltering = _excerpt_ranges(text, path, exceptions)
    if all(any(a < e and s < b for _i, a, b in sheltering) for s, e in found):
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
#: file (2026-09-28, spec review wave 1), not from ``research00_records.py``. Each spec pattern is written
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
    # F009 AC2, the data dir: F005-*, F006's three (both copies), the inventory, F008's decisions,
    # sprints/sprint-* except current, verify/*-verdict-cycle*.md.
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
        ("text\n~~~py\nunclosed", ["~~~py\nunclosed"]),
        ('an "unclosed quote', []),
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


@pytest.mark.parametrize("suffix", ["md", "yaml"])
def test_wrapped_comment_markers_stay_in_md_and_yaml(tmp_path, suffix):
    """T219: only ``.py`` loses its comment markers. The same text in ``.md`` or ``.yaml`` keeps the
    ``#`` (a heading, a YAML comment), so the split example is no hit there, as before."""
    rel, hits = _wrapped_world(tmp_path, suffix)
    raw = (tmp_path / rel).read_text(encoding="utf-8")
    text, offsets = gate_text(rel, raw)
    print(f"[slice compared] {rel} gate text {text!r}; hits {hits}")
    assert (text, offsets) == normalize_with_offsets(raw)
    assert "reference #: and" in text
    assert hits == []


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


def test_narrowed_from_names_real_keys_whose_old_pattern_was_wider():
    """Each ``NARROWED_FROM`` entry names an ``OLD_MEANINGS`` key, its old pattern differs from the
    current one, and both match the key's example (a narrowing keeps F008's positive control)."""
    rows = {key: (old, OLD_MEANINGS[key].pattern if key in OLD_MEANINGS else None)
            for key, old in NARROWED_FROM.items()}
    print(f"[slice compared] NARROWED_FROM (old, current): {rows}")
    assert sorted(NARROWED_FROM) == sorted(NARROWED_SITE_PATHS)
    for key, (old, current) in rows.items():
        assert current is not None and current != old, key
        example = normalize(OLD_MEANINGS[key].example)
        assert re.search(old, example) and re.search(current, example), key


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


def test_the_f009_exceptions_shelter_every_hrv_trend_hit_each_excerpt_once():
    """S4 for F009: every hit in ``hrv_trend.py`` is sheltered, only by F009 triples; each F009 excerpt
    occurs exactly once in the file (an excerpt shelters every occurrence of itself) and shelters a
    hit; and no F009 triple names another file."""
    exceptions = _OM.EXCEPTIONS
    f009 = [i for i, e in enumerate(exceptions) if e[2] == "F009"]
    text = gate_normal(_HRV_TREND, (_REPO_ROOT / _HRV_TREND).read_text(encoding="utf-8"))
    counts = {i: text.count(normalize(exceptions[i][1])) for i in f009}
    hits = [h for h in real_scan() if h.path == _HRV_TREND]
    for hit in hits:
        print(f"  {hit.path}:{hit.line} {hit.key} sheltered by {hit.sheltered_by}")
    print(f"[slice compared] {len(f009)} F009 triples, excerpt counts {counts}; {len(hits)} hrv_trend.py hits")
    assert f009 and hits
    assert all(exceptions[i][0] == _HRV_TREND for i in f009)
    assert all(count == 1 for count in counts.values()), counts
    assert all(h.sheltered_by and set(h.sheltered_by) <= set(f009) for h in hits)
    assert set(f009) <= {i for h in hits for i in h.sheltered_by}


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
    Text only another key's pattern (C05) matches is green: that hit is not the narrowing's."""
    c19, c05 = "C19-hrv-04-reduced-confidence", "C05-gate02-worse-rate-reopens"
    rel = "specification/spec/99-site.md"
    correct = "carried at reduced confidence until it is established"
    _plant(tmp_path, rel, (f"Correct: the tier is {correct}.\n\nOld: {OLD_MEANINGS[c19].example}.\n\n"
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
     "**Confidence and the anti-mixing rule.**"),
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
)

_C33_REASON = ("C33's prose and its EXCEPTIONS are F010's, not F011's (F011 Not in scope); this site is "
               "hrv_trend.py (F009's, sheltered by EXCEPTIONS) and a .py block, which T201 does not build")
_PINNED_REASON = ("a Pinned line is research/00's own rule-block field, and F009 replaces 'none (F009)'; "
                  "no downstream prose states it, so the row could never pass")

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
     "Pinned: none (F009)", _PINNED_REASON),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     "DEFERRED_EXCEPTION", "S6: already in the cost table at HEAD"),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     "IDEA-087", "S6: already in the cost table at HEAD"),
    ("spec-mirror/references/F006-dataset-model.md", "C06", "| cost | direction and why it is accepted |",
     "Pinned: none (F009)", _PINNED_REASON),
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


def _marked(marks):
    def decorate(func):
        for mark in marks:
            func = mark(func)
        return func
    return decorate


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
