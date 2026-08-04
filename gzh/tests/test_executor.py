import hashlib
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from gzh.executor import (
    ExecutorAuthorizationError,
    ExecutorConfigError,
    ExecutorSpec,
    ExecutorValidationError,
    InstallRequest,
    LocalExecutor,
    OwnedTransfer,
    SSHExecutor,
    SSHTransport,
    build_remote_command,
    create_commit_patch,
    load_executor_config,
    merge_argv,
    validate_remote_repository,
)
from gzh.executor_evidence import verify_evidence


ATOM = "=app-misc/foo-1.2.3::gentoo-zh"
COMMIT = "b" * 40
PARENT = "a" * 40


def _proc(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class LocalRunner:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.inventory_calls = 0

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args == ["portageq", "envvar", "ARCH"]:
            return _proc(args, stdout="amd64\n")
        if args == ["eselect", "profile", "show"]:
            return _proc(args, stdout="default/linux/amd64/23.0/desktop\n")
        if args == ["qlist", "-IC"]:
            self.inventory_calls += 1
            output = "sys-libs/zlib-1\n"
            if self.inventory_calls > 1:
                output += "dev-libs/new-dependency-2\n"
            return _proc(args, stdout=output)
        if args == ["qlist", "-e", ATOM]:
            return _proc(args, stdout="/usr/bin/foo\n")
        if "--onlydeps" in args:
            if self.failure == "onlydeps":
                return _proc(args, returncode=1, stderr="dependency failure")
            elog = Path(kwargs["env"]["PORTAGE_LOGDIR"]) / "elog"
            (elog / "dependency.log").write_text("ignored dependency warning\n")
            return _proc(args, stdout="dependencies installed\n")
        if args and args[0] == "emerge":
            if self.failure == "elog":
                elog = Path(kwargs["env"]["PORTAGE_LOGDIR"]) / "elog"
                (elog / "app-misc:foo-1.2.3:0.log").write_text("QA Notice\n")
            return _proc(
                args,
                returncode=1 if self.failure == "merge" else 0,
                stdout="warning: normal compiler output\n",
                stderr="merge failed" if self.failure == "merge" else "",
            )
        raise AssertionError(f"unexpected command: {args}")


def _local_spec(allow=True):
    return ExecutorSpec(
        name="local", type="local", allow_dependency_install=allow,
    )


def test_local_executor_preserves_merge_contract_and_console_warning_is_evidence(tmp_path):
    runner = LocalRunner()
    executor = LocalExecutor(_local_spec(), runner=runner)
    result = executor.execute(InstallRequest(
        atom=ATOM,
        commit=COMMIT,
        evidence_dir=tmp_path / "evidence",
        use_state=("+ssl",),
    ))

    emerges = [args for args, _kwargs in runner.calls if args[0] == "emerge"]
    assert emerges == [
        ["emerge", "--usepkg=n", "--usepkgonly=n", "--onlydeps", ATOM],
        ["emerge", "--usepkg=n", "--usepkgonly=n", "--oneshot", "--selective=n", ATOM],
    ]
    assert result["ok"] is True
    assert result["elog_inventory"] == []
    assert result["retained_dependencies"] == ["dev-libs/new-dependency-2"]
    assert "warning: normal compiler output" in (tmp_path / "evidence/logs/final.log").read_text()
    assert result["cleanup"]["ok"] is True
    assert not Path(result["cleanup"]["removed_paths"][0]).exists()
    assert verify_evidence(tmp_path / "evidence")["ok"] is True


@pytest.mark.parametrize(
    ("failure", "failed_step", "merge_count"),
    [("onlydeps", "onlydeps", 1), ("merge", "merge", 2), ("elog", "elog", 2)],
)
def test_local_executor_dependency_merge_and_saved_elog_failures(
    tmp_path, failure, failed_step, merge_count,
):
    runner = LocalRunner(failure)
    result = LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
        atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / failure,
    ))

    assert result["ok"] is False
    assert result["failed_step"] == failed_step
    assert len([args for args, _kwargs in runner.calls if args[0] == "emerge"]) == merge_count
    assert bool(result["elog_inventory"]) is (failure == "elog")


