import json as _json
from pathlib import Path

import click

from gzh.bump import bump_scaffold, diff_ebuild, highest_ebuild
from gzh.bump_issues import run_bump_issues
from gzh.buildtest import run_build_test
from gzh.commit import run_commit
from gzh.ebuild_parser import parse_ebuild
from gzh.lint import lint_ebuild
from gzh.manifest import run_manifest
from gzh.nvchecker_config import get_entry, set_entry
from gzh.pkgcheck import run_pkgcheck
from gzh.repo import find_overlay_root
from gzh.upstream import get_latest_version


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


@cli.command("lint")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def lint_cmd(ebuild):
    """Lint one ebuild against devmanual rules + gentoo-zh ~arch policy."""
    issues = lint_ebuild(parse_ebuild(ebuild))
    click.echo(_json.dumps(issues, indent=2, ensure_ascii=False))
    if any(i["severity"] == "error" for i in issues):
        raise SystemExit(1)


@cli.command("upstream-version")
@click.argument("cat_pkg")
def upstream_version_cmd(cat_pkg):
    """Look up the latest upstream version for category/package."""
    res = get_latest_version(cat_pkg, find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))


@cli.command("bump-scaffold")
@click.argument("cat_pkg")
@click.argument("new_pv")
def bump_scaffold_cmd(cat_pkg, new_pv):
    """Copy the highest existing ebuild to <pn>-<new_pv>.ebuild."""
    root = find_overlay_root()
    category, pn = cat_pkg.split("/", 1)
    pkg_dir = root / category / pn
    dst = bump_scaffold(pkg_dir, pn, new_pv)
    click.echo(str(dst))


@cli.command("diff-ebuild")
@click.argument("old", type=click.Path(exists=True, path_type=Path))
@click.argument("new", type=click.Path(exists=True, path_type=Path))
def diff_ebuild_cmd(old, new):
    """Print a unified diff between two ebuilds."""
    click.echo(diff_ebuild(old, new), nl=False)


@cli.command("nvchecker-config")
@click.argument("cat_pkg")
@click.argument("action", type=click.Choice(["get", "set"]))
@click.option("--source", help="nvchecker source key, e.g. github/pypi/git")
@click.option("--json", "json_entry", help="full entry as JSON (for set)")
def nvchecker_config_cmd(cat_pkg, action, source, json_entry):
    """Read or write a package's nvchecker entry in overlay.toml."""
    root = find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    if action == "get":
        click.echo(_json.dumps(get_entry(overlay_toml, cat_pkg), indent=2,
                               ensure_ascii=False))
    else:
        if not json_entry:
            raise click.UsageError("--json is required for set")
        set_entry(overlay_toml, cat_pkg, _json.loads(json_entry))
        click.echo("NOTE: overlay.toml rewritten; comments lost. Review the diff.")


@cli.command("manifest")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def manifest_cmd(ebuild):
    """Regenerate the Manifest for an ebuild via pkgdev."""
    res = run_manifest(Path(ebuild).resolve(), cwd=find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("pkgcheck")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--min-severity", default="warning",
              type=click.Choice(["error", "warning", "info", "style"]))
def pkgcheck_cmd(path, min_severity):
    """Run pkgcheck scan and print structured results filtered by severity."""
    res = run_pkgcheck(path, min_severity=min_severity)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("build-test")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
@click.option("--level", default="quick",
              type=click.Choice(["none", "quick", "full"]))
def build_test_cmd(ebuild, level):
    """Run a staged ebuild build test (none/quick/full)."""
    res = run_build_test(ebuild, level=level)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("commit")
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path))
@click.option("--message", "-m", default=None)
def commit_cmd(paths, message):
    """Commit via pkgdev (no AI attribution; gentoo-zh style)."""
    if not paths:
        raise click.UsageError("at least one path required")
    res = run_commit(list(paths), cwd=find_overlay_root(), message=message)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("bump-issues")
@click.option("--repo", default="Gentoo-zh/gentoo-zh", show_default=True)
@click.option("--state", default="open", show_default=True,
              type=click.Choice(["open", "all", "closed"]))
@click.option("--maintainer", default=None, help="filter by issue body 'CC: @<name>'")
@click.option("--pkg", default=None, help="filter by cat/pkg")
@click.option("--comments/--no-comments", default=True, show_default=True)
@click.option("--limit", default=100, show_default=True, type=click.IntRange(1, 100))
def bump_issues_cmd(repo, state, maintainer, pkg, comments, limit):
    """List nvchecker bump-reminder issues as a JSON queue (read-only)."""
    res = run_bump_issues(repo=repo, state=state, maintainer=maintainer, pkg=pkg,
                          with_comments=comments, limit=limit)
    exit_code = res.pop("exit_code", 0)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if exit_code:
        raise SystemExit(exit_code)


def main():
    cli()


if __name__ == "__main__":
    main()
