---
paths: ["specification/research/00-*.md", "runcoach-api/tests/test_research00_traceability.py", "runcoach-api/tests/support/research00_old_meanings.py"]
---
# Editing research/00: a fresh critic first, then only the changed literals

The four research/00 files (00-design-decisions.md, 00-history.md, 00-traceability.md and
00-meaning-review.md) are bound line by line by test_research00_traceability.py, so no edit to
them is silent. Written 2026-09-27 (T195) from R13 in the F008 decisions reference; F009, F010 and
F011 edit these files through this procedure.

## What is bound, and how

- **Rule lines and Glossary lines** (each rule line, its Scope, Not and Why lines, and each T-NN
  definition) are bound by the digest their own verdict line in the meaning review records
  (`reviewed_block_errors`). That digest is read from the review file, not from the test, so no
  regeneration of the test's literals clears a changed line; only a verdict line recording the new
  digest does. The verdict line is itself bound by `REVIEW_LINE_SHA256`, and regenerating that
  literal takes a changed verdict line only when a new round re-judged it (see Never). A line
  deleted with its verdict line stays red after regeneration unless a new round removes it in the
  clause form of step 3, as in `; removed PRIN-12/Why`; a passing mention of the row does not do it.
- **Everything else** is a frozen literal in the test: Pinned lines (`PINNED_SHA256`); headings,
  terms and rule IDs in order (`RESEARCH_STRUCTURE`, `GLOSSARY_TERMS`); table rows, retirements and
  authorities (`TRACEABILITY_ROW_SHA256`, `RETIRED_IDS`, `NON_C_AUTHORITIES`, `KEY_OWNERS` and
  their pins); history lines (`HISTORY_SHA256`); the old meanings (`OLD_MEANING_SHA256`); each
  verdict line with its digest cell, and every prose line of the review (`REVIEW_LINE_SHA256`,
  `REVIEW_PROSE_SHA256`); and each frozen round by its name (`FROZEN_ROUNDS` and its pin).
- **The inventory sentences** (`INVENTORY_SENTENCE_SHA256`) are the 4e47d0e inventory's and are
  never regenerated. The documented command prints the committed literal back, and names each table
  cell that differs from it as a problem; it never prints a new digest for one.

## The sequence

1. **A ruling first, for any meaning change.** The user rules (R3, R13), and the ruling is recorded
   as a new R-entry, or a dated bullet under one, in the F008 decisions reference. A rewording that
   keeps the meaning still goes through steps 2 to 5. A Pinned-line, history-line, structure or
   old-meanings change that touches no rule, Scope, Not, Why or Glossary line needs no critic round:
   steps 2, 4 and 5 only, as T221 and T223 did.
2. **The builder edits the rule text**, and the table, history or old meanings the change needs.
   History lines and dated notes use the vocabulary in force when they are edited; the period
   wording is recoverable from git (sprint-009 D3), so a history entry reworded to the current terms
   is not restored.
3. **A fresh critic that built nothing** re-judges every changed row. It writes each new verdict line
   with the digest of the line it judged (`_cell_digest`: whitespace collapsed, first 12 hex of the
   sha256) and a reason that begins with its round's name, then a new round paragraph under the
   Rounds heading, after the last round and before the Final line, and it rewrites the Final line.
   The paragraph is one line, never wrapped: only the line that begins with its name is read for
   labels, and a second line breaks the review's shape (`review_shape_errors`). Its name is a plain
   number above every earlier round's, so the next round after round 11 is 12, never 012 or 11b
   (3b and 4b are the only lettered rounds). It names every row it read, including the neighbours
   it left `same`; a round that counts its neighbours without naming them records nothing (sprint-009
   D4). A removal is its own clause, the lower-case `; removed <label>` after a semicolon or
   `. Removed <label>` after a full stop, one label per clause, as in
   `re-judged PRIN-12; removed PRIN-12/Why.`; a comma before it, `; Removed`, `. removed`, the
   round's opening clause and a second label after "and" remove nothing. The builder never edits
   the meaning review.
4. **Regenerate only the changed non-verdict literals**, plus `REVIEW_LINE_SHA256`,
   `REVIEW_PROSE_SHA256`, `FROZEN_ROUNDS` and its pin, from the one documented command. Its notes must
   end with an empty problems list (a misnamed round, a changed inventory sentence and a broken review
   shape are problems too); paste the entries the edit changed and no others, so the diff shows what
   was approved:

   ```sh
   uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py
   ```

