---
name: gzh-bump
description: Used for updating one existing gentoo-zh package to a verified upstream release.
---

Read first, in the overlay checkout: `AGENTS.md`, then `.agents/rules/version-bumps.md` and `.agents/rules/pr-text.md`; `.agents/rules/prebuilt-binaries.md` for a prebuilt payload, `.agents/rules/kernels.md` for a kernel package, `.agents/rules/openrc-systemd.md` for a unit or init script, `.agents/rules/desktop-integration.md` for desktop files, `.agents/rules/eclass-discovery.md` for an eclass or EAPI change.

Placeholders: `<cat>/<pkg>`, `<old>`, `<new>`, `<issue>`, `<checkout>`, `<log>` (a scratch file for emerge output), `<subject>` (the commit subject `pkgdev` produced), `<canonical>` (the gentoo-zh remote), `<fork>` (the personal fork remote), `<fork-owner>`, `<fork-repo>`, `<repo>` (the repository name Portage knows this checkout by, from `portageq get_repo_path`), `<smoke-command>` (how this program is started once, when it has an executable entry point; a kernel has none).

Work in a checkout Portage already knows as a repository: the one `portageq get_repo_path / gentoo-zh` prints, or your own clone registered in `/etc/portage/repos.conf` with `location = <checkout>`. A copy of one package directory is not enough; the overlay's eclasses, licenses, and profiles must be visible.

## Steps
1. Preflight: tools, fetch, clean tree, topic branch.
   ```bash
   command -v pkgdev pkgcheck >/dev/null || exit 1   # missing: stop, ask the human to emerge dev-util/pkgdev dev-util/pkgcheck
   git fetch <canonical> && test -z "$(git status --short)" || exit 1   # dirty tree: stop
   git switch -c <cat>-<pkg>-<new> <canonical>/master
   ```
2. Before downloading anything, report each URL and the size upstream states and wait for approval (`AGENTS.md` § Manifest).
3. Read the old ebuild and the package history, then compare the target release as `version-bumps.md` requires; `kernels.md` says which variables a kernel bump copies.
   ```bash
   git log --oneline -- <cat>/<pkg>
   cat <cat>/<pkg>/<pkg>-<old>.ebuild
   ```
4. Decide from `version-bumps.md` § Keeping Old Versions whether `<old>` goes: `git mv` when it goes, `cp` when it stays. Edit only what the release changed, plus any file the rules tie to it (a `virtual/dist-kernel` for a dist-kernel, `overlay.toml` when tracking changes).
   ```bash
   git mv <cat>/<pkg>/<pkg>-<old>.ebuild <cat>/<pkg>/<pkg>-<new>.ebuild   # or cp
   pkgdev manifest <cat>/<pkg>
   ```
5. Clean install from this repository; a green CI run does not replace it. Where the environment cannot run a real merge, skip step 6 and record in the completion report that install, QA output, installed files, and smoke test were not checked, and why.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<new>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
6. Inspect QA output and installed files; start the program once when it has an entry point.
   ```bash
   grep -E 'QA Notice|ERROR' <log>; rm -f <log>
   qlist -v <cat>/<pkg>
   <smoke-command>
   ```
7. Scan the package, stage everything the change touched, and commit through `pkgdev`; add `--gpg-sign` when a key is configured. Take the subject `pkgdev` prints as `<subject>`.
   ```bash
   pkgcheck scan <cat>/<pkg> --net
   pkgdev commit --all --scan false --signoff
   ```
8. Rebase, run the commit scan over the exact branch range, and push the topic branch to the fork only. Any later fix commit repeats this step.
   ```bash
   git fetch <canonical> && git rebase <canonical>/master
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   git push --force-with-lease <fork> <cat>-<pkg>-<new>
   ```
9. Prepare the PR text: `<subject>` as the title, the template with `Closes #<issue>` above its marker as the body (drop that line when no issue exists), and the file list. Show all three to the human and stop.
   ```bash
   git diff --name-only <canonical>/master...HEAD
   { echo "Closes #<issue>"; echo; cat .github/pull_request_template.md; } > <log>
   ```
10. After the human approves that PR: a routine bump (rename plus Manifest, nothing else changed) may open it; anything else, or no `gh`, is opened by the human from the compare link.
    ```bash
    gh pr create --base master --head <fork-owner>:<cat>-<pkg>-<new> --title "<subject>" --body-file <log>
    ```
    ```text
    https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<cat>-<pkg>-<new>
    ```

## Checklist
- [ ] Tools present, tree clean, topic branch from fetched `<canonical>/master`.
- [ ] Downloads approved before the first fetch.
- [ ] Old ebuild, history, and target release compared per `version-bumps.md`; kernel variables per `kernels.md`.
- [ ] Old version kept or dropped per `Keeping Old Versions`; tied files (virtual, `overlay.toml`) updated.
- [ ] Clean install exit status, QA output, `qlist`, and smoke command checked, or recorded as skipped with the reason.
- [ ] Package scan, `pkgdev commit --all --signoff`, rebase, commit-range scan with `--net`, `--force-with-lease` push to the fork only.
- [ ] Title, body, and file list shown and approved before any `gh pr create`; template kept; routine body is only `Closes #N`.
