# License and Redistribution Validation

Use this workflow for every bump that changes source, bundled components, binary
artifacts, license text, or distribution terms. Read the live overlay `README`,
`AGENTS.md`, `profiles/license_groups`, and the official Gentoo license guidance first.

## Identify the licensed works

1. List the main program, bundled libraries, fonts, data, plugins, installers, and every
   upstream-built artifact distributed by the ebuild.
2. Read the `LICENSE*`, `COPYING*`, file headers, package metadata, and notices shipped in
   the exact target release. Inspect binary archives rather than assuming that the source
   repository license governs the distributed product.
3. Follow references from the shipped client or archive to the vendor's current software
   terms. Record the release, artifact, path or URL, retrieval date, and relevant clause.
4. Distinguish software terms from a privacy policy, website or account terms, service
   agreement, trademark notice, and third-party acknowledgements. None of those alone
   establishes the product's license.
5. Treat missing, contradictory, or unclear terms as a blocker. Do not infer permission
   from download availability, prior ebuild metadata, a free source component, or a
   vendor's silence.

Convert a PDF to text for review when necessary, but verify the conversion against the
original. Do not replace the authoritative artifact or omit clauses because the source
format is inconvenient.

## Map the evidence to Gentoo metadata

- Check the main Gentoo repository's `licenses/` before adding a local license. Reuse an
  identical existing license name; do not maintain an overlay duplicate that can drift.
- When the required license is absent from the main tree, follow the live overlay policy
  for adding its complete text under `licenses/`. A link or summary is sufficient only
  when the current repository policy and source terms explicitly permit that form.
- Express all applicable licenses in `LICENSE`. Include the main work and bundled works
  according to the official Devmanual syntax; do not substitute the license of a related
  project, website, or third-party component.
- Evaluate `RESTRICT=mirror`, `RESTRICT=bindist`, and `RESTRICT=fetch` independently from
  the actual clauses. Distfile mirroring and redistribution of built packages are
  different permissions. A proprietary label does not prove either result.
- Read the current definitions in the main tree and overlay before adding a license to
  `profiles/license_groups`. Classify the exact terms, including requirements for
  agreement, fees, notices, source availability, and redistribution.
- When multiple documents jointly grant the required rights, keep every required license
  token and group entry. Do not classify one document in isolation if installation or
  redistribution depends on the complete set.
- Implement requirements to accompany the software with license or notice text. Install
  the exact required material in the format and location allowed by current policy, and
  commit every referenced `files/` input with the ebuild.

## Verify the result

1. Compare the new `LICENSE` and `RESTRICT` values with the previous release and explain
   every change from primary evidence.
2. Query the current main-tree path with Portage rather than assuming its location, then
   check both repositories for the selected license names and groups.
3. Run `gzh lint`, regenerate the Manifest, and run `gzh qa`. Treat
   `UnknownLicense` as a name or repository defect, not proof that the selected terms are
   correct.
4. Inspect the installed image for every license or notice that the terms require the
   package to ship.
5. Recheck the terms on every bump. A stable URL, product name, or unchanged ebuild does
   not prove that the current release has unchanged rights.

## Current evidence

The gentoo-zh license review merged on 2026-08-02 corrected 42 package declarations,
three files that did not contain the product's license terms, and 19 missing distribution
restrictions. Use those commits as regression examples only. The live overlay policy,
official Gentoo license guide, and current upstream terms remain authoritative.

## Primary references

- The live gentoo-zh `README`, `AGENTS.md`, `profiles/license_groups`, package history,
  and `licenses/`
- [Gentoo license guidance](https://devmanual.gentoo.org/general-concepts/licenses/)
- [Gentoo ebuild revisions](https://devmanual.gentoo.org/general-concepts/ebuild-revisions/)
- The exact upstream release artifacts and software terms
