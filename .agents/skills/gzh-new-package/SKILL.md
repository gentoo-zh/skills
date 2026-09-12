---
name: gzh-new-package
description: Used for adding one independently versioned package to the gentoo-zh overlay.
---

Read first, in the overlay checkout: `AGENTS.md`, `.agents/rules/new-packages.md`, `.agents/rules/eclass-discovery.md`, `.agents/rules/pr-text.md`; `.agents/rules/prebuilt-binaries.md` when the package installs a prebuilt or bundled payload, `.agents/rules/desktop-integration.md` for a desktop application, `.agents/rules/openrc-systemd.md` for a service.

Placeholders in angle brackets are filled per task: `<cat>/<pkg>`, `<ver>`, `<canonical>`, `<fork>`.

## Steps
1. Search the overlay and every configured repository for the package name, upstream project, former name, and forks before choosing an atom.
   ```bash
   eix <pkg>; eix <upstream-project>; eix <former-name-or-fork>
   ```
2. Stop and ask when any condition under "Stop when any of these holds" in `rules/new-packages.md` applies.
   ```bash
   grep -RInE 'LICENSE|COPYING|redistribut' <source-dir>
   ```
3. Record from the release itself: the immutable source artifact, build system, direct dependencies, installed files, shipped architectures.
   ```bash
   tar -tf <distfile>
   grep -RInE 'find_package|pkg_check_modules|dependency\(|dlopen' <source-dir>
   ```
4. Find current `::gentoo` precedents that use the planned eclass and language, then read each selected eclass before writing.
   ```bash
   gentoo="$(portageq get_repo_path / gentoo)"
   grep -rl 'inherit.*<eclass>' "${gentoo}" --include='*.ebuild'
   less "${gentoo}/eclass/<eclass>.eclass"
   ```
5. Preflight and branch, then write the ebuild, `metadata.xml`, and any `files/` inputs as one package change.
   ```bash
   command -v pkgdev pkgcheck >/dev/null || exit 1   # missing: stop and ask the human to emerge dev-util/pkgdev dev-util/pkgcheck
   git fetch <canonical> && git status --short
   git worktree add <worktree> -b <cat>-<pkg>-<ver> <canonical>/master && cd <worktree>
   ```
6. Add the nvchecker entry in `category/package` alphabetical order, or a commented entry with the concrete reason it cannot be tracked.
   ```bash
   grep -nE '^\["<cat>/|^#\["<cat>/' .github/workflows/overlay.toml
   ```
   ```toml
   ["<cat>/<pkg>"]
   source = "github"
   github = "<owner>/<repo>"
   use_latest_release = true
   prefix = "v"
   github_account = "<your GitHub login>"
   ```
   Drop `prefix` when tags have no `v`; see `rules/new-packages.md` for `acct-*` and `virtual` entries.
7. Generate the Manifest after every referenced source and local asset is present.
   ```bash
   pkgdev manifest <cat>/<pkg>
   ```
8. Clean install. Copy the package into a local repository Portage already knows (`eselect repository create local` makes one whose `layout.conf` accepts this overlay's thin Manifest), merge from it, then inspect QA output and installed files and start the program once with a package-specific command. A green CI run does not replace this step.
   ```bash
   cp -r <cat>/<pkg> /var/db/repos/<local>/<cat>/
   emerge --oneshot "=<cat>/<pkg>-<ver>::<local>" 2>&1 | tee /tmp/<pkg>.log
   grep -E 'QA Notice|ERROR' /tmp/<pkg>.log
   qlist -Iv <cat>/<pkg>
   <smoke-command>
   ```
   Where the environment cannot run a real merge, skip this step and step 9, and record in the completion report that the install, QA output, installed files, and smoke test were not checked, and why.
9. Remove the copy from the local repository, test distfiles, logs, and temporary configuration.
10. Rebase, scan the branch range and the package over the network, commit through `pkgdev`, push to the fork, and stop: a new package is not routine, so the human opens the PR.
    ```bash
    git fetch <canonical> && git rebase <canonical>/master
    pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
    pkgcheck scan <cat>/<pkg> --net
    pkgdev commit --scan false --signoff --gpg-sign -m "<cat>/<pkg>: new package, add <ver>"
    git push <fork> <cat>-<pkg>-<ver>
    ```

## Checklist
- [ ] `eix` searches covered the name, project, former name, and forks.
- [ ] The stop conditions in `rules/new-packages.md` were checked before any file was written.
- [ ] Release evidence covers source, redistribution, build system, dependencies, files, and architectures.
- [ ] `::gentoo` eclass and language precedents were read first.
- [ ] Ebuild, `metadata.xml`, Manifest, and `files/` inputs land together.
- [ ] `.github/workflows/overlay.toml` has an alphabetically placed entry or a commented exemption with a concrete reason.
- [ ] Clean install ran and QA output, installed files, and a launch command were inspected, or all four are recorded as skipped with the reason.
- [ ] Test artifacts removed.
- [ ] Commit and package scans passed; commit made through `pkgdev` with sign-off; branch pushed to the fork only.
