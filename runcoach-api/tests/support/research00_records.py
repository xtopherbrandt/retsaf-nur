"""F011 S1 / F009 AC2: the shared record set -- the documents the research/00 sweeps never read.

A record is a dated document that legitimately states an old meaning *as the thing that was
superseded*: research/00's own history, a review verdict, an archived draft. The downstream gate
(``test_research00_downstream.py``) walks F011's roots minus these records, and F009's citation
sweep reads the same set, so the two can never disagree about what is live.

``RECORD_SET`` rows are ``(space, prefix, reason)``, modelled on ``SCAN_EXCLUDED_HISTORY`` in
``test_hrv_trend_endpoint.py``:

- ``space`` is ``repo`` (a path relative to the repository root) or ``data`` (a path relative to the
  Shipyard project data dir). The downstream gate never scans the data dir (S2), so its ``data`` rows
  are records for F009 only.
- ``prefix`` is matched against a ``/``-separated path as ``fnmatch.fnmatchcase(path, prefix + "*")``:
  a plain prefix, or a prefix with a ``*`` wildcard where F009 AC2 names a family
  (``verify/*-verdict-cycle``).

F009 extends this module rather than keeping its own literal; a record F009 adds inside an F011 root
lowers that root's ``ROOT_FLOORS`` entry in the same commit (S1). F009 AC2's third record kind --
task files whose frontmatter says ``status: done`` or ``completed`` -- is a frontmatter test, not a
path, and lives with F009's walk, not here.

``SECTION_RECORD_FILES`` holds the files whose text under exactly ``^## Decision Log\\s*$`` is a
record. It is frozen: every other file under the roots that carries the heading is swept, and the gate
lists them.
"""

import fnmatch

RECORD_SET = (
    # S1, by path (repo).
    ("repo", "specification/research/00-history.md",
     "research/00's history: each superseded rule, quoted as the thing superseded"),
    ("repo", "specification/research/00-traceability.md",
     "the old-to-new traceability table quotes every old rule it maps"),
    ("repo", "specification/research/00-meaning-review.md",
     "the dated meaning review quotes each old meaning it judged"),
    ("repo", "spec-mirror/features/F005-",
     "F005's mirrored feature file, a shipped record F008 superseded (R5)"),
    ("repo", "spec-mirror/references/F005-",
     "F005's mirrored references, shipped records F008 superseded (R5)"),
    ("repo", "spec-mirror/references/F006-research-draft-archived-2026-09-23.md",
     "an archived draft, dated in its name"),
    ("repo", "spec-mirror/references/F006-no-regression-report.md",
     "a dated measurement report of the tree it measured"),
    ("repo", "spec-mirror/references/F006-sweep-findings.md",
     "dated sweep findings: each quotes the phrasing it found"),
    # F009 AC2, repo files outside F011's roots.
    ("repo", "runcoach-api/tests/support/research00_old_meanings.py",
     "it holds research/00's old meanings as the literals the sweeps search for (R4)"),
    ("repo", "runcoach-api/tests/test_research00_traceability.py",
     "F008's checker quotes old rules as its fixtures and frozen literals (R4)"),
    # F009 AC2, the data list. Records only: the downstream gate never scans the data dir (S2).
    ("data", "spec/features/F005-",
     "F005's feature file, a shipped record F008 superseded (R5)"),
    ("data", "spec/references/F005-",
     "F005's references, shipped records F008 superseded (R5)"),
    ("data", "spec/references/F006-research-draft-archived-2026-09-23.md",
     "an archived draft, dated in its name"),
    ("data", "spec/references/F006-no-regression-report.md",
     "a dated measurement report of the tree it measured"),
    ("data", "spec/references/F006-sweep-findings.md",
     "dated sweep findings: each quotes the phrasing it found"),
    ("data", "spec/references/research00-rewrite-inventory.md",
     "the rewrite inventory quotes every old wording at 4e47d0e (S5's two pointers are its carve-out)"),
    ("data", "spec/references/F008-rewrite-decisions.md",
     "F008's decisions quote the old rules each decision replaced"),
    ("data", "sprints/sprint-",
     "a closed sprint's files are its dated record; sprints/current is live and does not match"),
    ("data", "verify/*-verdict-cycle",
     "a review verdict quotes the phrasing it found, at the date it found it"),
)

SECTION_RECORD_FILES = frozenset({
    "spec-mirror/features/F006-per-tier-hrv-datasets.md",
})


def is_record(space: str, path: str) -> bool:
    """True when the ``/``-separated ``path`` in ``space`` falls under a ``RECORD_SET`` row."""
    return any(
        row_space == space and fnmatch.fnmatchcase(path, prefix + "*")
        for row_space, prefix, _reason in RECORD_SET
    )
