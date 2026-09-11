# Changelog

## Unreleased

- F005/T093, amended by T094: the baseline tier must also cover the judged week -- a tier with >= 14 readings in `[D-66, D-7]` owns the baseline only if it also holds >= 3 in `[D-6, D]`; when no such tier covers the week, the >= 14 tier the athlete was read on last keeps it (T094; T093 had said the densest), so a switched athlete's thin week no longer reverts to the abandoned device and an empty week no longer asserts a reset both neighbours withdraw. `tier_change` is reported only when the new tier sustains the window, differs from the tier the previous 60 days sustained, and the two eras do not interleave over the previous and current windows together (no reading of the new tier falls between the old tier's first and last there; sprint-005 review cycle 3 -- judged on the current window alone, a three-week strap trial that had aged wholly into the previous window reported a phantom `tier_change` for seven weeks to an athlete who never switched) -- so a two-week trial abandoned two months ago, and a young two-to-three-days-a-week strap habit, never report one, while abandoning an owning strap for the daily snapshot is reported the day the snapshot first owns the baseline rather than a month later; `reset_on` is the era's true first day and no longer slides once it is older than 60 days. Accepted costs, named in F005's Negative Class: the two-to-three-day strap alternates the tier whenever its count in the sliding judged week crosses 3, and a stale trial still inside the window plus three strap days this week is judged on the trial's band ("stale candidacy"); a single stray old-tier capture after a genuine switch is interleaving, so it hides the switch's reset, and a cleanly-ended strap trial followed by a thin week reads as a switch until the snapshot covers a week again (review cycle 3). The reset clears when the previous 60 days are no longer sustained by the old tier: for a forward switch when the new tier reaches 14 there (S+80 for a daily device), for the reverse one when the old strap drops below 14 there (T+114).

## 2026-09-09 — Sprint 005: Resting-HRV Trend

F005: the resting-HRV trend verdict on the contract's `GET /metrics/hrv`, computed from F004's
captures and bucketed into the athlete's **local** days. Bucketing into local days is what makes
this sprint a breaking config change — the server now has to know which days are the athlete's —
and the migration note below is the whole of the upgrade path. The feature's own entry follows at
release; this section is written at the point the break lands (T088/T089/T090) so an athlete who
pulls mid-sprint has the instruction beside the refusal.

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
