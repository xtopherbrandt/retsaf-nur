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

**Who judges a hit.** A hit on line L of file F naming ID X is judged by its writers: every commit
in ``base..head`` that added or removed a line of F naming X, and the commit ``git blame -w`` names
for line L. The writers come from one ``git log -w -p --follow`` of F, so a rename keeps the
writers from before it, a whitespace-only change writes nothing, and a commit before ``base`` does
not count. The blamed commit adds a merge that wrote the line, since the log shows no merge diff.
The worst writer judges the hit (``fail``, then ``listed``, then ``exempt``), and the report names
the oldest of the worst writers, so a later task that rewords, joins, splits or moves a hand-off
naming itself does not exempt it. The rule fails closed: when another task's commit adds or
removes any line of F naming X, every hit for X in F fails, a self-tag included. That over-fail is
accepted; the fix is to rewrite the line or to justify it as ``listed``. When the blamed commit is
not the one reported, the report adds ``last edited by <sha> <subject>``.

**The range.** ``base`` must be an ancestor of ``head`` and a different commit, or the sweep exits
2: ``--base HEAD`` compares nothing, and a reversed range reads removed lines as added.

**The user's git config.** Every diff and log passes ``--src-prefix=a/ --dst-prefix=b/ --no-color
--no-ext-diff --inter-hunk-context=0``, the log also ``--no-show-signature``, every blame
``--no-ignore-revs-file``, and every call ``-c core.quotepath=off``, so ``diff.noprefix``,
``diff.dstPrefix``, ``diff.interHunkContext``, ``log.showSignature`` and ``blame.ignoreRevsFile``
change nothing; ``--follow`` detects a rename whatever ``diff.renames`` says. A ``+++`` header
without the ``b/`` prefix is an error (exit 2), never a file that is skipped.

**Disposition.** The Conventional Commits scope of a writer decides:

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
ID and the writer that judges it, then a summary. Exit 1 when any hit is ``fail`` (or ``listed``
under ``--strict``), 2 on a usage, range or git error, 0 otherwise. The sprint's last-wave release
gate runs it with ``--strict`` and the sprint's own ID range.

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
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
#: The order of badness used to judge a hit by the worst of its writers.
_SEVERITY = {"exempt": 0, "listed": 1, "fail": 2}
#: Options that fix the text of ``git diff`` and ``git log -p`` whatever the user's config says
#: (prefixes, colour, external drivers, hunks fused across unchanged lines).
_DIFF_OPTIONS = (
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--no-color",
    "--no-ext-diff",
    "--inter-hunk-context=0",
)

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
    #: The oldest of the hit's worst writers (each commit in the range that added or removed a line
    #: of the file naming the ID, and the commit blame names for the line), and its subject.
    commit: str
    subject: str
    disposition: str
    #: The last commit to change the line other than in whitespace, when that is a different commit.
    last_commit: str = ""
    last_subject: str = ""


class Hunk(NamedTuple):
    removed: list[str]
    added: list[tuple[int, str]]  # (line number in the new side, text)


class Commit(NamedTuple):
    sha: str
    subject: str


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
    """The disposition a writer's subject gives a hit naming ``task_id``."""
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


def check_range(repo: Path, base: str, head: str) -> None:
    """Raises unless ``base`` is an ancestor of ``head`` and a different commit."""
    base_sha = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    if base_sha == head_sha:
        raise RuntimeError(f"empty range: base {base} and head {head} are the same commit {base_sha[:7]}")
    # Exit 1 means "not an ancestor", which _git would report as a failed call.
    done = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base_sha, head_sha],
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode == 1:
        raise RuntimeError(f"not a range: base {base} is not an ancestor of head {head}")
    if done.returncode != 0:
        raise RuntimeError(f"git merge-base --is-ancestor failed: {done.stderr.strip()}")


def _header_path(target: str, prefix: str = "b/") -> str | None:
    """The path a ``+++ `` (or ``--- ``) header names, ``prefix`` dropped, or None for ``/dev/null``.

    git pads a path holding a space with a trailing TAB and C-quotes a path holding a quote, a
    backslash or a control character; an unquoted path never holds a TAB, so the TAB ends it. Any
    other header is an error, so a diff whose prefix is not ``b/`` cannot compare 0 files and pass.
    """
    target = unquote_path(target.rstrip("\r").split("\t", 1)[0])
    if target == "/dev/null":
        return None
    if not target.startswith(prefix):
        raise RuntimeError(f"diff header without the {prefix} prefix: {target}")
    return target[len(prefix) :]


