# Design Decisions & Governing Principles

## Glossary

- **T-01 judgeable** IS said of a dataset that is established (T-03) and holds at least `min_window_readings` distinct local days in the judged week, as `is_judgeable` in `hrv_trend.py` tests it.
- **T-02 selected** IS said of the dataset `select_dataset(...).selected` returns, the first judgeable dataset in fidelity order that the recency gate did not skip; the dataset a response shows when nothing is selected is presented (`presented_by`), not selected.
- **T-03 established** IS said of a dataset whose own clipped baseline holds `n >= min_baseline_readings` distinct local days (`HrvDataset.established`).
- **T-04 skipped / struck / stale** IS the recency gate's vocabulary: struck names the set `_recency_struck` returns over its candidates, skipped names a judgeable dataset in that set, and stale survives only in the enum value `higher_fidelity_skipped_stale` and in a stale HRV band, one whose readings all predate the judged period by more than 28 days.
- **T-05 tier** IS a value of `hrv_source_tier`: `chest_strap_raw`, `health_snapshot` or `health_api_overnight` (`TIER_FIDELITY`); HRV Status is a quarantined sidecar metric and not a tier, and a device is not a tier.
- **T-06 dataset** IS one tier's readings for one target date, with its own clipped baseline window, HRV SWC band, `n`, `established`, reset fields and `withheld` flag (`HrvDataset`).
- **T-07 band** IS the HRV SWC band only, `Band(mean, half_width, lo, hi, floored)` over a dataset's ln rMSSD baseline with `half_width = max(0.5 · SD(ln rMSSD), 0.01)`, and the other quantities once given this HRV term are the ACWR range, the TSB target range, the CTL-rise range and the tolerance bracket.
- **T-08 baseline** IS the HRV term for a dataset's readings inside its dataset baseline window (`HrvDataset.baseline`), and the centre of its HRV SWC band is the baseline mean.
- **T-09 baseline window** IS one of three named HRV windows: the nominal baseline window `[D-66, D-7]` (`baseline_window`); the series baseline window `[max(D-66, gap resumption), D-7]` (`HrvSeries.baseline_window`), which the recency gate reads; and the dataset baseline window (`HrvDataset.baseline_window`), which establishment reads.
- **T-10 judged week** IS the local days `[D-6, D]` (`judged_window`), and the bare word window is never used for it.
- **T-11 withhold** IS the verdict withholding `verdict_withheld` decides and sets as `HrvDataset.withheld`, served as `week_not_representative`; every other absent verdict is unavailable (T-33), with its `unavailable_reason`.
- **T-12 silence / coverage gap / hole / days behind** IS the count of whole local days strictly between two readings (`_silence_between`); a coverage gap is a series silence of more than 21 days, a hole is one dataset's silence of more than 21 days inside its window, and g days behind in `last_read` is a silence of g−1.
- **T-13 sustains** IS the property `sustained_tier` tests, the highest-fidelity tier with at least 14 distinct days in a window, and it is used only by clause (b) of the era rule; clause (a) is the count in the series baseline window.
- **T-14 candidate** IS a judgeable dataset the recency gate may skip; the era rule's count-below-14 half is named the boundary-existence half, not candidacy.
- **T-15 era boundary** IS the `EraBoundary(first_day, reported)` that `_era_boundary` returns for a dataset.
- **T-16 reset** IS a reported `reset_reason` in {`coverage_gap`, `tier_change`} together with its `reset_on`, and re-establishment names only the reset after a coverage gap.
- **T-17 clip** IS moving the first day of a dataset's window and excluding every earlier reading as `before_reset: <reason>` (`_exclude_before_reset`), in three kinds (gap, era and hole) that compose as the latest first day.
- **T-18 reported** IS said of a reset whose `reset_reason` and `reset_on` are non-null for that dataset.
- **T-19 strays** IS the readings `_isolated` tests and the stray set `_era_boundary` counts.
- **T-20 return / carrier** IS a return, a dataset receiving judged-week readings after a silence while another dataset carried the series, and the carrier is that other dataset; `c` is the number of carrier readings inside the return week, a sweep axis.
- **T-21 fidelity** IS the ordinal rank in `TIER_FIDELITY`, the only quality term Section 3 applies; a confidence weight is a Section 6 quantity that Section 3 does not compute.
- **T-22 disagree** IS what `disagreed_with` reports, a dataset whose judged-week mean reads on the other side of its own HRV SWC band from the selected dataset's, served only when a verdict is conferred.
- **T-23 count** IS a number of distinct local days in the athlete's time zone, so a reading is one day's kept capture and a capture is a stored row.
- **T-24 forbidden direction** IS asserting `hrv_normal` on evidence the system reports as insufficient, or while any dataset reported in the same response reads below its own HRV SWC band, whether or not that dataset is judgeable.
- **T-25 rate** IS OPEN (IDEA-088).
- **T-26 input tiers** IS the three tiers of T-05 that feed the trend, stated as three input tiers plus the quarantined HRV Status classification rather than as four tiers.
- **T-27 rung / loop** IS a rung, a position on the arbitration ladder (ARB-01), and a loop or timescale, one of ARCH-03's five; the ladder arbitrates between loops, but its rungs are not the loops.
- **T-28 single-ecosystem** IS one vendor ecosystem per athlete's data at a time, and multi-vendor ingestion is not multi-device coordination.
- **T-29 goal contract** IS the three athlete-owned fields {`goal_pace_target`, `race_date`, `distance_m`}.
- **T-30 safety override / safety pathway** IS the override, the system's deterministic stop-or-escalate (ARB-02), and the pathway, the athlete-facing instructions that override issues (AUT-02).
- **T-31 D, R, R+k, k₃** IS D, the target local date; R, the first reading of the new era after a tier change or of the resumption after a coverage gap; R+k, the k-th local day after R; and k₃, the offset of the returning dataset's third day, as HRV-33 uses it.
- **T-32 resolved tier / rule 1–4** IS retired vocabulary (HRV-44), replaced by the selected or presented dataset, with rule 4 surviving only as the name of the era rule (HRV-38).
- **T-33 unavailable** IS the verdict value `hrv_unavailable`, always served with an `unavailable_reason` (HRV-28).

## Part 1 — Principle hierarchy and tie-breakers

### 1.1 The supreme objective

**PRIN-01.** The system MUST optimize expected average running pace over the full distance of the goal race, under that race's course profile and expected conditions, and this pace IS its terminal value.
Scope: every plan, adaptation, taper and recovery choice the system makes.
Not: a pace over any distance other than the goal race's.
Pinned: none

**PRIN-02.** Every design choice MUST be justified by whether it raises that expected average pace, and a trade-off that is otherwise unclear MUST be resolved in its favour.
Scope: plan structure, adaptation, taper and recovery choices alike.
Not: a conflict the arbitration ladder (ARB-01) already resolves, which is not otherwise unclear.
Pinned: none

### 1.2 The arbitration ladder

**PRIN-03.** The pace objective MUST be maximized subject to arriving healthy and adapted, so the constraints that protect health and adaptation MAY override the day's ambition.
Scope: the placement of the pace objective on the lowest rung of the arbitration ladder (ARB-06).
Not: a reading of that low rank as a contradiction of PRIN-01 (the terminal value).
Pinned: none

**ARB-01.** Conflicting adaptation signals on a day MUST be resolved in this strict order, highest first: (1) safety/injury override, (2) day-of readiness in the conservative direction, (3) short-term guardrails, (4) recent-workout re-anchoring, (5) long-term ambition.
Scope: every day on which two adaptation signals or loops disagree.
Not: a reordering of the rungs, or a shortcut past one.
Pinned: none

**ARB-02.** If injury risk is high or a hard flag fires (bone-stress-injury pattern, night pain, focal bony tenderness, systemic illness, RED-S indicators), the system MUST stop or escalate regardless of the pace objective.
Scope: the first rung of the arbitration ladder (ARB-01), the safety override.
Not: the athlete's clinical action and return-to-run clearance, which AUT-02 leaves to the athlete.
Pinned: none

**ARB-03.** A red readiness gate MUST downgrade the session even when the plan wants a hard day.
Scope: the second rung of the arbitration ladder, day-of readiness in the conservative direction.
Not: turning an easy day into a hard one on a green day, which PRIN-13 forbids.
Pinned: none

**ARB-04.** Ramp-rate caps, monotony limits and mandatory recovery weeks MUST bound what the long-term ambition may demand.
Scope: the third rung of the arbitration ladder, the short-term guardrails.
Not: a bound on the safety override or on day-of readiness, which rank above this rung.
Pinned: none

**ARB-05.** Recent-workout re-anchoring MAY update threshold, CS and paces and tweak the next one to three sessions, within the bounds that the rungs above it set.
Scope: the fourth rung of the arbitration ladder.
Not: a change that exceeds a bound set by a higher rung.
Pinned: none

**ARB-06.** Long-term ambition MAY drive mesocycle emphasis and training-intensity distribution, and it MUST be deliberately the lowest priority when it conflicts with any rung above it.
Scope: the fifth rung of the arbitration ladder, the pace objective.
Not: a reading of this low rank as a contradiction of PRIN-01, which PRIN-03 explains.
Pinned: none

**ARB-07.** Hard flags MUST fire deterministically, whatever the chat framing.
Scope: every hard flag that ARB-02 names, however the surrounding conversation is framed.
Not: a chat-originated path that suppresses a hard flag.
Pinned: none

### 1.3 The meta-rule

**PRIN-04.** Faster, more conservative, safety-relevant signals MAY always veto slower, more ambitious ones, never the reverse, and this rule generates the arbitration ladder (ARB-01) and extends to any conflict the ladder does not name.
Scope: every conflict between two signals or two loops, named by the ladder or not.
Not: a veto by a slower, more ambitious signal over a faster, more conservative one.
Pinned: none

### 1.4 Conflict resolution between subjective and objective signals

**PRIN-05.** When subjective and objective signals disagree, or the state estimate is low-confidence, the more conservative reading MUST win, but this rule MUST NOT arbitrate between two per-tier HRV datasets, where the fidelity rank of HRV-14 decides.
Scope: a disagreement between the subjective and the objective axis, or a low-confidence state estimate.
Not: the choice between two objective HRV instruments, which is selection by fidelity rank (HRV-14).
Pinned: none
Why: decision C06 states that this rule does not arbitrate between two HRV instruments (H-39).

**PRIN-06.** Signals MUST be interpreted on trends, not single readings.
Scope: the subjective and objective readiness signals that PRIN-05 (conservative reading wins) governs.
Not: a gate keyed on one reading alone.
Pinned: none

**PRIN-17.** The subjective axis MAY veto a hard session on its own.
Scope: session-RPE, wellness and soreness reports, read against a planned hard session.
Not: a subjective report used to make a session harder.
Pinned: none

**PRIN-18.** One below-baseline day IS weak evidence, a coherent multi-day or multi-item decline IS actionable, and a single good night MUST NOT clear accumulated multi-day suppression.
Scope: the weight each trend pattern carries on the day-of readiness rung.
Not: the HRV verdict's own windows and counts, which HRV-08 and HRV-09 set.
Pinned: none

### 1.5 Raw over derived

