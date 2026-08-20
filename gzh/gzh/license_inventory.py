from __future__ import annotations

import bz2
import errno
import gzip
import hashlib
import lzma
import os
import re
import stat
import struct
import tarfile
import tempfile
import unicodedata
import zipfile
import zlib
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


DEFAULT_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_MEMBERS = 100_000
DEFAULT_MAX_TOTAL_MEMBER_BYTES = 64 * 1024 * 1024 * 1024
DEFAULT_MAX_CANDIDATES = 512
DEFAULT_MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_CANDIDATE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_METADATA_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_NESTED_PROBE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_PATH_BYTES = 4096
MAX_MEMBER_COMPONENT_BYTES = 255
MAX_CONSECUTIVE_TAR_EXTENSIONS = 16
NESTED_HEADER_BYTES = 512
READ_CHUNK_BYTES = 1024 * 1024

_ZIP_EOCD = struct.Struct("<4s4H2LH")
_ZIP_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_ZIP_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_ZIP_LOCAL_SIGNATURE = b"PK\x03\x04"
_ZIP64_EXTRA_ID = 0x0001
_ZIP64_SENTINEL_16 = 0xFFFF
_ZIP64_SENTINEL_32 = 0xFFFFFFFF
_TAR_EXTENSION_TYPES = frozenset({
    tarfile.XHDTYPE,
    tarfile.XGLTYPE,
    tarfile.SOLARIS_XHDTYPE,
    tarfile.GNUTYPE_LONGNAME,
    tarfile.GNUTYPE_LONGLINK,
})
_DOCUMENT_EXTENSIONS = frozenset({
    "adoc", "asc", "html", "htm", "json", "markdown", "md", "pdf",
    "rst", "rtf", "spdx", "text", "txt", "yaml", "yml",
})
_CODE_EXTENSIONS = frozenset({
    "c", "cc", "cpp", "css", "go", "h", "hpp", "java", "js", "lua",
    "php", "pl", "py", "rb", "rs", "sh", "ts",
})
_LEGAL_DIRECTORY_NAMES = frozenset({
    "legal", "licence", "licences", "license", "licenses", "notices",
    "thirdpartylicences", "thirdpartylicenses", "thirdpartynotices",
})
_LEGAL_EXACT_STEMS = frozenset({
    "authors", "copyright", "copyrights", "patents", "trademarks", "unlicense",
})

_LICENSE_NAME_RE = re.compile(
    r"^(?:"
    r"licen[cs]e(?:s)?|copying|copyright|notice(?:s)?|eula|"
    r"agreement(?:s)?|license[-_. ]?agreement|user[-_. ]?agreement|"
    r"end[-_. ]?user[-_. ]?license[-_. ]?agreement|"
    r"third[-_. ]?party(?:[-_. ]?(?:licen[cs]e(?:s)?|notice(?:s)?|"
    r"agreement(?:s)?|terms))?|thirdparty(?:licen[cs]e(?:s)?|notice(?:s)?|"
    r"agreement(?:s)?|terms)"
    r")(?:$|[-_. ])",
    re.IGNORECASE,
)


class LicenseInventoryError(ValueError):
    pass


@dataclass(frozen=True)
class InventoryLimits:
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    max_members: int = DEFAULT_MAX_MEMBERS
    max_total_member_bytes: int = DEFAULT_MAX_TOTAL_MEMBER_BYTES
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_candidate_bytes: int = DEFAULT_MAX_CANDIDATE_BYTES
    max_total_candidate_bytes: int = DEFAULT_MAX_TOTAL_CANDIDATE_BYTES
    max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES
    max_nested_probe_bytes: int = DEFAULT_MAX_NESTED_PROBE_BYTES

    def as_dict(self) -> dict[str, int]:
        return {
            "max_archive_bytes": self.max_archive_bytes,
            "max_candidate_bytes": self.max_candidate_bytes,
            "max_candidates": self.max_candidates,
            "max_members": self.max_members,
            "max_metadata_bytes": self.max_metadata_bytes,
            "max_nested_probe_bytes": self.max_nested_probe_bytes,
            "max_total_candidate_bytes": self.max_total_candidate_bytes,
            "max_total_member_bytes": self.max_total_member_bytes,
        }

    def validate(self) -> None:
        for name, value in self.as_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise LicenseInventoryError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class _Member:
    path: str
    size: int
    kind: str
    source: object


