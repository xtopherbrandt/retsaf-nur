# Subjective Feedback, Injury Prevention & Recovery

**Abstract.** An autonomous running coach optimizes a single metric — average pace over a goal road race — but that metric is only realized if the athlete arrives at the start line healthy and adapted. An injured, non-functionally-overreached, or overtrained athlete produces zero race-day performance, so injury avoidance and recovery management are not competing objectives; they are constraints on pace maximization. This document establishes the evidence base for treating the athlete's subjective self-report as a first-class, continuous input alongside device data. It reviews the systematic-review evidence that self-report measures track training load and fitness–fatigue at least as sensitively as common objective measures; describes the early signals of overreaching, overtraining, and developing overuse injury, anchored to the ECSS/ACSM consensus; specifies concrete, implementable instruments (session-RPE with monotony and strain, DOMS and soreness mapping, five-item wellness questionnaires, Total Quality Recovery, POMS, RESTQ-Sport, and pain-monitoring models) with their items, scales, scoring, cadence, and validation status; reviews the models for tracking load and injury risk over time — including the acute:chronic workload ratio (ACWR) *and* its substantial methodological critiques; and lays out conservative, evidence-based return-to-running frameworks and the safety and escalation limits an autonomous system must respect.

---

## 1. Why subjective monitoring matters, and how it compares to objective data

The strongest single piece of evidence for building subjective self-report into an athlete-monitoring system is the systematic review by Saw, Main and Gastin, *Monitoring the athlete training response: subjective self-reported measures trump commonly used objective measures* [1]. Reviewing 56 studies, the authors found that subjective measures — chiefly self-reported wellness, mood, perceived fatigue, muscle soreness, stress and sleep — reflected acute and chronic training load with **greater sensitivity and consistency** than routinely used objective measures such as resting heart rate, submaximal heart rate, heart-rate variability, and biochemical/hormonal markers. Critically, subjective and objective measures often responded in *opposite directions* to changes in load: with an acute increase in load, subjective wellbeing worsened while some objective measures showed no clear or even a paradoxical response [1]. The practical conclusion is that a well-constructed self-report is not a soft supplement to "real" physiological data — it is frequently the more responsive signal, and it is cheap, non-invasive, and available daily.

This does not make objective data redundant. The value is in *corroboration and divergence* (Section 4.4): subjective and objective streams answer slightly different questions, and disagreement between them is itself diagnostic. HR/HRV reflect autonomic state; pace-at-a-given-HR reflects the integrated cardiovascular-metabolic cost of movement; soreness and perceived effort reflect mechanical and central fatigue that autonomic measures can miss. Saw et al. also caution that self-report quality depends heavily on *how* the instrument is administered — brevity, athlete buy-in, honest reporting environment, and consistent timing all materially affect validity [1]. An autonomous system must therefore engineer the *reporting process*, not just collect the numbers.

A complementary systematic review focused specifically on single-item wellbeing measures in team-sport athletes reached a consistent conclusion: simple single-item self-reports (fatigue, sleep, soreness, stress, mood) show meaningful, if modest and heterogeneous, relationships with training load, supporting their use for day-to-day monitoring while cautioning against over-interpreting any single value [2].

---

## 2. Early signs of overreaching, overtraining, and developing overuse injury

### 2.1 The overreaching–overtraining continuum

The authoritative framework is the joint consensus statement of the European College of Sport Science and the American College of Sports Medicine, *Prevention, diagnosis and treatment of the overtraining syndrome* [3]. It defines a continuum driven by the balance of training stress and recovery:

- **Functional overreaching (FOR):** a short-term, *planned* accumulation of fatigue and transient performance decrement (days to ~2 weeks) that, after adequate recovery, produces **supercompensation** — a rebound to higher performance. This is the intended outcome of a hard training block or overload microcycle [3].
- **Non-functional overreaching (NFOR):** when overload continues without sufficient recovery, performance stagnates or declines for **weeks to months**, accompanied by psychological and neuroendocrine disturbance. Recovery still occurs, but the athlete has paid a cost with no fitness gain — the block was net-negative [3].
- **Overtraining syndrome (OTS):** a severe, prolonged maladaptation (**months or longer**) with performance decrement plus persistent fatigue, mood disturbance, and often other systemic features, once other pathologies are excluded. OTS is a diagnosis of exclusion and there is **no single validated biomarker** for it [3].

