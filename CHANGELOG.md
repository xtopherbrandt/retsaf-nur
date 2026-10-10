# Changelog

## Unreleased

## 2026-10-09 — Sprint 014: saved session load and the load chart

### Added

F017: `GET /sessions/{session_id}/load` serves a stored session's training load (spec/03 section
3.4): `hr_trimp` (the Banister exponential TRIMP over `hr_time_s`, the session's running time with
usable HR, never stopped time), the threshold-hour reference on the session's own sex coefficients,
`session_load` on the "threshold hour = 100" scale with its `driver`, and `rtss` and `srpe`
unavailable as `no_threshold_pace` and `no_rpe` until threshold pace and an RPE input exist. The
load is computed in the upload's own transaction, after the profile update, and saved with every
value used: per field (`resting_hr_bpm`, `max_hr_bpm`, `threshold_hr_bpm`, `sex`) the value the
session's own FIT file carried, else the anchor in effect at upload with its version; a later
anchor change leaves the saved body unchanged, and a run is corrected by delete and re-upload,
which removes the saved load too. Sessions gain the nullable `timer_time_s` column (the FIT
`session.total_timer_time`), and `inputs` reports `hr_time_s`, `avg_hr_bpm`, `timer_time_s`,
`hr_time_fraction` and each value used with its source. Sessions stored before this version get
their load from a one-time fill at first start, the store's first startup data write: each session
without a saved load is computed and saved as an upload would, with `timer_time_s` copied from the
session's stored summary; a second start changes nothing. A fault computing or saving a load fails
the upload with a 500 and rolls it back, and stops startup naming the session. The gates, in order,
first match wins: `sport_not_running`, `declared_capture`, `no_hr`, `missing_anchor` (with
`unavailable_fields`), `order_conflict` (the order rule re-applied to the values used),
`avg_hr_below_resting` / `avg_hr_above_max`, `wrist_hr_threshold_unknown`, `wrist_hr_at_threshold`
(a session whose HR source is not a chest strap is flagged `wrist_hr` whatever the outcome and
refused at or above the usable threshold by average HR) and `not_representable` (an entered value
that does not convert to a finite float, or a max - resting that is not a positive finite float);
after TRIMP, `session_load` is `no_threshold_hr` or `threshold_order_conflict` without a usable
threshold, or `not_representable` when threshold - resting collapses in float. `flags` carries
`wrist_hr` and `sex_defaulted`. The operation is `getSessionLoad` in the contract. A stored
session with no saved load answers a 404 that says so, distinct from an unknown id.

F018: `GET /metrics/load` serves the fitness, fatigue and form chart (spec/03 section 3.5): for
each local date in `athlete_timezone` from the first running session to today, the day's `load` (the
sum of the saved loads of its counted runs), `ctl` and `atl` (EWMAs at the fixed 42/7 constants) and
`tsb` (yesterday's CTL minus yesterday's ATL), with the runs the day counted, the runs it could not
count with their reason, and the sessions it excluded (non-running sports and declared resting
captures). A day whose runs all have no load moves like a rest day and is `marked`. Both curves
start at the `seed`: the average daily load over the first 42 days of history, or over all of it
when shorter, with those days `provisional`. `from` and `to` bound the range; `from` after `to`, `to`
after the local today, or a malformed date is a 422. A store with no running session answers 200
with `first_day` and `seed` null and `days` empty. The operation is `getLoadChart` in the contract;
the planned `getLoadSeries` at `/plan/load` stays planned. spec/03 section 3.5.2 now says a run
with no load is never a 0 and a no-load day is treated as a rest day and marked; section 3.5.3 seeds
from the average daily load (no longer "average weekly load") with no switch at six weeks; section
3.5.4 says the 42/7 constants are fixed, no longer "exposed for tuning".

## 2026-10-07 through 2026-10-08 — Sprint 013: athlete profile and HR anchors

### Changed

IDEA-132: the task-ID sweep judges each hit by the worst of every commit in the sweep range that
added or removed a line of the file naming the ID, plus the commit blame names for the line, in
place of the similarity walk-back; another task's edit of such a line fails every hit for that ID
in the file, and a range whose base is not a strict ancestor of its head exits 2. The sweep also
exits 2 on a range that compares no file, rather than passing having judged nothing. A range with
the wrong IDs is not detected (an accepted limit, the user's ruling, 2026-10-07): the sweep knows
a task's commits only by their scope, which this project does not require.

### Added

F016: each stored session keeps the seven athlete profile settings its FIT file carried, read from
the first `user_profile` and `zones_target` messages: `sex`, `body_mass_kg`, `height_cm`,
`resting_hr_bpm`, `garmin_activity_class`, `max_hr_bpm` and `threshold_hr_bpm` (spec/02 section
2.2.1). A FIT invalid value, a missing field or message, a 0 or negative value for the five numeric HR
and body fields, and a non-finite body mass store no value; FIT `gender` 0 is `female`, and `garmin_activity_class` is the raw integer.
The athlete's entered values are kept one row per change. Each profile field's effective value,
for every field but `garmin_activity_class` (stored per session only, never resolved or served), is
the latest stored file that carries it, by session start time (for max and threshold HR, running
sessions only), else the latest entered value, else unavailable (spec/01 section 1.3; research/00
PRIN-28 admits the seven settings as profile inputs). spec/04 section 4.2.7 now seeds HR_max from
the profile, says a latest-file value can go down, and names no age-based max-HR formula (IDEA-138).

F016: `GET /me` returns each profile field's effective value, its source (`fit` with the session,
or `entered`) and any entry a file value shadows, plus the resting, max and threshold HR and sex
anchors, each available with a version or unavailable as `missing` or `order_conflict`, beside
the athlete's `id`, `display_name`, `units` and `created_at` (one settings row, seeded at startup
with `display_name` null and the default units). A store whose anchor version log does not hold a
served value is a 500 that names the repair: restarting the API repairs the log. In the contract,
`getMe` is `implemented`, inherits the global `bearerAuth` like the session operations, and lists
no 401. `Athlete`'s `sex` and `birth_date` change from scalars to per-field entries; that shape
change is accepted only because `getMe` was planned, and `createAthlete`'s 201 still points at
`Athlete`. `OnboardingInput` still names the weight `weight_kg`, where `Athlete` names it
`body_mass_kg`: two names for one field, flagged for the UI project.
`PATCH /me` stores entered values, one per field sent (`null` clears a field; `{}` changes
nothing), sets `display_name` and `units` (all three), and returns the same shape. It answers 422,
writing nothing, for an unknown field, a units object missing a key, or null, an HR value that is not a whole number above 0 (`true`,
`"188"` and `188.5` included), a body value that is not a finite number above 0, a `sex` outside
the enum, or a `birth_date` that is not a `YYYY-MM-DD` date or lies after today in
`athlete_timezone`; ordering across fields is left to the anchors. A JSON body carrying a `NaN` or
`Infinity` token that fails validation is a 422 that echoes the token as text. In the
contract, `updateMe` is `implemented`, inherits `bearerAuth` and lists no 401, and
`AthleteProfileUpdate` gains the seven entered fields; it and `UnitPrefs` refuse unknown keys.
`runcoach profile show` prints them, and `runcoach profile set` enters or clears them; it refuses a
non-finite body mass or height with an error before sending anything. `init_schema` holds the write
lock across its anchor version sync, so an upload and a `PATCH /me` that run at the same time no
longer log a stale anchor version.

Tests: a fixture whose name lacks a lowercase `.fit` suffix fails the fixture provenance tests, so
a file named `X.FIT` cannot escape the case-sensitive census globs on Linux.

### Migration required

F016: sessions stored before this change carry no profile values, and nothing is backfilled.
Uploading an already-stored file again returns 409, so to fill a session's values the athlete
deletes the session and uploads the file again. Until the newest files are re-ingested, a
pre-change row's empty values read as "this file had no value", so the profile can come from an
older file, or from an entered value.

## 2026-10-06 — Sprint 012: fixture provenance, hilly runs and chest-strap inference

F015: ingestion reads `hr_source` as `chest_strap` when a file has no RR stream but has HR and a
connected heart-rate sensor: any `device_info` entry whose device type is heart_rate and whose
`source_type` is antplus or bluetooth_low_energy (spec/02 section 2.4.2 step 1, as built in
`mapping._infer_hr_source`). No serial is needed, and `hr_sensor_serial` stays ANT+ only. RR present
still reads `chest_strap`; HR with no RR and no such entry still reads `wrist_ppg`; a file with no HR
is unchanged. Five corpus files move from `wrist_ppg` to `chest_strap` (`sample_run`,
`wrist_ppg_run`, `hilly_long_run_17k_fr945`, `strap_health_snapshot` and
`strap_health_snapshot_hrv`). The three runs among them lose their `cadence_lock` sample tags (154,
157 and 36 samples), so F013's `avg_hr_bpm` on those runs now counts the samples it used to exclude.
On the two Health Snapshots, `chest_strap` names the producer of the HR stream; their HRV source is
still their tier (`hrv_source_tier = health_snapshot`, `rr_source = health_snapshot_ppg`).
Known limitations: a strap paired but not worn reads `chest_strap`, so a wrist session recorded with a
strap connected escapes the cadence-lock check. So does a session from an external optical sensor
connected over ANT+ or Bluetooth Low Energy (an optical armband, or a watch broadcasting wrist HR),
because the heart-rate device type does not tell optical from ECG. A strap that drops out
mid-activity marks the whole session `chest_strap`. research/00 is amended to match: REG-24 names
both chest-strap signatures (RR presence, or, with no RR, a connected ANT+ or Bluetooth Low Energy
heart-rate sensor with a positive heart rate), REG-24's Not clause excludes only a resting capture's
HRV source, and REG-23 and REG-09 name the three accepted limits in their Not clauses (R16 in the
F008 decisions reference). F015 changes neither the key set of `GET /sessions/{id}` nor the contract.

### Migration required

Sessions stored before this change keep their stored `hr_source` and `cadence_lock` sample tags;
nothing is backfilled. Deleting such a session and re-uploading the original file re-derives both,
under the same session id (a duplicate upload is refused with 409, so the delete comes first).

### Added

F014: `runcoach-api/tests/fixtures/README.md` holds a provenance table for every fixture (kind,
recording mode, devices, positions, run proof). A test compares kind, mode and run proof with a
copy of the F014 reference rows it keeps, and positions, devices and each mode's recording interval
and pause lengths with the decoded files. Two real hilly runs join the corpus with positions
stripped, `hilly_run_8k_fr945` and `hilly_long_run_17k_fr945`, pinned against the GAP oracle and the
watch's own time and distance totals. A new fixture that still carries positions fails the
provenance test unless it is on the allow-list, and `tests/support/strip_fit_positions.py` refuses a
source with a bad CRC and will not overwrite its source or an existing file. F013's NGP premise,
that device speed absorbs GPS distance jumps, now cites real runs (spec/03 section 3.3.3 and
`metrics/ngp.py`): on three real runs device speed stayed continuous through `strap_run_hrv`'s
25.4 m and 40.2 m one-second jumps, `hilly_run_8k_fr945`'s 11.7 m GPS-acquisition jump and
`hilly_long_run_17k_fr945`'s 8.6 m jump (a jump is a 1 s step over device speed × dt by more than
5 m, as the F014 reference defines it). A device-speed spike is still unseen on a run: the corpus's
one spike is in a resting sample, and IDEA-124 holds it open. Two learnings rules ask that only
real-activity fixtures be cited as run proof and that features reading the time base name their
recording-mode population.

IDEA-122: a third learnings rule, `no-later-task-ids-in-durable-text.md`, says durable text never
names a later task of the same sprint, and `runcoach-api/tests/support/sweep_sprint_task_ids.py`
finds such sentences in a sprint's diff. The sprint's last-wave release gate runs the sweep with
`--strict` and the sprint's own ID range.

### Changed

IDEA-119: the served `DatasetSummary.tier` description and the contract's datasets tier now say the
connected heart-rate sensor's serial is stored but no rule reads it, so a per-unit key is later work.

## 2026-10-04 — Sprint 011: grade-adjusted pace and session descriptors

