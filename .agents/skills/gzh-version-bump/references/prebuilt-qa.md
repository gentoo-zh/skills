# QA for Prebuilt Packages

Use this checklist for packages that install upstream-built executables or libraries.
Read the live overlay `AGENTS.md` and CI workflows first. The PMS, Devmanual, current
ebuild manual, Portage behavior, and upstream primary material are authoritative.

## Contents

- [Provenance, license, and architecture](#provenance-license-and-architecture)
- [Ebuild declarations](#ebuild-declarations)
- [Audit every installed object](#audit-every-installed-object)
- [Dependencies and binary changes](#dependencies-and-binary-changes)
- [Installed image](#installed-image)
- [Required verification](#required-verification)
- [Heuristic review targets](#heuristic-review-targets)
- [Primary references](#primary-references)

## Provenance, license, and architecture

- Verify every downloaded asset on the upstream release or distribution page. Record
  the release, filename, architecture, size, and available signature or digest evidence.
- Read the license and redistribution terms for the exact binary distribution. Set
  `LICENSE` and `RESTRICT` from those terms; do not infer them from the source repository.
- Follow [license-validation.md](license-validation.md). A website agreement, privacy
  policy, or third-party notice does not substitute for the software terms. Inspect every
  license document shipped in the artifact, including PDFs, and evaluate `mirror` and
  `bindist` independently.
- Add only architectures for which upstream publishes the required artifact. Map each
  architecture to its own `SRC_URI` branch and Manifest entry.
- Follow the live overlay policy for testing keywords and its prebuilt arm64 exception.
  `-*` may be used to state that unlisted architectures are not candidates, but it does
  not replace verification of the listed assets.
- Compare the fetched file size with current upstream metadata when available. A
  successful HTTP response is not evidence that a large file is complete.

## Ebuild declarations

- Scope `QA_PREBUILT` and other QA exception variables to the reviewed installed paths.
  Do not use a package-wide pattern unless every matching object was reviewed and the
  live policy permits it.
- Use QA exception variables only for the condition documented by the current ebuild
  manual. They do not repair missing runtime libraries, unsafe segments, invalid paths,
  or incompatible binaries.
- Use `RESTRICT=strip` when Portage must not strip the upstream binary. Check `splitdebug`
  separately according to current Portage documentation.
- Declare tools executed by the ebuild, such as `patchelf`, in the appropriate build
  dependency class.
- For RPM input, read the current `rpm.eclass` documentation and set its pre-inherit
  variables before `inherit` when required. Do not infer the payload type from the file
  extension.

## Audit every installed object

Enumerate the installed ELF objects for every shipped architecture. Use non-executing
tools such as `file`, `readelf`, `scanelf`, and `patchelf` to inspect untrusted or
foreign-architecture files.

Check each object for:

- ELF class and machine matching the selected `SRC_URI` branch.
- Program interpreter matching the target Gentoo profile or a verified installed loader.
- `DT_NEEDED` entries and the packages or bundled objects that provide them.
- SONAME and symlink layout for installed shared libraries.
- RPATH or RUNPATH entries, especially absolute build-host paths and missing paths to
  bundled private libraries.
- Required libc, libstdc++, and other symbol versions against the target profile.
- Executable-stack, text-relocation, writable-and-executable segment, and pre-stripped
  findings reported by current Portage QA.
- CPU feature requirements when the binary exposes usable metadata. Absence of metadata
  is not proof of compatibility.

Do not run `ldd` on an untrusted executable. If runtime loader testing is necessary, use
an isolated environment and a trusted artifact on its matching architecture.

## Dependencies and binary changes

- Add runtime dependencies for system libraries and programs that the installed package
  actually links or invokes. Do not add dependencies for libraries retained inside the
  private bundle.
- Static `DT_NEEDED` inspection does not reveal every `dlopen` target or helper process.
  Confirm those through upstream documentation, source or wrapper inspection, and a
  controlled runtime test.
- A slot operator only schedules a rebuild. It cannot make unchanged prebuilt bytes
  compatible with a new ABI. Constrain verified provider slots or versions when needed.
- Change RPATH, replace a needed SONAME, or remove a bundled component only after
  verifying the runtime layout and ABI. Prefer an upstream fix.
- Replace a bundled library with a system library only after checking SONAME, symbol
  versions, required interfaces, launcher configuration, and actual startup behavior.
- Remove foreign-architecture or foreign-OS components only when the selected package
  cannot use them and no supported feature references them.

## Installed image

- Preserve executable bits for programs and helpers. Verify modes in the final image;
  helpers installed with a data-file helper may lose execution permission.
- Validate installed desktop files, service definitions, icons, MIME data, wrappers, and
  absolute paths with the relevant official tools.
- Compare the installed file list with the previous version and upstream package. Check
  renamed launchers, resources, locales, editions, and optional components.
- Keep QA exceptions path-specific and backed by an observed, documented false positive.
  Never add one only to silence CI.

## Required verification

1. Regenerate and review the Manifest.
2. Run the repository lint and pkgcheck gates.
3. Install into the Portage image and inspect every installed object and file mode.
4. Perform the clean emerge and isolated elog check required by the live overlay
   workflow, with `qa`, `warn`, and `error` classes saved to files.
5. Investigate every saved elog entry. Compare the previous version under the same
   conditions before treating a warning as environmental.
6. Run a minimal smoke test only for trusted binaries on a matching architecture. Record
   tests that require a graphical session, account, hardware, or unavailable architecture
   as skipped with their impact.

CI is a final verification layer, not a substitute for checks required before the push.
Report every architecture not exercised locally and every accepted QA warning.

## Heuristic review targets

Large size changes, new private libraries, changed product editions, removed locales,
foreign-architecture add-ons, and rewritten launchers often indicate packaging changes.
They are review signals only. Verify them against the current upstream artifact before
editing the ebuild.

## Primary references

- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
- [Gentoo ebuild manual](https://devmanual.gentoo.org/eclass-reference/ebuild/index.html)
- [Gentoo eclass reference](https://devmanual.gentoo.org/eclass-reference/index.html)
- [Gentoo Development Guide](https://devmanual.gentoo.org/)
- The live overlay `AGENTS.md` and emerge-on-PR workflow
- Current Portage QA output and source for behavior not covered by the manuals
