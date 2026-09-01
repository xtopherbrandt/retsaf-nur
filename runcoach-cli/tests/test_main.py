from typer.testing import CliRunner

from runcoach_cli.main import app

runner = CliRunner()


def test_help_lists_init_and_status():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "status" in result.stdout
