import typer

from runcoach_cli.commands import init, status

app = typer.Typer(no_args_is_help=True)


@app.callback()
def callback() -> None:
    """runcoach: a CLI for the runcoach training-adaptation API."""


app.command(name="init")(init.init)
app.command(name="status")(status.status)


def main():
    app()


if __name__ == "__main__":
    main()
