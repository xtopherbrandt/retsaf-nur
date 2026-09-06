import argparse
import sys
from pathlib import Path

import tomli_w

CONFIG_PATH = Path.home() / ".runcoach" / "api.toml"


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="runcoach-api init")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--force", action="store_true")
    # Repeatable: each occurrence appends one activity-profile name, and the
    # order given is the order written.
    #
    # `default=[]` is load-bearing, not tidiness. `action="append"` yields
    # `None` when the flag never appears, and `tomli_w.dumps` writes nothing
    # at all for a `None` value -- so omitting the default would produce a
    # config missing a field `AppConfig` requires, `serve` would refuse it,
    # and the refusal tells the athlete to run `init`: the fresh-install loop
    # this flag exists to close.
    #
    # Zero occurrences therefore write `[]`, which is the explicit "Tier 2
    # only" declaration rather than a default -- an athlete who uses only
    # Health Snapshot has said so. `'Health Snapshot'` is deliberately not
    # seeded: it already routes on Tier 2's numeric `sport == 60` identity,
    # so listing it would give one file two routes into the same decision,
    # and the name is locale-dependent free text where the number is not.
    #
    # Entry *content* is not validated here. `AppConfig` rejects blank and
    # whitespace-only names on load (F004's 2026-09-06 amendment), and one
    # validator in one place is what keeps `init` and `serve` from drifting
    # into two disagreeing notions of a valid profile name.
    parser.add_argument(
        "--resting-hrv-profile",
        action="append",
        default=[],
        dest="resting_hrv_profile",
        metavar="NAME",
        help=(
            "activity-profile name that means a resting-HRV capture, matched "
            "exactly and case-sensitively against the FIT file's "
            "sport_profile_name. Repeat for each profile; omit entirely to "
            "declare that no profile does (Health Snapshot still routes)."
        ),
    )
    args = parser.parse_args(argv)

    if CONFIG_PATH.exists() and not args.force:
        print(
            f"Error: config already exists at {CONFIG_PATH}. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        tomli_w.dumps(
            {
                "host": args.host,
                "port": args.port,
                "data_dir": args.data_dir,
                "resting_hrv_profile_names": args.resting_hrv_profile,
            }
        ),
        encoding="utf-8",
    )
    print(f"Wrote config to {CONFIG_PATH}")
