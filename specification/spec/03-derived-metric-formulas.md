# Section 3 — Derived-Metric Formulas

*Build-ready specification, Phase 2. This section defines the layer directly downstream of ingestion: the system's own transparent, reproducible metrics, computed **only** from the canonical raw streams Section 2 delivers. It is the concrete discharge of the raw-over-derived mandate — the point at which the system stops trusting any vendor number and computes its own load, fitness/fatigue/form, training response, heart-rate-variability trend, grade-adjusted pace, and durability from first principles. It conforms to `research/00` as the decision authority — the raw-over-derived principle (`research/00` §1.5), the transparency-and-explainability constraint (`research/00` §1.6), grade-adjusted pace as the universal downstream pace representation (`research/00` Part 2, finding 5), the parameter defaults in the Part 3 decision register (per-session load, fitness/fatigue constants, HRV gate, ACWR, durability), and the resting-HRV source tiering (`research/00` §3.3) — and cites `research/04` §4, `research/05` §1–§2, and `research/01` §5.4 for the evidence and derivations behind every formula it states. Per the project conventions, every formula is stated here in full; the evidence behind it lives in the cited docs and is not re-argued. Every numeric constant below is a **default flagged as a heuristic**, tunable per athlete as data accumulates (`research/00` Part 3).*

---

## 3.1 What this section computes, and the boundary it respects

Section 2 hands this layer a stream of quality-gated canonical session objects — a record stream, an RR stream, and a context object per session, each stamped with the provenance and quality flags the gates raised (§2.2, §2.4). Section 3 turns each session, and the rolling history of sessions, into a fixed set of **derived metrics** that the state model (Section 4) and the adaptation logic (Section 6) consume. The division of labor is strict and load-bearing:

- **Section 3 computes metrics, not state.** It produces per-session and rolling scalars — a load number, a fitness/fatigue/form triple, a decoupling percentage, an HRV-trend verdict, a durability drift figure — each a transparent function of raw streams. It does **not** estimate the athlete's physiological determinants (critical speed, vVO₂max, threshold pace, the endurance exponent); that is Section 4's job, and it consumes these metrics as inputs. The one place the boundary blurs by necessity is the *anchors* certain load metrics need — threshold pace, HR_rest, HR_max. These originate as athlete-profile inputs (Section 1 §1.3 supplies `resting_hr_bpm` and age-based max-HR priors) and are refined by Section 4 as history accrues (threshold pace especially, which Section 4 re-anchors continuously); Section 3 reads the current best estimate of each as a parameter — from the state model where Section 4 maintains one, else from the athlete profile — and records which value it used, so a metric can be recomputed when the estimate updates.
- **Raw only, derived never.** Every input to every formula here is a raw measured signal from the canonical record/RR/context streams. The quarantined vendor-derived sidecar (§2.3.6) is never read by any formula in this section. Where the system reproduces a documented open method that is itself an estimate (e.g. the Banister TRIMP weighting, the Performance-Management-Chart EWMAs), that method is owned and transparent — its formula is stated in full below — and therefore consistent with raw-over-derived (`research/00` §1.5). The one admitted device-computed input is a numeric *resting* rMSSD (§3.7), which is a standard statistic, not a vendor composite, and enters at reduced confidence per `research/00` §3.3.
- **Grade-adjusted pace is upstream of everything pace-based.** Because hills must never masquerade as fitness change (`research/00` Part 2, finding 5), the grade-adjusted-pace transform (§3.3) is computed first, and every pace-derived metric in this section — rTSS, decoupling, efficiency factor, HR-at-reference-pace, durability — consumes the grade-adjusted pace stream, never raw GPS pace.

The metrics are organized into five computational blocks, in dependency order: grade-adjusted pace (§3.3, the substrate), per-session training load (§3.4), the fitness/fatigue/form chart (§3.5), training-response signals (§3.6), and the HRV trend (§3.7), with a durability metric (§3.8) and the advisory ACWR/monotony risk context (§3.9) closing the section. Each block states its raw inputs, its formula, its shipped default constants, its graceful-degradation behavior when an input is missing or low-quality, and an **established-vs-pragmatic** flag distinguishing settled science from an engineering default.

---

## 3.2 The per-session feature-extraction pass

Every gated session runs once through a feature-extraction pass that computes the raw-derived quantities the rolling metrics need, and appends them to the athlete's history time series (`research/05` §1.3). The pass is the single point at which a session's streams are reduced to features; the rolling metrics (§3.5, §3.7, §3.9) then operate on the resulting per-session series, never re-reading the raw streams. The features extracted per session are:

- **Descriptors** — duration, distance, grade-adjusted average pace, average and time-in-zone HR, average cadence, average power (where present with its `power_model` provenance, §2.4.4), total ascent/descent, and the session's environmental context (from `context.env_*`, §2.2.4).
- **The grade-adjusted pace (GAP) stream** — the per-sample transform of §3.3, from which the grade-adjusted average pace and the normalized graded pace for load are taken.
- **Per-session load** — the reconciled load scalar of §3.4, in rTSS-normalized units.
- **Response features** — pace–HR decoupling, efficiency factor, and HR-at-reference-pace where the session contains a qualifying steady segment (§3.6); and, for long runs, the durability drift feature (§3.8).
- **Completion-vs-prescription** — for prescribed workouts, whether each segment's target band was met and the magnitude of any shortfall (`research/05` §1.3). Section 5 owns the prescription representation; Section 3 records only the measured-vs-target comparison as a feature.

