# F006 — the dataset model, the measurements behind it, and the retirement list

Extracted from `spec/features/F006-per-tier-hrv-datasets.md` at the 200-line limit. Everything here
was measured on 2026-09-18 at `3c01c9f` by decoding the real fixture corpus with `fitdecode` and by
grepping the shipped module, or is a dated user decision from that day's `/ship-discuss IDEA-071`.

## 1. Why the dataset key is the tier and not the device

The direction [[IDEA-071]] records is "per-device datasets, independently baselined". The column that
name points at cannot carry it.

`mapping._build_source_device` (`mapping.py:196`) takes `_first_of(by_name, "device_info")` — always
the `creator` entry, i.e. the **head unit** — and composes `f"{garmin_product} fw{software_version}"`.
Measured across all eleven fixtures, **ten return the single string `fr945_lte fw17.4`**; only
`dev_fields_run.fit` differs (`fr955 fw19.18`). A chest-strap RR capture and a wrist-PPG snapshot
taken on the same watch are **identical** under this column.

It is simultaneously **too coarse** (strap vs wrist invisible) and **too fine** (`fw17.4` is in the
key, so every firmware push mints a new identity — and under per-device baselines a new identity is a
lost baseline, i.e. silence).

### True per-unit identity is present and is discarded

`strap_hrv_capture.fit` carries 15 `device_info` messages:

| entry | `source_type` | `garmin_product` | `serial_number` | `software_version` |
|---|---|---|---|---|
| `device_index: 'creator'` | `local` | `fr945_lte` | **3408655245** | 17.4 |
| `device_index: 2, 6` | **`antplus`** | **`hrm_pro_plus`** | **3611410126** | 8.9 |

`wrist_ppg_hrv_snapshot.fit` (14 messages) and `sample_health_snapshot.fit` (10) carry **no `antplus`
entry at all** — every entry is `source_type: 'local'`. So the corpus already distinguishes the watch
(creator serial), the strap (antplus serial, stable across its own firmware because
`software_version` is a separate field), and the absence of a strap (no antplus entry).

**Caveat, stated rather than glossed.** One athlete, one watch, one strap, all `manufacturer:
'garmin'`. A non-Garmin strap carries a different manufacturer, and one entry in this same file
carries an unresolved numeric `garmin_product: 21`. The *shape* is proven; *coverage across vendors
is not*. This is why AC14 makes an unresolvable identity a first-class case rather than an error.

**Decision (user, 2026-09-18):** key on the tier (N = 3); resolve and persist sensor identity anyway
as groundwork (AC13), and leave it unread by the rule (AC15).

## 2. The missing model, and why the qualifier family is exhausted

`grep -c source_device runcoach-api/src/runcoach_api/metrics/hrv_trend.py` → **0**. The module says it
itself: *"the returning-or-brand-new-device shape, stated without a notion of 'device' in the
vocabulary"* (`hrv_trend.py:524`).

So ten ratified qualifiers — T093, T094, T095, T106, T107, T116, T117, T125, T129, T132 — are all
predicates **inferring a device event from counts and orderings of readings**. [[T130]] measured that
family exhausted over 12,300 rows: every predicate keyed on the shape of the judged week has a
carrier-overlap `c` that defeats it, because carrier overlap is a fact about the judged week.

**Stated in one line: the rule is inferring, from statistics, a fact the ingested data carries
directly.** The reframe does not add a tenth qualifier; it removes the question most of them answer.

## 3. The selection rule as decided

Among datasets that are **judgeable** — established (≥ `MIN_BASELINE_READINGS`) **and** holding
≥ `MIN_WINDOW_READINGS` distinct judged-week days — promote the **highest-fidelity** one, unless its
latest **baseline-window** reading falls **more than** `RECENCY_TOLERANCE_DAYS` behind the latest
baseline-window reading of any **established** dataset, in which case it is skipped.

Three properties carried over from the shipped `_recency_struck`, deliberately and not by accident:

- the comparison is **between datasets**, not against `D` — the reference maximum is taken once,
  simultaneously, over every **established** dataset, those that are not judgeable and those about to
  be skipped alike, while the **candidates** it strikes from are the judgeable ones, and an
  established dataset that is not judgeable is never skipped but can hold the reference maximum
  (AC7; `research/00` HRV-15, decision C10);
- the bound is **strictly greater than** (AC7);
- `RECENCY_TOLERANCE_DAYS` = 28 is **reused, not minted** — it rests on HRV-16's two reasons alone,
  (i) four judged weeks and (ii) greater than `gap_reset_days` (21); the `[18, 44]` bracket was measured
  against the fused band and is not cited for per-tier datasets (`research/00` GATE-03, GATE-07).