F013: `GET /sessions/{session_id}/features` serves each running session's grade-adjusted pace (GAP),
normalized graded pace (NGP) and the spec/03 section 3.2 descriptors, computed on read from the
stored canonical records. Nothing is stored and no table is added, so a deleted session has no
features: the route returns the 404 envelope of `GET /sessions/{id}`. The as-built rule: the time
base is recorded time (consecutive records with 0 < dt <= 5 s form a counted segment; a longer
segment is a break that contributes neither time nor distance; a dt = 0 duplicate contributes
nothing; a decreasing distance contributes 0 m and flags `distance_regressed`). The gradient is
taken over +/-25 m of reconstructed distance and clamped to +/-0.45 before Minetti's cost curve;
clamped segments count in `grade_clamped_fraction` and flag `grade_clamped`. GAP is the
distance-weighted mean, so `avg_pace / gap_avg_pace` is the mean g and flat ground leaves pace
unchanged; an ungraded segment contributes at g = 1 and `gap_coverage` reports the graded share,
with `gap_unavailable` flagged when it is below 1. NGP is the fourth-power mean of device speed
times g over 30 s windows within contiguous blocks (a block of fewer than 30 records adds nothing; a
record reached by a dt = 0 segment is left out and does not split its block; a 52 m one-second
distance jump cannot move it). `gps_degraded_fraction` reports and excludes nothing; the session's
`quality_flags` lead `flags`, `smart_recording` first. Every feature is `{value, unavailable}` with
exactly one side set; the reason codes are `sport_not_running`, `no_records`, `no_counted_segments`,
`no_distance` (a zero distance is never served as 0.0), `no_30s_block`, `no_motion`,
`no_heart_rate`, `cadence_lock`, `no_cadence`, `no_power`, `mixed_power_models`, `no_altitude` and
`not_recorded`. A non-running session is a 200 with every feature `sport_not_running`. The contract
gains `getSessionFeatures` (`x-readiness: implemented`) and the `SessionFeatures` component, whose
`duration_s`, `distance_m` and `avg_pace_s_per_km` are the quantities `SessionSummary` names:
recorded time, not elapsed. The key set of `GET /sessions/{id}` is unchanged.
Known limitation: a gap over 5 s while the runner keeps moving (smart recording, a GPS dropout)
is dropped from time and distance while its altitude change still enters the gradient window, so
`duration_s` and `distance_m` read low, GAP pace reads fast and NGP high for such runs, until a
follow-up feature treats moving gaps.

## 2026-10-03 — Sprint 010: per-unit sensor identity at ingestion

F007 (T248): the `sessions` table gains a nullable `hr_sensor_serial INTEGER` column, and
`models.Session` the matching `hr_sensor_serial: int | None = None` field, carried through the
insert path. It holds the serial of the ANT+ heart-rate sensor that was *connected* when the
session was recorded — that sensor's own unit serial (usually a chest strap; a watch broadcasting
its optical HR over ANT+ counts too), not the recording watch's (`source_device`). It records the
pairing, not the HR stream's provenance: `hr_source` is inferred (spec/02 section 2.4.2) and can read
`wrist_ppg` while a strap was connected, so the two fields can disagree and neither proves the
other. `NULL` means **unknown**, never "no sensor".

**No backfill.** Nothing fills `hr_sensor_serial` for sessions stored before this column existed,
and the app keeps no FIT bytes, so such a row stays `NULL` unless the athlete deletes the session
and re-uploads the original file: the re-ingest derives the same `session_id` (F007 AC5) and stores
the serial. Any later consumer must treat `NULL` as unknown. An existing database gains the
column in place through `db._reconcile_columns` on the next `init_schema` (app startup or any
ingest), with every row preserved — the same automatic reconcile that landed `resting_rmssd_ms`, so
no migration step is required.

F007 (T249): ingestion resolves and stores `hr_sensor_serial`. The rule, as built in
`mapping._resolve_hr_sensor_serial`: collect the distinct non-null `serial_number` values over the
file's `device_info` entries with `source_type` antplus and `antplus_device_type` heart_rate;
only a positive integer counts, and anything else a non-conforming writer sends (a 0, a tuple, a
string) is discarded. Exactly one distinct serial is stored; none, or two or more distinct
serials, store `NULL`. There is no fallback to the watch's (`creator`) serial or to a sibling channel of the same strap, and
firmware, manufacturer and product are not part of the identity, so a firmware push cannot split
one sensor into two. A Bluetooth (BLE) strap stores `NULL`: only ANT+ entries are read. Nothing
reads the column, and it is absent from `GET /sessions/{id}`.

## 2026-09-30 through 2026-10-03 — Sprint 009: research/00 citations, recency tolerance, review hand-off

F009: every live citation of research/00 names a rule ID that resolves. A citation gate
(`test_research00_citations_resolve.py`) classifies 268 files as live or record and checks 965
citation rows across five per-root CSVs; records keep their citations. `hrv_trend.py`'s comments
cite rule IDs, in three comment-only commits (one planned, two by review ruling), each
re-measured with byte-identical T162 rows. HRV-25's forbidden-direction population is counted and
pinned (24,099 of 307,500).

F010: `/metrics/hrv` responses serve `thresholds.recency_tolerance_days` (28, read from
`hrv_trend.RECENCY_TOLERANCE_DAYS`), so `selected_reason = higher_fidelity_skipped_stale` can be
recomputed from `datasets[].last_read` and the response alone (research/00 PRIN-12, C33). Additive:
both contract copies describe the new key identically and `info.version` is unchanged. This
reverses IDEA-070's 2026-09-15 decision to narrow the `thresholds` promise instead.

F012: F008's review hand-off is enforced by keys. research/00 splits PRIN-24 (`window_days`) from
the new PRIN-27 (every other unserved verdict-affecting input) and states the coverage-gap leading
stretch as HRV-85. Four old-meaning keys guard the retired readings, and the rule file records
the vocabulary, round-naming and history policies. IDEA-103 and IDEA-106 are resolved.

No API behaviour change beyond F010's additive key; `info.version` is unchanged. The suite grew
from 2761 to 2921 tests, with 0 failed and 0 skipped. Residuals are routed to IDEA-113 to IDEA-116.

## 2026-09-28 through 2026-09-30 — Sprint 008: research/00 downstream sweep

F011: every live document under the downstream roots now states research/00's current rules. No
live document states a meaning F008 changed.

- A census of every old-meaning site came first: `runcoach-api/tests/data/research00_census.csv`,
  now 228 rows, each red on its old text and green after the fix. It covers spec/01 to spec/09,
  research/02, 05 and 06, decisions/01, the development plan and outline, future-directions,
  `.claude/rules/`, the F006 pair and its mirror, the contract (both copies, description-only),
  and src comments.
- `test_research00_downstream.py` scans 58 live files against 54 old-meaning keys, with
  per-feature EXCEPTIONS (F009 owns `metrics/hrv_trend.py`). It reads quotation as CommonMark
  0.31.2 via markdown-it: code spans, fences, S3 quote pairing per paragraph, raw HTML and
  autolinks by the spec grammar. The gate proves verbatim absence. Review added a paraphrase
  sweep of all 54 keys, which found and fixed about 30 further sites.
- `--against` now reports checker-code edits as `# code` lines, and the EXCEPTIONS pin checks
  shape instead of being empty.
- decisions/01 conforms and is pinned from research/00 DEC-01.

There is no API behaviour change, and `info.version` is unchanged; contract edits are
descriptions only. The suite grew from 1901 to 2761 tests, with 0 failed and 0 skipped.
Residuals are routed to F012 (IDEA-106, AC6) and F009 (AC5).

## 2026-09-25 through 2026-09-27 — Sprint 007: research/00 as current rules

F008: `specification/research/00` is the decision authority. It is rewritten as 246 one-sentence
rules under permanent IDs, plus a 33-term Glossary, and split into four files:

- `00-design-decisions.md` holds the current rules only.
- `00-history.md` holds H-01 to H-41, the dated record of what changed.
- `00-traceability.md` maps every inventory row to its rule or rules. Each row records whether the
  meaning changed, the authorizing decision, and the key of the old meaning.
- `00-meaning-review.md` holds an independent critic's verdict on every rule block and Glossary
  term: 822 rows, all `same`, over rounds 1 to 11. Each verdict is bound to the digest of the text
  it judged.

A gate, `runcoach-api/tests/test_research00_traceability.py`, holds all four files. A changed rule
stays red until a new critic round names it. Edits follow
`.claude/rules/project-research00-edits.md`. The decisions behind the rewrite are R1 to R13 in the
F008 decisions reference.

B-CR-001: each span guard of the claim sweep now probes or abstains by name, strips fenced blocks
on markdown-it's fence tokens, and has a red case.

There is no API or contract change, and no migration is required. The suite grew from 1659 to 1901
tests, with 0 failed and 0 skipped. CI now checks out full history (`fetch-depth: 0`).

## 2026-09-18 through 2026-09-23 — Sprint 006: Per-Tier Resting-HRV Datasets

F006: each resting-HRV source tier keeps its own baseline, band, `n` and `established`, and the
verdict is computed against the dataset selected for the day rather than against the one tier that
won a single arbitration. The migration note below was written at the point the break landed
(T152), so a consumer that pulls mid-sprint has the instruction beside the change; the release
notes follow at release.

### Migration required

**`excluded[].reason` no longer carries `off_baseline_tier: <tier>`, and `contracts/openapi.yaml`
moves from `0.1.0-draft` to `0.2.0-draft`** — the sprint's one breaking change to an
`implemented` operation, `GET /metrics/hrv` (`getHrvTrend`). An enum value published as receivable
has been removed, so a client generated from the previous contract that switches on the reason
string, or that validates the response against the old enum, must drop the member.

Why the value is gone rather than merely unemitted: under F005 one tier owned the only baseline,
and every stored row of any other tier inside `[date-66, date]` was listed in `excluded[]` as
`off_baseline_tier: <tier>` — discarded for the verdict. Under F006 a reading of another tier is
**not excluded from anything**: it feeds that tier's own dataset, with its own band. A morning
carrying both a chest-strap capture and a watch snapshot now contributes one reading to *each*
dataset. `research/00` §1.6 requires every stored row in the span to be accounted for exactly once,
so the `included`/`excluded` partition is now **per dataset**: a row is in exactly one dataset's
series or in `excluded[]` with one of the remaining reasons, never both and never neither.

What a consumer sees:

- **`excluded[]` shrinks.** Rows that were `off_baseline_tier` simply disappear from the list; on a
  two-tier history the list can be empty where it held dozens of entries. The remaining reasons —
  `pre_amendment_window`, `null_tier`, `unknown_tier: <tier>`, `unusable_value: <value>`,
  `same_day_later_capture`, `before_reset: <coverage_gap|tier_change>` — are unchanged in meaning.
- **`same_day_later_capture` is now per dataset.** A second capture of tier X on a day is X's own
  re-take whether or not X is the tier reported in `baseline.tier`; under F005 it would have been
  `off_baseline_tier` when X was not the resolved tier.
- **Where the rows went is now visible on the wire** (T159, 2026-09-21, additive; `info.version`
  stays `0.2.0-draft`). `datasets[]` carries one entry per source tier present in the
  **gap-clipped** span — a tier read only before a coverage-gap resumption has no entry at all,
  and its rows are in `excluded` as `before_reset: coverage_gap` — each
  with its own `band`, `n`, `established`, `fidelity_rank`, `last_read`, `week_days`, `week_mean`,
  `below` and its own `reset_on`/`reset_reason`; `selected_dataset` and `selected_reason` name the
  dataset `baseline`/`band` describe — and `verdict`/`below_by` wherever a verdict was asserted, not
  on a selected dataset whose verdict is withheld (`week_not_representative`, or a future day) — and why it was promoted (both null when nothing is selected, which
  `unavailable_reason` does not imply: `week_not_representative` is served with nothing selected
  too, T168); `disagreed_with` names any
  dataset whose judged week reads the other side of its own band, with the judged-week count that
  weighs the name — and is empty whenever `verdict` is `hrv_unavailable`, for any cause (T167,
  `research/00` §5.4 (iii): a disagreement is with a conferred verdict); and `points[].dataset` names the dataset each day's band came from, since
  selection runs per local day. `baseline`/`band`/`included` still describe the selected dataset
  only, exactly as before, and no field that was non-nullable became nullable — a consumer that
  ignores the new fields needs no change.
- **Nothing else on the response moves** in this change: `baseline.tier`, `verdict`,
  `unavailable_reason`, `points[]`, `thresholds` and every non-nullable field keep their shape.

To upgrade a generated client: regenerate from the `0.2.0-draft` contract, or remove the
`off_baseline_tier` member from any hand-maintained enum and treat its absence from `excluded[]` as
"the row is in another tier's dataset", not as "the row was dropped".

### Added

- **A no-regression release gate against shipped F005** (T162, T164). The §1.7 rates are measured
  for both rules over a committed row set: 17,070 rows, of which 8,696 are gated. The provenance of
  that set is pinned to the module's git blob. Every gated rate blocks release, except one named,
  counted exception (below).
- **Three-valued retirement pins** (T160) for every F005 qualifier that per-tier datasets retired:
  - green on shipped F005;
  - red with the qualifier alone deleted;
  - green on F006.
- **Measured sweeps that name their axes** (T161), with capture density crossed on both datasets.

### Changed in review — cycles 1 to 3

- **`disagreed_with` is empty whenever `verdict` is `hrv_unavailable`, for any cause** (T167,
  `B-CR-002`). Before this, a returning athlete's withheld verdict could be served beside a named
  dissenter.
