"""The sprint task-ID sweep finds hand-offs to a later task in a sprint's durable text (IDEA-122).

``runcoach-api/tests/support/sweep_sprint_task_ids.py`` reads the added lines of a sprint's diff and
reports every mention of one of the sprint's own task IDs. Each hit is judged by the worst of its
writers: every commit in the sweep range that added or removed a line of the file naming the ID,
and the commit blame names for the line. A writer's scope gives ``exempt`` (that same ID),
``listed`` (a ``sprint-NNN`` scope or none) or ``fail`` (any other scope), and the worst writer
judges the hit: ``fail``, then ``listed``, then ``exempt``. The release gate runs it ``--strict``.

Three kinds of check, each against real git rather than a mock:

- **The replay.** Sprint-010's range ``c2839b6..6d76bc8`` with the IDs T247-T253 holds the five
  hand-offs IDEA-122 was written about ("Nothing populates the column yet (T249 does)" and its
  siblings). The script must fail on that range, name the seven lines that carry them, and judge
  each of the range's 20 hits as pinned, so an over-fail shows. CI checks
  out with ``fetch-depth: 0``, so both commits are present; if they are not, this test fails rather
  than skips.
- **The planted checks.** A temporary clone of this repository gets commits built with plumbing:
  "until T249 lands" under three subjects, so each disposition is shown to follow the commit scope;
  every spelling of an ID and the spellings that are not one; paths git pads or quotes; a reindent
  (which neither hides nor relabels a hand-off); a later task rewording a hand-off that names it;
  rewords, joins, splits, moves, section reorders, second mentions, renames and merges, each
  judged by every writer of the ID in the file; the range bound and the ranges that are an error;
  histories that once made the walk fan out, timed; and git
  config settings that change the text of ``git diff``, ``git log`` or ``git blame``.
- **The ID parser and the scope rule**, as plain functions.

Every task ID in this module belongs to sprint-010, so the module never names an ID of the sprint
that added it, and the sweep run over that sprint does not flag its own test.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "runcoach-api" / "tests" / "support" / "sweep_sprint_task_ids.py"

REPLAY_BASE = "c2839b6"
REPLAY_HEAD = "6d76bc8"
REPLAY_IDS = "T247-T253"

#: The seven lines of the replay range that hand off to a later task under another task's scope.
REPLAY_FAIL_SITES = (
    "CHANGELOG.md:17",
    "runcoach-api/src/runcoach_api/db.py:264",
    "runcoach-api/src/runcoach_api/models.py:78",
    "runcoach-api/tests/test_db_schema.py:1026",
    "runcoach-api/tests/test_session_detail_keyset.py:24",
    "runcoach-api/tests/test_session_detail_keyset.py:27",
    "runcoach-api/tests/test_session_detail_keyset.py:150",
)

#: Introduced by a ``fix(sprint-010)`` commit: a backward reference, listed but not failed.
REPLAY_LISTED_SITE = "runcoach-api/tests/test_hrv_trend_endpoint.py:1707"

#: Every hit of the replay as (site, ID, disposition, the judging commit): 8 fail, 1 listed and
#: 11 exempt. The eighth fail, T253 on keyset:27, is a hand-off to T253 written under feat(T251).
#: An over-fail regression turns an exempt row here into a fail.
REPLAY_MAP = (
    ("CHANGELOG.md:5", "T248", "exempt", "2d2754f"),
    ("CHANGELOG.md:17", "T249", "fail", "2d2754f"),
    ("runcoach-api/src/runcoach_api/db.py:264", "T249", "fail", "2d2754f"),
    ("runcoach-api/src/runcoach_api/ingestion/mapping.py:220", "T249", "exempt", "8bf1d10"),
    ("runcoach-api/src/runcoach_api/models.py:78", "T249", "fail", "2d2754f"),
    ("runcoach-api/tests/test_db_schema.py:911", "T248", "exempt", "2d2754f"),
    ("runcoach-api/tests/test_db_schema.py:1026", "T249", "fail", "2d2754f"),
    ("runcoach-api/tests/test_hr_sensor_serial_unread.py:1", "T250", "exempt", "60941a4"),
    ("runcoach-api/tests/test_hrv_trend_endpoint.py:1707", "T248", "listed", "f7899d1"),
    ("runcoach-api/tests/test_mapping_sensor_identity.py:1", "T249", "exempt", "8bf1d10"),
    ("runcoach-api/tests/test_session_detail_keyset.py:1", "T251", "exempt", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:24", "T251", "exempt", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:24", "T249", "fail", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:25", "T251", "exempt", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:27", "T253", "fail", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:27", "T249", "fail", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:28", "T251", "exempt", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:150", "T249", "fail", "cd68a48"),
    ("runcoach-api/tests/test_session_detail_keyset.py:158", "T251", "exempt", "cd68a48"),
    ("runcoach-api/tests/test_session_id_corpus_golden.py:1", "T247", "exempt", "644027b"),
)


def _load_sweep():
    """Loads the sweep script by file path, so no ``sys.path`` change outlives this module."""
    spec = importlib.util.spec_from_file_location("sweep_sprint_task_ids", _SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


# --- the ID parser and the scope rule ---------------------------------------------------------


def test_ids_accept_ranges_lists_and_an_en_dash():
    sweep = _load_sweep()
    assert sweep.parse_ids("T247-T250") == {"T247", "T248", "T249", "T250"}
    assert sweep.parse_ids("T247,T249") == {"T247", "T249"}
    assert sweep.parse_ids("T247–T249, T253") == {"T247", "T248", "T249", "T253"}
    assert sweep.parse_ids("t247-t248") == {"T247", "T248"}, "a lowercase range names the same IDs"
    with pytest.raises(ValueError):
        sweep.parse_ids("T253-T247")
    with pytest.raises(ValueError):
        sweep.parse_ids("F014")


@pytest.mark.parametrize(
    ("subject", "task_id", "expected"),
    [
        ("feat(T249): resolve the serial", "T249", "exempt"),
        ("feat(t249): resolve the serial", "T249", "exempt"),
        ("feat(T248): add the column", "T249", "fail"),
        ("test(f014): pin the runs", "T249", "fail"),
        ("feat(idea-122): add the sweep", "T249", "fail"),
        ("refactor(sprint-010): fold a helper", "T249", "listed"),
        ("chore: tidy", "T249", "listed"),
        ("Merge branch 'x'", "T249", "listed"),
    ],
)
def test_the_commit_scope_decides_the_disposition(subject, task_id, expected):
    assert _load_sweep().disposition(subject, task_id) == expected


# --- the replay on sprint-010's real history --------------------------------------------------


def test_the_replay_fails_and_names_the_seven_hand_off_sites(capsys):
    sweep = _load_sweep()
    rc = sweep.main(["--base", REPLAY_BASE, "--head", REPLAY_HEAD, "--ids", REPLAY_IDS])
    out = capsys.readouterr().out
    print(out)
    assert rc != 0, "the replay range holds hand-offs under another task's scope; the sweep passed"

    hits = sweep.sweep(_REPO_ROOT, REPLAY_BASE, REPLAY_HEAD, sweep.parse_ids(REPLAY_IDS))
    by_site = {}
    for hit in hits:
        by_site.setdefault(f"{hit.path}:{hit.line}", set()).add(hit.disposition)
    for site in REPLAY_FAIL_SITES:
        # :24 and :27 also name the introducing task itself, which is a separate, exempt hit.
        assert "fail" in by_site.get(site, set()), f"{site}: expected fail, got {by_site.get(site)}"
        assert f"fail   {site} " in out, f"{site} is not printed as fail"
    assert by_site.get(REPLAY_LISTED_SITE) == {"listed"}, by_site.get(REPLAY_LISTED_SITE)
    assert "compared " in out and "CHANGELOG.md" in out, "the report does not print the compared files"


def test_the_replay_judges_every_hit_as_pinned():
    sweep = _load_sweep()
    hits = sweep.sweep(_REPO_ROOT, REPLAY_BASE, REPLAY_HEAD, sweep.parse_ids(REPLAY_IDS))
    got = tuple((f"{hit.path}:{hit.line}", hit.task_id, hit.disposition, hit.commit[:7]) for hit in hits)
    print("\n".join(map(str, got)))
    assert got == REPLAY_MAP
    assert [sum(row[2] == name for row in got) for name in ("fail", "listed", "exempt")] == [8, 1, 11]


def test_the_replay_skips_the_excluded_paths():
    sweep = _load_sweep()
    hits = sweep.sweep(_REPO_ROOT, REPLAY_BASE, REPLAY_HEAD, sweep.parse_ids(REPLAY_IDS))
    for hit in hits:
        assert not sweep.is_excluded(hit.path), hit
    assert sweep.is_excluded("runcoach-api/tests/data/research00-citation-sites/python.csv")
    assert sweep.is_excluded("runcoach-api/tests/data/research00_census.csv")
    assert sweep.is_excluded("spec-mirror/references/F006-dataset-model.md")
    assert not sweep.is_excluded("runcoach-api/tests/data/other.csv")


# --- planted checks in a temporary clone ------------------------------------------------------


@pytest.fixture(scope="module")
def clone(tmp_path_factory):
    """A clone of this repository at HEAD, without a checkout (the commits are built with plumbing)."""
    path = tmp_path_factory.mktemp("sweep-clone") / "repo"
    subprocess.run(
        ["git", "clone", "-q", "--no-checkout", "--no-hardlinks", str(_REPO_ROOT), str(path)],
        check=True,
        capture_output=True,
    )
    _git(path, "config", "user.name", "sweep test")
    _git(path, "config", "user.email", "sweep@example.invalid")
    # The commits are plumbing and never checked out, so a path NTFS refuses (a double quote) may be planted.
    _git(path, "config", "core.protectNTFS", "false")
    return path


#: Wrapped prose whose second line carries the hand-off "until T249 lands".
HAND_OFF = "The column stays empty\nuntil T249\nlands.\n"


def _commit(repo: Path, parent: str, files: dict[str, str], subject: str, remove: tuple[str, ...] = ()) -> str:
    """Commits ``files`` (path -> text) on top of ``parent``, less ``remove``, with plumbing; returns the sha."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo / ".git" / "index-plant")}
    subprocess.run(["git", "-C", str(repo), "read-tree", parent], env=env, check=True)
    for path in remove:
        subprocess.run(["git", "-C", str(repo), "update-index", "--force-remove", path], env=env, check=True)
    for path, text in files.items():
        blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=text.encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode().strip()
        subprocess.run(
            ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
            env=env,
            check=True,
        )
    tree = subprocess.run(
        ["git", "-C", str(repo), "write-tree"], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    return _git(repo, "commit-tree", tree, "-p", parent, "-m", subject)


def _plant(repo: Path, subject: str, name: str) -> str:
    """Commits ``planted/<name>.md`` holding :data:`HAND_OFF` on top of HEAD; returns its sha."""
    return _commit(repo, _git(repo, "rev-parse", "HEAD"), {f"planted/{name}.md": HAND_OFF}, subject)


def _run(sweep, repo: Path, head: str, *extra: str, base: str | None = None) -> int:
    base = base or f"{head}~1"
    return sweep.main(["--repo", str(repo), "--base", base, "--head", head, "--ids", REPLAY_IDS, *extra])


def test_a_feature_scope_is_not_a_self_tag(clone, capsys):
    sweep = _load_sweep()
    head = _plant(clone, "test(f014): plant a hand-off", "feature")
    rc = _run(sweep, clone, head)
    out = capsys.readouterr().out
    print(out)
    assert rc != 0
    assert "fail" in out and "planted/feature.md:2" in out


def test_a_sprint_scope_is_listed_and_strict_fails_it(clone, capsys):
    sweep = _load_sweep()
    head = _plant(clone, "chore(sprint-010): plant a hand-off", "sprint")
    assert _run(sweep, clone, head) == 0
    out = capsys.readouterr().out
    print(out)
    assert "listed" in out and "planted/sprint.md:2" in out
    assert _run(sweep, clone, head, "--strict") != 0


def test_the_self_tag_is_exempt(clone, capsys):
    sweep = _load_sweep()
    head = _plant(clone, "test(T249): plant a hand-off", "self")
    assert _run(sweep, clone, head) == 0
    assert _run(sweep, clone, head, "--strict") == 0
    out = capsys.readouterr().out
    print(out)
    assert "exempt" in out and "planted/self.md:2" in out


# --- every spelling of an ID, and the spellings that are not one ---------------------------------

#: This repository writes task IDs in test names and constants in lowercase and snake_case
#: (``..._since_t164``, ``T017_D``). Each shape below names T249 and must be a hit.
ID_SHAPES = (
    ("lowercase", "Nothing populates it yet (t249 does).\n"),
    ("snake-lowercase", "def test_vacuous_until_t249():\n    pass\n"),
    ("snake-uppercase", "def test_vacuous_until_T249():\n    pass\n"),
    ("constant", "T249_SERIAL = 1\n"),
    ("letter-suffix", "See T249a for the serial.\n"),
)

#: The negative class: a fourth digit, or a letter or digit before the T, makes it not an ID.
NOT_ID_SHAPES = (
    ("four-digits", "Row T2490 of the table.\n"),
    ("letter-before", "The UT249 code.\n"),
    ("digit-before", "Code 1T249 of the serial.\n"),
)


@pytest.mark.parametrize(("shape", "text"), ID_SHAPES, ids=[shape for shape, _ in ID_SHAPES])
def test_every_spelling_of_an_id_is_a_hit(clone, capsys, shape, text):
    sweep = _load_sweep()
    path = f"planted/shape-{shape}.py"
    head = _commit(clone, _git(clone, "rev-parse", "HEAD"), {path: text}, "feat(T248): plant a hand-off")
    rc = _run(sweep, clone, head, "--strict")
    out = capsys.readouterr().out
    print(out)
    assert rc == 1, f"{shape}: the sweep passed with T249 in {text!r}"
    assert f"fail   {path}:1 T249 " in out


@pytest.mark.parametrize(("shape", "text"), NOT_ID_SHAPES, ids=[shape for shape, _ in NOT_ID_SHAPES])
def test_a_longer_number_or_a_prefixed_t_is_not_an_id(clone, capsys, shape, text):
    sweep = _load_sweep()
    path = f"planted/not-id-{shape}.md"
    head = _commit(clone, _git(clone, "rev-parse", "HEAD"), {path: text}, "feat(T248): plant a non-id")
    rc = _run(sweep, clone, head, "--strict")
    out = capsys.readouterr().out
    print(out)
    assert f"compared {path}\n" in out
    assert rc == 0 and "1 files compared, 0 hits" in out, f"{shape}: {text!r} was read as an ID"


# --- paths git pads or quotes in the diff header -----------------------------------------------

#: git writes "+++ b/my notes.md<TAB>" for a path with a space and C-quotes a path holding a quote.
#: The script sets ``core.quotepath=off``, so a non-ASCII path is written unquoted.
ODD_PATHS = (
    ("space", "planted/my notes.md"),
    ("quote", 'planted/say "hi".md'),
    ("non-ascii", "planted/café.md"),
)


@pytest.mark.parametrize(("label", "path"), ODD_PATHS, ids=[label for label, _ in ODD_PATHS])
def test_a_path_git_pads_or_quotes_is_swept(clone, capsys, label, path):
    sweep = _load_sweep()
    head = _commit(clone, _git(clone, "rev-parse", "HEAD"), {path: HAND_OFF}, "test(f014): plant a hand-off")
    rc = _run(sweep, clone, head)
    out = capsys.readouterr().out
    print(out)
    assert rc == 1, f"{label}: expected a fail hit, got rc {rc}"
    assert f"compared {path}\n" in out
    assert f"fail   {path}:2 T249 " in out


def test_a_c_quoted_path_is_unquoted():
    sweep = _load_sweep()
    assert sweep.unquote_path('"b/caf\\303\\251.md"') == "b/café.md"
    assert sweep.unquote_path('"b/say \\"hi\\".md"') == 'b/say "hi".md'
    assert sweep.unquote_path('"b/tab\\there"') == "b/tab\there"
    assert sweep.unquote_path('"b/back\\\\slash.md"') == "b/back\\slash.md"
    assert sweep.unquote_path("b/my notes.md") == "b/my notes.md"


# --- whitespace-only changes ---------------------------------------------------------------------


def test_a_reindent_neither_hides_nor_relabels_a_hand_off(clone, capsys):
    """A reindent under a sprint scope is not the commit that wrote the hand-off, and adds no text."""
    sweep = _load_sweep()
    base = _git(clone, "rev-parse", "HEAD")
    path = "planted/reindent.md"
    wrote = _commit(clone, base, {path: HAND_OFF}, "test(f014): plant a hand-off")
    reindented = _commit(
        clone, wrote, {path: HAND_OFF.replace("until", "    until")}, "chore(sprint-010): reindent the prose"
    )

    # Both commits in range: the hit is judged by the commit that wrote it (blame -w).
    rc = _run(sweep, clone, reindented, base=base)
    out = capsys.readouterr().out
    print(out)
    assert rc == 1, "a reindent under chore(sprint-010) turned the test(f014) hand-off into a pass"
    assert f"fail   {path}:2 T249 ({wrote[:7]} test(f014)" in out

    # Only the reindent in range: a whitespace-only change adds no line to sweep (diff -w).
    rc = _run(sweep, clone, reindented, "--strict", base=wrote)
    out = capsys.readouterr().out
    print(out)
    assert rc == 0 and ", 0 hits" in out, "a whitespace-only change was swept as new text"


def test_a_hand_off_by_another_task_fails_the_reindented_self_tag_beside_it(clone):
    """Another task's line naming the ID in the file is a writer of every hit for that ID there.

    The self-tag on line 1 is only reindented, but the commit that does it also writes a hand-off
    naming the same ID, so line 1 fails with line 2: the accepted over-fail of the rule.
    """
    sweep = _load_sweep()
    base = _git(clone, "rev-parse", "HEAD")
    path = "planted/neighbour.md"
    noted = _commit(clone, base, {path: "See T249 for the serial.\n"}, "test(T249): note the serial")
    added = _commit(
        clone,
        noted,
        {path: "  See T249 for the serial.\nThe column stays empty until T249 lands.\n"},
        "feat(T248): add the column",
    )
    hits = sweep.sweep(clone, base, added, sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [(hit.line, hit.disposition, hit.commit, hit.last_commit) for hit in hits] == [
        (1, "fail", added, noted),
        (2, "fail", added, ""),
    ]


# --- a later task that rewords the hand-off -----------------------------------------------------

REWORDS = (
    # feat(T248) hands off to T249, then feat(T249) rewords the line and keeps the ID.
    ("hand-off", "None until T249 populates it.\n", "None until T249 fills it.\n", "fail"),
    # feat(T249) rewrites a line that named no ID into one naming itself.
    ("self-tag", "None yet.\n", "Filled by T249.\n", "exempt"),
)


@pytest.mark.parametrize(
    ("label", "before", "after", "expected"), REWORDS, ids=[label for label, *_ in REWORDS]
)
def test_a_hand_off_is_judged_at_the_commit_that_wrote_the_id(clone, capsys, label, before, after, expected):
    sweep = _load_sweep()
    base = _git(clone, "rev-parse", "HEAD")
    path = f"planted/reword-{label}.md"
    wrote = _commit(clone, base, {path: before}, "feat(T248): add the column")
    reworded = _commit(clone, wrote, {path: after}, "feat(T249): populate the column")
    hits = sweep.sweep(clone, base, reworded, sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [hit.disposition for hit in hits] == [expected], hits
    assert hits[0].commit == (wrote if expected == "fail" else reworded)
    rc = _run(sweep, clone, reworded, "--strict", base=base)
    out = capsys.readouterr().out
    print(out)
    assert rc == (1 if expected == "fail" else 0)
    if expected == "fail":
        assert f"last edited by {reworded[:7]} feat(T249)" in out, "the rewording commit is not reported"


# --- every writer of the ID in the file judges the hit -------------------------------------------

#: Each history is a list of (file text, subject) commits, oldest first. ``expected`` lists every hit
#: as (line in head, disposition, index of the commit that judges it). The commit that judges a hit
#: is the oldest of its worst writers.
HAND_OFF_WRITTEN = "None until T249 populates it.\n"
HAND_OFF_REWORDED = "None until T249 fills it.\n"
#: A long self-tag, so that a second mention appended to it leaves the line similar to the original.
SELF_TAG_LONG = "T249 adds the column to the sessions table and fills it at ingest"
HISTORIES = (
    # One hunk rewords a self-tag and the hand-off below it. The hand-off's writer also wrote a line
    # naming T249 in the file, so it judges the self-tag too: both lines fail.
    (
        "another-task-in-the-file-fails-the-self-tag",
        [
            ("Self note on T249.\n", "feat(T249): note the serial"),
            ("Self note on T249.\n" + HAND_OFF_WRITTEN, "feat(T248): add the column"),
            ("Self note about T249.\n" + HAND_OFF_REWORDED, "docs(f015): reword the notes"),
        ],
        [(1, "fail", 1), (2, "fail", 1)],
    ),
    # A self-tag replaced wholesale by a new sentence that hands off to the task.
    (
        "self-tag-replaced",
        [
            ("T249 adds the column.\n", "feat(T249): add the column"),
            ("The cache stays cold until T249 warms it.\n", "feat(T251): add the cache"),
        ],
        [(1, "fail", 1)],
    ),
    # Two lines merged into one: two commits fail it, and the oldest of them is reported.
    (
        "merged-lines",
        [
            ("T249 fills the column.\n", "feat(T249): fill the column"),
            ("T249 fills the column.\nT249 fills the cache.\n", "feat(T248): add the cache"),
            ("T249 fills the column and the cache.\n", "docs(f015): merge the notes"),
        ],
        [(1, "fail", 1)],
    ),
    # The same with the hand-off above the self-tag.
    (
        "merged-lines-reversed",
        [
            ("T249 fills the column.\n", "feat(T249): fill the column"),
            ("T249 fills the cache.\nT249 fills the column.\n", "feat(T248): add the cache"),
            ("T249 fills the cache and the column.\n", "docs(f015): merge the notes"),
        ],
        [(1, "fail", 1)],
    ),
    (
        "lowercase",
        [
            ("None until t249 populates it.\n", "feat(T248): add the column"),
            ("None until t249 fills it.\n", "feat(T249): populate the column"),
        ],
        [(1, "fail", 0)],
    ),
    # The removed line names another task: only the rewording commit wrote this ID.
    (
        "other-id-removed",
        [
            ("None until T248 populates it.\n", "chore(sprint-010): note the column"),
            (HAND_OFF_WRITTEN, "feat(T250): hand off the column"),
        ],
        [(1, "fail", 1)],
    ),
    # Lines inserted above after the reword: the line's number in head is not its number when written.
    (
        "shifted-after-the-reword",
        [
            (HAND_OFF_WRITTEN, "feat(T248): add the column"),
            (HAND_OFF_REWORDED, "feat(T249): populate the column"),
            ("x\ny\n" + HAND_OFF_REWORDED, "chore: add a preface"),
        ],
        [(3, "fail", 0)],
    ),
    (
        "shifted-before-the-reword",
        [
            ("a\nb\n" + HAND_OFF_WRITTEN, "feat(T248): add the column"),
            ("x\ny\na\nb\n" + HAND_OFF_WRITTEN, "chore: add a preface"),
            ("x\ny\na\nb\n" + HAND_OFF_REWORDED, "feat(T249): populate the column"),
        ],
        [(5, "fail", 0)],
    ),
    (
        "two-hunks",
        [
            ("one\ntwo\nthree\nfour\n" + HAND_OFF_WRITTEN, "feat(T248): add the column"),
            ("ONE\ntwo\nthree\nfour\n" + HAND_OFF_REWORDED, "feat(T249): populate the column"),
        ],
        [(5, "fail", 0)],
    ),
    (
        "insert-above-in-the-reword",
        [
            ("one\n" + HAND_OFF_WRITTEN, "feat(T248): add the column"),
            ("zero\none\n" + HAND_OFF_REWORDED, "feat(T249): populate the column"),
        ],
        [(3, "fail", 0)],
    ),
    # The sprint-012 review's attack rows: the task the hand-off names reshapes it, and it still fails.
    # A hand-off written over two lines, joined into one.
    (
        "join",
        [
            ("The column stays empty until\nT249 populates it.\n", "feat(T248): add the column"),
            ("The column stays empty until T249 fills it.\n", "feat(T249): populate the column"),
        ],
        [(1, "fail", 0)],
    ),
    # One long hand-off split over two lines; the ID lands on the short second line.
    (
        "split",
        [
            (
                "The column stays empty and the cache stays cold until T249 populates both.\n",
                "feat(T248): add the column",
            ),
            ("The column stays empty and the cache stays cold\nuntil T249 fills both.\n", "feat(T249): split the note"),
        ],
        [(2, "fail", 0)],
    ),
    # The hand-off moved below three unchanged lines: removed in one hunk, added in another.
    (
        "move-past-an-unchanged-line",
        [
            (HAND_OFF_WRITTEN + "one\ntwo\nthree\n", "feat(T248): add the column"),
            ("one\ntwo\nthree\n" + HAND_OFF_WRITTEN, "feat(T249): move the note"),
        ],
        [(4, "fail", 0)],
    ),
    # The hand-off moved past one line and reworded: the next hunk adds it.
    (
        "adjacent-hunk-move",
        [
            (HAND_OFF_WRITTEN + "one\n", "feat(T248): add the column"),
            ("one\n" + HAND_OFF_REWORDED, "feat(T249): move the note"),
        ],
        [(2, "fail", 0)],
    ),
    # The section holding the hand-off moved below a longer section.
    (
        "section-reorder",
        [
            ("## A\n" + HAND_OFF_WRITTEN + "\n## B\nb1\nb2\nb3\nb4\n", "feat(T248): add the column"),
            ("## B\nb1\nb2\nb3\nb4\n\n## A\n" + HAND_OFF_WRITTEN, "feat(T249): reorder the notes"),
        ],
        [(8, "fail", 0)],
    ),
    # Another task appends a second mention to a self-tag: one hit for the line, and it fails.
    (
        "second-mention",
        [
            (SELF_TAG_LONG + ".\n", "feat(T249): add the column"),
            (SELF_TAG_LONG + "; see T249.\n", "feat(T251): add the cache"),
        ],
        [(1, "fail", 1)],
    ),
    # The log call reads ``-w``: another task's reindent of a self-tag writes no line naming the ID.
    (
        "reindent-by-another-task-keeps-the-self-tag",
        [
            ("T249 adds the column.\n", "test(T249): add the column"),
            ("    T249 adds the column.\n", "feat(T248): indent the note"),
        ],
        [(1, "exempt", 0)],
    ),
    # The severity order, both ways round for each pair: listed beats exempt, fail beats both.
    (
        "severity-listed-then-exempt",
        [(HAND_OFF_WRITTEN, "chore(sprint-010): note the column"), (HAND_OFF_REWORDED, "feat(T249): fill it")],
        [(1, "listed", 0)],
    ),
    (
        "severity-exempt-then-listed",
        [(HAND_OFF_WRITTEN, "feat(T249): note the column"), (HAND_OFF_REWORDED, "chore(sprint-010): reword it")],
        [(1, "listed", 1)],
    ),
    (
        "severity-listed-then-fail",
        [(HAND_OFF_WRITTEN, "chore(sprint-010): note the column"), (HAND_OFF_REWORDED, "feat(T248): reword it")],
        [(1, "fail", 1)],
    ),
    (
        "severity-fail-then-listed",
        [(HAND_OFF_WRITTEN, "feat(T248): add the column"), (HAND_OFF_REWORDED, "chore(sprint-010): reword it")],
        [(1, "fail", 0)],
    ),
    (
        "severity-exempt-then-fail",
        [(HAND_OFF_WRITTEN, "feat(T249): note the column"), (HAND_OFF_REWORDED, "feat(T248): reword it")],
        [(1, "fail", 1)],
    ),
    (
        "severity-fail-then-exempt",
        [(HAND_OFF_WRITTEN, "feat(T248): add the column"), (HAND_OFF_REWORDED, "feat(T249): fill it")],
        [(1, "fail", 0)],
    ),
    # A commit that only removes a line naming the ID is a writer of it: the self-tag left behind fails.
    (
        "removal-only-writer",
        [
            ("T249 adds the column.\nT249 fills the cache.\n", "feat(T249): add the column"),
            ("T249 adds the column.\n", "feat(T248): drop the cache note"),
        ],
        [(1, "fail", 1)],
    ),
    # A step of ``None`` deletes the file. The deletion removes the line naming the ID, so the task that
    # deleted it is a writer, and the self-tag recreated after it fails.
    (
        "deleted-and-recreated",
        [
            ("T249 adds the column.\n", "feat(T249): add the column"),
            (None, "feat(T248): drop the notes"),
            ("T249 adds the column.\n", "feat(T249): restore the notes"),
        ],
        [(1, "fail", 1)],
    ),
)

#: A path git C-quotes in diff headers (it holds a double quote).
QUOTED_PATH = 'planted/history say "hi".md'


def _history(clone, steps, path):
    """Commits each ``(text, subject)`` step to ``path`` in turn; a ``None`` text deletes the file."""
    base = _git(clone, "rev-parse", "HEAD")
    shas, parent = [], base
    for text, subject in steps:
        if text is None:
            parent = _commit(clone, parent, {}, subject, remove=(path,))
        else:
            parent = _commit(clone, parent, {path: text}, subject)
        shas.append(parent)
    return base, shas


@pytest.mark.parametrize(("label", "steps", "expected"), HISTORIES, ids=[label for label, *_ in HISTORIES])
def test_every_writer_of_the_id_in_the_file_judges_the_hit(clone, capsys, label, steps, expected):
    sweep = _load_sweep()
    path = f"planted/history-{label}.md"
    base, shas = _history(clone, steps, path)
    hits = sweep.sweep(clone, base, shas[-1], sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [(hit.line, hit.disposition, hit.commit) for hit in hits] == [
        (line, verdict, shas[index]) for line, verdict, index in expected
    ]
    rc = _run(sweep, clone, shas[-1], "--strict", base=base)
    print(capsys.readouterr().out)
    assert rc == (1 if any(verdict != "exempt" for _, verdict, _ in expected) else 0)


def test_a_quoted_path_is_judged_by_its_writers(clone):
    sweep = _load_sweep()
    steps = [(HAND_OFF_WRITTEN, "feat(T248): add the column"), (HAND_OFF_REWORDED, "feat(T249): fill it")]
    base, shas = _history(clone, steps, QUOTED_PATH)
    hits = sweep.sweep(clone, base, shas[-1], sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [(hit.path, hit.line, hit.disposition, hit.commit) for hit in hits] == [(QUOTED_PATH, 1, "fail", shas[0])]


def test_a_writer_before_the_base_does_not_count(clone):
    """The range bound: a hand-off written before ``base`` does not judge a self-tag added after it."""
    sweep = _load_sweep()
    path = "planted/range-bound.md"
    before = _commit(clone, _git(clone, "rev-parse", "HEAD"), {path: HAND_OFF_WRITTEN}, "feat(T248): add the column")
    after = _commit(clone, before, {path: HAND_OFF_WRITTEN + "T249 fills it.\n"}, "feat(T249): fill the column")
    hits = sweep.sweep(clone, before, after, sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [(hit.line, hit.disposition, hit.commit) for hit in hits] == [(2, "exempt", after)]


#: Unchanged lines, so git pairs the renamed file with its original at well over 50 % similarity.
RENAME_BODY = "".join(f"Unchanged line {n} of the notes.\n" for n in range(1, 9))


@pytest.mark.parametrize("renames", [None, "false"], ids=["rename", "rename-under-diff-renames-false"])
def test_a_rename_keeps_the_writers_from_before_it(clone, capsys, renames):
    """The log call follows the file (``--follow``, no ``--reverse``) past the commit that renames it."""
    sweep = _load_sweep()
    label = renames or "default"
    old, new = f"planted/rename-{label}-old.md", f"planted/rename-{label}-new.md"
    base = _git(clone, "rev-parse", "HEAD")
    wrote = _commit(clone, base, {old: RENAME_BODY + HAND_OFF_WRITTEN}, "feat(T248): add the column")
    renamed = _commit(
        clone, wrote, {new: RENAME_BODY + HAND_OFF_REWORDED}, "feat(T249): rename the notes", remove=(old,)
    )
    if renames:
        _git(clone, "config", "diff.renames", renames)
    try:
        hits = sweep.sweep(clone, base, renamed, sweep.parse_ids(REPLAY_IDS))
        rc = _run(sweep, clone, renamed, "--strict", base=base)
    finally:
        if renames:
            _git(clone, "config", "--unset", "diff.renames")
    out = capsys.readouterr().out
    print(hits, out)
    assert [(hit.path, hit.line, hit.disposition, hit.commit, hit.last_commit) for hit in hits] == [
        (new, 9, "fail", wrote, renamed)
    ]
    assert rc == 1


# --- histories that once made the walk fan out ---------------------------------------------------


def _tie_steps(lines: int, commits: int) -> list[tuple[str, str]]:
    """``lines`` near-identical lines naming T249, each reworded by every one of ``commits`` commits."""
    subjects = ("feat(T249): fill the column", "chore(sprint-010): reword the notes")
    return [
        ("".join(f"T249 fills part {i} of the column, pass {k}.\n" for i in range(lines)), subjects[k % 2])
        for k in range(commits)
    ]


@pytest.mark.parametrize(
    ("lines", "commits"), [(2, 8), (4, 6)], ids=["tie-two-lines-eight-commits", "tie-four-lines-six-commits"]
)
def test_a_tie_history_finishes_in_under_two_seconds(clone, capsys, lines, commits):
    sweep = _load_sweep()
    path = f"planted/tie-{lines}-{commits}.md"
    base, shas = _history(clone, _tie_steps(lines, commits), path)
    started = time.perf_counter()
    rc = _run(sweep, clone, shas[-1], base=base)
    elapsed = time.perf_counter() - started
    out = capsys.readouterr().out
    print(out, f"elapsed {elapsed:.2f} s")
    assert rc == 0, f"exit {rc}"
    assert f"{lines} hits: 0 fail, {lines} listed, 0 exempt" in out
    assert elapsed < 2.0, f"{elapsed:.2f} s"


# --- a range that is not base..head of one line of history is an error ----------------------------

BAD_RANGES = ("base-is-head", "reversed-range", "base-is-the-sha-of-head")


@pytest.mark.parametrize("case", BAD_RANGES)
def test_a_bad_range_exits_2(clone, capsys, case):
    sweep = _load_sweep()
    base = _git(clone, "rev-parse", "HEAD")
    head = _plant(clone, "test(f014): plant a hand-off", f"range-{case}")
    argv = {
        "base-is-head": ["--base", "HEAD"],
        "reversed-range": ["--base", head, "--head", base],
        "base-is-the-sha-of-head": ["--base", base],
    }[case]
    rc = sweep.main(["--repo", str(clone), *argv, "--ids", REPLAY_IDS])
    captured = capsys.readouterr()
    print(captured.out, captured.err)
    assert rc == 2, f"{case}: exit {rc}"
    assert "sweep error:" in captured.err and "range" in captured.err


# --- the user's git config does not change what is read ------------------------------------------

#: Each setting changes the text of ``git diff``, ``git log`` or ``git blame``: no ``b/`` prefix,
#: another prefix, fused hunks, skipped commits, colour, an external driver (``true`` prints no diff
#: at all), a signature check printed into the log, or no rename detection.
GIT_CONFIGS = (
    ("diff.noprefix", "true"),
    ("diff.dstPrefix", "new/"),
    ("diff.mnemonicPrefix", "true"),
    ("diff.interHunkContext", "10"),
    ("blame.ignoreRevsFile", "no-such-file"),
    ("color.ui", "always"),
    ("diff.external", "true"),
    ("log.showSignature", "true"),
    ("diff.renames", "false"),
)


@pytest.mark.parametrize(("key", "value"), GIT_CONFIGS, ids=[key for key, _ in GIT_CONFIGS])
def test_the_users_git_config_does_not_change_the_sweep(clone, capsys, key, value):
    sweep = _load_sweep()
    path = f"planted/config-{key}.md"
    # Under a fused hunk, the unchanged "two" sits between the edited line 1 and the hand-off on line 3.
    steps = [
        ("one\ntwo\n" + HAND_OFF_WRITTEN, "feat(T248): add the column"),
        ("ONE\ntwo\n" + HAND_OFF_REWORDED, "feat(T249): populate the column"),
    ]
    base, shas = _history(clone, steps, path)
    _git(clone, "config", key, value)
    try:
        rc = _run(sweep, clone, shas[-1], "--strict", base=base)
    finally:
        _git(clone, "config", "--unset", key)
    out = capsys.readouterr().out
    print(out)
    assert f"compared {path}\n" in out, f"{key}={value}: the file was not compared"
    assert rc == 1 and f"fail   {path}:3 T249 ({shas[0][:7]} feat(T248)" in out


@pytest.mark.parametrize("value", [None, "first-parent", "separate"], ids=["merge", "log.diffMerges-first-parent", "log.diffMerges-separate"])
def test_a_merge_that_brings_a_self_tag_in_is_no_writer_of_it(clone, capsys, value):
    """The log shows no merge diff, so a merge is a writer only of a line blame names it for."""
    sweep = _load_sweep()
    label = value or "default"
    path, other = f"planted/merge-{label}.md", f"planted/merge-{label}-other.md"
    base = _git(clone, "rev-parse", "HEAD")
    side = _commit(clone, base, {path: "T249 adds the column.\n"}, "feat(T249): add the column")
    main = _commit(clone, base, {other: "Unrelated.\n"}, "chore(sprint-010): add a note")
    both = _commit(clone, side, {other: "Unrelated.\n"}, "scratch: the merged tree")
    tree = _git(clone, "rev-parse", f"{both}^{{tree}}")
    merge = _git(clone, "commit-tree", tree, "-p", main, "-p", side, "-m", "feat(T248): merge the column")
    if value:
        _git(clone, "config", "log.diffMerges", value)
    try:
        hits = sweep.sweep(clone, base, merge, sweep.parse_ids(REPLAY_IDS))
    finally:
        if value:
            _git(clone, "config", "--unset", "log.diffMerges")
    print(hits)
    assert [(hit.path, hit.line, hit.disposition, hit.commit) for hit in hits] == [(path, 1, "exempt", side)]


def test_a_merge_that_writes_a_hand_off_is_its_writer(clone, capsys):
    """The log lists no merge diff, so only blame names a merge as the writer of a line it wrote: the
    hand-off the merge adds fails, judged by the merge, beside the side branch's self-tag."""
    sweep = _load_sweep()
    path, other = "planted/merge-writes.md", "planted/merge-writes-other.md"
    base = _git(clone, "rev-parse", "HEAD")
    side = _commit(clone, base, {path: "T249 adds the column.\n"}, "feat(T249): add the column")
    main = _commit(clone, base, {other: "Unrelated.\n"}, "chore(sprint-010): add a note")
    merged = {other: "Unrelated.\n", path: "T249 adds the column.\n" + HAND_OFF_WRITTEN}
    both = _commit(clone, side, merged, "scratch: the merged tree")
    tree = _git(clone, "rev-parse", f"{both}^{{tree}}")
    merge = _git(clone, "commit-tree", tree, "-p", main, "-p", side, "-m", "feat(T248): merge the column")
    hits = sweep.sweep(clone, base, merge, sweep.parse_ids(REPLAY_IDS))
    print(hits)
    assert [(hit.path, hit.line, hit.disposition, hit.commit) for hit in hits] == [
        (path, 1, "exempt", side),
        (path, 2, "fail", merge),
    ]
    assert _run(sweep, clone, merge, base=base) == 1
    print(capsys.readouterr().out)


def test_a_diff_header_without_the_b_prefix_is_an_error():
    sweep = _load_sweep()
    diff = "diff --git notes.md notes.md\n--- notes.md\n+++ notes.md\n@@ -0,0 +1 @@\n+until T249 lands\n"
    with pytest.raises(RuntimeError):
        sweep.parse_diff(diff)


# --- the learnings rule that points at the sweep ------------------------------------------------


def test_the_learnings_rule_carries_the_rule_and_the_gate():
    path = _REPO_ROOT / ".claude" / "rules" / "learnings" / "no-later-task-ids-in-durable-text.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---") and "\npaths: [" in text, "no paths: frontmatter, so it is never loaded"
    assert "Origin: `spec/ideas/IDEA-122-no-future-task-ids-in-durable-prose.md`" in text
    flat = " ".join(text.split())
    for phrase in (
        "states the as-built state, and never names a later task of the same sprint",
        "Name the function or field instead",
        "last-wave gate runs the sweep script with the sprint's ID range",
        "the worst of every commit in the sweep range that added or removed a line of F naming X",
        "a commit before the sweep base does not count",
        "every hit for X in F fails, a self-tag included",
        "sweep_sprint_task_ids.py",
    ):
        assert phrase in flat, f"the rule is missing {phrase!r}"
    assert chr(0xA7) not in text, "the rule carries a section sign"
