---
id: "F006"
title: "Per-Tier Resting-HRV Datasets"
type: feature
epic: "E003"
status: approved
story_points: 15
complexity: "high"
token_estimate: 38000
rice_reach: 1
rice_impact: 3
rice_confidence: 0.8
rice_effort: 5.0
rice_score: 0.48
feasibility: 0.8
dependencies: ["F004", "F005"]
references: ["spec/references/F006-dataset-model.md", "spec/references/T125-fix-form-measurements.md", "spec/references/T130-overlap-sweep-harness.py"]
children: []
tasks: ["T124", "T149", "T150", "T151", "T152", "T153", "T154", "T155", "T156", "T157", "T158", "T159", "T160", "T161", "T162", "T163", "T164"]
created: 2026-09-18
updated: 2026-09-21
source_idea: "IDEA-071"
demo_probe: |
  set -e
  # The cross-task flow, end to end through the endpoint: a two-tier resting-HRV history is
  # ingested, build_series gives each tier its own dataset with its own baseline and band,
  # select_dataset promotes the highest-fidelity judgeable one, judge returns the verdict,
  # and the route renders datasets[], selected_dataset, selected_reason, disagreed_with and
  # the dataset each point's band came from. Every assertion is on the served body, and the
  # slice each one compares is printed before it.
  export RUNCOACH_DATA_DIR=$(mktemp -d)
  export RUNCOACH_RESTING_HRV_PROFILE_NAMES='["HRV Snapshot"]'
  export RUNCOACH_ATHLETE_TIMEZONE=UTC
  trap "rm -rf $RUNCOACH_DATA_DIR" EXIT
  # The port is taken from the OS, not hardcoded (sprint-006 final spec review). A fixed
  # 8131 fails this feature's smoke test whenever something else holds that port -- a
  # failure about the machine, not about the feature. Each candidate is one the kernel
  # just handed out (bind :0, read the number, release it) and a candidate whose server
  # never answers /health is killed and the next tried, which also covers the race
  # between releasing the probe socket and uvicorn binding it. Every request below reads
  # $PORT, so nothing this probe asserts changes.
  PORT=""
  for attempt in 1 2 3 4 5; do
    CANDIDATE=$(uv run --package runcoach-api python -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
    uv run --package runcoach-api uvicorn runcoach_api.main:app --host 127.0.0.1 --port $CANDIDATE --app-dir runcoach-api/src &
    PID=$!
    trap "kill $PID 2>/dev/null; rm -rf $RUNCOACH_DATA_DIR" EXIT
    for i in $(seq 1 40); do
      curl -fsS "http://127.0.0.1:$CANDIDATE/health" -o /dev/null 2>/dev/null && PORT=$CANDIDATE && break
      kill -0 $PID 2>/dev/null || break
      sleep 0.5
    done
    if [ -n "$PORT" ]; then break; fi
    echo "port $CANDIDATE did not come up, trying another (attempt $attempt)"
    kill $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true
  done
  if [ -z "$PORT" ]; then echo "no usable port after 5 attempts"; exit 2; fi
  echo "serving on 127.0.0.1:$PORT"
  # The history is seeded through the suite's own generator -- the real
  # mapping -> classify -> db.persist chain, never raw SQL -- so the probe drives the same
  # seam test_hrv_trend_endpoint.py drives. The script prints the RESOLVED data dir and
  # exits 2 before its first write if it is not the one asked for.
  #   health_snapshot  daily 2026-05-11..2026-09-07, none suppressed
  #   chest_strap_raw  daily 2026-08-09..2026-09-07 at another wall-clock hour, so two tiers
  #                    captured on one morning do not share a session_id, last five suppressed
  # The snapshot holds 60 distinct local days of [D-66, D-7] and the strap 23, so neither
  # dataset can be a copy of the other. --print-expected states each band from the
  # generator's own values with sample SD, before the request, and it is never read back
  # from the response (IDEA-057).
  EXPECTED="$RUNCOACH_DATA_DIR/expected.json"
  uv run --package runcoach-api python runcoach-api/tests/support/seed_hrv_series.py \
    --data-dir "$RUNCOACH_DATA_DIR" --print-expected \
    --era health_snapshot:2026-09-07:120:0:7 --era chest_strap_raw:2026-09-07:30:5:6 > "$EXPECTED"
  jq -c '.eras[] | {tier, n, first_day, band_lo, band_hi}' "$EXPECTED"
  BODY=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?to=2026-09-07")
  # 1. Both tiers are in datasets[] in fidelity order, each carrying its own n, its own
  #    established and its own band -- and each band is the one computed for that era alone.
  echo "$BODY" | jq -c '.datasets[] | {tier, n, established, fidelity_rank, last_read, week_days, below, band_lo: .band.lo}'
  echo "$BODY" | jq -e --slurpfile e "$EXPECTED" '
    ([.datasets[].tier] == ["chest_strap_raw", "health_snapshot"])
    and (.datasets[0] | .n == 23 and .established == true and .fidelity_rank == 0
         and .week_days == 7 and .below == true
         and (.band.lo - $e[0].eras[1].band_lo | fabs) < 0.000001)
    and (.datasets[1] | .n == 60 and .established == true and .fidelity_rank == 1
         and .week_days == 7 and .below == false
         and (.band.lo - $e[0].eras[0].band_lo | fabs) < 0.000001)'
  # 2. The selection, its reason, the verdict, and who dissented. The snapshot reads within
  #    its own band while the selected strap reads below its own, so it is named -- and the
  #    naming changes nothing: hrv_status is the selected dataset's verdict (AC11).
  echo "$BODY" | jq -c '{verdict, unavailable_reason, selected_dataset, selected_reason, disagreed_with, baseline, band_lo: .band.lo}'
  echo "$BODY" | jq -e '
    .selected_dataset == "chest_strap_raw" and .selected_reason == "highest_fidelity_judgeable"
    and .verdict == "hrv_suppressed" and .unavailable_reason == null
    and .disagreed_with == [{"dataset": "health_snapshot", "week_days": 7}]
    and .baseline.tier == .selected_dataset and .baseline.n == .datasets[0].n
    and .band == .datasets[0].band
    and .datasets[1].band.lo != .datasets[0].band.lo
    and ([.excluded[] | select(.reason == null)] | length) == 0'
  # 3. Per-point dataset identity (AC14). Over 2026-08-25..2026-09-07 the strap crosses
  #    MIN_BASELINE_READINGS inside [d-66, d-7] on 2026-08-29 exactly, so every earlier day
  #    names the snapshot and every later day names the strap -- the instrument switch a
  #    chart would otherwise make silently. The last point agrees with the verdict's own
  #    dataset and its own band.
  RANGE=$(curl -fsS "http://127.0.0.1:$PORT/metrics/hrv?from=2026-08-25&to=2026-09-07")
  echo "$RANGE" | jq -c '[.points[] | {date, dataset}]'
  echo "$RANGE" | jq -e '
    (.points | length) == 14
    and ([.points[] | select(.dataset == null)] | length) == 0
    and ([.points[] | select(.date < "2026-08-29") | .dataset] | unique) == ["health_snapshot"]
    and ([.points[] | select(.date >= "2026-08-29") | .dataset] | unique) == ["chest_strap_raw"]
    and .points[-1].dataset == .selected_dataset and .points[-1].swc_low == .band.lo'
  # 4. The same response block asserted by the suite, so the flow above is not its only
  #    witness. A -k that selects nothing exits 0, so a non-zero passed count is required
  #    rather than an exit code, and a failed or errored count is required to be absent from
  #    the whole captured run rather than from any one line of it.
  K='both_tiers_are_rendered or dataset_with_no_band_is_visible or selected_reason_is_null_exactly or disagreed_with_names_the_dissenter or carries_its_own_reported_reset or points_name_the_dataset_each_days_band or schema_and_the_contract_both_publish'
  OUT="$RUNCOACH_DATA_DIR/pytest.out"
  uv run --package runcoach-api pytest runcoach-api/tests/test_hrv_trend_endpoint.py -q -p no:cacheprovider -k "$K" >"$OUT" 2>&1
  tail -n 1 "$OUT"
  grep -qE "[1-9][0-9]* passed" "$OUT"
  ! grep -qE "[0-9]+ (failed|error)" "$OUT"
  echo "F006 demo probe: OK"
---

# Per-Tier Resting-HRV Datasets

## User Story

As an athlete who records resting HRV on more than one source — a chest strap some mornings, the watch on
others — I want each source to keep its own baseline and band, so the verdict I am given is computed against
the instrument that produced this week's readings, not whichever source won a single arbitration.

## Why This Matters

One tier wins the baseline window today and every other tier's readings become `off_baseline_tier`,
discarded for the verdict. Because one tier owns the only band, the rule must answer *"which one band do I
judge this week against?"* — and nine cycles of F005 each added a qualifier to close one population of it
while **no condition was ever removed**; [[T130]] measured that family exhausted over 12,300 rows. When each
source keeps its own band, the **cross-tier** arbitration has nothing left to arbitrate. Fidelity-first is
externally corroborated: ~2.16% rMSSD error for chest straps against ECG versus ~17.49% for PPG, an ~8×
difference.

**What it does not do.** Per-tier baselining does not answer IDEA-071's missing model — *"does this week's
reading represent the athlete now?"* — it only makes it **askable per dataset**. AC6 and AC17 carry that
question; a first draft called them subsumed and was **measurably wrong** (reference §9).

## Acceptance Criteria

**AC1 — each tier keeps its own baseline and band.** *Given* 41 `chest_strap_raw` and 57 `health_snapshot`
distinct local days in `[D-66, D-7]`, *when* the trend is computed for `D`, *then* each tier has its own
baseline mean, SD, `n` and band, and no reading of one tier contributes to another's (§3.7.3 anti-mixing,
honoured by construction).

