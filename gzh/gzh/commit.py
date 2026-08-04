from __future__ import annotations

import os
import subprocess
from pathlib import Path

from gzh.repo import find_canonical_remote


def signing_key_configured(cwd: Path, runner=subprocess.run) -> bool:
    """Return whether Git has an explicit user signing key."""
    key = runner(["git", "config", "--get", "user.signingkey"],
                 cwd=cwd, capture_output=True, text=True)
    return key.returncode == 0 and bool(key.stdout.strip())


def _owned_paths(paths: list[Path], cwd: Path) -> tuple[list[str], list[str]]:
    root = cwd.resolve()
    exact: list[Path] = []
    scopes: list[Path] = []
    for raw in paths:
        requested = raw if raw.is_absolute() else root / raw
        candidate = Path(os.path.abspath(requested))
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"commit path is outside the overlay: {raw}") from exc
        if relative == Path("."):
            raise ValueError("the overlay root is not an allowed commit path")
        parent = candidate.parent
        while parent != root:
            if parent.is_symlink():
                raise ValueError(f"commit path traverses a symlink: {raw}")
            parent = parent.parent
        if relative not in exact:
            exact.append(relative)

        scope = candidate
        while scope != root and not (scope.exists() or scope.is_symlink()):
            scope = scope.parent
        relative_scope = scope.relative_to(root)
        if relative_scope not in scopes:
            scopes.append(relative_scope)

    collapsed: list[Path] = []
    for scope in sorted(scopes, key=lambda item: len(item.parts)):
        if any(parent == scope or parent in scope.parents for parent in collapsed):
            continue
        collapsed.append(scope)
    return ([str(path) for path in exact],
            [str(path) if path != Path(".") else "." for path in collapsed])


def _ebuild_identity(path: str) -> tuple[str, str] | None:
    parts = Path(path).parts
    if len(parts) != 3 or not parts[-1].endswith(".ebuild"):
        return None
    package = parts[1]
    filename = parts[2]
    prefix = f"{package}-"
    if not filename.startswith(prefix):
        return None
    return f"{parts[0]}/{package}", filename[len(prefix):-len(".ebuild")]


def _expected_bump_subject(name_status: str) -> str | None:
    changes: dict[str, dict[str, set[str]]] = {}

    def record(kind: str, path: str) -> None:
        identity = _ebuild_identity(path)
        if identity is None:
            return
        package, version = identity
        changes.setdefault(package, {"add": set(), "drop": set()})[kind].add(version)

    for line in name_status.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status = fields[0][:1]
        if status == "A":
            record("add", fields[1])
        elif status == "D":
            record("drop", fields[1])
        elif status == "R" and len(fields) >= 3:
            record("drop", fields[1])
            record("add", fields[2])

    candidates = [(package, values) for package, values in changes.items()
                  if len(values["add"]) == 1 and len(values["drop"]) <= 1]
    if len(candidates) != 1 or len(changes) != 1:
        return None
    package, values = candidates[0]
    added = next(iter(values["add"]))
    subject = f"{package}: add {added}"
    if values["drop"]:
        subject += f", drop {next(iter(values['drop']))}"
    return subject


def _restore_owned_index(cwd: Path, paths: list[str], runner) -> dict:
    restored = runner(
        ["git", "reset", "--mixed", "HEAD", "--", *paths], cwd=cwd,
        capture_output=True, text=True)
    return {
        "index_restored": restored.returncode == 0,
        "index_restore_stderr": restored.stderr,
    }


