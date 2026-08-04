from pathlib import Path
from types import SimpleNamespace

from gzh.binary_qa import inspect_binaries, inspect_elf


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


def _elf(path: Path) -> Path:
    path.write_bytes(b"\x7fELFpayload")
    return path


def test_inspect_elf_extracts_readelf_evidence(tmp_path):
    target = _elf(tmp_path / "demo")

    def runner(args, **kwargs):
        if args[0] == "file":
            return SimpleNamespace(returncode=0, stdout="ELF 64-bit, not stripped\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=READELF_OK, stderr="")

    report = inspect_elf(
        target,
        expected_machine="Advanced Micro Devices X86-64",
        runner=runner,
    )

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["elf"]["needed"] == ["libc.so.6"]
    assert report["elf"]["interpreter"] == "/lib64/ld-linux-x86-64.so.2"
    assert report["elf"]["runpath"] == ["$ORIGIN"]


def test_inspect_elf_flags_unsafe_headers_and_machine(tmp_path):
    target = _elf(tmp_path / "bad")
    unsafe = READELF_OK.replace("GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW ",
                                "GNU_STACK 0x0 0x0 0x0 0x0 0x0 RWE ")
    unsafe += " 0x0000000000000016 (TEXTREL)            0x0\n"

    def runner(args, **kwargs):
        stdout = "ELF\n" if args[0] == "file" else unsafe
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    report = inspect_elf(target, expected_machine="AArch64", runner=runner)
    codes = {finding["code"] for finding in report["findings"]}
    assert report["ok"] is False
    assert {"executable-stack", "text-relocations", "unexpected-machine"} <= codes


def test_inspect_binaries_does_not_follow_symlinks_and_reports_limit(tmp_path):
    _elf(tmp_path / "a")
    _elf(tmp_path / "b")
    (tmp_path / "link").symlink_to(tmp_path / "a")

    def runner(args, **kwargs):
        stdout = "ELF\n" if args[0] == "file" else READELF_OK
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    report = inspect_binaries(tmp_path, max_files=1, runner=runner)
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