**AC2 — the losers are retained.** *Given* two tiers each holding at least `MIN_BASELINE_READINGS`, *when*
one is selected, *then* the other's band, `n` and `established` are still computed and emitted.

**AC3 — a morning with two captures feeds both datasets.** *Given* a local day carrying a `chest_strap_raw`
capture at 07:00 and a `health_snapshot` at 07:05, *when* datasets are built, *then* that day contributes
one reading to each and neither is excluded as `off_baseline_tier`.

**AC4 — per-day collapse still applies within a dataset.** *Given* two `chest_strap_raw` captures on one
local day, *then* only the later is kept (`same_day_later_capture`) and the day counts once. Every count
here is in **distinct local days** (T095, IDEA-047).

**AC5 — selection promotes the highest fidelity judgeable dataset.** *Given* `chest_strap_raw` is judgeable
and last read 2 days ago and `health_snapshot` is judgeable, *when* selection runs, *then*
`selected_dataset` is `chest_strap_raw`.

**AC6 — the tolerance gate reads the BASELINE WINDOW.** *Given* a judgeable dataset whose latest reading
**within `[D-66, D-7]`** falls more than `RECENCY_TOLERANCE_DAYS` behind the latest baseline-window reading
of any **established** dataset (AC7's reference set; *amended T164 — it read "any judgeable dataset" until
2026-09-20*), *when* selection runs, *then* it is skipped. **The window is normative**: a strap
established on `D-66…D-36`, silent `D-35…D-5` and back on `D-4/D-2/D-0` **must** be skipped — its baseline
is entirely pre-layoff, and the unqualified reading selects it. Reproducing series and the §1.7 argument:
reference §9.

