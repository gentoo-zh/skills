from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from gzh.repo import validate_overlay_root


def atom_from_ebuild(ebuild: Path) -> str:
    ebuild = Path(ebuild).resolve()
    if ebuild.suffix != ".ebuild" or not ebuild.is_file() or len(ebuild.parents) < 3:
        raise ValueError(f"not an ebuild path: {ebuild}")
    package = ebuild.parent.name
    category = ebuild.parent.parent.name
    if not ebuild.name.startswith(f"{package}-"):
        raise ValueError(f"ebuild filename does not match its package: {ebuild}")
    try:
        root = validate_overlay_root(ebuild.parents[2])
    except RuntimeError as exc:
        raise ValueError(f"ebuild is not in a gentoo-zh development checkout: {ebuild}") from exc
    try:
        ebuild.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"ebuild is outside the overlay: {ebuild}") from exc
    return f"={category}/{ebuild.stem}::gentoo-zh"


def _elog_files(elog_dir: Path) -> list[Path]:
    return sorted(path for path in elog_dir.iterdir() if path.is_file())


def run_verify_install(ebuild: Path, logdir: Path | None = None,
                       runner=subprocess.run) -> dict:
    """Merge one exact ebuild with the overlay CI elog settings."""
    ebuild = Path(ebuild).resolve()
    atom = atom_from_ebuild(ebuild)
    logdir = (Path(logdir).resolve() if logdir else
              Path(tempfile.mkdtemp(prefix="gzh-verify-install-")))
    elog_dir = logdir / "elog"
    elog_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PORTAGE_ELOG_CLASSES": "qa warn error",
        "PORTAGE_ELOG_SYSTEM": "save",
        "PORTAGE_LOGDIR": str(logdir),
    })

    steps = []
    source_options = ["--usepkg=n", "--usepkgonly=n"]
    onlydeps = runner(["emerge", *source_options, "--onlydeps", atom],
                      capture_output=True, text=True, env=env)
    steps.append({"name": "onlydeps", "returncode": onlydeps.returncode,
                  "stdout": onlydeps.stdout, "stderr": onlydeps.stderr})
    if onlydeps.returncode != 0:
        return {"ok": False, "atom": atom, "logdir": str(logdir),
                "failed_step": "onlydeps", "elog_files": [], "steps": steps}

    for path in _elog_files(elog_dir):
        path.unlink()

    merge = runner(
        ["emerge", *source_options, "--oneshot", "--selective=n", atom],
        capture_output=True, text=True, env=env)
    steps.append({"name": "merge", "returncode": merge.returncode,
                  "stdout": merge.stdout, "stderr": merge.stderr})
    elog_files = _elog_files(elog_dir)
    elog = [{"path": str(path),
             "text": path.read_text(encoding="utf-8", errors="replace")}
            for path in elog_files]
    failed_step = "merge" if merge.returncode != 0 else ("elog" if elog else None)
    return {"ok": failed_step is None, "atom": atom, "logdir": str(logdir),
            "failed_step": failed_step, "elog_files": elog, "steps": steps}
