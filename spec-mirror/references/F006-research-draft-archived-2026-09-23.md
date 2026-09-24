---
topic: "IDEA-071 — re-derive the resting-HRV tier rule around per-source datasets with independent baselines"
created: 2026-09-18
obsolete: true
---

> The previous occupant of this path (the 2026-09-06 F004 draft, already `obsolete: true`) was
> archived verbatim to `spec/references/F004-research-draft-archived-2026-09-06.md` before this
> file was overwritten.

# Research Findings

All measurements below were taken on 2026-09-18 at `3c01c9f` by decoding the real fixture corpus
with `fitdecode` and by grepping the shipped module — not read out of any spec document.

## R1 — The rule has no notion of device identity. Confidence: HIGH (counted)

`grep -c source_device runcoach-api/src/runcoach_api/metrics/hrv_trend.py` → **0**. The module's own
docstrings say it in as many words: "the returning-*or-brand-new*-device shape, stated without a
notion of 'device' in the vocabulary" (`hrv_trend.py:524`), and "a device return and an abandoned
trial differ in the day order" (`:555`).

Nine review cycles of qualifiers — T093, T094, T095, T106, T107, T116, T117, T125, T129, T132 — are
predicates that **infer a device event from counts and orderings of readings**. [[T130]] then proved
that family exhausted: every predicate keyed on the shape of the judged week has a carrier-overlap
`c` that defeats it, measured over 12,300 rows.

**This is IDEA-071's "missing model", stated in one line: the rule is inferring, from statistics, a
fact the ingested data already carries directly.**

## R2 — `source_device` as stored cannot tell a strap from a wrist. Confidence: HIGH (measured)

`mapping._build_source_device` (`mapping.py:196`) takes `_first_of(by_name, "device_info")` — always
the `creator` entry, i.e. the **head unit** — and composes `f"{garmin_product} fw{software_version}"`.

Measured across all eleven fixtures:

| fixture | stored `source_device` |
|---|---|
| `strap_hrv_capture.fit` | `fr945_lte fw17.4` |
| `wrist_ppg_hrv_snapshot.fit` | `fr945_lte fw17.4` |
| `sample_health_snapshot.fit` | `fr945_lte fw17.4` |
| (7 others) | `fr945_lte fw17.4` |
| `dev_fields_run.fit` | `fr955 fw19.18` |

**Ten of eleven files are one string.** A chest-strap RR capture and a wrist-PPG snapshot taken on
the same watch are *identical* under this column. Keying "per-device datasets" on the stored column
would collapse exactly the two datasets the feature exists to separate.

It is simultaneously **too coarse** (strap vs wrist invisible) and **too fine** (`fw17.4` is in the
key, so every firmware push mints a new identity, and under per-device baselines a new identity is a
lost baseline — silence).

## R3 — True per-unit sensor identity IS in the FIT, and is discarded at mapping. Confidence: HIGH (measured)

`strap_hrv_capture.fit` carries 15 `device_info` messages. Distinct `source_type` values:

| entry | `source_type` | `garmin_product` | `serial_number` | `software_version` |
|---|---|---|---|---|
| `device_index: 'creator'` | `local` | `fr945_lte` | **3408655245** | 17.4 |
| `device_index: 2, 6` | **`antplus`** | **`hrm_pro_plus`** | **3611410126** | 8.9 |

`wrist_ppg_hrv_snapshot.fit` (14 messages) and `sample_health_snapshot.fit` (10) carry **no `antplus`
entry at all** — every entry is `source_type: 'local'`.

So the corpus already distinguishes, per unit and not per model:

- **the watch**, by the `creator` entry's `serial_number` (3408655245) — a real per-unit serial, not
  the model string `_build_source_device` composes;
- **the strap**, by the `antplus` entry's `serial_number` (3611410126), stable across the strap's own
  firmware (`software_version` is a separate field, so a firmware push does not mint a new identity);
- **the absence of a strap**, by the absence of any `antplus` entry.

**Dataset identity at true unit granularity is available. It is thrown away at ingestion**, because
`_build_source_device` reads only the first `device_info` message.

**Caveat, stated rather than glossed:** this is one athlete, one watch, one strap, all
`manufacturer: 'garmin'`. A non-Garmin strap carries a different `manufacturer`, and one entry in
this same file carries an unresolved numeric `garmin_product: 21`. The *shape* is proven; the
*coverage* across vendors is not, and the sprint must treat "no resolvable sensor identity" as a
first-class case rather than an error.

## R4 — §3.7.3 already mandates firmware-change re-establishment, and it is unimplemented. Confidence: HIGH

`specification/spec/03` §3.7.3: "When the primary source tier changes — the athlete adopts or
abandons the chest strap, switches to Health Snapshot, **or a device/firmware change shifts the
overnight pipeline (which is why `source_device` firmware is recorded, §2.2.1)** — the system treats
it as a **baseline re-establishment**."

