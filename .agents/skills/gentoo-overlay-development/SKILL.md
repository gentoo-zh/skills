---
name: gentoo-overlay-development
description: Develop and verify independently maintained Gentoo overlays. Use for existing or new packages, version bumps, dependency or USE corrections, EAPI, phase, source or prebuilt artifact, patch, license, Manifest, keyword, move, removal, metadata, profile, or eclass changes, package QA, builds, tests, install verification, and elog review when live repository evidence establishes every required capability and no reviewed repository-specific skill already covers the request. Do not publish without an explicit live procedure.
---

# Develop Gentoo Overlay Packages

Apply portable Gentoo semantics without importing another repository's workflow. Stop a
write when the target repository does not define the required capability.

## Establish Authority

1. Read the complete live repository policy, workflows, templates, and owned
   documentation before changing a file.
2. Read [authority-and-evidence.md](references/authority-and-evidence.md). Use official
   Gentoo sources for portable semantics and exact upstream primary records for package
   facts.
3. Treat repository history and comparable packages as precedent only. Confirm every
   general rule in current authoritative material.
4. Record the source, revision or retrieval date, claim, scope, and unresolved conflict
   for each decision that changes behavior.
5. Stop when a required source is unavailable or two applicable authorities conflict in
   a way that changes the result.

Query the registered inventory with
`python3 scripts/source_manager.py list --scope portable-core --topic <topic>` from this
skill directory. A lock is a reviewed locator, not a substitute for reading the current
source.

## Resolve Repository Capabilities

Read [repository-capabilities.md](references/repository-capabilities.md) before any write
or Git state change. Resolve the worktree identity, supported operation, writable scope,
canonical base, keyword policy, required commands, verification gates, commit procedure,
publication procedure, and approval gates from live repository evidence.

Classify each capability as `known`, `unsupported`, or `unknown`. Continue only when every
capability required by the requested operation is `known`. Never infer a remote, branch,
keyword, architecture, command, CI gate, commit format, or publication rule from the
directory layout or another overlay.

## Select One Supported Operation

1. Read [ebuild-change-workflow.md](references/ebuild-change-workflow.md) for a change to
   an existing package and its direct support files.
2. Read [package-lifecycle.md](references/package-lifecycle.md) for a new package,
   keyword change, move, rename, version or package removal.
3. Read [repository-development.md](references/repository-development.md) for an eclass,
   profile, repository metadata, category, license, or policy-owned file.
4. Inspect every ebuild in the package, `metadata.xml`, `Manifest`, referenced `files/`,
   relevant history, inherited eclass documentation, and exact upstream material.
5. Define one coherent change and its complete owned file set. Preserve unrelated work.
6. For dependency or USE changes, follow
   [dependency-review.md](references/dependency-review.md).
7. For source archives, prebuilt artifacts, patches, licenses, redistribution, or
   Manifest changes, follow
   [artifacts-and-licensing.md](references/artifacts-and-licensing.md).
8. Make the smallest evidence-backed edit. Keep global scope metadata-safe, keep USE
   branches consistent, and keep every referenced file in the same change.
9. Stop instead of guessing a version, dependency, slot, artifact, checksum, license,
   restriction, keyword, patch status, or installed layout.

## Verify the Result

Read [qa-verification.md](references/qa-verification.md) and run every gate required by
the resolved capability contract. At minimum, verify structure, regenerate and inspect
the Manifest when fetch inputs change, run package QA, exercise affected USE states,
build, run supported tests, inspect the installed image, perform a clean install, and
review saved `qa`, `warn`, and `error` elog records when the environment and repository
contract provide those operations.

Use `scripts/qa_runner.py` only for a bounded, read-only package-scoped `pkgcheck` result
when the live capability contract supplies the repository identity and permits that
gate. Use `scripts/dependency_analyzer.py` only on already extracted dependency metadata;
it never sources an ebuild. These reports are review inputs and do not establish a
repository capability or package fact by themselves.

Do not replace an unavailable required gate with a weaker check. Mark it unverified,
state the exact limitation and risk, and stop before any action that live policy makes
dependent on that gate. Review the complete diff and repository status after all checks.

## Commit or Publish Only Through Live Procedure

Use only the resolved repository commands, message rules, signing requirements, remote,
branch, template, approval gate, and CI process. If any publication capability is absent,
finish with verified local changes and report publication as unsupported. Never create a
commit, push, issue, or pull request merely because another overlay uses that workflow.

Report the repository identity, policy revision, base and synchronization state, changed
files, evidence reviewed, commands and outcomes, skipped gates and reasons, and remaining
risks.
