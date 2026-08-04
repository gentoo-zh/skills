from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

_TITLE_RE = re.compile(r'^\[nvchecker\]\s+(\S+)\s+can be bump to\s+(\S+)$')
_OLDVER_RE = re.compile(r'oldver:\s*(\S+)', re.IGNORECASE)
_CC_RE = re.compile(r'CC:\s*@?(\S+)', re.IGNORECASE)
_REPO_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
_REMOTE_RE = re.compile(r"[A-Za-z0-9_.-]+")
_OID_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_AUTOBUMP_SELECTORS = frozenset({"any", "off", "on", "manual-required"})
_ISSUE_MODES = frozenset({"include", "exact"})
_CONFIG_PATH = ".github/workflows/overlay.toml"
_STATUS_MARKER = "<!-- autobump-status -->"
_STATUS_AUTHOR = "gentoo-zh-autobump[bot]"
_STATE_MAP = {"open": "OPEN", "closed": "CLOSED", "all": None}


def split_repo(repo: str) -> tuple[str, str]:
    if not isinstance(repo, str):
        raise ValueError(f"invalid repository: {repo!r} (expect owner/name)")
    owner, separator, name = repo.partition("/")
    if (not separator or not name or "/" in name
            or not _REPO_PART_RE.fullmatch(owner)
            or not _REPO_PART_RE.fullmatch(name)):
        raise ValueError(f"invalid repository: {repo!r} (expect owner/name)")
    return owner, name


def parse_title(title: str) -> tuple[str, str] | None:
    m = _TITLE_RE.match((title or "").strip())
    return (m.group(1), m.group(2)) if m else None


def parse_body(body: str) -> dict:
    body = body or ""
    m_old = _OLDVER_RE.search(body)
    m_cc = _CC_RE.search(body)
    return {"oldver": m_old.group(1) if m_old else None,
            "maintainer": m_cc.group(1) if m_cc else None}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _comments_complete(comments: dict, nodes: list[dict]) -> tuple[bool, str | None]:
    reason = comments.get("incomplete_reason")
    if reason:
        return False, str(reason)
    total = comments.get("totalCount")
    page_info = comments.get("pageInfo")
    if not isinstance(total, int) or total < 0 or not isinstance(page_info, dict):
        return False, "comment-pagination-metadata-missing"
    if page_info.get("hasNextPage"):
        return False, "comment-pagination-incomplete"
    if total != len(nodes):
        return False, "comment-count-mismatch"
    ids = [node.get("id") for node in nodes]
    if any(not isinstance(comment_id, str) or not comment_id for comment_id in ids):
        return False, "stable-comment-id-missing"
    if len(ids) != len(set(ids)):
        return False, "duplicate-comment-id"
    return True, None


def graphql_to_queue(nodes: list, with_comments: bool = True) -> tuple[list[dict], int]:
    queue: list[dict] = []
    skipped = 0
    for node in nodes or []:
        parsed = parse_title(node.get("title", ""))
        if parsed is None:
            skipped += 1
            continue
        cat_pkg, target = parsed
        raw_body = node.get("body", "") or ""
        body = parse_body(raw_body)
        item = {
            "issue": node.get("number"),
            "cat_pkg": cat_pkg,
            "target_version": target,
            "oldver": body["oldver"],
            "maintainer": body["maintainer"],
            "title": node.get("title", ""),
            "body": raw_body,
            "url": node.get("url"),
            "state": (node.get("state") or "").lower() or None,
            "author": (node.get("author") or {}).get("login"),
            "updated_at": node.get("updatedAt"),
            "issue_revision": node.get("updatedAt"),
            "labels": [
                label.get("name") for label in
                ((node.get("labels") or {}).get("nodes") or [])
                if isinstance(label, dict) and isinstance(label.get("name"), str)
            ],
        }
        if with_comments:
            comments = node.get("comments") or {}
            comment_nodes = comments.get("nodes") or []
            item["comments"] = [
                {"id": comment.get("id"),
                 "url": comment.get("url"),
                 "author": (comment.get("author") or {}).get("login"),
                 "body": comment.get("body"),
                 "created_at": comment.get("createdAt"),
                 "updated_at": comment.get("updatedAt")}
                for comment in comment_nodes
            ]
            total = comments.get("totalCount", len(comment_nodes))
            item["comments_truncated"] = (
                isinstance(total, int) and total > len(comment_nodes))
            complete, reason = _comments_complete(comments, comment_nodes)
            item["comments_complete"] = complete
            item["comments_incomplete_reason"] = reason
        else:
            item["comments"] = []
            item["comments_truncated"] = False
            item["comments_complete"] = False
            item["comments_incomplete_reason"] = "comments-not-requested"
        queue.append(item)
    return queue, skipped


