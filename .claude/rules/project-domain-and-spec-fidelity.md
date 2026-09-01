---
paths: ["**/*"]
---
# Domain Vocabulary & Spec-Fidelity Rules

This project implements the spec in `specification/spec/01`–`09` against the research corpus in `specification/research/`, with `specification/research/00-design-decisions.md` as the decision authority. These rules exist because the domain has precise vendor-neutral terminology and hard quarantine/safety boundaries that an agent can easily blur by reaching for a plausible-sounding shortcut.

## Domain Vocabulary

Use these terms consistently — do not substitute synonyms or invent new ones:

- **Canonical raw stream** — vendor-neutral ingested data (HR, RR intervals, pace/GPS, cadence, barometric altitude, running dynamics, power). Never call this "processed" or "clean" data.
- **Quarantined vendor-derived sidecar** — vendor proprietary estimates (VO2max, Training Status, Training Readiness, Body Battery, Performance Condition). Ingested and stored, but never fed into a decision.
- **rTSS** — per-session load, primary metric on a valid GAP stream.
- **GAP** — grade-adjusted pace (Minetti cost-of-gradient curve). The universal downstream pace representation.
- **CTL / ATL / TSB** — Chronic Training Load (42-day EWMA), Acute Training Load (7-day EWMA), Training Stress Balance = CTL − ATL. Together: the PMC (Performance Management Chart).
- **HRV trend** — 7-day rolling ln(rMSSD) vs a ±0.5·CV smallest-worthwhile-change (SWC) band. Never a single-reading gate.
- **ACWR** — Acute:Chronic Workload Ratio, advisory context/spike flag only (~0.8–1.5 band). Never a hard gate.
- **Durability** — EF (efficiency factor)/decoupling drift over the back third of long runs, tracked as a within-athlete trend, flagged "emerging" not asserted.
- **Determinant profile** — CS/D′, vVO2max, functional threshold pace, EF economy proxy, durability, individual endurance exponent (Riegel *b*) — each carries a trend and a confidence, never a bare point value.
- **Arbitration ladder** — safety → readiness → short-term guardrails → recent re-anchoring → long-term ambition. This is the fixed precedence order for the five-timescale adaptation loop; do not reorder or shortcut it.

## Spec-Fidelity Guardrails (agent slop mitigation)

- **Never wire a quarantined sidecar metric into decision logic.** Body Battery, Training Readiness, VO2max estimate, and Performance Condition are display-only. If a feature seems to need one as a convenient shortcut, that's a signal the raw-stream-derived equivalent is missing — build that instead, or ask.
- **Never invent or approximate formula constants.** CTL/ATL windows, the HRV SWC multiplier, the ACWR band, taper duration/volume-cut percentages, etc. are specified in `specification/spec/03`–`07` and `research_00` Part 3's register. Cite the section; don't guess a "reasonable" number.
- **ACWR is advisory only — never a hard gate.** Don't add a blocking check keyed on ACWR crossing a threshold.
- **Safety hard-flags fire deterministically, regardless of framing.** The conversational layer (`spec/08`) translates language and explains decisions — it must never become a second adaptation path or a way to talk the system past a safety guardrail.
- **Every applied adaptation must be logged** (inputs + the rule that fired) per `spec/09`. Don't ship an adaptation path with logging as a follow-up TODO — the decision log is not optional plumbing.
- **Down-regulate freely, up-regulate cautiously.** This asymmetry (`research_00` §1.7) is a design invariant, not a tunable default — don't smooth it away for symmetry's sake.
- **When a spec section and a research doc appear to conflict, `research_00` governs** — note the conflict rather than silently picking one side.
- **Freedom-to-operate:** only use open, public-domain math (Minetti, standard EWMA/PMC formulas, published CS models). Do not reproduce a specific patented/proprietary construction described in `research/06` even as an "equivalent" — see that doc's guardrails before implementing anything that resembles a named commercial feature.
