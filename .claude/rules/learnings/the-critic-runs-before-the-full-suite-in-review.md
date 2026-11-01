---
paths: ["**/*"]
---
# In review, the critic runs before the full suite, and the suite starts once the code is final

Learned from sprint-023 (2026-10-30). Origin: `spec/ideas/IDEA-208-critic-before-the-full-suite-in-review.md`.

## The rule

A review on this project runs its read-only passes first and its full suite last:

1. **The Stage 0 scans, the spec review and the critic run read-only first**, on the head the review
   entered at. None of them waits for a suite run, and none of them starts one.
2. **The critic's findings are settled before the suite starts**: either the critic asks for no
   patch, or the patch is made and its narrow scan (the changed test files and the gates that read
   the changed files) is done.
3. **The full suite starts once, on the final head**, the commit the release will carry. A suite
   started on an earlier head is evidence about that head only.
4. **The order is written in the review cursor at entry, beside the stop rule**, so the review's
   first step records it and a resumed session reads it before choosing what to run.

The full suite takes about 1.5 hours alone on this machine. Run before the critic, it is spent on a
head the critic is likely to supersede, and the run that counts has to start again from zero.

## Why

Sprint-023's review started the full suite at e41c0b2, as the review's default order has it, before
the spec review and the critic. The critic then found the case where one race carries the projected
line; the athlete ruled a new rule for it, and the review patch superseded the suite's head. The
suite was stopped at 21 % after about 23 minutes, and a second full run went at 36f3692. The
athlete picked this as the change at the sprint-023 retro.

## How to apply

- **At review entry, write the order into the cursor** beside the stop rule: scans, spec review,
  critic, then one full suite on the final head.
- **Run nothing long beside the read-only passes.** The scans and the critic read code and run
  narrow probes; a full suite in parallel with scanner agents runs many times slower and its head
  may not survive the critic.
- **A critic patch gets a narrow scan, not a full run**: the test files it touches and the gates
  that read the files it changed, such as the 15 s citation gate. The full suite waits for the last
  patch.
- **If a finding after the suite starts leads to a patch**, stop the suite, kill any stray pytest
  process, and start the one full run again on the new final head; do not merge a partial run's
  evidence with a later head's.
- **Quote the final head's sha beside the suite's summary line** in the review record, so a reader
  can see that the run that passed is the run on the code that ships.

Related: [[release-demo-questions-are-asked-before-review]] — that rule settles the athlete's
questions before review starts; this one settles the critic's findings before the full suite starts.