The consensus stresses two points of direct relevance to an autonomous coach. First, FOR, NFOR and OTS differ mainly in the **duration of decreased performance and the degree of psychological disturbance**, not in a qualitatively different marker — so the system cannot reliably tell them apart in the moment; it can only distinguish them retrospectively by how long the decrement persists. This mandates conservative behavior: treat an unexplained, sustained performance/wellness decline as *at least* NFOR until proven otherwise. Second, the single most practical early-warning tool the statement endorses is **regular monitoring of performance together with psychological/mood state and simple self-report**, because mood and perceived-effort disturbances typically precede measurable performance loss [3].

### 2.2 The early-warning signal set

Drawing on the consensus [3] and the monitoring literature [1,4], the earliest and most accessible signals of accumulating maladaptation are:

- **Elevated perceived effort at a standard pace** ("pace feels harder than the number says"). A rising RPE for a controlled, fixed submaximal effort is one of the most useful field markers of fatigue.
- **Persistent or rising muscle soreness** beyond the expected DOMS window (see 3.2).
- **Mood disturbance:** reduced vigor, rising fatigue/tension/depression scores (the POMS "iceberg profile" inverting) — often the *first* thing to move [3,4].
- **Disturbed sleep** (quality and/or duration) and **elevated perceived stress / reduced motivation**.
- **Resting-HR and HRV changes** — but these are inconsistent in direction and magnitude between individuals and even within the OTS literature, so they are corroborating, not primary, signals [1,3].
- **Unexplained performance decrement** at a controlled test or in habitual training paces — the confirmatory sign, but a *lagging* one.

### 2.3 Developing overuse injury

Overuse running injuries — bone stress injuries (BSI), tendinopathies, patellofemoral pain (PFP), medial tibial stress syndrome — develop when mechanical load repeatedly exceeds tissue tolerance without adequate remodeling time. The early subjective signals are distinct from systemic overtraining and are **localized**:

- **Localized pain that warms up then returns**, or pain that appears *earlier* in successive runs.
- **Pain that persists or increases the next morning**, or point tenderness over bone (a red flag for BSI) or over a tendon.
- **Pain that climbs week to week** rather than settling to baseline.
- Escalating soreness confined to one site (rather than diffuse), often with a recent *spike* in volume, intensity, surface, or footwear change.

These map directly onto the pain-monitoring rules in Section 3.5 and the return-to-run criteria in Section 5. The autonomous coach should treat *localized, progressive, or morning-persistent pain* on a completely different pathway from *diffuse fatigue/soreness*: the former is a candidate injury requiring load reduction and possible escalation; the latter is a recovery-management problem.

---

## 3. Specific questions to ask, and validated instruments

This section specifies implementable instruments. Where exact published wording is not verifiable from primary sources, that is flagged explicitly rather than invented.

### 3.1 Session-RPE (Foster method) — the load backbone

Session-RPE (sRPE) is the best-validated, lowest-friction field method for quantifying internal training load [4,5]. Implementation:

- **The question:** "How hard was your session?" (i.e., rate the *global* intensity of the whole session), asked using the **modified CR-10 scale** (0 = rest, 1 = very easy … 5 = hard, 7 = very hard, 10 = maximal) [4,5].
- **Timing:** ask roughly **~30 minutes after the session ends**, so that a hard finish or easy cool-down does not bias the global rating; the ~30-minute delay is the conventional Foster recommendation [4,5]. In practice any consistent post-session delay of tens of minutes is acceptable; consistency matters more than the exact minute.
- **Session load:** `session load (AU) = sRPE (0–10) × session duration (minutes)` [4]. Units are arbitrary (AU).
- **Validity:** sRPE correlates well with heart-rate-based (e.g. Banister TRIMP, Edwards summated-HR-zone) internal-load methods across many sports and intensities, and remains valid in intermittent and resistance exercise where HR methods struggle; it is influenced by factors such as exercise mode, environment, and individual perception, but is robust enough for routine monitoring [5]. This is a **well-established** method.

**Weekly load, monotony and strain** [4]:
- **Weekly load** = sum of daily session loads over the week (AU).
- **Monotony** = mean daily load ÷ standard deviation of daily load across the week. High monotony ("medium every day") is associated with maladaptation; Foster's practical caution flag is **monotony > ~2.0**.
- **Strain** = weekly load × monotony (AU).

