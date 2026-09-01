# Future Directions — Ideas for Later Iterations

## Purpose and status

This document is a holding place for ideas that should shape the **next**
iteration of research and specification — the one that follows after the
current specification has been turned into a working product and tested with
real athletes. Nothing here is a requirement for the current specification
effort, and none of it should influence the models, formulas, schemas, or
decision logic being defined in `spec/` right now. If real-world testing of
the first product is successful, these ideas become candidate inputs to the
second round of research and design.

Entries here are deliberately loose. They are prompts for future
investigation, not settled decisions. When one of them is picked up, it should
go through the same discipline the project applies everywhere else: research
first, cite the science, be explicit about uncertainty, and only then specify.

## Ideas

### Chronic injury and long-term physical impairment as a planning input

Let an athlete declare a chronic injury or other long-term physical impairment
that constrains how they can train and must be accounted for in planning. This
is distinct from the acute, transient injury and recovery signals the current
spec already contemplates: it is a durable, athlete-declared constraint that
persists across the whole program rather than a state the system infers and
expects to resolve. Future research would need to cover how such constraints
translate into hard limits and soft preferences on workout selection,
volume, intensity, and progression — for example, which physiological
determinants can still be targeted and by what alternative stimulus when the
obvious workout is unavailable — and how the system should reason about the
trade-off between respecting the constraint and still improving expected
average race-day pace.

### Preferred running locations and routes as a planning input

Let an athlete declare the location(s) or route(s) they run most often because
of time constraints, convenience, or other real-life factors. These routes may
not be ideal for every workout type — a rolling trail is poorly suited to a
flat tempo effort, a short loop constrains long-run logistics — but they are
where the training will actually happen, so the planner should factor them in
rather than prescribe workouts the athlete cannot realistically execute where
they run. Future research would need to consider how to represent a route
(distance, elevation profile, surface, safety, available segments) and how to
match workout intent to the routes available, including when to prescribe a
compromise workout on a familiar route versus flagging that a given session
needs a different venue.

### Intra-workout heart-rate recovery between intervals as a metric

Track **how quickly heart rate returns toward a baseline during the recovery
periods between hard intervals**, and use that recovery rate as a metric of
fitness and within-session readiness. Post-exercise heart-rate recovery
kinetics are a recognized marker of autonomic (parasympathetic) reactivation
and cardiorespiratory fitness, and faster between-interval recovery plausibly
tracks improving fitness and better freshness on the day. This is attractive
for the project because it is derived from a **raw signal already recorded**
(the HR stream), fitting the raw-over-derived philosophy, and because it reads
the athlete's state *during* the very session the plan is prescribing rather
than only before or after it.

It is distinct from constructs the current spec already defines and should not
be conflated with them: the optional on-device intra-workout loop (`spec/06`
§6.2.5) uses performance-based *cutoffs* on pace/HR drift to end junk reps, not
recovery kinetics; pace–HR decoupling (`spec/03` §3.6.1) compares the first and
second half of a *steady* effort; and the HRV trend (`spec/03` §3.7) is built
strictly from resting/overnight RR and deliberately excludes intra-workout RR.
None of them measures the between-interval HR fall-off.

The current research base in the project does not cover this, which is why it
sits here rather than in the spec. When picked up, future research would need
to establish: whether between-interval HR recovery is a valid and stable
within-athlete marker of fitness/readiness (as opposed to noise); the
confounders that must be normalized out before it is comparable session to
session (absolute work intensity reached, the prescribed rest duration and
whether it is active or passive, ambient heat and dehydration, and the pace/HR
data source and its sampling rate); and a transparent, reproducible formula
(for example, the HR decay over a fixed recovery window, or a fitted recovery
time constant) consistent with the project's derived-metric conventions. A
practical prerequisite is reliable **interval/recovery segmentation** of the
workout, and — because the richest form needs beat-resolution recovery data —
attention to whether the recovery-phase HR/RR is captured at adequate fidelity
(chest-strap versus wrist PPG, and the post-session versus on-device data
routes discussed in `research/02`).

### Human-coach specification of system parameters (coach-in-the-loop)

