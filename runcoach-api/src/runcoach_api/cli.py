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
    uvicorn.run(app, host=config.host, port=config.port)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from runcoach_api.init_cmd import main as init_main

        init_main(sys.argv[2:])
    else:
        serve()
