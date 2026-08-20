# Evidence and Source Order

Use current primary evidence for every package decision. Do not turn memory, an old
commit, or a derived checklist into repository policy.

## Contents

- [Authority Order](#authority-order)
- [Primary Sources](#primary-sources)
- [Task Start](#task-start)
- [Conflicts and Drift](#conflicts-and-drift)

## Authority Order

Apply sources in this order:

1. Read the target overlay checkout's current `AGENTS.md`, workflows, pull request
   template, package files, and package history. Repository policy and executable CI
   behavior take priority for gentoo-zh work.
2. Use the current Gentoo Package Manager Specification, Devmanual, GLEPs, eclass
   reference, and official tool manuals for general Gentoo semantics.
3. Use the upstream release, tag, source tree, build metadata, license text, and
   published artifacts for facts about the package being bumped.
4. Use the current Gentoo tree and a genuinely comparable package as implementation
   precedent. Treat a commit as evidence for that change, not as a general rule.
5. Use GURU documentation only as secondary maintenance practice. Do not copy GURU's
   branch model, contributor permissions, package admission policy, or repository-local
   EAPI policy into gentoo-zh.
6. Use `gentoo-tree-lessons` only to discover candidate commits and test cases. Follow
   [lesson-intake.md](lesson-intake.md) before adopting any conclusion.
7. Use other skill repositories only for skill structure, validation, and distribution.
   They do not define Gentoo behavior.

The authority of a source depends on the claim. Upstream metadata is primary for the
package's release facts, but it does not override PMS semantics or overlay policy.
Package history supplies precedent only and cannot override current policy or standards.

Before adding custom phase code or QA exceptions, inspect the exact current Gentoo
package when one exists, then the smallest sibling that matches its source or prebuilt
model, archive format, build system, installed layout, runtime integration, and eclass
contract. Prefer the simpler established pattern when it covers the verified need.
Product family alone is insufficient: current `app-editors/vscode` and
`www-client/google-chrome` are compact prebuilt precedents, while `www-client/chromium`
is a large source-build precedent. Re-open the current files before use; these paths are
search starting points, not frozen templates.

Reviewed official history illustrates the boundary:

- [`app-editors/vscode` 1.131.0](https://github.com/gentoo/gentoo/commit/e21cc671ab0dad4a91a266f4dc26d1863c161815)
  retained its established prebuilt ebuild pattern.
- [`www-client/google-chrome` 150.0.7871.181](https://github.com/gentoo/gentoo/commit/6653d348c0b6946355a5d7d4ab75666ee41fe0a4)
  was an ebuild rename plus Manifest update.
- [`www-client/chromium` 150.0.7871.181](https://github.com/gentoo/gentoo/commit/e710fadac8230667074ca702739c9fa505c911d3)
  retained its source-build implementation; a later
  [shared Node update](https://github.com/gentoo/gentoo/commit/a7997f3e7c1cc103cfb4bbb88d3567f8af9a4210)
  changed several versions deliberately.

These commits prove only those package changes. A copied ebuild is acceptable only after
the new release's assets, dependencies, build inputs, layout, licenses, patches, and
eclass assumptions have been reverified. It still receives every live style and QA gate.

## Primary Sources

- [gentoo-zh `AGENTS.md`](https://github.com/gentoo-zh/overlay/blob/master/AGENTS.md)
- [gentoo-zh emerge-on-PR workflow](https://github.com/gentoo-zh/overlay/blob/master/.github/workflows/emerge-on-pr.yml)
- [gentoo-zh pkgcheck workflow](https://github.com/gentoo-zh/overlay/blob/master/.github/workflows/pkgcheck.yml)
- [gentoo-zh pull request template](https://github.com/gentoo-zh/overlay/blob/master/.github/pull_request_template.md)
- [gentoo-zh contributor README](https://github.com/gentoo-zh/overlay/blob/master/README.en.md)
- [gentoo-zh license groups](https://github.com/gentoo-zh/overlay/blob/master/profiles/license_groups)
- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
- [Gentoo Development Guide](https://devmanual.gentoo.org/)
- [Gentoo eclass reference](https://devmanual.gentoo.org/eclass-reference/)
- [Gentoo ebuild manual](https://devmanual.gentoo.org/eclass-reference/ebuild/index.html)
- [Gentoo QA Policy Guide](https://projects.gentoo.org/qa/policy-guide/)
- [Gentoo license guidance](https://devmanual.gentoo.org/general-concepts/licenses/)
- [GLEP 66](https://www.gentoo.org/glep/glep-0066.html)
- [GLEP 76 copyright policy](https://www.gentoo.org/glep/glep-0076.html)
- [pkgcheck manual](https://pkgcore.github.io/pkgcheck/man/pkgcheck.html)
- [pkgdev manual](https://pkgcore.github.io/pkgdev/man/pkgdev.html)
- [Portage emerge manual](https://dev.gentoo.org/~zmedico/portage/doc/man/emerge.1.html)
- [nvchecker usage](https://nvchecker.readthedocs.io/en/latest/usage.html)
- [Gentoo repository](https://github.com/gentoo/gentoo)

Secondary sources include the
[GURU project documentation](https://wiki.gentoo.org/wiki/Project:GURU) and
[`gentoo-tree-lessons`](https://github.com/Zakkaus/gentoo-tree-lessons).

## Task Start

1. Run `gzh repo` and read the complete `AGENTS.md` from that checkout.
2. Identify the canonical remote by URL, fetch it, and use its current `master` as the
   base required by the live repository policy.
3. Resolve the absolute directory containing this skill and its sibling
   `gentoo-overlay-development`, then query the evidence registry for the narrowest
   relevant official source:

   ```bash
   python3 <skills-root>/gentoo-overlay-development/scripts/source_manager.py list --scope portable-core --topic dependency
   python3 <skills-root>/gentoo-overlay-development/scripts/source_manager.py show overlay-policy
   ```

4. Read the package inventory the skill's evidence step already collected. Widen it to the
   remaining ebuilds, the full package history, or an inherited eclass's current source only
   when a changed surface, a finding, a shared referenced file, or a requested ebuild change
   needs that evidence. Do not re-collect what the first pass established.
5. Record the source URL, check date, claim, scope, and whether the evidence is normative,
   package-specific, precedent, or inference.

## Conflicts and Drift

Do not silently reconcile conflicting sources. Use the live workflow as the executable
CI contract and the live `AGENTS.md` as repository policy. Stop and report a material
conflict that changes the package result, publication decision, or required evidence.

Prefer current official text and current eclass behavior over an old example. Check a
commit for reverts and follow-up changes before using it. A short commit ID, an isolated
diff, or one similar package is never sufficient proof of a general rule.

Audit registered sources without rewriting policy:

```bash
python3 <skills-root>/gentoo-overlay-development/scripts/source_manager.py audit --scope portable-core
python3 <skills-root>/gentoo-overlay-development/scripts/source_manager.py audit --scope adapter:gentoo-zh --fail-on-drift
```

The shared `gentoo-overlay-development/references/source-lock.json` stores observed
fingerprints and a per-source UTC check timestamp, not
a trusted offline copy of the source. If a required current source cannot be read, report
the unavailable check and stop whenever the missing evidence affects licensing,
redistribution, security, artifacts, masks, eclass compatibility, CI behavior, or pull
request policy.

Do not update a fingerprint during package work. Hand source drift and its complete diff
to `gzh-maintain-skills`, which owns reviewed instruction, test, and lock changes.
