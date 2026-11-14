# Package PR review

Run in a clean overlay checkout at the PR head after the entry skill’s policy reads. Use a repos.conf entry whose location is the checkout.

Placeholders: `<cat>`, `<pkg>`, `<ver>` (target version), `<log>` (a separate emerge output file for each USE state), `<previous-log>` (previous version's saved result), `<canonical>` (canonical remote), `<repo>` (Portage repository name), `<pr>` (PR number), `<installed-elf>`, `<eclass>`, `<findings>` (local report).

Use [checks.md](checks.md): language floors for languages present; prebuilt payloads when shipped; bundled components and LICENSE for release inputs; library bump for dependency minima and, for libraries, reverse dependencies; fork for a ::gentoo base; overlay.toml when changed; install and QA output for each USE state; PR reports for the verified head.

1. Check tools, the head, history, previous version's result, and pkgcheck bot report before attributing findings (`AGENTS.md` § Commands and QA). Missing pkgdev or pkgcheck: ask the human to emerge `dev-util/pkgdev dev-util/pkgcheck`. Without `gh`, obtain the head and reports from the human; missing evidence stops review. Follow `.agents/rules/git-workflow.md` for the canonical remote.
   ```bash
   command -v pkgdev >/dev/null && command -v pkgcheck >/dev/null || exit 1
   test -z "$(git status --short)" || exit 1
   git fetch <canonical>
   test "$(git rev-parse HEAD)" = "$(gh pr view <pr> --repo gentoo-zh/overlay --json headRefOid --jq .headRefOid)" || exit 1
   git diff <canonical>/master...HEAD
   git log --oneline -- <cat>/<pkg>
   cat <previous-log>
   gh pr view <pr> --repo gentoo-zh/overlay --json title,body,comments
   ```
2. Run applicable checks against this head's exact release inputs. Obtain download approval per `AGENTS.md` § Manifest. Count current precedents for each eclass or construct and assess dependencies from the check evidence.
   ```bash
   grep -rl '<eclass>' "$(portageq get_repo_path / gentoo)" --include='*.ebuild' | wc -l
   ```
3. Reproduce claimed failures and compare the prior result. For each affected USE state, set that package's flags in `package.use` and run this one merge, choosing a distinct `<log>`. Where upstream tests exist, enable `FEATURES=test` through Portage's per-package `package.env` configuration per `AGENTS.md` § Tests. Record unavailable merges and affected states as untested, and findings as unreproduced.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
4. After each merge, run checks.md § Install and QA output and read every saved elog. Inspect files and linkage; use `.agents/rules/prebuilt-binaries.md` for blobs. Skip installed-file checks if the merge was unavailable and record why.
   ```bash
   qlist -v <cat>/<pkg>
   scanelf -n <installed-elf>
   grep -nE '^ \* (QA|WARN|ERROR)|QA Notice' <log>
   ```
5. Inspect Manifest, assets, modes and QA, then run both scans for the verified head.
   ```bash
   git diff --check <canonical>/master...HEAD
   pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net
   pkgcheck scan <cat>/<pkg> --net
   ```
6. Write local findings with file:line, evidence and gaps per `AGENTS.md` § Reviewing the change. Cover every changed path and checks.md § PR reports; never post PR comments or submit a review.
   ```bash
   git diff --name-only <canonical>/master...HEAD
   ```
7. Read the completed findings and give a local verdict. Do not approve while any finding is unreproduced, any dependency lacks evidence, install or QA failed, or any affected USE state is untested.
   ```bash
   cat <findings>
   ```
