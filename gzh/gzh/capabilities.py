"""Repository adapter validation and read-only capability inspection."""

from __future__ import annotations

import copy
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse


class CapabilityState(str, Enum):
    KNOWN = "known"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ProfileValidationError(ValueError):
    """Raised when an adapter profile violates the deterministic schema."""


class OperationBlockedError(RuntimeError):
    """Raised when a caller attempts an operation that is not ready."""


_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9.-]*")
_ADAPTER_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_RESOLUTION_KEYS = {
    "repository_names",
    "canonical_repositories",
    "default_branch",
    "remote_preference",
    "forbidden_roots",
}
_RUNTIME_FIELDS = {
    "root",
    "development_checkout",
    "repo_name",
    "identity",
    "canonical_remote",
    "default_branch",
    "current_branch",
    "clean",
    "ahead",
    "behind",
    "base_synchronized",
}


def _fail(location: str, message: str) -> None:
    raise ProfileValidationError(f"{location}: {message}")


def _require_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(location, "expected an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], location: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        _fail(location, f"keys differ; missing={missing}, extra={extra}")


def _nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(location, "expected a non-empty string")
    return value


def _string_list(value: Any, location: str, *, unique: bool = True) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(location, "expected a non-empty string array")
    result = [_nonempty_string(item, f"{location}[{index}]")
              for index, item in enumerate(value)]
    if unique and len(result) != len(set(result)):
        _fail(location, "values must be unique")
    return result


def _validate_sources(raw: Any) -> dict[str, dict[str, Any]]:
    sources = _require_mapping(raw, "sources")
    if not sources:
        _fail("sources", "at least one source is required")
    validated: dict[str, dict[str, Any]] = {}
    for identifier in sorted(sources):
        if not _IDENTIFIER_RE.fullmatch(identifier):
            _fail(f"sources.{identifier}", "invalid source identifier")
        source = _require_mapping(sources[identifier], f"sources.{identifier}")
        _require_exact_keys(
            source, {"title", "authority", "location", "reviewed_evidence"},
            f"sources.{identifier}")
        _nonempty_string(source["title"], f"sources.{identifier}.title")
        _nonempty_string(source["authority"], f"sources.{identifier}.authority")
        location = _nonempty_string(
            source["location"], f"sources.{identifier}.location")
        if not (location.startswith("https://") or location.startswith("repository:")):
            _fail(
                f"sources.{identifier}.location",
                "expected an HTTPS or repository-relative location")
        evidence = _require_mapping(
            source["reviewed_evidence"],
            f"sources.{identifier}.reviewed_evidence")
        _require_exact_keys(
            evidence, {"kind", "value", "checked_at"},
            f"sources.{identifier}.reviewed_evidence")
        _nonempty_string(evidence["kind"], f"sources.{identifier}.reviewed_evidence.kind")
        _nonempty_string(evidence["value"], f"sources.{identifier}.reviewed_evidence.value")
        checked_at = _nonempty_string(
            evidence["checked_at"],
            f"sources.{identifier}.reviewed_evidence.checked_at")
        if not _TIMESTAMP_RE.fullmatch(checked_at):
            _fail(
                f"sources.{identifier}.reviewed_evidence.checked_at",
                "expected a UTC timestamp")
        validated[identifier] = copy.deepcopy(dict(source))
    return validated


def _validate_state_record(
    raw: Any, location: str, sources: Mapping[str, Any],
) -> dict[str, Any]:
    record = _require_mapping(raw, location)
    state_value = record.get("state")
    try:
        state = CapabilityState(state_value)
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(
            f"{location}.state: expected known, unsupported, or unknown") from exc
    expected = ({"state", "value", "sources"} if state is CapabilityState.KNOWN
                else {"state", "reason", "sources"})
    _require_exact_keys(record, expected, location)
    source_ids = _string_list(record["sources"], f"{location}.sources")
    missing = sorted(set(source_ids) - set(sources))
    if missing:
        _fail(f"{location}.sources", f"unknown source identifiers: {missing}")
    if state is CapabilityState.KNOWN and record["value"] is None:
        _fail(f"{location}.value", "known capability requires a value")
    if state is not CapabilityState.KNOWN:
        _nonempty_string(record["reason"], f"{location}.reason")
    return copy.deepcopy(dict(record))


def _validate_canonical_repositories(
    value: Any, source_ids: set[str], location: str,
) -> None:
    if not isinstance(value, list) or not value:
        _fail(location, "expected at least one canonical repository")
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        item_location = f"{location}[{index}]"
        item = _require_mapping(raw, item_location)
        _require_exact_keys(
            item, {"host", "path", "priority", "case_sensitive", "source"},
            item_location)
        host = _nonempty_string(item["host"], f"{item_location}.host").lower()
        path = _nonempty_string(item["path"], f"{item_location}.path").strip("/")
        if not path or path.endswith(".git"):
            _fail(f"{item_location}.path", "expected a normalized repository path")
        if type(item["priority"]) is not int or item["priority"] < 0:
            _fail(f"{item_location}.priority", "expected a non-negative integer")
        if type(item["case_sensitive"]) is not bool:
            _fail(f"{item_location}.case_sensitive", "expected a boolean")
        source = _nonempty_string(item["source"], f"{item_location}.source")
        if source not in source_ids:
            _fail(f"{item_location}.source", "source is not attached to the capability")
        identity = (host, path if item["case_sensitive"] else path.casefold())
        if identity in identities:
            _fail(item_location, "duplicate canonical repository identity")
        identities.add(identity)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class RepositoryAdapter:
    """A validated, reviewed repository capability profile."""

    adapter_id: str
    profile_revision: int
    reviewed_at: str
    sources: Mapping[str, Mapping[str, Any]]
    resolution: Mapping[str, str]
    capabilities: Mapping[str, Mapping[str, Any]]
    operations: Mapping[str, Mapping[str, Any]]

    def capability(self, identifier: str) -> dict[str, Any]:
        try:
            return _thaw(self.capabilities[identifier])
        except KeyError as exc:
            raise KeyError(f"adapter has no capability {identifier!r}") from exc

    def operation(self, identifier: str) -> dict[str, Any]:
        try:
            return _thaw(self.operations[identifier])
        except KeyError as exc:
            raise KeyError(f"adapter has no operation {identifier!r}") from exc

    def as_profile_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "adapter_id": self.adapter_id,
            "profile_revision": self.profile_revision,
            "reviewed_at": self.reviewed_at,
            "sources": _thaw(self.sources),
            "resolution": _thaw(self.resolution),
            "capabilities": _thaw(self.capabilities),
            "operations": _thaw(self.operations),
        }


