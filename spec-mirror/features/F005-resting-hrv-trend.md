---
id: "F005"
title: "Resting-HRV Trend"
type: feature
epic: "E003"
status: done
story_points: 9
complexity: "medium"
token_estimate: 26000
rice_reach: 1
rice_impact: 3
rice_confidence: 0.85
rice_effort: 2.8
rice_score: 0.91
feasibility: 0.9
dependencies: ["F004"]
references: ["spec/references/F005-trend-construction.md", "spec/references/F005-decision-log.md"]
children: []
tasks: ["T078", "T079", "T080", "T081", "T082", "T083", "T084", "T085", "T086", "T087", "T088", "T089", "T090", "T091", "T092", "T093", "T094", "T095", "T096", "T097", "T098", "T099", "T100", "T101", "T102", "T103", "T104", "T105", "T106", "T107", "T108", "T109", "T110", "T111", "T112", "T113", "T114", "T115", "T116", "T117", "T118", "T119", "T120", "T121", "T122", "T123", "T124", "T125", "T126", "T127", "T128", "T129", "T130", "T131", "T132", "T133", "T134", "T135", "T136", "T137", "T138", "T139", "T140", "T141", "T142", "T143", "T144", "T145", "T146", "T147", "T148"]
created: 2026-09-07
updated: 2026-09-18
review_cycles: 9
last_review: "2026-09-16 — cycle 9 at 2f3ad06: Stage 0 CLEAN at iteration 2 ((4,3) -> (0,0)), full suite 1386 passed / 0 failed / 0 skipped, walk-verdict checker 3/3, no contract drift. Four gaps found and all four fixed: T132 (the withhold could not see a brand-new device — the SIXTH §1.7-forbidden population in this rule, and a behaviour change), T133 (the ~12-day figure T126 withdrew survived in research/00 and spec/03 — a precedence inversion), T134 (check_walk_verdicts.py was wired into nothing; two assertions stayed green through the failure they prevent). Spec review, goal verification, gap analysis, critic and demo probe NOT yet re-run at this head — the verdict is not final. Open by decision: T124 (the two normative documents behind the gitignored symlink), IDEA-071 (its own sprint after F005 ships, now seven data points), and T130's carrier-overlap residual, which ships named."
demo_probe: |
  set -e
  PORT=8130
  export RUNCOACH_DATA_DIR=$(mktemp -d)
  export RUNCOACH_RESTING_HRV_PROFILE_NAMES='["HRV Snapshot"]'
  export RUNCOACH_ATHLETE_TIMEZONE='Pacific/Auckland'
  trap "rm -rf $RUNCOACH_DATA_DIR" EXIT
  uv run --package runcoach-api uvicorn runcoach_api.main:app --host 127.0.0.1 --port $PORT --app-dir runcoach-api/src &
  PID=$!
  trap "kill $PID 2>/dev/null; rm -rf $RUNCOACH_DATA_DIR" EXIT
  for i in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$PORT/health" -o /dev/null 2>/dev/null && break
    sleep 0.5
  done
  # A real upload through the real classifier, so the probe exercises the
  # resolved-column seam this feature is built on rather than bypassing it.
  curl -fsS -X POST "http://127.0.0.1:$PORT/sessions" \
    -F "file=@runcoach-api/tests/fixtures/strap_hrv_capture.fit" >/dev/null
  # The 60-day baseline cannot come from a 4-file fixture corpus, so the rest of
  # the series is seeded through mapping -> classify -> db.persist (NOT raw SQL),
  # from a fixed generator whose expected band is stated in the script, computed
  # by hand and not read back from the endpoint (IDEA-057).
  # --package: the workspace root is `package = false`; a bare `uv run` there is the T056
  # zero-collection shape. --end pins the series to the fixture's Auckland date (its start_time
  # decodes to 2026-09-06T17:48:06+00:00 = 2026-09-07 local) so the probe is calendar-independent.
  # The history is five eras (T096, review cycle 3: the gates could not see the tier rule), so the
  # feature-level gate covers the discriminator the feature is built on:
  #   eras[0]  daily health_snapshot 2026-01-01..03-22, the last week suppressed
  #   eras[1]  a 14-day chest_strap_raw trial 02-16..03-01 inside it, at 07:00 so the two tiers'
  #            captures on one morning do not share a session_id -- the G10 shape: a candidate by
  #            count that covers no judged week
  #   eras[2]  daily health_snapshot 03-23..06-30 -- the old era, cleanly ended
  #   eras[3]  daily chest_strap_raw 07-01..09-07, the last five suppressed -- the genuine switch,
  #            and the baseline the verdict on `to` is judged against
  #   eras[4]  one health_snapshot capture on 08-10 (07:00, beside that morning's strap) -- an isolated
  #            old-tier capture after the switch
  uv run --package runcoach-api python runcoach-api/tests/support/seed_hrv_series.py \
    --data-dir "$RUNCOACH_DATA_DIR" --print-expected \
    --era health_snapshot:2026-03-22:81:7 --era chest_strap_raw:2026-03-01:14:0:7 \
    --era health_snapshot:2026-06-30:100 --era chest_strap_raw:2026-09-07:69:5 \
    --era health_snapshot:2026-08-10:1:0:7 > /tmp/expected.json
  # The contract's path. `to` is pinned to the fixture's local date; `from` defaults to `to`.
  BODY=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-09-07")
  # 1. The verdict, against an independently computed band (the strap era's, eras[3]).
  echo "$BODY" | jq -e --slurpfile e /tmp/expected.json '
    .verdict == "hrv_suppressed"
    and (.band.lo - $e[0].eras[3].band_lo | fabs) < 0.001
    and .baseline.established == true and .band.floored == false'
  # 2. The seam: the real upload landed as a reading via the resolved column.
  echo "$BODY" | jq -e '[.included[] | select(.tier == "chest_strap_raw")] | length >= 1'
  # 3. Explainability: every exclusion names a reason.
  echo "$BODY" | jq -e '([.excluded[] | select(.reason == null)] | length) == 0'
  # 4. The contract's series: one point per local day in [from, to], and the
  #    last point's band equals the verdict's band.
  echo "$BODY" | jq -e '(.points | length) == 1 and .points[-1].swc_low == .band.lo'
  RANGE=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?from=$(echo "$BODY" | jq -r '.window[0]')&to=$(echo "$BODY" | jq -r '.date')")
  echo "$RANGE" | jq -e '(.points | length) == 7 and .verdict == "hrv_suppressed"'
  # 5. The tier rule on `to`: baseline.tier, established and n as the seed states them, and the
  #    switch still reported on the era's true first day with one isolated old-tier capture after
  #    it (T095's density tolerance; the stray is listed off-tier, not read as the old era's last).
  echo "$BODY" | jq -e --slurpfile e /tmp/expected.json '
    .baseline.tier == $e[0].eras[3].tier and .baseline.tier == "chest_strap_raw"
    and .baseline.n == $e[0].eras[3].n and .baseline.n == 60 and .baseline.established == true
    and .baseline.reset_reason == "tier_change" and .baseline.reset_on == $e[0].eras[3].first_day
    and ([.excluded[] | select(.date == "2026-08-10" and .reason == "off_baseline_tier: health_snapshot")] | length) == 1'
  # 6. The G10 shape: on 03-22 the strap trial holds 14 days of [D-66, D-7] and none of the judged
  #    week, so the snapshot keeps the baseline, its suppression is still emitted against the
  #    snapshot era's own band, no reset is reported, and the 14 trial captures are listed off-tier.
  G10=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-03-22")
  echo "$G10" | jq -e --slurpfile e /tmp/expected.json '
    .baseline.tier == "health_snapshot" and .baseline.established == true and .baseline.n == 60
    and .baseline.reset_reason == null and .verdict == "hrv_suppressed"
    and (.band.lo - $e[0].eras[0].band_lo | fabs) < 0.001
    and ([.excluded[] | select(.reason == "off_baseline_tier: chest_strap_raw")] | length) == 14'
  # 7. The genuine switch, clean: first reported on the strap era's 21st day (14 strap days in
  #    [D-66, D-7]), naming the era's first day, on a clipped, just-established window; the day
  #    before, the snapshot still owns the baseline with no reset.
  SWITCH=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-07-21")
  echo "$SWITCH" | jq -e --slurpfile e /tmp/expected.json '
    .baseline.tier == "chest_strap_raw" and .baseline.reset_reason == "tier_change"
    and .baseline.reset_on == "2026-07-01" and .baseline.reset_on == $e[0].eras[3].first_day
    and .baseline.window == ["2026-07-01", "2026-07-14"] and .baseline.n == 14 and .baseline.established == true'
  curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-07-20" \
    | jq -e '.baseline.tier == "health_snapshot" and .baseline.reset_reason == null'
  # 8. points[] continuity across the flip: over 07-14..07-28 every day is judged on its own
  #    baseline; the tier changes exactly once, on 07-21, and no two adjacent days carry bands
  #    from different tiers without a reset on the later day; the range's points[] carries each
  #    day's own band.
  SCAN=$(for d in $(seq 14 28); do
    curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-07-$d" \
      | jq -c '{date, tier: .baseline.tier, reset: .baseline.reset_reason, lo: .band.lo}'
  done | jq -s .)
  echo "$SCAN" | jq -e '
    [range(1; length) as $i | select(.[$i].tier != .[$i-1].tier) | .[$i]] as $flips
    | (map(.tier) | unique) == ["chest_strap_raw", "health_snapshot"]
    and all($flips[]; .reset != null)
    and ($flips | map(.date)) == ["2026-07-21"] and $flips[0].reset == "tier_change"'
  RANGE=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?from=2026-07-14&to=2026-07-28")
  echo "$RANGE" | jq -e --argjson s "$SCAN" '(.points | length) == 15
    and ([range(0; 15) as $i | ((.points[$i].swc_low - $s[$i].lo) | fabs) < 1e-9] | all)'
  # 9. The as-built API matches the contract's implemented endpoints.
  uv run --package runcoach-api --with pyyaml python contracts/check_drift.py
---

# Resting-HRV Trend

## User Story

As the athlete, I want my morning resting-HRV captures turned into a **trend verdict** — is my
autonomic readiness normal, or suppressed relative to my own baseline — so that I can tell an
ordinary tired morning from genuine accumulated fatigue, and so the adaptation logic has a
trustworthy readiness input rather than yesterday's single number.

## Why This Matters

F004 made resting-HRV captures *trustworthy* — a reading exists only where the athlete declared the
capture, and `resting_rmssd_ms` is the resolved value across both tiers. But a single reading is not
actionable: HRV varies day to day for reasons unrelated to training. The constitution is explicit
that a single below-band morning is weak evidence and a coherent multi-day decline is what is
actionable (`research/00` §1.4; `research/04` §5.1).

This is the first consumer of the E003 hold that sprint-004 lifted, the first thing in the system
that answers a question the athlete actually asks in the morning, and the narrowest useful slice of
E003 — it needs neither the grade-adjusted-pace substrate (§3.3) nor the Section-4 anchors that
block §3.4–§3.6 (see [[IDEA-035]]).

## Scope

**In scope.** The ln-rMSSD series, the 7-day rolling mean, the SWC band, the verdict, per-source
baseline discipline, the coverage-gap reset, thin-data guards, one read endpoint that exposes the
verdict with everything that produced it **and the per-day series the UI contract asks for**
(`contracts/openapi.yaml`, `GET /metrics/hrv`, extended additively and flipped to `implemented`),
and the **domain-spec amendment** this feature's reading of §3.7 requires — swept tree-wide: §3.7
and §3.10 of spec/03, Section 2's trend-input sentences, spec/06's restatement, `spec_outline.md`,
`research/00` (as a stated clarification of its own register row), `research/05`, and the project
rule in `.claude/rules/project-domain-and-spec-fidelity.md`. The verified 21-site table is in the
construction reference.

**Out of scope.** Resting-HR corroboration ([[IDEA-036]]). Per-tier **confidence weights** — §3.7.4
mandates "at that tier's confidence" and this feature emits none; deferred with the sibling named in
the amendment. The readiness *fusion* is Section 6 / E007. No other E003 block is touched.

## Assumptions

**Single athlete.** `sessions.athlete_id` exists and is nullable, but nothing sets or filters on it
and there is no auth surface. The trend covers all sessions in the database and takes no athlete
parameter; `athlete_id` is *not* read. If multi-athlete lands, the endpoint gains an athlete scope
and the baseline becomes per-athlete — a known extension, not a discovered defect.

## Interface

`GET /metrics/hrv` — the path, operation (`getHrvTrend`) and `points[]` shape come from the UI↔engine
target contract (`contracts/openapi.yaml`, `x-owner-spec: spec/03 §3.7`); the verdict blocks are this
feature's additive extension of it. Query: optional `?from=YYYY-MM-DD&to=YYYY-MM-DD` (athlete-local
dates). `to` defaults to the athlete's local today; `from` defaults to `to`. `from` after `to` is a 422.
The contract's `from`/`to` are made optional in the same change; nothing else in it becomes stricter.

`points[]` carries one entry per local day in `[from, to]`, each judged against **its own** baseline
`[d-66, d-7]`: `ln_rmssd` (that day's reading, `null` when none), `baseline` (the baseline mean),
`swc_low`/`swc_high` (all three `null` when no band is asserted for that day). The `verdict` and the
blocks below describe `to`. A range longer than 366 days is a 422.

`timezone` is the IANA zone the response was bucketed in. `included[]` lists the readings that
contributed to the **7-day window mean** (baseline readings are summarised by `baseline.n`);
`excluded[]` lists every stored row inside `[to-66, to]` that contributed to neither, each with a
reason. `below_by` is `lo − mean` when the verdict is `hrv_suppressed`, else `null` — the "how far
below" the acceptance criteria require. `unavailable_reason` names **which** guard withheld the
verdict, and is `null` whenever the verdict is not `hrv_unavailable` — the same
reported-when-it-applies shape as `baseline.reset_reason`, which is `null` on a baseline that was
never re-established.

```json
{
  "date": "2026-09-08",
  "from": "2026-09-02",
  "timezone": "Pacific/Auckland",
  "points": [ { "date": "2026-09-02", "ln_rmssd": 3.80, "baseline": 3.824, "swc_low": 3.772, "swc_high": 3.877 } ],
  "verdict": "hrv_suppressed",
  "unavailable_reason": null,
  "ln_rmssd_7d_mean": 3.742,
  "below_by": 0.030,
  "band": { "mean": 3.824, "lo": 3.772, "hi": 3.877, "half_width": 0.0526, "floored": false },
  "baseline": {
    "window": ["2026-07-04", "2026-09-01"], "n": 41, "tier": "chest_strap_raw",
    "established": true, "reset_on": "2026-07-04", "reset_reason": "coverage_gap"
  },
  "window": ["2026-09-02", "2026-09-08"],
  "readings_in_window": 5,
  "included": [ { "date": "2026-09-08", "session_id": "…", "tier": "chest_strap_raw", "rmssd_ms": 41.2 } ],
  "excluded": [ { "date": "2026-09-06", "session_id": "…", "reason": "off_baseline_tier: health_snapshot" } ],
  "thresholds": { "baseline_days": 60, "min_baseline_readings": 14, "min_window_readings": 3,
                  "gap_reset_days": 21, "band_floor": 0.01, "swc_factor": 0.5 }
}
```

`verdict` ∈ `hrv_normal` | `hrv_suppressed` | `hrv_unavailable`, and `unavailable_reason` ∈
`no_tier_sustains_a_trend` | `no_band` | `week_too_thin` | `week_not_representative` |
`baseline_unestablished` | `day_not_happened` | `null` — a closed enum, non-`null` **exactly** when
`verdict` is `hrv_unavailable` (added 2026-09-18, review cycle 10, T144, stating the field
[[T137]] shipped; it was already published in `spec/03` and `contracts/openapi.yaml` and named in
the Negative Class here, but this section — F005's own statement of the shape, and the document
between those two in the authority order — did not carry it). The response carries inputs,
thresholds and exclusions because `research/00` §1.6 requires a derived verdict to be reproducible
by hand from what it reports. **Since T117 the promise is kept for the band, and only
half-kept for `baseline.tier` and for the `week_not_representative` reason** (amended 2026-09-16,
review cycle 8; re-scoped 2026-09-18, review cycle 10, T146 — the earlier wording also claimed the
promise kept for the verdict, which T125 made false, and named only the tier as the exception):
`thresholds` publishes the six constants the band was computed with, and every other verdict cause
is recomputable from the fields beside it — `band`, `baseline.n`, `readings_in_window`, `date`. Two
things are not. The tier rule applies `recency_tolerance_days`, which the response does not echo, so
a client can see that the reported `baseline.tier` disagrees with those six and cannot recompute the
choice. Since [[T125]] the same constant also decides `week_not_representative`: checking that
reason by hand means recomputing `_recency_struck`, which reads the same unpublished
`RECENCY_TOLERANCE_DAYS` = 28, so it is the one `unavailable_reason` value a client must take on
trust. Narrowing the published promise rather than publishing the constant was a user decision
([[IDEA-070]], 2026-09-15); the cost is priced in the Negative Class row below. **Read the two together — this paragraph alone overstates
what the response carries.**

**Contract discipline.** The operation is marked `x-readiness: implemented` in the same change set
that ships the route, and `contracts/check_drift.py` (path, method, 2xx codes, required query
params) passes against the as-built `/openapi.json`. Response-schema richness beyond the contract
is additive and permitted; a required parameter the as-built route does not accept is drift.
`bearerAuth` remains planned in the contract; this endpoint is unauthenticated like every as-built
route today.

## Configuration

| Field | Type | Default | Notes |
|---|---|---|---|
| `athlete_timezone` | IANA zone string | **none — required** | Day bucketing for the window, the gap and same-morning grouping. Follows F001's ratified no-defaults rule: a silent `"UTC"` would bucket a 06:00 capture at UTC+13 onto the previous day and shift which readings fall in the window, with no error. |

`tzdata` becomes a runtime dependency (Windows stdlib `zoneinfo` has no system tzdb). The zone is
validated at config load, never at request time.

Trend constants ship as stated heuristic defaults per Section 3's convention, as module constants
echoed in the response — not config fields. See the construction reference for their values and
rationale.

## Data Model

No schema change. Reads `sessions.start_time`, `resting_rmssd_ms`, `hrv_source_tier` — the rows of
`[to-126, to]` and, beside them, one `MIN(start_time)` scalar over the store's readings (the earliest
known reading, which tells a long layoff from a new athlete; T096). Writes
nothing. `resting_rmssd_ms` is the input, **never** `rmssd_precomputed` — which remains the
device-only audit record.

