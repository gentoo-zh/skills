import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import gzh.binary_qa as binary_mod
from gzh.binary_qa import ToolBudget, _run, inspect_binaries, inspect_elf


READELF_OK = """\
ELF Header:
  Class:                             ELF64
  Data:                              2's complement, little endian
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD 0x0 0x0 0x0 0x100 0x100 R E 0x1000
  GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW 0x10
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000000e (SONAME)             Library soname: [demo.so]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
"""


def _recorded_child_pid(pid_path) -> int | None:
    """Return the child PID, or None when the group died before recording it.

    The fixture shell echoes the PID right after spawning, so a bounded stop can
    kill the group before that write lands. An absent PID therefore means the
    child never outlived the runner, which is what the caller asserts.
    """
    for _attempt in range(100):
        try:
            raw = pid_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raw = ""
        if raw:
            return int(raw)
        time.sleep(0.01)
    return None


def _elf(path: Path) -> Path:
    path.write_bytes(b"\x7fELFpayload")
    return path


def _runner(
    stdout: str = READELF_OK,
    *,
    lddtree: str = "demo => /demo\n  libc.so.6 => /usr/lib64/libc.so.6\n",
):
    def run(args, **kwargs):
        if args[0] == "file":
            return SimpleNamespace(returncode=0, stdout="ELF 64-bit\n", stderr="")
        if args[0] == "lddtree":
            return SimpleNamespace(returncode=0, stdout=lddtree, stderr="")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return run


def test_inspect_elf_extracts_readelf_evidence(tmp_path):
    target = _elf(tmp_path / "demo")

    report = inspect_elf(
        target,
        expected_machine="Advanced Micro Devices X86-64",
        runner=_runner(),
    )

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["elf"]["needed"] == ["libc.so.6"]
    assert report["elf"]["interpreter"] == "/lib64/ld-linux-x86-64.so.2"
    assert report["elf"]["runpath"] == ["$ORIGIN"]
    assert report["runtime_dependency_resolution"]["complete"] is True


def test_inspect_elf_flags_unsafe_headers_and_machine(tmp_path):
    target = _elf(tmp_path / "bad")
    unsafe = READELF_OK.replace("GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW ",
                                "GNU_STACK 0x0 0x0 0x0 0x0 0x0 RWE ")
    unsafe += " 0x0000000000000016 (TEXTREL)            0x0\n"

    report = inspect_elf(
        target, expected_machine="AArch64", runner=_runner(unsafe))
    codes = {finding["code"] for finding in report["findings"]}
    assert report["ok"] is False
    assert {"executable-stack", "text-relocations", "unexpected-machine"} <= codes


def test_inspect_binaries_does_not_follow_symlinks_and_reports_limit(tmp_path):
    _elf(tmp_path / "a")
    _elf(tmp_path / "b")
    (tmp_path / "link").symlink_to(tmp_path / "a")

    report = inspect_binaries(tmp_path, max_files=1, runner=_runner())
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["scanned"] == 1


def test_missing_readelf_is_incomplete(tmp_path):
    target = _elf(tmp_path / "demo")

    def runner(args, **kwargs):
        if args[0] == "readelf":
            raise FileNotFoundError("readelf")
        return SimpleNamespace(returncode=0, stdout="ELF\n", stderr="")

    report = inspect_elf(target, runner=runner)
    assert report["ok"] is False
    assert report["complete"] is False
    assert "incomplete-tool-evidence" in {
        finding["code"] for finding in report["findings"]
    }


def test_unresolved_runtime_dependency_is_an_error(tmp_path):
    target = _elf(tmp_path / "demo")
    report = inspect_elf(
        target, runner=_runner(lddtree="demo => /demo\n  libmissing.so => None\n"))

    assert report["ok"] is False
    assert report["runtime_dependency_resolution"]["unresolved"] == [
        "libmissing.so"]
    assert "unresolved-needed" in {
        finding["code"] for finding in report["findings"]}


def test_missing_interpreter_is_an_error(tmp_path):
    target = _elf(tmp_path / "demo")
    missing = READELF_OK.replace(
        "/lib64/ld-linux-x86-64.so.2", "/gzh-missing/ld-linux.so")

    report = inspect_elf(target, runner=_runner(missing))

    assert report["ok"] is False
    assert "missing-interpreter" in {
        finding["code"] for finding in report["findings"]}