def run_commit(paths: list[Path], cwd: Path,
               message: str | None = None,
               runner=subprocess.run, *, require_clean_index: bool = True) -> dict:
    try:
        exact_paths, pkgdev_paths = _owned_paths(paths, cwd)
    except ValueError as exc:
        return {"ok": False, "stage": "paths", "error": str(exc)}
    if not exact_paths:
        return {"ok": False, "stage": "paths", "error": "no commit paths supplied"}
    if require_clean_index:
        clean = runner(["git", "diff", "--cached", "--quiet"], cwd=cwd,
                       capture_output=True, text=True)
        if clean.returncode != 0:
            error = ("index contains pre-existing staged changes" if clean.returncode == 1
                     else "cannot inspect the staged index")
            return {"ok": False, "returncode": clean.returncode,
                    "stage": "preflight", "error": error, "stderr": clean.stderr}

    staged = runner(["git", "add", "--", *exact_paths], cwd=cwd,
                    capture_output=True, text=True)
    if staged.returncode != 0:
        return {"ok": False, "returncode": staged.returncode,
                "stage": "git-add", "paths": exact_paths,
                "stdout": staged.stdout, "stderr": staged.stderr,
                **_restore_owned_index(cwd, exact_paths, runner)}
    staged_diff = runner(
        ["git", "diff", "--cached", "--name-status", "--find-renames", "--",
         *pkgdev_paths], cwd=cwd, capture_output=True, text=True)
    if staged_diff.returncode != 0:
        return {"ok": False, "returncode": staged_diff.returncode,
                "stage": "staged-diff", "stdout": staged_diff.stdout,
                "stderr": staged_diff.stderr,
                **_restore_owned_index(cwd, exact_paths, runner)}
    expected_subject = _expected_bump_subject(staged_diff.stdout or "")
    if expected_subject is not None and message is not None:
        supplied_subject = message.splitlines()[0].strip()
        if supplied_subject != expected_subject:
            return {"ok": False, "stage": "message-validation",
                    "expected_subject": expected_subject,
                    "actual_subject": supplied_subject,
                    "error": "supplied subject does not match the staged ebuild add/drop set",
                    **_restore_owned_index(cwd, exact_paths, runner)}

    head = runner(["git", "rev-parse", "HEAD"], cwd=cwd,
                  capture_output=True, text=True)
    old_head = (head.stdout or "").strip()
    if head.returncode != 0 or not old_head:
        return {"ok": False, "stage": "head",
                "error": "cannot resolve HEAD before commit",
                "stderr": head.stderr,
                **_restore_owned_index(cwd, exact_paths, runner)}

    # The overlay contract adds --gpg-sign only for an explicit user.signingkey.
    # Git still honors commit.gpgsign independently when this option is omitted.
    args = ["pkgdev", "commit", "--scan", "false", "--signoff=true"]
    if signing_key_configured(cwd, runner=runner):
        args.append("--gpg-sign")
    if message:
        args += ["--message", message]
    args += pkgdev_paths
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    result = {"ok": proc.returncode == 0, "returncode": proc.returncode,
              "stdout": proc.stdout, "stderr": proc.stderr,
              "pathspecs": pkgdev_paths}
    if proc.returncode != 0:
        current = runner(["git", "rev-parse", "HEAD"], cwd=cwd,
                         capture_output=True, text=True)
        current_head = (current.stdout or "").strip()
        if current.returncode == 0 and current_head and current_head != old_head:
            rollback = runner(["git", "reset", "--mixed", old_head], cwd=cwd,
                              capture_output=True, text=True)
            result.update({"commit_created": rollback.returncode != 0,
                           "rolled_back": rollback.returncode == 0,
                           "rollback_stderr": rollback.stderr})
        else:
            result.update(_restore_owned_index(cwd, exact_paths, runner))
        return result
    if expected_subject is None:
        return result
    subject = runner(["git", "log", "-1", "--format=%s", "HEAD"], cwd=cwd,
                     capture_output=True, text=True)
    actual_subject = (subject.stdout or "").strip()
    if subject.returncode != 0 or actual_subject != expected_subject:
        rollback = runner(["git", "reset", "--mixed", old_head], cwd=cwd,
                          capture_output=True, text=True)
        return {**result, "ok": False, "stage": "subject-validation",
                "commit_created": rollback.returncode != 0,
                "rolled_back": rollback.returncode == 0,
                "rollback_stderr": rollback.stderr,
                "expected_subject": expected_subject,
                "actual_subject": actual_subject,
                "error": "commit subject does not match the staged ebuild add/drop set"}
    return result


def _output(args: list[str], cwd: Path, runner) -> tuple[int, str, str]:
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "").strip(), proc.stderr or ""


