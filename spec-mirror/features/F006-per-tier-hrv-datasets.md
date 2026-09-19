---
id: "F006"
title: "Per-Tier Resting-HRV Datasets"
type: feature
epic: "E003"
status: in-progress
story_points: 15
complexity: "high"
token_estimate: 38000
rice_reach: 1
rice_impact: 3
rice_confidence: 0.8
rice_effort: 5.0
rice_score: 0.48
feasibility: 0.8
dependencies: ["F004", "F005"]
references: ["spec/references/F006-dataset-model.md", "spec/references/T125-fix-form-measurements.md", "spec/references/T130-overlap-sweep-harness.py"]
children: []
tasks: ["T124", "T149", "T150", "T151", "T152", "T153", "T154", "T155", "T156", "T157", "T158", "T159", "T160", "T161", "T162", "T163"]
created: 2026-09-18
updated: 2026-09-18
source_idea: "IDEA-071"
---

# Per-Tier Resting-HRV Datasets

## User Story

As an athlete who records resting HRV on more than one source — a chest strap some mornings, the watch on
others — I want each source to keep its own baseline and band, so the verdict I am given is computed against
the instrument that produced this week's readings, not whichever source won a single arbitration.

## Why This Matters

One tier wins the baseline window today and every other tier's readings become `off_baseline_tier`,
discarded for the verdict. Because one tier owns the only band, the rule must answer *"which one band do I
judge this week against?"* — and nine cycles of F005 each added a qualifier to close one population of it
while **no condition was ever removed**; [[T130]] measured that family exhausted over 12,300 rows. When each
source keeps its own band, the **cross-tier** arbitration has nothing left to arbitrate. Fidelity-first is
externally corroborated: ~2.16% rMSSD error for chest straps against ECG versus ~17.49% for PPG, an ~8×
difference.

**What it does not do.** Per-tier baselining does not answer IDEA-071's missing model — *"does this week's
reading represent the athlete now?"* — it only makes it **askable per dataset**. AC6 and AC17 carry that
question; a first draft called them subsumed and was **measurably wrong** (reference §9).

## Acceptance Criteria

**AC1 — each tier keeps its own baseline and band.** *Given* 41 `chest_strap_raw` and 57 `health_snapshot`
distinct local days in `[D-66, D-7]`, *when* the trend is computed for `D`, *then* each tier has its own
baseline mean, SD, `n` and band, and no reading of one tier contributes to another's (§3.7.3 anti-mixing,
honoured by construction).

**AC2 — the losers are retained.** *Given* two tiers each holding at least `MIN_BASELINE_READINGS`, *when*
one is selected, *then* the other's band, `n` and `established` are still computed and emitted.

**AC3 — a morning with two captures feeds both datasets.** *Given* a local day carrying a `chest_strap_raw`
capture at 07:00 and a `health_snapshot` at 07:05, *when* datasets are built, *then* that day contributes
one reading to each and neither is excluded as `off_baseline_tier`.

**AC4 — per-day collapse still applies within a dataset.** *Given* two `chest_strap_raw` captures on one
local day, *then* only the later is kept (`same_day_later_capture`) and the day counts once. Every count
here is in **distinct local days** (T095, IDEA-047).

**AC5 — selection promotes the highest fidelity judgeable dataset.** *Given* `chest_strap_raw` is judgeable
and last read 2 days ago and `health_snapshot` is judgeable, *when* selection runs, *then*
`selected_dataset` is `chest_strap_raw`.

**AC6 — the tolerance gate reads the BASELINE WINDOW.** *Given* a judgeable dataset whose latest reading
**within `[D-66, D-7]`** falls more than `RECENCY_TOLERANCE_DAYS` behind the latest baseline-window reading
of any judgeable dataset, *when* selection runs, *then* it is skipped. **The window is normative**: a strap
established on `D-66…D-36`, silent `D-35…D-5` and back on `D-4/D-2/D-0` **must** be skipped — its baseline
is entirely pre-layoff, and the unqualified reading selects it. Reproducing series and the §1.7 argument:
reference §9.

**AC7 — the gate boundary and its reference set.** *Given* a dataset exactly `RECENCY_TOLERANCE_DAYS`
behind, *then* it is **not** skipped (strictly greater than). The reference maximum is taken **once,
simultaneously, over all judgeable datasets including those about to be skipped** — never iteratively — so a
lone dataset is its own reference and is never skipped.

**AC8 — judgeability is a precondition of candidacy.** *Given* a dataset established but holding only 2
distinct judged-week days, *when* selection runs, *then* it is not a candidate. `established` is counted
over the **post-clip** baseline window (AC17).

