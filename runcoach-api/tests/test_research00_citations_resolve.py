"""F009: the citation gate -- every live reference to research/00 names a rule ID that resolves.

Authority: F009 AC1-AC4 and the sprint-009 rulings D11-D13, D19 and D20
(``spec/references/sprint-009-decisions.md``). The scanner pieces are F011's (``_load_module``,
``is_record``, ``record_ranges``, ``live_files``, ``ROOT_FLOORS`` in ``test_research00_downstream.py``)
and the rule-ID parser is F008's (``_RULE_ID``, ``rule_ids``, ``RETIRED_IDS`` in
``test_research00_traceability.py``), both loaded by path and never copied.

**The walk (AC1).** Two spaces: ``repo`` (this checkout) and ``data`` (``<SHIPYARD_DATA>/spec/`` only,
found through ``SHIPYARD_DATA_DIR`` or the ``.shipyard`` breadcrumb; when neither names a directory with
``spec/`` under it the gate **fails, never skips**). Both skip the directories ``.git``, ``.venv``,
``__pycache__`` and ``.shipyard``, every child directory holding ``.git`` (a nested checkout such as a
builder worktree; S2), every ``*.bak`` and every dot-prefixed file name (D12; ``.claude/`` is a
directory and is walked, so AC6's rule file is seen), and read only the text suffixes ``.md .py .yaml
.yml .toml .csv .txt .json``. A file is **in scope** when its text mentions ``research[_/]00``, carries a
``§1.x``, ``§3.x`` or ``§5.4`` token, or cites a rule ID (``_RULE_ID``: a rule ID alone is a citation of
research/00 and must resolve). Every in-scope file is classified **live** or **record**, and the gate
prints ``classified <N> files`` with the per-set counts.

**Records.** By path: F011's shared ``RECORD_SET`` (``support/research00_records.py``, which D13 widened
by the two literal-bearing support modules) plus this gate's own ``CITATION_RECORDS``, which holds
``specification/research/00-design-decisions.md``: research/00 is the target of citations, not a citer,
and its 29 self-references stay (user ruling 2026-09-30, D19). It is kept out of the shared set because
nothing in F009 asks F011's sweep to change, although F011's ``research`` root (glob ``0[1-6]-*``) would
exclude it anyway. By frontmatter: a ``status:`` of ``done``, ``completed`` or ``released`` (D12). By
section, inside a live file: the lines ``record_ranges`` covers -- the exact ``## Decision Log`` heading
in the frozen ``SECTION_RECORD_FILES`` and a ``CHANGELOG.md`` entry under ``## [x.y.z]`` **or a dated
sprint heading** ``## <date> through <date> — Sprint NNN`` (D11; only ``## Unreleased`` is live). The
frozen list names the spec-mirror copy; the data-dir original of a mirrored file carries the same
section record here, because ``test_normative_mirror.py`` holds the two byte-identical, so a Decision
Log that is a record in one copy cannot be live in the other.

**Roots and the CSV contract (AC3, D20).** Root membership: ``specification/`` -> ``spec``;
``runcoach-api/**/*.py`` except ``metrics/hrv_trend.py`` -> ``python``; ``hrv_trend.py`` ->
``hrv_trend``; the data space and its repo mirror ``spec-mirror/`` -> ``data`` (T236); everything else in the
repo -> ``misc``. The by-reading record
is ``tests/data/research00-citation-sites/<root>.csv`` (columns ``space,file,line,old_citation,new_id``;
T233-T236 and T238 write the rows); the gate consumes every ``*.csv`` in the directory. ``new_id`` is a
rule ID that must resolve to a rule line of research/00, or ``literal``: a search pattern, census excerpt
or printed witness (D13), which is **accepted, not resolved, and exempt from the old-token assertion** and
from every other per-line check on that one line. An empty `old_citation` records a line that mentions
research/00 and carries the spec's own section token, read and judged not a research/00 citation; the
old-token check skips it (T233's rows; stated at T246). A live file with a citation token (``§1.x``, ``§3.x``,
``§5.4`` or ``Part N``) on a live line and no row in any CSV is **unlisted**. So is one live line of a
**listed** file that carries the ``research[_/]00`` mention and a section or Part token with no row of its
own (T245): listing one line does not list the file. A bare token with no mention on such a line is not a
finding here (see the blind spots). A section number written without the sign counts too when it is joined
to the mention (``research/00 Sec 3.3``, ``research/00 Section 1.7``, ``research/00 1.6``, ``Section 1.7 of
research/00``), on one line or across a line break (two adjacent lines joined, a Python implicit string
concatenation closed); a "Section 3.x" merely near the mention is another document's (M2). Any case, the
file's own name or a possessive after the mention, ``Sections``, ``sec.`` and ``Section N of the research/00
...`` count; a measurement, a version or a sub-section number beside the mention does not (S5). So does a
locator the rewrite retired, joined to the mention: ``research/00:<line>`` and ``research/00 finding <N>``
(N4), ``research/00 line <N>``, ``research/00 (L<N>)``, ``findings <N> and <M>`` and a possessive, straight or curly
(S9); a section, line or finding of research/00-history.md, -traceability.md or -meaning-review.md (S10) and a
finding with no mention before it are not. A section number the sentence's full stop follows counts (G1). Line numbers are
``git grep -n``'s: lines split on ``\\n`` alone, never on U+2028, U+2029, a form feed or NEL (S1).

**Pending markers (S15 shape).** While ``pending-<root>.marker`` exists, that root's test is a strict
xfail: unlisted files, AC3 clause 3 and the AC4 checks are expected red until the migrating task deletes
the marker in the commit that lists the root. Once the marker is gone the same test is a hard failure, so
a file added after the sprint cannot hide behind an xfail. The row checks (``test_every_citation_row_is_valid``)
are hard from the start: a row exists only once its line was migrated.

**AC4.** Every rule ID on a live line resolves to a rule line (``rule_ids`` of the checkout's research/00);
a retired ID (``RETIRED_IDS``) is accepted only on a line that says it is retired. A live line that cites
research/00 and names the forbidden direction (``forbid``, ``weak evidence``, ``readiness-intact``) cites
PRIN-14; a live line that says the direction is "tolerated" cites neither PRIN-14 nor a retired ID.

**Blind spots and handovers, stated rather than argued away:**

- F009 AC2's ``sprints/sprint-`` and ``verify/*-verdict-cycle`` rows lie outside ``spec/``, so under this
  ``spec/``-only data walk they are dead: nothing under ``sprints/`` or ``verify/`` is read. The nine
  ``verify/`` files that do not match the cycle glob are irrelevant for the same reason.
- The gate does not classify a citation by regex: a bare ``§5.4`` may name spec/05's own section. The CSV
  is the by-reading record and the gate checks it (unlisted files and lines, old token gone, new ID
  present and resolving). A remaining bare ``§`` token in a live ``.md`` is therefore judged by the
  migrating task, not here (a token beside the ``research/00`` mention on a row-less line of a listed file
  is, since T245); in a live ``.py`` under ``runcoach-api/`` AC3 clause 3 applies (a ``spec/0N`` prefix
  or a ``literal`` row).
- ``runcoach-api/tests/data/research00_census.csv`` (T200's committed census) is live under ``misc``:
  its excerpts quote old tokens and old IDs. D13 named the two support modules only; the misc task
  (T236) lists its rows as ``literal`` or asks for a ruling.
- At this commit the ``data`` root cites ``HRV-85`` and ``PRIN-27``, which T226 is adding to research/00
  in the same wave, and F009's feature file and T232's task file carry the ``HRV-999`` witness and name
  the retired PRIN-16; the data marker covers them until T235 lists or records them. This module is
  itself live under ``python``: its docstring witnesses and its finding-message strings are T234's
  ``literal`` rows (D13), not prose to migrate.
- The citation-site CSVs themselves (``tests/data/research00-citation-sites/*.csv``) quote the tokens they
  retired in ``old_citation`` and carry the perturbation tests' witness rows, so once T233 and T234 filled them the
  walk saw them as live misc files. They are the by-reading record, not citers: ``CITATION_RECORDS`` holds
  the directory (T236). Nothing lists a row for a CSV.
- A ``spec-mirror/`` copy is one document in two places: ``test_normative_mirror.py`` holds it byte-identical
  to its data-dir original, so T235 rewrites both copies and writes the rows once, for the original. The
  mirror copy is therefore in the ``data`` root (``root_of``), and ``_aliases`` lets a row for either copy
  cover the same line of the other (T236; the section-record logic above already reads the pair as one).
- Printed witnesses are ASCII-escaped (``_show``): the probe runs ``-s`` on a cp1252 console.
"""

import csv
import fnmatch
import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS = Path(__file__).parent


