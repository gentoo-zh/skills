---
name: gzh-new-package
description: Used for adding one independently versioned package to the gentoo-zh overlay.
---

Read first, in the overlay checkout: `AGENTS.md`, then `.agents/rules/new-packages.md`, `.agents/rules/eclass-discovery.md`, and `.agents/rules/pr-text.md`; `.agents/rules/prebuilt-binaries.md` for a prebuilt payload, `.agents/rules/desktop-integration.md` for a desktop application, `.agents/rules/openrc-systemd.md` for a service, `.agents/rules/kernels.md` for a kernel package.

Placeholders: `<cat>/<pkg>`, `<ver>`, `<issue>`, `<log>` (a scratch file for emerge output), `<subject>` (the commit subject `pkgdev` produced), `<upstream>` (the project name to search for), `<eclass>`, `<canonical>`, `<fork>`, `<fork-owner>`, `<fork-repo>`, `<repo>` (the repository name Portage knows this checkout by), `<smoke-command>` (how the program is started once, when it has an executable entry point).

Work in a checkout Portage already knows as a repository, as in `gzh-bump`.

## Steps
1. Preflight: tools, fetch, clean tree, topic branch.
   ```bash
   command -v pkgdev pkgcheck >/dev/null || exit 1   # missing: stop, ask the human to emerge dev-util/pkgdev dev-util/pkgcheck
   git fetch <canonical> && test -z "$(git status --short)" || exit 1   # dirty tree: stop
   git switch -c <cat>-<pkg>-<ver> <canonical>/master
   ```
2. Search every configured repository for the name, the upstream project, and former names or forks; stop on any condition listed under "Stop when any of these holds" in `.agents/rules/new-packages.md`.
   ```bash
   eix <pkg>; eix <upstream>
   ```
3. Before downloading anything, report each URL and the size upstream states and wait for approval (`AGENTS.md` § Manifest).
4. Find current `::gentoo` precedents for the planned eclass and read the eclass before writing (`AGENTS.md` § Ebuild Policy, `.agents/rules/eclass-discovery.md`).
   ```bash
   grep -rl 'inherit.*<eclass>' "$(portageq get_repo_path / gentoo)" --include='*.ebuild'
   cat "$(portageq get_repo_path / gentoo)/eclass/<eclass>.eclass"
   ```
5. Write the ebuild, `metadata.xml`, any `files/` inputs, and the `overlay.toml` entry in `category/package` order (`.agents/rules/new-packages.md`), then generate the Manifest.
   ```bash
   grep -nE '^#?\["<cat>/' .github/workflows/overlay.toml
   pkgdev manifest <cat>/<pkg>
   ```
6. Clean install, QA output, installed files, and one start of the program when it has an entry point; a green CI run does not replace it. Where the environment cannot run a real merge, record the four as skipped with the reason.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   grep -E 'QA Notice|ERROR' <log>; rm -f <log>
   qlist -v <cat>/<pkg>
   <smoke-command>
   ```
7. Scan the package, stage everything, and commit through `pkgdev`; add `--gpg-sign` when a key is configured. Take the subject `pkgdev` prints as `<subject>`.
   ```bash
   pkgcheck scan <cat>/<pkg> --net
   pkgdev commit --all --scan false --signoff
   ```
8. Rebase, run the commit scan over the branch range, push to the fork only, and stop: a new package is not routine. Hand the human `<subject>`, the template body, the file list, and the compare link; the human opens the PR.
   ```bash
   git fetch <canonical> && git rebase <canonical>/master
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   git push --force-with-lease <fork> <cat>-<pkg>-<ver>
   git diff --name-only <canonical>/master...HEAD
   ```
   ```text
   https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<cat>-<pkg>-<ver>
   ```

## Checklist
- [ ] Tools present, tree clean, topic branch from fetched `<canonical>/master`.
- [ ] `eix` searches done; stop conditions in `new-packages.md` checked before writing.
- [ ] Downloads approved before the first fetch.
- [ ] `::gentoo` precedent and eclass read before writing.
- [ ] Ebuild, `metadata.xml`, `files/`, Manifest, and `overlay.toml` entry land together.
- [ ] Clean install exit status, QA output, installed files, and launch checked, or recorded as skipped with the reason.
- [ ] Package scan, `pkgdev commit --all --signoff`, rebase, commit-range scan with `--net`, `--force-with-lease` push to the fork only.
- [ ] Subject, body, file list, and compare link handed over; PR opened by the human.
