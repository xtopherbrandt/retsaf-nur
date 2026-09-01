# State-of-the-Art Running Coaching & Periodization

**Abstract.** This document surveys the established science and current practice of endurance-running coaching, with emphasis on the parts an autonomous, continuously-adapting coaching system must encode: periodization structure, intensity-distribution models, a machine-representable workout typology, quantitative training-load and impulse-response models (the Performance Management Chart), and the adaptive/autoregulatory methods that let a plan respond to incoming data. Throughout it distinguishes *well-established* science (broad consensus, replicated) from *contested or emerging* methods (mixed evidence, small samples) and from *marketing claims* (asserted by vendors, not independently validated). The single organizing question is always the project's success metric: does a given structure or adaptation raise the athlete's expected average pace over the goal race distance? The core finding relevant to the build is that the physiological logic of periodization, the load/response models, and the taper are on firm ground; adaptive HRV-guided training is promising with moderate supporting evidence; and no commercial system yet closes the full raw-data → state-estimate → plan-change loop autonomously with transparent methods — which is precisely the gap this project targets.

---

## 1. Established periodization structures

**Periodization** is the planned sequencing of training so that a specific adaptation peaks on a target date. The vocabulary is standardized [1][2]: a **macrocycle** is the full run-up to the goal race (typically 12–24 weeks for the amateur target athlete); **mesocycles** are multi-week blocks (commonly 3–5 weeks) each with a dominant physiological aim; **microcycles** are the recurring short unit, almost universally one week, in which sessions of differing purpose and intensity are arranged so that hard stimuli are separated by adequate recovery.

The dominant macro-structure is the **base → build → peak → taper** progression, whose physiological logic is *sequencing by trainability and specificity* [1][3]:

- **Base (general preparation).** Build aerobic infrastructure — mitochondrial density, capillarization, stroke volume/plasma volume, fat oxidation, and the musculoskeletal durability to absorb later load — chiefly through a high volume of low-intensity running plus supporting strides and hills. These central and peripheral adaptations are slow to develop but durable, which is why they come first and are trained longest [3][4].
- **Build (specific preparation).** Add threshold and VO2max stimuli that raise the metabolic ceiling (lactate/anaerobic threshold, VO2max) on top of the aerobic base. These adaptations respond faster and are more fatiguing, so they are layered on once the base can support them [1][3].
- **Peak / race-specific sharpening.** Shift the highest-quality work toward *goal race pace and effort*, integrating the developed capacities into the specific demand — the "specificity" endpoint of the sequence. Canova's marathon methodology is the clearest illustration of this principle: progressively transforming generic speed and generic endurance into work performed at, just above, and just below goal marathon pace as the race nears [5].
- **Taper.** Shed accumulated fatigue while preserving fitness, timed so form peaks on race day (Section 7).

This ordering reflects the **general-to-specific** and **trainability** principles: earliest emphasis on the adaptations that are slowest, most foundational, and least race-specific; latest emphasis on the adaptations that are fast, fatiguing, and most race-specific [1][2][3].

**Linear vs. block vs. undulating periodization.** *Traditional/linear* periodization (Matveyev) moves gradually from high-volume/low-intensity to low-volume/high-intensity across the macrocycle [2]. *Block* periodization (Issurin) concentrates a small number of compatible training targets into successive focused blocks, arguing that a highly-trained athlete cannot develop many qualities simultaneously and benefits from concentrated, sequential loading with residual carry-over between blocks [6]. *Undulating* (non-linear) periodization varies intensity and volume within the week or mesocycle rather than across months, and in practice nearly all modern distance-running weeks are undulating at the microcycle level — hard and easy days interleaved — even when the macrocycle is broadly linear [2]. The evidence does not crown one scheme universally superior for endurance; the defensible reading is that *some* systematic variation and progressive overload matters more than the specific label, and that block concentration becomes more useful as training age rises [1][6].

**Named systems as illustrations (cited, not endorsed).** Several traditional systems usefully illustrate the principles:

- **Lydiard** — the archetype of the large aerobic base built first, then a hill/strength phase, then anaerobic sharpening, then coordination/peaking; the canonical "aerobic base is the foundation of everything" argument [4].
- **Daniels' VDOT system** — a framework for anchoring workout intensities to a single fitness index (VDOT) derived from a recent race, with named training paces (Easy, Marathon, Threshold, Interval, Repetition); valuable because it gives every workout a *quantitative pace anchor* an algorithm can compute [7].
- **Pfitzinger** — structured marathon/half plans that formalize the *medium-long run*, *marathon-pace long run*, and lactate-threshold tempo progression, a good source of concrete workout templates for the target athlete [8].
- **Canova** — race-pace-centric marathon periodization, the exemplar of specificity and of expressing workout intensity as a percentage of goal race pace [5].

These are illustrations of the general-to-specific logic, not validated protocols; where their prescriptions conflict, the underlying physiology and the intensity-distribution evidence (Section 2) should adjudicate.

