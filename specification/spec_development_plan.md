# Run Coaching System — Phase 2 Spec Development Plan

*Living tracker for building the Phase-2 specification across multiple chat sessions. Because a fresh session starts with no memory of prior sessions and sees only what is saved in the project, this document is the hand-off record. **Every session: read this plan first; update and re-save it before stopping.** It is only as current as the last save.*

**Last updated:** 2026-08-31 (Session 12 — specification APPROVED by the user)
**Current phase:** Phase 2 **COMPLETE and USER-APPROVED — the specification is build-ready.** All nine sections are drafted, reviewed, open-items-resolved, and approved. The next phase is implementation (Phase 3), whenever the user chooses to start it.
**Outline approved:** ☑ **approved by the user, 2026-08-31** (`spec_outline.md` and all nine sections).

---

## ✅ Specification approved (2026-08-31)

**Chris approved the specification on 2026-08-31.** This is the project's Definition of Done for Phase 2: a competent implementer (human or Claude Code) can build the system from `spec/01`–`spec/09` without doing their own physiology, device, or coaching research — every model, formula, data field, and decision rule is either specified or cited to a source the spec points to (`research/01`–`research/06`, with `research/00` as the decision authority).

**What "approved" covers:** the outline (`spec_outline.md`) and all nine specification sections (`spec/01`–`spec/09`), together with the decision authority (`research/00`) and mechanism research (`research/01`–`research/06`) they rest on, and the dispositions recorded in `future/future-directions.md`.

**The one post-launch tuning item (not a blocker):** the Section 6 CTL-rise range (§6.2.2) is ratified and live in the spec, and the `research/00` Part 3 register carries it as a row marked PROVISIONAL (REG-19) until field data refines the numeric range. Approval does not change that — it is a post-launch tuning item, not an unfinished spec item.

**Handing off to implementation:** an implementer should start at `research/00` (the constitution — Part 1 conflict rules, Part 2 architecture, Part 3 parameter register, Part 4 FTO guardrails), then read `spec/01`→`spec/09` in order (each section states what it owns, its boundaries, and its hand-offs). The closed loop is: inputs (§1) → raw data (§2) → derived metrics (§3) → state estimate (§4) → plan (§5) → adaptation (§6) → recovery/taper (§7) → conversational surface (§8) → loop closure / decision log / explainability (§9).

---

## ⚠️ Persistence incident found in Session 3 (read this)

Session 2's change log claimed Sections 1 and 2 were saved to the project. **Neither was actually in the project at the start of Session 3.** The Session-2 saves did not persist (cause unknown; possibly a `local_path`-outside-working-directory failure reported as success, or a write that never committed). Section 1 was re-drafted in Session 3, Section 2 in Session 4, Sections 3–9 in Sessions 5–11 — each confirmed present via `project_info`/`project_read`/`project_search` after the review-fix pass. **Reminder: `local_path` must be inside `/home/claude`, NOT `/tmp` — a `/tmp` path is rejected outright, the likely root cause of the Session-2 silent failure. After every `project_write`, verify the doc is actually in the project before marking anything done.**

---

## How we worked through Phase 2

- One section per turn (or a coherent part), drafted in prose, grounded in the docs the outline names for that section.
- Section order followed dependencies; a section was not drafted before the sections it depends on.
- Sections 3, 4, and 6 (derived-metric formulas, state model, adaptation logic) were the highest-risk for subtle internal inconsistency and got an explicit review pass.
- `research_00` is the decision authority; any apparent conflict with a mechanism doc resolves to `research_00` and is noted in the spec.
- **Review pass:** every drafted section got an independent-subagent review pass (a fresh general-purpose agent re-reading the outline, cited research, and prior sections cold). Verdicts were "minor fixes needed, no blockers"; fixes applied before final save.

## Persistence protocol

- `project_write` writes directly into the project (inline `content`, or `local_path` inside `/home/claude`, **NOT `/tmp`**). Verify every save landed (`project_info`/`project_read`/`project_search`). At the start of each session, read this plan plus any relevant docs before writing.

---

## Section status