A feature that requires a stream the session does not carry, or that a quality gate marked untrustworthy, is emitted as **unavailable** with the gate's flag attached, never imputed to a guessed value (§2.4). Downstream metrics that depend on an unavailable feature degrade per the rules stated with each metric below.

---

## 3.3 Grade-adjusted pace (the universal pace substrate)

Grade-adjusted pace (GAP) expresses the pace an athlete ran on a graded segment as the equivalent-effort pace on the flat, so that a hilly session and a flat session become directly comparable and hills do not register as fitness changes (`research/00` Part 2, finding 5; `research/05` §1.3). It rests on the Minetti cost-of-gradient relationship (`research/01` §5.4), and it is the pace representation every downstream pace metric in this specification consumes.

### 3.3.1 Raw inputs

For each record sample the transform reads the smoothed barometric `altitude` and the cumulative `distance` from the canonical record stream (§2.2.2), both already quality-gated by Section 2 — altitude smoothed before gradient is taken and spans of barometric drift flagged (§2.4.4), pace treated as reliable only over an averaging window rather than sample-by-sample (§2.4.4). The instantaneous gradient at a sample is the change in smoothed altitude over the change in distance across a short window centered on the sample:

> **i = Δaltitude / Δdistance_horizontal**

expressed as a dimensionless grade (rise over run; e.g. i = 0.05 for a 5% uphill, i = −0.05 for a 5% downhill). The window is the same altitude-smoothing window the Section 2 gate applied, so training-run grade and race-course grade (§2.5) are treated consistently.

### 3.3.2 The Minetti cost-of-gradient curve

Minetti et al. measured the metabolic energy cost of running across gradients from steep downhill to steep uphill and fitted the asymmetric U-shaped cost-of-transport curve the physiology doc cites (`research/01` §5.4, ref. [27]). The system uses the published fifth-order polynomial for the energy cost of running per unit distance as a function of gradient *i*:

> **C(i) = 155.4·i⁵ − 30.4·i⁴ − 43.3·i³ + 46.3·i² + 19.5·i + 3.6**

where C(i) is the cost of transport in joules per kilogram per metre (J·kg⁻¹·m⁻¹) and *i* is the gradient as a fraction. On the flat (i = 0) this gives the baseline cost **C(0) = 3.6 J·kg⁻¹·m⁻¹**. The curve reproduces the two physiologically load-bearing features `research/01` §5.4 names: a cost minimum at a slight downhill (near i ≈ −0.10 to −0.20) and a steep cost rise on uphills, with the uphill cost exceeding the magnitude of the corresponding downhill saving — so time lost climbing is not fully repaid descending, and a net-flat rolling course is metabolically costlier than a truly flat one.

**Provenance note.** `research/01` §5.4 cites Minetti et al. (2002) for the curve but states it qualitatively; the fifth-order polynomial and its coefficients above are the standard published form of that same source, reproduced here in full so an implementer needs no further lookup. The coefficients are the shipped default; they are a fixed physiological curve, not a per-athlete tunable, though the whole GAP transform can be swapped behind its interface if a better-validated cost model is adopted.

### 3.3.3 From cost to adjusted pace

The **grade adjustment factor** at a sample is the ratio of the graded cost to the flat cost:

> **g(i) = C(i) / C(0) = C(i) / 3.6**

The grade-adjusted (flat-equivalent) speed for a sample is the actual speed scaled by this factor, and grade-adjusted pace is its reciprocal:

> **v_GAP = v_actual · g(i)** ,  **pace_GAP = 1 / v_GAP**

so that a segment run uphill (g > 1, costlier than flat) maps to a *faster* flat-equivalent pace — the runner was working harder than the raw pace implies — and a downhill segment (g < 1 in the assisted range) maps to a slower flat-equivalent pace. The per-sample v_GAP series is aggregated over a lap/interval or the whole session to give grade-adjusted average pace, honoring the Section 2 rule that pace is reliable only when averaged, not sample-by-sample (§2.4.4).

**Normalized Graded Pace (NGP).** For the load computation of §3.4, the session's GAP stream is further reduced to a single **NGP** — a *fourth-power–normalized* average of grade-adjusted speed (not a simple or duration-weighted mean), following the same Normalized-Power construction TrainingPeaks uses for rTSS (`research/04` §4.1). The fourth-power weighting up-weights the harder portions of a variable session so its physiological stress is not understated by averaging hard efforts with recoveries. The algorithm, stated in full so an implementer needs no further lookup:

1. Take the per-sample grade-adjusted speed series **v_GAP** (§3.3.3) on the uniform 1 s grid Section 2 guarantees (§2.4.1).
2. Compute a **30-second rolling average** of v_GAP, smoothing the second-by-second noise (the same 30 s window Normalized Power uses).
3. Raise each rolling-average value to the **fourth power**.
4. Take the **arithmetic mean** of those fourth-power values over the session.
5. Take the **fourth root** of that mean.

> **NGP = ( mean_t[ ( v̄_GAP,30s(t) )⁴ ] )^(1/4)**