def validate_profile(raw: Mapping[str, Any]) -> RepositoryAdapter:
    """Validate raw adapter data and return an immutable profile handle."""
    profile = _require_mapping(raw, "profile")
    _require_exact_keys(profile, {
        "schema_version", "adapter_id", "profile_revision", "reviewed_at",
        "sources", "resolution", "capabilities", "operations",
    }, "profile")
    if profile["schema_version"] != 1:
        _fail("profile.schema_version", "unsupported schema version")
    adapter_id = _nonempty_string(profile["adapter_id"], "profile.adapter_id")
    if not _ADAPTER_RE.fullmatch(adapter_id):
        _fail("profile.adapter_id", "invalid adapter identifier")
    revision = profile["profile_revision"]
    if type(revision) is not int or revision < 1:
        _fail("profile.profile_revision", "expected a positive integer")
    reviewed_at = _nonempty_string(profile["reviewed_at"], "profile.reviewed_at")
    if not _DATE_RE.fullmatch(reviewed_at):
        _fail("profile.reviewed_at", "expected YYYY-MM-DD")

    sources = _validate_sources(profile["sources"])
    raw_capabilities = _require_mapping(profile["capabilities"], "capabilities")
    if not raw_capabilities:
        _fail("capabilities", "at least one capability is required")
    capabilities: dict[str, dict[str, Any]] = {}
    for identifier in sorted(raw_capabilities):
        if not _IDENTIFIER_RE.fullmatch(identifier):
            _fail(f"capabilities.{identifier}", "invalid capability identifier")
        capabilities[identifier] = _validate_state_record(
            raw_capabilities[identifier], f"capabilities.{identifier}", sources)

    resolution = _require_mapping(profile["resolution"], "resolution")
    _require_exact_keys(resolution, _RESOLUTION_KEYS, "resolution")
    resolution_map: dict[str, str] = {}
    for role in sorted(resolution):
        identifier = _nonempty_string(resolution[role], f"resolution.{role}")
        if identifier not in capabilities:
            _fail(f"resolution.{role}", "references an unknown capability")
        resolution_map[role] = identifier

    for role in ("repository_names", "remote_preference", "forbidden_roots"):
        capability = capabilities[resolution_map[role]]
        if capability["state"] == CapabilityState.KNOWN.value:
            values = _string_list(
                capability["value"],
                f"capabilities.{resolution_map[role]}.value")
            if role == "forbidden_roots":
                for index, value in enumerate(values):
                    if not Path(value).is_absolute():
                        _fail(
                            f"capabilities.{resolution_map[role]}.value[{index}]",
                            "forbidden roots must be absolute")
    default = capabilities[resolution_map["default_branch"]]
    if default["state"] == CapabilityState.KNOWN.value:
        branch = _nonempty_string(
            default["value"],
            f"capabilities.{resolution_map['default_branch']}.value")
        if branch.startswith("-") or ".." in branch or branch.endswith("/"):
            _fail(
                f"capabilities.{resolution_map['default_branch']}.value",
                "invalid Git branch name")
    canonical = capabilities[resolution_map["canonical_repositories"]]
    if canonical["state"] == CapabilityState.KNOWN.value:
        _validate_canonical_repositories(
            canonical["value"], set(canonical["sources"]),
            f"capabilities.{resolution_map['canonical_repositories']}.value")

    raw_operations = _require_mapping(profile["operations"], "operations")
    if not raw_operations:
        _fail("operations", "at least one operation is required")
    operations: dict[str, dict[str, Any]] = {}
    identity_requirements = {
        resolution_map["repository_names"],
        resolution_map["canonical_repositories"],
        resolution_map["default_branch"],
    }
    for identifier in sorted(raw_operations):
        if not _IDENTIFIER_RE.fullmatch(identifier):
            _fail(f"operations.{identifier}", "invalid operation identifier")
        location = f"operations.{identifier}"
        operation = _validate_state_record(raw_operations[identifier], location, sources)
        if operation["state"] == CapabilityState.KNOWN.value:
            value = _require_mapping(operation["value"], f"{location}.value")
            _require_exact_keys(
                value, {"write", "requires_capabilities", "runtime_requirements"},
                f"{location}.value")
            if type(value["write"]) is not bool:
                _fail(f"{location}.value.write", "expected a boolean")
            required = _string_list(
                value["requires_capabilities"],
                f"{location}.value.requires_capabilities") \
                if value["requires_capabilities"] else []
            missing = sorted(set(required) - set(capabilities))
            if missing:
                _fail(
                    f"{location}.value.requires_capabilities",
                    f"unknown capabilities: {missing}")
            runtime = _string_list(
                value["runtime_requirements"],
                f"{location}.value.runtime_requirements") \
                if value["runtime_requirements"] else []
            invalid_runtime = sorted(set(runtime) - _RUNTIME_FIELDS)
            if invalid_runtime:
                _fail(
                    f"{location}.value.runtime_requirements",
                    f"unknown runtime fields: {invalid_runtime}")
            if value["write"] and not identity_requirements.issubset(required):
                _fail(
                    f"{location}.value.requires_capabilities",
                    "write operations must require repository identity, canonical "
                    "repository, and default branch capabilities")
        operations[identifier] = operation

    return RepositoryAdapter(
        adapter_id=adapter_id,
        profile_revision=revision,
        reviewed_at=reviewed_at,
        sources=_freeze(sources),
        resolution=_freeze(resolution_map),
        capabilities=_freeze(capabilities),
        operations=_freeze(operations),
    )


