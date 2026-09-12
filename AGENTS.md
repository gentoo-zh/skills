# Repository Guidelines

This repository holds three skills for gentoo-zh overlay work: `.agents/skills/gzh-{bump,new-package,review}/SKILL.md`. Policy lives in the overlay's `AGENTS.md` and `.agents/rules/`; a skill carries steps, commands, and a checklist, and never restates a rule. When a skill and the overlay disagree, fix the skill.

- Keep each SKILL.md under 100 lines: frontmatter, what to read first, numbered steps with one command block each, a checklist.
- Commands use official tools only: `pkgdev`, `pkgcheck`, `eix`, `qlist`, `scanelf`, `gh`. No environment variables, no machine-specific paths, no test hosts.
- Placeholders are angle-bracketed (`<cat>/<pkg>`, `<canonical>`, `<fork>`).
- Test a change by running the skill closed-book on a real package in a throwaway overlay clone and comparing with the merged commit.
