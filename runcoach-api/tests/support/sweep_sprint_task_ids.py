"""Sweep a sprint's diff for its own task IDs in durable text (IDEA-122).

    uv run --package runcoach-api python runcoach-api/tests/support/sweep_sprint_task_ids.py \\
        --base SHA [--head SHA] --ids T247-T253 [--strict] [--repo PATH]

Durable text (source comments, docstrings, the CHANGELOG, spec, test docstrings) states the state as
built when the sprint ends. A sentence such as "Nothing populates the column yet (T249 does)" is true
when written and false once that later task merges, and nothing re-reads it. This script finds such
sentences mechanically.

**What it reads.** The added lines of ``git diff -w base..head``. Each file's added lines are joined
and their whitespace flattened, and the pattern ``\\bT\\d{3}\\b`` is matched against the flattened
text and kept only when the ID is in ``--ids``. Every hit is mapped back to ``file:line`` in ``head``.

**``--ids``.** A comma-separated list of IDs and ranges: ``T247-T253``, ``T247,T249`` or
``T247–T249, T253`` (an en dash works too). A range is expanded to every ID between its ends.
The sweep matches single IDs only: when the scanned text itself writes a range such as
"T247–T253", the sweep sees only its two endpoints, not the IDs between them.

**Disposition.** ``git blame -w --porcelain -L n,n head`` names the commit that introduced the line,
and that commit's Conventional Commits scope decides:

- ``exempt``: the scope is the hit's own ID (``feat(T249): ...`` naming T249). A task may name
  itself; this is the user's ruling.
- ``listed``: the scope is ``sprint-NNN``, or the subject has no scope. A sprint-level commit naming
  an earlier task is a backward reference, so it is printed but does not fail unless ``--strict``.
- ``fail``: any other scope. ``feat(T248)`` naming T249, ``test(f014)`` or ``feat(idea-122)``: a
  feature or IDEA scope is not a self-tag.

**Excluded paths.** ``runcoach-api/tests/data/research00-citation-sites/*.csv``,
``runcoach-api/tests/data/research00_census.csv`` and everything under ``spec-mirror/``: these are
records and a mirror, not prose a builder writes.

**Output and exit.** Every compared file (``compared <path>``), then every hit with its disposition,
ID and introducing commit, then a summary. Exit 1 when any hit is ``fail`` (or ``listed`` under
``--strict``), 2 on a usage or git error, 0 otherwise. The sprint's last-wave release gate runs it
with ``--strict`` and the sprint's own ID range.

Git is called natively through ``subprocess.run(["git", ...])``; never through a shell.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[3]

_TASK_ID = re.compile(r"\bT\d{3}\b")
_ID_TOKEN = re.compile(r"^T(\d{3})$")
_SUBJECT = re.compile(r"^[A-Za-z]+(?:\((?P<scope>[^)]*)\))?!?:")
_SPRINT_SCOPE = re.compile(r"^sprint-\d{3}$", re.IGNORECASE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")

#: Paths never swept: the citation-site records, the research/00 census and the spec mirror.
EXCLUDED = (
    "runcoach-api/tests/data/research00-citation-sites/*.csv",
    "runcoach-api/tests/data/research00_census.csv",
    "spec-mirror/*",
)


class Hit(NamedTuple):
    path: str
    line: int
    task_id: str
    commit: str
    subject: str
    disposition: str


def parse_ids(spec: str) -> set[str]:
    """``"T247-T253"``, ``"T247,T249"`` or a mix of both (en dash accepted) -> the set of IDs."""
    ids: set[str] = set()
    for part in spec.replace("–", "-").split(","):
        part = part.strip()
        if not part:
            continue
        ends = [end.strip() for end in part.split("-")]
        numbers = []
        for end in ends:
            match = _ID_TOKEN.match(end)
            if not match:
                raise ValueError(f"not a task ID: {end!r} in {spec!r}")
            numbers.append(int(match.group(1)))
        if len(numbers) == 1:
            ids.add(f"T{numbers[0]:03d}")
        elif len(numbers) == 2 and numbers[0] <= numbers[1]:
            ids.update(f"T{n:03d}" for n in range(numbers[0], numbers[1] + 1))
        else:
            raise ValueError(f"not an ascending range: {part!r}")
    if not ids:
        raise ValueError(f"no task IDs in {spec!r}")
    return ids


def disposition(subject: str, task_id: str) -> str:
    """The disposition the introducing commit's subject gives a hit naming ``task_id``."""
    match = _SUBJECT.match(subject)
    scope = (match.group("scope") or "").strip() if match else ""
    if not scope or _SPRINT_SCOPE.match(scope):
        return "listed"
    if scope.upper() == task_id.upper():
        return "exempt"
    return "fail"


