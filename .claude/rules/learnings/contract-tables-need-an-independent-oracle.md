---
paths: ["**/*"]
---
# A check authored by the thing it checks is not a check

Learned from F004 sprint-003 (2026-09-06). Origin: `spec/ideas/IDEA-013-contract-tables-need-an-independent-oracle.md`.

## The rule

Where a **contract table** (or any enumerated test fixture set) is meant to constrain an
implementation, break the shared authorship:

1. **Author the table before the implementation**, from the spec and the domain — listing every
   combination of the inputs the predicate reads, *including the ones expected to be
   uninteresting*. Exhaustive enumeration over a small input space is what makes an omitted row
   visible; **the omission is the finding**.
2. **Or, where it is written after** (in review, say), have a different agent or pass generate the
   rows from the *spec text* without reading the implementation, then reconcile. Disagreements are
   the finding; agreement is worth something only because it could have failed.
3. **State in the table's own docstring which of the two it is.** "Derived from the spec
   independently" and "written alongside the fix" carry very different evidential weight, and
   nothing else distinguishes them to a later reader.

## Why

Sprint-003's review fixed the Tier-1 predicate, rewrote the reference document's contract table to
describe the fix, and added `_PROFILE_CONTRACT` pinning the same rows — **all on the same day, by
the same chain of reasoning.** Asked to check the test table against the spec, the critic reported
that the check *cannot fail*: the spec had been rewritten to describe the fix, so agreement among
the three was guaranteed by construction rather than being evidence of correctness.

The symptom was immediate. GAP 3 — a 5 m distance authorising a route with no corroboration — was
the one input combination none of the three considered, and it was also **the one row the table
omitted**. Three artifacts, one blind spot, because they shared an author.

This recurred during the 2026-09-06 declaration amendment: a freshly recorded cool-down-walk
fixture was proposed as the regression test for a new gate, and was refused **three times over**
(speed veto, duration gate, and the new rule), so an assertion that it did not route would have
passed with the new rule deleted. It was kept as evidence and explicitly *not* used as the pin.

## How to apply

- This is **not** an argument against contract tables. `_PROFILE_CONTRACT` was the best artifact in
  sprint-003 — it is why GAP 3 was cheap to fix and why the fix is visible in one place.
- Before accepting a fixture as a regression test, ask: **which single rule does this isolate?**
  If more than one rule refuses it, it proves the *assumption* but cannot pin the *mechanism*. Use
  a synthetic contract-table row for the mechanism and keep the real file as evidence.
- The existing **perturbation discipline** is the antidote and should extend to tables: T048, T049,
  T050 and the quarantine guard each demonstrated their tests failing against a deliberately broken
  implementation. A contract table deserves the same treatment — delete the rule, watch the row go
  red.
- Sprint-002's retro produced property testing after the same function was fixed four times;
  sprint-003 produced this after a table agreed with the code it came from. Same lesson, two
  altitudes.

Related: [[discriminators-must-name-their-negative-class]] — which determines what rows the table
should have in the first place.
