---
paths: ["**/*"]
---
# Bind the data dir before the process starts, and prove it before the first request

Learned from F004 sprint-003 (2026-09-06). Origin: `spec/ideas/IDEA-014-isolate-environment-before-the-first-request.md`.

## The rule

Any agent that drives the **running application** — not just a test suite — must do all three of
these, in this order, before it sends a single request:

1. **Set every state-directing environment variable before the process starts.**
   `RUNCOACH_DATA_DIR` today; the general form is *anything that decides where writes land*.
   Starting the server is the action that binds the data dir — after that, it is too late.
2. **Verify the isolation took effect, before the first request.** One assertion that the
   *resolved* data dir is inside the scratch path, printed in the return, is enough. A
   `curl /health` is **not** verification — the app is already up and pointed somewhere by then.
   An unverified `export` is an intention, not an isolation.
3. **Assert the real store is untouched at the end.** Row counts before and after, or a file
   mtime on `~/.runcoach/data/runcoach.db`. Cheap, and it converts a silent corruption into a
   reported one.

## Why

During sprint-003 review, the goal-verification agent started a real uvicorn and began uploading
fixtures **before** pointing `RUNCOACH_DATA_DIR` at a scratch directory. One session — 154 records,
156 RR intervals, 3 quarantine sidecar rows — was written into the athlete's real
`~/.runcoach/data/runcoach.db`.

Two things made that cheap rather than expensive, and **neither is a control**:

- The agent was **honest about a mistake nobody would otherwise have seen.** The cleanup was
  invisible in its output; only its own disclosure surfaced it.
- The write was **additive**, so removal was exact. A migration, a schema reconcile against a stale
  DB, or an idempotent-looking upsert would not have been.

Worth recording: `pipeline.ingest_fit_bytes` calls `db.init_schema(conn)` on every upload, so a
stray ingest against a real database also **reconciles its schema**. Harmless in this instance; not
harmless in general.

This is not about untrusted agents — every agent in that sprint behaved well, including this one.
It is about **the default being wrong**. The rule inverts the order so the safe path is the easy
one.

## How to apply

- Put the isolate-and-verify step in the **dispatch prompt template** for any task that runs the
  app, alongside the existing "never `tail -f` the event log" warning — which exists for exactly
  the same reason: an agent doing something reasonable-looking that silently damages state it did
  not think it was touching.
- Order matters more than thoroughness here. `export RUNCOACH_DATA_DIR=<scratch>` → start → **read
  back the resolved path** → first request. Any reordering re-creates the incident.
- Prefer a scratch path you created this session over any reused temp directory, so "is this row
  mine?" is never a question you have to answer later.
- If you discover mid-run that you were not isolated, **say so in your return even after cleaning
  up.** The disclosure is the only thing that made this incident recoverable, and it is the part a
  future agent is most tempted to skip.

Related: [[adversarial-input-probes-are-a-task-deliverable]] — probing by driving the real app with
hostile input is precisely the discipline that makes this rule more load-bearing, not less.
