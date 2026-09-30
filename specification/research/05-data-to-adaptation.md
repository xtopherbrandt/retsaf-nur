# From Data to Adaptation

**Abstract.** This document is the synthesis bridge of the Phase-1 research. It specifies, in implementable detail, how the coaching system turns raw wearable and subjective data into autonomous plan adaptation. It defines the processing pipeline (ingest → quality-gate → per-session feature extraction), the system's own transparent derived metrics organized into five families — training **load**, training **response/adaptation**, **fatigue**, **readiness**, and **injury-risk** — and the physiological **state model** that estimates the athlete's determinants of race pace and their trends. It then translates that state into a predicted race-day average pace with course and environmental modifiers, and defines the **adaptation decision logic across five nested timescales** (long-term trend, short-term trend, recent-workout, day-of readiness, intra-workout) together with the arbitration rule and the safety override that outranks the pace objective. It closes with an ADOPT/EXTEND/REPLACE evaluation of candidate open methods and an enumeration of unsettled choices with the defaults the system ships. It builds directly on and stays consistent with `research/01-exercise-physiology.md`, `research/02-wearable-data-garmin.md`, `research/03-subjective-injury-recovery.md`, and `research/04-coaching-periodization.md`. Every threshold given as a number is a **heuristic default** (`research/00` Part 3), tunable per athlete only under the individualization rule (`research/00` DOC-06, IND-01, §3.1) and never where it is a design invariant.

---

## 1. The processing pipeline

Adaptation is only as trustworthy as the features feeding it, so the pipeline front-loads quality gating. Every incoming activity (a FIT file or its API-decoded equivalent, `research/02`) passes through four stages: ingest into the canonical schema, quality-gate, per-session feature extraction, and append to the athlete's rolling time series.

### 1.1 Ingest — canonical schema

All vendor data is mapped onto the vendor-neutral, timestamped stream schema defined in `research/02`: per-record `timestamp`, `heart_rate`, `rr_interval[]` (beat-to-beat, when present), `speed`/`enhanced_speed` and derived `pace`, `position` (lat/lon), `distance`, `altitude` (barometric preferred), `cadence`, `power` (if a running-power source is present), and running-dynamics fields (vertical oscillation, ground-contact time, stride length) where available. Session-level metadata carries device/sensor identifiers, sport, and start time. Vendor-derived black-box fields (Garmin/Firstbeat VO2max, Training Status, Training Readiness, Body Battery, Performance Condition, and the HRV Status Balanced/Unbalanced/Low classification) are ingested into a **separate, quarantined namespace** and never feed a decision, whatever any setting or default says — they exist only for optional corroboration and for showing the athlete a familiar number, neither of which feeds a decision (`research/00` PRIN-08, PRIN-20; `research/02`).

### 1.2 Quality gating

Gating decides whether a stream is trustworthy enough to derive metrics from, and records a per-stream quality flag rather than silently discarding data.