**Amended 2026-09-21 (T164, following `research/00` HRV-51's amendment of 2026-09-20, then
`spec/03` §3.7.3/§3.7.4, then AC6/AC7 — this reference was missed in that sweep).** The rule and
the first property above read, from 2026-09-18 until now: *"unless its latest reading falls **more
than** `RECENCY_TOLERANCE_DAYS` behind the latest reading of any judgeable dataset, in which case it
is skipped"*, and *"the comparison is **between candidates**, not against `D` — so a lone judgeable
dataset is its own reference and is never skipped"*. Both halves are superseded, for two separate
reasons. **(a) The window was never named here**, which is §9's defect: AC6 named it normatively on
2026-09-18, matching `_last_read`'s scope, and this section kept the unqualified phrasing §9 is
written to refute. **(b) The reference population is every established dataset**, not the judgeable
ones; the candidates the gate strikes from remain the judgeable datasets, so nothing in AC8, in "the
selected dataset decides" or in the retained withhold moves. [[T162]] measured the narrowing this
section described: `hrv_normal` promoted on an entirely pre-layoff band rose from shipped F005's
**1,896 to 3,705 of 307,500** return-rectangle rows (×1.95) and **96 to 254 of 24,000**
device-return-walk rows (×2.65), worse at every `c`, on 82 of 150 cells, identically under both
overlap variants — the under-calling direction, and AC21's blocking one. [[T164]] widened the
population and the rate returned to shipped F005's **exactly**: **1,896** and **96**, flat at 316 per
`c`, cell for cell across all 25 capture-density pairs. **What it cost**, priced in §11: a **lone
judgeable** dataset is no longer automatically its own reference — an established but *weekless*
dataset read later strikes it — so **every judgeable candidate can be skipped at once**, a state
this reference's own pins had held unreachable, and AC9's fallback then presents one verdict-free;
the athlete pays in silence, roughly **3,600 more silent rectangle mornings** (`hrv_unavailable`
243,326 → 246,944 of 307,500) and 316 more on the walk (15,996 → 16,312 of 24,000).

**This is §3.7.1's ratified hierarchy preserved, not a new precedence.** §3.7.1: *"the system prefers
a chest-strap RR capture it reduces to rMSSD itself, and degrades to a device-computed numeric
resting rMSSD, admitted at reduced fidelity (an ordinal rank in selection), before it treats HRV as
unavailable."* What the reframe changes
is that the loser keeps its own band. That is precisely what made the hierarchy unsafe before:
striking a tier destroyed the only yardstick, which is why T117's gate had to exist and why T125 then
had to patch it.

### The two senses of "quality", split

| sense | what it is | role |
|---|---|---|
| **Fidelity rank** | the ordinal in `TIER_FIDELITY` / `_FIDELITY_RANK`; already shipped, ratified by §3.7.1 | **arbitrates selection** |
| **Confidence weight** | a numeric per-tier weight, which Section 3 neither computes nor emits and which is deferred to Section 6's readiness fusion (`research/00` HRV-04, HRV-54, decision C19) | **not emitted at all, and it never arbitrates** |

IDEA-071's cost 1 — a new recency × quality exchange rate — therefore never arises, which was the
point of splitting them.

**Corrected 2026-09-21 (T159).** The second row's role cell read "reported on `datasets[]`,
never arbitrates" from 2026-09-18 until then, and its first half was already false when the
response block shipped. `spec/03` §3.7.4 is the authority above this reference and states that
**no confidence weight is computed in Section 3 at all** — weighting by tier confidence is
deferred to Section 6's readiness fusion — so putting one on the wire would mint a constant
Section 3 does not own, which §10's "no new constant" refuses in the same breath. What
`datasets[]` carries instead is **`fidelity_rank`**, the ordinal the first row names, beside each
dataset's `tier`, `n`, `established`, `band`, `last_read`, `week_days`, `week_mean`, `below`,
`reset_on` and `reset_reason` — the rank being what makes `selected_reason` recomputable by hand
from the response. The split this table draws is unchanged, and since 2026-09-19 it is pinned:
`test_the_numeric_confidence_weight_never_participates_in_selection` (T156) swaps which tier
carries the noisy readings and watches the selection stay where it was, and its AST half reds if
any name mentioning a weight or a confidence reaches `select_dataset`, or the module at all.

