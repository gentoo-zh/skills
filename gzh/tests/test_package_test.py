from __future__ import annotations

import subprocess
from pathlib import Path

import gzh.package_test as package_test
import pytest
from gzh.package_test import run_package_test


class TattRunner:
    def __init__(self, *, generation_code=0, job_code=0, timeout=False,
                 cleanup_code=0, create_script=True, create_report=True,
                 create_config=False, portage_root=None):
        self.generation_code = generation_code
        self.job_code = job_code
        self.timeout = timeout
        self.cleanup_code = cleanup_code
        self.create_script = create_script
        self.create_report = create_report
        self.create_config = create_config
        self.portage_root = portage_root
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append((args, kwargs))
        if args == ["pkgdev", "--version"]:
            return subprocess.CompletedProcess(args, 0, "pkgdev 0.2.15\n", "")
        if args == ["eselect", "--brief", "profile", "show"]:
            return subprocess.CompletedProcess(
                args, 0, "default/linux/amd64/23.0/desktop\n", "")
        if args == ["portageq", "envvar", "ARCH"]:
            return subprocess.CompletedProcess(args, 0, "amd64\n", "")
        cwd = Path(kwargs["cwd"])
        if args[0] == "rm":
            for value in args[4:]:
                path = Path(value)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            return subprocess.CompletedProcess(args, 0, "removed files\n", "")
        if args[:2] == ["pkgdev", "tatt"]:
            job_name = args[args.index("--job-name") + 1]
            if self.create_config:
                files, directories = package_test._portage_config_paths(job_name)
                for path in files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("generated\n", encoding="utf-8")
                for path in directories:
                    path.mkdir(parents=True, exist_ok=True)
            if self.create_script:
                script = cwd / f"{job_name}.sh"
                script.write_text("#!/bin/sh\n", encoding="utf-8")
                script.chmod(0o700)
            return subprocess.CompletedProcess(
                args, self.generation_code, "generated\n", "")
        script = Path(args[0])
        if args[1:] == ["--clean"]:
            if self.cleanup_code == 0:
                script.unlink(missing_ok=True)
            return subprocess.CompletedProcess(
                args, self.cleanup_code, "cleaned\n", "cleanup failed")
        if self.create_report:
            script.with_suffix(".report").write_text(
                "USE='' succeeded for =cat/pkg-1\n", encoding="utf-8")
        if self.timeout:
            raise subprocess.TimeoutExpired(
                args, kwargs["timeout"], output="partial job output",
                stderr="still running")
        return subprocess.CompletedProcess(
            args, self.job_code, "job output\n", "job failed")


def test_success_records_exact_job_and_environment_evidence(tmp_path):
    runner = TattRunner()
    report = run_package_test(
        "=cat/pkg-1", tmp_path / "evidence", allow_side_effects=True,
        job_name="gzh-pkg-1", use_combos=3, use_preference="default",
        runner=runner)

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["state"] == "passed"
    assert report["tool"]["pkgdev"]["version"] == "pkgdev 0.2.15"
    assert report["environment"]["profile"]["value"].endswith("desktop")
    assert report["environment"]["arch"]["value"] == "amd64"
    generation = report["generation"]["command"]
    assert generation == [
        "pkgdev", "tatt", "--config", "false", "--color", "false",
        "--job-name", "gzh-pkg-1", "--test", "--logs-dir",
        str((tmp_path / "evidence" / "logs").resolve()),
        "--use-combos", "3", "--use-default", "--packages", "=cat/pkg-1",
    ]
    script = str((tmp_path / "evidence" / "gzh-pkg-1.sh").resolve())
    assert report["job"]["command"] == [script]
    assert report["cleanup"]["command"] == [script, "--clean"]
    assert report["artifacts"]["script"]["exists_after_cleanup"] is False
    assert len(report["artifacts"]["script"]["sha256"]) == 64
    assert report["artifacts"]["report"]["content"].startswith("USE=")
    assert all(isinstance(args, list) for args, _kwargs in runner.calls)


def test_side_effects_require_explicit_acknowledgement(tmp_path):
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", job_name="acknowledgement")

    assert report["skipped"] is True
    assert report["state"] == "skipped"
    assert not (tmp_path / "evidence").exists()


