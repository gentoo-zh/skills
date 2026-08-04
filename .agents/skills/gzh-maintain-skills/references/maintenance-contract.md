# Maintenance Contract

Use this contract to decide whether evidence justifies a repository change and how an
unattended cycle must stop.

## Contents

- [Authority Order](#authority-order)
- [Cycle States](#cycle-states)
- [Promotion Gate](#promotion-gate)
- [Unattended Boundaries](#unattended-boundaries)
- [Compatibility Gate](#compatibility-gate)
- [Release Gate](#release-gate)

## Authority Order

1. A target repository's live policy, workflows, templates, and repository-owned docs for
   behavior local to that repository.
2. Gentoo PMS, Devmanual, applicable GLEPs, QA policy, eclass reference, and official tool
   manuals for portable Gentoo behavior. Repository-scoped Gentoo policy remains
   comparative evidence unless the target repository adopts it.
3. Primary upstream release, source, build metadata, license, and artifact records.
4. Current official implementation and full commit history as implementation evidence,
   not as a target repository profile.
5. Current official documentation for each supported installation target and shared
   skill format.
6. Other overlays as comparative evidence only.
7. `gentoo-tree-lessons` and other derived corpora as secondary candidate evidence.

A repository policy can specialize portable Gentoo behavior inside that repository, but it
cannot redefine PMS or make its local convention universal. When two applicable primary
sources conflict, retain the narrower current behavior and report the exact conflict.

## Cycle States

`collect -> classify -> reproduce -> change -> verify -> review -> publish -> observe`

- `collect`: obtain complete current data and provenance.
- `classify`: identify authority, scope, and whether behavior changed.
- `reproduce`: produce an observable failing case before changing code.
- `change`: modify one coherent boundary and its documentation or eval surface.
- `verify`: run focused checks, repository validation, full tests, and source audit.
- `review`: inspect the diff and obtain an independent pass for high-risk behavior.
- `publish`: commit and push only within current authorization.
- `observe`: verify the remote SHA and watch every triggered workflow.

Do not skip directly from fingerprint drift to `change`. A cycle ends as a no-op when
classification finds no supported behavior change.

## Promotion Gate

Promote a finding into a hard instruction only when all fields are known:

- authoritative source URL and immutable revision or reviewed fingerprint;
- exact old and new behavior;
- repository, tool, package type, EAPI, eclass, or client scope;
- classification as portable core behavior or a named repository adapter behavior;
- deterministic pass and fail condition;
- regression test or justified forward-test;
- conflict check against higher-authority sources;
- rollback or stop behavior when the condition is not met.

Candidate-history observations cannot serve as primary evidence. Store official evidence
in a passed run, move the candidate to `reviewed`, and create an explicit reviewed-evidence
link before promotion. Discovery in the same report is not a review.

A batch decision must enumerate every exact candidate key, expected state, requested state,
and individual reason. Validate the complete bounded manifest against one state snapshot and
apply it atomically with one transition record per candidate. Never batch promotions because
each promotion requires its own linked primary evidence and complete checklist.

Statistics and incident counts remain dated evidence. They do not become permanent
thresholds. One package fix remains package precedent unless a primary source defines the
general behavior.

## Unattended Boundaries

An unattended collector may fetch canonical refs, read public sources, refresh a separate
derived-corpus cache, run validators and tests, write an explicitly requested report, and
open or update the repository's maintenance issue from GitHub Actions.

It must not rewrite skill instructions, refresh source locks, reset or clean a checkout,
replace unowned installation paths, commit, push, create releases, or modify overlay issues
and pull requests. Those actions require reviewed evidence and current user authorization.

Persist queue and evidence state in one SQLite database. Pin complete task payloads,
absolute file inputs, hashes, repository identity, and the starting revision. Resume an
incomplete plan before deriving a plan from a newer cursor. Commit a successful producer
artifact and its queue completion in one database transaction. Keep the cursor-producing
task last so earlier gate failures cannot advance collection state.

Bound subprocess and network reads while streaming. Compact old routine runs and full
candidate-discovery reports while retaining normalized observations, candidate payloads,
transitions, reviewed links, original hashes, and the latest cursor per repository. Stop
and surface the backlog instead of dropping evidence when candidate or state-size ceilings
are reached.

Delete only fully succeeded queue plans during compaction. Retain every pending, running,
or blocked plan, and stop before mutation when incomplete plans exceed the retention
ceiling.

Stop rather than guess when a source is unavailable, a queue is truncated, a checkout is
dirty before baseline collection, the canonical remote is ambiguous, local `master` is
ahead or diverged, the same gate fails twice, or a required format cannot be established.

## Compatibility Gate

For installer or updater changes, verify every supported discovery path against its
current official documentation. Test default and explicit target selections, copy and
link modes, stale refresh, uninstall, unowned paths, dangling symlinks, duplicate
discovery directories, custom configuration roots, and rollback before writing any
destination.

For workflow changes, verify action tags against the action's official repository. Keep
workflow permissions minimal, preserve complete failure reports, and make issue mutation
idempotent.

## Release Gate

An unattended cycle cannot tag or publish. A release requires current user authorization,
a clean synchronized canonical `master`, one exact commit with all local and remote gates
complete, and an authenticated reference-audit run on that revision.

Keep the package version declarations and `v<version>` tag identical. State whether
installations follow a tag or a branch. If the repository has no explicit license, do not
invent one or upload a wheel, sdist, executable, or other custom package artifact. Record
the undeclared rights status and require an owner decision before enabling package
distribution.
