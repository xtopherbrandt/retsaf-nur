"""F008: research/00 as current rules with stable IDs -- the checkers, the assembler and the review
scope, proven on synthetic text and on one real block per group.

T170 (sprint-007) is the walking skeleton. It writes no research/00 text. Every checker is a pure
function over text, so the four drafting tasks (T171-T174) run it over their ``.txt`` fragments
(``fragment_errors``), the assembly task runs it over all of them together (``assemble_check``), the
cut-over (T175) runs it over the real files, and the critics (T177-T179) take their scope from
``required_review_rows``. The names in the T170 probe's ``need`` list are that interface.

Each checker is proven twice: red on a minimal synthetic violation and green on a minimal valid case,
so a checker that cannot fail is visible here; and green on one hand-written real block per group,
written from its inventory row, so a checker that is too strict for real text is visible here rather
than in four stalled parallel builders (T170 AC4). The real-path cases -- the committed research/00,
history and traceability table -- are added by the cut-over, so the suite never carries an xfail or a
skip (R11).

Authority: ``spec/references/F008-rewrite-decisions.md`` (R1-R11) and the inventory
``spec/references/research00-rewrite-inventory.md`` at commit ``4e47d0e``. Neither is read by the
committed tests: CI has no data dir, so every fact taken from them is frozen here as a literal.
"""

import ast
import importlib.util
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = Path(__file__).parent / "support" / "research00_old_meanings.py"
_ENDPOINT_SUITE = Path(__file__).with_name("test_hrv_trend_endpoint.py")


def _load_module(name: str, path: Path) -> ModuleType:
    """Import a module from its path: the workspace runs pytest with ``--import-mode=importlib``,
    under which nothing in ``tests/`` is importable by name (as at test_hrv_trend_endpoint.py)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OM = _load_module("research00_old_meanings", _SUPPORT)
OldMeaning = _OM.OldMeaning
normalize = _OM.normalize


# ---------------------------------------------------------------------------
# Constants, frozen from the inventory and from research/00 at 4e47d0e
# ---------------------------------------------------------------------------

#: The fifteen rule prefixes, in R11's assembly order.
PREFIX_ORDER = (
    "DOC", "PRIN", "ARB", "AUT", "GOAL", "ARCH", "REG", "IND", "COLD", "HRV", "GATE", "FIG", "LT1", "FTO", "DEC",
)

#: The three drafting groups (T172, T173, T174), each owning its prefixes.
GROUPS = {
    "doc-goal": ("DOC", "PRIN", "ARB", "AUT", "GOAL"),
    "arch-dec": ("ARCH", "REG", "IND", "COLD", "GATE", "FIG", "LT1", "FTO", "DEC"),
    "hrv": ("HRV",),
}


def _span(prefix: str, first: int, last: int) -> tuple[str, ...]:
    return tuple(f"{prefix}-{n:02d}" for n in range(first, last + 1))


#: The 149 inventory IDs (inventory Section 1), frozen: ``grep -oE '^\| (<prefixes>)-[0-9]{2,3} \|'``
#: over the inventory yields exactly these, unique. ARCH runs 00-08.
INVENTORY_IDS = frozenset(
    _span("DOC", 1, 15) + _span("PRIN", 1, 16) + _span("ARB", 1, 6) + _span("AUT", 1, 6)
    + _span("GOAL", 1, 6) + _span("ARCH", 0, 8) + _span("REG", 1, 19) + _span("IND", 1, 2)
    + _span("COLD", 1, 7) + _span("HRV", 1, 46) + _span("GATE", 1, 3) + _span("FIG", 1, 5)
    + _span("LT1", 1, 2) + _span("FTO", 1, 6) + _span("DEC", 1, 1)
)

#: The Part and section headings of research/00 at 4e47d0e, in order: every line starting ``#``,
#: minus any carrying an ISO date (none does). A draft may use only these (R11).
HEADINGS = (
    "# Design Decisions & Governing Principles",
    "## Part 1 — Principle hierarchy and tie-breakers",
    "### 1.1 The supreme objective",
    "### 1.2 The arbitration ladder",
    "### 1.3 The meta-rule",
    "### 1.4 Conflict resolution between subjective and objective signals",
    "### 1.5 Raw over derived",
    "### 1.6 Transparency and explainability",
    "### 1.7 Down-regulate freely, up-regulate cautiously",
    "### 1.8 Autonomy posture",
    "### 1.9 System ownership: plan versus goal",
    "## Part 2 — Load-bearing findings",
    "## Part 3 — Decision register",
    "### 3.1 Individualization — the governing rule (resolved)",
    "### 3.2 Cold-start — establishing day-one state (resolved, amends `research/05` §3.2)",
    "### 3.3 Resting-HRV source tiering (resolved, amends the data-quality-gating and HRV-gate register rows)",
    "### 3.4 Aerobic-threshold (LT1) determination — recommended path (open, deferred to a future determinant)",
    "## Part 4 — Design and freedom-to-operate guardrails",
    "## Part 5 — Document map, authority, and decision records",
    "### 5.1 This document's authority",
    "### 5.2 The mechanism research docs (the evidence this document points to)",
    "### 5.3 Decision records (`decisions/`)",
    "### 5.4 Reconciliations and amendments",
)

#: The Glossary's heading. It is not a 4e47d0e heading: ``assemble`` places it directly before the
#: first Part (R11), and ``rule_grammar_errors`` leaves its lines to ``glossary_errors``.
GLOSSARY_HEADING = "## Glossary"

#: Every decision R3 requires to appear in the decision column.
DECISIONS = tuple(f"C{n:02d}" for n in range(1, 34)) + ("C37", "C38")

#: R3: these change no meaning, so they appear only on ``no`` rows.
NO_ONLY = frozenset({"C11", "C16", "C17", "C18", "C19", "C21", "C25", "C29"})

#: The decisions reference's two tables, for the H-NN a merged-away ID retires under (R11).
GROUP_A = frozenset({
    "C01", "C02", "C03", "C04", "C10", "C11", "C12", "C13", "C14", "C15", "C16", "C17", "C18", "C19",
    "C21", "C24", "C25", "C26", "C29",
})
GROUP_B = frozenset({
    "C05", "C06", "C07", "C08", "C09", "C20", "C22", "C23", "C27", "C28", "C30", "C31", "C32", "C33",
    "C37", "C38",
})

#: Who writes each C-number's ``yes`` row (R11). The explicit table first; otherwise the group of the
#: C-number's first inventory row, read from each row's Status cell (``C09``'s first is HRV-33, whose
#: status homes its open question in C09). C38 cites no inventory row: its decision text names DOC-09.
YES_ROW_OWNER = {
    "C01": "hrv", "C02": "hrv", "C03": "hrv", "C04": "hrv", "C05": "arch-dec",
    "C06": "doc-goal",  # R11: PRIN-15
    "C07": "doc-goal",  # R11: PRIN-14
    "C08": "arch-dec",  # R11: ARCH-08 (the first row, PRIN-16, is doc-goal)
    "C09": "hrv", "C10": "hrv", "C11": "hrv", "C12": "hrv", "C13": "hrv", "C14": "hrv", "C15": "hrv",
    "C16": "hrv", "C17": "hrv", "C18": "hrv", "C19": "doc-goal", "C20": "doc-goal", "C21": "arch-dec",
    "C22": "doc-goal", "C23": "doc-goal", "C24": "doc-goal", "C25": "doc-goal", "C26": "arch-dec",
    "C27": "hrv",  # R11: HRV-05
    "C28": "arch-dec", "C29": "arch-dec",
    "C30": "arch-dec",  # R11: REG-19 (the first row, DOC-07, is doc-goal)
    "C31": "doc-goal", "C32": "hrv",
    "C33": "doc-goal",  # R11: PRIN-12
    "C37": "arch-dec", "C38": "doc-goal",
}

#: F008 AC7: the rule each decision is stated in, and the strings that rule's block must carry after
#: ``normalize()``. C05's absence half ("worse rate reopens") is built from ``OLD_MEANINGS``, never
#: from a literal (R11).
OPERATIVE = {
    "C01": ("HRV-31", ("could not have been selected: not judgeable, or skipped",)),
    "C02": ("HRV-31", ("the dataset being judged",)),
    "C04": ("HRV-37", ("more than",)),
    "C05": ("GATE-02", ("MUST NOT add hysteresis",)),
    "C06": ("PRIN-15", (
        "may not grow", "F005-parity", "DEFERRED_EXCEPTION", "IDEA-087", "HRV-25", "IDEA-099",
        "Pinned: none (F009)",
    )),
    "C07": ("PRIN-14", ("forbidden direction",)),
    "C32": ("HRV-07", ("max(0.5 · SD(ln rMSSD), 0.01)",)),
    "C33": ("PRIN-12", ("recency_tolerance_days", "Pinned: none (F010)")),
    "C38": ("DOC-09", ("only current rules", "dated summary")),
}

#: F008 decisions reference section 7: the traceability table's header, exactly.
TRACE_HEADER = (
    "| inventory ID | inventory sentence | Cited today as | new ID(s) | decision | meaning changed "
    "| old-meaning key |"
)
TRACE_COLUMNS = tuple(cell.strip() for cell in TRACE_HEADER.strip().strip("|").split("|"))

#: R11: the three source_change anchors and COST_SITES / spec_cost's FIG-01 lead.
ANCHOR_RULES = ("HRV-01", "HRV-06", "HRV-10", "FIG-01")
PER_TIER_ANCHORS = ("HRV-01", "HRV-06", "HRV-10")

#: R6's AC9 quantifiers.
QUANTIFIERS = ("strictly", "more than", "at least", "every", "only", "never")

ADDITION = "—"


# ===========================================================================
# The checkers. Every *_errors function returns a list of error strings, each prefixed with the
# check that raised it, and never raises on bad text.
# ===========================================================================

_P = "|".join(PREFIX_ORDER)
_RULE_ID = re.compile(rf"\b(?:{_P})-\d{{2,3}}\b")
_H_ID = re.compile(r"\bH-\d{2}\b")
_C_ID = re.compile(r"\bC\d{2}\b")
_ISO_DATE = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}")
_RULE_LINE = re.compile(rf"^\*\*(?P<id>(?:{_P})-\d{{2,3}})\.\*\* (?P<body>\S.*)$")
_SUB_LINE = re.compile(r"^(?P<kind>Scope|Not|Pinned|Why): \S")
_PINNED = re.compile(r"^Pinned: (?:none|none \(F0\d\d\)|(?P<path>[^\s`:]+)::(?P<node>[^\s`]+))$")
_CODE_SPAN = re.compile(r"`[^`]*`")
_ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|vs|cf)\.", re.IGNORECASE)
#: R6: after the ID, an interior sentence terminator.
_TERMINATOR = re.compile(r"[.!?](?=\s+[A-Z(])")
_GLOSSARY_LINE = re.compile(r"^- \*\*(?P<id>T-\d{2}) (?P<term>[^*]+?)\*\* IS (?P<definition>\S.*)\.$")
_HISTORY_ENTRY = re.compile(
    r"^- \*\*(?P<id>H-\d{2})\*\* \((?:20\d{2}-\d{2}-\d{2}(?:, 20\d{2}-\d{2}-\d{2})*|undated)\) (?P<what>\S.*) → "
    rf"(?P<ids>(?:{_P})-\d{{2,3}}(?:, (?:{_P})-\d{{2,3}})*)$"
)
_RETIRED_LINE = re.compile(rf"^- \*\*(?P<id>(?:{_P})-\d{{2,3}})\*\* retired → (?P<h>H-\d{{2}})$")
_MAX_LINE = 400
_SUB_ORDER = {"Scope": 0, "Not": 1, "Pinned": 2, "Why": 3}
_RETIRED_HEADING = "## Retired IDs"
_GROUP_OF = {prefix: group for group, prefixes in GROUPS.items() for prefix in prefixes}
_SOURCE = re.compile(r"^[^\s:]+:\d+(?:-\d+)?@4e47d0e$")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _prefix(rule_id: str) -> str:
    return rule_id.rsplit("-", 1)[0]


def _order_key(rule_id: str) -> tuple[int, int]:
    return PREFIX_ORDER.index(_prefix(rule_id)), int(rule_id.rsplit("-", 1)[1])


def _is_blank(cell: str) -> bool:
    return cell.strip() in ("", ADDITION, "-")


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def _glossary_span(lines: list[str]) -> tuple[int, int] | None:
    """The [start, end) line range of the Glossary section's body, or None."""
    if GLOSSARY_HEADING not in lines:
        return None
    start = lines.index(GLOSSARY_HEADING) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
    return start, end


# ---------------------------------------------------------------------------
# Parsers and extractors
# ---------------------------------------------------------------------------


def rule_ids(text: str) -> list[str]:
    """The ID of every rule line, in document order (duplicates kept, so they can be counted)."""
    return [m.group("id") for line in _lines(text) if (m := _RULE_LINE.match(line))]


def rule_blocks(text: str) -> dict[str, str]:
    """``{rule_id: block}``: the rule line plus the Scope, Not, Pinned and Why lines directly under it."""
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in _lines(text):
        m = _RULE_LINE.match(line)
        if m:
            current = blocks.setdefault(m.group("id"), [])
            current.append(line)
        elif current is not None and _SUB_LINE.match(line):
            current.append(line)
        else:
            current = None
    return {rule_id: "\n".join(lines) for rule_id, lines in blocks.items()}


def history_ids(text: str) -> list[str]:
    """The ``H-NN`` of every history entry line, in order (duplicates kept)."""
    return re.findall(r"^- \*\*(H-\d{2})\*\* \(", text.replace("\r\n", "\n"), re.M)


