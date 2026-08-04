# Existing-Package Change Workflow

Use this procedure for one coherent change to an existing package. Apply the live
repository capability contract before every Git or publication action.

## Inspect Before Editing

1. Read every ebuild in the package, `metadata.xml`, `Manifest`, referenced files, and
   relevant package history.
2. Read the current documentation for each inherited eclass, including supported EAPIs,
   variables set before `inherit`, exported phases, defaults, and call-time helpers.
3. Read the exact upstream release notes, tag, source, build metadata, tests, artifacts,
   and license material affected by the request.
4. Compare the current and target source for dependencies, toolchain floors, build
   options, installed paths, bundled components, tests, licenses, and patches.
5. Before writing custom phase or eclass integration code, inspect the exact current
   Gentoo package and a genuinely comparable package with the same source model, build
   system, archive format, installed layout, and eclass contract. Prefer the simpler
   established current pattern when it satisfies the verified requirement.
6. Define the owned file set and observable completion conditions. Preserve unrelated
   changes.

## Edit from Evidence

- Normalize the ebuild filename version according to Gentoo version syntax while
  preserving the literal upstream identifier separately when required.
- Keep release and live ebuild behavior distinct. Apply a version-independent correction
  to another existing sibling only when current evidence shows it has the same defect.
- Keep global scope metadata-invariant and side-effect-free. Move work that requires
  build, profile, system, repository, or phase context into the applicable phase.
- Preserve documented eclass defaults. When overriding a phase, compose it explicitly;
  `default` invokes the EAPI default, not an inherited implementation.
- Make each USE state control all applicable options, dependencies, source selection,
  tests, and installed files consistently. Disable verified automagic behavior.
- Declare all build and test inputs so the package succeeds without a warm cache or an
  undeclared network fetch.
- Keep patches and substitutions narrow. Verify each one against the target source and
  remove it only when the exact change is upstream or no supported ebuild uses it.
- Keep every referenced `files/` input, local license, metadata update, and Manifest entry
  in the same atomic change.
- Decide a revision from the current
  [Gentoo ebuild revision guidance](https://devmanual.gentoo.org/general-concepts/ebuild-revisions/)
  and live repository policy. Do not infer it from habit.
- Retain or remove an older ebuild only through the live repository procedure and verified
  dependency, slot, keyword, and source-availability evidence. Default to retention when
  the procedure or evidence is incomplete.

## Review the Result

1. Compare every changed variable, phase, patch, dependency, USE branch, installed path,
   license, restriction, and Manifest entry with its evidence.
2. Verify every `${FILESDIR}` reference names a file included in the change or retained
   by another supported ebuild.
3. Inspect the complete diff and status for unrelated hunks, generated debris, debug
   output, missing support files, and unintended Manifest changes.
4. Run the complete repository-defined verification sequence from
   [qa-verification.md](qa-verification.md).
5. Stop on an unexplained change or failed required gate. Do not weaken the ebuild or a
   check merely to obtain a passing result.

Primary references:

- [Ebuild file format](https://devmanual.gentoo.org/ebuild-writing/file-format/index.html)
- [Common ebuild problems](https://devmanual.gentoo.org/appendices/common-problems/)
- [Eclass reference](https://devmanual.gentoo.org/eclass-reference/index.html)
- [Ebuild revisions](https://devmanual.gentoo.org/general-concepts/ebuild-revisions/)
