"""Workspace-root conftest.

`uv run --package <member> pytest` (re)syncs the shared workspace .venv to
that member's own dependencies, but pytest invoked with no path argument
still collects every test file under the repo root, including sibling
workspace members whose dependencies aren't installed in the currently
synced environment. Skip a member's test suite from collection when its
runtime dependencies aren't importable, rather than hard-failing collection.
"""

import importlib.util

collect_ignore: list[str] = []

if importlib.util.find_spec("fastapi") is None:
    collect_ignore.append("runcoach-api")

if importlib.util.find_spec("typer") is None:
    collect_ignore.append("runcoach-cli")
