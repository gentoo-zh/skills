from __future__ import annotations

SUPPORTED_EAPI = {"7", "8"}
REQUIRED_VARS = ("DESCRIPTION", "HOMEPAGE", "LICENSE", "SRC_URI", "SLOT")


def lint_ebuild(parsed: dict) -> list[dict]:
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
        if not str(parsed.get(var, "")).strip():
            issues.append({"severity": "error", "rule": f"missing-{var.lower()}",
                           "msg": f"{var} not set"})
    return issues
