# Dependency and USE Review

Classify dependencies by observed behavior under the active EAPI. Use the
[Gentoo dependency guide](https://devmanual.gentoo.org/general-concepts/dependencies/)
and the current Package Manager Specification for variable and dependency syntax.

## Build the Evidence Set

1. Read upstream build files, lock files, source imports, linker output, test
   configuration, runtime launchers, plugin discovery, and installed service files.
2. Compare the prior and target release. Record added, removed, renamed, optional,
   bundled, dynamically loaded, and helper-process requirements.
3. Inspect the active eclass contracts for dependencies they add or variables that
   control dependency generation.
4. Record the consumer action, phase, USE state, provider atom, slot or version
   requirement, and evidence for each direct dependency.

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

## Declare the Relationship

- Select `BDEPEND`, `DEPEND`, `RDEPEND`, `IDEPEND`, or `PDEPEND` from the active EAPI's
  documented install-root, build-host, and phase semantics.
- Declare direct requirements only. Do not add transitive libraries merely because they
  appear in another package's dependency closure.
- Use a slot or subslot operator only for a verified provider compatibility or rebuild
  relationship. A rebuild operator does not make fixed prebuilt bytes compatible with a
  changed ABI.
- Keep built slot operators out of syntax contexts where the active EAPI forbids them.
- Put only verified interchangeable providers in an any-of group and preserve the
  repository's documented preference order.
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
