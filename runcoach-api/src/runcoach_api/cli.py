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
        in_use_errnos = {errno.EADDRINUSE}
        wsa = getattr(errno, "WSAEADDRINUSE", None)
        if wsa is not None:
            in_use_errnos.add(wsa)
        if e.errno in in_use_errnos:
            print(f"Error: port {config.port} is already in use.", file=sys.stderr)
            sys.exit(1)
        raise

    uvicorn.run(app, host=config.host, port=config.port)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from runcoach_api.init_cmd import main as init_main

        init_main(sys.argv[2:])
    else:
        serve()