def load_bundled_adapter(identifier: str) -> RepositoryAdapter:
    """Load and validate one bundled adapter without guessing from a checkout."""
    from gzh.adapters import profile

    return validate_profile(profile(identifier))


def _record(
    state: CapabilityState, *, value: Any = None, reason: str | None = None,
    provenance: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": state.value,
        "provenance": provenance or [],
    }
    if state is CapabilityState.KNOWN:
        result["value"] = value
    else:
        result["reason"] = reason or "no evidence available"
    return result


def _profile_record(
    adapter: RepositoryAdapter, record: Mapping[str, Any],
) -> dict[str, Any]:
    result = _thaw(record)
    result["source_records"] = {
        source: _thaw(adapter.sources[source])
        for source in record["sources"]
    }
    return result


def _run_git(
    cwd: Path, arguments: list[str], runner,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", *arguments], cwd=str(cwd), capture_output=True, text=True)


def _git_field(
    cwd: Path, arguments: list[str], runner, label: str,
) -> tuple[str | None, str | None]:
    try:
        process = _run_git(cwd, arguments, runner)
    except OSError as exc:
        return None, f"cannot resolve {label}: {exc}"
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "Git command failed").strip()
        return None, f"cannot resolve {label}: {detail}"
    return (process.stdout or "").strip(), None