def test_dangling_evidence_symlink_stops_before_any_command(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.symlink_to(tmp_path / "missing-target")
    runner = TattRunner()

    with pytest.raises(FileExistsError, match="already exists"):
        run_package_test(
            "cat/pkg", evidence, allow_side_effects=True, runner=runner)

    assert runner.calls == []


@pytest.mark.parametrize("overrides", [
    {"atom": "cat/pkg;command"},
    {"job_name": "../job"},
    {"use_combos": package_test.MAX_USE_COMBOS + 1},
    {"use_preference": "unknown"},
])
def test_invalid_targets_and_options_stop_before_side_effects(tmp_path, overrides):
    options = {
        "atom": "cat/pkg",
        "evidence_dir": tmp_path / "evidence",
        "allow_side_effects": True,
        "job_name": "validated-job",
    }
    options.update(overrides)

    with pytest.raises(ValueError):
        run_package_test(**options)
    assert not (tmp_path / "evidence").exists()


def test_generation_failure_still_runs_generated_script_cleanup(tmp_path):
    runner = TattRunner(generation_code=2)
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="generation-failure", runner=runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["state"] == "generation-failed"
    assert report["job"]["skipped"] is True
    assert report["cleanup"]["returncode"] == 0
    assert report["artifacts"]["script"]["exists_after_cleanup"] is False
    assert any(args[1:] == ["--clean"] for args, _kwargs in runner.calls)


def test_generation_failure_without_script_uses_exact_fallback_cleanup(
        tmp_path, monkeypatch):
    portage_root = tmp_path / "portage"
    monkeypatch.setattr(package_test, "PORTAGE_CONFIG_ROOT", portage_root)
    runner = TattRunner(
        generation_code=2, create_script=False, create_config=True,
        portage_root=portage_root)
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="partial-generation", runner=runner)

    files, directories = package_test._portage_config_paths("partial-generation")
    expected_rm = [
        "rm", "-d", "-f", "--",
        *(str(path) for path in (*files, *directories)),
    ]
    assert report["state"] == "generation-failed"
    assert report["cleanup"]["fallback"] is True
    assert report["cleanup"]["commands"] == [expected_rm]
    assert report["cleanup"]["complete"] is True
    assert report["artifacts"]["portage_config"]["remaining_after_cleanup"] == []
    assert not any(path.exists() for path in (*files, *directories))


def test_preexisting_job_config_blocks_generation_without_removing_it(
        tmp_path, monkeypatch):
    portage_root = tmp_path / "portage"
    monkeypatch.setattr(package_test, "PORTAGE_CONFIG_ROOT", portage_root)
    files, _directories = package_test._portage_config_paths("existing-job")
    files[0].parent.mkdir(parents=True)
    files[0].write_text("owned by another run\n", encoding="utf-8")
    runner = TattRunner()

    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="existing-job", runner=runner)

    assert report["state"] == "config-collision"
    assert report["generation"]["skipped"] is True
    assert files[0].read_text(encoding="utf-8") == "owned by another run\n"
    assert not any(args[:2] == ["pkgdev", "tatt"] for args, _kwargs in runner.calls)


def test_job_failure_is_complete_evidence_and_cleanup_runs(tmp_path):
    runner = TattRunner(job_code=1)
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="job-failure", runner=runner)

    assert report["ok"] is False
    assert report["complete"] is True
    assert report["state"] == "failed"
    assert report["job"]["returncode"] == 1
    assert report["cleanup"]["returncode"] == 0
    assert report["artifacts"]["report"]["exists"] is True


def test_job_timeout_is_incomplete_evidence_and_cleanup_runs(tmp_path):
    runner = TattRunner(timeout=True)
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="job-timeout", timeout=1, runner=runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["timed_out"] is True
    assert report["truncated"] is True
    assert report["state"] == "timed-out"
    assert report["job"]["stdout"] == "partial job output"
    assert report["cleanup"]["returncode"] == 0


def test_cleanup_failure_is_a_hard_failure(tmp_path):
    runner = TattRunner(cleanup_code=1)
    report = run_package_test(
        "cat/pkg", tmp_path / "evidence", allow_side_effects=True,
        job_name="cleanup-failure", runner=runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["state"] == "cleanup-failed"
    assert report["cleanup"]["returncode"] == 1
    assert report["artifacts"]["script"]["exists_after_cleanup"] is True
    assert "generated job cleanup is incomplete" in report["errors"]