- **The contract's descriptions of the verdict-free responses were corrected** (T168 and review
  fixes):
  - `week_not_representative` had been described in the wrong direction;
  - `selected_reason` claimed a verdict source where none is served;
  - the served cause does not imply a selection: `week_not_representative` can be served with
    nothing selected.
  Only description text changed; `info.version` stays `0.2.0-draft`.
- **AC21 carries its exception** (user decision): the release gate blocks on any worsened §1.7 rate
  save one named, counted exception. `research/00` was amended first.
- Review-cycle record: 3 cycles; 1659 tests passing, 0 skipped; demo probe passing.
  `verify/F006-verdict.md` holds the full gap table.

### Known limitations, carried

- **`DEFERRED_EXCEPTION`**: 64 gated rows where F006 is worse than F005 on the forbidden-direction
  rate (25 rows at `c = 4`, 35 at `c = 5`, and 4 totals) under the independent-instruments
  fixture. It is pinned and may not grow. Owned by IDEA-087.
- **AC22 and AC23 hold only under the sweep's equal-dispersion corpus** (IDEA-089 (b)). The
  no-hysteresis decision (2026-09-21) stands, and the 80 worsened flip cells are a closed set.
- **7 of 24 acceptance criteria are partial.** Each is routed in the verdict. Most are settled by
  the planned research/00 rewrite (F008 → F011 → F010 → F009), which also publishes
  `recency_tolerance_days` (F010).
- Commit `502bb84` is typed `refactor` but changed response behaviour. It is named here because the
  history cannot be retyped.

## 2026-09-09 through 2026-09-18 — Sprint 005: Resting-HRV Trend

F005: the resting-HRV trend verdict on the contract's `GET /metrics/hrv`, computed from F004's
captures and bucketed into the athlete's **local** days. Bucketing into local days is what makes
this sprint a breaking config change — the server now has to know which days are the athlete's —
and the migration note below is the whole of the upgrade path. The migration note was written at
the point the break landed (T088/T089/T090), so an athlete who pulled mid-sprint had the
instruction beside the refusal; everything from *Added* down was written at release, after ten
review cycles had finished with the feature.

### Migration required

**`athlete_timezone` is now a required config field with no default of any kind** — the second
breaking config change, after sprint-004's `resting_hrv_profile_names`, and for the same reason.
`AppConfig` sets `extra="forbid"` and validates on load, so **an `api.toml` written before today
fails startup**: `runcoach-api serve` refuses to start and names the field, and a server booted
directly through uvicorn fails at application startup with pydantic's own
`athlete_timezone — Field required`.

Why there is no default: the trend's 7-day window, its 60-day baseline, the 21-day coverage gap and
same-morning grouping are all measured in the athlete's local calendar, while readings are stored
`+00:00`. A silent `"UTC"` would bucket a 06:00 capture at UTC+13 onto the *previous* day and shift
which readings fall inside the window, with no error anywhere — exactly the failure the
no-defaults rule (F001, ratified) exists to prevent. The zone is validated at load, so an
unresolvable name is a startup failure rather than a per-request 500.

Three ways to upgrade an existing install:

1. **Add one line to `api.toml`.** The value is the zone's IANA name, as in the tz database:

   ```toml
   athlete_timezone = "Pacific/Auckland"
   ```

   An unrecognised name is refused at startup with a message that says so and shows the expected
   form. A file that also predates sprint-004 — missing `resting_hrv_profile_names` *and*
   `athlete_timezone` — is told about both fields in one startup message; add both lines in the
   same edit.

2. **Or set the environment variable**, which overrides the file. Unlike sprint-004's list-valued
   field, `athlete_timezone` is a plain string, so the value is written bare — no JSON, no
   brackets, no quotes:

   ```
   RUNCOACH_ATHLETE_TIMEZONE=Pacific/Auckland
   ```

   Because nothing is JSON-parsed here, IDEA-016's raw `pydantic_settings.SettingsError` traceback
   for an unparseable env value does not arise for this field; a bad value reaches the same
   validator as the file and gets the same message.

3. **Or re-run `runcoach-api init`**, which now takes a required `--athlete-timezone ZONE` flag and
   writes the key. `init` refuses an unknown zone *before* writing anything, so it can never
   produce a file `serve` then rejects. As before, this is the fresh-install path rather than an
   upgrade path: `init` refuses to overwrite an existing config without `--force`, and `--force`
   rewrites `host`, `port`, `data_dir` and `resting_hrv_profile_names` too. For an existing
   install, edit the file.

**A zone can only be checked against a time-zone database.** Python bundles none and Windows ships
none, so `runcoach-api` now declares `tzdata` as a dependency; run `uv sync --all-packages` after
pulling. On a host with no database at all, *every* zone is refused, and the message says that
rather than calling your zone invalid.

**Editing `api.toml` requires a server restart**, as in sprint-004: `db._load_config_cached` is
`@lru_cache(maxsize=1)`, so the config is read once per process.

**The feature demo probes for F001, F003 and F004 now export `RUNCOACH_ATHLETE_TIMEZONE=UTC`**
beside F004's existing profile-names export. They boot a server against the athlete's real
`api.toml`, which lacks the field until this note is followed; none of the three asserts anything
date-bucketed, so the export is hermeticity, not correctness.

### Changed

- **The HRV SWC band is `0.5 · SD(ln rMSSD)` (sample SD), not `0.5 · CV`.** The domain spec
  (§3.7.3 and every restatement of it, `research/00`'s register row included, as a stated
  clarification) is amended in this sprint. The `±0.5·CV` wording in the sprint-004 *Fixed in
  review* entry below is superseded by that amendment and is left as written — it describes the
  poisoning defect as it was understood at the time, and the row it protects is the same either
  way.

### Added — the resting-HRV trend verdict

**`GET /metrics/hrv?from=&to=`** (T085) returns the per-day series the UI charts and the verdict the
engine owes `research/00` §1.6, computed from F004's captures and bucketed into the athlete's local
days.

- **The verdict is one of `hrv_normal`, `hrv_suppressed` or `hrv_unavailable`**, decided against a
  smallest-worthwhile-change band in log space — `mean(ln rMSSD) ± 0.5 · SD(ln rMSSD)` over the
  baseline, floored, with `band.floored` reporting when the floor was used.
- **Baseline**: the 60 local days ending a week before the judged day, established at **14 distinct
  local days**. **Window**: the 7 local days ending on the judged day, judged at **3 distinct local
  days**. One tier owns the baseline — ranked `chest_strap_raw` > `health_snapshot` >
  `health_api_overnight` — and it must also cover the judged week.
- **A silence of more than 21 days resets the era**; a tier last read more than 28 days ago cannot
  own the baseline. `reset_reason` distinguishes `coverage_gap` from `tier_change`.
- **Every excluded reading is reported with the reason it was excluded**, so the verdict is
  reproducible from the response alone (§1.6) rather than from the database.
- **Silence is the free direction.** Anything the rule cannot establish reads `hrv_unavailable`
  rather than `hrv_normal`, because §1.7 forbids up-regulation on weak evidence. Ten review cycles
  found **seven** separate populations where the rule had up-regulated anyway; each is one of the
  entries below.

### Changed in review — cycles 3 through 9

