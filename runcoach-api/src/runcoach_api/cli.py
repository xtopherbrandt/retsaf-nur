import sys


def serve() -> None:
    ...  # filled in by T004


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from runcoach_api.init_cmd import main as init_main

        init_main(sys.argv[2:])
    else:
        serve()