**The divergence this section recorded is closed, and the authority was corrected last
(2026-09-21).** From 2026-09-18 until then `research/00` (now HRV-54) still said of the confidence
weight that "it is reported per dataset and left to Section 6". That document is the decision
authority above `spec/03`, so this reference, which sits below both, did not amend it; the conflict
was recorded here for the next reader instead of being resolved from underneath, as
`project-domain-and-spec-fidelity` requires of a research-corpus conflict. HRV-54 now states what
shipped — `datasets[]` carries `fidelity_rank`, **no** confidence weight is emitted or computed in
Section 3, and the weighting is deferred to Section 6's readiness fusion (§3.7.4), while HRV-19 has
the fidelity rank alone arbitrate selection — so the exchange rate still never arises, and now for
the stronger reason that there is no weight to trade recency against. Until F008 the authority
preserved the superseded half in place and in a dated correction bullet of its own; it now states
only the current rule, and the dated summary is H-30 in `research/00-history.md`. Nothing in the
code or in any pin moved.

### Forms considered and rejected, with the reason

| form | rejected because |
|---|---|
| recency × quality score | mints the exchange rate; §3.7.4 says no confidence weight is computed in this section today, so it would need its own research basis |
| recency only | highest PRIN-14 exposure — the noisier wrist tier is read most nights, so it habitually wins and can promote `hrv_normal` while the strap sits below its own band; inverts §3.7.1 precedence |
| coverage-first | a daily wrist tier outranks a 3×/week strap on coverage alone, so the athlete is habitually judged on the lower-fidelity instrument; same §3.7.1 inversion, reached differently |
| stability-first | makes `judge` path-dependent — it needs yesterday's selection, so either persisted state or recomputation of every prior day |

**A measurement that does *not* transfer, and must not be cited against this rule.** The
parameter-free recency form built on 2026-09-15 turned **5 pinned tests red in both error
directions**. That measured an *admission gate on a fused band* — striking a tier removed the only
band, so a daily watch beating a 3×/week strap was an error. Under N datasets, promoting the watch
means judging the week against **the watch's own band**, which is correct rather than wrong. Those
five pins must be re-derived under AC16 either way.

## 4. The PRIN-14 exposure, stated

The forbidden shape is: **the promoted verdict is `hrv_normal` while another established dataset
reads below its own band.** Because the selected dataset decides (user decision, 2026-09-18), the
feature's entire PRIN-14 exposure sits in selection.

Quality-first is the strongest available defence — any `hrv_normal` it promotes is made on the best
available instrument — but it is exposure, not immunity. A consumer reading `hrv_status` alone (every
consumer today, and Section 6 as specified) is **not** told about `disagreed_with`; the disagreement
is legible afterward, not acted on.

**This rate is newly measurable.** Under the fused rule the losing tier had no band, so it could not
be computed at all. AC20 requires it swept before release, and it ships only as HRV-25's population — the named PRIN-14 exception PRIN-15 lists, owned by IDEA-099, which may not grow (`research/00` HRV-25, PRIN-15).

## 5. Stability — the two flip triggers

| # | trigger | speed |
|---|---|---|
| 1 | the fidelity leader's latest baseline-window reading falls more than `RECENCY_TOLERANCE_DAYS` behind the latest-read **established** dataset (§3) | slow, rare — but reachable more often since T164 widened the reference population from the judgeable datasets to the established ones, which is what the ~3,600 extra silent rectangle mornings in §3 are |
| 2 | its judged-week days fall below `MIN_WINDOW_READINGS` | **fast, common** |

Trigger 2 dominates, and **every candidate form inherits it identically**, because judgeability is a
precondition under all of them (AC8) — promoting a dataset with 2 week-readings would emit
`hrv_unavailable` while another dataset could have spoken.

A 7-day sliding window covers each weekday exactly once, so a **Mon/Wed/Fri** wearer holds *exactly*
`MIN_WINDOW_READINGS` permanently:

- one missed session → 2 for the seven days that window contains it → flips to the other dataset;
- that day leaves the window → back to 3 → flips back.

**One missed session costs seven days on the other dataset**, with a verdict change possible at each
end. A 2×/week wearer sits permanently at 2 and is never a candidate; a 4×/week wearer sits at 3–4
and absorbs one miss. The danger zone is exactly the 3×/week wearer — the population F005 already
prices as the oscillation.

**It cannot be hysteresis'd away.** Letting the incumbent hold at 2 judged-week days while a
challenger needs 3 would have a 2-reading week produce a verdict, and §3.7.4 is explicit: *"fewer
than three readings in the judged week is HRV unavailable, whatever they say."* Ratified; not
re-opened here. **Proposed and withdrawn during the 2026-09-18 discussion — do not re-propose.**

It is **pre-existing, not created** — `CRITIC-F005` priority 3, open today — and marginally improved
here, because the challenger's band is now continuously warm rather than possibly cold.

**Consequence, and the reason the form was chosen on PRIN-14 rather than on stability: the flip rate
will be dominated by capture-density geometry, not by selection form.**

## 6. The retirement list