**AC9 — no judgeable dataset, and the presentation fallback.** *Given* no dataset is judgeable, *then*
`hrv_status` is `hrv_unavailable` and `selected_dataset` is `null`, **but `baseline` and `band` are still
populated from the dataset the athlete used last** (F005's rule 3, retained) so the non-nullable
`baseline.n`/`window`/`established` carry a value and no contract break occurs. No verdict is conferred by
the fallback. `unavailable_reason` is resolved by a **defined precedence across datasets** and must remain
`week_too_thin` on the illness/holiday week — `judge`'s existing single-series order is undefined when N
datasets satisfy different causes.

**AC10 — disagreement is reported from any dataset with a computable band.** *Given* a dataset whose
baseline holds at least two readings, so a band exists, and whose judged-week mean reads below that band,
*then* it is named in `disagreed_with` — **whether or not it is judgeable**. A dataset with fewer than two
baseline readings has no band, cannot disagree, and is instead visible in `datasets[]` carrying its `n` and
its judged-week count.

**AC11 — disagreement never overrides.** *Given* any number of judgeable datasets disagree with the selected
one in **either** direction, *then* `hrv_status` is the selected dataset's verdict, unchanged. Both
directions are reported.

**AC12 — the contract change is additive, including the null case.** *Given* a consumer reading `baseline`
and `band`, *then* those carry the selected dataset's values; and *when* `selected_dataset` is `null` (AC9),
*then* fields non-nullable in `contracts/openapi.yaml` before this change stay non-nullable, or the contract
records a breaking change with an `info.version` bump.

**AC13 — `selected_reason` is a closed enum.** *Given* `selected_dataset` is non-null, *then*
`selected_reason` is non-null and drawn from a closed, documented enum; null with null (T144).

**AC14 — `points[]` discloses which dataset drew each day.** *Given* a range whose days select different
datasets, *when* the series is rendered, *then* each point names the dataset its band came from — selection
runs per local day (`CRITIC-F005` priority 3).

**AC15 — the `included`/`excluded` partition survives per dataset.** *Given* any stored row in `[D-66, D]`,
*then* it is accounted for exactly once (`research/00` §1.6) — a row feeding a non-selected dataset is
neither dropped nor listed `off_baseline_tier`. Removing that population reds
`test_every_published_exclusion_reason_is_observed_in_a_rendered_response` and
`test_the_schema_names_every_exclusion_reason_and_the_verdict_enum`; both are in scope.

**AC16 — `coverage_gap_reset` stays global.** *Given* one tier goes silent while another carries the series,
*then* **no** coverage-gap reset fires — it measures the silence of the series as a whole, every tier
together, which justifies `RECENCY_TOLERANCE_DAYS` (28) > `GAP_RESET_DAYS` (21). This is **already true** of
shipped code and pinned by `test_a_gap_bridged_by_off_tier_readings_is_not_a_gap`; it is stated so the N-way
partition cannot silently make the clip per-dataset.

**AC17 — the band clip and the reported reset are separated.** *Given* a dataset whose own baseline window
spans an internal capture hole of at least `GAP_RESET_DAYS`, *then* that dataset's band is clipped at the
hole — a **new, unreported** per-dataset clip. *And given* a genuine tier change, *then* `tier_change_reset`
still decides the **reported** `reset_reason`/`reset_on` by asking the existing cross-tier question once per
dataset, with T129's stray population left **globally unclipped**. The two are distinct: `_era_boundary`
requires an old-tier reading followed by a new-tier one, so on a single dataset it returns `None` (measured)
and cannot see an internal hole. **AC6, not this criterion, is what defends the reference §9 series** — for
the dataset that *is* `previous_tier`, clause (b) short-circuits and no clip ever fires.

**AC18 — every retirement carries a three-valued pin.** *Given* a qualifier retired by this feature, *then*
its pin is **green** on shipped F005, **red** on F005 with that qualifier deleted and nothing else changed,
and **green** on F006. Existing pins are **re-pointed, never deleted**.

**AC19 — sweeps name their axes, and density is a cross-product.** *Given* any sweep, *then* it states every
axis it holds constant **including the carrier's**, and capture density is varied on **both** datasets
independently — daily, 4/wk clustered, 4/wk spread, 3/wk, 2/wk ([[T145]]). Any walk indexed by "days since
return" states whether it means **days elapsed or mornings captured**.

**AC20 — the two unpinned populations are pinned first.** *Given* the measurement deliverables, *then*
`switch_away_rows` and `inter_rows` are pinned **before** any candidate form is measured.

**AC21 — no regression against shipped F005.** *Given* every sweep in AC19, *then* it runs against **both**
shipped F005 and F006 with both rates reported side by side, and **any §1.7 rate that worsens blocks
release**.

**AC22 — the §1.7 promotion exposure is measured and priced.** *Given* the AC19 sweeps, *then* the rate at
which `hrv_normal` is promoted while another judgeable dataset reads below its own band is measured,
compared against F005 per AC21, and recorded in the Negative Class with its direction.

**AC23 — flip rate is scored against a criterion it can fail.** *Given* the selection form, *then* its
dataset-flip rate per athlete-year is measured across the AC19 sweeps and compared against F005 per AC21;
**a worse rate triggers the deferred hysteresis decision**.

**AC24 — the withhold is retained at dataset scope.** *Given* a dataset that is **not** judgeable but holds
at least `MIN_WINDOW_READINGS` judged-week days, every one later than every judged-week day of the selected
dataset, *then* `hrv_status` is `hrv_unavailable` and no verdict is promoted. This is T125/T132's
`verdict_withheld`, kept rather than retired: without it a brand-new device (zero baseline days, so never
judgeable) leaves the outgoing dataset selected and promotes `hrv_normal` on its stale week — the sixth
§1.7-forbidden population, which shipped F005 closes and AC21 therefore forbids regressing.

## Interface

`GET /metrics/hrv` (`operationId: getHrvTrend`, schema `HrvTrend`) gains `datasets[]`,
`selected_dataset`, `selected_reason`, `disagreed_with`; `points[]` gains per-point dataset identity
(AC14); `included`/`excluded` become per-dataset (AC15). `contracts/openapi.yaml` moves in the same
change set, `check_drift.py` must pass, and `test_hrv_unavailable_causes.py` — an **AST oracle over
`judge`'s source**, with §3.7.4 asserting six causes — reds on any reshaping of `judge`.

## Technical Notes

**Read `spec/references/F006-dataset-model.md` §9, §10 and §13 before implementing.** §13 holds the four
notes that change what you build: the dataset key and why it is the tier; N is 3 in the enum and 2 in every
real corpus; `RECENCY_TOLERANCE_DAYS` is inherited but **not** re-justified in this frame; and fidelity rank
arbitrates while the confidence weight never does — pinned by nothing today. `research/00` §5.4 is amended
**first**, then swept tree-wide.

## Negative Class

Full table in reference §11; the governing row:

| cost | direction and why it is accepted |
|---|---|
| **The §1.7 promotion exposure** — the selected dataset decides, so `hrv_normal` can be promoted while another judgeable dataset reads below its own band, and a consumer reading `hrv_status` alone (every consumer today, and Section 6 as specified) is not told about `disagreed_with` | **Up-regulation while contrary evidence exists — the direction §1.7 forbids.** Accepted because quality-first promotes the *best available* instrument (~8× lower rMSSD error), and suppressed-wins lets a noisier dataset veto a good week. **Newly measurable** — under the fused rule the losing tier had no band. AC21/AC22 gate it: any worsening against F005 blocks release |

Carried forward (§11): same-tier replacement invisible; §3.7.3's device/firmware clause
unimplemented; the 18-day adoption silence and its wrong `week_too_thin` reason; the 3×/week
seven-day flip.

**New, from AC17 (T153):** a dataset's internal hole of **at most** `GAP_RESET_DAYS` silent local
days (21, the constant's own boundary: 22 clips, 21 does not, as `coverage_gap_reset` counts) is not
clipped, so its band still mixes the two eras either side of it — measured: 21 silent days between a
60 ms and a 40 ms era give a band mean of 3.97, neither `ln 60` nor `ln 40`. And a hole whose
resumption lies **after** `D-7` is not internal to the baseline window and is not clipped either:
the band is the pre-layoff era's, and whether it is judged is AC6's (a strap last read `D-30` beside
a snapshot read `D-7` is 23 days behind, inside `RECENCY_TOLERANCE_DAYS`, and is selected on a band
30..66 days old). Both directions are unreported. Who notices: nobody from the response —
`reset_reason` is null in every case; the first is visible only in `baseline.window` staying `[D-66, D-7]`,
the second not at all until `datasets[]` (T159) shows the dataset's latest baseline-window
day. Cost: the first mixes eras (either direction, bounded by three weeks of silence); the second is
up-regulation on a stale band when the layoff crosses `D-7`, the AC6 boundary IDEA-080 names.

- **The withhold is retained, not retired (T158, AC24).** T125/T132's `verdict_withheld` stays,
  restated at dataset scope: a dataset that could not be selected — not judgeable, or skipped by the
  recency gate — holding >= `MIN_WINDOW_READINGS` judged-week days every one later than the selected
  dataset's withholds the verdict (`hrv_unavailable`, `week_not_representative`). Without it a
  brand-new device (zero baseline days) leaves the outgoing dataset selected and promotes
  `hrv_normal` on its stale week — the sixth §1.7-forbidden population, which shipped F005 closes.
  **What widened, priced:** T132's form B touched only a zero-baseline-day tier; at dataset scope a
  dataset with 1..13 baseline days and a full later week withholds too — `hrv_normal ->
  hrv_unavailable`, the freely tolerated direction, measured as one row of T130's matched table (era
  10: shipped `TP 16 / FN 1` at `c = 0` -> `TP 17 / FN 0`) and no other verdict in the HRV suites.
  **Carried unchanged:** T130's carrier-overlap disarm (one carrier morning inside the return and
  the clause is false; `0 of 17` at every `c >= 1`), pinned as the current fact and swept by
  T161/T162; T125's `min(WINDOW_DAYS - MIN_WINDOW_READINGS, k3)` opening-mornings residual; a
  judgeable, unskipped lower-fidelity dataset never withholds (it could have been selected; the
  selected dataset decides and it is named in `disagreed_with`).
