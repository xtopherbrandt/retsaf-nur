# Changelog

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

### Known limitation
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
