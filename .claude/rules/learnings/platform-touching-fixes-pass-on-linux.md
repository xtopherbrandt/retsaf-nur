---
paths: ["**/*"]
---
# A change touching platform-dependent behaviour is not fixed until its tests pass on Linux

Learned from sprint-021 (2026-10-22). Origin: `spec/ideas/IDEA-182-platform-touching-fixes-pass-on-linux.md`.

## The rule

A change that touches **dates and times, paths, line endings, shells, encodings, or `os` / `platform`
calls** names the test files that cover it, and those tests pass on Linux before the item counts as
fixed, built or done. This holds for a review fix as much as for a task.

The evidence is the Linux run's **summary line**, quoted in the return or the commit body as the
runner prints it:

```
bash .github/scripts/run_on_linux.sh <ref> <test files>
```

The script runs the named files in a Linux clone inside WSL at a committed ref. It prints `linux: ref
<sha>`, the resolved ref, first, before the setup's and pytest's output, and ends with two lines
prefixed `linux: `: the platform line and pytest's summary line. Its exit status is pytest's.
Commit first: the ref is resolved on the Windows side and uncommitted edits are not seen. Its usage comment records the measured cost: on 2026-10-26 a warm run of two test files
(45 tests, the lock unchanged) took 8 to 19 s of wall time.

A Windows run is not Linux evidence, however many times it is green.

## Why

Sprint-021's round-1 review fix reworded every `ValueError` the FIT decoder raises. On Windows a
date_time past year 9999 raises `OSError`; on Linux the same call raises
`ValueError('year N is out of range')`. The fix passed every Windows run and would have turned three
tests red on CI, which runs on `ubuntu-latest`. The round-2 scanner found it one round later, by
reasoning and a WSL `python3` check, not by a test run.

Every run in this project's sprints is on Windows. CI is the only Linux run, and nobody waits for it
before a fix is called done. The athlete named this as what went badly in sprint-021.

## How to apply

- **Decide at the start whether the change is platform-touching.** Anything that formats or parses a
  timestamp, converts between time zones or epochs, builds or compares a path, reads or writes text
  where line endings or encodings matter, spawns a shell, or branches on `os` / `platform` is.
- **Name the test files**, the ones that exercise the changed branch, not the whole suite.
- **Run them through the script and quote its summary line.** Inside an isolated worktree the Bash
  tool may refuse a command that names `wsl` or runs `bash <script>`; run the script from the
  PowerShell tool with Git Bash (`C:\Program Files\Git\bin\bash.exe`) instead.
- **A red Linux run with a green Windows run is a finding**, not a flake: read which exception or
  value differs and fix the code or the test for both platforms.
- **When no Linux run is possible**, say so in the return and leave the item open, instead of calling
  it fixed on Windows evidence.

Related: [[a-published-invariant-needs-a-test-that-can-break-it]] — a test that cannot break on the
platform it runs on proves nothing about the other one.
