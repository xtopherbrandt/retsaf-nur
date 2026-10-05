---
paths: ["**/*"]
---
# A feature that reads the time base names its recording-mode population

Learned from F014 sprint-012 (2026-10-04). Origin: `spec/ideas/IDEA-127-name-the-recording-mode-population-in-specs.md`.

## The rule

A feature that reads per-record time, distance, speed or altitude names three recording-mode
populations in its **Negative Class**:

1. **1 Hz with pauses**: one record per second, with stationary pauses and auto-pauses whose gaps
   the timer excludes.
2. **moving dropouts over 5 s**: the athlete keeps moving across a gap longer than 5 s, so distance
   and altitude change across it.
3. **smart recording**: variable gaps, often 6 to 8 s. Record it as "not observed on runs in the
   corpus (see the provenance table); revisit when a fixture shows it".

Each population gets an **expected behaviour**, and either **a probe** that exercises it or **a
dated deferral with an owner** (an IDEA or a feature). "Pace is unaffected" with no probe is neither.

## Why

F013 was tested only on 1 Hz recordings and stationary pauses. Its Negative Class named "isolated
recording gaps over 5 s" and asserted that pace is unaffected, with no probe that moved across a
gap. A critic later found that a moving gap inflates GAP and NGP and can drop up to 75 % of duration
and distance (IDEA-126). The tests had exercised the branch beside the bug.

The smart-recording population turned out to be absent rather than routine. Two runs recorded with
Smart selected decoded at 1 Hz; only HRV Snapshot captures show 6 s steps. That is why the rule asks
for the corpus status, not an assumed one: the provenance table in
`runcoach-api/tests/fixtures/README.md` records each fixture's mode.

## How to apply

- Write the three rows before the acceptance criteria, as
  [[discriminators-must-name-their-negative-class]] asks of any Negative Class.
- For a moving dropout, the probe crosses a gap over 5 s while distance and altitude change; a
  stationary pause does not test it.
- A deferral names a date and an owner. When a fixture shows smart recording on a run, the owner
  replaces the corpus line with a probe.
- Do not infer the recording mode from a file name or a device setting; read the record interval
  from the decoded file. See [[cite-only-real-activity-fixtures-as-proof]].
