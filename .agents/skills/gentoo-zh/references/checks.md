# Conditional checks

Run only applicable sections in the overlay checkout; record why others do not apply. Command errors or missing required evidence stop a check. For grep, exit 1 means no match, not a command error. Inspect each pipeline stage; the final status alone is not evidence.

Placeholders: `<cat>`, `<dep-cat>`, `<dep-pkg>`, `<dep>`, `<ebuild>`, `<elf>`, `<elog>`, `<log>`, `<manifest>`, `<orig-ver>`, `<orig>`, `<payload>`, `<pkg>`, `<pr>`, `<src>`. `<src>` is the exact release source including vendors; `<manifest>` is each built Cargo.toml with inheritance resolved; `<payload>` is one extracted architecture payload; `<elf>` is each ELF in it; `<dep>` includes the upstream minimum; `<dep-cat>` and `<dep-pkg>` identify an overlay dependency; `<orig>` and `<orig-ver>` identify the ::gentoo base; `<ebuild>` is the target ebuild; `<log>` is emerge output; `<elog>` is each saved elog; `<pr>` is the PR number.

Repeat prebuilt checks for every shipped arch and ELF. Within language floors, run only the language in use. Run the library section’s minimum checks for all dependencies and its reverse-dependency search for library bumps. PR reports require an existing PR and `gh`; without it, obtain the same evidence from a human.

## Go/Rust/dotnet floors

- [ ] `find <src> -name go.mod -exec grep -nH '^go[[:space:]]' {} +` — stop if any module's directive is unaccounted for in the floor comparison below.
- [ ] `grep -nE 'dev-lang/go|GO_OPTIONAL' "$(portageq get_repo_path / gentoo)/eclass/go-module.eclass" <ebuild>` — compare the eclass floor and ebuild override with those directives; stop if the effective floor is lower (see `.agents/rules/version-bumps.md`).
- [ ] `grep -nE 'rust-version|RUST_MIN_VER' <manifest> <ebuild> "$(portageq get_repo_path / gentoo)/eclass/rust.eclass"` — stop if the effective Rust floor is lower or a workspace-inherited value remains unresolved.
- [ ] `grep -RnE '"tfm"|DOTNET_PKG_COMPAT' --include='*.runtimeconfig.json' --include='*.ebuild' <payload> <ebuild>` — stop if any runtimeconfig framework differs from the selected slot, or no framework was found.

## Prebuilt payloads

- [ ] `scanelf -nR <payload>` — stop if any arch's NEEDED entry lacks a verified bundled provider or system dependency; compare with `.agents/rules/prebuilt-binaries.md`.
- [ ] `objdump -T <elf> | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1` — stop if the highest symbol version exceeds the guaranteed libc floor without a matching dependency; explain empty output from the ELF evidence.
- [ ] `grep -nE 'RDEPEND|DEPEND|sys-libs/glibc' <ebuild>` — compare the full dependency blocks in the ebuild dump below; stop if their floors or arch conditions miss a measured requirement.
- [ ] `grep -n REQUIRED_USE <ebuild>` — read the full assignment in the ebuild dump below; stop if it contains `elibc_glibc`; no match means REQUIRED_USE is absent.

## Bundled components and LICENSE

- [ ] `find <src> \( -iname '*LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -name sqlite3.c -o -name sqlite_orm.h -o -iname '*icu*' \) -print` — stop if any bundled component or amalgamation lacks a license assessment.
- [ ] `grep -rhoE 'SPDX-License-Identifier: *[A-Za-z0-9.+-]+( (AND|OR|WITH) [A-Za-z0-9.+-]+)*' <src> | sort | uniq -c` — list licenses declared in file headers, which catches amalgamations and vendored headers that ship no LICENSE file; stop if one is missing from the assessment.
- [ ] `if git -C <src> rev-parse --show-toplevel 2>/dev/null; then git -C <src> submodule status --recursive; fi` — for a Git source checkout (confirm the printed root is the release checkout, not its parent), stop on unpopulated, mismatched or conflicted submodules, or an unassessed bundled license.
- [ ] `if test -f <src>/.gitmodules; then cat <src>/.gitmodules; fi` — for an archive declaring submodules, stop if a listed bundled component is absent from the source/license assessment.
- [ ] `cat <ebuild>` — map every bundled component found above to a license name under `$(portageq get_repo_path / gentoo)/licenses/` from its own notice or header; stop if `LICENSE` omits one, and report a component whose license you could not determine as a finding, not as pending evidence.