def test_dependency_install_requires_explicit_executor_authorization(tmp_path):
    runner = LocalRunner()
    with pytest.raises(ExecutorAuthorizationError, match="not authorized"):
        LocalExecutor(_local_spec(False), runner=runner).execute(InstallRequest(
            atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / "evidence",
        ))
    assert runner.calls == []


def test_existing_evidence_path_stops_before_executor_side_effects(tmp_path):
    runner = LocalRunner()
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    with pytest.raises(ExecutorValidationError, match="already exists"):
        LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
            atom=ATOM, commit=COMMIT, evidence_dir=evidence,
        ))

    assert runner.calls == []


def test_dangling_evidence_symlink_stops_before_executor_side_effects(tmp_path):
    runner = LocalRunner()
    evidence = tmp_path / "evidence"
    evidence.symlink_to(tmp_path / "missing-target")

    with pytest.raises(ExecutorValidationError, match="already exists"):
        LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
            atom=ATOM, commit=COMMIT, evidence_dir=evidence,
        ))

    assert runner.calls == []


def test_config_is_strict_and_contains_all_connection_values(tmp_path):
    identity = tmp_path / "identity"
    identity.write_text("private key placeholder")
    config = tmp_path / "executors.toml"
    config.write_text(f"""
version = 1

[executors.local]
type = "local"
allow_dependency_install = false

[executors.builder]
type = "ssh"
host = "build.example"
user = "portage"
port = 2200
identity_file = "{identity}"
remote_overlay_path = "/srv/overlay"
allow_dependency_install = true
""")
    specs = load_executor_config(config)

    assert specs["local"].allow_dependency_install is False
    assert specs["builder"].host == "build.example"
    assert specs["builder"].remote_overlay_path == PurePosixPath("/srv/overlay")

    config.write_text(config.read_text() + "unknown = true\n")
    with pytest.raises(ExecutorConfigError, match="unknown executor builder fields"):
        load_executor_config(config)


def test_exact_atom_is_structured_and_always_posix_quoted():
    assert merge_argv(ATOM, onlydeps=True)[-1] == ATOM
    command = build_remote_command(
        merge_argv(ATOM, onlydeps=False),
        cwd=PurePosixPath("/srv/overlay"),
        environment={"PORTAGE_ELOG_CLASSES": "qa warn error"},
    )
    assert f"'{ATOM}'" in command
    assert "'PORTAGE_ELOG_CLASSES'" not in command
    with pytest.raises(ValueError, match="exact"):
        merge_argv("app-misc/foo", onlydeps=True)


def test_ssh_transport_uses_no_local_shell_or_unrestricted_environment(tmp_path, monkeypatch):
    identity = tmp_path / "id"
    identity.write_text("key")
    seen = []

    def runner(args, **kwargs):
        seen.append((args, kwargs))
        return _proc(args, stdout="ok\n")

    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    spec = ExecutorSpec(
        name="builder", type="ssh", allow_dependency_install=True,
        host="private.example", user="portage", port=2200,
        identity_file=identity, remote_overlay_path=PurePosixPath("/srv/overlay"),
    )
    SSHTransport(spec, runner=runner).run(
        merge_argv(ATOM, onlydeps=False),
        cwd=PurePosixPath("/srv/overlay"),
        environment={"PORTAGE_ELOG_SYSTEM": "save"},
    )

    argv, kwargs = seen[0]
    assert kwargs.get("shell") is None
    assert kwargs["env"].get("GITHUB_TOKEN") is None
    assert f"'{ATOM}'" in argv[-1]
    assert argv.count("portage@private.example") == 1
    assert argv[-3:] == ["--", "portage@private.example", argv[-1]]

    if shutil.which("ssh"):
        parsed = subprocess.run(
            [argv[0], "-G", *argv[1:]], capture_output=True, text=True,
            timeout=10,
        )
        assert parsed.returncode == 0, parsed.stderr
        assert "hostname private.example" in parsed.stdout


