# Conditional checks

Run applicable items in the overlay checkout; record non-applicability. Missing required evidence or command errors stop that check. Grep exit 1 means no match; diff exit 1 means differences. Inspect all pipeline stages. Conditions precede commands; absence of an optional condition is not failure.

Placeholders: `<body>` (completed PR draft), `<bundle>` (Go vendor archive), `<cat>`, `<branch>` (topic branch), `<canonical>` (canonical remote), `<dep>` (minimum dependency atom), `<dep-cp>` (its category/package), `<ebuild>`, `<elf>` (each installed ELF), `<elog>` (each saved elog), `<fork>` (personal fork remote), `<log>` (merge output), `<orig-ebuild>` (current ::gentoo base), `<payload>` (one architecture's extracted/installed tree), `<pkg>`, `<pr>`, `<repo>` (Portage repository name), `<src>` (complete release work tree, including workspace members and vendors), `<symbol>` (candidate library API symbol), `<wheel>` (unpacked wheel).

## Before editing

- [ ] `command -v pkgdev && command -v pkgcheck` — stop if either is missing (`.agents/rules/qa.md`).
- [ ] `git fetch <canonical> && test -z "$(git status --short)" && git switch -c <branch> <canonical>/master` — stop on a failed fetch, unrelated changes or an existing branch (`.agents/rules/git-workflow.md`).
- [ ] `test "$(portageq get_repo_path / <repo>)" -ef "$(git rev-parse --show-toplevel)"` — stop if Portage's repository is another tree: an install would test that tree instead of this checkout (`AGENTS.md` § Ebuild Policy).
- [ ] Before the first payload download: list each URL with its upstream-stated size — stop and wait for approval (`.agents/rules/qa.md` § Manifest).

## Release inputs and ebuilds

- [ ] For changed ebuilds: `cat <ebuild>` — inspect full assignments, phases and branches; stop on global side effects, unapplied PATCHES, inconsistent USE controls, or variable order differing from skel.ebuild (`.agents/rules/ebuild-construction.md`).
- [ ] For patch/asset carry-over: `find <cat>/<pkg> -type f \( -path '*/files/*' -print -o -name '*.ebuild' -exec grep -nH '^' {} \; \)` — compare old/new PATCHES with history; stop if any files/*.patch has no surviving consumer, any PATCHES/FILESDIR entry fails to resolve, or a removed backport lacks release evidence (`.agents/rules/version-bumps.md`).
- [ ] For a ::gentoo fork: `diff -u <orig-ebuild> <ebuild>` — classify every hunk; fork-only flags/patches/options, removed or shortened upstream comments, and dropped workarounds need an upstream release file citation (path:line); stop without it (`.agents/rules/new-packages.md`).
- [ ] For release inputs: `find <src> \( -iname '*LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -name .gitmodules \) -print` — license files found; feeds the component table below (`.agents/rules/ebuild-construction.md`).
- [ ] For bundled notices: `grep -rlI -E 'SPDX-License-Identifier|Permission is hereby granted|disclaims copyright|GNU (Affero |Lesser )?General Public|Unicode, Inc|amalgamation' <src> | sed 's@/[^/]*$@@' | sort | uniq -c` — every directory listed is a candidate component (`.agents/rules/ebuild-construction.md`).
- [ ] For build inputs: `grep -RhoI -E '[[:alnum:]_./${}-]*(ThirdParty|third_party|external|vendor|libs|lib_[[:alnum:]_]+)/[[:alnum:]_./${}-]+\.(c|cc|cpp|cxx|h|hh|hpp|inc)\b' --include=CMakeLists.txt --include='*.cmake' --include=meson.build --include='Makefile*' --include='*.pro' --include='*.pri' --include='*.gn' <src> | sed 's@/[^/]*$@@' | sort | uniq -c` — each directory listed holds vendored sources the build compiles by name, and is a component with or without a notice; also add directories the build adds by glob or `add_subdirectory` (`.agents/rules/version-bumps.md`).
- [ ] For release inputs: `grep -rhoE 'SPDX-License-Identifier:[[:space:]]*[^[:cntrl:]]+' <src> | sed 's@[[:space:]]*\*/.*$@@' | sort | uniq -c` — every license counted must appear in the table below (`.agents/rules/ebuild-construction.md`).
- [ ] Component table, one row per library (a directory can hold several single-file libraries: list each source file it compiles or includes) from the three lists above: `path | compiled by (build file:line, or "not built") | upstream project | license | in LICENSE?` — stop on any built row whose license is undetermined or missing from LICENSE (public domain is listed as `public-domain`), and report it as a finding; a candidate without a row is a skipped check (`.agents/rules/ebuild-construction.md`).
- [ ] If the release has .gitmodules: `if test -d <src>/.git; then git -C <src> submodule status --recursive; elif test -f <src>/.gitmodules; then cat <src>/.gitmodules; fi` — stop on unpopulated or mismatched submodules, or one missing from the component table (`.agents/rules/version-bumps.md`).

## Language and build conditions

- [ ] For Go: `test -n "$(find <src> -name go.mod)" && find <src> -name go.mod -exec grep -nHE '^go[[:space:]]|dev-lang/go|GO_OPTIONAL' <ebuild> "$(portageq get_repo_path / gentoo)/eclass/go-module.eclass" {} +` — compare every module's directive to the effective floor; stop if no go.mod is found or the floor is below the highest used module minimum (`.agents/rules/version-bumps.md`, `.agents/rules/eclass-discovery.md`).
- [ ] For Cargo: `test -n "$(find <src> -name Cargo.toml)" && find <src> -name Cargo.toml -exec grep -nHE '^\[|rust-version|RUST_MIN_VER' <ebuild> {} +` — `rust-version.workspace = true` takes the root `[workspace.package]` value; compare the highest rust-version across workspace members AND vendored crates; stop if no Cargo.toml is found, on a missing or lower RUST_MIN_VER, or on unresolved inheritance (`.agents/rules/version-bumps.md`).
- [ ] For Cargo installing ELF: `scanelf -R <payload>` — compare every installed ELF path with QA_FLAGS_IGNORED in the ebuild; stop if any is uncovered (`.agents/rules/ebuild-construction.md`).
- [ ] For Go build flags: `grep -Rn -- '-ldflags' <ebuild> <src>` — follow continued/constructed ldflags; stop on effective -s/-w or lost -X assignments (`.agents/rules/ebuild-construction.md`).
- [ ] For a Go vendor bundle: `tar -tf <bundle>` — stop unless vendor files unpack under the release's `${P}/vendor/` and map to `${S}/vendor`; top-level ./vendor/ fails (`.agents/rules/new-packages.md`).
- [ ] For Python bumps (obtain wheel metadata first): `find <wheel> -name METADATA -exec grep -nH '^Requires-Dist:' {} +` — compare non-extra requirements, including environment markers, with full RDEPEND/PDEPEND; stop on missing core requirements or unavailable required metadata (`.agents/rules/dependencies.md`).
- [ ] For prebuilt dotnet: `grep -RnE '"tfm"|"frameworks?"|"name"|"version"|DOTNET_PKG_COMPAT' --include='*.runtimeconfig.json' --include='*.ebuild' <payload> <ebuild>` — stop on absent framework evidence or a compatibility mismatch (`.agents/rules/prebuilt-binaries.md`).

## Dependencies and architectures

- [ ] For build/runtime dependency changes: `grep -RnE 'find_package|pkg_check_modules|dependency\(|dlopen|requires|install_requires|exec' --include=CMakeLists.txt --include='*.cmake' --include=meson.build --include=configure.ac --include=pyproject.toml --include=setup.cfg --include='*.sh' <src>` — compare direct linkage, commands and minima with atoms; stop on unsupported atoms or omitted requirements (`.agents/rules/dependencies.md`).
- [ ] For each minimum: `portageq best_visible / ebuild '<dep>'` — stop on no satisfying visible version; preserve the required floor and apply the draft route (`.agents/rules/version-bumps.md`).
- [ ] Before retaining a provider: `grep -nF '<dep-cp>' profiles/package.mask "$(portageq get_repo_path / gentoo)/profiles/package.mask"` — read matching removal entries and stop on a soon-removed provider (`.agents/rules/dependencies.md`).
- [ ] For libraries, removals or arch changes: `grep -rl '<cat>/<pkg>' --include='*.ebuild' .` — inspect reverse dependencies, pins and virtual providers on each retained arch; stop on unresolved ABI/keyword edges or missing consumer verification (`AGENTS.md` § Ebuild Policy, `.agents/rules/version-bumps.md`).
- [ ] For dependency/keyword graphs: `pkgcheck scan <cat>/<pkg> --net` — inspect DependencyMissingKeywords and each arch's provider graph, including affected reverse dependencies; stop on unresolved findings (`AGENTS.md` § Ebuild Policy, `.agents/rules/qa.md`).

## Installed binaries, per architecture

- [ ] For prebuilt payloads: `scanelf -R -F '%F %a %M %i %n %S %r' <payload>` — account for each object's machine, interpreter, NEEDED, SONAME, RPATH and installed location; stop on unresolved NEEDED, unsupported layout or unjustified suppression (`.agents/rules/prebuilt-binaries.md`).
- [ ] For dynamic ELF libc/libstdc++ floors: `objdump -T <elf> | grep -oE '(GLIBC|GLIBCXX)_[0-9.]+' | sort -Vu; grep -n REQUIRED_USE <ebuild>` — compare each arch's maximum per namespace with RDEPEND; stop on an unmet floor or elibc_glibc in REQUIRED_USE; explain empty output from ELF evidence (e.g. static-PIE) (`.agents/rules/prebuilt-binaries.md`).
- [ ] For ISA-dependent binaries: `grep -nE 'cpu_flags|REQUIRED_USE|IUSE' <ebuild>` — compare the full assignments with the measured upstream ISA baseline; stop if the amd64 cpu_flags gate is absent or insufficient (`.agents/rules/ebuild-construction.md`).
- [ ] For possible dlopen dependencies: `strings -n 6 <elf> | grep -E '^lib[A-Za-z0-9_+-]+\.so(\.[0-9]+)*$'` — candidates only; stop before declaring dependencies without symbol evidence from the next item (`.agents/rules/dependencies.md`).
- [ ] If a candidate was found: `strings <elf> | grep -F -c '<symbol>'` — stop on zero symbol references; a library-name table alone is not evidence (`.agents/rules/dependencies.md`).

## Tracking and integration changes

- [ ] For tracking changes: `grep -n -A18 -B3 '^#*\["<cat>/<pkg>"\]' .github/workflows/overlay.toml` — read the whole table if longer; stop on missing/misordered entries, wrong bundle fields or unresolved autobump-off reasons (`.agents/rules/new-packages.md`).
- [ ] For changed desktop/service assets: `find <payload> -type f \( -name '*.desktop' -o -name '*.service' -o -name '*.initd*' -o -name '*.confd*' \) -exec cat {} +` — compare identity/ExecStart/install paths and service mappings with runtime evidence; stop on mismatch or missing required OpenRC support (`.agents/rules/desktop-integration.md`, `.agents/rules/openrc-systemd.md`).

## Install evidence

- [ ] After each merge: `grep -niE '(^|[^[:alpha:]])(qa|warn|error)([^[:alpha:]]|$)' <log>` — locate QA/warn/error, then read saved elog; stop on missing merge status or elog, and attribute dependency notices separately (`.agents/rules/qa.md`).
- [ ] With saved elog: `cat <elog>` — stop on any qa/warn/error notice awaiting reproduction and attribution; report every such line or explicitly none (`.agents/rules/qa.md`).
- [ ] After successful install: `qlist -v <cat>/<pkg>` — inspect listed files and compare paths/modes/license notices with the release and phase intent; stop on missing/unintended output (`.agents/rules/qa.md`).

## PR evidence

- [ ] When preparing a PR: `cat <body>` — replace the template's first comment with the reason; put any Closes #N on its own line, fill Tested: with actual execution or "not run: reason", preserve both template checkboxes, and tick only true statements after the explicit-range scan in `.agents/rules/qa.md` § Scanning; stop on inaccurate/incomplete text (`.agents/rules/pr-text.md`).
- [ ] Before publishing: `git fetch <canonical> && git rebase <canonical>/master && pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net && git push --force-with-lease <fork> <branch>` — stop on a conflict or scan finding; repeat after each fix commit (`.agents/rules/git-workflow.md`, `.agents/rules/qa.md` § Scanning).
- [ ] Before `gh pr create`: `git diff --name-only <canonical>/master...HEAD` — show the title, body and this file list, then stop until the human confirms this PR (`.agents/rules/pr-text.md` § Opening the PR).
- [ ] With a PR and gh: `gh pr view <pr> --repo gentoo-zh/overlay --json headRefOid,title,body,comments` — match the local head and read that SHA's pkgcheck/elog reports; stop on stale/missing/unread reports or unresolved findings (`.agents/rules/qa.md`).
- [ ] With a PR and gh: `gh pr checks <pr> --repo gentoo-zh/overlay` — stop on pending/failed/missing required checks; without gh obtain equivalent head/report evidence from the human (`.agents/rules/qa.md`).
