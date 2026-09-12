---
name: gzh-review
description: Used for deciding whether a gentoo-zh package change has reproducible evidence and may be approved.
---

Read first, in the overlay checkout: `AGENTS.md` and `.agents/rules/pr-text.md`. Then, by what the diff touches: `.agents/rules/version-bumps.md` for a bump, `.agents/rules/new-packages.md` for a new package or `overlay.toml`, `.agents/rules/prebuilt-binaries.md` for a prebuilt payload, `.agents/rules/desktop-integration.md` for desktop files or Wayland flags, `.agents/rules/openrc-systemd.md` for units or init scripts, `.agents/rules/eclass-discovery.md` for an eclass or EAPI change, `.agents/rules/kernels.md` for a kernel package.

Placeholders: `<pr>`, `<cat>/<pkg>`, `<ver>`, `<log>` (a scratch file for emerge output), `<canonical>`, `<repo>` (the repository name Portage knows this checkout by), `<installed-elf>`, `<source-dir>`, `<eclass>`, `<flag>`.

## Steps
1. Preflight, then establish what is under review: the whole diff, its file list, the package history, the PR title and body, and the CI result.
   ```bash
   command -v pkgdev pkgcheck >/dev/null || exit 1   # missing: stop, ask the human to emerge dev-util/pkgdev dev-util/pkgcheck
   git fetch <canonical>
   git diff --name-only <canonical>/master...HEAD
   git diff <canonical>/master...HEAD
   git log --oneline -- <cat>/<pkg>
   gh pr view <pr> --json title,body; gh pr checks <pr>     # without gh: read the PR page
   ```
2. Reproduce each claimed install failure with a clean install before calling it introduced; check whether the previous version already failed the same way. Downloads need approval first (`AGENTS.md` § Manifest). Where no real merge is possible, the finding stays unreproduced and step 7 applies.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
3. Inspect the installed result and ELF linkage instead of inferring runtime dependencies from ebuild text.
   ```bash
   qlist -v <cat>/<pkg>
   scanelf -n <installed-elf>
   ```
4. Count `::gentoo` ebuilds using the same eclass or construct as each proposed one.
   ```bash
   grep -rl '<eclass>' "$(portageq get_repo_path / gentoo)" --include='*.ebuild' | wc -l
   ```
5. Check every direct dependency against the evidence `AGENTS.md` § Dependencies and Revisions accepts.
   ```bash
   scanelf -n <installed-elf>
   grep -RInE 'pkg-config|pkg_check_modules|find_package|dependency\(|dlopen' <source-dir>
   ```
6. Exercise every USE state the change touches and any upstream test path, then recheck the diff, Manifest, `files/` inputs, installed modes, QA output, and both scans.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>"   # once per USE state the change touches, set in package.use; FEATURES=test for upstream tests
   git diff --check <canonical>/master...HEAD
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   pkgcheck scan <cat>/<pkg> --net
   rm -f <log>
   ```
7. Write the verdict. Do not approve while any finding is unreproduced, a direct atom lacks evidence, a clean install or QA check fails, a touched USE state is untested, or the diff carries unrelated hunks or misses a package input.
   ```bash
   git diff --name-only <canonical>/master...HEAD   # every path here has a finding or a pass in the verdict
   ```

## Checklist
- [ ] Whole diff, file list, history, PR text, and CI read before classifying anything.
- [ ] Each finding reproduced by a clean install, or shown pre-existing.
- [ ] `qlist -v` and `scanelf -n` inspected the installed result.
- [ ] Precedent count taken from the `::gentoo` tree.
- [ ] Every direct dependency has evidence `AGENTS.md` accepts.
- [ ] Touched USE states and upstream tests ran; diff, Manifest, `files/`, modes, QA output, commit scan, and package scan clean.
- [ ] Nothing in step 7 remains.
