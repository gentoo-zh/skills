#!/usr/bin/env python3
"""Query and audit the skill's evidence registry without rewriting policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_ROOT / "references" / "sources.json"
LOCK_PATH = SKILL_ROOT / "references" / "source-lock.json"


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("sources"), list):
        raise ValueError("unsupported source registry schema")
    allowed = set(data.get("authorities", []))
    ids = set()
    for source in data["sources"]:
        required = {"id", "title", "authority", "kind", "url", "topics", "use"}
        if not required.issubset(source):
            raise ValueError(f"incomplete source entry: {source.get('id')}")
        if source["id"] in ids:
            raise ValueError(f"duplicate source id: {source['id']}")
        if source["authority"] not in allowed:
            raise ValueError(f"unknown authority: {source['authority']}")
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
                   authority: str | None) -> list[dict]:
    selected = registry["sources"]
    if ids:
        wanted = set(ids)
        selected = [source for source in selected if source["id"] in wanted]
        missing = wanted - {source["id"] for source in selected}
        if missing:
            raise ValueError(f"unknown source ids: {', '.join(sorted(missing))}")
    if topic:
        selected = [source for source in selected if topic in source["topics"]]
    if authority:
        selected = [source for source in selected
                    if source["authority"] == authority]
    return selected


def observe(source: dict) -> dict:
    try:
        if source["kind"] == "git":
            ref = source.get("ref", "HEAD")
            proc = subprocess.run(
                ["git", "ls-remote", source["url"], ref],
                capture_output=True, text=True, timeout=60)
            if proc.returncode != 0 or not proc.stdout.strip():
                raise RuntimeError(proc.stderr.strip() or f"ref not found: {ref}")
            revision = proc.stdout.split()[0]
            return {"ok": True, "kind": "git", "revision": revision}
        if source["kind"] == "mediawiki":
            request = urllib.request.Request(
                source["api_url"],
                headers={"User-Agent": "gentoo-zh-skills-source-audit/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read())
            pages = ((data.get("query") or {}).get("pages") or [])
            revisions = pages[0].get("revisions", []) if pages else []
            if not revisions or "revid" not in revisions[0]:
                raise RuntimeError("MediaWiki response has no revision id")
            return {"ok": True, "kind": "mediawiki",
                    "revision": str(revisions[0]["revid"])}
        request = urllib.request.Request(
            source["url"], headers={"User-Agent": "gentoo-zh-skills-source-audit/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
            return {
                "ok": True,
                "kind": "http",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "final_url": response.geturl(),
            }
    except Exception as exc:
        return {"ok": False, "kind": source["kind"],
                "error": f"{type(exc).__name__}: {exc}"}


def audit(sources: list[dict], lock: dict, workers: int = 8) -> list[dict]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
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
            state = "current" if locked.get(key) == current.get(key) else "drift"
        results.append({"id": source["id"], "title": source["title"],
                        "authority": source["authority"], "url": source["url"],
                        "state": state, "locked": locked, "observed": current})
    return results


def command_list(args, registry):
    sources = select_sources(registry, args.id, args.topic, args.authority)
    print(json.dumps(sources, ensure_ascii=False, indent=2))


def command_show(args, registry):
    sources = select_sources(registry, [args.source_id], None, None)
    print(json.dumps(sources[0], ensure_ascii=False, indent=2))


def command_audit(args, registry):
    sources = select_sources(registry, args.id, args.topic, args.authority)
    results = audit(sources, load_lock(), workers=args.workers)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.fail_on_drift and any(
            result["state"] in {"drift", "unlocked", "error"}
            for result in results):
        raise SystemExit(1)


def command_refresh_lock(args, registry):
    sources = select_sources(registry, args.id, args.topic, args.authority)
    results = audit(sources, {"schema": 1, "sources": {}}, workers=args.workers)
    failures = [result for result in results if result["state"] == "error"]
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
    lock = load_lock()
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
    LOCK_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(str(LOCK_PATH))


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

    for command in (list_parser, audit_parser, refresh_parser):
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--topic")
        command.add_argument("--authority")
    for command in (audit_parser, refresh_parser):
        command.add_argument("--workers", type=int, default=8)
    return result


def main() -> None:
    args = parser().parse_args()
    registry = load_registry()
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
