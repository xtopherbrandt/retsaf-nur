# Specification Review — Findings Log

*Chris's review of the build-ready specification. This is a running log of findings raised during review, captured so they can be resolved **holistically** — reconciled against each other and the decision authority (`research/00`) in one pass — rather than patched piecemeal. Each finding records the observation and the proposed direction; nothing here is applied to the spec until the batch is worked through. Status values: **open** (captured, not yet resolved), **actioned** (a disposition has been carried out), **resolved** (change applied to the spec), **won't-fix** (considered and declined, with reason).*

---

## Finding 1 — Gap remedy should propose a change of goal *pace*, not goal *date*

**Locus:** `spec/01-scope-inputs-pace-target.md` §1.4.6, which defers to the decision authority for the goal-contract change (the underlying rule is GOAL-02 in `research/00`).

**Status:** resolved — ordering applied across the decision authority and every spec locus that surfaces the proposal (2026-08-31).

**Observation.** When the gap between the system's projected race pace and the athlete's declared goal pace grows critically large, §1.4.6 has the system *propose* (never impose) a "goal-contract change." As written, the goal contract bundles `race_date`, `distance_m`, and `goal_pace_target` together, and the natural reading — reinforced by the periodization framing elsewhere — is that moving the race date is a first-class remedy for closing the gap.

**Why this is wrong.** Changing the race date is one of the *hardest* things to ask of an athlete: races are entered, paid for, travel is booked, and life is arranged around a fixed date. Adjusting the goal *pace* (i.e. accepting a more realistic target for the same race) is far less disruptive and is the appropriate first-line remedy. A date change may still be possible and worth surfacing, but it should be a **secondary** option, not co-equal with a pace adjustment.

**Proposed direction.** Establish an explicit *ordering of remedies* for a critical projection-vs-goal gap:

1. **Primary — propose a revised goal pace** that the current projection can support, so the goal becomes achievable on the existing date.
2. **Secondary — propose a change of goal race date** (more training time), offered as an available-but-costlier alternative for athletes for whom it is feasible.

This ordering should be stated wherever the gap-triggered proposal is defined — §1.4.6 and, as the decision authority, `research/00` GOAL-03 — so the two stay consistent. (Note: `distance_m` is a third contract field; the review is not asking to make distance change a proposed remedy — flagging only that the ordering language should be precise about *which* fields the system proposes to move and in what order.)

**Resolution (applied 2026-08-31).** The ordering of remedies is now stated as the decision authority in **`research/00` GOAL-03 and GOAL-04** — primary: a revised goal pace on the existing date; secondary: a later race date; `distance_m` explicitly excluded as a proposed remedy; the ordering governs only what the *system proposes*, not which field the athlete may elect to move. A reconciliation note was added to `research/00` (now GOAL-06). The same ordering was then stated at every point the gap-triggered proposal is surfaced, all deferring to GOAL-03:

- **`spec/01` §1.4.6** — the pace-target output now states the primary/secondary ordering and the distance exclusion where it defines the gap-triggered proposal.
- **`spec/06` §6.8** (goal-contract exception) — the goal-contract bullet now states the pace-first ordering when Section 6 proposes a change.
- **`spec/06` §6.4.4** (return-to-run) — the injury-layoff proposal previously listed "a later race, a revised target pace" (date first); reordered to a revised target pace first, a later race date as the costlier secondary option, never a distance change. This was exactly the "periodization framing elsewhere" the observation warned about.
- **`spec/08` §8.5.2** (conversational coach) — a system-*initiated* proposal now leads with a revised pace and offers a later date as secondary; athlete-*initiated* goal-change requests (pace or date) remain valid inputs the parser routes to the goal-contract hand-off, since the ordering constrains only what the system proposes.

Cross-references were verified consistent: no remaining locus frames a race-date change as a co-equal or first-line gap remedy.

---

## Finding 2 — Intra-workout HR recovery between intervals is unaddressed → routed to future directions

**Locus:** absent across the research and spec. Nearest existing (and distinct) constructs: the optional on-device intra-workout loop (`spec/06` §6.2.5, which does performance-based *cutoffs* on pace/HR drift, not recovery kinetics); pace–HR decoupling (`spec/03` §3.6.1, first vs. second half of a steady effort); and the HRV trend (`spec/03` §3.7), which is built strictly from resting/overnight RR and explicitly *excludes* intra-workout RR.

**Status:** resolved as won't-fix-for-now — disposition is to record it in `future/future-directions.md` rather than specify it now; confirmed present there ("Intra-workout heart-rate recovery between intervals as a metric").

**Observation.** The spec has no metric for **intra-workout heart-rate recovery** — practically, how quickly HR returns toward a baseline during the rest between hard intervals. This is a recognized marker of autonomic function and cardiorespiratory fitness, and faster between-interval recovery plausibly tracks improving fitness and freshness within a session. It is a raw-signal-derived metric (HR stream only), which fits the project's raw-over-derived philosophy well.

**Disposition (per Chris's instruction).** No current research in the project covers this, so it is recorded in `future/future-directions.md` as a prompt for a later iteration rather than being specified now. When picked up it should go through the project's normal discipline — research and cite the HR-recovery-kinetics science first (validity as a fitness/readiness marker, confounders such as absolute intensity, ambient heat, and rest duration), then specify a transparent formula — before any spec change. **Confirmed (2026-08-31):** the entry exists in `future/future-directions.md` with the distinct-from-existing-constructs note and the research prerequisites intact; no research or spec change is warranted, consistent with the instruction to keep it out of the current spec.

---

## How this log is worked

When the review is complete, the findings are triaged together: overlaps reconciled, any conflicts between a proposed change and `research/00` (the decision authority) surfaced, and a single consolidated set of edits planned so the spec's cross-references stay coherent. Only then are changes applied and statuses moved to **resolved**.

**Batch status (2026-08-31): worked and closed.** Both findings are resolved. Finding 1 was applied as a single consolidated edit set — the ordering fixed in the decision authority (`research/00` GOAL-03) and then stated at every spec locus that surfaces the proposal (`spec/01` §1.4.6, `spec/06` §6.8 and §6.4.4, `spec/08` §8.5.2) — with cross-references verified consistent. Finding 2's disposition (route to `future/future-directions.md`, do not specify now) was confirmed carried out. No conflicts with `research/00` remained after the batch.
