---
name: gentoo-zh
description: Bump an existing gentoo-zh overlay package, add a new package, or review a package PR using the overlay's policy and Gentoo tools.
---

Read the overlay checkout's `AGENTS.md` first. Rule paths below are relative to that checkout; procedure links are relative to this skill.

Placeholders: none. Each reference declares its own.

Load `.agents/rules/git-workflow.md` before editing, `.agents/rules/qa.md` before validation, and `.agents/rules/pr-text.md` before commit or PR preparation. Follow `Read with:` links for the affected surface:

| Surface | Overlay rule |
| --- | --- |
| Ebuild or package review | `.agents/rules/ebuild-construction.md`, `.agents/rules/dependencies.md` |
| Version bump | `.agents/rules/version-bumps.md` |
| New package or tracking entry | `.agents/rules/new-packages.md` |
| Eclass choice or EAPI change | `.agents/rules/eclass-discovery.md` |
| Bundled or prebuilt binaries | `.agents/rules/prebuilt-binaries.md` |
| Desktop integration | `.agents/rules/desktop-integration.md` |
| Service unit or init script | `.agents/rules/openrc-systemd.md` |
| Kernel package | `.agents/rules/kernels.md` |

Load one procedure, then only the applicable conditions in [references/checks.md](references/checks.md):

- Version bump: [references/bump.md](references/bump.md).
- New package: [references/new-package.md](references/new-package.md).
- Package PR review: [references/review.md](references/review.md).

The overlay rules own policy; these references supply commands and evidence checks.
