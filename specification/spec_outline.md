# Run Coaching System — Phase 2 Specification Outline

*This is the structural contract for the build-ready specification. It fixes the section order, what each section defines, and which research/decision docs each section cites. The spec references the research docs (`research_00`–`research_06`, `decisions_01`) rather than restating them, and conforms to `research_00` as the project's decision authority: every conflict-resolution, parameter default, and freedom-to-operate question cites `00`; every physiological/method derivation cites the relevant mechanism doc. Section order follows the closed loop the system implements: inputs → raw data → derived metrics → state estimate → plan → adaptation → recovery/taper → interface → loop closure.*

*Convention note: throughout, "cites `0X`" means the spec states the rule/formula in full and points to `0X` for the evidence and derivation behind it — it does not re-argue the research.*

---

## Section 1 — Scope, System Inputs, and the Pace Target

**Defines.** The bounded scope (serious-amateur road racer, 5K–marathon, Garmin-first extensible) as tuning target, not a runtime limit. The goal-race input schema (date, distance, course elevation profile, expected environmental conditions: temperature, humidity, wind). The athlete input schema (profile + historical training data). How the race translates into a pace/effort target under the race's specific course profile and conditions — the terminal quantity the whole system optimizes.

**Cites.** `research_00` §1.1 (supreme objective); project instructions (inputs, scope decisions); `research_01` (physiology-to-pace models: CS/vVO2max/Riegel, environmental and course modifiers).

**Depends on.** Nothing — this is the entry point.

---

## Section 2 — Canonical Data Schema and Ingestion

**Defines.** The vendor-neutral canonical raw-stream schema, raw fields first (HR, RR intervals, pace/GPS, cadence, barometric altitude, running dynamics, power where present, all timestamped). The Garmin FIT adapter as the first concrete mapping. The quarantined vendor-derived sidecar namespace (VO2max, Training Status, Training Readiness, Body Battery, Performance Condition — ingested, never fed to a decision). Data-quality gating rules (1 Hz requirement; chest-strap for HRV and at/above-threshold HR; PPG down-weighted and flagged; never HRV from in-run wrist PPG). The ingestion abstraction that lets other vendors be added later without redesign.

**Cites.** `research_02` (measure-vs-estimate, FIT model, sampling/accuracy/failure modes, programmatic access); `research_00` Part 2 findings 1, 6 and the data-quality-gating register row (§Part 3); `research_00` §1.5 (raw-over-derived, quarantine).

**Depends on.** §1 (what athlete/race data must be ingestible).

---

## Section 3 — Derived-Metric Formulas

**Defines, each formula stated in full.** Per-session load (rTSS primary on valid GAP stream, reconciled against HR-TRIMP and sRPE, >25% divergence → mean-fallback, graceful degradation). Fitness/fatigue/form (CTL 42-day, ATL 7-day EWMA, TSB = CTL − ATL). HRV trend (7-day rolling ln rMSSD vs ±0.5·SD(ln rMSSD) smallest-worthwhile-change band — the sample SD of the athlete's own ln rMSSD baseline; the register's "±0.5·CV" as clarified 2026-09-09, `research_00` §5.4). Grade-adjusted pace (Minetti cost-of-gradient curve) as the universal downstream pace representation. Durability (EF/decoupling drift over back third of long runs; within-athlete trend; flagged emerging). ACWR as advisory context/spike flag only (wide ~0.8–1.5 band, never a hard gate).

**Cites.** `research_04` §4 and `research_05` §2 (load models, PMC, HRV, processing pipeline); `research_00` Part 3 register rows (per-session load, fitness/fatigue constants, HRV gate, ACWR, durability) and findings 5; `research_01` §5.4 (Minetti).

**Depends on.** §2 (raw streams these consume).

---

## Section 4 — Physiological State Model

