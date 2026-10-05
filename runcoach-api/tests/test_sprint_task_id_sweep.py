"""The sprint task-ID sweep finds hand-offs to a later task in a sprint's durable text (IDEA-122).

``runcoach-api/tests/support/sweep_sprint_task_ids.py`` reads the added lines of a sprint's diff and
reports every mention of one of the sprint's own task IDs, with the disposition the commit that
introduced the line gives it: ``exempt`` (the commit's scope is that same ID), ``listed`` (a
``sprint-NNN`` scope or none) or ``fail`` (any other scope). The release gate runs it ``--strict``.

Three kinds of check, each against real git rather than a mock:

- **The replay.** Sprint-010's range ``c2839b6..6d76bc8`` with the IDs T247-T253 holds the five
  hand-offs IDEA-122 was written about ("Nothing populates the column yet (T249 does)" and its
  siblings). The script must fail on that range and name the seven lines that carry them. CI checks
  out with ``fetch-depth: 0``, so both commits are present; if they are not, this test fails rather
  than skips.
- **The planted checks.** A temporary clone of this repository gets one commit adding
  "until T249 lands" under three subjects, so each disposition is shown to follow the commit scope.
- **The ID parser and the scope rule**, as plain functions.

Every task ID in this module belongs to sprint-010, so the module never names an ID of the sprint
that added it, and the sweep run over that sprint does not flag its own test.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
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
    return path


def _plant(repo: Path, subject: str, name: str) -> str:
    """Commits one new file whose wrapped prose says "until T249 lands" on top of HEAD; returns its sha."""
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="The column stays empty\nuntil T249\nlands.\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    base = _git(repo, "rev-parse", "HEAD")
    index = repo / ".git" / f"index-{name}"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(["git", "-C", str(repo), "read-tree", base], env=env, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"100644,{blob},planted/{name}.md"],
        env=env,
        check=True,
    )
    tree = subprocess.run(
        ["git", "-C", str(repo), "write-tree"], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    return _git(repo, "commit-tree", tree, "-p", base, "-m", subject)


def _run(sweep, repo: Path, head: str, *extra: str) -> int:
    return sweep.main(["--repo", str(repo), "--base", f"{head}~1", "--head", head, "--ids", REPLAY_IDS, *extra])


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
        "sweep_sprint_task_ids.py",
    ):
        assert phrase in flat, f"the rule is missing {phrase!r}"
    assert chr(0xA7) not in text, "the rule carries a section sign"