def test_appimage_reports_unreviewed_nested_scope(tmp_path):
    target = tmp_path / "demo.AppImage"
    target.write_bytes(b"\x7fELF" + b"\0" * 4 + b"AI\x02" + b"payload")

    report = inspect_binaries(target, runner=_runner())

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["nested_containers"][0]["format"] == "appimage"
    assert "nested-scope-unreviewed" in {
        finding["code"] for finding in report["findings"]}


def test_non_elf_file_cannot_pass_as_an_empty_scan(tmp_path):
    target = tmp_path / "plain"
    target.write_text("data\n", encoding="utf-8")

    report = inspect_binaries(target, runner=_runner())

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["scanned"] == 0
    assert report["findings"][0]["code"] == "unsupported-binary-target"


@pytest.mark.parametrize("tool", ["file", "readelf", "lddtree"])
def test_successful_empty_tool_output_is_incomplete(tmp_path, tool):
    target = _elf(tmp_path / "demo")

    def runner(args, **kwargs):
        if args[0] == tool:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _runner()(args, **kwargs)

    report = inspect_elf(target, runner=runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert any(
        finding["code"].startswith("malformed-")
        for finding in report["findings"])


def test_readelf_requires_core_header_fields(tmp_path):
    target = _elf(tmp_path / "demo")
    malformed = "ELF Header:\n  Class: ELF64\n"

    report = inspect_elf(target, runner=_runner(malformed))

    finding = next(
        item for item in report["findings"]
        if item["code"] == "malformed-readelf-output")
    assert report["complete"] is False
    assert finding["missing_fields"] == ["data", "machine", "type"]


def test_every_needed_entry_requires_lddtree_resolution(tmp_path):
    target = _elf(tmp_path / "demo")

    report = inspect_elf(
        target, runner=_runner(lddtree="demo => /demo\n"))

    assert report["runtime_dependency_resolution"]["missing_needed"] == [
        "libc.so.6"]
    assert "missing-runtime-resolution" in {
        finding["code"] for finding in report["findings"]}
    assert report["complete"] is False


def test_aggregate_tool_budget_fails_closed(tmp_path):
    target = _elf(tmp_path / "demo")
    budget = ToolBudget(command_limit=2)

    report = inspect_elf(target, runner=_runner(), tool_budget=budget)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["tool_budget"]["exhausted"] is True
    assert report["tool_budget"]["reason"] == "command-limit"


def test_unreadable_regular_file_is_incomplete(tmp_path, monkeypatch):
    target = _elf(tmp_path / "demo")
    original_open = binary_mod.os.open

    def denied(path, flags, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError("inspection denied")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(binary_mod.os, "open", denied)

    report = inspect_binaries(target, runner=_runner())

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["findings"][0]["code"] == "unreadable-file"


def test_bounded_runner_stops_output_flood_process_group(tmp_path):
    child_pid_path = tmp_path / "child.pid"
    command = tmp_path / "flood"
    command.write_text("""\
#!/bin/sh
(
    trap '' TERM
    while :; do
        printf '%8192s' x
    done
) &
echo "$!" > "$1"
wait
""", encoding="utf-8")
    command.chmod(0o755)
    budget = ToolBudget(
        command_limit=1,
        output_limit_bytes=32 * 1024,
        duration_limit_seconds=10,
    )

    started = time.monotonic()
    report = _run(
        [str(command), str(child_pid_path)],
        output_limit=32 * 1024,
        budget=budget,
    )
    elapsed = time.monotonic() - started

    # The behavioral assertions below carry the guarantee; this bound only
    # catches a hang, so it must not measure a loaded machine's speed.
    assert elapsed < 20
    assert report["complete"] is False
    assert report["truncated"] is True
    child_pid = _recorded_child_pid(child_pid_path)
    if child_pid is None:
        return
    # Reaping a killed process group is scheduler-bound, so poll for a
    # bound that proves the child does not survive rather than one that
    # measures how fast a loaded machine reaps it.
    for _attempt in range(500):
        try:
            state = (Path("/proc") / str(child_pid) / "stat").read_text(
                encoding="utf-8").split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"flood child {child_pid} survived process-group cleanup")
