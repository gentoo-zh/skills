#!/usr/bin/env python3
"""Collect bounded QA and style candidates from Gentoo repository history."""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
TOOL_NAME = "gentoo-overlay-qa-style-collector"
MAX_RETRIEVAL_ATTEMPTS = 3
MAX_LIMIT = 1000
MAX_WORKERS = 32
MAX_INITIAL_DEPTH = 1000
MAX_SINCE_DAYS = 3650
MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
SKILL_ROOT = Path(__file__).resolve().parent.parent
ROOT = SKILL_ROOT.parents[2]
SOURCE_MANAGER_PATH = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
    / "source_manager.py")

TOPIC_ORDER = (
    "qa", "dependencies", "style", "metadata", "manifest", "license",
    "patch", "test", "build", "install",
)

TOPIC_SOURCE_IDS = {
    "qa": ("gentoo-qa-policy", "pkgcheck", "ebuild-manual"),
    "dependencies": ("pms-latest", "devmanual-dependencies"),
    "style": ("devmanual", "gentoo-qa-policy"),
    "metadata": ("pms-latest", "devmanual-metadata"),
    "manifest": ("devmanual-manifest", "pkgdev"),
    "license": ("pms-latest", "devmanual-licenses"),
    "patch": ("devmanual-patches",),
    "test": ("devmanual-tests", "ebuild-manual"),
    "build": ("devmanual", "ebuild-manual"),
    "install": ("ebuild-manual", "portage-emerge"),
}

SUBJECT_PATTERNS = {
    "qa": re.compile(
        r"\b(?:qa|pkgcheck|repoman|lint|elog|soname|rpath|prebuilt|strip)\b",
        re.IGNORECASE),
    "dependencies": re.compile(
        r"\b(?:deps?|dependenc(?:y|ies)|[bri]?depend|pdepend|idepend|"
        r"slot(?:\s+operator)?)\b", re.IGNORECASE),
    "style": re.compile(
        r"\b(?:style|cleanup|clean[ -]?up|format(?:ting)?|indent(?:ation)?|"
        r"whitespace|quot(?:e|ing)|shellcheck)\b", re.IGNORECASE),
    "metadata": re.compile(
        r"\b(?:metadata|maintainer|remote-id|repo(?:sitory)? name|profile)\b",
        re.IGNORECASE),
    "manifest": re.compile(r"\bmanifest\b", re.IGNORECASE),
    "license": re.compile(
        r"\b(?:licen[cs]e|copyright|redistribut(?:e|ion)|bindist|mirror)\b",
        re.IGNORECASE),
    "patch": re.compile(
        r"\b(?:patch|backport|cherry[ -]?pick)\b", re.IGNORECASE),
    "test": re.compile(
        r"\b(?:tests?|pytest|ctest|src_test|test suite)\b", re.IGNORECASE),
    "build": re.compile(
        r"\b(?:build|compile|toolchain|cmake|meson|src_compile|configure)\b",
        re.IGNORECASE),
    "install": re.compile(
        r"\b(?:install|merge|emerge|src_install|postinst|preinst)\b",
        re.IGNORECASE),
}


class GitError(RuntimeError):
    """A Git read or temporary-clone operation failed."""