def _load_module(name: str, path: Path) -> ModuleType:
    """Import a module from its path: the workspace runs pytest with ``--import-mode=importlib``,
    under which nothing in ``tests/`` is importable by name (as at test_research00_downstream.py)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DS = _load_module("research00_downstream_scanner", _TESTS / "test_research00_downstream.py")
_TR = _load_module("research00_traceability_parser", _TESTS / "test_research00_traceability.py")
_REC = _load_module("research00_records", _TESTS / "support" / "research00_records.py")

is_record = _REC.is_record
RECORD_SET = _REC.RECORD_SET
SECTION_RECORD_FILES = _REC.SECTION_RECORD_FILES
record_ranges = _DS.record_ranges
_RULE_ID = _TR._RULE_ID
rule_ids = _TR.rule_ids
RETIRED_IDS = _TR.RETIRED_IDS

# --------------------------------------------------------------------------------------------------
# The contract's constants.
# --------------------------------------------------------------------------------------------------

DATA_DIR_ENV = "SHIPYARD_DATA_DIR"
SITES_DIR = _TESTS / "data" / "research00-citation-sites"
RESEARCH_00 = "specification/research/00-design-decisions.md"
HRV_TREND = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
#: The repo copy of a data-dir ``spec/`` document (``test_normative_mirror.py`` holds the pair byte-identical).
MIRROR_PREFIX = "spec-mirror/"
SITES_DIR_REL = "runcoach-api/tests/data/research00-citation-sites/"
TEXT_SUFFIXES = (".md", ".py", ".yaml", ".yml", ".toml", ".csv", ".txt", ".json")
SKIPPED_DIRS = frozenset({".git", ".venv", "__pycache__", ".shipyard"})
#: A child directory holding this (a file in a worktree, a directory in a clone) is a nested git checkout,
#: a second copy of the tree, and is not entered (S2): ``SCAN_EXCLUDED_CHECKOUT_MARKER``'s rule in
#: ``test_hrv_trend_endpoint.py`` (3c18d26). The walked root holds one too and is still walked.
NESTED_CHECKOUT_MARKER = ".git"
ROOTS = ("spec", "python", "data", "misc", "hrv_trend")
CSV_COLUMNS = ("space", "file", "line", "old_citation", "new_id")
LITERAL = "literal"
RECORD_STATUSES = frozenset({"done", "completed", "released"})
#: The gate's own extension of ``RECORD_SET`` (D19): ``(space, prefix, reason)``, matched like its rows.
CITATION_RECORDS = (
    ("repo", RESEARCH_00,
     "the target of citations, not a citer; its self-references stay (user ruling 2026-09-30, D19)"),
    ("repo", SITES_DIR_REL,
     "the by-reading record itself: old_citation quotes the tokens the rows retired (T236)"),
)
#: The D13 rows this task added to the shared set, asserted present with their reason.
D13_RECORDS = (
    ("repo", "runcoach-api/tests/support/build_research00_census.py", "search literals and census excerpts"),
    ("repo", "runcoach-api/tests/support/withdrawn_phrasings.py", "search literals and census excerpts"),
)
#: F011's ``ROOT_FLOORS`` as they stood when D13's two records were added (58 live files): neither file
#: lies under an F011 root, so no floor moved.
F011_FLOORS_AT_D13 = (
    ("spec", 9), ("decisions", 1), ("spec_star", 2), ("future", 1), ("research", 6),
    ("contracts", 3), ("src", 20), ("rules", 13), ("spec_mirror", 3),
)

_MENTION = re.compile(r"research[_/]00")
_SECTION_TOKEN = re.compile(r"§\s?(?:1\.\d|3\.\d|5\.4)\b")
#: A research/00 section number written with or without the sign (M2), **joined to the mention**: right
#: after it (``research/00 Sec 3.3``, ``research/00 Section 1.7``, ``research/00 1.6``, ``research/00 §1.7``)
#: or right before an ``of``/``in`` it (``Section 1.7 of research/00``). The join is the whole test: a
#: "Section 3.x" merely on the mention's line or the next one is research/02's (quality_gates.py,
#: rr_reconstruction.py), spec/03's or spec/01's as often as research/00's, and the walk measured six such
#: false findings when the mention and the number were only required to share a line or a line pair.
#:
#: S5 widened it to the spellings the repo used at 099b1cd, in any case: the file's own name or a possessive
#: after the mention (``research/00-design-decisions.md Section 1.6``, ``research/00's Sec 5.4``), ``Sections``
#: (the first number of ``Sections 1.6 and 1.7``), ``sec. 1.6``, and ``Section 1.6 of the research/00 ...``.
#: A number is a section only when no digit and no ``.<digit>`` follows it (``1.10``, ``1.2.0``, ``5.4.1``); the
#: full stop that ends a sentence does not stop it (G1: ``research/00 Section 1.6.``). A sign-less one is a section
#: only when no unit follows it (``research/00 3.3 ms``).
#:
#: Both tokens read research/00 itself (S10): the decisions file by its short name or its own file name, never
#: research/00-history.md, -traceability.md or -meaning-review.md, whose sections, lines and findings are not
#: research/00's. A possessive may follow, straight, curly (U+2019) or after a closing backtick (S9).
_RESEARCH00_ITSELF = r"research[_/]00(?:-design-decisions(?:\.md)?)?(?![\w-])"
_POSSESSIVE = r"[`'\"*]*(?:['’]s\b)?"
_SECTION_NUMBER = r"(?:1\.\d|3\.\d|5\.[1-4])(?!\d|\.\d)"
_SECTION_WORD = r"(?:§\s?|\bSec\.?\s*|\bSections?\s)"
_NOT_A_UNIT = r"(?!\s*(?:ms|s|%|bpm|days?|mornings?|readings?|weeks?|x)\b)"
_ADJACENT_SECTION_TOKEN = re.compile(
    _RESEARCH00_ITSELF + _POSSESSIVE + r"\W{1,4}(?:" + _SECTION_WORD + _SECTION_NUMBER + "|" + _SECTION_NUMBER + _NOT_A_UNIT + ")"
    r"|" + _SECTION_WORD + _SECTION_NUMBER + r"(?:\s*(?:,|and|&|or)\s*(?:§\s?)?" + _SECTION_NUMBER + ")*"
    r"\W{1,4}(?:of|in)\W{1,3}(?:the\s+)?" + _RESEARCH00_ITSELF,
    re.IGNORECASE)
#: N4: a locator the rewrite retired, joined to the mention of research/00 itself: a line number
#: (``research/00``:219, ``research/00:220``, ``research/00-design-decisions.md:970``, and since S9 ``research/00
#: line 219`` and ``research/00 (L219)``) or a finding (``the six research/00 finding 2 names``, and since S9
#: ``research/00's finding 2`` and ``research/00 findings 2 and 3``).
_LOCATOR_TOKEN = re.compile(
    _RESEARCH00_ITSELF + _POSSESSIVE
    + r"(?::\d{1,4}\b|\W{0,4}(?:findings?\s*#?\s*\d+|lines?\s*\d{1,4}\b|L\d{1,4}\b))", re.IGNORECASE)
#: Python's implicit string concatenation where a line pair is joined: ``"... (research/00 "`` + ``"1.6)."``.
_STRING_JOIN = re.compile(r"\"\s*\"")
_PART_TOKEN = re.compile(r"\bPart [1-5]\b")
_SPEC_PREFIX = re.compile(r"spec/0\d")
_FORBIDDEN_DIRECTION = re.compile(r"forbid|weak evidence|readiness[- ]intact", re.IGNORECASE)
_TOLERATED = re.compile(r"tolerat", re.IGNORECASE)
_RETIRED_SAID = re.compile(r"retire", re.IGNORECASE)
_STATUS = re.compile(r"^status:\s*[\"']?([A-Za-z_-]+)[\"']?\s*$", re.MULTILINE)
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def _show(value) -> str:
    """A witness safe for a cp1252 console: non-ASCII escaped, never dropped."""
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


# --------------------------------------------------------------------------------------------------
# AC1: the data dir, the walk and the classification.
# --------------------------------------------------------------------------------------------------

def locate_data_dir(env=None, repo_root: Path = _REPO_ROOT) -> Path:
    """The Shipyard data dir: ``$SHIPYARD_DATA_DIR`` when set, else the ``.shipyard`` breadcrumb, the
    first of them with ``spec/`` under it. Neither is a **failure**, never a skip (F009's probe fails on
    a skip): a gate that skips without its data dir reports all-clear over half its population."""
    env = os.environ if env is None else env
    named = env.get(DATA_DIR_ENV)
    candidates = ([Path(named)] if named else []) + [repo_root / ".shipyard"]
    for candidate in candidates:
        if (candidate / "spec").is_dir():
            return candidate
    pytest.fail(f"the citation gate needs the Shipyard data dir and found none: ${DATA_DIR_ENV} is "
                f"{named!r} and {repo_root / '.shipyard'} has no spec/ under it; set {DATA_DIR_ENV} "
                f"(this is a failure, not a skip)")


def walk(base: Path, prefix: str = ""):
    """``(rel, path)`` for every text-suffixed file under ``base``, ``rel`` a ``/``-separated path with
    ``prefix`` in front: ``SKIPPED_DIRS`` and nested checkouts (S2) are not entered, ``*.bak`` and
    dot-prefixed names are skipped (D12)."""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS
                             and not (Path(dirpath) / d / NESTED_CHECKOUT_MARKER).exists())
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix not in TEXT_SUFFIXES or name.startswith(".") or name.endswith(".bak"):
                continue
            yield prefix + path.relative_to(base).as_posix(), path


def in_scope(raw: str) -> bool:
    """AC1's population: the text mentions research/00, carries a section token, or cites a rule ID."""
    return bool(_MENTION.search(raw) or _SECTION_TOKEN.search(raw) or _RULE_ID.search(raw))