Each of these is a candidate for removal, and **each requires its own red-then-green pin** over the
population it was originally added to close (AC16). The qualifier's existing pins are **re-pointed at
the new mechanism, never deleted**.

| added by | condition | population it closed | expected disposition |
|---|---|---|---|
| T093 | rule 1's week-coverage half | a tier with count but no week | **subsumed** — judgeability (AC8) is the same condition, stated once |
| T094 | rule 4(c) interleaving, both windows | a finished trial aged into the previous window | **re-derive at DATASET scope — not retired** (AC17) |
| T095 | the density tolerance | one stray capture silencing a whole era | **re-derive at DATASET scope — not retired** (AC17) |
| T106 | the week half as first ordering term | which admitted boundary is chosen | ~~**subsumed** — the three-term ordering key has nothing left to order~~ — **wrong, and measured so: KEPT** (see below) |
| T107 | the clip survives a coverage gap | a gap cancelling the era clip | **retain** — coverage-gap semantics are untouched by this feature |
| T116 | symmetric establishment gate | `hrv_normal` on a thin baseline | **retain** — per-dataset, unchanged and still required |
| T117 | relative recency tolerance | the abandoned July trial owning a week | **redeployed** — becomes the AC6 gate, same constant, now safe |
| T125 | the order clause at the verdict | the week predating the athlete's return | **NOT subsumed — see §9.** Restated as AC6's baseline-window gate |
| T129 | unclipped stray counting | a gap *creating* an era boundary | **re-derive at DATASET scope** with T094/T095 (AC17); `coverage_gap_reset` itself stays global (AC16) |
| T132 | the withhold widened to a brand-new device | adoption | **out of scope** — adoption is excluded from this feature |

**What was measured, against the expectations above (T160, 2026-09-19; corrected here 2026-09-21).** This table is the *planning-time* expectation and is left standing as that. T160 produced the honest list, and it is shorter: **two** mechanisms are retired — `resolve_baseline_tier`'s role as the cross-tier arbitration, and the `off_baseline_tier` exclusion (T152), retired — each with its three-valued pin in `runcoach-api/tests/test_hrv_three_valued_retirements.py`. The **T106 row above was wrong**: the three-term era-boundary ordering key is **kept**, as a sub-mechanism of the per-dataset era clip (§6 rows T094/T095/T129), and judgeability cannot subsume it — judgeability decides *which dataset is selected*, the key decides *which of several admitted era boundaries a dataset's own band is clipped at*, and no value of the first determines the second. It is live in the shipped module, reached from `tier_change_reset`, and covered by `test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of` with `test_the_era_boundary_ordering_key_keeps_its_three_terms` per dataset — it carries no task-labelled pin, so a grep-based retirement audit reports it unpinned and is wrong. `research/00` HRV-83 carried the same error in its retirement clause and was corrected first, on 2026-09-21 ([[IDEA-086]]); this note follows it, per precedence.

**IDEA-071's own evidence that at least one is near-vacuous:** T117's form 1 strikes only tiers with
`week_counts < MIN_WINDOW_READINGS`, while the selection loop directly below returns only tiers with
`>= MIN_WINDOW_READINGS` — **disjoint sets**. Deleting T117's gate outright reds 4 of 401; form 1
reds 3. [[T145]] then measured T125 and T132 to change **no verdict at all** at sub-daily capture
density. Neither observation licenses deletion on its own — AC16's pin does.

## 7. Measurement obligations

- **`switch_away_rows` and `inter_rows` are pinned before any candidate form is measured** (AC18).
  The suite is green on things these two catch: the candidate that met [[T130]]'s written
  deliverable was green only because the one `trial_then_switch` fixture places its strays at
  `4/3/2`, the single placement where the medians tie. Move them to `3/2/1` and it false-withholds.
- **Capture density is a required axis** (AC17), at least daily, 4/wk clustered, 4/wk spread, 3/wk,
  2/wk. No sweep in nine cycles varied it: `T125-fix-form-measurements.md` states its rectangle as a
  contiguous **daily** return run in all 2050 rows; [[T130]]'s harness reuses that rectangle;
  [[T123]]'s half-1 generator *requires* a judged week of ≥ 3 new-tier days by construction.
- **Any walk indexed by "days since return" must state whether it means days elapsed or mornings
  captured.** `test_hrv_trend_band.py`'s device-return walk reasons as though they are the same, and
  `_seed_return_series` seeds daily, so the identity holds only there.
- Existing corpora to reuse rather than rebuild: `spec/references/T130-overlap-sweep-harness.py`
  (the 2050-row return rectangle × `c` = 0..5, and nine measured candidates) and
  `spec/references/T125-fix-form-measurements.md`.

## 8. Reuse — what this feature composes rather than invents

