# Existing-Package Change Workflow

Use this procedure for one coherent change to an existing package. Apply the live
repository capability contract before every Git or publication action.

## Contents

- [Inspect Before Editing](#inspect-before-editing)
- [Edit from Evidence](#edit-from-evidence)
- [Keep the Code Style](#keep-the-code-style)
- [Review the Result](#review-the-result)

## Inspect Before Editing

1. Read every ebuild in the package, `metadata.xml`, `Manifest`, referenced files, and
   relevant package history.
2. Read the current documentation for each inherited eclass, including supported EAPIs,
   deprecation status, variables set before `inherit`, exported phases, defaults, and
   call-time helpers. Do not inherit an eclass for a single helper when direct phase code
   and current precedent are clearer. On an EAPI bump, re-audit the whole ebuild, including
   disabled USE branches, generated dependencies, dead helpers, and changed eclass defaults.
3. Read the exact upstream release notes, tag, source, build metadata, tests, artifacts,
   and license material affected by the request.
4. Compare the current and target source for dependencies, toolchain floors, build
   options, installed paths, bundled components, tests, licenses, and patches.
5. Before writing custom phase or eclass integration code, inspect the exact current
   Gentoo package and a genuinely comparable package with the same source model, build
   system, archive format, installed layout, and eclass contract. Prefer the simpler
   established current pattern when it satisfies the verified requirement. A construct with
   no counterpart in the current Gentoo tree or in the target repository needs a stated
   reason.
6. Define the owned file set and observable completion conditions. Preserve unrelated
   changes.

## Edit from Evidence

- Normalize the ebuild filename version according to Gentoo version syntax while
  preserving the literal upstream identifier separately when required.
- Keep release and live ebuild behavior distinct. Apply a version-independent correction
  to another existing sibling only when current evidence shows it has the same defect. Port
  applicable dependency, QA, EAPI, and phase fixes to the live ebuild.
- A live ebuild must sort above every release the package has. `9999` is lower than a date
  version, so a package versioned `20240203` needs `99999999`. Confirm the order with
  `vercmp` before naming the file.
- Keep global scope metadata-invariant and side-effect-free. Move work that requires
  build, profile, system, repository, or phase context into the applicable phase. Do not
  use pipes, process substitution, heredocs, or herestrings there; Bash may back the latter
  two with temporary files that the metadata sandbox forbids. Deterministic package-manager
  helpers and pure shell expansion remain valid.
- Preserve documented eclass defaults. When overriding a phase, compose it explicitly;
  `default` invokes the EAPI default, not an inherited implementation.
- Make each USE state control all applicable options, dependencies, source selection,
  tests, and installed files consistently. Disable verified automagic behavior.
- Prune only what `src_install` would otherwise install. Deleting a path that phase never
  reaches is dead code, as is a branch that only removes files the USE state does not
  install.
- `${ED}` and `${D}` already carry the offset; never append `${EPREFIX}` to them. Use a
  bare `${EPREFIX}` only in installed content read at runtime, and only where that consumer
  runs under Prefix.
- Declare all build and test inputs so the package succeeds without a warm cache or an
  undeclared network fetch.
- Change only what the request or the new release invalidates. When the change exposes a
  defect, fix it in the existing ebuild; rewrite the ebuild only when the release or the
  request leaves it unusable, and state what made it so. Restyling or reordering code that
  nothing invalidated is out of scope.
- Keep patches and substitutions narrow. Verify each one against the target source and
  remove it only when the exact change is upstream or no supported ebuild uses it.
- If `PATCHES` coexists with a custom `src_prepare`, call `default` or apply the patches
  explicitly. `eapply_user` alone does not apply `PATCHES`.
- Keep every referenced `files/` input, local license, metadata update, and Manifest entry
  in the same atomic change. Never replace a `files/` input a surviving ebuild still
  references; give its replacement a version- or revision-specific name.
- Record a backport's upstream commit, pull request, or bug URL and the versions it applies
  to and was tested on. A security fix covers every still-keyworded vulnerable branch and
  every relevant sibling or fork.
- Decide a revision from the current
  [Gentoo ebuild revision guidance](https://devmanual.gentoo.org/general-concepts/ebuild-revisions/)
  and live repository policy. Do not infer it from habit. Revise when the change can alter
  installed output or behavior, runtime dependencies, subslot binding, or default USE
  behavior, and for an affected non-free or soon-to-be-removed license or a non-trivial EAPI
  change. Do not revise a descriptive, copyright, keyword, message, test, build-failure, or
  build-dependency-relaxation change that cannot leave an installed result wrong.
- Retain or remove an older ebuild through the live repository's own retention rule and
  verified dependency, slot, keyword, and source-availability evidence. A repository may
  make either retaining or dropping the superseded version its default; resolve which one
  applies rather than assuming, and stop when the rule or its required evidence is
  unavailable.

## Keep the Code Style

- Keep code concise. Prefer clear naming, established helpers, direct control flow, and
  simple structure over explanatory comments or complex shell code.
- Implement logic beside the code it changes rather than adding a separate script. Introduce
  a function only when it materially reduces local complexity or duplication.
- Keep a comment only when it explains non-obvious intent, a constraint, a trade-off, or a
  workaround that the code cannot express and a future maintainer must preserve.
- Do not add a comment that restates an eclass-documented variable assignment,
  configuration, declaration, option value, function call, command, or standard setting.
- Do not comment to justify a QA suppression, including `QA_PREBUILT` and `QA_SONAME`.
- Preserve an existing useful comment unless it is outdated or incorrect.

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
