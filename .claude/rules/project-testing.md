---
paths: ["**/tests/**", "**/test_*.py"]
---
# Testing Discipline

## Verify mocks against real dependency behavior

Before mocking a third-party call in a way that's **load-bearing for the test's pass/fail** (not just isolating unrelated I/O), check the dependency's actual source (already vendored under `.venv/`) to confirm the mocked behavior matches what the real call does in the failure mode under test.

**Why:** sprint-001's `test_cli_port_in_use_exits_nonzero_without_traceback` monkeypatched `uvicorn.run()` to raise `OSError` directly on a port conflict — a reasonable-looking assumption that was wrong. Real `uvicorn.Server.startup()` catches the bind `OSError` internally and calls `sys.exit()` itself, so the code path the test validated could never execute in production. The test passed; the feature didn't work. Only a direct code-review read of `uvicorn/server.py` caught it — the test suite gave false confidence.

**Scope:** this applies to mocks of *behavior* (what a call does when it fails/succeeds in a specific way you're asserting on) — not mocks of *unrelated I/O* (e.g. mocking a network call so a unit test doesn't need a real round-trip). The latter is standard practice and doesn't need source verification.

**How to apply:** when a test's Red step depends on a specific exception type, return value, or side effect from a third-party call, grep/read that library's source for the code path you're mocking before trusting the mock. One source read now is cheaper than a bug that survives a full green test suite.