def _snapshot_archive(
    source: BinaryIO,
    snapshot: BinaryIO,
    *,
    maximum_size: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    observed = 0
    while chunk := source.read(
            min(READ_CHUNK_BYTES, maximum_size + 1 - observed)):
        observed += len(chunk)
        if observed > maximum_size:
            raise LicenseInventoryError("archive size exceeds the limit")
        snapshot.write(chunk)
        digest.update(chunk)
    snapshot.flush()
    snapshot.seek(0)
    return digest.hexdigest(), observed


def _file_identity(metadata) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_member_path(raw: str) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw:
        raise LicenseInventoryError("archive contains an empty member path")
    if "\x00" in raw or "\\" in raw:
        raise LicenseInventoryError(f"unsafe archive member path: {raw!r}")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise LicenseInventoryError(f"unsafe archive member path: {raw!r}")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
           for character in raw):
        raise LicenseInventoryError(f"unsafe archive member path: {raw!r}")
    try:
        encoded_path = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LicenseInventoryError(
            f"archive member path is not valid UTF-8 text: {raw!r}") from exc
    if len(encoded_path) > MAX_MEMBER_PATH_BYTES:
        raise LicenseInventoryError("archive member path exceeds the size limit")

    parts = []
    for component in raw.rstrip("/").split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            raise LicenseInventoryError(f"unsafe archive member path: {raw!r}")
        if len(component.encode("utf-8")) > MAX_MEMBER_COMPONENT_BYTES:
            raise LicenseInventoryError(
                f"archive member path component exceeds the size limit: {raw!r}")
        parts.append(component)
    if not parts:
        raise LicenseInventoryError(f"ambiguous archive member path: {raw!r}")
    normalized = "/".join(parts)
    collision_key = unicodedata.normalize("NFC", normalized).casefold()
    return normalized, collision_key


def _is_license_candidate(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    suffix = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    if suffix in _CODE_EXTENSIONS:
        return False
    stem = name.rsplit(".", 1)[0] if suffix in _DOCUMENT_EXTENSIONS else name
    compact_stem = re.sub(r"[^a-z0-9]+", "", stem.casefold())
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    directory_parts = {
        re.sub(r"[^a-z0-9]+", "", part.casefold())
        for part in path.split("/")[:-1]
    }
    strong_markers = (
        "eula",
        "licenseagreement",
        "licenceagreement",
        "thirdpartylicense",
        "thirdpartylicence",
        "thirdpartynotice",
    )
    if (compact_stem in _LEGAL_EXACT_STEMS
            or _LICENSE_NAME_RE.match(name) is not None):
        return True
    document_name = not suffix or suffix in _DOCUMENT_EXTENSIONS
    if not document_name:
        return False
    return (bool(directory_parts & _LEGAL_DIRECTORY_NAMES)
            or any(marker in compact for marker in strong_markers))


def _read_exact(stream: BinaryIO, size: int, *, label: str) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise LicenseInventoryError(f"truncated {label}")
        chunks.extend(chunk)
    return bytes(chunks)


def _zip_extra_contains_zip64(extra: bytes) -> bool:
    position = 0
    while position < len(extra):
        if position + 4 > len(extra):
            raise LicenseInventoryError("truncated ZIP extra field")
        field_id, field_size = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + field_size > len(extra):
            raise LicenseInventoryError("truncated ZIP extra field")
        if field_id == _ZIP64_EXTRA_ID:
            return True
        position += field_size
    return False


def _discard_exact(stream: BinaryIO, size: int, *, label: str,
                   copy_to: BinaryIO | None = None) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(READ_CHUNK_BYTES, remaining))
        if not chunk:
            raise LicenseInventoryError(f"truncated {label}")
        if copy_to is not None:
            copy_to.write(chunk)
        remaining -= len(chunk)


