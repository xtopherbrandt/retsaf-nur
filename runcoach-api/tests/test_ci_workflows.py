"""T131 — the installed CI workflows, and the claims made about them.

The state this file exists to make un-repeatable: `contracts/contract-drift.yml`
was a valid, tracked GitHub Actions workflow sitting at a path Actions never
looks at, its own `paths:` filter named
`.github/workflows/contract-drift.yml` — a file that did not exist — and
`contracts/contracts-README.md` asserted *"Wired in
`.github/workflows/contract-drift.yml`"* as a live statement of fact. Three
documents agreeing with each other about a file none of them had checked was
there (IDEA-067).

**Every check here derives, none enumerates.** T119's lesson is that an
allowlist passes the moment the sentence moves file: a test that says "the
README's line 60 must name an existing workflow" goes green when that claim is
cut, or moved to another paragraph, or copied into a fourth document where
nobody is watching. So the rule asserted below is the general one —

    every ``.github/...`` path named anywhere in this tree must exist

— derived by walking the tree and reading what it says, which holds for the
sentence wherever it lives and for the sentence nobody has written yet. The
same shape applies to the workflows themselves: they are discovered by listing
`.github/workflows/`, never by name.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

#: What a GitHub Actions workflow file is allowed to be called.
_WORKFLOW_SUFFIXES = (".yml", ".yaml")

#: Text files whose prose (or YAML, or code comments) can make a claim about a
#: path under ``.github/``. The corpus phrasing scan's three suffixes
#: (``.py``, ``.md``, ``.yaml``) plus ``.yml`` and ``.toml``, because a
#: workflow's own ``paths:`` filter and a packaging file are both places a
#: ``.github/`` path gets named.
_TEXT_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".toml")

#: Machinery and copies, matched on a **directory name** at any depth so this
#: cannot grow into a list of individual files.
_EXCLUDED_DIR_NAMES = (
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    "node_modules",
    ".shipyard",  # machine-local breadcrumb; absent in every checkout and in CI
    "worktrees",  # .claude/worktrees: a second copy of the tree, every hit a duplicate
)

#: Detached mutation-testing worktrees (``.mut-T108`` and friends).
_EXCLUDED_DIR_PREFIX = ".mut-"

#: A repo-relative path under ``.github/``. The character class stops at a
#: backtick, quote, space, comma or closing bracket, so a mention inside prose
#: or inside a YAML list item is captured and its punctuation is not.
_GITHUB_PATH = re.compile(r"\.github/[A-Za-z0-9._/-]+")

#: Trailing sentence punctuation the character class above does admit.
_TRAILING_PUNCTUATION = ".,;:"


def _walked_files() -> tuple[Path, ...]:
    """Every text file in the committed tree, by walking it."""
    found: list[Path] = []
    stack = [_REPO_ROOT]
    while stack:
        directory = stack.pop()
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if entry.name in _EXCLUDED_DIR_NAMES:
                    continue
                if entry.name.startswith(_EXCLUDED_DIR_PREFIX):
                    continue
                stack.append(entry)
            elif entry.suffix in _TEXT_SUFFIXES:
                found.append(entry)
    return tuple(found)


def _installed_workflows() -> tuple[Path, ...]:
    """The workflows Actions will actually run — listed, never named."""
    if not _WORKFLOWS_DIR.is_dir():
        return ()
    return tuple(
        sorted(
            path
            for path in _WORKFLOWS_DIR.iterdir()
            if path.is_file() and path.suffix in _WORKFLOW_SUFFIXES
        )
    )


def _load(workflow: Path) -> dict:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))


def _load_or_none(workflow: Path) -> dict | None:
    """Used by the module-level derivations below. An unparseable workflow must
    surface as a named failure from ``test_every_installed_workflow_parses...``,
    not as a collection error that takes the whole module down with it and
    reports the defect once instead of where it belongs."""
    try:
        document = _load(workflow)
    except (yaml.YAMLError, UnicodeDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _triggers(document: dict) -> dict:
    """``on:`` is YAML 1.1's boolean ``True`` once ``safe_load`` is done with
    it, which is the single most common way a workflow test reads a workflow
    it has not actually parsed."""
    for key in (True, "on", "on:"):
        if key in document:
            return document[key]
    raise KeyError("on")


def _steps(document: dict) -> list[tuple[str, dict]]:
    """``(job name, step)`` for every step of every job."""
    return [
        (job_name, step)
        for job_name, job in (document.get("jobs") or {}).items()
        for step in (job.get("steps") or [])
    ]


def _run_commands(document: dict) -> list[str]:
    return [step["run"] for _job, step in _steps(document) if isinstance(step.get("run"), str)]


_WORKFLOWS = _installed_workflows()
_WORKFLOW_IDS = [path.name for path in _WORKFLOWS]


# ------------------------------------------------------- the workflows exist
def test_ci_is_installed_somewhere_actions_will_look() -> None:
    """The non-vacuity guard for every parametrised case below: with an empty
    ``.github/workflows/`` they would all pass by having nothing to run, which
    is the precise state this task was opened to end."""
    assert _WORKFLOWS_DIR.is_dir(), (
        f"{_WORKFLOWS_DIR} does not exist: GitHub Actions runs nothing, and every "
        "check in this repo is again 'whatever the author's laptop happened to run'"
    )
    assert _WORKFLOWS, (
        f"{_WORKFLOWS_DIR} holds no *.yml/*.yaml file: the directory exists but "
        "Actions still has nothing to run"
    )


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=_WORKFLOW_IDS)
def test_every_installed_workflow_parses_and_declares_work(workflow: Path) -> None:
    """A workflow Actions cannot parse is reported in the Actions tab and
    nowhere a commit can see it, so it fails the same way an uninstalled one
    does: quietly."""
    try:
        document = _load(workflow)
    except yaml.YAMLError as exc:
        pytest.fail(f"{workflow} is not parseable YAML, so Actions will skip it: {exc}")
    assert isinstance(document, dict), f"{workflow} is not a YAML mapping"
    assert document.get("name"), f"{workflow} declares no name"

    try:
        triggers = _triggers(document)
    except KeyError:
        pytest.fail(f"{workflow} declares no `on:` triggers, so nothing will ever start it")
    assert triggers, f"{workflow} has an empty `on:`, so nothing will ever start it"

    jobs = document.get("jobs")
    assert jobs, f"{workflow} declares no jobs"
    for job_name, job in jobs.items():
        assert job.get("runs-on"), f"{workflow}: job {job_name} declares no runner"
        assert job.get("steps"), f"{workflow}: job {job_name} has no steps"


# ------------------------------------- every path anyone names actually exists
def _claimed_github_paths() -> list[tuple[Path, int, str]]:
    """``(citing file, line number, claimed path)`` for every ``.github/``
    path this tree mentions — in a ``paths:`` filter, in a ``run:`` command,
    in a comment, or in prose. Derived by reading the tree, so a claim that
    moves to another file, or a claim written tomorrow, is covered without
    anything here changing."""
    claims: list[tuple[Path, int, str]] = []
    for path in _walked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - the corpus is utf-8
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _GITHUB_PATH.finditer(line):
                claimed = match.group(0).rstrip(_TRAILING_PUNCTUATION)
                claims.append((path, lineno, claimed))
    return claims


_CLAIMS = _claimed_github_paths()


def test_the_tree_does_claim_something_about_github_paths() -> None:
    """The non-vacuity guard for the case below. Zero claims would make it
    green over an empty read — the shape the corpus scan's floors exist to
    catch, one level out."""
    assert _CLAIMS, (
        "no file in this tree names a path under .github/: either the walk has "
        "stopped descending, or the workflows and the documents that cite them "
        "have both been removed"
    )


@pytest.mark.parametrize(
    ("citing_file", "lineno", "claimed"),
    _CLAIMS,
    ids=[f"{path.name}:{lineno}:{claimed}" for path, lineno, claimed in _CLAIMS],
)
def test_every_github_path_this_tree_names_exists(
    citing_file: Path, lineno: int, claimed: str
) -> None:
    """The general form of the defect. `contracts-README.md` asserted a wiring
    into a file that did not exist, and the workflow's own `paths:` filter
    named the same absent file — both went unnoticed because nothing compared
    a named path against the filesystem."""
    target = _REPO_ROOT / claimed.rstrip("/")
    assert target.exists(), (
        f"{citing_file.relative_to(_REPO_ROOT)}:{lineno} names `{claimed}`, "
        f"which does not exist at {target}. Either install the file or correct "
        "the claim — a path filter pointed at nothing never matches, and a "
        "sentence pointed at nothing is simply false."
    )


# --------------------------------- no workflow left lying outside the run path
def _looks_like_a_workflow(path: Path) -> bool:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return False
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return False
    try:
        _triggers(document)
    except KeyError:
        return False
    return any(isinstance(job, dict) and "runs-on" in job for job in document["jobs"].values())


def test_no_workflow_shaped_file_sits_where_actions_will_never_look() -> None:
    """How this started: a real workflow parked under `contracts/`, indexed by
    git, cited by the README, and run by nobody. Derived from the *shape* of
    the file (triggers + jobs + a runner), so any future misfiling is caught
    whatever it is called and wherever it is put."""
    misfiled = [
        path.relative_to(_REPO_ROOT)
        for path in _walked_files()
        if path.suffix in _WORKFLOW_SUFFIXES
        and path.parent != _WORKFLOWS_DIR
        and _looks_like_a_workflow(path)
    ]
    assert not misfiled, (
        f"workflow-shaped files outside .github/workflows/: {misfiled}. Actions "
        "reads only .github/workflows/, so one of these is a check that looks "
        "installed and has never run."
    )


# ------------------------------------- the suite workflow cannot pass on half
def _suite_workflows() -> list[Path]:
    suites = []
    for workflow in _WORKFLOWS:
        document = _load_or_none(workflow)
        if document is None:
            # Reported by test_every_installed_workflow_parses_and_declares_work.
            continue
        if any("pytest" in command for command in _run_commands(document)):
            suites.append(workflow)
    return suites


def test_some_installed_workflow_runs_the_test_suite() -> None:
    """Deliverable 2 stated as an invariant rather than as a filename: the
    corpus scan's coverage is only reproducible if the suite runs off this
    laptop at all."""
    assert _suite_workflows(), (
        "no installed workflow invokes pytest: the 1,300-case suite, the corpus "
        "phrasing scan and its per-root floors still run on one machine only"
    )


@pytest.mark.parametrize("workflow", _suite_workflows(), ids=lambda path: path.name)
def test_a_suite_workflow_cannot_report_green_over_a_partial_collection(
    workflow: Path,
) -> None:
    """Two silent-pass shapes, both specific to this workspace and neither
    visible in pytest's exit code:

    * the workspace-root `conftest.py` drops a member from collection when its
      deps are missing, so a half-synced environment exits 0; and
    * `uv run --package <member>` re-syncs the shared venv *down to* that one
      member, manufacturing exactly that half-synced environment.

    So a workflow that runs the suite has to sync the whole workspace and then
    measure what it collected. The measurement is required by *structure* — a
    JUnit report is emitted and a later step consumes that same file — not by
    naming the guard script, which would go green the moment the script were
    renamed to something that does nothing."""
    document = _load(workflow)
    commands = _run_commands(document)
    joined = "\n".join(commands)

    assert "uv sync --all-packages" in joined, (
        f"{workflow.name} runs pytest without `uv sync --all-packages`: a member "
        "whose deps are absent is dropped from collection and the job still exits 0"
    )

    emitted = re.findall(r"--junitxml=(\S+)", joined)
    assert emitted, (
        f"{workflow.name} runs pytest but emits no --junitxml report, so nothing "
        "downstream can tell a full run from an empty one"
    )
    for report in emitted:
        consumers = [
            command
            for command in commands
            if report in command and "--junitxml" not in command
        ]
        assert consumers, (
            f"{workflow.name} writes {report} and no later step reads it: the run "
            "is measured by nobody and a collection of zero passes"
        )
        # Matched with a boundary, not as a substring: `--min-tests-disabled`
        # is not `--min-tests`, and a substring test passes on it.
        floors = [
            command for command in consumers if re.search(r"--min-tests[\s=]", command)
        ]
        assert floors, (
            f"{workflow.name} reads {report} but asserts no floor on it "
            "(--min-tests): an empty collection is still green"
        )
        for floor_command in floors:
            required = re.findall(r"--require-dir\s+(\S+)", floor_command)
            #: Derived: every workspace member that has a tests/ directory.
            members = [
                member.name
                for member in sorted(_REPO_ROOT.iterdir())
                if (member / "tests").is_dir() and (member / "pyproject.toml").is_file()
            ]
            missing = [
                member
                for member in members
                if not any(requirement.startswith(member) for requirement in required)
            ]
            assert not missing, (
                f"{workflow.name}: the floor check does not require cases from "
                f"{missing} — those workspace members can be dropped from "
                "collection with the job still green"
            )


# ------------------------------------------- the guard itself has to have teeth
def _junit(tests: int, errors: int = 0, failures: int = 0, files: tuple[str, ...] = ()) -> str:
    """A report carrying a `file` attribute on each case."""
    cases = "".join(
        f'<testcase classname="c" name="t{index}" file="{path}"/>'
        for index, path in enumerate(files)
    )
    return (
        "<testsuites><testsuite "
        f'name="pytest" tests="{tests}" errors="{errors}" failures="{failures}">'
        f"{cases}</testsuite></testsuites>"
    )


def _guard(tmp_path: Path, xml: str, *args: str) -> int:
    sys.path.insert(0, str(_REPO_ROOT / ".github" / "scripts"))
    try:
        import assert_suite_floor
    finally:
        sys.path.pop(0)
    report = tmp_path / "pytest-report.xml"
    report.write_text(textwrap.dedent(xml), encoding="utf-8")
    return assert_suite_floor.main([str(report), *args])


_BOTH_MEMBERS = ("runcoach-api/tests/test_a.py", "runcoach-cli/tests/test_b.py")
_FLOOR_ARGS = (
    "--min-tests",
    "10",
    "--require-dir",
    "runcoach-api/tests",
    "--require-dir",
    "runcoach-cli/tests",
)


def test_the_floor_guard_passes_a_run_that_was_as_wide_as_it_claimed(tmp_path: Path) -> None:
    assert _guard(tmp_path, _junit(20, files=_BOTH_MEMBERS), *_FLOOR_ARGS) == 0


def test_the_floor_guard_fails_a_run_that_collected_too_little(tmp_path: Path) -> None:
    assert _guard(tmp_path, _junit(3, files=_BOTH_MEMBERS), *_FLOOR_ARGS) == 1


def test_the_floor_guard_fails_when_a_workspace_member_contributed_nothing(
    tmp_path: Path,
) -> None:
    """The shape the root conftest produces: every collected test passed, the
    count is healthy, and one member was never imported."""
    only_api = tuple(f"runcoach-api/tests/test_{index}.py" for index in range(20))
    assert _guard(tmp_path, _junit(20, files=only_api), *_FLOOR_ARGS) == 1


def test_the_floor_guard_fails_on_a_collection_error(tmp_path: Path) -> None:
    assert _guard(tmp_path, _junit(20, errors=1, files=_BOTH_MEMBERS), *_FLOOR_ARGS) == 1


def _junit_classname_only(modules: tuple[str, ...]) -> str:
    """**The shape pytest 8 actually writes on this workspace**: no `file`
    attribute at all, the source path carried dot-separated in `classname`.
    Copied from a real `--junitxml` run on 2026-09-16, after a first cut of
    the guard read `file` alone and declared both workspace members missing on
    a run where both had passed. Without this case the guard's unit tests
    agree with a fixture the guard's author invented, and the one thing they
    are checking — does this read what pytest emits — goes untested
    (contract-tables-need-an-independent-oracle)."""
    cases = "".join(
        f'<testcase classname="{module}" name="t{index}" time="0.01" />'
        for index, module in enumerate(modules)
    )
    return (
        '<testsuites name="pytest tests"><testsuite name="pytest" errors="0" '
        f'failures="0" skipped="0" tests="{len(modules)}">{cases}'
        "</testsuite></testsuites>"
    )


def test_the_floor_guard_reads_the_report_pytest_actually_writes(tmp_path: Path) -> None:
    both = ("runcoach-api.tests.test_health", "runcoach-cli.tests.test_main")
    assert _guard(tmp_path, _junit_classname_only(both), "--min-tests", "2",
                  "--require-dir", "runcoach-api/tests",
                  "--require-dir", "runcoach-cli/tests") == 0


def test_the_floor_guard_still_sees_a_dropped_member_without_file_attributes(
    tmp_path: Path,
) -> None:
    api_only = tuple(f"runcoach-api.tests.test_{index}" for index in range(20))
    assert _guard(tmp_path, _junit_classname_only(api_only), "--min-tests", "2",
                  "--require-dir", "runcoach-api/tests",
                  "--require-dir", "runcoach-cli/tests") == 1


def test_the_floor_guard_reports_a_missing_or_unreadable_report(tmp_path: Path) -> None:
    assert _guard(tmp_path, "<testsuites>", *_FLOOR_ARGS) == 2
    sys.path.insert(0, str(_REPO_ROOT / ".github" / "scripts"))
    try:
        import assert_suite_floor
    finally:
        sys.path.pop(0)
    assert assert_suite_floor.main([str(tmp_path / "absent.xml"), "--min-tests", "1"]) == 2
