---
paths: ["**/*"]
---
# A question on whether a workout step counts toward an estimate shows each step's Garmin target beside the pace run

Learned from sprint-020 (2026-10-20). Origin: `spec/ideas/IDEA-178-workout-rulings-show-step-targets.md`.

## The rule

Before the athlete is asked whether a workout, a step or a rep counts toward an estimate (threshold
pace, an effort class, a rep floor, a race prediction), the question shows **one table** with a row
per step and three columns beside the step's name:

| step | intensity | Garmin target (min/km, low-high) | pace run (min/km) |
|---|---|---|---|

1. **the intensity** the workout file gives the step (warm-up, active, recovery, cool-down, rest);
2. **the Garmin target**, as the low-high pace range the step was planned at, or "none" when the step
   has no pace target;
3. **the pace run**, what the athlete actually ran on that step.

The table comes before the question, not after it and not in a follow-up. A question that shows only
the pace run asks the athlete to judge an effort from its outcome; the target says what the step was
meant to be, and the two together are what the ruling is about.

## Why

The same Progression Run was ruled twice and the answers disagreed.

On **2026-10-18** the athlete was shown the Progression Run's fast finish by its run paces,
4:10-4:30/km, and ruled it threshold work. On **2026-10-20**, at the sprint-020 review, the same
finish was shown with its step target beside the paces: it is planned at 3:53-4:05/km, about 30 s/km
faster than the Goal Pace Runs. Shown that, the athlete ruled it faster than threshold, and the rule
built on the first ruling had to change.

The ruling changed because the question changed, not the athlete. The athlete named this as what went
badly in sprint-020, and the step targets as the fix.

## How to apply

- **Read the targets from the workout itself**, the structured workout's step list, not from a family
  table or a summary of similar runs.
- **One row per step**, repeats included, so a rep run off its target shows as such instead of being
  averaged into the set.
- **Pace in min/km for both columns**, the same unit and the same rounding, so the two can be compared
  by eye.
- **A step with no target says so.** "none" in the target column is data: a free run is ruled
  differently from a targeted one.
- **Ask in plain language after the table**, one ruling at a time, and say what the answer changes.

Related: [[re-measure-data-premises-at-planning]] — that rule checks the spec's numbers against the
athlete's data before a task is written; this one makes sure a ruling the athlete gives is given on
the data that decides it.