def _zip_preflight(stream: BinaryIO, limits: InventoryLimits) -> bool:
    stream.seek(0, 2)
    archive_size = stream.tell()
    tail_size = min(archive_size, 65_557)
    stream.seek(archive_size - tail_size)
    tail = stream.read(tail_size)
    search_end = len(tail)
    index = -1
    fields = None
    while search_end:
        candidate = tail.rfind(_ZIP_EOCD_SIGNATURE, 0, search_end)
        if candidate < 0:
            break
        if candidate + _ZIP_EOCD.size <= len(tail):
            candidate_fields = _ZIP_EOCD.unpack_from(tail, candidate)
            if candidate + _ZIP_EOCD.size + candidate_fields[-1] == len(tail):
                index = candidate
                fields = candidate_fields
                break
        search_end = candidate
    if fields is None:
        return False
    (_, disk_number, directory_disk, disk_entries, total_entries,
     directory_size, directory_offset, comment_size) = fields
    if disk_number != 0 or directory_disk != 0 or disk_entries != total_entries:
        raise LicenseInventoryError("multi-disk ZIP archives are not supported")
    if (total_entries == _ZIP64_SENTINEL_16
            or directory_size == _ZIP64_SENTINEL_32
            or directory_offset == _ZIP64_SENTINEL_32):
        raise LicenseInventoryError(
            "ZIP64 metadata is not supported by the bounded inventory")
    if total_entries > limits.max_members:
        raise LicenseInventoryError("archive member count exceeds the limit")
    if directory_size > limits.max_metadata_bytes:
        raise LicenseInventoryError(
            "ZIP central-directory metadata exceeds the limit")

    eocd_offset = archive_size - tail_size + index
    prefix_size = eocd_offset - directory_size - directory_offset
    if prefix_size < 0:
        raise LicenseInventoryError("invalid ZIP central-directory bounds")
    central_offset = prefix_size + directory_offset
    if central_offset + directory_size != eocd_offset:
        raise LicenseInventoryError("invalid ZIP central-directory bounds")
    stream.seek(central_offset)
    directory = _read_exact(
        stream, directory_size, label="ZIP central directory")

    position = 0
    observed_entries = 0
    local_entries = []
    while position < len(directory):
        if position + _ZIP_CENTRAL_HEADER.size > len(directory):
            raise LicenseInventoryError("truncated ZIP central-directory entry")
        fields = _ZIP_CENTRAL_HEADER.unpack_from(directory, position)
        if fields[0] != _ZIP_CENTRAL_SIGNATURE:
            raise LicenseInventoryError("invalid ZIP central-directory entry")
        compressed_size = fields[8]
        member_size = fields[9]
        name_size, extra_size, member_comment_size = fields[10:13]
        local_header_offset = fields[-1]
        if (compressed_size == _ZIP64_SENTINEL_32
                or member_size == _ZIP64_SENTINEL_32
                or local_header_offset == _ZIP64_SENTINEL_32):
            raise LicenseInventoryError(
                "ZIP64 member metadata is not supported by the bounded inventory")
        content_start = position + _ZIP_CENTRAL_HEADER.size
        name_end = content_start + name_size
        extra_end = name_end + extra_size
        record_end = extra_end + member_comment_size
        if record_end > len(directory):
            raise LicenseInventoryError("truncated ZIP central-directory entry")
        raw_name = directory[content_start:name_end]
        extra = directory[name_end:extra_end]
        if b"\x00" in raw_name:
            raise LicenseInventoryError("ZIP member name contains a NUL byte")
        if _zip_extra_contains_zip64(extra):
            raise LicenseInventoryError(
                "ZIP64 member metadata is not supported by the bounded inventory")
        local_entries.append((local_header_offset, raw_name))
        position = record_end
        observed_entries += 1
        if observed_entries > limits.max_members:
            raise LicenseInventoryError("archive member count exceeds the limit")
    if observed_entries != total_entries:
        raise LicenseInventoryError("ZIP member count differs from its directory record")

    metadata_bytes = directory_size
    seen_offsets = set()
    for local_header_offset, central_name in local_entries:
        if local_header_offset in seen_offsets:
            raise LicenseInventoryError("duplicate ZIP local-header offset")
        seen_offsets.add(local_header_offset)
        absolute_offset = prefix_size + local_header_offset
        if (absolute_offset < prefix_size
                or absolute_offset + _ZIP_LOCAL_HEADER.size > central_offset):
            raise LicenseInventoryError("invalid ZIP local-header bounds")
        stream.seek(absolute_offset)
        local_header = _read_exact(
            stream, _ZIP_LOCAL_HEADER.size, label="ZIP local header")
        local_fields = _ZIP_LOCAL_HEADER.unpack(local_header)
        if local_fields[0] != _ZIP_LOCAL_SIGNATURE:
            raise LicenseInventoryError("invalid ZIP local-header entry")
        compressed_size = local_fields[7]
        member_size = local_fields[8]
        name_size, extra_size = local_fields[9:11]
        metadata_bytes += _ZIP_LOCAL_HEADER.size + name_size + extra_size
        if metadata_bytes > limits.max_metadata_bytes:
            raise LicenseInventoryError("ZIP parser metadata exceeds the limit")
        raw_name = _read_exact(stream, name_size, label="ZIP local member name")
        extra = _read_exact(stream, extra_size, label="ZIP local extra field")
        if b"\x00" in raw_name:
            raise LicenseInventoryError("ZIP member name contains a NUL byte")
        if raw_name != central_name:
            raise LicenseInventoryError(
                "ZIP local and central member names differ")
        if (compressed_size == _ZIP64_SENTINEL_32
                or member_size == _ZIP64_SENTINEL_32
                or _zip_extra_contains_zip64(extra)):
            raise LicenseInventoryError(
                "ZIP64 member metadata is not supported by the bounded inventory")
    return True


