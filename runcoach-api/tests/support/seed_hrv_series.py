"""Seed a resting-HRV series for F005's demo probe, and state the band it must produce.

    uv run --package runcoach-api python runcoach-api/tests/support/seed_hrv_series.py \\
        --data-dir "$RUNCOACH_DATA_DIR" --tier chest_strap_raw --end 2026-09-07 \\
        --days 70 --suppress-last 5 --print-expected > expected.json

or, for a history of several eras (T096 -- the demo probe's two-tier history:
a snapshot era with a strap trial inside it, a genuine switch, one stray):

    uv run --package runcoach-api python runcoach-api/tests/support/seed_hrv_series.py \\
        --data-dir "$RUNCOACH_DATA_DIR" --print-expected \\
        --era health_snapshot:2026-03-22:81:7 --era chest_strap_raw:2026-03-01:14:0:7 \\
        --era health_snapshot:2026-06-30:100 --era chest_strap_raw:2026-09-07:69:5 \\
        --era health_snapshot:2026-08-10:1 > expected.json

The series is written through the **real** ``mapping.to_canonical ->
hrv_classification.classify -> db.persist`` path -- never raw SQL -- by the
generator in ``tests/conftest.py`` (``_seed_hrv_series``). This file is a thin
wrapper over it: the workspace runs pytest with ``--import-mode=importlib``,
under which nothing in ``tests/`` is importable by name, so the generator lives
in ``conftest.py`` (reachable by tests as the ``seed_hrv_series`` fixture) and
is loaded here by path with ``importlib.util.spec_from_file_location``. One
implementation, two entry points.

**Isolation first.** ``--data-dir`` is exported as ``RUNCOACH_DATA_DIR`` before
``runcoach_api.db`` is imported, the config is resolved, and the *resolved*
data dir is printed -- on stderr, so stdout stays the expected-band JSON alone
for ``jq --slurpfile`` -- before the first write. If the resolved dir is not
the requested one the script exits 2 without writing
(``isolate-the-environment-before-the-first-request.md``). The rest of the
config (``athlete_timezone``, ``resting_hrv_profile_names``) is the athlete's
real one: ``load_config`` still needs ``~/.runcoach/api.toml`` to exist, exactly
as F003's and F004's probes do, and ``RUNCOACH_``-prefixed variables override
its fields.

``--end`` is the last **local** day to seed, in the configured zone -- Auckland's
2026-09-07 morning is 2026-09-06 in UTC, and a UTC-computed seed would leave
the last reading a day short of ``to``.

``--era TIER:END:DAYS[:SUPPRESS_LAST[:LOCAL_HOUR]]`` seeds one era per flag,
in the order given, each through the same generator; ``--tier``/``--end``/
``--days``/``--suppress-last`` are the one-era shorthand and are ignored when
``--era`` is present. ``LOCAL_HOUR`` (default ``SEED_LOCAL_HOUR``) places an
era's captures at another wall-clock hour, which two tiers captured on the
same morning need: ``session_id`` is derived from ``(source_device,
start_time)`` and the synthetic device is one string, so two tiers at one
instant would be one row.

``--print-expected`` writes a JSON object with ``band_lo``, ``band_hi``,
``band_mean``, ``half_width``, ``floored``, the baseline ``tier``, its size
``n``, ``baseline_window`` and the era's ``first_day`` -- computed from the
generator's own values with sample SD (``statistics.stdev``), the estimator
``metrics.hrv_trend`` uses, and never read back from the endpoint the probe
then checks (IDEA-057). With ``--era`` it writes ``{"eras": [...]}``, one such
object per era in the order given, each stated for that era at its own
``END`` over that era's values alone.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"
TIERS = ("chest_strap_raw", "health_snapshot")


def _load_conftest():
    """``tests/conftest.py`` as a module, loaded by path: the plain
    ``_seed_hrv_series`` generator and the ``SEED_*`` constants."""
    spec = importlib.util.spec_from_file_location("runcoach_api_tests_conftest", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    # ``conftest.py`` uses ``from __future__ import annotations`` with a
    # ``@dataclass``, and ``dataclasses`` resolves those string annotations
    # through ``sys.modules[cls.__module__]`` -- so the module must be
    # registered before it executes, exactly as the import system would.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_era(spec: str) -> dict:
    """``TIER:END:DAYS[:SUPPRESS_LAST[:LOCAL_HOUR]]`` -> the generator's keyword
    arguments for one era (``local_hour`` absent when not given, so the
    generator's default applies)."""
    parts = spec.split(":")
    if not 3 <= len(parts) <= 5:
        raise argparse.ArgumentTypeError(f"expected TIER:END:DAYS[:SUPPRESS_LAST[:LOCAL_HOUR]], got {spec!r}")
    tier = parts[0]
    if tier not in TIERS:
        raise argparse.ArgumentTypeError(f"tier must be one of {TIERS}, got {tier!r}")
    try:
        era = {
            "tier": tier,
            "end": date.fromisoformat(parts[1]),
            "days": int(parts[2]),
            "suppress_last": int(parts[3]) if len(parts) > 3 else 0,
        }
        if len(parts) > 4:
            era["local_hour"] = int(parts[4])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{spec!r}: {exc}") from exc
    return era


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", required=True, type=Path, help="the data dir to write into (exported as RUNCOACH_DATA_DIR)")
    parser.add_argument("--end", type=date.fromisoformat, help="the last local day to seed, YYYY-MM-DD (one-era shorthand)")
    parser.add_argument("--days", type=int, default=70, help="how many consecutive local days to seed (default 70)")
    parser.add_argument("--suppress-last", type=int, default=0, help="how many of the last days read suppressed")
    parser.add_argument("--tier", choices=TIERS, default="chest_strap_raw", help="the source tier to seed")
    parser.add_argument(
        "--era",
        action="append",
        type=_parse_era,
        metavar="TIER:END:DAYS[:SUPPRESS_LAST[:LOCAL_HOUR]]",
        help="seed one era per flag, in order; replaces the one-era shorthand",
    )
    parser.add_argument("--print-expected", action="store_true", help="print the expected band as JSON on stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.era:
        eras = args.era
    elif args.end is None:
        parser.error("the following arguments are required: --end (or one or more --era)")
    else:
        eras = [{"tier": args.tier, "end": args.end, "days": args.days, "suppress_last": args.suppress_last}]
    requested = args.data_dir.resolve()

    # Bind the data dir before anything reads the config, and before
    # ``runcoach_api.db`` is imported at all.
    os.environ["RUNCOACH_DATA_DIR"] = str(requested)
    from runcoach_api import db
    from runcoach_api.config import validate_zone

    config = db._load_config_cached()
    resolved = Path(config.data_dir).resolve()
    print(f"resolved data dir: {resolved}", file=sys.stderr)
    if resolved != requested:
        print(f"refusing to write: resolved data dir is not the requested {requested}", file=sys.stderr)
        return 2

    zone = validate_zone(config.athlete_timezone)
    seed = _load_conftest()._seed_hrv_series
    results = []
    conn = db.get_connection()
    try:
        for era in eras:
            result = seed(conn, zone=zone, profile_names=config.resting_hrv_profile_names, **era)
            results.append(result)
            print(
                f"seeded {len(result.sessions)} {era['tier']} readings ending {era['end']} in {zone.key} "
                f"({era['suppress_last']} suppressed); baseline n={result.expected['n']}",
                file=sys.stderr,
            )
    finally:
        conn.close()

    if args.print_expected:
        json.dump({"eras": [r.expected for r in results]} if args.era else results[0].expected, sys.stdout, indent=2)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
