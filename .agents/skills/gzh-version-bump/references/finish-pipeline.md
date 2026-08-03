# Finish Pipeline

Run every applicable gate after the version-specific edit. Treat the live overlay
`AGENTS.md`, workflows, and pull request template as the current contract.

## 1. Confirm Scope

List every changed ebuild and every referenced `files/`, license, metadata, profile, or
workflow file. Preserve unrelated worktree changes. Run each ebuild-specific gate for
every changed ebuild, including a changed live ebuild or retained older revision.

## 2. Run the Local Structural Check

```bash
gzh lint <changed-ebuild>
```

Stop on an error. This command implements a limited set of deterministic repository
checks; it does not replace `pkgcheck`, the Devmanual, current eclass documentation, or
manual semantic review.

## 3. Regenerate and Review the Manifest

Run the wrapper whenever distfiles or an ebuild's fetch inputs changed:

```bash
gzh manifest <changed-ebuild>
```

If Portage's configured `DISTDIR` is not writable, pass a writable directory through the
current command instead of bypassing the wrapper:

```bash
gzh manifest <changed-ebuild> --distdir <writable-directory>
```

Stop on a fetch or manifest failure. For `RESTRICT=fetch`, follow the package's
`pkg_nofetch` instructions. Never place credentials, acceptance tokens, or expiring URLs
in `SRC_URI`.

Review the `Manifest` diff against the expected upstream artifacts. Investigate changed
entries for retained versions, reused distfile names with different content, missing
per-architecture artifacts, and unexpected files. Do not use an arbitrary file-size
threshold as proof of integrity or provenance.

## 4. Run Package QA

```bash
gzh pkgcheck <package-directory> --min-severity error
```

Resolve every error. Record warnings and review whether the change introduced them. This
package scan is offline and does not replace the networked commit scan before a pull
request.

## 5. Build, Test, and Verify Installation

Use the phase runner for focused build diagnosis:

```bash
gzh build-test <changed-ebuild>
```

Then run the install and elog helper:

```bash
gzh verify-install <changed-ebuild> [--logdir <evidence-directory>]
```

`gzh verify-install` derives an exact repository-qualified atom, disables binary package
selection so the changed ebuild is built from source, emerges its dependencies, clears
dependency elog files from an isolated `PORTAGE_LOGDIR`, requests the target with
`PORTAGE_ELOG_CLASSES="qa warn error"`, and fails when the merge fails or the target
produces an elog file. It passes `--oneshot --selective=n` so an installed exact version
is remerged without adding it to the world set. Inspect the saved elog file; do not
substitute console output for the file gate.

The live emerge workflow remains authoritative. It currently tests every selected target
on the amd64 desktop OpenRC and systemd profiles, and also tests arm64-keyworded targets
on the arm64 desktop systemd profile. It checks out the pull request head SHA and fails on
any saved `qa`, `warn`, or `error` elog. A local merge on one profile does not prove the
other workflow legs.

Exercise every USE state affected by the change. Run supported upstream tests with
`FEATURES=test` and declared test dependencies. Verify installed files, modes, launchers,
libraries, notices, and licenses against the current upstream release rather than relying
only on a successful command.

If the environment cannot perform a real merge, record `gzh verify-install` as skipped,
state the exact limitation, and report the untested install and elog behavior as remaining
risk. Do not claim the branch is fully verified. A successful `gzh build-test` does not
replace this gate.

## 6. Review the Change

```bash
gzh diff-ebuild <old-ebuild> <new-ebuild>
git status --short
git diff --stat
git diff
```

Reject unrelated changes, debug output, missing referenced files, unexplained dependency
or license changes, and unintended `Manifest` entries. Confirm that every commit will be
self-contained and leave the package installable.

## 7. Commit Locally

Pass every owned path required by the change to `gzh commit`, including additions,
modifications, and deletions:

```bash
gzh commit <path> [<path> ...]
gzh commit <path> [<path> ...] -m '<subject and optional body>'
```