**How periodization differs by distance and training age.** The shorter and faster the race, the larger the relative role of VO2max, anaerobic capacity, and neuromuscular speed, so 5K/10K preparation carries more VO2max intervals, faster race-pace work, and speed development, with a shorter taper. The longer the race, the more it is limited by fractional utilization, glycogen economy, and *durability* (fatigue resistance late in the race), so half-marathon and especially marathon preparation emphasize threshold volume, long runs, marathon-pace-specific work, and fueling practice [3][8][9]. **Training age** matters strongly: novices adapt to almost any progressive stimulus and need less specialized structure, whereas experienced athletes have smaller adaptive windows, need higher and more specific stimuli to progress, and benefit more from concentrated block loading and careful fatigue management [1][6]. The system's periodization defaults should therefore be parameterized by both goal distance and an estimate of the athlete's training age/fitness.

---

## 2. Intensity distribution models and the evidence

**Training-intensity distribution (TID)** describes what fraction of training is done in each intensity band. The standard analytic frame is a **three-zone model** anchored to the two ventilatory/lactate thresholds [10][11]:

- **Zone 1 (low):** below the first lactate/ventilatory threshold (roughly below ~2 mmol·L⁻¹ blood lactate; conversational, "easy").
- **Zone 2 (moderate):** between the two thresholds — the "threshold"/tempo grey zone.
- **Zone 3 (high):** above the second threshold / critical intensity (hard intervals).

Three named distributions dominate the literature:

| Model | Approximate zone proportions (Z1 / Z2 / Z3) | Character |
|---|---|---|
| **Polarized** | ~80 / ~5 / ~15 | Most training easy, a meaningful minority genuinely hard, little in the middle [10][11] |
| **Pyramidal** | ~80 / ~15 / ~5 | Mostly easy, decreasing volume with rising intensity, most quality at threshold, little truly high-intensity [12][13] |
| **Threshold** | ~50 / ~35 / ~15 (higher Z2) | Substantial deliberate time in the tempo/threshold band [10][14] |

Two definitions of "**80/20**" circulate and must not be conflated. The popular formulation (Seiler; 80/20 Endurance) is by **session count or time: ~80% of sessions/time easy, ~20% hard** [10][15]. This is *compatible with both polarized and pyramidal* distributions, because both put ~80% of volume in Zone 1 — they differ in how the remaining ~20% is split between the threshold (Z2) and high-intensity (Z3) bands [12][13]. Careful specification (as this system requires) should track the full three-zone split, not just an easy/hard binary.

**The evidence.** Descriptive studies of elite endurance athletes (Seiler & Kjerland) consistently find a large majority of training at low intensity, with the hard portion skewed toward genuinely high intensity — i.e., broadly polarized or pyramidal, rarely threshold-heavy [10][16]. Esteve-Lanao and colleagues showed in sub-elite runners that greater time at low intensity was associated with better performance, and that a more polarized allocation of the hard work improved outcomes over a more threshold-clustered one [14][17]. Stöggl & Sperlich's frequently-cited controlled study reported that a polarized block produced greater gains in VO2max, peak velocity, and related variables than threshold, high-intensity, or high-volume blocks over 9 weeks [11]. Systematic reviews and meta-analyses broadly support that (a) a high proportion of low-intensity work is a robust feature of successful endurance training, and (b) *some* high-intensity work is necessary to maximize VO2max and performance — while cautioning that direct head-to-head evidence favoring *polarized specifically over pyramidal* is limited, heterogeneous, and often short-duration [12][18].

**When each is appropriate.** The pragmatic, evidence-consistent synthesis is:
- **Base phase and higher-volume athletes / longer events** tend toward **pyramidal** — abundant easy running plus threshold support, minimal Z3 [12][13].
- **Build/peak phases, and shorter events (5K/10K)** shift toward **polarized** — the same easy base but with the quality work sharpened into Z3 VO2max/race-pace intervals [11][14].
- **Pure threshold-heavy distributions** are generally the least supported for maximizing adaptation and carry higher chronic-fatigue risk from too much time in the fatiguing middle zone ("grey-zone" or "black-hole" training) [10][15].

**Established vs. contested.** *Established:* endurance athletes should spend the large majority (~75–85%) of training at low intensity; polarized/pyramidal both beat threshold-dominant patterns; a non-trivial dose of high intensity is needed to peak [10][11][12][14]. *Contested:* whether polarized is genuinely superior to pyramidal, and whether TID should shift across the season, remain under active debate with mixed RCTs and reviews [12][18]. **Defensible default for the system:** anchor to a periodized TID — pyramidal-leaning in base, polarizing through build/peak — hold Zone-1 volume near ~80% throughout, and treat the Z2/Z3 split as a phase- and distance-dependent parameter rather than a fixed constant, stating the assumption explicitly.

---

## 3. Workout typology

