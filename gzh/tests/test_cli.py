from click.testing import CliRunner
from gzh.cli import cli


def test_help_works():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gzh" in result.output.lower()
