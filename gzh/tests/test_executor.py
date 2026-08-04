import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

from gzh.executor import (
    ExecutorConfigError,
    ExecutorError,
    ExecutorSpec,
    ExecutorValidationError,
    InstallRequest,
    LocalExecutor,
    MAX_COMMAND_OUTPUT_BYTES,
    OwnedTransfer,
    SSHExecutor,
    SSHTransport,
    build_remote_command,
    create_commit_patch,
    load_executor_config,
    merge_argv,
    validate_remote_repository,
    _executor_plan,
    _REMOTE_ELOG_COLLECTOR,
)
from gzh.executor_evidence import verify_evidence


ATOM = "=app-misc/foo-1.2.3::gentoo-zh"
COMMIT = "b" * 40
PARENT = "a" * 40


def _proc(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class LocalRunner:
    def __init__(
        self, repository=None, failure=None, *, dependencies=True,
        git_root=None, dirty=False, head=COMMIT, canonical=True,
    ):
        self.repository = repository
        self.failure = failure
        self.dependencies = dependencies
        self.git_root = git_root or repository
        self.dirty = dirty
        self.head = head
        self.canonical = canonical
        self.calls = []
        self.inventory_calls = 0

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _proc(args, stdout=f"{self.git_root}\n")
        if args == ["git", "remote", "-v"]:
            url = ("git@github.com:gentoo-zh/overlay.git" if self.canonical
                   else "git@github.com:someone/fork.git")
            return _proc(args, stdout=f"origin\t{url} (fetch)\n")
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _proc(
                args, stdout=" M app-misc/foo/foo-1.2.3.ebuild\n"
                if self.dirty else "")
        if args == ["git", "rev-parse", "HEAD"]:
            return _proc(args, stdout=f"{self.head}\n")
        if args == ["portageq", "envvar", "PORTAGE_REPOSITORIES"]:
            return _proc(args, stdout=(
                "[DEFAULT]\nmain-repo = gentoo\n\n"
                "[gentoo]\nlocation = /var/db/repos/gentoo\n\n"
                "[gentoo-zh]\nlocation = /var/db/repos/gentoo-zh\n"))
        if args == ["portageq", "get_repo_path", "/", "gentoo-zh"]:
            return _proc(args, stdout=f"{self.repository}\n")
        if args == ["portageq", "envvar", "ARCH"]:
            return _proc(args, stdout="amd64\n")
        if args == ["eselect", "--brief", "profile", "show"]:
            return _proc(args, stdout="default/linux/amd64/23.0/desktop\n")
        if args == ["qlist", "-IC"]:
            self.inventory_calls += 1
            output = "sys-libs/zlib-1\n"
            if self.inventory_calls > 1:
                output += "app-misc/foo-1.2.3\n"
                if self.dependencies:
                    output += "dev-libs/new-dependency-2\n"
            return _proc(args, stdout=output)
        if args == ["qlist", "-e", ATOM]:
            return _proc(args, stdout="/usr/bin/foo\n")
        if args and args[0] == "emerge":
            if "--pretend" in args:
                if self.failure == "pretend":
                    return _proc(args, returncode=1, stderr="pretend failed")
                if self.failure == "pretend-large":
                    return _proc(args, stdout="x" * (1024 * 1024))
                dependency = (
                    "[binary  N     ] dev-libs/new-dependency-2::gentoo\n"
                    if self.dependencies else "")
                if self.failure == "plan-upgrade":
                    dependency = (
                        "[binary     U ] dev-libs/existing-2::gentoo [1]\n")
                return _proc(args, stdout=(
                    dependency
                    + "[ebuild  N     ] app-misc/foo-1.2.3::gentoo-zh\n"
                    + f"Total: {2 if dependency else 1} packages\n"))
            if self.failure == "elog":
                elog = Path(kwargs["env"]["PORTAGE_LOGDIR"]) / "elog"
                (elog / "app-misc:foo-1.2.3:0.log").write_text("QA Notice\n")
            if self.failure == "elog-overflow":
                elog = Path(kwargs["env"]["PORTAGE_LOGDIR"]) / "elog"
                for index in range(9):
                    (elog / f"dependency-{index}.log").write_bytes(
                        b"x" * (240 * 1024))
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


def _overlay(tmp_path):
    root = (tmp_path / "overlay").resolve()
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "repo_name").write_text("gentoo-zh\n")
    return root