@contextmanager
def _open_tar_stream(raw: BinaryIO) -> Iterator[tuple[BinaryIO, bool]]:
    raw.seek(0)
    magic = raw.read(6)
    raw.seek(0)
    if magic.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
            yield stream, True
    elif magic.startswith(b"BZh"):
        with bz2.BZ2File(raw, mode="rb") as stream:
            yield stream, True
    elif magic.startswith(b"\xfd7zXZ\x00"):
        with lzma.LZMAFile(raw, mode="rb") as stream:
            yield stream, True
    elif magic.startswith(b"\x28\xb5\x2f\xfd"):
        raise LicenseInventoryError(
            "Zstandard tar archives are not supported by the bounded inventory")
    else:
        yield raw, False


def _tar_preflight(stream: BinaryIO, limits: InventoryLimits, *,
                   compressed_spool: BinaryIO) -> tuple[bool, bool]:
    member_count = 0
    metadata_bytes = 0
    total_member_bytes = 0
    consecutive_extensions = 0
    saw_header = False
    saw_end_marker = False
    with _open_tar_stream(stream) as (stream, compressed):
        while True:
            block = stream.read(tarfile.BLOCKSIZE)
            if not block:
                return saw_header or saw_end_marker, compressed
            if compressed:
                compressed_spool.write(block)
            if len(block) != tarfile.BLOCKSIZE:
                if saw_header:
                    raise LicenseInventoryError("truncated tar header")
                return False, compressed
            if block == tarfile.NUL * tarfile.BLOCKSIZE:
                saw_end_marker = True
                continue
            if saw_end_marker:
                raise LicenseInventoryError(
                    "non-zero data follows the tar end marker")
            try:
                member = tarfile.TarInfo.frombuf(
                    block, encoding="utf-8", errors="surrogateescape")
            except tarfile.HeaderError:
                if saw_header:
                    raise LicenseInventoryError("invalid tar header")
                return False, compressed
            saw_header = True
            if member.size < 0:
                raise LicenseInventoryError("tar member has a negative size")
            padded_size = (member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
            padded_size *= tarfile.BLOCKSIZE
            if member.type in _TAR_EXTENSION_TYPES:
                consecutive_extensions += 1
                if consecutive_extensions > MAX_CONSECUTIVE_TAR_EXTENSIONS:
                    raise LicenseInventoryError(
                        "too many consecutive tar extended headers")
                metadata_bytes += member.size
                if metadata_bytes > limits.max_metadata_bytes:
                    raise LicenseInventoryError(
                        "tar extended-header metadata exceeds the limit")
                payload = _read_exact(
                    stream, padded_size, label="tar extended-header payload")
                if compressed:
                    compressed_spool.write(payload)
                if (member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE,
                                    tarfile.SOLARIS_XHDTYPE}
                        and b"GNU.sparse" in payload[:member.size]):
                    raise LicenseInventoryError(
                        "sparse tar metadata is not supported")
                continue
            consecutive_extensions = 0
            if member.type == tarfile.GNUTYPE_SPARSE:
                raise LicenseInventoryError("sparse tar members are not supported")
            member_count += 1
            if member_count > limits.max_members:
                raise LicenseInventoryError("archive member count exceeds the limit")
            total_member_bytes += member.size
            if total_member_bytes > limits.max_total_member_bytes:
                raise LicenseInventoryError(
                    "archive total declared member size exceeds the limit")
            _discard_exact(
                stream, padded_size, label="tar member payload",
                copy_to=compressed_spool if compressed else None)