**AC7 — the gate boundary and its reference set.** *Given* a dataset exactly `RECENCY_TOLERANCE_DAYS`
behind, *then* it is **not** skipped (strictly greater than). The reference maximum is taken **once,
simultaneously, over every ESTABLISHED dataset — the ones that are not judgeable and the ones about to be
skipped alike** — never iteratively; the **candidates** it strikes from remain the judgeable datasets, so a
lone **established** dataset is its own reference and is never skipped. *Amended 2026-09-20 (T164,
[[IDEA-080]] option 2, `research/00` §5.4 amended first): the reference was the judgeable datasets from
2026-09-18, and T162 measured that narrowing at `hrv_normal` on an entirely pre-layoff band 1,896 → 3,705 of
307,500 rectangle rows (×1.95) and 96 → 254 of 24,000 walk rows (×2.65) against shipped F005, whose own
rule-1 reference was every established tier — AC21's blocking direction, so it is restored.* **A lone
JUDGEABLE dataset is no longer automatically its own reference**: an established but weekless dataset read
later strikes it, every judgeable dataset can therefore be skipped at once, and AC9's fallback then presents
one verdict-free.

**AC8 — judgeability is a precondition of candidacy.** *Given* a dataset established but holding only 2
distinct judged-week days, *when* selection runs, *then* it is not a candidate. `established` is counted
over the **post-clip** baseline window (AC17).

