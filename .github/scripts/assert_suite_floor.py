#!/usr/bin/env python3
"""Fail a CI test-suite run that passed over too little.

`pytest` exits non-zero on a collection *error*, but this workspace has a
quieter failure mode that exits **0**: the workspace-root `conftest.py` drops
a member from collection (`collect_ignore`) when that member's runtime
dependencies are not importable. A run that installed only half the workspace
therefore reports "all tests passed" while never having imported the other
half. T131 installed CI precisely so the checks stop being "whatever one
machine happened to run", so a green tick has to mean the whole suite ran --
not that whatever was collected ran.

This reads the JUnit XML the suite emitted and fails when:

  * any test errored or failed (belt-and-braces beside pytest's exit code),
  * fewer than ``--min-tests`` cases were recorded (an empty or partial
    collection), or
  * a required source directory contributed no cases at all
    (``--require-dir``), which is exactly the silently-dropped-member shape.

Exit 0 = the run was as wide as it claimed, 1 = it was not, 2 = the report
could not be read.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _testsuites(root: ET.Element) -> list[ET.Element]:
    """pytest emits ``<testsuites><testsuite>``; older shapes emit the bare
    ``<testsuite>``. Accept either rather than depending on the wrapper."""
    if root.tag == "testsuite":
        return [root]
    return list(root.iter("testsuite"))


def _source_path(case: ET.Element) -> str:
    """Where a ``<testcase>`` came from, as a forward-slash path.

    **pytest does not always write a ``file`` attribute.** Measured against
    pytest 8 on this workspace (2026-09-16), a run emits only
    ``classname="runcoach-api.tests.test_health"`` -- the path, dot-separated,
    with the module name on the end. A first cut of this script read ``file``
    alone and reported *both* workspace members as missing on a run where both
    had plainly passed. So ``file`` is used when present and ``classname`` is
    translated when it is not; a directory prefix matches either form."""
    explicit = (case.get("file") or "").replace("\\", "/")
    if explicit:
        return explicit
    return (case.get("classname") or "").replace(".", "/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JUnit report floor check")
    parser.add_argument("report", type=Path, help="the JUnit XML pytest wrote")
    parser.add_argument(
        "--min-tests",
        type=int,
        required=True,
        help="floor on recorded cases; a partial collection lands under it",
    )
    parser.add_argument(
        "--require-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="a source directory that must have contributed at least one case "
        "(repeatable); catches a workspace member dropped from collection",
    )
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(
            f"error: no JUnit report at {args.report}: the suite step did not run",
            file=sys.stderr,
        )
        return 2
    try:
        root = ET.parse(args.report).getroot()
    except ET.ParseError as exc:
        print(f"error: {args.report} is not parseable XML: {exc}", file=sys.stderr)
        return 2

    suites = _testsuites(root)
    if not suites:
        print(f"error: {args.report} records no <testsuite>", file=sys.stderr)
        return 2

    total = sum(int(suite.get("tests", 0)) for suite in suites)
    errors = sum(int(suite.get("errors", 0)) for suite in suites)
    failures = sum(int(suite.get("failures", 0)) for suite in suites)
    sources = {
        _source_path(case) for suite in suites for case in suite.iter("testcase")
    }
    sources.discard("")

    problems: list[str] = []
    if errors:
        problems.append(f"{errors} collection/setup error(s)")
    if failures:
        problems.append(f"{failures} failing test(s)")
    if total < args.min_tests:
        problems.append(
            f"only {total} cases recorded, under the floor of {args.min_tests}: "
            "the run collected far less than this suite holds"
        )
    for required in args.require_dir:
        wanted = required.replace("\\", "/").rstrip("/") + "/"
        if not any(path.startswith(wanted) for path in sources):
            problems.append(
                f"no test case came from {required}: that workspace member was "
                "dropped from collection (its dependencies are probably not installed)"
            )

    if problems:
        print(f"suite floor check FAILED on {args.report}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"suite floor check OK: {total} cases from {len(sources)} modules, "
        "no errors or failures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