def is_excluded(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in EXCLUDED)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def added_lines(repo: Path, base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    """``{path: [(line number in head, text), ...]}`` for every added line of ``git diff -w``."""
    diff = _git(repo, "diff", "-w", "--no-color", "--no-ext-diff", "-U0", f"{base}..{head}")
    files: dict[str, list[tuple[int, str]]] = {}
    path: str | None = None
    line_no = 0
    in_header = False  # between "diff --git" and the first hunk, where "+++ b/<path>" lives
    for raw in diff.split("\n"):
        if raw.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and raw.startswith("+++ "):
            target = raw[4:].rstrip("\r")
            path = target[2:] if target.startswith("b/") else None
            if path is not None:
                files.setdefault(path, [])
        elif raw.startswith("@@"):
            in_header = False
            match = _HUNK.match(raw)
            line_no = int(match.group("start")) if match else 0
        elif raw.startswith("+") and path is not None:
            files[path].append((line_no, raw[1:].rstrip("\r")))
            line_no += 1
    return files


def _blame(repo: Path, head: str, path: str, line: int) -> tuple[str, str]:
    """``(commit sha, subject)`` of the commit that introduced ``path:line`` in ``head``."""
    out = _git(repo, "blame", "-w", "--porcelain", "-L", f"{line},{line}", head, "--", path)
    lines = out.split("\n")
    sha = lines[0].split(" ", 1)[0]
    subject = next((entry[len("summary ") :] for entry in lines if entry.startswith("summary ")), "")
    return sha, subject


def find_ids(lines: list[tuple[int, str]], ids: set[str]) -> list[tuple[int, str]]:
    """Joins the added lines, flattens whitespace and returns ``(line, id)`` for each hit, in order."""
    flat_parts: list[str] = []
    starts: list[tuple[int, int]] = []
    offset = 0
    for number, text in lines:
        flat = " ".join(text.split())
        starts.append((offset, number))
        flat_parts.append(flat)
        offset += len(flat) + 1
    joined = " ".join(flat_parts)
    hits: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    index = 0
    for match in _TASK_ID.finditer(joined):
        if match.group(0) not in ids:
            continue
        while index + 1 < len(starts) and starts[index + 1][0] <= match.start():
            index += 1
        key = (starts[index][1], match.group(0))
        if key not in seen:
            seen.add(key)
            hits.append(key)
    return hits


def sweep(repo: Path, base: str, head: str, ids: set[str], compared: list[str] | None = None) -> list[Hit]:
    """Every hit of ``ids`` in the added lines of ``base..head``, with its disposition."""
    hits: list[Hit] = []
    blames: dict[str, tuple[str, str]] = {}
    for path, lines in sorted(added_lines(repo, base, head).items()):
        if is_excluded(path):
            continue
        if compared is not None:
            compared.append(path)
        for line, task_id in find_ids(lines, ids):
            key = f"{path}:{line}"
            if key not in blames:
                blames[key] = _blame(repo, head, path, line)
            sha, subject = blames[key]
            hits.append(Hit(path, line, task_id, sha, subject, disposition(subject, task_id)))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--ids", required=True)
    parser.add_argument("--strict", action="store_true", help="fail listed hits too")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        ids = parse_ids(args.ids)
        compared: list[str] = []
        hits = sweep(args.repo, args.base, args.head, ids, compared)
    except (ValueError, RuntimeError) as error:
        print(f"sweep error: {error}", file=sys.stderr)
        return 2

    print(f"sweep {args.base}..{args.head} for {len(ids)} task IDs ({args.ids}), strict={args.strict}")
    for path in compared:
        print(f"compared {path}")
    for hit in hits:
        print(f"{hit.disposition:<6} {hit.path}:{hit.line} {hit.task_id} ({hit.commit[:7]} {hit.subject})")
    counts = {name: sum(hit.disposition == name for hit in hits) for name in ("fail", "listed", "exempt")}
    failing = counts["fail"] + (counts["listed"] if args.strict else 0)
    print(
        f"{len(compared)} files compared, {len(hits)} hits: "
        f"{counts['fail']} fail, {counts['listed']} listed, {counts['exempt']} exempt"
    )
    print("FAIL" if failing else "PASS")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
