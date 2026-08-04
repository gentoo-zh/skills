# Authority and Evidence

Use evidence according to the claim it supports. Do not use familiarity, a generated
checklist, or a neighboring package as a substitute for a current source.

## Contents

- [Authority Order](#authority-order)
- [Evidence Record](#evidence-record)
- [Upstream and Ecosystem Discovery](#upstream-and-ecosystem-discovery)
- [Conflict Handling](#conflict-handling)
- [Registered Official Sources](#registered-official-sources)

## Authority Order

1. Use the target repository's live policy, workflows, templates, and repository-owned
   documentation for its local workflow, writable scope, keywords, verification gates,
   commits, publication, and review.
2. Use the current Gentoo Package Manager Specification, Development Guide, ebuild and
   eclass references, QA policy, applicable GLEPs, and official tool manuals for portable
   Gentoo semantics.
3. Use the exact upstream release, tag, source, build metadata, artifacts, signatures,
   license text, and distribution terms for package facts.
4. Use the target package, its complete relevant history, and genuinely comparable
   packages as implementation precedent only.

A repository may specialize its own workflow. It cannot redefine package-manager
semantics. Upstream is authoritative for its release and artifacts, but not for Gentoo
dependency syntax or repository policy.

An official source is not automatically portable. A GLEP scoped to the Gentoo ebuild
repository remains comparative evidence for another overlay unless that overlay's live
policy adopts it.

## Evidence Record

For every non-obvious decision, record:

- source URL and immutable revision, release identifier, or retrieval date;
- exact claim and affected file or behavior;
- scope: repository-local, portable Gentoo, or package-specific;
- whether the source is normative, executable policy, primary package evidence, or
  precedent;
- conflicts, missing facts, and the resulting stop condition;
- verification that would disprove the decision.

Read the complete relevant section and inspect later corrections before relying on an old
commit. A fingerprint or successful download proves neither meaning nor provenance.

## Upstream and Ecosystem Discovery

1. Establish the canonical project identity from its current owned website, source
   repository, release records, and package metadata. Treat search results, mirrors,
   distribution packages, and version trackers as discovery leads.
2. Resolve the exact release identifier, immutable tag or commit, source archive, build
   metadata, dependency lockfiles, generated bundles, binary artifacts, signatures,
   notices, and license texts affected by the change.
3. Compare the current and target release records. Do not infer a renamed project,
   repository transfer, tag prefix, artifact host, ecosystem package, or build-system
   change from a version string alone.
4. Use ecosystem registries and lockfiles for the facts they own. Confirm that their
   package identity and release correspond to the canonical upstream release before using
   them as package evidence.
5. Treat `latest` endpoints, tracker output, issue text, and generated summaries as
   mutable observations. Re-resolve them to immutable release evidence before a write.

Record unresolved identities and conflicting release records as stop conditions. An
upstream project defines its own release and files; it does not define Gentoo dependency
classes, keyword policy, repository workflow, or publication authority.

## Conflict Handling

- Apply repository policy only to repository-local behavior.
- Apply the current specification when an example conflicts with portable semantics.
- Apply exact release material when old package history conflicts with the current
  release.
- Retain the narrower supported change and report the conflict when two applicable
  primary sources disagree.
- Stop when the conflict changes licensing, redistribution, artifact selection,
  dependencies, EAPI compatibility, keywords, verification, or publication.

## Registered Official Sources

- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
  for normative EAPI, ebuild, dependency, version, and repository semantics.
- [Gentoo Development Guide](https://devmanual.gentoo.org/) for ebuild maintenance
  guidance.
- [Gentoo ebuild reference](https://devmanual.gentoo.org/eclass-reference/ebuild/index.html)
  and [eclass reference](https://devmanual.gentoo.org/eclass-reference/index.html) for
  current variables, phases, helpers, and eclass contracts.
- [Gentoo QA Policy Guide](https://projects.gentoo.org/qa/policy-guide/) for official QA
  requirements.
- [pkgcheck manual](https://pkgcore.github.io/pkgcheck/man/pkgcheck.html),
  [pkgdev manual](https://pkgcore.github.io/pkgdev/man/pkgdev.html), and
  [emerge manual](https://dev.gentoo.org/~zmedico/portage/doc/man/emerge.1.html) for
  current tool behavior.

Use the repository's registered source inventory and reviewed locks to locate sources,
but read current content before changing a rule.

Fingerprint the behavior-bearing official source when a rendered page includes unrelated
build timestamps or other volatile presentation metadata. Keep the human-readable official
documentation URL in prose when it is useful, but do not let an unrelated site rebuild
masquerade as a behavior change.

The machine-readable inventory is [sources.json](sources.json), its reviewed fingerprints
are in [source-lock.json](source-lock.json), and
[`source_manager.py`](../scripts/source_manager.py) provides bounded list, audit, and
review-only lock refresh commands. Use `--capability <id>` to select the exact registered
source route for one behavior, optionally intersected with topic, authority, scope, or
explicit source IDs.
