# Repository Guidelines

This repository holds one entry skill at `.agents/skills/gentoo-zh/SKILL.md`, with on-demand procedures and shared checks in its `references/` directory. Policy lives in the overlay's `AGENTS.md` and `.agents/rules/`; the skill carries steps, commands, and checks, and never restates a rule. When the skill and overlay disagree, fix the skill.

The skill has no dedicated CLI dependency. The separate `gentoo-zh/gzh` tool is paused and must not be used.

- Keep SKILL.md at most 40 lines: frontmatter, policy reads by surface, and routing to bump, new-package, or review references.
- Keep references/bump.md, references/new-package.md, and references/review.md at most 60 lines each, with numbered steps and one command block per step.
- Keep references/checks.md at most 90 lines, split by condition; each item has one runnable command and a stopping result.
- Keep every supporting file inside the skill directory so a symlink installation includes it.
- Commands use Gentoo tools (`pkgdev`, `pkgcheck`, `emerge`, `eix`, `qlist`, `scanelf`, `portageq`), git, `gh`, and POSIX basics. No environment variables, no machine-specific paths, no test hosts.
- Placeholders are angle-bracketed. Each file lists every placeholder it uses, and no unused ones, in its second paragraph.
- Test a change by running the skill closed-book on a real package in a throwaway overlay clone and comparing with the merged commit.