**PRIN-07.** The state model and the load, response, fatigue, readiness and injury-risk metrics MUST be built only from raw measured signals: HR, RR, pace/GPS, cadence, barometric altitude, running dynamics, and power where present.
Scope: the state model and the load, response, fatigue, readiness and injury-risk metrics, wherever the system computes them.
Not: a vendor-derived estimate, which PRIN-08 quarantines.
Pinned: none

**PRIN-08.** Vendor black-box metrics MUST be ingested into a quarantined namespace and MUST never feed a decision, whatever any setting or default says.
Scope: every vendor black-box metric from any vendor, the metrics PRIN-20 names among them.
Not: a device's numeric resting rMSSD, which PRIN-10 admits as an HRV input.
Pinned: none
Why: decision C24 leaves no default or switch under which the coaching logic may read a quarantined metric (H-38).

**PRIN-09.** A documented open method that is itself a derived estimate, the cold-start estimator for example, IS owned and transparent, so it IS consistent with PRIN-07 (raw over derived).
Scope: an open, cited method that the system reproduces in its owned namespace.
Not: a vendor's proprietary construction of the same estimate, which stays quarantined (COLD-06).
Pinned: none

**PRIN-10.** A device's numeric resting rMSSD (Garmin `lastNightAvg`, or Health Snapshot `RmssdAvgValue`) IS a standard statistic in known units, not a proprietary composite, so it MAY be admitted as an HRV input at reduced fidelity, an ordinal rank in selection.
Scope: the numeric tiers of the resting-HRV hierarchy (HRV-01).
Not: a numeric per-tier confidence weight, which is deferred to Section 6's readiness fusion (HRV-19).
Pinned: none

**PRIN-19.** A quarantined vendor metric MAY be used only for display to the athlete and for divergence surfacing (REG-17).
Scope: every vendor black-box metric that PRIN-08 quarantines, the metrics PRIN-20 names among them.
Not: a corroboration that feeds a decision, a flag or a state change.
Pinned: none

**PRIN-20.** The quarantine list IS Garmin/Firstbeat VO2max, Training Status, Training Readiness, Body Battery, Performance Condition, and the HRV Status Balanced/Unbalanced/Low classification.
Scope: the metrics the quarantine names, which PRIN-08 and PRIN-19 govern together with every other vendor black-box metric, and every restatement of the list downstream.
Not: a numeric resting rMSSD, which is an HRV input (PRIN-10) and not a listed metric.
Pinned: none
Why: decision C25 has the project rule's shorter list swept to this one after the rewrite, per DOC-02 (H-38).

**PRIN-21.** The HRV Status classification built on that rMSSD MUST stay quarantined.
Scope: the HRV Status Balanced/Unbalanced/Low classification.
Not: the numeric resting rMSSD beneath it, which PRIN-10 admits.
Pinned: none

### 1.6 Transparency and explainability

**PRIN-11.** Every derived metric MUST be defined by a transparent, reproducible formula that the spec states in full.
Scope: every metric the system derives, including an open method that is itself a derived estimate, such as the cold-start estimator (PRIN-09).
Not: a vendor black-box metric, which PRIN-08 quarantines.
Pinned: none

**PRIN-12.** Every derived verdict MUST be reproducible by hand from what the same response reports, so the response's `thresholds` block MUST serve `baseline_days`, `min_baseline_readings`, `min_window_readings`, `gap_reset_days`, `band_floor`, `swc_factor` and `recency_tolerance_days`, and every verdict-affecting constant it does not serve, `window_days` today (PRIN-24), IS an OPEN exception.
Scope: each response read on its own, including its `selected_reason`.
Not: the formulas themselves, which the spec states in full under PRIN-11 (transparent formulas).
Pinned: none (F010)
Why: decision C33 reads the transparency rule at the response level and makes `recency_tolerance_days` served, which F010 carries out, reversing the narrower promise recorded at H-17.

**PRIN-22.** Every applied adaptation or decision MUST be logged with its inputs and the rule that fired.
Scope: every adaptation the system applies, whichever loop or rung produced it.
Not: the explanation that chat gives on demand, which reads this log (AUT-05).
Pinned: none

**PRIN-23.** Every stored row inside the windows MUST be in exactly one dataset or listed as excluded with exactly one reason.
Scope: every stored resting-HRV row whose local day lies in `[D-66, D]`.
Not: a row outside those windows, which the era rule reads but the response does not list.
Pinned: none

**PRIN-24.** `window_days` (7), which sets the judged week and which the `thresholds` block does not serve, IS an OPEN exception to rule PRIN-12 (reproducible by hand from the response).
Scope: the verdict-affecting constants that the response does not serve.
Not: `recency_tolerance_days`, which PRIN-12 names as served once F010 lands.
Pinned: none
Why: decision C33 has PRIN-12 name every verdict-affecting constant it does not serve as an OPEN exception (H-39).

### 1.7 Down-regulate freely, up-regulate cautiously

**PRIN-13.** The daily gate MAY reduce load freely in response to poor readiness but MUST NOT manufacture hard work: a green day does not turn an easy day into a hard one, and hard-session placement stays with the weekly plan.
Scope: the day-of readiness rung's effect on the planned session.
Not: a tunable default, since this is a design invariant (DOC-17).
Pinned: none

**PRIN-14.** The system MUST NOT assert a readiness-intact verdict (`hrv_normal`) in the forbidden direction (T-24) outside the named exceptions of PRIN-15, and where the evidence it reports is insufficient it MUST withhold the verdict instead.
Scope: every served HRV verdict, whichever dataset and cause produced it.
Not: the manufacture of hard work on a green day, which PRIN-13 forbids separately.
Pinned: none
Why: decision C07 writes into this section the weak-evidence clause that more than 20 citations already read into it, with the forbidden direction defined once in the Glossary as T-24, and decision C06 makes the whole of that direction absolute but for the named exceptions of PRIN-15 (H-39).

**PRIN-15.** A forbidden-direction population MUST NOT ship on a rarity argument and MAY ship only as a named, counted, test-pinned exception owned by an open IDEA, which may not grow, and the exceptions today are exactly three: the F005-parity population and `DEFERRED_EXCEPTION`, both owned by IDEA-087, and HRV-25's population, owned by IDEA-099.
Scope: every population that the forbidden direction of PRIN-14 reaches, measured or not.
Not: the HRV verdict logic itself, which HRV-14 to HRV-31 state, since this rule governs only which of its populations may ship.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names
Pinned: none (F009)
Why: decision C06 makes the forbidden direction absolute but for named exceptions, and the refinements count three of them, not two (H-39, H-41).

**PRIN-25.** The F005-parity population IS the rows that T162's `forbidden` metric counts at F005 parity, 22,232 of 307,500 rectangle rows and 1,108 of 24,000 walk rows, and `DEFERRED_EXCEPTION` IS the 64 worsened rows at c = 4 and 5 that GATE-05 names.
Scope: the two IDEA-087 exceptions of PRIN-15, as T162 measured them.
Not: HRV-25's population, whose count F009 produces.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names

**PRIN-26.** An exception MAY be named before it is counted only while a feature owns its count, as F009 owns the count and pin of HRV-25's population, whose count is OPEN.
Scope: every exception PRIN-15 names before it is counted, of which HRV-25's population is the one today.
Not: an uncounted exception that no feature owns, which may not ship.
Pinned: none (F009)

### 1.8 Autonomy posture

**AUT-01.** The system MUST apply every plan adaptation autonomously, including major re-periodizations after a material state-model shift, and MUST explain it afterwards (apply-and-notify) with no confirmation step.
Scope: every change to the plan, which the system owns (GOAL-01).
Not: the two matters that AUT-02 names, which the system does not own.
Pinned: none

**AUT-02.** The system's autonomy MUST have exactly two exceptions, both matters the system does not own: the athlete's clinical action and return-to-run clearance on the safety pathway, and the goal contract (GOAL-02).
Scope: every decision that rests with the athlete rather than the system.
Not: the safety override itself, which the system applies (AUT-08).
Pinned: none
Why: decision C23 places the safety override with the system and leaves the athlete only the clinical action and the clearance (H-39).

**AUT-03.** The Conversational Coach Interface IS not a gating mechanism: plan changes never wait on athlete acknowledgment, and an "acknowledged-notification" step for major changes was considered and rejected.
Scope: every plan change, major or minor.
Not: the goal contract, where a change takes effect only as athlete input (GOAL-02).
Pinned: none

**AUT-04.** Chat MAY carry only athlete input and explanation: the probe of an applied change, life and scheduling constraints (routed to the weekly-microcycle loop), injury, soreness and subjective reports (routed to the `research/03` instruments), and plan-change requests (routed through the arbitration ladder).
Scope: every chat message, in either direction.
Not: an approval or confirmation of a change, since chat is never a gate (AUT-03).
Pinned: none
Why: decision C20 adds the input kinds that `decisions/01`, the more specific conforming record, defines (H-39).

**AUT-06.** A coach-in-the-loop authority IS out of v1 scope, and if one is added it MUST be a third, optional authority that can never disable a safety flag.
Scope: any qualified human coach who may set or override shipped defaults.
Not: the two athlete-owned matters that AUT-02 names.
Pinned: none

**AUT-08.** The system MUST apply the safety override (ARB-02) itself, automatically and deterministically, and MUST issue the stop-and-escalate, return-to-run or "seek clinical assessment" instructions that the athlete must act on.
Scope: every hard flag and every high injury risk that ARB-02 names.
Not: the athlete's clinical action or return-to-run clearance, which the athlete owns.
Pinned: none

### 1.9 System ownership: plan versus goal

**GOAL-01.** The system IS the owner of how the goal is pursued (the plan: prescriptions, weekly volume, intensity distribution, taper, full re-periodization), and IS not the owner of what the goal is.
Scope: the split between the plan and the goal contract.
Not: the safety pathway, which AUT-02 governs.
Pinned: none

**GOAL-02.** The goal contract IS the three athlete-owned fields `goal_pace_target`, `race_date` and `distance_m`, which the system MAY propose to change but MUST never change itself, so a change takes effect only as an athlete-supplied input, exactly as at program start.
Scope: every goal-contract field, and every proposal the system makes about one.
Not: the order in which the system proposes remedies, which GOAL-03 sets.
Pinned: none
Why: decision C22 bundles the three fields that two of the three sources already bundle (H-39).

**GOAL-03.** For a critical projection-vs-goal gap, the system MUST propose remedies in a fixed order: first a revised goal pace, then a later race date.
Scope: every gap-triggered proposal of a goal-contract change.
Not: a change of race distance, which GOAL-04 excludes.
Pinned: none

**GOAL-04.** The system MUST never propose a change of race distance (`distance_m`) as a gap remedy.
Scope: every gap-triggered proposal.
Not: a distance change that the athlete enacts, which GOAL-05 allows.
Pinned: none

**GOAL-05.** The remedy ordering MUST govern only what the system proposes, and the athlete MAY enact any goal-contract change, date or distance included, whatever order the proposals came in.
Scope: the athlete's own goal-contract changes, supplied as inputs.
Not: a change the system applies itself, which GOAL-02 forbids.
Pinned: none