def frontmatter_status(raw: str) -> str | None:
    """The ``status:`` of a YAML frontmatter block, or None where there is no block or no status."""
    block = _FRONTMATTER.match(raw)
    if not block:
        return None
    status = _STATUS.search(block.group(1))
    return status.group(1) if status else None


def is_citation_record(space: str, path: str) -> bool:
    return any(row_space == space and fnmatch.fnmatchcase(path, prefix + "*")
               for row_space, prefix, _reason in CITATION_RECORDS)


def classify(space: str, path: str, raw: str) -> tuple[str, str]:
    """``("record", why)`` or ``("live", "")``: by path (``RECORD_SET``, then ``CITATION_RECORDS``), then
    by frontmatter status (D12). Section records are ranges inside a live file, not a kind."""
    if is_record(space, path):
        return "record", "RECORD_SET"
    if is_citation_record(space, path):
        return "record", "CITATION_RECORDS"
    status = frontmatter_status(raw)
    if status in RECORD_STATUSES:
        return "record", f"status: {status}"
    return "live", ""


def root_of(space: str, path: str) -> str:
    """The root that owns a file. A ``spec-mirror/`` copy is the ``data`` root's: it is the data-dir original
    in a second place, and its rows are written once, for the original (T236)."""
    if space == "data" or path.startswith(MIRROR_PREFIX):
        return "data"
    if path == HRV_TREND:
        return "hrv_trend"
    if path.startswith("specification/"):
        return "spec"
    if path.startswith("runcoach-api/") and path.endswith(".py"):
        return "python"
    return "misc"


def section_record_ranges(space: str, path: str, raw: str) -> list[tuple[int, int]]:
    """F011's ``record_ranges`` for this file. The frozen ``SECTION_RECORD_FILES`` names spec-mirror copies;
    the data-dir original ``spec/<x>`` of a mirrored ``spec-mirror/<x>`` is read under the same entry."""
    key = path
    if space == "data" and path.startswith("spec/"):
        mirrored = "spec-mirror/" + path[len("spec/"):]
        if mirrored in SECTION_RECORD_FILES:
            key = mirrored
    return record_ranges(key, raw)


def newline_lines(raw: str) -> list[str]:
    """The file's lines as ``git grep -n`` numbers them (S1): split on ``\\n`` alone, ``\\r\\n`` folded, never
    on the U+2028, U+2029, form feed or NEL that ``str.splitlines`` also breaks on."""
    lines = raw.replace("\r\n", "\n").split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def live_lines(space: str, path: str, raw: str) -> list[tuple[int, str]]:
    """``(1-based line number, text)`` for every line outside the file's section records, numbered by
    ``newline_lines``."""
    ranges = section_record_ranges(space, path, raw)
    lines, offset = [], 0
    for number, segment in enumerate(raw.split("\n"), start=1):
        start, offset = offset, offset + len(segment) + 1
        if start >= len(raw):
            break
        if not any(a <= start < b for a, b in ranges):
            lines.append((number, segment.removesuffix("\r")))
    return lines


@dataclass(frozen=True)
class Site:
    """One in-scope file: where it is, which root owns it, and whether it is live or a record."""
    space: str
    path: str
    root: str
    kind: str
    reason: str

    @property
    def ident(self) -> str:
        return f"{self.space}:{self.path}"


@dataclass(frozen=True)
class Row:
    """One CSV row, with the CSV it came from (its stem names the root) and its 1-based row number."""
    source: str
    number: int
    space: str
    file: str
    line: str
    old_citation: str
    new_id: str

    @property
    def ident(self) -> str:
        return f"{self.source}.csv:{self.number} ({self.space}:{self.file}:{self.line} -> {self.new_id})"


@dataclass(frozen=True)
class World:
    repo_root: Path
    data_dir: Path
    sites_dir: Path
    sites: tuple[Site, ...]
    texts: dict[str, str]
    rows: tuple[Row, ...]
    rule_set: frozenset[str]
    pending: frozenset[str]

    def live(self, root: str | None = None) -> list[Site]:
        return [s for s in self.sites if s.kind == "live" and (root is None or s.root == root)]

    def text(self, site: Site) -> str:
        return self.texts[site.ident]

    def listed(self) -> set[str]:
        return {f"{r.space}:{r.file}" for r in self.rows}

    def literal_lines(self) -> set[str]:
        return {f"{r.space}:{r.file}:{r.line}" for r in self.rows if r.new_id == LITERAL}

    def listed_lines(self) -> set[str]:
        return {f"{r.space}:{r.file}:{r.line}" for r in self.rows}


def read_rows(sites_dir: Path) -> tuple[Row, ...]:
    """Every row of every ``*.csv`` under ``sites_dir``; a header that is not ``CSV_COLUMNS`` is an error."""
    rows: list[Row] = []
    for csv_path in sorted(sites_dir.glob("*.csv")) if sites_dir.is_dir() else []:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            table = list(csv.reader(handle))
        assert table and tuple(table[0]) == CSV_COLUMNS, f"{csv_path.name}: header {table[:1]} is not {CSV_COLUMNS}"
        for number, cells in enumerate(table[1:], start=2):
            assert len(cells) == len(CSV_COLUMNS), f"{csv_path.name}:{number}: {len(cells)} cells"
            rows.append(Row(csv_path.stem, number, *(cell.strip() for cell in cells)))
    return tuple(rows)


def pending_roots(sites_dir: Path) -> frozenset[str]:
    """The roots whose ``pending-<root>.marker`` is still there."""
    if not sites_dir.is_dir():
        return frozenset()
    return frozenset(p.name[len("pending-"):-len(".marker")] for p in sites_dir.glob("pending-*.marker"))


def build_world(repo_root: Path, data_dir: Path, sites_dir: Path) -> World:
    """Walk both spaces, keep the in-scope files, classify each, and read the CSVs and the markers."""
    sites: list[Site] = []
    texts: dict[str, str] = {}
    spaces = (("repo", repo_root, ""), ("data", data_dir / "spec", "spec/"))
    for space, base, prefix in spaces:
        for rel, path in walk(base, prefix):
            raw = path.read_text(encoding="utf-8", errors="replace")
            if not in_scope(raw):
                continue
            kind, reason = classify(space, rel, raw)
            site = Site(space, rel, root_of(space, rel), kind, reason)
            sites.append(site)
            texts[site.ident] = raw
    research = repo_root / RESEARCH_00
    rule_set = frozenset(rule_ids(research.read_text(encoding="utf-8"))) if research.is_file() else frozenset()
    return World(repo_root, data_dir, sites_dir, tuple(sites), texts, read_rows(sites_dir), rule_set,
                 pending_roots(sites_dir))


# --------------------------------------------------------------------------------------------------
# AC3 and AC4: the per-root families and the row checks.
# --------------------------------------------------------------------------------------------------

def _cites(line: str) -> bool:
    """The line carries a research/00 citation: a section or Part token, a rule ID, or the mention."""
    return bool(_SECTION_TOKEN.search(line) or _PART_TOKEN.search(line) or _RULE_ID.search(line)
                or _MENTION.search(line))


def _joined_token(text: str) -> bool:
    """A section number joined to the mention (M2, S5) or a retired locator joined to it (N4)."""
    return bool(_ADJACENT_SECTION_TOKEN.search(text) or _LOCATOR_TOKEN.search(text))


def _cites_a_research00_section(line: str) -> bool:
    """One line cites a research/00 section: the mention with a ``§`` or Part token anywhere on the line
    (T245), or a section number, signed or not, or a line or finding locator joined to the mention (M2,
    S5, N4: ``_joined_token``)."""
    return bool(_MENTION.search(line) and (_SECTION_TOKEN.search(line) or _PART_TOKEN.search(line))
                or _joined_token(line))


def _joined(line: str, following: str) -> str:
    """Two adjacent lines as one, so a mention and its number split by a line break meet (M2): the break
    and the indent become one space, and a Python implicit string concatenation (``" "``) is closed."""
    return _STRING_JOIN.sub("", f"{line.rstrip()} {following.lstrip()}")


def _aliases(key: str) -> frozenset[str]:
    """``key`` (``space:file`` or ``space:file:line``) and, for a mirrored document, the same key on its other
    copy: ``repo:spec-mirror/<x>`` <-> ``data:spec/<x>``. A row for either copy covers both (T236)."""
    space, _, rest = key.partition(":")
    if space == "repo" and rest.startswith(MIRROR_PREFIX):
        return frozenset({key, "data:spec/" + rest[len(MIRROR_PREFIX):]})
    if space == "data" and rest.startswith("spec/"):
        return frozenset({key, "repo:" + MIRROR_PREFIX + rest[len("spec/"):]})
    return frozenset({key})