where v̄_GAP,30s(t) is the 30 s rolling average of grade-adjusted speed. NGP is a speed on the flat-equivalent scale (its reciprocal is the pace); it is the pace quantity the intensity factor in §3.4.1 is built from. The 30 s window and the fourth power are the established Normalized-Power constants, carried here unchanged.

### 3.3.4 Degradation

When the barometric altitude stream is absent or the Section 2 gate flagged it as drift-corrupted over a span, GAP over that span falls back to raw pace with a `gap_unavailable` flag on the affected samples, and any downstream metric that assumed grade correction is down-weighted accordingly. A session with no usable altitude at all yields raw-pace-based features carried at reduced confidence — which, per §3.4, also disqualifies rTSS as the primary load metric for that session and triggers the load-metric fallback.

**Flag: established** (Minetti cost-of-gradient is settled science; the NGP weighting is an established practitioner construction).

---

## 3.4 Per-session training load

Load quantifies how much stress a single session imposed, reduced to one number on a common scale so the fitness/fatigue chart (§3.5) can integrate it over time. No single internal-load metric is complete, so the system computes three in parallel from raw data and reconciles them into one unified daily load, exactly as the decision register directs (`research/00` Part 3, per-session-load row; `research/04` §4.1; `research/05` §2.1).

### 3.4.1 The three load metrics

**rTSS (running Training Stress Score) — the primary metric when a valid GAP stream exists.** rTSS anchors a session's load to the athlete's current functional threshold pace and scales it by duration, normalized so that one hour at threshold equals 100 points (`research/04` §4.1):

> **rTSS = (duration_s × NGP × IF) / (FTP_pace × 3600) × 100**

where **NGP** is the session's normalized graded pace as a speed (§3.3.3), **FTP_pace** (functional threshold pace, as a speed) is the athlete's current threshold estimate supplied by Section 4, and **IF** (intensity factor) is the ratio of the session's normalized graded intensity to threshold:

> **IF = NGP / FTP_pace**

Equivalently, since IF = NGP / FTP_pace, the score reduces to **rTSS = (duration_s / 3600) × IF² × 100** — one hour exactly at threshold (IF = 1) scores 100, and the quadratic in IF makes above-threshold work disproportionately costly. rTSS is the **primary** load metric whenever a valid grade-adjusted-pace stream exists (§3.3) and a current threshold estimate is available; it is the most terrain- and duration-portable of the three because it is externally anchored to pace, and it inherits any error in the threshold estimate it is normalized against (a stale threshold biases every rTSS, which is why Section 4 re-anchors threshold continuously; `research/00` §3.1).

**HR-TRIMP (Banister exponential form) — the fallback when GAP is missing.** TRIMP integrates heart-rate-based internal intensity over duration, weighting higher intensities exponentially (`research/04` §4.1, ref. Banister; `research/05` §2.1):

> **TRIMP = duration_min × ΔHR_ratio × 0.64 · e^(1.92 · ΔHR_ratio)**  (men)
> **TRIMP = duration_min × ΔHR_ratio × 0.86 · e^(1.67 · ΔHR_ratio)**  (women)

where

> **ΔHR_ratio = (HR_avg − HR_rest) / (HR_max − HR_rest)**

The sex-specific form changes **both** the pre-factor and the exponential coefficient — men use 0.64/1.92, women use 0.86/1.67 — reflecting the different lactate-profile constants Banister fitted (`research/04` §4.1, ref. Banister). *Correction note: `research/05` §2.1 states this weighting with the women's exponent (1.67) only and carries the men's 0.64 pre-factor across; the canonical Banister women's form uses the 0.86 pre-factor stated here. This section ships the complete canonical form; the `research/05` §2.1 statement should be corrected to match, and the item is logged for back-port.* When `sex` is unspecified in the athlete profile (§1.3), the system defaults to the men's coefficients and flags the choice, consistent with Section 1's sex-neutral-default posture. **ΔHR_ratio** is the fraction of heart-rate reserve: HR_rest and HR_max are athlete anchors supplied per §3.1 (athlete profile, refined by Section 4), and HR_avg is the session's average heart rate from the record stream. TRIMP requires trustworthy HR: per the Section 2 gates it is computed on chest-strap HR without qualification, and on a wrist-PPG session it is down-weighted and disqualified from the primary role for any at-or-above-threshold session (§2.4.2). The zone-based Edwards and Lucía TRIMP variants (`research/04` §4.1) are computed as cross-checks but are not the driving load.

**sRPE-load (session-RPE) — the always-available corroborator and last-resort fallback.** Foster's session-RPE load needs no device at all (`research/04` §4.1, ref. Foster; `research/05` §2.1):

> **sRPE-load = RPE(0–10) × duration_min**

where RPE is the athlete's whole-session rating of perceived exertion, ingested as the subjective input attached to the session (`context.subjective`, §2.2.4; `research/00` Part 2, finding 7). sRPE-load captures non-mechanical stress that HR and pace miss — heat, sleep debt, life stress, cumulative fatigue — and is always available, so it is both the corroborating cross-check on the device-based metrics and the final fallback when both HR and GAP are unusable.

### 3.4.2 Reconciliation into one unified daily load

