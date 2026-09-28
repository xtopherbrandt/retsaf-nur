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
- **Hits (T199, AC1).** Each ``OLD_MEANINGS`` key's pattern is searched in every live file its
  ``KEY_ROOTS`` entry lets it reach (S12: the four T-07 keys reach ``.claude/rules/`` only). A hit is
  quotation (S3), sheltered by an ``EXCEPTIONS`` excerpt overlapping it in that one file (S4), a
  pending site (S15: ``tests/data/research00_pending/<task id>.csv``, strict xfail, owned per
  ``OWNERSHIP``), or a failure.

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
from dataclasses import dataclass
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
        text, offsets = normalize_with_offsets(raw)
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
#: HRV-01 families are expanded to their exact names; the five C19 correct-prose hits T218 narrows are
#: recorded as explicit T218 rows beside their family or ``*`` owner (spec/03:81 and :155, spec/04:123,
#: spec/02:191, and rr_reconstruction.py:418). Hits outside every row fail the gate.
OWNERSHIP = (
    ("specification/spec/03-derived-metric-formulas.md", "*", "T202"),
    ("specification/spec/03-derived-metric-formulas.md", "C19-hrv-03-tag-and-confidence", "T213"),
    ("specification/spec/03-derived-metric-formulas.md", "C19-hrv-04-reduced-confidence", "T213"),
    ("specification/spec/03-derived-metric-formulas.md", "HRV-01-R13-four-tier-hierarchy", "T213"),
    ("specification/spec/03-derived-metric-formulas.md", "C19-hrv-04-reduced-confidence", "T218"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C19-hrv-03-tag-and-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C19-hrv-04-reduced-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "HRV-01-R13-four-tier-hierarchy", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "PRIN-10-C19-reduced-confidence", "T203"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "C19-hrv-04-reduced-confidence", "T218"),
    ("specification/spec/02-canonical-data-schema-ingestion.md", "*", "T214"),
    ("specification/spec/04-*", "*", "T204"),
    ("specification/spec/04-physiological-state-model.md", "C19-hrv-04-reduced-confidence", "T218"),
    ("specification/spec/05-*", "*", "T204"),
    ("specification/spec/08-*", "*", "T204"),
    ("specification/spec/09-*", "*", "T204"),
    ("specification/future/future-directions.md", "*", "T204"),
    ("specification/spec/06-adaptation-logic.md", "*", "T205"),
    ("specification/spec_outline.md", "*", "T205"),
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
    ("runcoach-api/src/runcoach_api/metrics/hrv_trend.py", "*", "T218"),
    ("runcoach-api/src/runcoach_api/ingestion/rr_reconstruction.py", "C19-hrv-04-reduced-confidence", "T218"),
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


def stale_pending_rows(hits: list[Hit], pending: dict[str, list[tuple[str, str]]]) -> list[str]:
    """S15: each pending row with no live hit left to cover -- a ``(path, *)`` row needs any live hit
    in ``path``, a ``(path, key)`` row a live hit of that key in ``path``."""
    live = [h for h in hits if h.live]
    return [f"{task}.csv: {path},{key}" for task, rows in pending.items() for path, key in rows
            if not any(h.path == path and key in ("*", h.key) for h in live)]


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
        text, offsets = normalize_with_offsets(raw)
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
    ``(path, *)``) row needs a live hit that is still red. T200's census rows and T201's presence rows
    are the other red rows that keep a row needed; they join this check when those tasks add them."""
    live = [h for h in real_scan() if h.live]
    stale = stale_pending_rows(live, read_pending())
    print(f"[slice compared] {sum(len(r) for r in read_pending().values())} pending rows against "
          f"{len(live)} live hits; stale {stale}")
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
        "T213", "T218"}
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
