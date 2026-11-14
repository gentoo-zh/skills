---
name: gentoo-zh
description: Bump an existing gentoo-zh overlay package, add a new package, or review a package PR using the overlay's policy and Gentoo tools.
---

Read the overlay checkout's `AGENTS.md` first, then `.agents/rules/git-workflow.md` before the first edit. All rule paths below are relative to that checkout; procedure links are relative to this skill.

Placeholders: none. Each procedure and the shared checks declare their own.

Read `.agents/rules/pr-text.md` for commit, PR, or review text, then load rules by the changed surface:

| Surface | Overlay rule |
| --- | --- |
| Version bump | `.agents/rules/version-bumps.md` |
| New package or overlay.toml | `.agents/rules/new-packages.md` |
| Eclass choice or EAPI change | `.agents/rules/eclass-discovery.md` |
| Prebuilt payload | `.agents/rules/prebuilt-binaries.md` |
| Desktop application or Wayland flags | `.agents/rules/desktop-integration.md` |
| Service unit or init script | `.agents/rules/openrc-systemd.md` |
| Kernel package | `.agents/rules/kernels.md` |

Load only the applicable procedure:

- Version bump → [references/bump.md](references/bump.md).
- New package → [references/new-package.md](references/new-package.md).
- Package PR review → [references/review.md](references/review.md).

Each procedure selects sections of [references/checks.md](references/checks.md). Policy remains in the overlay; use its current rules when a procedure disagrees.