def _history_arrows(text: str) -> dict[str, list[str]]:
    arrows: dict[str, list[str]] = {}
    for line in _lines(text):
        m = _HISTORY_ENTRY.match(line)
        if m:
            arrows[m.group("id")] = m.group("ids").split(", ")
    return arrows


def _retired_listed(history_text: str) -> dict[str, str]:
    """``{ID: H-NN}`` as listed under ``## Retired IDs``."""
    if _RETIRED_HEADING not in history_text:
        return {}
    section = history_text.replace("\r\n", "\n").split(_RETIRED_HEADING, 1)[1]
    return {m.group("id"): m.group("h") for line in section.split("\n") if (m := _RETIRED_LINE.match(line))}


def _split_cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def parse_traceability(text: str) -> list[dict[str, str]]:
    """The table's rows, each keyed by ``TRACE_COLUMNS``. Takes the committed table (with its exact
    header and separator) or a header-less draft fragment. Raises ``ValueError`` on a row with the
    wrong number of columns or a header that is not ``TRACE_HEADER``."""
    rows = []
    for number, line in enumerate(_lines(text), 1):
        if not line.strip().startswith("|"):
            continue
        cells = _split_cells(line)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if cells and cells[0] == TRACE_COLUMNS[0]:
            if tuple(cells) != TRACE_COLUMNS:
                raise ValueError(f"line {number}: a header that is not TRACE_HEADER: {line.strip()!r}")
            continue
        if len(cells) != len(TRACE_COLUMNS):
            raise ValueError(f"line {number}: {len(cells)} columns, not {len(TRACE_COLUMNS)}: {line.strip()[:120]!r}")
        rows.append(dict(zip(TRACE_COLUMNS, cells, strict=True)))
    return rows