def parse_diff(diff: str, deleted: bool = False) -> dict[str, list[Hunk]]:
    """``{path in the new side: [hunk, ...]}`` for a ``-U0`` diff; a binary file is absent.

    A deleted file is absent too, unless ``deleted`` is set: then it is keyed by its old path, so
    the lines it removes are read.
    """
    files: dict[str, list[Hunk]] = {}
    path: str | None = None
    old_path: str | None = None
    new_no = 0
    in_header = False  # between "diff --git" and the first hunk, where "--- a/" and "+++ b/" live
    for raw in diff.split("\n"):
        if raw.startswith("diff --git "):
            path, old_path, in_header = None, None, True
        elif in_header and raw.startswith("--- "):
            old_path = _header_path(raw[4:], "a/")
        elif in_header and raw.startswith("+++ "):
            path = _header_path(raw[4:])
            if path is None and deleted:
                path = old_path
            if path is not None:
                files.setdefault(path, [])
        elif raw.startswith("@@"):
            in_header = False
            match = _HUNK.match(raw)
            new_no = int(match.group("start")) if match else 0
            if path is not None:
                files[path].append(Hunk([], []))
        elif in_header or path is None or not files[path]:
            continue
        elif raw.startswith("+"):
            files[path][-1].added.append((new_no, raw[1:].rstrip("\r")))
            new_no += 1
        elif raw.startswith("-"):
            files[path][-1].removed.append(raw[1:].rstrip("\r"))
        elif raw.startswith(" "):  # a context line, which a fused hunk holds
            new_no += 1
    return files


def added_lines(repo: Path, base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    """``{path: [(line number in head, text), ...]}`` for every added line of ``git diff -w``."""
    diff = _git(repo, "diff", "-w", *_DIFF_OPTIONS, "-U0", f"{base}..{head}")
    return {path: [line for hunk in hunks for line in hunk.added] for path, hunks in parse_diff(diff).items()}


def file_writers(repo: Path, base: str, head: str, path: str) -> tuple[list[Commit], dict[str, set[int]]]:
    """``(commits, {id: indices})`` for ``path`` over ``base..head``, oldest commit first.

    ``commits`` is every commit ``git log -w --follow`` lists for the file; ``indices`` points into
    it at each commit whose diff of the file adds or removes a line naming the ID. ``--reverse`` is
    not passed, because it drops commits under ``--follow``; the list is reversed here instead.
    """
    out = _git(
        repo,
        "--literal-pathspecs",
        "log",
        "--no-show-signature",
        "--format=%x00%H%x00%s",
        "-w",
        "-p",
        "--follow",
        *_DIFF_OPTIONS,
        "-U0",
        f"{base}..{head}",
        "--",
        path,
    )
    chunks = out.split("\x00")[1:]
    commits: list[Commit] = []
    diffs: list[str] = []
    for sha, rest in zip(chunks[0::2], chunks[1::2]):
        subject, _, diff = rest.partition("\n")
        commits.append(Commit(sha, subject))
        diffs.append(diff)
    commits.reverse()
    diffs.reverse()
    writers: dict[str, set[int]] = {}
    for index, diff in enumerate(diffs):
        for hunks in parse_diff(diff, deleted=True).values():
            for hunk in hunks:
                for text in [*hunk.removed, *(added for _, added in hunk.added)]:
                    for task_id in ids_in(text):
                        writers.setdefault(task_id, set()).add(index)
    return commits, writers


def _blame(repo: Path, rev: str, path: str, line: int) -> Commit:
    """The last commit at or before ``rev`` that changed ``path:line`` other than in whitespace."""
    # --no-ignore-revs-file drops a configured blame.ignoreRevsFile, which would skip commits.
    out = _git(
        repo, "blame", "-w", "--porcelain", "--no-ignore-revs-file", "-L", f"{line},{line}", rev, "--", path
    )
    lines = out.split("\n")
    sha = lines[0].split(" ")[0]
    summary = next((entry[len("summary ") :] for entry in lines[1:] if entry.startswith("summary ")), "")
    return Commit(sha, summary)


def judge(commits: list[Commit], indices: set[int], blamed: Commit, task_id: str) -> Commit:
    """The oldest of the worst writers: the commits at ``indices``, and ``blamed``.

    A blamed commit the log does not list (a merge) counts as newer than every listed one.
    """
    order = {commit.sha: index for index, commit in enumerate(commits)}
    candidates = [commits[index] for index in indices]
    if blamed.sha not in {commit.sha for commit in candidates}:
        candidates.append(blamed)
    return max(
        candidates,
        key=lambda commit: (_SEVERITY[disposition(commit.subject, task_id)], -order.get(commit.sha, len(commits))),
    )


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
    check_range(repo, base, head)
    hits: list[Hit] = []
    for path, lines in sorted(added_lines(repo, base, head).items()):
        if is_excluded(path):
            continue
        if compared is not None:
            compared.append(path)
        found = find_ids(lines, ids)
        if not found:
            continue
        commits, writers = file_writers(repo, base, head, path)
        for line, task_id in found:
            last = _blame(repo, head, path, line)
            worst = judge(commits, writers.get(task_id, set()), last, task_id)
            rest = ("", "") if last.sha == worst.sha else (last.sha, last.subject)
            verdict = disposition(worst.subject, task_id)
            hits.append(Hit(path, line, task_id, worst.sha, worst.subject, verdict, *rest))
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