- **The honest retirement count (task Technical Notes).** What this feature retires is
  `resolve_baseline_tier`'s role as "one tier owns the only band" and the `off_baseline_tier`
  exclusion (T152), i.e. the cross-tier *arbitration*. Everything else on IDEA-071's list is kept or
  redeployed: T094/T095/T129 re-derived at dataset scope (AC17), T106 kept as a sub-mechanism of the
  era clip, T107 and T116 retained, T093's fallback retained as AC9's presentation fallback (T156),
  T117 redeployed as AC6's gate, **T125 and T132 retained here**. "Ten qualifiers retired" is not
  the number; two mechanisms are.
- **The reported reset is per dataset, and only the selected dataset's is presented (AC17, T154).**
  `tier_change_reset` is asked once per dataset with that dataset's own tier over the same cross-tier
  populations (every tier's unclipped readings in `[D-66, D]` for the stray count, T129 kept global),
  so a dataset that is not selected can carry a reported `tier_change` the response does not show
  until `datasets[]` (T159) — measured: a strap era that cleanly follows an overnight era reports
  `(D-52, tier_change)` while the selected snapshot reports nothing, and `reset_reason` is null.
  Accepted because the report describes the clip of the band it sits beside and no other. Who
  notices: nobody from `hrv_status` or `reset_reason`; `datasets[]` (T159) shows it. And the mirror
  residual: for the dataset that *is* `previous_tier`, clause (b) short-circuits, so an outgoing
  dataset never reports anything however the incoming one was adopted (the task's "known" case) —
  AC6, not this rule, decides whether it is judged.
- **T093's row retires in two halves, honestly (T156).** T093 was one row for two mechanisms. Its
  *week-coverage half* — "the baseline tier must cover the judged week", the gate that kept a
  14-in-60 trial from owning a baseline it could never judge — is **subsumed by judgeability** (AC8):
  a dataset is a candidate only when established and holding >= `min_window_readings` judged-week
  days, so the population the gate closed cannot be selected, and its pins are re-pointed at
  `is_judgeable` (`test_a_thin_tier_that_alone_covers_the_week_does_not_take_the_baseline`, green
  unmoved). Its *rule-3 half* — "when no candidate covers the week, the tier the athlete was read on
  last holds it" — is **retained, not retired**, as AC9's presentation fallback: with nothing
  judgeable the response still carries `baseline`/`band` from the established dataset read last (ties
  `n` then fidelity; else densest by `n`; else densest in the week), so the non-nullable contract
  fields hold a value and the three illness-week pins stay green. What the fallback costs, in the
  tolerated direction: it presents, it never judges — `hrv_unavailable` with the presented dataset's
  own cause, no dissenter named — so an athlete whose new device already holds a full unestablished
  week beside a silent established strap is told `week_too_thin` on the strap's `n`, the T138 18-day
  adoption silence, unchanged and still carried above. Who notices: nobody from `hrv_status`;
  `datasets[]` (T159) shows the covering dataset with its `n` and week count.

## Decision Log

All 2026-09-18 (`/ship-discuss IDEA-071`, then sprint-006 planning); full text in reference §12/§14.

- Dataset key is the **tier**, not the device; a morning with two captures **feeds both datasets**.
- **The selected dataset decides**; disagreement reported, never overriding — which put the whole §1.7
  exposure into selection, and is why the selection form was re-opened rather than left open.
- Selection is **quality-first with a recency tolerance gate**, over recency × quality, recency-only and
  coverage-first (the last two invert §3.7.1 precedence).
- **Hysteresis deferred** with a trigger it can fire (AC23); **retirement needs a three-valued pin**;
  **adoption silence out of scope**.
- **Critic finding, fixed: AC6 had no window and re-created T125** — worse than shipped F005 on a reproduced
  series. AC6 names the baseline window (§9).
- **`coverage_gap_reset` stays global** (AC16) — already true of shipped code; stated so the N-way partition
  cannot silently make it per-dataset. **Sensor identity split to F007.**
- **Planning corrections (§14):** AC17 split into an unreported per-dataset band clip plus the existing
  cross-tier reported reset, after `_era_boundary` was measured to return `None` on one tier; AC9 keeps a
  presentation fallback; AC10 taken literally.