def load_meanings_jsonl(text: str) -> dict[str, OldMeaning]:
    """One JSON object per line (``key, pattern, example, source, decision``) into ``{key: OldMeaning}``.
    Raises ``ValueError`` on bad JSON, missing fields or a duplicate key."""
    entries: dict[str, OldMeaning] = {}
    need = {"key", "pattern", "example", "source", "decision"}
    for number, line in enumerate(_lines(text), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: not JSON: {exc}") from exc
        if not isinstance(obj, dict) or set(obj) != need:
            raise ValueError(f"line {number}: fields must be exactly {sorted(need)}, got {sorted(obj) if isinstance(obj, dict) else obj!r}")
        if obj["key"] in entries:
            raise ValueError(f"line {number}: duplicate key {obj['key']!r}")
        entries[obj["key"]] = OldMeaning(obj["pattern"], obj["example"], obj["source"], obj["decision"])
    return entries


def inventory_sentences(path) -> dict[str, str]:
    """``{ID: verbatim sentence}`` from the inventory's Section 1 tables (the "Current rule" cell).
    It reads the data dir, so probes and ``fragment_errors`` use it; the committed tests never do."""
    sentences = {}
    for line in _lines(Path(path).read_text(encoding="utf-8")):
        m = re.match(rf"^\| ((?:{_P})-\d{{2,3}}) \| ", line)
        if m:
            sentences[m.group(1)] = _split_cells(line)[1]
    return sentences


def public_names(path) -> set[str]:
    """Top-level ``ClassDef``, ``FunctionDef`` and ``Assign``/``AnnAssign`` targets without a leading
    underscore, read from the AST (R11). Imports are not definitions and are not counted."""
    names: set[str] = set()
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def _new_rule_ids(row: dict[str, str]) -> list[str]:
    return _RULE_ID.findall(row["new ID(s)"])


def retired_ids(rows: list[dict[str, str]]) -> dict[str, str | None]:
    """``{inventory ID: H-NN}`` for every ID that no longer names a rule (AC3, R6, R11):
    - a row whose new-ID cell does not name its own ID retires with the first ``H-NN`` in that cell;
    - otherwise (merged away) with its decision group's entry: H-38 for a Group A C-number, H-39 for
      Group B, H-40 for the critique-round ``HRV-11`` call, H-41 for an R-refinement or a T-number
      (R9's T-07 renames). The first decision in the cell decides.
    ``None`` marks a merged-away ID with no decision to retire under; ``traceability_errors`` flags it."""
    retired: dict[str, str | None] = {}
    for row in rows:
        inv = row["inventory ID"]
        if _is_blank(inv) or inv in _new_rule_ids(row):
            continue
        h = _H_ID.findall(row["new ID(s)"])
        if h:
            retired[inv] = h[0]
            continue
        retired[inv] = _decision_group_entry(row["decision"])
    return retired


def _decision_group_entry(cell: str) -> str | None:
    for token in re.findall(r"\bC\d{2}\b|\bHRV-11\b|\bR\d+\b|\bT-\d{2}\b", cell):
        if token in GROUP_A:
            return "H-38"
        if token in GROUP_B:
            return "H-39"
        if token == "HRV-11":
            return "H-40"
        if token.startswith(("R", "T-")):
            return "H-41"
    return None


# ---------------------------------------------------------------------------
# Grammar and structure
# ---------------------------------------------------------------------------


def _one_sentence(body: str) -> bool:
    plain = _ABBREVIATIONS.sub("ABBR", _CODE_SPAN.sub("CODE", body))
    return not _TERMINATOR.search(plain)


def rule_grammar_errors(text: str) -> list[str]:
    """AC1, AC2, AC3 and R6 over research/00 or a rules draft: no ISO date; no line over 400 characters;
    headings only from ``HEADINGS`` (or the Glossary's); every other line outside the Glossary is a
    blank line, a rule line in obligation or definition form holding one sentence, or a Scope, Not,
    Pinned or Why line under it -- exactly one Scope, one Not, one or more Pinned and at most one Why,
    in that order, with no blank line inside a block; rule IDs unique."""
    errors: list[str] = []
    flat = " ".join(text.split())
    for date in sorted(set(_ISO_DATE.findall(flat))):
        errors.append(f"[grammar] an ISO date remains: {date}")
    lines = _lines(text)
    glossary = _glossary_span(lines)
    state: dict | None = None

    def close(end: int) -> None:
        if state is None or state["closed"]:
            return
        state["closed"] = True
        seen = state["seen"]
        for kind in ("Scope", "Not", "Pinned"):
            if kind not in seen:
                errors.append(f"[grammar] {state['id']} (line {state['line']}): no {kind}: line")
        for kind in ("Scope", "Not"):
            if seen.count(kind) > 1:
                errors.append(f"[grammar] {state['id']} (line {state['line']}): {seen.count(kind)} {kind}: lines, not one")

    for number, line in enumerate(lines, 1):
        if len(line) > _MAX_LINE:
            errors.append(f"[grammar] line {number}: {len(line)} characters, over {_MAX_LINE}")
        if glossary and glossary[0] <= number - 1 < glossary[1]:
            continue
        if line.startswith("#"):
            close(number)
            state = None
            if line not in HEADINGS and line != GLOSSARY_HEADING:
                errors.append(f"[grammar] line {number}: a heading not in HEADINGS: {line[:100]!r}")
            continue
        if not line.strip():
            close(number)
            continue
        m = _RULE_LINE.match(line)
        if m:
            close(number)
            body = m.group("body")
            state = {"id": m.group("id"), "line": number, "seen": [], "closed": False}
            if not line.endswith("."):
                errors.append(f"[grammar] {m.group('id')} (line {number}): a rule line must end in '.'")
            elif not _one_sentence(body):
                errors.append(f"[grammar] {m.group('id')} (line {number}): more than one sentence (R6)")
            if not (re.search(r"\bMUST\b|\bMAY\b", body) or " IS " in f" {body} "):
                errors.append(f"[grammar] {m.group('id')} (line {number}): no MUST, MUST NOT, MAY or IS")
            continue
        sub = _SUB_LINE.match(line)
        if sub and state is not None and not state["closed"]:
            kind, seen = sub.group("kind"), state["seen"]
            if seen and _SUB_ORDER[kind] < _SUB_ORDER[seen[-1]]:
                errors.append(f"[grammar] {state['id']} (line {number}): {kind}: out of order after {seen[-1]}:")
            elif kind == "Why" and "Why" in seen:
                errors.append(f"[grammar] {state['id']} (line {number}): a second Why: line is out of order")
            elif kind in ("Scope", "Not") and kind in seen:
                errors.append(f"[grammar] {state['id']} (line {number}): a second {kind}: line is out of order")
            if kind == "Pinned" and not _PINNED.match(line):
                errors.append(f"[grammar] {state['id']} (line {number}): Pinned: must be plain path::name, "
                              f"'none' or 'none (F0NN)': {line[:120]!r}")
            seen.append(kind)
            continue
        errors.append(f"[grammar] line {number}: not a rule, Scope, Not, Pinned, Why, heading or blank line: {line[:100]!r}")
    close(len(lines))
    counts = Counter(rule_ids(text))
    errors += [f"[grammar] duplicate rule ID {i} ({n} rule lines)" for i, n in counts.items() if n > 1]
    return errors


def glossary_errors(text: str, complete: bool = True) -> list[str]:
    """R6's glossary format over the Glossary section of research/00 (or a whole glossary draft):
    ``- **T-NN <term>** IS <definition>.``, at most 400 characters, each term once, T-25's definition
    exactly ``OPEN (IDEA-088)``, and, when ``complete``, exactly the 33 terms T-01..T-33."""
    lines = _lines(text)
    span = _glossary_span(lines)
    body = lines[span[0]:span[1]] if span else [line for line in lines if not line.startswith("#")]
    errors, seen = [], []
    for line in body:
        if not line.strip():
            continue
        if len(line) > _MAX_LINE:
            errors.append(f"[glossary] {len(line)} characters, over {_MAX_LINE}: {line[:60]!r}")
        m = _GLOSSARY_LINE.match(line)
        if not m:
            errors.append(f"[glossary] not in the format '- **T-NN <term>** IS <definition>.': {line[:80]!r}")
            continue
        seen.append(m.group("id"))
        if m.group("id") == "T-25" and m.group("definition") != "OPEN (IDEA-088)":
            errors.append(f"[glossary] T-25 must be defined as exactly 'OPEN (IDEA-088)', not {m.group('definition')!r}")
    errors += [f"[glossary] duplicate term {t}" for t, n in Counter(seen).items() if n > 1]
    if complete and sorted(set(seen)) != [f"T-{i:02d}" for i in range(1, 34)]:
        errors.append(f"[glossary] {len(set(seen))} distinct terms, not the 33 T-01..T-33")
    return errors


def _sentences(line: str) -> list[str]:
    guarded = _ABBREVIATIONS.sub(lambda m: m.group(0).replace(".", "\0"), line)
    return [s.replace("\0", ".") for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", guarded)]


def band_errors(text: str) -> list[str]:
    """AC4 and R6, case-insensitive: every ``\\bbands?\\b`` sits in a sentence that, with its rule-ID
    tokens removed, also contains SWC, HRV or rMSSD. "Band" is reserved for the HRV SWC band."""
    errors = []
    for number, line in enumerate(_lines(text), 1):
        for sentence in _sentences(line):
            if re.search(r"\bbands?\b", sentence, re.IGNORECASE):
                rest = _RULE_ID.sub(" ", sentence)
                if not re.search(r"swc|hrv|rmssd", rest, re.IGNORECASE):
                    errors.append(f"[band] line {number}: 'band' outside the HRV SWC band: {sentence.strip()[:120]!r}")
    return errors


def history_errors(text: str) -> list[str]:
    """AC5 and R6 over the history file or its draft: every entry is
    ``- **H-NN** (<date>[, <date>...] | undated) <what changed> → <rule IDs>``, each H-NN once; no rule
    line; no uppercase MUST or MAY; a ``## Retired IDs`` section whose lines are
    ``- **<ID>** retired → H-NN``."""
    errors = []
    lines = _lines(text)
    if _RETIRED_HEADING not in lines:
        errors.append("[history] no '## Retired IDs' section")
    in_retired = False
    for number, line in enumerate(lines, 1):
        if line.startswith("#"):
            in_retired = line == _RETIRED_HEADING
            continue
        if _RULE_LINE.match(line):
            errors.append(f"[history] line {number}: a rule line: {line[:80]!r}")
        for modal in re.findall(r"\b(MUST|MAY)\b", line):
            errors.append(f"[history] line {number}: uppercase {modal}")
        if in_retired:
            if line.strip() and not _RETIRED_LINE.match(line):
                errors.append(f"[history] line {number}: not '- **<ID>** retired → H-NN': {line[:80]!r}")
        elif line.startswith("- **") and not _HISTORY_ENTRY.match(line):
            errors.append(f"[history] line {number}: an entry not in the format "
                          f"'- **H-NN** (<date>[, <date>…] | undated) <what> → <rule IDs>': {line[:100]!r}")
    errors += [f"[history] duplicate entry {h}" for h, n in Counter(history_ids(text)).items() if n > 1]
    return errors


# ---------------------------------------------------------------------------
# Traceability and decisions
# ---------------------------------------------------------------------------


def _keys(row: dict[str, str]) -> list[str]:
    cell = row["old-meaning key"]
    return [] if _is_blank(cell) else [k.strip() for k in cell.split(",") if k.strip()]


def traceability_errors(rows: list[dict[str, str]], research_text: str, history_text: str, meanings) -> list[str]:
    """AC3 and AC6, both directions: the inventory-ID column is the 149 IDs with no duplicates; every
    new-ID cell names at least one ID and each resolves (a rule line, or a history entry); every rule
    ID appears in some new-ID cell; no retired ID names a rule; every key exists in ``meanings``."""
    errors = []
    ids = [r["inventory ID"] for r in rows if not _is_blank(r["inventory ID"])]
    errors += [f"[trace] duplicate inventory ID {i}" for i, n in Counter(ids).items() if n > 1]
    errors += [f"[trace] {i} is not an inventory ID" for i in sorted(set(ids) - INVENTORY_IDS)]
    missing = sorted(INVENTORY_IDS - set(ids), key=_order_key)
    if missing:
        errors.append(f"[trace] {len(missing)} inventory IDs missing from the table: {missing[:10]}")
    rules, entries = set(rule_ids(research_text)), set(history_ids(history_text))
    named: set[str] = set()
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        new_rules, new_h = _new_rule_ids(row), _H_ID.findall(row["new ID(s)"])
        if not new_rules and not new_h:
            errors.append(f"[trace] {label}: names no new ID: {row['new ID(s)']!r}")
        named.update(new_rules)
        errors += [f"[trace] {label}: new ID {i} is not a rule line of research/00" for i in new_rules if i not in rules]
        errors += [f"[trace] {label}: {h} is not a history entry" for h in new_h if h not in entries]
        errors += [f"[trace] {label}: no such old-meaning key {k!r}" for k in _keys(row) if k not in meanings]
        for token in re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", row["new ID(s)"]):
            if not _RULE_ID.fullmatch(token) and not _H_ID.fullmatch(token):
                errors.append(f"[trace] {label}: {token} is neither a rule ID nor an H-NN")
    errors += [f"[trace] rule {i} is not named by any traceability row" for i in sorted(rules - named, key=_order_key)]
    retired = retired_ids(rows)
    errors += [f"[trace] {i}: merged away with no decision to retire under; cite its H-NN in the new-ID cell"
               for i, h in retired.items() if h is None]
    listed = _retired_listed(history_text)
    for i in sorted((set(retired) | set(listed)) & rules, key=_order_key):
        errors.append(f"[trace] retired ID {i} is reused by a rule line")
    return errors


def decision_column_errors(rows: list[dict[str, str]], complete: bool) -> list[str]:
    """R3 over the decision, meaning and key columns. Always: ``meaning changed`` is yes or no; every
    C-number is one of ``DECISIONS`` (a further one is a stop-and-ask); a ``yes`` row is authorized by
    at least one C-number outside NO_ONLY -- a no-only C-number never justifies a meaning change on its
    own, but may share an authorized cell (R3 "A shared cell", e.g. PRIN-08's "C24, C25"); every
    ``yes`` row names an old-meaning key (F008 AC6); an addition row cites a decision. With
    ``complete``, every decision appears and every decision outside NO_ONLY backs at least one ``yes``
    row. Group ownership is coverage, not exclusivity (R3), so a ``yes`` row may cite a C-number another
    group owns; the owner's duty is ``group_coverage_errors``."""
    errors = []
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        meaning, cs = row["meaning changed"], _C_ID.findall(row["decision"])
        if meaning not in ("yes", "no"):
            errors.append(f"[decision] {label}: meaning changed must be yes or no, not {meaning!r}")
        errors += [f"[decision] {label}: {c} is not a decision in the decisions reference (R3: stop and ask)"
                   for c in cs if c not in YES_ROW_OWNER]
        if meaning == "yes":
            no_only = [c for c in cs if c in NO_ONLY]
            if no_only and not any(c in YES_ROW_OWNER and c not in NO_ONLY for c in cs):
                errors.append(f"[decision] {label}: {', '.join(no_only)} never authorizes a yes on its own; "
                              "cite the C-number that changes the meaning, or mark the row no (R3)")
            if _is_blank(row["old-meaning key"]):
                errors.append(f"[decision] {label}: a yes row must name an old-meaning key (F008 AC6)")
        if _is_blank(row["inventory ID"]) and _is_blank(row["decision"]):
            errors.append(f"[decision] {label}: an addition row must cite a decision")
    if complete:
        cited = {c for r in rows for c in _C_ID.findall(r["decision"])}
        yes = {c for r in rows if r["meaning changed"] == "yes" for c in _C_ID.findall(r["decision"])}
        errors += [f"[decision] {c} appears in no decision cell (R3)" for c in DECISIONS if c not in cited]
        errors += [f"[decision] {c} backs no yes row (R3; YES_ROW_OWNER gives it to {YES_ROW_OWNER[c]})"
                   for c in DECISIONS if c not in NO_ONLY and c in cited and c not in yes]
    return errors


def group_coverage_errors(rows: list[dict[str, str]], group: str) -> list[str]:
    """R3 "Ownership is coverage, not exclusivity" (user, 2026-09-25): for every C-number
    ``YES_ROW_OWNER`` gives to ``group``, some row in ``rows`` is ``yes`` and cites it. ``yes`` rows
    citing C-numbers other groups own are allowed. NO_ONLY C-numbers never authorize a ``yes``, so
    no group owes one for them."""
    yes = {c for r in rows if r["meaning changed"] == "yes" for c in _C_ID.findall(r["decision"])}
    return [f"[coverage] group {group} owns {c} (YES_ROW_OWNER) and writes no yes row citing it (R3)"
            for c in DECISIONS if YES_ROW_OWNER[c] == group and c not in NO_ONLY and c not in yes]


def operative_string_errors(research_text: str, rows: list[dict[str, str]], meanings=None,
                            group: str | None = None) -> list[str]:
    """AC7: for each decision in ``OPERATIVE`` that some row cites, the rule ``OPERATIVE`` names for it
    carries each operative string after ``normalize()``. Only decisions the rows cite are checked, so
    it runs per group; with ``group`` set, a named rule outside that group is left to the assembled
    check. C05's absence half is built from the ``OLD_MEANINGS`` entries whose decision is C05
    (``meanings`` defaults to the support module's): each pattern must match nowhere in the text."""
    meanings = _OM.OLD_MEANINGS if meanings is None else meanings
    errors = []
    cited = {c for r in rows for c in _C_ID.findall(r["decision"])}
    blocks = rule_blocks(research_text)
    for decision, (rule_id, strings) in OPERATIVE.items():
        if decision not in cited:
            continue
        if rule_id not in blocks:
            if group is None or _GROUP_OF[_prefix(rule_id)] == group:
                errors.append(f"[operative] {decision}: its rule {rule_id} is not in the text")
            continue
        flat = normalize(blocks[rule_id])
        errors += [f"[operative] {decision}: {rule_id} does not state {s!r} (after normalize)"
                   for s in strings if normalize(s) not in flat]
    if "C05" in cited:
        c05 = {k: m for k, m in meanings.items() if re.search(r"\bC05\b", m.decision)}
        if not c05:
            errors.append("[operative] C05: no OLD_MEANINGS entry with decision C05, so the absence of the "
                          "reopened-decision wording cannot be asserted")
        flat = normalize(research_text)
        for key, entry in c05.items():
            try:
                if re.search(entry.pattern, flat):
                    errors.append(f"[operative] C05: the old wording of {key!r} is still stated")
            except re.error as exc:
                errors.append(f"[operative] C05: {key!r} does not compile: {exc}")
    return errors


def proxy_errors(sentence: str, block: str) -> list[str]:
    """AC9 and R6: an unchanged row's inventory sentence survives in its mapped block(s). Date
    expressions are removed first, and the block must then cite an ``H-NN``. Then every number
    (``\\d+(?:\\.\\d+)?``, after ``−`` and ``–`` fold to ``-``), every backticked identifier (exactly)
    and each quantifier (``QUANTIFIERS``, whole words, any case) must appear in the block."""
    errors = []
    undated, removed = _ISO_DATE.subn(" ", sentence)
    if removed and not _H_ID.search(block):
        errors.append("[proxy] the sentence carries a date, and the block cites no H-NN for it")
    fold = str.maketrans({"−": "-", "–": "-"})
    have = set(_NUMBER.findall(block.translate(fold)))
    lost = [n for n in dict.fromkeys(_NUMBER.findall(_CODE_SPAN.sub(" ", undated).translate(fold))) if n not in have]
    for span in re.findall(r"`([^`]*)`", undated):
        if span not in block:
            errors.append(f"[proxy] identifier `{span}` is lost")
        lost += [n for n in _NUMBER.findall(span.translate(fold)) if n not in have and n not in lost]
    errors += [f"[proxy] number {n} is lost" for n in lost]
    for q in QUANTIFIERS:
        words = q.replace(" ", r"\s+")
        pattern = rf"\b{words}\b"
        if re.search(pattern, undated, re.IGNORECASE) and not re.search(pattern, block, re.IGNORECASE):
            errors.append(f"[proxy] quantifier {q!r} is lost")
    return errors


def pinned_errors(research_text: str, repo_root) -> list[str]:
    """AC6: every ``Pinned: path::name`` names a file under ``repo_root`` in which ``def name`` is
    found; a parametrised ``name[id]`` is looked up by ``name``."""
    errors = []
    for line in _lines(research_text):
        m = _PINNED.match(line)
        if not m or not m.group("path"):
            continue
        path = Path(repo_root) / m.group("path")
        name = m.group("node").split("::")[-1].split("[", 1)[0]
        if not path.is_file():
            errors.append(f"[pinned] {m.group('path')} does not exist")
        elif not re.search(rf"^\s*(?:async\s+)?def {re.escape(name)}\(", path.read_text(encoding="utf-8"), re.M):
            errors.append(f"[pinned] no def {name} in {m.group('path')}")
    return errors


def old_meaning_errors(entries, research_text: str) -> list[str]:
    """R4, for each entry: the pattern compiles, matches ``normalize(example)``, and matches nowhere
    in ``normalize(research_text)``; ``source`` is ``path:line@4e47d0e``."""
    errors = []
    flat = normalize(research_text)
    for key, entry in entries.items():
        if not _SOURCE.match(entry.source):
            errors.append(f"[old-meaning] {key}: source {entry.source!r} is not path:line@4e47d0e")
        try:
            pattern = re.compile(entry.pattern)
        except re.error as exc:
            errors.append(f"[old-meaning] {key}: pattern does not compile: {exc}")
            continue
        if not pattern.search(normalize(entry.example)):
            errors.append(f"[old-meaning] {key}: pattern does not match its normalized example")
        if pattern.search(flat):
            errors.append(f"[old-meaning] {key}: pattern matches research/00 (the old meaning is still stated)")
    return errors


# ---------------------------------------------------------------------------
# Review scope (R7)
# ---------------------------------------------------------------------------


def required_review_rows(research_text: str, rows: list[dict[str, str]], group: str | None = None) -> list[str]:
    """R7's row IDs, in document order: the block of every rule a ``no`` or ``yes`` row names
    (``<ID>``), and every rule's ``<ID>/Scope``, ``<ID>/Not`` and ``<ID>/Why`` line. ``group`` keeps
    only the rules whose prefix is in that group."""
    blocks = rule_blocks(research_text)
    named = {i for r in rows if r["meaning changed"] in ("yes", "no") for i in _new_rule_ids(r)}
    need = []
    for rule_id, block in blocks.items():
        if group is not None and _GROUP_OF.get(_prefix(rule_id)) != group:
            continue
        if rule_id in named:
            need.append(rule_id)
        kinds = [line.split(":", 1)[0] for line in block.splitlines()[1:]]
        need += [f"{rule_id}/{k}" for k in ("Scope", "Not", "Why") if k in kinds]
    return need


# ---------------------------------------------------------------------------
# Assembly (R11) and the checks that span groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assembly:
    research: str
    history: str
    table: str
    meanings: dict
    problems: tuple[str, ...]


def _draft_blocks(text: str, name: str, problems: list[str]) -> dict[str, list[tuple[str, str]]]:
    """``{heading: [(rule_id, block)]}`` from one rules draft."""
    placed: dict[str, list[tuple[str, str]]] = {}
    heading, current = None, None
    for number, line in enumerate(_lines(text), 1):
        if line.startswith("#"):
            heading, current = line, None
            if line not in HEADINGS[1:]:
                problems.append(f"[assemble] {name} line {number}: a heading not in HEADINGS: {line[:100]!r}")
            continue
        m = _RULE_LINE.match(line)
        if m:
            if heading is None:
                problems.append(f"[assemble] {name} line {number}: {m.group('id')} sits under no heading")
                current = None
                continue
            current = [line]
            placed.setdefault(heading, []).append((m.group("id"), current))
        elif line.strip() and current is not None:
            current.append(line)
        elif line.strip():
            problems.append(f"[assemble] {name} line {number}: a line outside any rule block: {line[:100]!r}")
        else:
            current = None
    return {h: [(i, "\n".join(b)) for i, b in blocks] for h, blocks in placed.items()}


def _read(drafts: Path, name: str, problems: list[str]) -> str:
    path = drafts / name
    if not path.is_file():
        problems.append(f"[assemble] no draft {name}")
        return ""
    return path.read_text(encoding="utf-8")


def assemble(drafts_dir) -> Assembly:
    """Build research/00, the history, the traceability table and ``OLD_MEANINGS`` from the drafts
    (R11): the title, the Glossary directly before the first Part, then every heading of ``HEADINGS``
    in order, each holding the rules the drafts placed under it (merged across drafts) in
    ``PREFIX_ORDER`` then by number; ``## Retired IDs`` filled from ``retired_ids``. A missing group
    draft is a problem, not an exception, so a partial set still assembles for inspection."""
    drafts, problems = Path(drafts_dir), []
    glossary = [line for line in _lines(_read(drafts, "glossary.txt", problems))
                if line.strip() and line != GLOSSARY_HEADING]
    placed: dict[str, list[tuple[str, str]]] = {}
    table_rows, meanings = [], {}
    for group in GROUPS:
        if not (drafts / f"{group}.rules.txt").is_file():
            continue
        for heading, blocks in _draft_blocks(_read(drafts, f"{group}.rules.txt", problems), f"{group}.rules.txt", problems).items():
            placed.setdefault(heading, []).extend(blocks)
        table_rows += [line for line in _lines(_read(drafts, f"{group}.trace.txt", problems)) if line.strip().startswith("|")]
        if (drafts / f"{group}.meanings.txt").is_file():
            try:
                for key, entry in load_meanings_jsonl(_read(drafts, f"{group}.meanings.txt", problems)).items():
                    if key in meanings:
                        problems.append(f"[assemble] duplicate old-meaning key {key!r} ({group})")
                    meanings[key] = entry
            except ValueError as exc:
                problems.append(f"[assemble] {group}.meanings.txt: {exc}")
    out = [HEADINGS[0], "", GLOSSARY_HEADING, "", *glossary, ""]
    for heading in HEADINGS[1:]:
        out += [heading, ""]
        for _rule_id, block in sorted(placed.get(heading, []), key=lambda b: _order_key(b[0])):
            out += [block, ""]
    table = "\n".join([TRACE_HEADER, "|" + "---|" * len(TRACE_COLUMNS), *table_rows]) + "\n"
    try:
        retired = retired_ids(parse_traceability(table))
    except ValueError as exc:
        problems.append(f"[assemble] traceability: {exc}")
        retired = {}
    history = _read(drafts, "history.txt", problems).replace("\r\n", "\n").rstrip("\n")
    if _RETIRED_HEADING in history:
        history = history.split(_RETIRED_HEADING, 1)[0].rstrip("\n")
    else:
        problems.append("[assemble] history.txt has no '## Retired IDs' section")
    listed = [f"- **{i}** retired → {h}" for i, h in sorted(retired.items(), key=lambda kv: _order_key(kv[0])) if h]
    history = "\n".join([history, "", _RETIRED_HEADING, "", *listed]) + "\n"
    return Assembly("\n".join(out).rstrip("\n") + "\n", history, table, meanings, tuple(problems))


def _anchor_errors(research_text: str) -> list[str]:
    """R11: each anchor rule (the three source_change anchors and the FIG-01 lead) exists once, its
    ID plus opening words is unique in the flattened file, and the three HRV anchors' paragraphs say
    "per-tier dataset"."""
    errors, flat, blocks = [], normalize(research_text), rule_blocks(research_text)
    for rule_id in ANCHOR_RULES:
        if rule_id not in blocks:
            errors.append(f"[anchor] {rule_id} carries an anchor and has no rule line")
            continue
        lead = " ".join(normalize(blocks[rule_id].splitlines()[0]).split()[:6])
        if flat.count(lead) != 1:
            errors.append(f"[anchor] {rule_id}'s lead {lead!r} occurs {flat.count(lead)} times in the flattened file, not once")
        if rule_id in PER_TIER_ANCHORS and "per-tier dataset" not in normalize(blocks[rule_id]):
            errors.append(f"[anchor] {rule_id}'s paragraph does not say 'per-tier dataset' (R11)")
    return errors


def _proxy_rows(rows: list[dict[str, str]], research_text: str, only_within: set[str] | None = None) -> list[str]:
    """The AC9 proxy on every ``no`` row with an inventory sentence, over the blocks it names. With
    ``only_within``, a row naming a rule outside that set is left to the assembled check."""
    errors, blocks = [], rule_blocks(research_text)
    for row in rows:
        new = _new_rule_ids(row)
        if row["meaning changed"] != "no" or _is_blank(row["inventory sentence"]) or not new:
            continue
        if only_within is not None and not set(new) <= only_within:
            continue
        mapped = "\n".join(blocks[i] for i in new if i in blocks)
        errors += [f"{e} ({row['inventory ID']})" for e in proxy_errors(row["inventory sentence"], mapped)]
    return errors


def assemble_check(drafts_dir) -> list[str]:
    """Every check over ``assemble()``'s output with ``complete=True``, plus what no single group can
    see: the whole-document R4 negative (glossary included), history arrows resolving to a rule or a
    retired ID, the uniqueness of the anchors, and the AC9 proxy on every ``no`` row."""
    a = assemble(drafts_dir)
    errors = list(a.problems)
    try:
        rows = parse_traceability(a.table)
    except ValueError as exc:
        return errors + [f"[trace] {exc}"]
    errors += rule_grammar_errors(a.research) + glossary_errors(a.research) + band_errors(a.research)
    errors += history_errors(a.history)
    errors += traceability_errors(rows, a.research, a.history, a.meanings)
    errors += decision_column_errors(rows, True)
    errors += operative_string_errors(a.research, rows, meanings=a.meanings)
    errors += pinned_errors(a.research, _REPO_ROOT)
    errors += old_meaning_errors(a.meanings, a.research)
    resolvable = set(rule_ids(a.research)) | set(_retired_listed(a.history))
    for h, ids in _history_arrows(a.history).items():
        errors += [f"[history] {h}'s arrow cites {i}, neither a rule nor a retired ID" for i in ids if i not in resolvable]
    errors += _anchor_errors(a.research)
    errors += _proxy_rows(rows, a.research)
    return errors


def _git_show(path: str) -> str:
    done = subprocess.run(["git", "show", f"4e47d0e:{path}"], capture_output=True, text=True, encoding="utf-8",
                          cwd=_REPO_ROOT, check=False)
    return done.stdout


def fragment_text_errors(rules: str, trace: str, meanings_text: str, group: str, sentences: dict[str, str],
                         show: Callable[[str], str] | None = None, coverage: bool = True) -> list[str]:
    """``fragment_errors`` over text already read. ``coverage=False`` skips the whole-group coverage --
    the group's inventory IDs and ``group_coverage_errors`` (a ``yes`` row for each C-number the group
    owns) -- so a single real block can be checked (T170 AC4); ``show=None`` skips the verbatim check."""
    errors = rule_grammar_errors(rules) + band_errors(rules)
    try:
        rows = parse_traceability(trace)
    except ValueError as exc:
        return errors + [f"[trace] {group}.trace.txt: {exc}"]
    try:
        meanings = load_meanings_jsonl(meanings_text)
    except ValueError as exc:
        errors.append(f"[old-meaning] {group}.meanings.txt: {exc}")
        meanings = {}
    prefixes = GROUPS[group]
    ids = [r["inventory ID"] for r in rows if not _is_blank(r["inventory ID"])]
    errors += [f"[trace] duplicate inventory ID {i}" for i, n in Counter(ids).items() if n > 1]
    errors += [f"[trace] {i} is outside group {group}'s prefixes" for i in ids if _prefix(i) not in prefixes]
    if coverage:
        want = {i for i in INVENTORY_IDS if _prefix(i) in prefixes}
        missing = sorted(want - set(ids), key=_order_key)
        if missing:
            errors.append(f"[trace] {len(missing)} of group {group}'s inventory IDs have no row: {missing[:10]}")
    here = set(rule_ids(rules))
    errors += [f"[grammar] rule {i} has a prefix outside group {group}" for i in sorted(here) if _prefix(i) not in prefixes]
    named: set[str] = set()
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        new = _new_rule_ids(row)
        named.update(new)
        if not new and not _H_ID.findall(row["new ID(s)"]):
            errors.append(f"[trace] {label}: names no new ID: {row['new ID(s)']!r}")
        errors += [f"[trace] {label}: new ID {i} is not a rule line of the {group} draft"
                   for i in new if _prefix(i) in prefixes and i not in here]
        errors += [f"[trace] {label}: {h} is outside the history's H-01..H-41"
                   for h in _H_ID.findall(row["new ID(s)"]) if not 1 <= int(h[2:]) <= 41]
        errors += [f"[trace] {label}: no such old-meaning key {k!r} in {group}.meanings.txt"
                   for k in _keys(row) if k not in meanings]
        inv = row["inventory ID"]
        if _is_blank(inv):
            if not _is_blank(row["inventory sentence"]):
                errors.append(f"[trace] {label}: an addition's inventory sentence must be '{ADDITION}'")
        elif inv in sentences and row["inventory sentence"] != sentences[inv]:
            errors.append(f"[trace] {inv}: the inventory sentence cell is not the inventory's sentence verbatim")
        elif inv not in sentences and _prefix(inv) in prefixes:
            errors.append(f"[trace] {inv}: not in the inventory")
    errors += [f"[trace] rule {i} is not named by any {group} row" for i in sorted(here - named, key=_order_key)]
    errors += decision_column_errors(rows, False)
    if coverage:
        errors += group_coverage_errors(rows, group)
    errors += operative_string_errors(rules, rows, meanings=meanings, group=group)
    errors += _proxy_rows(rows, rules, only_within=here)
    errors += old_meaning_errors(meanings, rules)
    if show is not None:
        cache: dict[str, str] = {}
        for key, entry in meanings.items():
            path = entry.source.split(":", 1)[0]
            if path not in cache:
                cache[path] = normalize(show(path))
            if normalize(entry.example) not in cache[path]:
                errors.append(f"[old-meaning] {key}: example is not verbatim in {path} at 4e47d0e")
    return errors


def fragment_errors(drafts_dir, group: str, inventory_path, show: Callable[[str], str] | None = _git_show) -> list[str]:
    """What the T172-T174 probes run over ``<group>.rules.txt``, ``.trace.txt`` and ``.meanings.txt``:
    grammar and band; the group's inventory IDs, each once; new IDs resolving within the group's
    prefixes or to an H-NN (cross-group IDs are left to ``assemble_check``); each inventory-sentence
    cell equal to ``inventory_sentences()``; R3 per group, including a ``yes`` row for each C-number the group owns; AC7 for the group's decisions; the AC9 proxy
    on every ``no`` row; R4 against the group's rules; and each example verbatim in
    ``git show 4e47d0e:<source>``."""
    drafts, errors = Path(drafts_dir), []
    if group not in GROUPS:
        return [f"[fragment] unknown group {group!r}; one of {sorted(GROUPS)}"]
    texts = {}
    for kind in ("rules", "trace", "meanings"):
        path = drafts / f"{group}.{kind}.txt"
        if not path.is_file():
            errors.append(f"[fragment] no draft {path.name}")
        texts[kind] = path.read_text(encoding="utf-8") if path.is_file() else ""
    return errors + fragment_text_errors(texts["rules"], texts["trace"], texts["meanings"], group,
                                         inventory_sentences(inventory_path), show=show)


# ===========================================================================
# Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# The old-meanings support module (T170 AC1, AC5)
# ---------------------------------------------------------------------------


def test_the_old_meanings_module_exposes_exactly_its_four_public_names() -> None:
    """R4 and R11: the module the endpoint walk holds out by name may define only these four, so
    nothing else can shelter in the one unscanned literals file. Read from the AST, real path."""
    names = public_names(_SUPPORT)
    print(f"[slice compared] public names of {_SUPPORT.name}: {sorted(names)}")
    assert names == {"OldMeaning", "OLD_MEANINGS", "EXCEPTIONS", "normalize"}
    assert isinstance(_OM.OLD_MEANINGS, dict) and isinstance(_OM.EXCEPTIONS, tuple)
    tree = ast.parse(_SUPPORT.read_text(encoding="utf-8"))
    imported = [a.asname or a.name for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names]
    assert imported and all(name.startswith("_") for name in imported), f"unaliased import: {imported}"
    assert set(OldMeaning.__dataclass_fields__) == {"pattern", "example", "source", "decision"}
    with pytest.raises(AttributeError):
        OldMeaning("a", "b", "c", "d").pattern = "x"  # frozen


@pytest.mark.parametrize(
    ("raw", "flat"),
    [
        pytest.param("A  b\n\tC", "a b c", id="whitespace-collapsed-and-casefolded"),
        pytest.param("“quoted” ‘single’ \"straight\" 'apos'", "quoted single straight apos", id="all-quotes-dropped"),
        pytest.param("`min_window_readings` **bold** *em*", "minwindowreadings bold em", id="code-emphasis-underscore-dropped"),
        pytest.param("≥ ≤ — – −", ">= <= -- -- -", id="typography-folded"),
        pytest.param("ＲＭＳＳＤ", "rmssd", id="nfkc-before-folding"),
        pytest.param("Straße", "strasse", id="casefold-not-lower"),
        pytest.param("0.5 · SD(ln rMSSD)", "0.5 · sd(ln rmssd)", id="middle-dot-kept"),
    ],
)
def test_normalize_is_flat_plus_nfkc_casefold_and_curly_quotes(raw: str, flat: str) -> None:
    print(f"[slice compared] {raw!r} -> {normalize(raw)!r}")
    assert normalize(raw) == flat


def test_the_module_loads_by_path_as_every_probe_loads_it() -> None:
    """The T171-T180 probes load this file with ``spec_from_file_location`` and never register it in
    ``sys.modules``. A dataclass under string annotations looks its module up there and fails, which
    is why this module carries no ``from __future__ import annotations``."""
    spec = importlib.util.spec_from_file_location("t", Path(__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.assemble is not None and len(module.INVENTORY_IDS) == 149


def test_public_names_reads_classes_functions_and_assignments_only(tmp_path) -> None:
    module = tmp_path / "m.py"
    module.write_text(
        "import os as _os\nfrom x import y\nclass A: pass\ndef f(): pass\nB = 1\nC: int = 2\n_d = 3\n"
        "def _g(): pass\nif True:\n    Z = 1\n",
        encoding="utf-8",
    )
    assert public_names(module) == {"A", "f", "B", "C"}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_the_frozen_constants_have_the_inventorys_shape() -> None:
    per_prefix = Counter(i.split("-")[0] for i in INVENTORY_IDS)
    print(f"[slice compared] {len(INVENTORY_IDS)} IDs {dict(per_prefix)}")
    assert len(INVENTORY_IDS) == 149
    assert per_prefix == {"DOC": 15, "PRIN": 16, "ARB": 6, "AUT": 6, "GOAL": 6, "ARCH": 9, "REG": 19, "IND": 2,
                          "COLD": 7, "HRV": 46, "GATE": 3, "FIG": 5, "LT1": 2, "FTO": 6, "DEC": 1}
    assert "ARCH-00" in INVENTORY_IDS and "ARCH-09" not in INVENTORY_IDS
    assert sorted(p for ps in GROUPS.values() for p in ps) == sorted(PREFIX_ORDER)
    sizes = {g: sum(1 for i in INVENTORY_IDS if i.split("-")[0] in ps) for g, ps in GROUPS.items()}
    assert sizes == {"doc-goal": 49, "arch-dec": 54, "hrv": 46}
    assert len(HEADINGS) == 23 and len(set(HEADINGS)) == 23
    assert not any(re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", h) for h in HEADINGS)
    assert HEADINGS[1].startswith("## Part 1") and HEADINGS[-1] == "### 5.4 Reconciliations and amendments"
    assert set(YES_ROW_OWNER) == set(DECISIONS) and set(YES_ROW_OWNER.values()) == set(GROUPS)
    assert {YES_ROW_OWNER[c] for c in ("C06", "C07", "C33")} == {"doc-goal"}
    assert (YES_ROW_OWNER["C08"], YES_ROW_OWNER["C30"], YES_ROW_OWNER["C27"]) == ("arch-dec", "arch-dec", "hrv")
    assert GROUP_A | GROUP_B == set(DECISIONS) and not GROUP_A & GROUP_B
    assert NO_ONLY <= GROUP_A


# ---------------------------------------------------------------------------
# Grammar, glossary, band and history
# ---------------------------------------------------------------------------


def _block(rule_id: str, body: str | None = None, *, scope: str = "Scope: every synthetic case.",
           not_: str = "Not: anything else.", pinned: tuple[str, ...] = ("Pinned: none",),
           why: str | None = None) -> str:
    lines = [f"**{rule_id}.** {body or f'Rule {rule_id} MUST hold every day.'}", scope, not_, *pinned]
    if why:
        lines.append(why)
    return "\n".join(lines)


_GOOD = "\n\n".join([
    "### 1.1 The supreme objective",
    _block("PRIN-01"),
    _block("PRIN-02", "A green day IS not a hard day, and the ladder (`R+0 .. R+19`) MAY say so.", why="Why: C07."),
    _block("PRIN-03", "The system MUST NOT stop, e.g. on one reading."),
])


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        pytest.param(_GOOD.replace("every day", "every day on 2026-09-18", 1), "ISO date", id="iso-date"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 MUST " + "x" * 400 + "."), "characters", id="line-over-400"),
        pytest.param(_GOOD.replace("MUST hold every day.", "MUST hold. It does.", 1), "one sentence", id="two-sentences"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 holds every day."), "MUST", id="no-obligation-or-definition"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 MUST hold every day"), "end in", id="no-final-period"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.\nNot: anything else.", "Not: anything else.\nScope: every synthetic case.", 1), "order", id="not-before-scope"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.\n", "", 1), "Scope", id="missing-scope"),
        pytest.param(_GOOD.replace("Not: anything else.\n", "", 1), "Not", id="missing-not"),
        pytest.param(_GOOD.replace("Pinned: none\nWhy: C07.", "Why: C07."), "Pinned", id="missing-pinned"),
        pytest.param(_GOOD.replace("Why: C07.", "Why: C07.\nWhy: again."), "order", id="two-why"),
        pytest.param(_GOOD.replace("Pinned: none", "Pinned: `tests/test_x.py::test_y`", 1), "Pinned", id="pinned-as-code-span"),
        pytest.param(_GOOD.replace("Pinned: none", "Pinned: nothing yet", 1), "Pinned", id="pinned-malformed"),
        pytest.param(_GOOD.replace("### 1.1 The supreme objective", "### 1.1 The supreme objectives"), "heading", id="unknown-heading"),
        pytest.param(_GOOD + "\n\nSome prose the grammar does not allow.", "not a rule", id="stray-prose"),
        pytest.param(_GOOD + "\n\n" + _block("PRIN-01"), "duplicate", id="duplicate-id"),
        pytest.param(_GOOD.replace("**PRIN-01.**", "**PRIN-1.**"), "not a rule", id="bad-id-format"),
        pytest.param(_GOOD.replace("**PRIN-01.**", "**XYZ-01.**"), "not a rule", id="unknown-prefix"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.", "Scope: every synthetic case.\n", 1), "not a rule", id="blank-inside-block"),
    ],
)
def test_rule_grammar_errors_turns_red_on_each_violation(text: str, fragment: str) -> None:
    errors = rule_grammar_errors(text)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_rule_grammar_errors_is_green_on_valid_blocks_with_the_r6_exemptions() -> None:
    """Code spans are skipped (FIG-01's ``R+0 .. R+19``), ``e.g.`` is not a terminator, a definition
    form and a MUST NOT pass, and the Glossary's lines are left to ``glossary_errors``."""
    glossary = f"{GLOSSARY_HEADING}\n\n- **T-01 judgeable** IS a dataset. It has two sentences.\n"
    text = f"{HEADINGS[0]}\n\n{glossary}\n{HEADINGS[1]}\n\n{_GOOD}\n"
    errors = rule_grammar_errors(text)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert rule_ids(text) == ["PRIN-01", "PRIN-02", "PRIN-03"]


def _glossary(n: int = 33, **override: str) -> str:
    lines = []
    for i in range(1, n + 1):
        tid = f"T-{i:02d}"
        definition = "OPEN (IDEA-088)" if tid == "T-25" else f"the synthetic definition {i}"
        lines.append(override.get(tid, f"- **{tid} term{i}** IS {definition}."))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("text", "complete", "fragment"),
    [
        pytest.param(_glossary(32), True, "33", id="thirty-two-terms"),
        pytest.param(_glossary() + "- **T-01 again** IS twice.\n", True, "duplicate", id="duplicate-term"),
        pytest.param(_glossary(**{"T-03": "- T-03 established IS n days."}), True, "format", id="bad-format"),
        pytest.param(_glossary(**{"T-03": "- **T-03 established** means n days."}), True, "format", id="no-IS"),
        pytest.param(_glossary(**{"T-25": "- **T-25 rate** IS per cell."}), True, "T-25", id="t25-not-open"),
        pytest.param(_glossary(**{"T-04": "- **T-04 stale** IS " + "y" * 400 + "."}), True, "characters", id="over-400"),
        pytest.param("- **T-25 rate** IS per cell.\n", False, "T-25", id="partial-t25-not-open"),
    ],
)
def test_glossary_errors_turns_red(text: str, complete: bool, fragment: str) -> None:
    errors = glossary_errors(text, complete=complete)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_glossary_errors_is_green_complete_partial_and_inside_research() -> None:
    assert glossary_errors(_glossary()) == []
    assert glossary_errors(_glossary(3), complete=False) == []
    research = f"{HEADINGS[0]}\n\n{GLOSSARY_HEADING}\n\n{_glossary()}\n{HEADINGS[1]}\n\n{_block('PRIN-01')}\n"
    assert glossary_errors(research) == []


@pytest.mark.parametrize(
    ("text", "red"),
    [
        pytest.param("**HRV-07.** The SWC band MUST be computed.", False, id="swc-sentence"),
        pytest.param("The band of rMSSD readings is wide.", False, id="rmssd-sentence"),
        pytest.param("- **T-07 band** IS the hrv_trend band.", False, id="lowercase-hrv"),
        pytest.param("**REG-02.** ACWR MUST keep a wide band.", True, id="acwr-band"),
        pytest.param("**HRV-40.** The BANDS MUST be clipped.", True, id="only-hrv-is-the-rule-id"),
        pytest.param("The SWC reading holds. The band is wide.", True, id="other-sentence-carries-swc"),
        pytest.param("test_band_reconciliation.py::test_x holds.", False, id="identifier-not-a-word"),
    ],
)
def test_band_errors(text: str, red: bool) -> None:
    errors = band_errors(text)
    print(f"[slice compared] {text!r}: {errors}")
    assert bool(errors) == red, errors


def _history(n: int = 41, retired: str = "") -> str:
    entries = [f"- **H-{i:02d}** (2026-09-{(i % 28) + 1:02d}) Synthetic change {i}. → DOC-01" for i in range(1, n + 1)]
    return "# research/00 history\n\n" + "\n".join(entries) + "\n\n## Retired IDs\n" + retired


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        pytest.param(_history().replace("(2026-09-02)", "(2 Sept)"), "format", id="bad-date"),
        pytest.param(_history().replace("Synthetic change 3. → DOC-01", "Synthetic change 3."), "format", id="no-arrow"),
        pytest.param(_history().replace("→ DOC-01", "→ nothing", 1), "format", id="arrow-cites-no-id"),
        pytest.param(_history().replace("Synthetic change 5.", "The system MUST change 5."), "MUST", id="uppercase-must"),
        pytest.param(_history().replace("Synthetic change 5.", "It MAY change."), "MAY", id="uppercase-may"),
        pytest.param(_history().replace("## Retired IDs\n", ""), "Retired IDs", id="no-retired-section"),
        pytest.param(_history(retired="- **DOC-15** retired\n"), "retired", id="bad-retired-line"),
        pytest.param(_history() + "\n**DOC-01.** A rule MUST hold.\n", "rule line", id="rule-line"),
        pytest.param(_history().replace("- **H-02**", "- **H-01**"), "duplicate", id="duplicate-entry"),
    ],
)
def test_history_errors_turns_red(text: str, fragment: str) -> None:
    errors = history_errors(text)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_history_errors_is_green_with_multiple_dates_undated_and_lowercase_modals() -> None:
    text = _history(retired="- **DOC-15** retired → H-01\n").replace(
        "(2026-09-03)", "(2026-08-31, 2026-09-16)").replace("(2026-09-05)", "(undated)").replace(
        "Synthetic change 6.", "The rule must change and may stay.").replace("→ DOC-01", "→ DOC-01, HRV-31", 1)
    errors = history_errors(text)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert history_ids(text) == [f"H-{i:02d}" for i in range(1, 42)]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _row(inv: str, sentence: str, new: str, decision: str = "—", meaning: str = "no",
         key: str = "—", cited: str = "§5.4") -> str:
    return f"| {inv} | {sentence} | {cited} | {new} | {decision} | {meaning} | {key} |"