Below is a structured catalog of the workout archetypes an implementer must represent. For each: physiological target, the parameters needed to prescribe it, and its periodization home. Intensities are expressed relative to portable anchors the system can compute per-athlete — most robustly **threshold pace/velocity**, **goal race pace (GRP)**, **critical speed (CS)**, or a **VDOT-style zone** [7][19] — with heart-rate and RPE as secondary anchors that decouple from pace on hills/heat.

| Workout type | Physiological target | Key prescription parameters | Periodization home |
|---|---|---|---|
| **Recovery run** | Promote blood flow/recovery, add easy aerobic volume with minimal stress | Duration (20–40 min); intensity well below Z1 top (very easy, RPE 2–3) | All phases, day after hard sessions |
| **Easy / general aerobic** | Aerobic base: mitochondria, capillaries, fat oxidation, durability | Duration (40–90 min); Z1 pace/HR; optional strides appended | All phases; the bulk of volume |
| **Long run** | Peripheral endurance, glycogen economy, fatigue resistance/durability | Duration or distance (often 90–150 min, or ~20–35% weekly volume); Z1 base pace | All phases; a weekly anchor |
| • Progression long run | Durability + late-run pacing under fatigue | Total duration; final segment stepped from easy → threshold/GRP | Build/peak |
| • Fast-finish long run | Running fast on tired legs; race simulation | Easy body + final 15–40 min at ~GRP or threshold | Build/peak |
| • Marathon-pace (MP) long run | Marathon-specific efficiency, fueling, pacing | Total duration; embedded blocks or continuous segment at MP/GRP | Marathon peak phase [8] |
| **Tempo / threshold — continuous** | Raise lactate/anaerobic threshold; clearance | 20–40 min continuous at threshold pace (Z2 top / ~1-hr race effort) | Base→build support [7][19] |
| **Cruise intervals (broken threshold)** | Same as tempo, more total threshold volume at controlled effort | Reps × duration (e.g., 3–6 × 5–10 min) at threshold; short (~1 min) jog recovery | Base→build [7] |
| **VO2max intervals** | Maximize VO2max / oxygen delivery; velocity at VO2max | Reps × 2–5 min (or ~800–1200 m) at ~95–100% vVO2max / ~3–5K pace; recovery ≈ 50–100% of rep duration | Build/peak [7][19] |
| **Anaerobic / speed reps** | Anaerobic capacity, buffering, neuromuscular power | Short reps (200–400 m or 30–90 s) faster than VO2max pace; full/long recovery | Peak; more for 5K/10K [7] |
| **Hill repeats** | Strength/power, running economy, VO2 stimulus with lower impact | Reps × 30 s–4 min uphill at hard effort; jog-down recovery; grade specified | Base (strength) → build (power) [4] |
| **Strides** | Neuromuscular sharpening, economy, form; near-zero metabolic cost | 4–8 × 15–25 s at ~mile–3K effort (relaxed); full recovery | All phases, appended to easy runs |
| **Race-pace work** | Specificity: efficiency and pacing at exact goal pace | Volume at GRP as continuous or long intervals; ratio to GRP per rep | Peak/sharpening [5][8] |

**Design notes for a machine representation.** Every workout reduces to a small set of fields: an ordered list of **segments**, each with (a) a **target-intensity spec** — an anchor type (threshold/GRP/CS/VDOT-zone/HR/RPE/power) plus a value or range; (b) a **duration spec** — time, distance, or open-ended; (c) for interval sets, a **repeat count**, **recovery spec** (its own intensity + duration/distance), and optional **set structure** (sets of reps); and (d) metadata tagging the **primary physiological target** and **periodization phase**. This structure spans the entire catalog above and lets the adaptation logic reason over workouts uniformly (e.g., scale VO2max-interval volume, or swap a continuous tempo for cruise intervals, without special-casing). Pace anchors should be stored as *relative* to the athlete's current estimated threshold/VDOT so that when the state estimate updates, prescribed paces update automatically [7][19].

---

## 4. Load quantification and the Performance Management Chart

To adapt, the system must quantify how much stress each session imposed and how the athlete is responding over time. Two layers are needed: a **per-session load metric**, and a **longitudinal impulse-response model**.

### 4.1 Per-session load metrics

- **Banister TRIMP** (Training Impulse): load = duration × HR-based intensity, where intensity is a fraction of heart-rate reserve weighted by an exponential factor that up-weights higher intensities (using a sex-specific lactate-profile constant) [20]. Established and physiologically motivated, but the fixed exponential weighting is generic across individuals.
- **Edwards' (zone) TRIMP:** time spent in each of five HR zones multiplied by a zone weight (1–5) and summed [21]. Simple, transparent, robust to missing lactate data; coarse because it bins HR.
- **Lucía's TRIMP:** time in three intensity zones defined by *individual ventilatory thresholds*, weighted 1/2/3 [22]. More individualized when thresholds are known; requires threshold determination.
- **Session-RPE (Foster):** load = session RPE (0–10 category-ratio) × duration in minutes [23]. Remarkably valid against HR-based methods, captures non-HR stress (heat, hills, strength), and needs no device — an essential fallback and corroborating signal.
- **Pace/power-based load (TSS / rTSS / running power):** TrainingPeaks' **Training Stress Score (TSS)** normalizes intensity to threshold and scales by duration so that one hour at threshold = 100 [24]. Its running form, **rTSS**, uses a *Normalized Graded Pace* relative to threshold pace, and power variants (Stryd, GOVSS) use running power relative to threshold power [19][24]. Most portable across terrain when a good pace/power model and accurate threshold are available, but inherits any threshold-estimate error and, for pace, degrades on trails/hills unless grade-adjusted.

