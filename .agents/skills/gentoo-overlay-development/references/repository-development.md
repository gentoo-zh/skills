# Repository-Wide Development

Use this procedure for eclasses, profiles, repository metadata, categories, shared
licenses, and other repository-owned surfaces. These changes have broader consumers than
a package directory, so require an explicit live ownership and verification contract.

## Repository Layout and Metadata

1. Read the current Package Manager Specification sections for repository layout,
   profiles, updates, licenses, eclasses, and metadata before changing those surfaces.
2. Confirm the repository identity from `profiles/repo_name` and the configured
   repository set. Do not infer identity, masters, priority, or profile inheritance from
   a directory or remote name.
3. Resolve the active profile EAPI and repository configuration before interpreting
   masks, USE descriptions, update records, or inherited data.
4. Keep category, package, license, Manifest, and metadata indexes consistent with the
   packages that reference them. Reject orphaned references and names outside current
   syntax.
5. Treat workflow, automation, review, and publication files as repository-local policy.
   Their semantics must come from live documentation and executable configuration.

The current [Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
is authoritative for package-manager-visible layout and semantics. It does not define a
repository's contribution or publication process.

## Add or Change an Eclass

1. Establish the eclass purpose, ownership, supported EAPIs, public variables, functions,
   exported phases, defaults, side effects, and complete consumer set.
2. Prefer a package-local implementation until repeated consumers and live repository
   policy justify a shared interface. Do not copy an eclass merely because another
   repository has a similar package.
3. Keep global scope metadata-safe. Validate variables at the documented time, preserve
   user configuration, and use phase functions only for work requiring phase context.
4. Document the public contract and compatibility boundary. Mark internal helpers as
   internal according to current eclass documentation conventions.
5. Check phase composition across all inherited eclasses. An EAPI default phase does not
   invoke another eclass's exported implementation.
6. Search every consumer and update dependencies, pre-inherit variables, calls, EAPIs,
   and overrides atomically when the interface changes.
7. Run syntax and documentation checks, metadata generation, package QA, and every
   repository-required build, test, install, and elog gate across a representative set
   that live policy defines. Test every consumer when compatibility cannot be bounded.

Read the official [eclass writing guide](https://devmanual.gentoo.org/eclass-writing/)
and generated eclass reference. Approval and communication rules in that guide apply to
the target overlay only when its live policy adopts them.

## Change Profiles or Shared Policy Data

- Identify the active profile EAPI, inheritance graph, parent repositories, affected
  profiles, packages, architectures, and USE states.
- Model mask, unmask, keyword, USE, package set, update, and license changes with the
  exact package-manager semantics for that file. Do not edit a line until its current
  owner and override behavior are known.
- Check the complete repository set for contradictions, ineffective entries, invalid
  atoms, stale comments, and changes hidden by a parent or child profile.
- Resolve affected packages under every required profile before and after the change.
  Build and install representative or complete consumers according to blast radius and
  live policy.
- Keep a package move or ABI migration dependency-ordered with its ebuild, eclass,
  profile, and update records. Each repository-required standalone commit must leave a
  coherent state.

## Completion Rule

Record affected consumers and profiles, not only changed files. Stop when ownership,
profile semantics, consumer scope, compatibility, validation coverage, rollout order, or
rollback behavior is unknown. Publication still requires the separate live procedure.