Scanned 2026-09-18. No hand-rolled equivalents to replace and no dead code beyond §6's retirement
list; the findings are all reuse, and they are why 13 points is not larger.

- **The per-tier primitives already exist.** `_tier_counts` (`hrv_trend.py:451`), `_of_tier` (`:460`)
  and `_last_read` (`:465`) already partition readings by tier, and `build_band` (`:1210`) /
  `ln_rmssd` (`:1201`) already build a band from an arbitrary reading set. Constructing N datasets is
  a **re-composition of shipped helpers over a different partition**, not new math. `_tier_counts` is
  also documented as "the one place the unit is taken" (distinct local days, T095/IDEA-047) — keep
  that property, because AC1/AC4 depend on it.
- **The unresolvable-sensor case is already contemplated in the dedup design.** `mapping.py:144`
  already reasons about "non-Garmin devices, or a file missing `file_id`/`device_info`" with respect
  to SQLite's NULL handling in `UNIQUE (source_device, start_time)`. AC14 extends existing thinking
  rather than introducing a new failure mode — but note `derive_session_id` hashes that same pair, so
  neither the column's value nor the constraint may move (AC13).
- **`_first_of(by_name, "device_info")` is the single extension site** for AC13: the grouping
  `to_canonical` already builds is the full message list, so resolving the `antplus` entry needs no
  new parse pass.
- **Two sweep harnesses already exist** — `T129-direction-search-harness.py` and
  `T130-overlap-sweep-harness.py` — and AC17/AC18 extend them with a density axis rather than
  building new ones.

## 9. The stale-band defect — found by the cycle-0 critic, reproduced, and fixed in spec

The first draft of F006 claimed T125 was **"subsumed — a returning device's own band is already
warm"**. That is false, and the draft was **worse than shipped F005** for the population T125 closed.
Verified against `hrv_trend.py` on 2026-09-18: `baseline_window` is `[D-66, D-7]` (`:355`) and
`_last_read` is taken over `baseline_readings` only (`:959`).

### The reproducing series

All days local. Watch = `health_snapshot`, daily throughout. Strap = `chest_strap_raw`.

| span | strap |
|---|---|
| `D-66 … D-36` | captured daily — 31 distinct days, ≥ `MIN_BASELINE_READINGS` |
| `D-35 … D-5` | **silent, 31 days** — no coverage gap fires, because the watch carried the series |
| `D-4, D-2, D-0` | back — 3 judged-week days, ≥ `MIN_WINDOW_READINGS` |

**Shipped F005:** `_recency_struck` compares baseline-window `last_read` — strap `D-36`, watch `D-7`,
gap **29 > 28** → strap struck → watch selected → judged on the watch's warm band. **Correct.**

**F006 first draft:** the strap is established (31 ≥ 14) and judgeable (3 ≥ 3), so it is the
highest-fidelity candidate. AC6 said *"its latest reading"* with **no window named**, and the strap's
latest reading is `D-0` → gap 0 → not skipped → selected → **the athlete is judged against a band
whose every reading is 36–66 days old and entirely pre-layoff.** If that stale band sits low, the
verdict promoted is `hrv_normal`: the under-calling direction, on the exact mechanism T125 and three
review cycles were spent on.

### Why it happened, and the lesson worth keeping

Nothing in the first draft checked that the **selected dataset's baseline readings are
contemporaneous with its judged week**. The recency gate checks the *dataset's* recency; per-tier
baselining removes every clip that checked the *baseline's*.

**Per-tier baselining does not answer IDEA-071's missing model — it only makes it askable per
dataset.** The idea's own words are *"none of them asks whether the week's readings represent the
athlete now"*, and a per-dataset band satisfies "warm" in the sense of *existing*, not in the sense
of *being about him now*. Conflating those two is what produced a spec with fewer guards than the
feature it re-derives.

### The fix, as specced

- **AC6** names the baseline window normatively, matching `_last_read`'s scope.
- **AC7** fixes the reference set as simultaneous, not iterative — and, since T164 (2026-09-20, `research/00` HRV-51 amended first), as every **established** dataset rather than the judgeable ones, the candidates it strikes from staying the judgeable ones; see §3.
- **AC17** keeps the era clip (T094/T095/T129) at **dataset** scope rather than retiring it — it is
  the only mechanism that ever removed a stale era from a band.
- **AC16** keeps `coverage_gap_reset` **global**. The partition is by scope (`research/00` HRV-52): the
  coverage-gap reset (HRV-35) covers the silence of the whole series, while the hole clip (HRV-37) and then
  the recency gate (HRV-15) cover one dataset's silence while another dataset carries the series — the
  ratified *"not a race but a partition"* justification for `RECENCY_TOLERANCE_DAYS` (28) >
  `GAP_RESET_DAYS` (21). A per-dataset gap reset would put a second reset on one dataset's silence, beside
  the hole clip and the gate, voiding the reasoning that set 28.
