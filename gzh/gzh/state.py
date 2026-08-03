from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the durable state directory without touching an overlay checkout."""
    values = os.environ if env is None else env
    if override := values.get("GZH_STATE_DIR"):
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("GZH_STATE_DIR must be an absolute path")
        return path
    if xdg_state_home := values.get("XDG_STATE_HOME"):
        candidate = Path(xdg_state_home).expanduser()
        if candidate.is_absolute():
            return candidate / "gentoo-zh-skills"
    base = Path.home() / ".local" / "state"
    return base / "gentoo-zh-skills"


def queue_dir(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "queues"


def triage_log(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "triage" / "skip-log.jsonl"


def batch_dir(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "batches"
