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
  deleted with its verdict line stays red after regeneration unless a new round names the row.
- **Everything else** is a frozen literal in the test: Pinned lines (`PINNED_SHA256`); headings,
  terms and rule IDs in order (`RESEARCH_STRUCTURE`, `GLOSSARY_TERMS`); table rows, retirements and
  authorities (`TRACEABILITY_ROW_SHA256`, `RETIRED_IDS`, `NON_C_AUTHORITIES`, `KEY_OWNERS` and
  their pins); history lines (`HISTORY_SHA256`); the old meanings (`OLD_MEANING_SHA256`); each
  verdict line with its digest cell, and every prose line of the review (`REVIEW_LINE_SHA256`,
  `REVIEW_PROSE_SHA256`); and each frozen round by its name (`FROZEN_ROUNDS` and its pin).
- **The inventory sentences** (`INVENTORY_SENTENCE_SHA256`) are the 4e47d0e inventory's and are
  never regenerated.

## The sequence

1. **A ruling first, for any meaning change.** The user rules (R3, R13), and the ruling is recorded
   as a new R-entry, or a dated bullet under one, in the F008 decisions reference. A rewording that
   keeps the meaning still goes through steps 2 to 5.
2. **The builder edits the rule text**, and the table, history or old meanings the change needs.
3. **A fresh critic that built nothing** re-judges every changed row. It writes each new verdict line
   with the digest of the line it judged (`_cell_digest`: whitespace collapsed, first 12 hex of the
   sha256) and a reason that begins with its round's name, a new round paragraph under the Rounds
   heading with a name no earlier round has, naming every changed or removed row, and the Final
   line. The builder never edits the meaning review.
4. **Regenerate only the changed non-verdict literals**, plus `REVIEW_LINE_SHA256`,
   `REVIEW_PROSE_SHA256`, `FROZEN_ROUNDS` and its pin, from the one documented command. Its notes must
   end with an empty problems list; paste the entries the edit changed and no others, so the diff
   shows what was approved:

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

## The known limit

The gate cannot tell a regenerated literal from a hand-edited one. Each of these kept the
traceability test file green on the real files at this commit: a Why line and its verdict line
deleted, with the row's `REVIEW_LINE_SHA256` entry dropped by hand; a rule line changed, with its
verdict line's digest cell pasted and the row's entry set by hand to the new line's digest; a
frozen round's text edited, with its `FROZEN_ROUNDS` entry and pin set by hand (it still names no
row); and the review's opening paragraph and Final line edited, with `REVIEW_PROSE_SHA256`
regenerated. A removed title, paragraph, heading or Final line reds (`review_shape_errors`).
For the first three, `frozen_literals()` prints the committed value, not the hand-edited one; the
fourth is what it prints. So a reviewer compares every literal diff with what a legitimate round
gives:

- `REVIEW_LINE_SHA256`: each changed or added entry's verdict line has a reason that begins with
  the new round's name, and each removed entry is a row the new round names;
- `REVIEW_PROSE_SHA256`: one entry added before the last one, and the last one (Final) changed;
  nothing else moves;
- `FROZEN_ROUNDS` and its pin: one new entry in each for each new round; no entry changes.