| # | Section | Depends on | Status | Notes |
|---|---------|-----------|--------|-------|
| 1 | Scope, System Inputs, and the Pace Target | — | ✓ **Approved** | `spec/01-...`. Subagent review + 6 fixes. Composition-order open item resolved (`research_00` Part 3). **User-approved 2026-08-31.** |
| 2 | Canonical Data Schema and Ingestion | 1 | ✓ **Approved** | `spec/02-...`. Subagent review. Both open items (morning-HRV capture; course-geometry default) resolved (`research_00` Part 3). **User-approved 2026-08-31.** |
| 3 | Derived-Metric Formulas | 2 | ✓ **Approved** | `spec/03-...`. Subagent review. Women's HR-TRIMP coefficient corrected in `research_05` §2.1. **User-approved 2026-08-31.** |
| 4 | Physiological State Model | 3 | ✓ **Approved** | `spec/04-...`. Subagent review + 6 fixes. Cold-start estimators back-filled into `research_00` §3.2. **User-approved 2026-08-31.** |
| 5 | Training-Plan Generation | 4 | ✓ **Approved** | `spec/05-...`. Subagent review + 6 fixes. Four register open items resolved/dispositioned; §5.7.1→§5.5.4 slip resolved. **User-approved 2026-08-31.** |
| 6 | Adaptation Logic | 3,4,5 | ✓ **Approved** | `spec/06-...`. Subagent review + 5 fixes. Acclimation-block moved to future-directions; CTL-rise range fixed + provisionally ratified in-spec (§6.2.2), carried in the `research_00` register as a PROVISIONAL row (REG-19) until field data refines it. **User-approved 2026-08-31.** |
| 7 | Recovery and Taper | 6 | ✓ **Approved** | `spec/07-...`. Subagent review + 3 fixes. Recovery-week depth + race-day form band ratified + back-ported (`research_00` Part 3). **User-approved 2026-08-31.** |
| 8 | Conversational Coach Interface + Autonomy Boundary | 6 | ✓ **Approved** | `spec/08-...`. Subagent review + 2 fixes. No open items. **User-approved 2026-08-31.** |
| 9 | Loop Closure, Decision Log, Explainability | all | ✓ **Approved** | `spec/09-...`. Subagent review + 2 fixes. Both open items (sidecar-divergence surfacing threshold; retention granularity) ratified + back-ported (`research_00` Part 3). **User-approved 2026-08-31.** |

Status legend: ☐ Not started · ◐ Drafting · ▣ Drafted · ✓ Reviewed · ✓ **Approved**

---

## Open questions — register (all worked)

Every logged open item was worked across 2026-08-31 (the back-port batch + two follow-ups) and the specification was then approved. Nothing remains in the "still open" state.

### Resolved in the 2026-08-31 back-port batch

- **Environmental-modifier composition order (Section 1, §1.4.5)** — RESOLVED. `research_00` Part 3 (multiplicative; altitude → heat/humidity → wind → grade).
- **Morning/resting HRV capture protocol (Section 2, §2.4.5)** — RESOLVED. `research_00` Part 3 *Resting-HRV capture cadence* row; reconciled with §3.3 tiering.
- **Course-geometry ingestion resolution default (Section 2, §2.5)** — RESOLVED. `research_00` Part 3.
- **Women's HR-TRIMP correction to `research_05` §2.1 (Section 3, §3.4.1)** — RESOLVED. Corrected to 0.86·e^(1.67·ΔHR_ratio) for women.
- **Non-exercise cold-start estimator (Section 4, §4.4.2)** — RESOLVED (researched + back-filled). Uth–Sørensen + Jackson into `research_00` §3.2. Human-coach candidate.
- **LT1 / Zone-1 boundary derivation (Section 5, §5.4.2)** — RESOLVED (researched + path recommended). `research_00` §3.4 — fraction-of-threshold for v1; DFA-α1 = 0.75 the future determinant. Human-coach candidate.
- **Determinant-addressability scoring (Section 5, §5.7.2)** — RESOLVED. `research_00` Part 3. Human-coach candidate.
- **Base/build/peak phase-length split (Section 5, §5.5.1)** — RESOLVED. `research_00` Part 3. Human-coach candidate.
- **Strength/plyometric supporting work (Section 5)** — RESOLVED (dispositioned). Future-directions entry; human-coach candidate.
- **Heat/altitude acclimation-block prescription (Section 6 §6.9 / Section 7 §7.8)** — RESOLVED (dispositioned). Future-directions; out of scope v1. Human-coach candidate.
- **Recovery-week depth default (Section 7, §7.2.2)** — RESOLVED. `research_00` Part 3 (~20–40% cut). Future research + human-coach candidate.
- **Race-day target form band (Section 7, §7.4.1)** — RESOLVED. `research_00` Part 3 (TSB ~+5 to +25). Future research + human-coach candidate.

### Resolved in the 2026-08-31 follow-ups