def _stream_sha256(
    stream: BinaryIO,
    *,
    expected_size: int,
    maximum_size: int,
    prefix: bytes = b"",
) -> str:
    digest = hashlib.sha256()
    digest.update(prefix)
    observed = len(prefix)
    if observed > expected_size:
        raise LicenseInventoryError(
            "archive member prefix exceeds its declared size")
    while chunk := stream.read(min(READ_CHUNK_BYTES, maximum_size + 1 - observed)):
        observed += len(chunk)
        if observed > maximum_size:
            raise LicenseInventoryError(
                "license-like archive member exceeds the streamed size limit")
        digest.update(chunk)
    if observed != expected_size:
        raise LicenseInventoryError(
            "archive member size differs from the bytes returned by the archive")
    return digest.hexdigest()


def _nested_container_format(header: bytes) -> str | None:
    if header.startswith(b"\x7fELF") and header[8:11] in {b"AI\x01", b"AI\x02"}:
        return "appimage"
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "zip"
    if header.startswith((b"hsqs", b"sqsh")):
        return "squashfs"
    if header.startswith(b"!<arch>\n"):
        return "ar"
    if len(header) >= 262 and header[257:262] == b"ustar":
        return "tar"
    if header.startswith(b"\x1f\x8b"):
        return "gzip-stream"
    if header.startswith(b"BZh"):
        return "bzip2-stream"
    if header.startswith(b"\xfd7zXZ\x00"):
        return "xz-stream"
    if header.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd-stream"
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7zip"
    if header.startswith((b"070701", b"070702", b"070707")):
        return "cpio"
    if header.startswith(b"\xed\xab\xee\xdb"):
        return "rpm"
    return None


def _tar_members(archive: tarfile.TarFile) -> Iterator[_Member]:
    for member in archive:
        path, _ = _safe_member_path(member.name)
        if member.isdir():
            yield _Member(path, 0, "directory", member)
        elif member.isfile():
            if member.size < 0:
                raise LicenseInventoryError(
                    f"archive member has a negative size: {member.name!r}")
            yield _Member(path, member.size, "file", member)
        elif member.issym() or member.islnk():
            target = member.linkname
            if target:
                _safe_member_path(target)
            yield _Member(path, 0, "link", member)
        else:
            raise LicenseInventoryError(
                f"unsupported special archive member: {member.name!r}")


def _zip_kind(info: zipfile.ZipInfo) -> str:
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if info.is_dir():
        return "directory"
    if file_type == stat.S_IFLNK:
        return "link"
    if file_type not in {0, stat.S_IFREG}:
        return "special"
    return "file"


def _zip_members(archive: zipfile.ZipFile) -> Iterator[_Member]:
    for info in archive.infolist():
        path, _ = _safe_member_path(info.filename)
        if info.flag_bits & 0x1:
            raise LicenseInventoryError(
                f"encrypted archive member cannot be inventoried: {info.filename!r}")
        kind = _zip_kind(info)
        if kind == "special":
            raise LicenseInventoryError(
                f"unsupported special archive member: {info.filename!r}")
        yield _Member(path, info.file_size if kind == "file" else 0, kind, info)


