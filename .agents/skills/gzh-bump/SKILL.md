---
name: gzh-bump
description: Used for updating one existing gentoo-zh package to a verified upstream release.
---

Read first, in the overlay checkout: `AGENTS.md`, `.agents/rules/version-bumps.md`, `.agents/rules/pr-text.md`; `.agents/rules/prebuilt-binaries.md` when the package installs a prebuilt or bundled payload.

Placeholders in angle brackets are filled per task: `<cat>/<pkg>`, `<old>`, `<new>`, `<canonical>` (the gentoo-zh remote), `<fork>` (the personal fork remote).

## Steps
1. Preflight: fetch the canonical branch, confirm the tree is clean, create one topic worktree.
   ```bash
   command -v pkgdev pkgcheck >/dev/null || exit 1   # missing: stop and ask the human to emerge dev-util/pkgdev dev-util/pkgcheck
   git fetch <canonical> && git status --short
   git worktree add <worktree> -b <cat>-<pkg>-<new> <canonical>/master && cd <worktree>
   ```
2. Read the old ebuild and the package history; compare the target release with them: tag, artifact, build metadata, patch context, installed layout.
   ```bash
   git log --oneline -- <cat>/<pkg>
   cat <cat>/<pkg>/<pkg>-<old>.ebuild
   ```
3. For a vendor bundle, keep the host and naming already in `SRC_URI` and confirm the target release exists there.
   ```bash
   grep -n SRC_URI <cat>/<pkg>/<pkg>-<old>.ebuild
   gh release view <new> --repo <bundle-repo>
   ```
4. Rename with `git mv`, edit only what the release changed, regenerate the Manifest.
   ```bash
   git mv <cat>/<pkg>/<pkg>-<old>.ebuild <cat>/<pkg>/<pkg>-<new>.ebuild
   pkgdev manifest <cat>/<pkg>
   ```
5. Clean install. Copy the package into a local repository Portage already knows (`eselect repository create local` makes one whose `layout.conf` accepts this overlay's thin Manifest), then merge from it. A green CI run does not replace this step.
   ```bash
   cp -r <cat>/<pkg> /var/db/repos/<local>/<cat>/
   emerge --oneshot "=<cat>/<pkg>-<new>::<local>" 2>&1 | tee /tmp/<pkg>.log
   ```
   Where the environment cannot run a real merge, skip this step and steps 6–7, and record in the completion report that the install, QA output, installed files, and smoke test were not checked, and why.
6. Inspect QA output and installed files, then start the program once with a package-specific command; an install alone is not a smoke test.
   ```bash
   grep -E 'QA Notice|ERROR' /tmp/<pkg>.log
   qlist -Iv <cat>/<pkg>
   <smoke-command>
   ```
7. Remove the copy from the local repository, test distfiles, logs, and temporary configuration.
8. Rebase, then scan the exact branch range over the network.
   ```bash
   git fetch <canonical> && git rebase <canonical>/master
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   ```
9. Commit through `pkgdev` and push the topic branch to the personal fork only.
   ```bash
   pkgdev commit --scan false --signoff --gpg-sign -m "<cat>/<pkg>: add <new>, drop <old>"
   git push <fork> <cat>-<pkg>-<new>
   ```
10. Show the human the title, body, and `git diff --name-only <canonical>/master...HEAD` and wait for approval of that PR. A routine bump body is only `Closes #N`. Use `--draft` when the package needs a `::gentoo` version the CI snapshot does not have yet. Without `gh`, hand over the compare link and let the human open the PR.
    ```bash
    gh pr create --base master --head <fork-owner>:<cat>-<pkg>-<new> --title "<cat>/<pkg>: add <new>, drop <old>" --body "Closes #<issue>"
    ```
    ```text
    https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<cat>-<pkg>-<new>
    ```
11. Remove the worktree once the branch is pushed.
    ```bash
    git worktree remove <worktree>
    ```

## Checklist
- [ ] One topic branch from fetched canonical `master`.
- [ ] Target tag, artifact, build metadata, patch context, and installed layout compared.
- [ ] Vendor bundle keeps the existing `SRC_URI` host and naming.
- [ ] Rename used `git mv`; Manifest regenerated.
- [ ] Clean install ran and QA output, `qlist`, and a smoke command were inspected, or all four are recorded as skipped with the reason.
- [ ] Test artifacts removed.
- [ ] `pkgcheck scan --commits` over the merge-base range ran with `--net`.
- [ ] Commit made through `pkgdev` with sign-off; branch pushed to the fork only.
- [ ] Routine body is only `Closes #N`; CI-snapshot dependency makes the PR a draft.
