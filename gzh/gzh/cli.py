import json as _json
from datetime import datetime
from pathlib import Path

import click

from gzh.bump import (bump_scaffold, diff_ebuild, highest_ebuild,
                      resolve_package_directory)
from gzh.bump_issues import (get_issue_updated_at, run_bump_issues,
                             write_output)
from gzh.batch_report import (BatchReportConflict, checkpoint_batch_report,
                              create_batch_report, report_sha256)
from gzh.buildtest import run_build_test
from gzh.commit import run_commit, run_recommit
from gzh.drop_old import run_drop_old
from gzh.ebuild_parser import parse_ebuild
from gzh.lint import lint_ebuild
from gzh.manifest import (run_manifest, verify_manifest_sizes,
                          extract_src_uri_map, _pv_subs)
from gzh.notify import send_telegram
from gzh.nvcheck_audit import run_audit
from gzh.nvchecker_config import get_entry, set_entry
from gzh.pkgcheck import (run_pkgcheck, run_pkgcheck_commits,
                          reverify_url_findings)
from gzh.repo import find_overlay_root
from gzh.state import state_dir
from gzh.triage import TriageConflict, list_skipped, resolve_issue, skip_issue
from gzh.upstream import get_latest_version
from gzh.verify_install import run_verify_install


PREFERRED_COMMAND_ALIASES = {
    "build": "build-test",
    "bump": "bump-scaffold",
    "diff": "diff-ebuild",
    "latest": "upstream-version",
    "merge": "verify-install",
    "parse": "ebuild-parse",
    "qa": "pkgcheck",
    "urls": "pkgcheck-commits",
}


class GzhGroup(click.Group):
    """Expose concise command names while accepting legacy command names."""

    def get_command(self, ctx, cmd_name):
        command_name = PREFERRED_COMMAND_ALIASES.get(cmd_name, cmd_name)
        return super().get_command(ctx, command_name)

    def list_commands(self, ctx):
        commands = super().list_commands(ctx)
        legacy_names = set(PREFERRED_COMMAND_ALIASES.values())
        preferred = [name for name in commands if name not in legacy_names]
        return sorted([*preferred, *PREFERRED_COMMAND_ALIASES])


@click.group(cls=GzhGroup)
@click.version_option(package_name="gzh")
def cli():
    """gzh — deterministic tooling for gentoo-zh overlay maintenance."""


def _checked_state_dir() -> Path:
    try:
        directory = state_dir().resolve()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        overlay = find_overlay_root()
    except RuntimeError:
        return directory
    if directory == overlay or overlay in directory.parents:
        raise click.ClickException(
            f"state directory must be outside the overlay checkout: {directory}")
    return directory


def _require_live_issue_revision(repo: str, issue: int,
                                 expected: str) -> None:
    try:
        current = get_issue_updated_at(repo, issue)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(
            f"cannot verify the live issue revision: {exc}") from exc
    if current != expected:
        raise click.ClickException(
            f"issue changed: expected updated_at {expected}, found {current}; "
            "reload the complete issue before writing triage state")


@cli.command("repo")
def repo_cmd():
    """Print the detected overlay development checkout root."""
    click.echo(str(find_overlay_root()))


@cli.command("state-dir")
def state_dir_cmd():
    """Print the durable gzh state directory."""
    click.echo(str(_checked_state_dir()))


@cli.command("ebuild-parse")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def ebuild_parse_cmd(ebuild):
    """Print parsed ebuild variables as JSON."""
    click.echo(_json.dumps(parse_ebuild(ebuild), indent=2, ensure_ascii=False))


@cli.command("lint")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def lint_cmd(ebuild):
    """Run fast structural checks; pkgcheck and installation remain required."""
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
    try:
        _, pn, pkg_dir = resolve_package_directory(root, cat_pkg)
        dst = bump_scaffold(pkg_dir, pn, new_pv)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
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
@click.option("--distdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="writable distfiles directory passed to pkgdev")
def manifest_cmd(ebuild, distdir):
    """Regenerate the Manifest for an ebuild via pkgdev."""
    res = run_manifest(Path(ebuild).resolve(), cwd=find_overlay_root(),
                       distdir=distdir)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("pkgcheck")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--min-severity", default="warning",
              type=click.Choice(["error", "warning", "info", "style"]))
@click.option("--net", is_flag=True, default=False,
              help="enable network keychecks (DeadUrl/RedirectedUrl)")
def pkgcheck_cmd(path, min_severity, net):
    """Run pkgcheck scan and print structured results filtered by severity."""
    res = run_pkgcheck(path, min_severity=min_severity, net=net)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("pkgcheck-commits")
