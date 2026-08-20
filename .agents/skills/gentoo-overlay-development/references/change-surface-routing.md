# Change-Surface Routing

Classify the changed surface from current package, upstream, and repository evidence
before loading specialist references or running specialist tools. A version number, a
small diff, or an unchanged ebuild body does not prove that a release is a copy-only
change.

## Baseline for Every Ebuild Change

Always apply the complete live repository capability contract. Independently of change
size:

1. Review the ebuild against the active EAPI, inherited eclasses, current Gentoo
   semantics, and clear package-local style.
2. Inspect every changed variable, phase, dependency, USE branch, patch, restriction,
   installed path, support file, and Manifest entry.
3. Run every live gate required for the authorized operation. Routing cannot weaken a
   structural lint, package QA, Manifest, build, install, elog, diff, or network gate.
4. Resolve each finding at its cause. Do not add a QA suppression, phase override,
   dependency, or revision only to make a tool pass.
5. Record any required gate that cannot run as unverified with its exact impact.

## Select a Current Precedent

Before adding custom variables, phase code, or QA exceptions, find how the current Gentoo
tree or the target repository already solves the same problem and take that form. Prefer
the local main tree at `/var/db/repos/gentoo` when it is present. Inspect the exact package
in the current Gentoo tree when it exists. Then inspect its current history and only the
smallest genuinely comparable sibling. Match the source or prebuilt model, archive
format, build system, installed layout, runtime integration, and eclass contract. Product
family or directory proximity alone is insufficient.

Prefer an established eclass helper and the simpler current phase pattern when it covers
the verified requirement. Do not copy a large source-build ebuild into a prebuilt
package, retain obsolete workarounds, or reproduce code already owned by an eclass.
A construct with no precedent in either tree needs a stated reason. Precedent otherwise
remains advisory and never overrides PMS, the Devmanual, eclass documentation, upstream
facts, or live repository policy.

## Load and Run by Changed Surface

| Changed surface | Load or run in addition to the baseline |
| --- | --- |
| Verified copy-only release | Verify the exact target assets and prove that artifact selection and topology, dependencies, build inputs, USE behavior, patches, licenses, and installed layout are unchanged. Load no specialist reference for a package surface proved unchanged. |
| Dependency, slot, provider, or USE behavior | Read `dependency-review.md`; analyze affected states and providers, then build and install those states. |
| Source topology, archive format, generated bundle, license, redistribution, or high-risk distfile | Read `artifacts-and-licensing.md`; verify exact provenance, content, Manifest coverage, and terms. |
| New or changed prebuilt payload | Read `artifacts-and-licensing.md`; inventory every architecture, inspect binary objects, and verify the strict installed image. |
| Patch, build system, toolchain, EAPI, eclass, or phase behavior | Read current upstream build inputs and eclass documentation; exercise the affected phases and supported tests. |
| Installed layout, modes, symlinks, launchers, services, desktop files, or runtime integration | Inspect the installed image and run a bounded trusted runtime check when supported. |
| QA-only correction | Reproduce the exact finding, load only its affected domain, fix the cause, and rerun every live hard gate plus every check invalidated by the edit. Do not invent a release workflow or revision. |
| New package, keyword, move, rename, or removal | Read `package-lifecycle.md` and every domain reference required by the discovered package surface. This is not a copy-only path. |
| Eclass, profile, repository metadata, category, license, or policy file | Read `repository-development.md` and use the live ownership and rollout procedure. |

Routine versioned source archives still require upstream release verification and the
repository Manifest gate. Use additional artifact inventory when risk or live policy
requires it, including prebuilt payloads, generated bundles, per-architecture sets,
manual downloads, mutable or reused filenames, archive topology changes, or unclear
provenance. A valid Manifest digest alone does not establish origin or redistribution
permission.
