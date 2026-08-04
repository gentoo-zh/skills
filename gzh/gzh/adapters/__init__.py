"""Reviewed repository adapter profiles bundled with gzh."""

from __future__ import annotations

from copy import deepcopy

from gzh.adapters.gentoo_zh import PROFILE as GENTOO_ZH_PROFILE


_PROFILES = {
    GENTOO_ZH_PROFILE["adapter_id"]: GENTOO_ZH_PROFILE,
}


def profile(identifier: str) -> dict:
    """Return an isolated copy of a bundled adapter profile."""
    try:
        return deepcopy(_PROFILES[identifier])
    except KeyError as exc:
        raise KeyError(f"unknown bundled repository adapter: {identifier}") from exc


def identifiers() -> tuple[str, ...]:
    """Return bundled adapter identifiers in deterministic order."""
    return tuple(sorted(_PROFILES))