def load_source_manager():
    spec = importlib.util.spec_from_file_location(
        "gzh_qa_style_source_manager", SOURCE_MANAGER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the registered source manager")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


source_manager = load_source_manager()


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def current_time(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).replace(microsecond=0)


def parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO 8601 date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def structured_error(stage: str, error: Exception) -> dict:
    return {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error),
    }


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_limited_process(command: list[str], repository: Path, *,
                        timeout: float = 120,
                        max_output_bytes: int = MAX_GIT_OUTPUT_BYTES
                        ) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_output_bytes < 0:
        raise ValueError("output limit must not be negative")
    try:
        process = subprocess.Popen(
            command, cwd=repository, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise GitError(f"{type(exc).__name__}: {exc}") from exc
    assert process.stdout is not None and process.stderr is not None
    streams = (process.stdout, process.stderr)
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    buffers = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    output_bytes = 0
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminate_process(process)
                raise GitError(f"command timed out after {timeout:g} seconds")
            events = selector.select(remaining)
            if not events:
                terminate_process(process)
                raise GitError(f"command timed out after {timeout:g} seconds")
            for key, _ in events:
                read_size = min(
                    64 * 1024, max_output_bytes - output_bytes + 1)
                chunk = os.read(key.fd, read_size)
                if not chunk:
                    stream = (process.stdout
                              if key.fd == stdout_fd else process.stderr)
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffers[key.fd].extend(chunk)
                output_bytes += len(chunk)
                if output_bytes > max_output_bytes:
                    terminate_process(process)
                    raise GitError(
                        f"command output exceeds {max_output_bytes} bytes")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate_process(process)
            raise GitError(f"command timed out after {timeout:g} seconds")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            terminate_process(process)
            raise GitError(
                f"command timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        terminate_process(process)
        raise GitError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        selector.close()
        for stream in streams:
            if not stream.closed:
                stream.close()
    stdout = buffers[stdout_fd].decode("utf-8", errors="replace")
    stderr = buffers[stderr_fd].decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def run_git(repository: Path, *arguments: str, check: bool = True,
            timeout: float = 120,
            max_output_bytes: int = MAX_GIT_OUTPUT_BYTES
            ) -> subprocess.CompletedProcess[str]:
    proc = run_limited_process(
        ["git", *arguments], repository, timeout=timeout,
        max_output_bytes=max_output_bytes)
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git failed"
        raise GitError(detail)
    return proc


def validate_ref(ref: str) -> None:
    if not ref or ref.startswith("-") or any(ord(char) < 32 for char in ref):
        raise GitError("invalid Git ref")


def validate_after_revision(revision: str | None) -> None:
    if revision and not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise GitError("after revision must be an immutable commit ID")


def validate_remote_url(url: str) -> str:
    if not url or any(ord(char) < 32 or char.isspace() for char in url):
        raise GitError("remote URL contains invalid characters")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise GitError("remote URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise GitError("remote URL must not contain credentials")
    if not parsed.hostname or parsed.query or parsed.fragment:
        raise GitError("remote URL must identify one HTTPS repository")
    try:
        parsed.port
    except ValueError as exc:
        raise GitError("remote URL has an invalid port") from exc
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise GitError("remote URL must not use localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
            raise GitError("remote URL has an invalid public hostname")
    else:
        if not address.is_global:
            raise GitError("remote URL must not use a non-public IP address")
    return url


def repository_is_shallow(repository: Path) -> bool:
    value = run_git(
        repository, "rev-parse", "--is-shallow-repository").stdout.strip()
    if value not in {"true", "false"}:
        raise GitError("cannot determine whether repository history is shallow")
    return value == "true"


def resolve_ref(repository: Path, ref: str) -> str:
    validate_ref(ref)
    proc = run_git(
        repository, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
        check=False)
    revision = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise GitError(f"ref does not resolve to a commit: {ref}")
    return revision.lower()


def git_file(repository: Path, revision: str, path: str) -> str:
    proc = run_git(
        repository, "show", f"{revision}:{path}", check=False)
    if proc.returncode != 0:
        raise GitError(f"Gentoo repository is missing {path}")
    return proc.stdout


def parse_layout(content: str) -> dict[str, str]:
    layout: dict[str, str] = {}
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise GitError(f"invalid metadata/layout.conf line {number}")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            raise GitError(f"invalid metadata/layout.conf line {number}")
        layout[key] = value.strip()
    return dict(sorted(layout.items()))


def repository_identity(repository: Path, revision: str) -> dict:
    repo_name = git_file(
        repository, revision, "profiles/repo_name").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", repo_name):
        raise GitError("profiles/repo_name is empty or invalid")
    layout = parse_layout(git_file(
        repository, revision, "metadata/layout.conf"))
    return {
        "repo_name": repo_name,
        "layout": layout,
    }


def normalized_git_url(url: str) -> tuple[str, int | None, str]:
    if not url or any(ord(char) < 32 or char.isspace() for char in url):
        raise GitError("Git remote URL contains invalid characters")
    if "://" not in url:
        matched = re.fullmatch(
            r"(?:[^/@:]+@)?(?P<host>[^/:]+):(?P<path>.+)", url)
        if not matched:
            raise GitError("Git remote URL must identify a network repository")
        hostname = matched.group("host").lower()
        port = None
        path = matched.group("path")
    else:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"git", "http", "https", "ssh"}:
            raise GitError("Git remote URL must identify a network repository")
        if (parsed.password is not None
                or (parsed.scheme.lower() in {"http", "https"}
                    and parsed.username is not None)):
            raise GitError("Git remote URL must not contain credentials")
        if not parsed.hostname or parsed.query or parsed.fragment:
            raise GitError("Git remote URL must identify one network repository")
        try:
            port = parsed.port
        except ValueError as exc:
            raise GitError("Git remote URL has an invalid port") from exc
        hostname = parsed.hostname.rstrip(".").lower()
        path = parsed.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not hostname or not path:
        raise GitError("Git remote URL must identify one network repository")
    return hostname, port, path


def local_origin(repository: Path, canonical_url: str) -> str:
    expected = normalized_git_url(canonical_url)
    remotes = run_git(repository, "remote").stdout.splitlines()
    matches: dict[str, str] = {}
    for remote in remotes:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", remote):
            raise GitError("local repository has an invalid remote name")
        proc = run_git(
            repository, "remote", "get-url", "--all", remote, check=False)
        if proc.returncode != 0:
            continue
        for url in proc.stdout.splitlines():
            try:
                normalized = normalized_git_url(url)
            except GitError:
                continue
            if normalized == expected:
                matches.setdefault(remote, url)
    if not matches:
        raise GitError("no Git remote matches the configured canonical URL")
    if len(matches) > 1:
        names = ", ".join(sorted(matches))
        raise GitError(
            f"multiple Git remotes match the configured canonical URL: {names}")
    return next(iter(matches.values()))


def commit_headers(repository: Path, revision: str, count: int,
                   after_revision: str | None = None
                   ) -> list[tuple[str, datetime]]:
    revision_range = (
        f"{after_revision}..{revision}" if after_revision else revision)
    output = run_git(
        repository, "log", f"--max-count={count}",
        "--format=%H%x00%cI", revision_range).stdout
    records = []
    for line in output.splitlines():
        fields = line.split("\0", 1)
        if len(fields) != 2:
            raise GitError("cannot parse commit dates")
        try:
            date = datetime.fromisoformat(fields[1])
        except ValueError as exc:
            raise GitError("cannot parse commit date") from exc
        records.append((fields[0].lower(), date.astimezone(timezone.utc)))
    return records


def history_record(state: str, limit: int, retrieved: int,
                   shallow: bool | None, attempts: int,
                   since: datetime | None = None,
                   commits_in_window: int | None = None,
                   boundary_reached: bool | None = None,
                   limit_truncated: bool = False,
                   after_revision: str | None = None,
                   cursor_state: str = "missing",
                   truncation_reason: str | None = None,
                   error: dict | None = None) -> dict:
    return {
        "state": state,
        "complete": state == "complete",
        "truncated": state == "truncated",
        "repository_complete": None if shallow is None else not shallow,
        "shallow": shallow,
        "requested_commits": limit,
        "commits_retrieved": retrieved,
        "since": utc_text(since) if since else None,
        "after_revision": after_revision,
        "cursor_state": cursor_state,
        "commits_in_window": commits_in_window,
        "time_boundary_reached": boundary_reached,
        "time_window_complete": (
            bool(boundary_reached and not limit_truncated) if since else None),
        "limit_truncated": limit_truncated,
        "truncation_reason": truncation_reason or (
            "limit" if limit_truncated else
            "history-boundary" if state == "truncated" else None),
        "retrieval_attempts": attempts,
        "deepening_attempts": max(0, attempts - 1),
        "max_retrieval_attempts": MAX_RETRIEVAL_ATTEMPTS,
        "error": error,
    }


def commit_available(repository: Path, revision: str) -> bool:
    return run_git(
        repository, "cat-file", "-e", f"{revision}^{{commit}}",
        check=False).returncode == 0


def inspect_history(repository: Path, revision: str, limit: int,
                    attempts: int, since: datetime | None,
                    after_revision: str | None) -> dict:
    shallow = repository_is_shallow(repository)
    if after_revision:
        validate_after_revision(after_revision)
        after_revision = after_revision.lower()
        if not commit_available(repository, after_revision):
            records = commit_headers(repository, revision, limit + 1)
            limit_truncated = len(records) > limit
            return history_record(
                "truncated", limit, len(records), shallow, attempts,
                after_revision=after_revision,
                cursor_state="not-retrieved",
                limit_truncated=limit_truncated,
                truncation_reason=(
                    "limit" if limit_truncated else "cursor-not-retrieved"))
        ancestor = run_git(
            repository, "merge-base", "--is-ancestor", after_revision,
            revision, check=False)
        if ancestor.returncode == 1:
            raise GitError("after revision is not an ancestor of the observed tip")
        if ancestor.returncode != 0:
            raise GitError("cannot verify after-revision ancestry")
        records = commit_headers(
            repository, revision, limit + 1, after_revision)
        limit_truncated = len(records) > limit
        return history_record(
            "truncated" if limit_truncated else "complete", limit,
            len(records), shallow, attempts,
            commits_in_window=len(records), boundary_reached=True,
            limit_truncated=limit_truncated, after_revision=after_revision,
            cursor_state="verified")

    scan_count = limit + 1 if since else limit
    records = commit_headers(repository, revision, scan_count)
    if since is None:
        return history_record(
            "truncated", limit, len(records), shallow, attempts,
            cursor_state="missing", truncation_reason="missing-cursor")

    commits_in_window = sum(date >= since for _, date in records)
    limit_truncated = commits_in_window > limit
    boundary_reached = not shallow or any(date < since for _, date in records)
    return history_record(
        "truncated", limit, len(records), shallow,
        attempts, since=since, commits_in_window=commits_in_window,
        boundary_reached=boundary_reached, limit_truncated=limit_truncated,
        cursor_state="missing", truncation_reason=(
            "limit" if limit_truncated else "missing-cursor"))


def parse_numstat(output: str) -> tuple[list[str], dict]:
    files: list[str] = []
    insertions = 0
    deletions = 0
    binary_files = 0
    for record in output.split("\0"):
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) != 3:
            raise GitError("cannot parse commit numstat")
        added, removed, path = fields
        files.append(path)
        if added == "-" or removed == "-":
            binary_files += 1
        else:
            try:
                insertions += int(added)
                deletions += int(removed)
            except ValueError as exc:
                raise GitError("cannot parse commit numstat totals") from exc
    files.sort()
    return files, {
        "files_changed": len(files),
        "insertions": insertions,
        "deletions": deletions,
        "binary_files": binary_files,
    }


def commit_provenance(repository: Path, revision: str) -> dict:
    header = run_git(
        repository, "show", "--no-show-signature", "-s",
        "--format=%H%x00%cI%x00%s", revision).stdout.rstrip("\n")
    fields = header.split("\0", 2)
    if len(fields) != 3:
        raise GitError("cannot parse commit provenance")
    sha, date, subject = fields
    numstat = run_git(
        repository, "diff-tree", "--root", "--first-parent",
        "--no-commit-id", "--numstat", "-r", "-z", "--no-renames",
        revision).stdout
    files, stat = parse_numstat(numstat)
    return {
        "sha": sha.lower(),
        "date": date,
        "subject": subject,
        "files": files,
        "stat": stat,
    }


def path_matches(topic: str, path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    parts = set(lowered.split("/"))
    if topic == "qa":
        return name in {"pkgcheck.yml", "pkgcheck.yaml"} or "qa" in parts
    if topic == "dependencies":
        return name in {"package.mask", "package.use", "package.accept_keywords"}
    if topic == "style":
        return name in {".editorconfig", ".shellcheckrc"}
    if topic == "metadata":
        return (name == "metadata.xml" or "metadata" in parts
                or name in {"layout.conf", "repo_name", "categories"})
    if topic == "manifest":
        return name == "manifest"
    if topic == "license":
        return "licenses" in parts or bool(re.search(
            r"(?:^|[-_.])licen[cs]e(?:[-_.]|$)", name))
    if topic == "patch":
        return lowered.endswith((".patch", ".diff"))
    if topic == "test":
        return bool(parts & {"test", "tests"}) or name.startswith("test_")
    if topic == "build":
        return name in {
            "cmakelists.txt", "meson.build", "configure.ac", "makefile",
            "build.yml", "build.yaml",
        }
    if topic == "install":
        return name in {"install", "install.sh", "emerge-on-pr.yml"}
    raise ValueError(f"unknown topic: {topic}")


def route_topics(subject: str, files: list[str]) -> list[str]:
    return [
        topic for topic in TOPIC_ORDER
        if SUBJECT_PATTERNS[topic].search(subject)
        or any(path_matches(topic, path) for path in files)
    ]


def primary_source_ids(topics: list[str]) -> list[str]:
    return sorted({
        source_id
        for topic in topics
        for source_id in TOPIC_SOURCE_IDS[topic]
    })


def candidate_source_id(revision: str) -> str:
    return f"candidate-history:{revision}"


def evidence_url(scope: dict) -> str:
    return (scope.get("canonical_origin")
            or scope.get("configured_origin") or scope["location"])


def collect_candidates(repository: Path, revision: str, limit: int,
                       since: datetime | None, after_revision: str | None,
                       scope: dict) -> list[dict]:
    if after_revision:
        records = commit_headers(
            repository, revision, limit + 1, after_revision)
        revisions = [sha for sha, _ in records[:limit]]
    else:
        scan_count = limit + 1 if since else limit
        records = commit_headers(repository, revision, scan_count)
        revisions = [
            sha for sha, date in records if since is None or date >= since
        ][:limit]
    candidates = []
    for commit in revisions:
        provenance = commit_provenance(repository, commit)
        topics = route_topics(provenance["subject"], provenance["files"])
        for topic in topics:
            candidates.append({
                "topic": topic,
                "scope": scope["canonical_repository"],
                "adapter_id": scope["adapter_id"],
                "authority": "candidate-history",
                "source_id": candidate_source_id(provenance["sha"]),
                "source_url": evidence_url(scope),
                "source_revision": provenance["sha"],
                "policy_status": "not-established",
                "validation_source_ids": list(TOPIC_SOURCE_IDS[topic]),
                "provenance": provenance,
            })
    return candidates


def observed_revision(record: dict | None) -> str | None:
    if not record:
        return None
    return record.get("revision") or record.get("sha256")


def official_source_records(topics: list[str], audit_sources: bool,
                            workers: int) -> list[dict]:
    registry = source_manager.load_registry()
    lock = source_manager.load_lock()
    registered = {source["id"]: source for source in registry["sources"]}
    wanted = primary_source_ids(topics)
    missing = [source_id for source_id in wanted if source_id not in registered]
    if missing:
        raise ValueError(
            f"registered sources are missing: {', '.join(missing)}")
    sources = [registered[source_id] for source_id in wanted]
    permitted = {"gentoo-standard", "gentoo-tool", "gentoo-practice"}
    invalid = [source["id"] for source in sources
               if source["authority"] not in permitted
               or source.get("scope") != "portable-core"]
    if invalid:
        raise ValueError(
            f"sources are not official Gentoo evidence: {', '.join(invalid)}")
    topic_map = {
        source["id"]: [topic for topic in TOPIC_ORDER
                       if source["id"] in TOPIC_SOURCE_IDS[topic]
                       and topic in topics]
        for source in sources
    }

    if not audit_sources:
        return [{
            "id": source["id"],
            "authority": source["authority"],
            "url": source["url"],
            "revision": observed_revision(lock["sources"].get(source["id"])),
            "state": "validation-target",
            "validated": False,
            "role": "primary-validation",
            "title": source["title"],
            "topics": topic_map[source["id"]],
            "use": source["use"],
        } for source in sources]

    audited = source_manager.audit(sources, lock, workers=max(1, workers))
    return [{
        "id": result["id"],
        "authority": result["authority"],
        "url": result["url"],
        "revision": observed_revision(result["observed"]),
        "state": result["state"],
        "validated": result["state"] == "current",
        "role": "primary-validation",
        "title": result["title"],
        "topics": topic_map[result["id"]],
        "use": registered[result["id"]]["use"],
        "locked": result["locked"],
        "observed": result["observed"],
    } for result in audited]


def candidate_source_records(candidates: list[dict], scope: dict) -> list[dict]:
    records: dict[str, dict] = {}
    topics: dict[str, set[str]] = {}
    for candidate in candidates:
        source_id = candidate["source_id"]
        if source_id not in records:
            records[source_id] = {
                "id": source_id,
                "source_id": source_id,
                "authority": "candidate-history",
                "url": candidate["source_url"],
                "revision": candidate["source_revision"],
                "state": "observed",
                "validated": False,
                "role": "candidate",
                "adapter_id": scope["adapter_id"],
                "canonical_repository": scope["canonical_repository"],
                "repo_name": scope["repo_name"],
            }
            topics[source_id] = set()
        topics[source_id].add(candidate["topic"])
    for source_id, record in records.items():
        record["topics"] = [
            topic for topic in TOPIC_ORDER if topic in topics[source_id]]
    return list(records.values())


def cursor_source_record(scope: dict, history: dict) -> dict:
    return {
        "id": "scope-cursor",
        "source_id": "scope-cursor",
        "authority": "repository-cursor",
        "url": evidence_url(scope),
        "revision": scope.get("resolved_ref"),
        "state": "observed" if scope.get("resolved_ref") else history["state"],
        "validated": False,
        "role": "cursor",
        "topics": [],
        "adapter_id": scope.get("adapter_id"),
        "canonical_repository": scope.get("canonical_repository"),
        "repo_name": scope.get("repo_name"),
        "complete": history["complete"],
        "truncated": history["truncated"],
    }


def report_for_repository(scope: dict, repository: Path, revision: str,
                          history: dict, audit_sources: bool, workers: int,
                          generated_at: datetime, since: datetime | None,
                          after_revision: str | None) -> dict:
    errors = []
    candidates = collect_candidates(
        repository, revision, history["requested_commits"], since,
        after_revision if history["cursor_state"] == "verified" else None,
        scope)
    topics = [topic for topic in TOPIC_ORDER
              if any(candidate["topic"] == topic for candidate in candidates)]
    source_records = [
        cursor_source_record(scope, history),
        *candidate_source_records(candidates, scope),
    ]
    try:
        source_records.extend(
            official_source_records(topics, audit_sources, workers))
    except (OSError, RuntimeError, ValueError) as exc:
        error = structured_error("official-sources", exc)
        errors.append(error)
        source_records.append({
            "id": "official-source-registry",
            "authority": "configuration",
            "url": str(SOURCE_MANAGER_PATH),
            "revision": None,
            "state": "error",
            "validated": False,
            "role": "primary-validation",
            "error": error,
        })
    for source in source_records:
        if (source.get("role") != "primary-validation"
                or source["state"] != "error" or "error" in source):
            continue
        observed = source.get("observed") or {}
        errors.append({
            "stage": "official-sources",
            "type": "SourceRetrievalError",
            "message": observed.get("error", f"source failed: {source['id']}"),
            "source_id": source["id"],
        })
    primary = [source for source in source_records
               if source.get("role") == "primary-validation"]
    primary_validation_complete = (
        audit_sources and all(
            source["state"] == "current" and source.get("validated") is True
            for source in primary))
    limitations = []
    if history["cursor_state"] == "missing":
        limitations.append(
            "date windows are bootstrap discovery and do not prove history "
            "completeness")
    elif history["cursor_state"] == "not-retrieved":
        limitations.append("after revision was not retrieved within three attempts")
    if after_revision and since:
        limitations.append(
            "the date boundary was ignored because after revision defines the window")
    if not audit_sources:
        limitations.append("primary source validation was not requested")
    elif not primary_validation_complete:
        limitations.append("one or more primary sources are not current")
    complete = history["complete"] and primary_validation_complete
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "generated_at": utc_text(generated_at),
        "complete": complete,
        "truncated": history["truncated"],
        "history_complete": history["complete"],
        "primary_validation_complete": primary_validation_complete,
        "output_complete": True,
        "scope": scope,
        "history": history,
        "source_records": source_records,
        "candidates": candidates,
        "errors": errors,
        "limitations": limitations,
        "output": {
            "max_bytes": MAX_REPORT_BYTES,
            "candidate_records_total": len(candidates),
            "candidate_records_emitted": len(candidates),
        },
        "ok": complete,
    }
    return enforce_output_cap(report)


def report_size(report: dict) -> int:
    return len(json.dumps(report, indent=2, sort_keys=True).encode("utf-8")) + 1


def records_for_candidates(records: list[dict],
                           candidates: list[dict]) -> list[dict]:
    source_ids = {candidate["source_id"] for candidate in candidates}
    return [
        record for record in records
        if record.get("role") != "candidate" or record["id"] in source_ids
    ]


def enforce_output_cap(report: dict,
                       maximum: int = MAX_REPORT_BYTES) -> dict:
    if report_size(report) <= maximum:
        return report
    candidates = report["candidates"]
    source_records = report.get("source_records")
    report["complete"] = False
    report["truncated"] = True
    report["output_complete"] = False
    report["ok"] = False
    report["limitations"].append(
        f"candidate output exceeded the {maximum}-byte report cap")
    report["output"]["max_bytes"] = maximum
    low = 0
    high = len(candidates)
    while low < high:
        middle = (low + high + 1) // 2
        report["candidates"] = candidates[:middle]
        if source_records is not None:
            report["source_records"] = records_for_candidates(
                source_records, report["candidates"])
        report["output"]["candidate_records_emitted"] = middle
        if report_size(report) <= maximum:
            low = middle
        else:
            high = middle - 1
    report["candidates"] = candidates[:low]
    if source_records is not None:
        report["source_records"] = records_for_candidates(
            source_records, report["candidates"])
    report["output"]["candidate_records_emitted"] = low
    return report


def error_report(scope: dict, limit: int, error: Exception,
                 generated_at: datetime, since: datetime | None,
                 stage: str, attempts: int = 0) -> dict:
    detail = structured_error(stage, error)
    history = history_record(
        "error", limit, 0, None, attempts, since=since,
        after_revision=scope.get("after_revision"), cursor_state="error",
        error=detail)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "generated_at": utc_text(generated_at),
        "complete": False,
        "truncated": False,
        "history_complete": False,
        "primary_validation_complete": False,
        "output_complete": True,
        "scope": scope,
        "history": history,
        "source_records": [cursor_source_record(scope, history)],
        "candidates": [],
        "errors": [detail],
        "limitations": [],
        "output": {
            "max_bytes": MAX_REPORT_BYTES,
            "candidate_records_total": 0,
            "candidate_records_emitted": 0,
        },
        "ok": False,
    }


def validate_adapter_configuration(adapter_id: str | None,
                                   canonical_repository: str | None) -> None:
    if not adapter_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", adapter_id):
        raise GitError("adapter ID is missing or invalid")
    if (not canonical_repository
            or any(char.isspace() or ord(char) < 32
                   for char in canonical_repository)):
        raise GitError("canonical repository identity is missing or invalid")


def initial_scope(kind: str, location: str, ref: str, limit: int,
                  since: datetime | None, after_revision: str | None,
                  adapter_id: str | None,
                  canonical_repository: str | None) -> dict:
    return {
        "kind": kind,
        "location": location,
        "requested_ref": ref,
        "resolved_ref": None,
        "commit_limit": limit,
        "since": utc_text(since) if since else None,
        "after_revision": after_revision,
        "window_mode": (
            "revision-cursor" if after_revision else
            "time-bootstrap" if since else "count-bootstrap"),
        "evidence_use": "candidate-discovery",
        "policy_claims": False,
        "adapter_id": adapter_id,
        "canonical_repository": canonical_repository,
        "observed_revision": None,
        "repo_name": None,
        "layout": None,
        "configured_origin": None,
        "canonical_origin": None,
        "canonical_origin_state": "adapter-pending",
    }


def collect_local(path: Path, ref: str, limit: int,
                  audit_sources: bool, workers: int,
                  since: datetime | None = None,
                  generated_at: datetime | None = None,
                  after_revision: str | None = None,
                  adapter_id: str | None = None,
                  canonical_repository: str | None = None,
                  canonical_url: str | None = None) -> dict:
    generated = current_time(generated_at)
    after_revision = after_revision.lower() if after_revision else None
    location = str(path.expanduser().resolve())
    scope = initial_scope(
        "local", location, ref, limit, since, after_revision, adapter_id,
        canonical_repository)
    try:
        validate_adapter_configuration(adapter_id, canonical_repository)
        if not canonical_url:
            raise GitError("canonical URL is required for local collection")
        validate_after_revision(after_revision)
        repository = Path(location)
        valid = run_git(repository, "rev-parse", "--git-dir", check=False)
        if valid.returncode != 0:
            raise GitError("local path is not a Git repository")
        revision = resolve_ref(repository, ref)
        scope["resolved_ref"] = revision
        scope["observed_revision"] = revision
        origin = local_origin(repository, canonical_url)
        scope.update(repository_identity(repository, revision))
        scope["configured_origin"] = origin
        scope["canonical_origin"] = canonical_url
        scope["canonical_origin_state"] = "verified"
        history = inspect_history(repository, revision, limit, attempts=0,
                                  since=since, after_revision=after_revision)
        if history["cursor_state"] == "not-retrieved":
            raise GitError("after revision is not available in the local repository")
        return report_for_repository(
            scope, repository, revision, history, audit_sources, workers,
            generated, since, after_revision)
    except (GitError, OSError, ValueError) as exc:
        return error_report(
            scope, limit, exc, generated, since, "local-repository")


def collect_remote(url: str, ref: str, limit: int, initial_depth: int,
                   audit_sources: bool, workers: int,
                   since: datetime | None = None,
                   generated_at: datetime | None = None,
                   after_revision: str | None = None,
                   adapter_id: str | None = None,
                   canonical_repository: str | None = None) -> dict:
    generated = current_time(generated_at)
    after_revision = after_revision.lower() if after_revision else None
    scope = initial_scope(
        "remote", url, ref, limit, since, after_revision, adapter_id,
        canonical_repository)
    scope["initial_depth"] = initial_depth
    attempts = 0
    try:
        validate_adapter_configuration(adapter_id, canonical_repository)
        validate_after_revision(after_revision)
        validated_url = validate_remote_url(url)
        validate_ref(ref)
    except GitError as exc:
        return error_report(
            scope, limit, exc, generated, since, "remote-validation")
    try:
        with tempfile.TemporaryDirectory(prefix="gzh-qa-style-") as temporary:
            repository = Path(temporary) / "repository.git"
            repository.mkdir()
            run_git(repository, "init", "--bare", "--quiet")
            revision = None
            history = None
            for attempt in range(1, MAX_RETRIEVAL_ATTEMPTS + 1):
                attempts = attempt
                if attempt == 1:
                    depth_option = f"--depth={initial_depth}"
                else:
                    deepen = initial_depth * (2 ** (attempt - 2))
                    depth_option = f"--deepen={deepen}"
                proc = run_git(
                    repository, "fetch", "--quiet", "--no-tags",
                    depth_option, validated_url, ref, check=False)
                if proc.returncode != 0:
                    detail = proc.stderr.strip() or proc.stdout.strip()
                    raise GitError(detail or "remote fetch failed")
                observed = resolve_ref(repository, "FETCH_HEAD")
                if revision is None:
                    revision = observed
                    scope["resolved_ref"] = revision
                    scope["observed_revision"] = revision
                    scope.update(repository_identity(repository, revision))
                    scope["configured_origin"] = validated_url
                    scope["canonical_origin_state"] = "adapter-pending"
                elif observed != revision:
                    raise GitError("remote ref changed during history retrieval")
                history = inspect_history(
                    repository, revision, limit, attempts=attempts, since=since,
                    after_revision=after_revision)
                if history["complete"] or history["limit_truncated"]:
                    break
            assert revision is not None and history is not None
            return report_for_repository(
                scope, repository, revision, history, audit_sources, workers,
                generated, since, after_revision)
    except (GitError, OSError, ValueError) as exc:
        return error_report(
            scope, limit, exc, generated, since, "remote-retrieval",
            attempts=attempts)


def atomic_write(path: Path, content: str) -> None:
    destination = path.expanduser().resolve()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bounded_int(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be between {minimum} and {maximum}")
        return number
    return parse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--local", "--overlay", "--overlay-path", dest="local", type=Path,
        help="read an existing local overlay without modifying it")
    source.add_argument(
        "--remote", "--overlay-url", dest="remote",
        help="fetch a public HTTPS repository into a temporary clone")
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--since", type=parse_time,
        help="collect commits at or after this ISO 8601 boundary")
    window.add_argument(
        "--since-days",
        type=bounded_int("--since-days", 1, MAX_SINCE_DAYS),
        help="collect this many UTC days before generated_at")
    parser.add_argument(
        "--after-revision",
        help="immutable prior tip that must be an ancestor of the observed tip")
    parser.add_argument(
        "--adapter-id", required=True,
        help="configured repository adapter identifier")
    parser.add_argument(
        "--canonical-repository", required=True,
        help="configured canonical repository identity")
    parser.add_argument(
        "--canonical-url",
        help="adapter-configured canonical URL for local remote resolution")
    parser.add_argument(
        "--ref", default="HEAD", help="commit, branch, or tag to inspect")
    parser.add_argument(
        "--limit", type=bounded_int("--limit", 1, MAX_LIMIT), default=500,
        help="maximum commits in the requested window")
    parser.add_argument(
        "--initial-depth",
        type=bounded_int("--initial-depth", 1, MAX_INITIAL_DEPTH), default=128,
        help="initial remote shallow-fetch depth")
    parser.add_argument(
        "--audit-sources", action="store_true",
        help="audit routed official sources against the reviewed source lock")
    parser.add_argument(
        "--workers", type=bounded_int("--workers", 1, MAX_WORKERS), default=4,
        help="workers for the optional registered-source audit")
    parser.add_argument("--output", type=Path,
                        help="atomically write JSON to this explicit path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = current_time()
    since = args.since
    if args.since_days is not None:
        since = generated_at - timedelta(days=args.since_days)
    if args.local is not None:
        report = collect_local(
            args.local, args.ref, args.limit, args.audit_sources, args.workers,
            since, generated_at, args.after_revision, args.adapter_id,
            args.canonical_repository, args.canonical_url)
    else:
        report = collect_remote(
            args.remote, args.ref, args.limit, args.initial_depth,
            args.audit_sources, args.workers, since, generated_at,
            args.after_revision, args.adapter_id, args.canonical_repository)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        try:
            atomic_write(args.output, payload)
        except OSError as exc:
            error = structured_error("output", exc)
            report["errors"].append(error)
            report["complete"] = False
            report["output_complete"] = False
            report["ok"] = False
            print(
                f"Could not write QA/style report to {args.output}: {exc}",
                file=sys.stderr)
            return 1
        state = "complete" if report["ok"] else "incomplete"
        print(f"Wrote {state} QA/style report to {args.output}.")
    else:
        print(payload, end="")
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
