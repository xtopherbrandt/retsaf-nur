# Decision Note — Conversational Coach Interface

**Status:** Accepted (design decision, pending formalization in the Phase-2 spec)
**Date:** 2026-08-25
**Governing authority:** `research/00-design-decisions.md` (this record conforms to it, DEC-01; see the autonomy rules AUT-01–AUT-06, the goal-contract rules GOAL-01–GOAL-05 and the record rule DOC-03 there)
**Context docs:** `research/05-data-to-adaptation.md`, `research/03-subjective-injury-recovery.md`, `research/06-competitive-landscape-patents.md` §11

## Decision

The system will include a **Conversational Coach Interface**: a natural-language chat surface through which the athlete can ask the coach to explain its decisions and can supply life constraints, injury/soreness reports, and plan-change requests in free text. It is included as a product differentiator — it dissolves the opacity of the underlying formulae and tables, and lets the athlete negotiate the coming days around real-life constraints and niggling injury.

## Guiding principle — a new I/O layer, not a new decision layer

The chat interface sits **on top of** the existing closed loop (`research/05` §6); it does not replace or duplicate any part of it. The deterministic five-timescale adaptation engine remains the single source of truth. The language model's role is **translation in both directions** — turning athlete free-text into the structured inputs the engine already consumes, and turning the engine's computed state and decisions into plain-language explanation. The LLM proposes and explains; the engine decides. This preserves the project's raw-over-derived, transparent-formula philosophy: chat must never become a second, opaque adaptation path, and it never touches the derived-metric formulas or the state-model math (those stay deterministic and owned).

## How it hooks into the existing architecture

**Read / explain direction (transparency).** `research/05` §5.6 already requires that *every applied decision is logged with its inputs and the rule that fired.* The chat layer consumes that log. The decision is to **promote that log from an implied byproduct to a first-class, queryable explanation record**, so the chat can answer "why is today easy?" by reading the record plus the current state model, the derived metrics, and the gap-to-goal decomposition (§4) — presenting numbers the engine already computed, not inventing new ones.

**Write / negotiate direction (constraints and reports).** Free-text is parsed into structured inputs that route to existing entry points, not a new one:

- **Life constraints** (travel, unavailable days, per-day time budget) are scheduling constraints on the **short-term weekly-microcycle loop** (`research/05` §5.2). This requires a new, explicit **athlete-availability / constraints model** for that loop to consume; the weekly planner then re-solves within its existing ramp-rate and monotony guardrails and reports back what moved.
- **Injury / soreness / subjective feedback** become a natural-language **front-end to the instruments already specified in `research/03`** — the pain traffic-light with localized-pain mapping, the five-item wellness questionnaire, session-RPE — feeding injury-risk (§2.5) and readiness (§2.4). The parser produces the same structured fields the forms would (e.g. pain: location, 0–10 intensity, overnight-settle, week-over-week trend).

## Guardrails (non-negotiable)

- All chat-originated plan changes route through the **same §5.6 arbitration** as every other signal.
- The **safety override and injury hard-flags fire deterministically**, regardless of how the conversation is framed. The LLM cannot talk the system past a red flag (bone-stress-pattern pain, night pain, systemic illness, RED-S), and the athlete cannot talk the system into unsafe loading. Escalation pathways in `research/03` §6 remain authoritative.
- Chat is bounded by the engine's authority: it can supply inputs and request changes, but cannot override guardrails or write directly to metrics/state.

## Relationship to the autonomy posture (resolved)

The autonomy question that `research/05` §7 raised — *"how much autonomy before athlete confirmation"* — is **resolved** in the decision authority, `research/00-design-decisions.md` AUT-01 and GOAL-01: the system is **fully autonomous and applies plan changes without a confirmation step** (apply-and-notify). That autonomy has exactly two exceptions, both matters the system does not own (`research/00` AUT-02): the **goal contract**, the three athlete-owned fields `goal_pace_target`, `race_date` and `distance_m`, which the system may propose to change but never changes itself (GOAL-02); and, on the **safety pathway**, the athlete's own clinical action and return-to-run clearance. The safety override itself is the system's, applied automatically and deterministically, with the instructions the athlete must act on (AUT-08). Consequently, **the Conversational Coach Interface is not a gating or confirmation mechanism.** Chat carries only athlete input and explanation (AUT-04): it lets the athlete *probe* an applied change to understand why it happened (explanation on demand, not acknowledgment); it carries life and scheduling constraints, which route into the weekly-microcycle loop as inputs; it carries injury, soreness and subjective reports, which route to the `research/03` instruments; and it carries plan-change requests, which route through the arbitration ladder. An "acknowledged-notification" checkpoint for major changes was considered and deliberately rejected. (This supersedes an earlier framing of this note that described chat as a checkpoint for "awareness/confirmation.")

## Implications for the Phase-2 specification

Add a dedicated module — **Conversational Coach Interface** — defined by three things:

1. The **explanation / decision-log record** it reads from (formalize the §5.6 log as a queryable structure).
2. A new **athlete-availability / constraints model** consumed by the weekly-microcycle planner (§5.2).
3. The **natural-language ↔ structured-input contract**: a schema for the recognized input types (constraint, injury report, subjective report, plan-change request) plus the rule that every write is bounded by the engine's authority and §5.6 arbitration.

The module references `research/03` and `research/05` rather than repeating them.

## Open follow-up — resolved

The freedom-to-operate cross-check against the patent landscape is complete: see `research/06-competitive-landscape-patents.md` §11. Summary: conversational coaching is a crowded, decades-old patent space, but our architecture (LLM confined to explanation and translation; the deterministic engine decides; chat is not a gate) is distinguishable from the coaching-specific claims — the closest being Philips US10504379B2, which turns on the agent *generating/personalizing* the plan, which our LLM explicitly does not do. The practical guidance: keep training decisions in the deterministic engine, avoid describing the LLM as "generating/personalizing/deciding" coaching, and do not rely on "a conversational coach" as the differentiator — the differentiator is the transparent, raw-data-derived engine underneath. This is engineering research, not legal advice; FTO must be confirmed with counsel.