Add an optional **coach-in-the-loop** mode in which a qualified human coach can
directly specify, confirm, or override parameters the v1 system currently
ships as transparent defaults derived under scientific uncertainty. The v1
design is deliberately fully autonomous (`research/00` §1.8): where the science
does not pin a value down, the system adopts a documented default, flags it,
and tunes it from the athlete's own data over time. That is the right posture
for a self-serve product, but several of those determinations are exactly the
kind of judgement an experienced coach makes well from context the system does
not have — a lab test result, knowledge of the athlete's training history
before the device era, an eye for an individual's response. A coach-in-the-loop
mode would let a coach set these directly (with the system still enforcing its
safety guardrails and still learning from data), turning the shipped default
into a *fallback for the un-coached athlete* rather than the only path.

Future research/design would need to define, for each coach-settable parameter,
the interface (what the coach sets and in what units), how a coach override
interacts with the system's own continuous re-estimation (does the coach value
pin the parameter, seed it, or bound it?), how confidence is represented when a
value is coach-asserted rather than data-derived, and how conflicts between a
coach setting and a strong contrary data signal are surfaced and resolved. The
autonomy boundary of `research/00` §1.8–§1.9 is the natural place to add a
third, *optional* authority (the coach) alongside the athlete-owned goal
contract and safety pathway — with the important constraint that a coach input
must never be able to disable a safety hard-flag.

The parameters flagged during the Phase-2 spec build as the leading
coach-specification candidates — each currently a flagged default in the spec,
each cross-referenced to its open item in `spec_development_plan.md` — are:

- **The non-exercise cold-start fitness seed** (`spec/04` §4.4.2). The v1 seed
  is built from open published estimators (Uth–Sørensen heart-rate-ratio and a
  Jackson-form non-exercise regression; now cited in `research/00` §3.2). A
  coach can often supply a far better day-one fitness estimate from a recent
  race, a lab VO₂max, or direct knowledge of the athlete — a natural override.
- **The aerobic-threshold (LT1) / Zone-1–Zone-2 boundary** (`spec/05` §5.4.2,
  and see the LT1 entry below). The v1 default derives it as a fraction of the
  functional-threshold anchor; a coach who has an athlete's lab or field LT1 can
  set it directly.
- **The determinant-addressability scoring weights** (`spec/05` §5.7.2) — how
  the planner ranks which physiological determinant a training block should
  target. The multiplicative default (gap × trainability × time-to-race ×
  confidence) is now ratified in `research/00` Part 3, but a coach's read of
  *which* limiter to attack, and when, is a classic coaching judgement.
- **The base/build/peak phase-length split** (`spec/05` §5.5.1). The v1 default
  (~50/30/20 of non-taper weeks) is now ratified in `research/00` Part 3, but
  the shape of a macrocycle is something coaches routinely tailor to the athlete
  and the race.
- **Recovery-week depth and the race-day target form band** (`spec/07` §7.2.2,
  §7.4.1). Both are now ratified spec defaults (`research/00` Part 3), and both
  are strongly individual — a coach who knows an athlete peaks best a little
  fresh or a little loaded, or who fades on a shallow down week, is well placed
  to set them.

None of this changes the v1 spec; it is recorded here so the coach-in-the-loop
option and these specific override points are researched and designed together
in the next iteration rather than bolted on piecemeal.

### An independent, field-identifiable aerobic-threshold (LT1) determinant

The v1 state model publishes no independent aerobic-threshold (LT1)
determinant, so the plan derives the Zone-1/Zone-2 boundary as a fixed fraction
(~80–88%) of the functional-threshold-velocity anchor (`spec/05` §5.4.2). This
is a safe, transparent default and should stand for v1, but it makes LT1 a
*derived* quantity rather than a measured one, so it cannot move independently
of the threshold anchor even when an individual's aerobic and threshold
ceilings diverge. A future iteration should research promoting LT1 to its own
estimated determinant.

The leading candidate identified during the Phase-2 build is the **DFA-α1
(detrended-fluctuation-analysis short-term scaling exponent) aerobic-threshold
marker**: a DFA-α1 value of **0.75** in the beat-to-beat HR series corresponds
closely to the first ventilatory / aerobic threshold, with very strong lab
agreement in the originating work (r ≈ 0.99 for VO₂ at threshold; mean
difference ≈ −0.33 mL·kg⁻¹·min⁻¹ and ≈ −1.9 bpm for HR — Rogers et al., 2020,
*Front. Physiol.*; single-case field application Rogers et al., 2021, *Front.
Sports Act. Living*; further runner validation Rogers et al./others, 2023,
*J. Sports Sci.*). It is attractive because it is derived from raw RR intervals
the system may already capture, fitting the raw-over-derived philosophy, and
because it is a genuinely independent estimate of LT1 rather than a fraction of
LT2.