**GOAL-06.** The same ordering MUST be stated at every spec site where the gap proposal is defined or surfaced: spec §1.4.6, §6.8, §6.4.4 and §8.5.2.
Scope: the spec sections that define or surface the gap-triggered proposal.
Not: the order itself, which GOAL-03 and GOAL-04 set.
Pinned: none

## Part 2 — Load-bearing findings

**ARCH-00.** The spec MUST honour all eight Part 2 keystones, stated in rules ARCH-01 to ARCH-13, and a design that violates one is wrong whatever its other merits.
Scope: every design choice the spec makes.
Not: the detail of each keystone, which rules ARCH-01 to ARCH-13 state.
Pinned: none

**ARCH-01.** Post-session adaptation MUST be the primary loop: the core is the four between-session loops, and real-time intra-workout adaptation is an optional on-device stretch module, approximated post hoc when absent.
Scope: every adaptation the system makes to an athlete's training.
Not: an in-session cutoff from the core system, which only the optional on-device module can deliver.
Pinned: none

**ARCH-02.** The system MUST maintain its own determinant profile: CS and D′, vVO2max, functional threshold pace, an EF economy proxy, durability and the individual endurance exponent, each with a trend and a confidence.
Scope: every determinant of race pace in the state model.
Not: a vendor-computed VO2max, which rule PRIN-08 quarantines.
Pinned: none

**ARCH-03.** Adaptation MUST be a five-timescale nested loop (long-term, short-term, recent-workout, day-of readiness, optional intra-workout).
Scope: every loop that adjusts the plan.
Not: the rungs of the arbitration ladder, which are ladder positions and not loops (T-27).
Pinned: none

**ARCH-04.** All pace targets MUST be stored relative to the current threshold/CS/VDOT estimate.
Scope: every prescribed pace and zone pace in the plan.
Not: a race-day goal pace, which the athlete's goal contract holds.
Pinned: none

**ARCH-05.** Every pace feature that feeds a decision MUST be grade-adjusted through the Minetti cost-of-gradient curve.
Scope: every pace-derived input to a decision.
Not: a pace shown for display only, which feeds no decision.
Pinned: none

**ARCH-06.** Ingestion MUST map every vendor into one vendor-neutral, timestamped raw-stream schema (Garmin FIT first), and vendor-derived metrics MUST live only in a labelled quarantined sidecar that never feeds a decision.
Scope: every vendor's data at ingestion, and every vendor-derived metric it carries.
Not: display of a sidecar metric or the divergence surfacing of rule REG-17, which feed no decision.
Pinned: none
Why: decision C24 aligns keystone 6 with §1.5, which admits no setting under which a vendor-derived metric feeds a decision.

**ARCH-07.** Subjective input (session-RPE, five-item wellness, pain mapping) MUST be a continuous input on equal footing with device data, and its reporting process MUST be engineered, not just collected.
Scope: every subjective report the athlete gives.
Not: chat as a gate, which rule AUT-04 excludes.
Pinned: none

**ARCH-08.** Every determinant MUST carry a confidence reflecting data recency, quantity and quality.
Scope: every determinant estimate in the state model.
Not: a numeric per-tier HRV confidence weight, which Section 3 does not compute and which is deferred to Section 6's readiness fusion (decision C19).
Pinned: none

**ARCH-09.** Slower loops MUST set the frame and faster loops adjust within it, and the arbitration ladder MUST compose them into one daily prescription.
Scope: every daily prescription.
Not: the precedence between conflicting signals, which the ladder's rungs set (rule ARB-01).
Pinned: none

**ARCH-10.** The plan MUST never hard-code absolute paces.
Scope: every pace the plan stores.
Not: a pace the athlete enters as a goal, which is an input and not a plan target.
Pinned: none

**ARCH-11.** Low confidence MUST make the adaptation logic more conservative and MUST be surfaced to the athlete.
Scope: every determinant whose confidence is low.
Not: false precision, which the athlete is never shown in its place.
Pinned: none

**ARCH-12.** Section 6 MUST treat `hrv_unavailable` as low confidence that widens its guardrails.
Scope: every day on which the HRV input reads `hrv_unavailable`, whatever its `unavailable_reason`.
Not: a verdict about the athlete's readiness, which `hrv_unavailable` does not assert.
Pinned: none
Why: decision C08 makes a withheld HRV verdict conservative through Section 6's treatment of it, as keystone 8 requires of low confidence.

**ARCH-13.** Until Section 6 exists, a withheld HRV day IS a net cost.
Scope: every day on which the HRV input reads `hrv_unavailable` before Section 6's readiness fusion is built.
Not: a withheld day once Section 6 exists, which rule ARCH-12 governs.
Pinned: none
Why: decision C08 prices the withheld days honestly while the fusion that would widen guardrails on them is not built.

## Part 3 — Decision register

**DOC-06.** Every number in research/00 IS a heuristic default that MAY be tuned per athlete only under the individualization rule (IND-01).
Scope: every constant and default that research/00 states.
Not: a design invariant, which DOC-17 holds untunable.
Pinned: none
Why: decision C31 makes the individualization rule govern all per-athlete tuning (H-39).

**DOC-07.** The Part 3 register IS the single place where the spec reconciles its choices made under scientific uncertainty or among competing methods, parameter defaults included.
Scope: every such choice, whether research or the spec introduced it.
Not: the derivations behind a default, which the mechanism docs hold (DOC-04).
Pinned: none

**DOC-08.** Every spec-introduced register row MUST name its research basis, or say it is a pure engineering default, and MUST ship with graceful degradation.
Scope: the spec-introduced rows of the Part 3 register, REG-10, REG-11, REG-12, REG-13, REG-14, REG-15 and REG-17.
Not: a row ratified from the cited research, which DOC-18 governs.
Pinned: none

**DOC-17.** A design invariant, such as PRIN-13 (down-regulate freely, up-regulate cautiously), MUST NOT be tuned per athlete.
Scope: every rule that research/00 names as a design invariant.
Not: a heuristic default, which DOC-06 governs.
Pinned: none

**DOC-18.** Register rows MUST be ratified from the cited research, except the §3.1–§3.4 resolutions and the spec-introduced rows REG-10, REG-11, REG-12, REG-13, REG-14, REG-15 and REG-17, whose batches were ratified as H-04 and H-05 record.
Scope: every row of the Part 3 register.
Not: a row's tuning per athlete, which IND-01 governs.
Pinned: none

**REG-01.** CTL MUST be a 42-day EWMA and ATL a 7-day EWMA, with TSB = CTL − ATL.
Scope: every fitness, fatigue and form value the system computes.
Not: an individually fitted time constant, which rule REG-20 defers.
Pinned: none

**REG-02.** ACWR MUST be advisory context and a spike flag inside injury risk only, with a wide ~0.8–1.5 range, and MUST never be a hard gate.
Scope: every use of the acute:chronic workload ratio.
Not: the TSB target range or the CTL-rise range, which are separate quantities (T-07).
Pinned: none
Why: T-07 reserves the word band for the HRV SWC band, so the ACWR's interval is named a range.

**REG-03.** Per-session load MUST use rTSS as primary when a valid GAP stream exists, reconciled against HR-TRIMP and sRPE.
Scope: every session's load.
Not: the fallback when the sources diverge or one is missing, which rule REG-21 states.
Pinned: none

**REG-04.** The HR-TRIMP sex coefficients MUST be men 0.64·e^(1.92·ΔHR) and women 0.86·e^(1.67·ΔHR).
Scope: every HR-TRIMP computation.
Not: a coefficient pair other than these two sex forms.
Pinned: none

**REG-05.** The endurance exponent MUST be the individual Riegel *b* when confidence is adequate, else the population 1.06 with a widened interval.
Scope: every race-time projection that uses the endurance exponent.
Not: an individual exponent fitted while its confidence is inadequate.
Pinned: none

**REG-06.** Durability MUST be EF/decoupling drift over the back third of long runs, tracked as a within-athlete trend only and flagged as emerging.
Scope: every durability estimate.
Not: a comparison of durability between athletes.
Pinned: none

**REG-07.** Training-intensity distribution MUST be pyramidal-leaning in base and polarizing through build/peak, with ~80% easy by time throughout.
Scope: every training block's intensity distribution.
Not: a threshold-dominant distribution.
Pinned: none

**REG-08.** The taper MUST be about 2 weeks with a ~50% volume cut and exponential decay, with intensity maintained and frequency mostly held, and it MUST then be individualized by chronic load and distance.
Scope: every pre-race taper.
Not: a mid-block down week, which rule REG-15 sets.
Pinned: none

**REG-09.** Data-quality gating MUST require 1 Hz recording, and a chest strap for any at-or-above-threshold HR metric.
Scope: every recording the system ingests, and every at-or-above-threshold HR metric.
Not: resting HRV sourcing, which the tier hierarchy of §3.3 governs.
Pinned: none

**REG-10.** The four course/environment pace modifiers MUST compose multiplicatively in the order altitude → heat/humidity → wind → grade.
Scope: every pace adjusted for course and environment.
Not: an additive composition of the modifiers.
Pinned: none

**REG-11.** The system MUST ask for a daily morning resting HRV measurement, sourced through the tier hierarchy (strap preferred, Health Snapshot the no-strap default, `lastNightAvg` where available).
Scope: every athlete's morning resting HRV capture.
Not: a chest strap every day, which the tier hierarchy does not require.
Pinned: none

**REG-12.** Course elevation MUST be resampled to a uniform ≤50 m distance grid, with sparser sources linearly interpolated and flagged `interpolated`, and elevation MUST be smoothed before gradient is computed.
Scope: every course-geometry ingestion.
Not: the per-session GAP pipeline, which smooths altitude on its own terms.
Pinned: none

**REG-13.** A block's target determinant MUST be ranked by gap-contribution × trainability × time-to-race × confidence (multiplicative), degrading to aerobic base plus phase-appropriate specificity when the score is uninformative.
Scope: every training block's choice of target determinant.
Not: an additive score, under which a near-zero factor would not drop a determinant.
Pinned: none

**REG-14.** The non-taper weeks MUST split base ≈50%, build ≈30%, peak ≈20%, followed by a terminal ~2-week taper, with base-first compression on short calendars.
Scope: every macrocycle the plan lays out.
Not: the order of the blocks, which the research fixes.
Pinned: none
Why: the split follows common macrocycle practice and the never-abandon-aerobic-base guardrail of the long-term loop in `research/05` §5 (decision C29).

**REG-15.** A mid-block down week MUST cut ~20–40% of weekly volume, holding intensity and frequency, every 3–4 weeks.
Scope: every mid-block recovery week.
Not: the pre-race taper, which rule REG-08 sets.
Pinned: none

**REG-16.** The taper MUST drive race-day TSB into a target range of ~+5 to +25, bounded on both sides, as a TSB controller.
Scope: every pre-race taper.
Not: a fixed volume recipe that ignores TSB.
Pinned: none
Why: T-07 reserves the word band for the HRV SWC band, so the race-day TSB interval is named a target range.

**REG-17.** An own-vs-vendor divergence MUST be proactively surfaced when it exceeds about one own-estimate confidence interval, or ~10% where no interval is defined.
Scope: every labelled comparison between an own estimate and a vendor metric.
Not: a divergence below that threshold, which is shown only on request.
Pinned: none

