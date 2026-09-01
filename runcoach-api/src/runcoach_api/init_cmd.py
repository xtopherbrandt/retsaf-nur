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
    args = parser.parse_args(argv)

    if CONFIG_PATH.exists() and not args.force:
        print(
            f"Error: config already exists at {CONFIG_PATH}. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        tomli_w.dumps({"host": args.host, "port": args.port, "data_dir": args.data_dir}),
        encoding="utf-8",
    )
    print(f"Wrote config to {CONFIG_PATH}")