It is **not** recommended for v1, for concrete reasons the same literature
raises: DFA-α1 is sensitive to RR-artefact contamination (the originating study
excluded participants with ectopy and tolerated only 0–3% artefact), to device
sampling rate, and possibly to chest-belt-versus-ECG differences; it was
validated in small samples, without female participants, and not for
constant-load or >60-minute efforts. Those are exactly the conditions a field
product cannot control. When picked up, future research would need to establish
the RR-quality gating DFA-α1 requires (which the `spec/02` pipeline already
partly enforces), validate the 0.75 cut-off in the target population including
women, and decide how a DFA-α1 LT1 estimate is blended with (or bounded by) the
threshold-anchored default. This is also a strong **human-coach-specification
candidate** (see the coach-in-the-loop entry above): a coach with a lab or
field LT1 for the athlete can set it directly, side-stepping the measurement
difficulty entirely.

### Structured strength and plyometric supporting work

Give the system a real prescription for **strength and plyometric training** as
a running-economy stimulus, rather than the v1 treatment, which carries it only
as a flagged, non-device-executed recommendation (`spec/05` §5.3, §5.7.3).
Heavy resistance and plyometric work are among the better-supported
non-running routes to improved running economy — a first-class determinant of
race pace — so a fuller treatment is a natural extension. Future research would
need to cover exercise selection and dose, weekly frequency, how strength
blocks are periodized against running blocks (concurrent-training interference,
placement relative to key sessions and the taper), and how the resulting
economy stimulus is represented in the workout schema and credited (if at all)
to the state model's economy proxy. Because none of this is executed or
measured on the running device, it is also a strong **human-coach-specification
candidate** (see the coach-in-the-loop entry): the supporting-work program is
something a coach may prefer to own directly while the system plans the running
around it.

### Heat and altitude acclimation blocks

Give the system an explicit **acclimation-block** capability for a goal race in
hot/humid conditions or at altitude. This is out of scope for the initial
release: v1 deliberately holds Section 1's conservative no-acclimatization
default, treats acclimatization as a plan-scope concern rather than a tracked
daily determinant (`spec/06` §6.9), and records only the binding *taper-timing
constraint* it would impose (`spec/07` §7.8) — namely that because heat/altitude
adaptations decay, an acclimation block must sit close to race day, yet must not
reintroduce the fatigue the taper is shedding. The full capability is deferred
to a future iteration.

When picked up, future research would need to specify the acclimation protocol
and its exposure dosing (active heat exposure, sauna, or altitude/hypoxia
strategies), the timing window relative to the taper that satisfies the §7.8
constraint, and — critically for this project's single success metric — how a
*completed* acclimation block is credited back into the race-pace projection,
i.e. how much of the heat- or altitude-imposed slowdown (`research/01`'s
environmental modifiers) the block is expected to recover. The capability is
jointly homed in plan generation (`spec/05`) and taper timing (`spec/07`). It is
a plausible **human-coach-specification candidate** as well, since the decision
to run a heat or altitude block, and its timing, is often a coach-led strategic
call.

### An overarching model for continuous system improvement

Establish a deliberate framework for the system to get better over time, rather
than treating the first version's metrics and models as fixed. This has at
least two distinct manifestations worth researching separately:

First, **learning from aggregate real-world feedback.** As the system takes on
more athletes, the accumulated data may create an opportunity to extend or
improve the underlying physiological research itself — potentially new derived
metrics, or new models of how humans respond to physical stress and strain
under varying conditions (heat, altitude, terrain, training history, and so
on). This is the possibility that the product becomes a research instrument in
its own right, feeding back into the science the next specification is built
on.

Second, **monitoring the first version's own methods for quality.** The metrics
and models defined in v1 should themselves be measured for veracity,
usefulness, stability, and predictive value against the single success metric
(average race-day pace). The goal is a continuously improving feedback loop and
data pipeline: instrument the derived metrics and adaptation decisions so their
real-world performance can be evaluated, surface where a method is weak or
mis-calibrated, and use that to prioritize what the next iteration should
refine or replace. This is an explicit commitment to treating every v1 formula
as a hypothesis to be validated, not a settled truth.