def _as_row(line: str) -> dict[str, str]:
    return parse_traceability(line)[0]


def test_parse_traceability_reads_the_header_form_and_a_header_less_fragment() -> None:
    body = (_row("HRV-37", "A band \\| pipe.", "HRV-37", "C04", "yes", "C04-at-least") + "\n"
            + _row(ADDITION, ADDITION, "HRV-47", "C03"))
    full = f"{TRACE_HEADER}\n|---|---|---|---|---|---|---|\n{body}\n"
    for text in (full, body):
        rows = parse_traceability(text)
        print(f"[slice compared] {rows}")
        assert [r["inventory ID"] for r in rows] == ["HRV-37", ADDITION]
        assert set(rows[0]) == set(TRACE_COLUMNS)
        assert rows[0]["inventory sentence"] == "A band | pipe." and rows[0]["old-meaning key"] == "C04-at-least"
    with pytest.raises(ValueError, match="columns"):
        parse_traceability("| HRV-01 | too | few |")
    with pytest.raises(ValueError, match="header"):
        parse_traceability("| inventory ID | sentence | cited | new | decision | meaning | key |")


def test_load_meanings_jsonl_builds_old_meanings_and_rejects_bad_lines() -> None:
    line = {"key": "k1", "pattern": "a", "example": "a", "source": "x.md:1@4e47d0e", "decision": "C05"}
    entries = load_meanings_jsonl(json.dumps(line) + "\n\n")
    assert entries == {"k1": OldMeaning("a", "a", "x.md:1@4e47d0e", "C05")}
    with pytest.raises(ValueError, match="duplicate"):
        load_meanings_jsonl(json.dumps(line) + "\n" + json.dumps(line))
    with pytest.raises(ValueError, match="fields"):
        load_meanings_jsonl(json.dumps({"key": "k1", "pattern": "a"}))
    with pytest.raises(ValueError, match="JSON"):
        load_meanings_jsonl("{not json")