5. **Run the seven gate files** and require 0 failed and 0 skipped:

   ```sh
   uv run --package runcoach-api pytest runcoach-api/tests/test_research00_traceability.py runcoach-api/tests/test_source_change_rule_sweep.py runcoach-api/tests/test_band_reconciliation.py runcoach-api/tests/test_spec_cost_figures.py runcoach-api/tests/test_hrv_unavailable_causes.py runcoach-api/tests/test_hrv_trend_endpoint.py runcoach-api/tests/test_hrv_no_regression_gate.py -q -p no:cacheprovider -rs
   ```

## Never

- **Paste a digest to clear a red.** A digest an error prints is for step 4 of a reviewed edit, not
  a way past a red. A red on a rule or Glossary line means no critic has judged it. No message about
  a verdict line or a frozen round prints a digest; the prose-line message prints the new prose
  tuple, and pasting it re-opens no row.
- **Clear a verdict line no critic re-judged.** A changed or added verdict line counts only when its
  reason begins `Round N:`, N is a round not in `FROZEN_ROUNDS`, and that round's paragraph names
  the row (`_rejudged`). A round that mentions the row in passing does not clear it, and a pasted
  digest cell under its old reason stays red. The critic writes the prefix only on lines it
  re-judged.
- **Edit or remove a frozen round.** A round is frozen by its name in `FROZEN_ROUNDS`, so an edited
  frozen round re-opens no row and reds (`frozen_round_errors`), and the regeneration keeps its
  committed entry. A new finding goes in a new round.
- **Let a builder write verdicts.** The critic owns the meaning review; the builder's diff never
  touches it.

## The known limit, and the reviewer's check

The suite cannot tell a regenerated literal from a hand-edited one. Each of these keeps the
traceability test file green on the real files:

1. a Why line and its verdict line deleted, with the row's `REVIEW_LINE_SHA256` entry dropped by
   hand;
2. a rule line changed, with its verdict line's digest cell pasted and the row's entry set by hand
   to the new line's digest;
3. a frozen round's text edited, with three literals set by hand: its `FROZEN_ROUNDS` entry, the
   pin's entry and `REVIEW_PROSE_SHA256`. Done in one step, the edited round stays frozen and
   names no row. Done in two, it clears a row: its name is dropped from `FROZEN_ROUNDS` and the pin
   by hand, the literals are regenerated while the round, now unfrozen, names a row whose pasted
   cell cites it, and the name is put back by hand with the new digest;
4. the review's opening paragraph and Final line edited, with `REVIEW_PROSE_SHA256` regenerated.

A removed title, paragraph, heading or Final line reds (`review_shape_errors`). The documented
command derives against this file's own `REVIEW_LINE_SHA256`, `FROZEN_ROUNDS` and
`INVENTORY_SENTENCE_SHA256`, so for the first three it prints the hand-edited value back, and for
the fourth it prints what the file holds. The reviewer therefore runs it against the commit the
change was written on, with `<base>` as that commit:

```sh
uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py --against <base>
```

It reads the base's literals with `git show` and `ast`, and derives from the current files with the
base's three literals in place of this file's. For each literal it prints what changed from the
base, by key or position and never by value, and whether this file's literal is the derived one.
It prints a line beginning `# difference:` for each of these, and exits 1 if there is one: a
problem in the derivation; a literal that is not the derived one (routes 1 to 3, route 3 in one
step or two); `INVENTORY_SENTENCE_SHA256` changed at all; and `REVIEW_PROSE_SHA256` moved other
than as a legitimate round moves it (route 4). The change is approved only when it ends with
`# differences: none`. A legitimate round gives:

- `REVIEW_LINE_SHA256`: changed or added entries only for rows whose verdict line's reason begins
  with a new round's name and whose paragraph names the row, and removed entries only for rows a
  new round says "removed" of;
- `REVIEW_PROSE_SHA256`: one entry per new round, each before Final's; Final's changed; nothing
  else moves;
- `FROZEN_ROUNDS` and its pin: one new entry in each for each new round; no entry changes;
- `INVENTORY_SENTENCE_SHA256`: never changes.

`--against` runs this file's checker code, so it cannot judge a change to that code. It prints
`# code changed: <name>`, `# code added: <name>` or `# code removed: <name>` for each top-level
definition outside the frozen literals and the `__main__` block that differs from the base's. These
are report lines, not differences, and do not change the exit code. The reviewer reads the diff of
every name they list. The suite also cannot tell a critic from a builder: a round is written and
committed by a subagent that built nothing in that task, named as the critic in that commit's author
or body, and the reviewer checks that the commit's diff touches only the meaning review.
