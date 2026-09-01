import sys

import uvicorn

from runcoach_api.config import load_config
from runcoach_api.main import app


def serve() -> None:
    config = load_config()
    print(f"Starting on {config.host}:{config.port}...", file=sys.stdout)
    uvicorn.run(app, host=config.host, port=config.port)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from runcoach_api.init_cmd import main as init_main

        init_main(sys.argv[2:])
    else:
        serve()
