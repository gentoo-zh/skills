import click


@click.group()
@click.version_option(package_name="gzh")
def cli():
    """gzh — deterministic tooling for gentoo-zh overlay maintenance."""


def main():
    cli()


if __name__ == "__main__":
    main()
