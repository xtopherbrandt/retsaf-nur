# Changelog

## Unreleased — Sprint 002

### Changed
- **F003 / T032 — `session_id` is now deterministic**: derived as a truncated SHA-256 over `(source_device, start_time)` — the same tuple the `UNIQUE (source_device, start_time)` constraint dedups on — instead of a random UUID. Re-ingesting the same activity into a rebuilt database now reproduces the same `session_id` (spec §2.2.1, "stable per activity"). The DB-level UNIQUE constraint is unchanged and remains the concurrent-upload race guard.

### Migration required
- **A database created before this change holds random-UUID `session_id`s.** Those rows still read back correctly by their stored id, but they will never match the id a re-ingest of the same FIT file now derives, so they cannot be re-identified from the corpus. **Rebuild the database from the FIT corpus** (delete `<data_dir>/runcoach.db` and re-ingest) to move existing sessions onto derived ids. No automatic conversion is provided: the pre-change ids are not recoverable from anything but the row itself, and no downstream consumer (E009 decision log, E010 state model) exists yet to be broken by the rebuild. See F003's Decision Log for the rationale.

## 2026-09-01 — Sprint 001: Delivery Architecture Scaffold

### Added
- **F001 — Backend API Scaffold**: FastAPI backend (`runcoach-api`) with a `GET /health` endpoint, explicit TOML configuration (no silent defaults), a `runcoach-api init` command, and clean non-zero-exit error handling for missing/invalid/corrupt config and port-already-in-use conditions.
- **F002 — CLI Scaffold**: Typer CLI (`runcoach-cli`) with `runcoach init`/`runcoach status`, talking to the backend API over HTTP, with defensive parsing of the API's response and clean error handling for an unreachable API, non-2xx responses, and malformed response bodies.

Both packages live as a `uv` workspace (`runcoach-api/`, `runcoach-cli/`), single-athlete/local-first, no auth. This establishes the delivery architecture every future domain feature (data ingestion, derived metrics, plan generation, adaptation, conversational interface) builds on top of.
