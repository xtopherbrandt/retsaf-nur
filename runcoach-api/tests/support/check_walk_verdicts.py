"""Fail unless each of F005's three long walks asserts a verdict *inside* its loop.

    uv run --package runcoach-api python runcoach-api/tests/support/check_walk_verdicts.py

T127's acceptance probe calls this after the five HRV suites. It is a
structural check, not a behavioural one: pytest can only tell you that the
assertions present are green, and the defect this exists to prevent is an
assertion that is *absent* from a loop that runs 114 times.

**Why the loop body and not the function.** At ``3f1430c`` the G3 walk
``test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline``
already mentioned ``verdict`` once and already called ``judge`` once -- both
at a single target, ``T+20``, *after* the loop. A function-level "does it
mention a verdict" check passes there, and would have certified as verdict-
pinned the exact walk whose blindness made [[T125]]'s form 5a score 0 red of
401 while flipping two of its targets from ``hrv_normal`` to
``hrv_unavailable``. So each walk must carry, in the body of one of its own
``for`` loops, both a call to ``judge`` and a reference to the ``verdict``
field it returns. The loop's ``iter`` and ``target`` do not count.

This checks the shape of the walk, not what it asserts -- a loop calling
``judge`` and reading ``.verdict`` without comparing it to anything would pass
here. The expectations themselves are the reviewer's business and the
docstrings' (each of the three states how its expected verdicts were derived).
What this makes impossible is the failure mode that actually occurred: the
loop silently having nothing to say about the verdict at all.

Exit 0 when every walk carries one; exit 1, naming each walk that does not,
otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: ``(path relative to the repo root, test function name)``. These are the
#: three walks T127 was raised over: each ran a long loop asserting a
#: tier-shaped tuple per target and never the verdict that tuple decides.
WALKS = (
    (
        "runcoach-api/tests/test_hrv_trend_reset.py",
        "test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline",
    ),
    (
        "runcoach-api/tests/test_hrv_trend_series.py",
        "test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it",
    ),
    (
        "runcoach-api/tests/test_hrv_trend_series.py",
        "test_the_seam_row_is_the_only_one_whose_red_onset_is_at_gap_reset_days",
    ),
)

#: The function whose result a verdict assertion has to come from, and the
#: field on it. Spelled as names rather than imported: this module parses
#: source and never runs it.
JUDGE = "judge"
VERDICT = "verdict"


def _find_function(tree: ast.Module, name: str) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]


def _calls_judge(node: ast.AST) -> bool:
    """Any ``judge(...)`` or ``<anything>.judge(...)`` in ``node``'s subtree."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == JUDGE:
            return True
        if isinstance(func, ast.Attribute) and func.attr == JUDGE:
            return True
    return False


def _reads_verdict(node: ast.AST) -> bool:
    """Any reference to the ``verdict`` field in ``node``'s subtree -- read as
    an attribute (``judged.verdict``), bound by name (``verdict, n, ... =``)
    or used by name."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == VERDICT:
            return True
        if isinstance(child, ast.Name) and child.id == VERDICT:
            return True
    return False


def _loop_bodies(function: ast.FunctionDef):
    """Every ``for`` statement in ``function``, as its body plus ``else``
    clause -- deliberately **not** its ``iter`` or ``target``, so a loop over
    something merely named ``verdicts`` proves nothing."""
    for node in ast.walk(function):
        if isinstance(node, ast.For):
            yield node, [*node.body, *node.orelse]


def check_walk(path: Path, name: str) -> list[str]:
    """The reasons ``name`` in ``path`` fails; empty when it passes."""
    if not path.exists():
        return [f"{path} does not exist"]
    functions = _find_function(ast.parse(path.read_text(encoding="utf-8")), name)
    if len(functions) != 1:
        return [f"{name} is defined {len(functions)} times in {path.name}, expected exactly once"]

    loops = list(_loop_bodies(functions[0]))
    if not loops:
        return [f"{name} in {path.name} contains no `for` loop at all -- it is not a walk"]

    for loop, body in loops:
        if any(_calls_judge(stmt) for stmt in body) and any(_reads_verdict(stmt) for stmt in body):
            return []

    detail = ", ".join(
        f"line {loop.lineno} (calls judge: {any(_calls_judge(s) for s in body)}, "
        f"reads verdict: {any(_reads_verdict(s) for s in body)})"
        for loop, body in loops
    )
    return [
        (
            f"{name} in {path.name}: no loop body both calls `{JUDGE}` and reads `.{VERDICT}`. "
            f"Loops found: {detail}. A mention outside the loop does not count -- that is exactly "
            f"the shape that let T125's form 5a flip two targets of this walk with nothing red."
        )
    ]


def main() -> int:
    failures: list[str] = []
    for relative, name in WALKS:
        path = REPO_ROOT / relative
        reasons = check_walk(path, name)
        status = "FAIL" if reasons else "ok"
        print(f"[{status}] {relative}::{name}")
        failures += reasons

    if failures:
        print(f"\n{len(failures)} walk(s) assert no verdict per target:", file=sys.stderr)
        for reason in failures:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    print(f"\nall {len(WALKS)} walks assert a verdict inside their loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
