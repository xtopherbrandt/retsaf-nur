import argparse
import errno
import socket
import sys

import uvicorn
from pydantic import ValidationError

from runcoach_api.config import ConfigCorruptError, ConfigNotFoundError, load_config
from runcoach_api.main import app

# The paragraph every remediation block ends on: what to do once api.toml is
# edited, and the fresh-install path. Stated once because the `init` line has
# to name every flag the command requires (T089 added `--athlete-timezone`),
# and two copies of it would drift the next time a flag is added.
_RESTART_AND_INIT_FOOTER = """\
  Restart the server after editing api.toml; the config is read once at startup.
  For a fresh install, `runcoach-api init` writes every field it asks for:

      runcoach-api init --host HOST --port PORT --data-dir DIR \\
          --resting-hrv-profile NAME --athlete-timezone ZONE"""

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

""" + _RESTART_AND_INIT_FOOTER

# F005 (T088, 2026-09-09) made `athlete_timezone` the second required field
# with no default -- the same breaking shape as the block above, for the same
# reason: pydantic's "Field required" names nothing to write, and this text is
# the upgrade path for every api.toml written before the field existed.
#
# It states what to write and never what would be assumed. A silent "UTC"
# would bucket a 06:00 capture at UTC+13 onto the previous local day with no
# error, which is exactly the failure F001's no-defaults rule exists to
# prevent -- so there is no "say nothing" form here, unlike `[]` above.
_MISSING_ATHLETE_TIMEZONE_HELP = """\
Error: config field 'athlete_timezone' is missing, and it has no default.

  It was added on 2026-09-09 and is required, so an api.toml written before then
  must be edited before the server will start. It is the IANA time zone the
  resting-HRV trend counts your days in: the 7-day window, the 21-day coverage
  gap and same-morning grouping are all measured in this zone's local calendar.

  Add one line to your api.toml, naming the zone as the tz database does:

      athlete_timezone = "Pacific/Auckland"

  Or set the environment variable instead:

      RUNCOACH_ATHLETE_TIMEZONE='Pacific/Auckland'

  An unrecognised zone is refused at startup, not per request. There is no
  default because a wrong zone moves captures across day boundaries silently.

""" + _RESTART_AND_INIT_FOOTER

# The dispatch table: `(type, loc)` -> remediation text. Keyed on the error
# *type* as well as the field because `'missing'` is the only shape either block
# is correct advice for. A blank profile entry (`'value_error'`), a bare string
# instead of a list (`'list_type'`), an unresolvable zone (`'value_error'`) and
# an unparseable value all name one of these fields while meaning something
# else entirely, and each already carries its own actionable message from
# pydantic or from `AppConfig`'s validators -- the terse form below renders it.
_REMEDIATION_BY_ERROR: dict[tuple[str, tuple[str, ...]], str] = {
    ("missing", ("resting_hrv_profile_names",)): _MISSING_PROFILE_NAMES_HELP,
    ("missing", ("athlete_timezone",)): _MISSING_ATHLETE_TIMEZONE_HELP,
}


def _remediation_for(error: dict) -> str | None:
    return _REMEDIATION_BY_ERROR.get((error["type"], tuple(error["loc"])))


def _render_validation_error(e: ValidationError) -> str:
    """Turn a config `ValidationError` into one operator-facing message.

    Two modes, chosen by the **reported** error -- `errors()[0]`, the position
    `test_cli_startup` has pinned since T014:

    * If the reported error has remediation text, render the remediation for
      **every** error that has it, in `errors()` order. With two required
      fields that each carry an upgrade path (T088 added `athlete_timezone`
      beside `resting_hrv_profile_names`) there is no field-declaration order
      that is right for every broken `api.toml`: a file missing both must be
      told about both, and which field pydantic lists first must not decide
      which fix the athlete sees. Iterating the table removes that dependence
      instead of choosing a winner (T081).

    * Otherwise render the reported error alone, in the terse form. The mode
      is gated on `errors()[0]` rather than on "does any error have help"
      because pydantic reports every failure at once: a config with an
      out-of-range `port` *and* no `resting_hrv_profile_names` yields both
      errors together, and answering a port complaint with upgrade
      instructions would be confusing at best and misleading at worst. This
      is why `AppConfig` declares `port` before the remediation-bearing fields
      (`config.py`): the order still decides *which* mode a mixed error lands
      in, even though it no longer decides which remediation is shown.

    Errors after the reported one that carry no remediation (an unknown key,
    say, beside two missing fields) are deferred to the next start, exactly as
    the single-block form always deferred them; the terse form is for what
    pydantic says about the field it reports, never a dump of every internal.
    """
    errors = e.errors()
    reported = errors[0]

    if _remediation_for(reported) is not None:
        blocks = [text for text in map(_remediation_for, errors) if text is not None]
        return "\n\n".join(blocks)

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
