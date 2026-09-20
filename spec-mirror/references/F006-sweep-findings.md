---
task_id: T161
completed_at: 2026-09-19T21:40:00Z
sources_consulted:
  - spec/tasks/T161-sweeps-that-name-their-axes-with-density-as-a-cross-product.md
  - spec/features/F006-per-tier-hrv-datasets.md (AC19-AC24, Negative Class through wave 6)
  - spec/references/F006-dataset-model.md (§5, §7, §9, §10, §11)
  - spec/references/T125-fix-form-measurements.md (the daily rectangle, its 2050 rows, T130's 54/72/90)
  - spec/tasks/T130-a-return-predicate-that-survives-carrier-overlap.md
  - spec/tasks/T150-pin-switch-away-and-inter-rows.md
  - spec/tasks/T145-the-withhold-residual-is-four-mornings-not-two-whenever-the-return-is-sub-daily.md
  - spec/ideas/IDEA-080-a-stopped-carrier-leaves-the-recency-gate-with-no-reference.md
  - spec/ideas/IDEA-084-the-hole-clip-makes-a-return-after-a-layoff-longer-than-gap-reset-days-not-free.md
  - specification/research/00-design-decisions.md §5.4 (2026-09-18 amendment, clauses (i)-(v)) and the RECENCY_TOLERANCE_DAYS ratification
  - .claude/rules/learnings/a-sweep-must-name-the-axes-it-holds-constant.md, sweep-the-claim-not-the-diff.md, retiring-a-ratified-behaviour-needs-a-three-valued-pin.md; memory: a-witness-must-print-the-slice-it-compared, measurement-sweeps-silently-fix-an-axis, pytest-k-deselection-exits-zero
  - .shipyard/codebase-context.md
  - runcoach-api/src/runcoach_api/metrics/hrv_trend.py at 5b3415c (build_series, is_judgeable, verdict_withheld, select_dataset, selected_view, _presentation_fallback, disagreed_with, judge, _unavailable_reason, RECENCY_TOLERANCE_DAYS)
  - runcoach-api/tests/support/hrv_populations.py; runcoach-api/tests/test_hrv_dataset_populations.py; runcoach-api/tests/test_hrv_trend_band.py (RETURN_DENSITIES, _seed_return_series, the walk constants)
  - sprints/current/.subagent-returns/T155.txt, T158.txt, T153.txt, T153-iter2.txt
harness: spec/references/T130-overlap-sweep-harness.py
reproduce: "cd C:/xtopher/code/retsaf-nur && H=.shipyard/spec/references/T130-overlap-sweep-harness.py && uv run --package runcoach-api python $H t161-check && uv run --package runcoach-api python $H t161-rect --procs 12 --csv <out>/t161-rect.csv && uv run --package runcoach-api python $H t161-walk --procs 12 --csv <out>/t161-walk.csv && uv run --package runcoach-api python $H t161-inter --csv <out>/t161-inter.csv && uv run --package runcoach-api python $H t161-switch --csv <out>/t161-switch.csv && T161_OVERLAP=suppressed uv run --package runcoach-api python $H t161-rect --procs 12 --csv <out>/t161-rect-suppressed-overlap.csv && uv run --package runcoach-api python $H t161-tol --procs 14 --s-step 2 --tol 14:45"
---

# Research: the per-tier rule swept with capture density varied on both datasets, carrier overlap `c = 0..5` as an axis, and every fixed axis named

## TL;DR

Over 307,500 rectangle rows (25 density pairs x `c = 0..5` x T125's 2050-row rectangle), 24,000 device-return-walk rows, 5,100 matched-pair rows and 1,400 switch-away rows, all on the current rule at `5b3415c`: **T158's retained withhold decides a verdict in only 6 of the 25 density pairs** (a `daily` or `4wk-clustered` return against a `daily`, `4wk-clustered` or `4wk-spread` carrier: 424 of 51,250 rows at `c = 0`, 150 of 2050 at `daily`/`daily`), and **one captured carrier morning inside the return disarms 100% of them** at every carrier density (320/424 at `c = 1`, 424/424 at `c >= 3`; by mornings captured, `c_cap = 1` disarms 300/300, 204/204 and 80/80). The §1.7-forbidden promotion via the carrier's week is 1,614 of 51,250 rows at `c = 0` -- every one of them T125/T145's opening-mornings residual (`min(4, k3)`: 2 mornings for `daily`/`4wk-clustered` returns, 4 for `4wk-spread`/`3wk`/`2wk`) -- and grows to 5,722 at `c = 5`, where 2,680 rows promote `hrv_normal` on a week in which the athlete holds three or more suppressed mornings of his own; at `daily`/`daily` that is 114/154/195/236/277 at `c = 1..5` against T130's 54/72/90 on shipped F005, because T153's hole clip keeps the returning dataset unestablished past the seventh morning and the overlapping carrier stays the only judgeable dataset (walk: forbidden `r = 1..9` at `c = 5` under F006, `1..7` under shipped F005). **Every one of those cross-device flips depends on the fixture's two instruments disagreeing on the same morning**: with the carrier's overlap mornings carrying the athlete's suppressed value, the forbidden count with `>= 3` return mornings is 0 at every `c` and every density pair -- the disarm still happens, the verdict is right for another reason. IDEA-080's `hrv_normal`-on-a-stale-band shape is 865 rows at `c = 0` (1.7%), only for `daily`/`4wk-clustered`/`4wk-spread`/`3wk` returns whose era A still holds 14 captured days inside the window (s <= 50/41/38/31), never for `2wk`; IDEA-084's second silence is 13 mornings at `daily`, 24 at `4wk-clustered`, 26 at `4wk-spread`, 33 at `3wk`, and the `2wk` athlete is never judgeable at all. **`RECENCY_TOLERANCE_DAYS` is inert below 24 on every geometry here and above 24 is a linear exchange rate with no knee** (5.6 million rows, 14..44): it moves only `c >= 1` rows, where two datasets are judgeable at once, trading forbidden-via-carrier-week for `hrv_normal`-on-a-stale-band about one row for one (28 -> 44: -871 / +915; 28 -> 23: +198 / -222), the withhold never moves, and at `c = 0` -- IDEA-080's stopped carrier -- the gate has nothing to compare. Nothing in 14..44 is a better value than 28 and nothing re-justifies it; the constant stays.

## Context

AC19 requires every sweep to name the axes it holds constant, including the carrier's, and to vary capture density on **both** datasets. Nine cycles of F005 fixed density at `daily` on every axis; T145 then found the seventh forbidden population on exactly that axis. T130 closed into this task as a measured axis: the carrier-overlap count `c` that disarms T125's order clause, which T158 retains at dataset scope as F006's only guard against the brand-new-device population, and which AC3 makes the designed norm rather than an edge case.

Everything below runs on the **current rule** -- the installed `hrv_trend` at `5b3415c` (T151 datasets, T153 hole clip, T154 per-dataset reset, T155 selection, T156 fallback, T157 disagreement, T158 withhold) -- through `build_series -> selected_view -> judge`, the seam the route uses. One walk was additionally run against the shipped-F005 module (`git show 42f7705:.../hrv_trend.py`, loaded through `T161_MODULE`) to place the F006 numbers beside T130's; the full AC21 side-by-side is T162's and the harness now supports it without change.

**Verdict vocabulary used throughout.** *Forbidden* = the athlete's own return is suppressed (25 ms) and `hrv_normal` is promoted either from the carrier's week (T125's shape, `via carrier_week`) or from the returning dataset's own entirely-pre-layoff band (`via stale_band`; structurally 0 on a suppressed return, since 25 ms reads below any 38/44 band). A verdict on the returning dataset's *rebuilt* band -- one that already holds return mornings -- is **not** counted forbidden even when it reads `hrv_normal` on a suppressed return: there the fixture's constant 25 ms has become the athlete's baseline, which is re-establishment, not up-regulation on contrary evidence (2050 such rows per `c`, all at `q >= 20` on `daily` returns). *Normal on a stale band* = `hrv_normal` on a healthy return from a band every reading of which predates the layoff (IDEA-080's shape). *Withheld flag* = the presented dataset's `withheld`; *decisive* = the response's reason is `week_not_representative`, i.e. the withhold is what the athlete is told rather than a flag `week_too_thin` precedes (T145's inert case). *Disarmed* = the row's `c = 0` twin (same pair, `s`, `q`, values) had the clause armed and this row does not.

**Days elapsed vs mornings captured.** Every time axis here is **days elapsed** as T130/T125/the band walk define them: `s` (silence), `q` (return days before `D`, so `r = q + 1`), `c` (carrier days into the return), `r` (days since the return's first morning). Each row also reports the **mornings captured** inside those spans -- `ret_week` (the returning dataset's distinct judged-week days), `c_cap` (carrier mornings captured inside the overlap), `a_last_cap` (era A's last captured day) -- and where the two differ the tables below say so. They coincide only at `daily`.

## Axes held constant

### Sweep (a) -- the return rectangle (T125's), `t161-rect`

| axis | value |
|---|---|
| target `D`, zone | 2026-09-12, `Pacific/Auckland` |
| era A (returning dataset, `chest_strap_raw`) | 80 days ending `D-(q+s+1)`, 38/44 ms alternating **over the mornings captured**, local 06:00; pattern anchored at its **last** day so the silence `s` is exact in days elapsed |
| silence `s` | 29..69 days elapsed (41 values) |
| return | `D-q .. D`, `q = 0..24` (`q+1` days elapsed); pattern anchored at `D-q` so the first morning back is always captured (T147's convention); 25 ms when suppressed, else 38/44 |
| carrier (`health_snapshot`) | from the day after era A to `D-q-1+c`, 38/44 ms, local 07:00; pattern anchored at its **first** day, so which overlap mornings are captured depends on the phase, which the sweep over `s` samples (distribution of `c_cap` per `c` reported below) |
| carrier overlap `c` | 0, 1, 2, 3, 4, 5 carrier **days elapsed** past `D-q-1` |
| carrier's overlap values | `healthy` (T130's fixture: 38/44 on a suppressed return), and a second full run at `suppressed` (25 ms on those mornings) |
| densities | returning dataset (era A **and** return) x carrier, each of `daily` (0-6), `4wk-clustered` (0,1,2,4), `4wk-spread` (0,2,4,6), `3wk` (0,2,4), `2wk` (0,3) -- `test_hrv_trend_band.RETURN_DENSITIES` verbatim; 25 pairs |
| value levels | suppressed and healthy return, both |
| seed | none; every generator is deterministic |
| `RECENCY_TOLERANCE_DAYS` | 28 (module value) |
| rows | 2050 per (pair, `c`); 307,500 in all; at `daily`/`daily` byte-identical to T130's `rows_for` (`t161-check`) |

### Sweep (b) -- T150's two pinned populations, `t161-inter` and `t161-switch`

| axis | `inter_rows` | `switch_away_rows` |
|---|---|---|
| target, zone | `D` = 2026-09-12, Auckland | `SWITCH_BASE` = 2026-09-07, Auckland |
| era | strap 10..90 by 5 days ending `D-43` at 79 ms, 06:00, pattern anchored at its end | old snapshot era 14/15/20/25/29/30/50/87 days ending `base-40`, 38/44, 06:00, anchored at its end |
| the week | **the population's definition, held constant**: resumed `D-2, D-1, D` / abandoned `D-6, D-4, D-2` at 25 ms | strays `(6,5,4) (5,4,3) (4,3,2) (3,2,1) (2,1,0) (6,3,0) (2,1,0,3)` at 40 ms, 08:00 -- held constant |
| carrier / new tier | snapshot daily-pattern from the day after the era to `D-3+c` (resumed) or `D` (abandoned), plus `D-199..era-1`; 38/44, 07:00; anchored at its start | new strap `base-39..base`, 40 ms to `base-7` then 33 ms, 07:00, anchored at `base-39` |
| densities | era density x carrier density (25 pairs) | old-era density x new-era density (25 pairs) |
| `c` | 0..5 carrier days elapsed inside the return (resumed only) | not an axis (no overlap in this population) |
| rows | 5,100 | 1,400 |

### Sweeps (c)/(d) -- the device-return walk, `t161-walk` (IDEA-080 at `c = 0`; IDEA-084 past `r = 8`)

| axis | value |
|---|---|
| eras | home 80 days ending 2026-07-31 (06:00); carrier 2026-08-01..2026-09-08 **+ `c`** (07:00); return from 2026-09-09 -- `test_hrv_trend_band._seed_return_series`'s constants |
| `r` | 1..40 **days elapsed** since 2026-09-09 (target = 2026-09-08 + `r`); mornings captured reported per `r` |
| `c` | 0..5; `c = 0` is the walk's own **stopped** carrier |
| densities | home dataset (era A and return) x carrier, 25 pairs |
| orientation | `strap-returns` (home strap, carrier snapshot) and `snapshot-returns` -- measured **identical on every row**: fidelity rank never decides a row of this walk |
| values | 38/44 over captured mornings; return 25 ms when suppressed; overlap `healthy` (and `suppressed` in the second run) |
| rows | 24,000 (x2 for the suppressed-overlap run; x1 on the shipped-F005 module) |

### Sweep (e) -- `RECENCY_TOLERANCE_DAYS`, `t161-tol`

Sweep (a) with `s` stepped by 2 (21 values, 1,050 rows per pair per `c`) and sweep (c)/(d), both orientations, at every tolerance 14..44; everything else as above. The constant is set on the judged module inside the worker; the source is untouched.

### How to read `c` at a sparse carrier

`c` counts days; a sparse carrier captures only some of them. Distribution of `c_cap` (carrier mornings captured inside the overlap) over the rectangle's 10,250 rows per (carrier density, `c`):

| carrier | c = 0 | c = 1 | c = 2 | c = 3 | c = 4 | c = 5 |
|---|---|---|---|---|---|---|
| daily | 0 | 1 | 2 | 3 | 4 | 5 |
| 4wk-clustered | 0 | 0:4500, 1:5750 | 0:1500, 1:6000, 2:2750 | 1:4500, 2:4500, 3:1250 | 1:1500, 2:4500, 3:4250 | 2:3000, 3:6000, 4:1250 |
| 4wk-spread | 0 | 0:4500, 1:5750 | 1:8750, 2:1500 | 1:3000, 2:7250 | 2:7250, 3:3000 | 2:1500, 3:8750 |
| 3wk | 0 | 0:6000, 1:4250 | 0:1500, 1:8750 | 1:7500, 2:2750 | 1:3000, 2:7250 | 2:9000, 3:1250 |
| 2wk | 0 | 0:7500, 1:2750 | 0:4500, 1:5750 | 0:1500, 1:8750 | 1:9000, 2:1250 | 1:6000, 2:4250 |

In the walk (one phase, carrier anchored at 2026-08-01) `c = 1..5` captures 1/1/1/2/3 mornings at `4wk-clustered`, 1/1/2/3/3 at `4wk-spread`, 1/1/1/2/2 at `3wk` and 0/0/0/1/1 at `2wk`; in the matched pairs (carrier anchored at `D-42`) a `4wk-clustered` or `3wk` carrier captures its first overlap morning only at `c = 3`, a `4wk-spread` one at `c = 2`. Wherever a table below says "at `c = 1`", read the `c_cap` row beside it.

### Finding 1: the retained withhold decides a verdict in 6 of 25 density pairs, and only on the athlete's third and fourth morning back

**Claim.** T158's `verdict_withheld` changes what the athlete is told (`week_not_representative`) only when the return is `daily` or `4wk-clustered` **and** the carrier is `daily`, `4wk-clustered` or `4wk-spread`; on every other pair it is inert -- the flag is set on thousands of rows, but `week_too_thin` precedes it.

**Evidence.** Rectangle at `c = 0`, per pair (2050 rows each): withheld flag / decisive / forbidden / normal-on-stale-band / rows where the return holds `>= 3` week mornings:

| ret / carrier | flag | decisive | forbidden | normal on stale band | ret_week >= 3 |
|---|---|---|---|---|---|
| daily / daily | 1348 | **150** | 82 | 64 | 1886 |
| daily / 4wk-clustered | 1294 | **42** | 71 | 91 | 1886 |
| daily / 4wk-spread | 1286 | **20** | 75 | 95 | 1886 |
| daily / 3wk | 1272 | 0 | 24 | 100 | 1886 |
| daily / 2wk | 1148 | 0 | 0 | 100 | 1886 |
| 4wk-clustered / daily | 1812 | **150** | 82 | 37 | 1886 |
| 4wk-clustered / 4wk-clustered | 1786 | **42** | 71 | 50 | 1886 |
| 4wk-clustered / 4wk-spread | 1780 | **20** | 75 | 53 | 1886 |
| 4wk-clustered / 3wk | 1776 | 0 | 24 | 55 | 1886 |
| 4wk-clustered / 2wk | 1676 | 0 | 0 | 55 | 1886 |
| 4wk-spread / daily | 1668 | 0 | 164 | 27 | 1722 |
| 4wk-spread / 4wk-clustered | 1668 | 0 | 95 | 27 | 1722 |
| 4wk-spread / 4wk-spread | 1668 | 0 | 87 | 27 | 1722 |
| 4wk-spread / 3wk | 1668 | 0 | 24 | 27 | 1722 |
| 4wk-spread / 2wk | 1610 | 0 | 0 | 27 | 1722 |
| 3wk / daily | 1710 | 0 | 164 | 6 | 1722 |
| 3wk / 4wk-clustered | 1710 | 0 | 95 | 6 | 1722 |
| 3wk / 4wk-spread | 1710 | 0 | 87 | 6 | 1722 |
| 3wk / 3wk | 1704 | 0 | 24 | 6 | 1722 |
| 3wk / 2wk | 1668 | 0 | 0 | 6 | 1722 |
| 2wk / daily | 0 | 0 | 164 | 0 | 0 |
| 2wk / 4wk-clustered | 0 | 0 | 95 | 0 | 0 |
| 2wk / 4wk-spread | 0 | 0 | 87 | 0 | 0 |
| 2wk / 3wk | 0 | 0 | 24 | 0 | 0 |
| 2wk / 2wk | 0 | 0 | 0 | 0 | 0 |

The 424 decisive rows sit at `q = 2, 3` (the third and fourth morning back; `q = 2` only against a `4wk-spread` carrier), `s = 32..69`, half suppressed and half healthy -- T125's population exactly, at the two densities where `k3 = 2`. On the `daily`/`daily` pair the walk shows it as `r = 3, 4` (`unavailable`, decisive), between the two forbidden opening mornings and IDEA-080's three stale-band mornings. The flag is set on 1,148..1,812 rows per pair because the presented dataset on `q >= 7` is the *fallback* carrier (nothing judgeable, Finding 5) whose flag is true but whose week is empty; at `2wk` returns the flag is never set: a (0,3) pattern never puts three mornings in one seven-day window, so the `2wk` athlete's dataset is never judgeable and never holds `MIN_WINDOW_READINGS` later days.

**Confidence.** High: 51,250 rows, deterministic, and the six non-zero cells are exactly the pairs T145's closed form predicts (`k3 = 2` on the return, and a carrier that still holds three week days when the athlete's third morning lands).

**Tradeoff.** The withhold's value is concentrated on the dual-device athlete who captures near-daily on both; for the three sparse return patterns the Negative Class's "four mornings" residual is the whole story and the clause never speaks.

### Finding 2: one captured carrier morning inside the return disarms the decisive withhold, at every carrier density

**Claim.** Measured over the 424 decisive rows' `c = 0` twins: disarm is 320/424 at `c = 1`, 400/424 at `c = 2`, 424/424 at `c >= 3` -- and re-indexed by mornings captured, **`c_cap = 1` disarms every row** (daily 300/300, `4wk-clustered` 204/204, `4wk-spread` 80/80). The residual arming at `c = 1, 2` on sparse carriers is the phase at which the carrier's pattern captured nothing yet (`c_cap = 0`: 0/88 and 0/40 disarmed), not a survival of the clause.

**Evidence.** Disarm of the decisive withhold, by carrier density:

| carrier | c = 1 | c = 2 | c = 3 | c = 4 | c = 5 |
|---|---|---|---|---|---|
| daily | 300 / 300 | 300 / 300 | 300 / 300 | 300 / 300 | 300 / 300 |
| 4wk-clustered | 20 / 84 | 60 / 84 | 84 / 84 | 84 / 84 | 84 / 84 |
| 4wk-spread | 0 / 40 | 40 / 40 | 40 / 40 | 40 / 40 | 40 / 40 |
| 3wk, 2wk | 0 / 0 (never decisive) | | | | |

by carrier mornings captured inside the return (`c = 1..5` pooled):

| carrier | c_cap = 0 | c_cap = 1 | c_cap = 2 | c_cap = 3 |
|---|---|---|---|---|
| daily | - | 300 / 300 | 300 / 300 | 300 / 300 |
| 4wk-clustered | 0 / 88 | 204 / 204 | 84 / 84 | 44 / 44 |
| 4wk-spread | 0 / 40 | 80 / 80 | 60 / 60 | 20 / 20 |

The matched pairs say the same in T130's own table: `TP 17 / FN 0` at `c = 0` on every pair (era 10 included, T158's one moved row), and the moment the carrier captures one morning inside the return `TP` goes to 0 and forbidden to 17/17 -- at `c = 1` for a `daily` carrier, `c = 2` for `4wk-spread`, `c = 3` for `4wk-clustered` and `3wk`, never for a `2wk` carrier (which is never judgeable in the resumed week, so the strap is selected on its own band and reads `hrv_suppressed`, the correct verdict). `FP` is 0 on every one of the 2,550 abandoned rows.

**Confidence.** High. This is T130's structural result restated in the density frame: the clause is a fact about the judged week, and one captured morning rewrites it.

**Tradeoff.** In days elapsed a sparse carrier "protects" the withhold for up to two days; in mornings it never does. Any pricing of the disarm that quotes `c` must quote the carrier's density beside it.

### Finding 3: the §1.7-forbidden rate at `c = 0` is entirely the opening-mornings residual; with overlap it grows past the seventh morning under F006, and vanishes when the two instruments agree

**Claim.** At `c = 0` every forbidden row (1,614 of 51,250, 3.1%) has fewer than three return mornings in the week -- T125/T145's `min(4, k3)` residual: 82 per pair for `daily`/`4wk-clustered` returns against a `daily` carrier (2 mornings x 41 `s`), 164 for `4wk-spread`/`3wk`/`2wk` returns (4 mornings), 24 against a `3wk` carrier and 0 against a `2wk` carrier (which is rarely or never judgeable in the week after it stops). With overlap, rows that promote `hrv_normal` while the athlete holds **three or more** suppressed mornings appear: 450 / 896 / 1,428 / 2,040 / 2,680 at `c = 1..5`.

**Evidence.** Forbidden per `c`, split by the return's captured week mornings:

| c | forbidden | ret_week < 3 (residual) | ret_week >= 3 (T130-comparable) | which `q`, daily/4wk-clustered return | which `q`, 4wk-spread/3wk return |
|---|---|---|---|---|---|
| c = 0 | 1614 | 1614 | 0 | - | - |
| c = 1 | 2525 | 2075 | 450 | 2..4 | 4 |
| c = 2 | 3333 | 2437 | 896 | 2..5 | 4..5 |
| c = 3 | 4116 | 2688 | 1428 | 2..6 | 4..6 |
| c = 4 | 4922 | 2882 | 2040 | 2..7 | 4..7 |
| c = 5 | 5722 | 3042 | 2680 | 2..8 | 4..8 |

Per pair (total = residual + `>= 3`), the `daily` carrier column: `daily`/`daily` 82 = 82+0, 196 = 82+114, 236 = 82+154, 277 = 82+195, 318 = 82+236, 359 = 82+277; `4wk-spread`/`daily` and `3wk`/`daily` 164+0, 164+39, 164+79, 164+120, 164+161, 164+202; `2wk`/`daily` 164, 205, 246, 287, 328, 369 -- all residual, since the `2wk` return never holds three mornings. Against a `2wk` carrier the count is 0 at every `c` and every return density.

**Against shipped F005 (walk, `daily`/`daily`, suppressed return, `T161_MODULE` = the module at `42f7705`):** F005 promotes `hrv_normal` on `r = 1..2` at `c = 0` and `1..7` at `c >= 3`, then re-admits the strap at `r = 8` on a band mixing era A with the return; F006 promotes on `1..2`, `1..5`, `1..6`, `1..7`, `1..8`, `1..9` at `c = 0..5`. **Two extra forbidden mornings at `c = 4, 5`**, because T153's hole clip leaves the returning dataset unestablished (`n` 1..2 in the window) past the seventh morning and the overlapping carrier remains the only judgeable dataset for as long as it holds three week days. That is the `q = 7, 8` tail in the table above, absent on shipped F005 where the return day entering the baseline window re-admitted the strap. T162 must run the rectangle on both modules; the harness does it with one environment variable.

**With correlated instruments (`T161_OVERLAP=suppressed`, same 307,500 rows):** forbidden 1614 / 565 / 343 / 325 / 325 / 325 at `c = 0..5`, and **0 rows at every `c` with `ret_week >= 3`**; what remains is the residual at the phases where the carrier captured no overlap morning. A single 25 ms carrier morning inside a five-day 38/44 week pulls the carrier's own mean below its band. The withhold is disarmed exactly as before (same 424 -> 0), but the carrier says `hrv_suppressed` for its own reasons.

**Confidence.** High on the counts; medium on the F005 comparison, which is one walk and not the rectangle (T162).

**Tradeoff.** T130's 54/72/90 (and F005's Negative Class row) price a flip that exists only if a wrist snapshot reads 38-44 ms on a morning the strap reads 25 ms -- reference §10's "the datasets may not be independent instruments" is load-bearing for this rate in both directions: if they are correlated the exposure is far smaller than priced; if they are independent, F006 is worse than F005 past the seventh morning whenever the watch stays on.

### Finding 4: IDEA-080's stale-band promotion is a stopped-carrier phenomenon bounded by the hole clip, and it needs 14 captured era-A days inside the window

**Claim.** `hrv_normal` on an entirely pre-layoff band occurs on 865 of 51,250 rows at `c = 0` (3.4% of healthy returns), only where the carrier holds fewer than three judged-week days and era A still has `MIN_BASELINE_READINGS` captured days inside `[D-66, D-7]`; the window shrinks with `c` (785, 675, 548, 454, 378) as the overlapping carrier stays judgeable and outranks nothing -- it is selected over a *skipped* strap.

**Evidence.** By returning density at `c = 0`, all carriers:

| return | rows | `q` (morning `q+1`) | `s` | by carrier (daily / 4wk-clustered / 4wk-spread / 3wk / 2wk) |
|---|---|---|---|---|
| daily | 450 | 2..6 | 29..50 | 64 / 91 / 95 / 100 / 100 |
| 4wk-clustered | 250 | 2..6 | 29..41 | 37 / 50 / 53 / 55 / 55 |
| 4wk-spread | 135 | 4..6 | 29..38 | 27 x 5 |
| 3wk | 30 | 4..6 | 29..31 | 6 x 5 |
| 2wk | 0 | - | - | - |

The `s` ceiling is where era A drops below 14 captured days inside the window (`66 - q - s >= 14` at `daily`; proportionally earlier at each sparser pattern); the `q` floor is where the carrier's week falls under three days (`q >= 4` for a `daily` carrier that stopped; `q >= 2` for one that was never judgeable in that week). In the walk it is `r = 5..7` at `daily`/`daily` (three mornings), `r = 3..7` at `daily`/`3wk` (five: the sparse carrier is not judgeable from the third morning), `r = 4..5` at `4wk-clustered`/`4wk-clustered`, and **never** for a `4wk-spread`, `3wk` or `2wk` home dataset in the walk's geometry (a 39-day layoff leaves under 14 captured era-A days in the window by `r = 5`).

**Confidence.** High; the boundaries are arithmetic and the sweep reproduces them.

**Tradeoff.** Down-regulating this (widening the reference set to established datasets, IDEA-080 option 2) would re-import T125's shape; leaving it costs a short window of `hrv_normal` on a 36..66-day-old band, on healthy returns only, for the denser athletes.

### Finding 5: IDEA-084's second silence is 13 mornings at `daily` and 24 / 26 / 33 / never at the sparser return patterns

**Claim.** After a layoff longer than `GAP_RESET_DAYS`, the hole clip makes the returning dataset unestablished from the morning its first return day enters the baseline window (`r = 8` at every density), and it stays unestablished until 14 **captured** mornings are inside `[D-66, D-7]`: own-band verdicts resume at `r = 21` (`daily`), 30 (`4wk-clustered`), 31 (`4wk-spread`), 38 (`3wk`) and not within 40 days at `2wk` -- the `2wk` athlete never holds three mornings in a week and is never judgeable.

**Evidence.** Walk, suppressed return, `c = 0`, `strap-returns` (identical for `snapshot-returns`):

| home / carrier | selected by `r` (H home, C carrier) | forbidden `r` | stale-band normal `r` (healthy) | unavailable `r` | own-band verdict from | flips / 40 days |
|---|---|---|---|---|---|---|
| daily / daily | `CCCCHHHCCCCCCCCCCCCCHHHH...` | 1-2 | 5-7 | 3-4, 8-20 | 21 | 3 |
| daily / 3wk | `CCHHHHHCCCCCCCCCCCCCHHHH...` | - | 3-7 | 1-2, 8-20 | 21 | 3 |
| daily / 2wk | `HHHHHHHCCCCCCCCCCCHHHHHH...` | - | 3-7 | 1-2, 8-18 | 21 | 2 |
| 4wk-clustered / daily | `CCCHHHHCCCC...` | 1-2 | 5 | 3-4, 6-29 | 30 | 3 |
| 4wk-spread / daily | `CCCC...C(30)HHH...` | 1-4 | - | 5-30 | 31 | 1 |
| 3wk / daily | `CCCC...C(37)HHH` | 1-4 | - | 5-37 | 38 | 1 |
| 3wk / 3wk | `CCCC...C(37)HHH` | - | - | 1-37 | 38 | 1 |
| 2wk / daily | `CCCC...C(40)` | 1-4 | - | 5-40 | never | 0 |
| 2wk / 2wk | `CCCC...` | - | - | 1-40 | never | 1 |

"`r` days elapsed" here means the `daily` athlete has captured `r` mornings; the `3wk` athlete at `r = 37` has captured 16, the `2wk` athlete at `r = 40` has captured 12 and holds two in any week. In the rectangle the same shape is the fallback: on `q = 4..19` at `daily`/`daily` (1,198 of 2,050 rows) nothing is judgeable and the carrier is presented `established_read_last`; at every sparser return density the fallback covers `q` up to 24 and **no row of the rectangle reaches an own-band verdict**. The sequence a `daily` athlete sees after a 39-day layoff is: `hrv_normal` from the watch (1-2), silence (3-4), `hrv_normal` on the stale strap band (5-7), silence (8-20), his own verdict (21+) -- 15 silent mornings of the first 20, non-monotone. Shipped F005 on the same walk: 1-2, silence 3-7, own verdict from 8 on a band that mixes era A with the return.

**Confidence.** High.

**Tradeoff.** The clip closes the stale-band mixing F006 §9 called worse than F005 and pays for it with a second silence longer than the first at every sparse density; the flip count per 40-day walk is 3 at `daily` (F005: 1 on the same pair, 7 at `4wk-clustered`/`4wk-clustered` where F005 oscillated week by week and F006 does not).

### Finding 6: AC22's exposure, taken literally, is dominated by one- and two-reading weeks of the dissenter

**Claim.** `hrv_normal` with a non-empty `disagreed_with` is 117 rows at `daily`/`daily`, `c = 0` (258 at `c >= 3`); the `snap/suppressed` part (71 -> 221) is Finding 3's forbidden set seen from the other side, and the remaining `strap/healthy` and `snap/healthy` rows (46 at `c = 0`) are dissenters holding a **single** judged-week reading whose 38 ms lands below their own band's `lo` (the 38/44 fixture's `SD(ln)` gives a half-width of 0.037; `ln 38` is 0.07 under the mean): over all 25 pairs and every `c`, 614 strap-selected rows with a one-reading carrier week and 6,670 snapshot-selected rows with a one-reading strap week, against 61 rows where the dissenting strap holds three mornings.

**Evidence.** Rectangle, per pair and `c`, with the selected tier and value level of the rows (from the per-row CSV; summary): at `c = 0`, `daily` returns 117/112/118/64/28 against `daily`/`4wk-clustered`/`4wk-spread`/`3wk`/`2wk` carriers, of which `strap`-selected healthy returns are 10/15/18/28/28 -- every one with a one-reading carrier week; `2wk` returns 229/149/144/38/0, every one `snap`-selected with the never-judgeable strap dissenting on a single morning.

**Confidence.** High on the mechanism (the CSV names the week size); the literal AC10 reading is the spec's own, so this is not a defect in the code.

**Tradeoff.** T162 should report AC22's rate twice: as specified, and conditioned on the dissenter holding `>= MIN_WINDOW_READINGS` week days; the first is a fixture artefact of alternating values at 1-2 readings, the second is the exposure the Negative Class row describes.

### Finding 7: no false withhold on any switch-away row at any density pair, and a `2wk` new device hands the week back to the abandoned watch on a stale band

**Claim.** 0 of 1,400 switch-away rows withhold; wherever the new strap holds three week days (every pattern but `2wk`) it is selected and reads `hrv_suppressed`. With a `2wk` new strap the athlete is never judgeable on it; against a `daily`/`4wk-clustered`/`4wk-spread` old era the abandoned snapshot -- whose three strays make a judgeable week on a band 40+ days old -- is selected on 42..56 of 56 rows and reads `hrv_normal` (the strays are at the old band's value).

**Evidence.**

| old era / new strap | strap selected | snapshot selected | hrv_suppressed | hrv_normal | hrv_unavailable | strap week days |
|---|---|---|---|---|---|---|
| any / daily, 4wk-clustered, 4wk-spread, 3wk | 56 | 0 | 56 | 0 | 0 | 7 / 4 / 4 / 3 |
| daily / 2wk | 0 | 56 | 0 | 56 | 0 | 2 |
| 4wk-clustered / 2wk, 4wk-spread / 2wk | 14 | 42 | 0 | 35 | 21 | 2 |
| 3wk / 2wk | 21 | 35 | 0 | 0 | 56 | 2 |
| 2wk / 2wk | 56 | 0 | 0 | 0 | 56 | 2 |

**Confidence.** High.

**Tradeoff.** The `2wk`/stray shape is IDEA-080's mechanism on T130's third population: a lone judgeable dataset is its own reference, and three stray captures of a device the athlete left are a judgeable week. Nothing pins it; the T150 pins are all at `daily`.

### Finding 8: the harness reproduces T130/T150 byte for byte at `daily`, and runs unchanged on the shipped-F005 module

`t161-check` asserts 456 generator rows identical to `rows_for` / `inter_rows` / `switch_away_rows` at `daily`/`daily`, and the sparse anchoring. `load_copy("shipped")` now tolerates the F006 module (T158 removed T130's body anchor; the T130 candidate bodies still splice into an F005 module named by `T161_MODULE`). The walk on `42f7705` reproduces the band suite's pinned F005 values (`r = 1..2` normal, `3..7` withheld/unavailable, `8` the strap's own verdict).

## RECENCY_TOLERANCE_DAYS in the new frame

Sweep (e): the rectangle (`s` stepped by 2: 21 x 25 x 2 = 1,050 rows per pair per `c`, 157,500 per tolerance) and the walk (24,000 per tolerance) at every integer tolerance 14..44, on the current module with `RECENCY_TOLERANCE_DAYS` overridden in the worker; 5,626,500 rows in all. `[18, 44]` was F005's band against the fused band and is not what is being tested here; the question is what the constant *does* in the per-dataset frame, on these geometries.

**What it does: nothing below 24, and above 24 a monotone exchange between two shapes, with no knee.** All counts are over all 25 pairs and `c = 0..5`:

| tol | forbidden (via carrier week) | hrv_normal on a stale band | withheld flag | returning dataset selected | carrier selected | walk forbidden | walk stale-band normal | walk flips |
|---|---|---|---|---|---|---|---|---|
| 14..23 | 11534 | 1742 | 82144 | 12656 | 144844 | 1108 | 254 | 840 |
| 24 | 11516 | 1764 | 82136 | 12700 | 144800 | 1108 | 254 | 840 |
| 26 | 11408 | 1888 | 82104 | 12948 | 144552 | 1108 | 254 | 840 |
| **28** | **11336** | **1964** | **82096** | **13100** | **144400** | **1108** | **254** | **840** |
| 30 | 11203 | 2107 | 82076 | 13386 | 144114 | 1108 | 254 | 840 |
| 32 | 11060 | 2254 | 82068 | 13680 | 143820 | 1108 | 254 | 840 |
| 33 | 10870 | 2448 | 82060 | 14068 | 143432 | 1090 | 276 | 896 |
| 36 | 10708 | 2616 | 82048 | 14404 | 143096 | 1034 | 340 | 924 |
| 40 | 10548 | 2789 | 82022 | 14750 | 142750 | 1007 | 367 | 912 |
| 44 | 10465 | 2879 | 82008 | 14930 | 142570 | 1007 | 367 | 912 |

(The full 31-row table, and the same by `c` and by returning density, are in the harness output; `t161-tol` reprints them.)

- **Below 24 the constant is inert on every geometry here.** The rectangle's silences start at `s = 29` and era A is anchored at its end, so the returning dataset's latest baseline-window reading is at least 24 days behind the carrier's on every row where both are judgeable; a tolerance under that skips it identically. There is no knee at `GAP_RESET_DAYS` (21/22): nothing in these populations sits there, so the "28 > 21" partition argument is neither confirmed nor contradicted by this sweep -- it is simply outside the rectangle, which was built (T125) to start where the gate strikes.
- **The rows the constant moves are `c >= 1` rows only** (the `c = 0` column is 815 forbidden at every tolerance). At `c = 0` a stopped carrier is not judgeable when the athlete's third morning lands, so the returning dataset is the lone candidate and its own reference -- the gate has nothing to compare (IDEA-080's point). Only with overlap are two datasets judgeable at once, and only then does the tolerance choose. The withheld flag does not move (the overlap has already disarmed it).
- **What it chooses between is one exposure or the other, roughly one row for one.** From 28 to 44: forbidden-via-carrier-week -871, `hrv_normal`-on-a-stale-band +915 -- every raised day admits an older pre-layoff band, which on a suppressed return says `hrv_suppressed` (the right verdict, from a band up to 66 days old) and on a healthy return says `hrv_normal` on that stale band. From 28 down to 23: +198 forbidden, -222 stale-band. The rate is ~0.4-1% of the forbidden set per day of tolerance and roughly linear; there is no value in 14..44 at which either curve bends.
- **By returning density** the constant reaches `3wk` returns only between 26 and 29 (their era A is established in the window only for `s <= 31`), `4wk-spread` and `4wk-clustered` returns up to 37, `daily` returns through 44 and beyond (bounded by `s <= 52 - q`, era A's 14-day establishment ceiling), and `2wk` returns never (never judgeable). By `c`, the effect grows with `c` (at 44 vs 28: `c = 1` -69 forbidden, `c = 5` -267), because the longer the carrier overlaps the more rows have two judgeable datasets.
- **On the walk** the strap's era A is 33 days behind the carrier's `D-7` at `r = 1`, so tolerances `>= 33` select the strap on its stale band from the first morning back: forbidden mornings 1108 -> 1007 (the `daily`/`daily` opening mornings become `hrv_suppressed` on the era-A band), stale-band `hrv_normal` on healthy returns 254 -> 367, flips 840 -> 912. `[29, 32]` changes nothing on the walk because its layoff is exactly 39 days; the rectangle covers that range through `s`.

**Reading.** In the per-dataset frame `RECENCY_TOLERANCE_DAYS` is no longer an admission gate on the only band; it is the exchange rate between "judge the overlapping carrier's week" and "judge the returning dataset on its pre-layoff band", it operates only while both are judgeable (an overlap of at least one captured carrier morning, and a return of `daily`/`4wk` density whose era A survives the hole clip inside the window), and neither side of the exchange is the athlete's own current physiology. Nothing in 14..44 is a better value than 28 by these numbers, and nothing makes 28 right either: the band that would justify it needs a geometry where a *fresh* judgeable dataset should beat a *stale* one of higher fidelity by a margin the athlete would recognise, and this sweep shows the return population cannot supply it. **The constant stays at 28** (this task changes nothing), and the reference §10 note that `[18, 44]` does not transfer is confirmed in the specific sense that the band's two ends no longer describe anything: the lower end (`>= 18`, four judged weeks) sits in the inert region, and the upper end (`<= 44`, the July trial re-admitted) no longer binds because a stale trial is not judgeable.

## The retained withhold's disarm rate

Per density pair and per `c`, decisive withhold: `disarmed / armed at c = 0` (the flag's disarm, which includes rows where the verdict was `week_too_thin` regardless, is in the harness output and the CSV; it runs 2,706 / 4,536 / 6,696 / 7,842 / 9,892 of 31,962 at `c = 1..5` and is not the number to price):

| ret / carrier | c = 1 | c = 2 | c = 3 | c = 4 | c = 5 |
|---|---|---|---|---|---|
| daily / daily | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 |
| daily / 4wk-clustered | 10 / 42 | 30 / 42 | 42 / 42 | 42 / 42 | 42 / 42 |
| daily / 4wk-spread | 0 / 20 | 20 / 20 | 20 / 20 | 20 / 20 | 20 / 20 |
| 4wk-clustered / daily | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 | 150 / 150 |
| 4wk-clustered / 4wk-clustered | 10 / 42 | 30 / 42 | 42 / 42 | 42 / 42 | 42 / 42 |
| 4wk-clustered / 4wk-spread | 0 / 20 | 20 / 20 | 20 / 20 | 20 / 20 | 20 / 20 |
| the other 19 pairs | 0 / 0 (never decisive) | | | | |
| **all pairs** | **320 / 424** | **400 / 424** | **424 / 424** | **424 / 424** | **424 / 424** |

By carrier mornings captured inside the return: `c_cap = 1` disarms 584 of 584 decisive rows across the three carrier densities that ever arm it; `c_cap = 0` disarms 0 of 128. The rate is therefore **100% per captured morning** and the days-elapsed table above is that fact seen through each pattern's phase. The matched pairs agree (`TP 17 -> 0` at the first captured overlap morning on every pair), and on the suppressed-overlap run the disarm is identical while the forbidden consequence is 0.

## Recommendation

1. **T162 runs the rectangle on both modules** (`T161_MODULE=<git show 42f7705:...>` for F005; the installed module for F006) and reports, per density pair and `c`, the forbidden-with-`ret_week >= 3` count side by side. The one walk measured here says F006 is worse than F005 by two mornings at `c = 4, 5` on the `daily`/`daily` return (forbidden `1..9` vs `1..7`) and better by a 13..33-morning silence in the tolerated direction; AC21 blocks release on the first if the rectangle confirms it. Report it twice: with the carrier's overlap mornings healthy (T130's fixture) and suppressed (`T161_OVERLAP=suppressed`), and say which one the real corpus resembles (reference §10 -- measure the strap/snapshot same-morning correlation before trusting either).
2. **Record in the Negative Class**, beside the T158 bullet: the decisive withhold exists only for `daily`/`4wk-clustered` returns against `daily`/`4wk-clustered`/`4wk-spread` carriers; one captured carrier morning disarms it at every density; the `2wk` athlete is never judgeable on either side of a return.
3. **IDEA-084's second silence should be priced per density** in the Negative Class (13 / 24 / 26 / 33 / never) beside IDEA-080's window (3 / 2 / 0 / 0 / 0 mornings on the walk); the two are one trade, the hole clip's, and the current text prices only the daily case.
4. **AC22's rate is conditioned on the dissenter's week size** in T162's report, or the literal rate will be mostly one-reading weeks of an alternating fixture.
5. **Leave `RECENCY_TOLERANCE_DAYS` at 28** -- not because 28 is re-justified but because on every return geometry swept it is a monotone exchange between two exposures with no value that improves both, inert below 24 and irrelevant whenever the carrier stops; the justification it needs is a geometry where a fresh judgeable dataset should beat a stale higher-fidelity one by a margin the athlete would recognise, which the return population cannot supply. Record in reference §10 that `[18, 44]`'s two ends no longer describe anything (lower end in the inert region, upper end unbinding).

## Reproducing the rows (for T162)

No randomness anywhere: every generator is a deterministic function of its named parameters, so a row is identified by `(sweep, ret_density, car_density, c, s, q | r | era_len+mode | era_len+strays, suppressed)` and two runs produce identical CSVs. From the repo root, with the harness at `.shipyard/spec/references/T130-overlap-sweep-harness.py`:

- `uv run --package runcoach-api python <harness> t161-check` -- asserts the `daily` generators equal T130's/T150's rows before anything is trusted.
- `... t161-rect --procs 12 --csv <path>` -- 307,500 rows, one per (pair, `c`, `s` in 29..69, `q` in 0..24, value level); columns `ROW_FIELDS` (selected, presented_by, withheld, verdict, reason, baseline_n, established, riw, judgeable, skipped, disagreed_with, band_last, a_last_cap, ret_week, car_week, c_cap, stale_band, week_tiers, forbidden, via, decisive, disarmed). ~2 min.
- `... t161-walk --procs 12 --csv <path>` (24,000 rows), `... t161-inter --csv <path>` (5,100), `... t161-switch --csv <path>` (1,400), `... t161-tol --procs 14 --s-step 2 --tol 14:45` (aggregates only, ~25 min).
- `T161_MODULE=<path to an hrv_trend.py> ...` judges the same rows with that module -- `git show 42f7705:runcoach-api/src/runcoach_api/metrics/hrv_trend.py > f005.py` is shipped F005; the adapter uses `selected_view` when the module has one and the F005 series otherwise. `T161_OVERLAP=suppressed` puts the athlete's 25 ms on the carrier's overlap mornings. The harness writes nothing unless `--csv` is given; the CSVs behind this document were written to the session scratchpad, not the data dir, since T130's harness never wrote artefacts there.
- The AC21 comparison is a join of two such CSVs on the identifying columns; per-row `forbidden`, `via`, `decisive` and `stale_band` are already computed on both sides with the same definitions (the *Verdict vocabulary* above).

## Open Questions

- Is a wrist snapshot on a strap morning correlated with the strap's reading (reference §10)? The whole forbidden-flip pricing (T130's and this one's) turns on it; the harness has both variants but the corpus has not been measured.
- Should the hole clip (§5.4 (v)) exempt a dataset's own return inside the judged week, or should (iv)'s "free" be qualified to `<= GAP_RESET_DAYS` -- IDEA-084's question, now with the per-density cost above?
- The `2wk` switch-away row (Finding 7) promotes `hrv_normal` on the abandoned device's 40-day-old band from three stray captures; is that IDEA-080 option 2's population, and does anything pin it?
- The rectangle's `own` rows (2050 per `c`, `q >= 20`, `daily` returns) rebuild a band from 20 suppressed mornings and read `hrv_normal` on it -- is a return that stays suppressed for three weeks a new baseline or a three-week suppression? §3.7.4 says the band is the baseline's; the sweep says it becomes the return's.