def test_inventory_sentences_reads_the_current_rule_cell_verbatim(tmp_path) -> None:
    inv = tmp_path / "inventory.md"
    inv.write_text(
        "| ID | Current rule | Assembled from | Cited today as | Status |\n|---|---|---|---|---|\n"
        "| HRV-09 | Every count is in distinct local days, with `min_window_readings` = 3. | L217 | §5.4 | SETTLED |\n"
        "| C01 | not a rule row | x | y | z |\n",
        encoding="utf-8",
    )
    assert inventory_sentences(inv) == {"HRV-09": "Every count is in distinct local days, with `min_window_readings` = 3."}


def test_rule_blocks_and_ids_parse_each_block_with_its_lines() -> None:
    blocks = rule_blocks(_GOOD)
    assert list(blocks) == ["PRIN-01", "PRIN-02", "PRIN-03"]
    assert blocks["PRIN-02"].splitlines()[-1] == "Why: C07."
    assert blocks["PRIN-01"].splitlines()[1:] == ["Scope: every synthetic case.", "Not: anything else.", "Pinned: none"]


# ---------------------------------------------------------------------------
# A complete synthetic world: every draft, all 149 rows, every decision
# ---------------------------------------------------------------------------

#: The row each decision sits on in the synthetic world. PRIN-08 holds the shared cell "C24, C25"
#: (R3 "A shared cell"): C24 authorizes its ``yes``, and C25, no-only, rides along.
_WORLD_DECISION_ROWS = {
    "C01": "HRV-31", "C02": "HRV-31", "C03": "HRV-34", "C04": "HRV-37", "C05": "GATE-02", "C06": "PRIN-15",
    "C07": "PRIN-14", "C08": "ARCH-08", "C09": "HRV-33", "C10": "HRV-15", "C11": "HRV-14", "C12": "HRV-12",
    "C13": "HRV-38", "C14": "HRV-34", "C15": "HRV-34", "C16": "HRV-21", "C17": "HRV-24", "C18": "HRV-30",
    "C19": "PRIN-10", "C20": "AUT-04", "C21": "DEC-01", "C22": "GOAL-02", "C23": "ARB-02", "C24": "PRIN-08",
    "C25": "PRIN-08", "C26": "COLD-01", "C27": "HRV-05", "C28": "LT1-01", "C29": "FTO-06", "C30": "REG-19",
    "C31": "DOC-06", "C32": "HRV-07", "C33": "PRIN-12", "C37": "GATE-03", "C38": "DOC-09",
}