By R1 the rule reads neither device nor firmware, so **the device/firmware clause of this ratified
sentence has never been implemented**. This feature closes a live spec-vs-code gap rather than
opening a new direction — and R2 shows the column §3.7.3 points at cannot carry the clause either.

## R5 — The quality attribute is an activation, not an invention. Confidence: HIGH

§3.7.1 defines per-tier **confidence weights**, "shipped defaults grounded in the
resting/nocturnal-PPG validation literature (§2.4.5) and tunable as the athlete's own multi-source
history accumulates". §3.7.4 states "no confidence weight is computed in this section today",
deferring application to Section 6's readiness fusion. A quality attribute carried on a dataset is
that concept applied one section earlier. **No new research basis is needed** — but see Cost 1 in
IDEA-071: the exchange rate between recency and quality is a new tunable constant of exactly the
kind this idea was filed about.

## R6 — Per-source datasets do not touch the adoption silence. Confidence: HIGH (already measured by T138)

F005's cost table, `Negative Class`: a clean, gapless, permanent switch costs **18** consecutive
`hrv_unavailable` days (`min_baseline_readings + 7 − min_window_readings` = 14 + 7 − 3), with
`established: true` and both reset fields `null` throughout, and the reset reported **20 days late**.
A device the athlete has **never used** holds no baseline under any scheme, so this survives the
reframe untouched. [[T137]] and [[T138]] are `done` but **documented the cost rather than fixing it**
(`behaviour_change: false`, "no file under `runcoach-api/src/` may change").

F005 names the honest fix itself: on **sixteen** of those 18 days the response reports
`unavailable_reason: week_too_thin` to an athlete who captured every single morning — "true of the
resolved tier, false of the athlete. The honest reason would be a **seventh cause**, *the tier that
owns the baseline is not the one you are recording on*."

## R7 — Constitution check

**Tensions:** none found. §3.7.3 forbids mixing tiers *within one band*; N independent bands honours
that exactly — it retains the losers rather than discarding them, and never mixes.

**Gaps (no rule covers these; resolve in discussion, offer to codify in Phase 6):**

1. **No rule governs a reframe that supersedes ratified behaviour.** Ten behaviours (T093, T094,
   T095, T106, T107, T116, T117, T125, T129, T132) were each ratified on measured evidence. Nothing
   in `.claude/rules/` says what standard of evidence is required to *remove* one, or that a
   superseded behaviour's pins must be re-pointed rather than deleted.
2. **No rule requires a sweep to state the axes it holds constant** — the lesson [[T145]] just paid
   for (capture density was fixed in every sweep for nine cycles), and
   `sweep-the-claim-not-the-diff.md` covers corrections, not measurements.

# Challenge Resolutions

All resolved by user decision on 2026-09-18 during this discussion. Each line is a decision, not a
recommendation.

## The shape

| # | Question | Decision |
|---|---|---|
| 1 | What is a dataset keyed on? | **`hrv_source_tier`** — N = 3. Not sensor identity, despite R3 showing it available. |
| 2 | Does sensor identity get persisted anyway? | **Yes, as groundwork.** Resolved from the full `device_info` list and stored additively; **unread by the rule**. |
| 3 | A morning with a strap AND a watch capture | **Feeds both datasets.** Each keeps its own per-day collapse. |
| 4 | Contract shape | **Additive `datasets[]`.** `baseline`/`band` stay, now meaning *the selected dataset's*. Adds `selected_dataset`, `selected_reason`, `disagreed_with`. |
| 5 | Two established datasets disagree | **The selected dataset decides.** Disagreement is reported, never overrides. |
| 6 | Selection form | **Quality-first with a recency tolerance gate** (below). |
| 7 | Does the §3.7.1 confidence weight arbitrate? | **No.** See the split below. |
| 8 | Explicit hysteresis | **Not now.** Flip rate becomes a scored criterion; hysteresis only if the winning form fails it. |
| 9 | Retiring ratified qualifiers | **Red-then-green pin per retirement.** Old pins re-pointed, never deleted. |
| 10 | The 18-day adoption silence | **Fully out of scope**, including the `week_too_thin` reason string on 16 of those days. |
| 11 | Feature shape | **One feature, measurement-first.** |

## The selection rule, as decided

Among datasets that are **judgeable** — established (>= `MIN_BASELINE_READINGS`) **and** holding
>= `MIN_WINDOW_READINGS` distinct days in the judged week — promote the **highest-fidelity** one,
**unless** it has gone unread longer than `RECENCY_TOLERANCE_DAYS`, in which case it is skipped.

