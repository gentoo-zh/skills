from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


# A skip is sticky (the bump is blocked and should stay off the queue); an escalate is
# provisional (the bump is not offline-reproducible right now -- e.g. a metadata-driven
# hackport regen that needs upstream .cabal data -- so revisit it when that data arrives).
# The replay eval showed these two are genuinely different, so record which one it is.
KINDS = ("skip", "escalate")


def list_skipped(log_path: Path, pkg: str | None = None,
                 kind: str | None = None) -> list[dict]:
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    out: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if pkg and rec.get("cat_pkg") != pkg:
            continue
        # old records predate the field; treat a missing kind as "skip".
        if kind and rec.get("kind", "skip") != kind:
            continue
        out.append(rec)
    return out


def skip_issue(log_path: Path, issue: int, cat_pkg: str,
               target_version: str, reason: str, kind: str = "skip") -> dict:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"issue": issue, "cat_pkg": cat_pkg, "target_version": target_version,
           "reason": reason, "kind": kind if kind in KINDS else "skip",
           "skipped_at": datetime.now(timezone.utc)
           .isoformat(timespec="seconds").replace("+00:00", "Z")}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
