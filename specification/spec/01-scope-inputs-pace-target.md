# Section 1 — Scope, System Inputs, and the Pace Target

*Build-ready specification, Phase 2. This section is the entry point of the closed loop: it fixes what the system is for, what it accepts as input at program start, and how it turns a declared goal race into the single quantity everything downstream optimizes — the athlete's expected average pace over the full race distance. It conforms to `research/00` as the decision authority and cites `research/01` for the physiology-to-pace models it states. Per the project conventions, rules and formulas are stated here in full; the evidence and derivations behind them live in the cited docs and are not re-argued.*

---

## 1.1 Scope: the tuning target versus the runtime envelope

The system is a general-purpose adaptive running coach. Its job, stated once and inherited by every later section, is to raise the **expected average running pace over the full distance of a specific goal race, under that race's specific course profile and expected conditions** (`research/00` §1.1). This is the terminal value; scope, inputs, and the pace target defined below are all in service of it.

Two things must be kept distinct, because conflating them is the most common way a general-purpose system gets accidentally narrowed into a special-purpose one.

**The tuning target** is the athlete and race type the system is *designed, defaulted, and validated around*: the serious recreational or competitive amateur — an adult road racer training hard for a goal race of roughly 5K through marathon, who has a consistent history of recorded training to learn from. Every default parameter, every priority, and every validation case in this specification assumes this athlete unless stated otherwise. When a design trade-off is otherwise unclear, it is resolved for this athlete and, above that, for the pace objective (`research/00` §1.1).

**The runtime envelope** is what the software must *accept and process without redesign*: any athlete profile and any road-race target supplied as inputs at program start. The tuning target sets defaults; it does not bound the input domain. An implementer must therefore build the input schemas (§1.2, §1.3), the physiological-state estimator (Section 4), and the pace-target procedure (§1.4) to run on inputs outside the tuning center — a first-time racer with thin history, a sub-elite with a very low endurance exponent, an unusually hilly or hot target race — degrading to honestly-flagged wide-confidence defaults rather than failing or silently substituting a tuning-target assumption. Where an input falls outside the range the system can model with confidence, the system widens the confidence band on its output (§1.4.6) rather than refusing the input; this is the same low-confidence-widens-guardrails posture that governs the whole state model (`research/00` Part 2, finding 8).

Three scope boundaries are settled and are not re-litigated here (they are fixed by the project instructions and `research/00`):

- **Race type is road racing.** Course elevation profile and environmental conditions are first-class inputs and are modeled explicitly (§1.4.4). Track, trail, and ultra-distance racing are out of the tuning target; the input schema does not forbid them, but the pace models in §1.4 are validated only across the 5K–marathon road range and their confidence degrades outside it.
- **Data scope is Garmin-first, extensible.** The concrete primary data source is the Garmin ecosystem and the FIT data format, specified in full in Section 2. This section refers to athlete "historical training data" abstractly; the canonical schema and the Garmin adapter that populate it are Section 2's responsibility.
- **The distance range is 5K to marathon.** This is the range over which the endurance-exponent and critical-speed models (§1.4.1–§1.4.2) are calibrated (`research/01` §4.2–§4.3).

---

## 1.2 The goal-race input

The goal race is supplied as a structured object at program start. It is the *contract* the athlete owns: the system may propose a change to it but never alters it autonomously (`research/00` §1.9). The following fields are required unless marked optional. Field names are canonical and are the ones later sections reference.

| Field | Type | Units / format | Required | Notes |
|---|---|---|---|---|
| `race_date` | date | ISO-8601 calendar date, athlete's local timezone | Yes | Anchors the whole periodization timeline (Sections 5, 7). Determines weeks-to-race at program start. |
| `distance_m` | number | metres | Yes | Canonical race distance. Standard values (5000, 10000, 21097.5, 42195) are recognized; any positive value is accepted. |
| `course_profile` | course-geometry reference | see §1.2.1 | Yes | Cumulative distance-versus-elevation series for the course. Drives the grade modifier (§1.4.4). |
| `expected_conditions` | environmental object | see §1.2.2 | Yes | Expected race-day temperature, humidity, and wind. Drives the environmental modifiers (§1.4.4). |
| `start_time_local` | time | local clock time | Optional | Used, when present, to refine the expected-conditions estimate (e.g. a dawn start in a hot climate). Absent → conditions taken as supplied. |
| `goal_pace_target` | pace | seconds per kilometre | Optional | The athlete's declared target, if they have one. When present it becomes the pace field of the goal contract the system tracks its projection against (`research/00` GOAL-02). When absent, the system's own projection (§1.4) stands in until the athlete declares one. |