**AC9 — no dataset *selected*, and the presentation fallback.** *Given* no dataset is selected —
*which is* "no dataset is judgeable" **or** "every judgeable dataset was skipped" (*amended 2026-09-21,
`research/00` §5.4 (iii) amended first; the *given* read "no dataset is judgeable" from 2026-09-18 until
then, which AC7's 2026-09-20 widening made a strict subset: the fallback fires on `selection.selected is
None`, and since the recency reference may be held by an established dataset that is not judgeable, every
judgeable candidate can now be skipped at once*) — *then* `hrv_status` is `hrv_unavailable` and
`selected_dataset` is `null`, **but `baseline` and `band` are still populated from the dataset the athlete
used last** (F005's rule 3, retained) so the non-nullable `baseline.n`/`window`/`established` carry a value
and no contract break occurs. **No verdict is conferred by the fallback, and it cannot be:** clause 1
presents the **established** dataset read last, which holds the recency reference maximum and is therefore
never struck by the gate — so had it been judgeable it would have survived as a candidate and been
selected, and clauses 2 and 3 run only when nothing is established, which judgeability requires. The
presented dataset is never judgeable; the invariant is stated in full in `research/00` §5.4 (iii) and
pinned by `test_probe_every_judgeable_dataset_can_be_skipped_at_once_since_t164`.
`unavailable_reason` is resolved by a **defined precedence across datasets** and must remain
`week_too_thin` on the illness/holiday week — `judge`'s existing single-series order is undefined when N
datasets satisfy different causes.

**AC10 — disagreement is reported from any dataset with a computable band.** *Given* a dataset whose
baseline holds at least two readings, so a band exists, and whose judged-week mean reads below that band,
*then* — wherever a verdict is conferred (*amended 2026-09-21, T167; `research/00` §5.4 (iii) amended
first: where the served verdict is `hrv_unavailable`, for any cause, nobody is named*) — it is named in
`disagreed_with` — **whether or not it is judgeable**. A dataset with fewer than two
baseline readings has no band, cannot disagree, and is instead visible in `datasets[]` carrying its `n` and
its judged-week count. Here "reads below" means reads the **other side of its own band from the selected
dataset** (clarified 2026-09-19, T156, `research/00` §5.4): a dataset below its band beside a selected
dataset that is also below its own agrees with it and is not named.

**AC11 — disagreement never overrides.** *Given* any number of judgeable datasets disagree with the selected
one in **either** direction, *then* `hrv_status` is the selected dataset's verdict, unchanged. Both
directions are reported.

**AC12 — the contract change is additive, including the null case.** *Given* a consumer reading `baseline`
and `band`, *then* those carry the selected dataset's values; and *when* `selected_dataset` is `null` (AC9),
*then* fields non-nullable in `contracts/openapi.yaml` before this change stay non-nullable, or the contract
records a breaking change with an `info.version` bump.

**AC13 — `selected_reason` is a closed enum.** *Given* `selected_dataset` is non-null, *then*
`selected_reason` is non-null and drawn from a closed, documented enum; null with null (T144).

**AC14 — `points[]` discloses which dataset drew each day.** *Given* a range whose days select different
datasets, *when* the series is rendered, *then* each point names the dataset its band came from — selection
runs per local day (`CRITIC-F005` priority 3).

**AC15 — the `included`/`excluded` partition survives per dataset.** *Given* any stored row in `[D-66, D]`,
*then* it is accounted for exactly once (`research/00` §1.6) — a row feeding a non-selected dataset is
neither dropped nor listed `off_baseline_tier`. Removing that population reds
`test_every_published_exclusion_reason_is_observed_in_a_rendered_response` and
`test_the_schema_names_every_exclusion_reason_and_the_verdict_enum`; both are in scope.

**AC16 — `coverage_gap_reset` stays global.** *Given* one tier goes silent while another carries the series,
*then* **no** coverage-gap reset fires — it measures the silence of the series as a whole, every tier
together, which justifies `RECENCY_TOLERANCE_DAYS` (28) > `GAP_RESET_DAYS` (21). This is **already true** of
shipped code and pinned by `test_a_gap_bridged_by_off_tier_readings_is_not_a_gap`; it is stated so the N-way
partition cannot silently make the clip per-dataset.

**AC17 — the band clip and the reported reset are separated.** *Given* a dataset whose own baseline window
spans an internal capture hole of at least `GAP_RESET_DAYS`, *then* that dataset's band is clipped at the
hole — a **new, unreported** per-dataset clip. *And given* a genuine tier change, *then* `tier_change_reset`
still decides the **reported** `reset_reason`/`reset_on` by asking the existing cross-tier question once per
dataset, with T129's stray population left **globally unclipped**. The two are distinct: `_era_boundary`
requires an old-tier reading followed by a new-tier one, so on a single dataset it returns `None` (measured)
and cannot see an internal hole. **AC6, not this criterion, is what defends the reference §9 series** — for
the dataset that *is* `previous_tier`, clause (b) short-circuits and no clip ever fires.

**AC18 — every retirement carries a three-valued pin.** *Given* a qualifier retired by this feature, *then*
its pin is **green** on shipped F005, **red** on F005 with that qualifier deleted and nothing else changed,
and **green** on F006. Existing pins are **re-pointed, never deleted**.

**AC19 — sweeps name their axes, and density is a cross-product.** *Given* any sweep, *then* it states every
axis it holds constant **including the carrier's**, and capture density is varied on **both** datasets
independently — daily, 4/wk clustered, 4/wk spread, 3/wk, 2/wk ([[T145]]). Any walk indexed by "days since
return" states whether it means **days elapsed or mornings captured**.

**AC20 — the two unpinned populations are pinned first.** *Given* the measurement deliverables, *then*
`switch_away_rows` and `inter_rows` are pinned **before** any candidate form is measured.

**AC21 — no regression against shipped F005.** *Given* every sweep in AC19, *then* it runs against **both**
shipped F005 and F006 with both rates reported side by side, and **any §1.7 rate that worsens blocks
release**.

**AC22 — the §1.7 promotion exposure is measured and priced.** *Given* the AC19 sweeps, *then* the rate at
which `hrv_normal` is promoted while another judgeable dataset reads below its own band is measured,
compared against F005 per AC21, and recorded in the Negative Class with its direction.

**AC23 — flip rate is scored against a criterion it can fail.** *Given* the selection form, *then* its
dataset-flip rate per athlete-year is measured across the AC19 sweeps and compared against F005 per AC21;
**a worse rate triggers the deferred hysteresis decision**.

**AC24 — the withhold is retained at dataset scope.** *Given* a dataset that is **not** judgeable but holds
at least `MIN_WINDOW_READINGS` judged-week days, every one later than every judged-week day of the selected
dataset, *then* `hrv_status` is `hrv_unavailable` and no verdict is promoted. This is T125/T132's
`verdict_withheld`, kept rather than retired: without it a brand-new device (zero baseline days, so never
judgeable) leaves the outgoing dataset selected and promotes `hrv_normal` on its stale week — the sixth
§1.7-forbidden population, which shipped F005 closes and AC21 therefore forbids regressing.

## Interface

`GET /metrics/hrv` (`operationId: getHrvTrend`, schema `HrvTrend`) gains `datasets[]`,
`selected_dataset`, `selected_reason`, `disagreed_with`; `points[]` gains per-point dataset identity
(AC14); `included`/`excluded` become per-dataset (AC15). `contracts/openapi.yaml` moves in the same
change set, `check_drift.py` must pass, and `test_hrv_unavailable_causes.py` — an **AST oracle over
`judge`'s source**, with §3.7.4 asserting six causes — reds on any reshaping of `judge`.

## Technical Notes

**Read `spec/references/F006-dataset-model.md` §9, §10 and §13 before implementing.** §13 holds the four
notes that change what you build: the dataset key and why it is the tier; N is 3 in the enum and 2 in every
real corpus; `RECENCY_TOLERANCE_DAYS` is inherited but **not** re-justified in this frame; and fidelity rank
arbitrates while the confidence weight never does — pinned since 2026-09-19 by T156, and no weight is
emitted at all: `datasets[]` carries `fidelity_rank` (`spec/03` §3.7.4; reference §3, corrected
2026-09-21, T159). `research/00` §5.4 is amended **first**, then swept tree-wide.

## Negative Class

Full table in reference §11; the governing row:

| cost | direction and why it is accepted |
|---|---|
| **The §1.7 promotion exposure** — the selected dataset decides, so `hrv_normal` can be promoted while another judgeable dataset reads below its own band, and a consumer reading `hrv_status` alone (every consumer today, and Section 6 as specified) is not told about `disagreed_with` | **Up-regulation while contrary evidence exists — the direction §1.7 forbids.** Accepted because quality-first promotes the *best available* instrument (~8× lower rMSSD error), and suppressed-wins lets a noisier dataset veto a good week. **Newly measurable** — under the fused rule the losing tier had no band. AC21/AC22 gate it: any worsening against F005 blocks release |

Carried forward (§11): same-tier replacement invisible; §3.7.3's device/firmware clause
unimplemented; the 18-day adoption silence and its wrong `week_too_thin` reason; the 3×/week
seven-day flip.

**New, from AC17 (T153):** a dataset's internal hole of **at most** `GAP_RESET_DAYS` silent local
days (21, the constant's own boundary: 22 clips, 21 does not, as `coverage_gap_reset` counts) is not
clipped, so its band still mixes the two eras either side of it — measured: 21 silent days between a
60 ms and a 40 ms era give a band mean of 3.97, neither `ln 60` nor `ln 40`. And a hole whose
resumption lies **after** `D-7` is not internal to the baseline window and is not clipped either:
the band is the pre-layoff era's, and whether it is judged is AC6's (a strap last read `D-30` beside
a snapshot read `D-7` is 23 days behind, inside `RECENCY_TOLERANCE_DAYS`, and is selected on a band
30..66 days old). Both directions are unreported. Who notices: nobody from the response —
`reset_reason` is null in every case; the first is visible only in `baseline.window` staying `[D-66, D-7]`,
the second not at all until `datasets[]` (T159) shows the dataset's latest baseline-window
day. Cost: the first mixes eras (either direction, bounded by three weeks of silence); the second is
up-regulation on a stale band when the layoff crosses `D-7`, the AC6 boundary IDEA-080 names.

- **The withhold is retained, not retired (T158, AC24).** T125/T132's `verdict_withheld` stays,
  restated at dataset scope: a dataset that could not be selected — not judgeable, or skipped by the
  recency gate — holding >= `MIN_WINDOW_READINGS` judged-week days every one later than the selected
  dataset's withholds the verdict (`hrv_unavailable`, `week_not_representative`). Without it a
  brand-new device (zero baseline days) leaves the outgoing dataset selected and promotes
  `hrv_normal` on its stale week — the sixth §1.7-forbidden population, which shipped F005 closes.
  **What widened, priced:** T132's form B touched only a zero-baseline-day tier; at dataset scope a
  dataset with 1..13 baseline days and a full later week withholds too — `hrv_normal ->
  hrv_unavailable`, the freely tolerated direction, measured as one row of T130's matched table (era
  10: shipped `TP 16 / FN 1` at `c = 0` -> `TP 17 / FN 0`) and no other verdict in the HRV suites.
  **Carried unchanged:** T130's carrier-overlap disarm (one carrier morning inside the return and
  the clause is false; `0 of 17` at every `c >= 1`), pinned as the current fact and swept by
  T161/T162; T125's `min(WINDOW_DAYS - MIN_WINDOW_READINGS, k3)` opening-mornings residual; a
  judgeable, unskipped lower-fidelity dataset never withholds (it could have been selected; the
  selected dataset decides and it is named in `disagreed_with`).
- **The honest retirement count, and every retirement's three-valued pin (T160, AC18).** What this
  feature retires is **two mechanisms**, not ten qualifiers: `resolve_baseline_tier`'s role as "one
  tier owns the only band" — the cross-tier *arbitration*, which `build_series` no longer asks for
  at all, though the function itself survives with its tie-order pin — and the `off_baseline_tier`
  exclusion (T152). Each carries the pin the rule requires, in
  `runcoach-api/tests/test_hrv_three_valued_retirements.py`, which loads `git show
  42f7705:.../hrv_trend.py` into a temporary copy, deletes the one qualifier there and drives the
  same pin against all three states. **State 2 is the deliverable and it is observed, not argued:**
  deleting the arbitration reds "the band mixes tiers: readings of ['chest_strap_raw',
  'health_snapshot'] are all in one baseline of 60 days" and moves the band's mean from 4.093974
  (the strap's own) to 3.746843 (the pooled value) with the half-width 0.0137 → 0.1764; deleting the
  exclusion reds "67 stored rows are in neither the series nor excluded". Because the exclusion is
  the arbitration's own bookkeeping and no smaller edit exists, a 2×2 cross-check measures that each
  red is its own qualifier's — both off-diagonals are green. Everything else on IDEA-071's list is
  kept or redeployed and therefore has **no** three-valued pin, deliberately: T094/T095/T129
  re-derived at dataset scope (AC17, T154), T106 kept as a sub-mechanism of the era clip, T107 and
  T116 retained, T093's week-coverage half subsumed by judgeability (AC8) with its rule-3 half
  retained as AC9's presentation fallback (T156), T117 redeployed as AC6's gate, T125 and T132
  retained (AC24, T158). **Two audit traps, priced because an auditor will hit them:** T106 carries
  no task-labelled pin, so a grep-based retirement audit reports it unpinned and is wrong — its
  cover is `test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of`
  (`test_hrv_trend_reset.py:1827`, the pin `research/00`:219 names) with
  `test_the_era_boundary_ordering_key_keeps_its_three_terms`
  (`test_hrv_tier_change_per_dataset.py:482`) covering it per dataset; and
  `test_a_gap_and_a_switch_compose_as_the_later_first_day`, cited at `reset.py:2558`, **does not
  exist** — the only occurrence of that name in the tree is a docstring mention at `reset.py:2665`,
  and the real cover is `test_the_gap_keeps_the_report_while_the_era_keeps_the_clip` at
  `reset.py:2281`. Who notices: nobody from the response — both are defects of the audit trail, and
  the cost is a future retirement pass deleting a live mechanism's pins or writing a pin that tests
  something else, which is the failure mode AC18 exists to prevent.
- **The reported reset is per dataset, and only the selected dataset's is presented (AC17, T154).**
  `tier_change_reset` is asked once per dataset with that dataset's own tier over the same cross-tier
  populations (every tier's unclipped readings in `[D-66, D]` for the stray count, T129 kept global),
  so a dataset that is not selected can carry a reported `tier_change` the response does not show
  until `datasets[]` (T159) — measured: a strap era that cleanly follows an overnight era reports
  `(D-52, tier_change)` while the selected snapshot reports nothing, and `reset_reason` is null.
  Accepted because the report describes the clip of the band it sits beside and no other. Who
  notices: nobody from `hrv_status` or `reset_reason`; `datasets[]` (T159) shows it. And the mirror
  residual: for the dataset that *is* `previous_tier`, clause (b) short-circuits, so an outgoing
  dataset never reports anything however the incoming one was adopted (the task's "known" case) —
  AC6, not this rule, decides whether it is judged.
- **T093's row retires in two halves, honestly (T156).** T093 was one row for two mechanisms. Its
  *week-coverage half* — "the baseline tier must cover the judged week", the gate that kept a
  14-in-60 trial from owning a baseline it could never judge — is **subsumed by judgeability** (AC8):
  a dataset is a candidate only when established and holding >= `min_window_readings` judged-week
  days, so the population the gate closed cannot be selected, and its pins are re-pointed at
  `is_judgeable` (`test_a_thin_tier_that_alone_covers_the_week_does_not_take_the_baseline`, green
  unmoved). Its *rule-3 half* — "when no candidate covers the week, the tier the athlete was read on
  last holds it" — is **retained, not retired**, as AC9's presentation fallback: with nothing
  judgeable the response still carries `baseline`/`band` from the established dataset read last (ties
  `n` then fidelity; else densest by `n`; else densest in the week), so the non-nullable contract
  fields hold a value and the three illness-week pins stay green. What the fallback costs, in the
  tolerated direction: it presents, it never judges — `hrv_unavailable` with the presented dataset's
  own cause, no dissenter named — so an athlete whose new device already holds a full unestablished
  week beside a silent established strap is told `week_too_thin` on the strap's `n`, the T138 18-day
  adoption silence, unchanged and still carried above. Who notices: nobody from `hrv_status`;
  `datasets[]` (T159) shows the covering dataset with its `n` and week count.
- **A claim can hide from its own sweep by wrapping (T163).** The false "reachable after every reset
  ... a coverage gap **or a tier change**" composition was corrected at three document sites by T138
  and T142 and left standing in two places: `judge`'s docstring, which T138's `behaviour_change:
  false` scope rule forbade it from touching, and `spec/06` 6.2.4 cause (4), which nobody's sweep
  reached. The second is the one worth pricing. It was not protected by a scope rule and was not on
  IDEA-073's list of three sites — it survived because every sweep for it was **line-oriented
  grep**, and in `hrv_trend.py` the sentence wrapped mid-phrase across two lines. T163's own
  acceptance probe inherited the flaw: `! grep -rn "or a tier change" runcoach-api/src/` exited 0
  against the *unfixed* file, so a builder who trusted the probe would have shipped the claim intact
  and reported the sweep green. Accepted cost, named 2026-09-19: the project's sweep probes are
  written as line greps and will keep passing vacuously on wrapped prose. What is done instead:
  every claim sweep in this feature is run whitespace-flattened before the probe is believed
  (`sweep-the-claim-not-the-diff` already says this — "a wrapped sentence hides from it" — and the
  rule was right and the probe was written anyway). Who notices: nobody at runtime; the reader of
  `judge` and the next task that writes a `! grep` sweep clause.
- **The §1.7 promotion exposure, measured against shipped F005 and re-measured after the fix
  (T162/T164, AC21/AC22/AC23).** Over 1,353,000 judgings — T125's return rectangle × 25
  capture-density pairs × carrier overlap `c = 0..5`, the 40-morning device-return walk in both
  orientations, and T150's two populations, each run on F006 and on shipped F005 at `42f7705` under
  both overlap variants — the exposure is priced in three numbers rather than one. **AC22's rate,
  and its direction:** `hrv_normal` promoted while another dataset reads the *other side* of its own
  band is **7.97% → 7.84%** taken literally (24,510 → 24,099 of 307,500), **1.76% → 1.72%**
  conditioned on the dissenter holding `>= MIN_WINDOW_READINGS` judged-week days (5,415 → 5,287),
  and **0.597% → 0.597%, identical**, conditioned on the dissenter being *judgeable*, which is
  AC22's own word. The literal figure is thirteen times the judgeable one and most of it is a
  dissenter holding one or two readings, whose 38 ms lands 0.07 below its own band because the
  fixture alternates 38/44 — an artefact of the fixture, not of the rule. The direction in every
  counted row is the forbidden one: up-regulation while contrary evidence exists, and a consumer
  reading `hrv_status` alone is not told. **Not worse than F005 on any of the three readings, and
  better at every `c` on the literal one.**
- **`hrv_normal` on an entirely pre-layoff band: measured as a regression, and PAID (T162 → T164).**
  F006 as first built took AC7's recency reference over the *judgeable* datasets, where shipped F005
  took its equivalent over every *established* tier. A carrier that had stopped, or whose judged
  week was too thin to be judgeable, therefore left the reference set, the returning dataset became
  its own reference, and it was selected on a band 36-66 days old: **1,896 → 3,705 of 307,500**
  rectangle rows and **96 → 254 of 24,000** walk rows, worse at every `c`, on 82 of 150 cells,
  identically under both overlap variants. Who noticed: nobody from `hrv_status` — the athlete was
  told he was normal on evidence up to 66 days old with no dissenter to name, since under the AC9
  fallback `disagreed_with` names nobody (IDEA-082). **T164 widened the reference population to
  every established dataset (`research/00` §5.4 amended first) and the rate returned to shipped
  F005's exactly: 1,896 and 96, flat at 316 per `c`, cell for cell across all 25 density pairs, in
  both overlap variants.** What it costs, and it is a real cost: a **lone judgeable** dataset is no
  longer automatically its own reference — an established but weekless dataset read later strikes it
  — so every candidate can now be skipped at once and the AC9 fallback presents one verdict-free.
  The athlete pays for the closed exposure in **silence**: rectangle `hrv_unavailable` 243,326 →
  246,944 of 307,500 and walk 15,996 → 16,312 of 24,000, and the returning dataset is selected on
  21,520 rectangle rows against F005's 59,468. This also re-imports the shape T125 was written
  against — the gate striking the tier the athlete is currently using — at F005's own rate.
- **One §1.7 rate is still worse than shipped F005, and it is DEFERRED rather than paid, by user
  decision of 2026-09-20 (T162 Finding 2, T164).** A suppressed return promoted `hrv_normal` from
  the overlapping carrier's week is worse at `c = 4` and `c = 5` on 17 of 150 cells — **22,217 →
  22,232 of 307,500** in total (+15, +0.07% relative) and **1,104 → 1,108 of 24,000** on the walk —
  and **better** at `c <= 2` (1,713 → 1,614 at `c = 0`). It exists **only** with the carrier's
  overlap mornings healthy, i.e. only if the two datasets are independent instruments; with them
  suppressed F006 is better at every `c` (3,625 → 3,497) and the T130-comparable subset goes **128 →
  0**. Whether they are independent is unmeasured and, from the recorded corpus, unmeasurable
  (IDEA-087), so this regression's *sign* is unknown and it is not repriced on an assumption. It is
  carried as the release gate's one named, counted and conditioned exception — 64 gated rows, pinned
  by row count, by marginal total and by the requirement that the correlated side stay no worse — in
  `runcoach-api/tests/test_hrv_no_regression_gate.py`, which reds if any of those move. Mechanism:
  T153's hole clip leaves the returning dataset unestablished past the seventh morning back while
  the overlapping carrier stays the only judgeable dataset, where shipped F005 re-admitted the strap
  at `r = 8` on a band mixing era A with the return — a verdict reference §9 calls worse than its
  successor, which on this population happens to land right.
- **T130's carrier-overlap disarm, closed with numbers on both rules and unmoved by T164 (T162
  deliverable 7).** T158's retained order clause is decisive on **6 of 25 density pairs** on both
  F005 and F006 — a `daily` or `4wk-clustered` return against a `daily`, `4wk-clustered` or
  `4wk-spread` carrier — and **one captured carrier morning disarms 100% of it on both**: 226 armed
  rows on F005 and 424 on F006 (T158's widening to dataset scope, measured), disarmed 170 / 212 /
  226 / 226 / 226 and 320 / 400 / 424 / 424 / 424 at `c = 1..5`, and 100% at `c_cap = 1` read in
  mornings captured rather than days elapsed. T158 doubled the clause's reach without moving its
  disarm rate by one row, and T164 moved neither. Its value is therefore not those rows but AC24's
  brand-new-device population, which no overlap can disarm.
- **AC23's criterion fired, and the decision it obliges is conditional — AC23 is PARTIAL, not MET
  (T162, re-measured T164; corrected T166).** **80 worsened cells** of the 1,200 per-cell `walk_flips`
  rows, every one at `car_density = 2wk`, each 0 → 2 flips per 40-morning walk; AC21 compares per
  cell as well as marginally. The marginal, **18.47 → 9.36 per athlete-year** (600 against 1,184
  selection changes over 23,400 day-to-day transitions), is what **concealed** them, not what settles
  it. Decided 2026-09-21, **no hysteresis**, and **conditional** ([[IDEA-089]], `status: conditional`):
  `research/00` still says a worse rate *reopens* the decision and does not carry it, and
  `T130-overlap-sweep-harness.py`'s `era()` gives every tier the same value generator ("the band's
  dispersion is the same at every density"), so "no §1.7 rate moved" is true **by construction**.
  What the flips were traded for: **2,984 more `hrv_unavailable` mornings** over the same 24,000 walk
  rows — IDEA-084's second silence, now larger. F006 trades flips for silence at roughly one for
  five; whether an athlete prefers a changing answer to no answer is not measured and is not
  measurable from fixtures.
- **Whether `disagreed_with` measures two instruments at all is still unmeasured, and it now gates a
  deferred regression rather than a release (T162, IDEA-087).** The recorded corpus establishes that
  on a strap morning the watch reads the strap over ANT+ (every strap-morning Health Snapshot names
  the HRM-Pro-Plus by serial), that the Health Snapshot stores **no beats**, that the two numbers
  differ by ×2.17 and ×1.38 on the two same-morning pairs that exist, and that a snapshot needs no
  strap at all. It **cannot** establish whether they are the same beats post-processed: no
  simultaneous pair exists or can exist on one watch, and n = 2. The statistic the rules depend on
  is not the correlation but the **sign-agreement rate** — how often the two datasets fall on the
  same side of their own bands — which T162's two overlap variants bracket at 0% and 100%, and which
  T159's `datasets[]` could estimate from ordinary use at no capture cost.

## Decision Log

All 2026-09-18 (`/ship-discuss IDEA-071`, then sprint-006 planning); full text in reference §12/§14.

- Dataset key is the **tier**, not the device; a morning with two captures **feeds both datasets**.
- **The selected dataset decides**; disagreement reported, never overriding — which put the whole §1.7
  exposure into selection, and is why the selection form was re-opened rather than left open.
- Selection is **quality-first with a recency tolerance gate**, over recency × quality, recency-only and
  coverage-first (the last two invert §3.7.1 precedence).
- **Hysteresis deferred** with a trigger it can fire (AC23); **retirement needs a three-valued pin**;
  **adoption silence out of scope**.
- **Hysteresis decision TAKEN 2026-09-21: no hysteresis (CONDITIONAL)** (`/ship-discuss --idea IDEA-089`). AC23's trigger
  did fire — 80 of the 1,200 per-cell `walk_flips` rows are worse under F006, every one at `car_density = 2wk` — so the
  sprint-006 handoff's "NOT triggered" (read off the halved marginal, 18.47 → 9.36) was wrong about the
  antecedent. The consequent is discharged rather than deferred: at that same density **no §1.7 forbidden
  family moved at all** — all 850 family×cell rows (370 cells) equal — and **AC22 promotion exposure improved** (52 better, 0 worse), so
  the flips buy withheld days, not wrong verdicts. A flip is a proxy; §1.7 forbids a harm. Damping it would
  add state and a tunable to a selection form nine F005 cycles already accreted qualifiers onto, against an
  instability with no measured cost. The 80-cell pin stays and reds if the set grows, shrinks or shifts —
  the decision is against the set **as measured on 2026-09-21**, not a licence for it to grow. The separate
  cost this surfaced — more withheld mornings at sparse capture — is [[IDEA-092]], undecided.
- **Critic finding, fixed: AC6 had no window and re-created T125** — worse than shipped F005 on a reproduced
  series. AC6 names the baseline window (§9).
- **`coverage_gap_reset` stays global** (AC16) — already true of shipped code; stated so the N-way partition
  cannot silently make it per-dataset. **Sensor identity split to F007.**
- **Planning corrections (§14):** AC17 split into an unreported per-dataset band clip plus the existing
  cross-tier reported reset, after `_era_boundary` was measured to return `None` on one tier; AC9 keeps a
  presentation fallback; AC10 taken literally (2026-09-18) — judgeability is never consulted, and that is
  the sense in which it survives the 2026-09-19 re-scoping of "reads below" to the other-side reading
  (`research/00` §5.4, T156).
- **Rule TAKEN 2026-09-21 for `disagreed_with` in every verdict-free state (T167, fixes [[B-CR-002]]):**
  **one condition — `verdict == hrv_unavailable` — names nobody**, for any cause. A disagreement is a
  claim *about* a verdict and a withheld verdict makes no claim to contradict, so the list is empty in all
  three states alike: nothing selected (AC9's fallback), a **selected** dataset whose verdict is withheld
  under `research/00` §5.4 (v) (`week_not_representative` — T125's returning athlete), and a day that has
  not happened (`day_not_happened`). The alternative considered and rejected was reverting the sprint-006
  M2 fix and reading `disagreed_with` as a report of *band readings* rather than of claims. Authority first:
  `research/00` §5.4 (iii) amended, `spec/03` §3.7.4 restated, then the predicate. **AC10 is re-scored from
  MET to PARTIAL as of `2e4230f` and back to MET here** — its once-unconditional *then* now has an authority-
  carried exception, recorded on the AC text itself, that is one sentence, states all three states, and is pinned at the served seam by
  `test_hrv_dataset_populations.test_a_withheld_verdict_names_no_dissenter_and_a_conferred_one_still_does`,
  whose control is the same rows one carrier morning apart, where the verdict *is* conferred and the
  dissenter *is* named. Judgeability is still never consulted for the naming (AC10 taken literally, above);
  what gates the served list is the verdict, not the candidate set.
