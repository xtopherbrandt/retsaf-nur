"""Sweep a sprint's diff for its own task IDs in durable text (IDEA-122).

    uv run --package runcoach-api python runcoach-api/tests/support/sweep_sprint_task_ids.py \\
        --base SHA [--head SHA] --ids T247-T253 [--strict] [--repo PATH]

Durable text (source comments, docstrings, the CHANGELOG, spec, test docstrings) states the state as
built when the sprint ends. A sentence such as "Nothing populates the column yet (T249 does)" is true
when written and false once that later task merges, and nothing re-reads it. This script finds such
sentences mechanically.

**What it reads.** The added lines of ``git diff -w base..head``, so a whitespace-only change adds
nothing. A path with a space (which git pads with a TAB) or one git C-quotes is read as the path it
names. Each file's added lines are joined and their whitespace flattened, and the pattern
``(?<![A-Za-z0-9])[Tt]\\d{3}(?!\\d)`` is matched against the flattened text and kept only when the ID
is in ``--ids``. The match ignores case and treats ``_`` as a boundary, so ``(t249 does)``,
``test_..._until_t249``, ``T249_SERIAL`` and ``T249a`` all name T249; ``T2490``, ``UT249`` and
``1T249`` name no ID. Every hit is mapped back to ``file:line`` in ``head``.

**``--ids``.** A comma-separated list of IDs and ranges: ``T247-T253``, ``T247,T249`` or
``T247–T249, T253`` (an en dash works too; a lowercase ``t`` too). A range is expanded to every ID
between its ends. The sweep matches single IDs only: when the scanned text itself writes a range
such as "T247–T253", the sweep sees only its two endpoints, not the IDs between them.

**The commit that wrote the ID.** ``git blame -w --porcelain -L n,n head`` names the last commit
that changed the line other than in whitespace. If the hunk of that commit's ``git diff -w`` that
wrote the line also removed a line naming the same ID, the ID was already there: the sweep steps to
that removed line in the parent and blames again, until it reaches the commit whose hunk removed no
line naming the ID. That commit wrote the ID, and the hit is judged by it, so a later task that
rewords a hand-off naming itself does not exempt it. The step is hunk-sized: when that hunk removed
several lines naming the ID, the walk follows the first. When the commit that wrote the ID is not
the last one to change the line, the report adds ``last edited by <sha> <subject>``.

**Disposition.** The Conventional Commits scope of the commit that wrote the ID decides:

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
ID and the commit that wrote the ID, then a summary. Exit 1 when any hit is ``fail`` (or ``listed`` under
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

_TASK_ID = re.compile(r"(?<![A-Za-z0-9])[Tt](\d{3})(?!\d)")
_ID_TOKEN = re.compile(r"^[Tt](\d{3})$")
_SUBJECT = re.compile(r"^[A-Za-z]+(?:\((?P<scope>[^)]*)\))?!?:")
_SPRINT_SCOPE = re.compile(r"^sprint-\d{3}$", re.IGNORECASE)
_HUNK = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
#: Bound on the walk back to the commit that wrote an ID; a line rewritten more often is an error.
_MAX_STEPS = 100

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
    #: The commit that wrote the ID on this line, and its subject; the disposition follows its scope.
    commit: str
    subject: str
    disposition: str
    #: The last commit to change the line other than in whitespace, when that is a different commit.
    last_commit: str = ""
    last_subject: str = ""


class Hunk(NamedTuple):
    removed: list[tuple[int, str]]  # (line number in the old side, text)
    added: list[tuple[int, str]]  # (line number in the new side, text)


class _Blame(NamedTuple):
    sha: str
    subject: str
    line: int  # the line's number in ``sha``'s version of ``path``
    path: str
    previous: tuple[str, str] | None  # (parent sha, path in the parent), absent when ``sha`` created the file


def unquote_path(path: str) -> str:
    """A path as git writes it in a diff or blame header, C-quoted or not, as the path it names."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    body, out, i = path[1:-1], bytearray(), 0
    while i < len(body):
        if body[i] == "\\" and body[i + 1 : i + 2] in _C_ESCAPES:
            out.append(_C_ESCAPES[body[i + 1]])
            i += 2
        elif body[i] == "\\" and re.fullmatch(r"[0-7]{3}", body[i + 1 : i + 4]):
            out.append(int(body[i + 1 : i + 4], 8))
            i += 4
        else:
            out.extend(body[i].encode("utf-8"))
            i += 1
    return out.decode("utf-8", errors="replace")


def ids_in(text: str) -> list[str]:
    """Every task ID ``text`` names, in order, spelled ``T`` and three digits."""
    return [f"T{match.group(1)}" for match in _TASK_ID.finditer(text)]


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
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _header_path(target: str) -> str | None:
    """The path a ``+++ `` header names (``b/`` dropped), or None for ``/dev/null``.

    git pads a path holding a space with a trailing TAB and C-quotes a path holding a quote, a
    backslash or a control character; an unquoted path never holds a TAB, so the TAB ends it.
    """
    target = unquote_path(target.rstrip("\r").split("\t", 1)[0])
    return target[2:] if target.startswith("b/") else None


