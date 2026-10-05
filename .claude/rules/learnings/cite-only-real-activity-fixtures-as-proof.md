---
paths: ["**/*"]
---
# Cite only real-activity fixtures as proof of how runs behave

Learned from F014 sprint-012 (2026-10-04). Origin: `spec/ideas/IDEA-128-label-fixtures-by-provenance.md`.

## The rule

A spec, reference, docstring or test docstring may cite a FIT fixture as evidence of behaviour on
real running data **only when** the provenance table in `runcoach-api/tests/fixtures/README.md` gives
that file run proof `yes`. For walking, the value is `walk only`, and the file proves walking only.

- **Captures and snapshots are cited for what they are.** An HRV capture is evidence about HRV
  captures; a health snapshot is evidence about health snapshots. Neither is evidence about a run,
  whatever its profile, sport field or speed channel says.
- **A file's name is not its provenance.** Read the row, not the name. If a fixture has no row, add
  one from the decoded file and the user's ruling before citing it.
- **A premise that only a non-run file supports is unverified.** Say so where it is written, and
  name the open IDEA that will test it on a real run.

## Why

F013's NGP premise, that device speed absorbs a GPS position jump, rested on
`strap_hrv_sample_run.fit`. The name says "run". The file is a resting sample recorded on the Run
profile: 0 cadence throughout, and its 108 m of distance is GPS acquisition while standing still.
Its device speed reads 24 m/s while stationary. The premise passed planning, build and review
because nothing in the corpus said which files were real activities (IDEA-124 holds the open
question).

The same sprint showed the opposite trap. The user supplied two hilly runs named
"Smart_Recorded". Both decode at 1 Hz: the watch had Smart recording selected and still wrote one
record per second. Cited by name, they would have "proved" smart-recording behaviour the corpus
does not contain. They are in the corpus as `hilly_run_8k_fr945` and `hilly_long_run_17k_fr945`,
renamed so the names claim nothing.

## How to apply

- Before citing a fixture as evidence, open the README row and quote its `kind` and `run proof`.
- When a sentence says "on a real run" or "the runs show", check that every file behind it has run
  proof `yes`. A corpus-wide claim covers only the rows that qualify.
- When the provenance table and a file name disagree, the table wins; the name may be historical.
- Profile a new fixture before naming it: sport, cadence, record interval, distance and devices,
  from the decoded file.

Related: [[name-the-recording-mode-population]] and
[[contract-tables-need-an-independent-oracle]].
