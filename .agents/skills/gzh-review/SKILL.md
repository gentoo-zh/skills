---
name: gzh-review
description: Used for deciding whether a gentoo-zh package change has reproducible evidence and may be approved.
---

Read first, in the overlay checkout: `AGENTS.md`. Then, by what the diff touches: a version bump → `.agents/rules/version-bumps.md`; a new package → `.agents/rules/new-packages.md`; prebuilt binaries → `.agents/rules/prebuilt-binaries.md`; desktop files or Wayland flags → `.agents/rules/desktop-integration.md`; units or init scripts → `.agents/rules/openrc-systemd.md`; commit or PR text → `.agents/rules/pr-text.md`.

Placeholders in angle brackets are filled per task: `<cat>/<pkg>`, `<ver>`, `<canonical>`, `<pr>`.

## Steps
1. Establish what is under review before classifying any finding: package, version, changed files, package history, and the CI result.
   ```bash
   command -v pkgcheck >/dev/null || exit 1   # missing: stop and ask the human to emerge dev-util/pkgcheck
   git diff --name-status <canonical>/master...HEAD -- <cat>/<pkg>
   git log --oneline -- <cat>/<pkg>
   gh pr checks <pr>
   ```
2. Reproduce each claimed install failure with a clean install from a copy in a local repository (as in `gzh-bump` step 5) before calling it introduced; check whether the previous version already failed the same way. Where no real merge is possible, the finding stays unreproduced and step 8 applies.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<local>" 2>&1 | tee /tmp/<pkg>.log
   ```
3. Inspect the installed result and ELF linkage instead of inferring runtime dependencies from ebuild text.
   ```bash
   qlist -Iv <cat>/<pkg>
   scanelf -n <installed-elf>
   ```
4. Count `::gentoo` ebuilds using the same eclass or construct as each proposed one; do not grep for the literal `::gentoo` string.
   ```bash
   gentoo="$(portageq get_repo_path / gentoo)"
   grep -rl '<eclass-or-construct>' "${gentoo}" --include='*.ebuild' | wc -l
   ```
5. Require one consumer-side fact for every direct dependency: a `NEEDED` entry, a build-system request, or a `dlopen` call.
   ```bash
   scanelf -n <installed-elf>
   grep -RInE 'pkg-config|pkg_check_modules|find_package|dependency\(|dlopen' <source-dir>
   ```
6. Exercise every USE state the change touches and any upstream test path.
   ```bash
   USE='<flag>' FEATURES=test emerge --oneshot "=<cat>/<pkg>-<ver>::<local>"
   ```
7. Recheck the final diff, Manifest, `files/` inputs, installed modes, QA output, and both scans.
   ```bash
   git diff --check <canonical>/master...HEAD
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   pkgcheck scan <cat>/<pkg> --net
   ```
8. Do not approve while any finding is unreproduced, a direct atom has no evidence, a clean install or QA check fails, a touched USE state is untested, or the diff carries unrelated hunks or misses a package input.

## Checklist
- [ ] Each finding reproduced by a clean install, or shown pre-existing from history and CI.
- [ ] `qlist -Iv` and `scanelf -n` inspected the installed result.
- [ ] Precedent count taken from the `::gentoo` tree.
- [ ] Every direct dependency has `NEEDED`, build-system, or `dlopen` evidence.
- [ ] Every touched USE state and upstream test path ran.
- [ ] Final diff, Manifest, `files/`, modes, QA output, commit scan, and package scan have no unexplained result.
- [ ] Nothing in step 8 remains.