**Recommendation for the build.** No single metric is best; the transparent, vendor-independent path is to compute *several in parallel from raw data* — an HR-based TRIMP (Edwards or Banister), a pace/power-based rTSS-style score, and session-RPE — and reconcile them, using the pace/power score as the primary load when threshold and terrain data are reliable and session-RPE as the always-available corroborator. All formulas are fully specified in the sources cited and should be reproduced verbatim in the Phase-2 spec [20][21][22][23][24].

### 4.2 The impulse-response model and the Performance Management Chart

The **Banister fitness–fatigue model** treats performance as the difference between two exponentially-decaying traces driven by the same training-load impulses: a slow-decaying **fitness** term and a fast-decaying, larger-gain **fatigue** term [25]. Its practical, parameter-light instantiation is the **Performance Management Chart (PMC)** popularized by Coggan and TrainingPeaks [24][26]:

| Quantity | Meaning | Computation | Common constant |
|---|---|---|---|
| **CTL** (Chronic Training Load) | "Fitness" | Exponentially-weighted moving average of daily load | Time constant **42 days** |
| **ATL** (Acute Training Load) | "Fatigue" | Exponentially-weighted moving average of daily load | Time constant **7 days** |
| **TSB** (Training Stress Balance) | "Form"/freshness | **TSB = CTL − ATL** (typically yesterday's values) | — |

Rising CTL indicates accumulating fitness; ramping it too fast raises injury/overtraining risk, so coaches cap the weekly CTL ramp rate. TSB reads freshness: strongly **negative during hard loading** (fatigued), crossing toward **positive during taper** as ATL falls faster than CTL. Practitioner heuristics place race-day form in a **modestly positive TSB band (roughly +5 to +25)** for peak performance, athlete- and event-dependent [24][26]. This is exactly the read-out a taper controller needs (Section 7).

**Usefulness.** The PMC gives the system a compact, interpretable state summary from a single daily load number, a principled ramp-rate guardrail, and a quantitative taper target — all computable from raw data with fully-documented formulas [24][25][26].

**Known limitations and critiques (must be stated in the spec).**
1. **Garbage-in:** every PMC output is only as good as the daily load metric and the threshold it is normalized to; a stale threshold biases CTL/ATL systematically [24].
2. **Fixed, population-generic time constants.** The 42/7 defaults are conventions, not individually fitted; true fitness/fatigue decay constants vary between athletes, and the original Banister model's fitted parameters are notoriously unstable and overfit-prone on limited data [25][27].
3. **Intensity-blind aggregation.** Collapsing a session to one scalar discards *type* — 100 units of easy volume and 100 units of VO2max work are not interchangeable stimuli, though the PMC treats them alike.
4. **No mechanism, no plateau/nonlinearity.** The linear impulse-response form omits saturation, overreaching dynamics, and adaptation lag; it predicts trend, not causation.
5. **Load-only fatigue.** ATL captures training-derived fatigue but ignores sleep, life stress, and illness — which is exactly why the adaptive layer (Section 5) adds physiological readiness signals on top.

**Defensible default:** use the PMC with 42/7 constants as the backbone longitudinal state summary and ramp-rate guardrail, treat its "form" band as advisory, and explicitly layer readiness signals and per-session-type accounting on top rather than trusting a single scalar. Where data volume allows, fit athlete-specific decay constants but flag the overfitting risk [25][27].

---

## 5. Adaptive coaching methods — the heart of the system

Static plans assume a fixed dose-response; real athletes vary day to day and adapt at individual rates. Adaptive coaching closes the loop by adjusting the prescription to the athlete's *current measured state*. Four families of method matter.

### 5.1 HRV-guided training

**Heart-rate variability (HRV)** — beat-to-beat variation in R-R intervals — indexes cardiac parasympathetic (vagal) activity; the standard endurance marker is **rMSSD**, usually log-transformed (**ln rMSSD**) and measured in a standardized morning condition [28][29]. The guiding idea: elevated/stable vagal activity signals readiness to absorb hard training; suppressed HRV signals incomplete recovery, so hard sessions are scheduled only when HRV sits within the athlete's normal range.

**Method as used in the literature [28][30][31]:**
1. Measure morning HRV daily (short 1–5 min recording; ultra-short measures via phone/chest strap are validated for rMSSD).
2. Track a **~7-day rolling mean** of ln rMSSD as the baseline, which smooths day-to-day noise better than single readings.
3. Define a normal band using the **smallest worthwhile change (SWC)** — commonly the rolling mean ± a coefficient-of-variation-based or ±0.5×SD-style band.
4. **Decision rule:** if today's ln rMSSD (or the 7-day mean) is **within/above** the band → prescribe the planned **high-intensity** session; if it has **dropped below** the band → substitute **easy/low-intensity** or rest. Weekly HIT dose thus becomes an *output* of the athlete's state rather than a fixed input.

**Evidence.** Kiviniemi et al. first showed that timing hard training to daily HRV improved outcomes versus predefined training [28]. Vesterinen et al. (2016) ran a controlled trial in recreational runners in which HRV-guided training produced **greater or at least equal improvement in 3000 m performance than a predefined program**, with the guided group achieving it via a favorable reallocation of high-intensity sessions [30]. Javaloyes et al. demonstrated benefits of HRV-guided prescription over traditional/block periodization in trained cyclists, and later in runners [31][32]. A systematic review with meta-analysis concluded HRV-guided training tends to yield **similar or superior performance outcomes with potentially less high-intensity volume**, while noting heterogeneity in protocols and modest sample sizes [33].

**Status.** *Emerging-toward-established.* The direction of evidence is consistently favorable and the mechanism is sound, but trials are relatively small, protocols differ, and effects are moderate. **Defensible default:** implement HRV guidance on the *7-day rolling ln rMSSD vs. SWC* rule as a modifier that can down-shift (never blindly up-shift beyond plan) intensity, require a standardized measurement protocol, and treat a single low reading as weaker evidence than a multi-day baseline decline [29][30][33].

### 5.2 Broader readiness/recovery-guided adjustment

HRV is one input; robust readiness combines several **weakly-correlated signals** so no single noisy channel dominates [29][34]:
- **Resting/orthostatic HR** (elevated morning HR suggests incomplete recovery or illness),
- **Sleep** (duration/quality/consistency),
- **Subjective wellness** — brief validated questionnaires covering fatigue, soreness, mood, stress, sleep; subjective wellness repeatedly tracks training load sensitively and cheaply [34],
- **Prior-session performance** (see 5.4).

The system should fuse these into a **readiness estimate** that modulates the day's prescription (green/amber/red style, but continuous), recognizing that (a) subjective ratings are cheap and surprisingly informative, and (b) multi-signal agreement is more trustworthy than any one metric [29][34]. This is also the layer that catches non-training stressors the load-only PMC misses (Section 4.2).

### 5.3 Autoregulation imported from strength training

Strength science formalized **autoregulation** — adjusting the session in real time to daily capacity — with tools that have direct running analogues [35][36]:
- **RPE / RIR (reps-in-reserve) prescription:** target an effort rather than a fixed load; the running analogue is **pace-at-target-RPE** and effort-capped sessions (run the interval "at 5K effort" and let pace float with conditions/fatigue).
- **Velocity-based training (VBT):** in the weight room, terminate a set when bar velocity drops by a set percentage. The running analogue is a **performance-based interval cutoff**: prescribe reps until pace at a fixed effort/HR can no longer be held (e.g., stop the VO2max set once rep pace falls >X% off target, or HR-at-pace drifts beyond a threshold). This converts a fixed rep count into a *state-dependent* one and is a concrete mechanism for in-session autonomy.

These concepts are well-validated in resistance training [35][36]; their running translations are sensible and increasingly used but have **thinner direct running evidence**, so they should be implemented as bounded rules (with min/max volume guards) and flagged as principled extrapolation.

### 5.4 Feedback signals from within and after workouts

The richest adaptation inputs come from the workouts themselves, computed from raw pace/HR streams:
- **Pace–HR decoupling (aerobic decoupling / cardiac drift):** compare the pace:HR (or power:HR) ratio in the first vs. second half of a steady effort; a rise means HR climbed for the same pace. A commonly-used practitioner threshold treats **<5% decoupling** as good aerobic durability for the effort, and larger decoupling as a durability/fatigue or heat/dehydration signal [37]. This is a direct, raw-data readout of the *durability* determinant that governs late-race pace — highly relevant to the success metric.
- **HR drift** on fixed-pace runs — the same phenomenon, trended over weeks as an aerobic-fitness indicator.
- **Efficiency Factor (EF):** normalized pace (or power) ÷ average HR for a steady aerobic run; a rising EF over weeks at matched conditions indicates improving aerobic efficiency [37]. Useful as a longitudinal state-trend signal, with the caveat that heat, sleep, and surface confound it, so it must be compared under matched conditions.
- **Completion vs. prescription:** did the athlete hit prescribed paces/durations, and at what HR/RPE cost? Systematic *overshooting* effort to hit paces (HR higher than expected) or *undershooting* paces flags that the fitness estimate or the plan is mis-calibrated — a primary trigger to update the state model and re-anchor future paces.

These per-session features are the machine-computable feedback that lets the system revise its physiological state estimate and re-prescribe, and several (decoupling, EF, HR-at-pace) map cleanly onto the physiological determinants of race pace.

---

## 6. How far real systems push automation

This survey distinguishes **documented method** from **marketing claim**. The consistent finding: commercial platforms are strong on *load tracking and workout suggestion* but remain largely **advisory**, **derived-metric-dependent**, and **not continuously autonomous** in the way this project targets.

- **TrainingPeaks / WKO5.** The reference implementation of the PMC (TSS/CTL/ATL/TSB) and rTSS/power metrics; WKO adds modeled power-duration/critical-power analytics [24][26]. Method is transparent and well-documented, but the platform is an *analysis and planning tool for a human coach/athlete* — it computes state and flags it; it does not autonomously rewrite the plan.
- **Garmin (Daily Suggested Workouts, Training Status/Readiness), powered by Firstbeat.** Garmin devices generate daily suggested runs that adapt to recent training load, "load focus," recovery time, HRV status, and estimated VO2max [38][39]. This is the closest widely-deployed thing to autonomous day-to-day adjustment. But the adaptation is driven by **Firstbeat's proprietary, undocumented, vendor-derived metrics**, which the project explicitly relegates to secondary/corroborating status; the algorithms are black boxes, change without notice, and are not independently validated at the level of method [38][39]. Its planned-race "Garmin Coach" plans are more static, adapting mainly around missed/added sessions.
- **AI endurance platforms — Athletica, Humango, AI Endurance, TriDot, Runna.** These market **AI-driven adaptive plans** that reshuffle upcoming workouts based on completed sessions, performance, and readiness inputs [40][41]. Reality vs. claim: they genuinely *re-plan* around completion, missed sessions, and some readiness inputs, but published detail on the exact adaptation algorithms is limited, they depend heavily on vendor/derived inputs, and independent validation is scarce — the "AI" characterization is largely a marketing framing over rule-based/heuristic adaptation with some model fitting [41].
- **Stryd (running power).** Provides a raw-ish, portable intensity signal (running power) and a critical-power model, plus power-based workout targets; strong on *measurement and pacing*, not an autonomous coach [19].

**In-session / real-time adjustment.** Almost all adaptation in real systems is **between sessions**, not within them. The main real-time behaviors that exist are shallow: live pace/HR target guidance on the watch, auto-lap and workout-step advancement, and Firstbeat's on-the-fly *performance condition* readout. **Genuine in-session autoregulation** — e.g., extending or truncating an interval set based on live pace-at-HR decoupling — is essentially absent from mainstream products and is where Section 5.3's performance-based cutoffs would be novel.

**The gap this project fills.** No surveyed system simultaneously (1) derives its load/readiness/state metrics **transparently from raw signals** rather than vendor black boxes, (2) maintains an **explicit, inspectable physiological state model** tied to the determinants of race pace, (3) **continuously and autonomously adapts** the plan across multiple timescales (within-session cutoffs, daily readiness gating, weekly load/TID adjustment, mesocycle re-planning), and (4) *acts* on those decisions rather than merely advising. Commercial systems occupy pieces of this — Garmin is autonomous but opaque and derived-metric-driven; TrainingPeaks is transparent but advisory; AI platforms are adaptive but proprietary and lightly validated. The project's raw-data-driven, transparent, fully-autonomous, continuously-adaptive design targets exactly the empty quadrant.

---

## 7. Taper and peaking

The **taper** is the pre-race reduction in training load that lets accumulated fatigue dissipate faster than fitness, so form (TSB) peaks on race day. It is one of the best-quantified areas in the field.

**What the evidence says (Bosquet meta-analysis; Mujika & Padilla) [42][43][44]:**
- **Volume is the primary lever.** Performance is optimized by a **substantial reduction in training volume, commonly ~40–60%**, without a corresponding drop in intensity [42].
- **Maintain intensity.** High-intensity/race-pace stimuli should be *retained* (at reduced volume) through the taper; cutting intensity de-trains and blunts the peak [42][43].
- **Hold frequency roughly constant.** Large cuts in session frequency are not necessary and can feel de-conditioning for trained athletes; keep most sessions but shorten them [42][43].
- **Duration ~1–3 weeks, optimized around ~2 weeks.** The meta-analytic optimum centers on roughly **8–14 days**, scaling with prior training load and race distance (longer, more glycogen-demanding races and higher chronic loads warrant somewhat longer tapers) [42][43].
- **Exponential (progressive) taper beats a step taper** [42].
- **Expected magnitude:** meta-analytic performance improvements on the order of **~2–3%** — large relative to the margins that decide the success metric [42].

**Peaking / timing to race day.** Operationally, the taper is a **TSB controller**: reduce daily load so that **ATL falls quickly while CTL erodes only slightly**, bringing TSB from strongly negative into the modestly-positive "form" band precisely on race day (Section 4.2) [24][26]. The system should (a) preserve race-pace intensity while cutting volume progressively/exponentially, (b) size the taper length and depth by chronic load and race distance, and (c) verify the peak by reading the projected TSB trajectory plus readiness signals (HRV rebounding toward or above baseline, restored subjective wellness), since a well-executed taper is typically accompanied by a **parasympathetic rebound** [29][45]. A defensible default is a ~2-week, ~50% volume reduction, exponential decay, intensity maintained, frequency mostly held — then individualized by chronic load, response history, and goal distance, with the assumption stated.

**Status.** *Well-established.* The taper direction, magnitude, and volume-vs-intensity asymmetry are among the most robust findings in endurance science; the main uncertainty is *individual optimal duration/depth*, which is exactly what the adaptive system can personalize by watching TSB and readiness converge.

---

## References

[1] Seiler S. What is best practice for training intensity and duration distribution in endurance athletes? *IJSPP*, 2010;5(3):276-291. https://pubmed.ncbi.nlm.nih.gov/20861519/

[2] Bompa TO, Buzzichelli C. *Periodization: Theory and Methodology of Training.* 6th ed. Human Kinetics, 2019.

[3] Bassett DR, Howley ET. Limiting factors for maximum oxygen uptake and determinants of endurance performance. *MSSE*, 2000;32(1):70-84. https://pubmed.ncbi.nlm.nih.gov/10647532/

[4] Lydiard A, Gilmour G. *Running to the Top.* Meyer & Meyer Sport, 1997.

[5] Magness S. *The Science of Running.* Origin Press, 2014 (summary of Canova's specific-endurance approach). https://www.scienceofrunning.com/

[6] Issurin VB. New horizons for the methodology and physiology of training periodization: block periodization. *Sports Medicine*, 2010;40(3):189-206. https://pubmed.ncbi.nlm.nih.gov/20199119/

[7] Daniels J. *Daniels' Running Formula.* 4th ed. Human Kinetics, 2021.

[8] Pfitzinger P, Douglas S. *Advanced Marathoning.* 3rd ed. Human Kinetics, 2019.

[9] Joyner MJ, Coyle EF. Endurance exercise performance: the physiology of champions. *The Journal of Physiology*, 2008;586(1):35-44. https://pubmed.ncbi.nlm.nih.gov/17901124/

[10] Seiler KS, Kjerland GØ. Quantifying training intensity distribution in elite endurance athletes. *Scand J Med Sci Sports*, 2006;16(1):49-56. https://pubmed.ncbi.nlm.nih.gov/16430681/

[11] Stöggl T, Sperlich B. Polarized training has greater impact on key endurance variables than threshold, high intensity, or high volume training. *Frontiers in Physiology*, 2014;5:33. https://www.frontiersin.org/articles/10.3389/fphys.2014.00033/full

[12] Kenneally M, Casado A, Santos-Concejero J. The effect of periodization and training intensity distribution on middle- and long-distance running performance: a systematic review. *IJSPP*, 2018;13(9):1114-1121. https://pubmed.ncbi.nlm.nih.gov/29688059/

[13] Casado A, Hanley B, Santos-Concejero J, Ruiz-Pérez LM. World-class long-distance running performances are best predicted by volume of easy runs and deliberate practice of short-interval and tempo runs. *J Strength Cond Res*, 2021;35(9):2525-2531. https://pubmed.ncbi.nlm.nih.gov/31045681/

[14] Esteve-Lanao J, Foster C, Seiler S, Lucia A. Impact of training intensity distribution on performance in endurance athletes. *J Strength Cond Res*, 2007;21(3):943-949. https://pubmed.ncbi.nlm.nih.gov/17685689/

[15] Fitzgerald M. *80/20 Running.* NAL/Penguin, 2014; 80/20 Endurance. https://www.8020endurance.com/allaboutintensitybalance/

[16] Seiler S, Tønnessen E. Intervals, thresholds, and long slow distance. *Sportscience*, 2009;13:32-53. https://sportsci.org/2009/ss.htm

[17] Muñoz I, Seiler S, Bautista J, et al. Does polarized training improve performance in recreational runners? *IJSPP*, 2014;9(2):265-272. https://pubmed.ncbi.nlm.nih.gov/23752040/

[18] Rosenblat MA, Perrotta AS, Vicenzino B. Polarized vs. threshold training intensity distribution on endurance sport performance: a systematic review and meta-analysis. *J Strength Cond Res*, 2019;33(12):3491-3500. https://pubmed.ncbi.nlm.nih.gov/30153216/

[19] Stryd. Running power and Critical Power documentation; Skiba PF. The GOVSS running stress algorithm, 2006. https://www.stryd.com/

[20] Banister EW. Modeling elite athletic performance. In: *Physiological Testing of the High-Performance Athlete.* Human Kinetics, 1991:403-424.

[21] Edwards S. *The Heart Rate Monitor Book.* Polar Electro Oy, 1993.

[22] Lucia A, Hoyos J, Santalla A, Earnest C, Chicharro JL. Tour de France versus Vuelta a España: which is harder? *MSSE*, 2003;35(5):872-878. https://pubmed.ncbi.nlm.nih.gov/12750599/

[23] Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise training. *J Strength Cond Res*, 2001;15(1):109-115. https://pubmed.ncbi.nlm.nih.gov/11708692/

[24] Allen H, Coggan AR, McGregor S. *Training and Racing with a Power Meter.* 3rd ed. VeloPress, 2019; TrainingPeaks, "The Science of the Performance Manager." https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/

[25] Banister EW, Calvert TW, Savage MV, Bach T. A systems model of training for athletic performance. *Aust J Sports Med*, 1975;7:57-61.

[26] TrainingPeaks. A Coach's Guide to ATL, CTL & TSB. https://www.trainingpeaks.com/coach-blog/a-coachs-guide-to-atl-ctl-tsb/

[27] Hellard P, Avalos M, Lacoste L, et al. Assessing the limitations of the Banister model in monitoring training. *J Sports Sci*, 2006;24(5):509-520. https://pubmed.ncbi.nlm.nih.gov/16608765/

[28] Kiviniemi AM, Hautala AJ, Kinnunen H, Tulppo MP. Endurance training guided individually by daily heart rate variability measurements. *Eur J Appl Physiol*, 2007;101(6):743-751. https://pubmed.ncbi.nlm.nih.gov/17849143/

[29] Plews DJ, Laursen PB, Stanley J, Kilding AE, Buchheit M. Training adaptation and heart rate variability in elite endurance athletes. *Sports Medicine*, 2013;43(9):773-781. https://pubmed.ncbi.nlm.nih.gov/23852425/

[30] Vesterinen V, Nummela A, Heikura I, et al. Individual endurance training prescription with heart rate variability. *MSSE*, 2016;48(7):1347-1354. https://pubmed.ncbi.nlm.nih.gov/26909534/

[31] Javaloyes A, Sarabia JM, Lamberts RP, Moya-Ramon M. Training prescription guided by heart-rate variability in cycling. *IJSPP*, 2019;14(1):23-32. https://pubmed.ncbi.nlm.nih.gov/29809080/

[32] Javaloyes A, Sarabia JM, Lamberts RP, Plews D, Moya-Ramon M. Training prescription guided by heart rate variability vs. block periodization in well-trained cyclists. *J Strength Cond Res*, 2020;34(6):1511-1518. https://pubmed.ncbi.nlm.nih.gov/31490431/

[33] Granero-Gallegos A, et al. Monitoring and adapting endurance training on the basis of heart rate variability: a systematic review with meta-analysis. 2021. https://www.sciencedirect.com/science/article/pii/S1440244021001080

[34] Saw AE, Main LC, Gastin PB. Monitoring the athlete training response: subjective self-reported measures trump commonly used objective measures. *BJSM*, 2016;50(5):281-291. https://pubmed.ncbi.nlm.nih.gov/26423706/

[35] Helms ER, Cronin J, Storey A, Zourdos MC. Application of the repetitions in reserve-based RPE scale for resistance training. *Strength Cond J*, 2016;38(4):42-49. https://pubmed.ncbi.nlm.nih.gov/27531969/

[36] Weakley J, Mann B, Banyard H, et al. Velocity-based training: from theory to application. *Strength Cond J*, 2021;43(2):31-49. https://journals.lww.com/nsca-scj/fulltext/2021/04000/velocity_based_training__from_theory_to.5.aspx

[37] Friel J / TrainingPeaks. "Aerobic Decoupling" and "Efficiency Factor" (Pw:HR / Pa:HR) methodology. https://www.trainingpeaks.com/blog/aerobic-endurance-and-decoupling/

[38] Firstbeat/Garmin. Firstbeat Analytics white papers: VO2max, Training Load, Training Status, HRV Status, Training Readiness. https://www.firstbeat.com/en/science-and-physiology/white-papers-and-publications/

[39] Garmin. Daily Suggested Workouts and Training Status support documentation. https://support.garmin.com/

[40] Athletica.ai — adaptive endurance training platform. https://athletica.ai/ ; Humango.ai. https://humango.ai/

[41] Independent comparison of AI endurance platforms (TriDot, Athletica, Humango, AI Endurance, Runna, TrainingPeaks). https://www.transition.fun/blog/

[42] Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a meta-analysis. *MSSE*, 2007;39(8):1358-1365. https://pubmed.ncbi.nlm.nih.gov/17762369/

[43] Mujika I, Padilla S. Scientific bases for precompetition tapering strategies. *MSSE*, 2003;35(7):1182-1187. https://pubmed.ncbi.nlm.nih.gov/12840640/

[44] Mujika I. *Tapering and Peaking for Optimal Performance.* Human Kinetics, 2009.

[45] Le Meur Y, Hausswirth C, Mujika I. Tapering for competition: a review. *Science & Sports*, 2012;27(2):77-87. https://www.sciencedirect.com/science/article/abs/pii/S0765159711001961
