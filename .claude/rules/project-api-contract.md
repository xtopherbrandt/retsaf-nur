---
paths: ["runcoach-api/src/**", "contracts/**"]
---
# The API contract and the as-built API move in the same change set

`contracts/openapi.yaml` is the UI↔engine **target contract**, kept in sync with the UI project,
which generates its client from it. The backend converges its handlers to the contract; the
contract does not trail the backend. Codified 2026-09-09 during `/ship-discuss F005`, when the
feature's endpoint was found to differ from the contract in path, parameters and shape.

## The rule

1. **A route that ships is folded into the contract in the same change set.** Flip the operation's
   `x-readiness` to `implemented`, and make the contract's operation exact to what `/openapi.json`
   serves: path, method, success status codes, required request fields and required query
   parameters. An endpoint the contract does not describe is not done.
2. **Required means required.** `contracts/check_drift.py` fails when the contract requires a query
   parameter or request field the as-built route does not accept. If the engine wants a parameter
   optional, change the contract — loosening a `planned` operation is not a breaking change; a
   silent mismatch is.
3. **Richer responses are additive.** The engine may return more than the contract's schema; add
   the fields to the contract's component (do not remove or rename what the UI already targets).
   Response-schema gaps are advisory in the drift check, not failures — so they are still yours to
   close, not the checker's.
4. **The drift check runs in the task's acceptance probe** for any task that flips an operation to
   `implemented` or changes a live route:

   ```sh
   uv run --package runcoach-api --with pyyaml python contracts/check_drift.py
   ```

   Exit 0 is the only pass. Note the script imports `runcoach_api.main:app` in-process, so it needs
   no server and no `api.toml`.
5. **Bump `info.version` on a breaking change** to an `implemented` operation, and say so in the
   CHANGELOG's migration section, as with a breaking config change.

## Why

The contract is authored top-down from the engine spec; the engine is authored from the same spec.
Two documents derived from one source drift the moment either is edited alone — the same shape as
[[sweep-the-claim-not-the-diff]], one artifact over. F005 first specified `GET /metrics/hrv-trend`
with `?date=`; the contract said `GET /metrics/hrv` with required `from`/`to` and a per-day series.
Both were faithful readings of §3.7. Neither document knew about the other until sprint planning
compared them.

## How to apply

- Read the contract's operation **before** writing the route's Red step; the operation id, path
  and parameter names come from the contract, not from the feature file's first draft.
- `check_drift.py` and the workflow must be **committed**, not merely present in a working tree —
  a worktree-isolated builder cannot see untracked files.
- The contracts README's editing rules apply: authored file, additive changes, reconcile before
  committing a refresh.
