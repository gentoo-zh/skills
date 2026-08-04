import hashlib
import json as _json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from gzh.artifacts import ArtifactError, audit_artifacts
from gzh.binary_qa import inspect_binaries
from gzh.bump import (bump_scaffold, diff_ebuild, highest_ebuild,
                      resolve_package_directory)
from gzh.bump_plan import PACKAGE_MODELS, build_bump_plan
from gzh.bump_issues import (get_issue_updated_at, load_canonical_config,
                             run_bump_issues, write_output)
from gzh.batch_report import (BATCH_UPDATE_STATES, BatchReportConflict,
                              batch_report_digest, checkpoint_batch_report,
                              create_batch_report, reconcile_batch_report,
                              report_sha256, update_batch_outcome,
                              validate_batch_report)
from gzh.buildtest import run_build_test
from gzh.capabilities import inspect_repository, load_bundled_adapter
from gzh.check import Gate, run_read_only_checks
from gzh.commit import run_commit, run_recommit
from gzh.drop_old import run_drop_old
from gzh.ebuild_parser import parse_ebuild
from gzh.deps import (DependencyMetadataError, analyze_ebuild_dependencies,
                      compare_ebuild_dependencies)
from gzh.dependency_query import query_reverse_dependencies
from gzh.executor import (ExecutorError, InstallRequest, create_commit_patch,
                          create_executor, load_executor_config)
from gzh.executor_evidence import verify_evidence
from gzh.github_observation import (GitHubPublicationProvider,
                                    GitHubReadError, read_ci)
from gzh.image_qa import inspect_image
from gzh.lint import lint_ebuild
from gzh.license_inventory import (LicenseInventoryError,
                                   inspect_license_archive)
from gzh.manifest import (run_manifest, verify_manifest_sizes,
                          extract_src_uri_map, _pv_subs)
from gzh.notify import send_telegram
from gzh.nvcheck_audit import run_audit
from gzh.nvchecker_config import get_entry, set_entry
from gzh.package_test import (MAX_USE_COMBOS, USE_PREFERENCES,
                              run_package_test)
from gzh.pkgcheck import (run_pkgcheck, run_pkgcheck_commits,
                          reverify_url_findings)
from gzh.pr_plan import build_pr_plan
from gzh.ci_observation import observe_ci
from gzh.cleanup_plan import analyze_cleanup_dry_run
from gzh.repo import (fetch_canonical_remote, find_canonical_remote,
                      find_overlay_root, validate_canonical_remote)
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


class DependencyGroup(click.Group):
    """Keep the pre-group ebuild form working during the CLI transition."""

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands:
            candidates = [
                Path(value).expanduser() for value in args
                if not value.startswith("-") and value.endswith(".ebuild")
            ]
            if len(candidates) == 1 and candidates[0].is_file():
                args = ["inspect", *args]
        return super().parse_args(ctx, args)


class PkgcheckCompatCommand(click.Command):
    """Accept pkgcheck's explicit scan verb on the legacy command name."""

    def parse_args(self, ctx, args):
        if ctx.info_name == "pkgcheck" and args[:1] == ["scan"]:
            args = args[1:]
        return super().parse_args(ctx, args)


def _pkgcheck_cli_selectors(values):
    selectors = []
    for value in values:
        selectors.extend(value.split(","))
    return tuple(selectors)


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


def _package_test_evidence_dir(atom: str) -> Path:
    safe_atom = re.sub(r"[^A-Za-z0-9._-]+", "-", atom).strip(".-_")[:80]
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return _checked_state_dir() / "evidence" / "tests" / f"{safe_atom}-{timestamp}"


def _build_evidence_dir(ebuild: Path) -> Path:
    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "-", Path(ebuild).stem).strip(".-_")[:80]
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return _checked_state_dir() / "evidence" / "builds" / f"{safe_name}-{timestamp}"


