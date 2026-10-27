---
paths: ["**/*"]
---
# The release demo loads the athlete's real config files through the new release before approval

Learned from sprint-021 (2026-10-22). Origin: `spec/ideas/IDEA-183-release-demo-checks-the-real-config.md`.

## The rule

The release gate runs `runcoach-api/tests/support/check_real_config.py` on the athlete's real
`~/.runcoach/api.toml` and `~/.runcoach/cli.toml`:

```
uv run --package runcoach-api python runcoach-api/tests/support/check_real_config.py
```

The script loads both files through the release's own config models, the same loaders `serve` and
the CLI use, and prints `api.toml: OK` or `api.toml: ERROR` with the message `serve` would print, then
the same for `cli.toml`. It names any `RUNCOACH_*` environment variable that is set (names only),
since one can hide a key the file lacks. It is read-only: it opens no store, starts no server and
contacts nothing, Garmin included. Exit 0 when both files load, 1 otherwise.

A failure is a **demo finding the athlete sees before approval**: the demo shows the error as printed,
and the CHANGELOG states the edit the athlete must make to the file (the key, where it goes, a value
that works). The release is not approved on a demo whose real config does not load.

A demo run against a store copy with a config written for the demo proves the release works with that
config, not with the athlete's.

## Why

Sprint-020 made `threshold_workout_names` a required `api.toml` field with no default. The athlete's
real `api.toml`, last written 2026-09-10, lacks it, so the athlete's API could not start for two
releases. Both release demos ran against a store copy with a config written for the demo, and neither
saw it. Sprint-021's API scanner hit it by accident, when a timing script read the default config,
and the review critic named it. The athlete named this as what went badly in sprint-021.

## How to apply

- **Run it at the release gate, on the real files**, not on a copy and not on a demo config. Pass
  `--api` and `--cli` only to check a candidate edit before the athlete makes it.
- **Quote the output verbatim** in the demo record, the OK lines included, so a reader sees what was
  loaded.
- **Any release that adds a required field or changes a key's type** names the edit in the CHANGELOG
  even when the check passes, since a set environment variable can be what made it pass.
- **Do not edit the athlete's files.** The check reads; the athlete makes the change from the
  CHANGELOG's words.

Related: [[changelog-claims-are-tried-on-real-activities]] — that rule tries the release's words on
the athlete's runs; this one tries the release's config models on the athlete's files.