def run_recommit(paths: list[Path], cwd: Path, message: str | None = None,
                 runner=subprocess.run) -> dict:
    """Rebuild the sole canonical-base commit through pkgdev after local fixes."""
    staged, _, stderr = _output(
        ["git", "diff", "--cached", "--quiet"], cwd, runner)
    if staged != 0:
        return {"ok": False, "stage": "preflight",
                "error": "index must be clean before recommit", "stderr": stderr}
    try:
        remote = find_canonical_remote(cwd, runner=runner)
    except RuntimeError as exc:
        return {"ok": False, "stage": "canonical-remote", "error": str(exc)}
    code, base, stderr = _output(
        ["git", "merge-base", f"{remote}/master", "HEAD"], cwd, runner)
    if code != 0 or not base:
        return {"ok": False, "stage": "merge-base",
                "error": f"no merge-base with {remote}/master", "stderr": stderr}
    code, count, stderr = _output(
        ["git", "rev-list", "--count", f"{base}..HEAD"], cwd, runner)
    if code != 0 or count != "1":
        return {"ok": False, "stage": "commit-count",
                "error": f"expected one local commit, found {count or 'unknown'}",
                "stderr": stderr}
    code, old_head, stderr = _output(["git", "rev-parse", "HEAD"], cwd, runner)
    if code != 0 or not old_head:
        return {"ok": False, "stage": "head", "error": "cannot resolve HEAD",
                "stderr": stderr}
    code, parent, stderr = _output(["git", "rev-parse", "HEAD^"], cwd, runner)
    if code != 0 or not parent:
        return {"ok": False, "stage": "parent", "error": "cannot resolve HEAD parent",
                "stderr": stderr}
    try:
        exact_paths, _pkgdev_paths = _owned_paths(paths, cwd)
    except ValueError as exc:
        return {"ok": False, "stage": "paths", "error": str(exc)}
    if not exact_paths:
        return {"ok": False, "stage": "paths", "error": "no commit paths supplied"}
    changed = runner(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"],
        cwd=cwd, capture_output=True, text=True)
    if changed.returncode != 0:
        return {"ok": False, "stage": "path-coverage",
                "error": "cannot inspect the existing commit paths",
                "stderr": changed.stderr}
    committed_paths = [item for item in (changed.stdout or "").split("\0") if item]
    supplied = [Path(item) for item in exact_paths]
    missing = sorted(
        item for item in committed_paths
        if not any(path == Path(item) or path in Path(item).parents
                   for path in supplied))
    if missing:
        return {"ok": False, "stage": "path-coverage",
                "error": "recommit paths do not cover the existing commit",
                "missing_paths": missing}
    if message is None:
        code, message, stderr = _output(
            ["git", "log", "-1", "--format=%B", "HEAD"], cwd, runner)
        if code != 0 or not message:
            return {"ok": False, "stage": "message",
                    "error": "cannot read the existing commit message", "stderr": stderr}

    reset = runner(["git", "reset", "--soft", parent], cwd=cwd,
                   capture_output=True, text=True)
    if reset.returncode != 0:
        return {"ok": False, "stage": "reset", "error": "cannot rebuild commit",
                "stderr": reset.stderr}
    result = run_commit(paths, cwd=cwd, message=message, runner=runner,
                        require_clean_index=False)
    if not result["ok"]:
        rollback = runner(["git", "reset", "--mixed", old_head], cwd=cwd,
                          capture_output=True, text=True)
        result.update({"stage": "recommit", "rolled_back": rollback.returncode == 0,
                       "rollback_stderr": rollback.stderr})
        return result
    rebuilt = runner(["git", "rev-parse", "HEAD"], cwd=cwd,
                     capture_output=True, text=True)
    clean = runner(["git", "diff", "--cached", "--quiet"], cwd=cwd,
                   capture_output=True, text=True)
    if (rebuilt.returncode != 0 or not (rebuilt.stdout or "").strip()
            or (rebuilt.stdout or "").strip() == parent or clean.returncode != 0):
        rollback = runner(["git", "reset", "--mixed", old_head], cwd=cwd,
                          capture_output=True, text=True)
        return {**result, "ok": False, "stage": "recommit-validation",
                "rolled_back": rollback.returncode == 0,
                "rollback_stderr": rollback.stderr,
                "error": "recommit did not produce one complete replacement commit"}
    result.update({"old_head": old_head, "remote": remote})
    return result