The chart in §3.5 is driven by a single load series on the rTSS scale, so the three metrics are reconciled per session by the register's rule (`research/00` Part 3, per-session-load row; `research/05` §2.1). Because the three metrics live on very different native scales — rTSS ≈ 100 points per hour at threshold, Banister HR-TRIMP ≈ 150–170 per threshold-hour, sRPE-load ≈ 400+ per threshold-hour — they must be brought onto a common scale **before** they can be compared or averaged. The reconciliation therefore runs in four steps:

1. **Common-scale normalization (the "threshold-hour = 100" anchor).** Each metric is expressed on the rTSS scale by dividing it by that metric's **threshold-hour reference value** — the value the metric takes for one hour at the athlete's functional-threshold intensity — and multiplying by 100, so that one hour at threshold scores 100 on every metric by construction. The threshold-hour reference is the athlete's own historically-established value for that metric when enough matched history exists (the preferred, self-calibrating path); absent that, the **shipped default references** are used: HR-TRIMP at threshold ≈ 60 · ΔHR_ratio_threshold · 0.64·e^(1.92·ΔHR_ratio_threshold) with ΔHR_ratio_threshold taken at the threshold-HR fraction of reserve, and sRPE-load at threshold ≈ (threshold RPE ≈ 8) × 60 = 480. rTSS is already on the scale by definition (its threshold-hour value is 100). This scaling is what makes both the divergence check (step 3) and the fallback ladder (step 2) meaningful; it is not merely a fallback-path detail.
2. **Primary selection by data quality (the graceful-degradation ladder).** If a valid GAP stream and a current threshold estimate exist, the primary is **rTSS**. Else if trustworthy HR exists, it is the scaled **HR-TRIMP**. Else it is the scaled **sRPE-load**. Each step is taken only when the one above it is unavailable: rTSS → HR-TRIMP → sRPE-load.
3. **Divergence check.** When more than one metric is available (all now on the common scale from step 1), compare the primary against the mean of the available metrics. If the primary diverges from that mean by **more than 25%**, flag the session `load_divergence` and set the session's load to the **mean of the available scaled metrics** rather than the primary alone. The assumption, stated in the register: modest divergence is ordinary sensor noise and the primary is trusted, but a large divergence means no single metric is trustworthy on that session, so the conservative reconciliation is to average (`research/00` Part 3).
4. **Output.** One scalar `session_load` per session on the rTSS scale, tagged with which metric drove it, the threshold-hour references used, whether the divergence fallback fired, and the quality flags of its inputs — carried as a feature so the chart and any audit can see how each day's load was derived.

The 25% divergence threshold, the fallback-to-mean rule, and the default threshold-hour references are the shipped defaults, tunable per athlete and displaced by the athlete's own calibration as history accrues (`research/00` Part 3).

**Flag: established** (each of rTSS, HR-TRIMP, sRPE-load is individually well-established; the parallel-compute-and-reconcile scheme is a pragmatic engineering extension, flagged as such per `research/05` §6).

---

## 3.5 Fitness, fatigue, and form (the Performance-Management Chart)

From the unified daily load series the system maintains the longitudinal fitness/fatigue/form summary — the Performance-Management-Chart (PMC) instantiation of the Banister impulse-response idea (`research/04` §4.2; `research/05` §2.3). It is the compact state summary the long- and short-term adaptation loops read (Section 6) and the quantity the taper controller drives (Section 7).

### 3.5.1 The three quantities

- **CTL (Chronic Training Load) — the "fitness" proxy** — an exponentially-weighted moving average (EWMA) of daily load with a **42-day** time constant.
- **ATL (Acute Training Load) — the "fatigue" proxy** — an EWMA of the same daily load with a **7-day** time constant.
- **TSB (Training Stress Balance) — the "form"/freshness proxy** — the difference **TSB = CTL − ATL**, computed by convention from the previous day's CTL and ATL.

### 3.5.2 The EWMA update

Each quantity updates daily from the day's load by the standard exponential recursion (`research/04` §4.2; `research/05` §2.3):

> **X_today = X_yesterday + (load_today − X_yesterday) · (1 − e^(−1/τ))**

with **τ = 42 days for CTL** and **τ = 7 days for ATL**, and `load_today` the unified daily load (§3.4), summed across sessions on a multi-session day and taken as zero on a rest day (so both averages decay correctly on days off). TSB is then CTL − ATL using yesterday's values.

### 3.5.3 Seeding from history (cold start of the chart)

The chart must not start every athlete at an artificial zero fitness. At program start the EWMAs are seeded by running the recursion forward over the athlete's ingested historical load series (the bulk-backfill pass of §2.6.1), so CTL/ATL arrive at program start already reflecting the athlete's accumulated training (`research/05` §3.2). When the available history is shorter than roughly six weeks — less than the CTL time constant — CTL is seeded from the athlete's average weekly load and the seed is flagged provisional, carried at reduced confidence until enough real history has accrued to displace it. This is the chart-level half of the cold-start problem; the physiological-determinant cold start (the non-exercise VO₂max seed of `research/00` §3.2) is Section 4's.

### 3.5.4 What the chart is trusted for, and its stated limits

