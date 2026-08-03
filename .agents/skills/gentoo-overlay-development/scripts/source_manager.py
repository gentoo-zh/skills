#!/usr/bin/env python3
"""Query and audit the shared evidence registry without rewriting policy."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_ROOT / "references" / "sources.json"
LOCK_PATH = SKILL_ROOT / "references" / "source-lock.json"
MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MEDIAWIKI_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 256 * 1024
MAX_NETWORK_RESULT_BYTES = 64 * 1024
NETWORK_TOTAL_TIMEOUT_SECONDS = 60
NETWORK_CONNECT_TIMEOUT_SECONDS = 15
MAX_AUDIT_WORKERS = 16
GIT_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


def read_bounded(response, maximum: int) -> bytes:
    content = bytearray()
    while True:
        remaining = maximum - len(content)
        chunk = response.read(min(65536, remaining + 1))
        if not chunk:
            return bytes(content)
        if len(chunk) > remaining:
            raise ValueError(f"response exceeds {maximum} bytes")
        content.extend(chunk)


def stop_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        proc.wait()


def bounded_process(command: list[str], *, timeout: float,
                    maximum: int) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    if proc.stdout is None or proc.stderr is None:
        stop_process_group(proc)
        raise RuntimeError("cannot capture subprocess output")
    stdout_fd = proc.stdout.fileno()
    stderr_fd = proc.stderr.fileno()
    streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    selector.register(proc.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"command timed out after {timeout} seconds")
            for key, _events in selector.select(min(remaining, 0.25)):
                current_size = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, maximum - current_size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = maximum - current_size
                streams[key.fd].extend(chunk[:available])
                if len(chunk) > available:
                    raise RuntimeError(
                        f"command output exceeded {maximum} bytes")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"command timed out after {timeout} seconds")
        try:
            returncode = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"command timed out after {timeout} seconds") from exc
    except Exception:
        stop_process_group(proc)
        raise
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    return subprocess.CompletedProcess(
        command, returncode,
        stdout=bytes(streams[stdout_fd]).decode("utf-8", errors="replace"),
        stderr=bytes(streams[stderr_fd]).decode("utf-8", errors="replace"))


@contextmanager
def exclusive_lock(path: Path):
    descriptor = os.open(
        path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("sources"), list):
        raise ValueError("unsupported source registry schema")
    allowed = set(data.get("authorities", []))
    scopes = set(data.get("scopes", []))
    authority_scopes = data.get("authority_scopes")
    if (not allowed or not scopes or not isinstance(authority_scopes, dict)
            or set(authority_scopes) != allowed):
        raise ValueError("source registry has invalid authority scopes")
    for authority, allowed_scopes in authority_scopes.items():
        if (not isinstance(allowed_scopes, list) or not allowed_scopes
                or any(scope not in scopes for scope in allowed_scopes)):
            raise ValueError(
                f"invalid scope allowlist for authority: {authority}")
    ids = set()
    for source in data["sources"]:
        required = {
            "id", "title", "authority", "scope", "kind", "url", "topics", "use"}
        if not required.issubset(source):
            raise ValueError(f"incomplete source entry: {source.get('id')}")
        if source["id"] in ids:
            raise ValueError(f"duplicate source id: {source['id']}")
        if source["authority"] not in allowed:
            raise ValueError(f"unknown authority: {source['authority']}")
        if source["scope"] not in scopes:
            raise ValueError(f"unknown source scope: {source['id']}")
        if source["scope"] not in authority_scopes[source["authority"]]:
            raise ValueError(
                f"authority is not allowed in source scope: {source['id']}")
        if source["kind"] not in {"git", "http", "mediawiki"}:
            raise ValueError(f"unknown source kind: {source['kind']}")
        if source["kind"] == "mediawiki" and not source.get("api_url"):
            raise ValueError(f"mediawiki source has no api_url: {source['id']}")
        ids.add(source["id"])
    return data


def load_lock(path: Path = LOCK_PATH) -> dict:
    if not path.exists():
        return {"schema": 1, "sources": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("sources"), dict):
        raise ValueError("unsupported source lock schema")
    return data


def select_sources(registry: dict, ids: list[str], topic: str | None,
                   authority: str | None,
                   scope: str | None = None) -> list[dict]:
    selected = registry["sources"]
    if ids:
        wanted = set(ids)
        selected = [source for source in selected if source["id"] in wanted]
    if topic:
        selected = [source for source in selected if topic in source["topics"]]
    if authority:
        selected = [source for source in selected
                    if source["authority"] == authority]
    if scope:
        if scope not in set(registry.get("scopes", [])):
            raise ValueError(f"unknown source scope: {scope}")
        selected = [source for source in selected if source["scope"] == scope]
    if ids:
        missing = set(ids) - {source["id"] for source in selected}
        if missing:
            raise ValueError(
                "source ids do not match the selected filters: "
                + ", ".join(sorted(missing)))
    return selected


def resolve_git_ref(url: str, ref: str) -> str:
    proc = bounded_process(
        ["git", "ls-remote", url, ref],
        timeout=60, maximum=MAX_GIT_OUTPUT_BYTES)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ref not found: {ref}")
    lines = [line.split() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
        raise RuntimeError(f"expected exactly one result for ref: {ref}")
    revision = lines[0][0]
    if not GIT_REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError(f"invalid revision for ref: {ref}")
    return revision


def observe_network_direct(kind: str, url: str, maximum: int) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "gentoo-overlay-source-audit/1"})
    with urllib.request.urlopen(
            request, timeout=NETWORK_CONNECT_TIMEOUT_SECONDS) as response:
        content = read_bounded(response, maximum)
        if kind == "mediawiki":
            data = json.loads(content)
            pages = ((data.get("query") or {}).get("pages") or [])
            revisions = pages[0].get("revisions", []) if pages else []
            if not revisions or "revid" not in revisions[0]:
                raise RuntimeError("MediaWiki response has no revision id")
            return {"ok": True, "kind": kind,
                    "revision": str(revisions[0]["revid"])}
        return {
            "ok": True,
            "kind": kind,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "final_url": response.geturl(),
        }


def observe_network_bounded(source: dict) -> dict:
    kind = source["kind"]
    url = source["api_url"] if kind == "mediawiki" else source["url"]
    maximum = (MAX_MEDIAWIKI_RESPONSE_BYTES if kind == "mediawiki"
               else MAX_HTTP_RESPONSE_BYTES)
    proc = bounded_process(
        [sys.executable, str(Path(__file__).resolve()), "_observe-network",
         "--kind", kind, "--url", url, "--maximum", str(maximum)],
        timeout=NETWORK_TOTAL_TIMEOUT_SECONDS,
        maximum=MAX_NETWORK_RESULT_BYTES)
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or f"network observer failed for {url}")
    result = json.loads(proc.stdout)
    if (not isinstance(result, dict) or result.get("kind") != kind
            or not isinstance(result.get("ok"), bool)):
        raise RuntimeError("network observer returned an invalid result")
    return result


def observe(source: dict) -> dict:
    try:
        if source["kind"] == "git":
            ref = source.get("ref", "HEAD")
            revision = resolve_git_ref(source["url"], ref)
            return {"ok": True, "kind": "git", "revision": revision}
        return observe_network_bounded(source)
    except Exception as exc:
        return {"ok": False, "kind": source["kind"],
                "error": f"{type(exc).__name__}: {exc}"}


def audit(sources: list[dict], lock: dict, workers: int = 8) -> list[dict]:
    if not 1 <= workers <= MAX_AUDIT_WORKERS:
        raise ValueError(
            f"workers must be between 1 and {MAX_AUDIT_WORKERS}")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        observed = list(executor.map(observe, sources))
    results = []
    for source, current in zip(sources, observed):
        locked = lock["sources"].get(source["id"])
        if not current["ok"]:
            state = "error"
        elif locked is None:
            state = "unlocked"
        else:
            key = "sha256" if source["kind"] == "http" else "revision"
            matches = locked.get(key) == current.get(key)
            if source["kind"] == "http":
                matches = (matches
                           and locked.get("final_url")
                           == current.get("final_url"))
            state = "current" if matches else "drift"
        results.append({"id": source["id"], "title": source["title"],
                        "scope": source["scope"],
                        "authority": source["authority"], "url": source["url"],
                        "state": state, "locked": locked, "observed": current})
    return results


def command_list(args, registry):
    sources = select_sources(
        registry, args.id, args.topic, args.authority, args.scope)
    print(json.dumps(sources, ensure_ascii=False, indent=2))


def command_show(args, registry):
    sources = select_sources(registry, [args.source_id], None, None)
    print(json.dumps(sources[0], ensure_ascii=False, indent=2))


def command_audit(args, registry):
    sources = select_sources(
        registry, args.id, args.topic, args.authority, args.scope)
    results = audit(sources, load_lock(), workers=args.workers)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.fail_on_drift and any(
            result["state"] in {"drift", "unlocked", "error"}
            for result in results):
        raise SystemExit(1)


def update_lock(results: list[dict], path: Path = LOCK_PATH) -> None:
    with exclusive_lock(path):
        lock = load_lock(path)
        lock.pop("checked", None)
        lock.pop("checked_timezone", None)
        checked_at = datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
        for result in results:
            observed = result["observed"]
            lock["sources"][result["id"]] = {
                **{key: value for key, value in observed.items() if key != "ok"},
                "checked_at": checked_at,
            }
        lock["updated_at"] = checked_at
        atomic_write(
            path, json.dumps(lock, ensure_ascii=False, indent=2) + "\n")


def command_refresh_lock(args, registry):
    sources = select_sources(registry, args.id, None, None, None)
    results = audit(sources, {"schema": 1, "sources": {}}, workers=args.workers)
    failures = [result for result in results if result["state"] == "error"]
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
    update_lock(results)
    print(str(LOCK_PATH))


def command_resolve_git_ref(args):
    print(resolve_git_ref(args.url, args.ref))


def command_observe_network(args):
    try:
        result = observe_network_direct(args.kind, args.url, args.maximum)
    except Exception as exc:
        result = {"ok": False, "kind": args.kind,
                  "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False))


def audit_worker_count(value: str) -> int:
    workers = int(value)
    if not 1 <= workers <= MAX_AUDIT_WORKERS:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_AUDIT_WORKERS}")
    return workers


def network_maximum(value: str) -> int:
    maximum = int(value)
    if not 1 <= maximum <= MAX_HTTP_RESPONSE_BYTES:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_HTTP_RESPONSE_BYTES}")
    return maximum


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list matching sources")
    show_parser = subparsers.add_parser("show", help="show one source")
    show_parser.add_argument("source_id")
    audit_parser = subparsers.add_parser("audit", help="compare live sources to lock")
    audit_parser.add_argument("--fail-on-drift", action="store_true")
    refresh_parser = subparsers.add_parser(
        "refresh-lock", help="write current fingerprints after human review")
    resolve_parser = subparsers.add_parser(
        "resolve-git-ref", help="resolve one exact Git ref with bounded output")
    resolve_parser.add_argument("--url", required=True)
    resolve_parser.add_argument("--ref", required=True)
    for command in (list_parser, audit_parser):
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--topic")
        command.add_argument("--authority")
        scope = command.add_mutually_exclusive_group()
        scope.add_argument("--scope")
        scope.add_argument("--all-scopes", action="store_true")
    refresh_parser.add_argument("--id", action="append", required=True)
    for command in (audit_parser, refresh_parser):
        command.add_argument("--workers", type=audit_worker_count, default=8)
    return result


def main() -> None:
    if sys.argv[1:2] == ["_observe-network"]:
        internal = argparse.ArgumentParser(add_help=False)
        internal.add_argument("--kind", choices=("http", "mediawiki"),
                              required=True)
        internal.add_argument("--url", required=True)
        internal.add_argument(
            "--maximum", type=network_maximum, required=True)
        command_observe_network(internal.parse_args(sys.argv[2:]))
        return
    args = parser().parse_args()
    if args.command == "resolve-git-ref":
        command_resolve_git_ref(args)
        return
    registry = load_registry()
    if (args.command in {"list", "audit"} and not args.id
            and not args.scope and not args.all_scopes):
        raise SystemExit(
            "error: select --scope, --all-scopes, or at least one --id")
    if args.command == "list":
        command_list(args, registry)
    elif args.command == "show":
        command_show(args, registry)
    elif args.command == "audit":
        command_audit(args, registry)
    else:
        command_refresh_lock(args, registry)


if __name__ == "__main__":
    main()
