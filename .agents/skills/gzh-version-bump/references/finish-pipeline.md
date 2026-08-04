# Finish Pipeline

Run every applicable gate after the version-specific edit. Treat the live overlay
`AGENTS.md`, workflows, and pull request template as the current contract.

## Contents

- [1. Confirm Scope](#1-confirm-scope)
- [2. Run the Local Structural Check](#2-run-the-local-structural-check)
- [3. Regenerate and Review the Manifest](#3-regenerate-and-review-the-manifest)
- [4. Run Package QA](#4-run-package-qa)
- [5. Build, Test, and Verify Installation](#5-build-test-and-verify-installation)
- [6. Review the Change](#6-review-the-change)
- [7. Commit Locally](#7-commit-locally)
- [8. Run the Networked Commit Gate](#8-run-the-networked-commit-gate)
- [Failure Limit](#failure-limit)
- [Delivery Report](#delivery-report)

## 1. Confirm Scope

List every changed ebuild and every referenced `files/`, license, metadata, profile, or
workflow file. Preserve unrelated worktree changes. Run each ebuild-specific gate for
every changed ebuild, including a changed live ebuild or retained older revision.

Run the adapter preflight before the first write and again after any repository-state
ambiguity:

```bash
gzh doctor --operation repository-write-preflight
```

Require every operation capability to be `known`. An `unsupported` or `unknown`
capability is a stop condition, not permission to copy another overlay's workflow.

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

When the release changes one or more distfiles, prepare a reviewed JSON record for every
`DIST` entry and run:

```bash
gzh artifacts <Manifest> --evidence <reviewed-artifacts.json> \
  [--distdir <writable-directory>]
```

The report proves Manifest coverage and any available local digest match. It does not
infer upstream authorship, archive completeness, license terms, or redistribution
permission.

## 4. Run Package QA

```bash
gzh qa <package-directory> --min-severity error
```

Resolve every error. Record warnings and review whether the change introduced them. This
package scan is offline and does not replace the networked commit scan before a pull
request.

For a dependency or USE change, analyze the matching Portage cache entry without sourcing
the ebuild and compare the retained old version with the target:

```bash
gzh deps inspect <changed-ebuild> --use +flag --use -other
gzh deps diff <old-ebuild> <changed-ebuild> --use +flag --use -other
```

List every referenced USE state explicitly. Add `--resolve-providers` only when the
active Portage repository set is the intended provider evidence. Before removing or
narrowing a provider, run `gzh deps reverse <atom>` as a raw potential-consumer index,
then verify each relevant consumer under the required profile. None of these reports
proves ABI compatibility or replaces dependency resolution, build, or install evidence.

## 5. Build, Test, and Verify Installation

Use the phase runner for focused build diagnosis:

```bash
gzh build <changed-ebuild>
```

Then run the install and elog helper:

```bash
gzh merge <changed-ebuild> [--logdir <evidence-directory>]
```

`gzh merge` derives an exact repository-qualified atom, disables binary package
selection so the changed ebuild is built from source, and emerges its dependencies before
the target. It saves `qa`, `warn`, and `error` classes in one isolated
`PORTAGE_LOGDIR` and fails when either emerge fails or either step produces an elog file.
Dependency elog evidence is retained and blocks the target merge. The target command uses
`--oneshot --selective=n` so an installed exact version is remerged without adding it to
the world set. Inspect every saved elog file; do not substitute console output for the
file gate.

The live emerge workflow remains authoritative. It currently tests every selected target
on the amd64 desktop OpenRC and systemd profiles, and also tests arm64-keyworded targets
on the arm64 desktop systemd profile. It checks out the pull request head SHA and fails on
any saved `qa`, `warn`, or `error` elog. A local merge on one profile does not prove the
other workflow legs.

Exercise every USE state affected by the change. Run supported upstream tests with
`FEATURES=test` and declared test dependencies. Verify installed files, modes, launchers,
libraries, notices, and licenses against the current upstream release rather than relying
only on a successful command.

Use the package test driver when its generated matrix is supported and relevant:

```bash
gzh test '=category/package-version::gentoo-zh' -x \
  [--use-combos <count>] [--use-preference default]
```

After a staged or real install, run `gzh image <image-root>`. For a prebuilt package,
also run `gzh binary <installed-object-or-image>` before executing any trusted runtime
smoke test. These static reports complement the merge and saved-elog gate; they do not
replace it.

When the local host cannot perform the required install and an authorized named executor
exists, read [executors.md](executors.md) and run `gzh exec` with the exact atom, commit,
USE state, and complete commit-owned path set. Keep the returned evidence digest in the
work item. Do not reconstruct a remote shell procedure from prose.

If the environment cannot perform a real merge, record `gzh merge` as skipped,
state the exact limitation, and report the untested install and elog behavior as remaining
risk. Do not claim the branch is fully verified. A successful `gzh build` does not
replace this gate.

## 6. Review the Change

```bash
gzh diff <old-ebuild> <new-ebuild>
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
gzh urls
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
gzh urls
```

`gzh recommit` requires a clean index and exactly one commit above canonical `master`. It
preserves unrelated unstaged work, uses the existing message unless `-m` is supplied, and
restores the old commit if `pkgdev` fails. Verify the resulting diff, one-commit range, and
commit message, then rerun the network gate. Never use a raw amend or leave a second
package commit. Apply this repair loop to CI failures after inspecting the failing job
log; a CI repair does not permit an extra commit or waive any invalidated gate.

Stop after the network gate unless the user explicitly asks to publish or update a pull
request. Publication follows the separate conditional procedure linked from `SKILL.md`.

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
