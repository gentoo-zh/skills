import hashlib
import gzip
import io
import json
import struct
import tarfile
import zipfile

import pytest
from click.testing import CliRunner

import gzh.cli as cli_mod
import gzh.license_inventory as license_mod
from gzh.license_inventory import (
    InventoryLimits,
    LicenseInventoryError,
    inspect_license_archive,
)


def _tar_archive(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))


def _tar_archive_mode(path, members, mode):
    with tarfile.open(path, mode) as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))


def _zip_archive(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members:
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, content)


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_inventory_hashes_archive_and_license_like_members(tmp_path, kind):
    path = tmp_path / f"release.{kind}"
    members = [
        ("project/src/main.c", b"code"),
        ("project/NOTICE.txt", b"notice"),
        ("project/LICENSE-APACHE", b"license"),
        ("project/BCompareEULA.txt", b"eula"),
        ("project/ThirdPartyNotices.md", b"third party"),
    ]
    (_tar_archive if kind == "tar" else _zip_archive)(path, members)

    report = inspect_license_archive(path)

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["archive"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["archive"]["type"] == kind
    assert report["members_scanned"] == 5
    assert report["legal_conclusion"] is None
    assert [item["path"] for item in report["license_like_members"]] == [
        "project/BCompareEULA.txt",
        "project/LICENSE-APACHE",
        "project/NOTICE.txt",
        "project/ThirdPartyNotices.md",
    ]
    for item in report["license_like_members"]:
        expected = dict(members)[item["path"]]
        assert item["size"] == len(expected)
        assert item["sha256"] == hashlib.sha256(expected).hexdigest()


def test_inventory_does_not_extract_members(tmp_path):
    archive = tmp_path / "release.tar"
    _tar_archive(archive, [("nested/LICENSE", b"terms")])

    report = inspect_license_archive(archive)

    assert report["license_like_members"][0]["path"] == "nested/LICENSE"
    assert not (tmp_path / "nested").exists()


@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_inventory_supports_documented_tar_compression_modes(tmp_path, mode):
    archive = tmp_path / "release.tar"
    _tar_archive_mode(archive, [("LICENSE", b"terms")], mode)

    report = inspect_license_archive(archive)

    assert report["archive"]["type"] == "tar"
    assert report["license_like_members"][0]["path"] == "LICENSE"


@pytest.mark.parametrize("mode", ["w", "w:gz"])
def test_inventory_accepts_empty_tar(tmp_path, mode):
    archive = tmp_path / "empty.tar"
    _tar_archive_mode(archive, [], mode)

    report = inspect_license_archive(archive)

    assert report["archive"]["type"] == "tar"
    assert report["members_scanned"] == 0
    assert report["license_like_members"] == []


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_inventory_includes_legal_directories_and_common_exact_names(
        tmp_path, kind):
    path = tmp_path / f"release.{kind}"
    members = [
        ("project/LICENSES/Apache-2.0.txt", b"apache"),
        ("project/LICENSES/licenses.yaml", b"licenses: []"),
        ("project/LICENSE.json", b"{}"),
        ("project/LICENSE.MIT", b"mit"),
        ("project/COPYING.LIB", b"library terms"),
        ("project/THIRD_PARTY_LICENSES.json", b"{}"),
        ("project/third_party_licenses/Apache-2.0.txt", b"apache"),
        ("project/third_party_licenses/helper.py", b"code"),
        ("project/legal/vendor-terms.txt", b"vendor"),
        ("project/legal/logo.png", b"image"),
        ("project/UNLICENSE", b"unlicense"),
        ("project/COPYRIGHTS.md", b"copyrights"),
        ("project/src/license_manager.py", b"code"),
    ]
    (_tar_archive if kind == "tar" else _zip_archive)(path, members)

    report = inspect_license_archive(path)

    assert [item["path"] for item in report["license_like_members"]] == [
        "project/COPYING.LIB",
        "project/COPYRIGHTS.md",
        "project/LICENSE.MIT",
        "project/LICENSE.json",
        "project/LICENSES/Apache-2.0.txt",
        "project/LICENSES/licenses.yaml",
        "project/THIRD_PARTY_LICENSES.json",
        "project/UNLICENSE",
        "project/legal/vendor-terms.txt",
        "project/third_party_licenses/Apache-2.0.txt",
    ]


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_inventory_accepts_explicit_license_directory_entries(tmp_path, kind):
    archive = tmp_path / f"release.{kind}"
    if kind == "tar":
        with tarfile.open(archive, "w") as output:
            directory = tarfile.TarInfo("LICENSES/")
            directory.type = tarfile.DIRTYPE
            output.addfile(directory)
            member = tarfile.TarInfo("LICENSES/Apache-2.0.txt")
            member.size = 5
            output.addfile(member, io.BytesIO(b"terms"))
    else:
        _zip_archive(archive, [
            ("LICENSES/", b""),
            ("LICENSES/Apache-2.0.txt", b"terms"),
        ])

    report = inspect_license_archive(archive)

    assert [item["path"] for item in report["license_like_members"]] == [
        "LICENSES/Apache-2.0.txt",
    ]


@pytest.mark.parametrize("name", ["../LICENSE", "/LICENSE", "C:\\LICENSE"])
def test_inventory_rejects_unsafe_member_paths(tmp_path, name):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [(name, b"terms")])

    with pytest.raises(LicenseInventoryError, match="unsafe archive member path"):
        inspect_license_archive(archive)


