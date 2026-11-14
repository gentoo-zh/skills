# Version bump

Run in the overlay checkout after the entry skill’s policy reads. Use a repos.conf entry whose location is the checkout, including its eclasses, licenses, and profiles.

Placeholders: `<body>`, `<canonical>`, `<cat>`, `<fork-owner>`, `<fork-repo>`, `<fork>`, `<issue>`, `<log>`, `<new>`, `<old>`, `<pkg>`, `<repo>`, `<smoke-command>`, `<subject>`. `<new>` is the target version; `<repo>` is the Portage repository name; `<log>` is the emerge output file; `<body>` is a separate PR body file; `<smoke-command>` starts an executable entry point, if present.

Use [checks.md](checks.md): language floors for the languages present; prebuilt payloads when shipped; bundled components and LICENSE for release inputs; library bump for dependency minima and, for libraries, reverse dependencies; fork for a ::gentoo base; overlay.toml when changing autobump; install and QA output after each merge; PR reports once a PR exists.

1. If either tool is missing, ask the human to emerge `dev-util/pkgdev dev-util/pkgcheck` and stop. Preflight per `.agents/rules/git-workflow.md`; resolve the canonical remote and clean topic branch before editing.
   ```bash
   command -v pkgdev >/dev/null && command -v pkgcheck >/dev/null || exit 1
   git fetch <canonical> && test -z "$(git status --short)" || exit 1
   git switch -c <cat>-<pkg>-<new> <canonical>/master
   ```
2. Read the old ebuild and history, compare the release per `.agents/rules/version-bumps.md`, then report each download URL and upstream size and wait for approval (`AGENTS.md` § Manifest).
   ```bash
   git log --oneline -- <cat>/<pkg>
   cat <cat>/<pkg>/<pkg>-<old>.ebuild
   ```
3. Choose retention per `.agents/rules/version-bumps.md` § Keeping Old Versions (`cp` instead of `git mv` when retaining). For a kernel, use `.agents/rules/kernels.md` § What each bump checks for the variables to copy. Edit release changes and tied inputs, run the applicable sections of [checks.md](checks.md), then generate the Manifest.
   ```bash
   git mv <cat>/<pkg>/<pkg>-<old>.ebuild <cat>/<pkg>/<pkg>-<new>.ebuild
   pkgdev manifest <cat>/<pkg>
   ```
4. Clean install from this repository per `AGENTS.md` § Ebuild Policy. If a real merge is unavailable, record install, QA, installed files and smoke test as skipped with the reason; skip step 5.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<new>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
5. Run checks.md § Install and QA output, reading each saved elog; inspect installed files; execute the entry point when present. Keep the log for the completion report.
   ```bash
   grep -nE '^ \* (QA|WARN|ERROR)|QA Notice' <log>
   qlist -v <cat>/<pkg>
   <smoke-command>
   ```
6. Scan and commit the changed inputs per `.agents/rules/pr-text.md`; use its signing option when available and keep the printed subject.
   ```bash
   pkgcheck scan <cat>/<pkg> --net
   pkgdev commit --all --scan false --signoff
   ```
7. Rebase and scan the explicit branch range, then push per `.agents/rules/git-workflow.md`. Repeat after a fix commit.
   ```bash
   git fetch <canonical> && git rebase <canonical>/master
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   git push --force-with-lease <fork> <cat>-<pkg>-<new>
   ```
8. Prepare title, template body and file list per `.agents/rules/pr-text.md`, then show them and stop for approval. Choose exactly one body variant below; add required rationale above the marker.
   ```bash
   git diff --name-only <canonical>/master...HEAD
   # With an issue:
   { echo "Closes #<issue>"; echo; cat .github/pull_request_template.md; } > <body>
   # Without an issue:
   cat .github/pull_request_template.md > <body>
   ```
9. After approval, open only a routine bump as defined in `.agents/rules/pr-text.md`; otherwise, or without `gh`, hand the compare link to a human. Once opened, run checks.md § PR reports.
   ```bash
   gh pr create --repo gentoo-zh/overlay --base master --head <fork-owner>:<cat>-<pkg>-<new> --title "<subject>" --body-file <body>
   ```
   Compare: https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<cat>-<pkg>-<new>