**Defines.** The owned determinant profile — critical speed and D′, vVO2max, functional threshold pace, EF economy proxy, durability, individual endurance exponent (Riegel *b*) — each carrying a trend and a confidence. The cold-start estimator (owned, transparent, non-exercise VO2max seed → hand-off to raw-data critical-speed estimate as history accrues; population-default fallback with widened intervals). The individualization governing rule (individualize a parameter only when identifiable from the athlete's own data *and* backed by enough quality data to beat the population default). How low confidence widens the guardrails.

**Cites.** `research_05` §3 (state model, confidence); `research_00` §3.1 (individualization rule, resolved), §3.2 (cold-start, resolved — owned estimator is a Phase-2 requirement), Part 2 findings 2 and 8; `research_01` §4.3 (endurance exponent), §1.6 (durability).

**Depends on.** §3 (the derived metrics that populate and update the state estimate).

---

## Section 5 — Training-Plan Generation

**Defines.** The machine-representable workout typology and workout data representation. Periodization structure (base/build/peak/taper). Intensity distribution (pyramidal-leaning base → polarizing through build/peak, ~80% easy by time, Z2/Z3 split as a phase- and distance-dependent tunable parameter). Relative-anchored pace prescription (all paces stored relative to current state estimate so zones update automatically when the model updates). How workouts target specific physiological determinants.

**Cites.** `research_04` §2–3 (periodization, intensity distribution, workout typology); `research_00` Part 2 finding 4 (relative anchoring) and the intensity-distribution register row; `research_01` (determinant-to-stimulus mapping).

**Depends on.** §4 (the state estimate paces anchor to; determinants workouts target).

---

## Section 6 — Adaptation Logic

**Defines.** The five-timescale nested loop (long-term/mesocycle, short-term/weekly, recent-workout, day-of readiness, optional intra-workout), each with inputs, triggers, actions, guardrails. The arbitration ladder that composes them into one daily prescription (safety → readiness → short-term guardrails → recent re-anchoring → long-term ambition). Subjective/objective conflict resolution (more conservative reading wins; trends not single readings). The down-regulate-freely / up-regulate-cautiously asymmetry. The autonomy posture (apply-and-notify).

**Cites.** `research_05` §5 (five-timescale logic, arbitration); `research_00` §1.2 (arbitration ladder — the constitution), §1.4 (subjective/objective conflict), §1.7 (asymmetry), §1.8 (autonomy), Part 2 finding 3; `research_03` (subjective instruments, ACWR critique).

**Depends on.** §3, §4, §5 (metrics, state, and plan this logic reads and modifies).

---

## Section 7 — Recovery and Taper

**Defines.** Recovery-week structure and mandatory recovery guardrails. The taper (~2 weeks, ~50% volume cut, exponential decay, intensity maintained, frequency mostly held), then individualized by chronic load and distance. How taper is personalized by the adaptive system so the athlete arrives optimally recovered and peaked.

**Cites.** `research_04` §7 (taper); `research_00` Part 3 taper register row; `research_05` (fatigue/form dynamics into peak).

**Depends on.** §6 (taper is an adaptation-driven mesocycle behavior).

---

## Section 8 — Conversational Coach Interface and Autonomy Boundary

**Defines.** The natural-language I/O layer on top of the deterministic closed loop: LLM translates athlete free-text into structured engine inputs (life constraints → weekly loop; injury/soreness/subjective reports → the `research_03` instruments) and turns the decision log into plain-language explanation. The hard boundary: chat is not a second adaptation path and not a gating mechanism; safety hard-flags fire deterministically regardless of framing; the only athlete-owned matters are the goal contract and the safety pathway.

**Cites.** `decisions_01` (conversational coach interface); `research_00` §1.8–1.9 (autonomy resolved, plan-vs-goal ownership; supersedes the `decisions_01` "Bonus" wording), §1.5, §1.6.

**Depends on.** §6 (the engine whose inputs/outputs this layer wraps).

---

## Section 9 — Loop Closure, Decision Log, and Explainability

**Defines.** The end-to-end trace: raw data → derived metrics → state estimate → plan change, as one closed loop, showing where each prior section plugs in. The decision-log schema: every applied adaptation logged with its inputs and the rule that fired, promoted to a first-class queryable record. How the system explains any change on demand. The confidence-surfacing behavior (low confidence shown, not hidden as false precision).

**Cites.** `research_00` §1.6 (explainability, decision log), Part 2 finding 8; `research_05` (pipeline end-to-end); `decisions_01` (decision log as queryable record).

**Depends on.** All prior sections — this is the closure that ties them together.

---

## Cross-cutting conventions (apply to every section)

- **Authority.** Where any mechanism doc and `research_00` appear to differ, `research_00` governs and the spec notes it.
- **Prose first.** Clear prose throughout; tables/lists reserved for genuinely enumerable content (data fields, workout parameters, metric definitions).
- **Uncertainty explicit.** Where a section depends on an unsettled scientific question or a proprietary metric, the spec states the default chosen and the assumption behind it, per `research_00` Part 3.
- **Freedom-to-operate.** Section content honors the Part 4 guardrails (open public-domain math; avoid the specific protected constructions; single-ecosystem FIT-driven; learn-the-teaching-build-a-different-construction).
- **Definition of done.** A section is done when a competent implementer could build that part with no further physiology/device/coaching research — every model, formula, field, and decision rule is specified or cited to a pointed source.
