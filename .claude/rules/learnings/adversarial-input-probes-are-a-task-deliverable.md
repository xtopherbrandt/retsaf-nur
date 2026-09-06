---
paths: ["**/*"]
---
# The degenerate input set ships with the predicate, not with its review

Learned from F004 sprint-003 (2026-09-06). Origin: `spec/ideas/IDEA-011-adversarial-input-probes-as-a-task-deliverable.md`.

## The rule

Any task whose deliverable is a **classifier, predicate, discriminator, gate, parser, or resource
bound** carries an **adversarial-probe table** alongside its acceptance probe, produced *by the
implementing task*, before it is called done:

1. **Enumerate every input the predicate actually reads.** Not the parameters of the function —
   the fields it consults after everything upstream has transformed them. For each, name its
   degenerate forms: zero, negative, absent, present-but-unparseable, the vendor sentinel, the
   empty collection, the duplicated or repeated message.
2. **Drive the real path with each**, end to end, not the unit in isolation. F004's defects were
   only visible through `mapping.to_canonical` → `classify`, because the bug was in how
   `_build_summary` strips `None` but keeps `0.0`. A unit test of `classify` could not see it.
3. **Report what routed, and argue each result was intended.** The deliverable is a table of *what
   I fed it and what happened* — never the sentence "no issues found". A probe that passed and was
   not defended is not a probe.

The author of the predicate writes this table, and writes it **during** the task, because that is
the only moment the input space is actually known. It is not a review activity, and deferring it
to review changes what gets produced: a reviewer enumerates the inputs the code *appears* to read,
which is the list the author already had.

## Why

Sprint-003's two HIGH defects were both found by **generating inputs**, not by reading code. The
zero-distance false positive was found by calling `classify()` with `distance=0.0` directly, and
its residual by binary-patching a real fixture's session message (`total_distance` → 0,
`avg_heart_rate` → the uint8 invalid sentinel, CRC repaired) and uploading it over HTTP. The
multi-session defect was found by synthesising a two-`session` message list and watching a
two-hour run classify on its first 240-second leg.

Neither is exotic. Both took minutes. Neither was done by the task that shipped the code, by three
parallel Stage-0 scanners, by the spec reviewer, or by the wave gates — all of which read the code
and ran the suite, **which is a different activity**. The 405-test suite was green through every
one of those defects, because the tests exercised the branch beside the bug.

This is **not** the same as property testing (IDEA-005, already in place and used well here).
Properties explore a *generated* space defined by the author's own model of the input domain.
Adversarial probes attack **the boundary of that model** — values the author never classified as
inputs at all. `distance_m = 0.0` was inside hypothesis's reach and outside the author's model,
which is why four property suites did not find it.

## How to apply

- The probe table's home is the task file's **Technical Notes**, as a required section for
  `kind: feature` tasks that gate, route, classify, or bound — the same place acceptance probes
  already live. If the task has shipped a predicate and has no such section, it is not done.
- **"Write more tests" is the wrong reading of this rule.** More tests by the same author from the
  same model produce more coverage of the same region. The deliverable is the *enumeration of
  degenerate forms*; its value is in the inputs it names, not the assertions it adds.
- Enumerate at the **canonical layer**, after mapping and defaulting, because that is where
  absence and zero stop being distinguishable. Ask of every field: *what does this look like when
  the vendor had nothing to say?*
- A probe that routes unexpectedly is a finding even when the behaviour turns out to be correct.
  Record the argument — the next reader will otherwise re-derive the same doubt from scratch.

Related: [[discriminators-must-name-their-negative-class]] — that rule asks whether the predicate
was written against the right world; this one attacks the predicate as written.
[[contract-tables-need-an-independent-oracle]] — probes are worth most when the table they feed did
not come from the same pass as the fix.
[[isolate-the-environment-before-the-first-request]] — probing by driving the real app is exactly
the activity that makes environment isolation load-bearing.
