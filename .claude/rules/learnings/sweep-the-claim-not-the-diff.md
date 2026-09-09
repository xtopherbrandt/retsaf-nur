---
paths: ["**/*"]
---
# A correction is not done until the claim has been swept tree-wide

Learned from F004 sprint-004 (2026-09-07). Origin: `spec/ideas/IDEA-033-sweep-the-claim-not-the-diff.md`.

## The rule

When you correct or retract a **normative claim** — a rule the code is written against, an
invariant a consumer is told to rely on, a formula, a contract — the edit is not finished until you
have swept the **claim** across the whole tree and dispositioned every hit.

1. **Search the claim's distinctive phrasing, not the file you are editing and not the diff.**
   The diff is the wrong search space: any sibling copy written *earlier* is invisible in it by
   construction.
2. **Search the repo and the Shipyard data dir.** Normative claims live in source docstrings, schema
   comments, test-module headers, feature specs, reference documents, task files, the CHANGELOG, the
   research corpus, and in `.claude/rules/` itself.
3. **Disposition every hit as *live assertion* or *historical record*.** A completed task file, a
   raw `.subagent-returns/` transcript, and an explicitly-superseded Decision Log entry are
   legitimately stale and should be left (mark the supersession rather than rewriting history). A
   docstring, a schema comment, a project rule, or any block that declares itself still in force is
   a live assertion and must be corrected.
4. **Record the patterns you ran** in the commit message, so the next author can re-run them.

## Why

Sprint-004 logged **six** instances of one shape: a claim corrected in one place and left standing
in a sibling. Twice it happened *inside the very commit written to fix the previous instance*.

| # | Claim | Where the survivor was |
|---|---|---|
| 1 | `resting_rmssd_ms` "populated for every successful reading" | 331 lines from its own fix, same file |
| 2 | "`_numeric` itself is unchanged" | `_resting_profile`, 180 lines away |
| 3 | the same claim again | a test module, 270 lines from the test that commit appended |
| 4 | a `nan` "declined *every* veto" | written by the commit correcting the other four copies |
| 5 | "E003 must exclude them explicitly" | the carried-forward hold, 220 lines below the corrected copy |
| 6 | the window holds "every pre-amendment **Tier-1** row" | `db.py`'s schema comment |

Instances 5 and 6 predated the review pass entirely, so no diff-scoped grep could ever have found
them. Where inspection failed four times on the same claim, one tree-wide sweep closed it
immediately.

This is the mechanised form of the older observation that documentation outlives its code. The
earlier framing said *be careful*; this one says *what to type*.

## How to apply

- **Grep is line-oriented, so a wrapped sentence hides from it.** Search a short unwrapped fragment,
  not the full sentence. This produced a false all-clear during sprint-004 and was caught only by
  searching a distinctive two-word phrase instead.
- **Prefer a property to a string.** A sweep that asserts "this exact sentence is gone" passes as
  soon as the sentence is reworded. A sweep that asserts "every document mentioning X also carries
  the corrected form Y" keeps working. Sprint-005's own amendment probe was first written the weak
  way, found 1 hit where the real pattern found 6, and would have gone green with five restatements
  alive.
- **A sweep that only finds what you already knew about is not a sweep.** If your grep returns
  exactly the list you started with, widen it — sprint-005's found a seventh location in
  `research/05` that neither the feature's own reference document nor a dedicated analyst had named.
- **Amending a claim can invert a precedence you did not intend.** If the claim also appears in a
  document this project names as an authority, amending the derived copy alone silently overrides
  the authority. Amend both, and write down whether you are *clarifying* it or *contradicting* it.

Related: [[normative-docs-must-be-checked-against-each-other]] — the observation this mechanises.
[[contract-tables-need-an-independent-oracle]] and
[[adversarial-input-probes-are-a-task-deliverable]] govern how a claim is *established*; this one
governs how a claim is *retracted*.
