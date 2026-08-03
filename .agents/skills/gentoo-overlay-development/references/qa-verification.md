# QA, Style, Build, Install, and Elog Verification

Run the exact commands and profiles in the live repository capability contract. Use the
official tools below to understand behavior; do not replace repository-required gates
with guessed equivalents.

## Style and Structural Review

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
   severity.
4. Run repository or commit scans, including network checks, only with the exact
   repository-defined range and remote behavior.
5. Investigate each finding from source and current tool documentation. Do not silence a
   genuine defect or treat a transport failure as a passing URL check.

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

The runner requires a clean Git worktree, keeps its cache outside the overlay, bounds
time and output, and records the exact command, tool version, revision, findings, and
reviewed tool source lock. Add `--net` only when current policy requests network checks.
The supplied adapter and repository identities remain configured but unverified; this
helper does not discover publication policy or replace a repository-required command.

## Build and Tests

1. Exercise every USE state and profile affected by the change.
2. Build with the repository-required toolchain and features. Reject undeclared network
   access and warm-cache dependencies.
3. Run supported upstream tests with declared test inputs. Verify that tests use the
   current build tree rather than an installed or unrelated copy.
4. Keep the largest reliable subset when individual tests require unavailable resources.
   Record the exact skipped behavior and impact. Use a broad test restriction only after
   current evidence proves no reliable subset remains.

## Install and Elog

1. Perform the clean install or merge required by live policy; compilation alone is not
   installation verification.
2. Save `qa`, `warn`, and `error` elog classes to an isolated location when the live
   contract requires this gate. Inspect the saved files rather than relying on console
   output.
3. Verify the final image: files, modes, ownership expectations, symlinks, launchers,
   libraries, services, desktop metadata, notices, and licenses.
4. Investigate every relevant elog entry. Compare an unchanged package under the same
   environment before classifying a warning as environmental.
5. Run a trusted minimal runtime check when supported. Record unavailable hardware,
   sessions, accounts, architectures, or external services as unverified with their
   impact.

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