- **AC21** requires every sweep to run against shipped F005 too, so "no worse" is a gate and not a
  hope.

## 10. Technical detail moved out of the feature file

- **N is 3 in the enum and 2 in every real corpus.** `health_api_overnight` "is in the enum but is
  never written by the classifier" (`hrv_trend.py:218`). Every AC is exercised at N = 2; the
  3-dataset shape is **untested by construction** and must not be assumed covered.
- **`RECENCY_TOLERANCE_DAYS` is inherited, not re-justified.** Its `[18, 44]` bracket was measured over
  F005's 394 tests against the **fused** band; the upper end was "the July trial is re-admitted",
  which under F006 harms nothing because a stale trial is not judgeable. **Do not cite `[18, 44]` as
  if it transferred**: the constant (28) rests on HRV-16's two reasons alone, (i) four judged weeks
  and (ii) greater than `GAP_RESET_DAYS` (21), until a re-measurement against per-tier datasets is
  recorded (`research/00` GATE-03, decision C37). This is the same trap as the 2026-09-15
  parameter-free measurement in §3.
- **Pipeline order is verdict-determining.** Per-dataset clips → establishment → the simultaneous recency reference set, taken over the **established** datasets → judgeability, which fixes the
  candidates that reference strikes from → selection → promotion. (*Amended 2026-09-21 with §3: this read "establishment → judgeability → the simultaneous recency reference set" while the reference population was the judgeable datasets. Since T164 the reference does not depend on judgeability at all — `select_dataset` takes it over every established dataset and intersects the struck set with the judgeable ones.*) `judge` resolves `unavailable_reason`
  from a fixed guard order over **one** series (`:1246`); N datasets need a defined order *across*
  datasets (AC9). T145 happened because a fixed order was treated as incidental.
- **`test_hrv_unavailable_causes.py` is an AST oracle over `judge`'s source**, and §3.7.4 asserts six
  causes. Any reshaping of `judge` reds it — budget for it.
- **The fidelity-rank / confidence-weight split is pinned by no AC.** It is the decision that keeps
  IDEA-071's cost 1 from arising; pin it during task decomposition, along with "no new constant".
  *Done, 2026-09-19 (T156): `test_the_numeric_confidence_weight_never_participates_in_selection`;
  and no weight is emitted, `datasets[]` carrying `fidelity_rank` — §3.*
- **`research/00` HRV-34 is amended first.** §3.7.3's main clause stated that adopting or
  abandoning the strap **is** a baseline re-establishment withholding any verdict; `research/00` (HRV-34)
  now states that a source change triggers no rule-level re-establishment and collapses no SWC
  band, the coverage-gap reset being the only one that re-establishes (HRV-34, decision C03). The
  authority is amended before `spec/03` (project rule: `research_00` governs, DOC-01), and the claim is
  swept tree-wide (`sweep-the-claim-not-the-diff`).
- **Assumption to test, not assume: the datasets may not be independent instruments.** The HRM-Pro-Plus
  broadcasts over ANT+ **to the same watch** that produces the `health_snapshot`. If a snapshot on a
  strap morning is computed from the same beats, `disagreed_with` measures vendor post-processing
  rather than two views of physiology, and AC22's rate measures something other than what it claims.
  AC3's "one reading to each dataset" would then be a *correlated* pair biasing both bands toward the
  same mornings. **Measure this before trusting AC22.**

## 11. Negative Class — the costs table (the feature file keeps its priced bullets)

This section holds the **table** of accepted costs — all seven rows, moved out of the feature file
so the feature stays inside its 200-line cap. It is **not** the whole Negative Class, and a reader
who stops here has not read it. The feature file (`spec/features/F006-per-tier-hrv-datasets.md`,
§ Negative Class) keeps the governing row beside **eleven further priced bullets that are not
reproduced here** — among them the one PRIN-14 rate this release **deferred rather than paid** (a
suppressed return promoted `hrv_normal` from an overlapping carrier’s week, worse at `c = 4` and
`c = 5`, existing only if the two datasets are independent instruments; user decision 2026-09-20,
T162 Finding 2 / T164), the honest retirement count and its three-valued pins, T093’s two halves,
the per-dataset reported reset, T130’s carrier-overlap disarm, AC23’s flip-rate score, and
IDEA-087’s unmeasured independent-instrument question. The two documents are complements, not a
copy and an original (heading corrected 2026-09-21, sprint-006 final spec review, finding 3: it
read “full table, moved from the feature file”, which a reader following the pointer took to mean
the Negative Class had moved in full).