_WORLD_BODIES = {
    "DOC-09": ("Rule DOC-09 MUST hold every day, so research/00 states only current rules and a dated summary "
               "goes to the history file."),
    "PRIN-12": "Rule PRIN-12 MUST hold every day and serves `recency_tolerance_days`.",
    "PRIN-14": "Rule PRIN-14 MUST hold every day against the forbidden direction.",
    "PRIN-15": ("Rule PRIN-15 MUST hold every day, and the F005-parity population, `DEFERRED_EXCEPTION` (IDEA-087) "
                "and the HRV-25 population (IDEA-099) may not grow."),
    "HRV-07": "Rule HRV-07 MUST hold every day with the half-width `max(0.5 · SD(ln rMSSD), 0.01)`.",
    "HRV-31": ("Rule HRV-31 MUST hold every day for a dataset that could not have been selected: not judgeable, "
               "or skipped, against the dataset being judged."),
    "HRV-37": "Rule HRV-37 MUST hold every day after more than 21 silent days.",
    "GATE-02": "Rule GATE-02 MUST hold every day, and the system MUST NOT add hysteresis.",
}
_WORLD_PINNED = {
    "PRIN-12": ("Pinned: none (F010)",),
    "PRIN-15": ("Pinned: none (F009)",),
    "GATE-02": ("Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::"
                "test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned",),
}
_WORLD_HEADINGS = {"doc-goal": HEADINGS[2], "arch-dec": HEADINGS[11], "hrv": HEADINGS[-1]}
_WORLD_C05 = {"key": "C05-reopens", "pattern": "worse rate reopens",
              "example": "a worse rate reopens the deferred hysteresis decision",
              "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C05"}
#: F008 AC6: every ``yes`` row names an old-meaning key. Each group's ``yes`` rows share one key; the
#: arch-dec rows share C05's, and the other two quote ``_old_show``'s text so the verbatim check holds.
_WORLD_OLD = {
    "doc-goal": {"key": "doc-goal-old", "pattern": "flip rate is measured", "example": "the flip rate is measured",
                 "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C24"},
    "arch-dec": _WORLD_C05,
    "hrv": {"key": "hrv-old", "pattern": "deferred hysteresis decision [(]f006[)]",
            "example": "the deferred hysteresis decision (F006)",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C01"},
}
#: DOC-15 retires to its history entry; FTO-06 merges into FTO-05 under C29 (Group A, so H-38).
_WORLD_NEW_ID = {"DOC-15": "H-01 (moved to history)", "FTO-06": "FTO-05"}
_WORLD_SENTENCE = {"FTO-06": "The rule holds every day."}


def _sorted_ids(ids) -> list[str]:
    return sorted(ids, key=lambda i: (PREFIX_ORDER.index(i.rsplit("-", 1)[0]), int(i.rsplit("-", 1)[1])))


def _world_sentence(inv: str) -> str:
    return _WORLD_SENTENCE.get(inv, f"Rule {inv} holds every day.")


def _world_files() -> dict[str, str]:
    decisions: dict[str, list[str]] = {}
    for c, row_id in _WORLD_DECISION_ROWS.items():
        decisions.setdefault(row_id, []).append(c)
    files = {"glossary.txt": _glossary(), "history.txt": _history().replace("→ DOC-01", "→ FTO-06", 1)}
    for group, prefixes in GROUPS.items():
        ids = _sorted_ids(i for i in INVENTORY_IDS if i.split("-")[0] in prefixes)
        blocks, rows, meanings = [_WORLD_HEADINGS[group]], [], []
        for inv in ids:
            new = _WORLD_NEW_ID.get(inv, inv)
            if new == inv:
                scope = "Scope: every per-tier dataset." if inv in PER_TIER_ANCHORS else "Scope: every synthetic case."
                pinned = _WORLD_PINNED.get(inv, ("Pinned: none",))
                blocks.append(_block(inv, _WORLD_BODIES.get(inv), scope=scope, pinned=pinned))
            cs = decisions.get(inv, [])
            meaning = "yes" if any(c not in NO_ONLY for c in cs) else "no"
            key = _WORLD_OLD[group]["key"] if meaning == "yes" else ADDITION
            rows.append(_row(inv, _world_sentence(inv), new, ", ".join(cs) or ADDITION, meaning, key))
        meanings.append(json.dumps(_WORLD_OLD[group], ensure_ascii=False))
        files[f"{group}.rules.txt"] = "\n\n".join(blocks) + "\n"
        files[f"{group}.trace.txt"] = "\n".join(rows) + "\n"
        files[f"{group}.meanings.txt"] = "\n".join(meanings) + ("\n" if meanings else "")
    return files


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    drafts = tmp_path / "drafts"
    drafts.mkdir(exist_ok=True)
    for name, text in files.items():
        (drafts / name).write_text(text, encoding="utf-8")
    return drafts


def _inventory(tmp_path: Path) -> Path:
    lines = ["| ID | Current rule | Assembled from | Cited today as | Status |", "|---|---|---|---|---|"]
    lines += [f"| {i} | {_world_sentence(i)} | L1 | §5.4 | SETTLED |" for i in _sorted_ids(INVENTORY_IDS)]
    path = tmp_path / "inventory.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _old_show(path: str) -> str:
    """Stands in for ``git show 4e47d0e:<path>``: the committed suite runs where there is no full clone."""
    return "... the flip rate is measured, and a worse rate reopens the deferred hysteresis decision (F006) ..."


def _world(tmp_path: Path):
    a = assemble(_write(tmp_path, _world_files()))
    return a, parse_traceability(a.table)


def test_the_synthetic_world_passes_assemble_check_so_the_checkers_compose(tmp_path) -> None:
    """Green on a complete world proves the checkers do not contradict each other; every red case
    below is one mutation of it."""
    errors = assemble_check(_write(tmp_path, _world_files()))
    print(f"[slice compared] assemble_check over the synthetic world: {errors[:10]}")
    assert errors == []


def test_assemble_orders_headings_rules_and_fills_the_retired_ids(tmp_path) -> None:
    files = {
        "glossary.txt": _glossary(2),
        "history.txt": _history(2),
        "hrv.rules.txt": f"{HEADINGS[-1]}\n\n{_block('HRV-10')}\n\n{_block('HRV-02')}\n\n{HEADINGS[2]}\n\n{_block('HRV-01')}\n",
        "hrv.trace.txt": "\n".join(_row(i, "s", i) for i in ("HRV-01", "HRV-02", "HRV-10")) + "\n"
                         + _row("HRV-03", "s", "H-02 (history)") + "\n",
        "doc-goal.rules.txt": f"{HEADINGS[-1]}\n\n{_block('DOC-02')}\n\n{_block('PRIN-01')}\n",
        "doc-goal.trace.txt": _row("DOC-02", "s", "DOC-02") + "\n" + _row("PRIN-01", "s", "PRIN-01") + "\n"
                              + _row("DOC-01", "s", "DOC-02", "C38", "yes") + "\n",
    }
    a = assemble(_write(tmp_path, files))
    heads = [line for line in a.research.splitlines() if line.startswith("#")]
    order = rule_ids(a.research)
    print(f"[slice compared] headings {heads[:4]} ... rules {order}")
    assert heads == [HEADINGS[0], GLOSSARY_HEADING, *HEADINGS[1:]]
    assert order == ["HRV-01", "DOC-02", "PRIN-01", "HRV-02", "HRV-10"]
    retired = a.history.split("## Retired IDs", 1)[1]
    assert re.findall(r"^- \*\*(\S+)\*\* retired → (H-\d{2})$", retired, re.M) == [("DOC-01", "H-39"), ("HRV-03", "H-02")]
    assert len(parse_traceability(a.table)) == 7 and a.table.startswith(TRACE_HEADER)


def test_retired_ids_follow_r11s_h_nn_rule() -> None:
    rows = parse_traceability("\n".join([
        _row("DOC-15", "s", "H-06 (moved to history)"),
        _row("DOC-06", "s", "IND-01", "C31", "yes"),
        _row("DOC-13", "s", "DOC-14", "C29"),
        _row("HRV-12", "s", "HRV-13", "HRV-11", "yes"),
        _row("REG-02", "s", "REG-03", "T-07, R9", "yes"),
        _row("FIG-05", "s", "FIG-04"),
        _row("HRV-15", "s", "HRV-15, H-17", "C10", "yes"),
    ]))
    retired = retired_ids(rows)
    print(f"[slice compared] {retired}")
    assert retired == {"DOC-15": "H-06", "DOC-06": "H-39", "DOC-13": "H-38", "HRV-12": "H-40", "REG-02": "H-41",
                       "FIG-05": None}


# ---------------------------------------------------------------------------
# Traceability, both directions (AC6), and the four "proving it" mutations
# ---------------------------------------------------------------------------


def test_traceability_errors_is_green_on_the_world(tmp_path) -> None:
    a, rows = _world(tmp_path)
    assert traceability_errors(rows, a.research, a.history, a.meanings) == []


def _drop_rule(research: str, rule_id: str) -> str:
    return "\n".join(line for line in research.splitlines() if not line.startswith(f"**{rule_id}.**"))


@pytest.mark.parametrize(
    "mutation",
    ["delete-a-rule-line", "add-an-unmapped-rule", "point-a-row-at-HRV-999", "key-pattern-misses-its-example"],
)
def test_ac6_proving_it_each_mutation_turns_the_check_red(tmp_path, mutation: str) -> None:
    a, rows = _world(tmp_path)
    research, meanings = a.research, dict(a.meanings)
    if mutation == "delete-a-rule-line":
        research = _drop_rule(research, "HRV-09")
    elif mutation == "add-an-unmapped-rule":
        research = research.replace(_block("HRV-09"), _block("HRV-09") + "\n\n" + _block("HRV-47"))
    elif mutation == "point-a-row-at-HRV-999":
        rows = [dict(r, **{"new ID(s)": "HRV-999"}) if r["inventory ID"] == "HRV-09" else r for r in rows]
    else:
        meanings["C05-reopens"] = OldMeaning("worse rate re-opens", _WORLD_C05["example"], _WORLD_C05["source"], "C05")
    errors = traceability_errors(rows, research, a.history, meanings) + old_meaning_errors(meanings, research)
    print(f"[slice compared] {mutation}: {errors}")
    assert errors, f"{mutation} left the traceability check green"


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ("drop-row", "missing"), ("duplicate-row", "duplicate"), ("unknown-id", "not an inventory ID"),
        ("empty-new-id", "names no new ID"), ("history-unresolved", "H-99"), ("reuse-retired", "reused"),
        ("missing-key", "no such old-meaning key"), ("merged-without-decision", "retire"),
    ],
)
def test_traceability_errors_turns_red(tmp_path, mutation: str, fragment: str) -> None:
    a, rows = _world(tmp_path)
    research = a.research
    first = rows[0]["inventory ID"]
    if mutation == "drop-row":
        rows = rows[1:]
    elif mutation == "duplicate-row":
        rows = rows + [rows[0]]
    elif mutation == "unknown-id":
        rows = rows + [dict(rows[0], **{"inventory ID": "HRV-47"})]
    elif mutation == "empty-new-id":
        rows = [dict(rows[0], **{"new ID(s)": "(dropped)"})] + rows[1:]
    elif mutation == "history-unresolved":
        rows = [dict(r, **{"new ID(s)": "H-99"}) if r["inventory ID"] == "DOC-15" else r for r in rows]
    elif mutation == "reuse-retired":
        research = research.replace(_block("DOC-14"), _block("DOC-14") + "\n\n" + _block("DOC-15"))
        rows = rows + [_as_row(_row(ADDITION, ADDITION, "DOC-15", "C38", "yes"))]
    elif mutation == "missing-key":
        rows = [dict(r, **{"old-meaning key": "nope"}) if r["inventory ID"] == first else r for r in rows]
    else:
        rows = [dict(r, decision=ADDITION) if r["inventory ID"] == "FTO-06" else r for r in rows]
    errors = traceability_errors(rows, research, a.history, a.meanings)
    print(f"[slice compared] {mutation}: {errors}")
    assert any(fragment in e for e in errors), errors


# ---------------------------------------------------------------------------
# The decision column (R3)
# ---------------------------------------------------------------------------


def test_decision_column_errors_is_green_on_the_world_complete_and_per_group(tmp_path) -> None:
    _a, rows = _world(tmp_path)
    assert decision_column_errors(rows, True) == []
    for prefixes in GROUPS.values():
        group_rows = [r for r in rows if r["inventory ID"].split("-")[0] in prefixes]
        assert decision_column_errors(group_rows, False) == []


