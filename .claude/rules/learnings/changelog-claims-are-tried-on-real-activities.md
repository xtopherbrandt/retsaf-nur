---
paths: ["**/*"]
---
# A CHANGELOG sentence that tells the athlete what to do is tried on their real activities first

Learned from sprint-019 (2026-10-17). Origin: `spec/ideas/IDEA-174-test-changelog-advice-on-real-runs.md`.

## The rule

Before a release is approved, the demo lists every sentence in the sprint's CHANGELOG entry that
either **tells the athlete to act** (tag, untag, re-sync, run a command) or **says what a rule
includes or excludes**, and tries each one against the athlete's actual activities in the demo copy
of the store.

1. **List the sentences, not the paragraphs.** "Retag each threshold workout as Training", "its
   recoveries no longer enter the estimate" and "tag only continuous threshold sessions" are three
   claims, and each is checked on its own.
2. **For each sentence, name the real activities it would change, include or exclude**, by date and
   name as `runcoach state` or the activity list shows them. "None" is an answer too, and so is "the
   two sessions since September that the rule now reads".
3. **Write the answers in a `## CHANGELOG claims` section of the demo summary**, one line per
   sentence: the sentence, the activities, and whether the athlete recognises the result.
4. **A surprising answer is fixed before approval.** Either the code, the sentence or the advice is
   wrong; the release waits until the athlete agrees with what the line says about their own runs.

A sentence written from the rule and never tried on an activity is a prediction. The demo already
has the activities on screen; reading them against the sentence costs minutes.

## Why

Sprint-019's CHANGELOG over-claimed twice, and both sentences were on the page while the demo ran.

The entry said a threshold workout is read over its active steps, "so its warm-up, recoveries and
cool-down no longer enter the estimate". The code then counted every lap under a step the watch
marked active, and the 2026-10-07 Speed Repeats' jogs were written as active steps, so that workout was
read with its jogs: 16 laps at 4:52/km in one block, moving the estimate from 4:28/km to 4:41/km.
The sentence was corrected after the demo to say that a recovery written as an active step is
counted.

The known-issue paragraph then advised "tag only continuous threshold sessions Training". Tried on
the athlete's own activities, that advice would have had them untag the 2016-01-19 interval run
(2 mi, 1 mi and 0.5 mi reps with jogs between) and the 2026-09-10 Goal Pace Run (15 + 5 min), whose
two work steps run back to back with no recovery lap between and read as one block. The Goal Pace
Run reads correctly as tagged. The 2016 run is a manual-lap run with no workout steps, so it is
excluded as `no_workout_steps` until manual-lap workouts get a rule (IDEA-173). The advice was
replaced with a way to tell the two kinds apart in `runcoach state`.

Neither sentence was false about the rule as written. Both were false about the athlete's runs, and
only reading the runs could show it.

## How to apply

- Start from the CHANGELOG entry, not from the feature file: the sentence the athlete reads is the
  one that is checked.
- Print the activities the sentence touches, with the raw figures beside them, before judging the
  sentence; a derived number checked against its own source proves nothing
  ([[a-demo-that-checks-derived-numbers-against-their-source-is-circular]]).
- When a sentence's answer depends on how the watch wrote a workout (active, rest or recovery steps,
  one block or several), say which, so the athlete can find the same shape in their next activity.
- A claim that names no activity because the demo copy has none that it touches is noted as such;
  the gap is a finding for the fixture corpus, not a pass.

Related: [[a-demo-that-checks-derived-numbers-against-their-source-is-circular]] — the demo's
numbers were right by construction; this rule asks the demo to try the words on the same data.
