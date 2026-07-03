from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def list_skipped(log_path: Path, pkg: str | None = None) -> list[dict]:
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
        if pkg and rec.get("cat_pkg") != pkg:
            continue
        out.append(rec)
    return out


def skip_issue(log_path: Path, issue: int, cat_pkg: str,
               target_version: str, reason: str) -> dict:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"issue": issue, "cat_pkg": cat_pkg, "target_version": target_version,
           "reason": reason,
           "skipped_at": datetime.now().isoformat(timespec="seconds")}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
