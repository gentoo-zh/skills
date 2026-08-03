# Gentoo Repository Adapter Architecture

Most Gentoo repositories share ebuild mechanics, but they do not share one publication
contract. Build a portable core and require an explicit repository adapter for every local
policy decision.

## Portable Core

Keep these behaviors independent of a repository brand when official Gentoo sources define
them:

- EAPI and PMS syntax;
- version parsing and live-ebuild detection;
- dependency, slot, USE, phase, and eclass semantics;
- `SRC_URI`, Manifest, patch, license, and redistribution review;
- source and prebuilt artifact inspection;
- `pkgcheck`, `pkgdev`, Portage build, install, test, and elog mechanics;
- installed-file and binary QA;
- evidence provenance, retry limits, state locking, and atomic writes.

Portable code accepts repository capabilities as input. It must not contain an overlay
owner, remote name, default branch, keyword set, PR template, CI profile, or issue format.

## Repository Adapter

Resolve and validate all of these fields before a write:

- Git worktree root and `profiles/repo_name`;
- canonical repository identities and fetch remote;
- publication repository and allowed push remote;
- default branch and topic-branch convention;
- supported package operations;
- keyword and architecture policy;
- commit generator, sign-off, signing, and subject rules;
- required local gates and authoritative CI jobs;
- install and elog profiles;
- issue tracker, closing syntax, PR template, and approval gate;
- new-package metadata and version-check registration;
- repository paths that CI must ignore as package atoms.

Read the adapter from the checked-out repository and a reviewed profile. Reject an unknown
or ambiguous repository. Never select an adapter from the directory basename alone.

## Adapter Evolution

Keep existing repository-local skills explicitly scoped until the portable core and
adapter boundary are tested. Do not describe a repository as fully adapted until every
required capability has a machine-checkable source and a regression fixture.

Add future adapters in this order:

1. extract a repository-neutral model from deterministic helpers without changing current
   `gentoo-zh` behavior;
2. add capability fixtures for independently reviewed overlays from their live repository
   policy and current official Gentoo documentation;
3. run the same portable test suite against every profile and add adapter-specific tests;
4. enable each operation in the generic skill only after the adapter provides every
   operation-specific capability and the shared procedure has a regression fixture;
5. retain repository-local compatibility commands until a migration has an explicit
   deprecation and rollback plan.

Comparative repositories and implementation histories are evidence sources, not operation
profiles. An unsupported operation must fail with the adapter and missing capability. Do
not silently fall back to another repository's policy.

## Cross-Repository Evidence

Use another overlay to discover a question or candidate implementation. Before promoting
it, confirm the behavior in official Gentoo material or classify it under that overlay's
adapter. Record the source repository, revision, package or path, later fixes, and the exact
portable condition. Similar directory structure is not evidence that review or publication
policy is shared.