def _write_build_report(report: dict, evidence_dir: Path) -> dict:
    evidence_dir = Path(evidence_dir).expanduser()
    if evidence_dir.is_symlink():
        raise ValueError(f"evidence directory must not be a symlink: {evidence_dir}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if not evidence_dir.is_dir():
        raise ValueError(f"evidence directory is not a directory: {evidence_dir}")
    report_path = evidence_dir.resolve() / "report.json"
    content = (_json.dumps(
        report, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    created = False
    try:
        descriptor = os.open(report_path, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                report_path.unlink()
            except FileNotFoundError:
                pass
        raise
    return {
        "path": str(report_path),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _executor_evidence_dir(atom: str) -> Path:
    safe_atom = re.sub(r"[^A-Za-z0-9._-]+", "-", atom).strip(".-_")[:80]
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return _checked_state_dir() / "evidence" / "executors" / f"{safe_atom}-{timestamp}"


def _default_executor_config() -> Path:
    explicit = os.environ.get("GZH_EXECUTOR_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    config_root = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return config_root / "gzh" / "executors.toml"


def _git_output(root: Path, arguments: list[str]) -> str:
    proc = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True,
        timeout=60)
    if proc.returncode != 0:
        raise click.ClickException(
            proc.stderr.strip() or f"git {' '.join(arguments)} failed")
    return proc.stdout.strip()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise click.ClickException(f"output path already exists: {path}")
    payload = _json.dumps(
        value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise click.ClickException(
                f"output path already exists: {path}") from exc
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _owned_batch_report(path: Path, *, suffixes: set[str]) -> Path:
    report = path.resolve()
    directory = (_checked_state_dir() / "batches").resolve()
    if (report.parent != directory or not report.name.startswith("bump-batch-")
            or report.suffix not in suffixes):
        expected = ", ".join(sorted(suffixes))
        raise click.ClickException(
            f"report must be a gzh batch report ({expected}) under {directory}")
    return report


def _pr_template(root: Path, requested: Path | None) -> str:
    candidates = ([requested] if requested is not None else [
        Path(".github/PULL_REQUEST_TEMPLATE.md"),
        Path(".github/pull_request_template.md"),
    ])
    for relative in candidates:
        assert relative is not None
        path = relative if relative.is_absolute() else root / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (FileNotFoundError, ValueError):
            continue
        if resolved.is_file() and not resolved.is_symlink():
            return resolved.read_text(encoding="utf-8")
    raise click.ClickException("the live pull request template was not found")


def _worktree_inventory(root: Path, publication_items: list[dict]) -> list[dict]:
    raw = _git_output(root, ["worktree", "list", "--porcelain", "-z"])
    records = []
    current: dict[str, object] = {}
    for field in raw.split("\x00"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        if key in {"detached", "bare", "locked", "prunable"} and not value:
            current[key] = True
        else:
            current[key] = value
    if current:
        records.append(current)

    publications = {
        item.get("branch"): item for item in publication_items
        if isinstance(item.get("branch"), str)
    }
    inventory = []
    for record in records:
        path_value = record.get("worktree")
        if not isinstance(path_value, str) or not path_value:
            continue
        path = Path(path_value)
        branch_ref = record.get("branch")
        branch = None
        if isinstance(branch_ref, str) and branch_ref.startswith("refs/heads/"):
            branch = branch_ref.removeprefix("refs/heads/")
        status = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain=v1"],
            capture_output=True, text=True, timeout=60)
        dirty = status.returncode != 0 or bool(status.stdout)
        ahead_proc = subprocess.run(
            ["git", "-C", str(path), "rev-list", "--count", "@{upstream}..HEAD"],
            capture_output=True, text=True, timeout=60)
        ahead = None
        if ahead_proc.returncode == 0 and ahead_proc.stdout.strip().isdigit():
            ahead = int(ahead_proc.stdout.strip())
        matched = publications.get(branch)
        if (ahead is None and matched is not None
                and matched.get("state") == "merged"
                and matched.get("recorded_commit") == record.get("HEAD")):
            ahead = 0
        inventory.append({
            "path": str(path),
            "branch": branch,
            "head_sha": record.get("HEAD"),
            "detached": record.get("detached") is True or branch is None,
            "dirty": dirty,
            "unpushed_commits": ahead,
        })
    return inventory


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


@cli.command("doctor")
@click.option("--repository", type=click.Path(exists=True, file_okay=False,
                                               path_type=Path), default=".",
              show_default=True)
@click.option("--adapter", "adapter_id", default="gentoo-zh", show_default=True)
@click.option("--operation", default="inspect", show_default=True,
              help="adapter operation whose readiness should be reported")
def doctor_cmd(repository, adapter_id, operation):
    """Inspect repository identity, capabilities, Git state, and readiness."""
    try:
        adapter = load_bundled_adapter(adapter_id)
        report = inspect_repository(repository, adapter, operation=operation)
    except (KeyError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.command("check")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--adapter", "adapter_id", default="gentoo-zh", show_default=True)
@click.option("--min-severity", "--exit", "min_severity", default="warning",
              type=click.Choice(["error", "warning", "info", "style"]))
@click.option("--net", is_flag=True, default=False,
              help="enable pkgcheck network checks for the selected target")
@click.option("-p", "--profile", "--profiles", "profiles", multiple=True,
              help="pkgcheck profile selector; repeat to define the QA scope")
@click.option("-a", "--arch", "--arches", "arches", multiple=True,
              help="pkgcheck architecture selector; repeat to define the QA scope")
def check_cmd(target, adapter_id, min_severity, net, profiles, arches):
    """Run the side-effect-free adapter, lint, and pkgcheck gates."""
    target = Path(target).resolve()
    try:
        root = find_overlay_root(target if target.is_dir() else target.parent)
        adapter = load_bundled_adapter(adapter_id)
    except (KeyError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    gates = [
        Gate("doctor", lambda _root: inspect_repository(
            root, adapter, operation="inspect")),
    ]
    if target.is_file() and target.suffix == ".ebuild":
        def run_lint(_root):
            issues = lint_ebuild(
                parse_ebuild(target),
                source_text=target.read_text(encoding="utf-8"))
            return {
                "complete": True,
                "issues": issues,
                "ok": not any(item["severity"] == "error" for item in issues),
                "truncated": False,
            }
        gates.append(Gate("lint", run_lint))
    else:
        gates.append(Gate(
            "lint", runner=None, required=False,
            skip_reason="target is not an ebuild"))
    gates.append(Gate(
        "qa", lambda _root: run_pkgcheck(
            target, min_severity=min_severity, net=net,
            profiles=_pkgcheck_cli_selectors(profiles),
            arches=_pkgcheck_cli_selectors(arches))))
    report = run_read_only_checks(root, gates)
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


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
    issues = lint_ebuild(
        parse_ebuild(ebuild),
        source_text=Path(ebuild).read_text(encoding="utf-8"))
    click.echo(_json.dumps(issues, indent=2, ensure_ascii=False))
    if any(i["severity"] == "error" for i in issues):
        raise SystemExit(1)


@cli.command("license")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False,
                                            path_type=Path))
def license_cmd(archive):
    """Inventory license-like files in a local tar or ZIP archive."""
    try:
        report = inspect_license_archive(archive)
    except LicenseInventoryError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
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


@cli.command("plan")
@click.argument("cat_pkg")
@click.argument("new_pv")
@click.option("--package-model", required=True, type=click.Choice(PACKAGE_MODELS),
              help="reviewed installed-payload model; prebuilt requires asset evidence")
@click.option("--assets-evidence", type=click.Path(exists=True, dir_okay=False,
                                                    path_type=Path),
              default=None,
              help="complete previous/current prebuilt release asset inventory")
def bump_plan_cmd(cat_pkg, new_pv, package_model, assets_evidence):
    """Create a read-only, source-pinned bump plan."""
    try:
        report = build_bump_plan(
            find_overlay_root(), cat_pkg, new_pv,
            package_model=package_model,
            assets_evidence=assets_evidence)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"] or not report["can_apply"]:
        raise SystemExit(1)


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
@click.option("-d", "--distdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="writable distfiles directory passed to pkgdev")
def manifest_cmd(ebuild, distdir):
    """Regenerate the Manifest for an ebuild via pkgdev."""
    res = run_manifest(Path(ebuild).resolve(), cwd=find_overlay_root(),
                       distdir=distdir)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("pkgcheck", cls=PkgcheckCompatCommand)
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--min-severity", "--exit", "min_severity", default="warning",
              type=click.Choice(["error", "warning", "info", "style"]))
@click.option("--net", is_flag=True, default=False,
              help="enable network keychecks (DeadUrl/RedirectedUrl)")
@click.option("-p", "--profile", "--profiles", "profiles", multiple=True,
              help="pkgcheck profile selector; repeat to define the QA scope")
@click.option("-a", "--arch", "--arches", "arches", multiple=True,
              help="pkgcheck architecture selector; repeat to define the QA scope")
def pkgcheck_cmd(path, min_severity, net, profiles, arches):
    """Run pkgcheck scan and print structured results filtered by severity."""
    try:
        res = run_pkgcheck(
            path, min_severity=min_severity, net=net,
            profiles=_pkgcheck_cli_selectors(profiles),
            arches=_pkgcheck_cli_selectors(arches))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"] or not res["complete"]:
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
    try:
        selected_remote = (
            validate_canonical_remote(root, remote) if remote
            else find_canonical_remote(root))
        fetch = fetch_canonical_remote(root, selected_remote)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    scan = run_pkgcheck_commits(root, net=True, remote=selected_remote)
    scan_complete = scan.get("complete", False)
    out = {"scan_ok": scan["ok"], "scan_complete": scan_complete,
           "results": scan["results"], "fetch": fetch, "scan": scan}
    url_blockers = []
    if reverify:
        rv = reverify_url_findings(scan["results"])
        out["url_recheck"] = rv
        url_blockers = rv["confirmed"] + rv["redirected"] + rv["needs_human"]
    else:
        out["url_recheck"] = {"skipped": True}
    click.echo(_json.dumps(out, indent=2, ensure_ascii=False))
    if not scan["ok"] or not scan_complete or not reverify or url_blockers:
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
    if not res["ok"] or not res["complete"]:
        raise SystemExit(1)


@cli.command("artifacts")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False,
                                             path_type=Path))
@click.option("--evidence", type=click.Path(exists=True, dir_okay=False,
                                             path_type=Path), required=True,
              help="reviewed JSON mapping for every DIST entry")
@click.option("--distdir", type=click.Path(exists=True, file_okay=False,
                                            path_type=Path), default=None,
              help="verify local DIST files and Manifest digests")
def artifacts_cmd(manifest, evidence, distdir):
    """Audit every Manifest DIST entry and its reviewed source mapping."""
    try:
        report = audit_artifacts(manifest, evidence=evidence, distdir=distdir)
    except (ArtifactError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.group("deps", cls=DependencyGroup)
def deps_group():
    """Inspect and compare verified Portage dependency metadata."""


@deps_group.command("inspect")
@click.argument("ebuild", type=click.Path(exists=True, dir_okay=False,
                                           path_type=Path))
@click.option("--use", "use_flags", multiple=True,
              help="explicit +FLAG or -FLAG state; repeat for every referenced flag")
@click.option("--resolve-providers", is_flag=True, default=False,
              help="query the active Portage repository set for matching providers")
def deps_inspect_cmd(ebuild, use_flags, resolve_providers):
    """Analyze verified Portage cache metadata without sourcing an ebuild."""
    try:
        report = analyze_ebuild_dependencies(
            ebuild,
            use=list(use_flags) if use_flags else None,
            resolve_providers=resolve_providers,
        )
    except (DependencyMetadataError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@deps_group.command("diff")
@click.argument("old_ebuild", type=click.Path(exists=True, dir_okay=False,
                                               path_type=Path))
@click.argument("new_ebuild", type=click.Path(exists=True, dir_okay=False,
                                               path_type=Path))
@click.option("--use", "use_flags", multiple=True,
              help="shared explicit +FLAG or -FLAG state; repeat for every flag")
def deps_diff_cmd(old_ebuild, new_ebuild, use_flags):
    """Compare old and new verified dependency declarations."""
    try:
        report = compare_ebuild_dependencies(
            old_ebuild,
            new_ebuild,
            use=list(use_flags) if use_flags else None,
        )
    except (DependencyMetadataError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@deps_group.command("reverse")
@click.argument("atom")
def deps_reverse_cmd(atom):
    """List raw potential direct reverse dependencies from ebuild repos."""
    report = query_reverse_dependencies(atom)
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.command("binary")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--expected-machine", default=None,
              help="exact readelf Machine value required for every ELF")
@click.option("--max-files", default=4096, show_default=True,
              type=click.IntRange(1, 4096))
def binary_cmd(target, expected_machine, max_files):
    """Inspect ELF metadata without executing target binaries."""
    try:
        report = inspect_binaries(
            target, expected_machine=expected_machine, max_files=max_files)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.command("image")
@click.argument("root", type=click.Path(exists=True, file_okay=False,
                                         path_type=Path))
@click.option("--expected-machine", default=None,
              help="exact readelf Machine value required for every ELF")
@click.option("--binaries/--no-binaries", default=True, show_default=True)
@click.option("--inventory-evidence", type=click.Path(dir_okay=False,
                                                       path_type=Path),
              default=None,
              help="new relative file for the complete image inventory")
@click.option("--allow-executable", multiple=True,
              help="exact image-relative executable path to allow")
@click.option("--require-non-elf-allowlist", is_flag=True, default=False,
              help="require every executable non-ELF file to be allowlisted")
def image_cmd(root, expected_machine, binaries, inventory_evidence,
              allow_executable, require_non_elf_allowlist):
    """Audit an installed or staged filesystem image without executing it."""
    try:
        report = inspect_image(
            root, include_binaries=binaries,
            expected_machine=expected_machine,
            executable_allowlist=allow_executable,
            require_non_elf_allowlist=require_non_elf_allowlist,
            inventory_evidence=inventory_evidence)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.command("build-test")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
@click.option("--level", default="full",
              type=click.Choice(["none", "quick", "full"]))
@click.option("--logdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="isolated PORTAGE_LOGDIR to retain as evidence")
def build_test_cmd(ebuild, level, logdir):
    """Run a staged ebuild build test (none/quick/full)."""
    selected_logdir = logdir if logdir is not None else _build_evidence_dir(ebuild)
    try:
        res = run_build_test(ebuild, level=level, logdir=selected_logdir)
        res["evidence_report"] = _write_build_report(res, selected_logdir)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)


@cli.command("test")
@click.argument("atom")
@click.option("--execute", "-x", is_flag=True, default=False,
              help="acknowledge Portage configuration changes and package merges")
@click.option("--evidence-dir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="new directory for the bounded test evidence")
@click.option("--job-name", default=None,
              help="pkgdev tatt job name; a unique name is generated by default")
@click.option("--use-combos", default=0, show_default=True,
              type=click.IntRange(0, MAX_USE_COMBOS))
@click.option("--use-preference", default="default", show_default=True,
              type=click.Choice(sorted(USE_PREFERENCES)))
@click.option("--timeout", default=21600, show_default=True,
              type=click.IntRange(1, 86400), help="package test timeout in seconds")
def package_test_cmd(atom, execute, evidence_dir, job_name, use_combos,
                     use_preference, timeout):
    """Run a pkgdev tatt package test and retain bounded evidence."""
    if not execute:
        raise click.UsageError(
            "--execute is required because package testing changes Portage "
            "configuration and merges packages")
    selected_evidence_dir = (
        evidence_dir if evidence_dir is not None
        else _package_test_evidence_dir(atom))
    try:
        report = run_package_test(
            atom, selected_evidence_dir, allow_side_effects=True,
            job_name=job_name, use_combos=use_combos,
            use_preference=use_preference, timeout=timeout)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"] or not report["complete"]:
        raise SystemExit(1)


@cli.command("exec")
@click.argument("atom")
@click.option("--executor", "executor_name", required=True,
              help="named executor from the strict TOML configuration")
@click.option("--config", type=click.Path(exists=True, dir_okay=False,
                                            path_type=Path),
              default=_default_executor_config, show_default=True)
@click.option("--commit", default="HEAD", show_default=True,
              help="full local commit recorded in evidence")
@click.option("--path", "owned_paths", multiple=True,
              help="exact commit-owned path; required and repeatable for SSH")
@click.option("--use", "use_state", multiple=True,
              help="selected +FLAG or -FLAG evidence")
@click.option("--evidence-dir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="new durable evidence directory")
@click.option("--execute", "allow_execute", "-x", is_flag=True, default=False,
              help="acknowledge the authorized pretend plan and exact package merge")
def executor_cmd(atom, executor_name, config, commit, owned_paths, use_state,
                 evidence_dir, allow_execute):
    """Run the local or configured SSH install contract with durable evidence."""
    if not allow_execute:
        raise click.UsageError(
            "--execute is required because the executor installs dependencies "
            "and merges the exact package")
    root = find_overlay_root()
    resolved_commit = _git_output(root, ["rev-parse", "--verify", f"{commit}^{{commit}}"])
    selected_evidence = evidence_dir or _executor_evidence_dir(atom)
    try:
        specs = load_executor_config(config)
        if executor_name not in specs:
            raise click.ClickException(
                f"executor is not configured: {executor_name}")
        spec = specs[executor_name]
        if spec.type == "local" and owned_paths:
            raise click.UsageError("--path applies only to SSH executors")
        if spec.type == "ssh" and not owned_paths:
            raise click.UsageError(
                "SSH execution requires every commit-owned path through --path")
        with tempfile.TemporaryDirectory(prefix="gzh-owned-patch-") as temporary:
            transfer = None
            if spec.type == "ssh":
                transfer = create_commit_patch(
                    root, resolved_commit, owned_paths,
                    Path(temporary) / "owned.patch")
            report = create_executor(spec).execute(InstallRequest(
                atom=atom,
                commit=resolved_commit,
                evidence_dir=selected_evidence,
                use_state=tuple(use_state),
                transfer=transfer,
                repository=root if spec.type == "local" else None,
            ))
    except click.ClickException:
        raise
    except (ExecutorError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    verification = verify_evidence(
        selected_evidence, expected_digest=report.get("digest"))
    output = {"execution": report, "verification": verification}
    click.echo(_json.dumps(output, indent=2, ensure_ascii=False))
    if not report.get("ok") or not verification["ok"]:
        raise SystemExit(1)


@cli.command("verify-install")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
@click.option("--logdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="isolated PORTAGE_LOGDIR to retain as evidence")
@click.option("--allow-plan-package", "authorized_packages", multiple=True,
              help="category/package allowed to rebuild, upgrade, or downgrade")
def verify_install_cmd(ebuild, logdir, authorized_packages):
    """Merge an exact ebuild and fail on the overlay CI elog classes."""
    res = run_verify_install(
        ebuild, logdir=logdir, authorized_packages=authorized_packages)
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
@click.option("--autobump", default="any", show_default=True,
              type=click.Choice(["any", "off", "on", "manual-required"]),
              help="select from canonical autobump config or current bot status")
@click.option("--issue", "issues", multiple=True, type=click.IntRange(min=1),
              help="include an exact issue with complete revision evidence")
@click.option("--issue-mode", default="include", show_default=True,
              type=click.Choice(["include", "exact"]),
              help="union explicit issues with the queue or select only them")
@click.option("--git-remote", default=None,
              help="fetched canonical remote used for selector provenance")
@click.option("--limit", default=100, show_default=True,
              type=click.IntRange(1, 1000))
@click.option("--no-output", is_flag=True, default=False,
              help="skip writing the state/queues/bump-issues-<ts>.json snapshot")
def bump_issues_cmd(repo, state, maintainer, pkg, comments, autobump, issues,
                    issue_mode, git_remote, limit, no_output):
    """List nvchecker bump-reminder issues as a JSON queue (read-only)."""
    selected_remote = git_remote
    canonical_loader = None
    if autobump != "any" or selected_remote is not None:
        try:
            root = find_overlay_root()
            selected_remote = (
                validate_canonical_remote(root, selected_remote)
                if selected_remote else find_canonical_remote(root))
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        canonical_loader = lambda remote, path, runner: load_canonical_config(
            remote, path, runner, cwd=root, expected_repository=repo)
    res = run_bump_issues(repo=repo, state=state, maintainer=maintainer, pkg=pkg,
                          with_comments=comments, limit=limit,
                          autobump=autobump, issues=issues,
                          issue_mode=issue_mode,
                          canonical_remote=selected_remote,
                          canonical_loader=canonical_loader)
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


@cli.command("pr-plan")
@click.option("--title", required=True, help="exact pkgdev-generated PR title")
@click.option("--body", "body_file", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="file containing the complete body and retained template")
@click.option("--head", "head_branch", default=None,
              help="local topic branch; defaults to the current branch")
@click.option("--base", "base_branch", default="master", show_default=True)
@click.option("--git-remote", default=None,
              help="canonical remote; discovered by URL when omitted")
@click.option("--template", "template_path", default=None,
              type=click.Path(dir_okay=False, path_type=Path),
              help="live PR template path when the repository uses a custom path")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help="new content-addressed plan path")
def pr_plan_cmd(title, body_file, head_branch, base_branch, git_remote,
                template_path, output):
    """Record an immutable PR plan without pushing or calling gh pr create."""
    root = find_overlay_root()
    try:
        remote = (
            validate_canonical_remote(root, git_remote) if git_remote
            else find_canonical_remote(root))
        fetch = fetch_canonical_remote(root, remote)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if base_branch != fetch["default_branch"]:
        raise click.ClickException(
            f"PR base must be the fetched canonical branch "
            f"{fetch['default_branch']!r}")
    selected_head = head_branch or _git_output(
        root, ["symbolic-ref", "--short", "HEAD"])
    head_sha = _git_output(
        root, ["rev-parse", "--verify", f"refs/heads/{selected_head}^{{commit}}"])
    base_sha = _git_output(
        root, ["rev-parse", "--verify", f"{remote}/{base_branch}^{{commit}}"])
    if base_sha != fetch["after_oid"]:
        raise click.ClickException(
            f"fetched {remote}/{base_branch} does not match its recorded OID")
    changed = _git_output(root, [
        "diff", "--name-only", "--diff-filter=ACDMRTUXB",
        f"{base_sha}...{head_sha}", "--",
    ]).splitlines()
    if not changed:
        raise click.ClickException("the PR plan has no changed files")
    try:
        plan = build_pr_plan(
            title=title,
            body=body_file.read_text(encoding="utf-8"),
            files=changed,
            head_branch=selected_head,
            head_sha=head_sha,
            base_branch=base_branch,
            base_sha=base_sha,
            template=_pr_template(root, template_path),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    destination = output or (
        _checked_state_dir() / "plans" / f"{plan['plan_id'].replace(':', '-')}.json")
    _atomic_json(destination, plan)
    click.echo(_json.dumps({
        "path": str(destination.resolve()), "plan": plan,
    }, indent=2, ensure_ascii=False))


@cli.command("ci")
@click.argument("pr_number", type=click.IntRange(min=1))
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--watch", is_flag=True, default=False,
              help="poll until every check reaches a terminal state")
@click.option("--interval", default=15, show_default=True,
              type=click.IntRange(5, 60), help="watch interval in seconds")
@click.option("--timeout", default=3600, show_default=True,
              type=click.IntRange(1, 86400), help="maximum watch duration")
def ci_cmd(pr_number, repo, watch, interval, timeout):
    """Report complete pull request check names, URLs, counts, and final state."""
    deadline = time.monotonic() + timeout
    snapshots = []
    last_signature = None
    while True:
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            snapshot = observe_ci(
                repo, pr_number,
                lambda repository, number: read_ci(repository, number),
                observed_at=observed_at)
        except (GitHubReadError, TypeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        signature = {
            key: value for key, value in snapshot.items()
            if key != "observed_at"
        }
        if signature != last_signature:
            snapshots.append(snapshot)
            last_signature = signature
        terminal = snapshot["checks_state"] in {"passed", "failed"}
        if not watch or terminal or time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    report = {
        "schema_version": 1,
        "watch": watch,
        "timed_out": watch and not terminal,
        "snapshots": snapshots,
        "final": snapshot,
    }
    click.echo(_json.dumps(report, indent=2, ensure_ascii=False))
    if not snapshot["complete"] or snapshot["checks_state"] != "passed":
        raise SystemExit(1)


@cli.group("batch-report")
def batch_report_group():
    """Create and atomically checkpoint durable batch reports."""


@batch_report_group.command("create")
@click.option("--input", "input_stream", type=click.File("r"), default="-",
              show_default=True, help="complete report, or '-' for stdin")
@click.option("--format", "report_format", default="markdown", show_default=True,
              type=click.Choice(["markdown", "json"]),
              help="JSON is required for publication reconciliation")
def batch_report_create_cmd(input_stream, report_format):
    """Create a uniquely named initial report checkpoint."""
    content = input_stream.read()
    if not content.strip():
        raise click.UsageError("batch report input must not be empty")
    suffix = ".md"
    if report_format == "json":
        try:
            value = _json.loads(content)
        except _json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON report: {exc}") from exc
        if not isinstance(value, dict):
            raise click.UsageError("JSON batch report must be an object")
        try:
            validate_batch_report(value)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        content = _json.dumps(
            value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        suffix = ".json"
    path = create_batch_report(
        _checked_state_dir() / "batches", content, suffix=suffix)
    click.echo(_json.dumps({"path": str(path), "sha256": report_sha256(path)},
                           indent=2))


@batch_report_group.command("checkpoint")
@click.argument("report", type=click.Path(exists=True, path_type=Path))
@click.option("--expected-sha256", required=True,
              help="sha256 returned by the preceding create or checkpoint")
@click.option("--input", "input_stream", type=click.File("r"), default="-",
              show_default=True, help="complete Markdown report, or '-' for stdin")
def batch_report_checkpoint_cmd(report, expected_sha256, input_stream):
    """Atomically replace an owned Markdown or JSON batch report."""
    report = _owned_batch_report(report, suffixes={".json", ".md"})
    content = input_stream.read()
    if not content.strip():
        raise click.UsageError("batch report input must not be empty")
    if report.suffix == ".json":
        try:
            value = _json.loads(content)
        except _json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON report: {exc}") from exc
        if not isinstance(value, dict):
            raise click.UsageError("JSON batch report must be an object")
        try:
            validate_batch_report(value)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        content = _json.dumps(
            value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        digest = checkpoint_batch_report(
            report, content, expected_sha256=expected_sha256)
    except (BatchReportConflict, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps({"path": str(report), "sha256": digest}, indent=2))


@batch_report_group.command("update")
@click.argument("report", type=click.Path(exists=True, dir_okay=False,
                                            path_type=Path))
@click.option("--expected-sha256", required=True,
              help="file sha256 returned by the preceding checkpoint")
@click.option("--item-id", required=True, help="stable batch item ID")
@click.option("--state", required=True, type=click.Choice(BATCH_UPDATE_STATES))
@click.option("--reason", required=True, help="evidence-based transition reason")
@click.option("--at", "recorded_at", default=None,
              help="UTC transition time; defaults to the current time")
@click.option("--evidence", type=click.Path(exists=True, dir_okay=False,
                                             path_type=Path), required=True,
              help="JSON object containing transition evidence")
@click.option("--branch", default=None,
              help="exact branch identity when first recording a local commit")
@click.option("--commit", default=None,
              help="full commit identity when first recording a local commit")
def batch_report_update_cmd(report, expected_sha256, item_id, state, reason,
                            recorded_at, evidence, branch, commit):
    """Append one typed batch item outcome through compare-and-swap."""
    report = _owned_batch_report(report, suffixes={".json"})
    current_file_sha = report_sha256(report)
    if current_file_sha != expected_sha256:
        raise click.ClickException(
            f"batch report changed: expected {expected_sha256}, "
            f"found {current_file_sha}")
    try:
        value = _json.loads(report.read_text(encoding="utf-8"))
        evidence_value = _json.loads(evidence.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("batch report must contain a JSON object")
        if not isinstance(evidence_value, dict):
            raise ValueError("outcome transition evidence must be a JSON object")
        timestamp = recorded_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z")
        updated = update_batch_outcome(
            value,
            expected_input_sha256=batch_report_digest(value),
            item_id=item_id,
            state=state,
            at=timestamp,
            reason=reason,
            evidence=evidence_value,
            branch=branch,
            commit=commit,
        )
        content = _json.dumps(
            updated, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        digest = checkpoint_batch_report(
            report, content, expected_sha256=expected_sha256)
    except (_json.JSONDecodeError, BatchReportConflict, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    item = next(item for item in updated["items"] if item["id"] == item_id)
    click.echo(_json.dumps({
        "path": str(report),
        "sha256": digest,
        "item_id": item_id,
        "outcome": item["outcome"],
    }, indent=2, ensure_ascii=False))


@batch_report_group.command("reconcile")
@click.argument("report", type=click.Path(exists=True, dir_okay=False,
                                            path_type=Path))
@click.option("--expected-sha256", required=True,
              help="file sha256 returned by the preceding checkpoint")
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--fork", "fork_repository", default=None,
              help="publication fork owner/name; defaults to the current user fork")
def batch_report_reconcile_cmd(report, expected_sha256, repo, fork_repository):
    """Read GitHub publication state into a structured report checkpoint."""
    report = _owned_batch_report(report, suffixes={".json"})
    current_file_sha = report_sha256(report)
    if current_file_sha != expected_sha256:
        raise click.ClickException(
            f"batch report changed: expected {expected_sha256}, "
            f"found {current_file_sha}")
    try:
        value = _json.loads(report.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("batch report must contain a JSON object")
        provider = GitHubPublicationProvider(
            repo, fork_repository=fork_repository)
        reconciled = reconcile_batch_report(
            value,
            expected_input_sha256=batch_report_digest(value),
            provider=provider,
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        content = _json.dumps(
            reconciled, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        digest = checkpoint_batch_report(
            report, content, expected_sha256=expected_sha256)
    except (BatchReportConflict, GitHubReadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_json.dumps({
        "path": str(report),
        "sha256": digest,
        "observation": reconciled["publication_observations"][-1],
    }, indent=2, ensure_ascii=False))


@cli.group("batch")
def batch_group():
    """Inspect durable batch closeout state without publishing or deleting."""


@batch_group.command("cleanup")
@click.argument("report", type=click.Path(exists=True, dir_okay=False,
                                            path_type=Path))
@click.option("--dry-run", is_flag=True, default=False,
              help="list candidates only; removal is intentionally unsupported")
def batch_cleanup_cmd(report, dry_run):
    """Classify safe worktree cleanup candidates without removing anything."""
    if not dry_run:
        raise click.UsageError(
            "--dry-run is required; this command never removes worktrees or branches")
    report = _owned_batch_report(report, suffixes={".json"})
    try:
        value = _json.loads(report.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError) as exc:
        raise click.ClickException(f"cannot read batch report: {exc}") from exc
    observations = value.get("publication_observations") if isinstance(value, dict) else None
    if not isinstance(observations, list) or not observations:
        raise click.ClickException("batch report has no publication observation")
    latest = observations[-1]
    items = latest.get("items") if isinstance(latest, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise click.ClickException("latest publication observation is incomplete")
    root = find_overlay_root()
    inventory = _worktree_inventory(root, items)
    result = analyze_cleanup_dry_run(inventory, items)
    click.echo(_json.dumps(result, indent=2, ensure_ascii=False))


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
