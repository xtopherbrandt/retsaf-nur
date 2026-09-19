---
paths: ["**/*"]
---
# Deleting a rule needs a test that goes red without it

Learned from F005/F006 sprint-005→006 (2026-09-18). Origin:
`spec/ideas/IDEA-071-the-tier-rule-is-accumulating-qualifiers-not-gaining-a-model.md`.

This is the deletion-side counterpart of
[`a-published-invariant-needs-a-test-that-can-break-it`](a-published-invariant-needs-a-test-that-can-break-it.md).
That rule governs asserting an invariant. This one governs **removing** one.

## The rule

A behaviour ratified on measured evidence may only be retired when a pin exists that is:

| | state | must be |
|---|---|---|
| 1 | on the shipped code, qualifier **present** | **green** |
| 2 | on the shipped code, qualifier **deleted and nothing else changed** | **red** |
| 3 | on the replacement | **green** |

**State 2 is the whole rule.** States 1 and 3 are what any feature-addition pin already gives you,
and a pin that has only those two proves nothing about the retirement: a test over the population a
qualifier was added to close is green on the shipped code *by construction*, because the shipped code
contains the qualifier.

The retired qualifier's existing pins are **re-pointed at the new mechanism, never deleted**. A
deleted pin is indistinguishable from a pin that never existed.

## Why

F006's first draft specified a two-valued pin — red on the pre-feature model, green on the post — and
it was **unsatisfiable**. Nothing would have satisfied it, so in practice it would have been met by
pins that tested something else.

Worse, two of the qualifiers queued for retirement had already been *measured* as near-vacuous:
T117's form 1 strikes a set disjoint from what the selection loop returns, and [[T145]] found T125
and T132 changed **no verdict at all** at sub-daily capture density. Neither observation licenses
deletion. "Inert on the corpora we have" is not "inert" — it is the [[T130]] lesson: **a green suite
is not evidence about a change until the suite contains the population the change could break.**

## Applies to

Any deletion of a behaviour that a spec, a decision log or `research/00` records as ratified —
including behaviours a reframe claims to have "subsumed". *Subsumed* is a hypothesis; state 2 is
how it gets tested.
