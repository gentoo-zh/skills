from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Callable
from urllib.parse import quote

from gzh.qa_evidence import run_evidence_command


_FULL_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.IGNORECASE)
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_CHECK_PAGES = 100


class GitHubReadError(RuntimeError):
    pass


def _run_json(
        command: Sequence[str], *, runner: Callable = subprocess.run,
        not_found: Any = None, allow_not_found: bool = False,
) -> Any:
    evidence = run_evidence_command(
        command, timeout=60, max_output_bytes=_MAX_OUTPUT_BYTES,
        runner=runner)
    if not evidence["complete"] or evidence["truncated"]:
        message = (evidence.get("error") or {}).get("message") or "incomplete command"
        raise GitHubReadError(f"GitHub query did not complete: {message}")
    if evidence["returncode"] != 0:
        stderr = evidence["stderr"]
        if allow_not_found and re.search(r"(?:HTTP\s+)?404|Not Found", stderr, re.I):
            return not_found
        raise GitHubReadError(
            f"GitHub query failed with exit {evidence['returncode']}: "
            f"{stderr.strip() or 'no error text'}")
    try:
        return json.loads(evidence["stdout"])
    except json.JSONDecodeError as exc:
        raise GitHubReadError("GitHub query returned malformed JSON") from exc


def _owner_login(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("login")
    return value if isinstance(value, str) and value else None


def _normalize_check(raw: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("__typename") or "")
    if kind == "StatusContext" or "context" in raw:
        state = str(raw.get("state") or "").upper()
        if state in {"PENDING", "EXPECTED"}:
            status, conclusion = "IN_PROGRESS", None
        elif state == "SUCCESS":
            status, conclusion = "COMPLETED", "SUCCESS"
        elif state in {"FAILURE", "ERROR"}:
            status, conclusion = "COMPLETED", "FAILURE"
        else:
            status, conclusion = "COMPLETED", state or None
        return {
            "name": raw.get("context"),
            "url": raw.get("targetUrl"),
            "status": status,
            "conclusion": conclusion,
            "started_at": raw.get("startedAt"),
            "completed_at": None,
        }
    return {
        "name": raw.get("name"),
        "url": raw.get("detailsUrl"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "started_at": raw.get("startedAt"),
        "completed_at": raw.get("completedAt"),
    }


_PR_FIELDS = (
    "number,url,state,isDraft,headRefName,headRefOid,headRepositoryOwner,"
    "baseRefOid,createdAt,updatedAt,mergedAt,mergeCommit"
)

_CHECKS_QUERY = """query(
  $owner: String!
  $name: String!
  $number: Int!
  $endCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      commits(last: 1) {
        nodes {
          commit {
            statusCheckRollup {
              contexts(first: 100, after: $endCursor) {
                totalCount
                nodes {
                  __typename
                  ... on CheckRun {
                    name
                    detailsUrl
                    status
                    conclusion
                    startedAt
                    completedAt
                  }
                  ... on StatusContext {
                    context
                    state
                    targetUrl
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
      }
    }
  }
}"""


def _check_page(
        repository: str, number: int, *, expected_head_sha: str,
        end_cursor: str | None, runner: Callable,
) -> tuple[list[Mapping[str, Any]], int, bool, str | None]:
    owner, name = repository.split("/", 1)
    command = [
        "gh", "api", "graphql", "--method", "POST",
        "-f", f"query={_CHECKS_QUERY}",
        "-F", f"owner={owner}", "-F", f"name={name}",
        "-F", f"number={number}",
    ]
    if end_cursor is not None:
        command.extend(["-f", f"endCursor={end_cursor}"])
    raw = _run_json(command, runner=runner)
    if not isinstance(raw, Mapping):
        raise GitHubReadError("check query returned a non-object")
    if raw.get("errors"):
        raise GitHubReadError("check query returned GraphQL errors")
    data = raw.get("data")
    repository_data = data.get("repository") if isinstance(data, Mapping) else None
    pull_request = (
        repository_data.get("pullRequest")
        if isinstance(repository_data, Mapping) else None)
    if not isinstance(pull_request, Mapping):
        raise GitHubReadError("check query did not return the pull request")
    if pull_request.get("headRefOid") != expected_head_sha:
        raise GitHubReadError("pull request head changed during check pagination")

    commits = pull_request.get("commits")
    commit_nodes = commits.get("nodes") if isinstance(commits, Mapping) else None
    if (not isinstance(commit_nodes, list) or len(commit_nodes) != 1
            or not isinstance(commit_nodes[0], Mapping)):
        raise GitHubReadError("check query did not return the pull request head commit")
    commit = commit_nodes[0].get("commit")
    if not isinstance(commit, Mapping):
        raise GitHubReadError("check query returned an invalid head commit")
    rollup = commit.get("statusCheckRollup")
    if rollup is None:
        if end_cursor is not None:
            raise GitHubReadError("check rollup disappeared during pagination")
        return [], 0, False, None
    contexts = rollup.get("contexts") if isinstance(rollup, Mapping) else None
    if not isinstance(contexts, Mapping):
        raise GitHubReadError("check query returned an invalid rollup connection")
    nodes = contexts.get("nodes")
    total_count = contexts.get("totalCount")
    page_info = contexts.get("pageInfo")
    if (not isinstance(nodes, list) or not isinstance(total_count, int)
            or total_count < 0 or not isinstance(page_info, Mapping)
            or not all(isinstance(item, Mapping) for item in nodes)):
        raise GitHubReadError("check query returned an invalid rollup page")
    has_next_page = page_info.get("hasNextPage")
    next_cursor = page_info.get("endCursor")
    if not isinstance(has_next_page, bool):
        raise GitHubReadError("check query did not prove rollup completeness")
    if has_next_page and (not isinstance(next_cursor, str) or not next_cursor):
        raise GitHubReadError("check query omitted the next rollup cursor")
    return list(nodes), total_count, has_next_page, next_cursor


def _read_checks(
        repository: str, number: int, *, expected_head_sha: str,
        runner: Callable,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    end_cursor = None
    expected_count = None
    for _page_number in range(_MAX_CHECK_PAGES):
        nodes, total_count, has_next_page, next_cursor = _check_page(
            repository, number, expected_head_sha=expected_head_sha,
            end_cursor=end_cursor, runner=runner)
        if expected_count is None:
            expected_count = total_count
        elif total_count != expected_count:
            raise GitHubReadError("check rollup count changed during pagination")
        checks.extend(_normalize_check(item) for item in nodes)
        if not has_next_page:
            if len(checks) != total_count:
                raise GitHubReadError("check query returned an incomplete rollup")
            return checks
        if next_cursor == end_cursor:
            raise GitHubReadError("check query repeated the rollup cursor")
        end_cursor = next_cursor
    raise GitHubReadError(
        f"check query exceeded the {_MAX_CHECK_PAGES}-page safety limit")


def read_pull_request(
        repository: str, number: int, *, runner: Callable = subprocess.run,
) -> dict[str, Any]:
    raw = _run_json([
        "gh", "pr", "view", str(number), "--repo", repository,
        "--json", _PR_FIELDS,
    ], runner=runner)
    if not isinstance(raw, Mapping):
        raise GitHubReadError("pull request query returned a non-object")
    head_sha = raw.get("headRefOid")
    if not isinstance(head_sha, str) or not _FULL_OID_RE.fullmatch(head_sha):
        raise GitHubReadError("pull request query did not return a full head SHA")
    checks = _read_checks(
        repository, number, expected_head_sha=head_sha, runner=runner)
    merge_commit = raw.get("mergeCommit")
    merge_commit_sha = (
        merge_commit.get("oid") if isinstance(merge_commit, Mapping) else None)
    return {
        "complete": True,
        "number": raw.get("number"),
        "url": raw.get("url"),
        "state": raw.get("state"),
        "is_draft": raw.get("isDraft") is True,
        "head_branch": raw.get("headRefName"),
        "head_sha": head_sha,
        "head_owner": _owner_login(raw.get("headRepositoryOwner")),
        "base_sha": raw.get("baseRefOid"),
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
        "merged_at": raw.get("mergedAt"),
        "merge_commit_sha": merge_commit_sha,
        "merged": bool(raw.get("mergedAt")),
        "checks_complete": True,
        "checks": checks,
    }


def read_ci(
        repository: str, number: int, *, runner: Callable = subprocess.run,
) -> dict[str, Any]:
    return read_pull_request(repository, number, runner=runner)


def _flatten_pages(raw: Any) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise GitHubReadError("paginated GitHub query returned a non-array")
    values: list[Mapping[str, Any]] = []
    for page in raw:
        if not isinstance(page, list):
            raise GitHubReadError("paginated GitHub query returned an invalid page")
        for item in page:
            if not isinstance(item, Mapping):
                raise GitHubReadError("paginated GitHub query returned an invalid item")
            values.append(item)
    return values


class GitHubPublicationProvider:
    """Resolve publication state through read-only GitHub CLI queries."""

    def __init__(
            self, repository: str, *, fork_repository: str | None = None,
            runner: Callable = subprocess.run,
    ) -> None:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise ValueError("repository must use owner/name form")
        if (fork_repository is not None
                and not re.fullmatch(r"[^/\s]+/[^/\s]+", fork_repository)):
            raise ValueError("fork repository must use owner/name form")
        self.repository = repository
        self.runner = runner
        if fork_repository is None:
            user = _run_json(["gh", "api", "user"], runner=runner)
            if not isinstance(user, Mapping) or not _owner_login(user):
                raise GitHubReadError("GitHub user query did not return a login")
            fork_owner = _owner_login(user)
            fork_repository = f"{fork_owner}/{repository.rsplit('/', 1)[1]}"
        else:
            fork_owner = fork_repository.split("/", 1)[0]
        self.fork_owner = fork_owner
        self.fork_repository = fork_repository

    def _branch_ref(self, branch: str) -> list[dict[str, Any]]:
        raw = _run_json([
            "gh", "api",
            f"repos/{self.fork_repository}/git/ref/heads/{quote(branch, safe='')}",
        ], runner=self.runner, allow_not_found=True, not_found=None)
        if raw is None:
            return []
        if not isinstance(raw, Mapping):
            raise GitHubReadError("branch query returned a non-object")
        obj = raw.get("object")
        sha = obj.get("sha") if isinstance(obj, Mapping) else None
        return [{
            "branch": branch,
            "sha": sha,
            "url": raw.get("url"),
            "pushed_at": None,
        }]

    def _pull_requests(self, branch: str) -> list[dict[str, Any]]:
        pages = _run_json([
            "gh", "api", "--paginate", "--slurp", "--method", "GET",
            f"repos/{self.repository}/pulls", "-f", "state=all",
            "-f", f"head={self.fork_owner}:{branch}", "-f", "per_page=100",
        ], runner=self.runner)
        summaries = _flatten_pages(pages)
        results = []
        for summary in summaries:
            number = summary.get("number")
            if not isinstance(number, int) or number < 1:
                raise GitHubReadError("pull request query returned an invalid number")
            results.append(read_pull_request(
                self.repository, number, runner=self.runner))
        return results

    def _issue(self, number: Any) -> dict[str, Any] | None:
        if number is None:
            return None
        if not isinstance(number, int) or number < 1:
            raise GitHubReadError("recorded issue number is invalid")
        raw = _run_json([
            "gh", "api", f"repos/{self.repository}/issues/{number}",
        ], runner=self.runner, allow_not_found=True, not_found=None)
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise GitHubReadError("issue query returned a non-object")
        return {
            "number": raw.get("number"),
            "state": raw.get("state"),
            "url": raw.get("html_url"),
            "updated_at": raw.get("updated_at"),
            "closed_at": raw.get("closed_at"),
        }

    def __call__(self, item: Mapping[str, Any]) -> dict[str, Any]:
        branch = item.get("branch")
        if not isinstance(branch, str) or not branch:
            raise GitHubReadError("recorded branch is invalid")
        return {
            "complete": True,
            "refs": self._branch_ref(branch),
            "pull_requests": self._pull_requests(branch),
            "issue": self._issue(item.get("issue")),
        }
