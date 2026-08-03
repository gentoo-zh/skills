# Ecosystem Checks for Version Bumps

Use this file as a selective checklist after reading the live overlay policy and the
package itself. It does not replace the PMS, Devmanual, current eclass documentation,
or tool output. Follow the evidence order in [official-sources.md](official-sources.md).

## Evidence rules

- Treat a check as required only when it follows from current repository policy,
  official Gentoo documentation, a current eclass, or reproducible tool output.
- Treat source searches and historical examples as heuristics. Inspect every match and
  verify it against the current release before changing the ebuild.
- Compare the new release with the previous release, the existing ebuilds, package
  history, upstream build metadata, and release notes. Do not infer a dependency or
  option change from the version number alone.

## Checks for every source package

- Fetch all build and test inputs before the phase functions run. Builds must not rely
  on network access after fetch; declare generated or vendored inputs through
  `SRC_URI`, an eclass mechanism, or another repository-approved method.
- Recheck every applied patch against the new source. Keep its upstream reference and
  purpose current. Remove a patch only after proving that the new source no longer
  needs it, and retain every file still used by another ebuild.
- Recheck build options and optional feature detection. Every USE flag must control the
  corresponding option, dependency, source selection, and installed output.
- Respect user compiler and linker flags. Do not retain upstream stripping, forced
  optimization, blanket `-Werror`, or LTO settings unless current package evidence and
  Gentoo policy justify them.
- Classify dependencies by when they are needed. Use the PMS and the Devmanual rather
  than copying `DEPEND`, `RDEPEND`, `BDEPEND`, `IDEPEND`, or `PDEPEND` from a similar
  package.
- Read the current documentation and source of every inherited eclass. Check supported
  EAPIs, variables that must be set before `inherit`, exported phases, and defaults.
- Exercise each changed USE state and run the reliable upstream test subset with
  `FEATURES=test`. Declare test-only dependencies and resources.
- Inspect the installed image. Check file paths, modes, generated launchers, service
  files, desktop files, and references to removed files or package atoms.

## C, C++, CMake, Meson, and Autotools

- Read the new release's declared compiler, language-standard, CMake, Meson, and
  library requirements. Confirm them with configure output or a clean build.
- Check `FetchContent`, `ExternalProject`, subprojects, and similar mechanisms for
  undeclared downloads. Provide fixed inputs before the build or disable the download
  path using a documented upstream option.
- Compare available configure options with the ebuild arguments. A renamed or removed
  option can make a USE flag ineffective.
- If a patch changes Autoconf input, follow the current autotools eclass guidance for
  regeneration. Do not add `eautoreconf` solely because a package uses Autoconf.
- Diagnose missing declarations and headers from the actual compiler error. Use the
  smallest upstreamable fix and do not hide the defect by selecting an older compiler.

Useful heuristic searches include `FetchContent_Declare`, `ExternalProject_Add`,
`add_subdirectory`, `-Werror`, `-flto`, explicit optimization flags, and optional
dependency probes. A match is a review target, not an automatic violation.

## Rust

- Compare `Cargo.toml`, the workspace manifests, and `Cargo.lock` with the previous
  release. Regenerate `CRATES`, `GIT_CRATES`, or vendor data from the new lock file
  using the current cargo eclass workflow.
- Treat upstream `rust-version` as a minimum and reconcile it with `RUST_MIN_VER` and
  the current Rust eclass. Do not invent an upper bound without a demonstrated
  incompatibility.
- Verify that all crates and git dependencies resolve offline. Preserve exact git
  source identities required by the lock file.
- Let Portage control stripping and debug handling. Remove upstream linker settings
  that pre-strip installed objects unless current official guidance requires them.

## Go

- Compare the new `go.mod`, `go.sum`, workspace files, and generated dependency data.
- Treat the `go` directive as the minimum language/toolchain requirement and reconcile
  it with the package's `BDEPEND` and the current go-module eclass. Under the overlay's
  `GOTOOLCHAIN=local` policy, do not treat the `toolchain` suggestion as a minimum.
- Verify module data offline and ensure every versioned dependency archive belongs to
  the target release.
- Preserve useful version injection flags, but let Portage control stripping and debug
  handling.

## Python

- Read the new `pyproject.toml` or equivalent build metadata. Set the backend and build
  dependencies according to current distutils-r1 and Python eclass documentation.
- Recheck `PYTHON_COMPAT` against the current eclass and the release's declared Python
  support. Do not use a hard-coded implementation age list from this reference.
- Apply `${PYTHON_USEDEP}` or `${PYTHON_SINGLE_USEDEP}` where the current Python eclass
  requires it.
- Define `EPYTEST_*` variables before `distutils_enable_tests` when they affect generated
  test dependencies or pytest behavior. Verify the plugin set with the current
  python-utils-r1 documentation.
- Confirm imports and runtime entry points against the installed package. Do not infer a
  runtime dependency from a source-text match alone.

## Other ecosystems

- For Java, Perl, Node.js, Haskell, kernel modules, and other specialized packages,
  start with the current upstream metadata and the current Gentoo eclass reference.
- Regenerate dependency metadata with the documented generator when the ebuild is
  generator-driven. Escalate if the generator or required source data is unavailable.
- For kernel modules, use the current supported module eclass and test against the
  declared kernel range. Do not copy phase composition or kernel bounds from an older
  eclass.

## Install and elog review

- Validate installed `.desktop` files with the same tool used by the current Portage
  path when the package installs them. Fix the file or use a documented, path-scoped QA
  exception only for a verified false positive.
- Perform the clean install and isolated elog check required by the live overlay
  workflow. Treat every saved `qa`, `warn`, or `error` message as evidence to
  investigate.
- Compare with the previous version under the same conditions before calling a message
  environmental. Record any accepted warning and its evidence in the completion report.

## Primary references

- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
- [Gentoo Development Guide](https://devmanual.gentoo.org/)
- [Gentoo eclass reference](https://devmanual.gentoo.org/eclass-reference/index.html)
- [Gentoo QA Policy Guide](https://projects.gentoo.org/qa/policy-guide/)
- The live overlay `AGENTS.md` and CI workflows
