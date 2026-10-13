import typer

from runcoach_cli.commands import imports, ingest, init, loads, profile, sessions, status, sync

app = typer.Typer(no_args_is_help=True)
profile_app = typer.Typer(no_args_is_help=True, help="Show or set the athlete profile and HR anchors.")
history_app = typer.Typer(no_args_is_help=True, help="Add, list or remove dated HR settings.")
loads_app = typer.Typer(no_args_is_help=True, help="Recompute saved session loads.")


@app.callback()
def callback() -> None:
    """runcoach: a CLI for the runcoach training-adaptation API."""


app.command(name="init")(init.init)
app.command(name="status")(status.status)
app.command(name="ingest")(ingest.ingest)
app.command(name="sessions")(sessions.sessions)
app.command(name="import")(imports.import_archive)
app.command(name="import-status")(imports.import_status)
profile_app.command(name="show")(profile.show)
profile_app.command(name="set")(profile.set_profile)
history_app.command(name="add")(profile.history_add)
history_app.command(name="list")(profile.history_list)
history_app.command(name="remove")(profile.history_remove)
profile_app.add_typer(history_app, name="history")
app.add_typer(profile_app, name="profile")
loads_app.command(name="recompute")(loads.recompute)
app.add_typer(loads_app, name="loads")
app.add_typer(sync.sync_app, name="sync")


def main():
    app()


if __name__ == "__main__":
    main()