**REG-18.** Decision, version and derived-feature records MUST be retained indefinitely.
Scope: every decision, version and derived-feature record.
Not: raw per-sample streams, which rule REG-29 sets.
Pinned: none

**REG-19.** The register MUST carry the weekly CTL-rise range as a row marked PROVISIONAL, with a soft ~+5/week in build, a range of +3–7 and a hard ceiling of +8 as ratified in spec §6.2.2, until field data refines it.
Scope: every week's planned rise in CTL.
Not: a settled default, which the row becomes only when field data refines it.
Pinned: none
Why: decision C30 keeps the register the single place the spec reconciles its defaults, and T-07 reserves the word band for the HRV SWC band.

**REG-20.** The fitness/fatigue time constants MUST stay at 42/7 long-term, and per-athlete fitting of them IS a later enhancement.
Scope: every CTL and ATL computation.
Not: a launch feature, which per-athlete fitting of the constants is not.
Pinned: none

**REG-21.** A divergence above 25% between rTSS, HR-TRIMP and sRPE MUST fall back to their mean, and load MUST degrade to HR-TRIMP, then sRPE.
Scope: every session whose load sources diverge or are missing.
Not: a modest divergence, which is sensor noise.
Pinned: none

**REG-22.** The Z2/Z3 split MUST be a phase- and distance-dependent parameter.
Scope: every training block's intensity split above easy running.
Not: the ~80% easy share, which rule REG-07 holds throughout.
Pinned: none

**REG-23.** PPG input MUST be flagged, PPG input other than resting HRV (in-run wrist HR, for example) MUST be down-weighted, and resting HRV from PPG MUST carry its reduced fidelity as an ordinal rank below the chest strap in selection, with any numeric per-source confidence weight deferred to Section 6's readiness fusion.
Scope: every input the system takes from optical PPG.
Not: HRV computed from in-run wrist PPG, which rule HRV-05 excludes.
Pinned: none
Why: decision C19 changes the wording for resting HRV only, since Section 3 applies fidelity as an ordinal rank and computes no confidence weight, and other PPG input keeps its down-weighting.

**REG-24.** The chest-strap signature IS the presence of RR in an activity.
Scope: every activity the system checks for a chest strap.
Not: a resting HRV capture, whose source is its declared tier.
Pinned: none

**REG-25.** A skipped morning reading MUST degrade the readiness gate gracefully and never fail it, and a day it leaves reading `hrv_unavailable` MUST reach Section 6 as low confidence that widens guardrails (rule ARCH-12).
Scope: every morning on which the athlete takes no resting HRV reading.
Not: a hold on the plan until a reading arrives.
Pinned: none
Why: decision C08 makes a missing reading conservative through Section 6, and until Section 6 exists rule ARCH-13 prices the withheld day.

**REG-26.** The mid-block down-week cut MUST be shallower than the taper's volume cut.
Scope: every mid-block recovery week.
Not: a cut deep enough to detrain.
Pinned: none

**REG-27.** The individual best-form TSB MUST be refined under rule IND-01.
Scope: every athlete with race or tune-up history.
Not: a fixed population TSB target held once the athlete's own history identifies one.
Pinned: none

**REG-28.** The surfacing threshold MUST govern surfacing only, and never a decision, flag or state change.
Scope: every own-vs-vendor divergence.
Not: a vendor number entering a decision, which rule PRIN-08 excludes.
Pinned: none

**REG-29.** Raw per-sample streams MUST be kept for the current program plus a tunable rolling window.
Scope: every raw per-sample stream.
Not: decision, version and derived-feature records, which rule REG-18 keeps indefinitely.
Pinned: none

**REG-30.** Record and stream retention MUST affect no engine decision.
Scope: every retention setting.
Not: storage cost, which the rolling window of rule REG-29 tunes.
Pinned: none

**HRV-07.** The HRV trend MUST compare the 7-day mean of ln rMSSD over the judged week against the smallest-worthwhile-change (SWC) band, centred on the baseline mean with half-width `max(0.5 · SD(ln rMSSD), 0.01)`, where SD is the sample (n−1) standard deviation of the athlete's own baseline ln rMSSD.
Scope: every per-tier dataset's SWC band, as `build_band` builds it.
Not: a coefficient of variation (CV) of rMSSD or of ln rMSSD, which is never the SWC band's statistic.
Pinned: runcoach-api/tests/test_hrv_trend_band.py::test_the_floor_fires_for_a_degenerate_baseline
Why: decision C32 states the 0.01 floor that `build_band` applies, so research/00 states the SWC band the code computes on a degenerate rMSSD series.

### 3.1 Individualization — the governing rule (resolved)

**IND-01.** A parameter MUST be individualized only when it is identifiable from the athlete's own data and backed by enough quality data to beat the population default.
Scope: every parameter the system could fit per athlete.
Not: a fit on thin field data that cannot beat the population default.
Pinned: none
Why: decision C31 makes this the governing rule for every per-athlete tuning.

**IND-02.** Threshold and zone paces MUST re-anchor continuously.
Scope: every threshold and zone pace.
Not: the fitness/fatigue time constants, which rule IND-06 holds.
Pinned: none

**IND-03.** Until a parameter is individualized, the population default MUST be held with honest confidence intervals.
Scope: every parameter not yet individualized.
Not: a fit to noise.
Pinned: none

**IND-04.** Every constant in research/00 IS a default, tunable per athlete only under rule IND-01, except an invariant, which is never tuned per athlete.
Scope: every numeric constant research/00 states.
Not: a constant tuned per athlete outside rule IND-01.
Pinned: none
Why: decision C31 makes the constants defaults while §3.1 governs their tuning, and keeps design invariants such as §1.7 out of it.

**IND-05.** The endurance exponent and the CS profile MUST individualize once enough qualifying maximal efforts exist.
Scope: every athlete's endurance exponent and CS profile.
Not: an individual fit before those efforts exist.
Pinned: none

**IND-06.** The fitness/fatigue constants MUST stay at 42/7.
Scope: every CTL and ATL computation.
Not: per-athlete fitting of the constants, which rule REG-20 defers.
Pinned: none

### 3.2 Cold-start — establishing day-one state (resolved, amends `research/05` §3.2)

**COLD-01.** Before enough running history exists to fit a CS curve, and when its inputs are present, the system MUST seed day-one state with an owned non-exercise VO2max estimate and MUST hand it off to the raw-data CS estimate as history accumulates, blending the two by confidence during the transition.
Scope: every athlete without enough running history for a CS fit.
Not: an athlete with enough running history, whom the raw-data CS estimate serves.
Pinned: none
Why: decision C26 bases the seed on the two ratified estimators that rule COLD-08 names.

**COLD-02.** The Uth–Sørensen ratio, VO2max ≈ 15.3 × (HR_max / HR_rest), MUST be the primary seed when a credible HR_max and a true resting HR are clean.
Scope: every cold-start seed whose HR inputs are clean.
Not: a seed whose HR input is missing or untrustworthy, which rule COLD-03 serves.
Pinned: none

**COLD-03.** The Jackson-form non-exercise regression (age, sex, BMI or %BF, self-reported activity rating) MUST be the corroborator, and the fallback when an HR input for Uth–Sørensen is missing or untrustworthy.
Scope: every cold-start seed.
Not: a seed with no non-exercise inputs, which rule COLD-05 serves.
Pinned: none

**COLD-04.** Where both estimators are available they MUST be blended by confidence, and both MUST hand off to the CS estimate.
Scope: every cold-start seed with both estimators available.
Not: a seed held after running history supports a CS estimate.
Pinned: none

**COLD-05.** With no non-exercise inputs, the system MUST fall back to population defaults (b = 1.06, fractional-utilization curves) with wide, honestly flagged confidence intervals.
Scope: every athlete with no non-exercise inputs and too little running history.
Not: an athlete whose inputs support a seed.
Pinned: none

**COLD-06.** The Polar OwnIndex MUST stay quarantined.
Scope: the Polar OwnIndex and every output of it.
Not: the open math rule COLD-10 allows.
Pinned: none

**COLD-07.** The cold start of §3.2 MUST supersede the population-defaults-only cold start of `research/05` §3.2, which is read through it.
Scope: every reading of `research/05` §3.2.
Not: the population-defaults fallback, which rule COLD-05 keeps.
Pinned: none

**COLD-08.** The non-exercise estimate MUST take only the inputs of the two ratified estimators: HR_max and resting HR (rule COLD-02), and age, sex, a body-composition term (BMI or %BF) and a self-reported activity rating (rule COLD-03).
Scope: every non-exercise VO2max estimate.
Not: an input that neither ratified estimator takes.
Pinned: none
Why: decision C26 lists the inputs of the estimators ratified in the research back-fill (H-02).

**COLD-09.** The Uth–Sørensen seed MUST be held low-confidence, widened for older athletes, and superseded by the CS estimate as soon as history allows.
Scope: every Uth–Sørensen seed.
Not: a Jackson-form seed, which rule COLD-03 sets.
Pinned: none

**COLD-10.** The system MAY reproduce only the open Uth–Sørensen and Jackson math, and only in the owned namespace.
Scope: every non-exercise estimator the system implements.
Not: a proprietary estimator.
Pinned: none

**COLD-11.** The owned cold-start estimator IS defined and validated in `spec/04` §4.4.2, a requirement that is discharged.
Scope: the owned cold-start estimator.
Not: the ratified estimators themselves, which rules COLD-02 and COLD-03 name.
Pinned: none

### 3.3 Resting-HRV source tiering (resolved, amends the data-quality-gating and HRV-gate register rows)

**HRV-01.** Resting HRV MUST come through a four-tier source hierarchy: (1) chest-strap resting RR, reduced to rMSSD by the system's own artefact filter; (2) Health Snapshot `RmssdAvgValue`; (3) Health API `lastNightAvg`; (4) the HRV Status classification, which stays quarantined and MUST never be a trend input.
Scope: every resting-HRV reading the HRV trend consumes; the rule applies per per-tier dataset (T-06).
Not: the order in which datasets are selected, which HRV-14 (selection) sets.
Pinned: none

**HRV-02.** The chest strap IS the preferred, highest-fidelity resting-HRV source, and the system MUST NOT make it a prerequisite.
Scope: the choice of resting-HRV source on any day.
Not: the reduced fidelity of the numeric tiers, which HRV-04 (numeric tiers) states.
Pinned: none

**HRV-03.** Ingestion MUST accept a pre-computed resting rMSSD (a numeric value with a source-tier tag) distinct from a raw RR series.
Scope: the ingestion layer, for tiers 2 and 3 of HRV-01 (the hierarchy), which supply a scalar rather than beats.
Not: a confidence value carried with the reading, since Section 3 computes no confidence weight (HRV-54).
Pinned: none

**HRV-04.** The numeric tiers (2–3) MUST be admitted at reduced fidelity, an ordinal rank that selection reads (HRV-19), and never at a numeric per-tier confidence weight, which is deferred to Section 6's readiness fusion.
Scope: Health Snapshot and Health API readings, each in its own per-tier dataset.
Not: the HRV Status classification, which stays quarantined under HRV-01 (the hierarchy).
Pinned: none
Why: decision C19 words the admission as fidelity, because Section 3 holds no confidence weight (HRV-54).