def root_findings(world: World, root: str) -> dict[str, list[str]]:
    """The root's red, by family. ``unlisted``: a live file with a citation token on a live line and no
    CSV row, or a live line of a listed file carrying the ``research/00`` mention and a section or Part
    token with no row of its own (T245; a bare token with no mention is the migrating task's), or a
    section number joined to the mention without the sign, on the line or across it and the next (M2,
    ``_ADJACENT_SECTION_TOKEN``; a row on either line of the pair covers it). ``clause3``
    (AC3, ``.py`` under ``runcoach-api/``): a ``§1.x``/``§3.x``/``§5.4`` token on a
    live line without a ``spec/0N`` prefix and not a ``literal`` row. ``resolve`` (AC4): a rule ID on a
    live line that is no rule line of research/00 (a retired ID passes on a line that says "retired").
    ``forbidden`` (AC4): a line citing research/00 that names the forbidden direction and not PRIN-14.
    ``tolerated`` (AC4): a line saying "tolerated" that cites PRIN-14 or a retired ID. A ``literal``
    row's line is exempt from every per-line family."""
    listed, literal, listed_lines = world.listed(), world.literal_lines(), world.listed_lines()
    findings: dict[str, list[str]] = {"unlisted": [], "clause3": [], "resolve": [], "forbidden": [], "tolerated": []}
    for site in world.live(root):
        cites_a_section = False
        is_py_under_api = site.space == "repo" and site.path.endswith(".py") and site.path.startswith("runcoach-api/")
        lines = live_lines(site.space, site.path, world.text(site))
        for index, (number, line) in enumerate(lines):
            where = f"{site.ident}:{number}"
            following = lines[index + 1][1] if index + 1 < len(lines) and lines[index + 1][0] == number + 1 else None
            own = _cites_a_research00_section(line)
            paired = (following is not None and not own and not _cites_a_research00_section(following)
                      and _joined_token(_joined(line, following)))
            if _SECTION_TOKEN.search(line) or _PART_TOKEN.search(line) or own or paired:
                cites_a_section = True
            if _aliases(where) & literal:
                continue
            covered = _aliases(where) & listed_lines or (paired and _aliases(f"{site.ident}:{number + 1}") & listed_lines)
            if _aliases(site.ident) & listed and (own or paired) and not covered:
                findings["unlisted"].append(
                    f"{where}: cites research/00 with a section token and has no row in any CSV")
            if is_py_under_api and _SECTION_TOKEN.search(line) and not _SPEC_PREFIX.search(line):
                findings["clause3"].append(f"{where}: bare {_show(_SECTION_TOKEN.search(line).group(0))} without a spec/0N prefix")
            for rule_id in _RULE_ID.findall(line):
                if rule_id in world.rule_set:
                    continue
                if rule_id in RETIRED_IDS and _RETIRED_SAID.search(line):
                    continue
                findings["resolve"].append(f"{where}: {rule_id} is not a rule line of research/00")
            if _FORBIDDEN_DIRECTION.search(line) and _cites(line) and "PRIN-14" not in line:
                findings["forbidden"].append(f"{where}: names the forbidden direction without citing PRIN-14")
            if _TOLERATED.search(line) and ("PRIN-14" in line or any(r in line for r in RETIRED_IDS)):
                findings["tolerated"].append(f"{where}: says tolerated and cites PRIN-14 or a retired ID")
        if cites_a_section and not _aliases(site.ident) & listed:
            findings["unlisted"].append(f"{site.ident}: cites a research/00 section and has no row in any CSV")
    return findings


def _squash(text: str) -> str:
    return _WHITESPACE.sub("", text)


def row_findings(world: World) -> list[str]:
    """Each CSV row's red: an unknown space or root-mismatched file, a file the walk did not classify live,
    a line number off the file, a non-literal row whose line still carries ``old_citation`` (whitespace
    ignored) or lacks ``new_id``, or a ``new_id`` that is neither ``literal`` nor a rule line."""
    by_ident = {site.ident: site for site in world.sites}
    findings: list[str] = []
    for row in world.rows:
        if row.source not in ROOTS:
            findings.append(f"{row.ident}: {row.source}.csv names no root")
            continue
        if row.space not in ("repo", "data"):
            findings.append(f"{row.ident}: space must be repo or data")
            continue
        if root_of(row.space, row.file) != row.source:
            findings.append(f"{row.ident}: the file belongs to root {root_of(row.space, row.file)}")
        site = by_ident.get(f"{row.space}:{row.file}")
        if site is None:
            findings.append(f"{row.ident}: the walk classified no such in-scope file")
            continue
        if site.kind == "record":
            findings.append(f"{row.ident}: a record's citations are not rewritten ({site.reason})")
            continue
        lines = newline_lines(world.text(site))
        if not row.line.isdigit() or not 1 <= int(row.line) <= len(lines):
            findings.append(f"{row.ident}: line is not 1..{len(lines)}")
            continue
        if row.new_id == LITERAL:
            continue
        line = lines[int(row.line) - 1]
        if not (_RULE_ID.fullmatch(row.new_id) and row.new_id in world.rule_set):
            findings.append(f"{row.ident}: {row.new_id} is neither literal nor a rule line of research/00")
        if row.new_id not in line:
            findings.append(f"{row.ident}: the line does not carry {row.new_id}")
        if row.old_citation and _squash(row.old_citation) in _squash(line):
            findings.append(f"{row.ident}: the line still carries {_show(row.old_citation)}")
    return findings


def root_params(sites_dir: Path = SITES_DIR) -> list:
    """One param per root; a strict xfail while its ``pending-<root>.marker`` exists (S15), bare once gone."""
    pending = pending_roots(sites_dir)
    return [pytest.param(root, id=root,
                         marks=(pytest.mark.xfail(strict=True, reason=f"pending-{root}.marker: the {root} root's "
                                                  f"citations are not yet migrated (S15)"),) if root in pending else ())
            for root in ROOTS]


# ==================================================================================================
# The gate on the checkout.
# ==================================================================================================

@pytest.fixture(scope="module")
def world() -> World:
    return build_world(_REPO_ROOT, locate_data_dir(), SITES_DIR)


def test_every_matching_file_is_classified_live_or_record(world):
    """AC1: every in-scope file of both spaces is live or a record, and the count is printed. The floor
    (200) is below the 242 this gate classified at 9b11b9c, so a walk that stops descending is red."""
    counts = {(s.space, s.kind) for s in world.sites}
    print(f"classified {len(world.sites)} files")
    for space in ("repo", "data"):
        live = sum(1 for s in world.sites if s.space == space and s.kind == "live")
        records = [s for s in world.sites if s.space == space and s.kind == "record"]
        reasons = sorted({s.reason for s in records})
        print(f"  {space}: {live} live, {len(records)} record "
              f"({', '.join(f'{r} {sum(1 for s in records if s.reason == r)}' for r in reasons)})")
    for root in ROOTS:
        print(f"  root {root}: {len(world.live(root))} live")
    design = [s for s in world.sites if s.ident == f"repo:{RESEARCH_00}"]
    print(f"[slice compared] {len(world.sites)} in-scope files against the two kinds {sorted(counts)}; "
          f"research/00 itself {[(s.kind, s.reason) for s in design]}; data paths outside spec/: "
          f"{[s.path for s in world.sites if s.space == 'data' and not s.path.startswith('spec/')]}")
    assert len(world.sites) >= 200
    assert {s.kind for s in world.sites} == {"live", "record"}
    assert design and design[0].kind == "record" and design[0].reason == "CITATION_RECORDS"
    assert all(len(world.live(root)) >= 1 for root in ROOTS)
    assert all(s.path.startswith("spec/") for s in world.sites if s.space == "data")
    assert len(world.rule_set) >= 200


def test_record_set_gained_the_literal_bearing_modules_without_moving_a_floor():
    """D13: both modules are ``RECORD_SET`` rows with the ruling's reason, both lie outside every F011
    root (``live_files`` never saw them), and ``ROOT_FLOORS`` is what it was."""
    rows = {(space, prefix): reason for space, prefix, reason in RECORD_SET}
    walked = sorted(p for paths in _DS.live_files(_REPO_ROOT).values() for p in paths)
    seen = [prefix for _space, prefix, _reason in D13_RECORDS if prefix in walked]
    print(f"[slice compared] D13 rows {[(rows.get((s, p)) == r) for s, p, r in D13_RECORDS]}; under an F011 root "
          f"{seen}; floors {_DS.ROOT_FLOORS} against {F011_FLOORS_AT_D13}; shortfalls "
          f"{_DS.floor_shortfalls(_DS.live_files(_REPO_ROOT))}")
    assert all(rows.get((space, prefix)) == reason for space, prefix, reason in D13_RECORDS)
    assert seen == []
    assert tuple(_DS.ROOT_FLOORS) == F011_FLOORS_AT_D13
    assert _DS.floor_shortfalls(_DS.live_files(_REPO_ROOT)) == []


