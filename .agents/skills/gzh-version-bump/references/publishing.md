# Publish a Version Bump

Read this file only after the user separately asks to publish or update a pull request.
Complete the local finish pipeline before preparing publication.

## Prepare and Confirm the Pull Request

1. Fetch the canonical remote and rebase the topic branch onto its current `master`.
2. Re-run every gate invalidated by the rebase.
3. Re-run `gzh urls` when the rebase changed its commit range or inputs.
4. Build the pull request body above the live template marker. Preserve the template and
   tick only checks that actually ran. Follow the live `AGENTS.md` for the description;
   do not turn broader checklist wording into routine passing-test or tested-architecture
   prose. Do not let an agent attest the human review box. Invoke `chinese-skill` when the
   repository policy requires a Chinese pull request body; keep its writing rules and
   examples in that skill.
5. Write the complete body to a file and run
   `gzh pr-plan --title '<pkgdev subject>' --body <body-file>`. Show the user its immutable
   plan ID, exact title, complete body, and file list. Obtain confirmation for every
   identified plan before running `gh pr create` or `gh pr edit`. One response may approve
   several separately enumerated exact plan IDs. A request made before the plans were
   rendered, a wildcard, or draft status does not satisfy this gate.
6. Recompute every confirmed plan immediately before publication. Stop when its head SHA,
   base SHA, base-sensitive file set, title, body, or live template changes.
7. Push only the topic branch to the uniquely identified personal fork. Resolve the fork
   owner from `gh api user`; never push `master` or a canonical remote.
8. Run `gh pr create` with the confirmed title and complete body, including the preserved
   template. Do not change either after confirmation. A draft pull request is not an
   exception to the confirmation gate.
9. Run `gzh ci <pr-number> --watch` to retain full check names, URLs, state counts, head
   SHA, and final pull request state. For a failure, inspect the failing job log and apply
   the complete post-commit repair loop from the finish pipeline before pushing a
   replacement commit.

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