def apply_filters(queue: list, maintainer: str | None = None,
                  pkg: str | None = None) -> list[dict]:
    out = queue
    if maintainer:
        out = [item for item in out if item.get("maintainer") == maintainer]
    if pkg:
        out = [item for item in out if item.get("cat_pkg") == pkg]
    return out


def _comment_fields() -> str:
    return "id url author{login} body createdAt updatedAt"


def _issue_fields(with_comments: bool) -> str:
    comments = (
        " comments(first:100){totalCount pageInfo{hasNextPage endCursor} "
        f"nodes{{{_comment_fields()}}}}}" if with_comments else "")
    return ("number title body state url updatedAt author{login} "
            "labels(first:100){totalCount pageInfo{hasNextPage endCursor} "
            "nodes{name}}"
            f"{comments}")


def build_query(owner: str, name: str, state: str | None,
                limit: int, with_comments: bool,
                after: str | None = None) -> str:
    args = ['labels:["nvchecker"]', f"first:{min(int(limit), 100)}"]
    if state:
        args.append(f"states:[{state}]")
    if after:
        args.append(f"after:{json.dumps(after)}")
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issues({', '.join(args)}) {{\n"
        "      totalCount\n"
        "      pageInfo { hasNextPage endCursor }\n"
        f"      nodes {{ {_issue_fields(with_comments)} }}\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def build_issue_query(owner: str, name: str, issue: int,
                      with_comments: bool) -> str:
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issue(number:{int(issue)}) {{ {_issue_fields(with_comments)} }}\n"
        "  }\n"
        "}\n"
    )