def test_design_decisions_is_a_citation_record_by_ruling_not_a_shared_record():
    """D19: research/00 is a record for this gate alone. The shared set does not hold it, and F011's
    ``research`` root (``0[1-6]-*``) would exclude it anyway, so either home moves no floor."""
    research_root = next(r for r in _DS.ROOTS if r[0] == "research")
    glob_excludes = not fnmatch.fnmatchcase(Path(RESEARCH_00).name, research_root[3])
    print(f"[slice compared] shared is_record {is_record('repo', RESEARCH_00)}; CITATION_RECORDS "
          f"{is_citation_record('repo', RESEARCH_00)}; F011 research root glob {research_root[3]!r} excludes it {glob_excludes}")
    assert not is_record("repo", RESEARCH_00)
    assert is_citation_record("repo", RESEARCH_00)
    assert glob_excludes
    assert RESEARCH_00 not in _DS.live_files(_REPO_ROOT)["research"]


def test_the_sites_directory_holds_one_csv_per_root_with_the_contract_header():
    """D20: five CSVs, one per root, each with the header; every marker names a root; no stray CSV."""
    csvs = sorted(p.stem for p in SITES_DIR.glob("*.csv"))
    headers = {p.stem: p.read_text(encoding="utf-8").splitlines()[:1] for p in SITES_DIR.glob("*.csv")}
    markers = sorted(pending_roots(SITES_DIR))
    print(f"[slice compared] CSVs {csvs} against roots {sorted(ROOTS)}; headers {headers}; markers {markers}")
    assert csvs == sorted(ROOTS)
    assert all(header == [",".join(CSV_COLUMNS)] for header in headers.values())
    assert set(markers) <= set(ROOTS)


@pytest.mark.parametrize("root", root_params())
def test_root_citations_are_listed_migrated_and_resolve(world, root):
    """AC3 and AC4 for one root, every family at once: a strict xfail while ``pending-<root>.marker``
    exists, a hard failure once it is gone (an unlisted live file cannot hide behind an xfail)."""
    findings = root_findings(world, root)
    for family, items in findings.items():
        print(f"[slice compared] {root}/{family}: {len(world.live(root))} live files, {len(items)} findings; "
              f"first {_show(items[:3])}")
    marker = SITES_DIR / f"pending-{root}.marker"
    print(f"  marker {marker.name} exists: {marker.exists()}")
    assert all(items == [] for items in findings.values()), _show(
        {family: items[:10] for family, items in findings.items() if items})


def test_every_citation_row_is_valid(world):
    """AC3: every row of every CSV names a live file of its root, a line within it that no longer carries
    the old token and does carry a ``new_id`` resolving to a rule line, or is ``literal``."""
    findings = row_findings(world)
    print(f"[slice compared] {len(world.rows)} rows in {sorted({r.source for r in world.rows})}; "
          f"{sum(r.new_id == LITERAL for r in world.rows)} literal; findings {_show(findings[:10])}")
    assert findings == [], _show(findings)


# ==================================================================================================
# Worlds: each mechanism shown red and green in a tmp tree.
# ==================================================================================================

_RESEARCH_00_WORLD = (
    "# research/00\n\n## Part 1\n\n"
    "**PRIN-14.** No readiness-intact verdict is asserted on evidence the system reports as insufficient.\n\n"
    "**HRV-31.** The withhold is judged against the dataset being judged.\n\n"
    "## Retired IDs\n\n- **PRIN-16** retired → H-39\n"
)
_HRV_999_WORLD_FILE = "specification/spec/03-derived-metric-formulas.md"