| cost | direction and why it is accepted |
|---|---|
| **The PRIN-14 promotion exposure.** The selected dataset decides, so `hrv_normal` can be promoted while another judgeable dataset reads below its own band. A consumer reading `hrv_status` alone — every consumer today, and Section 6 as specified — is not told about `disagreed_with` | **Up-regulation while contrary evidence exists — the direction PRIN-14 forbids.** It ships only as HRV-25's population, the named PRIN-14 exception PRIN-15 lists, owned by [[IDEA-099]], whose count PRIN-26 states, pinned by test_hrv25_population.py, and it may not grow (`research/00` HRV-25, PRIN-15, decision C06). Quality-first explains why the selected dataset decides: it promotes the *best available* instrument (~8× lower rMSSD error than PPG), and suppressed-wins lets a noisier dataset veto a good week. **Newly measurable** — under the fused rule the losing tier had no band. AC21/AC22 gate it: any worsening against F005 blocks release, save the exceptions PRIN-15 lists, each of which may not grow: the F005-parity population and `DEFERRED_EXCEPTION`, both owned by [[IDEA-087]] (T168), and HRV-25's population |
| **Same-tier device replacement (strap A → identical strap B) stays invisible** | Costs **0** silent days — holds the tier constant, fires no reset, opens no era boundary. Benign; what [[T141]] withdrew the composed-silence paragraph over |
| **§3.7.3's device/firmware re-establishment clause is retired, not pending** | `research/00` HRV-34: a source change never triggers a rule-level re-establishment, and only the coverage-gap reset (HRV-73) re-establishes. The column the clause pointed at carries the **watch's** firmware, so honouring it would have re-established a *strap* dataset when the *watch* updated — the wrong event. F007 persists the identity the clause assumed |
| **The 18-day adoption silence, and `week_too_thin` on 16 of those days** | Out of scope by user decision, 2026-09-18. A never-used device holds no baseline under any scheme. Priced by [[T137]]/[[T138]], unchanged here |
| **A 3×/week wearer's dataset flips for seven days on one missed session** | Pre-existing (`CRITIC-F005` priority 3), inherited identically by every form, un-fixable without re-opening §3.7.4's count rule. AC14 makes it visible in `points[]`; AC23 gates it against F005 |
| **A hole of at most `GAP_RESET_DAYS` silent days, or one whose resumption is after `D-7`, is not clipped** (AC17, T153) | The first still mixes the eras either side of it — bounded by three weeks of silence, either direction, unreported; the second leaves the pre-layoff band in place, unreported, and hands the question to AC6, which does not skip a dataset fewer than 29 days behind the latest-read **established** dataset (the reference population as widened by T164, 2026-09-20; §3). Accepted because the constant has one meaning (`coverage_gap_reset`'s) and a per-dataset clip firing earlier than the global gap would make two rules disagree about the same number of days (the 28 > 21 partition); the straddling case is AC6's population by AC17's own text. Who notices: nobody from `hrv_status`; `baseline.window` shows the first, `datasets[]` (T159) will show the second |
| **A lone judgeable dataset is no longer its own reference, and every judgeable candidate can be skipped at once** (AC6/AC7 as widened by T164, 2026-09-20; §3) | The price paid for restoring shipped F005's stale-band promotion rate **exactly** — `hrv_normal` on an entirely pre-layoff band, 3,705 → 1,896 of 307,500 rectangle rows and 254 → 96 of 24,000 walk rows. An established but **weekless** dataset read later now strikes the returning one, so the athlete pays in **silence**: ~3,600 more silent rectangle mornings (`hrv_unavailable` 243,326 → 246,944) and 316 more on the walk (15,996 → 16,312). The state this reference once held unreachable — every judgeable dataset skipped at once — is now reachable, and AC9's presentation fallback handles it, conferring no verdict. Accepted because the alternative is the under-calling direction at roughly twice shipped F005's rate, which AC21 blocks on. Who notices: nobody from `hrv_status` — the athlete is told nothing rather than told something stale. Full measurement in the feature file's Negative Class and `spec/references/F006-no-regression-report.md` |


## 12. Decision log (full text, moved from the feature file)

## Decision Log

All entries 2026-09-18, from `/ship-discuss IDEA-071`.

- **Dataset key is the tier, not the device** — stored `source_device` is the *watch*.
- **A morning with two captures feeds both datasets** — the alternative leaves the watch dataset
  cold, defeating the reframe's purpose.
- **The selected dataset decides; disagreement reported, never overriding** — over suppressed-wins
  and withhold-on-disagreement. This puts the whole PRIN-14 exposure in selection, which is why the
  selection form was re-opened rather than left measure-first.
- **Selection is quality-first with a recency tolerance gate** — over recency × quality (mints an
  exchange rate), recency-only and coverage-first (both invert §3.7.1 precedence).
- **No hysteresis in dataset selection** (decided 2026-09-21, replacing the deferral this entry
  first recorded): the system MUST NOT add hysteresis to dataset selection, and the worsened
  dataset-flip set must remain the 80 pinned `walk_flips` cells at `car_density = 2wk` (AC23;
  `research/00` GATE-02, decision C05).
- **Retirement requires a three-valued pin** — corrected from a two-valued form that was
  **unsatisfiable**, since a pin over a retired qualifier's population is green on shipped F005 by
  construction.
- **The adoption silence stays out of scope**, including the wrong `week_too_thin` reason on 16 of
  its 18 days, and the seventh `unavailable_reason` cause F005 names.
- **Critic finding, accepted and fixed: AC6 had no window and re-created T125.** A first draft
  claimed T125 and the era clip were "subsumed". Reproduced against the code: a strap established
  `D-66…D-36`, silent 31 days while the watch carried the series, back on `D-4/D-2/D-0` is judgeable
  and highest fidelity, so an unqualified gate selects it and judges the athlete against an entirely
  pre-layoff band — **worse than shipped F005**, which strikes it at 29 > 28. AC6 now names the
  baseline window; AC17 keeps the era clip per dataset. Full record in the reference, §9.
- **`coverage_gap_reset` stays global** (AC16), preserving the *"not a race but a partition"*
  justification for 28 > 21 that a per-dataset gap reset would void.
- **Sensor identity split to F007** — `db.py` is `CREATE TABLE IF NOT EXISTS` with no migration tool,
  and the work delivers zero behaviour by construction. Takes schema risk off the verdict's path.

## 13. Technical notes (moved in full from the feature file, 2026-09-18 sprint planning)


Full detail in `spec/references/F006-dataset-model.md` — **read §9 and §10 before implementing.**

- **Dataset key is `hrv_source_tier`** — stored `source_device` is the *watch*; identity is F007.
  `health_api_overnight` is never written by the classifier, so N is 3 in the enum and **2 in every
  real corpus**, and the 3-dataset shape is untested by construction.
- **`RECENCY_TOLERANCE_DAYS` (28) is inherited, not re-justified** — the `[18, 44]` bracket was measured
  against the *fused* band and is **not** cited as transferring; the constant rests on HRV-16's two
  reasons alone until a re-measurement against per-tier datasets is recorded (`research/00` GATE-03,
  decision C37).
- **Fidelity rank arbitrates; the confidence weight never does** — pinned since 2026-09-19 by
  `test_the_numeric_confidence_weight_never_participates_in_selection` (T156), behaviourally and
  structurally at once. No weight is emitted either: `datasets[]` carries `fidelity_rank`
  (`spec/03` §3.7.4; §3's table above, corrected 2026-09-21, T159).
- **`research/00` HRV-34 is amended FIRST** — it now states that a source change triggers no rule-level
  re-establishment and collapses no SWC band, the coverage-gap reset being the only one that
  re-establishes (HRV-34, decision C03). Sweep the claim tree-wide.


## 14. Decision log additions from sprint-006 planning (2026-09-18)

- **AC17 split into two mechanisms** after the sprint analyst showed `_era_boundary(old, new, judged)`
  returns `None` when `old is new` (verified by direct call). The reported `tier_change_reset` stays
  cross-tier; a **new, unreported** per-dataset band clip cuts a dataset's band at an internal hole of
  more than `GAP_RESET_DAYS` (21) silent local days of its tier, so 21 does not clip and 22 does
  (`research/00` HRV-37, decision C04), reusing that constant rather than minting one. Chosen over naming the
  residual and over dropping AC17 entirely.
- **AC9 keeps a presentation fallback**: on an illness/holiday week `selected_dataset`
  is null but `baseline`/`band` are populated, for presentation only, from the dataset the
  presentation fallback names (`research/00` HRV-24, HRV-59), so the non-nullable contract fields
  carry a value and three shipped pins stay green.
- **AC10's disagreement rule, as T167 fixed it (2026-09-21)**: a dataset with a computable band
  whose judged-week mean reads on the other side of its own band from the selected dataset's, in
  either direction, is named in `disagreed_with` whether or not it is judgeable, wherever a verdict
  is conferred; whenever the served verdict is `hrv_unavailable`, for any cause, `disagreed_with` is
  empty (`research/00` HRV-21, HRV-22, decision C16).
- **AC6, not AC17, defends reference §9.** For the dataset that *is* `previous_tier`, clause (b)
  short-circuits and no clip ever fires. The two are sequential, not complementary.
