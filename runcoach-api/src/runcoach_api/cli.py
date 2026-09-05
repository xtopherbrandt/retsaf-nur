import argparse
import errno
import socket
import sys

import uvicorn
from pydantic import ValidationError

from runcoach_api.config import ConfigCorruptError, ConfigNotFoundError, load_config
from runcoach_api.main import app


def serve() -> None:
    try:
        config = load_config()
    except ConfigNotFoundError as e:
        print(f"Error: {e}. Run `runcoach-api init` first.", file=sys.stderr)
        sys.exit(1)
    except ConfigCorruptError as e:
        print(f"Error: config file could not be parsed: {e}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as e:
        field, msg = e.errors()[0]["loc"][0], e.errors()[0]["msg"]
        print(f"Error: invalid config field '{field}': {msg}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting on {config.host}:{config.port}...", file=sys.stdout)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((config.host, config.port))
        finally:
            probe.close()
    except OSError as e:
        wsa = getattr(errno, "WSAEADDRINUSE", None)
        if e.errno == errno.EADDRINUSE or (wsa is not None and e.errno == wsa):
            print(f"Error: port {config.port} is already in use.", file=sys.stderr)
            sys.exit(1)
        raise

    uvicorn.run(app, host=config.host, port=config.port)


def build_parser() -> argparse.ArgumentParser:
    """Top-level parser for the ``runcoach-api`` console entry point.

    ``serve`` is additive: a bare ``runcoach-api`` still means ``serve``, so
    existing scripts and habits keep working. The ``init`` subparser
    deliberately declares no flags of its own -- ``init_cmd.main`` owns
    ``--host/--port/--data-dir/--force``, and restating them here would let
    the two parsers drift apart.
    """
    parser = argparse.ArgumentParser(
        prog="runcoach-api",
        description="RunCoach backend API. With no subcommand, starts the server.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{init,serve}")
    subparsers.add_parser(
        "init",
        help="write the API config file (--host/--port/--data-dir/--force)",
        add_help=False,
    )
    subparsers.add_parser(
        "serve",
        help="start the API server (the default when no subcommand is given)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args, extra = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    if args.command == "init":
        # `init` opts out of top-level flag parsing so everything after it is
        # forwarded verbatim to the subcommand's own parser.
        from runcoach_api.init_cmd import main as init_main

        init_main(extra)
        return

    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    serve()
