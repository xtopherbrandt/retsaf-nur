---
id: "F005-trend-construction"
feature: "F005"
type: reference
created: 2026-09-07
---

# F005 — Trend Construction

The computational detail behind F005's acceptance criteria. Every constant here is a **stated
heuristic default**, tunable per athlete, per Section 3's own convention.

## Constants

| Name | Value | Why |
|---|---|---|
| `baseline_days` | 60 | The Plews/Altini lineage the register cites; long enough for a stable dispersion without spanning a whole training block. |
| `min_baseline_readings` | 14 | Enough spread for a meaningful SD while staying reachable for an athlete capturing a few times a week. |
| `min_window_readings` | 3 | §3.7.4's trends-not-single-readings rule, made testable. |
| `recency_tolerance_days` | 28 | How far behind the most recently read candidate a candidate may fall and still be one (T117, 2026-09-15; `research/00` §5.4). Four judged weeks, and deliberately **greater than `gap_reset_days`** so no tier is struck for a silence shorter than the shortest silence this feature calls a break. Relative to the candidates, not to `D-7`. It is **not** published in `thresholds` ([[IDEA-070]], 2026-09-15). Measured green band on the five HRV suites, 2026-09-15, over the **394 tests** (`BAND_CORPUS_WHEN_MEASURED`) they held on that date less the tolerance pin that existed then (now `BAND_CORPUS_EXCLUDES`) (those pins assert this value or its measured consequences and are red at every other by construction, so over all of the five suites the band is `{28}` — re-scoped by T121, scope pinned by review cycle 8, `acfebae`): [18, 44]. The subtraction, and the live collection it is taken from, are asserted by `test_the_scoped_suite_count_the_band_was_measured_over_is_pinned_not_published` in `test_hrv_trend_endpoint.py`; the live count is `SCOPED_SUITE_COLLECTED` there and is deliberately **not** transcribed here, T121's literal having been invalidated by the next commit of its own fix batch with nothing able to see it (review cycle 8, `acfebae`). That relation grows with every test added to the five suites, so the bracket stays a claim about the 394 it held when it was run. |
| `gap_reset_days` | 21 | Survives a taper, a holiday or a two-week illness; catches an era break. The asymmetry favours keeping a hard-won baseline. |
| `swc_factor` | 0.5 | The register's shipped SWC width. |
| `band_floor` | 0.01 | Guards a degenerate dispersion only. **Expected firing rate: near zero** — see below. |

## The band

```
series      = one ln(resting_rmssd_ms) value per local day, post-exclusion
baseline    = series over [D-66, D-7]        (60 local days, closed)
window      = series over [D-6,  D]          (7 local days, closed)

half_width  = max(0.5 * SD(baseline), band_floor)
band        = mean(baseline) ± half_width
verdict     = unavailable if band is None                (fewer than two baseline readings; T126)
              unavailable if readings_in_window < min_window_readings   (§3.7.4, AC 11)
              unavailable if withheld                   (the week is not a fair sample; T125)
              unavailable if not established            (baseline < min_baseline_readings)
              suppressed  if mean(window) < band.lo
              normal      otherwise (inside or above)

withheld    = any tier in (struck by rule 1's recency gate)
                       union (never used: 0 baseline-window days, T132) holds
                >= min_window_readings distinct days of [D-6, D]
                and min(those days) > max(the resolved tier's days in [D-6, D])
```

**The establishment gate is symmetric** (2026-09-15, review cycle 7, T116, closing [[IDEA-062]];
user decision; a **behaviour change**). Both `suppressed` and `normal` assert an established
baseline; every position on an unestablished one is `unavailable`, and the band is still reported.
Until T116 the gate sat inside the `< band.lo` arm alone, so a week inside or above a band built
from 2 to 13 readings read `hrv_normal` -- telling Section 6 readiness is intact on a baseline the
same response called unestablished, the up-regulating direction `research/00` §1.7 forbids, and
reachable after every reset this feature performs. Pinned by the band suite's contract table and by
its three establishment-gate tests; priced in F005's Negative Class under "The verdict".

**A stale week is not judged either** (2026-09-16, review cycle 8, [[T125]]; user decision on six
measured fix forms; a **behaviour change**). Rule 1's recency gate below is measured over the
**baseline window**; the verdict is a claim about the **judged week**. Nothing required the
surviving tier's week readings to be the recent ones, so the gate can strike the tier the athlete is
*currently recording on* in favour of one whose week coverage has just run out, and the verdict is
then computed entirely from readings that predate his return. On the reproducing series -- strap
daily to `2026-07-31`, a 39-day strap silence carried by a daily snapshot to `09-08`, the strap
resuming `09-09` with four suppressed mornings -- `D = 2026-09-12` read `health_snapshot`,
`readings_in_window` 3 (`09-06`, `09-07`, `09-08`), the athlete's own four mornings excluded
`off_baseline_tier`, and **`hrv_normal`**: readiness intact on a week he did not live, `research/00`
§1.7's forbidden direction. The rule is stated at the verdict: **a candidate struck for staleness
whose judged-week readings are all later than the resolved tier's means the week is not a fair
sample of the tier being judged, so no verdict is asserted.**

Three properties of the rule, each of which was a measured choice:

- **The predicate is over day sets and their order, not counts.** "Every one of the struck tier's
  week days follows every one of the resolved tier's" is what separates a device *return* from an
  *abandoned trial* picked up for three days in a week the carrier happens to miss. The count form
  ("the struck tier covers the week") is satisfied by the July trial too -- measured 2026-09-16 by
  dropping the order clause and keeping the count: **5 red**, including
  `test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band`, the recency walk
  and the seam matrix, all of which now read `hrv_unavailable` on weeks the July trial legitimately
  does not own. (The same predicate under the *re-admit* mechanism -- form 1 -- hands the trial the
  baseline outright and reds the same July pin for the opposite reason. Either way the count alone
  cannot tell a return from a trial.) This is why the condition cannot live inside
  `resolve_baseline_tier`, which sees counts:
  `build_series` computes it from the week's day sets and `judge` reads it off the series
  (`verdict_withheld`, `HrvSeries.withheld`).