**HRV-05.** HRV MUST never be computed from in-run wrist PPG, and in-activity HRV MUST NOT be computed in v1 or for any readiness input, so adopting DFA-α1 of the in-run RR series for LT1 (LT1-02) MUST first amend this rule explicitly.
Scope: every HRV computation the system makes, from any source.
Not: resting HRV from the source hierarchy of HRV-01 (the hierarchy).
Pinned: none
Why: decision C27 scopes the prohibition so that it no longer contradicts the recommended future LT1 path.

**HRV-06.** Because different sources carry different systematic biases, a reading of one tier MUST NOT ever enter another tier's baseline or SWC band, and a source switch MUST never be read as a physiological HRV shift.
Scope: every resting-HRV reading of every tier; the rule applies per per-tier dataset (T-06).
Not: a device replaced within the same tier, which HRV-84 (same-tier replacement) governs.
Pinned: none

**HRV-47.** Health Snapshot IS the preferred default resting-HRV source where no chest strap is in use.
Scope: an athlete who is not using a chest strap.
Not: Health API `lastNightAvg`, which ranks below Health Snapshot in HRV-01 (the hierarchy).
Pinned: none

### 3.4 Aerobic-threshold (LT1) determination — recommended path (open, deferred to a future determinant)

**LT1-01.** In v1 the Z1/Z2 boundary MUST be a fixed fraction (~80–88%) of functional-threshold velocity, so it cannot move independently of the threshold anchor.
Scope: every Z1/Z2 boundary the v1 system sets.
Not: an LT1 determinant, which rule LT1-02 defers.
Pinned: none
Why: decision C28 fixes the v1 boundary as a fraction of threshold, and F011 makes the matching design change to spec §5.4.2.

**LT1-02.** DFA-α1 = 0.75 IS the recommended future LT1 determinant, and it MUST NOT be adopted for v1.
Scope: every LT1 determination.
Not: the v1 boundary, which rule LT1-01 sets.
Pinned: none
Why: decision C27 keeps the future path consistent with rule HRV-05, which forbids in-activity HRV in v1.

**LT1-03.** Any LT1 surrogate IS future work, outside v1.
Scope: every proposed LT1 surrogate.
Not: the v1 fraction of threshold, which rule LT1-01 sets.
Pinned: none

**LT1-04.** DFA-α1 MAY be adopted for LT1 only under RR-quality gating, after validation in the target population including women, and through an explicit amendment of rule HRV-05.
Scope: every future adoption of DFA-α1.
Not: any readiness input, which rule HRV-05 governs.
Pinned: none
Why: decision C27 requires the amendment because DFA-α1 is computed from the RR series during running.

**LT1-05.** Until DFA-α1 is adopted, LT1 IS a human-coach-specification candidate.
Scope: every athlete whose coach can supply a lab or field LT1.
Not: a measured LT1 determinant.
Pinned: none

## Part 4 — Design and freedom-to-operate guardrails

**FTO-01.** Load, fatigue and form MUST come from the open TRIMP / TSS / PMC / Banister lineage, and fitness and thresholds from open CS/CP and lactate-threshold-from-raw-data methods.
Scope: every load, fatigue, form, fitness and threshold computation.
Not: a proprietary construction, which rule FTO-02 names.
Pinned: none

**FTO-02.** The system MUST NOT replicate Firstbeat's reliability-weighted HR-to-VO2max pipeline (US9237868B2), EPOC-based load or Training Effect, or WHOOP's recovery-indicator construction (US11574722B2).
Scope: every estimator and readiness construction the system builds.
Not: the published math the system derives its own constructions from.
Pinned: none

**FTO-03.** Readiness MUST derive from the published HRV literature, with the system computing its own baseline, SWC band and verdict with open math.
Scope: every readiness verdict.
Not: a vendor recovery score.
Pinned: none

**FTO-04.** Plan generation and adaptation MUST be single-ecosystem, Garmin-first and FIT-driven, materially distinct from the multi-device-coordination claims of US11517790.
Scope: every plan the system generates or adapts.
Not: multi-vendor ingestion, which is not multi-device coordination (T-28).
Pinned: none

**FTO-05.** Three patent teachings MAY be adopted only through non-infringing constructions: quality-weighting raw windows into a CS fit, separating aerobic and anaerobic load channels with TRIMP/rTSS plus a W′-depletion term, and environment- and individual-normalizing load with published heat and GAP models.
Scope: every idea the system takes from a live patent.
Not: the patented construction itself.
Pinned: none

**FTO-06.** This Part IS engineering research, not legal advice, and freedom-to-operate reliance MUST be confirmed with counsel.
Scope: every freedom-to-operate judgement in research/00.
Not: a legal opinion.
Pinned: none

**FTO-07.** Consuming a device's numeric rMSSD IS distinct from reproducing a proprietary recovery construction.
Scope: every numeric resting rMSSD the system takes from a device.
Not: a vendor HRV classification, which stays quarantined.
Pinned: none

## Part 5 — Document map, authority, and decision records

**DOC-15.** Deferred and future items MUST live in `future/future-directions.md`: coach-in-the-loop, LT1 detail and the human-coach-specification candidates.
Scope: every item research/00 defers beyond v1.
Not: a current rule, which research/00 states.
Pinned: none

### 5.1 This document's authority

**DOC-01.** research/00 IS the project's single decision authority, and it governs the Phase-2 specification, every record under `decisions/`, and any place where it and a mechanism research doc (`research/01`–`06`) appear to differ.
Scope: every rule research/00 states, and every derived document that restates one.
Not: the physiology and device evidence itself, which the mechanism docs hold (DOC-04).
Pinned: none

**DOC-05.** The spec MUST reference research/00 for every conflict-resolution, parameter-default and freedom-to-operate question, and MUST reference the mechanism docs for the derivations.
Scope: every section of the Phase-2 specification.
Not: a derivation restated in research/00, which DOC-04 leaves to the mechanism docs.
Pinned: none

### 5.2 The mechanism research docs (the evidence this document points to)

**DOC-04.** research/00 MUST treat the mechanism docs `research/01`–`06` as the evidence, elevating and prioritising them, and MAY change them only where §5.4 records an exception: the cold-start amendment of `research/05` §3.2 (COLD-07), and the HRV SWC band restatement of `research/05` §2.4/§6 (H-09).
Scope: every mechanism research doc that §5.2 lists.
Not: the spec and the decision records, which conform to research/00 (DOC-01).
Pinned: none

### 5.3 Decision records (`decisions/`)

**DOC-03.** Decision records MUST conform to Parts 1–4, and where a record and research/00 conflict, research/00 MUST govern until the record is reconciled here.
Scope: every record under `decisions/`.
Not: the mechanism docs, which DOC-04 governs.
Pinned: none

**AUT-05.** Chat IS an I/O layer over the deterministic engine, in which the LLM translates and explains and the engine decides.
Scope: the Conversational Coach Interface of `decisions/01`.
Not: a decision taken by the LLM.
Pinned: none

**AUT-07.** Every chat-originated change MUST route through the arbitration ladder (ARB-01), and chat MUST never be a second, opaque adaptation path.
Scope: every change that starts in a chat message.
Not: a chat message that asks for no change, such as a probe of an applied change (AUT-04).
Pinned: none

**DEC-01.** `decisions/01` (Conversational Coach Interface) IS a record that conforms to research/00 and is consistent with §1.5, §1.6 and the ladder.
Scope: the whole of `decisions/01`.
Not: a record that governs research/00, which it does not.
Pinned: none (F011)
Why: decision C21 moves the reconciliation that described the record's earlier framing to history entry H-07.

**DEC-02.** Any checkpoint framing that `decisions/01` records as superseded MUST be read through §1.8–§1.9.
Scope: every earlier framing `decisions/01` records as superseded.
Not: the record's current posture, which conforms.
Pinned: none

### 5.4 Reconciliations and amendments

**DOC-02.** Every change MUST be made in research/00 first, and the derived statements (spec sections, feature files, construction references, contracts, the project rule, code docstrings) MUST then be restated to match.
Scope: every change to a rule, whoever makes it.
Not: a restatement made first in a derived document, which DOC-16 forbids from resolving the conflict.
Pinned: none

**DOC-09.** research/00 MUST state only current rules, and a dated summary of what changed MUST go to the history file, `specification/research/00-history.md`, under an H-NN entry.
Scope: every change to a rule of research/00, with its date and what it changed.
Not: verbatim superseded wording, which DOC-19 places elsewhere.
Pinned: none
Why: decision C38 replaces keeping superseded text in place in research/00 with a dated summary in the history file (H-39).

**DOC-10.** A retired ratified mechanism MUST keep its pins, re-pointed at the mechanism that now closes its population.
Scope: every ratified mechanism that a feature retires.
Not: a mechanism that is kept, re-derived or redeployed.
Pinned: none

**DOC-11.** The authority MUST publish no figure that nobody measured, and a published number MUST be a measurement or a derivation, never an assumption about how people behave.
Scope: every figure research/00 publishes.
Not: a heuristic default, which DOC-06 governs.
Pinned: none

**DOC-12.** Any new HRV measurement MUST vary capture density, on both datasets independently.
Scope: every new HRV sweep, harness or parameter search.
Not: a figure already recorded at one density, which says so where it is stated.
Pinned: none

**DOC-13.** `spec_outline.md` Section 3 MUST state only the HRV SWC band and the window, and it MUST be swept for HRV SWC band restatements and exempt from tier-rule sweeps.
Scope: `spec_outline.md` Section 3.
Not: the rest of the spec, which the sweeps cover in full.
Pinned: none

**DOC-14.** F005's feature file, construction reference and release record MUST describe the rule as shipped and MUST NOT be swept.
Scope: the three F005 records named here.
Not: the current per-tier dataset model, which DOC-22 locates.
Pinned: none

**DOC-16.** A derived document MUST never resolve a conflict with the authority from underneath, and MUST record the conflict for the next reader.
Scope: every derived statement that DOC-02 lists.
Not: research/00 itself, where the conflict is resolved (DOC-02).
Pinned: none

**DOC-19.** Verbatim superseded wording IS kept in git, the task files, `withdrawn_phrasings.py` and `research00_old_meanings.py`, and MUST NOT be kept in research/00.
Scope: every withdrawn or retired phrasing of a rule.
Not: the dated summary of a change, which the history file holds (DOC-09).
Pinned: none

**DOC-20.** Each such retired mechanism MUST get a three-valued pin: green on the shipped code, red with that mechanism alone deleted, and green on the replacement.
Scope: the retired set only.
Not: a mechanism that is kept, which needs no three-valued pin.
Pinned: none

**DOC-21.** Any walk indexed by "days since return" MUST say whether it means days elapsed or mornings captured.
Scope: every HRV walk or figure indexed by time since a return.
Not: a walk indexed by calendar date alone.
Pinned: none

**DOC-22.** The per-tier dataset model IS stated in full in `spec/references/F006-dataset-model.md`.
Scope: the per-tier dataset model of the HRV rules.
Not: F005's single-baseline rule, which DOC-14 leaves as shipped.
Pinned: none

**HRV-08.** For a target local date D, the judged week IS the local days `[D-6, D]`, the nominal baseline window IS `[D-66, D-7]` (60 days) and the previous window IS `[D-126, D-67]`.
Scope: every per-tier dataset and every target date D.
Not: an overlap, since the judged week and the nominal baseline window are disjoint.
Pinned: none