Foster's original observations linked spikes in weekly load, high monotony, and high strain with the onset of illness and maladaptation, motivating the deliberate hard–easy variation that periodization already prescribes [4]. These formulas are transparent, reproducible, and ideal for an autonomous system to own. (For the *ratio*-based interpretation of acute vs chronic load, see Section 4.1 and its critiques.)

### 3.2 DOMS / muscle-soreness scales

Delayed-onset muscle soreness typically peaks ~24–72 h after unaccustomed or high-eccentric load and resolves within a few days; soreness that persists beyond this window, or rises week over week, is a maladaptation/injury signal (Section 2). Practical implementation:

- **Global soreness:** a single item rated **0–10** (0 = no soreness, 10 = severe/unable to move), or the 1–5/1–7 general-muscle-soreness item embedded in wellness questionnaires (Section 3.3). There is no single universally "gold-standard" DOMS scale; 0–10 numeric ratings and short verbal-anchored scales are widely used and adequately reliable for tracking within-athlete change.
- **Localized soreness/pain mapping:** ask the athlete to indicate *location* (e.g. anterior shin, Achilles, lateral knee, plantar heel, hip) alongside intensity. A body-region map converts a diffuse "I'm sore" into an actionable, site-specific signal that feeds the injury pathway and the pain-monitoring rules (3.5). Distinguishing **muscle soreness** (broad, symmetrical, warms up, expected after hard sessions) from **localized joint/bone/tendon pain** (focal, tender to touch, may worsen with continued loading) is the key triage the system must make.

### 3.3 Wellness / readiness questionnaires

**The five-item morning wellness questionnaire** is the workhorse of daily subjective monitoring. Its lineage runs from Hooper and Mackinnon's overtraining-monitoring recommendations [6] to the widely-copied five-item format popularized by McLean et al. [7]. The five items are:

1. **Fatigue**
2. **Sleep quality**
3. **General muscle soreness**
4. **Stress levels**
5. **Mood**

In the McLean-style version each item is rated on a **1–5 scale** (commonly in 0.5 increments), with anchors running from a worst state (e.g. 1 = "always tired"/"very sore"/"highly stressed") to a best state (5 = "very fresh"/"feeling great"), and the five items summed to a daily wellness score [7]. Hooper and Mackinnon's original used **1–7 ratings** for fatigue, stress, sleep and muscle soreness [6]. **Important implementation caution:** the *exact anchor wording* varies between published versions and is often adapted per squad; the system should adopt one explicit, fixed anchor set and keep it constant per athlete rather than assume a single canonical wording exists. What is well-established is the *item set and the 1–5 or 1–7 Likert format*; the specific sentence anchors are not standardized [1,6,7].

- **Cadence:** daily, on waking, before training — timing consistency is essential because wellness has a strong diurnal and pre/post-training pattern [1].
- **Validation status:** individual items show modest-to-moderate associations with training load and are sensitive to acute load changes [1,2]; the instrument is valued for responsiveness and low burden rather than for classical psychometric validation of a fixed scale.

**Total Quality Recovery (TQR).** Developed by Kenttä and Hassmén as a recovery analogue to Borg's RPE [8], TQR asks the athlete to rate *perceived recovery* on a scale **deliberately mirroring the Borg 6–20 RPE scale** — commonly **6 = "very, very poor recovery"** to **20 = "very, very good recovery"** — so that recovery can be tracked on the same mental yardstick as exertion. The conceptual rule of thumb is that recovery (TQR) should keep pace with exertion (sRPE) for adaptation to proceed; a persistent gap (high exertion, low recovery) flags accumulating deficit [8]. Kenttä and Hassmén also describe an "action" version that itemizes recovery behaviors (nutrition, sleep, relaxation, stretching) summing to a score, but the perceived-recovery 6–20 rating is the core field tool. Cadence: daily (morning) or post-session. Validation: conceptually grounded and used in practice; psychometric validation is lighter than for sRPE.

