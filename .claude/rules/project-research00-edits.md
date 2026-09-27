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
  (`reviewed_block_errors`). No literal in the test holds them, so regenerating clears nothing: only
  a critic's new verdict does.
- **Everything else** is a frozen literal in the test: Pinned lines (`PINNED_SHA256`); headings,
  terms and rule IDs in order (`RESEARCH_STRUCTURE`, `GLOSSARY_TERMS`); table rows, retirements and
  authorities (`TRACEABILITY_ROW_SHA256`, `RETIRED_IDS`, `NON_C_AUTHORITIES`, `KEY_OWNERS` and
  their pins); history lines (`HISTORY_SHA256`); the old meanings (`OLD_MEANING_SHA256`); and each
  verdict line with its digest cell, and every prose line of the review (`REVIEW_LINE_SHA256`,
  `REVIEW_PROSE_SHA256`).

## The sequence

1. **A ruling first, for any meaning change.** The user rules (R3, R13), and the ruling is recorded
   as a new R-entry, or a dated bullet under one, in the F008 decisions reference. A rewording that
   keeps the meaning still goes through steps 2 to 5.
2. **The builder edits the rule text**, and the table, history or old meanings the change needs.
3. **A fresh critic that built nothing** re-judges every changed row. It writes each new verdict line
   with the digest of the line it judged (`_cell_digest`: whitespace collapsed, first 12 hex of the
   sha256), a new `Round N:` paragraph under the Rounds heading that names every changed row, and the
   Final line. The builder never edits the meaning review.
4. **Regenerate only the changed non-verdict literals**, plus `REVIEW_LINE_SHA256` and
   `REVIEW_PROSE_SHA256`, from the one documented command. Its notes must end with an empty problems
   list; paste the entries the edit changed and no others, so the diff shows what was approved:

   ```sh
   uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py
   ```

5. **Run the seven gate files** and require 0 failed and 0 skipped:

   ```sh
   uv run --package runcoach-api pytest runcoach-api/tests/test_research00_traceability.py runcoach-api/tests/test_source_change_rule_sweep.py runcoach-api/tests/test_band_reconciliation.py runcoach-api/tests/test_spec_cost_figures.py runcoach-api/tests/test_hrv_unavailable_causes.py runcoach-api/tests/test_hrv_trend_endpoint.py runcoach-api/tests/test_hrv_no_regression_gate.py -q -p no:cacheprovider -rs
   ```

## Never

- **Paste a digest to clear a red.** A digest an error prints is for step 4 of a reviewed edit, not
  a way past a red. A red on a rule or Glossary line means no critic has judged it, and a pasted
  verdict digest stays red until a round names the row.
- **Edit a frozen `Round N:` paragraph.** Rounds already in `REVIEW_PROSE_SHA256` are history; a new
  finding goes in a new round.
- **Let a round name a row it did not re-judge.** `unfrozen_round_labels` treats any mention as
  naming, a cited neighbour included, so that row's changed verdict line would pass unreviewed.
- **Let a builder write verdicts.** The critic owns the meaning review; the builder's diff never
  touches it.

## The known limit

Editing a frozen round and then regenerating `REVIEW_PROSE_SHA256` shows in the diff only as a
changed prose digest. A reviewer checks every `REVIEW_PROSE_SHA256` diff line by line against the
review file's prose, and rejects any change to a round that was already frozen.