@pytest.mark.parametrize("length", [39, 41, 63, 65])
def test_executor_rejects_impossible_git_oid_lengths(tmp_path, length):
    with pytest.raises(ExecutorValidationError, match="commit"):
        LocalExecutor(_local_spec(), runner=LocalRunner()).execute(InstallRequest(
            atom=ATOM, commit="a" * length, evidence_dir=tmp_path / str(length),
        ))


class FakeTransport:
    def __init__(self, *, path="/srv/overlay", canonical=True, failure=None,
                 dirty=False):
        self.path = path
        self.canonical = canonical
        self.failure = failure
        self.dirty = dirty
        self.events = []
        self.inventory_calls = 0
        self.patch_sha256 = ""

    def upload_file(self, source, destination):
        self.events.append(("upload", str(source), str(destination)))
        self.patch_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    def run(self, argv, *, cwd=None, environment=None):
        argv = list(argv)
        self.events.append(("run", argv, cwd, dict(environment or {})))
        if argv == ["portageq", "get_repo_path", "/", "gentoo-zh"]:
            return _proc(argv, stdout=f"{self.path}\n")
        if argv == ["git", "rev-parse", "--show-toplevel"]:
            return _proc(argv, stdout="/srv/overlay\n")
        if argv == ["git", "remote", "-v"]:
            url = ("git@github.com:gentoo-zh/overlay.git" if self.canonical
                   else "git@github.com:someone/fork.git")
            return _proc(argv, stdout=f"origin\t{url} (fetch)\n")
        if argv == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _proc(argv, stdout=" M eclass/example.eclass\n" if self.dirty else "")
        if argv == ["git", "rev-parse", "HEAD"]:
            return _proc(argv, stdout=f"{PARENT}\n")
        if argv[:3] == ["git", "apply", "--reverse"] and self.failure == "cleanup":
            return _proc(argv, returncode=1, stderr="reverse failed")
        if argv[0] in {"mkdir", "git", "rm"}:
            return _proc(argv)
        if argv[0] == "sha256sum":
            return _proc(argv, stdout=f"{self.patch_sha256}  {argv[-1]}\n")
        if argv == ["portageq", "envvar", "ARCH"]:
            return _proc(argv, stdout="amd64\n")
        if argv == ["eselect", "profile", "show"]:
            return _proc(argv, stdout="default/linux/amd64/23.0/desktop\n")
        if argv == ["qlist", "-IC"]:
            self.inventory_calls += 1
            value = "sys-libs/zlib-1\n"
            if self.inventory_calls > 1:
                value += "dev-libs/new-dependency-2\n"
            return _proc(argv, stdout=value)
        if argv == ["qlist", "-e", ATOM]:
            return _proc(argv, stdout="/usr/bin/foo\n")
        if "--onlydeps" in argv:
            return _proc(
                argv, returncode=1 if self.failure == "onlydeps" else 0,
                stderr="deps failed" if self.failure == "onlydeps" else "",
            )
        if argv and argv[0] == "emerge":
            return _proc(
                argv, returncode=1 if self.failure == "merge" else 0,
                stdout="warning: phase output\n",
            )
        if argv[0] == "find" and "-delete" in argv:
            return _proc(argv)
        if argv[0] == "find" and "-printf" in argv:
            output = "app-misc:foo-1.2.3:0.log\n" if self.failure == "elog" else ""
            return _proc(argv, stdout=output)
        if argv[0] == "stat":
            return _proc(argv, stdout="10\n")
        if argv[0] == "head":
            return _proc(argv, stdout="QA Notice\n")
        raise AssertionError(f"unexpected remote command: {argv}")


def _ssh_spec():
    return ExecutorSpec(
        name="builder", type="ssh", allow_dependency_install=True,
        host="build.example", user="portage", port=2200,
        identity_file=Path("/private/id"),
        remote_overlay_path=PurePosixPath("/srv/overlay"),
    )


def _transfer(tmp_path):
    patch = tmp_path / "owned.patch"
    patch.write_text("patch evidence\n")
    return OwnedTransfer(
        commit=COMMIT, parent=PARENT,
        paths=("app-misc/foo/foo-1.2.3.ebuild",),
        patch=patch,
        sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
        size=patch.stat().st_size,
    )


