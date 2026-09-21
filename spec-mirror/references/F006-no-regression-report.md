---
task_id: T162
re_measured_by: T164
re_measured_at: 2026-09-20T18:30:00Z
completed_at: 2026-09-19T23:55:00Z
sources_consulted:
  - spec/tasks/T162-the-no-regression-gate-against-shipped-f005.md (including "Added in planning")
  - spec/references/F006-sweep-findings.md (T161: all eight findings, the row identity tuple, the 35 CSV columns, T161_MODULE / T161_OVERLAP)
  - spec/features/F006-per-tier-hrv-datasets.md (AC6, AC7, AC10, AC21, AC22, AC23, AC24; Negative Class through wave 7)
  - spec/references/F006-dataset-model.md (§9, §10, §11)
  - specification/research/00-design-decisions.md §1.7 and §5.4 (the 2026-09-18 amendment, clauses (i)-(vi))
  - spec/tasks/T150-pin-switch-away-and-inter-rows.md (the shipped-F005 baseline pins), T130-*.md (the carrier-overlap question), T158-*.md (the retained order clause)
  - spec/ideas/IDEA-080-a-stopped-carrier-leaves-the-recency-gate-with-no-reference.md; IDEA-084-the-hole-clip-makes-a-return-after-a-layoff-longer-than-gap-reset-days-not-free.md; IDEA-082 (dissent under the fallback)
  - .claude/rules/learnings/a-published-invariant-needs-a-test-that-can-break-it.md, a-sweep-must-name-the-axes-it-holds-constant.md, sweep-the-claim-not-the-diff.md, retiring-a-ratified-behaviour-needs-a-three-valued-pin.md; memory: a-witness-must-print-the-slice-it-compared, measurement-sweeps-silently-fix-an-axis, pytest-k-deselection-exits-zero
  - runcoach-api/tests/test_hrv_dataset_populations.py (T150's shipped-F005 pins), test_hrv_trend_band.py (RETURN_DENSITIES, _seed_return_series), test_normative_mirror.py
  - runcoach-api/src/runcoach_api/metrics/hrv_trend.py at 99a5755 (F006); git show 42f7705:.../hrv_trend.py (shipped F005)
  - runcoach-api/tests/test_fixture_corpus.py and the six recorded FIT fixtures (the independence question)
harness: spec/references/T130-overlap-sweep-harness.py (T162 section, `t162-check` / `t162-gate`; additive -- T130's and T161's functions and signatures are untouched; unchanged by T164, which re-ran it verbatim)
rows: spec/references/T162-no-regression-rows.csv (17,070 paired comparison rows; committed copy at runcoach-api/tests/data/T162-no-regression-rows.csv)
reproduce: "cd C:/xtopher/code/retsaf-nur && H=.shipyard/spec/references/T130-overlap-sweep-harness.py && git show 42f7705:runcoach-api/src/runcoach_api/metrics/hrv_trend.py > /tmp/f005.py && uv run --package runcoach-api python $H t162-check --f005 /tmp/f005.py && uv run --package runcoach-api python $H t162-gate --f005 /tmp/f005.py --procs 16 --rows .shipyard/spec/references/T162-no-regression-rows.csv --rowdir /tmp/t162rows   # ~13 min, 1,353,000 judgings; both modules x both overlap variants are inside this one command. The single-variant T161 commands still work unchanged: T161_MODULE=/tmp/f005.py uv run --package runcoach-api python $H t161-rect --procs 12 --csv <out> and T161_OVERLAP=suppressed ... t161-walk ..."
---

# T162 — every T161 sweep on shipped F005 as well as F006, and the §1.7 rates that decide release

> **Re-measured 2026-09-20 by [[T164]], after the fix this report recommended.** AC7's recency
> reference is now taken over every **established** dataset rather than the judgeable ones alone
> (`research/00` §5.4 amended first, then spec §3.7.3/§3.7.4, then AC6/AC7). Every table below was
> re-run by the same `t162-gate` command on the same shipped-F005 module at `42f7705`, and the
> numbers in it are **the re-measured ones** unless a line says otherwise. What changed, in one
> line: **the stale-band regression is gone — F006 now meets shipped F005 exactly, 1,896 of 307,500
> and 96 of 24,000, cell for cell, at every `c`, under both overlap variants** — and the
> forbidden-rate finding is **bit-for-bit unchanged**, so it is now carried as the release gate's one
> named, counted exception (`DEFERRED_EXCEPTION` in
> `runcoach-api/tests/test_hrv_no_regression_gate.py`) pending [[IDEA-087]]. Worsened gated rows:
> **548 → 64**, all 64 the deferred exception, 0 unexcused. Where a figure is T162's original and was
> not re-derived, it is marked *(T162, not re-derived)*.

## TL;DR

**As measured 2026-09-19, before T164, F006 was measurably worse than shipped F005 on two §1.7 rates and
AC21 blocked release. One of the two is now paid in full; the other is deferred, and it is still real:**

**REGRESSION: the §1.7-forbidden rate — a suppressed return promoted `hrv_normal` from the carrier's
week — remains worse on F006 at `c = 4` and `c = 5` under the independent-instruments fixture, 22,217 →
22,232 of 307,500 rectangle rows and 1,104 → 1,108 of 24,000 walk rows, on 17 of 150 cells; it is
DEFERRED rather than paid (user decision, 2026-09-20), because it reverses under the correlated fixture
and [[IDEA-087]] cannot yet say which world this is, and it is carried as the release gate's one named,
counted and conditioned exception (64 gated rows) rather than by softening the gate.**
Over 1,353,000 judgings — T125's 2050-row return rectangle × 25 capture-density pairs × carrier overlap
`c = 0..5` (307,500 rows), the 40-morning device-return walk in both orientations (24,000), T150's matched
pairs (5,100) and switch-away rows (1,400), each run **twice over**, once on today's F006 at `99a5755` and
once on shipped F005 at `42f7705`, under **both** overlap variants — 548 of 8,696 gated comparison rows are
worse on F006. Two families carry them.

1. ~~**`hrv_normal` on an entirely pre-layoff band (IDEA-080's shape) nearly doubles: 1,896 → 3,705 of
   307,500 (0.62% → 1.20%, ×1.95) on the rectangle and 96 → 254 of 24,000 (×2.65) on the walk, worse at
   every `c`, on 82 of 150 rectangle cells, and identically under both overlap variants.**~~
   **PAID by [[T164]], 2026-09-20: 1,896 → 1,896 and 96 → 96, identical to shipped F005 on every one
   of the 150 rectangle cells and every walk cell, in both overlap variants.** The mechanism is
   AC7, not AC6: F005's recency gate took its reference set over every **established** tier, so a carrier
   that had stopped (or was too sparse to hold three judged-week days) still struck the returning strap;
   F006 took it over the **judgeable** datasets only, so that carrier left the reference set, the
   returning strap became its own reference, and it was selected on a band 36–66 days old. `research/00`
   §5.4 (iv) calls that return "free"; §1.7 calls promoting `hrv_normal` on it up-regulation on evidence
   that is not current, and AC6's own normative text says such a dataset "**must** be skipped". **T164
   widened the reference population back to every established dataset** — the gate, its constant, its
   baseline-window scope and its once-and-simultaneously maximum all untouched — and the rate returned to
   F005's exactly. See Finding 3.
2. **T161's Finding 3 candidate is confirmed on the rectangle, and it is conditional on the two instruments
   disagreeing. Re-measured 2026-09-20: every figure in this item is unchanged to the row by T164's fix,
   which is the evidence that the two regressions have different mechanisms. It is now the release gate's
   one deferred exception (64 gated rows, all `overlap = healthy`), not paid, pending [[IDEA-087]].** With the carrier's overlap mornings healthy (T130's fixture), the §1.7-forbidden count —
   a suppressed return promoted `hrv_normal` — is worse at `c = 4` (4,880 → 4,922) and `c = 5` (5,621 →
   5,722) on 17 cells, every one a `daily`/`4wk-*` return against a `daily`/`4wk-*` carrier, and better at
   `c ≤ 2` (1,713 → 1,614 at `c = 0`); the net over the whole rectangle is **+15 rows (+0.07% relative)**.
   With the overlap mornings suppressed — one athlete, one physiology, two devices — **F006 is better
   everywhere**: 3,625 → 3,497, and the T130-comparable subset (the athlete holds ≥ 3 suppressed mornings
   of his own) goes 128 → **0**.

**The two rates that are not worse.** AC22's §1.7 promotion rate — `hrv_normal` promoted while another
dataset reads the other side of its own band — is **better** in total (24,510 → **24,099** of 307,500
literal; 5,415 → 5,287 conditioned on the dissenter holding ≥ `MIN_WINDOW_READINGS` judged-week days;
1,837 → 1,837 conditioned on the dissenter being judgeable). AC23's dataset-flip rate is **better by
49%**: **18.47 → 9.36 flips per athlete-year**, so the deferred hysteresis decision is **not** triggered.
*(Both figures are T164's re-measurement; T162 measured 24,422 and 13.10 on the same rows before the
reference-set change. Both moved in the improving direction, and neither is gated in the worsening one.)*

**And the answer to whether any of this measures two instruments.** The recorded corpus can establish that
the watch is reading the strap over ANT+ on a strap morning, that the Health Snapshot stores **no beats at
all**, and that the two numbers are **not** the same number (2026-09-06: snapshot 90 ms against the
project's own arithmetic over the strap's beats, 41.52 ms, 20 minutes apart). It **cannot** establish
whether they are the same beats post-processed, because no simultaneous pair exists or can exist on one
watch, and n = 2. Every row of the `healthy`-overlap tables above is priced on the assumption that they are
independent; every row of the `suppressed` tables on the assumption that they are not; and the release
decision falls on different sides of that assumption for rate 2. That question is now on the critical path.

## Context

AC21: "*Given* every sweep in AC19, *then* it runs against **both** shipped F005 and F006 with both rates
reported side by side, and **any §1.7 rate that worsens blocks release**." T161 measured F006 alone and
closed with one walk against F005, rating its own confidence "medium on the F005 comparison, which is one
walk and not the rectangle (T162)". This document is the rectangle, on both modules, under both overlap
variants, with the comparison emitted as rows a test can fail on.

**Nothing here was a fix.** `kind: research`; no behaviour under `runcoach-api/src/` was changed by T162.
What landed was the harness extension, the comparison rows, this report and the gate
(`runcoach-api/tests/test_hrv_no_regression_gate.py`), which was **red on the measured rows** — that was
the release block, not a defect of the test. **[[T164]] is the fix**, taken as a mid-sprint patch task by
user decision of 2026-09-20: it changes one population inside `select_dataset` and `build_series`, re-runs
this whole sweep, regenerates the rows, and rewrites the numbers below. The gate is now **green**, with
the second regression carried as an explicit exception rather than by softening the predicate.

**Verdict vocabulary.** T161's, unchanged, so the two documents' numbers are comparable row for row.
*Forbidden* = the athlete's own return is suppressed (25 ms) and `hrv_normal` is promoted either from the
carrier's week (`via carrier_week`, T125's shape) or from a band every reading of which predates the layoff
(`via stale_band`; structurally 0 on a suppressed return). A promotion on the returning dataset's own
**rebuilt** band is not counted forbidden. *Normal on a stale band* = `hrv_normal` on a healthy return from
an entirely pre-layoff band (IDEA-080's shape). *Decisive* withhold = the response's reason is
`week_not_representative`, as opposed to a flag `week_too_thin` precedes. *Disarmed* = the row's `c = 0`
twin had the clause armed and this row does not.

**The comparison is paired.** Both modules judge **the same fixture rows**, so `f005` and `f006` share a
denominator on every row of `T162-no-regression-rows.csv` and "worse" is simply `f006 > f005`. A separate
assertion (`test_the_paired_comparison_is_actually_paired`) fails if the two ever judged different
populations.

## Axes held constant

### Common to every sweep

| axis | value |
|---|---|
| modules | **F006** = the installed `runcoach_api.metrics.hrv_trend` at `99a5755`; **F005** = `git show 42f7705:runcoach-api/src/runcoach_api/metrics/hrv_trend.py`, loaded as a copy through the harness's `load_copy`/`_module_from_path` |
| module drift since T161 | none behavioural: `5b3415c..99a5755` touches `hrv_trend.py` once (`0ec5e21`, T163) and only inside a docstring; every F006 number below that T161 also reports reproduces exactly |
| `RECENCY_TOLERANCE_DAYS` | 28 on both modules, each module's own value, unmodified |
| `MIN_BASELINE_READINGS` / `MIN_WINDOW_READINGS` / `WINDOW_DAYS` / `GAP_RESET_DAYS` | 14 / 3 / 7 / 21 on both modules |
| overlap variants | `healthy` (T130's fixture: the carrier keeps its 38/44 alternation on a morning the athlete's strap reads 25) and `suppressed` (the overlap mornings read 25 ms too) — **both run in full** |
| zone | `Pacific/Auckland` |
| seed | none; every generator is a deterministic function of its named parameters, so two runs produce identical rows |
| N | 2 (`chest_strap_raw` + `health_snapshot`). The 3-dataset shape is untested by construction (reference §10) and this sweep does not reach it |
| judging seam | `build_series → selected_view → judge` on F006; `build_series → judge` on F005, whose `build_series` already returns the one resolved-tier series |

### Sweep (a) — the return rectangle, `t162-gate`'s `rect`

| axis | value |
|---|---|
| target `D` | 2026-09-12 |
| era A (returning dataset, `chest_strap_raw`) | 80 days ending `D-(q+s+1)`, 38/44 ms alternating **over the mornings captured**, local 06:00, pattern anchored at its **last** day so `s` is exact in days elapsed |
| silence `s` | 29..69 days elapsed (41 values, step 1) |
| return | `D-q .. D`, `q = 0..24`, anchored at `D-q`; 25 ms when suppressed, else 38/44 |
| carrier (`health_snapshot`) | the day after era A to `D-q-1+c`, 38/44 ms, local 07:00, anchored at its **first** day |
| carrier overlap `c` | 0, 1, 2, 3, 4, 5 carrier **days elapsed** past `D-q-1` (mornings captured reported as `c_cap`) |
| densities | returning dataset (era A **and** return) × carrier, each of `daily` (0-6), `4wk-clustered` (0,1,2,4), `4wk-spread` (0,2,4,6), `3wk` (0,2,4), `2wk` (0,3) — `test_hrv_trend_band.RETURN_DENSITIES` verbatim; 25 pairs |
| value levels | suppressed and healthy return, both |
| rows | 2050 per (pair, `c`) → 307,500 per (module, overlap); **1,230,000 rectangle judgings in all** |

### Sweep (b)/(c) — the device-return walk, `t162-gate`'s `walk`

| axis | value |
|---|---|
| eras | home 80 days ending 2026-07-31 (06:00); carrier 2026-08-01 .. 2026-09-08 **+ `c`** (07:00); return from 2026-09-09 — `test_hrv_trend_band._seed_return_series`'s constants |
| `r` | 1..40 **days elapsed** since 2026-09-09 (target = 2026-09-08 + `r`); mornings captured reported per row as `ret_week` |
| `c` | 0..5; `c = 0` is the walk's own **stopped** carrier, IDEA-080's geometry |
| orientation | `strap-returns` and `snapshot-returns`, both |
| densities / values | as the rectangle; 25 pairs, both value levels |
| rows | 24,000 per (module, overlap); **96,000 walk judgings** |

### Sweep (d) — T150's two pinned populations

| axis | `inter_rows` | `switch_away_rows` |
|---|---|---|
| target | `D` = 2026-09-12 | `SWITCH_BASE` = 2026-09-07 |
| the judged week | **the population's definition, held constant**: resumed `D-2, D-1, D` / abandoned `D-6, D-4, D-2` at 25 ms | strays `(6,5,4) (5,4,3) (4,3,2) (3,2,1) (2,1,0) (6,3,0) (2,1,0,3)` at 40 ms, held constant |
| densities | era density × carrier density (25 pairs) | old-era density × new-era density (25 pairs) |
| `c` | 0..5 (resumed only) | not an axis |
| rows | 5,100 per (module, overlap) | 1,400 per (module, overlap) |

### What this sweep does **not** vary, stated so it is not read as settled

`RECENCY_TOLERANCE_DAYS` (T161 swept 14..44 and left it at 28; here it is fixed at the module value on both
sides, so nothing below prices it); the era-A **length** (80 days in the rectangle, 10..90 in the matched
pairs only); the 38/44 alternation itself, which is what gives a one- or two-reading judged week a mean
below its own band and so dominates AC22's literal rate (Finding 4); the number of datasets (N = 2); and the
zone. A real corpus is not swept at all — the fixtures are synthetic, which is the whole of Finding 6.

## The three rates, side by side

Rates are over the whole swept population of each sweep; `worse` marks a rate on which F006 exceeds F005.

| rate | sweep | overlap | shipped F005 | today's F006 | |
|---|---|---|---|---|---|
| **§1.7 promotion rate** (forbidden: suppressed return, `hrv_normal` promoted) | rect | healthy | 22,217 / 307,500 = **7.225%** | 22,232 / 307,500 = **7.230%** | **worse (+15) — DEFERRED** |
| | rect | suppressed | 3,625 / 307,500 = **1.179%** | 3,497 / 307,500 = **1.137%** | better |
| | walk | healthy | 1,104 / 24,000 = **4.600%** | 1,108 / 24,000 = **4.617%** | **worse (+4) — DEFERRED** |
| | walk | suppressed | 78 / 24,000 = **0.325%** | 78 / 24,000 = **0.325%** | equal |
| | inter | both | 1,439 / 5,100 = **28.22%** | 1,275 / 5,100 = **25.00%** | better |
| | switch | both | 126 / 1,400 = **9.00%** | 126 / 1,400 = **9.00%** | equal |
| **§1.7 promotion on a stale band** (`hrv_normal` on an entirely pre-layoff band) | rect | healthy | 1,896 / 307,500 = **0.617%** | **1,896 / 307,500 = 0.617%** | **equal — PAID (T164)** |
| | rect | suppressed | 1,896 / 307,500 = **0.617%** | **1,896 / 307,500 = 0.617%** | **equal — PAID (T164)** |
| | walk | both | 96 / 24,000 = **0.400%** | **96 / 24,000 = 0.400%** | **equal — PAID (T164)** |
| **AC22 exposure, literal** (`hrv_normal` with a dissenter the other side of its own band) | rect | healthy | 24,510 / 307,500 = **7.971%** | 24,099 / 307,500 = **7.837%** | better |
| | rect | suppressed | 10,334 / 307,500 = **3.361%** | 9,923 / 307,500 = **3.227%** | better |
| **AC22 exposure, conditioned** (dissenter holds ≥ `MIN_WINDOW_READINGS` judged-week days) | rect | healthy | 5,415 / 307,500 = **1.761%** | 5,287 / 307,500 = **1.719%** | better |
| | rect | suppressed | 189 / 307,500 = **0.061%** | 61 / 307,500 = **0.020%** | better |
| **AC22 exposure, conditioned** (dissenter *judgeable* — AC22's own word) | rect | healthy | 1,837 / 307,500 = **0.597%** | 1,837 / 307,500 = **0.597%** | identical |
| **AC23 dataset-flip rate** | walk | both | **18.47 per athlete-year** | **9.36 per athlete-year** | better (−49%) |

**Every F006 column above is T164's re-measurement of 2026-09-20.** For comparison, the pre-T164 F006
column read: stale band 3,705 / 3,705 / 254; AC22 literal 24,422 / 10,246; flip rate 13.10. The
forbidden-rate row is identical before and after, to the row — which is why one of these two regressions
could be paid without touching the other.

### Finding 1 (deliverable 1 and 3): the §1.7 promotion rate, per `c`, on both modules

**Claim.** F006's §1.7 promotion rate is **better than F005's for `c ≤ 2`, equal at `c = 3`, and worse at
`c = 4` and `c = 5`**, on the healthy-overlap rectangle; the crossover is exactly where T153's hole clip
keeps the returning dataset unestablished past the seventh morning while the overlapping carrier is still
judgeable. On the suppressed-overlap rectangle F006 is better or equal at every `c`.

**Evidence.** Rectangle, all 25 density pairs, 51,250 rows per `c` (F005 → F006):

| `c` | rows | forbidden, healthy | forbidden with `ret_week ≥ 3`, healthy | forbidden, suppressed | forbidden with `ret_week ≥ 3`, suppressed |
|---|---|---|---|---|---|
| 0 | 51,250 | 1,713 → 1,614 | 99 → 0 | 1,713 → 1,614 | 99 → 0 |
| 1 | 51,250 | 2,549 → 2,525 | 474 → 450 | 589 → 565 | 24 → 0 |
| 2 | 51,250 | 3,338 → 3,333 | 901 → 896 | 348 → 343 | 5 → 0 |
| 3 | 51,250 | 4,116 → 4,116 | 1,428 → 1,428 | 325 → 325 | 0 → 0 |
| 4 | 51,250 | **4,880 → 4,922** | **1,998 → 2,040** | 325 → 325 | 0 → 0 |
| 5 | 51,250 | **5,621 → 5,722** | **2,579 → 2,680** | 325 → 325 | 0 → 0 |
| **total** | **307,500** | **22,217 → 22,232** | **7,479 → 7,494** | **3,625 → 3,497** | **128 → 0** |

Every forbidden row on both modules is `via carrier_week`; `via stale_band` is 0 on both, at every `c`, in
both overlap variants (a 25 ms week reads below any 38/44 band, so a suppressed return cannot be promoted on
its own pre-layoff band — the two exposures are disjoint by construction, not by rule).

The 17 worsened cells are all at `c = 4, 5`, on returns of `daily`, `4wk-clustered`, `4wk-spread` or `3wk`
density against carriers of `daily`, `4wk-clustered` or `4wk-spread` density (`daily`/`daily` 300 → 318 and
323 → 359; `4wk-clustered`/`daily` 309 → 318 and 339 → 359; `4wk-spread`/`daily` 316 → 325 and 349 → 366).
Against a `3wk` or `2wk` carrier the two modules are identical at every `c` (24/57/92/127/164/201 and
0/0/0/0/0/0 per pair), because such a carrier is not judgeable in the week after the athlete returns and
neither rule has anything to promote from.

**Confidence.** High. 1,230,000 deterministic judgings; the improved cells and the worsened cells have
different mechanisms and each is reproduced in the walk (Finding 2).

**Re-measured 2026-09-20 (T164): every number in this finding's tables is unchanged**, at every `c`, in
both overlap variants, and on all 17 worsened cells. AC7's reference-set widening moves the *stale-band*
population and nothing here, which is the strongest evidence in this document that Findings 1/2 and
Finding 3 are two mechanisms and not one.

**Tradeoff.** F006 buys `c ≤ 2` with `c ≥ 4`. An athlete who takes the watch off within two days of putting
the strap back on is strictly better off; one who keeps wearing it is worse off by up to 36 rows per 2,050
on the `daily`/`daily` pair.

### Finding 2 (deliverable 2): T161's Finding 3 regression candidate — **confirmed**, and conditional

**Claim.** T161 Finding 3 rated itself "medium on the F005 comparison, which is one walk and not the
rectangle". Run on the rectangle and on both modules and both overlap variants, the candidate is
**confirmed under `T161_OVERLAP=healthy` and not reproduced under `T161_OVERLAP=suppressed`** — it exists if
and only if the two instruments disagree on the same morning.

**Evidence, the walk T161 measured, now on both modules** (`daily`/`daily`, `strap-returns`, suppressed
return, healthy overlap; `H` = home strap selected, `C` = carrier):

| `c` | F005 forbidden `r` | F006 forbidden `r` | F005 selection `r = 1..40` | F006 selection `r = 1..40` |
|---|---|---|---|---|
| 0 | 1-2 | 1-2 | `CCCCCCCHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH` | `CCCCHHHCCCCCCCCCCCCCHHHHHHHHHHHHHHHHHHHH` |
| 1 | 1-5 | 1-5 | `CCCCCCCHHH…` | `CCCCCHHCCCCCCCCCCCCCHHH…` |
| 2 | 1-6 | 1-6 | `CCCCCCCHHH…` | `CCCCCCHCCCCCCCCCCCCCHHH…` |
| 3 | 1-7 | 1-7 | `CCCCCCCHHH…` | `CCCCCCCCCCCCCCCCCCCCHHH…` |
| 4 | 1-7 | **1-8** | `CCCCCCCHHH…` | `CCCCCCCCCCCCCCCCCCCCHHH…` |
| 5 | 1-7 | **1-9** | `CCCCCCCHHH…` | `CCCCCCCCCCCCCCCCCCCCHHH…` |

That is exactly T161's reading (`1..9` vs `1..7`), now with both sides measured in one run. Over the whole
walk (25 pairs × 2 orientations, suppressed returns) it is +1 forbidden morning at `c = 4` and +3 at `c = 5`
(78/150/160/190/**249→250**/**277→280**), and on the rectangle it is the `c = 4, 5` column of Finding 1.

**Under `T161_OVERLAP=suppressed` it does not reproduce**: the walk's forbidden count is 78 → 78 at every
`c` and the rectangle's is better on F006 at every `c`, with the T130-comparable subset (`ret_week ≥ 3`)
going **128 → 0**. One 25 ms carrier morning inside a five-day 38/44 week pulls the carrier's own mean below
its band, so the carrier says `hrv_suppressed` for its own reasons and there is nothing to promote.

**Adjudication.** *Confirmed, conditional on the two instruments being independent.* The mechanism is the
one T161 named: T153's hole clip leaves the returning dataset unestablished (`n` 1..2 in the window) from
the eighth morning back, and the overlapping carrier remains the only judgeable dataset for as long as it
holds three judged-week days, so F006 keeps promoting from it where F005 re-admitted the strap at `r = 8` on
a band mixing era A with the return. F005's `r = 8` verdict is not obviously better — it is a verdict on a
mixed band, which reference §9 called worse than F005's successor — but on this population it lands on
`hrv_suppressed`, the right answer, and F006's lands on `hrv_normal`, the forbidden one.

**Confidence.** High on the counts. The conditionality is not a hedge: it is the finding.

**Status after T164 (2026-09-20): re-measured, unchanged, and deliberately NOT paid.** The walk table
above, the `c = 4` and `c = 5` columns and the 17 rectangle cells all reproduce exactly on the widened
reference. Per the user decision of 2026-09-20 this regression is carried as the release gate's single
named exception — 64 gated rows, every one `overlap = healthy`, on `forbidden`,
`forbidden_carrier_week`, `forbidden_ret_week_ge3` and `walk_forbidden` — with its row count, its
marginal totals and its condition (that the `suppressed` side is *not* worse anywhere) pinned in
`runcoach-api/tests/test_hrv_no_regression_gate.py`. The gate reds if that count moves in either
direction. [[IDEA-087]] is what must be answered before it is re-priced.

### Finding 3 (deliverable 3, and the block): `hrv_normal` on an entirely pre-layoff band nearly doubles — **CLOSED by T164**

> **RESOLVED 2026-09-20 ([[T164]]).** The reference population was widened to every **established**
> dataset and this rate now meets shipped F005 **exactly**: 1,896 of 307,500 rectangle rows and 96 of
> 24,000 walk rows, in both overlap variants, at every `c` (316 per `c`, flat, as on F005), and cell for
> cell across all 25 density pairs. The `f005` column of `T162-no-regression-rows.csv` *was* the
> prediction, and the re-measurement met it with no residual: **0 rows of difference**, not a reduction.
> The text below is T162's finding as measured on 2026-09-19 and is kept as the record of what was
> wrong; the updated tables follow it.

**REGRESSION (as measured 2026-09-19, now closed): the §1.7 promotion rate on a stale band worsens from
1,896 to 3,705 of 307,500 rectangle rows (0.617% → 1.205%, ×1.95) and from 96 to 254 of 24,000 walk rows
(0.400% → 1.058%, ×2.65), worse on F006 at every `c`, on 82 of the 150 rectangle cells, and identically
under both overlap variants — so AC21 blocks release on it.**

**Claim.** Under shipped F005 the count is **flat in `c`** — 316 per `c`, every `c` — because F005's recency
reference set is every **established** tier, and a carrier that has stopped (or is too sparse to cover the
week) is still established and still strikes the returning strap. Under F006, AC7 takes the reference
maximum over the **judgeable** datasets only, so that carrier leaves the reference set, the returning
dataset becomes its own reference (AC7's own sentence: "a lone dataset is its own reference and is never
skipped"), and it is selected on a band 36–66 days old. The count therefore **decays with `c`** — the
longer the carrier overlaps, the more often it is still judgeable — from 865 at `c = 0` to 378 at `c = 5`,
and is above F005's 316 at every one of them.

**Evidence.** Rectangle, all 25 pairs, 51,250 rows per `c`:

| `c` | 0 | 1 | 2 | 3 | 4 | 5 | total |
|---|---|---|---|---|---|---|---|
| shipped F005 | 316 | 316 | 316 | 316 | 316 | 316 | 1,896 |
| F006 at `99a5755` (2026-09-19, the block) | **865** | **785** | **675** | **548** | **454** | **378** | **3,705** |
| **F006 after T164 (2026-09-20)** | **316** | **316** | **316** | **316** | **316** | **316** | **1,896** |

The F006 row is now flat in `c`, which is the signature of the mechanism being closed rather than
attenuated: the count no longer decays with how long the carrier overlaps, because the carrier is in the
reference set whether or not it is still judgeable. Identical under both overlap variants, as before.

By density pair at `c = 0` (F005 → F006 at `99a5755`), the shape of the difference **as it was**:

| ret / carrier | daily | 4wk-clustered | 4wk-spread | 3wk | 2wk |
|---|---|---|---|---|---|
| daily | 10 → 64 | 10 → 91 | 10 → 95 | 20 → 100 | 95 → 100 |
| 4wk-clustered | 10 → 37 | 10 → 50 | 10 → 53 | 20 → 55 | 55 → 55 |
| 4wk-spread | 3 → 27 | 3 → 27 | 3 → 27 | 9 → 27 | 27 → 27 |
| 3wk | 3 → 6 | 3 → 6 | 3 → 6 | 6 → 6 | 6 → 6 |
| 2wk | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |

Read the last column first: **against a `2wk` carrier the two modules agree exactly** (95/55/27/6/0). That
is the control. A `2wk` carrier is never established in the relevant window either, so F005's broader
reference set contains nothing to strike with and it behaves as F006 does. Every cell where the two differ
is a cell where the carrier **is** established but **is not** judgeable — precisely the population AC7's
"judgeable" narrowed away.

**The same table after T164 (2026-09-20), F005 → F006:** every cell is `n → n`.

| ret / carrier | daily | 4wk-clustered | 4wk-spread | 3wk | 2wk |
|---|---|---|---|---|---|
| daily | 10 → 10 | 10 → 10 | 10 → 10 | 20 → 20 | 95 → 95 |
| 4wk-clustered | 10 → 10 | 10 → 10 | 10 → 10 | 20 → 20 | 55 → 55 |
| 4wk-spread | 3 → 3 | 3 → 3 | 3 → 3 | 9 → 9 | 27 → 27 |
| 3wk | 3 → 3 | 3 → 3 | 3 → 3 | 6 → 6 | 6 → 6 |
| 2wk | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |

The `2wk` column was the control that isolated the mechanism, and it is now the whole table: the two rules
agree everywhere, because the population that distinguished them — established, not judgeable — is back in
the reference set on both. The `s` ceiling and `q` floor are unchanged (`q` 2..6, `s` 29..50 on F006;
`q` 2..6, `s` 29..49 on F005), so this is a widening of one population, not a new one.

On the walk (healthy return, `c = 0`, `strap-returns`), the same fact as mornings the athlete is told he is
normal on a band 36–66 days old:

| home / carrier | F005 stale-normal `r` | F006 stale-normal `r` (2026-09-19) | F005 flips / 40 | F006 flips / 40 |
|---|---|---|---|---|
| daily / daily | — | **5-7** | 1 | 3 |
| daily / 3wk | — | **3-7** | 1 | 3 |
| daily / 2wk | 3-7 | 3-7 | 0 | 2 |
| 4wk-clustered / daily | — | **5** | 7 | 3 |
| 4wk-clustered / 3wk | — | **3-5** | 7 | 3 |
| 4wk-clustered / 2wk | 3-5 | 3-5 | 0 | 2 |
| 4wk-spread, 3wk, 2wk / any | — | — | 0-2 | 0-2 |

The `2wk`-carrier rows are again the control: identical on both modules.

**After T164 (2026-09-20), the walk's stale-normal cells are the `2wk`-carrier cells and nothing else, on
both modules.** Over all 600 walk cells per overlap variant, the only non-zero ones are `daily / 2wk`
(5 mornings) and `4wk-clustered / 2wk` (3 mornings), at every `c`, in both orientations — `5 → 5` and
`3 → 3` — totalling 96 → 96 of 24,000. Every cell the two modules used to differ on is now `0 → 0`.

**Confidence.** High. The mechanism is named in the spec (AC7's reference set, `research/00` §5.4 (ii)), it
is IDEA-080's own claim, the `2wk`-carrier control isolates it, and the predicted remedy met the predicted
number exactly on re-measurement — which is the strongest form of confirmation this sweep can produce,
because the prediction (`f005`) was committed to the tree before the fix was written.

**Tradeoff, and why this is the one that blocks.** The direction is the forbidden one: `hrv_normal` on
evidence up to 66 days old, with nothing current to compare against, told to a consumer reading `hrv_status`
alone. It is up-regulation while the athlete's current state is simply unknown — worse, in §1.7's terms,
than up-regulation while contrary evidence exists, because there is not even a dissenter to name (the
carrier is not judgeable, and under the AC9 fallback `disagreed_with` names nobody at all: IDEA-082). It is
also the exposure AC6 was amended into existence to close — "its baseline is entirely pre-layoff, and the
unqualified reading selects it" — closed at AC6 and reopened at AC7. **T164 closes it again, at AC7, and the cost it re-imports is
priced in this same sweep**: the `2wk`-carrier control shows the two rules are identical wherever F005's
extra reference holders are absent, and Findings 1, 4, 5 and 7 show what moved where they are present —
notably the flip rate (better again, 13.10 → 9.36) and the silence that pays for it (`hrv_unavailable`
15,996 → 16,312 walk mornings).

### Finding 4 (deliverable 4): AC22's exposure, reported twice, and a third time

**Claim.** Taken literally, AC22's promotion rate is **better** on F006 (24,510 → **24,099** of 307,500,
healthy overlap, re-measured 2026-09-20; 24,422 before T164), but most of it on both modules is a fixture
artefact: the dissenter holds a **single** judged-week reading. Conditioned on the dissenter holding
≥ `MIN_WINDOW_READINGS` judged-week days it is still better (5,415 → 5,287, unmoved by T164); conditioned
on the dissenter being **judgeable** — which is AC22's own word, and a stricter condition than the one T161
recommended — it is **identical** (1,837 → 1,837, also unmoved). T164 improved only the literal reading,
by 323 rows, because widening the reference set removes promotions on stale bands and those rows carried
a one-reading dissenter.

**The oracle, stated because it is the one thing that is not simply "run it twice".** `disagreed_with` is an
F006 structure; shipped F005 has no per-tier bands, so a bare "F005: 0" would measure the rule having
nowhere to put the number. For every row of both runs the dissent is therefore computed the same way: the
F006 datasets are built over **the same fixture rows** (construction is a fact about the fixture, not about
the verdict rule), each is read against its own band with `read_against_band`, and the dissenters are the
tiers reading the **other side** from the tier the **judged** module selected. On the F006 runs this
reproduces `selection.disagreed_with` exactly — `t162-check` asserts it row for row over 918 non-fallback
rows, 359 of which name a dissenter, so the agreement is not vacuous — and it additionally counts 79 rows
which are AC9 fallback presentations holding a dissenter the response deliberately does not name (IDEA-082).
All AC22 metrics below gate on `hrv_normal`, a verdict F006 can only reach with `selection.selected`
non-null, so on exactly the rows AC22 counts, oracle and `disagreed_with` coincide.

**Evidence.** Rectangle, per `c` (F005 → F006), healthy overlap:

| `c` | rows | literal | ≥ `MIN_WINDOW_READINGS` | judgeable dissenter |
|---|---|---|---|---|
| 0 | 51,250 | 2,465 → 2,368 | 97 → 0 | 0 → 0 |
| 1 | 51,250 | 3,268 → 3,243 | 382 → 357 | 137 → 137 |
| 2 | 51,250 | 3,911 → 3,905 | 712 → 706 | 260 → 260 |
| 3 | 51,250 | 4,526 → 4,489 | 1,114 → 1,114 | 392 → 392 |
| 4 | 51,250 | 4,993 → 4,890 | 1,423 → 1,423 | 486 → 486 |
| 5 | 51,250 | 5,347 → 5,204 | 1,687 → 1,687 | 562 → 562 |
| **total** | **307,500** | **24,510 → 24,099** | **5,415 → 5,287** | **1,837 → 1,837** |

Re-measured 2026-09-20. Under T164 the literal rate is better at **every** `c` — before it was worse at
`c = 0, 1, 2` (2,472 / 3,353 / 3,974) and better at `c = 3, 4, 5`. The two conditioned readings are
unchanged at every `c`, which is the same separation of mechanisms Findings 1 and 3 show: the rows T164
removed are stale-band promotions whose dissenter holds one or two readings.

Dissenter judged-week day counts over the 24,510 (F005) / 24,422 (F006 at `99a5755`) literal rows
*(T162, 2026-09-19; **not re-derived** by T164 — it is computed from the ~500 MB per-row CSVs, which are
not kept, and the F006 column is superseded by the 24,099 total above. The F005 column and the 78% claim
stand; the F006 column should be read as "as of 2026-09-19" and not quoted as current)*:

| dissenter week days | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| F005 | 13,914 | 5,181 | 3,119 | 1,471 | 389 | 271 | 165 |
| F006 | 13,954 | 5,181 | 3,007 | 1,455 | 389 | 271 | 165 |

T161 Finding 6 is confirmed on both modules as of 2026-09-19: **78% of the literal rate is a dissenter
with one or two readings**, whose 38 ms lands 0.07 below its own band's `lo` because the 38/44 alternation gives a half-width
of 0.037. The dissenting tier is `chest_strap_raw` on 23,936 of F005's rows and 23,808 of F006's; the
snapshot dissents on 574 / 614.

**Direction, for the Negative Class.** In every one of these rows the promoted verdict is `hrv_normal` and
the dissenter reads **below** its own band — up-regulation while contrary evidence exists, `research/00`
§1.7's forbidden direction, and the consumer reading `hrv_status` alone is not told. Under the suppressed
overlap the literal rate falls to 10,334 → **9,923** and the conditioned rate to 189 → **61**
(re-measured 2026-09-20; 10,246 before T164).

**Confidence.** High on the counts and on the mechanism (the row carries the dissenter's week size).
Medium on what the rate *means*: see Finding 6.

**Tradeoff.** AC22's three readings disagree about whether this is 8% of mornings or 0.6% of them, and the
difference is entirely the dissenter's week size. The Negative Class should carry the **judgeable** figure,
since that is AC22's own word, and say that the literal figure is thirteen times larger and mostly a
one-reading week.

### Finding 5 (deliverable 5): the dataset-flip rate per athlete-year is better, so hysteresis is not triggered

**Claim.** F006 flips the selected dataset **less** than shipped F005: **600** against 1,184 changes over
23,400 day-to-day transitions (600 forty-morning walks), i.e. **9.36 against 18.47 flips per
athlete-year**, a 49% improvement. AC23's trigger ("a worse rate triggers the deferred hysteresis
decision") does **not** fire. *(Re-measured 2026-09-20; T162 measured 840 changes and 13.10 per
athlete-year before the reference-set change. T164 improved it further: a dataset that loses the recency
comparison stays lost while the carrier keeps reading, so the selection stops oscillating across the
return.)*

**Evidence.** The walk, 25 density pairs × 2 orientations × `c = 0..5` × 2 value levels, `r = 1..40`
(39 transitions each), identical under both overlap variants:

| | shipped F005 | F006 at `99a5755` (2026-09-19) | **F006 after T164 (2026-09-20)** |
|---|---|---|---|
| selection changes | 1,184 | 840 | **600** |
| day-to-day transitions | 23,400 | 23,400 | 23,400 |
| **flip rate per athlete-year** | **18.47** | **13.10** | **9.36** |
| walk rows reading `hrv_unavailable` | 13,328 | 15,996 | **16,312** |

Where the improvement comes from is legible in the sequences: the `4wk-clustered` home dataset against a
`daily` carrier oscillates **seven** times on F005 (`CCCCCCCCHHHHCCCHHHHCCCHHHHCCCHHHHHHHHHHH` — the 3×/week
wearer's week-by-week flip the Negative Class carries from `CRITIC-F005`) and **three** times on F006
(`CCCCHCCCCCCCCCCCCCCCCCCCCCCCCHHHHHHHHHHH`). F006 buys that stability with silence: 2,668 more
`hrv_unavailable` mornings over the same 24,000 rows, which is IDEA-084's second silence (the hole clip
leaves the returning dataset unestablished from `r = 8` until 14 captured mornings are inside the window:
`r = 21` at `daily`, 30 / 31 / 38 / never at the sparser patterns).

The sequence tables above are T162's, measured on `99a5755` *(not re-derived)*; the counts in the table
beside them are T164's.

**Confidence.** High on the count. Medium on the *per athlete-year* framing: the denominator is 600 walks of
a **device return**, not 600 ordinary athlete-years, so 13.10 is "flips per 365 consecutive mornings of the
return geometry", which is a worst case, not a population mean. It is nevertheless the same denominator on
both sides, which is what AC23 asks for.

**Tradeoff.** F006 trades flips for silence — after T164, 584 fewer flips for **2,984** more
`hrv_unavailable` mornings over the same 24,000 rows, roughly one for five. Whether an athlete prefers a
changing answer to no answer is not measured here and is not measurable from fixtures. On the rectangle
the same trade reads 210,914 → 246,944 `hrv_unavailable` rows of 307,500, and the returning dataset is
selected on 21,520 rows against F005's 59,468: **T164's reference widening is bought with silence, and
that is the honest name for it.**

### Finding 6 (deliverable 6): whether the two datasets are independent instruments — what the corpus can and cannot settle

**Claim.** The recorded corpus settles three things and cannot settle the one that matters.

**What it establishes.** Six FIT files recorded on the athlete's own FR945 LTE + HRM-Pro-Plus, decoded from
the committed bytes:

| fixture | recorded | profile | ANT+ peer | beats stored | device `rmssd_hrv` | project's own rMSSD over the stored beats |
|---|---|---|---|---|---|---|
| `strap_health_snapshot_hrv.fit` | 2026-09-06 17:27:36Z, 120.2 s | Health Snapshot | **HRM-Pro-Plus, serial 3611410126, `antplus_device_type: heart_rate`** | **0** | **90** | — |
| `strap_hrv_capture.fit` | 2026-09-06 17:48:06Z, 150.5 s | HRV Snapshot | same strap, same serial | 165 | — | **41.52 ms** |
| `strap_health_snapshot.fit` | 2026-09-05 03:48:30Z, 120.2 s | Health Snapshot | same strap, same serial | **0** | **51** | — |
| `strap_hrv_sample_run.fit` | 2026-09-05 05:02:36Z, 150.8 s | Run (undeclared resting capture) | same strap, same serial | 156 | — | **36.87 ms** |
| `sample_health_snapshot.fit` | 2026-09-01 12:11:26Z, 120.2 s | Health Snapshot | **none** | 0 | **37** | — |
| `wrist_ppg_hrv_snapshot.fit` | 2026-09-07 12:55:38Z, 153.5 s | HRV Snapshot | **none** | 0 | — | — |

1. **On a strap morning the watch *is* reading the strap.** Every Health Snapshot recorded with the strap on
   carries an ANT+ `device_info` naming the HRM-Pro-Plus by serial, `antplus_device_type: heart_rate`. So
   AC3's "a morning with two captures feeds both datasets" is, at the sensor level, a **correlated** pair —
   the same chest electrodes are the HR source of both files.
2. **But the two numbers are not the same number.** On 2026-09-06, twenty minutes apart, the snapshot says
   **90** and the project's own arithmetic over the strap's stored beats says **41.52** — a factor of 2.17.
   On 2026-09-05, seventy-four minutes apart, **51** against **36.87** — a factor of 1.38. So it is not a
   fixed vendor transform either, over this n.
3. **The snapshot does not need the strap at all.** `sample_health_snapshot.fit` carries no ANT+ peer and
   still reports `rmssd_hrv = 37`. On a strap-free morning the snapshot is unambiguously a different
   instrument (wrist PPG), which is the ~17.49%-vs-2.16% error gap the feature's fidelity rank is built on.
4. **The two captures are mutually exclusive on one watch.** `strap_health_snapshot_hrv.fit` was recorded
   with `Log HRV` enabled and still stores **zero** beats (T057's load-bearing pin). A simultaneous pair is
   not merely absent from the corpus; on this hardware it cannot be produced.

**What it cannot establish, plainly.** *Whether, on a strap morning, the snapshot's `rmssd_hrv` is computed
from the same beats.* The snapshot stores no beats, so the two can never be reconciled at the beat level
from stored data; the nearest pairs are 20.5 and 74 minutes apart, across a real physiological gradient and
over different windows (120 s against 150 s); Garmin's `rmssd_hrv` arithmetic is undocumented; and n = 2.
The 2.17× and 1.38× gaps are therefore confounded three ways and are **not** evidence of independence — two
views of the same beats, differently post-processed and differently windowed, would look like this too.
**The corpus cannot tell a correlated pair from an independent one.** That is the honest answer.

**Why it is now on the critical path rather than a footnote.** The release decision changes sign on this
axis. Under `T161_OVERLAP=healthy` (independent instruments) Finding 2's regression exists and Finding 1's
rate worsens; under `T161_OVERLAP=suppressed` (the same physiology seen twice) it does not, and F006 is
better at every `c`. AC22's whole rate is likewise a statement about two views of physiology only if they
are two views. Finding 3's block does **not** depend on it — the stale-band regression is identical in both
variants — but everything else does.

**What would settle it, concretely.**

- **The measurement.** On each of N ≥ 20 mornings spanning a real readiness range (including genuinely
  suppressed ones), record a Health Snapshot and an `HRV Snapshot` raw-beat capture **back to back within
  five minutes**, strap paired throughout. Back-to-back is the closest achievable on one watch; a *second*
  watch paired to the same strap would give a genuinely simultaneous pair and is the stronger design.
- **The statistic that matters is not the correlation.** Report (a) Spearman of (snapshot `rmssd_hrv`,
  computed strap rMSSD) in ln space and (b) Bland–Altman limits of agreement, but above all (c) the
  **sign-agreement rate**: the fraction of mornings on which the two datasets fall on the same side of
  their **own** bands. That single number is what AC22's exposure and T130's flip pricing actually depend
  on, and it is the one this sweep's two overlap variants bracket (100% agreement = the `suppressed`
  column; 0% = the `healthy` column).
- **A free first-order estimate, needing no new capture protocol.** T159 renders `datasets[]` with each
  dataset's `n`, judged-week count and band reading. Logging, per response, whether the two datasets' `below`
  flags agree on days both hold a reading would produce the sign-agreement rate from ordinary use within a
  few weeks, at no capture cost.

**Confidence.** High on what the bytes say (the decode is the oracle; the numbers above are transcribed from
a `fitdecode` run over the committed fixtures and the rMSSD from the project's own
`rr_reconstruction.reconstruct` → `rmssd.resting_rmssd`, which is where the suite's pinned 41.52 comes
from). High on the negative claim — n = 2, no simultaneous pair, no stored beats — which is the finding.

### Finding 7 (deliverable 7): T158's retained order clause is disarmed by one captured carrier morning, on both modules — T130's question, closed

**Claim.** The decisive withhold (`week_not_representative`) is armed on 226 of 307,500 rows under shipped
F005 and on **424** under F006 — T158's widening to dataset scope, measured — and on **both modules one
captured carrier morning inside the return disarms 100% of it**. By days elapsed the disarm reaches 100% at
`c = 3`; by mornings captured it is 100% at `c_cap = 1`. The clause is decisive on only **6 of the 25
density pairs** on both modules.

**Evidence, per density pair and per `c`** (`disarmed / armed at c = 0`, healthy overlap; the 19 pairs not
listed are `0 / 0` on both modules at every `c`):

| ret / carrier | module | decisive @ `c = 0` | `c = 1` | `c = 2` | `c = 3` | `c = 4` | `c = 5` |
|---|---|---|---|---|---|---|---|
| daily / daily | F005 | 98 | 98 / 98 | 98 / 98 | 98 / 98 | 98 / 98 | 98 / 98 |
| daily / daily | **F006** | **150** | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 |
| daily / 4wk-clustered | F005 | 26 | 6 / 26 | 18 / 26 | 26 / 26 | 26 / 26 | 26 / 26 |
| daily / 4wk-clustered | **F006** | **42** | 10 / 42 | 30 / 42 | 42 / 42 | 42 / 42 | 42 / 42 |
| daily / 4wk-spread | F005 | 14 | 0 / 14 | 14 / 14 | 14 / 14 | 14 / 14 | 14 / 14 |
| daily / 4wk-spread | **F006** | **20** | 0 / 20 | 20 / 20 | 20 / 20 | 20 / 20 | 20 / 20 |
| 4wk-clustered / daily | F005 | 62 | 62 / 62 | 62 / 62 | 62 / 62 | 62 / 62 | 62 / 62 |
| 4wk-clustered / daily | **F006** | **150** | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 |
| 4wk-clustered / 4wk-clustered | F005 | 18 | 4 / 18 | 12 / 18 | 18 / 18 | 18 / 18 | 18 / 18 |
| 4wk-clustered / 4wk-clustered | **F006** | **42** | 10 / 42 | 30 / 42 | 42 / 42 | 42 / 42 | 42 / 42 |
| 4wk-clustered / 4wk-spread | F005 | 8 | 0 / 8 | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |
| 4wk-clustered / 4wk-spread | **F006** | **20** | 0 / 20 | 20 / 20 | 20 / 20 | 20 / 20 | 20 / 20 |

Marginal, all 25 pairs:

| `c` | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| shipped F005 | 0 / 226 | 170 / 226 | 212 / 226 | **226 / 226** | 226 / 226 | 226 / 226 |
| today's F006 | 0 / 424 | 320 / 424 | 400 / 424 | **424 / 424** | 424 / 424 | 424 / 424 |

The residual arming at `c = 1, 2` on a sparse carrier is the phase at which the carrier's pattern has
captured nothing yet (`c_cap = 0`), not a survival of the clause: re-indexed by mornings captured, `c_cap = 1`
disarms every armed row on both modules. That is T130's structural result — "one extra carrier morning is
enough to make *all earlier* false" — restated in the density frame and now measured on **both** rules.
T158 roughly doubled the clause's reach (226 → 424 decisive rows) without changing its disarm rate by a
single row.

On T150's own populations (both modules, all 25 density pairs, `c = 0..5`): the matched pairs go
`TP 399 / FN 2,151` on F005 to **`TP 849 / FN 1,701`** on F006 with **`FP 0` on both** (re-measured
2026-09-20; T162 read `TP 626 / FN 1,924` before the reference-set change, so T164 improved the true
positives by a further 223 with no false positive), and the forbidden verdicts on the resumed side fall
1,439 → 1,275, unchanged by T164; the switch-away rows produce **0 false withholds on both
modules** out of 1,400, with `hrv_normal` on 126 rows of each — the `2wk`-new-device population T161
Finding 7 describes, unchanged by F006.

**Re-measured 2026-09-20 (T164): the marginals are unchanged.** 226 decisive rows armed at `c = 0` on
shipped F005 and **424** on F006, disarmed 170 / 212 / 226 / 226 / 226 and 320 / 400 / 424 / 424 / 424 at
`c = 1..5` — identical to the row before the reference-set change (the rows carry these as
`armed_decisive_c0` 1,356 vs 2,544 and `disarmed_decisive` 1,060 vs 1,992, each replicated across the six
`c` values). The per-pair table above is T162's *(not re-derived)*; its six non-zero pairs are unchanged
by T164, since the withhold reads the same struck set the selection does and that set moved only on the
stale-band population, where the clause was never armed.

**Confidence.** High. Deterministic; and the six non-zero pairs are the ones T145's closed form predicts on
both rules.

**Tradeoff, priced.** T158's clause protects the `daily`/`4wk-clustered` dual-device athlete on the athlete's
third and fourth morning back **only while the watch is off**. The moment the watch records one morning
inside the return — AC3's designed norm — it protects nobody, on either rule. Its value is therefore not the
424 rows but the brand-new-device population AC24 names, which no overlap can disarm because a brand-new
device has no earlier week to overlap with.

## Recommendation

*Recommendations 1 and 2 were acted on by [[T164]] on 2026-09-20 and are marked accordingly; 3 to 6
stand.*

1. ~~**Do not release.**~~ **Superseded 2026-09-20.** AC21's condition was met in the blocking direction
   on two rates. One is paid (Finding 3: the stale-band rate now equals shipped F005 exactly). The other
   (Findings 1 and 2: the §1.7-forbidden rate at `c ≥ 4`) is **not** paid and is carried as the gate's one
   named exception — 64 gated rows, every one under the independent-instruments fixture, with its row
   count, its marginal totals and its condition pinned, and the gate red if any of them moves. The gate,
   `runcoach-api/tests/test_hrv_no_regression_gate.py`, is now **green with that exception declared in
   its own source**; no other §1.7 rate is worse on any swept cell.
2. ~~**Finding 3 is actionable inside this sprint and the cheapest of the three.**~~ **Done (T164).** The
   mechanism was one word of AC7 — the reference maximum was taken over the *judgeable* datasets.
   IDEA-080's option 2 was taken: the reference set is every **established** dataset, and it restored
   F005's numbers **exactly**, as the `2wk`-carrier control predicted. The prediction was committed to the
   tree before the fix (`the f005 column`), and the re-measurement met it with zero residual. The cost is
   the one IDEA-080 named — the shape T125 was written against is re-imported — and it is priced in
   Findings 1, 5 and 7 above: no forbidden-rate change at all, a further 240 fewer flips, and 316 more
   `hrv_unavailable` walk mornings.
3. **Finding 2's block should not be paid before the corpus question is answered.** It exists only under
   `T161_OVERLAP=healthy`; under the correlated assumption F006 is strictly better, including `128 → 0` on
   the T130-comparable subset. Spending a rule change on it while its sign is unknown is the
   parameter-before-measurement mistake reference §10 names. Run Finding 6's back-to-back protocol, or the
   free `datasets[]` sign-agreement estimate, first. **Still the recommendation after T164, and now the
   documented justification for the gate's deferred exception.**
4. **Record in the Negative Class** the AC22 exposure with its direction and all three readings (literal
   7.97% → **7.84%**; dissenter ≥ `MIN_WINDOW_READINGS` 1.76% → 1.72%; dissenter judgeable 0.597% →
   0.597%, identical), the **closed** stale-band regression, the **deferred** forbidden-rate one with its
   condition, and the flip rate improvement. Text is in T164's `NEGATIVE_CLASS_ENTRY` return, which
   supersedes T162's.
5. **AC23 needs no action.** 18.47 → **9.36** flips per athlete-year; the deferred hysteresis decision is
   not triggered. Note in the decision log that it was scored against a criterion it could have failed and
   did not, and that the **2,984** extra `hrv_unavailable` mornings are what paid for it.
6. **Close T130's question in the Negative Class with Finding 7's numbers** rather than with T130's
   `54 / 72 / 90`: the disarm is 100% per captured carrier morning on **both** rules, the clause is decisive
   on 6 of 25 density pairs, and T158 doubled its reach (226 → 424 rows) without moving its disarm rate.

## Reproducing the rows

Deterministic throughout. The rows in the tree and in the data dir are **T164's run of 2026-09-20**,
against the installed module after the AC7 reference-set change and the same shipped F005 at `42f7705`;
the command and every axis are T162's, unchanged. A row is identified by
`(sweep, module, overlap, ret_density, car_density, c, s, q | r | era_len+mode | era_len+strays, suppressed)`
and two runs produce identical files.

- `uv run --package runcoach-api python <harness> t162-check --f005 <f005.py>` — runs T161's generator check,
  asserts the AC22 oracle reproduces `selection.disagreed_with` row for row on the F006 module, and prints
  shipped F005's `r = 1..8` walk so the band suite's pinned values are visible before anything is trusted.
- `uv run --package runcoach-api python <harness> t162-gate --f005 <f005.py> --procs 16 --rows <rows.csv>
  [--rowdir <dir>]` — the whole cross-product in one command (~13 min on 16 processes). `--rows` writes the
  17,070 paired comparison rows; `--rowdir` additionally writes the 8 per-row CSVs (4 × 307,500 rectangle
  rows, 4 × 24,000 walk rows, 4 × 5,100 and 4 × 1,400), ~500 MB, which are **not** kept in the data dir.
- T161's single-variant commands are unchanged and still reproduce T161's tables:
  `T161_MODULE=<f005.py> ... t161-rect --procs 12 --csv <out>`, `T161_OVERLAP=suppressed ... t161-walk ...`.
- The comparison rows' columns: `sweep, overlap, scope, ret_density, car_density, c, orientation,
  value_level, metric, criterion, gated, denom, f005, f006, f005_rate, f006_rate, delta, worse`. `scope` is
  `cell` (one density pair × `c`), `by_c` (all pairs at one `c`) or `total`. `gated = 1` marks the rates
  AC21 blocks release on; AC23's flip rate is `gated = 0`, `criterion = AC23`.

## Open questions this report leaves

- Is a Health Snapshot on a strap morning the same beats? Finding 6 says the corpus cannot tell and names
  the protocol that would. **This now gates a release decision, not only AC22's interpretation.**
- ~~Should AC7's reference set be the judgeable datasets or the established ones?~~ **Answered
  2026-09-20 (T164, user decision): the established ones.** Finding 3 made it the choice between
  reopening AC6's population (F006 as measured, 3,705 stale-band rows) and reopening T125's (F005,
  1,896). T125's shape is the one that is *priced* — every other finding here measures it — and AC6's is
  the one §1.7 forbids with no dissenter to name, so the established set wins. What the sweep still does
  **not** distinguish is whether the re-imported T125 shape hurts a real athlete more or less often than
  the stale-band one, because that needs the capture-geometry weights nobody has ([[IDEA-088]] option 3).
- A new one, opened by T164: **a lone judgeable dataset is no longer automatically its own reference**, so
  every candidate can now be skipped at once and the AC9 fallback presents a dataset verdict-free. The
  rectangle prices the aggregate (`hrv_unavailable` 243,326 → 246,944 of 307,500) but nothing measures how
  often that state is reached by an athlete whose *only* usable instrument is the one being skipped.
- Is F005's `r = 8` re-admission of the returning strap — on a band mixing era A with the return, which
  reference §9 calls worse than its successor — actually better than F006's continued promotion from the
  carrier? On this population F005 lands on the right verdict for a reason the spec has already rejected.
  Finding 2's regression is real but its remedy may not be F005's.
- The `2wk` athlete is never judgeable on either rule, at any `c`, in any sweep here. Nothing in the suite
  pins that, and the Negative Class describes him as a first-class population.
