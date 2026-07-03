import json as _json
from pathlib import Path

import click

from gzh.ebuild_parser import parse_ebuild
from gzh.repo import find_overlay_root


@click.group()
@click.version_option(package_name="gzh")
def cli():
    """gzh — deterministic tooling for gentoo-zh overlay maintenance."""


@cli.command("repo")
def repo_cmd():
    """Print the detected overlay development checkout root."""
    click.echo(str(find_overlay_root()))


@cli.command("ebuild-parse")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def ebuild_parse_cmd(ebuild):
    """Print parsed ebuild variables as JSON."""
    click.echo(_json.dumps(parse_ebuild(ebuild), indent=2, ensure_ascii=False))


def main():
    cli()


if __name__ == "__main__":
    main()
