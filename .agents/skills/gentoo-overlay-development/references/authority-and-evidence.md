# Authority and Evidence

Use evidence according to the claim it supports. Do not use familiarity, a generated
checklist, or a neighboring package as a substitute for a current source.

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

The machine-readable inventory is [sources.json](sources.json), its reviewed fingerprints
are in [source-lock.json](source-lock.json), and
[`source_manager.py`](../scripts/source_manager.py) provides bounded list, audit, and
review-only lock refresh commands.