**This is §3.7.1's ratified hierarchy preserved, not a new precedence.** The reframe's contribution
is that the loser now keeps its own band. That is what made the hierarchy unsafe before: striking a
tier destroyed the only yardstick, which is why T117's gate had to exist and why T125 then had to
patch it. With N bands, striking a dataset merely promotes another dataset's *own* band.

**No new constant.** `RECENCY_TOLERANCE_DAYS` (28) already exists, is already measured — justified
band [18, 44] — and is already reasoned against `gap_reset_days`. It is redeployed, not minted.

### The quality split — this resolves an apparent contradiction in the answers

Two distinct things were both being called "quality":

- **Fidelity rank** — the ordinal in `TIER_FIDELITY` / `_FIDELITY_RANK`. **Arbitrates selection.**
  Already shipped, already ratified by §3.7.1.
- **Confidence weight** — the numeric per-tier weight §3.7.1 defines and §3.7.4 defers to Section 6.
  **Reported on `datasets[]`, never arbitrates.**

So quality-as-precedence decides, quality-as-number is carried. **IDEA-071 cost 1 — the new
recency × quality exchange rate — therefore never arises**, which was its purpose.

## The §1.7 exposure, stated plainly and carried into the Negative Class

Because the selected dataset decides, the feature's entire §1.7 exposure sits in selection. The
forbidden shape is: **promoted verdict `hrv_normal` while another established dataset reads below its
own band.** Under quality-first that promotion is made on the *best available* instrument, which is
the strongest defence available, but it is exposure and not immunity: a consumer reading `hrv_status`
alone — every consumer today, and Section 6 as specified — is not told about `disagreed_with`.

**This is newly measurable and was not measurable before.** Under the fused rule the losing tier had
no band, so the rate of this shape could not be computed at all. It must be swept and priced.

## Stability — the correction that decided the form

Two flip triggers, not one:

1. **Tolerance gate** — the fidelity leader goes unread > 28 days. Slow, rare.
2. **Loss of week coverage** — its judged-week days fall below `MIN_WINDOW_READINGS`. Fast, common.

**Trigger 2 dominates, and every candidate form inherits it identically**, because judgeability is a
precondition under all of them — promoting a dataset with 2 week-readings would emit
`hrv_unavailable` while another dataset could have spoken.

A 7-day sliding window covers each weekday exactly once, so a **Mon/Wed/Fri** wearer sits at
*exactly* `MIN_WINDOW_READINGS` permanently. **One missed session flips the dataset for seven days** —
the length of time that day stays in the window — and flips back. A 2x/week wearer sits at 2 and is
never a candidate; a 4x/week wearer sits at 3-4 and absorbs one miss. The danger zone is exactly the
3x/week wearer, which is the population F005 already prices as the oscillation.

**It cannot be hysteresis'd away.** Letting the incumbent hold at 2 week-days while a challenger
needs 3 would have a 2-reading week produce a verdict, and §3.7.4 is explicit: "fewer than three
readings in the judged week is HRV unavailable, whatever they say." Ratified; not re-opened here.
**This mitigation was proposed and withdrawn during the discussion — do not re-propose it.**

**It is pre-existing, not created** — `CRITIC-F005` priority 3, open today. The reframe improves it
marginally (the challenger's band is now continuously warm rather than possibly cold).

Consequence, and it is the reason the form was chosen on §1.7 rather than on stability: **the flip
rate will be dominated by capture-density geometry, not by selection form.**

## Accepted costs, to be carried into the Negative Class

| cost | why accepted |
|---|---|
| Same-tier device replacement (strap A -> strap B) stays invisible | Costs **0** silent days — it holds the tier constant, fires no reset, opens no era boundary. Benign. This is what [[T141]] withdrew the composed-silence paragraph over. |
| §3.7.3's device/firmware re-establishment clause stays unimplemented | R2: the column it points at carries the **watch's** firmware, so honouring it would re-establish a *strap* dataset when the *watch* updates — the wrong event. R3's identity is persisted by this feature but deliberately left unread. |
| The 18-day adoption silence, and `week_too_thin` on 16 of those days | Out of scope by decision. A never-used device holds no baseline under any scheme; [[T137]]/[[T138]] priced it and it is unchanged here. |
| The §1.7 promotion exposure above | Accepted with the direction named, to be swept and rated. |

## Constitution gaps — resolved here, offer to codify in Phase 6

1. **Evidence standard for retiring a ratified behaviour** — resolved: red-then-green pin over the
   population the qualifier was added to close; the qualifier's own pins are re-pointed, never
   deleted. Candidate new rule in `.claude/rules/learnings/`.
2. **A sweep must state the axes it holds constant** — resolved: every sweep in this feature names
   them, and **capture density is a required axis**. This is [[T145]]'s lesson; candidate new rule.