**HRV-09.** Every count in the HRV rule MUST be in distinct local days, with `min_baseline_readings` = 14 and `min_window_readings` = 3.
Scope: every count the HRV rule takes, in every per-tier dataset.
Not: a count of captures, since a second capture on one local day adds no day.
Pinned: none

**HRV-10.** Every resting-HRV reading MUST feed the dataset of its own tier, and a morning with both a strap capture and a Health Snapshot MUST feed both.
Scope: every resting-HRV reading; the rule applies per per-tier dataset (T-06).
Not: the HRV Status classification, which is no tier and feeds no dataset.
Pinned: none

**HRV-11.** Within a per-tier dataset, each local day MUST keep its earliest capture.
Scope: every local day in the athlete's own zone, in every per-tier dataset.
Not: a collapse across tiers, since each dataset collapses only its own readings.
Pinned: none
Why: the critique-round call adopts "earliest", on which the code and IDEA-079 agree.

**HRV-12.** A per-tier dataset IS established when its dataset baseline window (T-09), the nominal `[D-66, D-7]` clipped at the latest of the coverage-gap resumption (HRV-73), its era boundary (HRV-38) and its last internal-hole resumption (HRV-37), holds at least `min_baseline_readings` distinct local days.
Scope: every per-tier dataset, counted after all three clips.
Not: the unclipped nominal baseline window, which a clipped dataset does not count in.
Pinned: none
Why: decision C12 counts establishment in the window the code reads, since the nominal window is shared by every dataset only before clipping.

**HRV-13.** A per-tier dataset IS judgeable when it is established (HRV-12) and holds at least `min_window_readings` distinct days of the judged week.
Scope: every per-tier dataset, on every judged day.
Not: selection itself, which HRV-14 (selection) makes among the judgeable datasets.
Pinned: none

**HRV-14.** The HRV verdict MUST be taken from the highest-fidelity judgeable per-tier dataset that the recency gate did not skip, whose reference is every established dataset (HRV-15), with chest-strap raw RR ranked over the numeric tiers.
Scope: selection on every judged day.
Not: a confidence weight, which never arbitrates (HRV-19).
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_the_promoted_verdict_is_the_selected_datasets_unchanged_whatever_the_others_read

**HRV-15.** A judgeable per-tier dataset MUST be skipped when its latest reading in the series baseline window (T-09) falls strictly more than `recency_tolerance_days` (28) days behind the latest series-baseline-window reading of any established dataset.
Scope: the judgeable per-tier datasets on each judged day, which are the recency gate's candidates.
Not: an established dataset that is not judgeable, which the gate never skips but which can hold the reference maximum.
Pinned: runcoach-api/tests/test_hrv_trend_series.py::test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band
Why: decision C10 drops the wording that a lone candidate is never struck, since the reference taken over every established dataset (H-26), which decision C11 restates, can strike a lone judgeable one.

**HRV-16.** `recency_tolerance_days` MUST be 28, four judged weeks (4 × `window_days`), chosen greater than `gap_reset_days` (21) so that the recency gate and the coverage-gap reset do not disagree about the same number of silent days.
Scope: the recency gate of HRV-15 (the skip).
Not: the silence of the whole series, which the coverage-gap reset of HRV-73 (the global gap) measures.
Pinned: none

**HRV-17.** The response's `thresholds` block MUST publish `recency_tolerance_days`.
Scope: every HRV trend response.
Not: the other verdict-affecting constants, which PRIN-12 (the served constants) names.
Pinned: none (F010)
Why: decision C33 reads §1.6 at the response level, so the constant that decides `selected_reason` is served, and F010 publishes it.

**HRV-18.** Selection MUST run per judged day and read nothing from earlier days, and every point of the series MUST name the per-tier dataset its SWC band came from.
Scope: every judged day of a requested HRV series.
Not: a selection carried over from a previous judged day.
Pinned: none

**HRV-19.** The fidelity rank (`TIER_FIDELITY`) alone MUST arbitrate selection among the judgeable datasets.
Scope: selection on every judged day.
Not: recency, which only skips a candidate (HRV-15) and never ranks one.
Pinned: runcoach-api/tests/test_hrv_trend_series.py::test_the_numeric_confidence_weight_never_participates_in_selection

**HRV-20.** The selected per-tier dataset MUST supply the HRV verdict, `baseline` and `band`.
Scope: every judged day on which a dataset is selected.
Not: a day on which nothing is selected, which HRV-24 (the fallback) governs.
Pinned: none

**HRV-21.** A per-tier dataset IS disagreeing (`disagreed_with`) when its judged-week mean reads on the other side of its own SWC band from the selected dataset's, in either direction.
Scope: every reported per-tier dataset other than the selected one.
Not: a veto, since a disagreeing dataset is named and never overrides the selected one.
Pinned: none

**HRV-22.** Whenever the served HRV verdict is `hrv_unavailable`, for any cause, the dissent list (`disagreed_with`) MUST be empty.
Scope: every response, whichever of the three no-verdict states of HRV-23 (no-verdict states) holds.
Not: the reporting of each dataset against its own SWC band, which HRV-57 keeps.
Pinned: runcoach-api/tests/test_hrv_dataset_populations.py::test_a_withheld_verdict_names_no_dissenter_and_a_conferred_one_still_does

**HRV-23.** The set of no-verdict states IS exactly three: nothing selected; a selected dataset withheld under HRV-31 (the withhold); a day that has not happened, which MAY coincide with either of the other two (HRV-58).
Scope: every judged day on which the HRV verdict is `hrv_unavailable`.
Not: which `unavailable_reason` is served, which HRV-28 (the causes) sets.
Pinned: none

**HRV-24.** When nothing is selected, the HRV verdict MUST be unavailable and `baseline`/`band` MUST be populated for presentation only.
Scope: every judged day on which no per-tier dataset is selected.
Not: a conferred verdict, which the presented dataset never carries (HRV-60).
Pinned: none

**HRV-25.** The selected per-tier dataset MAY serve `hrv_normal` while another reported dataset reads below its own SWC band only as the named §1.7 exception that PRIN-15 (the §1.7 exceptions) lists, owned by IDEA-099, and that population MUST NOT grow.
Scope: every response whose selected dataset serves `hrv_normal` beside a dataset that reads below its own SWC band.
Not: a veto by the dataset that reads below, which selection never grants (HRV-14).
Pinned: none (F009)
Why: decision C06 makes §1.7 absolute, so this population ships only as a named exception whose count and pin F009 produces.

**HRV-26.** `judge` MUST judge one per-tier dataset in this fixed order: no SWC band (fewer than two baseline readings) → `week_too_thin` (fewer than `min_window_readings` in the judged week) → `week_not_representative` (withheld, HRV-31) → `baseline_unestablished` → otherwise `hrv_suppressed` iff the 7-day mean is strictly below `band.lo`, else `hrv_normal` (inside or above).
Scope: the dataset that speaks on a judged day, selected or presented.
Not: the choice of which dataset speaks, which HRV-14 (selection) and HRV-59 (the fallback order) make.
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_unavailable_reason_reports_the_withheld_week_before_an_unestablished_baseline

**HRV-27.** The system MUST NOT assert an HRV verdict of either kind on an unestablished baseline (the establishment gate is symmetric), and the SWC band MUST still be reported.
Scope: every per-tier dataset that speaks on a judged day.
Not: a dataset with fewer than two baseline readings, which has no SWC band to report (HRV-26).
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_unavailable_reason_baseline_unestablished_alone

**HRV-28.** `unavailable_reason` IS exactly one of `no_tier_sustains_a_trend`, `no_band`, `week_too_thin`, `week_not_representative`, `baseline_unestablished`, `day_not_happened`, and it IS null exactly when the verdict is not `hrv_unavailable`.
Scope: every served HRV verdict.
Not: a seventh cause, which the response never names.
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_the_six_unavailable_reason_names_are_the_same_six_in_the_module_the_schema_and_the_contract

**HRV-29.** `day_not_happened` MUST be decided at the route (`main._withhold_future`) for any day after the athlete's local today.
Scope: every requested day after the athlete's local today.
Not: the pure rule's own causes, which HRV-26 (the guard order) orders.
Pinned: none

**HRV-30.** `no_tier_sustains_a_trend` MUST fire only when the series holds no per-tier dataset, that is, no reading of any tier in `[D-66, D]` after the gap clip.
Scope: the structural cause, asked before any dataset's own guard order (HRV-26).
Not: a tier with fewer than two baseline readings, which reports `no_band`.
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_unavailable_reason_no_tier_when_the_store_holds_no_reading_at_all
Why: the enum value is contract-bound, so its name is kept although it overstates the condition.

**HRV-31.** A per-tier dataset's verdict MUST be withheld (`week_not_representative`) when another dataset that could not have been selected: not judgeable, or skipped by the recency gate (HRV-15), holds at least `min_window_readings` distinct judged-week days, every one later than every judged-week day of the dataset being judged.
Scope: every per-tier dataset on every judged day.
Not: a dataset that could have been selected, which never withholds another's verdict.
Pinned: runcoach-api/tests/test_hrv_trend_band.py::test_the_withhold_reaches_a_never_used_tier_bought_this_week
Why: decisions C01 and C02 state the withhold as built, which IDEA-083 option 1 chose.

**HRV-32.** The withhold predicate MUST be over day sets and their order, not counts.
Scope: the withhold of HRV-31 (the withhold).
Not: a comparison of how many days each dataset holds.
Pinned: none

**HRV-33.** The residual (mornings on which a return is still judged on pre-return readings) IS `min(window_days − min_window_readings, k₃)`: four at spread sub-daily capture, two at daily or clustered capture.
Scope: a returning per-tier dataset while it holds fewer than `min_window_readings` judged-week days.
Not: the silent mornings after the residual, which other guards decide (HRV-26).
Pinned: none

**HRV-34.** A source change MUST NOT trigger any rule-level re-establishment, and a source-tier change MUST NOT collapse any SWC band.
Scope: every source change between tiers; the rule applies per per-tier dataset (T-06).
Not: the coverage-gap reset of HRV-73 (the global gap), the only reset that re-establishes.
Pinned: none
Why: decisions C03, C14 and C15 state what a source change does, and reserve re-establishment for the coverage gap.

**HRV-35.** The coverage-gap reset (`coverage_gap_reset`) MUST be global, measured over the whole series.
Scope: the series of every tier together.
Not: one tier's silence while another tier carries the series, which HRV-52 (the partition) assigns elsewhere.
Pinned: none

**HRV-36.** The coverage gap MUST take precedence over the reset report only.
Scope: a judged day on which both a coverage gap and an era boundary would be reported.
Not: the clipped baseline window, which HRV-74 (clip composition) composes.
Pinned: none

**HRV-37.** A per-tier dataset's SWC band MUST also be clipped, unreported, at an internal capture hole in its own baseline window, meaning more than `gap_reset_days` (21) silent local days of its tier, so 21 does not clip and 22 does.
Scope: every per-tier dataset, over the silent local days of its own tier inside its dataset baseline window.
Not: a silence of the whole series, which the coverage gap of HRV-73 (the global gap) resets.
Pinned: runcoach-api/tests/test_hrv_internal_hole_clip.py::test_a_bridged_internal_hole_clips_the_band_to_the_post_hole_readings

