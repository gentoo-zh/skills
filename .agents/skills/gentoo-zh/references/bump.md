# Version bump

Run in the overlay checkout after the entry skill's policy reads and `.agents/rules/git-workflow.md` preflight. Portage's repository entry must resolve to this checkout.

Placeholders: `<body>` (PR draft file), `<cat>`, `<log>` (merge output), `<new>`, `<old>`, `<pkg>`, `<repo>` (Portage repository name), `<smoke-command>` (applicable runtime test).

1. Read history and the old ebuild; compare release evidence under `.agents/rules/version-bumps.md`. Use [checks.md](checks.md) § Before editing, then select its release and language checks. Downloads follow `.agents/rules/qa.md` § Manifest.
   ```bash
   git log --oneline -- <cat>/<pkg>
   cat <cat>/<pkg>/<pkg>-<old>.ebuild
   ```
2. Apply `.agents/rules/version-bumps.md` § Keeping Old Versions; replace `git mv` with `cp` when retaining. Edit the new ebuild and tied inputs, then run applicable checks against the release.
   ```bash
   git mv <cat>/<pkg>/<pkg>-<old>.ebuild <cat>/<pkg>/<pkg>-<new>.ebuild
   pkgdev manifest <cat>/<pkg>
   ```
3. Perform the install and USE-state tests selected by `AGENTS.md` § Ebuild Policy and `.agents/rules/qa.md` § Tests; use a separate log for each state. Record unavailable installs and their dependent checks as skipped.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<new>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
4. After a successful install, run [checks.md](checks.md) § Install evidence and the applicable runtime test; record the result.
   ```bash
   <smoke-command>
   ```
5. Inspect intended staging and commit per `.agents/rules/pr-text.md` § Commits; add its signing option when available. Then run [checks.md](checks.md) § PR evidence: publish, then confirm.
   ```bash
   git diff --cached --stat
   git diff --cached
   pkgdev commit --scan false --signoff
   ```
6. Prepare the draft per `.agents/rules/pr-text.md` § Opening the PR, using [checks.md](checks.md) § PR evidence. Follow that rule's publication path; read the head's reports once opened.
   ```bash
   cp .github/pull_request_template.md <body>
   ```
