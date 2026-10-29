---
paths: ["**/*"]
---
# A ruling on how a number is computed shows a worked table with every rule that reads that number

Learned from sprint-022 (2026-10-28). Origin: `spec/ideas/IDEA-205-rulings-shown-with-the-rules-that-read-them.md`.

## The rule

When a question asks the athlete to rule on how a number is computed (an error, a count, an
interval, a threshold, a weight), the question carries a **worked table in the question itself**:

1. **a few input sets**, chosen to cover the cases the rule is about: the smallest count the rule
   accepts, a typical one, and the one the current data holds;
2. **the changed number** under the proposed rule beside its value under the current rule, for each
   input set;
3. **the changed number beside every rule that reads it**, one column per reader, with what each one
   would show for that input set: the label it would print, the interval it would serve, the bar it
   would clear or fail.

The table comes before the question, not in a follow-up. The ruling is recorded with that table,
so a later reader sees what was judged and a later ruling on the same number can be judged against
it instead of beside it.

A ruling given on the number alone is a ruling on one column of a table nobody drew. The rules that
read the number were written on the old value; whether they still agree is the thing the athlete is
being asked to decide, and it is not visible without the row.

## Why

Sprint-022 made three rulings on the endurance exponent b, each on its own day and each judged
alone: the confidence label reads the fit error alone (2026-10-23); the interval uses Student's t
with the race count less two as its degrees of freedom (2026-10-24); a fading race counts toward
that degree-of-freedom count by its weight, not as a whole race (2026-10-28).

Each was right on the question put. Together, with three races, they disagree: a `medium` fit from
three races can serve an interval wider than the `low` population default it is meant to improve on,
because the t value for one degree of freedom is large and the label never looks at the width. A
table on 2026-10-24 with the label, the error and the interval side by side for three, four and
six races would have shown it before the second ruling was given. The athlete picked this at the
sprint-022 retro.

## How to apply

- **Find the readers first.** Before writing the question, list every rule, label, display line
  and threshold that reads the number being changed; the spec's acceptance criteria and the
  reference's worked examples name most of them, and `grep` for the field finds the rest.
- **Compute the rows, do not describe them.** Each cell holds the value the proposed rule would
  produce for that input set, worked from the real formula, so the athlete reads numbers and not a
  prediction about them.
- **Include the current data as one row.** The athlete's own count of races, nights or workouts is
  the row they will see served; it belongs in the table beside the hypothetical ones.
- **One ruling per question**, as [[workout-rulings-show-step-targets]] asks; the table is what makes
  the single question answerable.
- **Record the table with the ruling** in the feature's Decision Log or the R-number bullet, and
  when a later ruling touches the same number, extend that table rather than starting a new one.

Related: [[workout-rulings-show-step-targets]] — that rule shows the data a single ruling is given
on; this one shows the rules a ruling changes. [[re-measure-data-premises-at-planning]] — a premise
the table is computed from is re-measured before the rows are trusted.