Use the `pkgdev` English subject unchanged. Use `, drop OLD` only when the old version is
actually removed. Add a body only for reasons the subject cannot express. Follow the live
overlay policy for sign-off, identity, signing, atomic commits, and the 69-character
subject limit. Do not add AI attribution. When the requested body language is Chinese,
load `chinese-skill`; do not translate the `pkgdev` subject.

Chinese PR body example:

```text
因为上游将运行时组件改为共享库，所以新增对应的 `RDEPEND`。
```

Verify the result:

```bash
git show --stat --oneline HEAD
git status --short
```

## 8. Run the Networked Commit Gate

Run the required network check over the explicit canonical merge-base range:

```bash
gzh pkgcheck-commits
```

Review all URL findings. Resolve confirmed dead URLs and redirects. Retry or escalate
`needs_human` results; authentication, rate-limit, and transport failures are not passing
evidence. A package-scoped network scan may help diagnose a result but does not replace
this commit-range gate.

If this gate or any later check exposes a package defect, inspect the concrete evidence,
fix it, rerun every invalidated local gate, and rebuild the sole local commit through
`pkgdev`:

```bash
gzh recommit <every-owned-path-required-by-the-change>
gzh pkgcheck-commits
```

`gzh recommit` requires a clean index and exactly one commit above canonical `master`. It
preserves unrelated unstaged work, uses the existing message unless `-m` is supplied, and
restores the old commit if `pkgdev` fails. Verify the resulting diff, one-commit range, and
commit message, then rerun the network gate. Never use a raw amend or leave a second
package commit. Apply this repair loop to CI failures after inspecting the failing job
log; a CI repair does not permit an extra commit or waive any invalidated gate.

If the branch was already pushed, force-push the rebuilt commit with lease only to the
same personal topic branch. Rebuild the exact pull request title, complete body, and file
list after the repair. If any of them changed, obtain confirmation for that specific
updated pull request before changing its branch or metadata. Stop after the network gate
unless the user explicitly asks to publish or update a pull request.

## 9. Prepare and Create a Pull Request

Before preparing the final pull request text:

1. Fetch the canonical remote and rebase the topic branch onto its current `master`.
2. Re-run every gate invalidated by the rebase.
3. Re-run `gzh pkgcheck-commits` when the rebase changed its commit range or inputs.
4. Build the pull request body above the live template marker. Preserve the template and
   tick only checks that actually ran. Follow the live `AGENTS.md` for the description;
   do not turn broader checklist wording into routine passing-test or tested-architecture
   prose. Do not let an agent attest the human review box.
5. Show the user the exact title, complete body, and file list. Obtain confirmation for
   this specific pull request before running `gh pr create` or `gh pr edit`. A blanket
   approval, batch approval, or draft status does not satisfy this gate.
6. Push only the topic branch to the uniquely identified personal fork. Resolve the fork
   owner from `gh api user`; never push `master` or a canonical remote.
7. Run `gh pr create` with the confirmed title and complete body, including the preserved
   template. Do not change either after confirmation.
8. Watch all CI legs. For a failure, inspect the failing job log and apply the complete
   post-commit repair loop above before pushing a replacement commit.

When live policy explicitly permits a local install skip because the environment cannot
merge, publication may proceed only when the exact install and elog risk is included in
the delivery report. Preserve the live template without inventing a checkbox, and do not
describe the branch as fully verified.

## Failure Limit

Make at most three total attempts at a failed gate. Stop earlier when the identical error
is observed for the second time. Report the gate, exact error, attempts, and evidence
before asking how to continue.

## Delivery Report

Report all of the following after each completed change:

- topic branch;
- canonical remote and fetch result;
- base commit and synchronization status;
- changed files;
- commands run and pass or fail result;
- skipped checks and reasons;
- remaining warnings, risks, and limitations;
- commit ID, when a commit was created;
- pull request state and CI state, when publishing was requested.