def _record_members(
    members: Iterator[_Member],
    *,
    limits: InventoryLimits,
    open_member,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, int, int]:
    matches: list[dict[str, object]] = []
    nested_containers: list[dict[str, object]] = []
    seen: dict[str, str] = {}
    member_count = 0
    total_member_bytes = 0
    total_candidate_bytes = 0
    total_nested_probe_bytes = 0

    for member in members:
        member_count += 1
        if member_count > limits.max_members:
            raise LicenseInventoryError("archive member count exceeds the limit")
        _, collision_key = _safe_member_path(member.path)
        previous = seen.get(collision_key)
        if previous is not None:
            raise LicenseInventoryError(
                f"ambiguous archive member paths: {previous!r} and {member.path!r}")
        seen[collision_key] = member.path

        total_member_bytes += member.size
        if total_member_bytes > limits.max_total_member_bytes:
            raise LicenseInventoryError(
                "archive total declared member size exceeds the limit")
        if member.kind == "directory":
            continue
        candidate = _is_license_candidate(member.path)
        if member.kind != "file":
            if candidate:
                raise LicenseInventoryError(
                    "license-like archive member is not a regular file: "
                    f"{member.path!r}")
            continue

        if candidate:
            if len(matches) >= limits.max_candidates:
                raise LicenseInventoryError(
                    "license-like archive member count exceeds the limit")
            if member.size > limits.max_candidate_bytes:
                raise LicenseInventoryError(
                    "license-like archive member exceeds the size limit: "
                    f"{member.path!r}")
            total_candidate_bytes += member.size
            if total_candidate_bytes > limits.max_total_candidate_bytes:
                raise LicenseInventoryError(
                    "license-like archive member total size exceeds the limit")

        probe_size = min(member.size, NESTED_HEADER_BYTES)
        total_nested_probe_bytes += probe_size
        if total_nested_probe_bytes > limits.max_nested_probe_bytes:
            raise LicenseInventoryError(
                "nested-container probe bytes exceed the limit")
        with open_member(member.source) as stream:
            prefix = _read_exact(
                stream, probe_size, label="archive member prefix")
            nested_format = _nested_container_format(prefix)
            digest = (_stream_sha256(
                stream,
                expected_size=member.size,
                maximum_size=limits.max_candidate_bytes,
                prefix=prefix,
            ) if candidate else None)
        if nested_format is not None:
            nested_containers.append({
                "format": nested_format,
                "path": member.path,
                "size": member.size,
            })
        if candidate:
            matches.append({
                "path": member.path,
                "sha256": digest,
                "size": member.size,
            })

    matches.sort(key=lambda item: str(item["path"]))
    nested_containers.sort(key=lambda item: str(item["path"]))
    return (
        matches,
        nested_containers,
        member_count,
        total_member_bytes,
        total_nested_probe_bytes,
    )


