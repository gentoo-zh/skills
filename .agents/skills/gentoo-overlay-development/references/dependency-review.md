# Dependency and USE Review

Classify dependencies by observed behavior under the active EAPI. Use the
[Gentoo dependency guide](https://devmanual.gentoo.org/general-concepts/dependencies/)
and the current Package Manager Specification for variable and dependency syntax.

## Contents

- [Build the Evidence Set](#build-the-evidence-set)
- [Declare the Relationship](#declare-the-relationship)
- [Check Impact](#check-impact)

## Build the Evidence Set

1. Read upstream build files, lock files, source imports, linker output, test
   configuration, runtime launchers, plugin discovery, and installed service files.
2. Compare the prior and target release. Record added, removed, renamed, optional,
   bundled, dynamically loaded, and helper-process requirements.
3. Inspect the active eclass contracts for dependencies they add or variables that
   control dependency generation.
4. Record the consumer action, phase, USE state, provider atom, slot or version
   requirement, and evidence for each direct dependency.
5. Accept only observed behavior as evidence for an atom: a linked SONAME, a build-file
   `dependency()`, `find_package`, or `pkg-config` call, a `dlopen`ed library, or a program
   a phase runs. Another distribution's control file is not evidence.

Do not copy upstream package names into Gentoo atoms without verifying the provider in
the target repository set. Do not infer generated dependency archives, pins, sibling
versions, or optional providers from a successful build.

For deterministic syntax reduction, pass already extracted metadata to the bundled
analyzer from this skill directory:

```bash
python3 scripts/dependency_analyzer.py \
  --input /absolute/path/dependencies.json \
  --output /tmp/dependency-report.json
```

The JSON object accepts `eapi`, an explicit `use` state, and the dependency fields valid
for that EAPI. The analyzer uses the installed Portage API, reads at most 1 MiB, never
sources an ebuild, and records the input byte count and SHA-256. Treat reduced atoms,
blockers, and slot operators as syntax evidence only; upstream behavior and repository
resolution still establish whether a dependency is correct.

When `gzh` is installed, use its bounded wrappers for repository ebuilds:

```bash
gzh deps inspect /absolute/path/to/package-2.ebuild [--use +flag --use -other]
gzh deps diff /absolute/path/to/package-1.ebuild /absolute/path/to/package-2.ebuild
gzh deps reverse dev-libs/provider
```

`inspect` and `diff` verify a matching repository md5-cache entry only when it records no
inherited eclasses. The ebuild `_md5_` alone cannot prove that an `_eclasses_` set remains
current, so inherited, missing, or stale cache records run official
`egencache --external-cache-only` for the exact package and worktree. The command keeps
both intermediate and generated cache data in a private temporary directory, verifies the
generated `_md5_`, and removes the directory after the report captures its hashes. This
generation sources the ebuild and inherited eclasses in
Portage's metadata environment; the bundled analyzer still consumes only the extracted
metadata. Generator output is streamed with a 64-KiB aggregate limit and a 120-second
timeout; exceeding either limit terminates the owned process group and fails closed. The
commands reject non-regular or over-1-MiB ebuild and cache inputs. The diff
always compares potential declarations and adds a reduced delta only for one explicit,
complete USE state shared by both versions. The reverse query uses pquery's raw ebuild
repository view, so it reports potential direct consumers rather than active-profile,
transitive, or ABI relationships. Treat all three reports as review indexes, not proof
that a dependency is behaviorally required or compatible.

## Declare the Relationship

- Select `BDEPEND`, `DEPEND`, `RDEPEND`, `IDEPEND`, or `PDEPEND` from the active EAPI's
  documented install-root, build-host, and phase semantics. In EAPI 8 or later, a host tool
  that must execute while the package is merged, such as a post-install cache generator,
  belongs in `IDEPEND`.
- Declare direct requirements only. Do not add transitive libraries merely because they
  appear in another package's dependency closure.
- Do not declare what the environment already provides: `@system` members, tools an
  inherited eclass pulls in, or a compiler or libc floor the profile guarantees.
- Use one atom per package. Fold the version bound, slot, and USE constraints into that
  single entry.
- Carry the build system's own version floors into the atom and do not invent one it does
  not state. Add an upper bound only for a verified incompatibility with no available fix.
- Use a slot or subslot operator only for a verified provider compatibility or rebuild
  relationship. A rebuild operator does not make fixed prebuilt bytes compatible with a
  changed ABI.
- Before adding `:=` or `:slot=`, confirm that the provider declares a subslot. Without one
  the operator binds `slot/slot`, so consumers rebuild when the slot changes but never on an
  ABI break inside it.
- For a verified direct ABI linkage, put `:=` or `:slot=` on every `DEPEND` or `RDEPEND` atom
  that models it, and never copy the operator to a transitive dependency. A provider subslot
  represents an ABI that requires consumer rebuilds, so re-check provider SONAMEs,
  private-header ABI, and library renames on every bump.
- Keep built slot operators out of syntax contexts where the active EAPI forbids them.
- Before adding or retaining a dependency or an alternative provider, check the removal
  entries in both the target repository's and the main Gentoo tree's `profiles/package.mask`.
- Put only verified interchangeable providers in an any-of group and preserve the
  repository's documented preference order. Keep built slot operators out of `PDEPEND` and
  outside `|| ( )`.
- When a package is removed or renamed, update the dependency atoms together with the
  `elog` and `optfeature` recommendation strings that name it.
- Never derive a sibling package's version from `${PV}` without verifying that the derived
  atom exists and resolves.
- Make a USE condition control the dependency and every corresponding build option,
  source, test, and installed component. Check both enabled and disabled states.
- Gate test-only inputs on the same condition that runs the tests. Retain a reliable test
  subset rather than broadly disabling tests.
- Verify executable helpers and dynamically loaded components as runtime behavior; static
  linker output alone cannot prove or disprove them.

## Check Impact

1. Resolve the package in every repository-required profile and affected USE state.
2. Build and install every changed state required by live policy.
3. Inspect linked objects, runtime invocation, and installed files to confirm providers
   match the declaration.
4. Search reverse dependencies before removing a version, narrowing a slot, changing an
   ABI contract, removing a keyword, or replacing a provider.
5. Decide whether an existing installed package requires a revision from official
   revision guidance and the exact behavioral change.
6. Treat dependency scanner output as a review candidate. Confirm every addition or
   removal with package and upstream evidence.

Stop when a provider cannot be identified, an atom does not resolve in a required
profile, an ABI relationship is unknown, a USE state is inconsistent, or a needed
generated input is unavailable.
