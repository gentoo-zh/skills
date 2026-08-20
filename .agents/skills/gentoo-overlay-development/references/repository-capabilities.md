# Repository Capability Contract

Resolve this contract from the checked-out repository before changing files or Git state.
Use `profiles/repo_name` and canonical repository evidence for identity; never use only a
directory name.

## Capability States

Classify every field as:

- `known`: current live evidence defines one unambiguous value or procedure;
- `unsupported`: live evidence explicitly excludes the operation;
- `unknown`: evidence is missing, stale, conflicting, or ambiguous.

An `unsupported` or `unknown` field blocks every write that requires it. Read-only
inspection may continue to identify the missing fact.

## Required Fields

Resolve and record:

- worktree root, `profiles/repo_name`, repository identity, and allowed writable paths;
- which surface owns each affected path: package files under `category/package/`, repository
  metadata under `metadata/`, `profiles/`, and `repo.xml`, and CI configuration, wherever the
  target repository places them;
- live policy files and their current revisions;
- requested operation, affected repository surfaces, and whether each write is supported;
- canonical repository identity, fetch remote, default branch, and synchronization rule;
- topic-branch and worktree rules, including how unrelated changes must be preserved;
- package keyword and architecture policy, including evidence required to add or retain
  an architecture;
- repository lint, Manifest, package QA, network, build, test, install, elog, and diff
  commands;
- authoritative CI jobs and the exact behavior they verify;
- commit generator, atomicity, subject, body, sign-off, signing, and retry requirements;
- publication repository, allowed push remote, branch update method, template, issue
  syntax, approval gate, and CI observation procedure;
- retry, rollback, and failure-reporting rules.

Do not fill a missing field from another overlay, a conventional remote name, a common
default branch, or a familiar directory layout.

## Preflight

1. Read the complete live policy and all executable workflows relevant to the requested
   operation.
2. Inspect repository status, remotes, branch, upstream relationship, and base revision
   without changing them.
3. Identify the canonical remote by repository identity and URL. Stop on no match or
   multiple matches unless live policy gives a deterministic resolution.
4. Fetch or synchronize only through the documented procedure. Stop on a failed fetch,
   diverged protected branch, ambiguous base, unsafe worktree, or unrelated changes that
   prevent an isolated edit.
5. Re-resolve fields when policy, branch, remote, worktree, or requested scope changes.

## Operation Boundary

Use the applicable package-change, package-lifecycle, or repository-development
procedure only when every affected surface is `known`. A package move that also changes
profiles requires both procedures. A new eclass that changes consumers requires the
repository procedure and package verification for every affected consumer selected by
live policy. Treat an operation as `unknown` when the target repository does not define
its ownership, atomicity, validation, or approval boundary.

Publication is a separate capability. Local edit support does not imply permission to
commit, push, or create or update review records.