def build_comments_query(owner: str, name: str, issue: int,
                         after: str) -> str:
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issue(number:{issue}) {{\n"
        "      updatedAt\n"
        f"      comments(first:100,after:{json.dumps(after)}) {{\n"
        "        totalCount pageInfo { hasNextPage endCursor }\n"
        f"        nodes {{ {_comment_fields()} }}\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _check_gh_auth(runner) -> bool:
    try:
        return runner(["gh", "auth", "status"],
                      capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False


def _graphql(query: str, runner) -> tuple[dict | None, dict | None]:
    proc = runner(["gh", "api", "graphql", "-f", f"query={query}"],
                  capture_output=True, text=True)
    if proc.returncode != 0:
        return None, {"ok": False, "exit_code": 1,
                      "error": "gh graphql call failed", "stderr": proc.stderr}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None, {"ok": False, "exit_code": 1,
                      "error": "invalid JSON from gh", "stdout": proc.stdout}
    if data.get("errors"):
        return None, {"ok": False, "exit_code": 1,
                      "error": str(data["errors"])}
    return data, None


def get_issue_updated_at(repo: str, issue: int, runner=None) -> str:
    """Read the current GitHub issue revision immediately before triage writes."""
    if runner is None:
        runner = subprocess.run
    owner, name = split_repo(repo)
    if int(issue) < 1:
        raise ValueError(f"invalid issue number: {issue}")
    proc = runner(
        ["gh", "api", f"repos/{owner}/{name}/issues/{int(issue)}",
         "--jq", ".updated_at"],
        capture_output=True, text=True)
    updated_at = (proc.stdout or "").strip()
    if proc.returncode != 0 or not updated_at:
        raise RuntimeError(
            proc.stderr.strip() or f"cannot read current revision for issue {issue}")
    return updated_at


def _fetch_remaining_comments(nodes: list[dict], owner: str, name: str,
                              runner) -> dict | None:
    for node in nodes:
        comments = node.get("comments") or {}
        page_info = comments.get("pageInfo") or {}
        original_total = comments.get("totalCount")
        original_revision = node.get("updatedAt")
        seen_cursors = set()
        while page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
            if not cursor or cursor in seen_cursors:
                return {"ok": False, "exit_code": 1,
                        "error": f"invalid comment cursor for issue {node.get('number')}"}
            seen_cursors.add(cursor)
            query = build_comments_query(owner, name, node["number"], cursor)
            data, error = _graphql(query, runner)
            if error:
                return error
            issue = (((data.get("data") or {}).get("repository") or {})
                     .get("issue"))
            if not isinstance(issue, dict):
                return {"ok": False, "exit_code": 1,
                        "error": f"missing comment page for issue {node.get('number')}"}
            page = issue.get("comments") or {}
            page_total = page.get("totalCount")
            if (isinstance(original_total, int) and isinstance(page_total, int)
                    and page_total != original_total):
                comments["incomplete_reason"] = "comment-count-changed-during-fetch"
            page_revision = issue.get("updatedAt")
            if (original_revision and page_revision
                    and page_revision != original_revision):
                comments["incomplete_reason"] = "issue-revision-changed-during-fetch"
            comments.setdefault("nodes", []).extend(page.get("nodes") or [])
            comments["totalCount"] = page.get(
                "totalCount", comments.get("totalCount", len(comments["nodes"])))
            page_info = page.get("pageInfo") or {}
            comments["pageInfo"] = page_info
    return None


def _run_text(args: list[str], runner, failure: str) -> str:
    try:
        proc = runner(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(failure) from exc
    value = proc.stdout or ""
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise RuntimeError(f"{failure}: {detail}" if detail else failure)
    return value


def _run_bytes(args: list[str], runner, failure: str) -> bytes:
    try:
        proc = runner(args, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError(failure) from exc
    value = proc.stdout or b""
    if proc.returncode != 0:
        detail = proc.stderr or b""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        detail = detail.strip()
        raise RuntimeError(f"{failure}: {detail}" if detail else failure)
    return value.encode("utf-8") if isinstance(value, str) else value


def load_canonical_config(remote_name: str, config_path: str = _CONFIG_PATH,
                          runner=None, cwd: Path | None = None,
                          expected_repository: str | None = None) -> dict:
    """Fetch and read repository configuration from one explicit remote ref."""
    if runner is None:
        runner = subprocess.run
    if (not isinstance(remote_name, str) or not remote_name
            or not _REMOTE_RE.fullmatch(remote_name)):
        raise ValueError(f"invalid canonical remote name: {remote_name!r}")
    remote_url = _run_text(
        ["git", "-C", str(cwd), "remote", "get-url", remote_name]
        if cwd is not None else ["git", "remote", "get-url", remote_name], runner,
        f"cannot resolve canonical remote {remote_name!r}").strip()
    if not remote_url:
        raise RuntimeError(f"canonical remote {remote_name!r} has no URL")
    if expected_repository is not None:
        remote_repo = _remote_repo(remote_url)
        if (remote_repo is None
                or remote_repo.casefold() != expected_repository.casefold()):
            raise RuntimeError(
                f"canonical remote URL {remote_url!r} does not match "
                f"{expected_repository!r}")
    refspec = f"+refs/heads/master:refs/remotes/{remote_name}/master"
    fetch_command = ["git", "fetch", "--quiet", remote_name, refspec]
    if cwd is not None:
        fetch_command[1:1] = ["-C", str(cwd)]
    _run_text(fetch_command, runner,
              f"cannot fetch canonical remote {remote_name!r}")
    ref = f"refs/remotes/{remote_name}/master^{{commit}}"
    revision_command = ["git", "rev-parse", "--verify", ref]
    if cwd is not None:
        revision_command[1:1] = ["-C", str(cwd)]
    base_oid = _run_text(
        revision_command, runner,
        f"cannot resolve fetched {remote_name}/master").strip()
    if not _OID_RE.fullmatch(base_oid):
        raise RuntimeError(f"invalid canonical base OID: {base_oid!r}")
    show_command = ["git", "show", f"{base_oid}:{config_path}"]
    if cwd is not None:
        show_command[1:1] = ["-C", str(cwd)]
    content = _run_bytes(
        show_command, runner,
        f"cannot read {config_path} at {base_oid}")
    return {"remote_name": remote_name, "remote_url": remote_url,
            "base_oid": base_oid.lower(), "config_path": config_path,
            "content": content, "fetched": True}


def _remote_repo(remote_url: str) -> str | None:
    patterns = (
        r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?$",
        r"(?:ssh|https?|git)://(?:git@)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote_url)
        if match:
            return match.group(1)
    return None


def _json_safe(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def _canonical_evidence(repo: str, remote_name: str | None,
                        config_loader, runner) -> tuple[dict, dict | None]:
    if remote_name is None:
        return ({"remote_name": None, "remote_url": None, "base_oid": None,
                 "fetch_status": "not-requested",
                 "config": {"path": _CONFIG_PATH, "sha256": None}}, None)
    if config_loader is None:
        loaded = load_canonical_config(
            remote_name, _CONFIG_PATH, runner,
            expected_repository=repo)
    else:
        loaded = config_loader(remote_name, _CONFIG_PATH, runner)
    if not isinstance(loaded, dict):
        raise ValueError("canonical config loader returned invalid evidence")
    actual_name = loaded.get("remote_name")
    remote_url = loaded.get("remote_url")
    base_oid = loaded.get("base_oid")
    content = loaded.get("content")
    path = loaded.get("config_path")
    if (actual_name != remote_name or loaded.get("fetched") is not True
            or path != _CONFIG_PATH or not isinstance(remote_url, str)
            or not isinstance(base_oid, str) or not _OID_RE.fullmatch(base_oid)
            or not isinstance(content, (str, bytes))):
        raise ValueError("canonical config evidence is incomplete")
    remote_repo = _remote_repo(remote_url)
    if remote_repo is None or remote_repo.casefold() != repo.casefold():
        raise ValueError(
            f"canonical remote URL {remote_url!r} does not match {repo!r}")
    try:
        if isinstance(content, bytes):
            raw_content = content
            content = content.decode("utf-8")
        else:
            raw_content = content.encode("utf-8")
        parsed = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid canonical TOML at {base_oid}: {exc}") from exc
    digest = _sha256_bytes(raw_content)
    evidence = {
        "remote_name": remote_name,
        "remote_url": remote_url,
        "base_oid": base_oid.lower(),
        "fetch_status": "fetched",
        "config": {"path": path, "sha256": digest},
    }
    return evidence, parsed


def _config_entry_evidence(config: dict | None, cat_pkg: str,
                           canonical: dict) -> dict:
    config_meta = canonical["config"]
    base = {"path": config_meta["path"], "sha256": config_meta["sha256"],
            "entry": None, "entry_present": False,
            "autobump_present": False, "autobump": None}
    if config is None:
        return {**base, "state": "unknown", "complete": False,
                "reason": "canonical-config-not-loaded"}
    entry = config.get(cat_pkg)
    if not isinstance(entry, dict):
        return {**base, "state": "unknown", "complete": False,
                "reason": "package-entry-missing"}
    base.update({"entry": _json_safe(entry), "entry_present": True})
    if "autobump" not in entry:
        return {**base, "state": "off", "complete": True,
                "reason": "autobump-key-absent"}
    value = entry["autobump"]
    base.update({"autobump_present": True, "autobump": _json_safe(value)})
    if type(value) is bool:
        return {**base, "state": "on" if value else "off", "complete": True,
                "reason": "autobump-true" if value else "autobump-false"}
    return {**base, "state": "unknown", "complete": False,
            "reason": "autobump-value-is-not-boolean"}


def _run_link_pattern(repo: str, label: str = "(?:run|log)") -> str:
    return (rf"(?: \u00b7 \[{label}\]\(https://github\.com/"
            rf"{re.escape(repo)}/actions/runs/[0-9]+\))?")


def _protocol_status(body: str, cat_pkg: str, version: str,
                     repo: str) -> str | None:
    package = re.escape(cat_pkg)
    target = re.escape(version)
    run_link = _run_link_pattern(repo)
    log_link = _run_link_pattern(repo, "log")
    footer = (r"\n\u2014 `autobump` enabled"
              r"(?: \u00b7 keep_old=(?:all|[1-9][0-9]*))?"
              rf"\n\n{re.escape(_STATUS_MARKER)}")
    details = (r"(?:\n\n<details><summary>evidence</summary>\n\n```\n"
               r"[\s\S]*?\n```\n</details>)?")
    manual_patterns = (
        (rf"\*\*autobump\*\* can't bump `{package}` \u2192 `{target}` "
         rf"mechanically: \*\*[^\r\n]+\*\*\. Needs a manual bump\.{log_link}"
         rf"{details}{footer}"),
        (rf"\*\*autobump\*\* accepted the surface delta for `{package}` "
         rf"\u2192 `{target}` but the retry hit transient failures [1-9][0-9]* "
         rf"times\. A maintainer may need to bump it by hand\.{log_link}{footer}"),
        (rf"\*\*autobump\*\* gave up on `{package}` \u2192 `{target}` after "
         rf"[1-9][0-9]* tries: [^\r\n]+\. A maintainer may need to bump it by "
         rf"hand\.{log_link}{footer}"),
    )
    if any(re.fullmatch(pattern, body) for pattern in manual_patterns):
        return "manual-required"
    nonmanual_patterns = (
        (rf"\*\*autobump\*\* is bumping `{package}` \u2192 `{target}`\u2026"
         rf"{run_link}{footer}"),
        (rf"\*\*autobump\*\* deferred `{package}` \u2192 `{target}` "
         rf"\(transient: [^\r\n]+\)\. Will retry automatically\.{run_link}{footer}"),
        (rf"\*\*autobump\*\*: `{package}` is already at \(or ahead of\) "
         rf"`{target}` in the overlay \u2014 nothing to bump\.{run_link}{footer}"),
    )
    if any(re.fullmatch(pattern, body) for pattern in nonmanual_patterns):
        return "not-manual-required"
    return None


def _status_evidence(item: dict, repo: str) -> dict:
    issue_revision = item.get("issue_revision")
    evidence = {"state": "unknown", "complete": False,
                "reason": None, "issue_revision": issue_revision,
                "comment_id": None, "comment_url": None,
                "revision": None, "body_sha256": None,
                "marker": _STATUS_MARKER, "expected_author": _STATUS_AUTHOR,
                "candidates": []}
    if not item.get("comments_complete"):
        evidence["reason"] = (item.get("comments_incomplete_reason")
                              or "comment-pagination-incomplete")
        return evidence
    marked = []
    for comment in item.get("comments") or []:
        body = comment.get("body") or ""
        if _STATUS_MARKER in body:
            candidate = {
                "id": comment.get("id"), "url": comment.get("url"),
                "author": comment.get("author"),
                "revision": comment.get("updated_at"),
                "body_sha256": _sha256(body),
            }
            evidence["candidates"].append(candidate)
            marked.append(comment)
    if not marked:
        evidence.update({"state": "not-manual-required", "complete": True,
                         "reason": "status-marker-absent"})
        return evidence
    if len(marked) != 1:
        evidence["reason"] = "ambiguous-status-comments"
        return evidence
    comment = marked[0]
    body = comment.get("body") or ""
    evidence.update({
        "comment_id": comment.get("id"),
        "comment_url": comment.get("url"),
        "revision": comment.get("updated_at"),
        "body_sha256": _sha256(body),
    })
    if comment.get("author") != _STATUS_AUTHOR:
        evidence["reason"] = "status-author-mismatch"
        return evidence
    if (body.count(_STATUS_MARKER) != 1
            or not body.endswith(f"\n\n{_STATUS_MARKER}")):
        evidence["reason"] = "status-marker-not-exact"
        return evidence
    required = (comment.get("id"), comment.get("url"),
                comment.get("updated_at"), issue_revision)
    if not all(isinstance(value, str) and value for value in required):
        evidence["reason"] = "status-revision-evidence-incomplete"
        return evidence
    expected_url = f"https://github.com/{repo}/issues/{item['issue']}#issuecomment-"
    if not comment["url"].startswith(expected_url):
        evidence["reason"] = "status-url-mismatch"
        return evidence
    if comment["updated_at"] != issue_revision:
        evidence["reason"] = "status-revision-stale"
        return evidence
    state = _protocol_status(
        body, item["cat_pkg"], item["target_version"], repo)
    if state is None:
        evidence["reason"] = "status-body-not-current-protocol"
        return evidence
    evidence.update({"state": state, "complete": True,
                     "reason": "current-repository-status"})
    return evidence


def _normalise_issues(issues) -> tuple[list[int] | None, str | None]:
    if issues is None:
        return [], None
    if isinstance(issues, (str, bytes)):
        return None, "explicit issues must be a repeatable sequence"
    normalised = []
    seen = set()
    try:
        values = list(issues)
    except TypeError:
        return None, "explicit issues must be a repeatable sequence"
    for value in values:
        if isinstance(value, bool):
            return None, f"invalid explicit issue number: {value!r}"
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None, f"invalid explicit issue number: {value!r}"
        if str(number) != str(value).strip() or number < 1:
            return None, f"invalid explicit issue number: {value!r}"
        if number not in seen:
            seen.add(number)
            normalised.append(number)
    return normalised, None


def _fetch_explicit_issue(owner: str, name: str, issue_number: int,
                          with_comments: bool, runner) -> tuple[dict | None, dict | None]:
    data, error = _graphql(
        build_issue_query(owner, name, issue_number, with_comments), runner)
    if error:
        return None, error
    issue = (((data.get("data") or {}).get("repository") or {}).get("issue"))
    if not isinstance(issue, dict):
        return None, {"ok": False, "exit_code": 1,
                      "error": f"explicit issue #{issue_number} was not found"}
    if issue.get("number") != issue_number:
        return None, {"ok": False, "exit_code": 1,
                      "error": f"explicit issue #{issue_number} response mismatched"}
    labels = issue.get("labels")
    label_nodes = labels.get("nodes") if isinstance(labels, dict) else None
    label_page = labels.get("pageInfo") if isinstance(labels, dict) else None
    label_total = labels.get("totalCount") if isinstance(labels, dict) else None
    if (not isinstance(label_nodes, list) or not isinstance(label_page, dict)
            or not isinstance(label_total, int) or label_total < 0
            or label_page.get("hasNextPage") is not False
            or label_total != len(label_nodes)):
        return None, {"ok": False, "exit_code": 1,
                      "error": (f"explicit issue #{issue_number} label evidence "
                                "is incomplete")}
    label_names = [
        label.get("name") for label in label_nodes
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    ]
    if len(label_names) != len(label_nodes) or len(label_names) != len(set(label_names)):
        return None, {"ok": False, "exit_code": 1,
                      "error": (f"explicit issue #{issue_number} label evidence "
                                "is invalid")}
    if "nvchecker" not in label_names:
        return None, {"ok": False, "exit_code": 1,
                      "error": (f"explicit issue #{issue_number} does not have "
                                "the nvchecker label")}
    return issue, None


def _selection(item: dict, selector: str, explicit: set[int]) -> dict:
    config_state = item["config_evidence"]["state"]
    status_state = item["status_evidence"]["state"]
    if selector == "any":
        selector_result = "match"
        selector_reason = "selector:any"
    elif selector in {"on", "off"}:
        selector_result = ("unknown" if config_state == "unknown" else
                           "match" if config_state == selector else "no-match")
        selector_reason = f"selector:autobump-{selector}" if selector_result == "match" else None
    else:
        selector_result = ("unknown" if status_state == "unknown" else
                           "match" if status_state == "manual-required" else "no-match")
        selector_reason = "selector:manual-required" if selector_result == "match" else None
    reasons = []
    if item["issue"] in explicit:
        reasons.append("explicit-issue")
    if selector_reason:
        reasons.append(selector_reason)
    selected = bool(reasons)
    if selected:
        reason = "explicit-issue" if item["issue"] in explicit else selector_reason
    elif selector_result == "unknown":
        reason = "selector-evidence-unknown"
    else:
        reason = "selector-no-match"
    return {"selected": selected, "selection_reason": reason,
            "selection_reasons": reasons, "selector_result": selector_result}


def run_bump_issues(repo: str = "gentoo-zh/overlay", state: str = "open",
                    maintainer: str | None = None, pkg: str | None = None,
                    with_comments: bool = True, limit: int = 100,
                    runner=None, *, autobump: str = "any", issues=None,
                    issue_mode: str = "include",
                    canonical_remote: str | None = None,
                    canonical_loader=None) -> dict:
    if runner is None:
        runner = subprocess.run
    try:
        owner, name = split_repo(repo)
    except ValueError:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid --repo: {repo!r} (expect owner/name)"}
    if not isinstance(state, str) or state not in _STATE_MAP:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid issue state: {state!r}"}
    if not isinstance(autobump, str) or autobump not in _AUTOBUMP_SELECTORS:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid autobump selector: {autobump!r}"}
    if not isinstance(issue_mode, str) or issue_mode not in _ISSUE_MODES:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid issue selection mode: {issue_mode!r}"}
    original_limit = limit
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 0
    if (isinstance(original_limit, bool)
            or str(limit) != str(original_limit).strip()
            or limit < 1 or limit > 1000):
        return {"ok": False, "exit_code": 1,
                "error": "limit must be between 1 and 1000"}
    explicit_issues, issue_error = _normalise_issues(issues)
    if issue_error:
        return {"ok": False, "exit_code": 1, "error": issue_error}
    if issue_mode == "exact" and not explicit_issues:
        return {"ok": False, "exit_code": 1,
                "error": "exact issue selection requires at least one --issue"}
    if (autobump == "manual-required" or explicit_issues) and not with_comments:
        return {"ok": False, "exit_code": 1,
                "error": "manual and explicit issue selection require complete comments"}
    if autobump != "any" and not canonical_remote:
        return {"ok": False, "exit_code": 1,
                "error": "non-any autobump selectors require an explicit canonical remote"}
    if (canonical_remote is not None
            and (not isinstance(canonical_remote, str)
                 or not _REMOTE_RE.fullmatch(canonical_remote))):
        return {"ok": False, "exit_code": 1,
                "error": f"invalid canonical remote name: {canonical_remote!r}"}
    try:
        canonical, config = _canonical_evidence(
            repo, canonical_remote, canonical_loader, runner)
    except (RuntimeError, TypeError, ValueError) as exc:
        return {"ok": False, "exit_code": 1,
                "error": f"canonical evidence unavailable: {exc}"}
    if not _check_gh_auth(runner):
        return {"ok": False, "exit_code": 2,
                "error": "gh not authenticated (or not installed); run `gh auth login` first"}

    gstate = _STATE_MAP[state]
    nodes = []
    issue_numbers = set()
    cursor = None
    seen_cursors = set()
    reported_total = None
    more_available = False
    while issue_mode == "include" and len(nodes) < limit:
        requested = min(100, limit - len(nodes))
        query = build_query(owner, name, gstate, requested,
                            with_comments, after=cursor)
        data, error = _graphql(query, runner)
        if error:
            return error
        issues_page = (((data.get("data") or {}).get("repository") or {})
                       .get("issues") or {})
        page_nodes = issues_page.get("nodes") or []
        if not isinstance(page_nodes, list):
            return {"ok": False, "exit_code": 1,
                    "error": "invalid issue page nodes"}
        if len(page_nodes) > requested:
            return {"ok": False, "exit_code": 1,
                    "error": "issue page exceeded requested size"}
        page_total = issues_page.get("totalCount")
        if not isinstance(page_total, int) or page_total < 0:
            return {"ok": False, "exit_code": 1,
                    "error": "invalid issue totalCount"}
        if reported_total is not None and page_total != reported_total:
            return {"ok": False, "exit_code": 1,
                    "error": "issue totalCount changed during pagination"}
        reported_total = page_total
        for node in page_nodes:
            number = node.get("number") if isinstance(node, dict) else None
            if not isinstance(number, int) or number < 1 or number in issue_numbers:
                return {"ok": False, "exit_code": 1,
                        "error": "invalid or duplicate issue in pagination"}
            issue_numbers.add(number)
            nodes.append(node)
        if page_total < len(nodes):
            return {"ok": False, "exit_code": 1,
                    "error": "issue totalCount is smaller than fetched queue"}
        page_info = issues_page.get("pageInfo") or {}
        more_available = bool(page_info.get("hasNextPage"))
        if not more_available:
            break
        if not page_nodes:
            return {"ok": False, "exit_code": 1,
                    "error": "empty issue page before pagination completed"}
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen_cursors:
            return {"ok": False, "exit_code": 1,
                    "error": "invalid issue pagination cursor"}
        seen_cursors.add(cursor)

    queue_fetched_count = len(nodes)
    for issue_number in explicit_issues:
        if issue_number in issue_numbers:
            continue
        node, error = _fetch_explicit_issue(
            owner, name, issue_number, with_comments, runner)
        if error:
            return error
        issue_numbers.add(issue_number)
        nodes.append(node)
    if with_comments:
        error = _fetch_remaining_comments(nodes, owner, name, runner)
        if error:
            return error

    queue, skipped = graphql_to_queue(nodes, with_comments=with_comments)
    parsed_numbers = {item["issue"] for item in queue}
    missing_explicit = [number for number in explicit_issues
                        if number not in parsed_numbers]
    if missing_explicit:
        return {"ok": False, "exit_code": 1,
                "error": ("explicit issue is not an nvchecker bump reminder: "
                          + ", ".join(f"#{number}" for number in missing_explicit))}
    incomplete_explicit = [item["issue"] for item in queue
                           if item["issue"] in set(explicit_issues)
                           and not item["comments_complete"]]
    if incomplete_explicit:
        return {"ok": False, "exit_code": 1,
                "error": ("explicit issue comments are incomplete: "
                          + ", ".join(f"#{number}" for number
                                      in incomplete_explicit))}
    explicit_set = set(explicit_issues)
    filtered_numbers = {item["issue"] for item in apply_filters(
        queue, maintainer=maintainer, pkg=pkg)}
    candidates = [
        item for item in queue
        if (item["issue"] in explicit_set if issue_mode == "exact" else
            item["issue"] in filtered_numbers or item["issue"] in explicit_set)
    ]
    results = []
    selected = []
    for item in candidates:
        item["selector"] = autobump
        item["config_evidence"] = _config_entry_evidence(
            config, item["cat_pkg"], canonical)
        item["status_evidence"] = _status_evidence(item, repo)
        item["selection"] = _selection(item, autobump, explicit_set)
        item["selection_reason"] = item["selection"]["selection_reason"]
        if item["selection"]["selected"]:
            results.append(item)
            selected.append({
                "issue": item["issue"], "cat_pkg": item["cat_pkg"],
                "selection_reason": item["selection_reason"],
                "selection_reasons": item["selection"]["selection_reasons"],
            })
    fetched_count = len(nodes)
    total_count = reported_total if reported_total is not None else 0
    selection_expression = {
        "issue_mode": issue_mode,
        "composition": ("explicit_only" if issue_mode == "exact"
                        else "filtered_queue_or_explicit"),
        "queue": {
            "evaluated": issue_mode == "include",
            "repository": repo,
            "label": "nvchecker",
            "state": state,
            "limit": limit,
            "maintainer": maintainer,
            "package": pkg,
            "autobump": autobump,
        },
        "explicit_issues": explicit_issues,
    }
    return {
        "schema_version": 2,
        "ok": True,
        "selector": autobump,
        "issue_mode": issue_mode,
        "explicit_issues": explicit_issues,
        "selection_expression": selection_expression,
        "resulting_issues": [item["issue"] for item in results],
        "canonical": canonical,
        "results": results,
        "candidates": candidates,
        "selected": selected,
        "skipped": skipped,
        "total_count": total_count,
        "fetched_count": fetched_count,
        "selected_count": len(results),
        "truncated": more_available or total_count > queue_fetched_count,
        "exit_code": 0,
    }


def reconstruct_selected_results(snapshot: dict) -> list[dict]:
    """Verify a versioned snapshot and return its stored selected results."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 2:
        raise ValueError("unsupported bump issue snapshot schema")
    selector = snapshot.get("selector")
    if selector not in _AUTOBUMP_SELECTORS:
        raise ValueError("snapshot has an invalid selector")
    issue_mode = snapshot.get("issue_mode")
    if issue_mode not in _ISSUE_MODES:
        raise ValueError("snapshot has an invalid issue selection mode")
    expression = snapshot.get("selection_expression")
    explicit_issues = snapshot.get("explicit_issues")
    queue_expression = expression.get("queue") if isinstance(expression, dict) else None
    if (not isinstance(expression, dict)
            or expression.get("issue_mode") != issue_mode
            or expression.get("composition") != (
                "explicit_only" if issue_mode == "exact"
                else "filtered_queue_or_explicit")
            or not isinstance(queue_expression, dict)
            or expression.get("explicit_issues") != explicit_issues):
        raise ValueError("snapshot selection expression is incomplete")
    normalised_explicit, explicit_error = _normalise_issues(explicit_issues)
    if explicit_error or normalised_explicit != explicit_issues:
        raise ValueError("snapshot explicit issue set is invalid")
    try:
        split_repo(queue_expression.get("repository"))
    except ValueError as exc:
        raise ValueError("snapshot queue repository is invalid") from exc
    if (queue_expression.get("label") != "nvchecker"
            or queue_expression.get("evaluated") != (issue_mode == "include")
            or queue_expression.get("state") not in _STATE_MAP
            or queue_expression.get("autobump") != selector
            or not isinstance(queue_expression.get("limit"), int)
            or isinstance(queue_expression.get("limit"), bool)
            or not 1 <= queue_expression["limit"] <= 1000
            or (queue_expression.get("maintainer") is not None
                and not isinstance(queue_expression.get("maintainer"), str))
            or (queue_expression.get("package") is not None
                and not isinstance(queue_expression.get("package"), str))):
        raise ValueError("snapshot queue selection expression is incomplete")
    results = snapshot.get("results")
    selected = snapshot.get("selected")
    candidates = snapshot.get("candidates")
    if (not isinstance(results, list) or not isinstance(selected, list)
            or not isinstance(candidates, list)):
        raise ValueError("snapshot selected results are missing")
    if snapshot.get("selected_count") != len(results) or len(selected) != len(results):
        raise ValueError("snapshot selected counts do not match")
    result_ids = [item.get("issue") for item in results
                  if isinstance(item, dict)]
    selected_ids = [item.get("issue") for item in selected
                    if isinstance(item, dict)]
    if (len(result_ids) != len(results) or len(selected_ids) != len(selected)
            or result_ids != selected_ids or len(result_ids) != len(set(result_ids))
            or any(not isinstance(number, int) or number < 1 for number in result_ids)):
        raise ValueError("snapshot selected issue list is inconsistent")
    if snapshot.get("resulting_issues") != result_ids:
        raise ValueError("snapshot resulting issue set is inconsistent")
    if (issue_mode == "exact"
            and (not isinstance(explicit_issues, list)
                 or not explicit_issues
                 or result_ids != explicit_issues)):
        raise ValueError("snapshot exact issue set is inconsistent")
    for result, summary in zip(results, selected, strict=True):
        selection = result.get("selection")
        if (not isinstance(selection, dict) or selection.get("selected") is not True
                or not selection.get("selection_reason")
                or result.get("selection_reason") != summary.get("selection_reason")):
            raise ValueError("snapshot selection evidence is incomplete")
        if selection.get("selection_reasons") != summary.get("selection_reasons"):
            raise ValueError("snapshot selection reasons do not match")
        if not isinstance(result.get("config_evidence"), dict):
            raise ValueError("snapshot config evidence is incomplete")
        if not isinstance(result.get("status_evidence"), dict):
            raise ValueError("snapshot status evidence is incomplete")
    candidate_by_issue = {
        item.get("issue"): item for item in candidates if isinstance(item, dict)
    }
    if (len(candidate_by_issue) != len(candidates)
            or any(candidate_by_issue.get(item["issue"]) != item for item in results)):
        raise ValueError("snapshot candidates do not match selected results")
    counts = (snapshot.get("total_count"), snapshot.get("fetched_count"),
              snapshot.get("selected_count"))
    if any(not isinstance(count, int) or count < 0 for count in counts):
        raise ValueError("snapshot counts are invalid")
    if snapshot["fetched_count"] < len(candidates):
        raise ValueError("snapshot fetched count is inconsistent")
    if selector != "any":
        canonical = snapshot.get("canonical")
        config_meta = canonical.get("config") if isinstance(canonical, dict) else None
        if (not isinstance(canonical, dict)
                or canonical.get("fetch_status") != "fetched"
                or not canonical.get("remote_name")
                or not canonical.get("remote_url")
                or not _OID_RE.fullmatch(canonical.get("base_oid") or "")
                or not isinstance(config_meta, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", config_meta.get("sha256") or "")):
            raise ValueError("snapshot canonical evidence is incomplete")
    return copy.deepcopy(results)


def write_output(payload: dict, out_dir: Path, timestamp: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    for suffix in range(10000):
        discriminator = "" if suffix == 0 else f"-{suffix}"
        path = out_dir / f"bump-issues-{timestamp}{discriminator}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("cannot allocate a unique bump issue snapshot")