def test_local_executor_preserves_merge_contract_and_console_warning_is_evidence(tmp_path):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository)
    executor = LocalExecutor(_local_spec(), runner=runner)
    result = executor.execute(InstallRequest(
        atom=ATOM,
        commit=COMMIT,
        evidence_dir=tmp_path / "evidence",
        use_state=("+ssl",),
        repository=repository,
    ))

    emerges = [args for args, _kwargs in runner.calls if args[0] == "emerge"]
    assert emerges == [
        merge_argv(ATOM, pretend=True),
        merge_argv(ATOM, pretend=False),
    ]
    assert result["ok"] is True
    assert result["elog_inventory"] == []
    assert result["retained_dependencies"] == ["dev-libs/new-dependency-2"]
    assert result["provenance"]["plan"]["authorized"] is True
    binding = result["provenance"]["repository_binding"]
    assert binding["complete"] is True
    assert binding["worktree"] == str(repository)
    assert binding["git"] == {
        "path": str(repository),
        "git_root": str(repository),
        "canonical_urls": ["git@github.com:gentoo-zh/overlay.git"],
        "clean": True,
        "head": COMMIT,
        "commit_matches": True,
        "complete": True,
    }
    merge_environment = next(
        kwargs["env"] for args, kwargs in runner.calls
        if args == merge_argv(ATOM, pretend=False))
    assert f"location = {repository}" in merge_environment["PORTAGE_REPOSITORIES"]
    assert "warning: normal compiler output" in (tmp_path / "evidence/logs/final.log").read_text()
    assert result["cleanup"]["ok"] is True
    assert not Path(result["cleanup"]["removed_paths"][0]).exists()
    assert verify_evidence(tmp_path / "evidence")["ok"] is True


@pytest.mark.parametrize(
    ("failure", "failed_step", "merge_count"),
    [
        ("pretend", "pretend", 1),
        ("pretend-large", "evidence", 1),
        ("plan-upgrade", "plan-authorization", 1),
        ("merge", "merge", 2),
        ("elog", "elog", 2),
    ],
)
def test_local_executor_dependency_merge_and_saved_elog_failures(
    tmp_path, failure, failed_step, merge_count,
):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository, failure)
    result = LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
        atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / failure,
        repository=repository,
    ))

    assert result["ok"] is False
    assert result["failed_step"] == failed_step
    assert len([args for args, _kwargs in runner.calls if args[0] == "emerge"]) == merge_count
    assert bool(result["elog_inventory"]) is (failure == "elog")


def test_dependency_install_requires_explicit_executor_authorization(tmp_path):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository)
    result = LocalExecutor(_local_spec(False), runner=runner).execute(InstallRequest(
        atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / "evidence",
        repository=repository,
    ))

    assert result["ok"] is False
    assert result["failed_step"] == "plan-authorization"
    assert result["provenance"]["plan"]["unauthorized"][0]["category"] == (
        "new_dependency")
    assert len([args for args, _kwargs in runner.calls if args[0] == "emerge"]) == 1


def test_executor_without_dependency_authorization_accepts_target_only_plan(tmp_path):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository, dependencies=False)
    result = LocalExecutor(_local_spec(False), runner=runner).execute(InstallRequest(
        atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / "evidence",
        repository=repository,
    ))

    assert result["ok"] is True
    assert result["provenance"]["plan"]["authorization"] == {
        "new_dependency_install": False,
        "rebuild": False,
        "upgrade": False,
        "downgrade": False,
        "other": False,
    }


@pytest.mark.parametrize(
    ("runner_options", "message"),
    [
        ({"dirty": True}, "must be clean"),
        ({"head": "c" * 40}, "does not match the evidence commit"),
        ({"git_root": Path("/wrong/root")}, "not its Git worktree root"),
        ({"canonical": False}, "no fetch remote"),
    ],
)
def test_local_repository_identity_stops_before_portage(
    tmp_path, runner_options, message,
):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository, **runner_options)

    with pytest.raises(ExecutorValidationError, match=message):
        LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
            atom=ATOM, commit=COMMIT, evidence_dir=tmp_path / "evidence",
            repository=repository,
        ))

    assert not any(args[0] in {"portageq", "emerge"} for args, _kwargs in runner.calls)


