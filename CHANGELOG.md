# Changelog

## 2026-09-01 — Sprint 001: Delivery Architecture Scaffold

### Added
- **F001 — Backend API Scaffold**: FastAPI backend (`runcoach-api`) with a `GET /health` endpoint, explicit TOML configuration (no silent defaults), a `runcoach-api init` command, and clean non-zero-exit error handling for missing/invalid/corrupt config and port-already-in-use conditions.
- **F002 — CLI Scaffold**: Typer CLI (`runcoach-cli`) with `runcoach init`/`runcoach status`, talking to the backend API over HTTP, with defensive parsing of the API's response and clean error handling for an unreachable API, non-2xx responses, and malformed response bodies.

Both packages live as a `uv` workspace (`runcoach-api/`, `runcoach-cli/`), single-athlete/local-first, no auth. This establishes the delivery architecture every future domain feature (data ingestion, derived metrics, plan generation, adaptation, conversational interface) builds on top of.