def repository_identity(url: str) -> tuple[str, str] | None:
    """Normalize a Git network URL into a host and repository path."""
    value = url.strip()
    if not value:
        return None
    if "://" not in value:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", value)
        if not match:
            return None
        host, path = match.groups()
    else:
        parsed = urlparse(value)
        if not parsed.hostname:
            return None
        host, path = parsed.hostname, parsed.path
    normalized = path.strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    if not normalized:
        return None
    return host.lower(), normalized


def _matches_identity(
    observed: tuple[str, str], expected: Mapping[str, Any],
) -> bool:
    host, path = observed
    if host != expected["host"].lower():
        return False
    expected_path = expected["path"].strip("/")
    if expected["case_sensitive"]:
        return path == expected_path
    return path.casefold() == expected_path.casefold()


def _canonical_remote(
    root: Path, adapter: RepositoryAdapter, runner,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    provenance = [{"kind": "command", "location": "git remote -v"}]
    remote_text, error = _git_field(root, ["remote", "-v"], runner, "Git remotes")
    if error:
        return _record(
            CapabilityState.UNKNOWN, reason=error, provenance=provenance), []
    capability = adapter.capabilities[
        adapter.resolution["canonical_repositories"]]
    if capability["state"] != CapabilityState.KNOWN.value:
        return _record(
            CapabilityState(capability["state"]),
            reason=capability.get("reason"),
            provenance=provenance), []
    matches: dict[str, dict[str, Any]] = {}
    observed_remotes: list[dict[str, Any]] = []
    for line in (remote_text or "").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "(fetch)":
            continue
        name, url = parts[0], parts[1]
        identity = repository_identity(url)
        observed = {"name": name, "url": url, "identity": (
            {"host": identity[0], "path": identity[1]} if identity else None)}
        observed_remotes.append(observed)
        if identity is None:
            continue
        candidates = [
            expected for expected in capability["value"]
            if _matches_identity(identity, expected)
        ]
        if not candidates:
            continue
        selected = min(candidates, key=lambda item: item["priority"])
        previous = matches.get(name)
        if previous is None or selected["priority"] < previous["priority"]:
            matches[name] = {
                "name": name,
                "url": url,
                "identity": {"host": identity[0], "path": identity[1]},
                "profile_identity": _thaw(selected),
                "priority": selected["priority"],
            }
    if not matches:
        return _record(
            CapabilityState.UNKNOWN,
            reason="no fetch remote URL matches a canonical repository identity",
            provenance=provenance), observed_remotes
    best_priority = min(item["priority"] for item in matches.values())
    best = {name: item for name, item in matches.items()
            if item["priority"] == best_priority}
    default = adapter.capabilities[adapter.resolution["default_branch"]]
    aliases: list[dict[str, Any]] = []
    if len(best) > 1:
        if default["state"] != CapabilityState.KNOWN.value:
            return _record(
                CapabilityState.UNKNOWN,
                reason="cannot compare canonical aliases without a known default branch",
                provenance=provenance), observed_remotes
        for name, item in sorted(best.items()):
            ref = f"refs/remotes/{name}/{default['value']}"
            oid, oid_error = _git_field(
                root, ["rev-parse", "--verify", ref], runner,
                f"canonical remote ref {ref}")
            aliases.append({
                "name": name,
                "oid": oid if not oid_error else None,
                "ref": ref,
                "url": item["url"],
            })
        oids = {item["oid"] for item in aliases}
        if None in oids or len(oids) != 1:
            detail = ", ".join(
                f"{item['name']}={item['oid'] or 'missing'}" for item in aliases)
            return _record(
                CapabilityState.UNKNOWN,
                reason=("canonical remote aliases do not have one verified default-"
                        f"branch OID: {detail}"),
                provenance=provenance), observed_remotes
    preference = adapter.capabilities[
        adapter.resolution["remote_preference"]]
    if len(best) > 1 and preference["state"] == CapabilityState.KNOWN.value:
        for name in preference["value"]:
            if name in best:
                best = {name: best[name]}
                break
    if len(best) > 1 and aliases:
        name = sorted(best)[0]
        best = {name: best[name]}
    if len(best) != 1:
        return _record(
            CapabilityState.UNKNOWN,
            reason=("multiple canonical fetch remotes remain after applying the "
                    f"reviewed preference: {sorted(best)}"),
            provenance=provenance), observed_remotes
    selected = next(iter(best.values()))
    selected.pop("priority", None)
    if aliases:
        selected["aliases"] = aliases
        selected["oid"] = aliases[0]["oid"]
    return _record(
        CapabilityState.KNOWN, value=selected,
        provenance=provenance), observed_remotes


def _unknown_runtime_fields(reason: str) -> dict[str, dict[str, Any]]:
    return {
        name: _record(CapabilityState.UNKNOWN, reason=reason)
        for name in _RUNTIME_FIELDS
    }


def _runtime_fields(
    start: Path, adapter: RepositoryAdapter, runner,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    candidate = Path(start).expanduser().resolve()
    root_text, error = _git_field(
        candidate, ["rev-parse", "--show-toplevel"], runner,
        "Git worktree root")
    if error:
        return _unknown_runtime_fields(error), []
    root = Path(root_text).resolve()
    fields = _unknown_runtime_fields("field was not resolved")
    fields["root"] = _record(
        CapabilityState.KNOWN, value=str(root),
        provenance=[{"kind": "command", "location":
                     "git rev-parse --show-toplevel"}])

    forbidden = adapter.capabilities[adapter.resolution["forbidden_roots"]]
    if forbidden["state"] != CapabilityState.KNOWN.value:
        fields["development_checkout"] = _record(
            CapabilityState(forbidden["state"]),
            reason=forbidden.get("reason"),
            provenance=[{"kind": "profile", "location":
                         adapter.resolution["forbidden_roots"]}])
    else:
        blocked_root = next((
            Path(value).expanduser().resolve() for value in forbidden["value"]
            if root == Path(value).expanduser().resolve()
            or Path(value).expanduser().resolve() in root.parents
        ), None)
        if blocked_root:
            fields["development_checkout"] = _record(
                CapabilityState.UNSUPPORTED,
                reason=f"worktree is inside a forbidden root: {blocked_root}",
                provenance=[{"kind": "profile", "location":
                             adapter.resolution["forbidden_roots"]}])
        else:
            fields["development_checkout"] = _record(
                CapabilityState.KNOWN, value=True,
                provenance=[{"kind": "profile", "location":
                             adapter.resolution["forbidden_roots"]}])

    repo_name_path = root / "profiles" / "repo_name"
    try:
        repo_name = repo_name_path.read_text(encoding="utf-8").strip()
        if not repo_name or "\n" in repo_name:
            raise ValueError("expected one non-empty line")
        fields["repo_name"] = _record(
            CapabilityState.KNOWN, value=repo_name,
            provenance=[{"kind": "file", "location": "profiles/repo_name"}])
    except (OSError, UnicodeError, ValueError) as exc:
        fields["repo_name"] = _record(
            CapabilityState.UNKNOWN,
            reason=f"cannot read profiles/repo_name: {exc}",
            provenance=[{"kind": "file", "location": "profiles/repo_name"}])

    expected_names = adapter.capabilities[
        adapter.resolution["repository_names"]]
    if expected_names["state"] != CapabilityState.KNOWN.value:
        fields["identity"] = _record(
            CapabilityState(expected_names["state"]),
            reason=expected_names.get("reason"),
            provenance=[{"kind": "profile", "location":
                         adapter.resolution["repository_names"]}])
    elif fields["repo_name"]["state"] != CapabilityState.KNOWN.value:
        fields["identity"] = _record(
            CapabilityState.UNKNOWN,
            reason="repository identity is unavailable without profiles/repo_name")
    elif fields["repo_name"]["value"] not in expected_names["value"]:
        fields["identity"] = _record(
            CapabilityState.UNKNOWN,
            reason=(f"profiles/repo_name {fields['repo_name']['value']!r} does not "
                    f"match adapter names {expected_names['value']!r}"),
            provenance=[
                {"kind": "file", "location": "profiles/repo_name"},
                {"kind": "profile", "location":
                 adapter.resolution["repository_names"]},
            ])
    else:
        fields["identity"] = _record(
            CapabilityState.KNOWN,
            value={"adapter_id": adapter.adapter_id,
                   "repo_name": fields["repo_name"]["value"]},
            provenance=[
                {"kind": "file", "location": "profiles/repo_name"},
                {"kind": "profile", "location":
                 adapter.resolution["repository_names"]},
            ])

    canonical_remote, observed_remotes = _canonical_remote(root, adapter, runner)
    fields["canonical_remote"] = canonical_remote
    default = adapter.capabilities[adapter.resolution["default_branch"]]
    fields["default_branch"] = (
        _record(
            CapabilityState.KNOWN, value=default["value"],
            provenance=[{"kind": "profile", "location":
                         adapter.resolution["default_branch"]}])
        if default["state"] == CapabilityState.KNOWN.value else
        _record(
            CapabilityState(default["state"]), reason=default.get("reason"),
            provenance=[{"kind": "profile", "location":
                         adapter.resolution["default_branch"]}]))

    branch, branch_error = _git_field(
        root, ["symbolic-ref", "--quiet", "--short", "HEAD"], runner,
        "current branch")
    fields["current_branch"] = (
        _record(
            CapabilityState.KNOWN, value=branch,
            provenance=[{"kind": "command", "location":
                         "git symbolic-ref --quiet --short HEAD"}])
        if not branch_error else
        _record(
            CapabilityState.UNKNOWN, reason=branch_error,
            provenance=[{"kind": "command", "location":
                         "git symbolic-ref --quiet --short HEAD"}]))

    status, status_error = _git_field(
        root, ["status", "--porcelain=v1", "--untracked-files=normal"], runner,
        "worktree status")
    fields["clean"] = (
        _record(
            CapabilityState.KNOWN, value=not bool(status),
            provenance=[{"kind": "command", "location":
                         "git status --porcelain=v1 --untracked-files=normal"}])
        if not status_error else
        _record(
            CapabilityState.UNKNOWN, reason=status_error,
            provenance=[{"kind": "command", "location":
                         "git status --porcelain=v1 --untracked-files=normal"}]))

    if (fields["canonical_remote"]["state"] == CapabilityState.KNOWN.value
            and fields["default_branch"]["state"] == CapabilityState.KNOWN.value):
        remote_name = fields["canonical_remote"]["value"]["name"]
        default_name = fields["default_branch"]["value"]
        revision_range = f"HEAD...refs/remotes/{remote_name}/{default_name}"
        counts, counts_error = _git_field(
            root, ["rev-list", "--left-right", "--count", revision_range],
            runner, "ahead/behind state")
        try:
            if counts_error:
                raise ValueError(counts_error)
            ahead_text, behind_text = counts.split()
            ahead, behind = int(ahead_text), int(behind_text)
            if ahead < 0 or behind < 0:
                raise ValueError("negative revision count")
        except (AttributeError, TypeError, ValueError) as exc:
            reason = str(exc)
            fields["ahead"] = _record(
                CapabilityState.UNKNOWN, reason=reason,
                provenance=[{"kind": "command", "location":
                             f"git rev-list --left-right --count {revision_range}"}])
            fields["behind"] = _record(
                CapabilityState.UNKNOWN, reason=reason,
                provenance=[{"kind": "command", "location":
                             f"git rev-list --left-right --count {revision_range}"}])
        else:
            provenance = [{"kind": "command", "location":
                           f"git rev-list --left-right --count {revision_range}"}]
            fields["ahead"] = _record(
                CapabilityState.KNOWN, value=ahead, provenance=provenance)
            fields["behind"] = _record(
                CapabilityState.KNOWN, value=behind, provenance=provenance)

    if all(fields[name]["state"] == CapabilityState.KNOWN.value
           for name in ("current_branch", "default_branch", "ahead", "behind")):
        on_default_branch = (
            fields["current_branch"]["value"] == fields["default_branch"]["value"])
        synchronized = (
            fields["behind"]["value"] == 0
            and (not on_default_branch or fields["ahead"]["value"] == 0)
        )
        fields["base_synchronized"] = _record(
            CapabilityState.KNOWN, value=synchronized,
            provenance=[{"kind": "derived", "location":
                         "current_branch+default_branch+ahead+behind"}])
    else:
        fields["base_synchronized"] = _record(
            CapabilityState.UNKNOWN,
            reason="base synchronization requires branch and ahead/behind evidence",
            provenance=[{"kind": "derived", "location":
                         "current_branch+default_branch+ahead+behind"}])
    return fields, observed_remotes


def _operation_report(
    adapter: RepositoryAdapter, operation_name: str,
    runtime: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    raw = adapter.operations.get(operation_name)
    if raw is None:
        return {
            "name": operation_name,
            "state": CapabilityState.UNKNOWN.value,
            "write": True,
            "ready": False,
            "write_ready": False,
            "reason": "operation is not declared by the adapter",
            "sources": [],
            "source_records": {},
            "blockers": [{
                "field": f"operation.{operation_name}",
                "state": CapabilityState.UNKNOWN.value,
                "reason": "operation is not declared by the adapter",
            }],
        }
    expanded = _profile_record(adapter, raw)
    blockers: list[dict[str, str]] = []
    if raw["state"] != CapabilityState.KNOWN.value:
        blockers.append({
            "field": f"operation.{operation_name}",
            "state": raw["state"],
            "reason": raw["reason"],
        })
        write = True
        required_capabilities: list[str] = []
        runtime_requirements: list[str] = []
    else:
        write = raw["value"]["write"]
        required_capabilities = raw["value"]["requires_capabilities"]
        runtime_requirements = raw["value"]["runtime_requirements"]
        for identifier in required_capabilities:
            capability = adapter.capabilities[identifier]
            if capability["state"] != CapabilityState.KNOWN.value:
                blockers.append({
                    "field": f"capability.{identifier}",
                    "state": capability["state"],
                    "reason": capability["reason"],
                })
        for name in runtime_requirements:
            field = runtime[name]
            if field["state"] != CapabilityState.KNOWN.value:
                blockers.append({
                    "field": f"runtime.{name}",
                    "state": field["state"],
                    "reason": field["reason"],
                })
            elif type(field.get("value")) is bool and field["value"] is False:
                blockers.append({
                    "field": f"runtime.{name}",
                    "state": CapabilityState.KNOWN.value,
                    "reason": "required condition is false",
                })
    ready = raw["state"] == CapabilityState.KNOWN.value and not blockers
    return {
        "name": operation_name,
        **expanded,
        "write": write,
        "ready": ready,
        "write_ready": ready if write else None,
        "required_capabilities": required_capabilities,
        "runtime_requirements": runtime_requirements,
        "blockers": blockers,
    }


def inspect_repository(
    start: Path, adapter: RepositoryAdapter, *,
    operation: str = "inspect", runner=subprocess.run,
) -> dict[str, Any]:
    """Resolve repository identity and operation readiness without changing state."""
    if not isinstance(adapter, RepositoryAdapter):
        raise TypeError("adapter must be a validated RepositoryAdapter")
    operation = _nonempty_string(operation, "operation")
    runtime, remotes = _runtime_fields(Path(start), adapter, runner)
    operation_result = _operation_report(adapter, operation, runtime)
    essential = (
        "root", "development_checkout", "repo_name", "identity",
        "canonical_remote", "default_branch", "current_branch", "clean",
        "ahead", "behind", "base_synchronized",
    )
    complete = all(
        runtime[name]["state"] == CapabilityState.KNOWN.value
        for name in essential)
    return {
        "schema_version": 1,
        "adapter": {
            "id": adapter.adapter_id,
            "profile_revision": adapter.profile_revision,
            "reviewed_at": adapter.reviewed_at,
        },
        "capabilities": {
            identifier: _profile_record(adapter, record)
            for identifier, record in sorted(adapter.capabilities.items())
        },
        "repository": runtime,
        "observed_remotes": remotes,
        "operation": operation_result,
        "complete": complete,
        "ok": complete and operation_result["ready"],
    }


def require_operation_ready(report: Mapping[str, Any]) -> None:
    """Fail closed before a caller performs the inspected operation."""
    operation = report.get("operation")
    if not isinstance(operation, Mapping) or operation.get("ready") is not True:
        blockers = operation.get("blockers", []) if isinstance(operation, Mapping) else []
        reasons = "; ".join(
            f"{item.get('field', 'operation')}: {item.get('reason', 'blocked')}"
            for item in blockers if isinstance(item, Mapping))
        raise OperationBlockedError(reasons or "operation readiness was not established")
