import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

import gzh.cli as cli_mod


def test_doctor_cli_prints_adapter_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_bundled_adapter", lambda _identifier: object())
    monkeypatch.setattr(cli_mod, "inspect_repository", lambda *args, **kwargs: {
        "complete": True, "ok": True, "operation": {"ready": True}})
    result = CliRunner().invoke(cli_mod.cli, [
        "doctor", "--repository", str(tmp_path)])
    assert result.exit_code == 0
    assert json.loads(result.output)["operation"]["ready"] is True


def test_check_cli_runs_only_read_only_gates(monkeypatch, tmp_path):
    ebuild = tmp_path / "cat" / "pkg" / "pkg-1.ebuild"
    ebuild.parent.mkdir(parents=True)
    ebuild.write_text("EAPI=8\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda _start=None: tmp_path)
    monkeypatch.setattr(cli_mod, "load_bundled_adapter", lambda _identifier: object())
    monkeypatch.setattr(cli_mod, "inspect_repository", lambda *args, **kwargs: {
        "complete": True, "ok": True, "truncated": False})
    monkeypatch.setattr(cli_mod, "parse_ebuild", lambda _path: {})
    monkeypatch.setattr(cli_mod, "lint_ebuild", lambda _parsed: [])
    monkeypatch.setattr(cli_mod, "run_pkgcheck", lambda *args, **kwargs: {
        "complete": True, "ok": True, "truncated": False})

    result = CliRunner().invoke(cli_mod.cli, ["check", str(ebuild)])
    report = json.loads(result.output)
    assert result.exit_code == 0
    assert [gate["name"] for gate in report["gates"]] == ["doctor", "lint", "qa"]


def test_check_cli_passes_profile_and_arch_scope(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda _start=None: tmp_path)
    monkeypatch.setattr(cli_mod, "load_bundled_adapter", lambda _identifier: object())
    monkeypatch.setattr(cli_mod, "inspect_repository", lambda *args, **kwargs: {
        "complete": True, "ok": True, "truncated": False})

    def fake_pkgcheck(*args, **kwargs):
        calls.append((args, kwargs))
        return {"complete": True, "ok": True, "truncated": False}

    monkeypatch.setattr(cli_mod, "run_pkgcheck", fake_pkgcheck)
    result = CliRunner().invoke(cli_mod.cli, [
        "check", str(tmp_path), "--profile", "stable", "--arch", "amd64",
    ])

    assert result.exit_code == 0, result.output
    assert calls[0][1]["profiles"] == ("stable",)
    assert calls[0][1]["arches"] == ("amd64",)


def test_qa_cli_passes_repeatable_profile_and_arch_scope(monkeypatch, tmp_path):
    calls = []

    def fake_pkgcheck(*args, **kwargs):
        calls.append((args, kwargs))
        return {"complete": True, "ok": True, "truncated": False}

    monkeypatch.setattr(cli_mod, "run_pkgcheck", fake_pkgcheck)
    result = CliRunner().invoke(cli_mod.cli, [
        "qa", str(tmp_path),
        "--profile", "stable", "--profile", "-exp",
        "--arch", "amd64", "--arch", "-x86",
    ])

    assert result.exit_code == 0, result.output
    assert calls == [((tmp_path,), {
        "min_severity": "warning",
        "net": False,
        "profiles": ("stable", "-exp"),
        "arches": ("amd64", "-x86"),
    })]


def test_qa_cli_accepts_pkgcheck_option_spelling_and_comma_syntax(
        monkeypatch, tmp_path):
    calls = []

    def fake_pkgcheck(*args, **kwargs):
        calls.append((args, kwargs))
        return {"complete": True, "ok": True, "truncated": False}

    monkeypatch.setattr(cli_mod, "run_pkgcheck", fake_pkgcheck)
    result = CliRunner().invoke(cli_mod.cli, [
        "qa", str(tmp_path), "--exit", "error",
        "--profiles=stable,-exp", "--arches=amd64,-x86",
    ])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == {
        "min_severity": "error",
        "net": False,
        "profiles": ("stable", "-exp"),
        "arches": ("amd64", "-x86"),
    }


def test_qa_cli_accepts_pkgcheck_short_scope_options(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        cli_mod, "run_pkgcheck",
        lambda *args, **kwargs: calls.append(kwargs) or {
            "complete": True, "ok": True, "truncated": False})

    result = CliRunner().invoke(cli_mod.cli, [
        "qa", str(tmp_path), "-p", "stable", "-a", "amd64",
    ])

    assert result.exit_code == 0, result.output
    assert calls[0]["profiles"] == ("stable",)
    assert calls[0]["arches"] == ("amd64",)


def test_legacy_pkgcheck_command_accepts_scan_verb(monkeypatch, tmp_path):
    calls = []

    def fake_pkgcheck(*args, **kwargs):
        calls.append((args, kwargs))
        return {"complete": True, "ok": True, "truncated": False}

    monkeypatch.setattr(cli_mod, "run_pkgcheck", fake_pkgcheck)
    result = CliRunner().invoke(cli_mod.cli, [
        "pkgcheck", "scan", "--profiles=stable,-exp", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert calls[0][0] == (tmp_path,)
    assert calls[0][1]["profiles"] == ("stable", "-exp")


def test_plan_cli_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: tmp_path)
    monkeypatch.setattr(cli_mod, "build_bump_plan", lambda *args: {
        "can_apply": True, "complete": True, "ok": True, "truncated": False})
    result = CliRunner().invoke(cli_mod.cli, ["plan", "cat/pkg", "2"])
    assert result.exit_code == 0
    assert json.loads(result.output)["can_apply"] is True


def test_artifacts_cli_requires_evidence(tmp_path):
    manifest = tmp_path / "Manifest"
    manifest.write_text("", encoding="utf-8")
    result = CliRunner().invoke(cli_mod.cli, ["artifacts", str(manifest)])
    assert result.exit_code == 2
    assert "--evidence" in result.output


def test_binary_and_image_help_are_available():
    runner = CliRunner()
    assert runner.invoke(cli_mod.cli, ["binary", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["image", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["deps", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["deps", "inspect", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["deps", "diff", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["deps", "reverse", "--help"]).exit_code == 0
    assert runner.invoke(cli_mod.cli, ["test", "--help"]).exit_code == 0


def test_deps_inspect_cli_passes_use_and_provider_scope(monkeypatch, tmp_path):
    ebuild = tmp_path / "demo-1.ebuild"
    ebuild.write_text("EAPI=8\n", encoding="utf-8")
    calls = []

    def fake_analyze(path, **options):
        calls.append((path, options))
        return {"ok": True, "complete": True, "selection": "reduced"}

    monkeypatch.setattr(cli_mod, "analyze_ebuild_dependencies", fake_analyze)
    result = CliRunner().invoke(cli_mod.cli, [
        "deps", "inspect", str(ebuild), "--use", "+ssl", "--use", "-test",
        "--resolve-providers",
    ])

    assert result.exit_code == 0, result.output
    assert calls == [(ebuild, {
        "use": ["+ssl", "-test"],
        "resolve_providers": True,
    })]


def test_legacy_flat_deps_syntax_forwards_without_polluting_json(
        monkeypatch, tmp_path):
    ebuild = tmp_path / "demo-1.ebuild"
    ebuild.write_text("EAPI=8\n", encoding="utf-8")
    calls = []

    def fake_analyze(path, **options):
        calls.append((path, options))
        return {"ok": True, "complete": True, "selection": "potential"}

    monkeypatch.setattr(cli_mod, "analyze_ebuild_dependencies", fake_analyze)
    result = CliRunner().invoke(cli_mod.cli, ["deps", str(ebuild)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["selection"] == "potential"
    assert calls == [(ebuild, {"use": None, "resolve_providers": False})]


def test_legacy_flat_deps_syntax_accepts_options_before_ebuild(
        monkeypatch, tmp_path):
    ebuild = tmp_path / "demo-1.ebuild"
    ebuild.write_text("EAPI=8\n", encoding="utf-8")
    calls = []

    def fake_analyze(path, **options):
        calls.append((path, options))
        return {"ok": True, "complete": True, "selection": "reduced"}

    monkeypatch.setattr(cli_mod, "analyze_ebuild_dependencies", fake_analyze)
    result = CliRunner().invoke(cli_mod.cli, [
        "deps", "--use", "+ssl", "--resolve-providers", str(ebuild),
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["selection"] == "reduced"
    assert calls == [(ebuild, {
        "use": ["+ssl"], "resolve_providers": True,
    })]


def test_deps_diff_cli_uses_old_new_order_and_allows_changes(monkeypatch, tmp_path):
    old = tmp_path / "demo-1.ebuild"
    new = tmp_path / "demo-2.ebuild"
    old.write_text("EAPI=8\n", encoding="utf-8")
    new.write_text("EAPI=8\n", encoding="utf-8")
    calls = []

    def fake_compare(before, after, **options):
        calls.append((before, after, options))
        return {"ok": True, "complete": True, "changed": True}

    monkeypatch.setattr(cli_mod, "compare_ebuild_dependencies", fake_compare)
    result = CliRunner().invoke(cli_mod.cli, [
        "deps", "diff", str(old), str(new), "--use", "+feature",
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed"] is True
    assert calls == [(old, new, {"use": ["+feature"]})]


def test_deps_diff_cli_fails_on_incomplete_evidence(monkeypatch, tmp_path):
    old = tmp_path / "demo-1.ebuild"
    new = tmp_path / "demo-2.ebuild"
    old.write_text("EAPI=8\n", encoding="utf-8")
    new.write_text("EAPI=8\n", encoding="utf-8")
    monkeypatch.setattr(
        cli_mod, "compare_ebuild_dependencies",
        lambda *_args, **_kwargs: {
            "ok": False, "complete": False, "changed": None,
        })

    result = CliRunner().invoke(
        cli_mod.cli, ["deps", "diff", str(old), str(new)])

    assert result.exit_code == 1
    assert json.loads(result.output)["complete"] is False


def test_deps_reverse_cli_preserves_empty_complete_result(monkeypatch):
    calls = []

    def fake_query(atom):
        calls.append(atom)
        return {"ok": True, "complete": True, "state": "complete", "results": []}

    monkeypatch.setattr(cli_mod, "query_reverse_dependencies", fake_query)
    result = CliRunner().invoke(
        cli_mod.cli, ["deps", "reverse", "dev-libs/target"])

    assert result.exit_code == 0
    assert json.loads(result.output)["results"] == []
    assert calls == ["dev-libs/target"]


def test_deps_reverse_cli_fails_on_incomplete_evidence(monkeypatch):
    monkeypatch.setattr(
        cli_mod, "query_reverse_dependencies",
        lambda _atom: {"ok": False, "complete": False, "errors": []})

    result = CliRunner().invoke(
        cli_mod.cli, ["deps", "reverse", "dev-libs/target"])

    assert result.exit_code == 1


def test_package_test_cli_requires_explicit_execution():
    result = CliRunner().invoke(cli_mod.cli, ["test", "=cat/pkg-1"])
    assert result.exit_code == 2
    assert "--execute is required" in result.output


def test_package_test_cli_uses_durable_default_evidence(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli_mod, "_checked_state_dir", lambda: tmp_path / "state")

    def fake_run(atom, evidence_dir, **kwargs):
        calls.append((atom, evidence_dir, kwargs))
        return {"ok": True, "complete": True, "state": "passed"}

    monkeypatch.setattr(cli_mod, "run_package_test", fake_run)
    result = CliRunner().invoke(cli_mod.cli, [
        "test", "=cat/pkg-1", "-x", "--use-combos", "2",
        "--use-preference", "random",
    ])

    assert result.exit_code == 0
    atom, evidence_dir, options = calls[0]
    assert atom == "=cat/pkg-1"
    assert evidence_dir.parent == tmp_path / "state" / "evidence" / "tests"
    assert evidence_dir.name.startswith("cat-pkg-1-")
    assert options["allow_side_effects"] is True
    assert options["use_combos"] == 2
    assert options["use_preference"] == "random"


def test_executor_cli_requires_explicit_execution(tmp_path):
    config = tmp_path / "executors.toml"
    config.write_text("""
version = 1
[executors.local]
type = "local"
allow_dependency_install = true
""", encoding="utf-8")
    result = CliRunner().invoke(cli_mod.cli, [
        "exec", "=cat/pkg-1::gentoo-zh", "--executor", "local",
        "--config", str(config),
    ])
    assert result.exit_code == 2
    assert "--execute is required" in result.output


def test_bump_issues_cli_passes_typed_selector_and_explicit_issues(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: Path("/overlay"))
    monkeypatch.setattr(
        cli_mod, "find_canonical_remote", lambda _root: "canonical")

    def fake_run(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True, "exit_code": 0, "results": [],
            "schema_version": 2,
        }

    monkeypatch.setattr(cli_mod, "run_bump_issues", fake_run)
    result = CliRunner().invoke(cli_mod.cli, [
        "bump-issues", "--autobump", "manual-required",
        "--issue", "12", "--issue", "14", "--no-output",
    ])

    assert result.exit_code == 0, result.output
    assert calls[0]["autobump"] == "manual-required"
    assert calls[0]["issues"] == (12, 14)
    assert calls[0]["canonical_remote"] == "canonical"
    assert callable(calls[0]["canonical_loader"])


def test_ci_cli_preserves_complete_job_names(monkeypatch):
    name = "emerge =cat/pkg-1 (amd64-desktop-systemd complete name)"
    monkeypatch.setattr(cli_mod, "read_ci", lambda repository, number: {
        "complete": True,
        "state": "MERGED",
        "merged": True,
        "merged_at": "now",
        "merge_commit_sha": "b" * 40,
        "head_sha": "a" * 40,
        "url": "https://github.example/pull/1",
        "checks_complete": True,
        "checks": [{
            "name": name,
            "url": "https://github.example/job/1",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        }],
    })
    result = CliRunner().invoke(cli_mod.cli, ["ci", "1"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["final"]["checks"][0]["name"] == name
    assert report["final"]["final_pr_state"] == "merged"


def test_pr_plan_cli_records_content_addressed_local_evidence(monkeypatch, tmp_path):
    repository = tmp_path / "overlay"
    repository.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / ".github").mkdir()
    (repository / ".github/PULL_REQUEST_TEMPLATE.md").write_text(
        "<!-- template -->\n", encoding="utf-8")
    (repository / "package").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "base"], cwd=repository, env=env, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/canonical/master", base],
        cwd=repository, check=True)
    subprocess.run(
        ["git", "switch", "-qc", "cat-pkg-1"], cwd=repository, check=True)
    (repository / "package").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"],
                   cwd=repository, env=env, check=True)
    subprocess.run(["git", "switch", "-q", "-"],
                   cwd=repository, check=True)
    (repository / "canonical-only").write_text("advanced\n", encoding="utf-8")
    subprocess.run(["git", "add", "canonical-only"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "advance canonical"],
                   cwd=repository, env=env, check=True)
    advanced_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "switch", "-q", "cat-pkg-1"],
                   cwd=repository, check=True)
    body = tmp_path / "body.md"
    body.write_text("Closes #1\n\n<!-- template -->\n", encoding="utf-8")
    output = tmp_path / "plan.json"
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: repository)
    monkeypatch.setattr(
        cli_mod, "find_canonical_remote", lambda _root: "canonical")

    fetch_calls = []

    def fake_fetch(root, remote):
        fetch_calls.append((root, remote))
        subprocess.run(
            ["git", "update-ref", "refs/remotes/canonical/master", advanced_base],
            cwd=repository, check=True)
        return {
            "after_oid": advanced_base,
            "default_branch": "master",
            "ok": True,
            "complete": True,
        }

    monkeypatch.setattr(cli_mod, "fetch_canonical_remote", fake_fetch)

    result = CliRunner().invoke(cli_mod.cli, [
        "pr-plan", "--title", "cat/pkg: add 1", "--body", str(body),
        "--output", str(output),
    ])

    assert result.exit_code == 0, result.output
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["head"]["branch"] == "cat-pkg-1"
    assert fetch_calls == [(repository, "canonical")]
    assert advanced_base != base
    assert plan["base"] == {"branch": "master", "sha": advanced_base}
    assert plan["files"] == ["package"]
    assert plan["plan_id"].startswith("pr-plan:")
