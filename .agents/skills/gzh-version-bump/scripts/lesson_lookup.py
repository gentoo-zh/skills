#!/usr/bin/env python3
"""Query gentoo-tree-lessons as secondary evidence with full commit provenance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


REPOSITORY = "https://github.com/Zakkaus/gentoo-tree-lessons.git"


def default_repo() -> Path:
    configured = os.environ.get("GZH_LESSONS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache).expanduser() if cache else Path.home() / ".cache"
    return base / "gentoo-zh-skills" / "gentoo-tree-lessons"


def run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *arguments],
                          capture_output=True, text=True)


def refresh(repo: Path) -> None:
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "clone", "--depth", "1", REPOSITORY,
                               str(repo)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())
        return
    if not (repo / ".git").is_dir():
        raise RuntimeError(f"not a gentoo-tree-lessons checkout: {repo}")
    status = run_git(repo, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("lesson checkout is dirty; refusing to update")
    branch = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode != 0:
        raise RuntimeError("lesson checkout is detached; refusing to update")
    pull = run_git(repo, "pull", "--ff-only")
    if pull.returncode != 0:
        raise RuntimeError(pull.stderr.strip())


def require_repo(repo: Path) -> None:
    if not (repo / "data" / "lessons.jsonl").is_file():
        raise RuntimeError(
            f"lesson data not found at {repo}; set GZH_LESSONS_DIR or use --refresh")


def stats(repo: Path) -> dict:
    provenance = json.loads((repo / "data" / "PROVENANCE.json").read_text())
    lessons = sum(1 for line in (repo / "data" / "lessons.jsonl").open()
                  if line.strip())
    topics = sorted(path.stem for path in (repo / "docs").glob("*.md")
                    if path.name != "MINING.md")
    return {"repo": str(repo), "corpus": provenance.get("corpus"),
            "corpus_head": provenance.get("corpus_head"),
            "classified": provenance.get("classified"), "lessons": lessons,
            "topics": topics}


def search(repo: Path, query: str, limit: int) -> list[dict]:
    query = query.casefold()
    matches = []
    with (repo / "data" / "lessons.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if query not in line.casefold():
                continue
            item = json.loads(line)
            sha = item.get("sha", "")
            if len(sha) == 40:
                item["commit_url"] = f"https://github.com/gentoo/gentoo/commit/{sha}"
            matches.append(item)
            if len(matches) >= limit:
                break
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=default_repo())
    parser.add_argument("--refresh", action="store_true")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--stats", action="store_true")
    action.add_argument("--search")
    action.add_argument("--topic")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    if args.refresh:
        refresh(repo)
    require_repo(repo)
    if args.stats:
        output = stats(repo)
    elif args.search:
        output = search(repo, args.search, max(1, args.limit))
    else:
        path = repo / "docs" / f"{args.topic}.md"
        if not path.is_file():
            raise SystemExit(f"unknown lesson topic: {args.topic}")
        output = {"topic": args.topic, "path": str(path),
                  "authority": "derived-corpus"}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