- **HR source / chest-strap detection.** Presence of an `rr_interval` stream is treated as the signature of a chest strap (or arm ECG), which is research-grade for HR and the only RR source the system computes HRV from itself (a device's numeric resting rMSSD enters separately, at reduced fidelity, `research/00` HRV-01; `research/02`). Absence of RR plus wrist-optical device metadata flags the HR stream as **PPG-degraded**: usable for easy-run averages but down-weighted for anything at or above threshold, where PPG suffers cadence-lock and lag. HRV (rMSSD) is **never** computed from in-run wrist PPG.
- **Smart-recording detection.** Uniform 1-second sampling is required for TRIMP, decoupling and any uniformly-integrated metric (`research/02`). Detect smart recording by inspecting the inter-record interval distribution: if the median gap > 1 s or the interval variance exceeds a small tolerance, flag the file `NON_UNIFORM`. Non-uniform files may still yield lap-averaged features (average pace, average HR) but are **excluded** from decoupling and integrated-TRIMP computation, or resampled to 1 Hz by interpolation with a reduced-confidence flag.
- **RR artefact filtering.** Before any HRV computation, filter the RR series: remove intervals outside a physiological band (default 300–2000 ms) and apply an adaptive-threshold filter that rejects an interval differing from the local median by more than a set fraction (default 20–25%), replacing rejected beats by interpolation. Reject the whole HRV sample if the retained fraction < 80%. This follows the standard artefact-correction practice HRV research depends on (`research/03`).
- **GPS smoothing.** Instantaneous GPS pace is noisy; smooth speed with a short rolling window (default 10–30 s) or, preferably, work with **lap/interval averages** for any pace feature that anchors decision logic (`research/02`). Grade is computed from barometric altitude (smoothed) over distance, not from GPS elevation.

### 1.3 Per-session feature extraction

From each gated session the system computes a fixed feature vector, appended to the athlete's history:

- **Descriptors:** duration, distance, average and time-in-zone pace, average HR, average cadence, average power (if present), total ascent/descent, environmental context (temperature/humidity if available; else from a weather lookup by time/location).
- **Grade-adjusted pace (GAP):** each record's pace corrected for gradient via the Minetti cost-of-gradient curve so that uphill and downhill segments are expressed as equivalent-flat pace (`research/01`). GAP is the pace representation used everywhere downstream, so hills do not masquerade as fitness changes.
- **Internal-load inputs:** the HR time series (for TRIMP), the GAP time series relative to threshold (for rTSS), and — attached post-session — the athlete's session-RPE (for sRPE-load).
- **Response features:** pace–HR **decoupling** (§2.2), **efficiency factor** (EF = GAP-speed ÷ HR), and **HR-at-reference-pace** sampled where the session contains a steady segment near a known anchor. For long runs specifically, a **durability** feature: the drift in EF/decoupling between the first and last thirds of the run (`research/01`).
- **Completion-vs-prescription:** for prescribed workouts, whether each segment's target pace/HR band was met, and the intensity of any shortfall (`research/04`).

These features — not the raw streams — are what the metric and state layers consume.

---

## 2. The system's own derived metrics

The project mandates that the system own transparent, reproducible metrics rather than trust vendor black boxes (project instructions). The metrics are grouped into five families. For each, the definition, raw inputs, and an **established vs pragmatic** flag are given, with defaults named as assumptions.

### 2.1 Training LOAD (per session and rolling)

Load is quantified three ways because no single internal-load metric is complete, then reconciled. All three are **well-established** individually (`research/04`).

1. **HR-TRIMP (Banister exponential form).** `TRIMP = duration_min × ΔHR_ratio × 0.64·e^(1.92·ΔHR_ratio)` for men, and `TRIMP = duration_min × ΔHR_ratio × 0.86·e^(1.67·ΔHR_ratio)` for women — the sex-specific weighting factor **and** exponent both differ (Banister; `research/04`). *(Correction, 2026-08-31: an earlier version of this line gave the women's variant as the men's `0.64` pre-factor with only the exponent changed to `1.67`; the canonical Banister women's form is `0.86·e^(1.67·ΔHR_ratio)`. Corrected here and carried correctly in spec §3.4.1; logged in the `research/00` decision register.)* Here `ΔHR_ratio = (HR − HR_rest)/(HR_max − HR_rest)`. Requires reliable HR (chest-strap preferred) and calibrated HR_rest/HR_max. Edwards/Lucía zone-TRIMP variants are computed as cross-checks.
2. **rTSS (running Training Stress Score).** `rTSS = (duration_s × NGP × IF) / (FTP_pace × 3600) × 100`, where **NGP** is normalized graded pace (from the GAP stream), **IF** = NGP ÷ threshold pace, and threshold pace is the athlete's current functional threshold (`research/04`). This is the pace-based, externally-anchored load and is the **primary** load metric when a valid GAP stream exists.
3. **sRPE-load.** `session-RPE(0–10) × duration_min` (Foster; `research/03`). Always available (needs no device), captures non-mechanical stress (heat, sleep-debt sessions), and is the fallback when HR/GPS are missing.

**Reconciliation into a single daily load.** The system stores all three but drives the Performance-Management-Chart off one **unified load unit** normalized to the rTSS scale. Default rule: if a valid GAP stream exists, `load = rTSS`; cross-check against HR-TRIMP and sRPE-load, and if rTSS diverges from the sRPE-load-implied value by more than a tolerance (default 25%), flag the session and fall back to the mean of the available metrics (assumption: no single sensor is trusted when they disagree materially; the conservative reconciliation is the mean). When GAP is missing, `load = HR-TRIMP` (or sRPE-load if HR is also PPG-degraded). This keeps one consistent load series while degrading gracefully.

### 2.2 Training RESPONSE / adaptation

Response metrics answer "is fitness moving, and which way?" They separate genuine adaptation from day-to-day noise.

- **Pace–HR decoupling.** Percentage rise in the pace:HR (or EF) ratio from first to second half of a steady aerobic effort; **< 5% is the well-established aerobic-durability benchmark** (`research/04`). Falling decoupling at a fixed pace over weeks = improving aerobic fitness/durability.
- **Efficiency Factor (EF = GAP-speed ÷ HR).** Tracked as a trend at controlled easy/steady efforts; a rising EF trend at equal internal load indicates improving economy/aerobic fitness (`research/04`). Established as a monitoring heuristic; the absolute value is individual, so only the **within-athlete trend** is interpreted.
- **HR-at-reference-pace trend.** For a recurring anchor pace, the trend in average HR; a downward drift signals positive adaptation. Complementary to EF.
- **Determinant re-estimation.** The response layer's deepest signal is periodic re-estimation of threshold pace, critical speed (CS), and vVO2max from performance data (§3). A rightward shift in the CS/vVO2max estimates is the strongest response evidence and directly moves the race-pace projection.

Response metrics are **pragmatic trend signals**, not single-session verdicts: the system requires a sustained multi-session move (default: a trend holding over ≥ 2–3 weeks, or a re-estimation supported by ≥ 2 qualifying efforts) before crediting adaptation, to avoid chasing noise.

### 2.3 FATIGUE (and the fitness–fatigue chart)

Fatigue is modeled at two resolutions, reconciling the Banister fitness–fatigue model with the Performance-Management-Chart, which are two expressions of the same exponential-impulse idea (`research/01`, `research/04`).

- **PMC (primary, operational).** From the unified daily load series:
  - **CTL** (Chronic Training Load, the "fitness" proxy) = exponentially-weighted moving average of daily load, **time constant 42 days**.
  - **ATL** (Acute Training Load, the "fatigue" proxy) = EWMA of daily load, **time constant 7 days**.
  - **TSB** (Training Stress Balance, the "form"/freshness proxy) = **CTL − ATL** (using yesterday's values by convention).
  - EWMA update: `X_today = X_yesterday + (load_today − X_yesterday)·(1 − e^(−1/τ))`, τ ∈ {42, 7} (`research/04`; Coggan/TrainingPeaks PMC).
  These constants are **conventional defaults, not physiologically fitted** — flagged as a known open question (§7). The 42/7 split is adopted because it is the documented standard and interpretable, not because it is validated for this athlete.
- **Banister two-component (corroborating / per-session-type).** Fitness and fatigue as separate impulse responses with distinct gains and decay constants; the system layers **session-type-specific recovery time-courses** on top of the single-load PMC — easy ≈ 1 day, threshold ≈ 24–48 h, VO2max/long ≈ 48–72 h+ (`research/01`). Practically this means the fatigue accounting for a hard VO2max session decays slower than an equal-load easy session, which the readiness gate (§5.4) and next-session spacing use even though both contribute equally to the CTL/ATL load series.

Fatigue signals are **subjective-corroborated**: a low TSB that coincides with declining wellness/HRV is treated as real accumulated fatigue; a low TSB with intact wellness/HRV is treated as expected, tolerable overload (§5).

### 2.4 READINESS (day-of state)

Readiness is a daily fusion that gates today's session. It combines objective autonomic signals with first-class subjective input (`research/03`).

- **Morning ln rMSSD trend** vs the athlete's rolling baseline: 7-day rolling mean of ln rMSSD compared against a **smallest-worthwhile-change (SWC) band** centred on the baseline mean with half-width `max(0.5 · SD(ln rMSSD), 0.01)` — SD the sample standard deviation of the athlete's own ln rMSSD baseline, the half-width floored at 0.01 on the ln scale (`research/00` HRV-07) — per HRV-guided-training practice (Vesterinen, Plews; `research/04`. This note first stated the width as "± 0.5 × the athlete's own coefficient of variation"; `research/00` §5.4 clarifies, 2026-09-09, that the register's "CV" conflated the SWC with Plews's separate rolling-mean-CV stability metric). Within band = normal; below band = suppressed.
- **Resting HR** deviation from baseline (elevated = stress/illness/incomplete recovery).
- **Sleep** (duration/quality if available) and the **5-item wellness** self-report (fatigue, sleep, soreness, stress, mood), each interpreted as a **z-score against the athlete's own rolling baseline**, not an absolute cutoff (`research/03`).
- **Prior-session residual fatigue** from the session-type recovery clock (§2.3).

**Fusion → traffic light.** Compute a readiness score as a weighted, baseline-normalized composite, then map to **green / amber / red**. Default logic (heuristic): **red** if HRV is below the SWC band **and** ≥ 2 wellness items are ≥ 1.5 z below baseline, or if any single hard flag fires (see injury-risk); **amber** if either the HRV or the wellness axis is degraded but not both; **green** otherwise. The **subjective axis can veto to amber/red on its own** because self-report is often more responsive than objective measures (Saw et al.; `research/03`) — but a single good night should not instantly clear accumulated multi-day suppression, so the gate uses trends, not one reading. Weighting of subjective vs objective is an open question (§7); default is to let the **more conservative** of the two axes dominate.

### 2.5 INJURY-RISK

A conservative composite whose purpose is to protect the success metric (a sidelined athlete averages zero pace), not to compete with it. It fuses load-dynamics context with subjective tissue signals (`research/03`).

- **Load-spike / ACWR context.** ACWR = acute(7 d) ÷ chronic(28 d) load, EWMA form with λ = 2/(N+1) (`research/03`). **Used only as a soft context flag / spike detector, never as a hard gate**, because ACWR is heavily critiqued for mathematical coupling and spurious correlation (Lolli) and conceptual pitfalls (Impellizzeri) (`research/03`). A ratio outside a wide band (default < 0.8 or > 1.3–1.5) raises context, nothing more.
- **Monotony & strain.** Weekly monotony = mean/SD of daily load; **strain = weekly load × monotony**; flag monotony > ~2.0 (Foster; `research/03`). High monotony is an independent, better-supported risk signal than ACWR and directly informs the injection of easy/rest variation.
- **Subjective pain / soreness trajectory.** The pain traffic-light (Silbernagel): pain ≤ 5/10 acceptable **if** it settles overnight and is **not worsening week-over-week**; rising soreness z-scores across days escalate risk (`research/03`).
- **Hard flags (non-negotiable).** Bone/BSI-pattern pain, night pain, focal bony tenderness, systemic illness, or RED-S indicators → immediate escalation, overriding everything (`research/03`).

The injury-risk output is an ordinal level (low / elevated / high / stop-and-escalate) that feeds the safety override in §5.6.

---

## 3. The physiological state model

The state model is the athlete's estimated current profile of the **determinants of race pace** plus a trend and confidence for each. It is the system's owned alternative to trusting a vendor VO2max number.

### 3.1 Estimated determinants

- **Critical Speed (CS) and D′.** Fitted from the 2-parameter model `distance = CS·t + D′` using the athlete's best sustained efforts across durations (~2–20 min) drawn from races, time trials, and hard workout segments (`research/01`). CS is the single most useful anchor for threshold-to-10K pace; D′ captures anaerobic reserve. Re-fit whenever a new qualifying maximal effort arrives.
- **vVO2max / velocity at VO2max.** Estimated from short maximal efforts (~3–8 min equivalents) and cross-checked against CS; anchors VO2max-interval prescription (`research/01`).
- **Threshold pace (functional).** The pace at the lactate-threshold (LT2) anchor, estimated from ~20–60 min sustained efforts and from the CS fit; this is the `FTP_pace` used in rTSS and the reference for zone paces (`research/01`, `research/04`).
- **Running economy / efficiency proxy.** No lab VO2, so use **EF at a controlled reference effort** as a within-athlete economy proxy and track its trend (`research/01`). Absolute economy is not claimed; only relative change.
- **Durability.** Quantified as the magnitude of EF/decoupling drift over the back third of long runs at equal pace — smaller drift = better fatigue resistance (`research/01`). Emerging science; flagged as such.
- **Individual endurance exponent (fatigue exponent b).** Fitted to the athlete's own duration–performance data via the Riegel power law `T2 = T1·(D2/D1)^b`, b ≈ 1.06 population average but **individual and trainable** (Riegel; `research/01`). Equivalently expressed as the athlete's **fractional-utilization curve** — the sustainable %vVO2max/%CS as a function of race duration (~95–100% at 5K down to ~75–85% at marathon; `research/01`). A lower personal b means better endurance and flattens the projected pace decline with distance.

### 3.2 Establishing the STARTING state from history

At program start the system has only historical raw data (project inputs). Procedure:

1. Ingest and gate all available history; build the GAP and load time series.
2. Mine **maximal-effort segments** (races, time trials, hard intervals) across durations and fit CS/D′ and the Riegel exponent b; if fewer than the minimum qualifying efforts exist (default: efforts spanning at least two well-separated durations), fall back to population defaults (b = 1.06, fractional-utilization defaults) and mark those estimates **low-confidence**.
3. Estimate threshold pace from the best sustained tempo/threshold efforts and from the CS fit.
4. Seed CTL/ATL by running the EWMA over the historical load series (so the athlete does not start at an artificial zero fitness); if history is shorter than ~6 weeks, seed CTL from average weekly load and flag it provisional.
5. Establish HRV, resting-HR, and wellness **baselines** from whatever history exists; if none, the first 1–2 weeks of the plan are a **baseline-collection period** during which the readiness gate runs in observe-only mode.

*(Cold-start amendment, per `research/00` §3.2.) When running history is too thin to fit a critical-speed curve, this population-defaults-only start is superseded by an owned non-exercise fitness seed: `research/00` §3.2 now names the specific open, cited estimators the seed is built from — the Uth–Sørensen Heart Rate Ratio Method (VO₂max ≈ 15.3 × HRmax/HRrest) and a Jackson-form non-exercise demographic regression — blended by confidence and handed off to the CS estimate as history accumulates. Read step 2 above through that amendment.)*

### 3.3 Trend and confidence

Each determinant carries (a) a current point estimate, (b) a short-term and long-term trend, and (c) a **confidence** that reflects data recency, quantity, and quality. Confidence rises with more, recent, high-quality (chest-strap, 1 Hz, clean GAP) qualifying efforts and decays with staleness. A defensible default is to model each determinant as a slowly-varying quantity updated by an exponential/Kalman-style filter: new qualifying observations pull the estimate proportional to their quality and inversely to the estimate's staleness. Low-confidence estimates widen the guardrails in §5 and make the adaptation logic more conservative. Confidence is surfaced to the athlete for transparency.

---

## 4. State → race-pace translation

Planning is driven by a **gap-to-goal**: the difference between the goal pace the athlete wants and the pace the current state predicts for the specific race. Computing the projection:

1. **Baseline distance-specific pace.** From CS/vVO2max and the **individual** endurance exponent b (or the fractional-utilization curve), predict the sustainable average pace for the goal distance. The Riegel law extrapolates a known performance to the goal distance; CS bounds the sustainable end (`research/01`). Use the individual b when confidence is adequate, else the population default with a widened prediction interval.
2. **Course modifiers.** Apply the **Minetti** cost-of-gradient integration over the goal course's elevation profile to convert the flat-equivalent projection into a course-specific projection — critically, uphill cost is **not** fully recovered on the corresponding downhill, so a rolling course is slower than its net elevation change suggests (`research/01`).
3. **Environmental modifiers.**
   - **Heat/humidity** via WBGT, with a slowdown that is larger for slower runners and longer races (`research/01`).
   - **Altitude:** ~6–7% VO2max loss per 1000 m, propagated through to pace (`research/01`).
   - **Wind:** Pugh's model — energetic cost ~8% into a headwind, quadratic in relative velocity, only partially offset by tailwind (`research/01`).
4. **Projected race pace + interval.** Combine into a projected average race pace with an uncertainty interval derived from the determinant confidences. The **gap-to-goal** = goal pace − projected pace, decomposed by determinant (how much of the gap is threshold vs VO2max vs durability vs economy) so that periodization can target the **largest addressable gap** (§5.1).

This projection is recomputed whenever the state model updates, so the plan is always aimed at the current best estimate of race day under the expected conditions.

---

## 5. The adaptation decision logic across five timescales

This is the centerpiece: continuous adaptation across nested timescales, composed into **one decision each day**. Each timescale has defined inputs, triggers, actions, and guardrails. Slower timescales set the frame; faster ones adjust within it; a safety override sits above all of them.

### 5.1 Long-term trend (mesocycle / multi-week)

- **Inputs:** the state model and its trends, the race-pace projection and decomposed gap-to-goal (§4), phase in the base→build→peak→taper structure, weeks-to-race (`research/04`).
- **Triggers:** scheduled mesocycle boundaries; or a material shift in which determinant dominates the gap-to-goal; or a stalled/negative long-term determinant trend.
- **Actions:** (re-)periodize — shift mesocycle **emphasis and training-intensity-distribution** toward the determinant with the biggest addressable gap (e.g. more threshold/CS work if threshold limits; more long-run durability work for a marathon durability gap; VO2max blocks if vVO2max is the ceiling), always respecting phase logic and the ~80%-easy-by-time polarized/pyramidal frame (`research/04`). Adjust the overall CTL ramp target for the mesocycle.
- **Guardrails:** never abandon aerobic base proportion; keep the mesocycle CTL ramp within the safe range; changes here are proposals that the faster loops still modulate day to day.

### 5.2 Short-term trend (weekly microcycle)

- **Inputs:** last 1–4 weeks of load (CTL/ATL/TSB), monotony/strain, injury-risk context, adherence, aggregate readiness (`research/03`, `research/04`).
- **Triggers:** weekly planning; CTL ramp-rate outside guardrail; rising monotony/strain; a run of amber/red days.
- **Actions:** set next week's **volume and TID** — scale weekly load up or down, place hard/easy days, inject variation to break monotony, schedule the recovery/down week. Enforce the **ramp-rate guardrail** (default: weekly CTL rise held to a modest cap; keep weekly-load growth within a conservative band) rather than any hard ACWR gate (`research/04`; ACWR only as context per Lolli/Impellizzeri).
- **Guardrails:** cap weekly volume increase (classic ~10%/week as a soft default, flagged heuristic); mandatory recovery week cadence (default every 3–4 weeks); monotony kept below ~2.0.

### 5.3 Recent-workout feedback (last 1–3 sessions)

- **Inputs:** per-session response features — completion-vs-prescription, decoupling, EF, HR-at-pace — and any new maximal effort (§2.2, §3).
- **Triggers:** a workout completed well above/below prescription; decoupling or EF materially better/worse than expected; a qualifying max effort that updates a determinant.
- **Actions:** **re-anchor paces** (update threshold/CS/VDOT → all relative zone paces auto-update per `research/04`); tweak the **next 1–3 sessions** — make the next key session slightly harder if the athlete is over-delivering with low decoupling, or easier/rescheduled if they under-delivered or decoupled early. Feed any determinant change into the state model and re-project race pace.
- **Guardrails:** require corroboration before large re-anchoring (a single hot session does not raise threshold; §2.2); pace changes bounded to small steps unless a genuine max effort justifies more.

### 5.4 Day-of readiness (the daily gate)

- **Inputs:** the readiness fusion (§2.4) — morning ln rMSSD vs SWC, resting HR, sleep, wellness z-scores, session-type residual fatigue.
- **Triggers:** every day, before the prescribed session.
- **Actions — green/amber/red gate (HRV-guided rule, Vesterinen/Plews; `research/04`):**
  - **Green** → proceed with the prescribed session as written.
  - **Amber** → reduce the session: cut volume, drop the hardest interval set, or lower intensity one notch (e.g. threshold → steady); keep the session's physiological intent where possible.
  - **Red** → swap to easy/recovery or rest; **do not** run the prescribed hard session. Reschedule the displaced key session rather than dropping it, if the week allows.
- **Guardrails:** the gate can **down-regulate freely** but only **up-regulate cautiously** (a green day does not turn an easy day into a hard one on impulse — hard-session placement stays under the weekly plan's control). Sustained red days escalate to the short-term loop (deload) and to injury-risk review.

### 5.5 Intra-workout real-time (stretch goal, on-device only)

- **Feasibility:** real-time in-session adaptation is possible on Garmin **only via on-device Connect IQ** (or ANT+/BLE broadcast to a companion); the official Activity/Health APIs are post-session only (`research/02`). So this is an **optional on-device module**, and the between-session loops (5.1–5.4) are the primary system.
- **Inputs (on-device):** live pace/GAP-at-effort and HR during interval sets.
- **Actions — performance-based interval cutoffs / autoregulation (`research/04`):** end an interval set when pace-at-target-effort or HR drifts beyond a set threshold (e.g. pace at prescribed HR slows past a cutoff, or HR at prescribed pace rises past a cutoff), preventing junk reps once the session's quality has decayed. Optionally hold target pace via live feedback.
- **Guardrails:** conservative cutoffs; the module can **stop** work but not add unplanned hard work; if the on-device module is absent, its intent is approximated post-hoc by the recent-workout loop (5.3).

### 5.6 Composing the timescales + arbitration + safety override

Each day the system composes a single prescription:

1. The **long-term** loop fixes the mesocycle intent and TID frame.
2. The **short-term** loop has already placed this day's role (hard/easy/long/rest) in the week and set volumes within ramp guardrails.
3. The **recent-workout** loop has re-anchored paces and tweaked the next sessions, so today's prescribed paces are current.
4. The **day-of readiness** gate scales or swaps today's session (green/amber/red).
5. If running, the **intra-workout** module (where present) trims sets in real time.

**Arbitration when signals conflict** (priority, highest first):

1. **Safety / injury override.** If injury-risk is *high* or a hard flag fires (BSI-pattern/bone pain, night pain, systemic illness, RED-S), **stop or escalate regardless of the pace objective** — this outranks everything, because an injured athlete averages zero pace (`research/03`). *Stop-and-escalate* halts the plan and surfaces a return-to-run / medical pathway.
2. **Day-of readiness (conservative direction).** A red readiness gate downgrades the session even if the plan wants a hard day; when subjective and objective readiness disagree, the **more conservative** reading wins (`research/03`).
3. **Short-term guardrails.** Ramp-rate, monotony, and mandatory recovery weeks cap what the long-term ambition can demand.
4. **Recent-workout re-anchoring.** Adjusts paces/next sessions within the above.
5. **Long-term ambition (the pace objective).** Drives emphasis but is the **lowest** priority when it conflicts with safety or readiness.

The governing rule: **faster, more conservative, safety-relevant signals can always veto slower, more ambitious ones, but not vice versa.** Every applied decision is logged with its inputs and the rule that fired, so the autonomous system stays transparent and can explain each change to the athlete (project thesis).

---

## 6. Closing the loop & candidate-method evaluation

**The cycle.** Each day and each session the system runs: **raw ingest → quality-gate → per-session features → derived metrics (load/response/fatigue/readiness/injury-risk) → updated state model → re-projected race pace and gap-to-goal → plan adaptation across the five timescales → prescribed session → observe outcome → back to ingest.** The slow loops re-periodize; the fast loops protect and fine-tune; the state model is the shared memory that makes response cumulative rather than reactive.

**ADOPT / EXTEND / REPLACE.**

- **PMC (CTL/ATL/TSB) — ADOPT, then EXTEND.** Adopt the documented 42/7 EWMA construction as the operational fitness/fatigue/form chart (`research/04`), but **extend** it with session-type-specific recovery time-courses (§2.3) and gate its interpretation through readiness and subjective corroboration. *Uncertainty:* the 42/7 constants are conventional, not fitted (§7). *Default:* ship 42/7; per-athlete fitting is a later enhancement (`research/00` REG-20).
- **HRV-guided training rule — ADOPT.** Adopt the 7-day rolling ln rMSSD vs SWC-band gate for hard/easy decisions (Vesterinen, Plews; `research/04`), sourced only from resting/overnight HRV through `research/00` HRV-01's three input tiers — chest-strap RR preferred, then a device's numeric resting rMSSD at reduced fidelity — and never from in-run wrist PPG (`research/02`). *Uncertainty:* SWC band width is athlete-specific. *Default:* half-width `max(0.5 · SD(ln rMSSD), 0.01)` around the baseline mean — SD the sample SD of the athlete's own ln rMSSD baseline, the half-width floored at 0.01 on the ln scale (`research/00` HRV-07; stated here originally as "± 0.5·CV"; clarified 2026-09-09, `research/00` §5.4) — so the width follows the athlete's own baseline.
- **ACWR — ADOPT as CONTEXT ONLY, do not gate.** Keep it as a spike/context flag inside injury-risk, never as a hard decision gate, given the coupling/spurious-correlation critique (Lolli) and conceptual pitfalls (Impellizzeri) (`research/03`). *Default:* wide band, advisory only; prefer monotony/strain and subjective trajectory as the real risk signals.
- **Load metric — ADOPT rTSS as primary, EXTEND with a reconciliation across TRIMP/sRPE.** No single internal-load metric is complete; the mean-fallback-on-divergence rule (§2.1) is the pragmatic extension. *Default:* rTSS when GAP valid, else HR-TRIMP, else sRPE-load.
- **Vendor VO2max / Training Readiness / Body Battery — REPLACE.** Own the state model (CS/vVO2max/threshold/EF/durability/b) rather than trust undocumented, unvalidated vendor estimates; use vendor numbers only as quarantined corroboration (project instructions; `research/02`). *Uncertainty:* our estimates need enough quality efforts; when data is thin we fall back to population defaults with wide intervals, not to the black box.
- **Riegel + CS + Minetti + WBGT/altitude/wind projection — ADOPT, EXTEND to individual parameters.** Adopt the established models but fit the **individual** endurance exponent and fractional-utilization curve where data allows (`research/01`). *Default:* individual b when confident, else 1.06.
- **Autoregulation / performance-based interval cutoffs — ADOPT where feasible.** On-device only (Connect IQ), else approximate post-hoc (`research/02`, `research/04`).

## 7. Open questions & defensible defaults

Each unsettled choice ships with a default and the assumption behind it; each is tunable per athlete only under the individualization rule (`research/00` DOC-06, IND-01) and never where it is a design invariant.

- **Fitness/fatigue time constants.** *Unsettled:* the true individual CTL/ATL (and Banister) decay constants are not identifiable from typical field data. *Default:* 42-day CTL, 7-day ATL. *Assumption:* the documented conventional values are interpretable and good enough as a starting frame; per-athlete fitting is a later enhancement.
- **Weighting subjective vs objective in readiness.** *Unsettled:* optimal fusion weights are unknown and individual. *Default:* let the **more conservative axis dominate**; require multi-day/multi-item subjective decline (or HRV below SWC) before red. *Assumption:* self-report is often more responsive (Saw et al.; `research/03`), and false-negative fatigue (overtraining) is costlier than a missed hard session.
- **Durability quantification.** *Unsettled:* no standard field metric; the science is emerging (`research/01`). *Default:* EF/decoupling drift over the back third of long runs, interpreted only as a within-athlete trend. *Assumption:* relative drift tracks fatigue resistance even if the absolute scale is uncalibrated.
- **Load-metric reconciliation threshold.** *Default:* 25% divergence triggers mean-fallback. *Assumption:* modest divergence is sensor noise; large divergence means no single metric is trustworthy, so average.
- **ACWR band and role.** *Default:* advisory context only, wide band (≈0.8–1.5). *Assumption:* ACWR's statistical problems make any hard threshold indefensible; monotony/strain and subjective pain carry the real risk weight.
- **Intra-workout feasibility.** *Unsettled/limited:* real-time adaptation needs on-device Connect IQ and cannot use the official post-session APIs (`research/02`). *Default:* ship the four between-session loops as the core system; treat intra-workout cutoffs as an optional on-device stretch module, approximated post-hoc when absent. *Assumption:* the between-session loop captures most of the achievable benefit; real-time is additive, not foundational.
- **How much autonomy before athlete confirmation.** *Default (superseded):* this section originally proposed surfacing safety escalations and major re-periodizations for athlete awareness/confirmation. `research/00` §1.8–§1.9 resolves the question the other way: the system is **fully autonomous, apply-and-notify** over the plan, reserving athlete decision only for the goal contract and, on the safety pathway, the athlete's own clinical action and return-to-run clearance, neither of which the system owns; the system applies the safety override and issues the pathway itself (`research/00` AUT-02, AUT-08). Read this bullet through that resolution.

---

## References

- Banister, E.W. — TRIMP and the fitness–fatigue impulse–response model. (`research/01`, `research/04`)
- Foster, C. — Session-RPE training load; monotony and strain. (`research/03`, `research/04`)
- Coggan, A. / TrainingPeaks — Performance Management Chart; CTL (42-day), ATL (7-day), TSB; TSS and rTSS/NGP. (`research/04`)
- Vesterinen, V., et al. — HRV-guided endurance training. (`research/04`)
- Plews, D.J., et al. — HRV monitoring; rolling ln rMSSD and SWC interpretation; artefact correction. (`research/03`, `research/04`)
- Lolli, L., et al. — Mathematical-coupling / spurious-correlation critique of ACWR. (`research/03`)
- Impellizzeri, F.M., et al. — Conceptual and methodological critique of ACWR. (`research/03`)
- Riegel, P.S. — Endurance-performance power law and fatigue exponent. (`research/01`)
- Minetti, A.E., et al. — Energy cost of running on gradients. (`research/01`)
- Pugh, L.G.C.E. — Energetic cost of wind resistance in running. (`research/01`)
- Silbernagel, K.G., et al. — Pain-monitoring (traffic-light) model. (`research/03`)
- Saw, A.E., Main, L.C., Gastin, P.B. — Self-report vs objective monitoring. (`research/03`)
- Jones, A.M. / Vanhatalo, A. — Critical-speed 2-parameter model and D′. (`research/01`)
- Foundational project research: `research/01-exercise-physiology.md`, `research/02-wearable-data-garmin.md`, `research/03-subjective-injury-recovery.md`, `research/04-coaching-periodization.md`.