@pytest.mark.parametrize(
    ("failure", "failed_step"),
    [(None, None), ("onlydeps", "onlydeps"), ("merge", "merge"), ("elog", "elog")],
)
def test_ssh_executor_validates_transfers_persists_and_cleans_exact_paths(
    tmp_path, failure, failed_step,
):
    transport = FakeTransport(failure=failure)
    result = SSHExecutor(_ssh_spec(), transport=transport).execute(InstallRequest(
        atom=ATOM,
        commit=COMMIT,
        evidence_dir=tmp_path / "evidence",
        transfer=_transfer(tmp_path),
    ))

    upload_index = next(i for i, event in enumerate(transport.events) if event[0] == "upload")
    prior_commands = [event[1] for event in transport.events[:upload_index] if event[0] == "run"]
    assert ["portageq", "get_repo_path", "/", "gentoo-zh"] in prior_commands
    assert ["git", "remote", "-v"] in prior_commands
    assert result["failed_step"] == failed_step
    assert result["executor"] == {"type": "ssh", "name": "builder"}
    assert result["provenance"]["owned_transfer"]["sha256"] == _transfer(
        tmp_path).sha256
    assert result["provenance"]["remote_repository"]["clean"] is True
    assert verify_evidence(tmp_path / "evidence")["ok"] is True
    cleanup_commands = [
        event[1] for event in transport.events
        if event[0] == "run" and event[1][0] in {"git", "rm"}
    ]
    assert any(command[:3] == ["git", "apply", "--reverse"] for command in cleanup_commands)
    assert any(command[:3] == ["rm", "-rf", "--"] for command in cleanup_commands)
    assert not any("clean" in command or "checkout" in command or "reset" in command
                   for command in cleanup_commands)


def test_failed_owned_patch_restoration_retains_recovery_patch(tmp_path):
    transport = FakeTransport(failure="cleanup")
    result = SSHExecutor(_ssh_spec(), transport=transport).execute(InstallRequest(
        atom=ATOM,
        commit=COMMIT,
        evidence_dir=tmp_path / "evidence",
        transfer=_transfer(tmp_path),
    ))

    assert result["failed_step"] == "cleanup"
    assert result["cleanup"]["restored_paths"] == []
    assert len(result["cleanup"]["retained_paths"]) == 1
    assert not any(event[0] == "run" and event[1][:3] == ["rm", "-rf", "--"]
                   for event in transport.events)


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        (FakeTransport(path="/wrong/path"), "does not match portageq"),
        (FakeTransport(canonical=False), "no fetch remote"),
    ],
)
def test_remote_path_and_canonical_identity_are_hard_gates(transport, message):
    with pytest.raises(ExecutorValidationError, match=message):
        validate_remote_repository(_ssh_spec(), transport)
    assert not any(event[0] == "upload" for event in transport.events)


def test_dirty_remote_worktree_stops_before_patch_upload(tmp_path):
    transport = FakeTransport(dirty=True)

    with pytest.raises(ExecutorValidationError, match="must be clean"):
        SSHExecutor(_ssh_spec(), transport=transport).execute(InstallRequest(
            atom=ATOM,
            commit=COMMIT,
            evidence_dir=tmp_path / "evidence",
            transfer=_transfer(tmp_path),
        ))

    assert not any(event[0] == "upload" for event in transport.events)


def test_commit_patch_is_tied_to_exact_owned_files(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / "one").write_text("before\n")
    (repository / "two").write_text("before\n")
    subprocess.run(["git", "add", "one", "two"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, env=env, check=True)
    (repository / "one").write_text("after\n")
    (repository / "two").write_text("after\n")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=repository, env=env, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    transfer = create_commit_patch(
        repository, commit, ["one", "two"], tmp_path / "owned.patch",
    )
    assert transfer.commit == commit
    assert transfer.paths == ("one", "two")
    assert transfer.sha256 == hashlib.sha256(transfer.patch.read_bytes()).hexdigest()
    with pytest.raises(ExecutorValidationError, match="exactly match"):
        create_commit_patch(repository, commit, ["one"], tmp_path / "partial.patch")
