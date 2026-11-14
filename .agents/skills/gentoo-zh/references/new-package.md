# New package

Run in the overlay checkout after the entry skill’s policy reads. Use a repos.conf entry whose location is the checkout, including its eclasses, licenses, and profiles.

Placeholders: `<canonical>`, `<cat>`, `<eclass>`, `<fork-owner>`, `<fork-repo>`, `<fork>`, `<log>`, `<pkg>`, `<repo>`, `<smoke-command>`, `<upstream>`, `<ver>`. `<ver>` is the target version; `<repo>` is the Portage repository name; `<log>` is the emerge output file; `<smoke-command>` starts an executable entry point, if present.

Use [checks.md](checks.md): language floors for the languages present; prebuilt payloads when shipped; bundled components and LICENSE for release inputs; library bump for dependency minima and, for libraries, reverse dependencies; fork for a ::gentoo base; overlay.toml when changing autobump; install and QA output after each merge; PR reports once a PR exists.

1. If either tool is missing, ask the human to emerge `dev-util/pkgdev dev-util/pkgcheck` and stop. Preflight per `.agents/rules/git-workflow.md`; resolve the canonical remote and clean topic branch before editing.
   ```bash
   command -v pkgdev >/dev/null && command -v pkgcheck >/dev/null || exit 1
   git fetch <canonical> && test -z "$(git status --short)" || exit 1
   git switch -c <cat>-<pkg>-<ver> <canonical>/master
   ```
2. Search configured repositories for the project, former names and forks; apply the stop conditions in `.agents/rules/new-packages.md`. Report each download URL and upstream size and wait for approval (`AGENTS.md` § Manifest).
   ```bash
   eix <pkg>; eix <upstream>
   ```
3. Find current precedents and read the planned eclass per `.agents/rules/eclass-discovery.md` before drafting.
   ```bash
   grep -rl 'inherit.*<eclass>' "$(portageq get_repo_path / gentoo)" --include='*.ebuild'
   cat "$(portageq get_repo_path / gentoo)/eclass/<eclass>.eclass"
   ```
4. Write the ebuild, metadata and tied inputs per `.agents/rules/new-packages.md`; run the applicable sections of [checks.md](checks.md), then generate the Manifest.
   ```bash
   grep -nE '^#?\["<cat>/' .github/workflows/overlay.toml
   pkgdev manifest <cat>/<pkg>
   ```
5. Clean install from this repository per `AGENTS.md` § Ebuild Policy. If a real merge is unavailable, record install, QA, installed files and smoke test as skipped with the reason; skip step 6.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
6. Run checks.md § Install and QA output, reading each saved elog; inspect installed files; execute the entry point when present. Keep the log for the completion report.
   ```bash
   grep -nE '^ \* (QA|WARN|ERROR)|QA Notice' <log>
   qlist -v <cat>/<pkg>
   <smoke-command>
   ```
7. Scan and commit the changed inputs per `.agents/rules/pr-text.md`; use its signing option when available and keep the printed subject.
   ```bash
   pkgcheck scan <cat>/<pkg> --net
   pkgdev commit --all --scan false --signoff
   ```
8. Rebase, scan the explicit branch range and push per `.agents/rules/git-workflow.md`. With or without `gh`, hand the human the subject, template body, file list and compare link per `.agents/rules/pr-text.md`; run checks.md § PR reports once the human opens it.
   ```bash
   git fetch <canonical> && git rebase <canonical>/master
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   git push --force-with-lease <fork> <cat>-<pkg>-<ver>
   git diff --name-only <canonical>/master...HEAD
   cat .github/pull_request_template.md
   ```
   Compare: https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<cat>-<pkg>-<ver>
