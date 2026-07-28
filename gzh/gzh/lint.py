from __future__ import annotations

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
        if var == "SRC_URI" and _src_uri_provided(parsed):
            continue
        if not str(parsed.get(var, "")).strip():
            issues.append({"severity": "error", "rule": f"missing-{var.lower()}",
                           "msg": f"{var} not set"})
    return issues