@pytest.mark.parametrize(
    ("rows", "complete", "fragment"),
    [
        pytest.param([_row("HRV-14", "s", "HRV-14", "C11", "yes", "k")], False, "never authorizes", id="no-only-on-yes"),
        pytest.param([_row("PRIN-08", "s", "PRIN-08", "C25", "yes", "k")], False, "never authorizes", id="no-only-alone-on-yes"),
        pytest.param([_row("PRIN-08", "s", "PRIN-08", "C19, C25", "yes", "k")], False, "never authorizes", id="no-only-pair-on-yes"),
        pytest.param([_row(ADDITION, ADDITION, "HRV-47", ADDITION, "yes", "k")], False, "cite a decision", id="addition-without-decision"),
        pytest.param([_row("HRV-14", "s", "HRV-14", "C34", "yes", "k")], False, "not a decision", id="further-c-number"),
        pytest.param([_row("HRV-14", "s", "HRV-14", "C11", "maybe")], False, "yes or no", id="bad-meaning"),
        pytest.param([_row("HRV-11", "s", "HRV-11", "C01", "yes")], False, "old-meaning key", id="yes-row-without-key"),
        pytest.param([_row("HRV-11", "s", "HRV-11", "C01", "yes", "")], False, "old-meaning key", id="yes-row-with-empty-key"),
        pytest.param([_row("HRV-31", "s", "HRV-31", "C01, C02", "yes", "k")], True, "C03", id="complete-missing-decision"),
    ],
)
def test_decision_column_errors_turns_red(rows: list[str], complete: bool, fragment: str) -> None:
    errors = decision_column_errors([_as_row(r) for r in rows], complete)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(
    "row",
    [
        # R3 "A shared cell": C24 authorizes the yes, and C25 rides along with its key for F011.
        pytest.param(_row("PRIN-08", "s", "PRIN-08", "C24, C25", "yes", "C25-quarantine-list"), id="shared-cell-prin-08"),
        # R3 "Ownership is coverage, not exclusivity": C06's owner is doc-goal, and GATE-01 is arch-dec.
        pytest.param(_row("GATE-01", "s", "GATE-01", "C06", "yes", "C06-one-exception"), id="cross-group-gate-01"),
        pytest.param(_row("HRV-25", "s", "HRV-25", "C06", "yes", "C06-hrv-25"), id="cross-group-hrv-25"),
        pytest.param(_row("HRV-14", "s", "HRV-14", "C11", "no"), id="no-only-on-a-no-row-without-key"),
        pytest.param(_row("PRIN-10", "s", "PRIN-10", "C19", "no", "C19-schemas"), id="no-row-with-key"),
    ],
)
def test_decision_column_errors_is_green_on_shared_cells_cross_group_yes_rows_and_keyed_rows(row: str) -> None:
    errors = decision_column_errors([_as_row(row)], False)
    print(f"[slice compared] {row} -> {errors}")
    assert errors == []


def test_group_coverage_errors_names_each_owned_decision_without_a_yes_row() -> None:
    """R3 (user, 2026-09-25): the owner group writes at least one ``yes`` row per C-number it owns;
    other groups' ``yes`` rows for it are allowed. NO_ONLY C-numbers never back a ``yes``, so no
    group owes one."""
    owned = sorted(c for c, g in YES_ROW_OWNER.items() if g == "doc-goal" and c not in NO_ONLY)
    only_c24 = group_coverage_errors([_as_row(_row("PRIN-08", "s", "PRIN-08", "C24, C25", "yes", "k"))], "doc-goal")
    print(f"[slice compared] doc-goal owns {owned}; with only PRIN-08 yes C24, C25 -> {only_c24}")
    for c in owned:
        assert any(c in e for e in only_c24) == (c != "C24"), (c, only_c24)
    assert not any("C25" in e or "C19" in e for e in only_c24), only_c24
    # a no row, or a yes row in the wrong group only, does not cover
    as_no = group_coverage_errors([_as_row(_row("PRIN-15", "s", "PRIN-15", "C06", "no"))], "doc-goal")
    assert any("C06" in e for e in as_no), as_no
    elsewhere = group_coverage_errors([_as_row(_row("GATE-01", "s", "GATE-01", "C06", "yes", "k"))], "arch-dec")
    assert not any("C06" in e for e in elsewhere), elsewhere
    covered = group_coverage_errors([_as_row(_row("PRIN-15", "s", "PRIN-15", "C06", "yes", "k"))], "doc-goal")
    assert not any("C06" in e for e in covered), covered
    every = [_as_row(_row(f"DOC-{i:02d}", "s", f"DOC-{i:02d}", c, "yes", "k")) for i, c in enumerate(owned, 1)]
    assert group_coverage_errors(every, "doc-goal") == []


def test_decision_column_errors_no_longer_checks_ownership() -> None:
    """Mutation witness for Q1: the old exclusivity check named YES_ROW_OWNER on a cross-group row."""
    errors = decision_column_errors([_as_row(_row("GATE-01", "s", "GATE-01", "C06", "yes", "k"))], False)
    assert not any("YES_ROW_OWNER" in e for e in errors), errors


def test_decision_column_errors_needs_a_yes_row_for_every_other_decision(tmp_path) -> None:
    _a, rows = _world(tmp_path)
    rows = [dict(r, **{"meaning changed": "no"}) if "C22" in r["decision"] else r for r in rows]
    errors = decision_column_errors(rows, True)
    print(f"[slice compared] {errors}")
    assert any("C22" in e and "yes row" in e for e in errors), errors


# ---------------------------------------------------------------------------
# AC7 operative strings, the AC9 proxy, pins and old meanings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", sorted(OPERATIVE))
def test_operative_string_errors_is_red_when_the_named_rule_loses_its_string(tmp_path, decision: str) -> None:
    a, rows = _world(tmp_path)
    assert operative_string_errors(a.research, rows, meanings=a.meanings) == []
    rule_id, strings = OPERATIVE[decision]
    block = rule_blocks(a.research)[rule_id]
    for s in strings:
        gutted = a.research.replace(block, block.replace(s, "zzz"))
        errors = operative_string_errors(gutted, rows, meanings=a.meanings)
        print(f"[slice compared] {decision} without {s!r} in {rule_id}: {errors}")
        assert any(decision in e and rule_id in e for e in errors), (s, errors)


def test_operative_string_errors_builds_the_c05_absence_from_old_meanings(tmp_path) -> None:
    a, rows = _world(tmp_path)
    reopened = a.research.replace("the system MUST NOT add hysteresis.",
                                  "the system MUST NOT add hysteresis, and a worse rate reopens it.")
    errors = operative_string_errors(reopened, rows, meanings=a.meanings)
    print(f"[slice compared] {errors}")
    assert any("C05" in e and "C05-reopens" in e for e in errors), errors
    errors = operative_string_errors(a.research, rows, meanings={})
    assert any("C05" in e and "OLD_MEANINGS" in e for e in errors), errors


def test_operative_string_errors_checks_only_the_decisions_its_rows_cite(tmp_path) -> None:
    a, rows = _world(tmp_path)
    hrv = [r for r in rows if r["inventory ID"].startswith("HRV-")]
    hrv_text = "\n\n".join(b for i, b in rule_blocks(a.research).items() if i.startswith("HRV-"))
    assert operative_string_errors(hrv_text, hrv, meanings={}, group="hrv") == []
    borrowed = hrv + [_as_row(_row("HRV-25", "s", "HRV-25", "C06", "yes"))]
    assert operative_string_errors(hrv_text, borrowed, meanings={}, group="hrv") == []
    assert any("PRIN-15" in e for e in operative_string_errors(hrv_text, borrowed, meanings={}))


@pytest.mark.parametrize(
    ("sentence", "block", "fragment"),
    [
        pytest.param("A gap of 21 days resets.", "**HRV-35.** A gap of 22 days MUST reset.", "21", id="number-lost"),
        pytest.param("Uses `min_window_readings`.", "**HRV-13.** It MUST use min window readings.", "min_window_readings", id="identifier-lost"),
        pytest.param("It is strictly below.", "**HRV-26.** It MUST be below.", "strictly", id="strictly-lost"),
        pytest.param("It holds more than 21.", "**HRV-37.** It MUST hold over 21.", "more than", id="more-than-lost"),
        pytest.param("Every count is 3 at least.", "**HRV-09.** A count MUST be 3 at least.", "every", id="every-lost"),
        pytest.param("It is 3 at least.", "**HRV-09.** It MUST be 3.", "at least", id="at-least-lost"),
        pytest.param("It is never CV.", "**HRV-07.** It MUST NOT be CV.", "never", id="never-lost"),
        pytest.param("It changes only here.", "**DOC-04.** It MUST change here.", "only", id="only-lost"),
        pytest.param("Left unchanged (2026-09-18).", "**HRV-33.** It MUST stay unchanged.", "H-NN", id="date-without-history-cite"),
    ],
)
def test_proxy_errors_turns_red_on_each_lost_token(sentence: str, block: str, fragment: str) -> None:
    errors = proxy_errors(sentence, block)
    print(f"[slice compared] {sentence!r} vs {block!r}: {errors}")
    assert any(fragment in e for e in errors), errors


def test_proxy_errors_is_green_when_every_token_survives() -> None:
    sentence = ("Every count uses `min_baseline_readings` = 14, 21 does not reset (2026-09-18), and it is never CV "
                "− only 0.5·SD.")
    block = ("**HRV-09.** Every count MUST use `min_baseline_readings` = 14, so 21 does not reset and it is never CV, "
             "only 0.5·SD.\nScope: every count.\nNot: the 2 CV forms.\nPinned: none\nWhy: user decision H-18.")
    errors = proxy_errors(sentence, block)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert proxy_errors("Every day is judged – not 7−1.", "**HRV-08.** Every day MUST be judged, 7-1 excepted.") == []
    assert proxy_errors("Everything is judged.", "**HRV-08.** All is judged.") == []


def test_pinned_errors_finds_real_nodes_and_rejects_missing_ones() -> None:
    here = f"runcoach-api/tests/{Path(__file__).name}"
    good = (f"Pinned: {here}::test_pinned_errors_finds_real_nodes_and_rejects_missing_ones\n"
            f"Pinned: {here}::test_band_errors[acwr-band]\nPinned: none\nPinned: none (F009)")
    assert pinned_errors(good, _REPO_ROOT) == []
    errors = pinned_errors(f"Pinned: runcoach-api/tests/no_such_file.py::test_x\nPinned: {here}::test_no_such_def",
                           _REPO_ROOT)
    print(f"[slice compared] {errors}")
    assert len(errors) == 2 and "no_such_file" in errors[0] and "test_no_such_def" in errors[1]


@pytest.mark.parametrize(
    ("entry", "research", "fragment"),
    [
        pytest.param(OldMeaning("(unclosed", "x", "a.md:1@4e47d0e", "C05"), "", "compile", id="pattern-does-not-compile"),
        pytest.param(OldMeaning("worse rate re-opens", "a worse rate reopens it", "a.md:1@4e47d0e", "C05"), "", "example", id="pattern-misses-example"),
        pytest.param(OldMeaning("worse rate reopens", "a worse rate reopens it", "a.md:1@4e47d0e", "C05"), "A **worse** rate `reopens`.", "research/00", id="pattern-matches-research"),
        pytest.param(OldMeaning("worse rate reopens", "a worse rate reopens it", "a.md", "C05"), "", "source", id="source-malformed"),
    ],
)
def test_old_meaning_errors_turns_red(entry, research: str, fragment: str) -> None:
    errors = old_meaning_errors({"k": entry}, research)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_old_meaning_errors_is_green_and_matches_the_normalized_example() -> None:
    entry = OldMeaning("capture hole of at least gapresetdays", "an internal capture hole of at least `gap_reset_days`",
                       "specification/research/00-design-decisions.md:230@4e47d0e", "C04")
    assert old_meaning_errors({"k": entry}, "A capture hole of more than `gap_reset_days` (21) days.") == []


# ---------------------------------------------------------------------------
# Review scope (R7)
# ---------------------------------------------------------------------------


def test_required_review_rows_is_every_block_and_every_scope_not_and_why_line() -> None:
    research = "\n\n".join([_block("DOC-01", why="Why: C38."), _block("HRV-01"), _block("HRV-02")])
    rows = [_as_row(_row("DOC-01", "s", "DOC-01", "C38", "yes")), _as_row(_row("HRV-01", "s", "HRV-01, HRV-02")),
            _as_row(_row("HRV-03", "s", "H-05 (history)"))]
    need = required_review_rows(research, rows)
    print(f"[slice compared] {need}")
    assert need == ["DOC-01", "DOC-01/Scope", "DOC-01/Not", "DOC-01/Why", "HRV-01", "HRV-01/Scope", "HRV-01/Not",
                    "HRV-02", "HRV-02/Scope", "HRV-02/Not"]
    assert required_review_rows(research, rows, group="hrv") == need[4:]
    assert required_review_rows(research, rows, group="arch-dec") == []


# ---------------------------------------------------------------------------
# assemble_check and fragment_errors, each red case one mutation of the green world
# ---------------------------------------------------------------------------


def _sub(name: str, old: str, new: str, count: int = -1):
    def mutate(files: dict[str, str]) -> None:
        assert old in files[name], f"the mutation's target {old!r} is not in {name}"
        files[name] = files[name].replace(old, new, count)
    return mutate


def _put(name: str, text: str, *, append: bool = False):
    def mutate(files: dict[str, str]) -> None:
        files[name] = (files.get(name, "") if append else "") + text
    return mutate