## Negative Class

F005 ships two discriminators — the three-way verdict and the **baseline-tier rule** — and
`.claude/rules/learnings/discriminators-must-name-their-negative-class.md` asks each to name what it
rejects, the population between accept and reject, the signal that separates it, and the cost of
each error direction with who notices. Added 2026-09-10 (sprint-005 review, T093), after the critic
found the population below sitting unnamed between the spec's two poles; amended 2026-09-11 (review
cycle 2, T094), after the critic found five more between the poles T093's rule named and one of
them — stale candidacy — was accepted as a named cost rather than fixed; amended again 2026-09-11
(review cycle 3), after the reset rule's clause (c), judged on the current window alone, was found
to fire on a finished trial that had aged into the previous window, and its own error direction
had no row here; amended 2026-09-12 (review cycle 3, T095), after the review found the count unit
split — captures for rules 1-3, days for the verdict, so one re-taken morning defeated the
week-coverage gate — and one isolated capture of either tier, on either side of a genuine switch,
silencing the switch's reset for the whole era; amended 2026-09-12 (review cycle 3, T096), after
the review found one resumption era reported `coverage_gap`, then `tier_change`, then nothing, with
no event between the three, and a layoff longer than the route's 126-day read reporting no gap at
all (fixed, not named: AC 19 stands as written); amended 2026-09-13 (review cycle 4, T098), after
the review found the baseline clip conditional on the reset being *reported*, so the tolerance's
week half moved the band and could flip an athlete-facing verdict in the under-calling direction
with no new data — the clip is now unconditional and the row below prices the report alone; amended 2026-09-13 (review cycle 5, T102), after the review found that T098 had moved only the tolerance's
*week* half off the band — its *candidacy* half decides whether a boundary exists at all, so the 14th stray
day un-clips the baseline and can flip the verdict; kept and priced (decision log D5), the row below now
prices the two halves apart; amended 2026-09-14 (review cycle 6, T107), after the review found that "the clip is unconditional" was false of any series carrying a **coverage gap** — `build_series` asked rule 4 only when no gap reset had fired, so a gap anywhere in `[D-66, D]` cancelled the era clip entirely and drew an abandoned device era back into the band. **Fixed, not priced** (user decision 2026-09-13): `research/00` §5.4 already says those readings are never in the band, so the code moved to the authority. This is a **behaviour change**; the gap keeps `reset_reason` / `reset_on` and nothing else. Amended again 2026-09-14 (review cycle 6, T110, G-C6-7), after the review found **two cells naming the wrong error direction**: the cleanly-ended-trial row called `hrv_normal` on the trial band "the direction §1.7 tolerates" and the stale-candidacy row called a false suppression "the direction §1.7 tolerates least", both inverted against `research/00` §1.7 and against `judge`'s own docstring. Both corrected, and the stale-candidacy row — which had priced only one direction, the free one — now names the forbidden one too and **its acceptance is re-opened for a user decision**, since the reasoning it was accepted on was the reversed one. Doc-only; no behaviour, test or contract change. Amended again 2026-09-15 (review cycle 7, T118, G-C7-3), after the review found that **T107's own fix made a new population reachable**: because `tier_change_reset` is handed a `baseline_readings` derived from the **gap-rebound** series, a coverage gap can now *create* an era boundary the full capture history dates earlier or refuses outright, and that boundary's `first_day` can fall after the resumption, clipping legitimate post-resumption days of the baseline tier out of the band. **Accepted and named, not fixed** (user decision 2026-09-15); the row below carries the mechanism, the dated measurements of its reachability, and why its error direction is **not** asserted here while T116 is about to move the rule that direction was measured against. Doc-and-test only; no behaviour, contract or logic change. Amended again 2026-09-15 (review cycle 7, T116, closing [[IDEA-062]]) — **a behaviour change**: the verdict's establishment gate was asymmetric. `judge` consulted `established` only inside the `window_mean < band.lo` arm, so a week inside or above a band built from 2 to 13 readings read `hrv_normal` while the same thinness withheld `hrv_suppressed` — up-regulation on weak evidence, the direction `research/00` §1.7 forbids, reachable after every reset this feature performs. `hrv_normal` now requires `established`, else `hrv_unavailable`; the two error directions of that change are priced in a new cost table under `### The verdict` below, and T118's row above is re-scoped to say the rule it measured against has now moved. Amended again 2026-09-15 (review cycle 7, T117, closing [[IDEA-064]]) — **a behaviour change**: the stale-candidacy row's acceptance, re-opened by T110 in cycle 6, is resolved **by change rather than re-acceptance** on a user decision taken that day. Rule 1's candidacy gate now carries a **relative recency condition with tolerance** — a candidate's latest baseline-window day must fall within `recency_tolerance_days` (28) of the most recent baseline-window day of any candidate — chosen after the parameter-free form was built, measured and found unbuildable (it turns five pinned tests red in both directions, because a daily lower-fidelity tier is always read at least as recently as a 2-3-day-a-week higher-fidelity one). The comparison is between candidates. `thresholds` gains no key and the contract shape is unchanged ([[IDEA-070]], 2026-09-15). The row below carries the new behaviour, the arithmetic of the rejected form, the dated band measurement [18, 44], the justification of 28 against `gap_reset_days`, and the error direction the change itself introduces with who pays for it.; amended 2026-09-16 (review cycle 8, T125), after the critic found — and the orchestrator independently reproduced — that the recency condition T117 added is measured over the baseline window while the verdict it decides is a claim about the judged week, so a returning device is judged on readings that predate its return: 36 of 724 return geometries read `hrv_normal` on a suppressed return, and neither the row above, the acceptance criterion nor the only fixture could see it, all three carrying the qualifier *"while a daily snapshot ran throughout"* — the sub-population where the cost is benign. The verdict subsection below now carries the population, the fix (withhold rather than judge on a stale week), its priced cost and the residual that no measured form closes — restated 2026-09-18 ([[T145]], review cycle 10) as the closed form `min(window_days − min_window_readings, k₃)` rather than a count, because it is **four** mornings, not two, whenever the return is captured **sub-daily**. Amended again 2026-09-16 (review cycle 8, T123, closing G-C8-1): the direction search G-C7-3's acceptance rested on was re-run against the shipped rule and **found the flip in both halves** -- 5 realized `hrv_suppressed -> hrv_normal` flips on an unchanged week mean in 30,000 randomized capture histories for the era-boundary half, and 54-90 of 2050 return geometries for the tier-substitution half once the carrier keeps recording through the return, which disarms T125's order clause on every row. The T118 row above and the verdict cost table below now carry both results; **both are open for a user decision, neither is pinned and neither is re-accepted**. Doc-only; no behaviour, test or contract change. Amended again 2026-09-16 (review cycle 8, T129, resolving G-C7-3) — **a behaviour change**: the era-boundary half is resolved **by change rather than re-acceptance**, on a user decision taken that day against T123's swept numbers, so the T118 paragraph above is the history of the rule and no longer describes it. Rule 4 counts its **strays** over the **unclipped** `[D-66, D]` of every tier together with the previous window; the gap clip still decides which readings enter the *band* and clause (a)'s candidacy count still reads the gap-clipped window. A coverage gap can therefore no longer *create* an era boundary the full capture history refuses, and the flip class the re-run found is **0 of 26,360** well-formed histories at the fix where the same harness found 18 immediately before. The tier-substitution half (T125's order clause, disarmed by a carrier that keeps recording through the return) is **untouched by this and remains open**. `research/00` §5.4 amended first; spec §3.7.3/§3.7.4, the construction reference, the Negative Class row and the decision log restated to match. Amended again 2026-09-17 (review cycle 9, [[T138]], G-C9-9), after the critic found — and the orchestrator reproduced independently — that the **20-day quiet every cost table here prices "after every reset" was measured after one of the two reset kinds**: [[T126]]'s pin is a coverage-gap walk asserting `reset_reason == ["coverage_gap"] * 21`, and is structurally incapable of producing the tier-change shape, in which nothing is reported at all until candidacy resolves. A clean, gapless, permanent switch costs **18** silent days with `established: true` and both reset fields `null` throughout, and its reset is reported **20 days late**. Both are now measured, derived from the constants and pinned; the reporting lag is **named and accepted** below for the first time, and the feature's four accumulated silences are **composed against a rate** for the first time. Doc-and-test only; **no behaviour, contract or logic change** — this is a cost the code has always had and nobody had computed.

### The tier rule

**What it rejects.** A tier is refused the baseline when (a) it was read on fewer than 14 distinct
local days in `[D-66, D-7]` — the single borrowed strap capture, the thin first weeks of a new
device — or (b) it was read on 14 or more days there but fewer than 3 days of the judged week
`[D-6, D]`, or (c) it was read on 14 or more days there, and covers the week, but its **latest**
day in `[D-66, D-7]` falls more than `recency_tolerance_days` (28) behind the latest such day of
any candidate — the July trial still sitting inside the window, struck before week coverage is
consulted (amended 2026-09-15, T117, closing [[IDEA-064]]; a **behaviour change**). The three are
a conjunction of conditions, so **this list is the whole negative class only with (c) in it**: a
July trial with 14 baseline days plus three strap days this week is refused by neither (a) nor
(b), and read without (c) this paragraph reinstates the stale-candidacy defect T117 fixed. Case
(b) is the earlier amendment: before it, (a) was the whole negative class, and every
counterexample anyone reasoned about was either "an occasional chest-strap capture" or "a
sustained switch". **The count unit is distinct local days**, everywhere — candidacy, week
coverage, rule 3's tie by count, rule 4's "sustains" and the tolerance below — the unit
`baseline.n`, `readings_in_window` and `established` already reported (T095; decision log
2026-09-12; [[IDEA-047]] closed). **A re-taken morning is worth exactly one day**: a second strap
capture on one morning neither covers a week nor adds to candidacy, and it is listed
`same_day_later_capture` on a strap baseline and `off_baseline_tier` on any other. Counted in
captures, one re-take turned a genuine `hrv_suppressed` on a 60-day snapshot baseline into
`hrv_unavailable` with `established: true` on 18 strap days — cell 1 of the cost table below,
which this subsection had presented as prevented — and 14 captures on 7 days emitted `hrv_normal`
on a baseline the same response reported unestablished (review cycle 3, G10; both pinned).

**The population in between.** Fourteen readings in 60 days is 1.6 a week; a judged week needs 3.
So a higher-fidelity tier can sustain a baseline *by count* while never sustaining a *week*, and
that population is larger than either pole:

- **Trial-then-abandon** — a chest strap used daily for two weeks and then never again. From the
  seventh day after the trial until its readings age out of the baseline window (47 days, for a
  two-week trial) the strap held ≥ 14 in the window and 0 in every week. Under the count-only rule
  it owned the baseline: `hrv_unavailable` with `established: true` on every one of those days, the
  60-reading snapshot series listed `off_baseline_tier`, a genuine two-week suppression on the
  snapshot never emitted, a `tier_change` reset reported daily and then withdrawn without an event,
  and `points[]` stepping between two tiers' bands on adjacent days over identical data.
- **Two days a week** — a daily-snapshot athlete who wears the strap on Tuesdays and Saturdays.
  Seventeen-odd strap readings in every baseline window, two in every week: `hrv_unavailable`
  permanently.
- **Oscillation** — the same athlete at two *or three* strap days a week. Three is enough to judge
  a week on the strap; two is not.
- **Stale candidacy** — the trial-then-abandon athlete who, seven weeks later, wears the strap on
  three days of one week. Until 2026-09-15 candidacy was measured over the whole 60-day window with
  no recency, so the July trial stayed a candidate until its first day aged out of `[D-66, D-7]`,
  and with the week now covered rule 2 handed it the baseline: two days judged on a band from July,
  then the snapshot again. Named below in T094, **not** fixed then (the candidacy question was
  [[IDEA-064]]); its acceptance was re-opened 2026-09-14 (T110) once the row named both error
  directions rather than one, and **closed by change on 2026-09-15** (T117): a candidate must now
  also have been read within `recency_tolerance_days` (28) of the most recently read candidate, so
  the strap — 45 days behind — is struck before week coverage is consulted. The separating signal
  for *this* population is therefore relative recency, not week coverage; the row below carries the
  measurement and the error direction the change itself introduces.

**The separating signal is week coverage, and the fallback is recency.** *Among the candidates,
and candidacy itself now carries a recency condition* (T117, 2026-09-15: a tier whose latest
`[D-66, D-7]` day falls more than `recency_tolerance_days` (28) behind the latest such day of any
candidate is struck from the candidate set **before** week coverage is asked, so "a tier that
sustains the baseline" below means a tier that sustains it *and was read recently enough*; the
paragraph read without that condition is the pre-T117 rule). A tier that sustains the
baseline owns it only if it also holds ≥ 3 readings in the judged week or no sustaining tier does;
in that case the sustaining tier **the athlete was read on last** takes the week — ties by count,
then fidelity; with no sustaining tier at all, the densest — so an illness week keeps the tier
stable and reads `hrv_unavailable`; it **begins no reset, and a reset already in force persists
through it** — the switched athlete's thin week keeps yesterday's `tier_change` on every empty day,
exactly as the @must below pins (corrected 2026-09-12, T095: "with no reset" was true only of
beginning one) (construction reference, "Baseline tier resolution"; amended 2026-09-11, T094: the densest fallback handed a switched athlete's first thin
week back to the device abandoned three weeks earlier, withdrawing yesterday's reset with it, and
asserted a reset on an empty week that both neighbouring weeks withdrew). **The reset's separating
signal is that the eras do not interleave**: `tier_change` is asserted only when the tier that now
sustains the window differs from the one the previous window sustained *and*, over both windows
`[D-126, D-7]` and the judged week together, the eras do not interleave beyond a **density
tolerance** for isolated captures — the readings on the wrong side of the era boundary, the new
tier's from the old era's first day up to its last reading and the old tier's after the new era's
first, are corroboration when together they are fewer than 14 distinct local days and fewer than 3
in the judged week, and only use dense enough to be a candidate or to cover a week continues or
begins an era (T095; decision log 2026-09-12: judged exactly, one capture of either tier on either
side of a genuine switch silenced its reset for the whole era; the same-instant switch-day tie
stays interleaved) — the old era ended before the new one began — and `reset_on` names that era's
true first day (construction reference, "Reset triggers"; T094; judged over both windows since
review cycle 3, 2026-09-11: on the current window alone the criterion was vacuously true for a three-week strap
trial that had aged wholly into the previous window, and a phantom `tier_change` was reported for
seven weeks to an athlete who never switched). This resolves the first two members cleanly — the daily snapshot judges them — the third
**deliberately not**, and the fourth **not at all**: the athlete at two-to-three strap days a week
sees the tier alternate `chest_strap_raw` / `health_snapshot` whenever the strap count in the
sliding judged week `[D-6, D]` crosses 3 — on the day a third strap reading enters or leaves the
week, not on a week boundary — with no reset in either direction, **because the eras interleave**:
the snapshot's readings run through the strap's, so neither era ends, from the habit's first
sustaining window on (T093's "changed and owns the baseline" said this only once the previous
window also sustained the strap, ~15 weeks in; a younger habit fired a phantom `tier_change` on
alternate weeks — review cycle 2, G6). That oscillation is the accepted cost of keeping a genuine
device switch at 14 days rather than ~30 (the "densest tier wins" alternative, rejected in the
decision log 2026-09-10). Both costs are pinned as tests, not hidden.

**Cost of each error direction, and who notices.**

| Error | What happens | Who notices |
|---|---|---|
| A tier owns a baseline it cannot judge (the pre-amendment failure) | `hrv_unavailable` with `established: true`; the other tier's real suppression is never emitted; a phantom `tier_change` | The athlete, in `baseline.tier` and `reset_reason` on a morning they took a snapshot as usual; the UI, as a band step in `points[]` between adjacent days; Section 6, which reads `hrv_unavailable` and withholds — a *silent* loss of the readiness input, the direction `research/00` §1.7 tolerates but nobody sees as an error unless they read the response |
| The week-covering tier is the lower-fidelity one while a higher tier sustains the window | The week is judged on the snapshot band although 14+ strap readings exist | Nobody, in the response — the strap readings are listed `off_baseline_tier`, which §3.7.3 makes correct; the cost is a wider band than the strap would give, not a wrong verdict |
| Oscillation | Tier, band and `included[]` alternate whenever the strap count in the sliding judged week crosses 3; each week is judged on the tier that covered it, against that tier's own baseline; **no reset in either direction, because the eras interleave** — the snapshot's readings run through the strap's, so neither era ends — from the habit's first sustaining window, not only once the previous window sustains the strap too (T094) | The athlete, in `baseline.tier`; the UI, as a periodic band step in `points[]` (a per-point `tier` is not yet in the contract — `CRITIC-F005.md`, priority 3) |
| A week-driven or interleaved tier reported as a reset | A snapshot baseline clipped at a strap day; `established` drops to false; on a young two-to-three-day habit the reset appeared on every strap week and vanished on the next; on a daily snapshot with a finished three-week strap trial, `tier_change on <the day after the trial>` for seven weeks — from the day the trial sat wholly inside `[D-126, D-67]` with 14+ readings until it dropped below 14 there — withdrawn without an event | Would be visible to all three; prevented by the **non-interleaved eras** rule — `tier_change` fires only when the resolved tier sustains the window, the previous window sustained another tier, and, over both windows, no reading of the new tier falls between the old tier's first and last beyond the **density tolerance** (T095, 2026-09-12): readings on the wrong side of the era boundary — the new tier's from the old era's first day up to its last reading and the old tier's after the new era's first — are corroboration, not interleaving, while together they are fewer than 14 distinct local days and fewer than 3 in the judged week `[D-6, D]`, so up to 13 isolated new-tier days may sit inside the old era and the reset is still reported; density enough to be a candidate or to cover the week is use, and the eras interleave (T094; T093's "changed and owns the baseline" let a young habit and a stale trial through; T094's clause (c), judged on the current window alone, let the finished trial through — review cycle 3, 2026-09-11) |
| The tolerance's own error direction: use of the other tier inside an era — a habit dense enough to be a candidate, or three captures in the judged week (T095) | It is use, so the eras interleave and no reset is reported: the young two-to-three-day strap habit (G6, intended); a stale trial of the *new* tier while it still holds 14 days in the previous window (G12: rule 4(b)'s stale candidate, the switch's reset waits until the trial ages below 14 there — 16 days for a 14-day trial 90 days before the switch; [[IDEA-064]]); and, because the week half is judged on the sliding `[D-6, D]` as rule 2's week coverage is, three old-tier captures in one week after a switch, which withdraw the reset for the days they sit in the judged week and hand it back once the week has slid past them. **Accepted and named**: the tolerance closed the three populations an exact clause silenced for the whole era — one new-tier capture before the switch (G9, the modal adoption pattern; it also stays the 14th distinct day, so the tier flips a day early with the reset explaining the step and `established: false` on the clipped era), one old-tier capture after it ([[IDEA-065]], closed), and an older trial of the new tier once it holds fewer than 14 days in the windows (G12) — at the price of reading dense use as use | **Priced in two halves** (amended 2026-09-13, review cycle 4, T098, decision log D4; re-priced 2026-09-13, review cycle 5, T102, decision log D5). *The week half decides the report — and, as the **first ordering term** in `_era_boundary`'s selection key, which of several admitted boundaries is taken, hence where the clip lands and where the band sits* (corrected 2026-09-13, review cycle 6, T106, following `research/00` §5.4; T104 corrected the same claim only at the three sites its diff had touched). Its cost is the athlete, in a null `reset_reason` on the days the other device was in use, and Section 6, which reads `established: true` on those days without being told the baseline is fresh; no *reported* band or verdict moves with it, but only because an isolated boundary is always a candidate, so wherever one existed it is still the one chosen and `reset_on` does not move (pinned on `_era_boundary` by `test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of`, where the week-clear boundary beats one with fewer stray days and `first_day` moves with it) — and because since T098 the baseline is clipped at the era boundary whenever one exists, reported or not, and the readings so clipped are listed `before_reset: tier_change`. Until T098 the two halves were one branch, so the week half — judged on the **sliding** `[D-6, D]` — un-clipped the baseline back to `[D-66, D-7]` and drew a device era abandoned seven weeks earlier back into the band, flipping `hrv_normal` ↔ `hrv_suppressed` with no new data (G-C4-1, HIGH: the conjunction of ≤13 new-tier stray days inside the old era and ≥3 old-tier days in the judged week, each priced alone above and the pair nowhere). *The candidacy half decides whether a boundary exists at all, and therefore the clip*, and there the threshold is a **cliff on the band** (G-C5-1, HIGH; D5: keep the rule, price it): the strays on the wrong side of a boundary — the new tier's inside the old era and the old tier's after the switch, **pooled into one count of distinct local days** — are corroboration below 14, so a boundary exists and the baseline is clipped at the switch; at 14 no boundary exists, nothing is clipped — not an unreported clip but none — and the new tier's pre-switch trial enters the band. Crossing it moves the band by a step and can flip the verdict **`hrv_suppressed` → `hrv_normal`** on an unchanged week mean — the under-calling direction `research/00` §1.7 tolerates least. Reproduced at HEAD on the suite's own `trial_then_switch` series (a daily snapshot, a 10-day strap trial seven weeks before a genuine switch, snapshot captures after it **outside** the judged week), target 2026-09-07: 13 stray days — `tier_change on 07-30`, window `(07-30, 08-31)`, n 33, `band.lo` 3.6789, `hrv_suppressed`; 14 — `reset_reason` null, window `(07-03, 08-31)`, n 43, `band.lo` 3.4791, `hrv_normal`; 7-day mean 3.4965 on both, a 0.20 ln step in the band, and n 43 / lo 3.4791 / normal are verbatim the pre-T098 numbers G-C4-1's pin forbids through the week half (pinned: `test_a_fourteenth_stray_day_outside_the_judged_week_moves_the_band_and_the_verdict`). The pooling is what makes the cliff reachable from a tier that contributes nothing to the week mean: an old-tier capture after the switch says nothing about whether the new tier's trial was an era, and it is what certifies it as one. Who notices: the athlete, in a readiness verdict that reads normal on a band partly built from a device era they abandoned; and Section 6, which reads the band. Accepted over counting only the new tier's strays for candidacy (re-opens T095's V1) and over a continuous clip, so that no behaviour changes and the cost is legible here rather than only in a review verdict; the unit at the gate — distinct local days, `baseline.n`'s unit — decides which side of the cliff an athlete lands on, and is pinned at all three sites it is read (T108, 2026-09-14, closing G-C5-2: a re-taken morning is one more stray capture and no more stray day, so counting captures instead of days would refuse the boundary and draw the abandoned trial back into the band). Before T095 one stray capture of either tier, on either side of a switch, produced the same silence for the whole era, and IDEA-065's row here presented the old-tier direction as the only one |
| A cleanly-ended strap trial followed by a thin week (snapshot daily to D-26, strap D-25..D-8, nothing D-7..D, snapshot resumed D+1) | The trial ended cleanly — every snapshot reading predates its first — so from D-5, when it holds 14 in the window and covers the week, it is indistinguishable from a switch: `tier_change` on D-25. Rule 3's recency keeps the strap (read last, D-8) through the thin days and the snapshot's first two resumed mornings; on D+3 the snapshot covers the week again, rule 2 hands it the baseline it sustains in both windows, and the reset is withdrawn without an event. Reported D-5..D+2, gone at D+3 (review cycle 3, reproduced) | The athlete, as a reset that appears for eight days and vanishes; the UI, as a band step in `points[]` at D-5 and back at D+3; Section 6, which reads `established: true` and `hrv_normal` on the trial's 14 readings on D-5 and D-4 — **the direction `research/00` §1.7 tolerates least**: §1.7 lets the daily gate *reduce* load freely but forbids it to *manufacture hard work*, and `hrv_normal` on a week that is genuinely suppressed tells Section 6 readiness is intact, so the plan's hard session stands on weak evidence — exactly what `judge`'s own docstring names as forbidden. (Corrected 2026-09-14, review cycle 6, T110: this cell had read "the direction §1.7 tolerates", inverted — G-C6-7.) |
| The switched athlete's thin week (snapshot to D-26, daily strap D-25..D-8, nothing since) | Under the densest fallback the first week with fewer than 3 strap readings handed the baseline back to the 44-reading snapshot, withdrew yesterday's `tier_change` and stepped the band 0.68 ln; under recency the strap — read last — keeps it with the same `reset_on` every day, and the thin days read `hrv_unavailable` | Was visible to the athlete as a reset that appeared and vanished and to the UI as a band step, for every athlete who switched 14–30 days ago on any week with fewer than 3 readings; prevented by rule 3's recency (T094) |
| The reverse transition (an owning strap abandoned for the daily snapshot at T) | Under T093's rule the reset arrived at T+54, when the strap dropped below 14 in the window — a month after the snapshot took the verdict at T+21 with `reset_reason` null; now `tier_change` with `reset_on = T+1` from T+21, unchanged on every later day through T+113, while the old strap still sustains `[D-126, D-67]` by fidelity; cleared at T+114 when it drops below 14 there (not at T+81, when the snapshot reaches 14 there — review cycle 3, S1) | The athlete, in a null `reset_reason` on a baseline that plainly began at T+1; Section 6, which read `established: true` on 14 snapshot readings without being told the baseline was fresh; prevented by the non-interleaved rule and the era-start `reset_on` (T094) |
| One resumption era reported three ways (a daily snapshot to 04-01, 30 silent local days, a daily strap from `R` = 05-02 — a layoff that was also a device switch) | `coverage_gap on R` from `R` to `R+66`, while the resumption lies inside `[D-66, D]`; `tier_change on R` from `R+67` to `R+79`, once it has left — the gap rule sees no silence and rule 4 reads the same era as a switch — and nothing from `R+80`, when the strap reaches 14 days in the previous window (rule 5's forward stop). One era, one `reset_on`, three reports, no event between them; `coverage_gap`'s `reset_on` never precedes `window[0]` *by the report outliving its own clip*, `tier_change`'s does from `R+67` (qualified 2026-09-14, T107: a `coverage_gap`'s does precede it when an era boundary clips later than the resumption — not this series, where the two clips coincide on `R`). **Accepted and named, not fixed** (review cycle 3, G14; T096): the gap rule is scoped to `[D-66, D]` and the switch rule to both windows, and each report's lifetime is stated in the construction reference and the schema rather than the two being aligned. T095's tolerance does not move the `R+67` hand-off (no strays on either side; pinned) | The athlete, who sees the reason for one unchanged baseline era change twice and its `reset_on` drop below `window[0]` on `R+67`; the UI, in nothing — `points[]` carries one continuous strap band throughout; Section 6, in nothing — `established: true` from `R+20` on, so nothing downstream is misled |
| A coverage gap **creates** the era boundary — **resolved by change, not re-accepted** (2026-09-16, review cycle 8, T129, withdrawing the 2026-09-15 acceptance of G-C7-3) | **The behaviour described below no longer happens.** `tier_change_reset` used to receive `baseline_readings` and `week_readings` derived from the **gap-rebound** `readings`, so every reading in `[D-66, gap_reset_on)` was invisible to `_era_boundary`'s stray count as well as to clause (a). The operative term was not the obvious one: the clip removes pre-gap readings of **both** tiers, and removing the pre-gap **new-tier** ones shrinks the stray term for *late* `A_end`s — those readings lie between the old era's first day and a late boundary's `B_start` — so late boundaries became admissible, or won on fewest strays, where the full history refuses them or prefers an earlier one. That boundary's `first_day` could fall **after** the resumption, and the composed clip then removed on-tier days at and after the resumption from the band, listed `before_reset: tier_change`. **T107 created that reachability**: before it a gap in `[D-66, D]` cancelled the era branch outright. **T129 removes the mechanism rather than pricing it**: rule 4 counts its strays over the **unclipped** `[D-66, D]` of every tier together with the previous window, so the clip decides which readings enter the *band* and no longer decides which readings `_era_boundary` can *see*. Clause (a)'s candidacy count still reads the gap-clipped window — it asks whether the resumption era sustains a baseline of its own — and the gap's precedence over the *report*, the composition of the two clips as the later first day, rules 1–3, the establishment gate and T125's withhold are all unchanged. **Why it was fixed rather than re-accepted.** The 2026-09-15 acceptance rested on a **direction**, not a rate: a 40,000-trial search for a flip to `hrv_normal` found **0**, so every collapse landed in `hrv_unavailable`, the under-call `research/00` §1.7 tolerates freely. That search was run against a rule that had since moved four times — **T116** (`73d6702`), **T117** (`eda0412`), **T127** (`28992a1`) and **T125** (`377534f`) — and T116 made the *thin* case, which is most of what it sampled, structurally unreachable, so it was **uninformative** about a clip leaving `baseline_n` at or above 14, which is where the flip lives because the clip also moves `band.lo` and a lower `band.lo` is an up-regulating band. **Re-run on 2026-09-16 (T123) at `377534f`** over 30,000 randomized capture histories (26,360 well-formed), against the *same-history* reference — the boundary the **full** capture history finds, same gap clip, same resolved tier, so only *which boundary* differs and the judged week and its mean are identical on both sides: **9,230 moved the boundary**, **4,466 (16.94%)** of those held `baseline_n >= 14`, the worst up-regulating band shift was **0.031367 ln**, **702** geometries were flip-reachable, and **5 trials realized the flip on an unchanged week mean** — shipped `hrv_normal`, the full-history reference `hrv_suppressed`, `baseline_n` 15..20 and established on both sides, no value tuned. The widest: `coverage_gap on 2026-08-03`, shipped boundary 2026-08-14 against the full history's 2026-07-07, `(2026-08-14, 2026-08-31)` `n` 18 `lo` 3.698502 against `(2026-08-03, 2026-08-31)` `n` 23 `lo` 3.712394, 7-day mean **3.702183 on both**. So "under-call only" was false, and §1.7 is not subject to a rarity argument. The user decision of 2026-09-16 was to **fix**, over accepting with the direction named and over narrowing the acceptance to `n < 14` (which splits the population and leaves the mechanism). The generator, the denominators and all five witnesses are in `spec/references/T123-direction-search-rerun.md` | **What the athlete gains, measured at T129's HEAD.** The direction search re-run at the fix finds the flip class **empty**: **0 of 26,360** well-formed histories (55,562 trials, seed 20260916) where the same harness found **18** immediately before, and shipped and the full-history reference agree on the baseline window, `baseline_n` and the verdict on **all 26,360**. A `coverage_gap` no longer reports a `reset_on` that precedes `baseline.window[0]` by this route (T107's own route is unaffected), and on-tier mornings at and after a resumption are counted rather than listed `before_reset: tier_change`. **The error direction the change itself carries, and its cost.** A boundary the *gap-clipped* population would have found is now refused where the full history says the eras interleave, so a genuine device switch inside a gapped window can go **unclipped** for longer — the band then spans the resumption era only when the gap's own clip says so. That is a **wider, older-evidence band**, the suppression-side conservatism §1.7 tolerates freely, and it is bounded by the gap clip, which is unchanged. **Pinned.** `test_the_unclipped_stray_count_refuses_the_gap_created_era_boundary` is the widest witness of the re-run, `build_series` end to end: one history, 7-day mean **3.430187 identical on both sides**, `(2026-08-17, 2026-08-31)` `n` 15 `lo` 3.371352 → `hrv_normal` before, `(2026-08-03, 2026-08-31)` `n` 22 `lo` 3.450867 → `hrv_suppressed` now, `established` and `withheld` unchanged on both sides (red-first at `2b5f569` on its first assertion). `test_the_gap_created_era_boundary_keeps_on_tier_days_at_the_resumption` is the T118 consequence pin inverted — it now asserts the reference numbers its own T118 docstring had recorded, `n` 22 and `band.lo` 3.6805 against the clipped 18 and 3.6757, with no `before_reset: tier_change` entry. The **population dependence itself** stays pinned on the rule directly by `test_the_gap_clip_moves_the_era_boundary_later_than_the_full_history_finds`, which asks `tier_change_reset` twice about one history with the population as its variable (2026-07-03 on the full population, 2026-08-14 on the clipped one) — that pin is why the fix is legible as a change of *population* and not of the era rule. **History.** Reached by T107 (2026-09-14, G-C6-5); named and accepted by T118 (2026-09-15, G-C7-3) on a measured direction; that direction falsified by T123 (2026-09-16) against the post-T116/T117/T125 rule; acceptance withdrawn and resolved by change here. Not pinned, and recorded as measurement rather than rule: the 26,360 / 9,230 / 4,466 / 702 / 5 denominators and the 18 → 0 before-and-after — nothing asserts them and no assertion goes red if the rate changes |
| Stale candidacy — **resolved by change, not re-accepted** (2026-09-15, review cycle 7, T117, closing [[IDEA-064]]) | **The behaviour described here no longer happens.** A July trial (14 strap readings still inside `[D-66, D-7]`) plus three strap days this week used to let rule 2 hand the strap the baseline on 2026-09-06 and 09-07 — `established: true`, `n` 14, `hrv_normal` judged on a band whose every reading was seven weeks old — with the snapshot taking it back on 09-08 when the first trial day aged out, and no reset (the eras interleave). Rule 1 now carries a **recency condition**: a candidate's latest baseline-window day must fall within `recency_tolerance_days` (**28**) of the most recent baseline-window day of **any candidate**, so the strap — 45 and 46 days behind the snapshot on those two targets — is struck from the candidate set before week coverage is consulted. The form is **relative with tolerance**, chosen by the user on 2026-09-15 after the **parameter-free** form ("read later than the other candidate") was built and measured and turned **five pinned tests red in both error directions**: a daily lower-fidelity tier is always read at least as recently as a 2-3-day-a-week higher-fidelity one, so a strict day-comparison rejects the oscillating strap (4 days behind) and the abandoned trial (45 days behind) alike, voiding rule 2's fidelity precedence instead of qualifying it, and it strips a legitimately resuming snapshot on `D+3` as well. The published `thresholds` block gains **no key** and the contract shape is unchanged ([[IDEA-070]], 2026-09-15). Why 28: four judged weeks (4 × `window_days`), and — the load-bearing half — greater than `gap_reset_days` (21), so relative staleness never strikes a tier for a silence shorter than the shortest silence this feature is willing to call a break; the two mechanisms partition rather than race, the coverage gap clipping a wholly silent series out of the window before candidacy is counted and this rule acting only where another tier kept the series alive. Dated measurement, 2026-09-15, re-scoped by T121, scope pinned by review cycle 8 (`acfebae`): green for every N in **[18, 44]** over the **394 tests** (`BAND_CORPUS_WHEN_MEASURED`) the five HRV suites held on that date less the tolerance pin that existed then (now `BAND_CORPUS_EXCLUDES`) — those pins assert `recency_tolerance_days == 28` or its measured consequences, so over all of the five suites the green band is `{28}` and the bracket is a claim about the rest of them. The subtraction, and the live collection it is taken from, are asserted by `test_the_scoped_suite_count_the_band_was_measured_over_is_pinned_not_published` in `test_hrv_trend_endpoint.py`; the live count is `SCOPED_SUITE_COLLECTED` there and is deliberately **not** transcribed here, T121's literal having been invalidated by the next commit of its own fix batch with nothing able to see it (review cycle 8, `acfebae`). That relation grows with every test added to the five suites, so the bracket stays a claim about the 394 it held when it was run. Red at 17 (a legitimately resuming snapshot is struck) and at 45 (the July trial is re-admitted). Pinned by `test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band` (the reproduction series, kept verbatim) and `test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it` (28 admits, 29 strikes, a `gap_reset_days`-long silence never strikes). [[IDEA-062]]/T116 does **not** subsume this: the stale trial reports `established: true` with `n` 14 — established, merely old — so both changes were needed and neither replaces the other | **What the athlete gains, measured 2026-09-15 on the reproduction series.** On 2026-09-06 the verdict moves from `hrv_normal` to **`hrv_suppressed`**: three of the series' fourteen genuinely suppressed days sit in that judged week and the snapshot's own 60-day band now calls them what they are, where the July band called the week normal. That is the forbidden direction this row was re-opened over — up-regulation on weak evidence, which `research/00` §1.7 forbids, silenced whenever `mean7 ≥ mean_July − 0.5·SD_July`, so an offset of ~0.10 ln (about 10% of rMSSD over seven weeks, the usual sign when the trial was captured while less fit) hid a suppression a full half-width deep against F005's own documented band steps of 0.20 and 0.68 ln. On 09-07 the verdict stays `hrv_normal` for the correct reason: only two suppressed days remain in the week, 7-day mean 3.5598 against `band.lo` 3.5079. `baseline.tier` no longer flips to the strap on a two-day-old habit, `baseline.window` no longer reaches back to July, and `points[]` carries no band step on 09-06 and back on 09-08. **The error direction the change itself carries, and its cost.** A tier genuinely resumed after more than four weeks away is **not** a candidate on the day it resumes: an athlete who puts the strap back on after a five-week holiday, while a daily snapshot ran throughout, is judged on the snapshot until the strap's own readings carry it back — which they do, because the strap then becomes the most recently read candidate and is its own reference. Who notices: the athlete, in `baseline.tier` staying on the lower-fidelity device for a few days after a genuine return, and Section 6, which reads the tier. Accepted: it is a **suppression-side** conservatism — the band stays the one the athlete's recent history supports — which is the direction §1.7 tolerates freely, and it is bounded, because the returning tier is struck only while a rival candidate is more recent. The measured bracket of this direction is the N = 17 end of the band above: at 17 the cost becomes a defect, a legitimately resuming snapshot losing a week it covers (`test_a_clean_ended_strap_trial_reads_as_a_switch_until_the_snapshot_covers_a_week_again`). **Not changed by this row:** the oscillating 2-3-day strap habit (both tiers are in current use, 4 days apart, so neither is struck — the `@must` criterion that made the parameter-free form unbuildable), and G12's stale clause-(b) candidate in the previous window, which the recency condition deliberately does not reach; see the row above and `tier_change_reset`'s docstring for why. **History.** This cost was accepted in review cycle 2 (T094) as a false suppression, the direction §1.7 tolerates freely; corrected and widened 2026-09-14 (review cycle 6, T110, G-C6-7) after that cell was found inverted and the forbidden direction found never to have been named; re-opened there rather than re-confirmed, and resolved here by a user decision that changed the behaviour |
| The tier choice is not reproducible from the response — **priced, not fixed** (2026-09-15, [[IDEA-070]], user decision at review cycle 8) | `recency_tolerance_days` (28) decides which tier's band the verdict is computed against and is echoed nowhere in the payload. `excluded[]` lists every row in `[D-66, D]` with its date and, for `off_baseline_tier: <tier>`, its tier, so a client can count the losing tier's distinct days in `[D-66, D-7]` and `[D-6, D]`, apply the six published `thresholds` and derive a tier the response does not report — on the reproduction series, `chest_strap_raw` against the reported `health_snapshot`, the two counts and the reported tier all asserted by `test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band` (14 strap baseline days, 3 strap week days, `tier == SNAPSHOT`). The conflict is derivable; **the magnitude that resolves it is not**, and that half of `research/00` §1.6's recompute-by-hand promise is now unmet. The decision was to narrow the promise rather than publish a seventh key, on contract-shape cost and T117's design intent — `Thresholds` gaining a key moves `openapi.yaml`, `check_drift.py` and every fixture asserting the block's exact six keys. (The "relative, so no key" reading is **not** the basis: `gap_reset_days` is equally relative and is published — [[IDEA-070]] §"Why the recorded reason does not settle it".) | The client, and only by suspecting it: nothing in the response says a constant is missing except the `thresholds` description itself, which now says so in both copies — pinned by `test_the_two_copies_of_the_thresholds_contract_publish_the_same_claims` |

### The verdict

**What it rejects.** **A verdict of any kind is withheld unless the baseline is established**
(n >= 14), the week can support it (>= 3 readings), **the week is a fair sample of the tier being
judged** (T125, 2026-09-16) and a band exists (n >= 2). Below an established band the verdict is
`hrv_suppressed`; inside or above it, `hrv_normal`; in every other case, `hrv_unavailable` — and the
band is still reported wherever the baseline can build one, so what was withheld stays checkable by
hand (`research/00` §1.6).

**The population in between, and how it was resolved.** Between "an established baseline judged
against its own band" and "no band at all" sits the baseline of n = 2..13: thin enough that the
feature itself reports `established: false`, dense enough to compute a band. Until 2026-09-15 that
population was split — the same thinness withheld `hrv_suppressed` and did **not** withhold
`hrv_normal`, so a week inside or above a band built from as few as two readings read `hrv_normal`.
The separating signal was `baseline.established`, which the verdict did not carry and `points[]`
still does not. **Resolved 2026-09-15 (review cycle 7, T116, closing [[IDEA-062]]; user decision;
a behaviour change): the gate is symmetric.** `hrv_normal` requires `established`, else
`hrv_unavailable`.

*Why it was not merely documented.* `hrv_normal` on an unestablished baseline tells Section 6 that
readiness is intact on evidence the same response calls inadequate — up-regulation on weak evidence,
the one direction `research/00` §1.7 **forbids**, and the direction `judge`'s own docstring already
named as forbidden for the cell below the band. Applying that reasoning to one cell and not its two
neighbours was the defect, not the spec's silence about it: this subsection's acceptance criterion
(AC 6) asked only whether a thin baseline can *suppress*, and the suite's contract table inherited
the frame. It was reachable after every **coverage-gap** reset the feature performs — a gap reset
collapses the baseline deliberately and the athlete then traverses 20 unestablished days — *corrected
in place 2026-09-18 ([[T142]]), following `research/00` §5.4: this read "after **every** reset the
feature performs — gap resets **and tier changes** collapse the baseline" until now, which is false of
a tier change. A clean source-tier change collapses nothing: `established` stays **true** and `n` merely
decays 60 → 47, so it traverses **zero** unestablished days and never reached this cell; its own 18-day
silence is week coverage on the abandoned tier, a different mechanism ([[T138]]). Corrected in place
rather than appended two sentences later, so a reader who stops at the first sentence does not carry
the withdrawn claim away — the append pattern is what left this site and the cost table behind.* It was
constructed and run at HEAD `da4cdf0` (Pacific/Auckland, D = 2026-09-14): 86 days of daily chest
strap at ~50 ms, a 30-day illness layoff, 10 days back at ~40 ms gave `reset_reason=coverage_gap`,
`baseline_n=3`, `established=False`, `readings_in_window=7` and `verdict=hrv_normal`, band
`lo=3.6798 hi=3.7058` — readiness reported intact for an athlete ~20% below his own pre-layoff
level.

*What the rule does not resolve.* [[IDEA-064]] is untouched **by T116**: the stale-trial route
reports `established: true` with `n` 14, so the trial *is* established, merely old, and the
symmetric gate lets it through. That is why the two changes were both needed and neither subsumes
the other; [[IDEA-064]] was closed separately, on the same day, by T117's recency condition on
candidacy, which strikes the trial from the candidate set before any verdict rule is reached
(stale-candidacy row above). IDEA-062's own observation — that the consumer could tell only by reading
`baseline.established`, which `points[]` does not carry — is **answered for the day the request
asks about, and only for that day** (corrected 2026-09-18, review cycle 10, T146; the argument
recorded here before that correction discharged the concern for the whole population, on the
premise that a point carries a verdict. `HrvPoint` carries `date`, `ln_rmssd`, `baseline`,
`swc_low` and `swc_high` and nothing else — **there is no verdict in a point** — so the premise was
false of the shipped shape and the §1.7 downgrade rested on it). Under the symmetric rule the
**top-level** `verdict` carries the information, since `hrv_normal` there asserts an established
baseline (pinned as a contract claim by
`test_the_two_copies_of_the_verdict_contract_publish_the_same_claims`, and behaviourally by the band
suite's three establishment-gate tests). It is **not** answered for the other days of a range
request: for every day but `to`, `points[]` publishes a full band that may rest on as few as two
baseline readings, with nothing in the point saying that day's verdict was withheld. Nor is it
answered for `hrv_unavailable`, which still conflates "no band", "too thin a week" and "an
unestablished baseline". Drawing the band anyway is the deliberate decision, unchanged here; a
per-point `tier` and `established` remain outside the contract (`CRITIC-F005.md`, priority 3).

**The second population in between, and how it was resolved — the judged week's freshness**
(amended 2026-09-16, review cycle 8, [[T125]]; user decision on six measured fix forms; a behaviour
change). Between "a week of the athlete's own recent mornings" and "no week at all" sits a week
whose three-or-more readings are real, on-tier, established — **and all of them older than the
athlete's return to a different device.** The tier rule's recency condition (above) is measured over
the **baseline window**; the verdict is a claim about the **judged week**; nothing required the
surviving tier's week readings to be the recent ones. So the condition strikes the tier the athlete
is currently recording on, keeps a carrier whose week coverage has just run out, and computes the
verdict from that carrier's last pre-return days with the athlete's own mornings listed
`off_baseline_tier`. Reproduced twice independently (Pacific/Auckland): a strap worn daily to
`2026-07-31`, a 39-day strap silence carried by a daily snapshot to `09-08`, the strap resuming
`09-09` with four consecutive **suppressed** mornings. At `D = 2026-09-12` the shipped rule reported
`baseline.tier` `health_snapshot`, `established: true`, `readings_in_window` **3** — `09-06`,
`09-07`, `09-08` — and the verdict **`hrv_normal`**. The athlete came back to his strap, recorded
four suppressed mornings, and was told readiness is intact on a week he did not live. Swept over 724
return geometries that is **36 geometries** of `hrv_suppressed -> hrv_normal`, `research/00` §1.7's
forbidden direction, none of them named anywhere in this document before now.

The separating signal is the **order of the two tiers' judged-week days**, not their counts.
**Resolved: a candidate struck for staleness whose judged-week readings are all *later* than the
resolved tier's means the week is not a fair sample, so no verdict is asserted** — the struck tier
holding at least `min_window_readings` (3) of those days, the same threshold any week must meet. A
count-based predicate ("the struck tier covers the week") is satisfied by an abandoned trial picked
up for three days as well, and re-opens the stale-candidacy defect T117 closed; the day order is
what distinguishes a *return* from a *trial*. **Nothing but the verdict moves:** measured over 2050
return geometries, the resolved tier is identical to the pre-T125 rule on all 2050 rows, the
reported baseline window is identical on all 2050, and the only verdict change in either direction
is `hrv_normal -> hrv_unavailable`.

*Why withholding and not answering him.* The competing form re-admitted the struck tier on the same
predicate and delivered `hrv_suppressed` from the athlete's third morning back — strictly more
useful. On the same sweep it closes the same **36** and **creates 54** geometries of
`hrv_unavailable -> hrv_normal` judged against a band **36 to 53 days old** (median 45), plus **36**
more where a surviving `hrv_normal` comes to rest on that band. Its extra reach is one predicate
that lands safe-side for a suppressed athlete and forbidden-side for a healthy one in exactly equal
numbers (54 and 54) and cannot deliver the first without the second. The created population is the
*more ordinary* of the two: the carrier tier recorded every day of the layoff, so a return at the
same level requires nothing to happen, while a suppressed return requires a discontinuity timed to
the device change. Cost on the ordinary athlete and benefit on the unusual one is the reverse of
§1.7's asymmetry, so the form whose cost is silence was taken: **36 closed, 0 forbidden-direction
geometries created, against 54**. (What the sweep measures is how often the competing form would
judge a return against a stale band — 90 geometries — not how often that band is *wrong*; every
healthy return in it is seeded at pre-layoff values, so the misjudged fraction is unknown and at
most 90. `mutation-results-prove-only-what-the-mutants-encode`: read the rows for the population
size, not for an error rate.)

*What the rule does not resolve.* **Mornings 1 and 2 back are not fixed by this form or by any of
the six measured.** One and two return mornings are fewer than `min_window_readings`, so the
returning tier is invisible to the predicate and the verdict still comes from pre-return readings
and still reads `hrv_normal`. The guard that keeps a stray cross-device capture from withholding a
legitimate verdict and the constant that hides those two rows are **the same constant**, so reaching
them is exactly the change that starts producing false withholds (none was found: four benign
interleave series read `hrv_normal`, unchanged). Named here rather than accepted silently. Nor does
`hrv_unavailable` say *why* it was withheld — this adds one more reason to the enum's undifferentiated
set, which is **six** causes in all and was miscounted as four here until 2026-09-16 ([[T128]]; the
set is enumerated in the last row of the table below and pinned to `judge` by
`runcoach-api/tests/test_hrv_unavailable_causes.py`); a per-point `tier` and `established` remain
outside the contract.

**The third population in between, and how it was resolved — a device the athlete has never used
before** (added 2026-09-16, review cycle 9, [[T132]], form B against the measured table in
`spec/references/T125-fix-form-measurements.md`; user decision; a behaviour change). T125's rule
above reaches only a tier the recency condition **struck** — and a struck tier must first be a
*candidate*, which requires `>= min_baseline_readings` distinct days in the baseline window. A tier
whose first-ever reading falls **inside the judged week** has zero baseline-window days, so it is
never a candidate, never struck, and T125's rule could never fire for it — however many judged-week
days it holds and however cleanly they are ordered after the resolved tier's own. Reproduced
(Pacific/Auckland): a `chest_strap_raw` habit daily `2026-07-04..2026-09-04` @ 40.0 ms, a
`health_snapshot` the athlete has **never used before** on `2026-09-05..2026-09-07` @ 15.0 ms (three
deeply-suppressed mornings, all strictly later than every strap day), judged at `D = 2026-09-08`.
Shipped resolved `chest_strap_raw`, `readings_in_window` 3 (the three stale strap mornings),
`withheld: false`, `baseline_n` 60, `established: true` — **`hrv_normal`**. The athlete's own three
brand-new-device mornings, the ones actually suppressed, were silently excluded `off_baseline_tier`
while he was told readiness was intact. `research/00` §1.7's forbidden direction, and the sixth
population of that shape found in this rule (review cycle 9, G-C9-1).

**Resolved: the order clause is asked of the union of the struck tier(s) with any tier holding
*zero* distinct days in the baseline window and at least `min_window_readings` distinct days in the
judged week.** The order clause itself is unchanged — every one of that tier's week days must still
be later than every judged-week day of the resolved tier. Zero is not a tuned threshold; it is the
only value that means "never used in the baseline window", so a tier with 1..13 days of history (an
established-adjacent geometry, not a new one) is untouched. Three widening forms were measured; two
broader ones (union on any under-candidate tier holding a full week; and dropping the candidacy gate
entirely in favour of "most-recently-read tier") also close a second, previously unnamed
forbidden-direction population (`inter_rows`'s "resumed" row at era length 10) but cost far more —
988 of 2050 swept geometries withheld at `c = 0`, reaching return days up to 19 back, not collapsing
at higher `c` — while the form here costs **260 of 2050 at `c = 0`** (+80 over shipped's 180, all in
the range `q = 2..6` shipped already reaches) and is **byte-identical to shipped at every `c >= 1`**.
**Tier selection, the band, the baseline window, both reset rules and the establishment gate are
unchanged**; this widening changes what is *said* about a week, never what the week is, matching
T125's own finding one axis over.

*What the rule does not resolve.* **A legitimate, permanent device switch is, for its first
`min_window_readings` days, the identical shape to this rule's own reproduction** — an un-established
tier holding a full judged week, entirely after the resolved tier's own. Nothing in the judged
week's readings, the baseline counts or the last-read days alone separates "a device he will never
use again" from "a device he bought yesterday and will use forever"; that is why the widening also
reopens two of the athlete's first four mornings on a permanent switch (days 3 and 4; days 1 and 2
are already invisible to T125's rule for the same reason). `tier_change_reset`'s 14-baseline-window-
day accumulation, not a predicate over one week's shape, is the mechanism built to make that
distinction in general, and this is a structural finding rather than a corner case: no predicate over
`week_readings`, `baseline_counts` and `last_read` alone can do what a time-accumulating mechanism is
for.

**The qualifier both accepted costs are missing: the compensating behaviour is deferred to E007, and
E007 does not exist** (added 2026-09-16, review cycle 8, [[T128]]). T116's cost and [[T126]]'s
re-confirmation of it at 20 days were both accepted on one sentence — that the silence is
*down-regulation on weak evidence, the direction `research/00` §1.7 tolerates freely*, because
Section 6 reads `hrv_unavailable` and **widens its guardrails** rather than being told readiness is
intact. That sentence describes a consumer. **Section 6 is E007, and E007 does not exist** — the
gate that would drop the HRV axis and lean harder on the subjective and resting-HR axes is
specified (§6.2.4, amended by T128 to name all six causes and to state that a withheld verdict
arriving with a real band and week mean beside it is still treated as a dropped HRV axis) and is not
built. So today both costs are **net cost with no offsetting benefit**: 20 silent days after every
reset, composed with the ~3 weeks of silence a coverage-gap reset needs to fire, and five of the
first seven mornings back after a device return (T125), paid by an athlete whose readiness is simply
not answered by anything. The benefit that was supposed to make them cheap is **deferred to E007**.
The acceptances themselves stand — a missed hard session is cheaper than one taken on a
three-reading baseline whether or not the fusion exists, and T125's silence is still the form whose
cost is silence rather than a forbidden-direction flip — but they are not free-because-Section-6-
compensates, and that was stated nowhere until now. A qualifier on two accepted costs, not a
re-opening of either.

**Cost of each error direction, and who notices.**

| Error | What happens | Who notices |
|---|---|---|
| `hrv_normal` on an unestablished baseline (the pre-T116 rule) | A week inside or above a band built from 2 to 13 readings reads `hrv_normal` with `established: false` beside it. Section 6 is told readiness is intact on a baseline the same response calls inadequate, so a planned hard session stands — **up-regulation on weak evidence, the direction `research/00` §1.7 forbids**. Reachable after every **coverage-gap** reset the feature performs: on the constructed series above, an athlete ~20% below his own pre-layoff level read `hrv_normal` on `baseline_n=3`. **Corrected in place 2026-09-18 ([[T142]]): this row read "Reachable after every reset the feature performs" until now, which is false of a tier change** — a clean source-tier change leaves `established` **true** (`n` decays 60 → 47) and traverses **zero** unestablished days, so this cell is unreachable through it ([[T138]]; see the corrected row below) | Nobody, in the verdict — that was the point: the athlete and the UI see `hrv_normal`, and only a consumer that reads `baseline.established` (which `points[]` does not carry) can tell. **Removed, not priced**, 2026-09-15 (T116) |
| The new cost: `hrv_unavailable` for 20 days after a **coverage-gap** reset | **Corrected 2026-09-17 ([[T138]]): this row's mechanism is true of a coverage gap and false of a tier change, and its heading said "after every reset" until now — see the two rows below.** A coverage gap collapses the baseline to a handful of days, and `established` stays false until the fresh baseline reaches 14 distinct local days — for a daily capturer that is `R+0 .. R+19`, **20 days**, and longer for a sparser one. **Twelve** of those days changed verdict here (`R+8 .. R+19`, which used to read `hrv_normal` unless the week fell below the band); the first eight already read `hrv_unavailable`, because a band needs two readings. The two quantities are not the same and this row stated the smaller one as the duration until 2026-09-16 (T126). The band, `baseline.n`, `established`, `reset_reason` and `reset_on` are all still reported, so the response says why | The athlete, as a readiness verdict that goes quiet for the 20 days after a device switch or a return from illness rather than saying "normal"; Section 6, which reads `hrv_unavailable` and **withholds** — it widens its guardrails instead of being told readiness is intact. That is down-regulation on weak evidence, **the direction §1.7 tolerates freely**: the daily gate may reduce load freely, and a missed hard session is cheaper than one taken on a three-reading baseline. **Accepted 2026-09-15 as the deliberate cost of the change** — but on a stated cost of "~12 days", which is the changed-verdict count, not the duration. **Re-opened 2026-09-16 (T126) at the corrected 20 days and awaiting the user's decision**, because a coverage-gap reset needs more than 21 silent days to fire, so the composed quiet is ~3 weeks of no captures followed by 20 days of `hrv_unavailable`. Pinned as a behaviour, not only as prose, by `test_hrv_trend_reset.test_one_new_tier_capture_before_a_genuine_switch_does_not_silence_its_reset`, whose `SW+20` — the day the reset clips the era to 13 readings — moved from `hrv_normal` to `hrv_unavailable` in this change while `SW+21` (14 readings) and `SW+60` (53) did not, and the **duration** by `test_hrv_trend_reset.test_the_establishment_delay_after_a_reset_is_twenty_days` (T126) |
| The pre-T125 rule: `hrv_normal` on a week that predates the athlete's return | The recency condition strikes the tier he is recording on, the surviving carrier's last pre-return days become the whole judged week, and the verdict reports readiness intact. **36 of 724 return geometries**, every one a suppressed return told `hrv_normal`. `research/00` §1.7's forbidden direction, so a planned hard session stands | Nobody, until it is walked at the verdict: the tier, the band, `established` and `excluded` are all correct and self-consistent, and the athlete's own mornings are listed `off_baseline_tier` with a true reason. Section 6 consumes the verdict |
| The new cost: five of the first seven mornings back say nothing | **72 of 2050** swept return geometries move `hrv_normal -> hrv_unavailable` — per athlete, mornings **3 and 4** back turn silent on top of mornings **5, 6 and 7**, already silent for want of week coverage. Two of the five are this change's doing. Across the sweep `hrv_unavailable` goes from 838 of 2050 to 910. It stacks **additively** in front of T116's 20 unestablished days when the return also trips a reset. And the **54** suppressed returns the competing form would have answered stay silent instead of being told `hrv_suppressed` | The athlete, who recorded four mornings and is told nothing about any of them; Section 6, which widens its guardrails and leans on the subjective and resting-HR axes for five days rather than two |
| The residual: the opening return mornings are still judged on pre-return readings — **`min(window_days − min_window_readings, k₃)`** of them | While the returning tier holds fewer than `min_window_readings` judged-week days it is invisible to the rule, so `hrv_normal` on a pre-return week survives there. `k₃` is the offset at which the returning tier's `min_window_readings`-th distinct local day enters the judged week; the cap of 4 is **not this rule's** but the *carrier's* judged-week coverage expiring (a daily carrier ending RET−1 leaves `6 − k` carrier days, so `week_too_thin` bites at `k` = 4). **Measured 2026-09-18 ([[T145]], all 64 weekly return patterns containing day 0, on this side and on T132's `never_used` side, zero mismatches, stable for layoffs s = 33..48): two mornings at daily and at 4/wk-*clustered* capture, and FOUR whenever the return is captured sub-daily** — 4/wk spread `{0,2,4,6}`, 3/wk `{0,2,4}`, 2/wk `{0,3}`. This row stated the daily figure as the general bound until then; the axis is the *spacing* of the captures, not their weekly count. **No form measured at T125 closes it** — the guard against false withholds and the constant hiding these rows are the same constant | The sub-daily athlete, for twice as many mornings as this row used to admit, on the band of a device he stopped using. Unchanged from the pre-T125 rule, and named here so it is not mistaken for closed. **Behaviour left unchanged 2026-09-18** (user decision, review cycle 10): closing it means acting on fewer than `min_window_readings` readings of the returning tier, the exact trade that constant governs, and [[T130]] measured that no predicate of this family closes the neighbouring residual — carried whole to [[IDEA-071]]'s sprint |
| The rule is **inert at sub-daily density**: T125 and T132 decide no verdict there | At 4/wk-spread and at 3/wk the withhold flips `series.withheld` true only at `k` = 4..6, where `readings_in_window` is already 2, 1 and 0 and `week_too_thin` precedes it in `judge`'s fixed order of unavailable causes — **the verdict would be identical with the withhold deleted**. At 2/wk it never fires at all (checked to `k` = 40). The whole of a sub-daily return's silence is incidental expiry of the *carrier's* week coverage. Measured 2026-09-18 ([[T145]]), on the return side and on T132's `never_used` side alike. Why nine cycles missed it: **capture density is the one axis no sweep varied** — `spec/references/T125-fix-form-measurements.md` uses a contiguous daily return run in all 2050 rows, and the band suite's device-return walk indexes by *days since return* while reasoning as though that equals *mornings captured* | The **"two days a week"** and oscillating athletes this Negative Class names as first-class populations: two ratified behaviour changes and three review cycles of work change nothing for them. T132's own price of two days of silence per permanent device switch is, for them, **zero** days attributable to T132. Recorded in [[IDEA-071]] as the strongest single argument that this rule wants re-deriving rather than an eighth qualifier |
| The residual T123 measured: the withhold is disarmed when the carrier keeps recording | T125's rule is an **order**, not a count -- every judged-week day of the struck tier must be later than **every** judged-week day of the resolved tier -- and the order clause is what separates a device return from an abandoned trial, so it cannot simply be dropped. It therefore holds only when the carrier **stops** on the day the athlete goes back. Measured 2026-09-16 (T123) at `377534f` over the same 2050-row return rectangle with one dimension added -- `c` carrier captures kept on the last `c` local days, the athlete who puts the strap back on and **keeps wearing the watch**: at `c = 0` the withhold fires on **180 of 2050** and there are **0** forbidden flips; at `c = 1` it fires on **none of the 2050** and **54** geometries read `hrv_normal` again; at `c = 2`, **72**; at `c >= 3`, the full **90** -- the critic's 36 plus the 54 shipped had been declining to answer. Representative (`c = 3`, `s = 32`, `q = 3`, `r = 4`): 7-day mean **3.7108** on the carrier's pre-return mornings against the athlete's own **3.2189**, `baseline_n` 29, `readings_in_window` 5 -- `hrv_normal` on a week he lived as four suppressed strap mornings. **T125 closed the reproduction, not the population**; this residual is distinct from the mornings-1-and-2 one above and survives at **every** `r`. Recorded in `spec/references/T123-direction-search-rerun.md`. **Accepted as a named residual and deferred to [[IDEA-071]]'s sprint -- user decision 2026-09-16, taken against two rounds of measurement and not as a rarity argument** ([[T130]]). **Nine candidate predicates were measured across two rounds at `cbd7134` and none is both suite-green and overlap-proof without an unjustified parameter.** Round 1 -- per-tier silence (5 red of 408, green band provably empty), currency (0 red, flips 0/0/0/18/36/54 by `c`), median order (1 red), fraction >= 1/2 (0 red, flips from `c` = 2), order + `tol` = 3 (0 red, 0 through `c` = 3, 72 at `c` = 4). The structural result, and the reason a third patch was not attempted: **every predicate that asks about the shape of the judged week has a `c` that defeats it, because carrier overlap is a fact about the judged week**, and the two that survive every `c` defeat themselves on the abandoned July trial instead. Round 2 measured the one axis outside the week -- the returning tier's **era length before its silence** (July trial 14 days, sweep 80). It is genuinely overlap-proof (**0 flips at every `c` = 0..5** for `T` = 15..80, the only candidate in either round to manage that) and it still fails, three ways: at `T <= 14`, **5 red** (the July era is exactly `MIN_BASELINE_READINGS`); at `T >= 81`, **6 red** and the sweep goes *worse than shipped* (36 flips at `c` = 0 against 0); and **inside** the nominal band at every `T` = 15..80, **2 red** on `trial_then_switch` -- an **87-day** era on a tier the athlete **left**, longer than the sweep's 80-day return and arithmetically indistinguishable from it. That is a **third population**, already in the suite and on nobody's list. At 17 matched era lengths, abandoned against resumed, the confusion is total (`FP` = `TP`, `TN` = `FN`; at `T` = 30, 13/4/13/4) -- the predicate answers identically for both because it never looks at the week, which is how per-tier silence failed, and one way worse, since era length is a continuous axis on which the truth is **constant**. **The form that met the written deliverable and was still not shipped:** `era >= 15` conjoined with the median order is **0 red of 408 and 0 forbidden flips at every `c` = 0..5**, unmatched by anything in either round. Refused because the era clause does no work -- the conjunction is the median row for row *except* the July trial, buying an exemption for one fixture at one day's margin while the overlap-proofness is entirely the median's -- because both `T` and the history population are free parameters (`MIN_BASELINE_READINGS + 1`, the `+1` being the whole constant, weaker than `tol` = 3, which at least described a shape in the data), and because it carries an **unpinned false-withhold class**: the athlete who switched away, blanked on a genuinely suppressed week his current daily device covers, green only because the suite's one fixture places the strays at `4/3/2`, the placement where the medians tie. **Why deferred rather than patched.** This is the fifth §1.7-forbidden population found in this rule and the third whose fix revealed the next one; the sprint that takes [[IDEA-071]] starts from the stated missing model -- a rule that cannot express *"the athlete came back"* is being asked what to tell an athlete who came back -- and from two named populations no test pins. All nine candidates, both harnesses and the denominators are in `spec/references/T125-fix-form-measurements.md` and `spec/references/T130-overlap-sweep-harness.py` | The athlete, in `hrv_normal` on a week of suppressed mornings he recorded himself; Section 6, which reads the verdict and lets a planned hard session stand. **This is `research/00` §1.7's forbidden direction and it ships open** -- **54** geometries of 2050 at `c` = 1, **72** at `c` = 2, **90** at `c` >= 3, against **0** when the carrier stops. It is accepted on the measured finding that no predicate of this family closes it, **not** on its rate |
| The pre-T132 rule: `hrv_normal` on a device the athlete has never used before | T125's rule reaches only a tier the recency condition **struck**, and a struck tier must first be a *candidate* (`>= min_baseline_readings` baseline-window days). A tier whose first reading falls inside the judged week has zero such days, so it is never struck and the withhold could never fire for it. Reproduced: a chest-strap habit daily to `2026-09-04`, a Health Snapshot never used before recording three deeply-suppressed mornings `2026-09-05..09-07`, judged `2026-09-08` — `chest_strap_raw` resolves, `readings_in_window` 3 (three stale strap mornings), `hrv_normal`, while the athlete's own three brand-new-device mornings are excluded `off_baseline_tier`. `research/00` §1.7's forbidden direction — the sixth population of this shape found in this rule | Nobody, in the verdict — the tier, the band and `established` are all correct and self-consistent, and the athlete's own mornings carry a true `off_baseline_tier` reason. Section 6 consumes the verdict. **Closed 2026-09-16 ([[T132]], form B)** |
| The new cost: two more mornings of a permanent device switch go silent | The widening also reaches a **legitimate, permanent** switch's third and fourth mornings, which are the identical shape for the first `min_window_readings` days: an un-established tier holding a full judged week, entirely after the resolved tier's. Swept over the 2050-geometry rectangle at `c = 0`: **260 withheld, up from shipped's 180** (+80, confined to `q = 2..6`, the range shipped already reaches), and **byte-identical to shipped at every `c >= 1`** (0 additional withheld, 0 additional flips) — the extra withholds recur where the returning tier's era falls entirely before the sweep's own baseline window, the same defect class as T132's own reproduction rather than a new one. Pinned by `test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline` (`k = 3`, `k = 4`, moved from `hrv_normal` to `hrv_unavailable`) | The athlete, on his third and fourth morning with a new device he will keep using, told nothing rather than `hrv_normal`; Section 6, which widens its guardrails two mornings earlier than before at daily capture (where his captures are spread — 4/wk spread, 3/wk, 2/wk — it widens them no earlier at all; a 4/wk *clustered* return pays the same two mornings the daily one does, the axis being the spacing of the captures rather than their weekly count — see the inertness row above, and [[T147]] 2026-09-18 for the qualifier). That is down-regulation on weak evidence, **the direction §1.7 tolerates freely** — the same trade T125 made, one axis over. **Accepted 2026-09-16 as the priced cost of closing the forbidden direction above ([[T132]], user decision, review cycle 9)**: nothing in `week_readings`, `baseline_counts` or `last_read` alone separates this population from T132's own reproduction, so the cost is structural, not a corner case |
| The verdict still cannot say *why* it is unavailable | `hrv_unavailable` was emitted on **six** distinct causes and the enum separated none of them: when there are fewer than two baseline readings, so no band exists; when there are fewer than `min_window_readings` (3) readings of `baseline.tier` in the judged week; when the judged week is not a fair sample of the resolved tier (T125); when there is a baseline below `min_baseline_readings` (14), reported as `established: false` (T116); when the judged day is after the athlete's local today; and when no resting-HRV reading of any tier can sustain a trend. This cell said **four** until 2026-09-16 — it had been written before T125 and never counted the structural case — which is the same staleness T128 found in `spec/02`, `spec/03` and `spec/06`; the enumeration was derived from `judge` and pinned at all four sites by `runcoach-api/tests/test_hrv_unavailable_causes.py` (T128). Reproduced concretely by the cycle-9 critic and independently by the orchestrator (Pacific/Auckland): a clean, gapless, permanent device switch — daily `chest_strap_raw` at 40.0 ms through 2026-08-31, daily `health_snapshot` at 40.0 ms from 2026-09-01, identical values on both tiers — reads `hrv_unavailable` on 2026-09-03 and 09-04 with `established: true`, `baseline.n: 60`, `readings_in_window` 4 and 3 (both at or above `min_window_readings`) and both reset fields `null`: every field the response exposed said the week was judgeable, and only the unexposed `HrvSeries.withheld` (T125/T132) explained it | A client that wants to distinguish them, which must read `band`, `readings_in_window`, `baseline.n` and `baseline.established` — all of which the response carries (`research/00` §1.6), and all of which `points[]` still omits (`CRITIC-F005.md` priority 3, unaffected by this change). **Closed 2026-09-17 ([[T137]], option (c), user decision, review cycle 9)**: a single `unavailable_reason` enum on the verdict block — `no_tier_sustains_a_trend`, `no_band`, `week_too_thin`, `week_not_representative`, `baseline_unestablished`, `day_not_happened` — names which of the six fired, `null` whenever `verdict` is not `hrv_unavailable`. `judge`'s four internal causes are reported in the fixed order it already evaluates them (pinned directly: a week that is both withheld and built on an unestablished baseline reports `week_not_representative`, never `baseline_unestablished`); the structural cause and the route's own `day_not_happened` (which overrides whichever of the other five the pure rule decided, since the fields that would explain them are still computed and returned as usual for a near-future day) are named apart from it. Pinned to the two opaque days above by `runcoach-api/tests/test_hrv_unavailable_reason.py`; `research/00` §1.6/§5.4, spec/03 §3.7.4 and `contracts/openapi.yaml` amended to match |
| The cost nobody had measured: **18** silent days after a clean tier change, all of them naming no reset | A clean, gapless, permanent device switch — no gap, no suppression, identical rMSSD on both tiers — reads `hrv_unavailable` on `R+2 .. R+19`, **18 consecutive days**, and reports `reset_reason: null` / `reset_on: null` on every one of them. The mechanism is **not** the row above's: the outgoing tier keeps its own full 60-day baseline, so `established` is **`true`** throughout and `baseline.n` merely decays from 60 — the establishment gate never fires at all. The silence is **week coverage on the tier the athlete has stopped using**, and it is `min_baseline_readings + 7 − min_window_readings` = 14 + 7 − 3 = **18**: the quiet begins at `min_window_readings − 1` = 2, when T125/T132's withhold arms, and ends at `R + min_baseline_readings + 6` = `R+20`, when clause (a)'s candidacy resolves. **Two dependencies, both measured:** it grows with a sparser new tier (**29** days at one capture every second day; the report day is `(min_baseline_readings − 1)·s + 7`), and it is **0** if the old device keeps recording across the boundary — but then the eras interleave past rule 4's density tolerance and `tier_change` is **never** reported, not late but never. The silence and the reset report are bought with each other | The athlete, who switched devices cleanly and is told nothing for 18 mornings by a response whose every exposed field — `established: true`, `baseline.n: 60`, both reset fields `null` — says *nothing happened*; Section 6, which widens its guardrails for 18 days. The direction is **down-regulation on weak evidence, which §1.7 tolerates freely**, so this is not a forbidden-direction flip; it is priced here because it was **unpriced**, and because a tier change requires *buying a device* while a coverage gap requires more than 21 days of no captures at all — the cheaper-to-trigger path was the one nobody measured. **Accepted as measured, not re-opened** (2026-09-17, [[T138]], `behaviour_change: false`). Pinned, derivation-first so that moving `MIN_BASELINE_READINGS` or `MIN_WINDOW_READINGS` reds the figure rather than tracking it ([[T133]]'s precedent), by `test_hrv_trend_reset.test_the_tier_change_silence_is_eighteen_days_and_names_no_reset_on_any_of_them` and its three siblings |
| The reporting lag: for **20** days the response says no reset happened when one did | Through `R+0 .. R+19` `baseline.reset_reason` and `baseline.reset_on` are both `null`; on `R+20` the reset appears carrying a `reset_on` **twenty days older than the day it appears on** (`min_baseline_readings + 7 − 1`). The date is **correct** when it arrives — it names the era's true first day — so this is **latency, not inaccuracy**. On the first two days of the lag the response is not merely silent but *judged*, on the band of a device the athlete has already stopped using; that half has a forbidden direction in it (`hrv_normal` on the outgoing tier's band) and its length is set by when T125/T132's withhold arms, which for a **daily** returner is `R+2` — two mornings. That is the daily case and not the general bound: the count is `min(window_days − min_window_readings, k₃)` (the residual row above, [[T145]] 2026-09-18), so an athlete whose captures are **spread** — 4/wk spread, 3/wk, 2/wk, where `k₃` is 4 or more — is handed that judged verdict on the retired device's band for **four** mornings, while a 4/wk *clustered* return is handed it for two, like the daily one (qualifier restored 2026-09-18, [[T147]]: the axis is the spacing of the captures, not their weekly count), and there the withhold ends none of them — `week_too_thin` does. Since [[T137]] the 18 silent days do carry an `unavailable_reason` — but it is `week_too_thin` on **sixteen** of them, told to an athlete who captured every single morning: true of the resolved tier, false of the athlete. The honest reason would be a seventh cause, *the tier that owns the baseline is not the one you are recording on* | A consumer that polls daily and reads `baseline.reset_reason` to decide whether the baseline moved — it is told "nothing happened" on each of the 20 days; the athlete, on the two judged days, as a verdict computed on a device he has retired. **Named and accepted 2026-09-17 ([[T138]]), where before it was neither named nor accepted.** Accepted *deliberately*, not by default: clause (a) is a **time-accumulating** mechanism and there is nothing to report before it resolves — a reset asserted earlier would be a prediction, and would have to be **withdrawn** on every trial the athlete abandons, which is exactly the phantom-`tier_change`-reported-then-withdrawn defect review cycle 3 removed. Pinned by `test_hrv_trend_reset.test_the_tier_change_delay_reports_the_switch_twenty_days_after_it_happened` |

**Composing the silences — the paragraph that stood here is WITHDRAWN (2026-09-18, [[T141]],
user decision, review cycle 10).** It was added 2026-09-17 by [[T138]] to answer the cycle-9
critic's question — at what point does a rule that mostly says nothing stop being
conservative and start being useless? — by summing this feature's four silences against a
**rate**, publishing the sum as a share of the year, and drawing a normative conclusion from it.
**It is withdrawn, not corrected, and no replacement figure is published.**

*Why it is withdrawn.* Its largest single term priced an athlete swapping one chest strap for
another, and **that is not an event this rule can detect**. The reset keys on
`hrv_source_tier`, whose entire domain is `chest_strap_raw`, `health_snapshot` and
`health_api_overnight`; **there is no notion of device identity anywhere in the rule's
vocabulary**, as `hrv_trend.py`'s own returning-or-brand-new-device note says in as many words.
A same-tier replacement holds the tier constant, fires no `tier_change_reset`, opens no era
boundary, and costs **0** silent days rather than the per-switch figure that term carried. That
per-switch figure was sound arithmetic *for a tier change* at the stated capture density and was
attached to the wrong event; the parenthetical asserting it was measured rather than
extrapolated made the defect worse rather than better, because it had indeed been measured —
for a different event.

*Why withdrawn rather than re-derived.* The user decision was taken against three alternatives:
re-deriving the rate, repairing the arithmetic while keeping the conclusion, and deferring the
whole question to [[IDEA-071]]'s sprint. Any replacement rate would have to assume how often a
real athlete crosses between those three tiers — a judgement about people, published in
`research/00`, the document this feature's costs are priced against and this system's authority
on **measured** facts. That authority should carry no such figure rather than carry one nobody
measured. Nothing is erased: the withdrawn wording is preserved verbatim in [[T141]]'s task file
and in git history, and its two phrasings are declared in
`runcoach-api/tests/support/withdrawn_phrasings.py`, so neither can return to any file the
withdrawn-phrasing walk reads.

**The critic's question is open again, and that is the accepted consequence of this decision
rather than an oversight:** no document in this feature now states at what point a rule that
mostly says nothing stops being conservative and starts being useless. It goes to
[[IDEA-071]]'s sprint — which is re-deriving the rule that produces the silence —
unanswered, and with no figure attached.

**What is not withdrawn.** [[T138]]'s measurement stands entire: the **18** silent days of the
cost row above, their closed form `min_baseline_readings + 7 − min_window_readings`, the
density dependency (**29** days at one capture every second day) and the overlap dependency (**0**
days, and no reset ever reported, if the old device keeps recording across the boundary), the
**20**-day reporting lag of the last row of that table, and all four of the `test_hrv_trend_reset.py` pins
that hold them. Only the composition goes.

**One further correction, independent of the withdrawal.** The struck paragraph priced a layoff
longer than `gap_reset_days` at **22** silent days. 22 is the length of the shortest *resetting*
layoff (`GAP_RESET_DAYS` is 21, so 21 does not reset and 22 does) but it is **not** the length
of its silence: the baseline is untouched by the layoff, and the verdict is withheld only once
the judged week falls below `MIN_WINDOW_READINGS`. On layoff day `k` the week `[D-6, D]` still
holds `7 − k` pre-layoff readings, and `7 − k >= MIN_WINDOW_READINGS` holds for
`k` = 1..4, so **days 1–4 are judged normally** and only days 5–22 are silent:
**18, not 22**, and with the **20** re-establishment days after it **38, not 42**. That 18 is a
*coverage-gap* figure and is **not** the tier change's 18 above — the two mechanisms are
unrelated and the shared length is a coincidence.


## Acceptance Criteria

```gherkin
@must
Scenario: A settled athlete inside the band reads normal
  Given an established baseline of 41 chest-strap readings
  And at least 3 readings in the 7-day window
  When the 7-day rolling mean of ln rMSSD sits inside the SWC band
  Then the verdict is hrv_normal
  And the response reports the mean, both band bounds and the baseline size

@must
Scenario: A mean above the band is also normal
  Given an established baseline and at least 3 readings in the 7-day window
  When the 7-day rolling mean sits strictly above the band's upper bound
  Then the verdict is hrv_normal
  And it is not reported as unavailable

@must
Scenario: A multi-day decline below the band reads suppressed
  Given an established baseline and at least 3 readings in the 7-day window
  When the 7-day rolling mean falls strictly below the band's lower bound
  Then the verdict is hrv_suppressed
  And the response reports how far below the bound the mean sits

@must
Scenario: The band is a dispersion of the log series, not a ratio to its origin
  Given a baseline of ln rMSSD values
  When the band is computed
  Then the half-width is 0.5 times the standard deviation of that baseline
  And expressing the same readings in seconds rather than milliseconds
    produces an identical half-width
  And the band's lower bound is never above its upper bound

@must
Scenario: The baseline window does not overlap the week being judged
  Given a request for local date D
  When the windows are resolved
  Then the 7-day window is the closed local-date interval [D-6, D]
  And the 60-day baseline window is the closed interval [D-66, D-7]
  And no reading contributes to both
  And a suppressed week therefore cannot lower its own band

@must
Scenario: A thin baseline can never produce a suppression verdict
  Given fewer than 14 readings in the baseline window
  When the 7-day mean falls below the band
  Then the verdict is not hrv_suppressed
  And baseline.established is false
  And the response reports the baseline size that fell short

@must
Scenario: A thin baseline can never produce a normal verdict either
  Given fewer than 14 readings in the baseline window
  When the 7-day mean sits inside the band
  Then the verdict is hrv_unavailable
  And the same holds when the mean sits strictly above the band
  And baseline.established is false
  And the band is still reported, because the band is a property of the
    baseline and only the verdict is withheld
  (added 2026-09-15, review cycle 7, T116, closing [[IDEA-062]] on the user
   decision of that date: **a behaviour change**. The criterion above named
   only the suppression and was silent on normal, and the contract table
   inherited that silence -- a week judged against a band built from 2 to 13
   readings emitted hrv_normal, telling Section 6 readiness is intact on a
   baseline the same response called unestablished. That is up-regulation on
   weak evidence, which research/00 §1.7 forbids, and it was reachable after
   every **coverage-gap** reset this feature performs, since a coverage gap
   collapses the baseline deliberately and the athlete then traverses 20
   unestablished days (corrected in place 2026-09-18, T142, following
   research/00 §5.4: this annotation read "after every reset this feature
   performs, since a coverage gap **or a tier change** collapses the baseline"
   until now, and that is false of a tier change — a clean source-tier change
   collapses nothing, `established` stays true and `n` decays 60 → 47, so it
   traverses zero unestablished days and this scenario is unreachable through
   it; its own 18-day silence is week coverage, a different mechanism, T138).
   The gate is now symmetric: hrv_normal requires
   established, exactly as hrv_suppressed already did. Pinned by
   test_hrv_trend_band.test_a_thin_baseline_inside_the_band_is_unavailable_not_normal,
   ..._above_the_band_is_unavailable_too and
   ..._the_establishment_gate_flips_normal_at_exactly_fourteen_readings, and
   by eight rows of that suite's contract table)

@must
Scenario: A baseline too small to have a dispersion asserts no band
  Given fewer than 2 readings in the baseline window
  When the trend is read
  Then no band bounds are asserted
  And the verdict is hrv_unavailable

@must
Scenario: A degenerate dispersion cannot manufacture suppression
  Given a baseline whose readings are so consistent that 0.5 times their
    standard deviation is below the band floor of 0.01
  When the band is computed
  Then the half-width is the floor rather than the computed value
  And band.floored is true

@must
Scenario: The floor does not fire for an ordinary athlete
  Given a baseline with a typical within-athlete spread of ln rMSSD
  When the band is computed
  Then the half-width is the computed value, not the floor
  And band.floored is false

@must
Scenario: Too few readings this week is unavailable, not normal
  Given an established baseline
  But fewer than 3 readings in the 7-day window
  When the trend is read
  Then the verdict is hrv_unavailable
  And the response reports readings_in_window

@must
Scenario: No reading of any tier is unavailable
  Given no resting-HRV reading in the 7-day window
  When the trend is read
  Then the verdict is hrv_unavailable
  And no band is asserted when the baseline holds fewer than 2 readings
  And a band the baseline can build is still reported, as the series scenario requires
    (clarified 2026-09-10, sprint-005 review: the earlier "no band is asserted" contradicted
    "a day with no reading ... still carries its band"; the band is a property of the baseline)

@must
Scenario: The series is filtered to the baseline tier before days are collapsed
  Given a local day carrying both a chest_strap_raw and a health_snapshot capture
  And a baseline established on health_snapshot
  When the day's contribution is resolved
  Then the health_snapshot capture is used
  And the day is not dropped merely because its highest-fidelity capture
    was off the baseline tier

@must
Scenario Outline: One reading per local day, chosen within the baseline tier by time
  Given a baseline established on <tier>
  And a local day carrying <captures>
  When the day's contribution is resolved
  Then the reading used is <chosen>
  And every other capture that day is listed as excluded with a reason

  Examples:
    | tier             | captures                                          | chosen                |
    | chest_strap_raw  | 06:05 chest_strap_raw, 06:12 chest_strap_raw       | 06:05 chest_strap_raw |
    | chest_strap_raw  | 06:12 chest_strap_raw, 07:40 health_snapshot       | 06:12 chest_strap_raw |
    | health_snapshot  | 06:12 chest_strap_raw, 07:40 health_snapshot       | 07:40 health_snapshot |

@must
Scenario: An occasional higher-tier capture does not demote an established baseline
  Given 45 health_snapshot readings sustaining an established baseline
  When the athlete records a single chest_strap_raw capture
  Then the baseline remains on health_snapshot
  And baseline.established stays true
  And the chest-strap reading is corroboration, not a new baseline
  And the same holds for chest_strap_raw captures on fewer than 14 distinct
    local days however many captures they are -- 14 captures on 7 days
    beside a daily snapshot, and a Tuesday/Saturday strap habit that
    re-takes one morning of the judged week, holding 3 captures on 2 days
    there: the strap neither becomes a candidate nor covers the week, a
    genuine snapshot suppression is still reported, and the re-take
    changes nothing but its own excluded[] entry
  And rule 1's recency condition (T117) changes nothing here in either
    direction: the strap is not a candidate at all, so it is never reached,
    and the snapshot is the only candidate, so it is its own recency
    reference and cannot be struck however long the athlete has used it
  (amended 2026-09-15, review cycle 7, T117: stating that the recency
   condition leaves this population alone, so its silence is not read as
   an oversight;
   amended 2026-09-12, review cycle 3, T095: rules 1-3 count distinct local
   days, the unit baseline.n and established report; counted in captures,
   the re-taken morning demoted the established baseline to a strap that
   could not judge the week -- the middle population between "a single
   capture" and "a sustained switch")

@must
Scenario: A higher tier that does not cover the judged week does not own the baseline
  Given a daily health_snapshot baseline of 60 readings
  And 14 chest_strap_raw readings inside the baseline window from a two-week
    trial that ended more than a week ago
  When the trend is read for any day the strap trial is inside [D-66, D-7]
  Then the baseline tier remains health_snapshot
  And a suppressed week on the snapshot series is reported as hrv_suppressed
  And no tier_change reset is reported
  And points[] carries one continuous band across those days
  And a strap holding 3 captures on fewer than 3 distinct local days of the
    judged week does not cover it, nor does one holding 14 captures on 7
    days of the baseline window sustain it -- a day is one day, however
    often it was captured
  And a strap trial that does cover the judged week -- 14 trial days still
    inside [D-66, D-7] plus 3 strap days in [D-6, D] -- does not own the
    baseline either, when its latest baseline-window day falls more than
    recency_tolerance_days (28) behind the snapshot's: on the reproduction
    series the gap is 45 and 46 days on 2026-09-06 and 09-07, the strap is
    struck from the candidate set before week coverage is consulted, and
    the week that used to read hrv_normal against a seven-week-old July
    band now reads hrv_suppressed against the athlete's own
  (amended 2026-09-15, review cycle 7, T117, closing IDEA-064: until then
   this criterion covered only the trial that covers no week, and the
   population beside it -- the same trial plus three strap days -- was the
   stale-candidacy cost the Negative Class priced; the acceptance was
   resolved by change, and the clause above is where the new behaviour is
   asserted;
   amended 2026-09-12, review cycle 3, T095: the middle population -- a
   re-taken morning -- defeated this gate while rules 1-3 counted captures;
   added 2026-09-10, sprint-005 review, T093: a tier that sustains a baseline
   by count but holds fewer than 3 readings in [D-6, D] does not own the
   verdict; amended 2026-09-11, T094: when no sustaining tier covers the week,
   the fallback is the sustaining tier the athlete used last, not the densest)

@must
Scenario: A device return is not judged on the mornings that predate it
  Given a chest_strap_raw baseline captured daily until 2026-07-31
  And a 39-day strap silence carried by a daily health_snapshot to 2026-09-08
  And the strap resuming on 2026-09-09 with four consecutive suppressed
    mornings
  When the trend is read for 2026-09-12, the athlete's fourth morning back
  Then the baseline tier is still health_snapshot with readings_in_window 3 --
    2026-09-06, 09-07 and 09-08 -- and the athlete's four return mornings are
    still excluded off_baseline_tier, because this criterion is about what is
    said and not about what the week is
  And the verdict is hrv_unavailable, not hrv_normal: the strap was struck
    from the candidate set for staleness, it holds min_window_readings (3) or
    more days of the judged week, and every one of them is later than every
    judged-week day of the resolved tier -- so the week is not a fair sample
    of the tier being judged and no verdict of any kind is asserted
  And the same holds with the two tiers exchanged, and whether the return is
    suppressed or healthy: it is the day order that withholds the verdict, not
    the values
  And the athlete's opening mornings back are NOT covered by this: while he
    holds fewer than min_window_readings return days in the judged week the
    verdict there still comes from pre-return readings and still reads
    hrv_normal -- an accepted, priced residual named in the Negative Class,
    closed by no form measured. There are
    min(window_days - min_window_readings, k3) of them, k3 being the offset
    at which his min_window_readings-th distinct return day enters the judged
    week. The axis is the SPACING of his captures, not how many he makes in
    a week: two mornings wherever they run contiguously -- captured daily,
    and on a 4/wk clustered {0,1,2,4} return alike -- and four wherever they
    are spread out, at 4/wk spread {0,2,4,6}, at 3/wk {0,2,4} and at 2/wk
    {0,3} (restated 2026-09-18, review cycle 10, T147, over T145's
    measurement of all 64 weekly return patterns. T145 replaced the bare
    count of two with the closed form, which is right at every density, and
    then glossed that formula as a weekly capture count -- a reading false
    of half the 4/wk athletes, whom the closed form puts with the daily one,
    and the third instance in two days of a bound stated as a property of
    the fixture family it was measured over. Pinned since T147 by
    test_hrv_trend_band.test_the_return_residual_turns_on_capture_spacing_not_weekly_count,
    which walks all five patterns; before it no test seeded any return but a
    contiguous daily one, which is how the gloss got in)
  And on the eighth morning back, when the returning tier owns the week alone,
    the verdict is his own mornings' again -- hrv_suppressed on a suppressed
    return, hrv_normal on a healthy one
  (added 2026-09-16, review cycle 8, T125, following research/00 5.4 -- a
   behaviour change, and a user decision on six measured fix forms. The
   population beside "A higher tier that does not cover the judged week does
   not own the baseline" above: there the struck trial is abandoned and the
   carrier runs daily forever, here the carrier stops because the athlete went
   back to the other device. The criterion above, the Negative Class row it
   priced and the only fixture all said "while a daily snapshot ran
   throughout", which is why six review cycles could not see this one. The
   competing form answered him hrv_suppressed from morning 3 instead and was
   refused on the sweep: it closes the same 36 geometries and creates 54 that
   read hrv_normal against a 36-53-day-old band, in the more ordinary return)

@must
Scenario: A device the athlete has never used before is not judged on the mornings that predate it
  Given athlete_timezone is Pacific/Auckland
  And a chest_strap_raw reading on every local day from 2026-07-04 to
    2026-09-04 at 40.0 ms
  And health_snapshot readings on 2026-09-05, 09-06 and 09-07 at 15.0 ms --
    the athlete's first-ever readings on that tier, a device he bought this
    week, with no health_snapshot reading anywhere before them
  When the trend is read for 2026-09-08
  Then the baseline tier is chest_strap_raw with baseline.n 60 and
    baseline.established true, and readings_in_window is 3 -- the strap
    mornings 2026-09-02, 09-03 and 09-04, every one of them earlier than
    every one of his three new-device mornings, which are excluded
    off_baseline_tier
  And the verdict is hrv_unavailable, not hrv_normal: a tier holding zero
    days of the baseline window, with min_window_readings (3) or more days
    of the judged week every one of which is later than every judged-week
    day of the resolved tier, is a device the athlete has never used before,
    and the week is therefore not a fair sample of the tier being judged, so
    no verdict of any kind is asserted
  And nothing but the verdict moves: the tier, the baseline window,
    baseline.n, the week's mean and the band are what they were and are
    still reported, exactly as in the device-return criterion above
  And rule 1's recency condition (T117) cannot reach this population in
    either direction: it strikes stale *candidates*, and a candidate needs
    14 distinct days in the baseline window, which a tier whose first-ever
    reading falls inside the judged week does not have -- so it is never
    struck, however cleanly its days are ordered after the resolved tier's
  (added 2026-09-18, review cycle 9, T135, stating a behaviour that shipped
   at T132 (2026-09-16, form B, a user decision on three measured widening
   forms) with tests and prose and no criterion. It is the population beside
   "A device return is not judged on the mornings that predate it" above:
   there the tier the athlete has gone back to holds *some* baseline-window
   history -- an abandoned trial or a genuine return -- which is what the
   recency gate measures staleness against; here there is none to measure.
   Zero baseline-window days is not a tuned threshold, it is the only value
   that means "never used". Pinned since T132 by
   test_hrv_trend_band.test_the_withhold_reaches_a_never_used_tier_bought_this_week;
   this criterion adds no behaviour and changes none)

@must
Scenario: Two mornings of silence is the priced cost of that rule, at contiguous capture
  Given a chest_strap_raw baseline captured daily to a day T
  And a daily health_snapshot from T+1 with no strap reading after T
  And every reading of both eras at the same healthy 40.0 ms -- an athlete
    who has simply changed devices for good, with nothing wrong with him
  When the trend is read for each morning after the switch
  Then his first and second mornings still read hrv_normal: one and two days
    of the new tier are fewer than min_window_readings (3), so the criterion
    above does not reach them and the verdict still comes from pre-switch
    readings
  And his third and fourth mornings read hrv_unavailable, where before T132
    they read hrv_normal -- for exactly those two days a permanent,
    legitimate switch is the same shape as a device that will go unused
    again: an un-established tier holding 3 or more judged-week days, every
    one later than the resolved tier's, and nothing in a single week's shape
    tells the two apart
  And from his fifth morning the verdict is unavailable in any case, and
    names week_too_thin rather than the week_not_representative of the two
    mornings above, because the strap then holds fewer than 3 days of the
    judged week and that guard precedes the withhold in judge's fixed order
    (reworded 2026-09-18, review cycle 10, T144: this clause used to say the
    week was withheld here, written before T137 gave that word a published
    meaning -- HrvSeries.withheld, reported as week_not_representative -- so
    a reader taking the scenario as the contract would have asserted the
    wrong enum member on those days. Measured: week_not_representative on
    the third and fourth mornings, week_too_thin from the fifth, which is
    the split test_hrv_trend_reset pins at R+2/R+3 and R+4.. since T143.
    Nothing else in this scenario changed)
  And on T+21, when the snapshot holds 14 days of the baseline window and
    covers the week, it owns the baseline with baseline.reset_reason
    tier_change and reset_on T+1, and the verdict is his own mornings' again
  And this is the direction research/00 1.7 tolerates freely -- staying
    silent on weak evidence rather than reporting readiness on it -- and it
    is an accepted, priced cost rather than a defect: two mornings of
    silence per permanent device switch whose captures run contiguously --
    daily, and a 4/wk clustered {0,1,2,4} switch alike -- named in the
    Negative Class, and zero days attributable to this rule wherever they
    are spread out (4/wk spread, 3/wk, 2/wk), where the withhold flips only
    on days week_too_thin already decides (T145, 2026-09-18; the density
    qualifier corrected 2026-09-18 by T147, review cycle 10, which found it
    glossed as a count of captures per week when the measured axis is their
    spacing, and pinned all five patterns in
    test_hrv_trend_band.test_the_return_residual_turns_on_capture_spacing_not_weekly_count).
    What
    separates the two shapes in general is tier_change_reset, which
    accumulates 14 baseline-window days of the new tier before handing the
    baseline over; no predicate over one week's shape can do what a
    time-accumulating mechanism is for
  (added 2026-09-18, review cycle 9, T135. The cost was measured and decided
   at T132 and is pinned in
   test_hrv_trend_reset.test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline,
   the walk that runs T+1 .. T+114 and, since T127, asserts a verdict at
   every one of its targets -- the criteria named it nowhere, which is the
   half of the trade a reader of this block could not see)

@must
Scenario: The fallback keeps the device the athlete used last
  Given a daily health_snapshot baseline to D-26
  And a daily chest_strap_raw series from D-25 to D-8 and nothing after
  When the trend is read for every day from D-5 to D
  Then the baseline tier is chest_strap_raw on every one of those days
  And baseline.reset_reason is tier_change with the same reset_on on every day
  And from D-3, when no tier covers the week, the week reads hrv_unavailable
    with readings_in_window 2, 1, 0, 0 on D-3, D-2, D-1 and D — the last
    strap reading, D-8, leaves the sliding week on D-1
  And no day hands the baseline back to health_snapshot
  And rule 1's recency condition (T117) strikes neither tier on any of
    those days: the snapshot's last baseline-window day is D-26 throughout
    while the strap's slides from D-12 at D-5 to D-8 at D, so the gap runs
    14 to 18 days, inside recency_tolerance_days (28) -- a margin measured
    on 2026-09-15, not a guarantee; the band measurement recorded in
    research/00 5.4 is where the margin is actually exercised, its lower
    bracket 18 being the clean-ended-trial series in the reset suite
  (added 2026-09-11, sprint-005 review cycle 2, T094: among the tiers that
   sustain the clipped baseline window, the one whose latest reading there is
   most recent takes a week no tier covers — ties by count, then fidelity; a
   window with no sustaining tier still falls to the densest tier)

@must
Scenario: Tier eras do not interleave, and a reset names the era's true first day
  Given a health_snapshot baseline in the previous window [D-126, D-67]
  And, inside the current baseline window, chest_strap_raw readings on two
    or three days of every week among daily health_snapshot readings
  When the trend is read for a day the strap covers
  Then the baseline tier is chest_strap_raw
  And baseline.reset_reason is null, because strap readings fall between the
    snapshot's first and last reading over both windows
  Given instead a daily health_snapshot throughout both windows and a
    three-week chest_strap_raw trial that now sits wholly inside [D-126, D-67]
  When the trend is read for any day the trial holds 14 or more readings there
  Then the baseline tier is health_snapshot and baseline.reset_reason is null,
    because the snapshot's readings run through the trial's
  Given instead a chest_strap_raw baseline to day T and daily health_snapshot
    readings from T+1 with no strap reading after T
  When the trend is read for T+21
  Then the baseline tier is health_snapshot with baseline.reset_reason
    tier_change and baseline.reset_on T+1
  And that reset_on is reported unchanged on every later day of the era,
    including days on which T+1 is earlier than D-66, until the previous
    window [D-126, D-67] is no longer sustained by the strap — on T+113 it
    still holds 14 strap readings and the reset is reported; on T+114 it
    holds 13 and nothing is (clause (b) is the binding one on *this* series
    only, where (a) and the week half hold throughout; liveness is rule 4's
    three conditions together — T109, noted here 2026-09-15 by T111)
  Given instead a daily health_snapshot to SW, a daily chest_strap_raw from
    SW+1, and one strap capture eleven days before SW
  When the trend is read for any day the strap owns the baseline
  Then baseline.reset_reason is tier_change with reset_on SW+1 on every one
    of them -- the isolated capture is corroboration, not the era's start
  And the tolerance's boundary is density, not count: readings on the
    wrong side of the era boundary, of either tier together, are
    corroboration while they are fewer than 14 distinct local days and
    fewer than 3 in the judged week -- 13 stray days of the new tier inside
    the old era are tolerated and 14 are an era; two old-tier captures in
    the judged week after a switch are tolerated and three are use
  And the two halves of that tolerance reach different things (amended
    2026-09-13, review cycle 5, T102; decision log D5): the week half --
    fewer than 3 stray days in the judged week -- decides the report and,
    as the first ordering term in the era-boundary selection, which of
    several admitted boundaries is taken, hence where the baseline is
    clipped and where the band sits; no *reported* band or verdict moves
    with it, because an isolated boundary is always a candidate and so is
    still the one chosen for any series this engine can be handed
    (corrected 2026-09-13, review cycle 6, T106); the candidacy half
    -- fewer than 14 distinct local days of strays, both tiers' pooled into
    one count -- decides whether an era boundary exists at all and hence
    whether the baseline is clipped, so crossing it moves the band and can
    flip the verdict hrv_suppressed -> hrv_normal on an unchanged week
    mean: 13 stray days clip the baseline at the switch (n 33, band.lo
    3.6789, suppressed on the series above at 2026-09-07) and 14 leave it
    unclipped with the new tier's pre-switch trial inside the band (n 43,
    band.lo 3.4791, normal; 7-day mean 3.4965 on both), and an old-tier
    capture after the switch, outside the judged week and contributing
    nothing to the week mean, is what can supply the 14th day -- an
    accepted, priced cost, named in the Negative Class
  And a previous-tier capture at the very instant of the new tier's first
    is still interleaved
  (amended 2026-09-12, review cycle 3, T095: judged exactly, the stray
   silenced the reset for the whole era, as did one old-tier capture after
   the switch and a two-week trial of the new tier three months before it;
   the density tolerance closes all three as one decision)
  (added 2026-09-11, sprint-005 review cycle 2, T094: tier_change is asserted
   only when the resolved tier sustains the window, differs from the tier the
   previous window sustains, and the eras do not interleave; reset_on is the
   resolved tier's first reading over both windows after the previous tier's
   last reading there, so it does not slide and an older era of the same tier
   is not mistaken for this one. Clarified 2026-09-11, review cycle 3: the
   interleaving is judged over both windows together — no reading of the
   resolved tier between the previous tier's first and last, since T095 none beyond
   the density tolerance — not on the
   current window alone, where a finished trial in the previous window passed
   vacuously; and the report is live only while (a), (b) and the week half all
   hold together — any one can lapse on its own and the reset is not reported
   that day (T109; qualified 2026-09-15, T111, which found this clause still
   reading the (b) route as the whole lifetime). Clause (b) lapses when the
   previous window stops being sustained by a tier other than the resolved
   one, which is when the new tier reaches 14
   there for a forward switch (S+80 for a daily device) and when the old strap
   drops below 14 there for the reverse one (T+114), since rule 1 picks the
   highest-fidelity tier with 14 — "once the previous window holds 14 of the
   new tier" was the forward case only; those dates bound the report, they do
   not promise it)

@must
Scenario: A sustained source-tier change re-establishes the baseline
  Given a baseline built on health_snapshot readings
  When chest_strap_raw readings become dense enough to sustain a baseline
  Then a fresh baseline is begun for the new tier
  And nothing is asserted meanwhile -- neither the suppression nor the
    normal -- and what silences those days is the outgoing tier's judged-week
    coverage, not a baseline waiting to be established:
    week_not_representative on the third and fourth mornings, then
    week_too_thin from the fifth until the new tier owns the baseline, 18
    silent days in all (restated 2026-09-18, review cycle 10, T147. This
    clause used to attribute the silence to the establishment gate instead.
    Measured over a clean permanent switch, k = 0..39: `established` is TRUE
    on every day of it -- the outgoing tier keeps its own baseline, n
    decaying 60 -> 47, and on the first day the new tier owns the baseline it
    holds exactly min_baseline_readings and answers at once -- so the gate
    that phrasing named cannot fire under this Given at all, because rule
    4(a) will not report the change until the resolved tier has held
    min_baseline_readings distinct baseline-window days. The clause was true
    only vacuously, and read as a contract it promised the quiet would end
    when a flag flipped that never flips. The establishment gate itself is
    real, symmetric since T116 and unchanged here; the scenario whose Given
    does reach it is the thin-baseline one above, and the pre-T116 asymmetric
    phrasing this criterion carried until 2026-09-15 (`acfebae`) remains
    withdrawn as `VERDICT_WITHDRAWN` entry 4. Mechanism measured at T138,
    split by cause at T143, and pinned since T147 by the judge walk in
    test_hrv_trend_reset.test_a_sustained_tier_change_starts_a_fresh_baseline_and_week_coverage_quiets_the_switch,
    which asserts `established` true on every day of the switch and
    partitions the quiet by unavailable_reason; before it that test never
    called judge at all)
  And the change is not reported as a physiological HRV drop
  And the same holds in the other direction, when an owning chest-strap
    baseline is abandoned for daily health_snapshot readings
  (amended 2026-09-11, T094: both directions; the reverse one reported its
   reset a month late under the cycle-1 rule)

@must
Scenario: A coverage gap re-establishes the baseline even with no tier change
  Given a baseline built on chest_strap_raw readings
  And more than 21 local days with no entry in the post-exclusion series
  When readings resume on the same tier
  Then a fresh baseline is begun from the resumption
  And baseline.reset_reason is coverage_gap
  And readings from before the gap do not contribute to the band
  And this holds however long the layoff: a 120-day silence resets exactly
    as a 60-day one does, and a genuinely new athlete's first capture,
    with no earlier reading of any tier in the store, resets nothing
  (implemented as written 2026-09-12, T096, review cycle 3 G13: the
   layoff is told from a new athlete by the store's earliest reading --
   one scalar read beside the rows -- not by whether the last pre-gap
   reading happened to fall inside the 126 days the route reads; before
   it, 87- and 120-day layoffs reported no gap at all)

@must
Scenario: A gap spanned only by excluded rows still counts as a gap
  Given 25 consecutive local days whose only captures are pre-amendment
    or null-tier rows -- stored rows that are not readings of any tier
    (clarified 2026-09-10, sprint-005 review: the gap is measured on the
    post-exclusion, pre-filter readings of every tier, so days holding only
    off-baseline-tier readings are not a gap -- the athlete kept capturing;
    construction reference "Reset triggers", IDEA-046 row 5)
  When the gap is measured
  Then those days count as absent
  And the baseline is re-established on resumption

@must
Scenario: Changing the configured timezone re-buckets the whole history under the new zone
  Given readings stored under one athlete_timezone
  When athlete_timezone is changed to a zone with a different UTC offset
  And the trend is read
  Then every reading is bucketed into local days of the new zone
  And the response reports the timezone it used
  And no verdict, reset or comparison is asserted about the change itself
  And a capture near local midnight may therefore move to an adjacent day,
    which is the new zone's correct day, not a physiological HRV event

@must
Scenario: The pre-amendment window is excluded from the readiness read
  Given stored rows where hrv_source_tier is not null and resting_rmssd_ms is null
  When the trend is computed
  Then those rows contribute to neither the mean nor the baseline
  And ln is never evaluated on a null or non-positive value
  And each is listed as excluded naming the pre-amendment window

@must
Scenario: A capture that failed F004's quality gates is already absent
  Given a capture whose rr_valid_fraction fell below the artefact-filter threshold
  When the trend is computed
  Then the row carries no hrv_source_tier and no resting_rmssd_ms
  And it is absent from the series without this feature re-applying the gate
  And this feature does not re-gate what ingestion already discharged

@must
Scenario: Days are the athlete's local days, not UTC days
  Given athlete_timezone is Pacific/Auckland
  And a capture stored as 2026-01-07T17:00:00+00:00
  When the series is bucketed
  Then the capture belongs to local day 2026-01-08
  And the stored offset form is parsed rather than matched against a "Z" suffix
  And the 7-day window and the 21-day gap are measured in those local days

@must
Scenario: A missing timezone is a startup error, not a silent default
  Given an api.toml with no athlete_timezone
  When the server starts
  Then startup fails with a validation error naming the field
  And the message shows the one-line fix
  And no request is served with an assumed zone

@must
Scenario: An invalid timezone fails at load, not per request
  Given athlete_timezone is not a recognised IANA zone
  When configuration is loaded
  Then it is rejected at load time
  And no request reaches the endpoint to fail with a 500

@must
Scenario: A future date asserts no verdict
  Given a request whose date is after the athlete's local today, resolved
    once per request from the route's own clock in the configured timezone
  When the trend is read
  Then the verdict is hrv_unavailable
  And no suppression is asserted about a day that has not happened
  (Given corrected 2026-09-18, review cycle 10, T139. The phrasing it
   replaces keyed the comparison on the athlete's last stored capture
   rather than on the route's clock, which states the weaker, pre-withhold
   rule -- the one found insufficient and replaced. It is weaker because a
   judged window is seven days wide: for any to up to four days past the
   last capture the window [to-6, to] still holds min_window_readings (3)
   or more readings on an intact baseline, so the clock-free rule asserted
   hrv_suppressed about a day that had not happened (sprint-005 review M1,
   which the only future-date test of the day could not see -- it used
   D + 400, where every row is outside_windows and the verdict is
   unavailable for an unrelated reason). The code was right and the
   criterion wrong, so no behaviour moves here: main._withhold_future
   compares each judged day against _today_in(zone), for to and for every
   points[] day alike, and the pure module stays clock-free. Pinned by
   test_hrv_trend_endpoint.test_a_to_a_few_days_ahead_with_a_full_window_asserts_no_verdict
   and, per point, by
   test_hrv_trend_points.test_a_range_ending_after_today_carries_no_verdict_past_today.
   Every other site stating this rule -- spec/02, spec/03 3.7.4, spec/06
   6.2.4, the Negative Class row above and contracts/openapi.yaml -- already
   named the athlete's local today; this criterion was the last survivor)

@must
Scenario: A date before any history asserts no verdict
  Given a request whose date precedes the earliest stored capture
  When the trend is read
  Then the verdict is hrv_unavailable
  And baseline.established is false

@must
Scenario: An unavailable verdict names which of the six causes withheld it
  Given any judged local day
  When the trend is read
  Then the verdict block carries unavailable_reason beside verdict, null
    whenever a verdict is asserted -- on hrv_normal and hrv_suppressed
    alike -- and non-null exactly when the verdict is hrv_unavailable, a
    biconditional no response breaks in either direction
  And its value is one of exactly six, and no response carries a seventh:
    no_tier_sustains_a_trend when no resting-HRV reading of any tier can
    sustain a trend at all; no_band when a tier did resolve but its
    baseline window holds fewer than two readings, so no band can be built;
    week_too_thin when the judged week holds fewer than
    min_window_readings (3); week_not_representative when the week is not a
    fair sample of the resolved tier (T125/T132); baseline_unestablished
    when the baseline holds fewer than min_baseline_readings (14); and
    day_not_happened when the judged day is after the athlete's local today
  And where more than one of judge's four internal causes holds at once,
    the field names the first to fire in the fixed order judge already
    evaluates them -- no band, then a too-thin week, then an
    unrepresentative week, then an unestablished baseline -- so a week that
    is both withheld and built on an unestablished baseline reports
    week_not_representative, never baseline_unestablished
  And the structural cause is separated from a merely-thin baseline by name
    only: both leave no band, and it is no_tier_sustains_a_trend exactly
    when no tier resolved at all, no_band when one did
  And a day after the athlete's local today reports day_not_happened
    whatever the pure rule decided -- including a day whose fields would
    otherwise have carried hrv_normal or hrv_suppressed, and a day for
    which one of the other five also holds -- because the route's withhold
    overrides the reason as well as the verdict: everything that would have
    explained those causes is still computed and reported as usual for a
    near-future day, so the only claim true of that response is that the
    day has not happened yet
  (added 2026-09-18, review cycle 10, T139, stating a published contract
   change [[T137]] shipped on 2026-09-17 with tests, prose, an amended
   Negative Class row above and an amended contracts/openapi.yaml, and no
   criterion -- exactly the position T132 was in when T135 was filed one
   task later, and for the same reason: behaviour that lives only in prose
   and tests is what a reader taking this block as the contract will
   simplify away. The six causes were already covered here at the
   behaviour level; what was missing is T137's field, its members and its
   precedence. This criterion adds no behaviour and changes none. Pinned by
   test_hrv_unavailable_reason.py throughout --
   test_unavailable_reason_is_null_whenever_the_verdict_is_asserted and
   test_unavailable_reason_is_set_exactly_when_the_verdict_is_withheld_across_the_switch_walk
   for the biconditional,
   test_the_six_unavailable_reason_names_are_the_same_six_in_the_module_the_schema_and_the_contract
   and test_the_schema_refuses_an_unavailable_reason_outside_the_six for
   the closed six,
   test_unavailable_reason_reports_the_withheld_week_before_an_unestablished_baseline
   and test_unavailable_reason_separates_the_opaque_withhold_days_from_the_thin_window_day
   for the precedence,
   test_unavailable_reason_no_tier_when_the_store_holds_no_reading_at_all
   and test_unavailable_reason_no_band_when_a_resolved_tier_has_fewer_than_two_baseline_readings
   for the two that share judge's first guard, and
   test_hrv_trend_endpoint.test_a_to_a_few_days_ahead_with_a_full_window_asserts_no_verdict
   for the route's override on days the pure rule would have judged. The
   biconditional is additionally structural since [[T144]]: HrvVerdict
   refuses construction with the two fields disagreeing. This criterion
   states the response contract, which that guard enforces from inside)

@must
Scenario: The contract's series is served alongside the verdict
  Given readings on every local day from D-6 to D
  When GET /metrics/hrv is read with from=D-6 and to=D
  Then points has exactly 7 entries, one per local day, in date order
  And each point's swc_low and swc_high come from that day's own baseline [d-66, d-7]
  And the last point's band equals the verdict's band
  And a day with no reading has ln_rmssd null but still carries its band
  And a day for which no band is asserted has baseline, swc_low and swc_high null

@must
Scenario: Both range parameters are optional and from cannot follow to
  Given no query parameters
  When GET /metrics/hrv is read
  Then to is the athlete's local today, from equals to, and points has one entry
  When it is read with from after to
  Then the response is 422 and names both parameters

@must
Scenario: The as-built endpoint matches the contract it implements
  Given contracts/openapi.yaml marks GET /metrics/hrv x-readiness implemented
  And from and to are optional in the contract
  When contracts/check_drift.py runs against the as-built /openapi.json
  Then it reports no drift
  And HrvTrend in the contract carries the verdict, band, baseline, window,
    included, excluded and thresholds blocks additively

@must
Scenario: The domain spec, the project rule and the authority are reconciled with what this feature computes
  Given section 3.7 names rmssd_precomputed as the trend input
  And section 3.7.3 defines the band as 0.5 times the CV of ln rMSSD
  And research/00, research/05, spec_outline, spec/06 and the project rule
    all state the band as 0.5 times CV
  When this feature is delivered
  Then 3.7.1 and 3.7.2 state resting_rmssd_ms as the input for both tiers
  And 3.7.3 and the 3.10 roll-up state the band as 0.5 times the sample
    standard deviation of ln rMSSD
  And 3.7.4's degradation is scoped to tier level, not per-day inside a
    live baseline, consistent with 3.7.3's single-tier band
  And section 2's trend-input sentences are corrected with it
  And research/00 carries the corrected form as a stated clarification of its
    own register row, naming that 0.5 times CV conflated the SWC with
    Plews's separate rolling-mean CV metric, so precedence is not inverted
  And every file under specification/ and .claude/rules that states the band
    also carries the corrected form, verified by a property sweep, not by
    the absence of one string

## Technical Notes

The construction — band math, series building, tier and gap rules, degenerate-input handling and the
required adversarial-probe table — is specified in
`spec/references/F005-trend-construction.md`. Decisions and their rationale are in
`spec/references/F005-decision-log.md`.

**Verified during sprint-005 planning (2026-09-09, at `8f2f6f5`; confidence HIGH unless noted):**

- The band uses **sample SD** (`statistics.stdev`, n−1). There is no existing `stdev` call in the
  codebase (`rr_reconstruction.py` uses `median` only). `stdev` raises below two readings, which is
  the "no band under 2 readings" scenario surfacing structurally.
- **Amendment surface:** 21 live hits in 8 files, table in the construction reference. The sweep
  probe must cover all of `specification/` (not only `specification/spec/`) and `.claude/rules/`.
- **Config blast radius: 35 edit points** — 9 `AppConfig(...)` constructor sites (the autouse
  fixture at `tests/conftest.py:53-55` breaks all 853 API tests until repaired), 4 TOML-writing
  helpers, 11 `init_cmd.main([...])` argv lists, 4 dict-equality assertions, 4 field-order
  assertions, and the F001/F003/F004 demo probes (which read the machine's real `api.toml`, so
  they need `RUNCOACH_ATHLETE_TIMEZONE` exported). `CHANGELOG.md` gains a migration section.
- **No tzdb at HEAD:** Python 3.12.2 has 0 available zones; `tzdata` is absent from every
  pyproject and `uv.lock`. `ZoneInfoNotFoundError` subclasses `KeyError` and fires identically for
  "bad zone" and "no tzdb"; the validator discriminates on `available_timezones()` being empty.
- **Bucketing:** rows are stored `+00:00` (`mapping.py:512`); parse with `fromisoformat` and convert
  `per row` with `astimezone(zone).date()` — measured DST cases: 2026-04-05 and 2026-09-27 in
  Pacific/Auckland. Guard a naive value (would silently assume the machine zone).
- **Zone reads go through `db._load_config_cached()`**, which the autouse fixture clears per test;
  the timezone-change scenario is driven by re-monkeypatching and `cache_clear()`, as
  `declared_config` does. The route resolves the `ZoneInfo` and passes it down; the metrics module
  stays pure (`pipeline.py:62-68`).
- `health_api_overnight` is in the tier enum but never written by the classifier; tier ordering
  must still rank it (MEDIUM — no fixture exercises it).
- Contract: `check_drift.py` is untracked in the working tree at the time of writing, and the
  workflow file sits under `contracts/` rather than `.github/workflows/`. Both must be committed
  before a worktree-isolated builder can run the drift check.