def test_inventory_rejects_casefolded_path_collisions(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE", b"one"), ("license", b"two")])

    with pytest.raises(LicenseInventoryError, match="ambiguous archive member paths"):
        inspect_license_archive(archive)


def test_inventory_rejects_non_utf8_member_name():
    with pytest.raises(LicenseInventoryError, match="not valid UTF-8"):
        license_mod._safe_member_path("\udcff/LICENSE")


@pytest.mark.parametrize("control", ["\u0085", "\u2028", "\u2029", "\u202e"])
def test_inventory_rejects_unicode_controls_without_rendering_them(
        tmp_path, control):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [(f"legal/NOTICE{control}.txt", b"terms")])

    result = CliRunner().invoke(cli_mod.cli, ["license", str(archive)])

    assert result.exit_code == 1
    assert "unsafe archive member path" in result.output
    assert control not in result.output


def test_inventory_rejects_license_like_symlink(tmp_path):
    archive = tmp_path / "release.tar"
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("LICENSE")
        info.type = tarfile.SYMTYPE
        info.linkname = "COPYING"
        output.addfile(info)

    with pytest.raises(LicenseInventoryError, match="not a regular file"):
        inspect_license_archive(archive)


def test_inventory_rejects_member_count_limit(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("one", b"1"), ("two", b"2")])
    limits = InventoryLimits(max_members=1)

    with pytest.raises(LicenseInventoryError, match="member count"):
        inspect_license_archive(archive, limits=limits)


def test_zip_limits_are_checked_before_zipfile_materializes_entries(
        tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("one", b"1"), ("two", b"2")])

    def unexpected_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed")

    monkeypatch.setattr(license_mod.zipfile, "ZipFile", unexpected_zipfile)

    with pytest.raises(LicenseInventoryError, match="member count"):
        inspect_license_archive(
            archive, limits=InventoryLimits(max_members=1))


