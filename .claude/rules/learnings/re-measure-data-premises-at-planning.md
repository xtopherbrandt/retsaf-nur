---
paths: ["**/*"]
---
# A spec sentence that states a bound or count on the athlete's data is re-measured against the full census at sprint planning

Learned from sprint-020 (2026-10-20). Origin: `spec/ideas/IDEA-179-re-measure-data-premises-at-planning.md`.

## The rule

At sprint planning, every spec sentence of the form "on the athlete's data, X is at most / at least /
always / N of M" in the sprint's features and references is a **premise**, and each one is listed in
the sprint draft with four things beside it:

1. **the query that re-measures it** against the full census or a read-only copy of the store, as
   one line naming the script and the step;
2. **the query's printed result**, verbatim, not a paraphrase of it;
3. **held or false**, with the corrected figure when it is false;
4. **where the premise sits** (feature AC, reference section), so the correction lands on the
   sentence that carried it.

A false premise stops planning for a ruling. The athlete is asked in plain language, with the data
that makes it false on the page (the dates, the counts, the pair of files), before any task is
written on top of it. A false premise that changes no behaviour is corrected in the spec and noted
in the table; one that changes a rule is a decision the athlete makes, not the planner.

A premise read off a summary table at discussion is a claim about the data. It becomes a fact only
when a query has been run against the whole population and its output read.

## Why

Two sprints in a row carried premises that nobody had measured.

**The 240 s rep.** F027's 290 s rep floor was justified by "on the athlete's structured runs the
longest speed or cruise rep is 240 s". The sentence was read off a family table at discussion, and
it passed planning, execution, three scan rounds and the spec review untested. The census held two
1600 m repeat sessions with reps of 374 to 406 s, well over the floor. Only the review critic found
it, after the rule had shipped on the wrong premise. The athlete named this as what went badly in
sprint-020, and the re-measure as the fix.

**Seven of fifty-two.** F028's planning re-measured every premise in the feature and its reference
against the full export (all 1,409 overnight files re-decoded) and a store copy. Seven of 52 were
judged false. Six were figures the spec had wrong, among them a maximum of 133 where it said 130, a
median of 85 values where it said 82, and a short night on 2024-05-31 where it named Oct 10. One of
the six "corrections" was itself wrong: planning replaced a 55-minute import estimate with the 21
minutes of the store's earlier import of the same export, which held no nights, and the import with
the nights took 62 minutes (1:01:52 at the release demo). A premise re-measured on a population that
lacks the feature's data is not re-measured. The seventh reversed a ruling: the spec said "the later
re-summary holds the fuller series" and built a fuller-wins rule on it. On 2024-01-01 the later file
was the thinner, a strict subset (106 values at 06:52, then 104 at 07:30). Shown that pair, the athlete chose later-wins, and the
feature's acceptance criteria changed before a task was written.

The same re-measure also surfaced seven populations the spec had not named at all (106 nights
written away from the home time zone, 22 summaries more than an hour after the last value, 18 series
with gaps), each of which became a negative-class entry or a dated deferral.

## How to apply

- **Re-walk the source, not the discussion's summary.** The premise came from a summary; measuring
  it from the same summary proves only that the summary was copied correctly. Decode the files, scan
  the export, query the store copy.
- **Measure independently.** The agent that re-measures is not the one that wrote the spec and has
  not read the planning scratchpad; it is given the premise sentences and the data.
- **Print the slice compared.** A verdict without its printed result is an exit code; the table
  holds the number the query produced, so a reader can see what "held" was judged against.
- **Look for the population the spec does not name.** A premise says what the data does; the
  re-measure also lists what it does besides (travel nights, gaps, re-summaries), because the spec's
  frame is where the next false premise hides.
- **Keep the scripts and outputs** under the sprint's planning capture directory in the data dir, so
  a reviewer can re-run them.
- **Stop for a ruling in plain language.** Lead with what the data shows in everyday words, show the
  pair of files or the dates, then ask; identifiers and rule names come after.

Related: [[changelog-claims-are-tried-on-real-activities]] — that rule tries the release's words on
the athlete's runs at the demo; this one tries the spec's numbers on the athlete's data before a
task is written. [[a-demo-that-checks-derived-numbers-against-their-source-is-circular]] — the same
circularity applies to a premise re-measured from the summary it was read from.