def _world(root: Path, repo: dict[str, str] | None = None, data: dict[str, str] | None = None,
           rows: dict[str, list[tuple[str, ...]]] | None = None, markers=ROOTS) -> World:
    """A tmp world: research/00 (``_RESEARCH_00_WORLD``), the given repo and data files (``data`` paths
    are under ``spec/``), one CSV per root with the given rows, and a marker per root in ``markers``."""
    files = {RESEARCH_00: _RESEARCH_00_WORLD, **(repo or {})}
    for rel, text in files.items():
        (root / "repo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "repo" / rel).write_text(text, encoding="utf-8")
    (root / "data" / "spec").mkdir(parents=True, exist_ok=True)
    for rel, text in (data or {}).items():
        (root / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "data" / rel).write_text(text, encoding="utf-8")
    sites = root / "sites"
    sites.mkdir()
    for name in ROOTS:
        lines = [",".join(CSV_COLUMNS)] + [",".join(r) for r in (rows or {}).get(name, [])]
        (sites / f"{name}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if name in markers:
            (sites / f"pending-{name}.marker").write_text("pending\n", encoding="utf-8")
    return build_world(root / "repo", root / "data", sites)


def test_a_changed_citation_to_hrv_999_fails_and_names_the_line(tmp_path):
    """F009 AC4's perturbation: a world whose one live citation says ``PRIN-14`` passes the resolver; the
    same world with that citation changed to ``HRV-999`` fails, and the finding names the file and line.
    The row check names a ``new_id`` of ``HRV-999`` the same way."""
    text = "# spec\n\nUp-regulation on weak evidence is what {} forbids.\n"
    good = _world(tmp_path / "good", repo={_HRV_999_WORLD_FILE: text.format("PRIN-14")},
                  rows={"spec": [("repo", _HRV_999_WORLD_FILE, "3", "§1.7", "PRIN-14")]})
    bad = _world(tmp_path / "bad", repo={_HRV_999_WORLD_FILE: text.format("HRV-999")},
                 rows={"spec": [("repo", _HRV_999_WORLD_FILE, "3", "§1.7", "HRV-999")]})
    good_root, bad_root = root_findings(good, "spec"), root_findings(bad, "spec")
    good_rows, bad_rows = row_findings(good), row_findings(bad)
    print(f"[slice compared] PRIN-14 world: root {good_root}, rows {good_rows}; HRV-999 world: root "
          f"{_show(bad_root)}, rows {_show(bad_rows)}")
    assert all(items == [] for items in good_root.values()) and good_rows == []
    assert bad_root["resolve"] == [f"repo:{_HRV_999_WORLD_FILE}:3: HRV-999 is not a rule line of research/00"]
    assert bad_root["forbidden"] == [f"repo:{_HRV_999_WORLD_FILE}:3: names the forbidden direction without citing PRIN-14"]
    assert len(bad_rows) == 1 and f"spec.csv:2 (repo:{_HRV_999_WORLD_FILE}:3 -> HRV-999)" in bad_rows[0]


def test_a_released_heading_makes_the_changelog_entry_a_record(tmp_path):
    """D11 in the walk: a ``§1.7`` under a dated sprint heading is inside a section record, so the
    CHANGELOG is not unlisted; the same token under ``## Unreleased`` makes it unlisted (misc root)."""
    released = "# Changelog\n\n## Unreleased\n- nothing\n\n## 2026-09-28 through 2026-09-30 — Sprint 008: x\n- §1.7 forbids\n"
    unreleased = "# Changelog\n\n## Unreleased\n- §1.7 forbids\n\n## 2026-09-28 through 2026-09-30 — Sprint 008: x\n- done\n"
    a = root_findings(_world(tmp_path / "a", repo={"CHANGELOG.md": released}), "misc")
    b = root_findings(_world(tmp_path / "b", repo={"CHANGELOG.md": unreleased}), "misc")
    print(f"[slice compared] released heading: {_show(a)}; unreleased: {_show(b)}")
    assert a["unlisted"] == [] and a["forbidden"] == []
    assert b["unlisted"] == ["repo:CHANGELOG.md: cites a research/00 section and has no row in any CSV"]
    assert b["forbidden"] == ["repo:CHANGELOG.md:4: names the forbidden direction without citing PRIN-14"]


def test_the_literal_disposition_is_accepted_not_resolved_and_keeps_its_old_token(tmp_path):
    """D13: a ``literal`` row's line may still carry its old token, is not resolved, and is exempt from the
    per-line families; the same row with a rule ID is red on the token still there."""
    py = "runcoach-api/tests/test_hrv_no_regression_gate.py"
    text = '"""gate"""\nPATTERN = "§1.7 forbids"  # research/00 search literal\n'
    literal = _world(tmp_path / "literal", repo={py: text}, rows={"python": [("repo", py, "2", "§1.7", LITERAL)]})
    resolved = _world(tmp_path / "resolved", repo={py: text}, rows={"python": [("repo", py, "2", "§1.7", "PRIN-14")]})
    lit_rows, lit_root = row_findings(literal), root_findings(literal, "python")
    res_rows, res_root = row_findings(resolved), root_findings(resolved, "python")
    print(f"[slice compared] literal row: rows {lit_rows}, root {_show(lit_root)}; PRIN-14 row on the same line: "
          f"rows {_show(res_rows)}, root {_show(res_root)}")
    assert lit_rows == [] and all(items == [] for items in lit_root.values())
    assert sorted(res_rows) == sorted([
        f"python.csv:2 (repo:{py}:2 -> PRIN-14): the line does not carry PRIN-14",
        f"python.csv:2 (repo:{py}:2 -> PRIN-14): the line still carries \\xa71.7",
    ])
    assert res_root["clause3"] == [f"repo:{py}:2: bare \\xa71.7 without a spec/0N prefix"]
    assert res_root["forbidden"] == [f"repo:{py}:2: names the forbidden direction without citing PRIN-14"]


def test_record_frontmatter_released_counts_as_record(tmp_path):
    """D12: ``status: released`` joins ``done`` and ``completed``; ``approved`` stays live, and a file with
    no frontmatter is live."""
    task = "---\nid: T9\nstatus: {}\n---\n\n# T9\n\nsee research/00 §1.7\n"
    world = _world(tmp_path, data={f"spec/tasks/T9-{s}.md": task.format(s) for s in ("released", "done", "completed", "approved")}
                   | {"spec/ideas/IDEA-1.md": "# idea\n\nresearch/00 §1.7\n"})
    kinds = {s.path: (s.kind, s.reason) for s in world.sites if s.space == "data"}
    print(f"[slice compared] data kinds {kinds}")
    assert kinds == {
        "spec/tasks/T9-released.md": ("record", "status: released"),
        "spec/tasks/T9-done.md": ("record", "status: done"),
        "spec/tasks/T9-completed.md": ("record", "status: completed"),
        "spec/tasks/T9-approved.md": ("live", ""),
        "spec/ideas/IDEA-1.md": ("live", ""),
    }
    assert sorted(f.split(":")[1] for f in root_findings(world, "data")["unlisted"]) == [
        "spec/ideas/IDEA-1.md", "spec/tasks/T9-approved.md"]


def test_marker_gone_hard_fails_on_an_unlisted_live_file(tmp_path):
    """S15's end: with ``pending-spec.marker`` the spec root's param is a strict xfail; without it the param
    carries no mark, and the unlisted live file is a plain failure of the same assertion."""
    spec_file = "specification/spec/06-adaptation-logic.md"
    text = "# spec\n\nresearch/00 §1.7 governs this.\n"
    pending = _world(tmp_path / "pending", repo={spec_file: text}, markers=ROOTS)
    gone = _world(tmp_path / "gone", repo={spec_file: text}, markers=())
    marks = {name: [m.name for p in root_params(w.sites_dir) if p.id == "spec" for m in p.marks]
             for name, w in (("pending", pending), ("gone", gone))}
    findings = root_findings(gone, "spec")
    print(f"[slice compared] spec param marks {marks}; findings without the marker {_show(findings)}")
    assert marks == {"pending": ["xfail"], "gone": []}
    assert findings["unlisted"] == [f"repo:{spec_file}: cites a research/00 section and has no row in any CSV"]
    assert pending.pending == frozenset(ROOTS) and gone.pending == frozenset()
    with pytest.raises(AssertionError):
        assert all(items == [] for items in findings.values())


def test_an_unlisted_line_in_a_listed_file_is_a_finding(tmp_path):
    """T245 (the CSV contract per line): listing one line of a file does not list the file. A second live
    line carrying the ``research/00`` mention and a section token with no row is ``unlisted``; the same
    line under a ``literal`` row is exempt; a bare token with no mention stays with the migrating task."""
    spec_file = "specification/spec/06-adaptation-logic.md"
    text = "# spec\n\nPRIN-14 governs this (research/00).\n\nresearch/00 §1.7 governs that too.\n\n§1.7 alone.\n"
    listed_row = ("repo", spec_file, "3", "§1.7", "PRIN-14")
    one = _world(tmp_path / "one", repo={spec_file: text}, rows={"spec": [listed_row]})
    both = _world(tmp_path / "both", repo={spec_file: text},
                  rows={"spec": [listed_row, ("repo", spec_file, "5", "§1.7", LITERAL)]})
    one_root, both_root = root_findings(one, "spec"), root_findings(both, "spec")
    print(f"[slice compared] one row listed: {_show(one_root)}, rows {row_findings(one)}; line 5 literal: "
          f"{_show(both_root)}, rows {row_findings(both)}")
    assert row_findings(one) == [] and row_findings(both) == []
    assert one_root["unlisted"] == [
        f"repo:{spec_file}:5: cites research/00 with a section token and has no row in any CSV"]
    assert both_root["unlisted"] == []
    assert one_root["clause3"] == [] and one_root["resolve"] == [] and one_root["forbidden"] == []


def test_the_gate_fails_rather_than_skips_without_a_data_dir(tmp_path):
    """AC1: no ``SHIPYARD_DATA_DIR`` and no ``.shipyard`` with ``spec/`` is a failure naming the variable,
    never a skip; a directory with ``spec/`` under it is found by either route."""
    (tmp_path / "found" / "spec").mkdir(parents=True)
    (tmp_path / "empty").mkdir()
    (tmp_path / "repo" / ".shipyard" / "spec").mkdir(parents=True)
    with pytest.raises(pytest.fail.Exception) as failed:
        locate_data_dir(env={}, repo_root=tmp_path / "bare")
    with pytest.raises(pytest.fail.Exception):
        locate_data_dir(env={DATA_DIR_ENV: str(tmp_path / "empty")}, repo_root=tmp_path / "bare")
    by_env = locate_data_dir(env={DATA_DIR_ENV: str(tmp_path / "found")}, repo_root=tmp_path / "bare")
    by_crumb = locate_data_dir(env={}, repo_root=tmp_path / "repo")
    print(f"[slice compared] failure {str(failed.value)[:120]!r}; by env {by_env.name}; by breadcrumb {by_crumb.name}")
    assert DATA_DIR_ENV in str(failed.value) and "not a skip" in str(failed.value)
    assert not isinstance(failed.value, pytest.skip.Exception)
    assert by_env == tmp_path / "found" and by_crumb == tmp_path / "repo" / ".shipyard"


def test_the_sites_directory_is_a_citation_record_and_no_csv_is_live(world):
    """T236: the by-reading record is not a citer. Every CSV the walk saw under ``SITES_DIR_REL`` is a
    ``CITATION_RECORDS`` record, and the misc root holds none of them."""
    seen = [s for s in world.sites if s.path.startswith(SITES_DIR_REL)]
    print(f"[slice compared] sites-dir files {[(s.path.rsplit('/', 1)[-1], s.kind, s.reason) for s in seen]}; "
          f"misc live {[s.path for s in world.live('misc')]}")
    assert seen, "the walk saw no CSV under the sites directory (they carry old tokens, so they are in scope)"
    assert all(s.kind == "record" and s.reason == "CITATION_RECORDS" for s in seen)
    assert not any(s.path.startswith(SITES_DIR_REL) for s in world.live())


def test_a_mirror_copy_is_the_data_roots_and_its_originals_row_covers_it(tmp_path):
    """T236: the same document under ``data:spec/`` and ``repo:spec-mirror/`` is one site in two places. With
    no row, the data root names both copies unlisted and the misc root neither; one row for the original
    covers both; a ``literal`` row for the mirror copy exempts the original's line too."""
    rel = "references/F006-dataset-model.md"
    old = "# model\n\nThe withhold (research/00 \u00a75.4 (v)).\n"
    new = "# model\n\nThe withhold (research/00 HRV-31).\n"
    pair = lambda doc: {"data": {"spec/" + rel: doc}, "repo": {MIRROR_PREFIX + rel: doc}}  # noqa: E731
    bare = _world(tmp_path / "bare", **pair(old))
    listed = _world(tmp_path / "listed", **pair(new), rows={"data": [("data", "spec/" + rel, "3", "\u00a75.4 (v)", "HRV-31")]})
    literal = _world(tmp_path / "literal", **pair(old), rows={"data": [("repo", MIRROR_PREFIX + rel, "3", "", LITERAL)]})
    bare_data, bare_misc = root_findings(bare, "data"), root_findings(bare, "misc")
    print(f"[slice compared] root_of mirror {root_of('repo', MIRROR_PREFIX + rel)!r}; bare data {_show(bare_data['unlisted'])}, "
          f"bare misc {_show(bare_misc)}; listed {_show(root_findings(listed, 'data'))}, rows {row_findings(listed)}; "
          f"literal {_show(root_findings(literal, 'data'))}, rows {row_findings(literal)}; "
          f"aliases {sorted(_aliases('repo:' + MIRROR_PREFIX + rel))}")
    assert root_of("repo", MIRROR_PREFIX + rel) == "data" and root_of("data", "spec/" + rel) == "data"
    assert sorted(bare_data["unlisted"]) == sorted([
        f"data:spec/{rel}: cites a research/00 section and has no row in any CSV",
        f"repo:{MIRROR_PREFIX}{rel}: cites a research/00 section and has no row in any CSV",
    ])
    assert all(items == [] for items in bare_misc.values())
    assert all(items == [] for items in root_findings(listed, "data").values()) and row_findings(listed) == []
    assert all(items == [] for items in root_findings(literal, "data").values()) and row_findings(literal) == []
    assert _aliases("repo:" + MIRROR_PREFIX + rel) == frozenset({"repo:" + MIRROR_PREFIX + rel, "data:spec/" + rel})
    assert _aliases("repo:CHANGELOG.md") == frozenset({"repo:CHANGELOG.md"})


# ==================================================================================================
# Code review, iteration 1: the sign-less section token (M2), newline-only line numbers (S1), nested
# checkouts (S2) and the pending markers pinned gone (S3).
# ==================================================================================================

_SIGNLESS_PY = "runcoach-api/src/runcoach_api/signless.py"
_SIGNLESS_HEAD = '"""PRIN-14 governs this (research/00)."""\n\n'
#: Each research/00 section citation written without the ``§`` sign, on line 3 (the split form's number
#: is on line 4, joined to its mention by Python's implicit string concatenation).
_SIGNLESS_FORMS = {
    "Sec": _SIGNLESS_HEAD + "#: research/00 Sec 3.3 (two paragraphs), measured 2026-09-18.\n",
    "Section": _SIGNLESS_HEAD + "#: research/00 Section 1.7 forbids it.\n",
    "bare number": _SIGNLESS_HEAD + 'NOTE = "from this and `band` (research/00 1.6)."\n',
    "split": _SIGNLESS_HEAD + 'NOTE = ("from this and `band` (research/00 "\n        "1.6).")\n',
    "Section, before": _SIGNLESS_HEAD + "#: as Section 1.7 of research/00 says.\n",
}
#: The shapes the walk measured as false findings when the number only had to share the mention's line or
#: line pair (each is another document's section beside a research/00 mention), and a section number
#: research/00 does not have.
_SIGNLESS_NEGATIVES = {
    "research/02 Section, same line": _SIGNLESS_HEAD + "# Section 3.4 nor `research/00` names one either\n",
    "research/02 Section, next line": _SIGNLESS_HEAD + '# Section 3.2 says only "filtering", no\n# ratio) nor `research/00` names one\n',
    "spec/03 Sec after a history mention": _SIGNLESS_HEAD + "#: research/00-history.md's H-09 names\n#: spec Sec 2.4.5, Sec 3.7.3/3.7.4.\n",
    "spec section line, mention next": _SIGNLESS_HEAD + "#: ratified by §3.7.1 |\n#: (`research/00` HRV-04, HRV-54)\n",
    "spec/01 section, mention next": _SIGNLESS_HEAD + "#: Section 1 §1.4 / Section 4 §4.7\n#: (`research/00` AUT-02)\n",
    "spec/02 Sec": _SIGNLESS_HEAD + "#: see research/00 and\n#: spec/02 Sec 2.4.5.\n",
}


def test_a_research00_section_cited_without_the_sign_is_a_finding(tmp_path):
    """M2: ``Sec 3.3``, ``Section 1.7``, a bare ``1.6`` right after the mention, and a mention and number
    split across two lines are each a research/00 section citation. In a listed file the line is
    ``unlisted`` until it has a row (a row on either line of the split pair clears it); in an unlisted
    file the file is. research/02's ``Section 3.x`` away from the mention, and a section number research/00
    does not have, are not citations."""
    listed_row = ("repo", _SIGNLESS_PY, "1", "", "PRIN-14")
    seen = {}
    for name, text in {**_SIGNLESS_FORMS, **_SIGNLESS_NEGATIVES}.items():
        slug = name.replace(" ", "-").replace("/", "-")
        listed = root_findings(_world(tmp_path / f"{slug}-listed", repo={_SIGNLESS_PY: text},
                                      rows={"python": [listed_row]}), "python")["unlisted"]
        bare = root_findings(_world(tmp_path / f"{slug}-bare", repo={_SIGNLESS_PY: text}), "python")["unlisted"]
        seen[name] = (listed, bare)
    split_rows = {
        line: root_findings(_world(tmp_path / f"split-row-{line}", repo={_SIGNLESS_PY: _SIGNLESS_FORMS["split"]},
                                   rows={"python": [listed_row, ("repo", _SIGNLESS_PY, line, "1.6", LITERAL)]}),
                            "python")["unlisted"]
        for line in ("3", "4")}
    print(f"[slice compared] per form (listed-file findings, unlisted-file findings): {_show(seen)}; "
          f"split pair with a literal row on line 3 / line 4: {_show(split_rows)}")
    for name in _SIGNLESS_FORMS:
        assert seen[name] == (
            [f"repo:{_SIGNLESS_PY}:3: cites research/00 with a section token and has no row in any CSV"],
            [f"repo:{_SIGNLESS_PY}: cites a research/00 section and has no row in any CSV"]), name
    for name, text in _SIGNLESS_NEGATIVES.items():
        # A ``§`` token anywhere in an unlisted live file still lists the file (the unchanged file-level rule).
        assert seen[name][0] == [] and (seen[name][1] == []) == ("§" not in text), name
    assert split_rows == {"3": [], "4": []}


def test_line_numbers_count_newlines_only(tmp_path):
    """S1: a line is what ``git grep -n`` numbers, so U+2028, U+2029, a form feed and NEL inside a line
    start no new one. A row on the cited line is valid, and an unlisted line is named by its own number."""
    spec_file = "specification/spec/06-adaptation-logic.md"
    text = ("# spec\n\nseparators inside one\x0cline\x85here.\n\n"
            "PRIN-14 governs this (research/00).\n\nresearch/00 §1.7 governs that too.\n")
    world = _world(tmp_path, repo={spec_file: text}, rows={"spec": [("repo", spec_file, "5", "§1.7", "PRIN-14")]})
    rows, unlisted = row_findings(world), root_findings(world, "spec")["unlisted"]
    site = next(s for s in world.live("spec") if s.path == spec_file)
    numbered = [n for n, line in live_lines(site.space, site.path, world.text(site)) if "research/00" in line]
    print(f"[slice compared] rows {_show(rows)}; unlisted {_show(unlisted)}; lines naming research/00 {numbered}")
    assert rows == []
    assert unlisted == [f"repo:{spec_file}:7: cites research/00 with a section token and has no row in any CSV"]
    assert numbered == [5, 7]


def test_the_walk_prunes_a_nested_checkout_but_not_the_directory_around_it(tmp_path):
    """S2: a child directory holding ``.git`` (a file, as a worktree has, or a directory, as a clone has) is
    a second copy of the tree and is not entered; the directory around it is, and so is the walked root,
    which holds ``.git`` itself."""
    cite = "research/00 §1.7\n"
    (tmp_path / ".git").mkdir()
    worktree = tmp_path / ".claude" / "worktrees" / "agent-0123456789abcdef0"
    (worktree / "specification" / "spec").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (worktree / "specification" / "spec" / "03.md").write_text(cite, encoding="utf-8")
    clone = tmp_path / "elsewhere" / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / "notes.md").write_text(cite, encoding="utf-8")
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    for rel in ("top.md", ".claude/rules/rule.md", ".claude/worktrees/notes.md", "elsewhere/kept.md"):
        (tmp_path / rel).write_text(cite, encoding="utf-8")
    walked = sorted(rel for rel, _path in walk(tmp_path))
    print(f"[slice compared] walked {walked}")
    assert walked == [".claude/rules/rule.md", ".claude/worktrees/notes.md", "elsewhere/kept.md", "top.md"]


def test_no_pending_marker_remains():
    """S3: every root is migrated, so no ``pending-<root>.marker`` may come back: a recreated one would turn
    its root's test into a strict xfail and hide its findings (the downstream gate's
    ``test_the_pending_directory_holds_no_csv`` is the model)."""
    pending = pending_roots(SITES_DIR)
    print(f"[slice compared] markers under {SITES_DIR.name}: {sorted(pending)}")
    assert pending == frozenset()


# ==================================================================================================
# Code review, iteration 2: the section spellings this repo used at base (S5) and the line and finding
# locators (N4), each a finding in a listed file and in an unlisted one, beside the shapes that are not.
# ==================================================================================================

#: S5: research/00 section citations as the repo spelled them at 099b1cd, each on line 3 of ``_SIGNLESS_PY``.
_SPELLED_FORMS = {
    "lowercase section": _SIGNLESS_HEAD + "#: as research/00 section 5.4 (iii) says.\n",
    "possessive Sec": _SIGNLESS_HEAD + "#: research/00's Sec 5.4 names the withhold.\n",
    "file name Section": _SIGNLESS_HEAD + "#: specification/research/00-design-decisions.md Section 1.6 asks it.\n",
    "Sections and": _SIGNLESS_HEAD + "#: research/00 Sections 1.6 and 1.7 govern it.\n",
    "sec dot": _SIGNLESS_HEAD + "#: research/00 sec. 1.6 asks it.\n",
    "Section of the": _SIGNLESS_HEAD + "#: Section 1.6 of the research/00 decisions asks it.\n",
    "Sections and, before": _SIGNLESS_HEAD + "#: Sections 1.6 and 1.7 of research/00 govern it.\n",
}
#: N4: a research/00 line locator or a "finding N" after the mention, each on line 3.
_LOCATOR_FORMS = {
    "line locator": _SIGNLESS_HEAD + "#: whose role ``research/00``:219 states normatively.\n",
    "line locator, plain": _SIGNLESS_HEAD + "#: one of them -- `research/00:220` -- is the authority.\n",
    "line locator, file name": _SIGNLESS_HEAD + "#: specification/research/00-design-decisions.md:970 says it.\n",
    "finding": _SIGNLESS_HEAD + "#: the six `research/00` finding 2 names.\n",
    "Finding, capitalised": _SIGNLESS_HEAD + "#: as research/00 Finding 7 says.\n",
}
#: Not research/00 section or locator citations: a measurement, a version and a sub-section number beside the
#: mention, a line locator into another research/00 file, a finding with no mention before it, another
#: research document's finding, and a bare colon.
_SPELLED_NEGATIVES = {
    "a measurement": _SIGNLESS_HEAD + "#: research/00 3.3 ms is the jitter the strap reports.\n",
    "a version": _SIGNLESS_HEAD + "#: research/00 1.2.0 of the schema.\n",
    "a sub-section": _SIGNLESS_HEAD + "#: research/00 5.4.1 is not a section it has.\n",
    "the history file's line": _SIGNLESS_HEAD + "#: research/00-history.md:12 records it.\n",
    "a finding without the mention": _SIGNLESS_HEAD + "#: Load-bearing finding 8 (PRIN-14).\n",
    "research/02's finding": _SIGNLESS_HEAD + "#: research/02 finding 2 says so.\n",
    "a colon and prose": _SIGNLESS_HEAD + "#: research/00: two rules apply.\n",
}


def _unlisted_both_ways(tmp_path: Path, name: str, text: str) -> tuple[list[str], list[str]]:
    """The ``python`` root's ``unlisted`` findings for ``text`` at ``_SIGNLESS_PY``, in a listed file (a row on
    line 1 only) and in an unlisted one."""
    slug = "".join(c if c.isalnum() else "-" for c in name)
    listed = root_findings(_world(tmp_path / f"{slug}-listed", repo={_SIGNLESS_PY: text},
                                  rows={"python": [("repo", _SIGNLESS_PY, "1", "", "PRIN-14")]}), "python")["unlisted"]
    bare = root_findings(_world(tmp_path / f"{slug}-bare", repo={_SIGNLESS_PY: text}), "python")["unlisted"]
    return listed, bare


def test_the_section_spellings_the_repo_used_are_findings_and_numbers_that_are_not_sections_are_not(tmp_path):
    """S5: lowercase, possessive, the file's own name before the section word, ``Sections ... and``, ``sec.``
    and ``Section N of the research/00 ...`` are each a research/00 section citation: ``unlisted`` on line 3
    of a listed file, and the file unlisted when it has no row. A measurement (``3.3 ms``), a version
    (``1.2.0``) and a sub-section research/00 does not have (``5.4.1``) beside the mention are not."""
    seen = {name: _unlisted_both_ways(tmp_path, name, text)
            for name, text in {**_SPELLED_FORMS, **_SPELLED_NEGATIVES}.items()}
    print(f"[slice compared] per form (listed-file findings, unlisted-file findings): {_show(seen)}")
    for name in _SPELLED_FORMS:
        assert seen[name] == (
            [f"repo:{_SIGNLESS_PY}:3: cites research/00 with a section token and has no row in any CSV"],
            [f"repo:{_SIGNLESS_PY}: cites a research/00 section and has no row in any CSV"]), name
    for name in ("a measurement", "a version", "a sub-section"):
        assert seen[name] == ([], []), name


def test_a_line_or_finding_locator_into_research00_is_a_finding(tmp_path):
    """N4: ``research/00:<line>`` (backticked or not, or by the file's own name) and ``finding <N>`` after the
    mention cite research/00 by a locator the rewrite retired: ``unlisted`` until the line has a row. A line
    locator into research/00-history.md, a finding with no mention before it, research/02's finding and a
    colon followed by prose are not."""
    seen = {name: _unlisted_both_ways(tmp_path, name, text)
            for name, text in {**_LOCATOR_FORMS, **_SPELLED_NEGATIVES}.items()}
    literal = root_findings(_world(tmp_path / "literal-row", repo={_SIGNLESS_PY: _LOCATOR_FORMS["line locator, plain"]},
                                   rows={"python": [("repo", _SIGNLESS_PY, "1", "", "PRIN-14"),
                                                    ("repo", _SIGNLESS_PY, "3", "research/00:220", LITERAL)]}),
                            "python")["unlisted"]
    print(f"[slice compared] per form (listed-file findings, unlisted-file findings): {_show(seen)}; "
          f"line locator under a literal row: {_show(literal)}")
    for name in _LOCATOR_FORMS:
        assert seen[name] == (
            [f"repo:{_SIGNLESS_PY}:3: cites research/00 with a section token and has no row in any CSV"],
            [f"repo:{_SIGNLESS_PY}: cites a research/00 section and has no row in any CSV"]), name
    for name in ("the history file's line", "a finding without the mention", "research/02's finding",
                 "a colon and prose"):
        assert seen[name] == ([], []), name
    assert literal == []


# ==================================================================================================
# Code review, iteration 3: a section number that ends a sentence (G1), the possessive, plural and line
# locators (S9), and a section of another research/00 file (S10).
# ==================================================================================================

#: G1: a section number the sentence's full stop follows, each on line 3. Iteration 2's ``(?![.\d])`` refused
#: all of them; only a following digit or ``.<digit>`` is refused now.
_SENTENCE_END_FORMS = {
    "Section, full stop": _SIGNLESS_HEAD + "#: as research/00 Section 1.6.\n",
    "Sec, full stop": _SIGNLESS_HEAD + "#: the rule is in research/00 Sec 5.4.\n",
    "bare number, full stop": _SIGNLESS_HEAD + "#: see `research/00` 1.6. Nothing else.\n",
    "Section of, full stop": _SIGNLESS_HEAD + "#: as Section 1.6 of research/00.\n",
}
#: S9: the locators iteration 2's token missed, each on line 3.
_S9_LOCATOR_FORMS = {
    "possessive finding": _SIGNLESS_HEAD + "#: research/00's finding 2 names it.\n",
    "findings and": _SIGNLESS_HEAD + "#: research/00 findings 2 and 3 name it.\n",
    "curly possessive finding": _SIGNLESS_HEAD + "#: research/00’s finding 2 names it.\n",
    "curly possessive Sec": _SIGNLESS_HEAD + "#: research/00’s Sec 5.4 names it.\n",
    "backticked possessive Sec": _SIGNLESS_HEAD + "#: `research/00`'s Section 1.6 names it.\n",
    "line N": _SIGNLESS_HEAD + "#: research/00 line 219 states it.\n",
    "L-number": _SIGNLESS_HEAD + "#: research/00 (L219) states it.\n",
}
#: Not research/00 section or locator citations: a version and a sub-section the sentence's full stop follows
#: (G1 keeps both refusals), a two-digit number, and a section, number, line or finding of another research/00
#: file (S10).
_ITER3_NEGATIVES = {
    "a version, full stop": _SIGNLESS_HEAD + "#: research/00 1.2.0.\n",
    "a sub-section, full stop": _SIGNLESS_HEAD + "#: research/00 Section 5.4.1.\n",
    "a two-digit number": _SIGNLESS_HEAD + "#: research/00 Section 1.10 is not one it has.\n",
    "history Section": _SIGNLESS_HEAD + "#: research/00-history.md Section 1.6 records it.\n",
    "history possessive Section": _SIGNLESS_HEAD + "#: research/00-history.md's Section 1.6 records it.\n",
    "traceability number": _SIGNLESS_HEAD + "#: research/00-traceability.md 3.3 maps it.\n",
    "meaning review Sec": _SIGNLESS_HEAD + "#: research/00-meaning-review.md Sec 5.4 judged it.\n",
    "Section of the history": _SIGNLESS_HEAD + "#: Section 1.6 of research/00-history.md records it.\n",
    "history line": _SIGNLESS_HEAD + "#: research/00-history.md line 12 records it.\n",
    "meaning review finding": _SIGNLESS_HEAD + "#: research/00-meaning-review's finding 2 records it.\n",
}


def test_sentence_final_possessive_plural_and_line_locators_are_findings_and_other_research00_files_are_not(tmp_path):
    """G1: ``research/00 Section 1.6.``, ``research/00 Sec 5.4.``, ``research/00 1.6.`` and ``Section 1.6 of
    research/00.`` cite a section although the full stop follows the number. S9: ``research/00's finding 2``,
    ``findings 2 and 3``, the curly possessive (U+2019) in both tokens, a backticked possessive, ``line 219``
    and ``L219``. Each is ``unlisted`` on line 3 of a listed file, and the file unlisted when it has no row. A
    version and a sub-section followed by a full stop, a two-digit number, and a section, number, line or
    finding of research/00-history.md, -traceability.md or -meaning-review.md are not (S10)."""
    seen = {name: _unlisted_both_ways(tmp_path, name, text)
            for name, text in {**_SENTENCE_END_FORMS, **_S9_LOCATOR_FORMS, **_ITER3_NEGATIVES}.items()}
    print(f"[slice compared] per form (listed-file findings, unlisted-file findings): {_show(seen)}")
    failed = [name for name in (*_SENTENCE_END_FORMS, *_S9_LOCATOR_FORMS) if seen[name] != (
        [f"repo:{_SIGNLESS_PY}:3: cites research/00 with a section token and has no row in any CSV"],
        [f"repo:{_SIGNLESS_PY}: cites a research/00 section and has no row in any CSV"])]
    failed += [name for name in _ITER3_NEGATIVES if seen[name] != ([], [])]
    assert failed == []