def test_inventory_rejects_zip_central_directory_metadata_limit(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE", b"terms")])

    with pytest.raises(LicenseInventoryError, match="central-directory metadata"):
        inspect_license_archive(
            archive, limits=InventoryLimits(max_metadata_bytes=16))


def test_inventory_rejects_zip64_metadata_before_zipfile(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [])
    content = bytearray(archive.read_bytes())
    eocd = content.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", content, eocd + 8, 0xFFFF, 0xFFFF)
    archive.write_bytes(content)

    with pytest.raises(LicenseInventoryError, match="ZIP64 metadata"):
        inspect_license_archive(archive)


def test_inventory_rejects_zip64_local_header_metadata(tmp_path):
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w", allowZip64=True) as output:
        with output.open("LICENSE", "w", force_zip64=True) as member:
            member.write(b"terms")

    with pytest.raises(LicenseInventoryError, match="ZIP64 member metadata"):
        inspect_license_archive(archive)


def test_inventory_rejects_raw_nul_in_zip_member_name(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE-evil", b"terms")])
    content = bytearray(archive.read_bytes())
    for signature, name_offset in ((b"PK\x03\x04", 30),
                                   (b"PK\x01\x02", 46)):
        header = content.find(signature)
        content[header + name_offset + len("LICENSE")] = 0
    archive.write_bytes(content)

    with pytest.raises(LicenseInventoryError, match="NUL byte"):
        inspect_license_archive(archive)


def test_inventory_normalizes_ambiguous_zip_comment_failure(tmp_path):
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.comment = b"comment PK\x05\x06 suffix"
        output.writestr("LICENSE", b"terms")

    with pytest.raises(LicenseInventoryError, match="cannot inspect archive"):
        inspect_license_archive(archive)


@pytest.mark.parametrize("archive_format", [tarfile.PAX_FORMAT,
                                             tarfile.GNU_FORMAT])
def test_tar_extended_metadata_is_bounded_before_tarfile_iteration(
        tmp_path, monkeypatch, archive_format):
    archive = tmp_path / "release.tar"
    with tarfile.open(archive, "w", format=archive_format) as output:
        info = tarfile.TarInfo(f"{'a' * 180}/LICENSE")
        info.size = 5
        output.addfile(info, io.BytesIO(b"terms"))

    def unexpected_tarfile(*args, **kwargs):
        raise AssertionError("tarfile.open must not be called")

    monkeypatch.setattr(license_mod.tarfile, "open", unexpected_tarfile)

    with pytest.raises(LicenseInventoryError, match="extended-header metadata"):
        inspect_license_archive(
            archive, limits=InventoryLimits(max_metadata_bytes=32))


@pytest.mark.parametrize("mode", ["w:gz", "w:bz2", "w:xz"])
def test_compressed_tar_is_parsed_from_the_preflight_spool(
        tmp_path, monkeypatch, mode):
    archive = tmp_path / "release.tar"
    _tar_archive_mode(archive, [("LICENSE", b"terms")], mode)
    original_open = license_mod.tarfile.open
    calls = []

    def tracked_open(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(license_mod.tarfile, "open", tracked_open)

    report = inspect_license_archive(archive)

    assert report["license_like_members"][0]["path"] == "LICENSE"
    assert len(calls) == 1
    assert calls[0][0] == ()
    assert calls[0][1]["mode"] == "r|"
    assert calls[0][1]["fileobj"] is not None


@pytest.mark.parametrize("compressed", [False, True])
def test_inventory_rejects_concatenated_tar_after_end_marker(
        tmp_path, compressed):
    streams = []
    for name in ("README", "LICENSE"):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as output:
            member = tarfile.TarInfo(name)
            member.size = 5
            output.addfile(member, io.BytesIO(b"terms"))
        streams.append(stream.getvalue())
    payload = b"".join(streams)
    archive = tmp_path / "release.tar"
    archive.write_bytes(gzip.compress(payload) if compressed else payload)

    with pytest.raises(
            LicenseInventoryError, match="non-zero data follows the tar end marker"):
        inspect_license_archive(archive)


def test_inventory_rejects_declared_member_size_limit(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("payload", b"1234")])
    limits = InventoryLimits(max_total_member_bytes=3)

    with pytest.raises(LicenseInventoryError, match="total declared member size"):
        inspect_license_archive(archive, limits=limits)


def test_inventory_rejects_candidate_size_and_count_limits(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE", b"1234"), ("NOTICE", b"x")])

    with pytest.raises(LicenseInventoryError, match="member exceeds the size limit"):
        inspect_license_archive(
            archive, limits=InventoryLimits(max_candidate_bytes=3))
    with pytest.raises(LicenseInventoryError, match="member count"):
        inspect_license_archive(
            archive, limits=InventoryLimits(max_candidates=1))


def test_inventory_rejects_unsupported_file_and_symlink_path(tmp_path):
    plain = tmp_path / "plain.txt"
    plain.write_text("not an archive", encoding="utf-8")
    link = tmp_path / "release.zip"
    link.symlink_to(plain)

    with pytest.raises(LicenseInventoryError, match="neither a supported"):
        inspect_license_archive(plain)
    with pytest.raises(LicenseInventoryError, match="symbolic link"):
        inspect_license_archive(link)


def test_inventory_rejects_zstandard_tar_without_bounded_preflight(tmp_path):
    archive = tmp_path / "release.tar.zst"
    archive.write_bytes(b"\x28\xb5\x2f\xfdnot-a-bounded-stream")

    with pytest.raises(LicenseInventoryError, match="Zstandard tar"):
        inspect_license_archive(archive)


def test_inventory_normalizes_temporary_workspace_failure(tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE", b"terms")])

    def fail_workspace(*args, **kwargs):
        raise OSError("workspace unavailable")

    monkeypatch.setattr(license_mod.tempfile, "TemporaryFile", fail_workspace)

    with pytest.raises(LicenseInventoryError, match="bounded archive workspace"):
        inspect_license_archive(archive)


def test_inventory_rejects_archive_changed_during_inspection(
        tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("LICENSE", b"terms")])
    original = license_mod._sha256_path
    calls = 0

    def replace_after_first_digest(path, *, maximum_size):
        nonlocal calls
        digest = original(path, maximum_size=maximum_size)
        calls += 1
        if calls == 1:
            _zip_archive(path, [("LICENSE", b"changed terms")])
        return digest

    monkeypatch.setattr(license_mod, "_sha256_path", replace_after_first_digest)

    with pytest.raises(LicenseInventoryError, match="changed during inspection"):
        inspect_license_archive(archive)


def test_license_cli_prints_json_and_has_concise_help(tmp_path):
    archive = tmp_path / "release.zip"
    _zip_archive(archive, [("EULA.txt", b"terms")])
    runner = CliRunner()

    help_result = runner.invoke(cli_mod.cli, ["license", "--help"])
    result = runner.invoke(cli_mod.cli, ["license", str(archive)])

    assert help_result.exit_code == 0
    assert "local tar or ZIP archive" in help_result.output
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["license_like_members"][0]["path"] == "EULA.txt"


def test_license_cli_normalizes_corrupt_deflate_errors(tmp_path):
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("LICENSE", b"terms " * 1024)
    with zipfile.ZipFile(archive, "r") as source:
        info = source.getinfo("LICENSE")
        with archive.open("r+b") as stream:
            stream.seek(info.header_offset + 26)
            name_size, extra_size = struct.unpack("<HH", stream.read(4))
            data_offset = info.header_offset + 30 + name_size + extra_size
            stream.seek(data_offset + max(0, info.compress_size // 2))
            byte = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([byte[0] ^ 0xFF]))
    runner = CliRunner()

    result = runner.invoke(cli_mod.cli, ["license", str(archive)])

    assert result.exit_code == 1
    assert result.output.startswith("Error: cannot inspect archive:")
    assert "Traceback" not in result.output
