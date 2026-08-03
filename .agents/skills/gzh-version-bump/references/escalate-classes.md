# Escalation Conditions

Escalation means stopping automatic edits and collecting the evidence needed for a
maintainer decision. It is not a package rejection. Use the live overlay policy first,
then the PMS, Devmanual, current eclass and tool documentation, package history, and
upstream primary metadata.

## Escalate immediately

Escalate when any of the following conditions prevents a verified version bump:

- The package has no versioned ebuild, the request is actually for a new package, or the
  change requires a package move, split, merge, or replacement.
- The requested version cannot be matched unambiguously to an upstream release or tag,
  including a prerelease from a package history that has only followed final releases.
- The source artifact, signature, checksum provenance, license, or redistribution terms
  cannot be verified from current upstream material.
- The new release requires generated dependency or package metadata, but the documented
  generator, input data, or reproducible output is unavailable.
- A version-specific vendor, dependency, crate, module, or `node_modules` archive is
  required but no matching artifact exists or its provenance is unclear.
- An applied patch cannot be rebased or its purpose cannot be confirmed for the new
  release.
- The release changes the build system, source layout, dependency model, ABI, package
  identity, or installation layout beyond what the package history and current official
  documentation can support safely.
- A required eclass is removed, deprecated, or incompatible with the target EAPI and the
  migration requires decisions outside the version bump.
- A source package would gain an architecture that has not been built as required by the
  live overlay policy, or a prebuilt ebuild would retain or add a keyword without the
  corresponding upstream artifact.
- Required local or CI verification cannot run and the missing result changes whether the
  ebuild is correct.

## Heuristic signals

The following signals request closer review. They do not prove that escalation is
necessary:

- A change in the leading version component or release channel.
- Version, commit, tag, crate, module, or toolchain pins in the ebuild.
- Versioned dependency archives or generated source lists in `SRC_URI`.
- Applied files under `files/`.
- Large changes in the upstream lock files, build metadata, bundled libraries, or
  installed file list.
- A project transfer, forge redirect, repository rename, or changed release publisher.

Inspect the matched data in the target release. Continue only when the current value can
be derived and verified without guessing.

## Conditions that do not escalate by themselves

- Multiple testing keywords. Verify and report each architecture according to the live
  overlay policy.
- A GUI package. Record any smoke test that cannot run, but continue with the available
  install and metadata checks.
- An unused historical patch. Confirm that no surviving ebuild references it before
  considering cleanup.
- A pin that is unchanged and independently verified against the new upstream metadata.
- A prebuilt release that omits an architecture supported by an older retained ebuild,
  when the new ebuild can correctly omit that keyword under the live overlay policy.
  Escalate instead when removing the keyword changes the requested scope or a maintainer
  decision is otherwise required.

## Transient failures

Treat timeouts, rate limits, and server errors as inconclusive. Retry within the
repository limit. Escalate after the retry limit only when the missing remote evidence is
required to continue; otherwise report the skipped check and its effect.

Do not interpret an HTTP `404` alone as proof that a release asset will never exist.
Confirm the expected asset name and release state using the upstream release API or page.

## Escalation record

Report:

1. The package and requested version.
2. The exact blocking condition.
3. The commands, files, and primary URLs checked.
4. The current ebuild or package-history evidence.
5. The smallest maintainer decision or missing datum needed to continue.

Do not edit the ebuild, create a commit, or mark a persistent skip unless the governing
workflow explicitly authorizes that action.

## Primary references

- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
- [Gentoo Development Guide](https://devmanual.gentoo.org/)
- [Gentoo eclass reference](https://devmanual.gentoo.org/eclass-reference/index.html)
- The live overlay `AGENTS.md`, package history, and CI workflows
- Upstream release pages, tags, source archives, build metadata, and license text
