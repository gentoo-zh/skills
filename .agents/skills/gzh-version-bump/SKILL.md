---
name: gzh-version-bump
description: "Bump and verify an existing package in the gentoo-zh overlay. Use for a package atom plus a requested or latest upstream version, including source, prebuilt, dependency, patch, license, Manifest, pkgcheck, build, install-elog, local commit, and PR preparation work. Apply only to existing gentoo-zh packages with testing keywords. Do not use for new packages, stabilization, the Gentoo main tree, or unrelated repositories."
---

# Bump an existing gentoo-zh package

Complete the package-specific work, then run the full verification and local commit pipeline in [finish-pipeline.md](references/finish-pipeline.md).

## Start from current policy and evidence

1. Run `gzh repo` and read the complete live `AGENTS.md` from that overlay worktree before changing anything. Its repository, CI, commit, and PR rules override this skill and its references.
   If `gzh` is unavailable, install it from a checked-out `gentoo-zh/skills` root with `./install.sh --gzh-only`. Then run inside the overlay or set `GZH_OVERLAY_DIR` to its absolute path.
2. Confirm that the worktree is the gentoo-zh overlay and inspect `git status --short --branch`. Identify an accepted canonical remote by the current or legacy URL listed in the live policy. If none exists, add `upstream` with the current HTTPS URL. Fetch its `master` and set its remote HEAD when missing.
3. Synchronize local `master` with `git switch master` and `git merge --ff-only <canonical>/master` before creating a topic branch. Then require `git rev-list --left-right --count master...<canonical>/master` to report `0 0`; stop if local `master` is ahead or behind. Preserve unrelated changes without stashing, staging, or moving them. Stop or use a separate clean worktree when switching cannot preserve them safely.
4. Use the evidence order in [official-sources.md](references/official-sources.md). Prefer current repository policy and package history, official Gentoo specifications, documentation, eclasses and tools, and upstream primary sources. Treat GURU practice and derived case collections as supporting evidence only; they never override higher-authority sources.
5. Read every ebuild in the target package, `metadata.xml`, referenced files, relevant history, inherited eclasses, upstream release material, build metadata, artifacts, and license terms needed for this bump.
6. Stop and report the missing fact or conflict when required evidence is unavailable. Do not infer dependencies, versions, artifacts, licenses, keywords, or package policy from memory.

## Keep the change within scope

- Use testing keywords only. Preserve existing keywords unless the live overlay policy and verified arch evidence justify a change.
- Use one package, topic branch, commit, and PR by default. Apply only the multi-package exceptions defined by the live overlay `AGENTS.md`.
- Prefer `gzh` for deterministic operations. Do not stage, rewrite, or commit unrelated work.
- Keep release and live ebuild behavior distinct, but apply version-independent fixes to a live sibling when current evidence requires it.
- Do not add AI attribution to commits, PRs, comments, or files.
- Make at most three total attempts at a failed operation. Stop earlier when the identical error is observed for the second time, then report the failed step and evidence.

## Perform the package-specific work

1. **Select and verify the release.** Run `gzh upstream-version <category/package>`, then verify the actual upstream release or tag and compare it with the highest current ebuild. Before editing, confirm every required source, generated dependency archive, and per-architecture artifact against upstream primary data. Never substitute an unverified host or unpublished file. Follow [upstream-lookup.md](references/upstream-lookup.md) when discovery is incomplete and [prebuilt-qa.md](references/prebuilt-qa.md) for prebuilt packages.
2. **Normalize the version.** Convert the upstream version to a valid Gentoo version without changing the upstream identifier used in URLs or tags. Use an ebuild variable when the two forms differ.
3. **Create or resume the topic branch.** Create `category-package-version` from the freshly synchronized local `master`, then run `gzh bump-scaffold <category/package> <version>`. Reuse the correct existing topic branch when resuming. Stop if the remote, branch, or ownership of existing changes is ambiguous.
4. **Reassess metadata.** Compare the new release's dependencies, build requirements, options, installed layout, licenses, and redistribution terms with the current ebuild. Follow [license-validation.md](references/license-validation.md) for license and redistribution evidence, and use [ecosystem-checks.md](references/ecosystem-checks.md) for ecosystem-specific questions.
5. **Reassess patches and workarounds.** Verify each referenced patch and Gentoo-side workaround against the new source. Preserve files still used by another ebuild and record upstream provenance for new backports.
6. **Decide whether to retain old versions.** Follow the package's established history and the live overlay policy. Check reverse dependencies and slots before removing anything; default to add-only when the evidence does not support a removal.
7. **Review version tracking.** Change `.github/workflows/overlay.toml` only when current repository policy and upstream evidence justify it. Review the resulting diff rather than treating tool output as sufficient.
8. **Review live siblings and related package state.** Apply only version-independent changes that the current live ebuild or directly related package state also needs.

Use [escalate-classes.md](references/escalate-classes.md) to identify work that requires maintainer input or missing generated data. Escalation means reporting the evidence and required decision; it does not authorize guessing or silently abandoning the task. Use [lesson-intake.md](references/lesson-intake.md) only to turn a derived case into a candidate check backed by the complete original change and current official rules.

## Run the required finish pipeline

After the package-specific work, run every applicable step in [finish-pipeline.md](references/finish-pipeline.md). A gate that live policy permits the environment to skip remains unverified and must be reported. Its core order is:

1. `gzh lint`
2. `gzh manifest`
3. `gzh pkgcheck`
4. `gzh build-test` and `gzh verify-install`
5. `gzh diff-ebuild`
6. `gzh commit`
7. `gzh pkgcheck-commits`

Run the additional tests, installed-file review, network checks, and per-ebuild validation required by the live overlay policy and the finish pipeline. Use `gzh commit`, never a bare `git commit`. The normal result is a locally committed topic branch that has also passed the networked commit gate.

## Keep publishing under human control

Do not push or create or edit a PR automatically. If the user separately asks to publish, first complete the pre-PR gates in the finish pipeline. Then show the exact PR title, body, and file list and obtain confirmation for that specific PR before `gh pr create` or `gh pr edit`. A draft PR and a batch authorization are not exceptions. When repository policy requires a Chinese PR body, invoke `chinese-skill`; keep Chinese writing rules and examples in that skill instead of duplicating them here.

## Exclusions

- New package creation
- Stabilization or stable keywords
- Packages in the Gentoo main tree or unrelated repositories
- Unrelated maintenance bundled into the bump
- Automatic push or PR creation
- Guessing through missing upstream, licensing, dependency, artifact, or repository-policy evidence
