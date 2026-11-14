# New package

Run in the overlay checkout after the entry skill's policy reads and `.agents/rules/git-workflow.md` preflight. Portage's repository entry must resolve to this checkout.

Placeholders: `<body>` (PR draft file), `<cat>`, `<eclass>`, `<log>` (merge output), `<pkg>`, `<repo>` (Portage repository name), `<smoke-command>` (applicable runtime test), `<upstream>` (project or former name), `<ver>`.

1. Run [checks.md](checks.md) § Before editing, then search the project and former names per `.agents/rules/new-packages.md`.
   ```bash
   eix <pkg>
   eix <upstream>
   ```
2. Read a matching precedent and the planned eclass per `.agents/rules/eclass-discovery.md`; use the fork check when deriving from ::gentoo. Downloads follow `.agents/rules/qa.md` § Manifest.
   ```bash
   grep -rl 'inherit.*<eclass>' "$(portageq get_repo_path / gentoo)" --include='*.ebuild'
   cat "$(portageq get_repo_path / gentoo)/eclass/<eclass>.eclass"
   ```
3. Draft the package and tracking entry per `.agents/rules/new-packages.md`; run applicable [checks.md](checks.md) conditions before manifesting.
   ```bash
   pkgdev manifest <cat>/<pkg>
   ```
4. Perform the install and USE-state tests selected by `AGENTS.md` § Ebuild Policy and `.agents/rules/qa.md` § Tests; use a separate log for each state. Record unavailable installs and dependent checks as skipped.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
5. After a successful install, run [checks.md](checks.md) § Install evidence and the applicable runtime test.
   ```bash
   <smoke-command>
   ```
6. Inspect intended staging and commit per `.agents/rules/pr-text.md` § Commits; add its signing option when available. Then run [checks.md](checks.md) § PR evidence: publish, then confirm.
   ```bash
   git diff --cached --stat
   git diff --cached
   pkgdev commit --scan false --signoff
   ```
7. Prepare the draft per `.agents/rules/pr-text.md` § Opening the PR, using [checks.md](checks.md) § PR evidence. Hand over its required title, body, files and compare link; read reports once opened.
   ```bash
   cp .github/pull_request_template.md <body>
   ```