@click.option("--reverify/--no-reverify", default=True, show_default=True,
              help="classify flagged SRC_URI URLs; skipping cannot pass the gate")
@click.option("--remote", default=None,
              help="canonical remote name; discovered from the remote URLs by default")
def pkgcheck_commits_cmd(reverify, remote):
    """Run the networked commit gate and classify flagged SRC_URI URL responses.

    The follow-up classification does not override pkgcheck's gate result. The overlay's
    own pkgcheck workflow runs offline, so this is not a CI reproduction.
    """
    root = find_overlay_root()
    scan = run_pkgcheck_commits(root, net=True, remote=remote)
    out = {"scan_ok": scan["ok"], "results": scan["results"]}
    url_blockers = []
    if reverify:
        rv = reverify_url_findings(scan["results"])
        out["url_recheck"] = rv
        url_blockers = rv["confirmed"] + rv["redirected"] + rv["needs_human"]
    else:
        out["url_recheck"] = {"skipped": True}
    click.echo(_json.dumps(out, indent=2, ensure_ascii=False))
    if not scan["ok"] or not reverify or url_blockers:
        raise SystemExit(1)


@cli.command("manifest-verify")
@click.argument("manifest", type=click.Path(exists=True, path_type=Path))
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def manifest_verify_cmd(manifest, ebuild):
    """Cross-check large DIST sizes in a Manifest against upstream (truncation guard).

    Best-effort: resolves SRC_URI with simple ${P}/${PV}/... expansion; entries whose URL
    still holds a computed var are skipped (reported in 'checked'). For a definitive check
    of a huge blob, compare the release asset size manually (e.g. gh api ... .assets[].size).
    """
    subs = _pv_subs(Path(ebuild).name)
    src_map = extract_src_uri_map(Path(ebuild).read_text(encoding="utf-8"), subs)
    res = verify_manifest_sizes(Path(manifest).read_text(encoding="utf-8"), src_map)
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


@cli.command("verify-install")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
@click.option("--logdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="isolated PORTAGE_LOGDIR to retain as evidence")
def verify_install_cmd(ebuild, logdir):
    """Merge an exact ebuild and fail on the overlay CI elog classes."""
    res = run_verify_install(ebuild, logdir=logdir)
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


@cli.command("recommit")
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path))
@click.option("--message", "-m", default=None)
def recommit_cmd(paths, message):
    """Rebuild the sole local commit through pkgdev after gate-driven fixes."""
    if not paths:
        raise click.UsageError("at least one path required")
    res = run_recommit(
        list(paths), cwd=find_overlay_root(), message=message)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("bump-issues")
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--state", default="open", show_default=True,
              type=click.Choice(["open", "all", "closed"]))
@click.option("--maintainer", default=None, help="filter by issue body 'CC: @<name>'")
@click.option("--pkg", default=None, help="filter by cat/pkg")
@click.option("--comments/--no-comments", default=True, show_default=True)
@click.option("--limit", default=100, show_default=True,
              type=click.IntRange(1, 1000))
@click.option("--no-output", is_flag=True, default=False,
              help="skip writing the state/queues/bump-issues-<ts>.json snapshot")
def bump_issues_cmd(repo, state, maintainer, pkg, comments, limit, no_output):
    """List nvchecker bump-reminder issues as a JSON queue (read-only)."""
    res = run_bump_issues(repo=repo, state=state, maintainer=maintainer, pkg=pkg,
                          with_comments=comments, limit=limit)
    exit_code = res.pop("exit_code", 0)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not no_output and res.get("ok"):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = write_output(res, _checked_state_dir() / "queues", ts)
        click.echo(f"wrote {out}", err=True)
    if exit_code:
        raise SystemExit(exit_code)


@cli.group("triage")
def triage_group():
    """Read/write the durable bump skip and escalation log."""


@triage_group.command("list")
@click.option("--pkg", default=None, help="filter by cat/pkg")
@click.option("--kind", type=click.Choice(["skip", "escalate", "resolved"]),
              default=None, help="filter by current event kind")
@click.option("--history", is_flag=True, help="show superseded events")
def triage_list_cmd(pkg, kind, history):
    """List skipped/escalated issues from the log."""
    records = list_skipped(
        _checked_state_dir() / "triage" / "skip-log.jsonl",
        pkg=pkg, kind=kind, history=history)
    click.echo(_json.dumps(records, indent=2, ensure_ascii=False))


@triage_group.command("skip")
@click.argument("issue", type=int)
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--cat-pkg", required=True)
@click.option("--target-version", required=True)
@click.option("--issue-updated-at", required=True,
              help="updated_at from the complete issue snapshot")
@click.option("--expected-event-id", required=True,
              help="current event_id, or 'none' when no record exists")
