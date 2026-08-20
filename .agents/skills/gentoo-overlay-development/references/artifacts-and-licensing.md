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
3. Preserve the established artifact host and its existing filename convention unless
   primary evidence and live repository policy authorize a migration that changes both. Do
   not invent a mirror, substitute a nearby archive, assume an upstream location for an
   artifact the existing `SRC_URI` fetches elsewhere, or use an expiring authenticated URL.
4. Prove that every versioned dependency, vendor, crate, or `node_modules` artifact already
   exists for the new version before referencing it; otherwise the fetch returns 404.
5. Give changed content a distinct distfile name. Investigate reused names with changed
   bytes before updating a Manifest. For an in-place replacement of an existing distfile,
   verify provenance, contents, the producing tag or commit, available signatures, and
   licenses, use a distinct distfile name, and revise the ebuild.
6. Cross-check a large distfile's size against its source, so a truncated download cannot
   produce a plausible but invalid Manifest entry.
7. Regenerate the Manifest with the repository-approved command and inspect every changed
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
- whether bytes were available for inspection, plus the independently observed Portage
  fetch state and its evidence;
- report completeness and the exact expected-item count.

The `gzh artifacts` evidence schema additionally requires `inspection_available` and
`portage_fetch_state` for every Manifest `DIST` entry. Fetch state is one of `verified`,
`failed`, `not-tested`, or `superseded-by-ci`; every state except `not-tested` that claims
an observed result requires `portage_fetch_evidence`. Only `verified` or a separately
reviewed `superseded-by-ci` state satisfies the fetch gate. These fields are caller-supplied
review records: the command checks their shape and Manifest coverage but does not
authenticate a CI run, source URL, or human conclusion.

Manifest `DIST` names must be basenames and each entry must carry valid full-length
`BLAKE2B` and `SHA512` digests. When a distdir is supplied, `gzh artifacts` opens each
regular file without following symlinks, hashes both digests in one pass, and rejects an
identity or metadata change during hashing. It reports `manifest-digest-matched` only
after the stable file's size and both required digests match.

Use the exact command input shape below. `architecture`, `signature_url`, and `size` are
optional identity evidence. `source_url` or `release_url` is required for a passing
record. Generic digest strings are rejected because the schema cannot establish their
algorithm or provenance; use the Manifest digests and local distfile comparison instead.
`portage_fetch_evidence` is required for `verified`, `failed`, and `superseded-by-ci`.

```json
{
  "artifacts": [
    {
      "filename": "package-2.0-amd64.tar.xz",
      "architecture": "amd64",
      "release_url": "https://upstream.example/releases/2.0",
      "source_url": "https://upstream.example/package-2.0-amd64.tar.xz",
      "size": 12345,
      "inspection_available": true,
      "portage_fetch_state": "verified",
      "portage_fetch_evidence": "pkgdev manifest completed with default fetch settings"
    }
  ]
}
```

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
truncation, decompressor errors, or input changes. It marks the report incomplete when a
regular member is itself a recognized archive or container because nested content is
listed but not traversed; inspect each listed member separately. Treat an empty or
successful inventory as filename evidence only; it does not prove that every applicable
term was found or establish a redistribution decision.

## Prebuilt Package Review

- For a non-trivial prebuilt review, use a genuinely comparable current Gentoo package
  when one exists. Compare its source model, eclass stack, archive layout, install
  helpers, and source and installed file-mode behavior. Record the comparison when it
  informs a decision, but treat it as advisory evidence: the absence of a comparable
  package does not by itself block readiness, and one package never establishes a
  portable rule.
- Select only architectures with verified published artifacts and map each to the exact
  `SRC_URI` branch and Manifest entry allowed by live keyword policy.
- Inspect every installed object without executing untrusted or foreign-architecture
  files. Check ELF class and machine, interpreter, `DT_NEEDED`, SONAME, symlink layout,
  RPATH or RUNPATH, symbol version requirements, executable stack, text relocations,
  writable-and-executable segments, stripping state, and supported CPU requirements.
- Use `gzh binary` with `pax-utils` available to resolve the host-visible interpreter and
  runtime dependency tree without executing the target. A missing interpreter, unresolved
  dependency, bounded-output failure, or nested container that has not been expanded and
  reviewed blocks a complete result.
- Confirm runtime libraries, dynamically loaded components, and helper programs through
  binary inspection, upstream source or documentation, and a controlled runtime test.
- Scope `QA_PREBUILT` and other exceptions to exact reviewed paths and documented
  conditions. An exception suppresses a report; it does not repair a runtime defect.
- Use the documented strip restriction when package-manager stripping must be disabled;
  do not substitute an unrelated QA variable.
- Verify final file modes, launchers, resources, notices, and licenses in the installed
  image. Count executable regular files in both the source payload and installed image,
  and classify each as ELF, script, or unexpected data. Do not use package size as the
  threshold for this review. Record the interpreter for scripts and investigate why
  unexpected data is executable. Exclude symlink mode bits from this count because Linux
  does not use them for access; audit broken, absolute, and image-escaping targets
  separately. Apply a hard allowlist only when the live repository or its deterministic
  inspection tool defines the trigger and failure condition. Run only trusted binaries
  on a matching architecture.

When an ebuild inherits `unpacker` and a custom `src_unpack` manually extracts a Debian
`.deb` payload, review it against the documented helper contract. `gzh lint` reports a
review finding when that Debian-specific path bypasses `unpack_deb`, and separately
reports archive extraction in `src_install`. These warnings identify an unusual phase
model; they do not require `unpack_deb` for another archive format or prove that every
custom format is invalid.

Stop when architecture compatibility, loader or library availability, ABI requirements,
CPU requirements, license terms, or the final installed layout cannot be verified.

Record binary inspection as machine-readable evidence per object: installed path, file
type, ELF class and machine, interpreter, `DT_NEEDED`, SONAME, RPATH or RUNPATH, symbol
floors, executable-stack, text-relocation, writable-and-executable, stripping, and CPU
results. Identify the command and tool version for each observation. Never execute an
untrusted object or a binary for a foreign architecture merely to complete the report.
