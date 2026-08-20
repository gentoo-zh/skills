# Publish a Version Bump

Read this file only after the user separately asks to publish or update a pull request.
Complete the local finish pipeline before preparing publication.

## Decide What May Be Opened

Open the pull request only for a routine bump: an ebuild rename plus `gzh manifest`, or a
version variable such as a build id, with nothing else changed. That still requires the
per-pull-request confirmation below.

The title is the `pkgdev` English subject verbatim. Where the branch carries more than one
commit, use the subject of the commit carrying the main change.

Every other change stops at the personal fork. Push the topic branch, hand the user the
drafted subject and body with the compare link against canonical `master`, and let them
open the pull request:

```text
https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<branch>
```

Hand over the same way, stating that the pull request must be opened as a draft, when the
change raises a toolchain floor the Gentoo tree does not yet provide.

## Prepare and Confirm the Pull Request

1. Fetch the canonical remote and rebase the topic branch onto its current `master`.
   Compare and open against canonical `master`, never the personal fork's `master`; the
   fork lags and inflates the diff with unrelated commits.
2. Re-run every gate invalidated by the rebase. An earlier commit scan no longer covers the
   rebased commit.
3. Re-run `gzh urls` when the rebase changed its commit range or inputs.
4. Write the pull request body in Chinese when the human directing this work item writes in
   Chinese and otherwise in English, never both. A routine or behavior-neutral change that
   closes an overlay issue needs only `Closes #N`. Keep that reference bare in the body:
   never pass its number or URL to `pkgdev commit -b/--bug` or `-c/--closes`, whose bare
   numeric value means a Gentoo Bugzilla ID, and never rewrite it as a Bugzilla URL.
   Build the pull request body above the live template marker. Preserve the template and
   tick only checks that actually ran. Follow the live `AGENTS.md` for the description;
   do not turn broader checklist wording into routine passing-test or tested-architecture
   prose. Do not let an agent attest the human review box. Reuse only the verified causal
   rationale established for the commit; do not infer a new reason from release-note or
   upstream wording during publication. Invoke `chinese-skill` when the repository policy
   requires a Chinese pull request body. Rewrite the verified meaning in natural Chinese
   with the live repository's Gentoo terminology instead of translating word by word or
   coining a term. Keep the exact `pkgdev` English subject unchanged.
5. Write the complete body to a file and run
   `gzh pr-plan --title '<pkgdev subject>' --body <body-file>`. Show the user its immutable
   plan ID, exact title, complete body, and file list. Obtain a separate confirmation for
   this one rendered pull request before running `gh pr create` or `gh pr edit`. Batch
   approval, a request made before the plan was rendered, a wildcard, or draft status does
   not satisfy this gate.
6. Recompute every confirmed plan immediately before publication. Stop when its head SHA,
   base SHA, base-sensitive file set, title, body, or live template changes.
7. Push only the topic branch to the uniquely identified personal fork. Resolve the fork
   owner from `gh api user`; never push `master` or a canonical remote. Use
   `--force-with-lease` after a rebase. A missing or ambiguous personal fork blocks
   publishing, not local editing.
8. Run `gh pr create` with the confirmed title and complete body, including the preserved
   template. Do not change either after confirmation. A draft pull request is not an
   exception to the confirmation gate.
9. Run `gzh ci <pr-number> --watch` to retain full check names, URLs, state counts, head
   SHA, and final pull request state. For a failure, inspect the failing job log and apply
   the complete post-commit repair loop from the finish pipeline before pushing a
   replacement commit.
10. Re-fetch the canonical remote before stating that the commit or pull request is merged,
    and before basing follow-up work on it. A stale ref reports the opposite.

When live policy explicitly permits a local install skip because the environment cannot
merge, publication may proceed only when the exact install and elog risk is included in
the delivery report. Preserve the live template without inventing a checkbox, and do not
describe the branch as fully verified.

## Repair an Already Published Branch

If a later gate exposes a package defect, rebuild the local commit through `gzh recommit`
and rerun every invalidated gate. Force-push with lease only to the same personal topic
branch. Rebuild the exact pull request title, complete body, and file list after the
repair. If any of them changed, obtain confirmation for that specific updated pull
request before changing its branch or metadata.