def _assert_child_stopped(pid_path):
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    for _attempt in range(100):
        try:
            state = (Path("/proc") / str(child_pid) / "stat").read_text(
                encoding="utf-8").split(") ", 1)[1][0]
        except FileNotFoundError:
            return
        if state in {"X", "Z"}:
            return
        time.sleep(0.01)
    pytest.fail("executor child remained running after bounded execution stopped")


def test_local_executor_bounds_flood_output_and_stops_process_group(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "flood"
    child_pid_path = tmp_path / "child.pid"
    executable.write_text("""\
#!/bin/sh
(
    trap '' TERM
    while :; do
        printf '%8192s' x
    done
) &
echo "$!" > "${GZH_TEST_CHILD_PID}"
wait
""", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("GZH_TEST_CHILD_PID", str(child_pid_path))
    executor = LocalExecutor(_local_spec())
    executor.timeout = 10

    started = time.monotonic()
    result = executor._run([str(executable)])
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert result.returncode == 125
    assert "output limit exceeded" in result.stderr
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= (
        MAX_COMMAND_OUTPUT_BYTES)
    _assert_child_stopped(child_pid_path)


def test_local_executor_timeout_stops_process_group(tmp_path, monkeypatch):
    executable = tmp_path / "wait"
    child_pid_path = tmp_path / "child.pid"
    executable.write_text("""\
#!/bin/sh
(
    trap '' TERM
    while :; do
        sleep 60
    done
) &
echo "$!" > "${GZH_TEST_CHILD_PID}"
wait
""", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("GZH_TEST_CHILD_PID", str(child_pid_path))
    executor = LocalExecutor(_local_spec())
    executor.timeout = 1

    started = time.monotonic()
    result = executor._run([str(executable)])
    elapsed = time.monotonic() - started

    assert elapsed < 4
    assert result.returncode == 124
    assert "timed out" in result.stderr
    _assert_child_stopped(child_pid_path)


def test_local_executor_records_aggregate_elog_overflow_before_cleanup(tmp_path):
    repository = _overlay(tmp_path)
    runner = LocalRunner(repository, "elog-overflow")
    evidence = tmp_path / "overflow"

    result = LocalExecutor(_local_spec(), runner=runner).execute(InstallRequest(
        atom=ATOM, commit=COMMIT, evidence_dir=evidence,
        repository=repository,
    ))

    assert result["ok"] is False
    assert result["failed_step"] == "evidence"
    assert result["elog_inventory"] == []
    error = result["provenance"]["collection_errors"][0]
    assert error["type"] == "ExecutorError"
    assert error["message"] == "saved elog evidence exceeds the aggregate limit"
    assert error["message_bytes"] == len(error["message"].encode())
    assert len(error["message_sha256"]) == 64
    assert error["truncated"] is False
    assert len([args for args, _kwargs in runner.calls if args[0] == "emerge"]) == 2
    assert verify_evidence(evidence)["ok"] is True
    assert not Path(result["cleanup"]["removed_paths"][0]).exists()


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
    pretend = merge_argv(ATOM, pretend=True)
    assert pretend[-1] == ATOM
    assert "--ignore-default-opts" in pretend
    assert "--autounmask=n" in pretend
    assert "--usepkg=y" in pretend
    assert f"--usepkg-exclude={ATOM}" in pretend
    assert "--onlydeps" not in pretend
    assert "--usepkg=n" not in pretend
    command = build_remote_command(
        merge_argv(ATOM, pretend=False),
        cwd=PurePosixPath("/srv/overlay"),
        environment={"PORTAGE_ELOG_CLASSES": "qa warn error"},
    )
    assert f"'{ATOM}'" in command
    assert "'PORTAGE_ELOG_CLASSES'" not in command
    with pytest.raises(ValueError, match="exact"):
        merge_argv("app-misc/foo", pretend=True)


@pytest.mark.parametrize(
    ("row", "expected_category", "complete"),
    [
        ("[binary    R    ] dev-libs/existing-2::gentoo", "rebuild", True),
        ("[binary     U   ] dev-libs/existing-2::gentoo", "upgrade", True),
        ("[binary    UD   ] dev-libs/existing-1::gentoo", "downgrade", True),
        ("[binary         ] dev-libs/existing-2::gentoo", "other", True),
        ("[uninstall     ] dev-libs/existing-1", None, False),
        ("[fetch         ] dev-libs/existing-1", None, False),
    ],
)
def test_executor_plan_fails_closed_on_non_install_actions(
    row, expected_category, complete,
):
    plan = _executor_plan(
        "\n".join([
            row,
            "[ebuild  N     ] app-misc/foo-1.2.3::gentoo-zh",
            "Total: 2 packages" if expected_category else "Total: 1 package",
        ]),
        ATOM,
        allow_dependency_install=True,
    )

    assert plan["complete"] is complete
    assert plan["authorized"] is False
    if expected_category is not None:
        assert plan["unauthorized"][0]["category"] == expected_category


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
        merge_argv(ATOM, pretend=False),
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
        self.target_merge_seen = False
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
        if argv == ["eselect", "--brief", "profile", "show"]:
            return _proc(argv, stdout="default/linux/amd64/23.0/desktop\n")
        if argv == ["qlist", "-IC"]:
            self.inventory_calls += 1
            value = "sys-libs/zlib-1\n"
            if self.inventory_calls > 1:
                value += "dev-libs/new-dependency-2\n"
            return _proc(argv, stdout=value)
        if argv == ["qlist", "-e", ATOM]:
            return _proc(argv, stdout="/usr/bin/foo\n")
        if argv and argv[0] == "emerge":
            if "--pretend" in argv:
                if self.failure == "pretend":
                    return _proc(argv, returncode=1, stderr="pretend failed")
                dependency = (
                    "[binary     U ] dev-libs/existing-2::gentoo [1]\n"
                    if self.failure == "plan-upgrade"
                    else "[binary  N     ] dev-libs/new-dependency-2::gentoo\n")
                return _proc(argv, stdout=(
                    dependency
                    + "[ebuild  N     ] app-misc/foo-1.2.3::gentoo-zh\n"
                    + "Total: 2 packages\n"))
            self.target_merge_seen = True
            return _proc(
                argv, returncode=1 if self.failure == "merge" else 0,
                stdout="warning: phase output\n",
            )
        if argv[:2] == ["python3", "-c"]:
            if self.failure == "remote-elog-race":
                return _proc(
                    argv, returncode=2,
                    stderr="remote elog changed while evidence was collected\n")
            if self.failure == "remote-elog-large-error":
                return _proc(argv, returncode=2, stderr="x" * (600 * 1024))
            has_elog = self.failure == "elog" and self.target_merge_seen
            files = ({"app-misc:foo-1.2.3:0.log": base64.b64encode(
                b"QA Notice\n").decode("ascii")} if has_elog else {})
            return _proc(argv, stdout=json.dumps({"files": files}))
        if argv[0] == "find" and "-delete" in argv:
            return _proc(argv)
        if argv[0] == "find" and "-printf" in argv:
            has_elog = self.failure == "elog" and self.target_merge_seen
            output = "app-misc:foo-1.2.3:0.log\n" if has_elog else ""
            return _proc(argv, stdout=output)
        if argv[0] == "stat":
            return _proc(argv, stdout="10\n")
        if argv[0] == "head":
            return _proc(argv, stdout="QA Notice\n")
        raise AssertionError(f"unexpected remote command: {argv}")


def test_local_elog_collection_rejects_path_replacement(tmp_path, monkeypatch):
    elog_dir = tmp_path / "elog"
    elog_dir.mkdir()
    entry = elog_dir / "entry.log"
    outside = tmp_path / "outside.log"
    entry.write_text("expected\n")
    outside.write_text("substituted\n")
    real_open = os.open
    replaced = False

    def replacing_open(path, flags, *args, dir_fd=None, **kwargs):
        nonlocal replaced
        if path == "entry.log" and dir_fd is not None and not replaced:
            entry.unlink()
            entry.symlink_to(outside)
            replaced = True
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(ExecutorError, match="cannot collect saved elog evidence"):
        LocalExecutor(_local_spec())._collect_elogs(elog_dir)


def test_local_elog_collection_rejects_growth_while_reading(tmp_path, monkeypatch):
    elog_dir = tmp_path / "elog"
    elog_dir.mkdir()
    entry = elog_dir / "entry.log"
    entry.write_text("initial\n")
    real_read = os.read
    changed = False

    def growing_read(descriptor, maximum):
        nonlocal changed
        content = real_read(descriptor, maximum)
        if content and not changed:
            with entry.open("ab") as handle:
                handle.write(b"growth\n")
            changed = True
        return content

    monkeypatch.setattr(os, "read", growing_read)

    with pytest.raises(ExecutorError, match="changed while evidence was collected"):
        LocalExecutor(_local_spec())._collect_elogs(elog_dir)


def test_remote_elog_collection_is_one_descriptor_bound_command():
    transport = FakeTransport(failure="elog")
    transport.target_merge_seen = True

    result = SSHExecutor(
        _ssh_spec(), transport=transport)._collect_elogs(
            PurePosixPath("/tmp/gzh-run/logs/elog"))

    assert result == {"app-misc:foo-1.2.3:0.log": b"QA Notice\n"}
    commands = [event[1] for event in transport.events if event[0] == "run"]
    assert len(commands) == 1
    assert commands[0][:2] == ["python3", "-c"]
    assert not any(command[0] in {"find", "stat", "head"} for command in commands)


def test_remote_elog_collection_fails_closed_on_collector_race():
    transport = FakeTransport(failure="remote-elog-race")

    with pytest.raises(ExecutorError, match="changed while evidence was collected"):
        SSHExecutor(
            _ssh_spec(), transport=transport)._collect_elogs(
                PurePosixPath("/tmp/gzh-run/logs/elog"))


def test_ssh_executor_bounds_collector_error_before_cleanup(tmp_path):
    transport = FakeTransport(failure="remote-elog-large-error")
    evidence = tmp_path / "evidence"

    result = SSHExecutor(
        _ssh_spec(), transport=transport).execute(InstallRequest(
            atom=ATOM,
            commit=COMMIT,
            evidence_dir=evidence,
            transfer=_transfer(tmp_path),
        ))

    assert result["ok"] is False
    assert result["failed_step"] == "evidence"
    error = result["provenance"]["collection_errors"][0]
    assert error["type"] == "ExecutorValidationError"
    assert error["message_bytes"] > 600 * 1024
    assert len(error["message"].encode()) <= 4096
    assert len(error["message_sha256"]) == 64
    assert error["truncated"] is True
    assert result["cleanup"]["ok"] is True
    assert verify_evidence(evidence)["ok"] is True


def test_remote_elog_collector_script_reads_regular_files_and_rejects_symlinks(
        tmp_path):
    elog_dir = tmp_path / "elog"
    elog_dir.mkdir()
    (elog_dir / "entry.log").write_bytes(b"QA Notice\n")
    command = [
        sys.executable, "-c", _REMOTE_ELOG_COLLECTOR, str(elog_dir),
        "64", str(256 * 1024), str(1024 * 1024),
    ]

    accepted = subprocess.run(command, capture_output=True, text=True)

    assert accepted.returncode == 0, accepted.stderr
    payload = json.loads(accepted.stdout)
    assert base64.b64decode(
        payload["files"]["entry.log"], validate=True) == b"QA Notice\n"

    (elog_dir / "entry.log").unlink()
    (elog_dir / "entry.log").symlink_to(tmp_path / "outside.log")
    rejected = subprocess.run(command, capture_output=True, text=True)

    assert rejected.returncode != 0
    assert "non-regular" in rejected.stderr


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
    [
        (None, None),
        ("pretend", "pretend"),
        ("plan-upgrade", "plan-authorization"),
        ("merge", "merge"),
        ("elog", "elog"),
    ],
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
    assert result["provenance"]["repository_binding"]["complete"] is True
    expected_plan_state = (
        "pretend-failed" if failure == "pretend"
        else "rejected" if failure == "plan-upgrade"
        else "authorized")
    assert result["provenance"]["plan"]["state"] == expected_plan_state
    assert verify_evidence(tmp_path / "evidence")["ok"] is True
    if failure is None:
        emerge_commands = [
            event[1] for event in transport.events
            if event[0] == "run" and event[1][0] == "emerge"]
        assert emerge_commands == [
            merge_argv(ATOM, pretend=True),
            merge_argv(ATOM, pretend=False),
        ]
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
