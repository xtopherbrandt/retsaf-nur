"""T134 -- wires ``check_walk_verdicts.py`` into something that runs on every commit.

``runcoach-api/tests/support/check_walk_verdicts.py`` (T127) AST-checks that each of F005's
three long walks calls ``judge`` **and** reads ``.verdict`` *inside the body of one of its own
``for`` loops* -- the shape whose absence let a 114-target walk assert only ``(tier,
reset_reason, reset_on)`` per target and score **0 red of 401** when a candidate fix flipped
two of its verdicts (T125's form 5a).

Its own docstring claimed *"T127's acceptance probe calls this after the five HRV suites"* --
true exactly once, when T127's probe ran. `grep -rn "check_walk"` across `runcoach-api/`,
`.github/`, and every `.py`/`.yml`/`.yaml`/`.toml` in the tree found no reference to it
outside its own file: no test imported it, no workflow step invoked it. A correct check that
cannot fire is not a check.

This module closes that by literally executing it, so a walk that loses its verdict
assertion goes red in the ordinary suite -- not only when someone remembers the script exists
and runs it by hand. ``.github/workflows/test-suite.yml`` also runs it directly as its own
step, so CI is covered even on a clean checkout that never imports this test module (the two
overlap deliberately: this is what makes it run locally too).

Kept out of the five HRV suites (test_hrv_trend_band.py/_endpoint.py/_points.py/_reset.py/
_series.py) so nobody's SCOPED_SUITE_COLLECTED pin has to move for a test about a support
script, not about a verdict.
"""

from __future__ import annotations

import ast
import importlib.util
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKER_PATH = _REPO_ROOT / "runcoach-api" / "tests" / "support" / "check_walk_verdicts.py"


def _load_checker():
    """Loads ``check_walk_verdicts.py`` by file path, not by adding
    ``tests/support`` to ``sys.path`` -- so this test leaves no import-path
    side effect for any test collected after it."""
    spec = importlib.util.spec_from_file_location("check_walk_verdicts", _CHECKER_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_CHECKER_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_walk_verdict_checker_runs_and_passes_over_the_current_walks() -> None:
    """Runs T127's structural AST check as part of the ordinary suite.

    Exit 0 means each of the three long walks calls ``judge`` and reads
    ``.verdict`` inside the same ``for`` loop body. Perturbation (T134,
    required by the task): move a walk's ``judge``/``.verdict`` reference
    outside its loop body (into module scope, after the loop, or into an
    assertion that follows it) and this test goes red -- not merely the
    script run by hand -- which is the whole point of wiring it in rather
    than leaving it as a script nobody invokes.
    """
    checker = _load_checker()
    assert checker.main() == 0


def test_the_checker_names_all_three_walks_it_was_raised_over() -> None:
    """A narrower pin on the checker's own table, independent of whether the
    walks currently pass: the three walks named in T127's finding are still
    the ones checked, so a future edit that quietly shrinks ``WALKS`` (the
    same "check exists but checks nothing" shape one level in) is visible
    here without needing a walk to actually break first."""
    checker = _load_checker()
    assert checker.WALKS == (
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


def test_moving_a_verdict_read_outside_its_walks_loop_body_is_caught(tmp_path: Path) -> None:
    """Exercises the checker's own logic directly (not through the wiring
    above), against a synthetic walk whose ``.verdict`` read sits after the
    loop rather than inside it -- the exact shape T127 was raised over.
    Independent of ``test_walk_verdict_checker_runs_and_passes_over_the_current_walks``:
    that test proves today's three walks are clean; this one proves the
    checker can still tell a dirty one apart from a clean one."""
    checker = _load_checker()
    broken = tmp_path / "broken_walk.py"
    broken.write_text(
        textwrap.dedent(
            """
            def test_a_walk_that_asserts_no_verdict_per_target():
                for target in range(5):
                    result = judge(target)
                    tier = result.tier
                verdict = result.verdict
                assert verdict == "hrv_normal"
            """
        ),
        encoding="utf-8",
    )
    reasons = checker.check_walk(broken, "test_a_walk_that_asserts_no_verdict_per_target")
    assert reasons, "a `.verdict` read after the loop must be reported, not accepted"

    clean = tmp_path / "clean_walk.py"
    clean.write_text(
        textwrap.dedent(
            """
            def test_a_walk_that_asserts_a_verdict_per_target():
                for target in range(5):
                    result = judge(target)
                    verdict = result.verdict
                    assert verdict == "hrv_normal"
            """
        ),
        encoding="utf-8",
    )
    assert checker.check_walk(clean, "test_a_walk_that_asserts_a_verdict_per_target") == []


def test_check_walk_verdicts_module_still_parses_as_the_ast_checker_it_documents() -> None:
    """A cheap non-vacuity guard on the loader itself: the module actually
    defines the surface the tests above depend on, so a future rewrite that
    silently drops ``main``, ``check_walk`` or ``WALKS`` is caught here
    rather than surfacing as a mysterious AttributeError three tests down."""
    tree = ast.parse(_CHECKER_PATH.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {"main", "check_walk"} <= defined
