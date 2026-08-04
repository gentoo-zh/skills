from __future__ import annotations

import re

SUPPORTED_EAPI = {"7", "8", "9"}
REQUIRED_VARS = ("DESCRIPTION", "HOMEPAGE", "LICENSE", "SRC_URI", "SLOT")
# eclasses that provide SRC_URI themselves, so an ebuild inheriting them need
# not set SRC_URI explicitly.
SRC_URI_ECLASSES = {"pypi", "github"}


def _src_uri_provided(parsed: dict) -> bool:
    inherit = set(parsed.get("inherit", []))
    if inherit & SRC_URI_ECLASSES:
        return True
    pv = str(parsed.get("PV", ""))
    # live ebuilds (9999) have no upstream distfile SRC_URI
    return pv == "9999" or pv.startswith("9999.")


def _phase_body(source_text: str, phase: str) -> str | None:
    match = re.search(
        rf"(?ms)^\s*{re.escape(phase)}\s*\(\)\s*\{{(.*?)^\s*\}}\s*$",
        source_text,
    )
    return match.group(1) if match else None


def _without_shell_comments(source_text: str) -> str:
    result = []
    quote: str | None = None
    escaped = False
    comment = False
    for character in source_text:
        if comment:
            if character == "\n":
                comment = False
                result.append(character)
            continue
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\" and quote != "'":
            result.append(character)
            escaped = True
            continue
        if quote is not None:
            result.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            result.append(character)
        elif character == "#":
            comment = True
        else:
            result.append(character)
    return "".join(result)


def _without_shell_strings(source_text: str) -> str:
    result = []
    quote: str | None = None
    escaped = False
    for character in _without_shell_comments(source_text):
        if escaped:
            result.append(" ")
            escaped = False
            continue
        if character == "\\" and quote != "'":
            result.append(" ")
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            result.append("\n" if character == "\n" else " ")
            continue
        if character in {"'", '"'}:
            quote = character
            result.append(" ")
        else:
            result.append(character)
    return "".join(result)


def _unpacker_review_issues(parsed: dict, source_text: str) -> list[dict]:
    issues = []
    src_unpack = _phase_body(source_text, "src_unpack")
    unpack_body = _without_shell_comments(src_unpack or "")
    unpack_commands = _without_shell_strings(src_unpack or "")
    manual_deb = re.search(
        r"(?i)(?:\bdpkg-deb\b|\bar\s+(?:x|p)\b|\.deb\b|\bdata\.tar(?:\.|\b))",
        unpack_body,
    )
    if ("unpacker" in set(parsed.get("inherit", []))
            and src_unpack is not None and manual_deb is not None
            and not re.search(r"\bunpack_deb\b", unpack_commands)):
        issues.append({
            "severity": "warning",
            "rule": "unpacker-helper-bypassed",
            "msg": (
                "src_unpack overrides unpacker.eclass without using unpack_deb; "
                "review the archive model and helper contract"),
        })
    src_install = _without_shell_strings(
        _phase_body(source_text, "src_install") or "")
    if re.search(
            r"(?m)(?:^|[;&|\s])"
            r"(?:ar|bsdtar|cpio|dpkg-deb|rpm2cpio|tar|unpack|unzip)\s",
            src_install):
        issues.append({
            "severity": "warning",
            "rule": "archive-extraction-in-src-install",
            "msg": (
                "src_install extracts an archive; review whether extraction belongs "
                "in src_unpack and should use an applicable eclass helper"),
        })
    return issues


def lint_ebuild(parsed: dict, *, source_text: str | None = None) -> list[dict]:
    issues: list[dict] = []
    eapi = str(parsed.get("EAPI", "")).strip()
    if not eapi:
        issues.append({"severity": "error", "rule": "eapi-missing",
                       "msg": "EAPI not set"})
    elif eapi not in SUPPORTED_EAPI:
        issues.append({"severity": "error", "rule": "eapi-unsupported",
                       "msg": f"EAPI={eapi} unsupported (expect {sorted(SUPPORTED_EAPI)})"})
    keywords = str(parsed.get("KEYWORDS", "")).split()
    stable = [k for k in keywords if k and not k.startswith("~") and k not in ("*", "-*", "-**")]
    if stable:
        issues.append({"severity": "error", "rule": "stable-keyword",
                       "msg": f"gentoo-zh allows ~arch only; found stable: {stable}"})
    for var in REQUIRED_VARS:
        if var == "SRC_URI" and _src_uri_provided(parsed):
            continue
        if not str(parsed.get(var, "")).strip():
            issues.append({"severity": "error", "rule": f"missing-{var.lower()}",
                           "msg": f"{var} not set"})
    if source_text is not None:
        issues.extend(_unpacker_review_issues(parsed, source_text))
    return issues
