# Release Contract

This repository publishes reviewed source snapshots. The installer operates from the
current checkout, and `update.sh` fast-forwards the canonical `master` branch before
refreshing managed installations. A release tag does not pin an existing installation.

## Identity

- Keep `project.version` in `gzh/pyproject.toml`, `gzh.__version__`, and `gzh --version`
  identical.
- Tag a release as `v<version>` with an annotated tag on the exact verified commit.
- Never move or replace a published tag. Publish a new version for a changed snapshot.
- Run `python scripts/release_check.py --mode source-only --tag v<version>` before
  creating the tag.

## Rights and Artifacts

The repository currently has no root license file and no `project.license` metadata.
Do not infer or add legal terms. Until the owner makes an explicit license decision, do
not upload a wheel, sdist, executable, or other custom distribution artifact.

A GitHub release still exposes GitHub-generated source archives for its tag. Treat their
extracted contents as the snapshot identity; GitHub does not promise stable compression
bytes. For a reproducible comparison, resolve and record the immutable commit SHA.

Package mode intentionally remains fail-closed until a separately reviewed,
deterministic rights-decision contract is implemented and tested. The presence of a root
license file and package metadata is a prerequisite, not authorization. After that
contract exists, clean-build, content inspection, empty-venv installation, and
`gzh --version` verification remain separate release gates.

## Preconditions

1. Fetch the canonical remote and require a clean `master` with zero ahead and behind
   counts.
2. Run the complete maintenance cycle with network checks, all registered sources
   current, repository validation, static evals, the full test suite, compile checks, and
   the release check.
3. Push the final commit and require every Python CI job to pass on that exact SHA.
4. Run the authenticated reference-audit workflow on that SHA. Review every candidate
   from separately established evidence and require the workflow to finish successfully.
5. Refresh managed Codex, Claude Code, and OpenCode installations from the final checkout,
   then verify installation status.

## Publish

Create and push the annotated tag only after all preconditions pass:

```bash
git tag -a v<version> <verified-sha> -m "gentoo-zh skills v<version>"
git push <canonical-remote> refs/tags/v<version>
```

Resolve `<canonical-remote>` by the `gentoo-zh/skills` repository URL. Do not assume that
`origin` is canonical in a fork clone.

Wait for tag-triggered CI. Then publish concise English release notes with
`gh release create --verify-tag`. Do not attach custom artifacts while the release check
rejects them.

Verify that the remote tag, GitHub release target, final `master`, and recorded verified
SHA are identical. Record the release URL and any skipped check or remaining limitation.

## Deferred Backlog

The registered official source audit tracks Python archive behavior and GitHub release,
source archive, and repository licensing guidance. Source drift creates a review item; it
does not rewrite this contract automatically.

This section is a version-controlled backlog, not durable maintenance queue state. Keep
these deferred items evidence-gated:

- Add a project license and package metadata only after an explicit owner decision.
- Add wheel or sdist publication only after the deterministic rights-decision contract
  and clean-build verification.
- Evaluate plugin packaging in a separate iteration after reviewing current official
  client documentation and proving install, update, collision, uninstall, and rollback
  behavior. The existing shared skill layout remains the compatibility baseline.
