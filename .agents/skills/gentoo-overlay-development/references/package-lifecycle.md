# Package Lifecycle Changes

Use this procedure for a new package, keyword change, package move or rename, and version
or package removal. Apply the target repository's live ownership, review, timing, and
publication rules; official Gentoo documentation defines mechanics but does not grant
authority in an independently maintained overlay.

## Add a New Package

1. Search the complete target repository set, repository history, configured masks, and
   upstream former names for duplicates, collisions, moves, forks, and providers.
2. Establish the category, package name, upstream identity, release or immutable source,
   build system, direct dependencies, licenses, redistribution rights, installed files,
   supported architectures, and maintenance owner from current evidence.
3. Read every inherited eclass contract before drafting. Choose the current EAPI and
   helpers from required behavior, not from a nearby package's shape.
4. Add the ebuild, `metadata.xml`, Manifest, and every referenced file as one atomic unit.
   Add category, license, tracker, or repository metadata only when live policy requires
   it and include it in the same dependency-ordered change.
5. Use exact upstream metadata identifiers and local USE descriptions. Do not invent a
   maintainer, source mirror, generated dependency bundle, optional feature, or keyword.
6. Add only keywords allowed by live policy and supported by the required architecture
   evidence. Upstream platform claims and copied keywords are not build or install tests.
7. Run every new-package QA, fetch, build, USE-state, test, install, elog, and runtime gate
   required by the repository.

The official [new ebuild guide](https://devmanual.gentoo.org/ebuild-maintenance/new-ebuild/)
and [metadata.xml guide](https://devmanual.gentoo.org/ebuild-writing/misc-files/metadata/)
provide Gentoo authoring guidance. Requirements tied to one repository's teams, mailing
lists, file hosting, or commit access apply only when the target repository adopts them.

## Change Keywords

- Resolve the target repository's stable, testing, masking, and architecture policy
  before editing `KEYWORDS`.
- Verify the exact ebuild and affected USE states on every architecture for which live
  policy requires direct testing. Do not treat another architecture, upstream support,
  a prebuilt filename, or CI configured only after keywording as equivalent evidence.
- Preserve valid existing keywords unless current evidence and policy authorize their
  removal. Check providers, reverse dependencies, virtuals, profiles, and retained
  versions before narrowing availability.
- Treat architecture-independent and prebuilt packages according to explicit live rules;
  do not generalize an exception from another repository.

## Move or Rename a Package

1. Confirm that the destination does not collide and that the operation is supported.
2. Before moving, inspect every use of `CATEGORY`, `PN`, `P`, `PF`, package paths, service
   names, cache keys, and artifact names. Preserve an old literal where behavior depends
   on the old identity.
3. Move the complete package directory with history and create the exact update record
   required by the repository and active profile EAPI.
4. Update dependency atoms, blockers, helper calls, eclasses, profile entries, metadata
   restrictions and package references, news conditions, CI configuration, automation,
   documentation, and repository-owned indexes.
5. Verify old-to-new package-manager behavior as well as a clean install at the new atom.

Follow the current official
[package move guidance](https://devmanual.gentoo.org/ebuild-maintenance/package-moves/)
for variable hazards and update semantics. A target overlay may impose a narrower move
or migration procedure.

## Remove a Version or Package

1. Establish the live removal procedure, reason, required notice or mask, waiting period,
   approval, and cleanup scope. Do not copy another repository's schedule.
2. Check every repository in the configured set for reverse dependencies, exact pins,
   providers, virtuals, profiles, masks, metadata references, automation, and retained
   architecture coverage.
3. For a version removal, prove that retained versions satisfy every required slot and
   keyword without an unintended downgrade or loss of immutable source bytes.
4. For a package removal, update or remove all non-blocker references and every owned
   state made obsolete by the package. Preserve blockers only when live behavior still
   requires them.
5. Keep removal records, update entries, masks, metadata, and dependent changes in the
   repository-defined atomic order. Verify dependency resolution after each standalone
   commit when the repository requires multiple commits.

Use the official [removal guidance](https://devmanual.gentoo.org/ebuild-maintenance/removal/)
for dependency and cleanup checks. Repository-specific communication, issue state, and
timelines are not portable overlay rules.