@click.option("--reason", required=True)
@click.option("--kind", type=click.Choice(["skip", "escalate"]), default="skip",
              show_default=True,
              help="skip=sticky (blocked); escalate=revisit when upstream data arrives")
def triage_skip_cmd(issue, repo, cat_pkg, target_version, issue_updated_at,
                    expected_event_id, reason, kind):
    """Append a skip/escalate record to the log."""
    _require_live_issue_revision(repo, issue, issue_updated_at)
    try:
        rec = skip_issue(
            _checked_state_dir() / "triage" / "skip-log.jsonl", issue, cat_pkg,
            target_version, reason, issue_updated_at=issue_updated_at,
            expected_event_id=expected_event_id, kind=kind)
    except (TriageConflict, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        _require_live_issue_revision(repo, issue, issue_updated_at)
    except click.ClickException as exc:
        try:
            resolve_issue(
                _checked_state_dir() / "triage" / "skip-log.jsonl",
                issue, cat_pkg, target_version,
                "Issue revision could not be confirmed after the triage write.",
                issue_updated_at=issue_updated_at,
                expected_event_id=rec["event_id"])
        except (TriageConflict, ValueError) as rollback_exc:
            raise click.ClickException(
                f"{exc}; triage deactivation also failed: {rollback_exc}") from exc
        raise click.ClickException(
            f"{exc}; the new triage event was deactivated") from exc
    click.echo(_json.dumps(rec, indent=2, ensure_ascii=False))


@triage_group.command("resolve")
@click.argument("issue", type=int)
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--cat-pkg", required=True)
@click.option("--target-version", required=True)
@click.option("--issue-updated-at", required=True,
              help="updated_at from the complete issue snapshot")
@click.option("--expected-event-id", required=True,
              help="event_id of the record being superseded")
@click.option("--reason", required=True)
def triage_resolve_cmd(issue, repo, cat_pkg, target_version, issue_updated_at,
                       expected_event_id, reason):
    """Supersede an exact skip or escalation record."""
    _require_live_issue_revision(repo, issue, issue_updated_at)
    try:
        rec = resolve_issue(
            _checked_state_dir() / "triage" / "skip-log.jsonl",
            issue, cat_pkg, target_version, reason,
            issue_updated_at=issue_updated_at,
            expected_event_id=expected_event_id)
    except (TriageConflict, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(rec, indent=2, ensure_ascii=False))


@cli.group("batch-report")
def batch_report_group():
    """Create and atomically checkpoint durable batch reports."""


@batch_report_group.command("create")
@click.option("--input", "input_stream", type=click.File("r"), default="-",
              show_default=True, help="complete Markdown report, or '-' for stdin")
def batch_report_create_cmd(input_stream):
    """Create a uniquely named initial report checkpoint."""
    content = input_stream.read()
    if not content.strip():
        raise click.UsageError("batch report input must not be empty")
    path = create_batch_report(_checked_state_dir() / "batches", content)
    click.echo(_json.dumps({"path": str(path), "sha256": report_sha256(path)},
                           indent=2))


@batch_report_group.command("checkpoint")
@click.argument("report", type=click.Path(exists=True, path_type=Path))
@click.option("--expected-sha256", required=True,
              help="sha256 returned by the preceding create or checkpoint")
@click.option("--input", "input_stream", type=click.File("r"), default="-",
              show_default=True, help="complete Markdown report, or '-' for stdin")
def batch_report_checkpoint_cmd(report, expected_sha256, input_stream):
    """Atomically replace an owned batch report with complete Markdown."""
    report = report.resolve()
    directory = (_checked_state_dir() / "batches").resolve()
    if report.parent != directory or not report.name.startswith("bump-batch-"):
        raise click.ClickException(
            f"report must be a gzh batch report under {directory}")
    content = input_stream.read()
    if not content.strip():
        raise click.UsageError("batch report input must not be empty")
    try:
        digest = checkpoint_batch_report(
            report, content, expected_sha256=expected_sha256)
    except (BatchReportConflict, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps({"path": str(report), "sha256": digest}, indent=2))


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
              help="disabled: deletion requires package-specific review")
def drop_old_cmd(all_, pkg, keep, apply):
    """List version-removal candidates; never delete files."""
    if all_ and pkg:
        raise click.UsageError("--all and --pkg are mutually exclusive")
    if not all_ and not pkg:
        raise click.UsageError("specify --all or --pkg")
    if pkg and "/" not in pkg:
        raise click.UsageError("--pkg must be cat/pkg (e.g. app-misc/foo)")
    if apply:
        raise click.UsageError(
            "--apply is disabled; review package history and reverse dependencies, "
            "then remove explicit files")
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
