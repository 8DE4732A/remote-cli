from typer.testing import CliRunner

from remote_cli.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "remote-cli" in result.output
    assert "ssh" in result.output
    assert "exec" in result.output
    assert "snapshot" in result.output
    assert "cp" in result.output
    assert "upload" in result.output
    assert "download" in result.output


def test_cli_session_help():
    result = runner.invoke(app, ["session", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "list" in result.output
    assert "attach" in result.output


def test_cli_cp_help():
    result = runner.invoke(app, ["cp", "--help"])
    assert result.exit_code == 0
    assert "src" in result.output.lower()
    assert "dest" in result.output.lower()