**Profile of Mood States (POMS).** POMS measures mood across subscales — classically Tension, Depression, Anger, Vigor, Fatigue, Confusion (the full instrument has 65 items; validated short forms of ~30–40 items exist). The characteristic healthy-athlete "**iceberg profile**" (Vigor elevated above the other, suppressed, negative-mood subscales) *flattens or inverts* with overreaching/overtraining — a classic early marker cited in the overtraining literature [3]. **Licensing note:** POMS is a copyrighted instrument; an autonomous system should not embed the verbatim items without a license. It can, however, track the *constructs* (vigor, fatigue, tension, mood) via the wellness items above, or license a validated short form. Cadence: weekly, or during heavy blocks. Validation: extensively validated as a mood instrument.

**RESTQ-Sport (Recovery–Stress Questionnaire for Athletes).** A multidimensional questionnaire assessing the frequency of stress and recovery activities/states over the recent period, across general and sport-specific stress and recovery scales; the full version has **77 items (19 scales)** and a **short 52-item** version exists, rated on a 0–6 frequency scale ("never" to "always") [9]. It is designed specifically to track the recovery–stress balance for overtraining prevention. Cadence: every 1–4 weeks (it asks about a recent multi-day window, so it is *not* a daily tool). Validation: psychometrically evaluated, though item-level performance is mixed [9]. **Licensing note:** RESTQ-Sport is also a copyrighted/published instrument requiring permission for verbatim use.

**Design recommendation for the autonomous system.** Use *daily* a five-item 1–5 wellness questionnaire plus per-session sRPE; use *weekly* a recovery–stress / mood check-in (either a licensed short RESTQ/POMS or a construct-matched proxy). Own the wellness and sRPE instruments outright (they are method-descriptions, not copyrighted scales); license or proxy POMS/RESTQ.

### 3.4 Sleep, stress and life-load check-ins

Because non-training stress (work, travel, illness, life events) consumes the same recovery budget as training, the daily check-in should include **sleep duration and quality**, **perceived life stress**, and a lightweight **illness flag** (sore throat, congestion, GI upset, fever). These contextualize the physiological data: a poor wellness score with high life stress and short sleep is a recovery problem, not necessarily a training-dose problem, and the appropriate response differs (protect recovery vs reduce load). Sleep and stress items are standard components of the wellness set [1,6,7].

### 3.5 Pain during and after running — interpretation

Pain requires its own instrument distinct from soreness, because its *interpretation* drives load decisions. The best-validated running-relevant framework is the **pain-monitoring model** of Silbernagel et al., developed and tested in a randomized controlled trial of continued running during Achilles tendinopathy rehabilitation [10]. On a **0–10 pain visual analog scale**, the rules are:

- **Pain up to ~5/10 during and immediately after loading is acceptable** ("safe zone") and does not indicate tissue damage;
- **Pain must settle to baseline by the next morning**; and
- **Pain must not increase week to week** [10].

This translates naturally into a **traffic-light system** for an autonomous coach:

- **Green (0–2/10, settles overnight, stable trend):** proceed / progress load.
- **Amber (3–5/10, settles by next morning, not worsening across weeks):** hold load; do not progress; monitor closely.
- **Red (>5/10, or pain that persists into the next morning, or that climbs week to week, or focal bony tenderness):** reduce load / stop running; escalate (Section 6).

The Silbernagel model is validated specifically for **tendinopathy**; the "pain up to 5, must settle overnight" principle is a reasonable *default* the system can apply to tendon and muscle presentations. **Bone stress injuries are the important exception:** bone pain should be treated far more conservatively — running through it is not acceptable, and even low-grade progressive localized bony pain/tenderness warrants stopping and, for suspected high-risk sites, escalation (Section 5.4). The system must therefore condition the traffic-light thresholds on the *tissue type* implied by pain location.

---

## 4. Models for tracking subjective load and injury risk over time

### 4.1 Acute:chronic workload ratio (ACWR) — widely used, and contested

ACWR compares recent ("acute," typically the most recent 7 days) training load to the athlete's medium-term ("chronic," typically the trailing 28 days) load, as a proxy for whether current load is well-matched to what the athlete is *prepared for*:

`ACWR = acute load (7-day) ÷ chronic load (28-day average)`

Load here can be any consistent internal or external measure — sRPE-load is the natural choice for a running system. Gabbett's influential *training–injury prevention paradox* paper popularized the idea of a **"sweet spot" (~0.8–1.3)** associated with lower injury risk and a **"danger zone" (> ~1.5)** of sharply elevated risk, and argued that *high chronic load built gradually is protective*, whereas rapid **spikes** in acute load are the injury driver — hence "train smarter *and* harder" [11]. This popularized the actionable heuristic: **avoid week-to-week load spikes; build chronic load progressively.**

