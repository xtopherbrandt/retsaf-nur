---
paths: ["**/*"]
---
# Asserting an invariant in four documents is repetition, not evidence

Learned from F004 sprint-004 (2026-09-07). Origin: `spec/ideas/IDEA-034-a-published-invariant-needs-a-test-that-can-break-it.md`.

## The rule

When a task publishes an **invariant that a downstream consumer is told to rely on**, it ships a
test whose failure mode *is the invariant being violated* — not a happy-path test that the invariant
happens to describe.

1. **Name the consumer and the operation the invariant authorises.** "E003 may take
   `ln(resting_rmssd_ms)` unguarded" *is* the test specification.
2. **Enumerate the values that break that operation**, not the values the feature produces: zero,
   negative, `-0.0`, `inf`, `-inf`, `nan`, absent, and the vendor sentinel. For `ln`, most of those
   are undefined or silently poisoning.
3. **Drive each through the real path** and assert either that the invariant holds or that the value
   never lands.
4. **Verify by perturbation.** Remove the guard, watch the test go red, restore it. A guard whose
   failing branch nothing exercises is indistinguishable from no guard.
5. **If the invariant cannot be enforced structurally, say so where it is written**, and prefer a
   constraint at the *write boundary* over one re-derived at each success point.

## Why

`resting_rmssd_ms` was documented as **"always > 0 when set"** in four places — `db.py`'s column
comment, the classifier module docstring, F004's Data Model, and the CHANGELOG — and E003 was told
that guarantee is what authorises an unguarded `ln()`.

**It was false on both tiers, and had been for two sprints.** `inf <= 0` and `nan <= 0` are both
`False`, so a non-finite value cleared the non-positive gate and was stored as a reading with no
quality flag. 858 tests passed throughout. The demo probe passed. Two review passes passed. Nothing
failed, because nothing tested the invariant itself — the tests exercised the values the fixtures
happen to contain, and no fixture contains a non-finite float.

A worse defect hid behind it. Because every comparison against a `nan` is `False`, a `nan`
`avg_heart_rate` silently defeated the one veto arm it appeared in while the file's other arms were
innocently satisfied — so a non-capture routed as a full resting-HRV reading with an empty
`quality_flags`. That is the feature's headline failure mode, reached through the arm that exists to
tell rest from a stationary maximal effort.

Fixing it took **two separate changes on two tiers**, because nothing held the invariant in one
place — which is itself the argument for the write-boundary constraint.

## How to apply

- An invariant and a predicate are different objects. A predicate is a decision to attack with
  inputs ([[adversarial-input-probes-are-a-task-deliverable]]); an invariant is a **promise made to
  a downstream consumer** and must be attacked at the point that publishes it.
- **Restating a claim is not evidence for it.** Four documents asserting the same false thing is
  four copies of one unverified sentence — and, per
  [[sweep-the-claim-not-the-diff]], four places to correct when it turns out wrong.
- **Watch for the guard that cannot fire.** A floor set above the value the formula normally
  produces, or a threshold no realistic input crosses, is a discriminator with no reachable negative
  case. Both branches need a test.
- **Comparisons are not screens.** `x <= 0` rejects negatives; it does not reject "not a number".
  Screen at the documented chokepoint for "is this a real number", and remember that changing a
  shared helper changes every call site — including ones whose ratified behaviour you did not intend
  to touch.

Related: [[review-catches-what-tests-cannot]] — every escaped defect on this project has had the
shape "the tests exercised the branch beside the bug"; this rule is that shape one level up, where
the tests exercised the values beside the invariant.