**HRV-38.** The era boundary (`tier_change_reset`) MUST be asked once per per-tier dataset over shared cross-tier facts: (a) the dataset's tier has ≥14 days in the gap-clipped baseline window; (b) it differs from the highest-fidelity tier with ≥14 days in `[D-126, D-67]`; (c) the two eras do not interleave over both windows and the judged week, within the stray tolerance of HRV-39 (strays).
Scope: every per-tier dataset on every judged day.
Not: the per-dataset hole clip, which HRV-37 (the hole clip) states separately.
Pinned: runcoach-api/tests/test_hrv_tier_change_per_dataset.py::test_the_era_boundary_ordering_key_keeps_its_three_terms
Why: decision C13 states the era clip and the hole clip as two clips, since the code runs both.

**HRV-39.** A stray IS a new-tier capture from the old era's first local day up to its last reading, or an old-tier capture after the new era's first, and strays MUST be pooled across both tiers.
Scope: every possible era boundary of HRV-38 (the era boundary).
Not: a capture simultaneous with the old era's last reading (HRV-79).
Pinned: none

**HRV-40.** The now-sustaining tier's pre-boundary readings MUST never be in its SWC band (the clip is unconditional).
Scope: every per-tier dataset with an era boundary.
Not: the reset report, which HRV-80 (the report condition) conditions.
Pinned: none

**HRV-41.** Clause (c)'s strays MUST be counted over every reading of every tier in `[D-66, D]`, gap-clipped or not, plus the previous window.
Scope: the stray count of HRV-38 (the era boundary).
Not: clause (a)'s count, which HRV-81 (clause a) keeps in the gap-clipped window.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_unclipped_stray_count_refuses_the_gap_created_era_boundary

**HRV-42.** An empty judged week MUST begin no reset, and a reset already in force MUST persist through it.
Scope: a judged week holding no reading of any tier.
Not: the verdict on that week, which reads unavailable under HRV-26 (the guard order).
Pinned: none

**HRV-43.** `reset_on` IS the era's true first day.
Scope: every reported reset of every per-tier dataset.
Not: the day the report first appears, which HRV-82 (the report lag) sets for a `tier_change` report and HRV-73 (the global gap) sets for a `coverage_gap` report.
Pinned: none

**HRV-44.** Exactly two mechanisms of the single-baseline rule MUST stay retired: `resolve_baseline_tier`'s role as cross-tier arbitration, and the `off_baseline_tier` exclusion.
Scope: the mechanisms IDEA-071 listed for the per-tier dataset model.
Not: the function `resolve_baseline_tier` itself, which survives with its tie-order pin.
Pinned: runcoach-api/tests/test_hrv_three_valued_retirements.py::test_each_pin_is_red_only_on_its_own_qualifiers_deletion

**HRV-45.** Two recency notions MUST be kept apart as different rules: the presentation fallback's tie-break by last read, and the admission gate of HRV-15 (the skip).
Scope: every use of recency in the HRV rule.
Not: the order of the fallback's clauses, which HRV-59 (the fallback order) sets.
Pinned: none

**HRV-46.** The HRV rule MUST key on `hrv_source_tier` alone and has no notion of device identity.
Scope: every reset and every selection in the HRV rule.
Not: per-unit sensor identity (F007), which is ingestion data only, so a device-change rule requires amending this rule.
Pinned: none

**HRV-48.** Each per-tier dataset MUST carry its own baseline mean, SD, `n`, `established` and SWC band, built from its own readings alone.
Scope: every per-tier dataset.
Not: an SWC band pooled across tiers, which HRV-06 (anti-mixing) forbids.
Pinned: none

**HRV-49.** Every capture later than the earliest on the same local day MUST be excluded from its per-tier dataset as `same_day_later_capture`.
Scope: every local day on which one tier holds more than one capture.
Not: a capture of another tier on that day, which feeds its own dataset (HRV-10).
Pinned: none

**HRV-50.** A per-tier dataset's SWC band MUST still be built and reported from two baseline readings up, established or not.
Scope: every per-tier dataset with at least two baseline readings.
Not: a verdict, which HRV-27 (the symmetric gate) withholds on an unestablished baseline.
Pinned: none

**HRV-51.** The reference maximum MUST be taken once, simultaneously, over every established dataset, while the candidates the recency gate strikes from MUST stay the judgeable datasets.
Scope: the recency gate of HRV-15 (the skip), on every judged day.
Not: a lone judgeable dataset exempt from the gate, since an established dataset read later can strike it.
Pinned: runcoach-api/tests/test_hrv_trend_series.py::test_probe_every_judgeable_dataset_can_be_skipped_at_once_since_t164

**HRV-52.** Silence MUST be partitioned by scope: the coverage-gap reset (HRV-35) covers the silence of the whole series, and the hole clip (HRV-37) and then the recency gate (HRV-15) cover one per-tier dataset's silence while another dataset carries the series.
Scope: every silence of one tier or of the whole series.
Not: the trailing-silence regime that HRV-53 (IDEA-093) leaves OPEN.
Pinned: none
Why: decision C10 scopes the partition, since the hole clip also acts on one tier's silence.

**HRV-53.** The 22–28-day trailing-silence regime of one per-tier dataset, which neither the coverage-gap reset, the hole clip nor the recency gate reaches, IS OPEN, owned by IDEA-093.
Scope: one tier falling silent at the end of its baseline window while another tier carries the series.
Not: an internal hole of more than 21 silent local days, which HRV-37 (the hole clip) clips.
Pinned: none