def parse_diff(diff: str) -> dict[str, list[Hunk]]:
    """``{path in the new side: [hunk, ...]}`` for a ``-U0`` diff; a deleted or binary file is absent."""
    files: dict[str, list[Hunk]] = {}
    path: str | None = None
    old_no = new_no = 0
    in_header = False  # between "diff --git" and the first hunk, where "+++ b/<path>" lives
    for raw in diff.split("\n"):
        if raw.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and raw.startswith("+++ "):
            path = _header_path(raw[4:])
            if path is not None:
                files.setdefault(path, [])
        elif raw.startswith("@@"):
            in_header = False
            match = _HUNK.match(raw)
            old_no, new_no = (int(match.group("old")), int(match.group("start"))) if match else (0, 0)
            if path is not None:
                files[path].append(Hunk([], []))
        elif in_header or path is None or not files[path]:
            continue
        elif raw.startswith("+"):
            files[path][-1].added.append((new_no, raw[1:].rstrip("\r")))
            new_no += 1
        elif raw.startswith("-"):
            files[path][-1].removed.append((old_no, raw[1:].rstrip("\r")))
            old_no += 1
    return files


def _diff(repo: Path, *args: str) -> dict[str, list[Hunk]]:
    return parse_diff(_git(repo, "diff", "-w", "--no-color", "--no-ext-diff", "-U0", *args))


def added_lines(repo: Path, base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    """``{path: [(line number in head, text), ...]}`` for every added line of ``git diff -w``."""
    return {
        path: [line for hunk in hunks for line in hunk.added]
        for path, hunks in _diff(repo, f"{base}..{head}").items()
    }


def _blame(repo: Path, rev: str, path: str, line: int) -> _Blame:
    """The last commit at or before ``rev`` that changed ``path:line`` other than in whitespace."""
    out = _git(repo, "blame", "-w", "--porcelain", "-L", f"{line},{line}", rev, "--", path)
    lines = out.split("\n")
    sha, orig_line = lines[0].split(" ")[:2]
    headers = {}
    for entry in lines[1:]:
        if entry.startswith("\t"):
            break
        key, _, value = entry.partition(" ")
        headers[key] = value
    previous = None
    if "previous" in headers:
        parent, _, parent_path = headers["previous"].partition(" ")
        previous = (parent, unquote_path(parent_path))
    return _Blame(
        sha, headers.get("summary", ""), int(orig_line), unquote_path(headers.get("filename", path)), previous
    )


def _hunk_writing(repo: Path, blame: _Blame) -> Hunk | None:
    """The hunk of ``blame.sha``'s ``git diff -w`` against its parent that wrote the blamed line."""
    if blame.previous is None:
        return None
    parent, parent_path = blame.previous
    paths = [parent_path] if parent_path == blame.path else [parent_path, blame.path]
    hunks = _diff(repo, "-M", parent, blame.sha, "--", *paths).get(blame.path, [])
    return next((hunk for hunk in hunks if any(no == blame.line for no, _ in hunk.added)), None)


def writer(repo: Path, head: str, path: str, line: int, task_id: str) -> tuple[_Blame, _Blame]:
    """``(commit that wrote task_id on path:line, last commit to change the line)`` in ``head``.

    Starts at the blamed commit and steps back while the hunk that wrote the line also removed a
    line naming ``task_id``: the ID was already there, so that commit only reworded it.
    """
    last = found = _blame(repo, head, path, line)
    for _ in range(_MAX_STEPS):
        hunk = _hunk_writing(repo, found)
        removed = [no for no, text in (hunk.removed if hunk else []) if task_id in ids_in(text)]
        if not removed:
            return found, last
        assert found.previous is not None  # a hunk with removed lines has a parent side
        found = _blame(repo, found.previous[0], found.previous[1], removed[0])
    raise RuntimeError(f"{path}:{line}: more than {_MAX_STEPS} commits reword {task_id}")


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
        task_id = f"T{match.group(1)}"
        if task_id not in ids:
            continue
        while index + 1 < len(starts) and starts[index + 1][0] <= match.start():
            index += 1
        key = (starts[index][1], task_id)
        if key not in seen:
            seen.add(key)
            hits.append(key)
    return hits


def sweep(repo: Path, base: str, head: str, ids: set[str], compared: list[str] | None = None) -> list[Hit]:
    """Every hit of ``ids`` in the added lines of ``base..head``, with its disposition."""
    hits: list[Hit] = []
    for path, lines in sorted(added_lines(repo, base, head).items()):
        if is_excluded(path):
            continue
        if compared is not None:
            compared.append(path)
        for line, task_id in find_ids(lines, ids):
            found, last = writer(repo, head, path, line, task_id)
            rest = ("", "") if last.sha == found.sha else (last.sha, last.subject)
            verdict = disposition(found.subject, task_id)
            hits.append(Hit(path, line, task_id, found.sha, found.subject, verdict, *rest))
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
        last = f", last edited by {hit.last_commit[:7]} {hit.last_subject}" if hit.last_commit else ""
        print(f"{hit.disposition:<6} {hit.path}:{hit.line} {hit.task_id} ({hit.commit[:7]} {hit.subject}){last}")
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