def inspect_license_archive(
    archive_path: Path,
    *,
    limits: InventoryLimits | None = None,
) -> dict[str, object]:
    limits = limits or InventoryLimits()
    limits.validate()
    requested = Path(archive_path)
    path = Path(os.path.abspath(requested))
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise LicenseInventoryError(f"cannot stat archive: {exc}") from exc
    if stat.S_ISLNK(path_metadata.st_mode):
        raise LicenseInventoryError("archive path must not be a symbolic link")
    if not stat.S_ISREG(path_metadata.st_mode):
        raise LicenseInventoryError("archive path must be a regular file")

    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0))
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise LicenseInventoryError(
            "this platform cannot open archives without following symbolic links")
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise LicenseInventoryError(
                "archive path must not be a symbolic link") from exc
        raise LicenseInventoryError(f"cannot open archive: {exc}") from exc

    try:
        source_stream = os.fdopen(descriptor, "rb")
    except OSError as exc:
        os.close(descriptor)
        raise LicenseInventoryError(f"cannot read archive: {exc}") from exc

    with source_stream as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise LicenseInventoryError("archive path must be a regular file")
        if _file_identity(metadata) != _file_identity(path_metadata):
            raise LicenseInventoryError("archive changed while it was opened")
        if metadata.st_size > limits.max_archive_bytes:
            raise LicenseInventoryError("archive size exceeds the limit")

        try:
            stack = ExitStack()
            archive_snapshot = stack.enter_context(
                tempfile.TemporaryFile(mode="w+b"))
            compressed_tar_spool = stack.enter_context(
                tempfile.TemporaryFile(mode="w+b"))
        except OSError as exc:
            stack.close()
            raise LicenseInventoryError(
                f"cannot create bounded archive workspace: {exc}") from exc
        with stack:
            try:
                initial_digest, snapshot_size = _snapshot_archive(
                    source, archive_snapshot,
                    maximum_size=limits.max_archive_bytes)
                # A same-size in-place rewrite can share one timestamp tick, so
                # the identity comparison cannot see it. Re-digest the source.
                source.seek(0)
                confirmation_digest = hashlib.sha256()
                while chunk := source.read(READ_CHUNK_BYTES):
                    confirmation_digest.update(chunk)
                copied_metadata = os.fstat(source.fileno())
                if (snapshot_size != metadata.st_size
                        or confirmation_digest.hexdigest() != initial_digest
                        or _file_identity(copied_metadata)
                        != _file_identity(metadata)):
                    raise LicenseInventoryError(
                        "archive changed while it was copied")
                archive_snapshot.seek(0)
                is_zip = _zip_preflight(archive_snapshot, limits)
                is_tar, tar_is_spooled = _tar_preflight(
                    archive_snapshot, limits,
                    compressed_spool=compressed_tar_spool)
            except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError,
                    zlib.error) as exc:
                raise LicenseInventoryError(
                    f"cannot read archive: {exc}") from exc
            if is_zip and is_tar:
                raise LicenseInventoryError("archive format is ambiguous")
            if not is_zip and not is_tar:
                raise LicenseInventoryError(
                    "archive is neither a supported tar nor ZIP file")

            try:
                if is_zip:
                    archive_snapshot.seek(0)
                    with zipfile.ZipFile(archive_snapshot, "r") as archive:
                        (matches, nested_containers, member_count,
                         total_member_bytes,
                         nested_probe_bytes) = _record_members(
                             _zip_members(archive), limits=limits,
                             open_member=archive.open)
                    archive_type = "zip"
                else:
                    if tar_is_spooled:
                        compressed_tar_spool.flush()
                        compressed_tar_spool.seek(0)
                        tar_context = tarfile.open(
                            fileobj=compressed_tar_spool, mode="r|")
                    else:
                        archive_snapshot.seek(0)
                        tar_context = tarfile.open(
                            fileobj=archive_snapshot, mode="r|")
                    with tar_context as archive:
                        def open_tar_member(source: object) -> BinaryIO:
                            assert isinstance(source, tarfile.TarInfo)
                            stream = archive.extractfile(source)
                            if stream is None:
                                raise LicenseInventoryError(
                                    "cannot read archive member: "
                                    f"{source.name!r}")
                            return stream

                        (matches, nested_containers, member_count,
                         total_member_bytes,
                         nested_probe_bytes) = _record_members(
                             _tar_members(archive), limits=limits,
                             open_member=open_tar_member)
                    archive_type = "tar"
            except (OSError, EOFError, tarfile.TarError, UnicodeError,
                    zipfile.BadZipFile, lzma.LZMAError, NotImplementedError,
                    RuntimeError, zlib.error) as exc:
                raise LicenseInventoryError(
                    f"cannot inspect archive: {exc}") from exc

            try:
                final_metadata = os.fstat(source.fileno())
                final_path_metadata = path.lstat()
            except OSError as exc:
                raise LicenseInventoryError(
                    f"cannot verify archive after inspection: {exc}") from exc
            if (_file_identity(final_metadata) != _file_identity(metadata)
                    or _file_identity(final_path_metadata)
                    != _file_identity(metadata)):
                raise LicenseInventoryError("archive changed during inspection")

    nested_scope_unreviewed = bool(nested_containers)
    return {
        "archive": {
            "path": str(path),
            "sha256": initial_digest,
            "size": metadata.st_size,
            "type": archive_type,
        },
        "complete": not nested_scope_unreviewed,
        "legal_conclusion": None,
        "license_like_members": matches,
        "limitations": [
            "Filename matching inventories evidence; it does not identify applicable terms.",
            "This report makes no legal, compatibility, or redistribution conclusion.",
            "Nested container members are detected but are not traversed.",
        ],
        "limits": limits.as_dict(),
        "members_scanned": member_count,
        "nested_containers": nested_containers,
        "nested_probe_bytes": nested_probe_bytes,
        "nested_scope_unreviewed": nested_scope_unreviewed,
        "ok": not nested_scope_unreviewed,
        "schema_version": 1,
        "total_declared_member_bytes": total_member_bytes,
        "truncated": False,
    }