- **It withholds; it does not re-resolve.** The tier, the band, the baseline window, `excluded` and
  both reset rules are exactly what they were. Measured over 2050 return geometries: resolved tier
  identical to the pre-T125 rule on **all 2050** rows, reported baseline window identical on all
  2050, and the only verdict change in either direction is `hrv_normal -> hrv_unavailable`, **72**
  times. The band is still reported wherever it can be built, so what was withheld is reproducible
  by hand (§1.6).
- **Rule 1's gate is unchanged.** T125 factored it out of `resolve_baseline_tier` into
  `_recency_struck` so the struck set could be read at the verdict; the gate applies the same
  constant to the same window and returns the same tier on every series in the suites.

**Why withholding and not re-admitting.** The competing form re-admitted the struck tier on the same
predicate and told the returning athlete `hrv_suppressed` from his third morning back, which is more
useful to him. Swept over the same 2050 geometries it closes the same **36** forbidden-direction
geometries and **creates 54** (`hrv_unavailable -> hrv_normal` judged against a band 36 to 53 days
old), plus **36** more where a surviving `hrv_normal` comes to rest on that band. Its reach into
mornings 5-7 is one predicate that lands safe-side for a suppressed athlete and forbidden-side for a
healthy one in exactly equal numbers, and it cannot deliver the first without the second. The
created population is the *ordinary* return -- the carrier recorded daily right through the layoff,
so coming back at the same level needs nothing to happen -- while the closed population needs a
suppressed return, a discontinuity timed to the device change. Cost on the ordinary athlete, benefit
on the unusual one is the reverse of §1.7's asymmetry, so the form whose cost is **silence** was
chosen: 36 closed, **0** forbidden-direction geometries created, against 54.

**The accepted cost and the residual**, both priced in F005's Negative Class: 72 geometries move
`hrv_normal -> hrv_unavailable`, which per athlete is mornings **3 and 4** back turning silent on
top of 5, 6 and 7, already silent for want of week coverage -- five of his first seven mornings say
nothing, two of them this change's doing -- and the 54 suppressed returns the competing form would
have answered stay silent. **Mornings 1 and 2 back are not fixed by this or by any form measured**:
the returning tier holds one and two week days against `min_window_readings`, so the verdict there
still comes from pre-return readings. The guard that keeps a stray cross-device capture from
withholding a legitimate verdict and the constant that hides those two mornings are the **same
constant**; loosening it to reach them is exactly the change that starts producing false withholds.
Pinned by `test_the_device_return_is_walked_morning_by_morning_through_judge` (band suite), which
walks both tier orders and both value levels morning by morning through `judge`.

**The withhold is widened to reach a device the athlete has never used before** (2026-09-16, review
cycle 9, [[T132]], form B against the measured table in `T125-fix-form-measurements.md`; user
decision; a **behaviour change**). T125's rule above reaches only a tier the recency gate **struck**
-- and a struck tier must first be a *candidate*, `>= min_baseline_readings` distinct days in the
baseline window. A tier whose first-ever reading falls **inside the judged week** has zero such days,
so it is never a candidate, never struck, and T125's rule could never fire for it, however many
judged-week days it holds and however cleanly they are ordered after the resolved tier's own.
Reproduced: `chest_strap_raw` daily `2026-07-04..2026-09-04` @ 40.0 ms, `health_snapshot` never used
before on `2026-09-05..2026-09-07` @ 15.0 ms, judged `2026-09-08` -- `chest_strap_raw` resolves,
`readings_in_window` 3 (the three stale strap mornings), `withheld: false`, `baseline_n` 60,
established -- **`hrv_normal`**, while the athlete's own three brand-new-device mornings are excluded
`off_baseline_tier`. `research/00` §1.7's forbidden direction; the sixth population of this shape
found in this rule.

**Resolved: the order clause is asked of the union of the struck tier(s) with any tier holding zero
distinct baseline-window days and `>= min_window_readings` distinct judged-week days.** The order
clause is unchanged. Zero is not a tuned threshold -- it is the only value that means "never used in
the baseline window" -- so a tier with 1..13 days of history is untouched, exactly the reason this
form (of three measured) was chosen over the two broader ones (union on any under-candidate tier
holding a full week; dropping the candidacy gate for "most-recently-read tier"), which also close a
second forbidden-direction population (`inter_rows` era 10) but at far greater cost: 988 of 2050
swept geometries withheld at `c = 0` against this form's **260** (+80 over shipped's 180, confined to
`q = 2..6`, the range shipped already reaches) and **byte-identical to shipped at every `c >= 1`**.
Tier selection, the band, the baseline window, both reset rules and the establishment gate are
unchanged; this widening changes what is *said* about a week, never what the week is.

**What the rule does not resolve.** A **legitimate, permanent** device switch is, for its first
`min_window_readings` days, the identical shape to this rule's own reproduction -- an un-established
tier holding a full judged week, entirely after the resolved tier's. Nothing in `week_readings`,
`baseline_counts` or `last_read` alone separates "a device he will never use again" from "a device he
bought yesterday and will use forever", so the widening also reopens two more of the athlete's first
four mornings on a permanent switch (days 3 and 4; days 1 and 2 are already invisible to T125's rule
for the same reason). `tier_change_reset`'s 14-baseline-window-day accumulation, not a predicate over
one week's shape, is the mechanism built to make that distinction in general -- a structural finding,
not a corner case. Pinned by
`test_the_withhold_reaches_a_never_used_tier_bought_this_week` (band suite, the new reproduction) and
by `test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline` (reset suite,
`k = 3` and `k = 4` moved from `hrv_normal` to `hrv_unavailable`).

**Why SD and not CV.** §3.7.3 as written says `0.5 · CV(ln rMSSD)`, and that is a defect this
feature amends. `CV = SD/mean` is a ratio to the origin, and the origin of a log scale is arbitrary:

```
realistic athlete (rMSSD ≈ 45 ms, day-to-day SD ≈ 9 ms)
  0.5 · SD(ln)  = 0.0526        0.5 · CV(raw) = 0.0525        0.5 · CV(ln) = 0.0138

same readings expressed in seconds instead of milliseconds
  0.5 · SD(ln)  = 0.0526        unchanged
  0.5 · CV(ln)  = −0.0171       sign flipped; band.lo > band.hi
```

`SD(ln)` is invariant under the unit change, is numerically indistinguishable from the literature's
`0.5·CV` of the **raw** series for realistic variation, and cannot invert the band. `CV(ln)` is
none of those things and is roughly 4× too narrow besides. The register's intent is preserved; only
the statistic that expresses it is corrected.

**Why the floor is 0.01 and not 0.05.** An ordinary athlete's `0.5·SD(ln)` is ≈ 0.05, so a floor at
0.05 would *be* the band for everyone and the computed half-width would never be used — a
discriminator with no reachable negative case, which
`.claude/rules/learnings/contract-tables-need-an-independent-oracle.md` names as a check that cannot
fail. At 0.01 the floor fires only for a genuinely degenerate series (SD(ln) < 0.02, i.e. daily
readings within about ±2%), and both branches are reachable and testable.

**Why the windows do not overlap.** If the baseline ended at `D`, roughly a seventh of it would be
the very week under judgement, so a sustained suppression would drag its own band down and
self-clear — against §3.7.4's "a single good morning does not instantly clear an accumulated
multi-day suppression". The baseline therefore ends at `D-7`, where the window begins.

## Series construction

Strict order, because the reverse silently drops days:

1. **Exclude** rows that are not readings this feature may trend:
   - the pre-amendment window — `hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL`
     (F004's published predicate);
   - any row with a null `hrv_source_tier` — including every capture F004's quality gates failed,
     which already carries a null tier *and* a null value. **This feature does not re-apply
     `rr_valid_fraction`**: ingestion discharged it, and a re-gate would describe a row that cannot
     exist in this series.
2. **Resolve the baseline tier** (below).
3. **Filter** to that tier.
4. **Collapse** to one reading per local day: earliest capture of that day, within the tier.

Doing (4) before (3) loses any day whose highest-fidelity capture is off the baseline tier, even
when a usable on-tier reading existed — and `readings_in_window` decides `hrv_unavailable`, so the
ordering is outcome-determining, not cosmetic.

## Baseline tier resolution

Amended 2026-09-10 (sprint-005 review, T093; decision log the same day) and 2026-09-11 (review
cycle 2, T094; rule 3). Over the baseline window `[D-66, D-7]` (after any coverage-gap clip) **and
the judged week `[D-6, D]`**:

1. The **candidates** are the tiers read on at least `min_baseline_readings` **distinct local days** in
   the baseline window — days, not captures (T095, decision log 2026-09-12): the unit `baseline.n`,
   `readings_in_window` and `established` report, taken in one place (`_tier_counts`) for rules 1-4.
   A candidate must **also have been read recently, relative to the other candidates**: its latest
   baseline-window day must fall within `recency_tolerance_days` (28) of the most recent
   baseline-window day of any candidate (T117, 2026-09-15, closing [[IDEA-064]]; `research/00`
   §5.4 — a behaviour change). A lone candidate is its own reference and is never struck, and the
   constant is **not** published in `thresholds` ([[IDEA-070]], 2026-09-15). Until T117 candidacy
   had no recency at all and a stale trial stayed a candidate until it aged out of the window,
   which is the defect F005's Negative Class priced as "stale candidacy" and now records as
   resolved **by change, not by re-acceptance**. The parameter-free form of this condition —
   "read later than the other candidate" — was built and measured on 2026-09-15 and rejected: a daily
   lower-fidelity tier is always read at least as recently as a 2-3-day-a-week higher-fidelity
   one, so it rejects the oscillating strap (4 days behind) and the abandoned July trial (45 days
   behind) alike, voiding rule 2's fidelity precedence rather than qualifying it; five pinned
   tests went red in both error directions. Pinned by
   `test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it`
   (28 admits, 29 strikes, and a `gap_reset_days`-long silence never strikes).
   **This gate decides candidacy and nothing else; since T125 (2026-09-16) the set it strikes is
   also read at the verdict** (`_recency_struck`, factored out unchanged), because striking the tier
   an athlete has just come back to leaves the verdict standing on the surviving tier's last
   pre-return days -- see "A stale week is not judged either" under **The band**. The gate itself,
   its window, its constant and the tier it resolves are unaltered.
2. If any candidate is read on at least `min_window_readings` distinct local days of the judged week,
   the baseline tier is the **highest-fidelity such candidate**. A re-taken morning is one day.
3. Otherwise — no candidate covers the week: illness, holiday, or the only week-covering tier is
   thin — the baseline tier is **the candidate the athlete was read on last**: the candidate whose
   latest reading in the baseline window is most recent, ties by count in the window, then by
   fidelity (T094; T093 said "the tier with the most readings", which handed a switched athlete's
   first thin week back to the device abandoned three weeks earlier and asserted a reset on an
   empty week both neighbouring weeks withdrew). When there is **no candidate at all**, the tier
   with the most readings in the baseline window, ties to higher fidelity (not simply the highest
   tier present). Either way a week with no readings of any tier keeps the tier stable, reads
   `hrv_unavailable`, and **begins no reset** — rule 4 reads the two windows, nothing inside the
   judged week that an empty week could change — while a reset already in force persists through
   it unchanged (corrected 2026-09-12, T095, review cycle 3 G11: the earlier "no reset" wording, carried
   verbatim from T093's densest fallback, was true only of *beginning* one; F005's own @must "The fallback
   keeps the device the athlete used last" pins `tier_change` on every empty day). **What separates
   the two empty-week series the suite pins** — the switched athlete's thin week, `tier_change` on
   every day, and the mixed baseline's empty week, no reset on any day — is nothing about the week:
   it is whether rule 4 holds for the fallback tier on the two windows alone. A strap era that
   followed a cleanly-ended snapshot era satisfies (a), (b) and (c) whether or not this week has
   readings; a snapshot that sustained both windows fails (b).

The no-candidate branch of (3) matters: an athlete with 45 Health Snapshot readings who borrows a
chest strap once must not have an established baseline demoted to `n=1` by that single capture.
§3.7.3 rules on this directly — the others are corroboration, "never merged into the same band".
The recency branch matters the other way: "densest" is a property of the window's past, "read
last" of the athlete's present, and no pinned row had discriminated the two until review cycle 2
found four populations living in exactly that gap (G1, G2 and the verdict's decision table).

Why (2) consults the week: `min_baseline_readings` is 14 in 60 days — 1.6 a week — and a judged
week needs `min_window_readings` = 3, so a tier can sustain a baseline *by count* while never
sustaining a *week*. Before the amendment the highest-fidelity tier with ≥ 14 won outright, and a
two-week strap trial in July gave the strap the baseline for 47 days of August and September with
`readings_in_window` 0 on every one of them — `hrv_unavailable` with `established: true`, a real
snapshot-tier suppression never emitted, and a `tier_change` reported daily then withdrawn without
an event (`CRITIC-F005.md`). The population between "an occasional capture" and "a sustained
switch" is named in F005's Negative Class subsection, with the accepted cost: a strap worn two or
three days a week alternates the tier whenever the strap count in the sliding judged week crosses 3
(the day a third strap reading enters or leaves `[D-6, D]`, not a week boundary — sprint-005 review
S4). The count unit is distinct local days, everywhere (T095; [[IDEA-047]] closed — counted in
captures, one re-taken morning covered a week the strap could not judge and 14 captures on 7 days
were a candidate whose baseline the same response reported unestablished; review cycle 3, G10).

**Interaction with the sustained-tier-change reset.** The reset (below) fires only when the
resolved baseline tier is a candidate, differs from the tier the previous window sustains (rule 1
alone: highest fidelity with ≥ 14 days), and the two eras do not interleave over both windows and
the judged week together, beyond the density tolerance for isolated captures stated under "Reset
triggers" (no *use* of the new tier between the old era's first day and last reading there). A
tier the week rejected
(the strap trial), a fallback the empty week forced (a mixed baseline, nothing captured this week),
and a tier whose readings run through the previous tier's in either window (an occasional-strap
habit, a stale trial, a finished trial that has aged into the previous window) are not changes
(T094; T093's "changed and owns the baseline" admitted the first two of those; T094's clause (c),
judged on the current window alone, admitted the last — review cycle 3, 2026-09-11).

## Reset triggers

The baseline is re-established on any of:

| Trigger | Detail |
|---|---|
| Sustained tier change | `tier_change` is asserted when, and only when: (a) the resolved baseline tier **sustains** the (gap-clipped) baseline window — ≥ `min_baseline_readings` distinct local days, rule 1's candidacy; (b) it differs from the tier the previous window `[D-126, D-67]` sustains (rule 1 alone: highest fidelity with ≥ 14 days — **without** rule 1's recency condition, decided 2026-09-15 with T117: that clause asks which tier sustained a window at least 67 days old, builds no band, and a staleness gate there would forget the tier the athlete used to be on and withdraw the report of the change it exists to announce); and (c) the eras are **non-interleaved**, judged over both windows `[D-126, D-7]` and the judged week together, with a **density tolerance** for isolated captures (T095; decision log 2026-09-12. The predicate has **two halves with two consequences**, and since T098 it is **stated in two places**: the *candidacy half* at `hrv_trend._era_boundary`'s own gate, which decides whether a boundary — and hence the baseline clip — exists at all, and the conjunction in `hrv_trend._isolated`, which decides whether an admitted boundary is *reported* and — as the **first ordering term** in `_era_boundary`'s selection key — which of several admitted boundaries is taken, hence where the clip lands (the "reported only" reading was corrected 2026-09-13, T104 iteration 2: it contradicted the T098 ordering stated later in this cell). Both count distinct local days with `_days` against `min_baseline_readings`, so no threshold has drifted. Corrected 2026-09-13, review cycle 5, T104 — this cell claimed `_isolated` was the one statement of the predicate, which T098's split below had already made false): with `A` the previous window's sustained tier and `B` the resolved tier, an *era boundary* is an `A` reading `A_end` and the first `B` reading after it, `B_start`, with no `A` reading between; the readings on its wrong side — every `A` reading after `B_start`, and every `B` reading from `A`'s first local day up to `A_end` — are the *strays*, and — **as T095 coupled them; superseded 2026-09-13 by the T098 split stated later in this cell**, under which the boundary and the clip turn on the candidacy half alone, the report on the conjunction, and the ordering takes the week half first — the eras do not interleave iff some boundary's strays, **together**, are *isolated*: fewer than `min_baseline_readings` distinct local days in all and fewer than `min_window_readings` inside `[D-6, D]` — the two densities at which a tier is a candidate or covers a week; isolated captures are corroboration, use continues or begins an era. Ordered on the captures' instants, as the same-day collapse orders them; a `B` capture at the very instant of `A_end` is simultaneous, not a stray, so the switch-day tie stays interleaved; `B` readings before `A`'s first day belong to the era `A` replaced. Of several era boundaries the one whose strays the judged week is clear of is taken **first**, then the one with the fewest stray days, ties to the later one (the younger baseline, `research/00` §1.7) — one ordering, the T098 one stated later in this cell (corrected 2026-09-13, T104 iteration 2: this sentence stated fewest-stray-days alone, which the T098 block already denied). Together, not per side, because a finished 21-day trial split down the middle leaves fewer than 14 strays on either side and 20 in all; from `A`'s first *day*, not its first instant, because on the one day a trial holds exactly 14 in the previous window the daily device's 13 readings inside the trial's instant span were otherwise isolated and a phantom reset was asserted (M1's series, 2026-08-12). Clarified 2026-09-11, review cycle 3 (T094 evaluated (c) on the current window only: a finished three-week strap trial that had aged wholly into `[D-126, D-67]` then satisfied it vacuously — the old tier had no reading in the current window to fail it — and, the previous window sustaining the trial's tier by fidelity, `tier_change on <the day after the trial>` was reported for seven weeks to a daily-snapshot athlete who never switched; over both windows the snapshot's readings run through the trial). `reset_on` is the era's true first day: `B_start`, the resolved tier's first reading after the old era's last — it does not slide once it is older than `D-66` (the reported window is `[max(D-66, reset_on), D-7]`, so `reset_on` may precede `window[0]`), and an older era of the same tier in the previous window (a 40-day strap trial between two snapshot eras) is not this one; that first reading always exists, since an era boundary has a `B` reading after it by construction. **The report is live only while (a), (b) and the week half all hold** (T109; qualified here 2026-09-15, T111 — this cell had read the (b) route as the whole lifetime, the equivalence T109 withdrew at both contract copies). One of those three ways the report ends is clause (b) lapsing: the previous window `[D-126, D-67]` stops being sustained by a tier other than the resolved one. It is the route with a closed form, and because rule 1 picks the highest-fidelity tier with ≥ `min_baseline_readings` there, the boundary is direction-dependent (review cycle 3, S1): for a forward switch (snapshot → strap) it is the day the strap reaches 14 there, `S+80` for a daily device; for the reverse one (strap → snapshot) the old strap keeps that window by fidelity until it drops below 14 there, `T+114`, a month after the snapshot reached 14 (`T+81`). Either way the old tier holds ≥ 14 readings in the previous window while (b) holds, so no reset ever lacks a first day. Those dates **bound** the report; they do not promise it — (a) or the week half may end it earlier, and on such a day `reset_reason` is null with (b) untouched. Amended 2026-09-11, T094 (T093's "changed and owns the baseline", 2026-09-10, fired on alternate weeks of a young two-to-three-day strap habit and on a stale trial, and reported the reverse transition a month late; the critic ran the non-interleaved criterion against all 322 probe tests and it kept every pin) and again in review cycle 3 (the both-windows criterion kept all 333; T095's tolerance kept all 336 and its decision table — every candidate reading of the tolerance against the whole probe suite — is in the task's Delivered note). A single off-tier capture is not a change and, since T095, does not hide one either: one new-tier capture eleven days before a switch (G9), a 14-day trial of the new tier three months before it once it holds fewer than 14 days in the windows (G12), and one old-tier capture after it ([[IDEA-065]], closed) are strays. Neither is a tier the judged week chose, an empty week's fallback, or a tier whose era interleaves with the previous tier's in either window. **The tolerance's own error direction**, named in F005's Negative Class: a habit of the other tier dense enough to be a candidate, or three of its captures in the judged week, is use — the eras interleave and no reset is reported (G6's intended behaviour); and because the week half is judged on the sliding `[D-6, D]` as rule 2's week coverage is, three old-tier captures in one week after a switch withdraw the reset for the days they sit in the judged week and hand it back once the week has slid past them. A stale trial of the *new* tier that still holds 14 days in the previous window is (b)'s stale candidate, not (c)'s: it sustains that window by fidelity and the switch's reset waits until it ages below 14 there — 16 days for G12's 14-day trial 90 days before the switch, rather than the 28 the exact clause (c) then added ([[IDEA-064]]). **The clip and the report are two consequences of the era boundary, not one** (amended 2026-09-13, review cycle 4, T098; decision log D4, following `research/00` §5.4). The boundary itself is clause (c)'s **candidacy half alone**: a boundary whose strays, together, are fewer than `min_baseline_readings` distinct local days — never dense enough to be a baseline of their own, so the old era ended before the new one began. Whenever such a boundary exists (with (a) and (b) holding), `build_series` clips `baseline` to `[max(D-66, <a gap resumption>, B_start), D-7]` **unconditionally** — of the report, and, since **2026-09-14 (review cycle 6, T107)**, of a **coverage gap** — and the readings of the resolved tier it removes are listed `before_reset: tier_change` — the clip is a property of the athlete's capture history. `reset_reason` / `reset_on` are *reported* only when that boundary's strays are also `_isolated` — the **week half**, fewer than `min_window_readings` stray days inside `[D-6, D]` — which is the statement about what the athlete is told. Of several boundaries the one whose strays the judged week is clear of is taken first, then the one with the fewest stray days, ties to the later one: ordering on the week half first is what makes T098 change **no** reported reset — an isolated boundary is always a candidate, so wherever one existed before it is still chosen and `reset_on` does not move (pinned directly on `_era_boundary`, since no series `build_series` can be handed distinguishes the two orderings). Coupled, as they were until T098, the week half's sliding `[D-6, D]` reached the band: three captures of the *other* tier in one week — contributing nothing to the week mean — withdrew the report, un-clipped `baseline` back to `[D-66, D-7]`, drew a seven-week-old device era back into it and flipped `hrv_normal` ↔ `hrv_suppressed` with no new data (G-C4-1). **The candidacy half is a cliff on the band, priced and kept** (amended 2026-09-13, review cycle 5, T102; decision log D5, following `research/00` §5.4). The week half decides the report and the ordering, as stated above — it is the first ordering term, so it also selects which admitted boundary is taken and hence where the clip lands, though no series `build_series` can be handed makes a *reported* band or verdict move with it (corrected 2026-09-13, review cycle 6, T106: this sentence still read "decides the report only" after T104 corrected the same claim earlier in this cell); the candidacy half decides whether a boundary exists at all and hence whether there is a clip to place, and at the threshold `_era_boundary` returns `None` — **no boundary and no clip**, not an unreported one — so the resolved tier's pre-switch readings enter the band. The count is **distinct local days** (`len(_days(strays))` over the raw captures `tier_change_reset` receives, before the same-day collapse), and it is **pooled over both directions**, so an `A` capture after `B_start` — which says nothing about whether `B`'s pre-switch readings were an era, and contributes nothing to the week mean — can supply the day that removes the boundary. On the suite's `trial_then_switch` series (daily snapshot, a 10-day strap trial at `base-60..base-51`, a daily strap from `base-39`, snapshot captures after it **outside** `[D-6, D]`), target 2026-09-07: 13 stray days — `tier_change on 07-30`, window `(07-30, 08-31)`, n 33, `band.lo` 3.6789, `hrv_suppressed`; 14 — `reset_reason` null, window `(07-03, 08-31)`, n 43, `band.lo` 3.4791, `hrv_normal`; 7-day mean 3.4965 on both, a 0.20 ln step in the band and a flip in the under-calling direction on no new week data (pinned: `test_a_fourteenth_stray_day_outside_the_judged_week_moves_the_band_and_the_verdict`). Accepted over counting only `B`'s strays for candidacy (re-opens T095's V1 for the candidacy half) and over a continuous clip; the unit at the gate is what decides which side of the cliff a series lands on, and is pinned at all three sites it is read — `_era_boundary`'s candidacy gate and both halves of `_isolated` (T108, 2026-09-14, closing G-C5-2; each of the three counting-captures mutants dies against a re-taken-morning fixture, verified independently by the orchestrator). **The coverage gap keeps precedence over the *report*, and over that alone** (amended 2026-09-14, review cycle 6, T107; G-C6-5, a **behaviour change**). Until T107 `build_series` asked rule 4 only `if reset_on is None`, so a gap anywhere in `[D-66, D]` meant no boundary was computed and **nothing was clipped** — an abandoned device era re-entered the band and a genuinely suppressed week read `hrv_normal`, the direction `research/00` §1.7 tolerates least, with **no threshold to cross**: any gap did it, while three lines below the gate sat the comment "the clip is unconditional". Rule 4 is now asked on every request and the **two clips compose as the later of their first days** — each says the same kind of thing, *these readings are not of this baseline's era*, so the band must contain neither the pre-gap nor the pre-boundary readings and the admissible set is the intersection. `reset_reason` / `reset_on` are unchanged: the gap still wins them. Two consequences follow. Nothing is listed twice — the gap branch rebinds the pre-filter `readings` before the same-day collapse, so `series` holds only what it kept and the era clip excludes out of `series` (`research/00` §1.6's "exactly one list"); and a `coverage_gap`'s `reset_on` **can now precede `window[0]`** when the era boundary clips later than the resumption, which is the one claim T107 had to qualify (see the Coverage gap row). Reproduced at `79b4d1c` on two series differing only by a 24-day silence, the judged week byte-identical, target 2026-09-07: with the silence — `coverage_gap`, window `(07-29, 08-31)`, n 25, `band.lo` 3.4915, `hrv_normal`; without it, the same switch — `tier_change`, window `(08-13, 08-31)`, n 19, `band.lo` 3.6771, `hrv_suppressed`; 7-day mean 3.6636 on both. After T107 both read `(08-13, 08-31)` / 19 / 3.6771 / `hrv_suppressed`, the gap keeping only its report (pinned: `test_a_coverage_gap_does_not_cancel_the_era_clip`, `test_the_gap_keeps_the_report_while_the_era_keeps_the_clip`). The existing precedence pin put the resumption and the switch on the **same day**, where the two clips coincide, which is why 1293 tests constrained this in neither direction. **The population clause (c) is asked over — amended 2026-09-16, T129, `research/00` §5.4, a behaviour change.** Clause (c)'s **strays** are counted over the **unclipped** `[D-66, D]` of every tier together with the previous window, not over the gap-clipped `baseline_readings`/`week_readings` clause (a) reads. Until T129 one clipped population answered both, so the readings of `[D-66, <the resumption>)` were absent from the stray count: new-tier readings hidden there would have been strays of every *late* `A_end`, so hiding them shrank the stray term for late boundaries and could admit — or promote over an earlier candidate — a boundary the full capture history **refuses or dates earlier**. That `first_day` could then fall *after* the resumption, and the composed clip removed post-resumption readings of the baseline tier from the band as `before_reset: tier_change`. T107 created the reachability (before it the gap cancelled this branch outright); it was accepted and named on 2026-09-15 (G-C7-3, T118) on a **direction** — 0 flips to `hrv_normal` in 40,000 trials — and that direction was **falsified** on 2026-09-16 by T123's re-run against the post-T116 rule: of 26,360 well-formed histories, 9,230 moved the boundary, 4,466 of those held `baseline_n ≥ 14`, 702 were flip-reachable and **5 flipped** `hrv_suppressed → hrv_normal` on an identical week mean with the baseline established on both sides. Clause (a) is **unchanged** and still reads the clipped window — it asks whether the resumption era sustains a baseline of its own — as are the gap's precedence over the report and the composition of the two clips. Measured at the fix: the flip class is **0 of 26,360** (55,562 trials) where it was **18** immediately before, and shipped agrees with the full-history reference on all 26,360. Pinned by `test_the_unclipped_stray_count_refuses_the_gap_created_era_boundary` (the widest witness: same 7-day mean 3.430187, `n` 15 → 22, `band.lo` 3.371352 → 3.450867, `hrv_normal` → `hrv_suppressed`) and by `test_the_gap_created_era_boundary_keeps_on_tier_days_at_the_resumption`; the *population dependence itself* stays pinned on the rule directly by `test_the_gap_clip_moves_the_era_boundary_later_than_the_full_history_finds`, which is why that pin calls `tier_change_reset` with the population as its variable. |
| Coverage gap | More than `gap_reset_days` local days with no entry in the **post-exclusion, pre-filter readings of any tier** (T083's `readings` — amended 2026-09-10, sprint-005 review S4; "the post-exclusion series" above is the tier-filtered series, and the gap is not measured on that). Measured on readings, not on stored rows — the pre-amendment window is stored rows that are not readings, and measuring on rows would let the reset the amendment window exists to trigger never fire. **Days holding only off-tier readings are not a gap: the athlete kept capturing** (T092's probe table; IDEA-046 row 5). **The leading stretch** of `[D-66, D]` before its first reading is a gap when the silence from the **latest reading known to precede the window** exceeds `gap_reset_days`: the latest reading before `D-66` among the rows read, or, when none was read at all, the store's earliest reading (`db.earliest_hrv_reading`, one `MIN(start_time)` scalar beside `read_hrv_rows`; the row read is **not** widened past `D-126`) — a reading that precedes the window and was not read lies before every row read, so the silence is at least the whole read-back. With no known earlier reading the stretch is the start of history, not a gap: a new athlete's first capture names no event. Amended 2026-09-12 (T096, review cycle 3 G13): until then the stretch was bounded by the previous window's readings alone, so "no earlier reading was *read*" was mistaken for "no earlier reading *exists*" — the same 34-reading era ending `D-100`, `D-127` or `D-160` before a resumption at `D-40` reported `coverage_gap` on the first row only, so the discriminator was where the last pre-gap reading fell, not the silence's length, and the route's 126-day read made the right answer unreachable for any layoff longer than about two months. AC 19 stands as written. **The report has a lifetime** (review cycle 3, G14): `coverage_gap` is reported only while the resumption lies inside `[D-66, D]` — 67 days from the resumption — because the scan runs over that window; its `reset_on` therefore never precedes `window[0]` *because the report outlived its own clip*, whereas `tier_change`'s can (the era's true first day against a window clipped at `D-66`). **Qualified 2026-09-14 (review cycle 6, T107)**: it *does* precede `window[0]` when a tier-change era boundary clips the window later than the resumption, because the two clips now compose as the later of their first days and only the report is the gap's. The unqualified "never" was true only while a gap cancelled the era clip outright, which is the defect T107 fixed. A resumption that was also a device switch (a daily snapshot to 04-01, 30 silent days, a daily strap from `R` = 05-02) is reported as `coverage_gap on R` through `R+66`; as `tier_change on R` from `R+67`, when rule 4 reads the same era as a switch — the strap sustains the window, the snapshot still sustains `[D-126, D-67]`, and no reading lies across the boundary — until rule 4(b) fails at `R+80`, rule 5's forward stop; and as nothing after. One era, one `reset_on`, three reports and no event between them; the re-attribution is an accepted cost, named in F005's Negative Class, and `established` is true from `R+20` on, so nothing downstream is misled. T095's density tolerance does not move the `R+67` hand-off: the boundary has no strays on either side (pinned as the three-phase walk, T096). |
| ~~Timezone change~~ | **Withdrawn 2026-09-09** (sprint-005 critique): the computation is stateless and this feature writes nothing, so no request can know the zone a previous verdict used. A zone change simply re-buckets the whole history under the new zone; the response reports the zone it used; no reset, comparison or verdict is asserted about the change. Two captures can still merge into one local day or split across two — that is the new zone's correct day. |

After a reset, `established` is false until `min_baseline_readings` accumulate, and **no verdict at
all** is emitted in the meantime -- neither a suppression nor a normal (2026-09-15, T116: until
then only the suppression was withheld, and 12 of the 20 unestablished days after every reset
read `hrv_normal` unless the week fell below the band — the other eight had no band
(corrected 2026-09-16, T126: this read "~12 unestablished days", conflating the
changed-verdict count with the duration). **Amended 2026-09-17 (review cycle 9, T138, following
`research/00` §5.4): this paragraph is true of a coverage gap and false of a source-tier change.**
A clean, gapless, permanent switch does not collapse the baseline at all — the outgoing tier keeps
its own full 60 days, so `established` stays **true** throughout and this paragraph's gate never
fires. Its silence is week coverage on the tier the athlete has stopped using, it lasts
`min_baseline_readings + 7 - min_window_readings` = **18** days (`R+2 .. R+19`) rather than 20, and
`reset_reason` is **`null` on every one of them** — the switch is first reported on `R+20`, carrying
a `reset_on` twenty days older than the day it appears on. `baseline.window` is **not** a function of `reset_reason`: since
2026-09-13 (T098) a window clipped at a tier-change era boundary with a `null` `reset_reason` is a
reachable, correct state — the band is era-correct and the athlete is simply told nothing about it,
which is the tolerance's whole remaining cost (F005's Negative Class).

## Degenerate inputs and required probes

`.claude/rules/learnings/adversarial-input-probes-are-a-task-deliverable.md` applies: this feature
ships a discriminator and several gates, so the implementing task carries an **adversarial-probe
table** in its Technical Notes before it is done. Enumerate the degenerate forms of every field
read, drive the real path with each, and defend every result in writing:

| Field | Degenerate forms to probe |
|---|---|
| `resting_rmssd_ms` | null, 0, negative, non-finite, 1 ms (ln ≈ 0), very large |
| `hrv_source_tier` | null, an unknown enum value, `health_api_overnight` (in the enum, never written) |
| `start_time` | a DST transition day, 29 February, a future timestamp from clock skew, a value predating all others |
| series shape | 0, 1, 2 readings in baseline; 0, 1, 2, 3 in window; all readings identical; a gap of exactly 21 and exactly 22 days |
| `athlete_timezone` | an invalid zone, a zone with a half-hour offset, a change mid-history |

Per [[IDEA-034]], the guards that make claims — "a thin baseline can never suppress", "the floor
cannot fire for an ordinary athlete", "the windows do not overlap" — each need a test verified by
**perturbation**: remove the guard and confirm the test goes red.

## Amendment surface

This feature's delivery includes reconciling the domain spec. The passages to correct, named so the
sweep is checkable. **Status 2026-09-09:** sprint-005 planning found the band amendment contradicts
`.claude/rules/project-domain-and-spec-fidelity.md:17`; `/ship-discuss F005` re-decided it the same
day — `0.5 · SD(ln rMSSD)` stands, and the amendment reaches every site below including the
authority (see the decision log). The table is the sweep result, verified tree-wide at commit
`8f2f6f5`. It is wider than the list first written here: the first list named 4 files; the sweep
found **21 live hits in 8 files** plus 2 historical ones.

What changes, by kind of claim:

- **Trend input** becomes `resting_rmssd_ms` for both tiers; `rmssd_precomputed` is named the
  device-only audit record. *Storage* claims about `rmssd_precomputed` (where the adapter writes a
  device value) are correct and stay.
- **Band statistic** becomes `0.5 · SD(ln rMSSD)` (sample SD, `statistics.stdev`), with the
  unit-invariance argument. `research/00` is the ratified authority and must carry the corrected
  form as a stated *clarification* (raw-series `0.5·CV` ≡ `0.5·SD(ln)` numerically), or amending
  the spec alone inverts the precedence rule in `.claude/rules/project-domain-and-spec-fidelity.md`.
- **§3.7.4 degradation** is scoped to **tier level** (a lower tier sustains the baseline when the
  higher one cannot), not per-day substitution inside a live baseline, which would contradict
  §3.7.3's single-tier band. The per-tier **confidence weight** §3.7.4 mandates is **not** delivered
  by this feature and is recorded there as deferred, with the sibling (Section 6 fusion) named.

| # | Location | Claim | Kind | Disposition |
|---|---|---|---|---|
| 1 | `specification/spec/03-derived-metric-formulas.md:229` | `band = mean_baseline(ln rMSSD) ± 0.5 · CV(ln rMSSD)` | band | live — §3.7.3 formula |
| 2 | `…/03:231` | "the coefficient of variation CV are computed from…" | band | live |
| 3 | `…/03:238` | "The ±0.5·CV width … a CV-based band" | band | live |
| 4 | `…/03:308` | §3.10 roll-up "±0.5·CV smallest-worthwhile-change band" | band | live — **outside §3.7**, missed by the first list |
| 5 | `…/03:215` (§3.7.1) | "supplied as the scalar `rmssd_precomputed` … enters the trend directly" | input | live |
| 6 | `…/03:223` (§3.7.2) | "taken directly from `rmssd_precomputed`" | input | live |
| 7 | `…/03:245` (§3.7.4) | "the trend uses the highest source tier available, at that tier's confidence" | degradation | live — re-scope to tier level; confidence weight recorded deferred |
| 8 | `specification/spec/02-canonical-data-schema-ingestion.md:72` | "`rmssd_precomputed` … enters the §3.7 trend directly" | input | live |
| 9 | `…/02:80` | "degrading … to a device-computed numeric resting rMSSD (`rmssd_precomputed`)" | input | live |
| 10 | `…/02:190` | "a numeric `rmssd_precomputed` reading … carries its tier's confidence weight" | input | live |
| 11 | `…/02:211` | "The 7-day rolling mean and CV-scaled SWC band the trend already uses (spec §3.7)" | band | live |
| — | `…/02:148, :162, :242` | `rmssd_precomputed` as the storage target | storage | **stay** |
| 12 | `specification/spec/06-adaptation-logic.md:64` | "versus the ±0.5·CV smallest-worthwhile-change band that Section 3 §3.7.3 emits" | band | live — consumer restatement |
| 13 | `specification/spec_outline.md:31` | "HRV trend (7-day rolling ln rMSSD vs ±0.5·CV …)" | band | live — structural contract; **not under `specification/spec/`**, so a sweep scoped to that directory misses it |
| 14 | `specification/research/00-design-decisions.md:105` | register row "±0.5·CV smallest-worthwhile-change band" | band | live — **the authority**; amend as a clarification |
| 15 | `…/00:155` | "the athlete's own CV-scaled SWC band" | band | live authority |
| 16 | `…/00:182` | "ln rMSSD rolling means, coefficient of variation — Plews/Altini/Kubios" | band | live but generic (freedom-to-operate note) — annotate, do not rewrite |
| 17 | `specification/research/05-data-to-adaptation.md:81` | "default ± 0.5 × the athlete's own coefficient of variation" | band | live |
| 18 | `…/05:219` | "*Default:* ± 0.5·CV, refined from the athlete's own baseline" | band | live |
| 19 | `.claude/rules/project-domain-and-spec-fidelity.md:17` | "vs a ±0.5·CV smallest-worthwhile-change (SWC) band" | band | live rule, `paths: ["**/*"]` — injected into every agent's context |
| 22 | `…/02:216` (§2.4.5 "Degradation.") | "On a given day the readiness logic uses the highest tier available, at that tier's confidence. Only when no resting-HRV reading of any tier is available does the HRV input go unavailable for that day" | degradation | live — **found in review** (sprint-005 code review M2, 2026-09-10): four lines below site 11, stating the per-day rule the amended §3.7.4 cites as its authority; restated at tier level, citing §3.7.4 |
| 23 | `specification/research/00-design-decisions.md:148` (§3.3) | "consumes the best tier available on a given day and degrades through the rest" | degradation | live authority — found by the same sweep (`on a given day`); clarified in place, as §5.4 was for the band, so precedence is preserved |
| — | `CHANGELOG.md:235` | "would poison E003's `ln(rMSSD)` trend and its ±0.5·CV SWC band" | band | historical (sprint-004 section) — leave, or mark superseded |
| — | `runcoach-api/src/**` | — | — | no hit for either claim in source docstrings |
| — | Shipyard `spec/` (this feature's own files) | quote the old form as the defect | — | self-referential — leave |

A sweep probe must assert a **property** — every file that states the band also carries the
corrected form — over `specification/` as a whole (not only `specification/spec/`) and
`.claude/rules/`, not the absence of one string. Sweep the **claim**, tree-wide, not the diff
([[IDEA-033]]).
