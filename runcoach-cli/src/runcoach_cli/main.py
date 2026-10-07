import typer

from runcoach_cli.commands import ingest, init, profile, status

app = typer.Typer(no_args_is_help=True)
profile_app = typer.Typer(no_args_is_help=True, help="Show or set the athlete profile and HR anchors.")


@app.callback()
def callback() -> None:
    """runcoach: a CLI for the runcoach training-adaptation API."""


app.command(name="init")(init.init)
app.command(name="status")(status.status)
app.command(name="ingest")(ingest.ingest)
profile_app.command(name="show")(profile.show)
profile_app.command(name="set")(profile.set_profile)
app.add_typer(profile_app, name="profile")


def main():
    app()


if __name__ == "__main__":
    main()