### 1.2.1 Course profile

`course_profile` is a course-geometry reference: a series of (cumulative-distance, elevation) samples spanning the course from start to finish, from which the system derives, for every point on the course, the local gradient used by the grade modifier (§1.4.4). At the level of this section the profile is treated as an already-resolved elevation-versus-distance series; the *ingestion* of that series — accepted source formats (e.g. GPX), the resampling and interpolation rules that turn a raw track into a clean distance-indexed series, and the quality flags on interpolated points — is specified in Section 2 (canonical schema and ingestion). Section 1 depends only on the resolved series being available; it does not define how the series is obtained. *(Forward reference: Section 2's course-geometry ingestion subsection closes this; the exact subsection number is fixed when Section 2 is drafted.)*

If no course profile is available for a target race, the system falls back to a flat profile (zero gradient throughout) and flags the grade modifier as defaulted, widening the pace-target confidence band accordingly (§1.4.6).

### 1.2.2 Expected conditions

`expected_conditions` carries the race-day environment the pace target is computed against:

| Sub-field | Type | Units | Required | Notes |
|---|---|---|---|---|
| `temperature_c` | number | °C, dry-bulb air temperature | Yes | Combined with humidity into a heat-stress index (§1.4.4). |
| `humidity_pct` | number | relative humidity, 0–100 | Yes | Compounds heat stress by impairing evaporative cooling. |
| `wind_speed_ms` | number | metres per second | Optional | Magnitude of expected wind. Absent → treated as still air. |
| `wind_direction` | enum / bearing | headwind, tailwind, crosswind, or a bearing relative to the dominant course heading | Optional | Governs how the quadratic wind cost nets out over the course (§1.4.4). Absent → wind treated as a net-zero-direction penalty using the conservative headwind-dominant assumption. |
| `altitude_m` | number | metres above sea level, course mean | Optional | Mean elevation of the course, used for the altitude modifier (§1.4.4). May be derived from `course_profile` if not supplied separately. |

Expected conditions are, by nature, a forecast made weeks out; they are an estimate and are treated as such. The pace target computed from them carries the environmental modifiers' uncertainty into its confidence band (§1.4.6), and the target is recomputed as race day approaches and the forecast firms up.

---

## 1.3 The athlete input

The athlete is supplied as a profile plus a body of historical training data. The profile carries the static and slowly-changing facts the system needs to seed its models; the historical data is what the state estimator (Section 4) mines to establish the athlete's current physiological starting state. This section defines *what is accepted*; Section 4 defines *how the starting state is inferred from it*, and Section 2 defines the canonical schema the historical data is normalized into.

| Field | Type | Units / format | Required | Notes |
|---|---|---|---|---|
| `athlete_id` | identifier | opaque string | Yes | Stable key for the athlete across sessions. |
| `sex` | enum | male / female / unspecified | Optional | Used only where a model parameter is sex-dependent (e.g. the non-exercise cold-start seed, Section 4). Unspecified → sex-neutral defaults. |
| `birth_date` or `age_years` | date / number | ISO date, or years | Optional | Feeds age-dependent defaults (e.g. max-HR priors, the cold-start estimator). Absent → population defaults with widened bands. |
| `body_mass_kg` | number | kg | Optional | Used in power/economy and load calculations where mass appears. Absent → left symbolic; metrics that require it are flagged unavailable rather than assuming a value. |
| `resting_hr_bpm` | number | beats per minute | Optional | A cold-start input for the non-exercise fitness seed (Section 4, per `research/00` §3.2). |
| `history` | training-data reference | canonical sessions (Section 2) | Yes* | The athlete's recorded training: per-session raw streams (HR, pace/GPS, cadence, altitude, RR intervals where present, power where present), normalized into the canonical schema. This is the substrate for the starting-state estimate. |
| `known_bests` | list of race/effort results | distance + time + date + conditions | Optional | Recent race results or maximal time-trial efforts. When present these are high-value anchors for critical speed and the endurance exponent (§1.4.1–§1.4.2), stronger than inference from ordinary training. |
| `subjective_baseline` | wellness/RPE reference | Section 6 / `research/03` instruments | Optional | Any historical self-report (session-RPE, wellness, soreness). Establishes the athlete's subjective baseline for the readiness logic downstream. |

\* `history` is required for the system to run in its designed mode. When it is absent or too thin to fit the athlete's own critical-speed curve, the system does not fail: it enters the **cold-start** path (Section 4, per `research/00` §3.2), seeding day-one state from the non-exercise fitness estimate when its inputs (`resting_hr_bpm` and demographics, only the inputs `research/00` COLD-08 names, no HRV) are present, and otherwise from population defaults with wide, honestly-flagged confidence intervals. The pace target (§1.4) is still produced in either case; it simply carries a low-confidence flag until running history accrues.

The division of labor is deliberate and must be preserved by the implementer: **Section 1 fixes the input contract, Section 2 normalizes the historical data into the canonical raw-stream schema, and Section 4 turns that normalized history into the physiological state estimate.** Section 1 consumes the *output* of Section 4 — the state estimate — to produce the pace target below; it does not itself perform the state estimation.

---

## 1.4 From physiological state to the pace target

This is the section's terminal product: a procedure that takes the athlete's current physiological state estimate (from Section 4) and the goal-race input (§1.2), and returns an expected average race pace with a confidence and a full provenance trace. The procedure is the physiology-to-pace bridge of `research/01` §4.4, made concrete.

The chain has five stages: (1) anchor a sustainable pace from the athlete's critical speed and velocity at VO₂max; (2) project that anchor to the goal-race distance with the athlete's endurance exponent; (3) adjust for running economy and durability; (4) apply the course and environmental modifiers; (5) compose the modifiers in a defined order and emit the target with its confidence. Every stage is stated as a formula or an explicit rule.

### 1.4.1 Stage 1 — Anchor sustainable pace from CS and vVO₂max

The athlete's state estimate supplies two velocity anchors (Section 4 owns their estimation; `research/01` §4.1–§4.2 gives the models):

- **Critical speed (CS)** — the asymptote of the speed–time-to-exhaustion relationship, the highest speed sustainable in a metabolic steady state, estimated from two or more maximal efforts of different durations via the linear distance–time form `distance = CS · t + D′`, where `D′` is the finite distance capacity usable above CS (`research/01` §4.2). CS anchors sustainable pace for efforts up to roughly 30–60 minutes.
- **Velocity at VO₂max (vVO₂max, maximal aerobic speed)** — the running velocity that folds VO₂max and economy into one speed, the practical anchor for 3K–10K, where time to exhaustion is ~4–8 min (`research/01` §4.1).

For a goal race whose predicted duration falls at or below the ~30–60 min steady-state horizon (in practice 5K and 10K for the tuning-target athlete), CS is the primary anchor and vVO₂max corroborates the short end. For a race longer than that horizon (half and full marathon), the race is run *below* CS and Stage 2 supplies the projection; CS is still the anchor the projection starts from. The choice of anchor and the gap between race pace and CS is a function of predicted race duration, resolved in Stage 2.

### 1.4.2 Stage 2 — Project to the goal-race distance with the endurance exponent

For any race longer than the CS steady-state horizon, sustainable pace falls as duration rises. This is encoded with the **Riegel endurance model** (`research/01` §4.3):

> **T₂ = T₁ · (D₂ / D₁)^b**

where `T₁` is a known time over a reference distance `D₁`, `T₂` is the predicted time over the goal distance `D₂`, and `b` is the athlete's **endurance exponent**. An exponent of `b = 1.0` would mean pace is held perfectly across distances; `b > 1` encodes the real slowdown as distance grows.

The reference pair `(T₁, D₁)` is obtained one of two ways, preferring the first when available: (a) directly from a `known_bests` entry or a recent race result close in duration to the goal race — a genuine (time, distance) the athlete has actually run; or (b) synthesized from the Stage-1 CS anchor by evaluating the critical-speed model `distance = CS · t + D′` at a reference duration `t_ref` chosen near the CS steady-state horizon (the system's default is `t_ref = 30 min`, inside the ~30–60 min range CS anchors reliably), giving `D₁ = CS · t_ref + D′` and `T₁ = t_ref`. Route (a) is the higher-confidence anchor and is used whenever a suitable effort exists; route (b) is the fallback when the athlete has no maximal effort near the goal duration. The choice of route is recorded in the pace-target provenance (§1.4.6).

The exponent is individualized when the athlete's own data supports it and held at the population default otherwise, per the governing individualization rule (`research/00` §3.1 and the endurance-exponent register row in Part 3):

- Use the **individual Riegel `b`** estimated from the athlete's own qualifying maximal efforts (from `history` and `known_bests`) **when confidence is adequate** — i.e. when enough quality efforts across a spread of distances exist to identify `b` above the noise.
- Otherwise use the **population default `b = 1.06`** (`research/01` §4.3) **with a widened confidence interval** on the projection.

The implementer must carry the caveat `research/01` §4.3 states: `b` is individual and trainable (durability and long-run volume flatten it), and extrapolating far — e.g. projecting a marathon from only a 5K anchor — systematically under-predicts the longer time for an athlete without distance-specific endurance. The system therefore prefers an anchor close in duration to the goal race, and flags wide extrapolations as lower-confidence.

### 1.4.3 Stage 3 — Adjust for running economy and durability

Stages 1–2 produce a still-air, flat-course, fresh-athlete pace. Two athlete-intrinsic factors modify it before the course and environment are applied.

**Running economy.** Economy is the oxygen (energy) cost of running at a given submaximal velocity; two athletes with identical CS can differ materially in the pace each sustains for a given fractional effort, because a lower cost raises the velocity achievable at any sustainable oxygen uptake (`research/01` §1.4). In this procedure economy enters two ways. First, it is already partly embedded in vVO₂max and in the empirically-anchored CS (both are velocities the athlete actually produced, so they reflect that athlete's economy). Second, an explicit **economy modifier** captures changes the anchors do not yet reflect — most concretely the well-replicated ~4% economy benefit of modern plate-and-foam "super-shoe" footwear (`research/01` §1.4), applied as an equipment modifier when the athlete's race-day footwear differs from the footwear in the efforts the anchors were built from. The economy modifier is expressed as a multiplicative factor on sustainable velocity. **Modeling assumption, flagged under the uncertainty convention:** the source (`research/01` §1.4) supports the ~4% economy *cost* reduction but does not give a mapping from an economy-cost percentage to a velocity/pace percentage. This spec ships the simplest defensible default — an approximately proportional mapping (a 4% reduction in oxygen cost → an on-the-order-of-4% sustainable-velocity gain), applied conservatively — and records it as an assumption, not a measured relationship. An implementer may refine it if a validated cost-to-velocity transfer is adopted. Where the system has no basis to distinguish race-day economy from anchor economy, the modifier is 1.0 (no adjustment) and this is recorded in the provenance.

**Durability.** For the longer races — the marathon above all — the fresh-state anchors overstate late-race pace, because CS, economy, and the pace–HR relationship all drift over prolonged exercise; this drift is *durability* (`research/01` §1.6, an emerging construct explicitly flagged as not yet fully established). The system carries a within-athlete durability estimate (Section 4 derives it from late-run pace/HR decoupling; `research/00` Part 3 durability register row) and applies it as a **duration-dependent decay** on sustainable pace over the back portion of long races — larger for the marathon, negligible for the 5K (`research/01` Table 1). Because the durability evidence base is young, this adjustment is treated as a transparently-derived *corroborating* modifier, not a validated primary term: it is applied, but it is flagged as emerging, and its magnitude is bounded and surfaced in the confidence band rather than presented as precise.

### 1.4.4 Stage 4 — Course and environmental modifiers

The pace from Stages 1–3 is a flat, temperate, still-air, sea-level estimate. Four modifiers correct it for the goal race's actual course and conditions (`research/01` §5). Each is stated here as the rule the implementer applies; each cites the model it rests on.

**Grade (course gradient).** The metabolic cost of running varies with gradient along the asymmetric U-shaped curve Minetti et al. measured: cost is minimized at a slight downhill (roughly −10% to −20% grade) and rises steeply on uphills, and — critically — the time lost climbing is not fully repaid on the equivalent descent, so a net-flat rolling course runs slower than a truly flat one (`research/01` §5.4). The system applies the **Minetti cost-of-gradient curve** point-by-point along the resolved `course_profile`, converting each course segment's gradient into a local pace cost and integrating over the course to yield a single grade adjustment for the race. This is the same grade-adjusted-pace representation used everywhere downstream (`research/00` Part 2, finding 5), applied here to the course rather than to a training run. A flat or missing profile yields a zero grade adjustment (§1.2.1).

**Altitude.** Aerobic capacity falls with altitude as inspired-oxygen partial pressure drops. The system applies the linear decrement `research/01` §5.2 gives: **≈ 6–7% loss of VO₂max per 1000 m of ascent above ~600–700 m**, attenuated by any acclimatization the athlete is modeled to have (the acclimatization state, when present, comes from the athlete's modeled profile in Section 4; absent an explicit estimate the modifier assumes no acclimatization — the conservative, larger-penalty default). The modifier uses the course mean altitude (`expected_conditions.altitude_m`, or the mean of `course_profile`). Below the ~600–700 m threshold the altitude modifier is 1.0. The decrement is on aerobic power and is translated into a pace penalty through the same physiology-to-pace chain (a reduced effective VO₂max/CS).

**Heat and humidity.** Performance is optimal in cool conditions and degrades as heat load rises, with humidity compounding the effect by impairing evaporative cooling; the stress is best captured by a wet-bulb-globe-style index combining temperature and humidity rather than by dry-bulb temperature alone (`research/01` §5.1). The system combines `temperature_c` and `humidity_pct` into a heat-stress index and applies a slowing that **grows with both the heat excess above the cool optimum (roughly 5–10 °C for fast runners, with meaningful slowing setting in above ~15 °C air temperature) and the race duration**, and — a point that matters specifically for the tuning-target amateur — **penalizes slower runners more than faster ones**, because they are on course longer and accumulate more heat load (`research/01` §5.1). The heat modifier is therefore a function of the index, the projected race duration, and the athlete's pace, not of temperature alone.

**Wind.** The energy cost of overcoming air resistance rises with the *square* of the relative air velocity (`research/01` §5.3). Because the cost is quadratic, a headwind costs more than an equal tailwind returns, so any out-and-back or looped course nets a wind penalty even when the wind is "even" over the lap. The system applies a **quadratic wind cost** from `wind_speed_ms` and `wind_direction`, netted over the course geometry: with a known direction it integrates the head/tail/cross components along the course; with direction absent it applies the conservative net-headwind-dominant penalty (§1.2.2). Still air (no `wind_speed_ms`) yields a wind modifier of 1.0.

### 1.4.5 Stage 5 — Composing the modifiers

The four Stage-4 modifiers must be combined into one adjustment, and the composition rule is a decision the source research does not settle: each of `research/01` §5.1–§5.4 characterizes its modifier *in isolation*, and none addresses combining all four simultaneously. This specification therefore ships a default and flags it as a decision made under uncertainty, per the project's uncertainty-explicit convention (the spec outline's cross-cutting conventions, which direct such choices to be registered in `research/00` Part 3).

**Shipped default.** The modifiers compose **multiplicatively** on sustainable velocity — each is a factor near 1.0, and the combined adjustment is their product — applied in the fixed order:

> **altitude → heat/humidity → wind → grade**

The ordering rationale: altitude and heat/humidity act on the athlete's aerobic *capacity* (they lower the effective VO₂max/CS the whole pace chain is built on), so they are applied first, to the physiological anchor; wind and grade are *mechanical* costs imposed on top of whatever capacity remains, so they are applied second, to the capacity-adjusted pace. Within each pair the more capacity-like effect precedes the more mechanical one. Multiplicative (rather than additive) composition is chosen because each modifier is naturally a proportional cost on velocity and because it keeps each factor bounded and independently interpretable in the provenance trace.

**Assumption and its risk.** Multiplicative composition assumes the modifiers are approximately independent — that, e.g., heat does not change the *shape* of the grade cost. This is an engineering approximation, not a measured interaction model; real interactions exist (heat plus a long climb is plausibly worse than the product of the two). The default is defensible and transparent, but it is a default. **This choice is a candidate for back-porting into the `research/00` Part 3 decision register** so the register stays the single reconciliation point for cross-cutting choices; until it is, it lives here as a section-local uncertainty flag. It is recorded as an open item in the development plan and does not block use of this section.

### 1.4.6 The pace-target output

The procedure returns a structured **pace target** object — the terminal quantity the whole system optimizes — carrying not just the number but its uncertainty and how it was produced, because explainability is a first-class constraint (`research/00` §1.6) and low confidence must be surfaced, not hidden as false precision (`research/00` Part 2, finding 8).

| Field | Type | Units | Notes |
|---|---|---|---|
| `expected_avg_pace` | pace | seconds per kilometre | The predicted average pace over the full goal-race distance, under the race's course and expected conditions. The success metric. |
| `expected_finish_time` | duration | seconds | `expected_avg_pace × (distance_m / 1000)` — note the unit conversion: pace is per-kilometre, `distance_m` is metres. Provided for athlete-facing convenience. |
| `confidence` | confidence descriptor | band + qualitative level | A prediction interval on `expected_avg_pace` plus a low/medium/high label. Widens with: thin or stale history, cold-start state, wide endurance-exponent extrapolation, defaulted course profile, forecast (not firm) conditions, and the durability/composition uncertainty flags. |
| `provenance` | trace | structured record | The full derivation: which anchor was used (CS vs vVO₂max), whether `b` was individual or the 1.06 default, each economy/durability/environmental modifier's value and whether it was measured or defaulted, and the composition order applied. Enables the system to explain the target on demand (Section 9). |
| `assumptions` | list | flags | Every default and uncertainty flag that fed this target: flat-profile fallback, still-air assumption, emerging-durability flag, multiplicative-composition flag, cold-start flag. Surfaced rather than buried. |

Two properties of this output are load-bearing downstream. First, it is **recomputed, not fixed**: as history accrues, as the state estimate updates, and as the race-day forecast firms, the pace target is regenerated and its confidence tightens — it is a live projection, not a one-time computation at program start. Second, it is the object the athlete's optional `goal_pace_target` is compared against: a large and growing gap between the system's projection and the declared goal is exactly the signal that triggers the system to *propose* (never impose) a goal-contract change (`research/00` §1.9).

When the system does propose a change to close a critical projection-vs-goal gap, it follows a **fixed order of remedy, least-disruptive first** (`research/00` §1.9, the decision authority for this ordering):

1. **Primary — a revised goal pace** that the current projection can support, keeping the existing `race_date`. Adjusting the target for the same race disrupts nothing outside the training plan, so it is the first-line remedy.
2. **Secondary — a change of the goal race date** (`race_date`), buying more training time, offered only as the costlier alternative for athletes for whom moving the date is feasible — because a date change means re-entering and re-paying for a race and rearranging travel and life around a new day.

The system does **not** propose changing the race distance (`distance_m`) to close the gap — it is treated as fixed by the athlete's chosen event. This ordering governs only *what the system proposes and in what order*; which field the athlete ultimately moves is entirely theirs, and any goal-contract change takes effect only as an athlete-supplied input (`research/00` §1.9). The pace target is thus both the optimization objective for the plan (Section 5 onward) and the tracking quantity for the goal contract.

---

## 1.5 What this section hands to the rest of the system

Section 1 establishes the contract the closed loop runs on: a bounded-but-general scope (a tuning target, not a runtime limit, §1.1); a structured goal-race input whose course and conditions are modeled rather than assumed away (§1.2); a structured athlete input that is the substrate for state estimation but is not itself estimated here (§1.3); and a fully specified procedure that converts a physiological state estimate into an expected average race pace with confidence and provenance (§1.4). Three explicit hand-offs carry work to later sections and must be honored: **course-profile ingestion** (source formats, resampling, interpolation flags) is Section 2's; **normalizing historical training data** into the canonical raw-stream schema is Section 2's; and **estimating the athlete's physiological state** from that normalized history — the input Stage 1 of §1.4 consumes — is Section 4's. Section 1 owns the objective and the inputs; the sections that follow own the machinery that turns raw data into the state estimate this section's pace target depends on.

---

*Open item logged for this section (see `spec_development_plan.md`): the environmental-modifier composition order and multiplicative form (§1.4.5) is a decision taken under uncertainty and is a candidate for back-porting into the `research/00` Part 3 decision register. It does not block use of this section.*