_C05_LINE = json.dumps(_WORLD_C05, ensure_ascii=False)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        pytest.param(_sub("hrv.rules.txt", "Rule HRV-02 MUST", "The band MUST"), "band", id="band-outside-hrv"),
        pytest.param(_sub("history.txt", "→ FTO-06", "→ FTO-09"), "arrow", id="history-arrow-unresolved"),
        pytest.param(_sub("hrv.rules.txt", "Rule HRV-02 MUST hold every day.", "Rule HRV-02 MUST hold. Rule HRV-02 MUST hold every day."), "one sentence", id="grammar-after-merge"),
        pytest.param(_sub("hrv.rules.txt", "Scope: every per-tier dataset.", "Scope: every dataset.", 1), "per-tier dataset", id="anchor-scope-lost"),
        pytest.param(_sub("arch-dec.rules.txt", "**FIG-02.** Rule FIG-02 MUST hold every day.", "**FIG-02.** FIG-01. Rule FIG-01 MUST hold every day and FIG-02."), "anchor", id="fig01-lead-duplicated"),
        pytest.param(_sub("hrv.trace.txt", "| Rule HRV-09 holds every day. |", "| Rule HRV-09 holds 40 days. |"), "40", id="proxy-on-a-no-row"),
        pytest.param(_put("glossary.txt", "- **T-01 again** IS twice.\n", append=True), "glossary", id="glossary-duplicate"),
        pytest.param(_put("doc-goal.rules.txt", "\n" + _block("DOC-16") + "\n", append=True), "not named", id="unmapped-rule"),
        pytest.param(_put("hrv.meanings.txt", _C05_LINE.replace("worse rate reopens", "worse rate") + "\n"), "duplicate", id="duplicate-key-across-groups"),
        pytest.param(_put("doc-goal.meanings.txt", json.dumps(dict(_WORLD_C05, key="k2", pattern="synthetic definition", example="the synthetic definition")) + "\n"), "research/00", id="r4-negative-over-the-glossary"),
        pytest.param(_sub("arch-dec.rules.txt", "test_the_ac23_flip", "test_no_ac23_flip"), "def", id="pinned-node-missing"),
        pytest.param(_sub("doc-goal.rules.txt", "### 1.1 The supreme objective", "### 1.1 A heading of our own"), "heading", id="unknown-heading"),
    ],
)
def test_assemble_check_turns_red_on_each_cross_group_finding(tmp_path, mutate, fragment: str) -> None:
    files = _world_files()
    mutate(files)
    errors = assemble_check(_write(tmp_path, files))
    print(f"[slice compared] {errors[:8]}")
    assert any(fragment in e for e in errors), errors


def test_fragment_errors_is_green_on_each_world_group(tmp_path) -> None:
    drafts, inventory = _write(tmp_path, _world_files()), _inventory(tmp_path)
    for group in GROUPS:
        errors = fragment_errors(drafts, group, inventory, show=_old_show)
        print(f"[slice compared] {group}: {errors[:5]}")
        assert errors == []


def _drop_file(name: str):
    def mutate(files: dict[str, str]) -> None:
        del files[name]
    return mutate


def _drop_first_row(files: dict[str, str]) -> None:
    files["hrv.trace.txt"] = files["hrv.trace.txt"].split("\n", 1)[1]


@pytest.mark.parametrize(
    ("group", "mutate", "fragment"),
    [
        pytest.param("hrv", _drop_first_row, "have no row: ['HRV-01']", id="missing-group-id"),
        pytest.param("hrv", _put("hrv.trace.txt", _row("HRV-02", "Rule HRV-02 holds every day.", "HRV-02") + "\n", append=True), "duplicate", id="duplicate-group-id"),
        pytest.param("hrv", _put("hrv.trace.txt", _row("DOC-01", "Rule DOC-01 holds every day.", "DOC-01") + "\n", append=True), "outside", id="row-from-another-group"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| §5.4 | HRV-09 |", "| §5.4 | HRV-48 |"), "HRV-48", id="in-group-id-unresolved"),
        pytest.param("hrv", _sub("hrv.trace.txt", "Rule HRV-09 holds every day.", "Rule HRV-09 holds each day."), "inventory sentence", id="sentence-not-verbatim"),
        pytest.param("hrv", _put("hrv.rules.txt", "\n" + _block("DOC-16") + "\n", append=True), "prefix", id="rule-outside-group-prefixes"),
        pytest.param("hrv", _sub("hrv.rules.txt", "could not have been selected", "was not selected"), "C01", id="operative-string-lost"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| Rule HRV-09 holds every day. |", "| Rule HRV-09 holds 40 days. |"), "40", id="proxy-no-row"),
        pytest.param("arch-dec", _put("arch-dec.meanings.txt", _C05_LINE.replace("worse rate reopens", "every day") + "\n"), "research/00", id="r4-negative-over-group"),
        pytest.param("arch-dec", _put("arch-dec.meanings.txt", _C05_LINE.replace("hysteresis decision\"", "hysteresis decision, invented\"") + "\n"), "4e47d0e", id="example-not-verbatim"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| C11 | no |", "| C11 | yes |"), "never authorizes", id="decision-column"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| C01, C02 | yes |", "| C01, C02 | no |"), "owns C01", id="group-coverage"),
        pytest.param("doc-goal", _sub("doc-goal.trace.txt", "| C24, C25 | yes | doc-goal-old |", "| C24, C25 | yes | — |"), "old-meaning key", id="yes-row-without-key"),
        pytest.param("hrv", _sub("hrv.rules.txt", "Rule HRV-02 MUST", "The band MUST"), "band", id="band"),
        pytest.param("hrv", _drop_file("hrv.meanings.txt"), "hrv.meanings.txt", id="missing-draft"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| no | — |", "| no | k9 |", 1), "no such old-meaning key", id="key-not-in-group-meanings"),
        pytest.param("hrv", _sub("hrv.rules.txt", "Rule HRV-02 MUST hold every day.", "Rule HRV-02 MUST hold. It does."), "one sentence", id="grammar"),
    ],
)
def test_fragment_errors_turns_red(tmp_path, group: str, mutate, fragment: str) -> None:
    files = _world_files()
    mutate(files)
    drafts, inventory = _write(tmp_path, files), _inventory(tmp_path)
    errors = fragment_errors(drafts, group, inventory, show=_old_show)
    print(f"[slice compared] {group}: {errors[:8]}")
    assert any(fragment in e for e in errors), errors


# ---------------------------------------------------------------------------
# Real-block sanity (T170 AC4): one hand-written rule block, trace row and old meaning per group,
# written from its inventory row. The fragment-level checks must report 0 on each: the proof that
# no checker is too strict for real text. Each example is verbatim research/00 at 4e47d0e; the
# git-show half of that check needs a full clone, so it runs in the probe, not here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealCase:
    group: str
    heading: str
    rules: str
    rows: tuple[str, ...]
    sentences: dict[str, str]
    meanings: dict[str, dict[str, str]] = field(default_factory=dict)


_GOAL_02 = ("The goal contract belongs to the athlete. The system may propose a change but never applies one, and a "
            "change takes effect only as an athlete-supplied input, exactly as at program start.")
_GATE_02 = 'The dataset-flip rate is measured the same way, and a worse rate "reopens the deferred hysteresis decision".'
_FIG_01 = ("After a coverage-gap re-establishment the athlete traverses 20 days beneath `min_baseline_readings` "
           "(`R+0 .. R+19`).")
_HRV_37 = "A dataset's band is also clipped, unreported, at an internal capture hole in its own baseline window."
_HRV_09 = ("Every count in the rule is in distinct local days, with `min_baseline_readings` = 14 and "
           "`min_window_readings` = 3.")

REAL_CASES = (
    RealCase(
        "doc-goal", "### 1.9 System ownership: plan versus goal",
        "\n".join([
            "**GOAL-02.** The goal contract IS the three athlete-owned fields `goal_pace_target`, `race_date` and "
            "`distance_m`, which the system MAY propose to change but MUST never change itself, so a change takes "
            "effect only as an athlete-supplied input, exactly as at program start.",
            "Scope: every goal-contract field, and every proposal the system makes about one.",
            "Not: the order in which the system proposes remedies, which GOAL-03 sets.",
            "Pinned: none",
            "Why: decision C22 bundles the three fields that two of the three sources already bundle.",
        ]),
        (_row("GOAL-02", _GOAL_02, "GOAL-02", "C22", "yes", "C22-goal-contract-two-fields", "§1.9"),),
        {"GOAL-02": _GOAL_02},
        {"C22-goal-contract-two-fields": {
            "pattern": r"goal contract -- the declared target pace and the race date",
            "example": "The *goal contract* — the declared target pace and the race date — is the athlete's.",
            "source": "specification/research/00-design-decisions.md:65@4e47d0e", "decision": "C22"}},
    ),
    RealCase(
        "arch-dec", "### 5.4 Reconciliations and amendments",
        "\n".join([
            "**GATE-02.** The system MUST NOT add hysteresis to dataset selection, and the worsened dataset-flip set "
            "MUST remain the 80 pinned `walk_flips` cells at `car_density = 2wk`.",
            "Scope: the dataset-flip rate, measured against shipped F005 on every sweep the same way as GATE-01.",
            "Not: a worsened cell outside that pinned set, which fails the gate.",
            "Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::"
            "test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned",
            "Why: the no-hysteresis decision is recorded at H-37 and is revisited only if the unequal-dispersion "
            "measurement of IDEA-089 (b) shows harm.",
            "",
            "**FIG-01.** After a coverage-gap re-establishment the athlete traverses 20 days beneath "
            "`min_baseline_readings` (`R+0 .. R+19`), and the spec MUST publish that figure.",
            "Scope: a coverage-gap re-establishment of the series (HRV-35), not a source-tier change.",
            "Not: the cost of a source-tier change, which FIG-02 states.",
            "Pinned: none",
        ]),
        (_row("GATE-02", _GATE_02, "GATE-02", "C05", "yes", "C05-worse-rate-reopens", "§5.4, AC23"),
         _row("FIG-01", _FIG_01, "FIG-01", ADDITION, "no", ADDITION, "§5.4 [T116/T126] (test-anchored)")),
        {"GATE-02": _GATE_02, "FIG-01": _FIG_01},
        {"C05-worse-rate-reopens": {
            "pattern": r"worse rate reopens",
            "example": "a worse rate reopens the deferred hysteresis decision",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C05"}},
    ),
    RealCase(
        "hrv", "### 5.4 Reconciliations and amendments",
        "\n".join([
            "**HRV-37.** A per-tier dataset's SWC band MUST also be clipped, unreported, at an internal capture hole "
            "in its own baseline window, meaning more than `gap_reset_days` (21) silent local days of its tier, so 21 "
            "does not clip and 22 does.",
            "Scope: every per-tier dataset, over the silent local days of its own tier inside its dataset baseline window.",
            "Not: a silence of the whole series, which the coverage gap of HRV-35 resets.",
            "Pinned: none",
            "",
            "**HRV-09.** Every count in the HRV rule MUST be in distinct local days, with `min_baseline_readings` = 14 "
            "and `min_window_readings` = 3.",
            "Scope: every count HRV-08 to HRV-46 take, in every per-tier dataset.",
            "Not: a count of captures, since a second capture on one local day adds no day.",
            "Pinned: none",
        ]),
        (_row("HRV-37", _HRV_37, "HRV-37", "C04", "yes", "C04-hole-at-least", "§5.4 (v), AC17"),
         _row("HRV-09", _HRV_09, "HRV-09", ADDITION, "no", ADDITION, "§5.4, (i)")),
        {"HRV-37": _HRV_37, "HRV-09": _HRV_09},
        {"C04-hole-at-least": {
            "pattern": r"capture hole of at least gapresetdays",
            "example": "an internal capture hole of at least `gap_reset_days`",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C04"}},
    ),
)


def _meanings_text(case: RealCase) -> str:
    return "\n".join(json.dumps(dict(v, key=k), ensure_ascii=False) for k, v in case.meanings.items())


@pytest.mark.parametrize("case", REAL_CASES, ids=lambda c: c.group)
def test_real_blocks_pass_every_fragment_level_check(case: RealCase) -> None:
    rules = f"{case.heading}\n\n{case.rules}\n"
    errors = fragment_text_errors(rules, "\n".join(case.rows), _meanings_text(case), case.group, case.sentences,
                                  coverage=False)
    errors += pinned_errors(rules, _REPO_ROOT)
    print(f"[slice compared] {case.group}: rules {rule_ids(rules)} rows {len(case.rows)} "
          f"keys {list(case.meanings)} -> {errors}")
    assert errors == []


def test_the_real_old_meaning_patterns_miss_their_new_rules() -> None:
    """R4's hard half: each pattern matches its old example and misses the correct new statement,
    e.g. C04's "at least" pattern against HRV-37's "more than"."""
    for case in REAL_CASES:
        for key, entry in load_meanings_jsonl(_meanings_text(case)).items():
            print(f"[slice compared] {key}: {entry.pattern!r} vs {normalize(entry.example)!r}")
            assert re.search(entry.pattern, normalize(entry.example)), key
            assert not re.search(entry.pattern, normalize(case.rules)), key


# ---------------------------------------------------------------------------
# The endpoint walk holds the literals module out (T170 AC6); read from its source, not imported
# ---------------------------------------------------------------------------


def test_the_endpoint_walk_declares_and_applies_the_literals_exclusion() -> None:
    source = _ENDPOINT_SUITE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    value = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SCAN_EXCLUDED_LITERALS" for t in node.targets)
    )
    print(f"[slice compared] SCAN_EXCLUDED_LITERALS = {value}")
    assert value[:2] == (0, "runcoach-api/tests/support/research00_old_meanings.py") and value[2]
    scanned = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_scanned_files")
    assert "SCAN_EXCLUDED_LITERALS" in ast.get_source_segment(source, scanned)
