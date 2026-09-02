import typer

from runcoach_cli.commands import ingest, init, status

app = typer.Typer(no_args_is_help=True)


@app.callback()
def callback() -> None:
    """runcoach: a CLI for the runcoach training-adaptation API."""


app.command(name="init")(init.init)
app.command(name="status")(status.status)
app.command(name="ingest")(ingest.ingest)


def main():
    app()


if __name__ == "__main__":
    main()