- F005/T093, amended by T094 and T095: the baseline tier must also cover the judged week -- a tier read on >= 14 distinct local days in `[D-66, D-7]` owns the baseline only if it is also read on >= 3 days in `[D-6, D]`; when no such tier covers the week, the >= 14-day tier the athlete was read on last keeps it (T094; T093 had said the densest), so a switched athlete's thin week no longer reverts to the abandoned device, and an empty week begins no reset -- a reset already in force persists through it (T095 corrected "with no reset", which was true only of beginning one). **Every count is in distinct local days** (T095; rules 1-3 counted captures, so one re-taken strap morning handed a genuinely suppressed week to a strap baseline that could not judge it, and 14 captures on 7 days made a tier a candidate the same response reported as not established). `tier_change` is reported only when the new tier sustains the window, differs from the tier the previous 60 days sustained, and the two eras do not interleave over the previous and current windows and the judged week together, **with a density tolerance for isolated captures** (T095): the readings on the wrong side of the era boundary -- the new tier's from the old era's first day to its last reading, the old tier's after the new era's first -- are corroboration when together they are fewer than 14 distinct days and fewer than 3 in the judged week, so one strap capture the week before the switch, one snapshot auto-capture after it, and a two-week strap trial three months earlier no longer silence a genuine switch's reset (judged exactly, each did, for the whole era; a two-week trial abandoned two months ago and a young two-to-three-days-a-week strap habit still never report one, and the same-instant switch-day tie stays interleaved). Abandoning an owning strap for the daily snapshot is reported the day the snapshot first owns the baseline rather than a month later; `reset_on` is the era's true first day and does not slide once it is older than 60 days. Accepted costs, named in F005's Negative Class: the two-to-three-day strap alternates the tier whenever its day count in the sliding judged week crosses 3; a stale trial still inside the window plus three strap days this week is judged on the trial's band ("stale candidacy"), and a stale trial of the new tier that still holds 14 days in the previous window delays the switch's reset until it ages below 14 there; a habit of the other tier dense enough to be a candidate, or three of its captures in the judged week, is use and interleaves, so no reset is reported for those days -- **the report only**: since T098 the baseline is clipped at the era boundary whenever one exists, reported or not, so no *reported* band or verdict moves when the sliding judged week passes over a capture of the other tier (before it, three such captures -- contributing nothing to the week mean -- withdrew the reset, un-clipped the baseline back to the full 60 days and flipped a verdict from suppressed to normal with no new data), and the readings the clip removes are listed `before_reset: tier_change` rather than appearing in neither list; that is the tolerance's *week* half -- its *candidacy* half (T102, decision log D5: kept and priced, no behaviour change) decides whether an era boundary exists at all and hence whether the baseline is clipped, and the count is pooled over both tiers in distinct local days, so the 14th stray day -- which an old-tier capture after the switch, outside the judged week and contributing nothing to the week mean, can supply -- removes the boundary, un-clips the baseline, steps the band and can flip a verdict from suppressed to normal on an unchanged week mean (13 stray days: n 33, `band.lo` 3.6789, suppressed; 14: n 43, `band.lo` 3.4791, normal; 7-day mean 3.4965 on both), an accepted cost now priced in F005's Negative Class and AC 17 and pinned; a cleanly-ended strap trial followed by a thin week reads as a switch until the snapshot covers a week again. The reset clears when the previous 60 days are no longer sustained by the old tier: for a forward switch when the new tier reaches 14 days there (S+80 for a daily device), for the reverse one when the old strap drops below 14 there (T+114). T103 (review cycle 5, G-C5-5): the contract and the schema had also published that a reported `tier_change` never sits beside an unclipped `window`; false once the era's first day is `date-66` or older, when the clip `max(date-66, R)` is a no-op and `window` is byte-identical to `[date-66, date-7]` while `tier_change` is still reported (S+67..S+80 for the daily forward switch above, `reset_on` beneath `window[0]` throughout) -- corrected at both sites in the same words and pinned through the endpoint's rendering; `contracts/check_drift.py` compares implemented endpoints (path, method, 2xx codes, required request shape), not description prose, so it passed throughout and a clean drift check is not a checked contract. T105 (review cycle 6, G-C6-1): T103's replacement gloss over-generalised one clause further, calling that no-op stretch "the last stretch of every report's lifetime"; it is conditional, not universal -- when the old tier's density in `[date-126, date-67]` decays on its own the report ends before `date-66` reaches `R` and every reported day is clipped (reproduced in `build_series`: a snapshot era thinning to every 5th day before a daily strap at R = 2026-05-01 reports `tier_change` on R+20..R+45 with `window[0]` = R throughout, while `date-66` reaches R only at R+66, 21 days after the report goes null; same shape at 6- and 7-day steps). Corrected at both sites in the same words, and both copies are now compared to each other by `test_the_two_copies_of_the_reset_reason_contract_publish_the_same_claims` (G-C6-2), which pins the six load-bearing claims and the two withdrawn universals rather than the paragraph -- until T105 nothing in the tree read this prose except a one-shot `! grep -q` inside each patch task's own acceptance probe. T105 also deleted a vacuous assertion in `test_a_reported_tier_change_sits_beside_the_unclipped_window_once_the_era_is_older_than_it` that no fixture row could make fail (G-C6-3). T106 (review cycle 6, G-C6-4): five documents said the tolerance's week half "decides the report only" and that neither the band nor the verdict moves with it. It is in fact the **first ordering term** in `_era_boundary`'s selection key (`max((_isolated(strays, judged), stray_days, a_end.start_time, first_day), key=(b[0], -b[1], b[2]))`), so it also selects which admitted boundary wins and hence where the baseline is clipped -- pinned by `test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of`, where a boundary with four stray days beats one with three because the judged week is clear of it. No reported outcome moves with it because an isolated boundary is always a candidate and so is still the one chosen for every series `build_series` can be handed; that reachability, not the rule, is why the simpler reading held, and it is now written down in `research/00` §5.4, spec §3.7.3, the construction reference, F005's Negative Class and AC 17's tolerance clause. T104 had corrected the same claim only at the three sites its own diff touched, leaving the authority contradicting the reference derived from it; no behaviour, test or contract changed. T108 (review cycle 6, G-C5-2, raised in cycle 5 and scheduled once cycle 6 measured its scope; tests only, no behaviour change): the tolerance counts **distinct local days**, and all three sites that say so were undefended -- `_era_boundary`'s candidacy gate (`stray_days = len(_days(strays))`, `hrv_trend.py:1099`) and both halves of `_isolated` (`:986`, `:987`). Each survived the whole F005 suite when regressed to counting captures, because every stray fixture in it seeded exactly one capture per stray day. The population that separates the two readings is F004's own -- an athlete who **re-takes a morning after a bad reading**, so 13 stray days are 14 stray captures -- and under the capture reading the boundary is refused, the pre-switch trial re-enters the band and the athlete crosses D5's priced candidacy cliff by taking one reading twice: on the pinned series, `tier_change` on `D-29` / window `(D-29, D-7)` / n 23 became `null` / the unclipped `baseline_window(D)` / n 30. Three pins now assert values that differ between days and captures at each site, and each kills its mutant (verified in-process at `7103fd7`, against a control mutant that killed 3). Rules 1-3's own day count (`_tier_counts`) was already pinned and is unchanged. T109 (review cycle 6, G-C6-6; docs and tests only, no behaviour change): T105's replacement gloss was itself false. It read report-liveness as "that is, only while the old tier still sustains the previous window [date-126, date-67]", equating the whole of rule 4 with clause (b); (b) is necessary, not sufficient. Reproduced in `build_series` -- a daily snapshot for 200 days to R-1 and a daily chest strap on R..R+10 only, R = 2026-05-01 -- where at D = R+66 the snapshot still holds **60** days in [D-126, D-67], so the old tier sustains and (b) holds, and `reset_reason` is **null** because the strap holds 11 of the 14 days clause (a) needs in the baseline window. The week half lapses independently in the same way: on a daily switch that reports `tier_change` at D = R+66 with an un-clipped window, three old-tier days inside [D-6, D] make it null on that day with (a) and (b) untouched. Both published copies now say liveness is rule 4's three conditions together and name them, in the same words at both sites. The same equivalence had been published a second time in the served copy alone, as the report's lifetime ("`tier_change` until the previous window ... is no longer sustained by the old tier"), where the two-copy oracle could not see it because it compares only the run the two copies share; corrected there too, and both spellings are now in `RESET_REASON_WITHDRAWN`. The reset clearing when the previous 60 days stop being sustained by the old tier (above) is therefore one of three ways the report can end, not the only one. **The oracle had inherited the defect**: `RESET_REASON_CLAIMS` entry 5 transcribed the false sentence in the same pass that wrote it, so the test built in T105 to stop false prose recurring made withdrawing it go red -- an oracle that transcribes prose converts a mistake into an enforced invariant, which is G-C5-7's no-independent-oracle shape applied to prose. All six entries were re-derived against `build_series` rather than against the paragraph, and the series each was checked on are recorded beside the tuple. T110 (review cycle 6, G-C6-7; documentation only, no behaviour, test or contract change): two accepted-cost rows in F005's Negative Class named the wrong error direction against `research/00` §1.7 ("down-regulate freely, up-regulate cautiously"). The cleanly-ended-trial row called `hrv_normal` on the trial's band "the direction §1.7 tolerates" -- it is the direction §1.7 tolerates *least*, since telling Section 6 that readiness is intact lets a planned hard session stand on weak evidence, exactly what `judge`'s own docstring names as forbidden. The stale-candidacy row called a false suppression "the direction §1.7 tolerates least" -- a false suppression is down-regulation, which §1.7 tolerates *freely*. Both corrected, and the same inverted sentence was found and corrected at a third site, IDEA-064. The stale-candidacy row had also priced only one of its two directions: if the July band sits *below* this week's readings, a genuinely suppressed week reads `hrv_normal` for two days -- the forbidden direction, never named in the row at all. It is the reachable one (the week reads normal whenever `mean7 >= mean_July - 0.5*SD_July`, so a stale mean about 0.10 ln low -- roughly 10% of rMSSD over seven weeks -- hides a suppression a full half-width deep, against F005's own documented band steps of 0.20 and 0.68 ln). Both directions are now stated, and **the acceptance of that row is re-opened rather than re-confirmed**: it was accepted in cycle 2 as a false suppression, the free direction, so the reasoning it rests on no longer holds. Left as a user decision with [[IDEA-064]]; nothing an athlete sees changes today. T111 (review cycle 7, G-C7-1 and G-C7-4; documentation and tests only, no behaviour or contract change): the (b)-alone equivalence T109 withdrew was still standing at four live sites, none of them a description string and so none of them visible to `RESET_REASON_WITHDRAWN` -- `tier_change_reset`'s own docstring (re-emitted by T107 two commits before T109 retracted the claim elsewhere), `test_a_reported_tier_change_sits_beside_the_unclipped_window_...`'s docstring ~250 lines below the tuple T109 corrected in that same file, the construction reference's Sustained-tier-change cell and F005's review-cycle-3 clarification. All four qualified: the `S+80` / `T+114` arithmetic is kept and is correct *for the (b) route*, but it bounds the report rather than defining its lifetime. The scan is widened rather than the sites merely patched -- `test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy` reads the withdrawn tuple, plus a new `RESET_REASON_WITHDRAWN_IDIOMS` for the docstring spellings no contract copy would use, against `hrv_trend.py`, `schemas.py`, `contracts/openapi.yaml`, both HRV trend suites and the two spec documents, **flattening whitespace first**: a line-oriented grep for the sentence returned nothing on `hrv_trend.py` because it wrapped mid-phrase, the false all-clear `sweep-the-claim-not-the-diff` warns about. The F005/T096 bullet above also carried T107's `reset_on` / `window[0]` claim unqualified and now carries the same qualification T107 applied at three other sites. T112 (review cycle 7, G-C7-2; tests and documentation only, no behaviour change): T109's "liveness is rule 4's three conditions together" was published to clients and enforced by nothing but string containment -- if `build_series` regressed so a lapsed clause (a) still reported `tier_change`, every assertion in the two-copy oracle stayed green. T109's own reproduction is now a pin: a daily snapshot for 200 days to `R-1` and a daily strap on `R..R+10` only (`R` = 2026-05-01), read at `D = R+66`, where the strap holds 11 of the 14 days clause (a) needs. It asserts all three facts, not just the null -- that (b) holds (the snapshot holds all 60 days of `[D-126, D-67]`, computed from the fixture's own days), that the week half holds, and that `reset_reason` is null anyway -- with a control three strap days longer that reports `tier_change on R` on the same window and the same judged week, so clause (a)'s day count is the only thing that moved. Verified by perturbation: deleting `tier_change_reset`'s clause (a) gate makes the 11-day strap report `tier_change on 2026-05-01` and turns the pin red, **while the two-copy oracle stays green** -- which is the gap, measured. `RESET_REASON_CLAIMS`' stated authorship is corrected with it: T109 said the entries were "re-derived from `build_series`", the wrong direction for a contract table; they are constrained by `research/00` §5.4 and F005 and were *reproduced* against `build_series`.
- F005/T096: a coverage gap is reported however long the layoff. The leading silence of `[D-66, D]` is now measured from the latest reading known to precede the window -- among the rows read, or, when none was read, the store's earliest reading, one `MIN(start_time)` scalar (`db.earliest_hrv_reading`) read beside the rows without widening the 126-day row read -- so an 87- or 120-day layoff resets at the resumption exactly as a 60-day one does (before, only a layoff whose last pre-gap reading fell inside `[D-126, D-67]` did), and a new athlete's first capture, with no earlier reading of any tier in the store, still resets nothing. Documented, not changed: `coverage_gap` is reported only while the resumption lies inside `[D-66, D]` (67 days), so its `reset_on` never precedes `window[0]` while `tier_change`'s can -- **qualified 2026-09-15 (T111, G-C7-4), as T107's own bullet below already qualifies the same claim at three other sites**: a `coverage_gap`'s `reset_on` *does* precede `window[0]` when a tier-change era boundary clips the window later than the resumption, since T107 made the two clips compose as the later of their first days and left only the report to the gap; and a resumption that was also a device switch is reported as `coverage_gap`, then `tier_change` on the same day, then nothing -- an accepted cost named in F005's Negative Class. F005's demo probe now seeds a five-era two-tier history and asserts the tier rule through the endpoint: `baseline.tier`/`established`/`n`, a strap trial that does not cover the judged week leaving the snapshot's suppression in force, a genuine switch's `tier_change` with its exact `reset_on` (still reported past one isolated old-tier capture), and `points[]` continuity across the flip.
- F005/T107 (**behaviour change**): a coverage gap no longer cancels the tier-change baseline clip. `build_series` asked rule 4 only when no gap reset had fired, so a silence of more than `gap_reset_days` anywhere in `[D-66, D]` meant no era boundary was computed and **nothing was clipped at all** -- the now-sustaining tier's pre-switch readings, which `research/00` §5.4 says are never in the band, re-entered it, and a genuinely suppressed week could read `hrv_normal` on an unchanged 7-day mean (the under-calling direction §1.7 tolerates least, with no threshold to cross: any gap did it). Rule 4 now runs on every request and the two clips compose as the later of their first days, so `baseline.window[0]` is `max(date-66, <the resumption>, <the era's first day>)` and the readings the era clip removes are listed `before_reset: tier_change` beside the gap's `before_reset: coverage_gap`. **What moves for an athlete:** any day whose baseline window holds both a gap resumption and a later device-era boundary now reports a narrower `baseline.window`, a smaller `baseline.n`, a different `band`, and possibly a different `verdict` -- on a reproduced series, `(2026-07-29, 2026-08-31)` / n 25 / `band.lo` 3.4915 / `hrv_normal` became `(2026-08-13, 2026-08-31)` / n 19 / `band.lo` 3.6771 / `hrv_suppressed` with no new data. **What does not move:** the gap keeps precedence over the *report* -- `reset_reason` stays `coverage_gap` and `reset_on` stays the resumption. One published claim is qualified with it: a `coverage_gap`'s `reset_on` **can** now precede `baseline.window[0]`, when the era boundary clips later than the resumption (`schemas.Baseline.reset_on`, the construction reference's Coverage-gap row and F005's Negative Class say so). Every reading is still in exactly one of `series` / `excluded` (`research/00` §1.6). A sixth site carried the same retracted claim as an identity rather than a "never" -- `schemas.Baseline.window`'s "For `coverage_gap` R is reset_on", served to clients through `app.openapi()` and contradicting `Baseline.reset_on` three fields below -- and now says that for `coverage_gap` R is the later of the resumption and any era boundary, so `window[0]` may lie after `reset_on`; `contracts/openapi.yaml`'s window description, which said nothing about `coverage_gap` at all, publishes the same sentence, and the two copies of the **window** contract are now compared claim by claim by the endpoint oracle that until now read only `reset_reason`. The composition is pinned in both directions: gap-earlier (the era boundary clips later) and gap-later (the resumption clips later, where taking the era's first day alone would publish a window claiming 51 days the series does not contain, with the reported `reset_on` inside it).

- F005/T116 (**behaviour change**): `hrv_normal` now requires an established baseline. `judge` consulted `established` only inside the `window_mean < band.lo` arm, so a week whose 7-day mean sat inside or above a band built from **2 to 13** readings reported `hrv_normal` while the same thinness withheld `hrv_suppressed` -- telling Section 6 that readiness is intact on a baseline the same response reported `established: false`, the up-regulating direction `research/00` §1.7 forbids and `judge`'s own docstring already named as forbidden for the cell below the band. The reasoning had been applied to one cell and not its two neighbours, and the suite **ratified** the asymmetry: `test_hrv_trend_band.expected_row` returned `NORMAL` regardless of establishment and `test_a_thin_baseline_with_the_mean_inside_the_band_is_normal` pinned it by name. It was reachable after every **coverage-gap** reset the feature performs -- a coverage gap collapses the baseline deliberately and the athlete then traverses 20 unestablished days -- (*corrected in place 2026-09-18, T142: this read "after **every** reset the feature performs -- a coverage gap **or a tier change** collapses the baseline" until now, which is false of a tier change; a clean source-tier change collapses nothing, `established` stays **true** and `n` merely decays 60 -> 47, so it traverses **zero** unestablished days and never reached this cell -- T138*) and was constructed and run at `da4cdf0` (Pacific/Auckland, D = 2026-09-14): 86 days of daily chest strap at ~50 ms, a 30-day illness layoff, 10 days back at ~40 ms gave `reset_reason=coverage_gap`, `baseline_n=3`, `established=False`, `readings_in_window=7` and `verdict=hrv_normal`, band `lo=3.6798 hi=3.7058`, for an athlete ~20% below his own pre-layoff level. The gate is now symmetric (user decision 2026-09-15, [[IDEA-062]] closed). **What moves for an athlete:** the ~12 days after every **coverage-gap** reset that used to read `hrv_normal` now read `hrv_unavailable` until the fresh baseline reaches 14 distinct local days (*corrected in place 2026-09-18, T142, from "after every coverage-gap or tier-change reset": a tier change begins no fresh baseline, so no day of one waits for one -- T138*); Section 6 reads that and widens its guardrails instead of being told readiness is intact, which is down-regulation on weak evidence -- the direction §1.7 tolerates freely, and the accepted cost of the change, priced in F005's Negative Class under "The verdict". **What does not move:** the band, which is still reported wherever the baseline can build one, established or not, so an unavailable verdict stays reproducible by hand (§1.6); `baseline.n`, `established`, `reset_reason`, `reset_on`, `readings_in_window` and the tier rules are untouched, and no established baseline changes verdict. Eight of the 90 `CONTRACT_TABLE` rows moved -- `{2, 13} baseline days` x `{3, 7} window days` x `{inside, above}`, every row the rule reaches -- and one day of one reset pin: `test_one_new_tier_capture_before_a_genuine_switch_does_not_silence_its_reset`'s `SW+20`, where the reset had just clipped the era to 13 readings, while `SW+21` (14) and `SW+60` (53) are unchanged, which is what shows the change is the establishment gate and not the clip. The `verdict` description is corrected in **both** contract copies (`schemas.HrvTrendResponse.verdict` and `contracts/openapi.yaml`) and they are now compared claim by claim by `test_the_two_copies_of_the_verdict_contract_publish_the_same_claims`, the third such pin after `reset_reason` (T105) and `window` (T107) -- `check_drift.py` compares paths, methods and parameters, never prose, so a correction landed in one copy alone would otherwise pass every gate in the tree. `research/00` §5.4 carries the clarification first, then spec §3.7.3's verdict bullets and anti-mixing paragraph, F005's acceptance criteria (a companion to AC 6, which asked only whether a thin baseline can *suppress*), F005's `### The verdict` subsection and its new cost table, and the construction reference. [[IDEA-064]] is **not** closed by this: the stale-trial route reports `established: true` with `n` 14, so the trial is established, merely old. T118's measurement row is re-scoped rather than restated: its 40,000-trial direction search was run against the pre-T116 rule and its re-run is still pending.
- F005/T117 (**behaviour change**): a stale trial can no longer own a judged week on a seven-week-old band. Rule 1's candidacy gate in `resolve_baseline_tier` counted days alone — `min_baseline_readings` (14) distinct local days in `[D-66, D-7]` — with no recency of any kind, so a two-week chest-strap trial abandoned in July stayed a live candidate for every judged week until its first day aged out of the 60-day window, and the first week that held three strap days handed it the baseline on fidelity. Reproduced on the series kept verbatim as `test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band` (a daily Health Snapshot throughout, a 14-day strap trial ending 2026-07-16, three strap days in the judged week): on 2026-09-06 and 09-07 the response reported `baseline.tier` chest strap, `established: true`, `n` 14 and `hrv_normal` on a band whose every reading was seven weeks old, while **three of that series' fourteen genuinely suppressed days sat inside the judged week**. That is up-regulation on weak evidence, the direction `research/00` §1.7 forbids and `judge`'s own docstring names as forbidden; it is silenced whenever `mean7 >= mean_July - 0.5*SD_July`, so an offset of about 0.10 ln — roughly a tenth of rMSSD over seven weeks, against F005's own documented band steps of 0.20 and 0.68 ln — hid a suppression a full half-width deep. The cost had been accepted in review cycle 2 as a *false suppression*, the free direction; T110 found that cell inverted and the forbidden direction never named, and re-opened the acceptance rather than re-confirming it. Resolved by change on a user decision of 2026-09-15 ([[IDEA-064]] closed): **candidacy now also requires recency**, relative to the other candidates — a tier whose latest `[D-66, D-7]` day falls more than `RECENCY_TOLERANCE_DAYS` (**28**) behind the most recent such day of any candidate is struck from the candidate set before week coverage is consulted. **What moves for an athlete:** on the reproduction series 2026-09-06 moves from `hrv_normal` on the July strap band to **`hrv_suppressed`** on the athlete's own 60-day snapshot band; 09-07 stays `hrv_normal` for the correct reason, only two suppressed days remaining in the week (7-day mean 3.5598 against the snapshot band's `lo` 3.5079); `baseline.tier` no longer flips to the strap on a two-day-old habit and `baseline.window` no longer reaches back to July. **What does not move:** the comparison is between candidates, never against an absolute offset from `D-7`, so a lone candidate is its own reference and is never struck however old it is; `schemas.Thresholds` keeps exactly its six keys, `contracts/openapi.yaml`'s `thresholds` block is unchanged and `contracts/check_drift.py` exits 0 — this is not a contract-shape change, and the constant is deliberately not published. Rule 4's previous-window clause (b) deliberately does **not** carry the condition (measured: applying it there turns `test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline` and `test_one_resumption_era_is_reported_coverage_gap_then_tier_change_then_nothing` red), so [[IDEA-064]]'s G12 stale clause-(b) candidate is unchanged. **Why 28:** four judged weeks (4 × `WINDOW_DAYS`), and — the load-bearing half — greater than `GAP_RESET_DAYS` (21), so relative staleness never strikes a tier for a silence shorter than the shortest silence this feature calls a break. That seam is an arithmetic claim about two units and is stated here in one (corrected 2026-09-15, T121, after it was published in a form no test enforced): the recency gate compares two `last_read` **days**, so a day difference of `g` is a silence of `g-1` whole days, while a break is a silence of **more than** 21 days — a day difference of 23 or more. Staleness strikes at `g > tolerance`, so the two rules stop disagreeing exactly at `tolerance >= 22`, which is `tolerance > GAP_RESET_DAYS`; the row that pins it is the one whose day difference is `GAP_RESET_DAYS + 1` = 22, a silence of exactly 21 days, admitted at a tolerance of 22 and struck at 21. The two mechanisms partition rather than race: a wholly silent series is clipped by the coverage gap before candidacy is counted, and this rule acts only where another tier kept the series alive. **The accepted cost:** a device genuinely resumed after more than 28 days away is not a candidate on the day it resumes — an athlete putting the strap back on after a five-week holiday, with a daily snapshot running throughout, is judged on the snapshot until the strap's own readings carry it back, which they do, because the strap then becomes the most recently read candidate and is its own reference. That is suppression-side conservatism, the direction §1.7 tolerates freely, bounded, and priced in F005's Negative Class; its measured bracket is the N=17 end of the tolerance band, where it becomes a defect. **The rejected form, recorded so it is not re-proposed:** the parameter-free strict comparison (`tier.last_read > other.last_read`) was built and measured on 2026-09-15 and turns five pinned tests red in both error directions — a daily lower-fidelity tier is always read at least as recently as a two-or-three-day-a-week higher-fidelity one, so a strict day-comparison rejects the oscillating strap (4 days behind) and the abandoned July trial (45 days behind) alike, voiding rule 2's fidelity precedence instead of qualifying it. The two populations differ only in magnitude, and a magnitude is a constant. `research/00` §5.4 carries the amendment first, then spec §3.7.3 and §3.7.4, F005's Negative Class row (resolved by change, not re-acceptance), its acceptance criteria and its tier-rule negative class, the construction reference's constants table and rule-1 cell, and the decision log; `spec_outline.md` Section 3 states the band and the window only and names no tier rule, so it is exempt rather than swept. [[IDEA-062]]/T116 does **not** subsume this: the stale trial reports `established: true` with `n` 14 — established, merely old.
- F005/T126 (documentation; **no behaviour change**): T116's accepted cost is **20 days** of `hrv_unavailable` after every **coverage-gap** reset (*corrected in place 2026-09-18, T142, from "after every reset": [[T138]] measured the tier change and found it collapses no baseline -- `established` stays true, `n` decays 60 -> 47 -- so it traverses zero unestablished days; its own silence is 18 days of week coverage, a different mechanism. This entry is corrected rather than left as a dated record because the sentence asserts a claim about **behaviour** in the project's own voice, not a record of what T126 did; the wording it replaced is quoted here, so nothing is erased*), not the "~12" six live sites stated as a duration. The baseline window is `[D-66, D-7]` and `MIN_BASELINE_READINGS` is 14, so a reading on reset day `R` enters the window at `D = R+7` and the fourteenth distinct day `R+13` at `D = R+20`: `established` first becomes true at `R+20` and `R+0 .. R+19` report `hrv_unavailable`. The 12 is a different quantity — the days whose verdict T116 *changed*, `R+8 .. R+19`, since a band needs two readings and `R+0 .. R+7` already read `hrv_unavailable`. Both numbers are true of different things, which is why five review iterations found no contradiction: each audited whether a claim was *enforced*, none whether a number was *true*. Corrected in F005's cost row and acceptance criteria, this file, the decision log, the construction reference, `judge`'s docstring and `test_hrv_trend_band`'s; the sentences scoped to "days that used to read `hrv_normal`" were already right and are unchanged. The duration is now **pinned**, not restated, by `test_hrv_trend_reset.test_the_establishment_delay_after_a_reset_is_twenty_days`, which walks `R+0 .. R+20` and asserts `established` first at `R+20`. **The 2026-09-15 acceptance of that cost was taken against the 12 and is re-opened, not re-confirmed** — a coverage gap needs more than 21 silent days to fire, so the composed quiet is ~3 weeks of no captures then 20 days of `hrv_unavailable`. Awaiting a user decision (F005 decision log, 2026-09-16).
- F005/T125 (**behaviour change**): a returning device is no longer judged on the mornings that predate its return. T117's recency gate strikes a stale candidate on `last_read` measured over the **baseline** window `[D-66, D-7]`, while the verdict's freshness lives in the **judged week** `[D-6, D]`, and nothing required the surviving tier's week readings to be the recent ones — so the gate can evict the tier the athlete is *currently recording on* in favour of one whose week coverage has just run out, and the verdict is then computed from that tier's last few pre-return days. The same shape as T117's original defect, one level over: not a stale *band*, a stale *week*. Reproduced by the critic over 724 device-return geometries and rebuilt independently by the orchestrator (Pacific/Auckland: chest strap daily to 2026-07-31, a 39-day strap silence carried by a daily Health Snapshot to 09-08, the strap resuming 09-09 with four consecutive suppressed mornings). At `D = 2026-09-12` the shipped code reported `baseline.tier` `health_snapshot`, `established: true`, `readings_in_window` **3** — 09-06, 09-07, 09-08 — the athlete's own four mornings all `off_baseline_tier`, and the verdict **`hrv_normal`**: he came back to his strap, recorded four suppressed mornings and was told readiness is intact on a week he did not live. That is up-regulation on weak evidence, the direction `research/00` §1.7 forbids, **36 times** across the 724 geometries, and named nowhere: the Negative Class row, the acceptance criterion and the only fixture all carried the qualifier *"while a daily snapshot ran throughout"* — the sub-population where the cost is benign — so the mirror population, where the carrier stops *because* the athlete went back to the other device, was never constructed (`spec-frame-blindness` exactly). **Resolved by withholding, on a user decision of 2026-09-16 taken on six measured forms and a 2050-geometry sweep:** a candidate struck for staleness whose judged-week readings are all *later* than the resolved tier's means the week is not a fair sample of the tier being judged, so **no verdict is asserted**. The predicate is over the **order of two tiers' judged-week day sets**, not counts — "entirely earlier than the return" is what separates a device return from an abandoned trial picked up for three days, and dropping the order clause while keeping the count reds 5, the July-trial pin among them. `_recency_struck()` is factored out of `resolve_baseline_tier` unchanged so the struck set can be read at the verdict; `build_series` computes `HrvSeries.withheld`; `judge`'s verdict branch gains `not series.withheld`. **What moves for an athlete:** 72 of 2050 swept return geometries go `hrv_normal` → `hrv_unavailable` — per athlete, mornings **3 and 4** back turn silent on top of mornings 5, 6 and 7, already silent for want of week coverage, so **five of his first seven mornings back say nothing, two of them this change's doing**; it stacks additively in front of T116's 20 unestablished days when the return also trips a reset. **What does not move:** the resolved tier and the reported baseline window are identical to the shipped rule on **all 2050** rows, the only verdict change in either direction is `hrv_normal` → `hrv_unavailable`, and `MIN_BASELINE_READINGS`, `MIN_WINDOW_READINGS`, `RECENCY_TOLERANCE_DAYS`, `GAP_RESET_DAYS`, `BASELINE_DAYS`, the band, the gap rule, rules 1-3's selection and the establishment gate are untouched; `schemas.Thresholds` and `contracts/openapi.yaml` keep exactly their six keys, `recency_tolerance_days` stays unpublished, and `check_drift.py` exits 0. **The form that was refused, and why, since it led on the earlier measurement:** form 4 re-admits the struck tier on the same predicate and tells the returning athlete `hrv_suppressed` from his third morning back — cleanest of the six on red counts (4 of 407, all the walk that pins the defect) and moving 0 of the 114 G3 targets. Swept, it closes the same 36 and **creates 54** (`hrv_unavailable` → `hrv_normal` on a band 36-53 days old, median 45) plus 36 more where a surviving `hrv_normal` comes to rest on that band; its reach into mornings 5-7 is one predicate landing safe-side for a suppressed athlete and forbidden-side for a healthy one in exactly equal numbers (54 and 54), and the created population is the *more ordinary* return — the carrier recorded daily right through the layoff, so continuity needs nothing to happen, while a suppressed return needs a discontinuity timed to the device change. Cost on the ordinary athlete and benefit on the unusual one is the reverse of §1.7's asymmetry, so the form whose cost is silence was taken: 36 closed, **0** forbidden-direction geometries created, against 54. Forms 1 and 3 are the same form and both re-break the July trial; form 5a widens `hrv_unavailable` into a genuine device switch with no returning device in it. **The residual, named rather than assumed closed:** mornings **1 and 2** back hold fewer than `MIN_WINDOW_READINGS` on the returning tier, so the verdict there still comes from pre-return readings — and no measured form reaches them, because the guard against false withholds and the constant that hides those rows are the same constant. `research/00` §5.4 carries the amendment first, then spec §3.7.3 (a fourth verdict bullet) and §3.7.4, the construction reference, F005's Negative Class, its cost table and a new `@must` acceptance criterion, and the decision log; `spec_outline.md` Section 3 names no tier rule and no withhold, so it is exempt rather than swept. Pinned by `test_the_device_return_is_walked_morning_by_morning_through_judge` (T127's walk, whose `r = 3`/`r = 4` expectations were authored as the defect and are the four assertions this change moves).
- F005/T123 (measurement; **no behaviour change**, no production logic touched): the direction search G-C7-3's acceptance rested on was re-run against the shipped rule, and **it finds the flip in both halves**. G-C7-3 -- a coverage gap *creates* an era boundary the full capture history refuses -- was accepted on 2026-09-15 not because it is rare (~5% of 30,000 randomized histories, `baseline_n` collapsing as deep as 27 -> 1) but because of its **direction**: a 40,000-trial search for a flip to `hrv_normal` found **0**, so every collapse landed in `hrv_unavailable`, the under-call `research/00` §1.7 tolerates freely. That search was run against a rule that has since moved **four** times -- T116 (`73d6702`), T117 (`eda0412`), T127 (`28992a1`) and T125 (`377534f`) -- and F005's row named only T116. **Half 1, the era-boundary search.** T116 makes the *thin* case structurally safe, so the 0/40,000 result is uninformative about the case that matters: a clip leaving `baseline_n >= 14`, where the clip still moves `band.lo` and a lower `band.lo` is an up-regulating band. Re-run at `377534f` over **30,000** randomized capture histories (26,360 well-formed) against the *same-history* reference the sibling pin names -- the boundary the **full** capture history finds, same gap clip, same resolved tier, so only *which boundary* differs and the judged week and its mean are identical on both sides: **9,230** moved the boundary, **4,466 (16.94%)** of those also held `baseline_n >= 14`, the worst band shift in the up-regulating direction was **0.031367 ln** (`n` 19 -> 14, `lo` 3.380689 -> 3.349322), **702** geometries were flip-reachable, and **5 trials realized the flip on an unchanged week mean** -- shipped `hrv_normal`, the full-history reference `hrv_suppressed`, `baseline_n` 15..20 and established on both sides, none of them constructed. The widest: `coverage_gap on 2026-08-03`, shipped boundary 2026-08-14 against the full history's 2026-07-07, window `(2026-08-14, 2026-08-31)` `n` 18 `lo` 3.698502 against `(2026-08-03, 2026-08-31)` `n` 23 `lo` 3.712394, 7-day mean **3.702183 on both**. **Half 2, tier substitution.** On the flat 2050-row return rectangle T125's withhold closes the population outright -- 180 of 2050 withheld, **0** forbidden flips, the critic's 36 + 54 now reading `hrv_unavailable` instead of `hrv_suppressed`. But T125's rule is an **order**, not a count: every judged-week day of the struck tier must be later than **every** judged-week day of the resolved tier, which holds only if the carrier *stops* on the day the athlete goes back. Add one dimension -- `c` carrier captures kept on the last `c` local days, the athlete who puts the strap back on and **keeps wearing the watch** -- and the withhold fires on **none** of the 2050 rows: **54** forbidden flips at `c = 1`, **72** at `c = 2`, the full **90** at `c >= 3`. Representative (`c = 3`, `s = 32`, `q = 3`, `r = 4`): 7-day mean **3.7108** on the carrier's pre-return mornings against the athlete's own **3.2189**, `baseline_n` 29, `readings_in_window` 5 -- `hrv_normal` on a week he lived as four suppressed strap mornings. **T125 closed the reproduction, not the population**, and this residual is distinct from the mornings-1-and-2 one its docstring names: it survives at every `r`. **Deliberately not pinned and not re-accepted.** Pinning either flip would freeze the behaviour the decision is about, and T123's branch is explicit that up-regulation on weak evidence is a user decision, not a builder's. F005's Negative Class row for G-C7-3 now names T116, T117 and T125 as the reasons the prior measurement was stale and carries the new result in place of "the re-run is still pending"; the T125 cost table gains the carrier-overlap residual; the amendment chain records both. The generator, the denominators and all five witnesses are in `spec/references/T123-direction-search-rerun.md`. Verified: docstring-stripped ASTs of `hrv_trend.py`, `schemas.py` and `main.py` are identical to `377534f`, the five HRV suites are green with no test added, removed or changed, so `SCOPED_SUITE_COLLECTED` is unmoved -- and its value stays where T121's lesson put it, in the pin rather than in a document.
- F005/T129 (**behaviour change**): a coverage gap can no longer **create** an era boundary the athlete's full capture history refuses. `tier_change_reset` was handed `baseline_readings` and `week_readings` derived from the **gap-rebound** `readings`, so every reading in `[D-66, gap_reset_on)` was invisible to `_era_boundary`'s **stray count** as well as to clause (a)'s candidacy count. New-tier readings hidden there would have been strays of every *late* `A_end`, so hiding them shrank the stray term for late boundaries and could admit — or promote over an earlier candidate — a boundary the full history refuses or dates earlier; that `first_day` could fall **after** the resumption, and the composed clip then removed post-resumption readings of the baseline tier from the band as `before_reset: tier_change`, lowering `band.lo`. **Rule 4 now counts its strays over the unclipped population**: every reading of every tier in `[D-66, D]`, gap-clipped or not, together with the previous window. The clip continues to decide which readings enter the *band*; it no longer decides which readings the era rule can *see*. Clause (a) is unchanged and still reads the gap-clipped window — it asks whether the resumption era sustains a baseline of its own — as are `MIN_BASELINE_READINGS`, `MIN_WINDOW_READINGS`, `RECENCY_TOLERANCE_DAYS`, `GAP_RESET_DAYS`, `BASELINE_DAYS`, the band formula, the gap rule's precedence over the *report*, the composition of the two clips as the later first day, rules 1–3, the establishment gate and T125's withhold. **Why this was fixed rather than priced.** G-C7-3 was accepted on 2026-09-15 on a **direction**, not a rate — a 40,000-trial search for a flip to `hrv_normal` found **0**. T123 re-ran that search on 2026-09-16 at `377534f` against the population the shipped rule actually reaches (the old search mostly sampled the thin case, which T116 made structurally unreachable): of **26,360** well-formed randomized histories the clip moved the boundary in **9,230**, **4,466** of those held `baseline_n >= 14`, **702** were flip-reachable and **5** realized `hrv_suppressed → hrv_normal` on an identical 7-day mean with the baseline established on both sides — up-regulation on weak evidence, which `research/00` §1.7 forbids and does not let rarity excuse. **Measured at the fix:** re-run at this HEAD with the same generator and denominators, the flip class is **0 of 26,360** well-formed histories (55,562 trials, seed 20260916) where it was **18** immediately before, and shipped agrees with the full-history reference on the baseline window, `baseline_n` and the verdict on **all 26,360**. The widest witness is pinned end to end by `test_the_unclipped_stray_count_refuses_the_gap_created_era_boundary` (one history, 7-day mean **3.430187 identical on both sides**; `(2026-08-17, 2026-08-31)` `n` 15 `lo` 3.371352 → `hrv_normal` before, `(2026-08-03, 2026-08-31)` `n` 22 `lo` 3.450867 → `hrv_suppressed` now; red-first at `2b5f569` on its first assertion). **One existing assertion moved, and it pinned the removed behaviour:** `test_the_gap_created_boundary_clips_on_tier_days_at_the_resumption` — whose own docstring named "handing rule 4 the *unclipped* population" as a red-making mutation — is now `test_the_gap_created_era_boundary_keeps_on_tier_days_at_the_resumption` and asserts the same-history reference numbers that docstring had already recorded (`n` 22, `band.lo` 3.6805, no `before_reset: tier_change` entry) in place of the priced 18 and 3.6757. The *population dependence itself* stays pinned directly on the rule by `test_the_gap_clip_moves_the_era_boundary_later_than_the_full_history_finds`. No verdict moved anywhere else: T127's three verdict walks are green and assert inside their loops, and T116, T117, T125 and `test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band` are unmoved. The T125 tier-substitution residual T123's half 2 found (a carrier that keeps recording through the return disarms the order clause) is **not** touched by this and remains open. Amended in authority order: `research/00` §5.4, spec §3.7.3/§3.7.4, `spec/references/F005-trend-construction.md`, F005's Negative Class row (G-C7-3 now reads *resolved by change, not re-accepted*) and F005's decision log. `SCOPED_SUITE_COLLECTED` re-measured and updated in `test_hrv_trend_endpoint.py` (one pin added, one renamed); the literal stays in that pin and is deliberately not transcribed here, per T121's lesson. Swept for the retracted claim with `Accepted and named, not fixed`, `under-call only`, `gap-rebound` and `stray count` across the repo and the Shipyard data dir; historical task files and T123's findings document are left as the record.

### Cycle 10 — a field, a withdrawal and two corrections

- F005/T137 (**additive**): `hrv_unavailable` now says **why** it was withheld. `unavailable_reason`
  carries one of six values — `no_tier_sustains_a_trend`, `no_band`, `week_too_thin`,
  `week_not_representative`, `baseline_unestablished`, `day_not_happened` — and is `null` whenever
  the verdict is not `hrv_unavailable`. A reader could previously see *that* the engine was silent
  but not which of six unrelated causes produced the silence, which §1.6 requires to be
  reproducible from the response. T144 then made the biconditional **structural**: constructing a
  verdict that breaks `hrv_unavailable ⇔ unavailable_reason is not None` raises rather than
  serialising.
- F005/T141 (**withdrawal**): T138's composed silence rate is **withdrawn, not re-derived**. Its
  largest term priced an event — an athlete switching device mid-week — that the rule has no way to
  detect at the moment the term assumed. A rate whose dominant term names an undetectable event is
  not a conservative estimate, so it was retracted rather than rescued. The withdrawal is recorded
  in place; nothing about the shipped behaviour changed.
- F005/T145, corrected by T147 (documentation; **no behaviour change**): **the T125/T132 withhold
  residual is four mornings, not two**, and it turns on **capture spacing, not weekly count**. Four
  normative sites said "two"; the closed form is
  `min(WINDOW_DAYS - MIN_WINDOW_READINGS, k3)`. This was the seventh §1.7-forbidden population and
  the first found by varying an axis no measurement in nine cycles had varied — every prior sweep,
  2050 rows included, pinned the return at daily capture. T145's own fix then glossed the formula as
  a weekly capture count, false for half the 4/wk athletes; T147 corrected that and pinned the
  mechanism with a five-density walk. **The behaviour itself is unchanged and stays open** — at
  sub-daily density the withhold changes no verdict, which is IDEA-071's sprint to settle, not a
  patch's.
- F005/T146, T148, T140 (**test and contract gates**): T128's unavailable-cause oracle could not see
  the **published** contract (T140 — `contracts/openapi.yaml` still named four causes, not six), and
  then could not see the `unavailable_reason` **block within** it (T148). Both gaps are closed and
  each closure is proved by a mutant that was green before and reds after. `week_too_thin` is now
  qualified everywhere it appears as a count of **`baseline.tier`** readings in the judged week
  (T146, T148) — readings from any other tier never counted, and three sites had implied otherwise.
- F005/T135, T139, T142, T143 (**acceptance criteria and pins**): three criteria the feature asserted
  in prose but never stated as `@must` were added, four false assertions whose premises do not hold
  of the shipped shape were corrected, and the tier-change walk now pins `unavailable_reason`
  alongside the count it always pinned.

## 2026-09-07 — Sprint 004: Resting-HRV Capture, cycle 2 (declaration amendment)

F004's **Amendment 2026-09-06**. Tier 1 stops inferring that a recording was *meant* as a
measurement and requires the athlete to say so. This supersedes the sprint-003 "Known limitation"
below — **IDEA-010** and **IDEA-007** are resolved, and the hold it placed on the next epic
(*"E003 should not consume `activity_tag` or the HRV tiers until it is resolved"*) is lifted **for
rows written from this release onward, and only for those**. The amendment resolved the *rule*;
nothing rewrote the *rows*. See *No backfill* below for the predicate that identifies the
pre-amendment window and what E003 must do about it.

**F004 is `released` with this entry.** The capture contract is settled and E003-ready. The feature
was held `in-progress` through sprint-003 and `approved` through the first sprint-004 review; both
holds are now lifted. Review verification: 888 tests passing with no failures or skips, the
feature's demo probe green against a real server at the released commit, all 50 acceptance criteria
MET, and the two live defects found during review — a non-finite rMSSD stored as a reading, and a
`nan` intensity field routing a non-capture — fixed and pinned. See *Fixed in review* below.

### Migration required

**`resting_hrv_profile_names` is now a required config field with no default of any kind.**
`AppConfig` sets `extra="forbid"` and validates on load, so **an `api.toml` written before today
fails startup** — a three-key file (`host`, `port`, `data_dir`) will not start the server. The
absence of a default is deliberate: an athlete who uses only Health Snapshot declares that with an
empty list rather than inheriting it.

Two ways to upgrade an existing install:

1. **Add one line to `api.toml`.** If a dedicated watch profile records your captures, name it
   exactly as it appears on the watch (matching is exact and case-sensitive):

   ```toml
   resting_hrv_profile_names = ["HRV Snapshot"]
   ```

   If no activity profile of yours means a resting-HRV capture, say so explicitly — this is a
   declaration, not a default, and Health Snapshot still routes on Tier 2 without it:

   ```toml
   resting_hrv_profile_names = []
   ```

2. **Or set the environment variable**, which overrides the file. Its value is parsed as **JSON**,
   so the brackets and quotes are required:

   ```
   RUNCOACH_RESTING_HRV_PROFILE_NAMES='["HRV Snapshot"]'
   RUNCOACH_RESTING_HRV_PROFILE_NAMES='[]'
   ```

   A bare unquoted value (`RUNCOACH_RESTING_HRV_PROFILE_NAMES=HRV Snapshot`) does **not** parse, and
   today it escapes `serve()` as a raw `pydantic_settings.SettingsError` traceback rather than a
   clean message — tracked as **IDEA-016**.

**`runcoach-api init` now writes the key** (`[]` when `--resting-hrv-profile` is not given; repeat
the flag once per profile name). That is the fresh-install path, not an upgrade path: `init` refuses
to overwrite an existing config without `--force`, and `--force` also rewrites `host`, `port` and
`data_dir`. For an existing install, edit the file.

`runcoach-api serve` names the missing field and prints the literal to paste, so the validation
error *is* the upgrade instruction — without it pydantic reports the bare "Field required", which
names nothing to write.

**Editing `api.toml` requires a server restart.** `db._load_config_cached` is
`@lru_cache(maxsize=1)`, so the config is read once per process.

**No backfill.** Pre-amendment `resting_hrv_check` rows keep `resting_rmssd_ms` null — the computed
value for those rows lives only in `context.provenance.computed_resting_rmssd_ms`. Recovery is
re-ingestion under the declaration rule (see `DELETE /sessions/{id}` below).

**The pre-amendment window, and the query that identifies it.** Nothing rewrote those rows, so an
upgraded database carries readings produced by the inference predicate this amendment exists to
discredit, sitting beside readings produced by the declaration rule with nothing in the data to
tell them apart — except this:

```sql
SELECT * FROM sessions
 WHERE hrv_source_tier IS NOT NULL
   AND resting_rmssd_ms IS NULL     -- no post-amendment writer ran on this row
```

**E003 must exclude this window from the readiness read rather than inherit it.** Every row it
returns predates the amendment: its `hrv_source_tier` is an inference-era verdict, and on Tier 1 its
`activity_tag` is too. They are not remediable — `mapping.py` has never persisted
`sport_profile_name`, so no stored row carries the field the declaration rule needs — so exclusion
is the disposition, not repair. The window empties only as the athlete re-ingests the original
files.

**Training load is the other consumer, and its disposition is the opposite one (2026-09-07).** The
window is a verdict about *HRV provenance*, not about whether the session happened. A cool-down walk
mis-tagged `resting_hrv_check` under the old inference rule is still a real activity, and excluding
it everywhere would leave it permanently absent from rTSS and the PMC — a silent, unbounded loss of
training history, and the second of the two consequences this window creates. So **rTSS must ignore
`activity_tag` on window rows and count them as ordinary sessions**, while readiness excludes them.

The accepted cost is the mirror case: a genuine two-minute resting capture inside the window is also
counted, contributing a small spurious rTSS. That is bounded by its own duration — a 2-minute
non-session is worth very little load — whereas the excluded walk is not bounded at all. The
asymmetry is why the two consumers get different rules rather than one convenient rule.

Outside the window this does not arise: post-amendment `activity_tag` is a *declaration*, so rTSS
can and should trust it and skip declared captures.

**Two terms, and the second one is the whole predicate.** It was confirmed in both directions
against a constructed database rather than taken on faith, and the negative direction is the one
that mattered: *every* post-amendment way a recognised capture can end with no reading — both
declaration routes, all three quality gates, a computed rMSSD of zero, no derivable pair, and each
Tier-2 device-value failure — was driven through the real classifier and persisted, and **none of
them is selected.** Both tiers write `hrv_source_tier` and `resting_rmssd_ms` at the same single
success point past every gate, so no post-amendment row can hold one without the other. No third
term is needed, and each candidate for one was checked and refused. `activity_tag IS NOT NULL`
excludes nothing the tier term does not already exclude — a gated post-amendment capture *is*
tagged — so it would only make the predicate look as though it discriminated on something it does
not. `quality_flags = '[]'` is worse than redundant: `quality_gates.apply` runs *after*
`classify()` and appends `smart_recording`, `gps_degraded` and `cadence_lock` to any session, so a
perfectly good pre-amendment reading recorded under smart recording would drop out of the window
and its inference-era verdict would flow into E003 unexcluded. A bound on
`json_extract(context, '$.ingested_at')` does work, but it needs a per-install release timestamp
and reads a JSON blob rather than a column; the tier/reading pair separates the two eras
structurally and needs no constant. Pinned in both directions by
`test_the_published_window_predicate_selects_the_window_and_nothing_else`.

**It covers both fields the sprint-003 hold named.** The hold named `activity_tag` *and* the HRV
tiers, and the predicate keys only on the tier — which is sufficient here because the pre-amendment
Tier-1 branch wrote `activity_tag = 'resting_hrv_check'` and `hrv_source_tier = 'chest_strap_raw'`
on adjacent lines at one success point (verified against `9465ded`), so no stored row carries the
inference-era tag without the tier. A pre-amendment capture that *failed* a gate was never tagged
at all.

**What the predicate returns, and what it does not.** It returns the pre-amendment **reading** rows
of *both* tiers, because there is no backfill on either: a pre-amendment Tier-2 reading carries
`hrv_source_tier = 'health_snapshot'` and a null `resting_rmssd_ms` exactly as a Tier-1 one carries
`'chest_strap_raw'` and a null. So both `activity_tag` values appear among its rows —
`resting_hrv_check` on Tier 1 and `health_snapshot` on Tier 2 — which is why the paragraph above
says the `hrv_source_tier` is an inference-era verdict *and, on Tier 1, so is the `activity_tag`*:
the Tier-2 tag came from the numeric `sport == 60` identity the amendment never touched, and is
still trustworthy on a row that is nonetheless inside the window. What the predicate does **not**
return is any **post-amendment** row, of either tier, reading or not. *(Corrected 2026-09-07, code
review iteration 3, finding S2: this previously read "the one `activity_tag` this predicate does not
return is `health_snapshot`", which contradicted the paragraph above it and would have told a
consumer that pre-amendment Tier-2 readings sit outside the window and are safe to `ln()`. They are
inside it, and their `resting_rmssd_ms` is null.)*

### Changed

- **Tier 1 no longer infers intent — it requires a declaration.** A resting-HRV route now needs
  either the file's `sport_profile_name` to appear in `resting_hrv_profile_names`, or an
  upload-time override (`resting_capture=true` on the `POST /sessions` form;
  `runcoach ingest --resting-capture`). The two are a disjunction: an override routes even when the
  configured list is empty.
- **The old duration/speed/heart-rate predicate is demoted to a veto set.** The 300 s ceiling, the
  1.0 m/s mean-speed bound and the mandatory `avg_heart_rate <= 100` keep their exact thresholds and
  citations, and keep refusing files — but none of them can *authorise* a route any more. The
  multi-session refusal, Tier 2's `sport == 60` identity and all five quality gates are unchanged.
- **A fresh install therefore derives no Tier-1 readings at all until a profile name is configured**
  (or an upload declares itself). This is the amendment's most surprising consequence, and it is
  intended: before today, Tier 1 routed with no configuration whatsoever.
- **Measured effect across the whole ten-file fixture corpus: exactly two outcomes changed, both in
  the undeclared column.** `strap_hrv_capture.fit` and `strap_hrv_sample_run.fit` no longer route
  Tier 1 without a declaration. The second is the false positive the amendment exists to kill — an
  ordinary run recorded on the `"Run"` profile was being stored as a resting-HRV capture and
  excluded from training load. **Every declared outcome, and every Tier-2 outcome, is unchanged, and
  no quality flag appears or disappears on any fixture.**

### Added

- **`resting_rmssd_ms`** — the resolved session column E003 reads, written on **both** tiers: the
  device value on Tier 2, the system-computed value on Tier 1. `rmssd_precomputed` keeps its
  narrower meaning ("what the device supplied") and stays null on Tier 1. A computed rMSSD of zero
  is gated to `hrv_reading_unavailable` rather than stored as a reading. **The guarantee is scoped
  to rows written from this release onward:** for such a row, a non-null `hrv_source_tier` implies a
  non-null, strictly positive `resting_rmssd_ms`. It does **not** hold across the upgrade — see
  *No backfill* above, which leaves pre-amendment rows with a non-null tier and a null column, so a
  consumer must guard that read or exclude the pre-amendment window rather than assume the column is
  always populated wherever a tier is set.
- **A provenance note for every file examined and refused**, on both sides of the declaration:
  `hrv_undeclared_capture_candidate` when the profile was not configured, and
  `hrv_declared_capture_vetoed` when a configured name was contradicted by a veto. **The note names
  which veto fired**, so a refusal is diagnosable rather than silent. An unhonoured upload-time
  override records its own reason under `hrv_resting_capture_override`.
- **`activity_tag` on a recognised-but-flagged capture**, so a declared capture that yielded no
  number is still excluded from rTSS and the PMC instead of being counted as training load.
- **`DELETE /sessions/{id}`** (204), the recovery path for a mis-ingested capture.
- **`ruff` declared as a dev dependency**, with `line-length = 110` and the default rule set — so
  lint can be run at all. Its first real run reports a 67-violation backlog across 55 of 77 files,
  filed as **IDEA-017** and not fixed here.

### Configure first, then capture again — the undeclared note is prospective

`UNIQUE (source_device, start_time)` makes a re-upload of the same file a **409**, and adding a name
to `resting_hrv_profile_names` **reclassifies nothing already stored**. Acting on an
`hrv_undeclared_capture_candidate` note therefore means *configure the profile, then record a new
capture* — **not** *re-upload this file*. If you do want an already-stored file reclassified, the
sequence is `DELETE /sessions/{id}` and then re-ingest under the new configuration.

### Recommended workflow: a dedicated activity profile

Create a **custom activity profile on the watch** for resting captures (e.g. `"HRV Snapshot"`) and
list that name. It is the only configuration that yields a declaration and raw beats together:
Garmin's built-in Health Snapshot never emits `hrv` messages — `strap_health_snapshot_hrv.fit` was
recorded with `Log HRV` on and carries zero — so a Health Snapshot can only ever reach Tier 2's
device-computed value, which it already does on its numeric `sport == 60` identity without being
listed at all.

**Never list a general-purpose profile such as `"Run"`.** Doing so restores exactly the behaviour
IDEA-010 exists to kill, and it was reproduced against a running server while writing this note:
with `resting_hrv_profile_names = ["Run"]`, `strap_hrv_sample_run.fit` — an ordinary run — is stored
as `activity_tag: resting_hrv_check`, `hrv_source_tier: chest_strap_raw`,
`resting_rmssd_ms: 36.87`, and drops out of training load.

### Also in this release

- Six contract questions raised by the pre-implementation declaration-contract table were ratified
  as **R1–R6** in `spec/references/F004-contract-resolutions.md` — three derived from the feature
  file as the authority, three decided by the athlete on 2026-09-06 — and implemented rather than
  re-litigated task by task. **R4 carries an explicit revisit condition**: if a future fixture shows
  a file carrying `hrv` messages *and* a usable `rmssd_hrv`, its reasoning fails and it must be
  reopened.
- **This is not a clean sweep.** Ten of that table's seventeen findings were deferred rather than
  resolved and go to review for triage; F10 — the reference document's §2 still reads as though the
  demoted predicate authorises a route — was the most likely to mislead a future reader and was
  filed as **IDEA-019**. *That triage has since happened:* **F10 and IDEA-019 are resolved** (see
  *Fixed in review* below); F5, F7, F9 and F11–F16 remain deferred. Also open from this sprint:
  **IDEA-015**, **IDEA-016**, **IDEA-017**, **IDEA-018**, **IDEA-020**, **IDEA-021**, **IDEA-022**
  and **IDEA-023**.

### Fixed in review

- **A non-finite device rMSSD is no longer stored as a reading.** `inf` and `nan` are `float`
  instances, so they passed the "is this a number?" screen and then defeated the *comparison* the
  Tier-2 gate is made of — `inf <= 0` and `nan <= 0` are both `False`. A Health Snapshot whose
  `rmssd_hrv` was non-finite was therefore stored as a full reading with **no quality flag**: `inf`
  went into `resting_rmssd_ms` literally and would poison E003's `ln(rMSSD)` trend and its
  ±0.5·CV SWC band silently, and `nan` was stored by SQLite as `NULL`, manufacturing a row with a
  non-null `hrv_source_tier` and a null `resting_rmssd_ms` — exactly the shape the pre-amendment
  window predicate selects, so a genuine post-amendment reading would have been misfiled as an
  inference-era row and excluded from the readiness read. Non-finite values are now rejected where
  every other unreadable value is, and land on the no-reading row with `hrv_reading_unavailable`
  like any other unusable device value.
- **A `nan` intensity field no longer routes a file as a resting-HRV capture.** The same screen
  closed a second, sharper hole on the *veto set*. `_intensity_signal` normalises
  `total_timer_time`, `total_distance` and `avg_heart_rate` through the same helper, and every
  comparison against a `nan` is `False` — so a `nan` silently defeated **the one arm it appeared
  in**, and the file's remaining arms were innocently satisfied. One corrupt field is therefore
  enough: a `nan` `total_distance` left the stillness ratio declining while a genuine 58 bpm
  cleared the ceiling, and a `nan` `avg_heart_rate` left the ceiling declining while a genuine
  100 m over 240 s was honestly still. Either way every veto passed and the file routed as a full
  `chest_strap_raw` reading with `activity_tag = resting_hrv_check` and an **empty**
  `quality_flags`. That is this feature's headline failure mode — a non-capture routing — reached
  through the one arm that exists to tell rest from a stationary maximal effort. Non-finite values
  now veto under the reading convention's *structurally impossible* class, alongside a negative
  value on an unsigned field.
- **The same hole existed on Tier 1 and is closed with it.** `_numeric` is not on that path: a
  Tier-1 value comes from `rmssd.resting_rmssd` over the beat stream, and its gate is the identical
  `<= 0` comparison, so `inf` and `nan` cleared it there too. One non-finite `rr_ms` propagates
  through the sum and poisons the whole capture rather than just its own pair, and the artefact
  filter does not catch it: `rr_reconstruction._out_of_band` flags an `inf` but returns `False` for
  a `nan`, because every comparison against a NaN is false, so a NaN beat was never judged an
  artefact upstream. A non-finite beat is now **non-contributing**, exactly as a null beat already
  was, so the surrounding beats still yield a reading instead of the capture being discarded for
  one bad value. Together these two restore the guarantee this release publishes for
  `resting_rmssd_ms` — **always > 0 when set** — on *both* tiers; until now it held on neither.
- **Normative-text corrections**, each of which contradicted a sibling passage rather than the
  code: the window predicate returns pre-amendment reading rows of **both** tiers (the paragraph
  above previously said it never returns `health_snapshot`, contradicting itself 30 lines earlier);
  F004's Data Model and the pipeline-wiring pin scoped their remaining unqualified restatements of
  the `resting_rmssd_ms` invariant; the feature file's "there is no delete or reclassify route"
  clause corrected to name `DELETE /sessions/{id}`, which this same sprint shipped (**IDEA-025**
  closed); `F004-detection-and-quality-rules.md` §2 restated with the declaration as the routing
  term and the 2026-09-05 conditions as the veto set, with a two-axis contract table (**IDEA-019**
  and Finding 10 closed); and §2's claim that the zero-`avg_heart_rate` case is unhandled corrected
  — it is closed by the stated `session.summary` convention in the same document.

## 2026-09-06 — Sprint 003: Resting-HRV Capture

### Added
- **F004 — Resting-HRV Capture** *(functionally complete; see Known limitation below before building on it)*: a resting-HRV reading is now recognised as a **reading** rather than logged as a training session, with its rMSSD stored alongside the tier it came from. Two tiers, ordered highest-fidelity-first per §2.4.5:
  - **Tier 1 `chest_strap_raw`** — a chest-strap resting capture carrying beat-to-beat `hrv` messages. The rMSSD is computed by the system using **strict pairwise adjacency over the artefact-flagged series**: a successive difference contributes only when neither of its two beats is flagged. Excising flagged beats and differencing the compacted list is a *different and wrong* statistic — it manufactures a difference between beats that were never adjacent (74.98 ms correct vs 76.43 ms wrong on the reference series). `rmssd_precomputed` stays null, because the device supplied no value.
  - **Tier 2 `health_snapshot`** — a Garmin Health Snapshot's device-computed `rmssd_hrv`. Requires **both** a capability signal (`rmssd_hrv` present) and an identity signal (`raw_sport_value == 60`); capability alone never routes, so firmware attaching an rMSSD to a long run cannot feed in-run wrist PPG into the readiness trend.
  - Tier 1 outranks Tier 2 on a capture carrying both, and the unused device value is preserved in provenance rather than discarded.
  - **Quality gates**: a capture under 120 s, below 0.80 surviving beats, with no beat stream, or yielding no derivable statistic is stored with the appropriate flag (`hrv_capture_too_short`, `hrv_capture_low_quality`, `hrv_capture_no_beats`, `hrv_reading_unavailable`) and no reading — never a `0`/`-1` sentinel.
  - **`activity_tag`** is written on both tiers, which is what lets E003 exclude two-minute non-sessions from rTSS and the PMC.
  - A **quarantined vendor field can never become an HRV input** — enforced as a checkable negative invariant that reconciles the classification module's actual field reads (recovered from its AST) against the quarantine registry, so a future read must be declared or the suite fails.

### Changed
- **FIT ingest is now bounded by three ceilings, all enforced during decode** rather than after the message list is materialised: 100,000 `record` messages, 500,000 data messages of any name, and 150,000 RR beats. The previous record-only bound left the `hrv` carrier — the one this feature consumes — unbounded: a ~200 KB upload of packed `hrv` messages passed every guard and drove an O(n²) burst detector for roughly 42 minutes. The 413 response now names which ceiling overflowed. The beat ceiling is a volume backstop sized so no plausible activity is refused (~18 hours at the corpus's 2.3 beats/sec), not a CPU budget.
- **`runcoach-api --help` prints usage and exits** instead of silently starting a server; `runcoach-api foo` exits 2. Dispatch is via argparse subparsers, with `init`'s own flags forwarded verbatim.
- **`runcoach-cli` test helpers consolidated** into a package `conftest.py`, removing three copies of `_install_mock_client` and four of `isolated_config_path`.
- Property-based test suites added for RR burst detection, the recording-interval classifier, and `derive_session_id` — each demonstrated to fail against deliberately broken implementations rather than merely passing.

### Known limitation *(superseded 2026-09-06 by Sprint 004 — kept for the record)*

> **Both limitations below are resolved by the Sprint 004 declaration amendment at the top of this
> file.** IDEA-010 is closed by requiring an explicit declaration; IDEA-007 by the `resting_rmssd_ms`
> column. **The E003 hold stated here is lifted for rows written from Sprint 004 onward, and stands
> for every row written before it.** The amendment resolved the rule; it rewrote no rows, so a
> session stored under the old predicate still carries the `activity_tag` and `hrv_source_tier` that
> predicate produced. On those rows the hold's own words continue to apply verbatim: **E003 must not
> consume `activity_tag` or the HRV tiers.** They are identified by the *No backfill* predicate at
> the top of this file — `hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL` — and E003's
> **readiness read** must exclude them explicitly. **Training load takes the opposite disposition**
> (Decision Log, 2026-09-07): rTSS and the PMC ignore `activity_tag` on these rows and count them as
> ordinary sessions, because the window is a verdict about HRV *provenance*, not about whether the
> session happened. Nothing remediates them: re-adjudication is impossible (no stored row
> carries `sport_profile_name`), and the corpus-rebuild decision stands, so the window empties only
> by re-ingestion. The text is left standing because it is the argument for *why* the amendment
> exists, because the migration note above is only legible against it, and now because the hold it
> states is still live for part of the table.

- **F004's Tier-1 discriminator cannot yet distinguish a deliberate resting capture from any short, easy activity.** The predicate asks whether a file is short and low-intensity; it does not ask whether the athlete *intended* it as a measurement. A separately-recorded cool-down walk, an aborted run, or a stretching block is therefore recorded as a resting-HRV reading with an rMSSD computed from a standing beat stream — and is simultaneously excluded from training load. Both failures are silent. This is a gap in the specification rather than a defect in the code, tracked as **IDEA-010**, and F004 deliberately remains `in-progress`: **E003 should not consume `activity_tag` or the HRV tiers until it is resolved.**
- The system-computed Tier-1 rMSSD has no session column and lives in `context.provenance` (**IDEA-007**). E003's natural query against `rmssd_precomputed` returns null for every Tier-1 capture, so a column is needed before the readiness trend is built.

## 2026-09-04 — Sprint 002: FIT File Ingestion

### Added
- **F003 — FIT File Ingestion**: parses a Garmin FIT file into a trustworthy, vendor-neutral canonical record — flagging quality issues (RR artefact bursts, GPS/altitude degradation, smart-recording gaps, wrist-PPG HR gating) instead of hiding them, and keeping vendor black-box inferences (VO2max, Training Status, Body Battery, etc.) quarantined out of the decision path. Rejects oversized, corrupt, non-FIT, and duplicate uploads. Unblocks E003–E010 (derived metrics through loop closure), which build on this canonical output.

### Changed
- **F003 / T032 — `session_id` is now deterministic**: derived as a truncated SHA-256 over `(source_device, start_time)` — the same tuple the `UNIQUE (source_device, start_time)` constraint dedups on — instead of a random UUID. Re-ingesting the same activity into a rebuilt database now reproduces the same `session_id` (spec §2.2.1, "stable per activity"). The DB-level UNIQUE constraint is unchanged and remains the concurrent-upload race guard.

### Migration required
- **A database created before this change holds random-UUID `session_id`s.** Those rows still read back correctly by their stored id, but they will never match the id a re-ingest of the same FIT file now derives, so they cannot be re-identified from the corpus. **Rebuild the database from the FIT corpus** (delete `<data_dir>/runcoach.db` and re-ingest) to move existing sessions onto derived ids. No automatic conversion is provided: the pre-change ids are not recoverable from anything but the row itself, and no downstream consumer (E009 decision log, E010 state model) exists yet to be broken by the rebuild. See F003's Decision Log for the rationale.

## 2026-09-01 — Sprint 001: Delivery Architecture Scaffold

### Added
- **F001 — Backend API Scaffold**: FastAPI backend (`runcoach-api`) with a `GET /health` endpoint, explicit TOML configuration (no silent defaults), a `runcoach-api init` command, and clean non-zero-exit error handling for missing/invalid/corrupt config and port-already-in-use conditions.
- **F002 — CLI Scaffold**: Typer CLI (`runcoach-cli`) with `runcoach init`/`runcoach status`, talking to the backend API over HTTP, with defensive parsing of the API's response and clean error handling for an unreachable API, non-2xx responses, and malformed response bodies.

Both packages live as a `uv` workspace (`runcoach-api/`, `runcoach-cli/`), single-athlete/local-first, no auth. This establishes the delivery architecture every future domain feature (data ingestion, derived metrics, plan generation, adaptation, conversational interface) builds on top of.
