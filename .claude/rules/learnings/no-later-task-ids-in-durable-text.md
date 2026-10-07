---
paths: ["**/*"]
---
# Durable text never names a later task of the same sprint

Learned from sprint-010 (2026-10-03). Origin: `spec/ideas/IDEA-122-no-future-task-ids-in-durable-prose.md`.

## The rule

Durable text states the as-built state, and never names a later task of the same sprint. Durable
text is anything that outlives the commit: source comments, docstrings, test names and test
docstrings, the CHANGELOG, spec and reference documents, and `.claude/rules/`.

- **Name the function or field instead.** "Populated by `_resolve_hr_sensor_serial`" stays true
  after the task that writes it merges; "populated by T249" does not, and "nothing populates it yet
  (T249 does)" is false the moment it does.
- **Or say nothing.** If the behaviour is not built yet, the text describes what is built.
- **Task IDs belong in the commit subject's scope**, where they are a record of who wrote the line,
  not a claim about the tree.
- **The sprint's last-wave gate runs the sweep script with the sprint's ID range:**

      uv run --package runcoach-api python runcoach-api/tests/support/sweep_sprint_task_ids.py \
          --base <sprint base> --ids <first>-<last> --strict

  It reads the added lines of the sprint's diff with whitespace flattened (wrapped prose defeats
  `grep`), prints every compared file and every hit, and fails a hit when any of its writers has
  a scope other than that same task ID, a `sprint-NNN` scope or none. `--strict` also fails the
  `listed` hits, whose worst writer has a `sprint-NNN` scope or no scope.
- **An ID is matched in any case and inside identifiers.** `(t249 does)`, `test_..._until_t249`,
  `T249_SERIAL` and `T249a` all name T249, because test names and constants are durable text too;
  `T2490`, `UT249` and `1T249` name no ID.
- **A hit is judged by every writer of its ID in the file.** A hit on a line of file F naming ID
  X is judged by the worst of every commit in the sweep range that added or removed a line of F
  naming X, plus the commit `blame` names for the line (which adds a merge that wrote it): `fail`,
  then `listed`, then `exempt`. The report names the oldest of the worst writers, and the commit
  that last edited the line when that is another. A whitespace-only change writes nothing, a
  rename keeps the writers from before it, and a commit before the sweep base does not count. So a
  hand-off that the task it names later rewords, joins, splits or moves still fails.
- **The accepted over-fail (the user's ruling, 2026-10-06).** When another task's commit adds or
  removes any line of F naming X, every hit for X in F fails, a self-tag included. Rewrite the
  line, or justify it as `listed`.
- **A bad range is an error.** The base must be an ancestor of the head and a different commit:
  `--base HEAD` and a reversed range exit 2, rather than comparing nothing and passing. The
  sweep also refuses, with exit 2, a range in which no commit has one of the given IDs as its
  scope (the wrong range or the wrong IDs), and a range that compares no file.
- **The user's git config does not change what is read.** The script fixes the diff prefixes and
  drops a configured `blame.ignoreRevsFile`; a diff header it cannot read is an error, not a file
  skipped.

## Why

In sprint-010, wave-1 tasks wrote hand-offs to a later task into durable text: "Nothing populates
the column yet (T249 does)" in the CHANGELOG, "None until T249 populates it" in `db.py`, "T249
populates it" in `models.py`, and two test docstrings saying the check was vacuous until T249
landed. Each was true when written and false once T249 merged. Nothing re-read them: the release
gate's cross-read checked meaning, not tense, and review found the five sites over two passes.

Replayed on that sprint's range (`c2839b6..6d76bc8`, T247-T253), the sweep fails and names all
seven lines that carry them.

## How to apply

- Before committing, search your own diff for the sprint's other task IDs. A hit is a sentence that
  will go false; rewrite it to name the code.
- In fixtures and docstrings that must show a task ID as an example, use an ID from an earlier
  sprint, so the sweep over the current sprint does not flag its own tooling.
- A hit under a `sprint-NNN` scope is a backward reference made at sprint level; it is listed, and
  the release gate's `--strict` run still asks for it to be rewritten or justified.

Related: [[sweep-the-claim-not-the-diff]], which governs correcting a claim once it is false. This
rule keeps a hand-off from becoming one.
