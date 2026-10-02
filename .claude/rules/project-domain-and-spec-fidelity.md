---
paths: ["**/*"]
---
# Domain Vocabulary & Spec-Fidelity Rules

This project implements the spec in `specification/spec/01`–`09` against the research corpus in `specification/research/`, with `specification/research/00-design-decisions.md` as the decision authority. These rules exist because the domain has precise vendor-neutral terminology and hard quarantine/safety boundaries that an agent can easily blur by reaching for a plausible-sounding shortcut.

## Domain Vocabulary

Use these terms consistently — do not substitute synonyms or invent new ones:

- **Canonical raw stream** — vendor-neutral ingested data (HR, RR intervals, pace/GPS, cadence, barometric altitude, running dynamics, power). Never call this "processed" or "clean" data.
- **Quarantined vendor-derived sidecar** — the metrics on the quarantine list (`research_00` PRIN-20): Garmin/Firstbeat VO2max, Training Status, Training Readiness, Body Battery, Performance Condition, and the HRV Status Balanced/Unbalanced/Low classification. Ingested and stored, but never fed into a decision. A numeric resting rMSSD is not on the list: it is an HRV input (PRIN-10).
- **rTSS** — per-session load, primary metric on a valid GAP stream.
- **GAP** — grade-adjusted pace (Minetti cost-of-gradient curve). The universal downstream pace representation.
- **CTL / ATL / TSB** — Chronic Training Load (42-day EWMA), Acute Training Load (7-day EWMA), Training Stress Balance = CTL − ATL. Together: the PMC (Performance Management Chart).
- **HRV trend** — 7-day rolling ln(rMSSD) vs the smallest-worthwhile-change (SWC) band, centred on the baseline mean with half-width `max(0.5 · SD(ln rMSSD), 0.01)` (`research_00` HRV-07, decision C32: the half-width is floored at 0.01 on the ln scale), SD being the *sample* standard deviation of the athlete's own ln rMSSD baseline. Never a single-reading gate. (The register's earlier "±0.5·CV" was clarified to this on 2026-09-09 — `research_00` HRV-07; `CV(ln rMSSD)` is not unit-invariant and is not the band. If you find `CV` in a document and `SD` in the code, the document is the stale one.)
- **ACWR** — Acute:Chronic Workload Ratio, advisory context and a spike flag inside injury risk only, with a wide ~0.8–1.5 range (`research_00` REG-02). Never a hard gate. Its interval is a range, not a band: T-07 reserves the word band for the HRV SWC band.
- **Durability** — EF (efficiency factor)/decoupling drift over the back third of long runs, tracked as a within-athlete trend, flagged "emerging" not asserted.
- **Determinant profile** — CS/D′, vVO2max, functional threshold pace, EF economy proxy, durability, individual endurance exponent (Riegel *b*) — each carries a trend and a confidence, never a bare point value.
- **Arbitration ladder** — safety → readiness → short-term guardrails → recent re-anchoring → long-term ambition (`research_00` ARB-01). This is the fixed precedence order the ladder applies when signals from the five timescale loops (ARCH-03) conflict; do not reorder or shortcut it. Its rungs are ladder positions, not the loops (T-27).

## Spec-Fidelity Guardrails (agent slop mitigation)

- **Never wire a quarantined sidecar metric into decision logic.** Every metric on the quarantine list (PRIN-20) is display-only: VO2max, Training Status, Training Readiness, Body Battery, Performance Condition and the HRV Status classification. If a feature seems to need one as a convenient shortcut, that's a signal the raw-stream-derived equivalent is missing — build that instead, or ask.
- **Never invent or approximate formula constants.** CTL/ATL windows, the HRV SWC multiplier and floor, the ACWR range, taper duration/volume-cut percentages, etc. are specified in `specification/spec/03`–`07` and the register (`research_00` DOC-07, DOC-18). Cite the section; don't guess a "reasonable" number.
- **ACWR is advisory only — never a hard gate.** Don't add a blocking check keyed on ACWR crossing a threshold.
- **Safety hard-flags fire deterministically, regardless of framing.** The conversational layer (`spec/08`) translates language and explains decisions — it must never become a second adaptation path or a way to talk the system past a safety guardrail.
- **Every applied adaptation must be logged** (inputs + the rule that fired) per `spec/09`. Don't ship an adaptation path with logging as a follow-up TODO — the decision log is not optional plumbing.
- **Down-regulate freely, up-regulate cautiously.** This asymmetry (`research_00` PRIN-13, PRIN-14) is a design invariant, not a tunable default — don't smooth it away for symmetry's sake.
- **When a spec section and a research doc appear to conflict, `research_00` DOC-01 governs** — note the conflict rather than silently picking one side.
- **Freedom-to-operate:** only use open, public-domain math (Minetti, standard EWMA/PMC formulas, published CS models). Do not reproduce a specific patented/proprietary construction described in `research/06` even as an "equivalent" — see that doc's guardrails before implementing anything that resembles a named commercial feature.
