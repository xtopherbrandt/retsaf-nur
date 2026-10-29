---
paths: ["**/*"]
---
# A release demo question that only the athlete can answer is asked when execute ends, before review starts

Learned from sprint-022 (2026-10-28). Origin: `spec/ideas/IDEA-206-ask-release-demo-questions-at-execute-end.md`.

## The rule

The release gate's real-data demo writes a "What the athlete must see" list. Each item on it that
**only the athlete can answer** (was this race all-out, was this a workout or a race, is this the
right activity for this date) is asked **with AskUserQuestion when execute ends**, and the answer is
recorded **before review starts**:

1. **Ask in plain words**, leading with what the demo saw in everyday terms: the activity, its date
   and name as the athlete would recognise them, and what the demo thinks it was.
2. **State the effect on the served number**, both ways: what the athlete sees if the answer is yes
   and what they see if it is no, as the values the release would print.
3. **Record the answer** where the next reader will look: an R-number bullet in the feature's
   reference, or a dated line in the feature's Decision Log, with the activity and the number it
   moved.
4. **Review starts with no open athlete question** from the demo summary. An item that cannot be
   asked yet is written into the summary as a dated deferral with an owner, not left as a note.

A question left in the demo notes is a finding nobody owns. The review reads the notes as a record
of what was shown, not as a list of things to ask, and the critic who raises it again does so at the
end of the review, when the answer can only close the review instead of shaping it.

## Why

Sprint-022's release demo found that an unlisted 2026-06-06 Whistler half marathon entered the
endurance exponent fit and moved b from 1.060 at `high` confidence to 1.074 at `medium`. The demo
summary asked whether the race was all-out and the question sat in the notes. The review's critic
raised it again near the end of the review, and the athlete answered at once: not all-out, backed by
their own Strava note on the activity. Asked when execute ended, the answer would have been in hand
before the review's first pass read the served b. The athlete picked this at the sprint-022 retro.

## How to apply

- **Read the demo summary for questions, not only for failures.** A sentence of the form "is this
  ...?" or "the athlete should confirm ..." in the summary is an item for this rule, whether or not
  the demo passed.
- **Ask every open item in one sitting**, one question per AskUserQuestion, with the review text
  inside the question itself: text printed before a question may go unseen.
- **Show the raw activity beside the derived number**: the distance, time and strap heart rate as
  the store holds them, so the athlete judges the effort from its record and not from the number it
  moved; a derived number checked against the source it came from proves nothing.
- **An answer that changes a number is a fix before review**, not a review finding: re-run the demo
  line it changes and quote the new value beside the old one in the recorded answer.
- **When the athlete is not available**, the summary says so with the date, and the review's
  preflight names the open item as a blocker rather than starting around it.

Related: [[release-demo-checks-the-real-config]] — that rule has the demo load the athlete's real
files before approval; this one has the demo's questions answered by the athlete before review.
[[changelog-claims-are-tried-on-real-activities]] — the demo tries the release's words on the
athlete's activities; an activity the words do not fit is the question this rule asks.
