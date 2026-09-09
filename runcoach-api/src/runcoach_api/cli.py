import argparse
import errno
import socket
import sys

import uvicorn
from pydantic import ValidationError

from runcoach_api.config import ConfigCorruptError, ConfigNotFoundError, load_config
from runcoach_api.main import app

# F004's 2026-09-06 amendment made `resting_hrv_profile_names` a required field
# with no default, which is a *breaking* config change: every `api.toml` written
# before it now fails startup. This message is the whole upgrade path -- it is
# the only thing between the athlete and a server that refuses to start for a
# reason it will not explain -- because pydantic's own `msg` for a missing field
# is the bare "Field required", which names nothing to write.
#
# It states what to *write*. It never states what the system will assume: there
# is no default, and an athlete who uses only Health Snapshot declares that
# explicitly with `[]`. That explicitness is the point of the rule, not an
# inconvenience it imposes.
_MISSING_PROFILE_NAMES_HELP = """\
Error: config field 'resting_hrv_profile_names' is missing, and it has no default.

  It was added on 2026-09-06 and is required, so an api.toml written before then
  must be edited before the server will start. It lists the activity-profile
  names -- matched exactly, case-sensitively -- that declare a recording to be a
  resting-HRV capture.

  Add one line to your api.toml. If a dedicated watch profile records your
  captures, name it:

      resting_hrv_profile_names = ["HRV Snapshot"]

  If no activity profile of yours means a resting-HRV capture, say so with the
  empty list -- that is a declaration, not a default:

      resting_hrv_profile_names = []

  Or set the environment variable instead. Its value is parsed as JSON, so the
  brackets and quotes are required and a bare name will not parse:

      RUNCOACH_RESTING_HRV_PROFILE_NAMES='["HRV Snapshot"]'
      RUNCOACH_RESTING_HRV_PROFILE_NAMES='[]'

  Restart the server after editing api.toml; the config is read once at startup.
  `runcoach-api init --resting-hrv-profile NAME` writes the field for a fresh
  install."""


def _render_validation_error(e: ValidationError) -> str:
    """Turn a config `ValidationError` into one operator-facing message.

    Only the **reported** error is rendered -- `errors()[0]` -- which is the
    behaviour `test_cli_startup` has pinned since T014 and which this function
    deliberately preserves.

    That position is also the gate on the missing-field remediation, and the
    gate has to be there rather than on "does any error name the field".
    Pydantic reports every failure at once, so a config with an out-of-range
    `port` *and* no `resting_hrv_profile_names` yields both errors together; a
    rule keyed on "any" would then answer a port complaint with instructions
    for upgrading a config field, which is confusing at best and misleading at
    worst.

    The error *type* matters as much as the location: `'missing'` is the only
    shape this text is correct advice for. A blank entry (`'value_error'`), a
    bare string instead of a list (`'list_type'`) and an unparseable value all
    name the same field while meaning something else entirely, and each already
    carries its own actionable message from pydantic or from `AppConfig`'s
    validator.
    """
    reported = e.errors()[0]

    if reported["type"] == "missing" and reported["loc"] == (
        "resting_hrv_profile_names",
    ):
        return _MISSING_PROFILE_NAMES_HELP

    field, msg = reported["loc"][0], reported["msg"]
    return f"Error: invalid config field '{field}': {msg}"


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
        print(_render_validation_error(e), file=sys.stderr)
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
    ``--host/--port/--data-dir/--resting-hrv-profile/--athlete-timezone/--force``,
    and restating them here would let the two parsers drift apart. The help
    *string* still names them, because it is the only place
    ``runcoach-api --help`` can advertise what ``init`` accepts.
    """
    parser = argparse.ArgumentParser(
        prog="runcoach-api",
        description="RunCoach backend API. With no subcommand, starts the server.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{init,serve}")
    subparsers.add_parser(
        "init",
        # Comma-separated rather than slash-joined: argparse wraps this line
        # with textwrap, which breaks a long unbreakable token mid-word --
        # slash-joining produced a literal `--resting-\nhrv-profile` in
        # `runcoach-api --help`. Spaces give it legal break points.
        help=(
            "write the API config file "
            "(--host, --port, --data-dir, --resting-hrv-profile, --athlete-timezone, --force)"
        ),
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