**HRV-54.** Section 3 MUST NOT compute or emit a confidence weight (weighting is deferred to Section 6's readiness fusion), and `datasets[]` MUST carry `fidelity_rank`.
Scope: every HRV trend response and every selection.
Not: Section 6's readiness fusion, which may weight the HRV input later.
Pinned: none

**HRV-55.** Every other dataset's SWC band, `n` and `established` MUST still be computed and reported.
Scope: every per-tier dataset other than the selected one.
Not: the verdict, which only the selected dataset supplies (HRV-20).
Pinned: none

**HRV-56.** Judgeability MUST never be consulted for disagreement, and a dataset with no SWC band or no week mean MUST NOT be named as disagreeing.
Scope: every reported per-tier dataset other than the selected one.
Not: the selected dataset itself, which cannot disagree with its own verdict.
Pinned: none

**HRV-57.** `datasets[]`, `selected_dataset`, `selected_reason`, `baseline` and `band` MUST still be reported as computed when the HRV verdict is `hrv_unavailable`.
Scope: every response whose HRV verdict is `hrv_unavailable`.
Not: the dissent list, which HRV-22 (empty dissent) empties.
Pinned: runcoach-api/tests/test_hrv_dataset_populations.py::test_week_not_representative_is_served_with_nothing_selected_and_names_no_dissenter

**HRV-58.** The cause alone MUST identify the third no-verdict state, a day that has not happened, and that state MAY coincide with either of the other two.
Scope: the three no-verdict states of HRV-23 (no-verdict states).
Not: the first two states, which whether a dataset is selected separates.
Pinned: none

**HRV-59.** The presented dataset MUST be, in order: the established dataset read last in the baseline window (ties by `n`, then fidelity); with none established, the densest by `n`; with no baseline reading of any tier, the densest in the judged week (ties to fidelity throughout).
Scope: every judged day on which no per-tier dataset is selected.
Not: the selected dataset, which HRV-14 (selection) chooses.
Pinned: none

**HRV-60.** The presented dataset IS never judgeable, so a verdict MUST NOT be conferred on it.
Scope: the presentation fallback of HRV-24 (the fallback).
Not: the presented dataset's own reading against its SWC band, which is still reported.
Pinned: runcoach-api/tests/test_hrv_unavailable_reason.py::test_the_fallback_confers_no_verdict_and_names_no_dissenter_even_when_its_own_week_reads_below

**HRV-61.** The first cause to fire MUST be the one reported as `unavailable_reason`.
Scope: every HRV verdict that is `hrv_unavailable`.
Not: `day_not_happened`, which overrides the pure rule's cause (HRV-62).
Pinned: none

**HRV-62.** `day_not_happened` MUST override whichever cause the pure rule reported, and MUST leave the selection and everything that produced it as computed.
Scope: every requested day after the athlete's local today.
Not: a day on or before the athlete's local today, which the pure rule alone decides.
Pinned: none

**HRV-63.** The withhold MUST be asked of every per-tier dataset as if it were the selected one.
Scope: every per-tier dataset on every judged day, selected, presented or neither.
Not: a withhold asked of the selected dataset alone.
Pinned: none

**HRV-64.** The returning dataset MUST hold at least `min_window_readings` of those later judged-week days for the withhold to fire.
Scope: the withhold of HRV-31 (the withhold).
Not: a dataset with fewer such days, which withholds nothing.
Pinned: none

**HRV-65.** The residual MUST be left unchanged, by the user decision recorded at H-18.
Scope: the residual mornings of HRV-33 (the residual).
Not: the question whether to close it, which HRV-66 (IDEA-092) leaves OPEN.
Pinned: none

**HRV-66.** Whether the residual's mornings should be closed IS OPEN, owned by IDEA-092.
Scope: the residual mornings of HRV-33 (the residual).
Not: the residual's formula, which HRV-33 (the residual) states.
Pinned: none
Why: decision C09 homes the question with an open owner, since the sprint it was carried to has shipped.

**HRV-67.** Adopting a never-used tier MUST start that tier's dataset from nothing, subject to the withhold (HRV-31), and abandoning a tier MUST let its dataset age out of the window.
Scope: every source change between tiers.
Not: a return to a previously established dataset, which HRV-68 (the return) governs.
Pinned: none

**HRV-68.** A returning per-tier dataset MUST be selected, by the fidelity order of HRV-14 (selection), once it is judgeable and not skipped by the recency gate.
Scope: a per-tier dataset whose tier resumes after a silence while another dataset carried the series.
Not: a never-used tier, which HRV-67 (adoption) governs.
Pinned: none

**HRV-69.** Until a returning per-tier dataset is selected, the recency gate MAY skip it, and its later judged-week days MAY withhold another dataset's verdict (HRV-31).
Scope: a returning per-tier dataset that is not yet selected.
Not: a returning dataset that is judgeable and not skipped, which HRV-68 (the return) selects.
Pinned: none

**HRV-70.** Once more than `gap_reset_days` (21) silent local days of its tier lie inside a returning dataset's own baseline window, its pre-silence readings MUST be clipped by the hole clip (HRV-37), while a trailing silence clips nothing (IDEA-093).
Scope: a returning per-tier dataset.
Not: a silence of the whole series, which the coverage-gap reset (HRV-73) clips.
Pinned: none

**HRV-71.** A return to a previously established per-tier dataset IS free, meaning it costs neither the skip of HRV-15, the hole clip of HRV-37 nor a withhold on its account under HRV-31, only when it follows 21 or fewer silent local days of its tier and the recency gate does not skip it.
Scope: a returning per-tier dataset.
Not: a return the recency gate skips, or one after more than 21 silent local days, which HRV-69 and HRV-70 (the return costs) govern.
Pinned: none

**HRV-72.** A `tier_change` reset IS an era boundary on one per-tier dataset: its pre-boundary readings are clipped from its own SWC band (HRV-40), and it is reported with the lag of HRV-82 (the report lag).
Scope: every reported `tier_change` of every per-tier dataset.
Not: the coverage-gap reset, the only re-establishment (HRV-34).
Pinned: none

**HRV-73.** When the whole series (every tier together) is silent for more than `gap_reset_days` (21) silent local days (21 does not reset; 22 does), the baseline window of every per-tier dataset MUST be clipped at the resumption and `coverage_gap` MUST be reported from the resumption day, R+0.
Scope: the series of every tier together.
Not: one tier's internal hole, which HRV-37 (the hole clip) clips unreported.
Pinned: none

**HRV-74.** The gap clip and the era clip MUST compose as the later first day.
Scope: every per-tier dataset with both a gap clip and an era clip.
Not: the reset report, over which the coverage gap takes precedence (HRV-36).
Pinned: none

**HRV-75.** The recency gate MUST apply to neither clause (a) nor clause (b) of the era boundary.
Scope: the era boundary of HRV-38 (the era boundary).
Not: selection, where the recency gate of HRV-15 (the skip) applies.
Pinned: none

**HRV-76.** The cross-tier era clip (HRV-40) and the per-dataset hole clip (HRV-37) MUST be applied as two separate clips, composed with the gap clip (HRV-73) as the latest first day.
Scope: every per-tier dataset's baseline window.
Not: the reset report, which only the era clip and the coverage gap produce.
Pinned: runcoach-api/tests/test_hrv_internal_hole_clip.py::test_probe_the_hole_is_scanned_on_the_era_clipped_window_and_the_tier_change_report_stays
Why: decision C13 separates the two clips, since the code runs both and composes them.

**HRV-77.** The boundary-existence half of the stray tolerance (fewer than 14 distinct days of strays) MUST decide whether an era boundary exists.
Scope: every possible era boundary of HRV-38 (the era boundary).
Not: the report, which the week half decides (HRV-78).
Pinned: none

**HRV-78.** The week half (fewer than 3 stray days in the judged week) MUST decide the report and MUST be the first ordering term of `_era_boundary`'s key, then the fewest stray days, then the later boundary.
Scope: every admitted era boundary of HRV-38 (the era boundary).
Not: whether a boundary exists, which the boundary-existence half decides (HRV-77).
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of

**HRV-79.** Simultaneous captures MUST NOT count as strays.
Scope: a capture at the very instant of the old era's last reading.
Not: a capture at any other instant inside the stray span of HRV-39 (strays).
Pinned: none

**HRV-80.** The reset report (`reset_reason`/`reset_on`) MUST additionally require that the other tier was not in use in the judged week.
Scope: every era boundary of every per-tier dataset.
Not: the clip, which HRV-40 (the unconditional clip) applies regardless.
Pinned: none

**HRV-81.** Clause (a) MUST still read the gap-clipped window.
Scope: clause (a) of HRV-38 (the era boundary).
Not: clause (c)'s stray count, which HRV-41 (unclipped strays) widens.
Pinned: none

**HRV-82.** A `tier_change` reset's report lag behind the reset IS `min_baseline_readings + 7 − 1` = 20 days, accepted as latency rather than inaccuracy because an earlier report would be a prediction.
Scope: every reported `tier_change` reset of every per-tier dataset.
Not: the reported date itself, which HRV-43 (reset_on) makes the era's true first day.
Pinned: none

**HRV-83.** Everything else on IDEA-071's list MUST be kept, re-derived or redeployed, apart from T093's week-coverage half, which judgeability subsumes.
Scope: the mechanisms IDEA-071 listed for the per-tier dataset model.
Not: the two retired mechanisms of HRV-44 (retirements).
Pinned: none

**HRV-84.** Replacing a device within the same tier MUST fire no reset and cost no silent days.
Scope: every device replacement within one `hrv_source_tier`.
Not: a change of tier, which HRV-72 (tier_change) governs.
Pinned: none

**GATE-01.** Every §1.7-forbidden rate MUST be measured against shipped F005 on every sweep, with capture density varied on both datasets independently.
Scope: every rate in the forbidden direction that the release gate computes.
Not: the dataset-flip rate, which rule GATE-06 measures.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_no_1_7_rate_worsens_against_shipped_f005
Why: decision C06 makes §1.7 absolute, so a forbidden-direction rate is gated on every sweep.

**GATE-02.** The system MUST NOT add hysteresis to dataset selection, and the worsened dataset-flip set MUST remain the 80 pinned `walk_flips` cells at `car_density = 2wk`.
Scope: the dataset-flip rate, measured against shipped F005 on every sweep the same way as rule GATE-01.
Not: a worsened cell outside that pinned set, which fails the gate.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned
Why: the no-hysteresis decision is recorded at H-37 and is revisited only if the unequal-dispersion measurement of IDEA-089 (b) shows harm.

**GATE-03.** The constant `recency_tolerance_days` = 28 MUST rest on the two reasons of rule HRV-16 alone, (i) four judged weeks and (ii) greater than `gap_reset_days` (21), until a re-measurement against per-tier datasets is recorded.
Scope: every citation of the recency tolerance's basis.
Not: a measured tolerance bracket, which rule GATE-07 excludes for per-tier datasets.
Pinned: none
Why: decision C37 records that no re-measurement result exists, and T-07 reserves the word band for the HRV SWC band.

**GATE-04.** Any worsening of such a rate MUST block release, save a named, counted, test-pinned exception that rule PRIN-15 lists with its owning open IDEA, and such an exception MUST NOT grow.
Scope: every §1.7-forbidden rate rule GATE-01 measures.
Not: a rate at parity with shipped F005, which is no worsening.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_no_1_7_rate_worsens_against_shipped_f005
Why: decision C06 lets a forbidden-direction population ship only as such an exception, never on a rarity argument.

**GATE-05.** `DEFERRED_EXCEPTION` (c = 4, 5, 64 gated rows) IS such an exception, owned by IDEA-087.
Scope: the gated rows at c = 4 and c = 5 under the independent-instruments fixture.
Not: any row outside those 64, which blocks release when it worsens.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names

**GATE-06.** The dataset-flip rate of a three-days-a-week wearer MUST be measured against shipped F005 on every sweep, the same way as the rates of rule GATE-01.
Scope: every sweep the release gate runs.
Not: the decision on hysteresis, which rule GATE-02 states.
Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned

**GATE-07.** The `[18, 44]` tolerance bracket MUST NOT be cited for per-tier datasets, because it was measured against the fused single-baseline HRV band.
Scope: every citation of a measured tolerance bracket for `recency_tolerance_days`.
Not: the value 28 itself, which rule GATE-03 grounds.
Pinned: none

**GATE-08.** Whether a strap-morning Health Snapshot is an independent instrument or the same beats post-processed by the watch IS unmeasured, so the disagreement rate may measure vendor processing until it is.
Scope: every disagreement rate between the strap and Health Snapshot datasets.
Not: a claim that the two are independent instruments.
Pinned: none

**FIG-01.** After a coverage-gap re-establishment the athlete traverses 20 days beneath `min_baseline_readings` (`R+0 .. R+19`), and the spec MUST publish that figure.
Scope: a coverage-gap re-establishment of the series (rule HRV-35), not a source-tier change.
Not: the cost of a source-tier change, which rule FIG-02 states.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_establishment_delay_after_a_reset_is_twenty_days
Pinned: runcoach-api/tests/test_spec_cost_figures.py::test_the_establishment_delay_is_stated_at_each_site

**FIG-02.** A clean, gapless source-tier change costs 18 silent days (`R+2 .. R+19`), closed form `min_baseline_readings + 7 − min_window_readings` = 18, with `established` true throughout, and the spec MUST publish that figure.
Scope: a clean, gapless source-tier change at daily capture on the new tier.
Not: the 20 days of a coverage-gap re-establishment, which rule FIG-01 states.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_tier_change_silence_is_eighteen_days_and_names_no_reset_on_any_of_them
Pinned: runcoach-api/tests/test_hrv_unavailable_causes.py::test_the_tier_change_silence_is_stated_beside_the_coverage_gap_figure
Why: the quiet ends when the new tier reaches `min_baseline_readings` distinct days at or before D−7, which is `R + (min_baseline_readings − 1) + 7` = R+20, and the 20-day figure of rule FIG-01 is true of a coverage gap and false of a source-tier change.

**FIG-03.** The spec MUST publish that during a layoff longer than 21 days, days 1–4 are judged and days 5–22 are silent (18).
Scope: a layoff of the whole series longer than `gap_reset_days`.
Not: the silence of a source-tier change, which rule FIG-02 states.
Pinned: none
Why: that 18 is a coverage-gap figure, and its equality with the tier change's 18 in rule FIG-02 is a coincidence.

**FIG-04.** The spec MUST publish that at a daily-capture return the withhold costs 72 of 2,050 swept geometries (`hrv_normal → hrv_unavailable`).
Scope: a daily-capture return only, since a figure measured at one capture density holds only there (rule DOC-12).
Not: a return at spread or sub-daily capture.
Pinned: none

**FIG-05.** The spec MUST NOT publish an aggregate HRV silence rate.
Scope: every sum of the HRV silences the spec names.
Not: the individual silence figures, which rules FIG-01 to FIG-04 publish.
Pinned: none
Why: decision C09 leaves the question of rule FIG-11 open under a named owner rather than answered by a rate.

**FIG-06.** At every-second-day capture on the new tier the tier-change silence IS 29 days, and the spec MUST publish that figure.
Scope: a clean, gapless source-tier change at one capture every second day on the new tier.
Not: daily capture, which rule FIG-02 prices.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_tier_change_silence_grows_with_the_new_tiers_capture_density

**FIG-07.** The spec MUST publish that the silent days of a source-tier change carry `week_not_representative` ×2 and `week_too_thin` ×16.
Scope: the 18 silent days of rule FIG-02.
Not: a seventh unavailable cause, which the response does not carry.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_tier_change_silence_is_eighteen_days_and_names_no_reset_on_any_of_them

**FIG-08.** The spec MUST publish that keeping the old device recording removes the silence and the `tier_change` report together.
Scope: a source-tier change across which the old device keeps recording.
Not: a clean switch, which rule FIG-02 prices.
Pinned: runcoach-api/tests/test_hrv_trend_reset.py::test_the_tier_change_silence_is_zero_when_the_old_tier_outlasts_candidacy

**FIG-09.** The spec MUST publish the layoff's total silence as 38 days, its 18 silent days plus the 20 re-establishment days after it.
Scope: a layoff of the whole series longer than `gap_reset_days`.
Not: a layoff of 21 days or fewer, which resets nothing.
Pinned: none

**FIG-10.** The spec MUST publish that at a daily-capture return five of the athlete's first seven mornings back are silent.
Scope: a daily-capture return only (rule DOC-12).
Not: a return at spread or sub-daily capture.
Pinned: none

**FIG-11.** The question of when a rule that mostly says nothing stops being conservative and starts being useless IS OPEN, owned by IDEA-092.
Scope: every rule in the HRV gate that can withhold a verdict.
Not: a published answer, which rule FIG-05 excludes.
Pinned: none
