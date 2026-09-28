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

**Blind spots, stated rather than argued away:**

- **Records (S1).** Nothing under a ``RECORD_SET`` path, and no text under a section record, is swept,
  whatever it says. A live claim written into a record is invisible here by construction -- the same
  class as the endpoint test's ``CHANGELOG.md`` row.
- **Test comments and docstrings (S2).** ``runcoach-api/tests/`` is not a root, so comments and
  docstrings in the test files are not swept. The spec critic found keyed phrases in 7 test files;
  only the ``test_hrv_no_regression_gate.py`` comment has a row (S6).
"""

import importlib.util
import os
import re
import unicodedata
from pathlib import Path
from types import ModuleType

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


def hit_is_quoted(raw: str, norm_start: int, norm_end: int) -> bool:
    """S3: a hit at ``[norm_start, norm_end)`` of ``normalize(raw)`` is quotation only when its raw
    extent lies wholly inside one quote span. Half inside is not sheltered."""
    _text, offsets = normalize_with_offsets(raw)
    raw_start, raw_end = offsets[norm_start], offsets[norm_end - 1] + 1
    return any(a <= raw_start and raw_end <= b for a, b in quote_spans(raw))


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
        "spec-mirror/features/F005-resting-hrv-trend.md",
        "spec-mirror/references/F005-decision-log.md",
        "spec-mirror/references/F006-sweep-findings.md",
    ]
    files = live_files(_tree(tmp_path, live + dropped))
    walked = sorted(p for paths in files.values() for p in paths)
    print(f"[slice compared] {len(walked)} walked of {len(live) + len(dropped)} planted: {walked}")
    assert walked == sorted(live)
    assert files["research"] == ("specification/research/01-physiology.md",
                                 "specification/research/06-landscape.md")


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
