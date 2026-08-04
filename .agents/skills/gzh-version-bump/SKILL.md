---
name: gzh-version-bump
description: "Bump and verify an existing package in the gentoo-zh overlay. Use for a package atom plus a requested or latest upstream version, including source, prebuilt, dependency, patch, license, Manifest, pkgcheck, build, install-elog, local commit, and PR preparation work. Apply only to existing gentoo-zh packages with testing keywords. Do not use for new packages, stabilization, the Gentoo main tree, or unrelated repositories."
---

# Bump an existing gentoo-zh package

Classify the changed surfaces, complete the package-specific work, then run the live hard
gates and applicable specialist checks in
[finish-pipeline.md](references/finish-pipeline.md).

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

Route references and specialist tools from verified release differences, never from
semantic-version size or line count:

- A copy-only source bump still verifies the exact target archive. It requires evidence
  that artifact selection and topology, dependencies, build inputs, USE behavior,
  patches, licenses, and installed layout are unchanged. It still receives complete
  Gentoo semantic and style review and every live hard gate, but does not load dependency,
  prebuilt, image, or test-matrix guidance without a matching package surface. Executor
  guidance remains conditional on the environment and an authorized named executor.
- A dependency, slot, provider, or USE change loads
  [ecosystem-checks.md](references/ecosystem-checks.md) and exercises the affected states.
- A generated bundle, archive-topology, license, redistribution, or high-risk distfile
  change loads [license-validation.md](references/license-validation.md) and the matching
  artifact checks.
- Every new or changed upstream-built payload loads
  [prebuilt-qa.md](references/prebuilt-qa.md), inventories each architecture, and receives
  artifact, static binary, and strict installed-image QA even when the ebuild body is
  copied unchanged.
- An installed-layout, mode, symlink, launcher, service, desktop-file, or runtime change
  receives installed-image review and a bounded runtime check when supported.
- A QA-only correction without a release change belongs to
  `gentoo-overlay-development`: reproduce the finding, fix its cause, and rerun the live
  hard gates plus invalidated surface checks. Do not invent a bump or revision.

1. **Select and verify the release.** Run `gzh latest <category/package>`, then verify the actual upstream release or tag and compare it with the highest current ebuild. Before editing, confirm every required source, generated dependency archive, and per-architecture artifact against upstream primary data. Never substitute an unverified host or unpublished file. Follow [upstream-lookup.md](references/upstream-lookup.md) when discovery is incomplete and [prebuilt-qa.md](references/prebuilt-qa.md) for prebuilt packages.
2. **Normalize the version.** Convert the upstream version to a valid Gentoo version without changing the upstream identifier used in URLs or tags. Use an ebuild variable when the two forms differ.
3. **Create or resume the topic branch.** Classify the installed payload from the ebuild and upstream evidence. Use `--package-model source` only when installed programs and libraries are built from source and the package installs no upstream-built native object, platform package, JVM bytecode, architecture-specific binary archive, executable application bundle, or executable script bundle. If any such payload exists, or an ambiguous archive contains it, use `--package-model prebuilt`. Run `gzh doctor --operation repository-write-preflight` and `gzh plan <category/package> <version> --package-model <source|prebuilt>` before writing. The plan rejects a source classification that conflicts with deterministic prebuilt indicators; absence of an indicator is not proof of a source model. For the prebuilt model, also supply the reviewed previous/current release inventory with `--assets-evidence` and resolve every reported architecture or filename change. Create `category-package-version` from the freshly synchronized local `master`, then run `gzh bump <category/package> <version>`. Reuse the correct existing topic branch when resuming. Stop if the remote, branch, classification, or ownership of existing changes is ambiguous.
4. **Reassess metadata.** Compare the new release's dependencies, build requirements, options, installed layout, licenses, and redistribution terms with the current ebuild. Follow [license-validation.md](references/license-validation.md) for license and redistribution evidence, and use [ecosystem-checks.md](references/ecosystem-checks.md) for ecosystem-specific questions.
5. **Reassess patches and workarounds.** Verify each referenced patch and Gentoo-side workaround against the new source. Preserve files still used by another ebuild and record upstream provenance for new backports.
6. **Decide whether to retain old versions.** Follow the package's established history and the live overlay policy. Check reverse dependencies and slots before removing anything; default to add-only when the evidence does not support a removal.
7. **Review version tracking.** Change `.github/workflows/overlay.toml` only when current repository policy and upstream evidence justify it. Review the resulting diff rather than treating tool output as sufficient.
8. **Review live siblings and related package state.** Apply only version-independent changes that the current live ebuild or directly related package state also needs.

Use [escalate-classes.md](references/escalate-classes.md) to identify work that requires maintainer input or missing generated data. Escalation means reporting the evidence and required decision; it does not authorize guessing or silently abandoning the task. Use [lesson-intake.md](references/lesson-intake.md) only to turn a derived case into a candidate check backed by the complete original change and current official rules.

## Run the required finish pipeline

After the package-specific work, run every live hard gate and applicable step in [finish-pipeline.md](references/finish-pipeline.md). Read [executors.md](references/executors.md) only when a named local or SSH executor is required. A gate that live policy permits the environment to skip remains unverified and must be reported. For gentoo-zh, the baseline order is:

1. `gzh lint`
2. `gzh manifest`
3. `gzh qa`
4. `gzh build` and `gzh merge`
5. `gzh diff`
6. `gzh commit`
7. `gzh urls`

This baseline applies even to a verified copy-only bump. Run additional tools only for
the surface they prove: dependency analysis for dependency or USE changes; artifact
inventory for prebuilt, generated, multi-architecture, mutable-name, manually fetched,
archive-topology, or unclear-provenance inputs; static binary and strict image QA for
prebuilt payloads; installed-image QA for layout changes; and `pkgdev tatt` only for a
supported relevant test matrix. Use `gzh commit`, never a bare `git commit`. The normal
result is a locally committed topic branch that has also passed the networked commit
gate.

## Keep publishing under human control

Do not push or create or edit a pull request automatically. Read
[publishing.md](references/publishing.md) only when the user separately asks to publish or
update a pull request.

## Exclusions

- New package creation
- Stabilization or stable keywords
- Packages in the Gentoo main tree or unrelated repositories
- Unrelated maintenance bundled into the bump
- Automatic push or PR creation
- Guessing through missing upstream, licensing, dependency, artifact, or repository-policy evidence
