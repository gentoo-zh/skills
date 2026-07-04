import json as _json
from datetime import datetime
from pathlib import Path

import click

from gzh.bump import bump_scaffold, diff_ebuild, highest_ebuild
from gzh.bump_issues import run_bump_issues, write_output
from gzh.buildtest import run_build_test
from gzh.commit import run_commit
from gzh.drop_old import run_drop_old
from gzh.ebuild_parser import parse_ebuild
from gzh.lint import lint_ebuild
from gzh.manifest import run_manifest
from gzh.notify import send_telegram
from gzh.nvcheck_audit import run_audit
from gzh.nvchecker_config import get_entry, set_entry
from gzh.pkgcheck import run_pkgcheck
from gzh.repo import find_overlay_root
from gzh.triage import list_skipped, skip_issue
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


@cli.group("nvchecker-config")
def nvchecker_config_group():
    """Read/write a package's nvchecker entry in overlay.toml."""


@nvchecker_config_group.command("get")
@click.argument("cat_pkg")
def nvchecker_config_get_cmd(cat_pkg):
    """Print a package's nvchecker entry as JSON."""
    root = find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    click.echo(_json.dumps(get_entry(overlay_toml, cat_pkg), indent=2,
                           ensure_ascii=False))


@nvchecker_config_group.command("set")
@click.argument("cat_pkg")
@click.option("--json", "json_entry", help="full entry as JSON")
def nvchecker_config_set_cmd(cat_pkg, json_entry):
    """Write a package's nvchecker entry (updates overlay.toml, preserves comments)."""
    if not json_entry:
        raise click.UsageError("--json is required")
    root = find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    set_entry(overlay_toml, cat_pkg, _json.loads(json_entry))
    click.echo("NOTE: overlay.toml updated. Review the diff.")


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
@click.option("--level", default="full",
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
@click.option("--no-output", is_flag=True, default=False,
              help="skip writing the .gzh/bump-issues-<ts>.json snapshot")
def bump_issues_cmd(repo, state, maintainer, pkg, comments, limit, no_output):
    """List nvchecker bump-reminder issues as a JSON queue (read-only)."""
    res = run_bump_issues(repo=repo, state=state, maintainer=maintainer, pkg=pkg,
                          with_comments=comments, limit=limit)
    exit_code = res.pop("exit_code", 0)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not no_output and res.get("ok"):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = write_output(res, Path.cwd() / ".gzh", ts)
        click.echo(f"wrote {out}", err=True)
    if exit_code:
        raise SystemExit(exit_code)


@cli.group("triage")
def triage_group():
    """Read/write the bump skip log (triage/skip-log.jsonl)."""


@triage_group.command("list")
@click.option("--pkg", default=None, help="filter by cat/pkg")
def triage_list_cmd(pkg):
    """List skipped issues from the skip log."""
    root = find_overlay_root()
    records = list_skipped(root / "triage" / "skip-log.jsonl", pkg=pkg)
    click.echo(_json.dumps(records, indent=2, ensure_ascii=False))


@triage_group.command("skip")
@click.argument("issue", type=int)
@click.option("--cat-pkg", required=True)
@click.option("--target-version", required=True)
@click.option("--reason", required=True)
def triage_skip_cmd(issue, cat_pkg, target_version, reason):
    """Append a skip record to the skip log."""
    root = find_overlay_root()
    rec = skip_issue(root / "triage" / "skip-log.jsonl", issue, cat_pkg,
                     target_version, reason)
    click.echo(_json.dumps(rec, indent=2, ensure_ascii=False))


@cli.group("notify")
def notify_group():
    """Send result notifications (e.g. telegram)."""


@notify_group.command("telegram")
@click.option("--message", "-m", required=True)
@click.option("--chat", "chat_id", default=None, help="override TELEGRAM_CHAT_ID")
def notify_telegram_cmd(message, chat_id):
    """Send a message via Telegram bot (token/chat from env)."""
    res = send_telegram(message, chat_id=chat_id)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    # non-fatal: never exit non-zero (notification is auxiliary)


@cli.command("drop-old")
@click.option("--all", "all_", is_flag=True, default=False, help="scan all packages")
@click.option("--pkg", default=None, help="single cat/pkg")
@click.option("--keep", default=2, show_default=True, type=click.IntRange(min=1))
@click.option("--apply", is_flag=True, default=False,
              help="actually delete + recompute Manifest (default: dry-run)")
def drop_old_cmd(all_, pkg, keep, apply):
    """List or drop old ebuild versions (keep newest N non-liveup; *-9999 kept)."""
    if all_ and pkg:
        raise click.UsageError("--all and --pkg are mutually exclusive")
    if not all_ and not pkg:
        raise click.UsageError("specify --all or --pkg")
    if pkg and "/" not in pkg:
        raise click.UsageError("--pkg must be cat/pkg (e.g. app-misc/foo)")
    target = "all" if all_ else pkg
    res = run_drop_old(target, keep=keep, apply=apply,
                       overlay_root=find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("nvcheck-audit")
@click.option("--apply", is_flag=True, default=False,
              help="write inferred entries to overlay.toml (preserves comments)")
@click.option("--no-filter-system", is_flag=True, default=False,
              help="include acct-*/virtual/* in missing check")
def nvcheck_audit_cmd(apply, no_filter_system):
    """Audit overlay.toml (nvchecker config) vs actual packages; infer upstreams."""
    res = run_audit(apply=apply, filter_system=not no_filter_system,
                    overlay_root=find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)
    if apply and res["missing"]:
        click.echo("NOTE: overlay.toml updated. Review the diff.",
                   err=True)


def main():
    cli()


if __name__ == "__main__":
    main()