The register adopts the 42/7 PMC as the operational backbone and ramp-rate guardrail, and explicitly does **not** trust it as a complete fatigue account (`research/00` Part 3, fitness/fatigue-constants row; `research/04` §4.2). The spec carries the four limitations `research/04` §4.2 names, because downstream loops must not over-read the chart:

- **The 42/7 constants are conventions, not fitted values.** True individual fitness/fatigue decay constants are not reliably identifiable from ordinary field data, and per-athlete Banister fits are unstable and overfit-prone (`research/04` §4.2; `research/00` §3.1). Per the governing individualization rule the constants **stay at 42/7 long-term** and are not fitted per athlete at launch (`research/00` §3.1); they are exposed for tuning but default fixed.
- **The chart is garbage-in-garbage-out on its load metric and threshold anchor** — a stale threshold biases every rTSS and therefore the whole chart, which is the operational reason Section 4 re-anchors threshold continuously.
- **The scalar is intensity-blind** — 100 units of easy volume and 100 units of VO₂max work move CTL/ATL identically though they are not interchangeable stimuli. Section 3 mitigates this with the session-type recovery accounting of §3.5.5, which the readiness and spacing logic reads on top of the chart.
- **ATL is load-only** — it captures training-derived fatigue but is blind to sleep, illness, and life stress, which is exactly why the readiness layer (§3.7, and Section 6's day-of gate) fuses HRV and subjective signals on top of the chart rather than trusting TSB alone.

### 3.5.5 Session-type recovery accounting (the extension on top of the chart)

Because the scalar chart is intensity-blind, the system layers session-type-specific recovery time-courses on top of the single-load PMC (`research/05` §2.3; `research/01` §6.3). Each session carries, in addition to its load, a **recovery-cost tag** by type, encoding the approximate time to full recovery the physiology doc gives: easy aerobic ≈ 1 day; threshold ≈ 24–48 h; VO₂max/severe intervals ≈ 48–72 h; long run / durability session ≈ 48–72 h+ (`research/01` §6.3). This does not change how a session contributes to CTL/ATL (both still see only the scalar load); it is a separate per-session residual-fatigue clock that the day-of readiness gate (Section 6, §5.4 of `research/05`) and next-session spacing consult, so that a hard VO₂max session's fatigue is treated as decaying more slowly than an equal-load easy run's. Section 3 computes and stores the tag and the residual-fatigue decay; Section 6 acts on it.

**Flag: established core (42/7 PMC), pragmatic extension (session-type recovery tags), individual-constant fitting deferred** (`research/00` §3.1).

---

## 3.6 Training-response signals

Response metrics answer whether fitness is moving and in which direction, separating genuine adaptation from day-to-day noise. Each is a raw-derived feature computed on qualifying steady segments and interpreted only as a **within-athlete trend**, never as a single-session verdict (`research/05` §2.2; `research/04` §5.4).

### 3.6.1 Pace–HR decoupling (aerobic decoupling)

Decoupling is the percentage rise in the pace-to-HR ratio from the first half to the second half of a steady aerobic effort — the degree to which HR climbed for the same grade-adjusted pace (`research/04` §5.4; `research/05` §2.2). On a steady segment split into first and second halves:

> **decoupling% = ( (v_GAP/HR)_first_half − (v_GAP/HR)_second_half ) / (v_GAP/HR)_first_half × 100**

where v_GAP is grade-adjusted speed (§3.3), so hills do not create spurious drift. The established practitioner benchmark is **< 5% decoupling** as good aerobic durability for the effort; larger decoupling signals a durability/fatigue limit or heat/dehydration on the day (`research/04` §5.4). Falling decoupling at a fixed grade-adjusted pace over weeks is positive aerobic adaptation. Decoupling is computed only on segments the gates marked steady and trustworthy (chest-strap HR preferred; §2.4.2), and on long runs its within-run form feeds the durability metric of §3.8.

### 3.6.2 Efficiency factor (EF)

EF is grade-adjusted speed per unit heart rate on a steady aerobic effort (`research/04` §5.4; `research/05` §2.2):

> **EF = v_GAP_avg / HR_avg**

computed over a steady segment at a controlled easy/steady effort. A rising EF trend at matched conditions and equal internal load indicates improving aerobic efficiency/economy. Because the absolute value is individual and confounded by heat, sleep, and surface, **only the within-athlete trend under matched conditions is interpreted**, never the absolute number or a cross-athlete comparison (`research/04` §5.4).

### 3.6.3 HR-at-reference-pace

For a recurring anchor pace the athlete runs often, the system trends the average HR observed at that grade-adjusted pace; a downward drift over weeks signals positive adaptation (lower cardiovascular cost for the same speed). It is complementary to EF and is computed whenever a session contains a steady segment near a known anchor pace (`research/05` §2.2).

### 3.6.4 Interpretation rule (corroboration before crediting adaptation)

Response signals are pragmatic trend signals, so the system requires a **sustained multi-session move** before crediting adaptation and letting it move the state estimate: a trend holding over roughly **≥ 2–3 weeks**, or a determinant re-estimation supported by **≥ 2 qualifying efforts**, before Section 4 shifts a determinant on the strength of it (`research/05` §2.2). A single strong (or weak) session does not re-anchor the state model; this corroboration rule is what keeps the adaptation logic from chasing noise, and it is the Section 3 side of the "trends, not single readings" principle the constitution states (`research/00` §1.4). Section 4 computes the features and flags a candidate trend; Section 4 decides whether the corroboration bar is met and updates the determinant.

**Flag: established as monitoring heuristics** (decoupling, EF, HR-at-pace are established practitioner signals; their thresholds are heuristic defaults, and durability-related interpretation is flagged emerging in §3.8).

---

## 3.7 The HRV trend

Heart-rate variability is the system's primary autonomic readiness signal, and its trend — not any single reading — is what the day-of readiness gate consumes (Section 6). The trend is built from a **resting-state** HRV reading, sourced by the four-tier hierarchy of `research/00` §3.3: the system prefers a chest-strap RR capture it reduces to rMSSD itself, and degrades — at reduced confidence — to a device-computed numeric resting rMSSD before it treats HRV as unavailable. It is **never** built from intra-workout RR and never from in-run wrist PPG (`research/00` Part 3, HRV-gate row and §3.3; `research/02` §2.3, §3.2, §4.1; `research/05` §2.4).

### 3.7.1 Raw input and its Section 2 interface

The input arrives from Section 2 in one of two forms, tagged by `hrv_source_tier` (§2.2.3):

- **Raw-RR tier (`chest_strap_raw`) — the preferred source.** The artefact-filtered RR series from a morning `resting_hrv_check` capture (§2.2.3, §2.4.5): reconstructed per §2.3.4, artefact-filtered per §2.4.3 (intervals outside 300–2000 ms or differing from the local median by more than 20% removed/corrected), and carrying its `rr_valid_fraction` quality weight. Section 3 consumes exactly this interface; it does not re-implement RR reconstruction or artefact filtering, which are Section 2's. A capture whose valid fraction is too low (the §2.4.3 default rejects a sample retaining < 80% of beats) is dropped from the trend rather than admitted as a clean reading.
- **Numeric-rMSSD tiers (`health_snapshot`, `health_api_overnight`) — the reduced-confidence fallback.** A device-computed resting rMSSD supplied as the scalar `rmssd_precomputed` (§2.2.3), with no raw beats — Health Snapshot's `RmssdAvgValue` or the Health API's overnight `lastNightAvg` (§2.4.5). There is nothing to artefact-filter; the reading enters the trend directly at its tier's confidence weight (below the raw-RR tier, per the validation caveats in §2.4.5: Liang et al. 2024, *Sensors*; Zuern et al. 2026, *Scientific Reports*).

### 3.7.2 rMSSD and the log transform

For the raw-RR tier, from a filtered resting RR series the system computes **rMSSD**, the root mean square of successive RR differences — the standard vagally-mediated HRV index for endurance monitoring (`research/04` §5.1; `research/05` §2.4):

> **rMSSD = √( (1/(N−1)) · Σ (RR_{k+1} − RR_k)² )**  (ms)

over the N filtered intervals of the capture. For the numeric-rMSSD tiers the value is taken directly from `rmssd_precomputed` (the device already reduced its beats to this same statistic; the system does not recompute it). Either way the trend then operates on the natural log, **ln rMSSD**, which is the better-behaved quantity for trending (it stabilizes the variance and is the form the HRV-guided-training literature uses). Because both forms are the *same statistic in the same units*, they are directly comparable in principle — but subject to the per-source baseline discipline of §3.7.3, because their systematic biases differ.

### 3.7.3 The rolling baseline, the smallest-worthwhile-change band, and per-source consistency

The trend the readiness gate reads is the **7-day rolling mean of ln rMSSD**, compared against the athlete's own **smallest-worthwhile-change (SWC) band** (`research/00` Part 3, HRV-gate row; `research/04` §5.1; `research/05` §2.4). The band is the athlete's rolling-baseline mean plus or minus a coefficient-of-variation-scaled width:

> **band = mean_baseline(ln rMSSD) ± 0.5 · CV(ln rMSSD)**

where the baseline mean and the coefficient of variation CV are computed from the athlete's own rolling history of ln rMSSD (a longer window than the 7-day mean, so the band reflects the athlete's normal variability, not just the last week). The verdict Section 3 emits:

- **Within or above the band** → HRV normal (autonomic readiness intact).
- **7-day mean dropped below the band** → HRV suppressed (incomplete recovery / accumulated fatigue / illness onset).

**Per-source baseline discipline (the anti-mixing rule).** Because each resting-HRV source carries its own systematic bias (a chest-strap rMSSD and an overnight-PPG rMSSD are the same statistic but not the same *number* for the same physiology), the rolling baseline and SWC band **must be built on a consistent source tier** (`research/00` §3.3). The trend does not average across tiers within one band. When the primary source tier changes — the athlete adopts or abandons the chest strap, switches to Health Snapshot, or a device/firmware change shifts the overnight pipeline (which is why `source_device` firmware is recorded, §2.2.1) — the system treats it as a **baseline re-establishment**: it begins accumulating a fresh baseline for the new source and withholds a below-band suppression verdict until that baseline is adequately established, rather than reading the source switch as a physiological HRV drop. Where the athlete supplies more than one tier concurrently (e.g. an occasional chest-strap capture alongside nightly overnight readings), the system trends the highest-fidelity tier that is present densely enough to sustain a baseline and uses the others only as corroboration, never merged into the same band.

The **±0.5·CV** width and the **7-day** rolling window are the shipped defaults from the register (`research/00` Part 3); both are tunable, and the band is refined as the athlete's own baseline accumulates. The construction deliberately uses only the published open HRV math — rolling ln rMSSD means and a CV-based band (Plews/Altini/Kubios lineage) — and no proprietary recovery-index construction, per the freedom-to-operate guardrail (`research/00` Part 4); admitting a device's numeric rMSSD as an input does not change this, because the baseline, band, and verdict are all computed here with the open math (`research/00` Part 4).