## Library bump

- [ ] `grep -RnE 'find_package|pkg_check_modules|dependency\(|dlopen|requires|install_requires|rust-version|^go ' --include=CMakeLists.txt --include='*.cmake' --include=meson.build --include=configure.ac --include=pyproject.toml --include=setup.cfg --include=Cargo.toml --include=go.mod --include=package.json <src>` — compare upstream dependency minima with the ebuild; stop on an unexplained lower bound or missing direct dependency (`AGENTS.md` § Dependencies and Revisions).
- [ ] `portageq best_visible / ebuild '<dep>'` — repeat for each declared minimum, including compiler floors; stop on no satisfying visible version.
- [ ] `find <dep-cat>/<dep-pkg> -maxdepth 1 -name '*.ebuild' -print` — when relying on an overlay version, stop if that version is absent or does not satisfy the upstream minimum.
- [ ] `grep -rl '<cat>/<pkg>' --include='*.ebuild' .` — for a library bump, inspect every in-overlay reverse dependency; stop on unresolved pins, ABI changes or missing consumer verification; no matches means none found by this literal search.

## Fork of a ::gentoo package

- [ ] `diff -u "$(portageq get_repo_path / gentoo)"/<cat>/<orig>/<orig>-<orig-ver>.ebuild <ebuild>` — exit 1 means differences; classify every hunk. Stop on any of these unless an upstream file of this release requires it (cite path:line): a USE flag, patch, flag or option the fork adds that the current ::gentoo ebuild lacks; a ::gentoo comment the fork removed or shortened; a ::gentoo workaround the fork dropped.
- [ ] `grep -nE '^(IUSE|PATCHES)|FILESDIR|append-|filter-' <ebuild> "$(portageq get_repo_path / gentoo)"/<cat>/<orig>/<orig>-<orig-ver>.ebuild` — list fork-only flags and patches side by side; each needs the citation above.

## overlay.toml autobump

- [ ] `grep -n -B3 '^\["<cat>/<pkg>"\]' .github/workflows/overlay.toml` — before setting `autobump = true`, stop if preceding context contains `# autobump off:`; no table means inspect/create it via `.agents/rules/new-packages.md` first.

## Install and QA output

- [ ] `grep -nE '^ \* (QA|WARN|ERROR)|QA Notice' <log>` — locate notices, then read the saved elog below; no matches are not proof of clean QA. Stop if the merge result or saved elog is missing.
- [ ] `cat <elog>` — read each saved elog named by the merge or collected per `AGENTS.md` § Ebuild Policy, including qa/warn/error sections. Stop on any such notice pending reproduction and attribution; report dependency elog separately under that policy.
- [ ] `qlist -v <cat>/<pkg>` — compare installed paths with the intended release; stop on missing or unintended files, or a failed clean install.

## PR reports

- [ ] `gh pr view <pr> --repo gentoo-zh/overlay --json headRefOid` — record the SHA; stop if it differs from the revision being checked.
- [ ] `gh pr checks <pr> --repo gentoo-zh/overlay` — stop on failed, pending or missing required checks.
- [ ] `gh pr view <pr> --repo gentoo-zh/overlay --json comments` — read the pkgcheck and elog reports for that SHA; stop on missing/stale reports or unresolved findings, attributing dependency elog separately per `AGENTS.md`.
