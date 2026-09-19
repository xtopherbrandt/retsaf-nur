---
paths: ["**/*"]
---
# A sweep proves nothing about an axis it silently fixed

Learned from F005 sprint-005 cycle 10 (2026-09-18). Origin: [[T145]], and
`spec/ideas/IDEA-071-the-tier-rule-is-accumulating-qualifiers-not-gaining-a-model.md`.

Companion to [`sweep-the-claim-not-the-diff`](sweep-the-claim-not-the-diff.md), which governs
**corrections**. This one governs **measurements**.

## The rule

Every sweep, harness or parameter search states **the axes it holds constant**, beside its result.
A result reported without them is a result about one slice presented as a result about the space.

Two specific obligations, both paid for:

- **Vary the axis the feature is actually about**, including on the *other* side of a comparison. A
  sweep that varies one dataset's capture density while the carrier stays daily has fixed the very
  axis under study. Where two populations interact, the sweep is a **cross-product**, not a line.
- **Any walk indexed by elapsed time must say whether it means days elapsed or observations
  captured.** The two are identical only at daily density, which is exactly where the fixtures sit.

## Why

Nine review cycles of F005 ran sweeps over capture *geometry* while **every one of them fixed capture
density**: `T125-fix-form-measurements.md` states its rectangle as a contiguous **daily** return run
in all 2050 rows; [[T130]]'s harness reuses that rectangle; [[T123]]'s generator *requires* a judged
week of ≥ 3 new-tier days, so it cannot contain the sparse geometry by construction; and
`test_hrv_trend_band.py`'s device-return walk indexes by *days since return* while `_seed_return_series`
seeds daily.

The cost: **[[T125]] and [[T132]] — two ratified behaviour changes, three review cycles of work —
change no verdict at all for any sub-daily athlete.** They were measured on everyone except the
athlete the feature's own Negative Class describes as a first-class population ("a strap worn two or
three days a week").

2050 rows had proved nothing about density, and nobody could see it, because no sweep said what it
was holding still.
