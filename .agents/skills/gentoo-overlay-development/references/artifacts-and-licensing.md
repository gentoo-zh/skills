# Sources, Prebuilt Artifacts, Patches, Licenses, and Manifests

Read exact release material before editing `SRC_URI`, `Manifest`, `PATCHES`, `LICENSE`,
`RESTRICT`, keywords, or installed notices.

## Contents

- [Source and Manifest Evidence](#source-and-manifest-evidence)
- [Patches and Local Files](#patches-and-local-files)
- [License and Redistribution](#license-and-redistribution)
- [Prebuilt Package Review](#prebuilt-package-review)

## Source and Manifest Evidence

1. Verify the release or immutable source revision on the upstream primary site.
2. Verify every archive, generated dependency bundle, and per-architecture artifact by
   filename, source, release, architecture, expected size, and available signature or
   digest evidence.
3. Preserve the established artifact host unless primary evidence and live repository
   policy authorize a migration. Do not invent a mirror, substitute a nearby archive, or
   use an expiring authenticated URL.
4. Give changed content a distinct distfile name. Investigate reused names with changed
   bytes before updating a Manifest.
5. Regenerate the Manifest with the repository-approved command and inspect every changed
   entry. Follow the official
   [Manifest guidance](https://devmanual.gentoo.org/general-concepts/manifest/).

Stop when required bytes, provenance, completeness, signature evidence required by
policy, or redistribution permission cannot be established.

For a repeatable artifact report, record every expected item with these independent
fields:

- upstream project and release or immutable source revision;
- final URL, upstream filename, local distfile name, artifact role, and architecture;
- observed size, Manifest digests, and any separately verified upstream digest or
  signature;
- provenance source and retrieval date;
- state as `resolved`, `unresolved`, or `skipped`, with a reason;
- report completeness and the exact expected-item count.

A matching size or digest establishes byte identity against that value, not who produced
the bytes or whether the artifact is complete for the release. Do not report success when
an expected URI, architecture branch, generated bundle, redirect target, signature, or
license record was silently omitted.

## Patches and Local Files

- Verify each patch against the exact target source and record its upstream commit, pull
  request, or issue when available.
- Follow the official
  [patch guidance](https://devmanual.gentoo.org/ebuild-writing/misc-files/patches/) for
  format, attribution, placement, and maintenance.
- Confirm that custom `src_prepare` composition still applies `PATCHES` and user patches
  according to the active EAPI and inherited eclasses.
- Remove a patch only after verifying that the target source contains the fix or no
  supported ebuild references it.
- Include every referenced local file in the atomic change and verify its final use,
  content, path, and mode.

## License and Redistribution

1. Identify the main work, bundled libraries, fonts, data, plugins, installers, and every
   distributed artifact.
2. Read the license files, notices, file headers, package metadata, and product terms in
   the exact source or binary release. A source repository license does not automatically
   govern a separately distributed binary.
3. Distinguish software terms from privacy, service, website, trademark, and account
   terms. Download availability is not permission to mirror or redistribute.
4. Map every applicable work to current Gentoo `LICENSE` syntax and names. Add a local
   license only through the live repository procedure and include its complete required
   text.
5. Evaluate `RESTRICT=mirror`, `RESTRICT=bindist`, and `RESTRICT=fetch` independently
   from the exact terms. Install every notice or license the terms require.
6. Recheck licenses and distribution terms for every changed release or artifact. Stop
   on missing, contradictory, or unclear permission.

Use the official
[license guidance](https://devmanual.gentoo.org/general-concepts/licenses/) and current
repository license groups. A passing license-name check does not prove the selected terms
are correct.

Keep a license evidence collector factual. It may inventory and hash exact license files,
notices, ebuild `LICENSE` tokens, local license definitions, installed notices, bundled
components, and `RESTRICT` values. It must not infer permission, compatibility, or a legal
conclusion. A human review still decides which terms apply to each distributed work and
artifact.

For a local tar or ZIP release archive, run `gzh license <archive>` before reading the
matched files. The command accepts plain, gzip, bzip2, or xz tar streams and non-ZIP64 ZIP
metadata. It rejects Zstandard tar and ZIP64 metadata because their bounded parser
preflight is not implemented. It does not extract or execute members. It records the
archive digest and the path, size, and digest of every bounded license-like member, and
fails on unsafe paths, ambiguous names, unsupported members, parser metadata limits,
truncation, decompressor errors, or input changes. Treat an empty or successful inventory
as filename evidence only; it does not prove that every applicable term was found or
establish a redistribution decision.

## Prebuilt Package Review

- Select only architectures with verified published artifacts and map each to the exact
  `SRC_URI` branch and Manifest entry allowed by live keyword policy.
- Inspect every installed object without executing untrusted or foreign-architecture
  files. Check ELF class and machine, interpreter, `DT_NEEDED`, SONAME, symlink layout,
  RPATH or RUNPATH, symbol version requirements, executable stack, text relocations,
  writable-and-executable segments, stripping state, and supported CPU requirements.
- Confirm runtime libraries, dynamically loaded components, and helper programs through
  binary inspection, upstream source or documentation, and a controlled runtime test.
- Scope `QA_PREBUILT` and other exceptions to exact reviewed paths and documented
  conditions. An exception suppresses a report; it does not repair a runtime defect.
- Use the documented strip restriction when package-manager stripping must be disabled;
  do not substitute an unrelated QA variable.
- Verify final file modes, launchers, resources, notices, and licenses in the installed
  image. Run only trusted binaries on a matching architecture.

Stop when architecture compatibility, loader or library availability, ABI requirements,
CPU requirements, license terms, or the final installed layout cannot be verified.

Record binary inspection as machine-readable evidence per object: installed path, file
type, ELF class and machine, interpreter, `DT_NEEDED`, SONAME, RPATH or RUNPATH, symbol
floors, executable-stack, text-relocation, writable-and-executable, stripping, and CPU
results. Identify the command and tool version for each observation. Never execute an
untrusted object or a binary for a foreign architecture merely to complete the report.
