import click
import pytest
from click.testing import CliRunner

from gzh.cli import PREFERRED_COMMAND_ALIASES, cli


def test_help_works():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gzh" in result.output.lower()


def test_help_lists_only_preferred_names_for_aliased_commands():
    commands = cli.list_commands(click.Context(cli))

    for alias, legacy_name in PREFERRED_COMMAND_ALIASES.items():
        assert alias in commands
        assert legacy_name not in commands


@pytest.mark.parametrize(
    ("alias", "legacy_name"), PREFERRED_COMMAND_ALIASES.items())
def test_preferred_alias_resolves_to_legacy_command(alias, legacy_name):
    ctx = click.Context(cli)

    assert cli.get_command(ctx, alias) is cli.get_command(ctx, legacy_name)


@pytest.mark.parametrize(
    ("alias", "legacy_name"), PREFERRED_COMMAND_ALIASES.items())
def test_preferred_and_legacy_command_help_work(alias, legacy_name):
    runner = CliRunner()

    assert runner.invoke(cli, [alias, "--help"]).exit_code == 0
    assert runner.invoke(cli, [legacy_name, "--help"]).exit_code == 0
