---
paths: ["**/*"]
---
# A discriminator spec must name its negative class

Learned from F004 sprint-003 (2026-09-06). Origin: `spec/ideas/IDEA-012-name-the-negative-class-in-the-spec.md`.

## The rule

Any spec section defining a **discriminator, router, gate, or classification rule** must carry a
**Negative Class** subsection stating four things:

1. **What must be rejected**, enumerated. Not "everything else".
2. **The population between accept and reject**, explicitly. This is the load-bearing part. If the
   answer is "there isn't one", say so and defend it — that claim is falsifiable and someone will
   check it.
3. **What signal separates the ambiguous population**, or an explicit statement that the spec
   accepts the ambiguity, and why.
4. **The cost of each error direction**, and for each, *who notices*. An error nobody can observe
   is a different kind of problem from one that surfaces immediately, and the difference belongs
   in the spec rather than in someone's head.

## Why

F004's Tier-1 spec described what a resting-HRV capture **is** and named exactly one thing it must
**not** be: an ordinary training run. That is a two-class model — {supine capture, maximal effort}
— and everything downstream inherited it. Every counterexample anyone reasoned about across
planning, six waves, three Stage-0 scanners, spec review and goal verification was a *hard* effort:
an erg piece at 180 bpm, a GPS-less FTP test, a 52-minute run.

The real world had a third class sitting **between** them: short *easy* activity at 60–100 bpm —
cool-downs, warm-ups, aborted starts, stretching, gear tests. It fell entirely inside the accept
region, it is a *larger* population than the hard-and-short class, and it appeared nowhere in the
feature file, the reference document, the Decision Log or any task. A real cool-down walk was later
measured at **avg HR 99** — one bpm under the ceiling.

**This was a framing failure, not a thoroughness failure.** More reviewers would not have found it;
every one was checking the predicate against the two classes the spec named.

## How to apply

- Write the Negative Class subsection **before** the acceptance criteria, not after. Criteria
  written first will enumerate the classes you already thought of.
- Gherkin cannot express this. It is a positive-example format — "Given X, When Y, Then Z" — and a
  format built from examples cannot say "and nothing in the gap between these examples". F004 had
  nine passing scenarios and the gap was still there. **The gap has to be named in prose,
  deliberately.**
- Treat "who notices?" as the highest-value column. F004's false positive silently poisoned the
  readiness trend *and* removed a session from training load; its false negative silently lost a
  reading. Both invisible — which is exactly why they needed writing down, and what eventually
  motivated a provenance note to make one of them observable.
- When the answer to (3) is "the user tells us", prefer that to any proxy. F004 ultimately replaced
  intent *inference* with an explicit athlete *declaration*, and the negative class collapsed from
  a list of physiological classes to "everything not declared".

Related: [[contract-tables-need-an-independent-oracle]].
