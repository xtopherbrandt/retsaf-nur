# Contracts

Machine-readable interface contracts for the Run Coaching System. This is the
`specification/` folder's operational sibling: `specification/` holds the human
design spec; `contracts/` holds the API contract the code is built and checked
against.

## `openapi.yaml` — the UI↔engine API contract

`openapi.yaml` is the **target contract** for the athlete-facing UI (web +
mobile) talking to the coaching engine. It is authored top-down from the engine
spec (`specification/spec/01`–`09`) — the engine owns the domain objects; this
file is their serialization over HTTP. It is **OpenAPI 3.1** and is the source
of truth both sides build from: the backend converges its handlers to it, and
the UI generates its client from it.

### Target vs. as-built

- **Target** = this file. What the API *should* expose, including endpoints not
  built yet.
- **As-built** = what `runcoach-api` actually serves, emitted for free by
  FastAPI at **`/openapi.json`** (and browsable at `/docs`).

The two are reconciled deliberately, not assumed equal. The diff between this
file's `implemented` endpoints and `/openapi.json` should be empty; the diff on
everything else is the build backlog.

### Custom extensions

Every operation carries three vendor extensions:

| Extension | Meaning |
|---|---|
| `x-readiness` | `implemented` \| `stubbed` \| `planned` — the endpoint's state in the backend today. |
| `x-owner-spec` | The engine spec section (or feature) that **owns** the underlying object; go there for field-level truth. |
| `x-story` | The UI user story/stories the operation serves. |

`implemented` today: `GET /health` and `/sessions` (`POST`, `GET /{id}`,
`DELETE /{id}`). Everything else is `planned` and is served by a mock until the
backend catches up.

## How to use it

- **Client generation (UI):** generate types/client from `openapi.yaml` so the
  UI codes to the contract, never to whatever the backend happens to serve today.
- **Mock server:** run a mock from `openapi.yaml` to develop against `planned`
  endpoints before they exist.
- **Conformance / drift check (recommended CI):** `check_drift.py` generates the
  as-built schema in-process and asserts that every path marked
  `x-readiness: implemented` here still matches its as-built shape (operation
  exists, keeps its method + success status, same required request fields). Any
  drift fails the check rather than rotting silently; non-`implemented` paths are
  exempt (not built yet), and response-typing gaps are printed as advisory notes,
  not failures. Run it:

  ```sh
  uv run --package runcoach-api --with pyyaml python contracts/check_drift.py
  ```

  Wired in `.github/workflows/contract-drift.yml`.

## Editing rules

- **Keep `implemented` endpoints exact to `/openapi.json`.** When the backend
  changes a live endpoint, fold the change into this file in the same change set
  so the two never disagree.
- **Additive and versioned.** Prefer additive changes; bump `info.version` on a
  breaking one. The UI targets the contract, so a silent breaking edit breaks the
  UI.
- **Reconcile before committing a refresh.** Diff the built surface against this
  file first; so far every backend change has been additive (no contradictions).
- **Don't hand-edit generated output.** This file is authored, not generated;
  `/openapi.json` is generated, not authored. Keep that direction.

## Open backend alignments (tracked in the contract's header comment)

1. Type `GET /sessions/{id}` with a Pydantic `SessionDetail` response model so it
   contributes a precise schema to `/openapi.json` (today it returns an untyped
   dict).
2. Auth + athlete scoping in the contract from day one (backend is
   single-athlete/no-auth; `athlete_id` is already modelled).
3. Uniform error envelope + `?since=` cursor pagination (as-built still returns
   FastAPI-default `{detail}` / plain text).
4. Resource naming: `sessions` (ingested activity records) vs. `workouts`
   (engine-planned prescriptions) — this file uses both deliberately.

## Related

- Human design spec: `../specification/`
- UI specification (authoring home of this contract): the "Run Coaching System
  Specification" project — `spec/ui-00-outline.md` §4 and
  `spec/ui-development-plan.md` (contract-convergence approach + backend
  recommendations).
