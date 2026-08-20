# QA, Style, Build, Install, and Elog Verification

Run the exact commands and profiles in the live repository capability contract. Use the
official tools below to understand behavior; do not replace repository-required gates
with guessed equivalents.

## Contents

- [Style and Structural Review](#style-and-structural-review)
- [Manifest and Package QA](#manifest-and-package-qa)
- [Triage a Finding](#triage-a-finding)
- [Review the Change](#review-the-change)
- [Build and Tests](#build-and-tests)
- [Install and Elog](#install-and-elog)
- [Completion Rule](#completion-rule)

## Style and Structural Review

- Apply this review to every changed ebuild, including a copy-only bump or QA-only fix.
- Preserve clear package-local style where it does not conflict with current Gentoo
  semantics or repository policy.
- Keep ebuild code direct and phase-appropriate. Avoid global-scope side effects,
  undeclared inputs, forced user flags, broad QA suppressions, and comments that merely
  restate assignments.
- Verify EAPI compatibility, eclass use, metadata, USE descriptions, dependency syntax,
  referenced files, patch application, and installed-path helpers.
- Run the repository's structural lint command and resolve errors before broader tests.

## Manifest and Package QA

1. Regenerate the Manifest through the repository-approved command whenever fetch inputs
   or distfile content change.
2. Review changed entries against the expected filenames, sizes, digests, architectures,
   and retained ebuilds. A valid digest proves integrity, not origin or permission.
3. Run the repository's package-scoped `pkgcheck` gate with its required scope and
   severity. Iterate with the narrowest relevant package check and rerun the command that
   exposed each failure.
4. Run repository or commit scans, including network checks, only with the exact
   repository-defined range and remote behavior.
5. Investigate each finding from source and current tool documentation. Do not silence a
   genuine defect or treat a transport failure as a passing URL check. Retain a notice only
   when it is a documented false positive or unavoidable, recording its rationale and
   remaining risk, and never rewrite working behavior merely to satisfy a checker.

When a portable, read-only `pkgcheck` report is useful and live policy has supplied the
identity and scope, run from this skill directory:

```bash
python3 scripts/qa_runner.py \
  --repository /absolute/path/to/overlay \
  --adapter-id <adapter-id> \
  --canonical-repository <owner/repository> \
  --target category/package \
  --output /tmp/pkgcheck-report.json
```

The runner requires a clean Git worktree, keeps its cache outside the overlay, bounds time
and output, and records the command, tool version, revision, findings, and source lock.
Add `--net` only when current policy requests it. Supplied identities remain unverified;
this helper does not discover policy or replace a repository-required command.

When the repository-approved wrapper is `gzh qa`, repeat `--profile` and `--arch` to pass
an explicit pkgcheck scope without constructing a comma expression manually:

```bash
gzh qa category/package --profile stable --arch amd64
```

These selectors constrain pkgcheck only. They do not establish a build, install, runtime,
or architecture test result.

## Triage a Finding

- Before treating a finding as introduced by the change, read the package history and the
  previous version's result. Report a finding that predates the change as pre-existing.
- When the resolved capability contract records a QA bot that reports on the review record,
  read that report too: it names the commit and packages it scanned, so read it instead of
  guessing what CI saw.
- Do not repeat a check that already passed on the same tree: no rebuilding what already
  built clean, no rerunning a scan that already passed. A rebase or a new commit makes an
  earlier commit scan stale and requires a rerun, and staleness never excuses a gate live
  policy requires.
- A network result is evidence about the running host only. An edge that returns 403 here
  may serve the same URL elsewhere, so re-check a flagged URL from a second network before
  calling it dead. CI that runs its QA scan without network checks proves nothing about
  those keywords.

## Build and Tests

Apply live build gates to every covered surface. Add USE matrices and upstream tests only when relevant:

1. Exercise every USE state and profile affected by the change.
2. Build with the repository-required toolchain and features. Reject undeclared network
   access and warm-cache dependencies.
3. Run supported upstream tests with declared test inputs. Verify that tests use the
   current build tree rather than an installed or unrelated copy.
   Prefer the repository-approved package test driver when it can enumerate profiles and
   USE combinations. Record the exact profile, USE state, test feature, command, and tool
   version; a default build does not prove that the test phase ran.
4. Gate a network-dependent test suite with `PROPERTIES="test? ( test_network )"` only
   when the package has `IUSE=test`; otherwise use `PROPERTIES="test_network"`.
5. Keep the largest reliable subset when individual tests require unavailable resources.
   Record the exact skipped behavior and impact. Use a broad test restriction only after
   current evidence proves no reliable subset remains.

## Install and Elog

1. Perform the clean install or merge required by live policy; compilation alone is not
   installation verification.
2. Save `qa`, `warn`, and `error` elog classes to an isolated location when the live
   contract requires this gate. Inspect the saved files rather than relying on console
   output.
3. When installed content or layout is under review, verify files, modes, ownership,
   symlinks, launchers, libraries, services, desktop metadata, notices, and licenses.
4. Investigate every relevant elog entry. Compare an unchanged package under the same
   environment before classifying a warning as environmental.
5. Run a trusted minimal runtime check when supported. Record unavailable hardware,
   sessions, accounts, architectures, or external services as unverified with their
   impact.

## Review the Change

- Stage only the owned paths, then review the staged diff and its diffstat before creating a
  commit. Reject unrelated hunks, debug output, missing `files/` assets, and unintended
  `Manifest` entries.
- Verify every patch, substitution, generator, and manual or glob install against the
  intended release source and the final installed files, modes, and license notices, not
  against command exit status alone.

## Completion Rule

Review the complete diff, status, and all gate output. A required skipped or failed gate
remains a blocker whenever live policy makes later actions depend on it. CI may confirm
the submitted revision, but it does not retroactively prove a skipped local requirement.

Official tool references:

- [pkgcheck](https://pkgcore.github.io/pkgcheck/man/pkgcheck.html)
- [pkgdev](https://pkgcore.github.io/pkgdev/man/pkgdev.html)
- [emerge](https://dev.gentoo.org/~zmedico/portage/doc/man/emerge.1.html)
- [Gentoo QA Policy Guide](https://projects.gentoo.org/qa/policy-guide/)
- [Gentoo test guidance](https://devmanual.gentoo.org/ebuild-writing/functions/src_test/index.html)