### 3.7.4 Trend, not single reading; graceful degradation

Two rules govern how the verdict is used, both from the constitution and the register:

- **Trends, not single readings.** A single below-band morning is weak evidence; a coherent multi-day baseline decline is actionable, and a single good morning does not instantly clear an accumulated multi-day suppression (`research/00` §1.4; `research/04` §5.1). The 7-day rolling mean is the mechanism that enforces this — the gate reads the smoothed baseline, not today's raw value alone. This smoothing is also what absorbs the extra per-reading noise the numeric-rMSSD tiers carry (§3.7.1).
- **Graceful degradation across tiers, then unavailable.** On a given day the trend uses the highest source tier available, at that tier's confidence (§3.7.1). A missing chest-strap capture no longer means "HRV unavailable" — the logic falls to a numeric resting rMSSD (Health Snapshot, then Health API overnight) if one is present, subject to the per-source baseline rule of §3.7.3. Only when **no** resting-HRV reading of any tier exists for the day does the HRV input go unavailable; Section 3 then emits `hrv_unavailable`, and the readiness logic (Section 6) widens its guardrails and leans harder on the subjective and resting-HR axes, exactly as §2.4.5 specifies — never substituting an intra-workout or in-run-PPG value (`research/00` Part 2, finding 8).

Resting HR is trended alongside HRV from the same morning captures as a corroborating autonomic signal (an elevated morning resting HR relative to the athlete's baseline suggests incomplete recovery or illness); it is a secondary input to the readiness fusion Section 6 performs, and Section 3 supplies its baseline-normalized deviation as a feature.

**Flag: emerging-toward-established** (HRV-guided training's evidence direction is consistently favorable but effects are moderate and protocols vary; `research/04` §5.1). The math (rMSSD, ln transform, rolling mean, CV band) is established; the decision rule built on it is the emerging part, and the numeric-wrist tiers are admitted at an explicit confidence discount grounded in the resting/nocturnal-PPG validation literature (§2.4.5).

---

## 3.8 Durability

Durability — resistance to the deterioration of economy, thresholds, and the pace–HR relationship over prolonged exercise — is the determinant most responsible for late-race slowdown, especially in the marathon, and is an explicitly emerging construct (`research/01` §1.6; `research/00` Part 3, durability row). Section 3 computes a transparent within-athlete durability feature; it does not claim an absolute, cross-athlete-comparable durability scale.

### 3.8.1 The metric

Durability is quantified as the **drift in efficiency factor (or pace–HR decoupling) over the back third of long runs at equal grade-adjusted pace** (`research/00` Part 3, durability row; `research/05` §2.2; `research/01` §1.6). Concretely, on a qualifying long run the session is split into thirds, and the durability feature is the degradation of EF (§3.6.2) — equivalently the decoupling (§3.6.1) — from the first third to the final third:

> **durability_drift% = ( EF_first_third − EF_final_third ) / EF_first_third × 100**

A smaller drift means better fatigue resistance. The feature is computed on grade-adjusted pace so terrain does not masquerade as fatigue, and only on runs long enough and steady enough to expose durability (a qualifying-long-run gate, e.g. sustained duration above a threshold at controlled effort; the exact qualifying duration is a Section 5 workout-typology parameter that this metric reads).

### 3.8.2 Interpretation and status

Three constraints govern its use, all from the emerging-construct flag:

- **Within-athlete trend only.** The absolute drift scale is uncalibrated across athletes, so only the athlete's own trend over weeks — is late-run drift shrinking as long-run volume accumulates? — is interpreted (`research/05` §2.2; `research/01` §2.4). A falling durability_drift trend is the read-out that durability training is working.
- **Corroborating, not primary.** Because the durability evidence base is young (`research/01` §1.6), the state model treats this as a transparently-derived corroborating signal feeding the durability determinant Section 4 carries, not a validated primary metric; its influence on the race-pace projection is bounded and surfaced in the confidence band rather than presented as precise (Section 1 §1.4.3 already consumes durability this way).
- **Flagged emerging everywhere it surfaces.** Any athlete-facing or downstream use carries the emerging-construct flag, per the uncertainty-explicit convention (`research/00` Part 3).

**Flag: emerging** (no settled field-measurement standard exists; `research/01` §1.6).

---

## 3.9 Injury-risk context: ACWR and monotony (advisory only)

The load-dynamics risk signals are computed here so the injury-risk logic (Section 6) can read them, but with a sharp constraint the register imposes: the acute-chronic workload ratio is **advisory context only, never a hard gate** (`research/00` Part 3, ACWR row; `research/03` §4.1–4.2; `research/05` §2.5).

### 3.9.1 ACWR (soft context / spike flag)

The acute-chronic workload ratio is the ratio of acute (7-day) to chronic (28-day) load, in EWMA form (`research/05` §2.5):

> **ACWR = acute_load(7-day EWMA) / chronic_load(28-day EWMA)**

computed from the same unified daily load series as the chart (§3.4). A ratio outside a **wide band (default < 0.8 or > 1.3–1.5)** raises a `load_spike` context flag — and nothing more. It never gates a session by itself, because ACWR is heavily critiqued for mathematical coupling and spurious correlation (Lolli) and for conceptual pitfalls (Impellizzeri), which make any hard threshold indefensible (`research/03` §4.1–4.2; `research/00` Part 3). It is a spike detector that adds context to the injury-risk composite, whose real weight sits on the better-supported signals below.

### 3.9.2 Monotony and strain

Foster's training monotony and strain are better-supported load-pattern risk signals than ACWR (`research/05` §2.5; `research/03`):

> **monotony = mean(daily load over the week) / SD(daily load over the week)**
> **strain = weekly total load × monotony**

High monotony — a week of samey daily loads with little hard/easy variation — is flagged above a default of **~2.0** and directly informs the injection of easy/rest variation by the weekly loop (`research/05` §2.5). Monotony and strain carry more risk weight than ACWR in the composite.

### 3.9.3 What Section 3 emits and what it does not

Section 3 emits ACWR (with its context flag), monotony, and strain as features, plus the subjective pain/soreness trajectory carried through from `context.subjective` (§2.2.4) for Section 6 to trend. It does **not** compute the injury-risk verdict or fire any hard flag — the ordinal injury-risk level and the non-negotiable hard flags (bone-stress-injury-pattern pain, night pain, focal bony tenderness, systemic illness, RED-S indicators) are Section 6's, sitting at the top of the arbitration ladder (`research/00` §1.2; `research/05` §2.5, §5.6). Section 3's role is strictly to compute the load-dynamics context those safety decisions read.

**Flag: ACWR contested (advisory only by design); monotony/strain better-supported** (`research/03` §4.1–4.2).

---

## 3.10 What this section hands to the rest of the system

Section 3 delivers the system's own transparent metric layer, computed only from the canonical raw streams and never from the quarantined vendor sidecar: a grade-adjusted-pace substrate (§3.3) that every pace metric downstream consumes so hills never read as fitness change; a reconciled per-session load on the rTSS scale with a graceful rTSS→HR-TRIMP→sRPE degradation ladder and a divergence-fallback (§3.4); the 42/7 fitness/fatigue/form chart with session-type recovery accounting layered on top (§3.5); the within-athlete training-response signals — decoupling, efficiency factor, HR-at-reference-pace — with the multi-week corroboration rule that governs when they may move the state (§3.6); the resting-HRV trend against a ±0.5·CV smallest-worthwhile-change band, sourced by the four-tier hierarchy (chest-strap raw RR preferred, degrading to a numeric resting rMSSD at reduced confidence) with per-source baseline discipline (§3.7); the emerging durability drift metric (§3.8); and the advisory-only ACWR/monotony/strain risk context (§3.9). Every constant stated is a tunable heuristic default; every formula is stated in full and grounded in the cited research.

Three explicit hand-offs carry forward. **Section 4** consumes these metrics to establish and update the physiological state estimate — it reads the load chart, the response signals (subject to the §3.6.4 corroboration rule), the HRV and durability trends, and it supplies back the threshold/HR_rest/HR_max anchors this section's load formulas depend on, closing a two-way interface. **Section 6** reads the load chart, the HRV-trend verdict (with its source-tier confidence), the session-type residual-fatigue clock, and the ACWR/monotony/subjective context to drive the five-timescale adaptation logic and the injury-risk safety override, which Section 3 deliberately does not itself compute. **Section 7**'s taper controller drives the same TSB the chart produces toward the race-day form band. The division of labor with Section 2 is preserved: Section 2 normalizes and quality-gates the raw data; Section 3 computes transparent metrics on it; Section 4 turns those metrics into the physiological state estimate the pace target (Section 1) depends on.

---

*Open items logged for this section (see `spec_development_plan.md`): (1) **Back-port correction to `research/05` §2.1** — that doc states the women's HR-TRIMP weighting with the corrected exponent (1.67) but carries the men's 0.64 pre-factor across; the canonical Banister women's form is 0.86·e^(1.67·ΔHR_ratio), which this section (§3.4.1) ships. The research doc should be corrected to match. No new decision-under-uncertainty is introduced. The load-fallback common-scale references (§3.4.2, step 1) are now stated in full (threshold-hour = 100 anchor, with default HR-TRIMP and sRPE threshold-hour references) — an implementation default, not an open scientific question. The NGP algorithm (§3.3.3) and the Minetti polynomial (§3.3.2) are likewise now stated in full; the Minetti coefficients are reproduced from the source `research/01` §5.4 cites, and if that citation is ever repointed to a different cost-of-gradient model, this section's GAP transform is the single place to change. (2) The resting-HRV source tiering (§3.7) implements `research/00` §3.3; the numeric-wrist tiers' confidence weights are shipped defaults grounded in the resting/nocturnal-PPG validation literature (§2.4.5) and are tunable as the athlete's own multi-source history accumulates.*