Two computation variants exist:
- **Rolling average (RA):** simple unweighted means over the 7- and 28-day windows.
- **Exponentially weighted moving average (EWMA):** weights recent days more heavily using a decay `λ = 2 ÷ (N + 1)` for a window of N days; Williams et al. proposed it and Murray et al. reported it as a *more sensitive* injury-likelihood indicator than rolling averages, because it better reflects the decaying influence of older sessions and handles missing days more gracefully [12,13].

### 4.2 The critiques — why ACWR must be used cautiously

ACWR is **contested**, and a responsible autonomous system must encode that caution rather than treat the sweet spot as a validated law:

- **Mathematical coupling / spurious correlation.** The acute load is itself part of the chronic load (the numerator is a component of the denominator), which mathematically induces correlation between the ratio and injury outcomes independent of any real physiological relationship. Lolli et al. showed this coupling produces **spurious correlation** in conventional ACWR calculations [14].
- **Conceptual and statistical pitfalls.** Impellizzeri and colleagues catalogued fundamental problems: the ratio discretizes a continuous exposure, is unstable at low chronic-load denominators, conflates distinct constructs, and has been analyzed with methods prone to bias; they argue the normalization the ratio performs is often *unnecessary* and that acute and chronic load are better modeled as separate predictors [15,16].
- **Weak/inconsistent predictive validity.** Subsequent reviews and re-analyses have found the association between ACWR and injury to be inconsistent and often weak once methodological artifacts are addressed; the original "sweet spot" thresholds do not generalize reliably across sports, load measures, or populations [15,16].

**Defensible default for the system.** Compute ACWR (EWMA form) and *display/track it as one context signal*, but do **not** make it a hard gate or the sole basis for load decisions. Use it primarily in its least-contested form: **flag large week-to-week increases in acute load** as a *caution to slow progression*, consistent with the well-established principle of gradual overload — while explicitly not asserting a validated injury-probability threshold. Pair it always with the subjective signals, which are more responsive [1]. Prefer the classic, robust guidance — **progress volume conservatively and avoid spikes** — over precise ACWR cut-points.

### 4.3 Monotony and strain (Foster)

As defined in 3.1, **monotony** (mean/SD of daily load) and **strain** (weekly load × monotony) capture a dimension ACWR misses: the *distribution* of load within the week [4]. Persistently high monotony — the same moderate load every day, with no genuine easy days — is associated with maladaptation and illness even when total load is unremarkable. These are transparent, well-behaved formulas the system should compute weekly and use to enforce hard–easy variation (flag monotony > ~2.0).

### 4.4 Trend-based interpretation of wellness scores

A single day's wellness number is noisy; the signal is in the **trend relative to the athlete's own baseline**. Recommended approach:

- **Individual rolling baseline.** Maintain a rolling mean and SD of each wellness item (and the composite) per athlete over a trailing window (e.g. 28–42 days). Interpret today's value as a **z-score** or as a change relative to that personal baseline, *not* against population norms — inter-individual variation in reporting style is large [1].
- **Meaningful change vs noise.** Treat a deviation beyond roughly **1 SD** as *possibly* meaningful and beyond ~**1.5–2 SD**, or a *sustained* multi-day decline, as a flag warranting a load-management response. Single one-off dips are usually noise (a bad night's sleep); **consecutive** below-baseline days, or a coherent decline across *multiple* items at once, are the actionable pattern.
- **Rolling baselines must adapt** as fitness changes, but slowly enough not to "launder" a genuine downward drift into the new normal — a slow chronic decline is exactly the NFOR signature the system must catch (Section 2).

### 4.5 Corroboration and divergence between subjective and objective signals

The system should continuously compare the subjective stream (wellness, sRPE-at-pace, soreness) with objective streams (resting HR, HRV, and especially **pace-at-a-given-HR / HR-at-a-given-pace**, i.e., internal-to-external efficiency). Interpretation:

- **Both degrade together** (wellness down, HRV down, higher HR or RPE at standard pace): high-confidence fatigue/maladaptation — reduce load.
- **Subjective degrades, objective stable:** given that self-report is often the *more sensitive* early signal [1], treat this as an early warning; hold or ease load and watch for objective confirmation rather than dismissing the subjective report.
- **Objective degrades, subjective fine:** possible autonomic perturbation (illness incubating, life stress, poor sleep) the athlete hasn't perceived; probe with an illness/stress check-in before progressing.
- **Divergence in general** is informative, not a data error — Saw et al. explicitly document that subjective and objective measures frequently move differently in response to load [1]. The autonomous coach should log divergence and default to the **more conservative** interpretation when the two disagree.

---

## 5. Return-to-running and rehabilitation progression after injury

An autonomous system generally should **not** attempt to *diagnose* or *treat* injury (Section 6). What it can responsibly do is (a) detect the injury-risk/pain signals above, (b) reduce load and recommend professional assessment, and (c) once an athlete is cleared or is managing a minor, non-red-flag overuse complaint, administer a **conservative, criteria-based graded return-to-run (RTR)** with safety-first defaults. This section provides the evidence base for that rule set.

### 5.1 Core principles

- **Criteria-based, not calendar-based.** Progression is gated by symptom response, not by elapsed time alone. The consistent theme across return-to-run guidance is that the athlete must be **pain-free with daily activity/walking before running is introduced**, and each step is earned by tolerating the previous one [17].
- **Walk–run progression.** Reintroduce running via **walk–run intervals**, progressing **duration/distance before speed and intensity**, and volume before pace [17].
- **Pain-monitoring gate.** Use the traffic-light rules (3.5): progress on green, hold on amber, regress/stop on red. Pain must settle to baseline by the next morning and must not climb week to week [10,17].
- **Conservative load stepping.** The familiar "**10% per week**" volume rule is a *starting heuristic, not a validated law* — the tibial-BSI scoping review explicitly notes it "is not generalisable to all runners" and that progression should be individualized to injury severity and runner experience [17]. Default to **small increments with a hold/step-back option** whenever symptoms rise.

### 5.2 A cautious default walk–run progression

The following is a conservative, illustrative RTR template consistent with the cited frameworks [10,17]; specific durations must be individualized and, for anything beyond minor soreness, supervised by a clinician. Each stage is repeated until it is symptom-clear (green) on two-to-three sessions before advancing; any amber holds the stage; any red regresses one or more stages and/or triggers escalation.

| Stage | Session structure (example) | Advance criterion |
|---|---|---|
| 0 | Pain-free walking (build to ~30–45 min brisk walk) | No pain during or after; no next-morning symptoms |
| 1 | Walk–run: e.g. 1 min run / 2–4 min walk × repeats, low volume, easy pace | Green across the session and next morning |
| 2 | Increase run interval, decrease walk (e.g. 2–3 min run / 1–2 min walk) | Green, stable week-to-week |
| 3 | Continuous easy running, short duration, ~every other day | Green; no accumulation over the week |
| 4 | Gradually increase easy-run *duration* (small steps), still no speed | Green; morning-clear |
| 5 | Reintroduce *frequency*, then gentle *intensity*/strides last | Green throughout |
| 6 | Return to structured training; rebuild toward goal-race workload | Full pain-free tolerance |

Only after full pain-free tolerance of easy volume should speed, hills, and race-specific intensity be layered back — intensity is the *last* variable restored, never the first.

### 5.3 Tissue-healing timelines and load-management by injury type

Return timelines differ by tissue and must inform how aggressively the system allows progression:

- **Bone stress injury (BSI).** Bone remodeling is slow. BSIs are graded (typically MRI grades 1–4, grade 4 indicating a fracture line) and stratified by **risk of the anatomical site**: *low-risk* sites (e.g. posteromedial tibia, metatarsal shafts) permit earlier, cautious RTR once pain-free with walking, whereas *high-risk* sites (e.g. anterior tibial cortex, femoral neck, navicular, medial malleolus) require imaging-confirmed healing before running resumes and carry a real risk of progression to complete fracture if loaded prematurely [17]. Return commonly spans **many weeks to several months**. Bone pain is a **red-flag exception** to the "run through mild pain" principle: the default is *do not run on suspected bone pain* and escalate.
- **Tendinopathy** (Achilles, patellar, others). Tendons *tolerate and often need* progressive loading; the Silbernagel pain-monitoring model (pain ≤5/10, settling overnight, not worsening weekly) was validated precisely to allow **continued controlled activity** during rehabilitation [10]. Progressive loading (isometrics/heavy-slow resistance/eccentrics under clinician guidance) is standard care; the running system's role is to keep running load within the pain-monitoring envelope and coordinate with, not replace, a rehab program. Adaptation is slow (weeks to months).
- **Patellofemoral pain (PFP).** Typically load- and volume-related; management is activity/load modification within an acceptable-pain envelope plus hip/knee strengthening. RTR follows the same green/amber/red gating with attention to downhill running, volume, and cadence. Symptoms can be managed while training if kept within the acceptable-pain range, but persistent or worsening pain warrants assessment.

### 5.4 Safety-first defaults for BSI suspicion

Because BSI is the injury where an autonomous system can do the most harm by encouraging loading, the system should hard-code conservative behavior: **focal bony tenderness, pain that worsens with continued running, night pain, or localized pain that fails to settle overnight → stop running and escalate for clinical/imaging assessment**, rather than applying the tendon-oriented "pain up to 5" rule.

---

## 6. Safety, scope, and the limits of self-report

An autonomous coach operates without a clinician in the loop for most decisions, so its defaults must be **conservative and its escalation thresholds explicit**.

**Where the system must be conservative.** When subjective and objective signals disagree, default to the *more cautious* reading [1]. When wellness shows a sustained multi-item, multi-day decline, reduce load rather than pushing through — the system cannot distinguish FOR from NFOR/OTS prospectively [3], so it must assume the worse of the two whenever a decrement persists. Treat *localized, progressive, or morning-persistent pain* as a candidate injury and reduce load first, ask questions second.

**What it must escalate to a human/medical professional.** The system should recommend professional assessment (and withhold "keep training" advice) for: suspected **bone stress injury** (focal bony tenderness, night pain, pain worsening with loading); pain rated **red** on the traffic-light model or not settling overnight or climbing week to week [10]; a **sustained, unexplained performance and wellness decline** consistent with NFOR/OTS despite adequate recovery [3]; any **systemic red flags** (fever, unexplained weight loss, chest pain, syncope, signs of illness beyond a mild cold); **acute/traumatic** pain or swelling; and symptoms suggestive of **RED-S / low energy availability** (recurrent BSI, menstrual disturbance, persistent underperformance). The autonomous coach diagnoses nothing; it detects patterns and routes to care.

**The limits of self-report.** Self-report is powerful but biased. Athletes **under-report** symptoms when they fear losing a training spot or a race build, especially near a goal race; social-desirability and "toughness" norms suppress honest reporting; and report quality decays with survey fatigue if the instrument is long or feels unactioned [1]. Mitigations the system should build in: keep daily instruments **short** (five items + sRPE), keep **timing consistent**, close the loop so the athlete *sees* their input change the plan (buy-in improves honesty), interpret against the athlete's **own baseline** rather than absolute cut-points, and treat *missing* reports and *implausibly flat* reports as signals in themselves. Finally, self-report should never be the *only* gate for a safety-critical decision: corroborate with objective data where possible, and escalate rather than resolve genuine ambiguity autonomously.

**Net principle for the coach.** The objective function is race-day average pace, but it is maximized *subject to* keeping the athlete healthy and adapted. Subjective feedback is the most responsive, cheapest early-warning instrument available [1]; the overtraining continuum means sustained decrements must be treated conservatively [3]; pain must be interpreted by tissue and trend, not toughness [10]; load-tracking tools like ACWR are useful context but contested and must not be hard gates [14,15,16]; and return-to-run must be criteria-based, conservative, and quick to escalate [17]. An injured or overtrained athlete runs no race — so within this system, caution *is* performance optimization.

---

## References

[1] Saw AE, Main LC, Gastin PB. Monitoring the athlete training response: subjective self-reported measures trump commonly used objective measures: a systematic review. *British Journal of Sports Medicine*. 2016;50(5):281–291. https://doi.org/10.1136/bjsports-2015-094758

[2] Jeffries AC, Wallace L, Coutts AJ, et al. Single-Item Self-Report Measures of Team-Sport Athlete Wellbeing and Their Relationship With Training Load: A Systematic Review. *Journal of Athletic Training*. 2020;55(9):932–953. https://pmc.ncbi.nlm.nih.gov/articles/PMC7534939/

[3] Meeusen R, Duclos M, Foster C, et al. Prevention, diagnosis, and treatment of the overtraining syndrome: Joint consensus statement of the ECSS and the ACSM. *Medicine & Science in Sports & Exercise*. 2013;45(1):186–205 (also *European Journal of Sport Science*. 2013;13(1):1–24). https://doi.org/10.1080/17461391.2012.730061

[4] Foster C. Monitoring training in athletes with reference to overtraining syndrome. *Medicine & Science in Sports & Exercise*. 1998;30(7):1164–1168. (See also Foster C, et al. A new approach to monitoring exercise training. *J Strength Cond Res*. 2001;15(1):109–115.) https://doi.org/10.1097/00005768-199807000-00023

[5] Haddad M, Stylianides G, Djaoui L, Dellal A, Chamari K. Session-RPE Method for Training Load Monitoring: Validity, Ecological Usefulness, and Influencing Factors. *Frontiers in Neuroscience*. 2017;11:612. https://doi.org/10.3389/fnins.2017.00612

[6] Hooper SL, Mackinnon LT. Monitoring overtraining in athletes: recommendations. *Sports Medicine*. 1995;20(5):321–327. https://doi.org/10.2165/00007256-199520050-00003

[7] McLean BD, Coutts AJ, Kelly V, McGuigan MR, Cormack SJ. Neuromuscular, endocrine, and perceptual fatigue responses during different length between-match microcycles. *International Journal of Sports Physiology and Performance*. 2010;5(3):367–383. https://doi.org/10.1123/ijspp.5.3.367

[8] Kenttä G, Hassmén P. Overtraining and recovery: a conceptual model. *Sports Medicine*. 1998;26(1):1–16. https://doi.org/10.2165/00007256-199826010-00001

[9] Kellmann M, Kallus KW. *Recovery-Stress Questionnaire for Athletes: User Manual*. Human Kinetics; 2001. (Psychometrics: Davis H, Orzeck T, Keelan P. *Psychology of Sport and Exercise*. 2007;8(6):917–938. https://doi.org/10.1016/j.psychsport.2006.10.003)

[10] Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J. Continued sports activity, using a pain-monitoring model, during rehabilitation in patients with Achilles tendinopathy: a randomized controlled study. *American Journal of Sports Medicine*. 2007;35(6):897–906. https://doi.org/10.1177/0363546506298279

[11] Gabbett TJ. The training-injury prevention paradox: should athletes be training smarter and harder? *British Journal of Sports Medicine*. 2016;50(5):273–280. https://doi.org/10.1136/bjsports-2015-095788

[12] Williams S, West S, Cross MJ, Stokes KA. Better way to determine the acute:chronic workload ratio? *British Journal of Sports Medicine*. 2017;51(3):209–210. https://doi.org/10.1136/bjsports-2016-096589

[13] Murray NB, Gabbett TJ, Townshend AD, Blanch P. Calculating acute:chronic workload ratios using exponentially weighted moving averages provides a more sensitive indicator of injury likelihood than rolling averages. *British Journal of Sports Medicine*. 2017;51(9):749–754. https://doi.org/10.1136/bjsports-2016-097152

[14] Lolli L, Batterham AM, Hawkins R, et al. Mathematical coupling causes spurious correlation within the conventional acute-to-chronic workload ratio calculations. *British Journal of Sports Medicine*. 2019;53(15):921–922. https://doi.org/10.1136/bjsports-2017-098110

[15] Impellizzeri FM, Woodcock S, Coutts AJ, Fanchini M, McCall A, Vigotsky AD. Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls. *International Journal of Sports Physiology and Performance*. 2020;15(6):907–913. https://doi.org/10.1123/ijspp.2019-0864

[16] Impellizzeri FM, Tenan MS, Kempton T, Novak A, Coutts AJ. Acute:Chronic Workload Ratio: An Inaccurate Scaling Index for an Unnecessary Normalization Process? *International Journal of Sports Physiology and Performance*. 2020;15(6). https://doi.org/10.1123/ijspp.2019-0864

[17] Criteria and Guidelines for Returning to Running Following a Tibial Bone Stress Injury: A Scoping Review. *Sports Medicine*. 2024. https://doi.org/10.1007/s40279-024-02051-y (PMID: 39141251)