- **Section 5 §5.7.1 → §5.5.4 cross-reference slip** — RESOLVED. §5.5.4 broadened to cover goal-race conditions; pointer corrected; Section 6 §6.9 aligned.
- **Ramp-rate CTL-rise range (Section 6, §6.2.2)** — RESOLVED (fixed + provisionally ratified in-spec). ~+5 CTL/week soft target, range +3–7, hard ceiling +8 (Friel/TrainingPeaks-grounded). `research_00` Part 3 carries it as a row marked PROVISIONAL (REG-19) until field data refines it.
- **Sidecar-divergence surfacing threshold (Section 9, §9.6)** — RESOLVED. `research_00` Part 3 (one own-estimate CI, or ~10%; governs only surfacing).
- **Decision-log / raw-stream retention granularity (Section 9, §9.3.5)** — RESOLVED. `research_00` Part 3, engineering default (records indefinitely; raw streams on a tunable window).

### The one post-launch tuning item (not a blocker)

- **Section 6 CTL-rise range** — the value is live and ratified in spec §6.2.2, and its `research_00` Part 3 register row is marked PROVISIONAL (REG-19) until field data refines the numeric range. Revisit once the product has real athlete data.

---

## Resume-here note

**Phase 2 is complete and USER-APPROVED (2026-08-31); the specification is build-ready.** All nine sections (`spec/01`–`spec/09`) are drafted, subagent-reviewed, open-items-resolved, and approved. `research_00` Part 3 carries eleven ratified spec-introduced defaults; `research_05` §2.1 is corrected; `future/future-directions.md` holds the deferred ideas (LT1 future determinant, strength/plyometric supporting work, heat/altitude acclimation blocks out-of-scope for v1, and a consolidated coach-in-the-loop entry).

**Next steps (the user's call):**
1. **Phase 3 — implementation.** The spec is ready for Claude Code to build from. A natural first move is a build plan / architecture pass that reads `research/00` then `spec/01`–`spec/09` and proposes the module/data-model breakdown; nothing in the spec needs further research to start.
2. **Post-launch tuning:** refine the Section 6 CTL-rise range and its PROVISIONAL register row (REG-19) once field data exists.
3. **Future iterations:** the `future/future-directions.md` backlog (only after v1 is tested with real athletes).

**If the user requests changes to an approved section**, edit that section's doc in place (`project_read` → change → `project_write` the full content to the same path), re-verify the save, re-run the subagent review if the change is substantive (Sections 3/4/6 remain the review-required trio), and note that the change post-dates approval.

---

## Change log

- **Session 1 (2026-08-25):** Confirmed Phase 1 complete. Created spec outline (9 sections) and this plan. Established persistence protocol.
- **Session 2 (2026-08-25):** Drafted Sections 1 and 2 and *believed* both saved. **Session 3 discovered neither persisted.**
- **Session 3 (2026-08-26):** Discovered the persistence failure. Re-drafted **Section 1**, verified, subagent review, 6 fixes. Tightened the persistence protocol.
- **Session 4 (2026-08-26):** Re-drafted **Section 2** (§2.5 closes the `course_profile` forward ref). Verified, reviewed, fixed.
- **Session 5 (2026-08-26):** Drafted **Section 3**. Verified, reviewed; women's HR-TRIMP corrected; NGP + load common-scale stated in full.
- **Session 6 (2026-08-26):** Drafted **Section 4**. Verified, reviewed, six fixes.
- **Session 7 (2026-08-26):** Drafted **Section 5**. Verified, reviewed, six fixes.
- **Session 8 (2026-08-27):** Drafted **Section 6** (review-required). Verified, reviewed, five fixes.
- **Session 9 (2026-08-27):** Drafted **Section 7**. Verified, reviewed, three fixes.
- **Session 10 (2026-08-27):** Drafted **Section 8**. Verified, reviewed, two fixes.
- **Session 11 (2026-08-27):** Drafted **Section 9** (final section). Verified, reviewed, two fixes. **Phase 2 drafting COMPLETE.**
- **Session 12 (2026-08-31):** Worked the **open-items back-port batch** (seven defaults into `research_00` Part 3; women's HR-TRIMP fix in `research_05` §2.1; cold-start estimators back-filled into `research_00` §3.2; LT1 recommended path in `research_00` §3.4; future-directions entries + coach-in-the-loop). **Follow-up A:** fixed the Section 5 §5.7.1→§5.5.4 slip; fixed + provisionally ratified the Section 6 CTL-rise band (register back-port deferred). **Follow-up B:** ratified + back-ported the two Section 9 items into `research_00` Part 3. **Then: Chris APPROVED the specification.** Phase 2 is complete and build-ready; recorded the approval across this plan. Remaining: Phase 3 implementation (user's call) + the one deferred CTL-band register back-port (post-launch).
