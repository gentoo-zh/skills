from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


_PLAN_FIELDS = ("title", "body", "files", "head", "base", "template")
_FULL_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"(?m)^(\s*[-*+]\s+)\[[ xX]\]")


def _payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(plan.get(field)) for field in _PLAN_FIELDS}


def _normalized_template(value: str) -> str:
    return _CHECKBOX_RE.sub(r"\1[ ]", value)


def _validate_retained_template(body: str, template: str) -> None:
    if not template.strip():
        raise ValueError("the live PR template must not be empty")
    normalized_body = _normalized_template(body)
    normalized_template = _normalized_template(template)
    if (not normalized_body.endswith(normalized_template)
            or normalized_body.count(normalized_template) != 1):
        raise ValueError(
            "PR body must retain the complete live template in its original order; "
            "only checkbox state may change")


def build_pr_plan(
        *, title: str, body: str, files: Sequence[str], head_branch: str,
        head_sha: str, base_branch: str, base_sha: str, template: str,
) -> dict[str, Any]:
    """Build a content-addressed plan without running Git or GitHub commands."""
    values = {
        "title": title,
        "body": body,
        "head_branch": head_branch,
        "head_sha": head_sha,
        "base_branch": base_branch,
        "base_sha": base_sha,
        "template": template,
    }
    invalid = [
        name for name, value in values.items()
        if (not isinstance(value, str)
            or (not value and name not in {"body", "template"}))
    ]
    invalid.extend(
        name for name in ("head_sha", "base_sha")
        if isinstance(values[name], str)
        and not _FULL_OID_RE.fullmatch(values[name])
    )
    if invalid:
        raise ValueError(f"invalid PR plan fields: {', '.join(invalid)}")
    _validate_retained_template(body, template)
    if isinstance(files, (str, bytes)) or any(
            not isinstance(path, str) or not path for path in files):
        raise ValueError("files must contain non-empty paths")
    file_list = list(files)
    if len(set(file_list)) != len(file_list):
        raise ValueError("files must not contain duplicates")

    payload = {
        "title": title,
        "body": body,
        "files": file_list,
        "head": {"branch": head_branch, "sha": head_sha},
        "base": {"branch": base_branch, "sha": base_sha},
        "template": template,
    }
    digest = _payload_digest(payload)
    return {
        "schema_version": 1,
        **payload,
        "sha256": digest,
        "plan_id": f"pr-plan:{digest}",
    }


def verify_pr_plan(
        confirmed: Mapping[str, Any], current: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify stored plan integrity and every publication-sensitive field."""
    confirmed_payload = _plan_payload(confirmed)
    current_payload = _plan_payload(current)
    confirmed_digest = _payload_digest(confirmed_payload)
    current_digest = _payload_digest(current_payload)
    stored_digest = confirmed.get("sha256")
    stored_id = confirmed.get("plan_id")
    integrity_ok = (
        isinstance(stored_digest, str)
        and hmac.compare_digest(stored_digest, confirmed_digest)
        and stored_id == f"pr-plan:{confirmed_digest}"
    )
    changed_fields = [
        field for field in _PLAN_FIELDS
        if confirmed_payload[field] != current_payload[field]
    ]
    return {
        "ok": integrity_ok and not changed_fields,
        "integrity_ok": integrity_ok,
        "confirmed_sha256": stored_digest,
        "computed_confirmed_sha256": confirmed_digest,
        "current_sha256": current_digest,
        "changed_fields": changed_fields,
    }


def verify_plan_confirmation(
        plans: Sequence[Mapping[str, Any]], approved_plan_ids: Sequence[str],
) -> dict[str, Any]:
    """Require explicit immutable plan IDs; wildcard approval is never sufficient."""
    if (isinstance(plans, (str, bytes))
            or isinstance(approved_plan_ids, (str, bytes))):
        raise TypeError("plans and approved plan IDs must be sequences")
    if any(not isinstance(plan, Mapping) for plan in plans):
        raise TypeError("plans must contain mappings")
    expected = [plan.get("plan_id") for plan in plans]
    approved = list(approved_plan_ids)
    valid_plans = all(
        verify_pr_plan(plan, plan)["ok"] for plan in plans
    )
    identifiers_valid = (
        all(isinstance(value, str) and value.startswith("pr-plan:")
            for value in expected + approved)
        and len(set(expected)) == len(expected)
        and len(set(approved)) == len(approved)
    )
    missing = sorted(set(expected) - set(approved))
    unknown = sorted(set(approved) - set(expected))
    return {
        "ok": valid_plans and identifiers_valid and not missing and not unknown,
        "expected_plan_ids": sorted(expected, key=str),
        "approved_plan_ids": sorted(approved, key=str),
        "missing_plan_ids": missing,
        "unknown_plan_ids": unknown,
    }
