# Package PR review

Run in an overlay checkout at the PR head after the entry skill's applicable policy reads. Resolve canonical state through `.agents/rules/git-workflow.md`; Portage's repository entry must resolve to this checkout.

Placeholders: `<canonical>` (canonical remote), `<cat>`, `<findings>` (local report), `<log>` (one merge log per USE state), `<pkg>`, `<previous-log>` (previous version's result), `<repo>` (Portage repository name), `<ver>`.

1. Run [checks.md](checks.md) § Before editing and § PR evidence for the reviewed head. Read history and the previous result before attributing findings under `.agents/rules/qa.md`.
   ```bash
   git diff <canonical>/master...HEAD
   git log --oneline -- <cat>/<pkg>
   cat <previous-log>
   ```
2. Run applicable [checks.md](checks.md) conditions on the exact release inputs. Inspect every changed path per `.agents/rules/qa.md` § Reviewing the change; downloads follow its Manifest section.
   ```bash
   git diff --name-only <canonical>/master...HEAD
   git diff --check <canonical>/master...HEAD
   ```
3. Reproduce failures against the previous result. Configure each affected USE state and upstream tests through Portage's per-package settings under `.agents/rules/qa.md` § Tests; merge once per state with a distinct log. Record unavailable states and unreproduced findings.
   ```bash
   emerge --oneshot "=<cat>/<pkg>-<ver>::<repo>" > <log> 2>&1; echo "emerge exit $?"
   ```
4. Run [checks.md](checks.md) § Install evidence after each merge, the explicit-range scan in `.agents/rules/qa.md` § Scanning, then § PR evidence for the verified head. Write local findings with file:line, evidence, prior-result attribution and gaps under `.agents/rules/qa.md` § Reviewing the change; never post PR comments or submit a review.
   ```bash
   git rev-parse HEAD
   ```
5. Read the findings and give a local verdict. Approval is blocked by unreproduced findings, unsupported dependencies, failed install/QA, untested affected USE states, pending/failed/missing head checks, or unread CI elog for that head. Findings quote the head SHA and every qa/warn/error elog line with package/dependency attribution, or state that the read report has none.
   ```bash
   cat <findings>
   ```
