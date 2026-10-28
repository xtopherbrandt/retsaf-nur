"""Load the athlete's config pair through the release's models, read-only.

    uv run --package runcoach-api python runcoach-api/tests/support/check_real_config.py
    uv run --package runcoach-api python runcoach-api/tests/support/check_real_config.py \\
        --api path/to/api.toml --cli path/to/cli.toml

Run before a release against the real ``~/.runcoach/api.toml`` and
``~/.runcoach/cli.toml``, so a required field the release adds is found missing
here rather than when ``runcoach-api serve`` refuses to start.

1. Names every ``RUNCOACH_*`` environment variable that is set. Each one
   overrides the matching key of ``api.toml`` in ``runcoach_api.config.load_config``,
   so a set variable can hide a key the file lacks. Names only: values are
   never printed.
2. Loads ``api.toml`` with ``runcoach_api.config.load_config`` and prints
   ``api.toml: OK`` or ``api.toml: ERROR`` followed by the message ``serve``
   prints for the same failure (a validation error is rendered with
   ``runcoach_api.cli._render_validation_error``).
3. Loads ``cli.toml`` with ``runcoach_cli.config.load_config``, its module-level
   ``CONFIG_PATH`` pointed at the file for the call and restored after, and
   prints ``cli.toml: OK`` or ``cli.toml: ERROR`` followed by the message the
   CLI writes to stderr.

It opens no store, starts no server and contacts nothing: both loaders only
read their file. Exit 0 when both files load, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path

import typer
from pydantic import ValidationError
from runcoach_api.cli import _render_validation_error
from runcoach_api.config import (
    CONFIG_PATH as API_CONFIG_PATH,
)
from runcoach_api.config import (
    ConfigCorruptError,
    ConfigNotFoundError,
    load_config,
)
from runcoach_cli import config as cli_config

ENV_PREFIX = "RUNCOACH_"


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


def report_env() -> list[str]:
    """Print and return the names of the set ``RUNCOACH_*`` variables."""
    names = sorted(name for name in os.environ if name.upper().startswith(ENV_PREFIX))
    if not names:
        print("RUNCOACH_* environment variables: none set")
    for name in names:
        print(f"env: {name} is set; it overrides the matching api.toml key")
    return names


def check_api(path: Path) -> bool:
    """Load ``path`` as ``api.toml``; print the verdict and any error."""
    try:
        load_config(path)
    except ConfigNotFoundError as exc:
        message = f"Error: {exc}. Run `runcoach-api init` first."  # as runcoach_api.cli.serve prints it
    except ConfigCorruptError as exc:
        message = f"Error: config file could not be parsed: {exc}"
    except ValidationError as exc:
        message = _render_validation_error(exc)
    else:
        print(f"api.toml: OK ({path})")
        return True
    print(f"api.toml: ERROR ({path})")
    print(_indent(message))
    return False


def check_cli(path: Path) -> bool:
    """Load ``path`` as ``cli.toml``; print the verdict and any error."""
    saved = cli_config.CONFIG_PATH
    stderr = io.StringIO()
    cli_config.CONFIG_PATH = path
    try:
        with contextlib.redirect_stderr(stderr):
            cli_config.load_config()
    except typer.Exit:
        print(f"cli.toml: ERROR ({path})")
        print(_indent(stderr.getvalue().rstrip("\n")))
        return False
    finally:
        cli_config.CONFIG_PATH = saved
    print(f"cli.toml: OK ({path})")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", type=Path, default=API_CONFIG_PATH, help="api.toml to load")
    parser.add_argument("--cli", type=Path, default=cli_config.CONFIG_PATH, help="cli.toml to load")
    args = parser.parse_args(argv)

    report_env()
    api_ok = check_api(args.api)
    cli_ok = check_cli(args.cli)
    return 0 if api_ok and cli_ok else 1


if __name__ == "__main__":
    sys.exit(main())
